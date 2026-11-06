# Shows Windows' folder picker (the modern one) on top of everything and
# prints the chosen folder, or nothing when cancelled. Used by Grab's
# "Select..." buttons.
param([string]$Start = "")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class GrabPicker {
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    class FileOpenDialog {}

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileDialog {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint count, IntPtr types);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetResult(out IShellItem item);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr pbc,
        [MarshalAs(UnmanagedType.LPStruct)] Guid riid, out IShellItem item);

    public static string Pick(string start, IntPtr owner) {
        IFileDialog dialog = (IFileDialog)new FileOpenDialog();
        dialog.SetOptions(0x20 | 0x40 | 0x800);  // pick folders, file system only, path must exist
        dialog.SetTitle("Where should Grab save downloads?");
        dialog.SetOkButtonLabel("Use this folder");
        if (!String.IsNullOrEmpty(start) && System.IO.Directory.Exists(start)) {
            try {
                IShellItem folder;
                SHCreateItemFromParsingName(start, IntPtr.Zero, typeof(IShellItem).GUID, out folder);
                dialog.SetFolder(folder);
            } catch (Exception) {}
        }
        if (dialog.Show(owner) != 0) return "";
        IShellItem result;
        dialog.GetResult(out result);
        string path;
        result.GetDisplayName(0x80058000, out path);  // SIGDN_FILESYSPATH
        return path;
    }
}
"@

Add-Type -AssemblyName System.Windows.Forms
# An invisible top-most owner, so the picker opens in front of Fenn.
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = "None"
$owner.Opacity = 0
# The picker opens at its owner's top-left corner: put that where a centered picker starts.
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$owner.StartPosition = "Manual"
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Location = New-Object System.Drawing.Point(([int]($area.X + $area.Width / 2 - 470)), ([int]($area.Y + $area.Height / 2 - 290)))
$owner.Show()
$owner.Activate()
try {
    $path = [GrabPicker]::Pick($Start, $owner.Handle)
    if ($path) { [Console]::Out.Write($path) }
} finally {
    $owner.Close()
}
