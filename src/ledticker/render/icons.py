"""Custom pixel-art weather icons, drawn in code at LED sizes (16/32/48), with day/night and animation frames.

Icons are drawn from simple shapes on a 32-unit grid and scaled with integer-friendly rounding, so each size
stays crisp. Every NWS condition code maps to one of our icon names (see NWS_MAP).
"""
from __future__ import annotations

import re
from functools import lru_cache

from PIL import Image, ImageDraw

FRAMES = 4

SUN = (255, 196, 0)
SUN_HI = (255, 236, 110)
RAY = (255, 150, 0)
MOON = (214, 222, 255)
MOON_SHADE = (150, 160, 210)
CLOUD = (210, 216, 228)
CLOUD_SHADE = (140, 148, 168)
CLOUD_DARK = (96, 102, 122)
CLOUD_DARK_SHADE = (62, 66, 84)
RAIN = (60, 150, 255)
SNOW = (245, 250, 255)
BOLT = (255, 232, 40)
BOLT_DIM = (150, 120, 10)
FOG = (150, 156, 170)
WIND = (150, 210, 235)
RED = (255, 60, 40)
ICE = (160, 220, 255)

ICONS = ("clear", "partly", "mostly", "overcast", "wind", "rain", "tstorm", "snow", "sleet", "fog",
         "tornado", "severe")

NWS_MAP = {
    "skc": "clear", "hot": "clear", "cold": "clear",
    "few": "partly", "sct": "partly",
    "bkn": "mostly",
    "ovc": "overcast",
    "wind_skc": "wind", "wind_few": "wind", "wind_sct": "wind", "wind_bkn": "wind", "wind_ovc": "wind",
    "rain": "rain", "rain_showers": "rain", "rain_showers_hi": "rain",
    "tsra": "tstorm", "tsra_sct": "tstorm", "tsra_hi": "tstorm",
    "snow": "snow", "blizzard": "snow",
    "sleet": "sleet", "fzra": "sleet", "rain_snow": "sleet", "rain_sleet": "sleet", "snow_sleet": "sleet",
    "rain_fzra": "sleet", "snow_fzra": "sleet",
    "fog": "fog", "haze": "fog", "smoke": "fog", "dust": "fog",
    "tornado": "tornado", "hurricane": "severe", "tropical_storm": "severe",
}

TEXT_HINTS = [  # fallback from shortForecast text, checked in order
    ("tornado", "tornado"), ("hurricane", "severe"), ("tropical", "severe"),
    ("thunder", "tstorm"), ("t-storm", "tstorm"),
    ("freezing", "sleet"), ("sleet", "sleet"), ("ice", "sleet"), ("snow", "snow"), ("flurr", "snow"),
    ("rain", "rain"), ("shower", "rain"), ("drizzle", "rain"),
    ("fog", "fog"), ("haze", "fog"), ("smoke", "fog"), ("dust", "fog"),
    ("wind", "wind"), ("breezy", "wind"), ("blustery", "wind"),
    ("overcast", "overcast"), ("mostly cloudy", "mostly"), ("cloudy", "overcast"),
    ("partly", "partly"), ("mostly sunny", "partly"), ("mostly clear", "partly"),
    ("sunny", "clear"), ("clear", "clear"), ("fair", "clear"),
]

_ICON_URL = re.compile(r"/icons/[^/]+/(day|night)/([a-z_]+)")


def icon_for(icon_url: str | None, short: str | None = None, is_day: bool | None = None) -> tuple[str, bool]:
    """(icon name, night?) from an NWS icon URL, falling back to forecast text."""
    night = False if is_day is None else not is_day
    if icon_url:
        m = _ICON_URL.search(icon_url)
        if m:
            night = m.group(1) == "night"
            name = NWS_MAP.get(m.group(2))
            if name:
                return name, night
    s = (short or "").lower()
    for hint, name in TEXT_HINTS:
        if hint in s:
            return name, night
    return "overcast", night


# ------------------------------------------------------------------ drawing helpers

