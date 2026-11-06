"""Downloads yt-dlp, ffmpeg and Deno into Grab's own folder (no winget, no
admin, nothing system-wide), checks each file against the SHA-256 checksum
its project publishes, and unpacks only the programs Grab needs."""

import hashlib
import os
import re
import urllib.request
import zipfile

UA = "Grab-for-Fenn (+https://github.com/lucaslapos8-cloud/fenn-grab)"
GH = "https://github.com"
PARTS = {
    "yt-dlp": {
        "label": "yt-dlp", "size": 24_000_000,
        "url": GH + "/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "sums": GH + "/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS", "file": "yt-dlp.exe",
    },
    "ffmpeg": {  # the "shared" build: ffmpeg and ffprobe share their DLLs (91 MB instead of 200)
        "label": "ffmpeg", "size": 91_000_000,
        "url": GH + "/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
        "sums": GH + "/yt-dlp/FFmpeg-Builds/releases/download/latest/checksums.sha256",
        "file": "ffmpeg-master-latest-win64-gpl-shared.zip",
    },
    "deno": {
        "label": "Deno", "size": 43_000_000,
        "url": GH + "/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
        "sums": GH + "/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip.sha256sum",
        "file": None,  # the checksum file holds just this one hash
    },
}


def _get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


def expected_hash(part):
    with _get(part["sums"]) as r:
        text = r.read(200_000).decode("utf-8", "replace")
    for line in text.splitlines():
        m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
        if m and (part["file"] is None or line.strip().endswith(" " + part["file"])
                  or line.strip().endswith("*" + part["file"])):
            return m.group(1).lower()
    raise RuntimeError(f"No checksum published for {part['label']}")


def size_of(names):
    return sum(PARTS[n]["size"] for n in names)


def install(names, bin_dir, progress):
    """Installs the named parts ("yt-dlp", "ffmpeg", "deno") into bin_dir.
    progress(text, fraction) is called as it goes. Blocks; raises on error."""
    os.makedirs(bin_dir, exist_ok=True)
    total = size_of(names)
    done = 0
    for name in names:
        part = PARTS[name]
        progress(f"Checking {part['label']}…", done / total)
        want = expected_hash(part)
        tmp = os.path.join(bin_dir, f".{name}.download")
        h = hashlib.sha256()
        got = 0
        with _get(part["url"], timeout=60) as r, open(tmp, "wb") as f:
            size = int(r.headers.get("Content-Length") or part["size"])
            total += size - part["size"]
            part["size"] = size
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                progress(f"Downloading {part['label']} · {got / 1e6:.0f} of {size / 1e6:.0f} MB",
                         (done + got) / total)
        if h.hexdigest() != want:
            os.remove(tmp)
            raise RuntimeError(f"{part['label']} didn't match its published checksum; nothing was installed")
        progress(f"Unpacking {part['label']}…", (done + got) / total)
        if name == "yt-dlp":
            os.replace(tmp, os.path.join(bin_dir, "yt-dlp.exe"))
        else:
            _unpack(tmp, bin_dir, name)
            os.remove(tmp)
        done += got
    progress("Done", 1.0)


def _unpack(zip_path, bin_dir, name):
    """Only the programs and the DLLs they need, flat into bin_dir."""
    wanted = {"ffmpeg": ("ffmpeg.exe", "ffprobe.exe"), "deno": ("deno.exe",)}[name]
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = os.path.basename(info.filename)
            if not base or (name == "ffmpeg" and "/bin/" not in "/" + info.filename):
                continue
            if base.lower() in wanted or (name == "ffmpeg" and base.lower().endswith(".dll")):
                out = os.path.join(bin_dir, base)
                with z.open(info) as src, open(out + ".new", "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                os.replace(out + ".new", out)
