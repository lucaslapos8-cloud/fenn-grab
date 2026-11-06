"""yt-dlp: finding it (and ffmpeg and Deno), reading what's behind a link,
building the command for a download, and running one with live progress."""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import winutil

NO_WINDOW = winutil.NO_WINDOW
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Grab-for-Fenn"
HEIGHTS = [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]
STAGES = {  # yt-dlp's post-processors, as words
    "Merger": "Merging", "ExtractAudio": "Converting audio", "VideoConvertor": "Converting",
    "VideoRemuxer": "Converting", "EmbedThumbnail": "Adding the thumbnail", "Metadata": "Adding metadata",
    "EmbedSubtitle": "Adding subtitles", "SponsorBlock": "Finding sponsor segments",
    "ModifyChapters": "Cutting sponsor segments", "FixupM3u8": "Fixing", "FixupM4a": "Fixing",
    "FixupTimestamp": "Fixing", "FixupDuplicateMoov": "Fixing", "ThumbnailsConvertor": "Converting the thumbnail",
    "SubtitlesConvertor": "Converting subtitles", "FFmpegConcat": "Joining",
}


# ---- finding the tools

def _extra_dirs(data_dir):
    """Where winget puts yt-dlp, ffmpeg and Deno, for when Fenn was started
    before they were installed (so they aren't on its PATH yet)."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet")
    dirs = [data_dir, os.path.join(base, "Links")]
    for pat in ("yt-dlp.yt-dlp_*", "yt-dlp.FFmpeg_*", "Gyan.FFmpeg*", "BtbN.FFmpeg*", "DenoLand.Deno_*"):
        for d in glob.glob(os.path.join(base, "Packages", pat)):
            dirs.append(d)
            dirs += glob.glob(os.path.join(d, "*", "bin"))
    return [d for d in dirs if os.path.isdir(d)]


class Tools:
    def __init__(self, data_dir):
        self.env = dict(os.environ)
        self.env["PATH"] = os.pathsep.join(_extra_dirs(data_dir) + [self.env.get("PATH", "")])
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"
        path = self.env["PATH"]
        exe = shutil.which("yt-dlp", path=path)
        if exe:
            self.cmd = [exe]
        else:
            try:
                import importlib.util
                self.cmd = [sys.executable, "-m", "yt_dlp"] if importlib.util.find_spec("yt_dlp") else None
            except (ImportError, ValueError):
                self.cmd = None
        self.exe = exe
        self.ffmpeg = shutil.which("ffmpeg", path=path)
        self.deno = shutil.which("deno", path=path)
        self.via_winget = bool(exe and "WinGet" in exe)
        self.version = None

    def read_version(self):
        if not self.cmd:
            return None
        try:
            out = subprocess.run(self.cmd + ["--version"], capture_output=True, env=self.env, stdin=subprocess.DEVNULL,
                                 creationflags=NO_WINDOW, timeout=60)
            self.version = out.stdout.decode("utf-8", "replace").strip().splitlines()[-1]
        except (OSError, subprocess.TimeoutExpired, IndexError):
            self.version = None
        return self.version


# ---- reading a link

def _last_error(text):
    lines = [ln for ln in text.splitlines() if ln.startswith("ERROR:")]
    return clean_error(lines[-1]) if lines else (text.strip().splitlines() or ["yt-dlp stopped"])[-1][:200]


def clean_error(line):
    line = re.sub(r"^ERROR:\s*", "", line.strip())
    line = re.sub(r"^\[[\w:]+\]\s*[\w-]+:\s*", "", line)  # "[youtube] dQw4w9WgXcQ: "
    line = line.replace("Use --cookies-from-browser or --cookies for the authentication.",
                        "Pick a browser under Settings > Sign-in.")
    return line[:240]


def probe(tools, url, playlist, cookies=None):
    """What's behind the link, as a small dict for the Add page. Blocks."""
    args = tools.cmd + ["-J", "--flat-playlist", "--no-warnings", "--yes-playlist" if playlist else "--no-playlist"]
    if cookies:
        args += ["--cookies-from-browser", cookies]
    out = subprocess.run(args + ["--", url], capture_output=True, env=tools.env, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                         timeout=120)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(_last_error(out.stderr.decode("utf-8", "replace")))
    d = json.loads(out.stdout.decode("utf-8", "replace"))
    if d.get("_type") == "playlist" or (d.get("entries") is not None and not d.get("formats")):
        entries = [e for e in d.get("entries") or [] if e and (e.get("url") or e.get("webpage_url"))]
        return {
            "type": "playlist", "url": d.get("webpage_url") or url, "title": d.get("title") or "Playlist",
            "uploader": d.get("uploader") or d.get("channel") or "", "count": len(entries),
            "thumb_url": pick_thumb(d) or (pick_thumb(entries[0]) if entries else None),
            "entries": [{"url": e.get("webpage_url") or e.get("url"), "title": e.get("title") or e.get("id") or "Video",
                         "uploader": e.get("uploader") or e.get("channel") or "", "duration": e.get("duration"),
                         "thumb_url": pick_thumb(e), "vid": e.get("id")} for e in entries],
        }
    formats = d.get("formats") or []
    heights = sorted({f.get("height") for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")},
                     reverse=True)
    has_video = bool(heights) or any(f.get("vcodec") not in (None, "none") for f in formats) or (
        not formats and d.get("vcodec") not in (None, "none"))
    return {
        "type": "video", "url": d.get("webpage_url") or url, "title": d.get("title") or d.get("id") or "Video",
        "uploader": d.get("uploader") or d.get("channel") or d.get("extractor_key") or "",
        "duration": d.get("duration"), "views": d.get("view_count"), "heights": heights, "video": has_video,
        "thumb_url": pick_thumb(d), "vid": d.get("id"), "site": d.get("extractor_key") or "",
        "in_list": bool(d.get("playlist_id")) or "list=" in url,
    }


def pick_thumb(d):
    """A thumbnail about 320-720 px wide, as JPG if there is one."""
    thumbs = [t for t in d.get("thumbnails") or [] if t.get("url")]
    best, score = None, None
    for t in thumbs:
        w, h = t.get("width") or 0, t.get("height") or 0
        url = t["url"]
        wide = 0 if (w and h and abs(w / h - 16 / 9) < 0.08) or re.search(r"/(mq|maxres)default", url) else 1
        s = (0 if re.search(r"\.jpe?g(\?|$)", url) else 1, 0 if 300 <= w <= 800 else 1, wide, abs((w or 480) - 480))
        if score is None or s < score:
            best, score = url, s
    best = best or d.get("thumbnail")
    m = re.search(r"//i\.ytimg\.com/vi(?:_webp)?/([\w-]{6,})/", best or "")
    return f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else best  # YouTube's 16:9 one


def fetch_thumb(tools, url, folder):
    """Downloads a thumbnail as a JPG/PNG Fenn can show; returns its path."""
    if not url:
        return None
    os.makedirs(folder, exist_ok=True)
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    for ext in (".jpg", ".png"):
        if os.path.exists(os.path.join(folder, name + ext)):
            return os.path.join(folder, name + ext)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(8_000_000)
    if data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    else:  # WebP and the like: let ffmpeg turn it into a JPG
        if not tools.ffmpeg:
            return None
        raw = os.path.join(folder, name + ".raw")
        with open(raw, "wb") as f:
            f.write(data)
        subprocess.run([tools.ffmpeg, "-y", "-loglevel", "error", "-i", raw, "-frames:v", "1",
                        "-vf", "scale='min(640,iw)':-2", os.path.join(folder, name + ".jpg")],
                       capture_output=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, timeout=60)
        os.remove(raw)
        out = os.path.join(folder, name + ".jpg")
        return out if os.path.exists(out) else None
    path = os.path.join(folder, name + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---- a download

def safe_name(name):
    """A Windows file name, with yt-dlp's % escaped."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', lambda m: {":": "-", '"': "'", "/": "-", "\\": "-", "|": "-"}.get(
        m.group(0), ""), name).strip().rstrip(".")
    return (name or "download")[:180].replace("%", "%%")


def assign_name(job):
    """Picks the file name once, before the first start, so an older file
    with the same name is never overwritten or skipped ("Title (2)"), and a
    resumed download (after a pause or a restart) finds its pieces again."""
    if job.get("out_name"):
        return
    os.makedirs(job["folder"], exist_ok=True)
    base = name = safe_name(job["name"]).replace("%%", "%")
    n = 1
    while any(os.path.exists(os.path.join(job["folder"], f"{name}.{job['ext']}{tail}")) for tail in ("", ".part")):
        n += 1
        name = f"{base} ({n})"
    job["out_name"] = name


def command(tools, job, settings, path_file):
    """The yt-dlp arguments for one job."""
    o = job["opts"]
    a = tools.cmd + ["--newline", "--progress", "--no-playlist",
                     "--progress-template",
                     "download:GRABP %(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
                     "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
                     "--print-to-file", "after_move:filepath", path_file,
                     "-P", job["folder"], "-o", safe_name(job.get("out_name") or job["name"]) + ".%(ext)s"]
    if not settings.get("keep_date"):
        a.append("--no-mtime")
    if job["kind"] == "audio":
        fmt = job["ext"]
        a += ["-f", "ba/b", "-x", "--audio-format", fmt]
        if fmt in ("mp3", "m4a", "opus"):
            a += ["--audio-quality", f"{job['quality']}K" if job["quality"] else "0"]
        if o.get("thumb") and fmt in ("mp3", "m4a", "opus", "flac"):
            a.append("--embed-thumbnail")
    else:
        ext = job["ext"]
        sort = f"res:{job['quality']}," if job["quality"] else "res,"
        # MP4 prefers H.264 (plays everywhere) where the size is the same; there's none above 1080p.
        sort += {"mp4": "vcodec:h264,acodec:aac,ext:mp4:m4a", "webm": "vcodec:vp9,acodec:opus,ext:webm:webm",
                 "mkv": "vcodec,acodec"}[ext]
        a += ["-f", "bv*+ba/b", "-S", sort, "--merge-output-format", ext]
        if ext != "webm":  # h264 can't go into WebM; MP4 and MKV take anything
            a += ["--remux-video", ext]
        if o.get("thumb") and ext in ("mp4", "mkv"):
            a.append("--embed-thumbnail")
        if o.get("subs"):
            a += ["--write-subs", "--sub-langs", settings.get("sub_langs") or "en.*", "--embed-subs"]
    if o.get("meta"):
        a.append("--embed-metadata")
    if o.get("chapters"):
        a.append("--embed-chapters")
    if o.get("sponsor"):
        a += ["--sponsorblock-remove", "sponsor"]
    if o.get("trim_from") or o.get("trim_to"):
        a += ["--download-sections", f"*{o.get('trim_from') or '0'}-{o.get('trim_to') or 'inf'}",
              "--force-keyframes-at-cuts"]
    if settings.get("limit"):
        a += ["-r", f"{settings['limit']}M"]
    if settings.get("cookies"):
        a += ["--cookies-from-browser", settings["cookies"]]
    return a + ["--", job["url"]]


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def run(tools, job, settings, data_dir, changed):
    """Runs one job to the end (blocks; call from a thread). Updates the job
    dict as it goes and calls changed() after each update. Returns the
    yt-dlp exit code; job["_proc"] can be killed to stop it."""
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    path_file = os.path.join(data_dir, "logs", job["id"] + ".path")
    log_path = os.path.join(data_dir, "logs", job["id"] + ".log")
    if os.path.exists(path_file):
        os.remove(path_file)
    assign_name(job)
    args = command(tools, job, settings, path_file)
    weights = [1.0]
    part = -1
    dests, extras = [], []  # what yt-dlp writes, to clean up after a cancel or failure
    last = 0.0
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {job['url']}\n")
        tree = winutil.ProcessTree(args, tools.env, cwd=job["folder"])
        job["_proc"] = tree
        try:
            for raw in tree.proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                if line.startswith("GRABP "):
                    done, total, est, speed, eta = (_num(x) for x in line[6:].split("|"))
                    total = total or est
                    if part < 0:
                        part = 0
                    frac = min(1.0, done / total) if done and total else 0.0
                    p = sum(weights[:part]) + weights[min(part, len(weights) - 1)] * frac
                    job.update(progress=min(0.99, p), speed=speed, eta=eta,
                               stage=("Downloading video" if part == 0 else "Downloading audio")
                               if len(weights) == 2 else "Downloading")
                    if time.monotonic() - last > 0.25:
                        last = time.monotonic()
                        changed()
                    continue
                log.write(line + "\n")
                m = re.match(r"\[info\] .*Downloading \d+ format\(s\): (\S+)", line)
                if m:
                    n = m.group(1).count("+") + 1
                    weights = [0.85, 0.15] if n == 2 else [1.0 / n] * n
                    continue
                m = re.match(r"\[download\] Destination: (.+)$", line)
                if m:
                    part += 1
                    dests.append(m.group(1))
                    continue
                m = re.match(r"\[info\] Writing video (?:thumbnail|subtitles).* to: (.+)$", line)
                if m:
                    extras.append(m.group(1))
                    continue
                m = re.match(r"\[(\w+)\]", line)
                if m and m.group(1) in STAGES:
                    job.update(stage=STAGES[m.group(1)], speed=None, eta=None,
                               progress=max(job.get("progress") or 0, 0.99 if m.group(1) != "SponsorBlock" else 0))
                    changed()
                    continue
                if line.startswith("ERROR:"):
                    job["error"] = clean_error(line)
            code = tree.proc.wait()
        finally:
            tree.close()
            job["_proc"] = None
    job["_dests"], job["_extras"] = dests, extras
    if code == 0 and os.path.exists(path_file):
        with open(path_file, encoding="utf-8", errors="replace") as f:
            paths = [p.strip() for p in f if p.strip()]
        if paths:
            job["file"] = paths[-1]
    return code


def cleanup_partial(job):
    """After a cancel or failure: removes the half-downloaded pieces, and the
    thumbnail and subtitle files waiting to be embedded, that this job made."""
    for p in job.get("_extras") or []:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    for p in job.get("_dests") or []:
        for f in (p + ".part", p + ".ytdl", p + ".part-Frag1"):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass
        if re.search(r"\.f[\w-]+\.\w+$", p):  # a video or audio piece waiting to be merged
            try:
                os.remove(p)
            except OSError:
                pass