class Pen:
    """Draw on a 32-unit grid scaled to the target size."""

    def __init__(self, size: int):
        self.s = size
        self.k = size / 32
        self.img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def p(self, v: float) -> int:
        return int(round(v * self.k))

    def box(self, x0, y0, x1, y1):
        return [self.p(x0), self.p(y0), max(self.p(x0), self.p(x1) - 1), max(self.p(y0), self.p(y1) - 1)]

    def ellipse(self, x0, y0, x1, y1, fill):
        self.d.ellipse(self.box(x0, y0, x1, y1), fill=fill)

    def rect(self, x0, y0, x1, y1, fill):
        self.d.rectangle(self.box(x0, y0, x1, y1), fill=fill)

    def line(self, pts, fill, width=1):
        w = max(1, int(round(width * self.k)))
        self.d.line([(self.p(x), self.p(y)) for x, y in pts], fill=fill, width=w)

    def poly(self, pts, fill):
        self.d.polygon([(self.p(x), self.p(y)) for x, y in pts], fill=fill)

    def dot(self, x, y, fill, r=1.0):
        rr = max(0.5, r * self.k)
        cx, cy = x * self.k, y * self.k
        if rr <= 0.75:
            self.d.point((int(cx), int(cy)), fill=fill)
        else:
            self.d.ellipse([cx - rr, cy - rr, cx + rr - 1, cy + rr - 1], fill=fill)


def _sun(pen: Pen, cx, cy, r, frame, rays=True):
    if rays:
        long_ = frame % 2 == 0
        import math
        for i in range(8):
            a = math.pi / 4 * i
            r0, r1 = r + 2.5, r + (6 if long_ else 4.5)
            if i % 2 and not long_:
                r1 = r + 5.5
            pen.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + r1 * math.cos(a), cy + r1 * math.sin(a))],
                     RAY, width=2 if pen.s >= 32 else 1)
    pen.ellipse(cx - r, cy - r, cx + r, cy + r, SUN)
    pen.ellipse(cx - r + 2, cy - r + 2, cx + r * 0.3, cy + r * 0.3, SUN_HI)


def _moon(pen: Pen, cx, cy, r):
    pen.ellipse(cx - r, cy - r, cx + r, cy + r, MOON)
    # carve the crescent
    m = Image.new("L", pen.img.size, 0)
    ImageDraw.Draw(m).ellipse(pen.box(cx - r + r * 0.7, cy - r - r * 0.35, cx + r + r * 0.7, cy + r - r * 0.35), fill=255)
    pen.img.paste((0, 0, 0, 0), (0, 0), m)
    pen.dot(cx - r * 0.35, cy + r * 0.3, MOON_SHADE, r=1.2)


def _cloud(pen: Pen, x, y, w, h, dark=False):
    """Cloud occupying box (x, y, x+w, y+h)."""
    top, shade = (CLOUD_DARK, CLOUD_DARK_SHADE) if dark else (CLOUD, CLOUD_SHADE)
    base = y + h * 0.45
    # shadow pass (offset down) then body
    for col, dy in ((shade, 1.5), (top, 0)):
        pen.ellipse(x, base - h * 0.15 + dy, x + w * 0.42, y + h + dy, col)
        pen.ellipse(x + w * 0.18, y + dy, x + w * 0.70, y + h * 0.85 + dy, col)
        pen.ellipse(x + w * 0.52, y + h * 0.25 + dy, x + w, y + h + dy, col)
        pen.rect(x + w * 0.2, base + dy, x + w * 0.8, y + h + dy, col)


def _rain(pen: Pen, frame, x0=8, x1=26, y0=22, color=RAIN, every=6):
    off = (frame % FRAMES) * 2
    for i, x in enumerate(range(x0, x1, every)):
        y = y0 + ((off + i * 3) % 8)
        pen.line([(x, y), (x - 1.5, y + 3.5)], color, width=1.5 if pen.s >= 32 else 1)


def _snow(pen: Pen, frame, x0=8, x1=27, y0=22):
    off = frame % FRAMES
    for i, x in enumerate(range(x0, x1, 6)):
        y = y0 + ((off * 2 + i * 3) % 8)
        dx = 1 if (off + i) % 2 else 0
        pen.dot(x + dx, y + 1, SNOW, r=1.4)
        if pen.s >= 32:
            pen.line([(x + dx - 1.5, y + 1), (x + dx + 1.5, y + 1)], SNOW)
            pen.line([(x + dx, y - 0.5), (x + dx, y + 2.5)], SNOW)


