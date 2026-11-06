# Grab for Fenn

Download video and audio from YouTube and
[about 1,800 other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
from a Fenn tile, with [yt-dlp](https://github.com/yt-dlp/yt-dlp) doing the work.

## What it does

- **Home:** the big Add New Download button, counters for downloading, waiting
  and done, and what's going on right now: the downloads running, the next
  ones up and the last ones finished (each with "See all").
- **Add New Download:** paste a link (or use the paste button) and Grab looks
  it up: thumbnail, title, channel, length and views. Pick **Video** (MP4,
  WebM or MKV; Best, 4K, 1440p, 1080p, 720p, 480p, only what the video
  has) or **Audio** (MP3, M4A, Opus, FLAC or WAV; Best, 320, 192 or 128 kbps),
  the folder and the file name. Under **Extra settings**: subtitles, embedded
  thumbnail, metadata and chapters, cutting out sponsor segments
  (SponsorBlock), and downloading only part of a video ("from 1:30 to 2:45").
- **Playlists:** a playlist link queues every video (or the first 10, 25, 50
  or 100 of a long one), in a folder named after the playlist. For a video
  link that's part of a playlist, a switch picks the video or the whole list.
- **Downloading:** progress, speed and time left; pause, resume or cancel
  (cancel deletes the partial files).
- **Queued:** what's waiting, in order; move one up, start it now or remove it.
  1 to 4 downloads run at once (Settings).
- **Completed:** open the file, show it in its folder, read yt-dlp's log, or
  try a failed one again. The trash clears the list (the files stay).
- **History:** everything you've downloaded; open it, download it again, or
  copy its link.
- **The tile** shows a ring with the progress of everything in the queue
  ("3 of 10"), and Fenn's island shows the same ring while downloading
  (it can be turned off).
- **Color:** Grab follows Fenn's accent color, or pick one of eight colors or
  type your own (#RRGGBB) under Settings. Fenn's own buttons, switches and
  chips keep Fenn's accent.
- **Settings:** the default folder, downloads at once, a speed limit, playlist
  folders, file dates, the island ring, the color, using a browser's sign-in for age-restricted or
  members-only videos (Firefox works best), subtitle languages, and yt-dlp's
  version with an update button.

A new download never overwrites an older file with the same name: it's saved
as "Title (2)". Downloads that were running when Fenn or the PC stopped carry
on where they left off the next time Grab starts.

## Requirements

Windows, Python 3.8+ (standard library only; Fenn can install Python for you),
and three free programs that Grab finds wherever winget put them:

```powershell
winget install yt-dlp.yt-dlp
```

That one command also installs **ffmpeg** (to merge video and audio above 720p
and to convert audio) and **Deno** (YouTube needs it to unlock most formats).
If yt-dlp is missing, Grab's home screen has a button that runs the same command.

Keep yt-dlp up to date (Settings > Update yt-dlp): sites change often, and an
old yt-dlp gets "HTTP Error 403" from YouTube.

## Install

```powershell
git clone https://github.com/lucaslapos8-cloud/fenn-grab "$env:APPDATA\Fenn\plugins\grab"
```

Fenn picks the new folder up on its own; restart Fenn once so the page gets
its full height.

## Privacy and network

Grab itself talks to no server. yt-dlp contacts the sites of the links you
add (and SponsorBlock's server when you choose to cut out sponsor segments);
thumbnails come from the same sites. The paste button reads the clipboard only
when you click it. With a browser chosen under Sign-in, yt-dlp reads that
browser's cookies for the sites you download from.

Kept in the plugin's data folder (`FENN_DATA_DIR`): `settings.json`,
`jobs.json` (the queue and finished downloads), `history.json`, `thumbs/`
(thumbnails), `logs/` (yt-dlp's output per download), and an empty
`reloaded` marker (the plugin restarts itself when its files change, at most
every 25 seconds).

Download only what you have the right to: many sites' terms forbid it, and
copyrighted material may need the owner's permission.

## Files

`main.py` (the queue, pages and clicks), `ytdlp.py` (finding yt-dlp, reading
links, building the command, following progress), `draw.py` (the sidebar,
home screen and other canvas drawing), `winutil.py` (clipboard, Downloads
folder, Explorer, and a job object so cancelling stops ffmpeg too),
`pick_folder.ps1` (Windows' folder picker), `fenn.py` (Fenn's plugin helper).

## License

MIT, see [LICENSE](LICENSE). yt-dlp is released into the public domain
(Unlicense); ffmpeg and Deno have their own licenses.

## Changelog

**1.0.0**
- First release.
