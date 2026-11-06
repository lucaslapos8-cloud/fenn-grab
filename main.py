"""Grab: download video and audio from YouTube and other sites with yt-dlp,
from a Fenn tile and page. Paste a link, pick video or audio, the file type
and quality, and Grab queues it, shows the progress, and keeps a history."""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import draw as d  # noqa: E402
import fenn  # noqa: E402
import winutil  # noqa: E402
import ytdlp  # noqa: E402

app = fenn.Plugin()

DEFAULTS = {
    "folder": None,            # None: the Downloads folder
    "kind": "video",           # what the Add page starts with (the last choice)
    "vext": "mp4", "vq": 0,    # video file type, height (0 = best)
    "aext": "mp3", "aq": 0,    # audio file type, kbps (0 = best)
    "subs": False, "thumb": True, "meta": True, "chapters": False, "sponsor": False,
    "parallel": 2,             # downloads at once
    "limit": 0,                # MB/s, 0 = no limit
    "cookies": None,           # browser to borrow the sign-in from
    "sub_langs": "en.*",
    "playlist_folder": True,
    "keep_date": False,
    "island": True,            # progress ring on Fenn's island while downloading
    "color": "fenn",           # "fenn" (its accent) or "#RRGGBB"
}
COLORS = ["fenn", "#9D45B0", "#0A84FF", "#5E5CE6", "#FF2D55", "#FF9F0A", "#30D158", "#64D2FF", "#FF453A"]
VEXT = [("MP4", "mp4"), ("WebM", "webm"), ("MKV", "mkv")]
AEXT = [("MP3", "mp3"), ("M4A", "m4a"), ("Opus", "opus"), ("FLAC", "flac"), ("WAV", "wav")]
AQ = [("Best", 0), ("320 kbps", 320), ("192 kbps", 192), ("128 kbps", 128)]
TAKE = [10, 25, 50, 100]  # "How many" for long playlists
LIMITS = [("No limit", 0), ("1 MB/s", 1), ("5 MB/s", 5), ("10 MB/s", 10)]
BROWSERS = [("Off", None), ("Firefox", "firefox"), ("Edge", "edge"), ("Chrome", "chrome"), ("Brave", "brave")]
SITES = "https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"
SIDE_W = 150
GAP = 12
SHOWN = 60  # list rows drawn at most (the rest are counted)

settings = dict(DEFAULTS)
settings.update(app.load("settings", {}) or {})
jobs = app.load("jobs", []) or []
history = app.load("history", []) or []
for _j in jobs:
    if _j.get("status") == "downloading":  # Fenn or the PC stopped mid-download: carry on
        _j["status"] = "queued"
    _j["progress"] = _j.get("progress") or 0.0

tools = ytdlp.Tools(app.data_dir)
view = "home"
add = None
gen = int(time.time()) % 100000      # fresh input ids, so Fenn shows the new text
page_w, page_h = 656, 430
tile = ("wide", None, None)
canvases = {}
dirty = False
clear_armed = 0.0
notice = ""
busy_tool = False
badge = ("stale",)  # unknown: the first update clears whatever an older run left on the island
thumb_q = queue.Queue()


def folder():
    return settings["folder"] or winutil.downloads_folder()


def save_settings():
    app.save("settings", settings)