def _bolt(pen: Pen, frame, x=15, y=17):
    col = BOLT if frame in (0, 2) else BOLT_DIM
    pen.poly([(x + 3, y), (x - 2, y + 8), (x + 2, y + 8), (x - 1, y + 15), (x + 7, y + 5), (x + 3, y + 5), (x + 6, y)], col)


def draw(name: str, size: int, night: bool = False, frame: int = 0) -> Image.Image:
    pen = Pen(size)
    f = frame % FRAMES
    if name == "clear":
        if night:
            _moon(pen, 16, 16, 10)
        else:
            _sun(pen, 16, 16, 7, f)
    elif name == "partly":
        if night:
            _moon(pen, 11, 10, 7.5)
        else:
            _sun(pen, 11, 11, 5.5, f)
        _cloud(pen, 9, 14, 21, 13)
    elif name == "mostly":
        if night:
            _moon(pen, 22, 8, 5.5)
        else:
            _sun(pen, 22, 9, 4.5, f, rays=size >= 32)
        _cloud(pen, 3, 10, 24, 15)
        _cloud(pen, 12, 17, 18, 11)
    elif name == "overcast":
        _cloud(pen, 9, 5, 20, 12, dark=True)
        _cloud(pen, 2, 11, 26, 16)
    elif name == "wind":
        shift = [0, 1, 2, 1][f]
        for y, x0, x1 in ((9, 3, 23), (16, 6, 28), (23, 2, 19)):
            pen.line([(x0 + shift, y), (x1 + shift, y)], WIND, width=2 if size >= 32 else 1)
            pen.line([(x1 + shift, y), (x1 + shift + 2, y - 2), (x1 + shift + 1, y - 4)], WIND,
                     width=1.5 if size >= 32 else 1)
    elif name == "rain":
        _cloud(pen, 3, 3, 26, 16, dark=True)
        _rain(pen, f)
    elif name == "tstorm":
        _cloud(pen, 3, 2, 26, 16, dark=True)
        _rain(pen, f, x0=6, x1=12, every=5)
        _rain(pen, f, x0=24, x1=29, every=5)
        _bolt(pen, f)
    elif name == "snow":
        _cloud(pen, 3, 3, 26, 16)
        _snow(pen, f)
    elif name == "sleet":
        _cloud(pen, 3, 3, 26, 16, dark=True)
        _rain(pen, f, x0=8, x1=26, every=9, color=ICE)
        _snow(pen, (f + 2) % FRAMES, x0=11, x1=27)
    elif name == "fog":
        _cloud(pen, 6, 2, 22, 12)
        shift = [0, 1, 2, 1][f]
        for i, y in enumerate((18, 22, 26, 30)):
            a = (i % 2) * 3 + shift
            pen.line([(3 + a, y - 1), (20 + a, y - 1)], FOG, width=1.6 if size >= 32 else 1)
            pen.line([(23 + a, y - 1), (29, y - 1)], FOG, width=1.6 if size >= 32 else 1)
    elif name == "tornado":
        _cloud(pen, 3, 1, 26, 11, dark=True)
        sway = [0, 1, 0, -1][f]
        for i, (w, y) in enumerate(((18, 13), (14, 16.5), (10, 20), (7, 23.5), (4, 27))):
            cx = 16 + sway * (i / 2)
            pen.line([(cx - w / 2, y), (cx + w / 2, y)], CLOUD_SHADE, width=2.2 if size >= 32 else 1)
    else:  # severe: warning triangle
        on = f in (0, 1)
        pen.poly([(16, 3), (30, 28), (2, 28)], RED if on else (150, 30, 20))
        pen.poly([(16, 8), (26, 26), (6, 26)], (40, 0, 0))
        pen.rect(15, 12, 18, 21, (255, 230, 220))
        pen.rect(15, 23, 18, 25.5, (255, 230, 220))
    return pen.img


@lru_cache(maxsize=512)
def get(name: str, size: int, night: bool = False, frame: int = 0) -> Image.Image:
    return draw(name, size, night, frame)


def frame_at(t: float, fps: float = 3.0) -> int:
    return int(t * fps) % FRAMES
