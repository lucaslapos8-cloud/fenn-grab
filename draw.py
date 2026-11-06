"""Canvas drawing for Grab: text measuring, the sidebar, the home screen and
empty lists. Canvases remember their clickable areas (Canvas.hit)."""

import fenn

ACCENT = "#9D45B0"
ACCENT_HI = "#B65CC9"
INK = "#FFFFFF"  # text on ACCENT
BLUE = "#0A84FF"
ORANGE = "#FF9F0A"
GREEN = "#30D158"
RED = "#FF453A"
WHITE = "#FFFFFF"

WIDTHS = {" ": 0.27, ".": 0.27, ",": 0.27, ":": 0.28, ";": 0.28, "%": 0.82, "/": 0.36, "-": 0.36,
          "–": 0.55, "·": 0.27, "(": 0.33, ")": 0.33, "'": 0.22, "’": 0.22, "|": 0.25, "!": 0.27,
          "i": 0.25, "l": 0.25, "j": 0.25, "t": 0.34, "f": 0.33, "r": 0.36, "m": 0.83, "w": 0.75,
          "I": 0.28, "M": 0.84, "W": 0.92}


def tw(text, size, bold=False):
    w = 0.0
    for ch in str(text):
        if ch in WIDTHS:
            w += WIDTHS[ch]
        elif ch.isdigit():
            w += 0.58
        elif ch.isupper():
            w += 0.66
        elif ord(ch) > 0x2E80:  # CJK and emoji are about square
            w += 1.0
        else:
            w += 0.53
    return w * size * (1.05 if bold else 1.0)


def fit(text, size, room, bold=False):
    text = str(text)
    if tw(text, size, bold) <= room:
        return text
    while text and tw(text + "…", size, bold) > room:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text, size, room, lines=2, bold=False):
    words, out, cur = str(text).split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if tw(trial, size, bold) <= room or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = wd
    if cur:
        out.append(cur)
    out = [fit(x, size, room, bold) for x in out]
    if len(out) > lines:
        out = out[:lines]
        out[-1] = fit(out[-1] + " …", size, room, bold)
    return out


def mix(a, b, t):
    t = min(1.0, max(0.0, t))
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02X}" for i in (0, 2, 4))


class Canvas:
    """Shapes plus clickable regions (checked top-most first)."""

    def __init__(self, width, height=0):
        self.w = width
        self.h = height
        self.shapes = []
        self.regions = []

    def text(self, x, y, value, size=13, color="text", anchor="start", bold=False):
        self.shapes.append(fenn.label(x, y, value, size=size, color=color, anchor=anchor, bold=bold))

    def rect(self, x, y, w, h, fill=None, radius=0, stroke=None, width=1):
        self.shapes.append(fenn.rect(x, y, w, h, fill=fill, radius=radius, stroke=stroke,
                                     width=width if stroke else None))

    def circle(self, x, y, r, fill=None, stroke=None, width=1):
        self.shapes.append(fenn.circle(x, y, r, fill=fill, stroke=stroke, width=width if stroke else None))

    def glyph(self, x, y, name, size=14, color="text", fill=False):
        self.shapes.append(fenn.glyph(x, y, name, size, color, fill or None))

    def region(self, left, top, right, bottom, action):
        self.regions.append((left, top, right, bottom, action))

    def hit(self, x, y):
        for x0, y0, x1, y1, action in reversed(self.regions):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return action
        return None

    def node(self, cid, tooltip=None):
        return fenn.canvas(self.h, self.shapes, width=self.w, id=cid, tooltip=tooltip)


def glass(dark):
    """Panel fill and rim on Fenn's glass, for the dark or light theme."""
    return ("#FFFFFF0C", "#FFFFFF16") if dark else ("#0000000A", "#00000014")


def set_accent(color):
    """The plugin's color (Fenn's accent or the user's own), plus a lighter
    tint and the text color that reads on it."""
    global ACCENT, ACCENT_HI, INK
    ACCENT = color
    ACCENT_HI = mix(color, "#FFFFFF", 0.18)
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    INK = "#1C1C1E" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.64 else WHITE


# ---- sidebar

NAV = [
    ("home", "house", "Home", None),
    ("-", None, "Downloading", None),
    ("downloading", "arrow-circle-down", "Downloading", BLUE),
    ("queued", "clock", "Queued", ORANGE),
    ("completed", "check-circle", "Completed", GREEN),
    ("-", None, "Other", None),
    ("history", "clock-counter-clockwise", "History", None),
]