def save_jobs():
    app.save("jobs", [{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs])


def save_history():
    app.save("history", history[:500])


def mark():
    global dirty
    dirty = True


def next_gen():
    global gen
    gen += 1
    return gen


def find(jid):
    return next((j for j in jobs if j["id"] == jid), None)


def counts():
    n = {"downloading": 0, "queued": 0, "completed": 0}
    for j in jobs:
        s = j["status"]
        if s in ("downloading", "paused"):
            n["downloading"] += 1
        elif s == "queued":
            n["queued"] += 1
        else:
            n["completed"] += 1
    return n


# ---- formatting

def dur(s):
    if not s:
        return ""
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def compact(n):
    if n is None:
        return ""
    for div, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + s
    return str(n)


def ago(t):
    if not t:
        return ""
    s = time.time() - t
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    lt = time.localtime(t)
    return time.strftime("%d %b", lt).lstrip("0")


def height_label(h):
    return {4320: "8K", 2160: "4K"}.get(h, f"{h}p")


# ---- thumbnails, one at a time in the background

def thumb_worker():
    while True:
        target, url = thumb_q.get()
        try:
            path = ytdlp.fetch_thumb(tools, url, os.path.join(app.data_dir, "thumbs"))
        except Exception:
            path = None
        if path:
            target["thumb"] = path
            mark()


threading.Thread(target=thumb_worker, daemon=True).start()
for _j in jobs:
    if not _j.get("thumb") and _j.get("thumb_url"):
        thumb_q.put((_j, _j["thumb_url"]))


# ---- the queue

def new_job(url, title, uploader, duration, thumb_url, thumb, name, folder_, a, playlist=None):
    kind = a["kind"]
    return {
        "id": uuid.uuid4().hex[:12], "url": url, "title": title, "uploader": uploader or "",
        "duration": duration, "thumb": thumb, "thumb_url": thumb_url, "kind": kind,
        "ext": a["vext"] if kind == "video" else a["aext"], "quality": a["vq"] if kind == "video" else a["aq"],
        "folder": folder_, "name": name or title, "playlist": playlist,
        "opts": {"subs": a["subs"], "thumb": a["thumb"], "meta": a["meta"], "chapters": a["chapters"],
                 "sponsor": a["sponsor"], "trim_from": a["trim_from"].strip(), "trim_to": a["trim_to"].strip()},
        "status": "queued", "progress": 0.0, "stage": "", "speed": None, "eta": None, "size": None,
        "file": None, "error": "", "added": time.time(), "finished": None,
    }


def pump():
    """Starts queued jobs while there's room."""
    if not tools.cmd:
        return
    active = sum(1 for j in jobs if j["status"] == "downloading")
    started = False
    for j in jobs:
        if active >= settings["parallel"]:
            break
        if j["status"] == "queued":
            begin(j)
            active += 1
            started = True
    if started:
        save_jobs()


def begin(j):
    try:
        ytdlp.assign_name(j)
    except OSError as e:
        j.update(status="failed", error=f"Can't use the folder: {e}", finished=time.time())
        return
    j.update(status="downloading", stage="Starting", error="", speed=None, eta=None, _stop=None)
    threading.Thread(target=worker, args=(j,), daemon=True).start()


def worker(j):
    try:
        code = ytdlp.run(tools, j, settings, app.data_dir, mark)
    except Exception as e:
        code = -1
        j["error"] = f"{type(e).__name__}: {e}"
    app._inbox.put({"type": "job_end", "id": j["id"], "code": code})


@app.on_message("job_end")
def on_job_end(msg):
    j = find(msg["id"])
    if not j:
        return
    stop = j.pop("_stop", None)
    j.update(speed=None, eta=None)
    if stop == "pause":
        j["status"] = "paused"
    elif stop in ("cancel", "remove"):
        ytdlp.cleanup_partial(j)
        j["status"] = "cancelled"
        if stop == "remove":
            jobs.remove(j)
    elif msg["code"] == 0:
        f = j.get("file")
        j.update(status="completed", progress=1.0, stage="", finished=time.time(),
                 size=os.path.getsize(f) if f and os.path.exists(f) else None)
        history.insert(0, {k: j.get(k) for k in ("id", "url", "title", "uploader", "duration", "thumb", "thumb_url",
                                                  "kind", "ext", "file", "size", "finished")})
        save_history()
    else:
        ytdlp.cleanup_partial(j)
        j.update(status="failed", finished=time.time(),
                 error=j.get("error") or f"yt-dlp stopped (code {msg['code']})")
    save_jobs()
    pump()
    draw()


def stop_job(j, how):
    j["_stop"] = how
    proc = j.get("_proc")
    if proc:
        proc.kill()


# ---- the Add page

def open_add(url=""):
    global view, add
    add = {"url": url, "gen": next_gen(), "busy": False, "info": None, "error": "", "thumb": None, "probe": 0,
           "kind": settings["kind"], "vext": settings["vext"], "vq": settings["vq"], "aext": settings["aext"],
           "aq": settings["aq"], "folder": folder(), "name": "", "name_gen": next_gen(), "extra": False,
           "subs": settings["subs"], "thumb": settings["thumb"], "meta": settings["meta"],
           "chapters": settings["chapters"], "sponsor": settings["sponsor"], "trim_from": "", "trim_to": "",
           "whole": False, "go": False}
    add["thumb_on"] = add.pop("thumb")
    add["thumb"] = None
    view = "add"
    if url:
        lookup()


def opts_of(a):
    """The Add page's choices in new_job's shape."""
    return {"kind": a["kind"], "vext": a["vext"], "vq": a["vq"], "aext": a["aext"], "aq": a["aq"],
            "subs": a["subs"], "thumb": a["thumb_on"], "meta": a["meta"], "chapters": a["chapters"],
            "sponsor": a["sponsor"], "trim_from": a["trim_from"], "trim_to": a["trim_to"]}


def lookup(go=False):
    a = add
    url = a["url"].strip()
    if not url:
        a["error"] = "Paste or type a link first."
        return
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    if not tools.cmd:
        a["error"] = "yt-dlp isn't installed. Install it from Home."
        return
    a.update(url=url, busy=True, error="", info=None, thumb=None, go=go)
    a["probe"] += 1
    token, whole = a["probe"], a["whole"]

    def work():
        try:
            info = ytdlp.probe(tools, url, whole, settings["cookies"])
            try:
                thumb = ytdlp.fetch_thumb(tools, info.get("thumb_url"), os.path.join(app.data_dir, "thumbs"))
            except Exception:
                thumb = None
            app._inbox.put({"type": "probe", "token": token, "info": info, "thumb": thumb})
        except Exception as e:
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            app._inbox.put({"type": "probe", "token": token, "error": msg or "That link didn't work."})

    threading.Thread(target=work, daemon=True).start()


@app.on_message("probe")
def on_probe(msg):
    a = add
    if not a or msg["token"] != a["probe"]:
        return
    a["busy"] = False
    if msg.get("error"):
        a["error"] = msg["error"]
        a["go"] = False
    else:
        info = msg["info"]
        a.update(info=info, thumb=msg.get("thumb"), name=info["title"], name_gen=next_gen())
        if info["type"] == "playlist":
            a["take"] = 0 if info["count"] <= 50 else 25
        if info["type"] == "video" and not info["video"]:
            a["kind"] = "audio"
        if a["go"]:
            start_download()
            return
    draw()


def start_download():
    global view, add
    a = add
    info = a["info"]
    if not info:
        if a["url"].strip():
            lookup(go=True)
        else:
            a["error"] = "Paste or type a link first."
        draw()
        return
    for k in ("kind", "vext", "vq", "aext", "aq", "subs", "meta", "chapters", "sponsor"):
        settings[k] = a[k]
    settings["thumb"] = a["thumb_on"]
    save_settings()
    o = opts_of(a)
    if info["type"] == "playlist":
        dest = a["folder"]
        if settings["playlist_folder"]:
            dest = os.path.join(dest, ytdlp.safe_name(info["title"]).replace("%%", "%"))
        for e in info["entries"][:a.get("take") or None]:
            j = new_job(e["url"], e["title"], e["uploader"] or info["uploader"], e["duration"], e["thumb_url"],
                        None, e["title"], dest, o, playlist=info["title"])
            jobs.append(j)
            if e["thumb_url"]:
                thumb_q.put((j, e["thumb_url"]))
    else:
        j = new_job(info["url"], info["title"], info["uploader"], info["duration"], info["thumb_url"], a["thumb"],
                    a["name"].strip() or info["title"], a["folder"], o)
        jobs.append(j)
    save_jobs()
    add = None
    view = "downloading" if any(j["status"] in ("downloading", "paused") for j in jobs) or \
        sum(1 for j in jobs if j["status"] == "downloading") < settings["parallel"] else "queued"
    pump()
    draw()


def pick_folder(target):
    start = add["folder"] if target == "add" and add else folder()

    def work():
        path = winutil.pick_folder(start)
        app._inbox.put({"type": "folder", "target": target, "path": path})

    threading.Thread(target=work, daemon=True).start()


@app.on_message("folder")
def on_folder(msg):
    if not msg.get("path"):
        return
    if msg["target"] == "add" and add:
        add["folder"] = msg["path"]
    elif msg["target"] == "settings":
        settings["folder"] = msg["path"]
        save_settings()
    draw()


# ---- yt-dlp itself

def tool_job(args, done_text):
    """Runs winget or yt-dlp -U in the background and reports how it went."""
    global busy_tool, notice
    busy_tool = True
    notice = "Working… this can take a minute."

    def work():
        try:
            out = subprocess.run(args, capture_output=True, creationflags=winutil.NO_WINDOW, stdin=subprocess.DEVNULL, timeout=900)
            text = (out.stdout + out.stderr).decode("utf-8", "replace")
            ok = out.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            text, ok = str(e), False
        app._inbox.put({"type": "tool_done", "ok": ok, "text": text, "done": done_text})

    threading.Thread(target=work, daemon=True).start()


@app.on_message("tool_done")
def on_tool_done(msg):
    global tools, busy_tool, notice
    busy_tool = False
    tools = ytdlp.Tools(app.data_dir)
    threading.Thread(target=read_version, daemon=True).start()
    if msg["ok"]:
        notice = msg["done"]
    else:
        lines = [ln.strip() for ln in msg["text"].splitlines() if ln.strip() and not set(ln.strip()) <= set("-\\|/ ")]
        notice = "Didn't work: " + (lines[-1][:160] if lines else "no output")
    pump()
    draw()


def read_version():
    tools.read_version()
    mark()


def install_tools():
    tool_job(["winget", "install", "--id", "yt-dlp.yt-dlp", "-e", "--source", "winget", "--disable-interactivity"],
             "yt-dlp is installed.")


def update_tools():
    if tools.via_winget:
        tool_job(["winget", "upgrade", "--id", "yt-dlp.yt-dlp", "-e", "--source", "winget",
                  "--disable-interactivity"], "yt-dlp is up to date.")
    elif tools.cmd and tools.exe:
        tool_job(tools.cmd + ["-U"], "yt-dlp is up to date.")
    elif tools.cmd:
        tool_job([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], "yt-dlp is up to date.")


# ---- views

def content_w():
    """The content column's width, less room for its scrollbar."""
    return max(300, page_w - SIDE_W - GAP - 12)


def sidebar():
    c = d.sidebar(SIDE_W, page_h, view if view != "add" else "", counts(), app.dark)
    canvases["side"] = c
    return c.node("side")


def section(title, sub=None):
    parts = [fenn.text(title, bold=True)]
    if sub:
        parts.append(fenn.text(sub, size="small", color="dim", wrap=True))
    return fenn.column(parts, gap=1)


def heading(title, items, target):
    c = d.heading(content_w(), title, len(items), "nav:" + target)
    canvases["head-" + target] = c
    return c.node("head-" + target)


def home_view():
    """The Add button, the counters, and what's going on right now."""
    missing = None if tools.cmd else "yt-dlp isn't installed yet."
    c = d.home_top(content_w(), counts(), app.dark, missing)
    canvases["home"] = c
    parts = [c.node("home")]
    if missing:
        parts.append(fenn.text(notice or "Grab downloads with yt-dlp. The button installs it, with ffmpeg and Deno, "
                                         "from winget (about 150 MB).", size="small", color="dim", wrap=True))
        return fenn.column(parts, gap=8, scroll=True, id="home")
    active = [j for j in jobs if j["status"] in ("downloading", "paused")]
    waiting = [j for j in jobs if j["status"] == "queued"]
    done = sorted((j for j in jobs if j["status"] in ("completed", "failed", "cancelled")),
                  key=lambda j: -(j.get("finished") or 0))
    if active:
        parts += [heading("Downloading now", active, "downloading")] + [job_card(j) for j in active[:3]]
    if waiting:
        parts += [heading("Up next", waiting, "queued")] + [job_card(j, i + 1) for i, j in enumerate(waiting[:3])]
    if done:
        parts += [heading("Recently finished", done, "completed")] + [job_card(j) for j in done[:3]]
    if not (active or waiting or done):
        parts.append(fenn.text("Paste a link from YouTube or another supported site to start. What's downloading, "
                               "waiting and finished shows up here.", color="dim", wrap=True))
    return fenn.column(parts, gap=8, scroll=True, id="home")


STATUS = {
    "queued": ("clock", d.ORANGE), "downloading": ("arrow-circle-down", d.BLUE), "paused": ("pause-circle", d.ORANGE),
    "completed": ("check-circle", d.GREEN), "failed": ("warning-circle", d.RED), "cancelled": ("x-circle", "dim"),
}


def status_line(j, place=None):
    s = j["status"]
    pct = f"{round((j.get('progress') or 0) * 100)}%"
    if s == "downloading":
        bits = [j.get("stage") or "Downloading", pct]
        if j.get("speed"):
            bits.append(size(j["speed"]) + "/s")
        if j.get("eta"):
            bits.append(dur(j["eta"]) + " left")
        return " · ".join(bits)
    if s == "paused":
        return f"Paused · {pct}"
    if s == "queued":
        bits = ["Queued"]
        if place:
            bits.append(f"{place}. in line")
        if j.get("playlist"):
            bits.append(j["playlist"])
        return " · ".join(bits)
    if s == "completed":
        f = j.get("file")
        if f and not os.path.exists(f):
            return "Completed · the file was moved or deleted"
        return " · ".join(x for x in ["Completed", dur(j.get("duration")), size(j.get("size")),
                                       (j.get("ext") or "").upper()] if x)
    if s == "failed":
        return "Failed: " + (j.get("error") or "unknown error")
    return "Cancelled"


def row_card(thumb, kind, title, line, icon, color, buttons, progress=None):
    cw = content_w()
    room = cw - 20 - 96 - 10 - 4 * 28 - 6
    if len(buttons) < 4:  # right-align the buttons, so rows line up
        buttons = [fenn.canvas(1, [], width=(4 - len(buttons)) * 28 - 2)] + buttons
    img = fenn.image(thumb, width=96, height=54, rounding=6) if thumb and os.path.exists(thumb) else \
        d.thumb_placeholder(96, 54, kind, app.dark)
    col = [fenn.text(d.fit(title, 13, room, True), bold=True),
           fenn.row([fenn.icon(icon, size=12, color=color, fill=True),
                     fenn.text(d.fit(line, 11, room - 18), size="small", color="dim")], gap=4)]
    if progress is not None:
        col.append(fenn.progress(progress, color=color if color.startswith("#") else None, width=room))
    col.append(fenn.canvas(1, [], width=room))  # holds the column at full width, so buttons line up
    return fenn.card([fenn.row([img, fenn.column(col, gap=3), fenn.row(buttons, gap=2)],
                               gap=10)], padding=10)


def job_card(j, place=None):
    s, jid = j["status"], j["id"]
    b = []
    if s == "downloading":
        b = [fenn.button(f"pause:{jid}", icon="pause", tooltip="Pause"),
             fenn.button(f"cancel:{jid}", icon="x", tooltip="Cancel and delete the partial file")]
    elif s == "paused":
        b = [fenn.button(f"resume:{jid}", icon="play", tooltip="Resume"),
             fenn.button(f"cancel:{jid}", icon="x", tooltip="Cancel and delete the partial file")]
    elif s == "queued":
        if place and place > 1:
            b.append(fenn.button(f"up:{jid}", icon="arrow-up", tooltip="Move up"))
        b.append(fenn.button(f"start:{jid}", icon="play", tooltip="Start now"))
        b.append(fenn.button(f"drop:{jid}", icon="x", tooltip="Remove from the queue"))
    elif s == "completed":
        b = [fenn.button(f"play:{jid}", icon="play-circle", tooltip="Open"),
             fenn.button(f"show:{jid}", icon="folder-open", tooltip="Show in folder"),
             fenn.button(f"log:{jid}", icon="file-text", tooltip="yt-dlp's log"),
             fenn.button(f"drop:{jid}", icon="x", tooltip="Remove from the list (keeps the file)")]
    else:
        b = [fenn.button(f"retry:{jid}", icon="arrow-clockwise", tooltip="Try again"),
             fenn.button(f"log:{jid}", icon="file-text", tooltip="yt-dlp's log"),
             fenn.button(f"drop:{jid}", icon="x", tooltip="Remove from the list")]
    icon, color = STATUS[s]
    prog = j.get("progress") or 0 if s in ("downloading", "paused") else None
    return row_card(j.get("thumb"), j["kind"], j["title"], status_line(j, place), icon, color, b, prog)


def list_view(items, empty_icon, empty_title, empty_line, card_fn):
    if not items:
        c = d.empty(content_w(), page_h - 20, empty_icon, empty_title, empty_line)
        canvases["empty"] = c
        return c.node("empty")
    rows = [card_fn(it) for it in items[:SHOWN]]
    if len(items) > SHOWN:
        rows.append(fenn.text(f"and {len(items) - SHOWN} more", size="small", color="dim"))
    return fenn.column(rows, gap=8, scroll=True, id="list-" + view)


def downloading_view():
    items = [j for j in jobs if j["status"] in ("downloading", "paused")]
    return list_view(items, "arrow-circle-down", "Nothing downloading", "Downloads you add show up here.", job_card)


def queued_view():
    items = [j for j in jobs if j["status"] == "queued"]
    place = {j["id"]: i + 1 for i, j in enumerate(items)}
    return list_view(items, "clock", "The queue is empty",
                     f"Up to {settings['parallel']} download{'s' if settings['parallel'] > 1 else ''} run at once; "
                     "the rest wait here.", lambda j: job_card(j, place[j["id"]]))


def completed_view():
    items = sorted((j for j in jobs if j["status"] in ("completed", "failed", "cancelled")),
                   key=lambda j: -(j.get("finished") or 0))
    return list_view(items, "check-circle", "Nothing finished yet", "Finished downloads show up here.", job_card)


def history_card(h):
    hid = h["id"]
    f = h.get("file")
    b = []
    if f and os.path.exists(f):
        b.append(fenn.button(f"hplay:{hid}", icon="play-circle", tooltip="Open"))
    b += [fenn.button(f"again:{hid}", icon="arrow-clockwise", tooltip="Download again"),
          fenn.button(f"hcopy:{hid}", icon="link", tooltip="Copy the link"),
          fenn.button(f"hdrop:{hid}", icon="x", tooltip="Remove from history")]
    line = " · ".join(x for x in [ago(h.get("finished")), (h.get("ext") or "").upper(), size(h.get("size")),
                                  h.get("uploader")] if x)
    return row_card(h.get("thumb"), h.get("kind"), h["title"], line, "clock-counter-clockwise", "dim", b)


def history_view():
    return list_view(history, "clock-counter-clockwise", "No history yet", "Everything you download is listed here.",
                     history_card)


def info_card(a):
    info = a["info"]
    cw = content_w()
    tw = 160 if cw > 420 else 128
    th = round(tw * 9 / 16)
    img = fenn.image(a["thumb"], width=tw, height=th, rounding=8) if a.get("thumb") else \
        d.thumb_placeholder(tw, th, a["kind"], app.dark)
    room = cw - 24 - tw - 12
    lines = [fenn.text(t, bold=True) for t in d.wrap(info["title"], 13.5, room, 2, True)]
    if info["type"] == "playlist":
        lines.append(fenn.text(d.fit(info.get("uploader") or "", 12, room), color="dim"))
        lines.append(fenn.row([fenn.icon("playlist", size=13, color="dim"),
                               fenn.text(f"Playlist · {info['count']} item{'s' if info['count'] != 1 else ''}",
                                         size="small", color="dim")], gap=4))
    else:
        if info.get("uploader"):
            lines.append(fenn.text(d.fit(info["uploader"], 12, room), color="dim"))
        meta = []
        if info.get("duration"):
            meta += [fenn.icon("clock", size=13, color="dim"), fenn.text(dur(info["duration"]), size="small",
                                                                         color="dim")]
        if info.get("views"):
            meta += [fenn.icon("eye", size=13, color="dim"), fenn.text(compact(info["views"]), size="small",
                                                                       color="dim")]
        if meta:
            lines.append(fenn.row(meta, gap=4))
    return fenn.card([fenn.row([img, fenn.column(lines, gap=3)], gap=12)], padding=12)


def labeled(label, control):
    return fenn.row([fenn.text(label, color="dim"), control], gap=10)


def add_view():
    a = add
    cw = content_w()
    parts = [fenn.text("Video / Playlist URL", bold=True),
             fenn.row([fenn.text_input(f"url{a['gen']}", a["url"], hint="Paste a link from YouTube, Vimeo, SoundCloud…",
                                       width=cw - 84, live=True),
                       fenn.button("paste", icon="clipboard-text", tooltip="Paste the link from the clipboard"),
                       fenn.button("lookup", icon="arrow-right", tooltip="Look it up")], gap=6)]
    if a["busy"]:
        parts.append(fenn.row([fenn.icon("hourglass-medium", size=14, color="dim"),
                               fenn.text("Looking up the link…", color="dim")], gap=6))
    elif a["error"]:
        parts.append(fenn.row([fenn.icon("warning-circle", size=14, color=d.RED, fill=True),
                               fenn.text(a["error"], size="small", color=d.RED, wrap=True)], gap=6))
    info = a["info"]
    if info:
        parts.append(info_card(a))
        if (info["type"] == "video" and info.get("in_list")) or (info["type"] == "playlist" and "v=" in a["url"]):
            parts.append(fenn.toggle("whole", a["whole"], label="Download the whole playlist"))
        if info["type"] == "playlist" and info["count"] > 10:
            opts = [0] + [t for t in TAKE if t < info["count"]]
            parts.append(labeled("How many", fenn.chips("take", [f"All {info['count']}"] + [f"First {t}" for t in opts[1:]],
                                                         opts.index(a.get("take", 0)) if a.get("take", 0) in opts else 0)))
            a["_take"] = opts
    can_video = not info or info["type"] == "playlist" or info.get("video")
    parts.append(fenn.text("Format", bold=True))
    kinds = ["Video", "Audio"] if can_video else ["Audio"]
    parts.append(fenn.chips("kind", kinds, kinds.index(a["kind"].title()) if a["kind"].title() in kinds else 0))
    if a["kind"] == "video":
        parts.append(labeled("File type", fenn.chips("vext", [x for x, _ in VEXT],
                                                      [v for _, v in VEXT].index(a["vext"]))))
        hs = [h for h in ytdlp.HEIGHTS if (h <= 2160 if not info or info["type"] == "playlist" else
                                           any(x >= h * 0.9 for x in info.get("heights") or []))]
        hs = [h for h in hs if h >= 480 or info and info["type"] == "video" and
              max(info.get("heights") or [0]) < 480][:5] or hs[-1:]
        a["_heights"] = hs
        if a["vq"] and a["vq"] not in hs:
            a["vq"] = next((h for h in hs if h <= a["vq"]), 0)
        opts = [0] + hs
        parts.append(labeled("Quality", fenn.chips("vq", ["Best"] + [height_label(h) for h in hs],
                                                    opts.index(a["vq"]) if a["vq"] in opts else 0)))
    else:
        parts.append(labeled("File type", fenn.chips("aext", [x for x, _ in AEXT],
                                                      [v for _, v in AEXT].index(a["aext"]))))
        if a["aext"] in ("mp3", "m4a", "opus"):
            parts.append(labeled("Quality", fenn.chips("aq", [x for x, _ in AQ],
                                                        [v for _, v in AQ].index(a["aq"]) if a["aq"] in
                                                        [v for _, v in AQ] else 0)))
    parts.append(fenn.text("Save to", bold=True))
    parts.append(fenn.row([fenn.button("pick_add", label="Select…", icon="folder-open"),
                           fenn.text(d.fit(a["folder"], 12.5, cw - 120), color="dim")], gap=10))
    if not info or info["type"] == "video":
        parts.append(fenn.text("File name", bold=True))
        parts.append(fenn.text_input(f"name{a['name_gen']}", a["name"], hint="The video's title", width=cw - 4, live=True))
    parts.append(fenn.button("extra", label="Extra settings", icon="caret-down" if a["extra"] else "caret-right"))
    if a["extra"]:
        ex = []
        if a["kind"] == "video":
            ex.append(fenn.toggle("o_subs", a["subs"], label=f"Download subtitles ({settings['sub_langs']})"))
        ex += [fenn.toggle("o_thumb", a["thumb_on"], label="Embed thumbnail"),
               fenn.toggle("o_meta", a["meta"], label="Embed metadata (title, artist, etc.)"),
               fenn.toggle("o_chapters", a["chapters"], label="Embed chapters"),
               fenn.toggle("o_sponsor", a["sponsor"], label="Cut out sponsor segments (SponsorBlock)"),
               fenn.row([fenn.text("Only from", color="dim"),
                         fenn.text_input(f"tfrom{a['gen']}", a["trim_from"], hint="0:00", width=70, live=True),
                         fenn.text("to", color="dim"),
                         fenn.text_input(f"tto{a['gen']}", a["trim_to"], hint="end", width=70, live=True)],
                        gap=8)]
        parts.append(fenn.card([fenn.column(ex, gap=8)], padding=12))
    n = min(info["count"], a.get("take") or info["count"]) if info and info["type"] == "playlist" else 0
    parts.append(fenn.row([fenn.button("download", label=f"Download {n}" if n else "Download", icon="download-simple",
                                       primary=True),
                           fenn.button("cancel_add", label="Cancel")], gap=8))
    return fenn.column(parts, gap=8, scroll=True, id=f"add{a['gen']}")


def swatch_row():
    current = settings["color"]
    choices = COLORS + ([current] if current not in COLORS else [])
    c = d.swatches(content_w(), choices, current, hex6(app.accent) or "#9D45B0")
    canvases["swatch"] = c
    return c.node("swatch")


def settings_view():
    cw = content_w()
    tool_lines = []
    if tools.cmd:
        tool_lines.append(f"yt-dlp {tools.version or '…'}" + (" (winget)" if tools.via_winget else ""))
    else:
        tool_lines.append("yt-dlp isn't installed.")
    tool_lines.append("ffmpeg: found" if tools.ffmpeg else
                      "ffmpeg: missing. Videos above 720p and audio conversion need it.")
    tool_lines.append("Deno: found" if tools.deno else "Deno: missing. YouTube may only offer some formats.")
    parts = [
        section("Downloads"),
        labeled("Save to", fenn.text(d.fit(folder(), 12.5, cw - 150), color="dim")),
        fenn.button("pick_settings", label="Select…"),
        labeled("At once", fenn.chips("parallel", ["1", "2", "3", "4"], settings["parallel"] - 1)),
        labeled("Speed limit", fenn.chips("limit", [x for x, _ in LIMITS],
                                          [v for _, v in LIMITS].index(settings["limit"])
                                          if settings["limit"] in [v for _, v in LIMITS] else 0)),
        fenn.toggle("playlist_folder", settings["playlist_folder"], label="Put each playlist in its own folder"),
        fenn.toggle("keep_date", settings["keep_date"], label="Date files by upload, not by download"),
        fenn.toggle("island", settings["island"], label="Show the progress on Fenn's island while downloading"),
        fenn.divider(),
        section("Color", "Grab follows Fenn's accent color, or pick your own."),
        swatch_row(),
        fenn.row([fenn.text("Your own", color="dim"),
                  fenn.text_input(f"hex{gen}", settings["color"] if settings["color"] != "fenn" else "",
                                  hint="#RRGGBB", width=110),
                  fenn.text("then Enter", size="small", color="faint")], gap=10),
        fenn.divider(),
        section("Sign-in", "For age-restricted, members-only or private videos, yt-dlp can use your sign-in "
                           "from a browser on this PC."),
        fenn.chips("cookies", [x for x, _ in BROWSERS], [v for _, v in BROWSERS].index(settings["cookies"])
                   if settings["cookies"] in [v for _, v in BROWSERS] else 0),
        fenn.text("Firefox works best. Chrome and Edge must be closed, and newer versions may refuse.",
                  size="small", color="faint", wrap=True),
        fenn.divider(),
        section("Subtitles", "Languages to download, e.g. en.*, de, sk, or all"),
        fenn.text_input(f"langs{gen}", settings["sub_langs"], hint="en.*", width=cw - 4, live=True),
        fenn.divider(),
        section("yt-dlp"),
        *[fenn.text(t, size="small", color="dim") for t in tool_lines],
        fenn.row([fenn.button("update", label="Update yt-dlp" if tools.cmd else "Install yt-dlp",
                              icon="arrow-clockwise" if tools.cmd else "download-simple"),
                  fenn.button("sites", label="Supported sites", icon="globe")], gap=8),
    ]
    if notice:
        parts.append(fenn.text(notice, size="small", color="dim", wrap=True))
    return fenn.column(parts, gap=8, scroll=True, id="settings")


VIEWS = {"home": home_view, "downloading": downloading_view, "queued": queued_view, "completed": completed_view,
         "history": history_view, "settings": settings_view, "add": add_view}


def page_view():
    canvases.clear()
    body = VIEWS.get(view, home_view)() if (view != "add" or add) else home_view()
    return fenn.row([sidebar(), body], gap=GAP, align="start")


def actions():
    if view == "add":
        return [{"id": "cancel_add", "icon": "x", "tooltip": "Close"}]
    acts = [{"id": "new", "icon": "plus", "tooltip": "Add a new download"}]
    if view == "completed" and counts()["completed"]:
        acts.append({"id": "clear", "icon": "trash", "tooltip": "Clear finished downloads (keeps the files)"})
    if view == "history" and history:
        armed = time.time() - clear_armed < 5
        acts.append({"id": "clear", "icon": "warning" if armed else "trash",
                     "tooltip": "Click again to clear the history" if armed else "Clear the history"})
    return acts


batch_start = None  # when the queue last went from idle to busy


def overall():
    """Progress across everything since the queue last started: (fraction,
    finished, total), or (None, 0, 0) when idle."""
    global batch_start
    active = [j for j in jobs if j["status"] == "downloading"]
    if not active:
        batch_start = None
        return None, 0, 0
    if batch_start is None:
        batch_start = min(j.get("added") or time.time() for j in active) - 1
    waiting = sum(1 for j in jobs if j["status"] == "queued")
    done = sum(1 for j in jobs if j["status"] in ("completed", "failed") and (j.get("finished") or 0) >= batch_start)
    total = done + len(active) + waiting
    return (done + sum(j.get("progress") or 0 for j in active)) / total, done, total


def tile_view():
    p, done_n, total = overall()
    if p is None:
        done = sum(1 for j in jobs if j["status"] == "completed" and (j.get("finished") or 0) > time.time() - 3600)
        return fenn.column([fenn.icon("download-simple", size=22, color=d.ACCENT_HI),
                            fenn.text("Grab" if not done else f"{done} done", size="small", color="dim")],
                           gap=2, align="center", justify="center")
    return fenn.row([fenn.ring(p, size=30, color=d.ACCENT_HI),
                     fenn.column([fenn.text(f"{round(p * 100)}%", bold=True),
                                  fenn.text(f"{done_n} of {total}" if total > 1 else "Downloading", size="small",
                                            color="dim")], gap=0)],
                    gap=8)


page_sent = False


def update_badge():
    """A progress ring on Fenn's island while downloading; nothing otherwise."""
    global badge
    p = overall()[0]
    want = ("ring", round(p * 50)) if p is not None and settings["island"] else None
    if want == badge:
        return
    if want:
        app.badge_ring(p)
    else:
        app.clear_badge()
    badge = want


def hex6(c):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", str(c or "").strip())
    return "#" + m.group(1).upper() if m else None


def accent():
    """Fenn's accent color, or the user's own."""
    c = settings.get("color") or "fenn"
    return (hex6(app.accent) if c == "fenn" else hex6(c)) or "#9D45B0"


def draw():
    global page_sent, dirty
    dirty = False
    d.set_accent(accent())
    try:
        app.tile(tile_view())
    except Exception as e:
        app.log(f"tile: {type(e).__name__}: {e}", "error")
    update_badge()
    if app.page_open or not page_sent:
        app.page(page_view(), actions())
        page_sent = True


# ---- events

def on_canvas(cid, x, y):
    global view
    c = canvases.get(cid)
    act = c.hit(x, y) if c else None
    if not act:
        return
    if act.startswith("nav:"):
        view = act[4:]
    elif act == "add":
        open_add()
    elif act == "sites":
        app.open(SITES)
    elif act.startswith("color:"):
        settings["color"] = act[6:]
        save_settings()
    elif act == "install" and not busy_tool:
        install_tools()


def job_action(cmd, jid):
    global view
    j = find(jid)
    if cmd.startswith("h"):  # history rows
        h = next((x for x in history if x["id"] == jid), None)
        if not h:
            return
        if cmd == "hplay":
            app.open(h["file"])
        elif cmd == "hcopy":
            app.copy(h["url"])
        elif cmd == "hdrop":
            history.remove(h)
            save_history()
        return
    if cmd == "again":
        h = next((x for x in history if x["id"] == jid), None)
        if h:
            open_add(h["url"])
        return
    if not j:
        return
    if cmd == "pause":
        stop_job(j, "pause")
    elif cmd == "cancel":
        if j["status"] == "paused":
            ytdlp.cleanup_partial(j)
            j["status"] = "cancelled"
            j["finished"] = time.time()
            save_jobs()
        else:
            stop_job(j, "cancel")
    elif cmd == "resume" or cmd == "retry":
        if cmd == "retry":
            j.update(progress=0.0, error="", out_name=None)
        j["status"] = "queued"
        jobs.remove(j)
        first = next((i for i, x in enumerate(jobs) if x["status"] == "queued"), len(jobs))
        jobs.insert(first, j)
        save_jobs()
        pump()
    elif cmd == "start":
        begin(j)
        save_jobs()
    elif cmd == "up":
        q = [x for x in jobs if x["status"] == "queued"]
        i = q.index(j)
        if i > 0:
            a, b = jobs.index(q[i - 1]), jobs.index(j)
            jobs[a], jobs[b] = jobs[b], jobs[a]
            save_jobs()
    elif cmd == "drop":
        if j["status"] == "downloading":
            stop_job(j, "remove")
        else:
            jobs.remove(j)
            save_jobs()
    elif cmd == "play":
        if j.get("file") and os.path.exists(j["file"]):
            app.open(j["file"])
    elif cmd == "show":
        winutil.show_in_folder(j.get("file"))
    elif cmd == "log":
        p = os.path.join(app.data_dir, "logs", j["id"] + ".log")
        if os.path.exists(p):
            app.open(p)


@app.on_message("event")
def on_event(msg):
    global view, clear_armed, notice
    eid, kind, value = msg.get("id", ""), msg.get("kind"), msg.get("value")
    if eid in canvases and isinstance(value, dict) and "x" in value:
        on_canvas(eid, value["x"], value["y"])
        draw()
        return
    a = add
    if a and eid.startswith(("url", "name", "tfrom", "tto")) and kind in ("change", "submit"):
        key = re.match(r"[a-z]+", eid).group(0)
        a[{"url": "url", "name": "name", "tfrom": "trim_from", "tto": "trim_to"}[key]] = str(value or "")
        if key == "url" and kind == "submit":
            lookup()
            draw()
        return
    if eid.startswith("langs") and kind in ("change", "submit"):
        settings["sub_langs"] = str(value or "").strip() or "en.*"
        save_settings()
        return
    m = re.match(r"^(pause|cancel|resume|retry|start|up|drop|play|show|log|hplay|hcopy|hdrop|again):(\w+)$", eid)
    if m:
        job_action(m.group(1), m.group(2))
    elif eid == "new":
        open_add()
    elif eid == "cancel_add":
        globals()["add"] = None
        view = "home"
    elif eid == "paste" and a:
        text = winutil.clipboard_text().strip()
        found = re.search(r"https?://\S+", text)
        a["url"] = found.group(0) if found else text.split()[0] if text.split() else ""
        a["gen"] = next_gen()
        if a["url"]:
            lookup()
        else:
            a["error"] = "The clipboard has no link."
    elif eid == "lookup" and a:
        lookup()
    elif eid == "download" and a:
        start_download()
        return
    elif eid == "whole" and a:
        a["whole"] = bool(value)
        if a["url"]:
            lookup()
    elif eid == "kind" and a:
        a["kind"] = str(value.get("label", "Video")).lower()
    elif eid == "vext" and a:
        a["vext"] = VEXT[value["index"]][1]
    elif eid == "vq" and a:
        a["vq"] = 0 if value["index"] == 0 else a["_heights"][value["index"] - 1]
    elif eid == "aext" and a:
        a["aext"] = AEXT[value["index"]][1]
    elif eid == "aq" and a:
        a["aq"] = AQ[value["index"]][1]
    elif eid == "take" and a:
        a["take"] = a["_take"][value["index"]]
    elif eid == "extra" and a:
        a["extra"] = not a["extra"]
    elif eid.startswith("o_") and a:
        a[{"o_subs": "subs", "o_thumb": "thumb_on", "o_meta": "meta", "o_chapters": "chapters",
           "o_sponsor": "sponsor"}[eid]] = bool(value)
    elif eid == "pick_add":
        pick_folder("add")
    elif eid == "pick_settings":
        pick_folder("settings")
    elif eid == "parallel":
        settings["parallel"] = value["index"] + 1
        save_settings()
        pump()
    elif eid == "limit":
        settings["limit"] = LIMITS[value["index"]][1]
        save_settings()
    elif eid == "cookies":
        settings["cookies"] = BROWSERS[value["index"]][1]
        save_settings()
    elif eid.startswith("hex") and kind == "submit":
        text = str(value or "").strip()
        if hex6(text) and len(text.lstrip("#")) == 6:
            settings["color"] = hex6(text)
            save_settings()
        elif not text:
            settings["color"] = "fenn"
            save_settings()
        else:
            notice = "Type a color like #9D45B0."
        next_gen()
    elif eid in ("playlist_folder", "keep_date", "island"):
        settings[eid] = bool(value)
        save_settings()
    elif eid == "update" and not busy_tool:
        update_tools() if tools.cmd else install_tools()
    elif eid == "sites":
        app.open(SITES)
    elif eid == "clear":
        if view == "completed":
            jobs[:] = [j for j in jobs if j["status"] not in ("completed", "failed", "cancelled")]
            save_jobs()
            prune_thumbs()
        elif view == "history":
            if time.time() - clear_armed < 5:
                history.clear()
                save_history()
                clear_armed = 0.0
                prune_thumbs()
            else:
                clear_armed = time.time()
    draw()


def prune_thumbs():
    """Deletes thumbnails nothing shows any more."""
    keep = {os.path.normcase(x["thumb"]) for x in jobs + history if x.get("thumb")}
    if add and add.get("thumb"):
        keep.add(os.path.normcase(add["thumb"]))
    folder_ = os.path.join(app.data_dir, "thumbs")
    for f in os.listdir(folder_) if os.path.isdir(folder_) else []:
        p = os.path.join(folder_, f)
        if os.path.normcase(p) not in keep:
            try:
                os.remove(p)
            except OSError:
                pass


@app.on_message("layout")
def on_layout(msg):
    global page_w, page_h, tile
    if msg.get("target") == "page":
        page_w, page_h = int(msg.get("width") or page_w), int(msg.get("height") or page_h)
    elif msg.get("target") == "tile":
        tile = (msg.get("size") or "wide", msg.get("width"), msg.get("height"))
    draw()


@app.on_message("page_open")
def on_open(_):
    draw()


@app.on_message("page_close")
def on_close(_):
    global view, add
    if view == "add" and add and not add["busy"] and not add["info"]:
        add, view = None, "home"


@app.on_message("theme")
def on_theme(_):
    draw()


@app.on_message("hello")
def on_hello(_):
    draw()


# ---- timers

def tick():
    if dirty or any(j["status"] == "downloading" for j in jobs):
        draw()


_stamps = {f: os.path.getmtime(os.path.join(HERE, f)) for f in os.listdir(HERE) if f.endswith((".py", ".ps1"))}


def check_code():
    """Restart (Fenn starts the plugin again 2 s after it exits) when its
    code changes, e.g. after a git pull. Running downloads carry on after."""
    for f, t in _stamps.items():
        try:
            if os.path.getmtime(os.path.join(HERE, f)) != t:
                mark_ = os.path.join(app.data_dir, "reloaded")
                if os.path.exists(mark_) and time.time() - os.path.getmtime(mark_) < 25:
                    return
                open(mark_, "w").close()
                app.stop()
                return
        except OSError:
            pass


threading.Thread(target=read_version, daemon=True).start()
app.every(0.4, tick)
app.every(2, check_code)
draw()
pump()
app.run()
