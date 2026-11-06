"""Small Windows helpers: the clipboard, the Downloads folder, a folder
picker, showing a file in Explorer, and a job object so that stopping a
download also stops everything yt-dlp started (ffmpeg, its own child)."""

import ctypes
import os
import subprocess
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

# ---- clipboard

u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.CloseClipboard.restype = wintypes.BOOL
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]


def clipboard_text():
    """The clipboard's text, or "" (only read when the paste button is
    clicked)."""
    if not u32.OpenClipboard(None):
        return ""
    try:
        handle = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k32.GlobalUnlock(handle)
    finally:
        u32.CloseClipboard()


# ---- folders

class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


def downloads_folder():
    """The user's Downloads folder (wherever they moved it)."""
    try:
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        guid = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        path = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            value = path.value
            ole32.CoTaskMemFree(path)
            if value:
                return value
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


def pick_folder(start):
    """Shows Windows' folder picker; returns the folder or None. Blocks, so
    call it from a thread."""
    script = os.path.join(HERE, "pick_folder.ps1")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", script,
                              "-Start", start or ""],
                             capture_output=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = out.stdout.decode("utf-8", "replace").strip()
    return path if path and os.path.isdir(path) else None


def show_in_folder(path):
    """Opens Explorer with the file selected."""
    if path and os.path.exists(path):
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif path and os.path.isdir(os.path.dirname(path)):
        os.startfile(os.path.dirname(path))


# ---- job object: kill the whole process tree

class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _Extended(ctypes.Structure):
    _fields_ = [("Basic", _Basic), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
k32.CreateJobObjectW.restype = wintypes.HANDLE
k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
ntdll = ctypes.WinDLL("ntdll")
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]


class ProcessTree:
    """Starts a command in a job object that dies with this plugin, so no
    yt-dlp or ffmpeg is left running after a cancel, a crash or Fenn quitting."""

    def __init__(self, args, env, cwd=None):
        self.job = k32.CreateJobObjectW(None, None)
        info = _Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info))
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=env, cwd=cwd, creationflags=NO_WINDOW | CREATE_SUSPENDED)
        # Started suspended so that it's in the job before it can start anything.
        k32.AssignProcessToJobObject(self.job, int(self.proc._handle))
        ntdll.NtResumeProcess(int(self.proc._handle))

    def kill(self):
        if self.job:
            k32.TerminateJobObject(self.job, 1)

    def close(self):
        if self.job:
            k32.CloseHandle(self.job)
            self.job = None