def sidebar(width, height, current, counts, dark):
    c = Canvas(width, height)
    fill, rim = glass(dark)
    c.rect(0, 0, width, height, fill=fill, radius=14, stroke=rim)
    y = 8
    for key, icon, label, badge in NAV:
        if key == "-":
            y += 8
            c.text(14, y + 7, label, size=10.5, color="faint", bold=True)
            y += 16
            continue
        on = key == current
        if on:
            c.rect(6, y, width - 12, 30, fill=ACCENT, radius=8)
        c.glyph(24, y + 15, icon, 15, INK if on else "text")
        c.text(40, y + 15, label, size=13, color=INK if on else "text")
        n = counts.get(key, 0)
        if badge and n:
            label_n = str(n) if n < 100 else "99+"
            w = max(18, tw(label_n, 10, True) + 10)
            c.rect(width - 14 - w, y + 6, w, 18, fill=badge, radius=9)
            c.text(width - 14 - w / 2, y + 15, label_n, size=10, color=WHITE if badge != ORANGE else "#1C1C1E",
                   anchor="center", bold=True)
        c.region(0, y, width, y + 30, "nav:" + key)
        y += 34
    on = current == "settings"
    sy = height - 40
    c.rect(6, sy, width - 12, 32, fill=ACCENT if on else rim, radius=9)
    c.glyph(24, sy + 16, "gear-six", 15, INK if on else "text")
    c.text(40, sy + 16, "Settings", size=13, color=INK if on else "text")
    c.region(0, sy, width, sy + 32, "nav:settings")
    return c


# ---- home

def pill_button(c, cx, y, label, icon, action, w=None, h=40, fill=None):
    fill = fill or ACCENT
    ink = INK if fill == ACCENT else WHITE
    w = w or tw(label, 13, True) + 64
    x = cx - w / 2
    c.rect(x, y, w, h, fill=fill, radius=h / 2)
    tx = cx - (tw(label, 13, True) + 22) / 2
    c.glyph(tx + 8, y + h / 2, icon, 16, ink, True)
    c.text(tx + 22, y + h / 2, label, size=13, color=ink, bold=True)
    c.region(x, y, x + w, y + h, action)


def home_top(width, counts, dark, missing=None):
    """The big Add button (or Install, without yt-dlp) and three counters."""
    c = Canvas(width, 122)
    if missing:
        pill_button(c, width / 2, 0, "Install yt-dlp with winget", "download-simple", "install", w=width, h=46)
    else:
        pill_button(c, width / 2, 0, "Add New Download", "plus-circle", "add", w=width, h=46)
    tiles = [("downloading", "Downloading", BLUE), ("queued", "In queue", ORANGE), ("completed", "Completed", GREEN)]
    gap = 10
    tw_ = (width - gap * 2) / 3
    for i, (key, label, color) in enumerate(tiles):
        x, y = i * (tw_ + gap), 58
        c.rect(x, y, tw_, 62, fill=color + ("22" if dark else "1C"), radius=12, stroke=color + "2E")
        c.text(x + 14, y + 23, str(counts.get(key, 0)), size=22, color=color, bold=True)
        c.text(x + 14, y + 46, label, size=10.5, color="dim")
        c.region(x, y, x + tw_, y + 62, "nav:" + key)
    return c


def heading(width, title, count, action):
    """A section title with a "See all" link on the right."""
    c = Canvas(width, 24)
    c.text(0, 13, title, size=13.5, color="text", bold=True)
    if count:
        c.text(tw(title, 13.5, True) + 8, 13, str(count), size=12, color="faint", bold=True)
    link = "See all"
    c.text(width - 14, 13, link, size=11.5, color=ACCENT_HI, anchor="end", bold=True)
    c.glyph(width - 6, 13, "caret-right", 11, ACCENT_HI)
    c.region(width - 26 - tw(link, 11.5, True), 0, width, 24, action)
    return c


def swatches(width, choices, current, fenn_color):
    """Color circles; the first is Fenn's own accent."""
    c = Canvas(width, 54)
    x = 18
    for value in choices:
        color = fenn_color if value == "fenn" else value
        if value == current:
            c.circle(x, 19, 16, stroke="text", width=2)
        c.circle(x, 19, 12, fill=color)
        if value == "fenn":
            c.text(x, 46, "Fenn", size=9.5, color="dim", anchor="center")
        c.region(x - 17, 0, x + 17, 50, "color:" + value)
        x += 38
    return c


def empty(width, height, icon, title, line, button=True):
    c = Canvas(width, max(220, height))
    cy = max(40, c.h / 2 - 70)
    c.glyph(width / 2, cy, icon, 40, "faint")
    c.text(width / 2, cy + 44, title, size=15, color="text", anchor="center", bold=True)
    c.text(width / 2, cy + 66, line, size=12, color="dim", anchor="center")
    if button:
        pill_button(c, width / 2, cy + 92, "Add New Download", "plus-circle", "add", w=200, h=36)
    return c


def thumb_placeholder(w, h, kind, dark):
    fill, rim = glass(dark)
    return fenn.canvas(h, [fenn.rect(0, 0, w, h, fill=fill, radius=6, stroke=rim, width=1),
                           fenn.glyph(w / 2, h / 2, "music-notes" if kind == "audio" else "film-strip", 20, "faint")],
                       width=w)
