"""Christmas: a seasonal overlay for every frame (garland, lights, snow) and a countdown widget.

The overlay is drawn *behind* the content: it only lights pixels the frame left dark, so scores, text and logos
in every layout section stay exactly as readable as without it. Everything is a pure function of time.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from .draw import big_text, text, text_center
from .fonts import font, text_width
from .screens import RC, Screen

PINE = ((14, 70, 26), (20, 92, 34), (28, 112, 44), (10, 56, 20))
HOLLY = (18, 104, 38)
BERRY = (225, 30, 40)
LIGHTS = ((255, 40, 40), (40, 220, 80), (255, 190, 40), (60, 120, 255), (255, 255, 255))
SNOW = (235, 240, 255)
RED = (230, 35, 45)
GREEN = (40, 200, 80)
GOLD = (255, 200, 60)
WHITE = (255, 255, 255)

SNOW_FLAKES = {1: 28, 2: 55, 3: 95}            # light / medium / heavy, on a 256x128 display
_DARK_LUT = [255 if v < 36 else 0 for v in range(256)]
_LIT_LUT = [0] + [255] * 255


# ---------------------------------------------------------------- overlay

def _swag(x: int, period: int) -> float:
    """Garland hanging from the top edge in swags: 0 at each tack point, deepest midway."""
    return 2 + 5 * math.sin(math.pi * (x % period) / period) ** 1.4


@lru_cache(maxsize=8)
def _garland(W: int, H: int, bottom: int) -> tuple[Image.Image, tuple]:
    """Static garland (pine swags across the top, pine down both sides, holly in the top corners) and the
    positions of its lights. `bottom` is where the side garland stops (above the ticker)."""
    img = Image.new("RGB", (W, H))
    px = img.load()
    r = random.Random(25)
    period = W // 4
    for x in range(W):                           # swags: a thick needle band following the curve
        c = _swag(x, period)
        for y in range(0, int(c) + 4):
            if abs(y - c) <= 2.2 or (y < c and r.random() < .25):
                if r.random() < .85:
                    px[x, y] = r.choice(PINE)
    for y in range(8, bottom):                   # sides: a ragged 2-4 px band
        for side in (0, 1):
            depth = 2 + int(1.5 + 1.5 * math.sin(y / 5 + side * 2))
            for d in range(depth):
                if r.random() < (.9 if d < 2 else .5):
                    px[d if side == 0 else W - 1 - d, y] = r.choice(PINE)
    d = ImageDraw.Draw(img)
    for cx in (0, W - 1):                        # holly sprigs in the top corners
        sgn = 1 if cx == 0 else -1
        for i, (lx, ly) in enumerate(((5, 4), (11, 7), (4, 11), (14, 3))):
            x0 = cx + sgn * lx
            d.ellipse([x0 - 4, ly - 2, x0 + 4, ly + 2], fill=HOLLY)
            d.point([(x0 - 3, ly), (x0 + 3, ly)], fill=(10, 60, 22))
        for bx, by in ((8, 6), (10, 8), (7, 9)):
            d.rectangle([cx + sgn * bx - 1, by - 1, cx + sgn * bx, by], fill=BERRY)
    for x in range(0, W, period):                # a red bow at every tack point
        if 0 < x < W - 1:
            d.polygon([(x, 2), (x - 4, 0), (x - 4, 4)], fill=BERRY)
            d.polygon([(x, 2), (x + 4, 0), (x + 4, 4)], fill=BERRY)
            d.line([(x, 2), (x - 2, 7)], fill=BERRY)
            d.line([(x, 2), (x + 2, 7)], fill=BERRY)
    lights = []
    for i, x in enumerate(range(period // 8, W, max(6, period // 6))):
        lights.append((x, int(_swag(x, period)) + 1, i))
    for i, y in enumerate(range(16, bottom - 2, 13)):
        lights.append((1, y, i + 3))
        lights.append((W - 2, y, i + 5))
    return img, tuple(lights)


def snow(img: Image.Image, t: float, flakes: int, seed: int = 1, top: int = 0, bottom: int | None = None,
         behind: bool = True) -> None:
    """Falling snow. behind=True only paints pixels that are currently dark (so it passes behind content)."""
    W = img.width
    H = img.height if bottom is None else bottom
    span = H - top + 4
    layer = Image.new("RGB", img.size)
    d = ImageDraw.Draw(layer)
    for i in range(flakes):
        r = random.Random(seed * 7907 + i)
        speed = r.uniform(5, 15)
        near = speed > 11                                      # faster flakes are closer: bigger, brighter
        prog = ((t * speed + r.random() * span) % span)
        y = top - 2 + prog
        x = (r.uniform(0, W) + math.sin(t * r.uniform(.4, 1.1) + i) * r.uniform(2, 6) + t * 3) % W
        k = r.uniform(.45, .75) if not near else 1.0
        c = tuple(int(v * k) for v in SNOW)
        if near and i % 3 == 0:
            d.point([(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)], fill=c)
        else:
            d.point((x, y), fill=c)
    _paste_behind(img, layer, behind)


def _paste_behind(img: Image.Image, layer: Image.Image, behind: bool) -> None:
    lit = layer.convert("L").point(_LIT_LUT)
    if behind:
        lit = ImageChops.multiply(lit, img.convert("L").point(_DARK_LUT))
    img.paste(layer, (0, 0), lit)


def decorate(img: Image.Image, t: float, settings: dict, ticker_h: int = 0) -> None:
    """Apply the Christmas overlay to a finished frame, in place."""
    W, H = img.size
    bottom = H - ticker_h - (1 if ticker_h else 0)
    layer = Image.new("RGB", img.size)
    if settings.get("garland", True):
        g, lights = _garland(W, H, bottom)
        layer.paste(g)
        d = ImageDraw.Draw(layer)
        for x, y, i in lights:
            on = math.sin(t * 2.4 + i * 1.9) > -.35                # twinkle
            c = LIGHTS[(i + int(t / 4)) % len(LIGHTS)]             # colors chase slowly
            if on:
                d.point([(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)], fill=c)
            else:
                d.point((x, y), fill=tuple(v // 4 for v in c))
    _paste_behind(img, layer, True)
    if settings.get("snow", True):
        n = SNOW_FLAKES.get(int(settings.get("snow_amount", 2)), 55) * W * H // (256 * 128)
        snow(img, t, n, seed=3, bottom=bottom)


def in_season(today: date) -> bool:
    """Dec 1 through Dec 31."""
    return today.month == 12


# ---------------------------------------------------------------- countdown widget

def next_christmas(now: datetime) -> datetime:
    xmas = now.replace(month=12, day=25, hour=0, minute=0, second=0, microsecond=0)
    return xmas if now < xmas + timedelta(days=1) else xmas.replace(year=now.year + 1)


def _tree(img: Image.Image, x: int, y: int, w: int, h: int, t: float) -> None:
    """A pixel Christmas tree in the box (x, y, w, h): star on top, three tiers, ornaments that twinkle, presents."""
    d = ImageDraw.Draw(img)
    cx = x + w // 2
    star = max(2, w // 12)
    trunk_h = max(2, h // 10)
    gifts = max(3, h // 9)
    top, base = y + star * 2 + 1, y + h - trunk_h - gifts // 2
    tiers = 3
    span = base - top
    for i in range(tiers):                               # overlapping triangles, each wider than the last
        t0 = top + span * i // (tiers + .4)
        t1 = top + span * (i + 1) // tiers
        half = int((w // 2 - 1) * (0.5 + 0.5 * (i + 1) / tiers))
        d.polygon([(cx, t0), (cx - half, t1), (cx + half, t1)], fill=PINE[2])
        d.line([(cx, t0), (cx - half, t1)], fill=PINE[1])
        d.line([(cx - half + 1, t1), (cx + half - 1, t1)], fill=PINE[3])
    tw = max(2, w // 8)
    d.rectangle([cx - tw // 2, base, cx + (tw - 1) // 2, base + trunk_h], fill=(110, 60, 25))
    # star: a plus with a bright center, gently pulsing
    k = .75 + .25 * math.sin(t * 3)
    sc = tuple(int(v * k) for v in GOLD)
    d.line([(cx - star, y + star), (cx + star, y + star)], fill=sc)
    d.line([(cx, y), (cx, y + star * 2)], fill=sc)
    d.point((cx, y + star), fill=WHITE)
    # ornaments at seeded spots inside the silhouette
    r = random.Random(w * 31 + h)
    n = max(4, (w * h) // 90)
    for i in range(n):
        oy = r.uniform(top + 3, base - 2)
        width_here = (oy - top) / max(1, span) * (w // 2 - 2)
        ox = cx + r.uniform(-width_here * .8, width_here * .8)
        c = LIGHTS[i % len(LIGHTS)]
        lit = math.sin(t * 2.2 + i * 2.3) > -.2
        sz = 1 if w < 40 else 2
        d.rectangle([int(ox), int(oy), int(ox) + sz - 1, int(oy) + sz - 1], fill=c if lit else tuple(v // 3 for v in c))
    # presents under the tree
    gy = y + h - gifts
    for gx, gc, rc in ((cx - w // 3, RED, GOLD), (cx + w // 6, (60, 120, 255), WHITE), (cx - 2, GREEN, RED)):
        gw = gifts + 1
        d.rectangle([gx, gy, gx + gw - 1, y + h - 1], fill=gc)
        d.line([(gx + gw // 2, gy), (gx + gw // 2, y + h - 1)], fill=rc)
        d.line([(gx, gy + gifts // 2), (gx + gw - 1, gy + gifts // 2)], fill=rc)


def _candy_text(img: Image.Image, cx: int, y: int, s: str, role: str, scale: int, t: float) -> None:
    """Text with letters alternating red / green / white, shifting every half second."""
    shift = int(t * 2)
    total = sum(text_width(ch, font(role)) for ch in s) * scale
    x = cx - total // 2
    for i, ch in enumerate(s):
        if ch != " ":
            big_text(img, x, y, ch, role, scale, (RED, GREEN, WHITE)[(i + shift) % 3])
        x += text_width(ch, font(role)) * scale


class ChristmasCountdown(Screen):
    """Days (and a ticking clock) until Christmas, with a tree. Any section size; Merry Christmas on the day."""
    name, label = "christmas_countdown", "Christmas countdown"
    duration = 12.0
    adaptive = True                                      # lays itself out for any section shape

    def animating(self, rc: RC, t: float) -> bool:
        return True                                      # snow, twinkling and the seconds: draw every frame

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        W, H = rc.W, rc.main_h
        now = datetime.now(rc.ctx.tz)
        xmas = next_christmas(now)
        is_day = now.date() == xmas.date()
        left = xmas - now
        days = left.days
        hms = f"{left.seconds // 3600:02d}:{left.seconds % 3600 // 60:02d}:{left.seconds % 60:02d}"
        d = ImageDraw.Draw(img)

        if H < 28:                                       # a strip: one line
            msg = "MERRY CHRISTMAS!" if is_day else f"{days} DAYS TO CHRISTMAS" if days != 1 else "1 DAY TO CHRISTMAS"
            text_center(d, W // 2, max(0, (H - 7) // 2), msg, "body", RED if int(t * 2) % 2 else GREEN)
            return

        tree_w = min(W // 3, int(H * .62))
        tree_h = H - 4
        _tree(img, 2, 2, tree_w, tree_h, t)
        cx = tree_w + 4 + (W - tree_w - 4) // 2          # center of the text column
        col_w = W - tree_w - 6

        if is_day:
            big = H >= 90 and col_w >= 140
            _candy_text(img, cx, H // 2 - (30 if big else 16), "MERRY", "bold8" if big else "bold", 2 if big else 1, t)
            _candy_text(img, cx, H // 2 + (2 if big else 0), "CHRISTMAS!", "bold8" if big else "bold", 2 if big else 1, t)
        else:
            num = str(days) if days else hms[:5]
            unit = ("DAYS" if days != 1 else "DAY") if days else "HOURS LEFT"
            if H >= 90 and col_w >= 120:                 # full size
                scale = 4 if len(num) <= 2 else 3
                y = 8
                big_text(img, cx, y, num, "bold8", scale, RED, anchor="c")
                y += 13 * scale + 2
                text_center(d, cx, y, unit, "large", GREEN)
                text_center(d, cx, y + 20, "UNTIL CHRISTMAS", "bold", WHITE)
                if days:
                    text_center(d, cx, y + 36, hms, "medium", GOLD)
            elif H >= 44:                                # tile, or a tall half (bigger number, centered)
                from .draw import big_width
                sc = 3 if H >= 96 and big_width(num, "bold8", 3) <= col_w - 4 else (2 if len(num) <= 3 else 1)
                show_hms = days and H >= 62
                blk = 13 * sc + 3 + 13 + 2 + 7 + (9 if show_hms else 0)
                y = max(2, (H - blk) // 2) if H >= 96 else 3
                big_text(img, cx, y, num, "bold8", sc, RED, anchor="c")
                y += 13 * sc + 3
                text_center(d, cx, y, unit, "bold", GREEN)
                text_center(d, cx, y + 15, "TO CHRISTMAS", "small", WHITE)
                if show_hms:
                    text_center(d, cx, y + 24, hms, "small", GOLD)
            else:                                        # short section
                text_center(d, cx, 2, f"{num} {unit}", "bold", RED)
                text_center(d, cx, 17, "TO CHRISTMAS", "small", GREEN)
        snow(img, t, max(6, W * H // 600), seed=9, bottom=H)
