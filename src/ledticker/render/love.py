"""Love tap: a full-display hearts animation with a message.

Every style is a pure function of time since the tap started (seeded randomness, no state), so it renders the same
at any frame rate and snapshots are reproducible. All drawing is small sprite pastes on black: cheap on a Pi.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw

from .fonts import clean, font, text_height, text_width

DEFAULT_MESSAGE = "bab misses you"
STYLES = {
    "flourish": "Hearts flourish",
    "burst": "Big heart bursts",
    "rain": "Heart rain",
    "heartbeat": "Heartbeat",
}
DEFAULT_STYLE = "burst"
MAX_MESSAGE = 60

SMALL = (
    ".XX.XX.",
    "XXXXXXX",
    "XXXXXXX",
    ".XXXXX.",
    "..XXX..",
    "...X...",
)
BIG = (
    "..XXX...XXX..",
    ".XXXXX.XXXXX.",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    ".XXXXXXXXXXX.",
    "..XXXXXXXXX..",
    "...XXXXXXX...",
    "....XXXXX....",
    ".....XXX.....",
    "......X......",
)
PINKS = ((255, 60, 110), (255, 120, 170), (255, 35, 70), (255, 160, 205), (230, 25, 55), (255, 90, 140))
RED = (255, 40, 80)
TEXT = (255, 236, 244)


# ---------------------------------------------------------------- sprites + helpers

def _shade(c, k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _mix(a, b, k: float) -> tuple[int, int, int]:
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


@lru_cache(maxsize=4096)
def _heart(rows: tuple[str, ...], width: int, color: tuple[int, int, int]) -> Image.Image:
    """Heart sprite scaled (nearest-neighbour, so it stays pixel-crisp) to `width` pixels wide."""
    h, w = len(rows), len(rows[0])
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch == "X":
                px[x, y] = (*color, 255)
    sx, sy = (2, 1) if w > 7 else (1, 1)          # a little shine on the left lobe
    if w >= 7:
        px[sx, sy] = (*_mix(color, (255, 255, 255), .55), 255)
    width = max(w, width)
    return im if width == w else im.resize((width, max(1, round(width * h / w))), Image.NEAREST)


def _put(img: Image.Image, rows, width: float, color, cx: float, cy: float, k: float = 1.0) -> None:
    k = round(max(0.0, min(1.0, k)) * 10) / 10      # quantized so the sprite cache stays small
    if k <= 0 or width < 1:
        return
    sp = _heart(rows, int(width), _shade(color, k))
    img.paste(sp, (int(cx - sp.width / 2), int(cy - sp.height / 2)), sp)


def _ease_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def _beat(t: float, period: float = 1.0) -> float:
    """Lub-dub: 0 at rest, ~1 at the first thump, ~0.7 at the second."""
    ph = t % period
    return math.exp(-((ph - 0.05) / 0.06) ** 2) + 0.7 * math.exp(-((ph - 0.3) / 0.06) ** 2)


# ---------------------------------------------------------------- message

# (font role, pixel scale), biggest first: short messages get chunky 2x bold text, long ones shrink to fit.
TEXT_SIZES = (("bold8", 2), ("bold7", 2), ("bold", 2), ("huge", 1), ("large", 1), ("bold8", 1), ("bold7", 1),
              ("bold", 1), ("medium", 1), ("body", 1), ("small", 1))


def _wrap(msg: str, f, max_w: int) -> list[str] | None:
    lines, cur = [], ""
    for word in msg.split():
        if text_width(word, f) > max_w:
            return None
        trial = f"{cur} {word}".strip()
        if text_width(trial, f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


@lru_cache(maxsize=64)
def layout(msg: str, max_w: int, max_h: int) -> tuple[tuple[str, ...], str, int, int]:
    """Largest text the message fits in (wrapped onto as many lines as the height allows).
    Returns (lines, font role, scale, line pitch in pixels)."""
    msg = " ".join(clean(msg or "").split()) or DEFAULT_MESSAGE
    for role, scale in TEXT_SIZES:
        f = font(role)
        pitch = (text_height(f) + 2) * scale + 1
        lines = _wrap(msg, f, max_w // scale)
        if lines and len(lines) * pitch - 1 <= max_h:
            return tuple(lines), role, scale, pitch
    role = TEXT_SIZES[-1][0]                       # last resort: hard-break characters
    f = font(role)
    lines, cur = [], ""
    for ch in msg:
        if text_width(cur + ch, f) > max_w:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    lines.append(cur)
    pitch = text_height(f) + 2
    return tuple(lines[:max(1, max_h // pitch)]), role, 1, pitch


def text_block(msg: str, max_w: int, max_h: int) -> tuple[int, int]:
    """(width, height) the message will take up."""
    lines, role, scale, pitch = layout(msg, max_w, max_h)
    return max(text_width(s, font(role)) for s in lines) * scale, len(lines) * pitch - 1


@lru_cache(maxsize=256)
def _line_img(s: str, role: str, scale: int, color: tuple) -> Image.Image:
    """One line of text, scaled up crisp, with a black outline baked in so it reads over hearts."""
    f = font(role)
    l, t, r, b = f.getbbox("Ag")
    w, h = text_width(s, f), b - t
    core = Image.new("L", (w, h), 0)
    ImageDraw.Draw(core).text((0, -t), s, font=f, fill=255)
    if scale > 1:
        core = core.resize((w * scale, h * scale), Image.NEAREST)
    out = Image.new("RGBA", (core.width + 2, core.height + 2), (0, 0, 0, 0))
    black = Image.new("RGBA", core.size, (0, 0, 0, 255))
    for ox in (0, 1, 2):
        for oy in (0, 1, 2):
            out.paste(black, (ox, oy), core)
    out.paste(Image.new("RGBA", core.size, (*color, 255)), (1, 1), core)
    return out


def _draw_message(img: Image.Image, msg: str, cy: float, k: float, max_w: int, max_h: int, dy: int = 0) -> int:
    """Centered on the display at height cy. Returns the block height."""
    if k <= 0:
        return 0
    lines, role, scale, pitch = layout(msg, max_w, max_h)
    block = len(lines) * pitch - 1
    y0 = int(cy - block / 2) + dy
    col = _shade(TEXT, round(min(1.0, k) * 10) / 10)
    for i, s in enumerate(lines):
        im = _line_img(s, role, scale, col)
        img.paste(im, ((img.width - im.width) // 2, y0 + i * pitch - 1), im)
    return block


def _drifters(img: Image.Image, t: float, n: int, seed: int, k_all: float = 1.0, down: bool = False,
              speeds=(9, 22)) -> None:
    """Hearts drifting up (or falling) across the whole display, each with its own sway, size and depth."""
    W, H = img.size
    for i in range(n):
        r = random.Random(seed * 7919 + i)
        speed = r.uniform(*speeds)
        span = H + 24
        tt = t + r.random() * span / speed
        cycle, prog = divmod(tt * speed / span, 1.0)
        r2 = random.Random(seed * 104729 + i * 131 + int(cycle))
        size = r2.choice((7, 7, 7, 14, 14, 21))
        depth = {7: .45, 14: .75, 21: 1.0}[size]
        x = r2.uniform(4, W - 4) + math.sin(tt * r.uniform(.8, 1.8) + i) * r.uniform(3, 9)
        y = (-12 + prog * span) if down else (H + 12 - prog * span)
        fade = min(1.0, prog * 5, (1 - prog) * 5) if not down else min(1.0, (1 - prog) * 5)
        _put(img, SMALL, size, r2.choice(PINKS), x, y, fade * depth * k_all)


# ---------------------------------------------------------------- styles

def _flourish(img: Image.Image, t: float, msg: str) -> None:
    """A wreath of hearts blooms out from the center and keeps swirling, twinkling, around the message."""
    W, H = img.size
    cx, cy = W / 2, H / 2
    _drifters(img, t, 10, 3, k_all=min(1.0, t / 2) * .6)
    tw, th = text_block(msg, W - 12, H - 44)
    rx_in, ry_in = min(W / 2 - 10, tw / 2 + 16), min(H / 2 - 8, th / 2 + 16)   # inner ring clears the text
    rx_out, ry_out = W / 2 - 8, H / 2 - 7
    n = 26
    for i in range(n):
        r = random.Random(900 + i)
        delay = (i % 13) / 13 * 0.9 + r.uniform(0, .25)
        tt = t - delay
        if tt <= 0:
            continue
        p = _ease_out(tt / 1.5)
        outer = i % 2 == 1
        ang = i / n * 2 * math.pi + t * 0.25 * (1 if outer else -1) + (1 - p) * 2.2
        rx, ry = (rx_out, ry_out) if outer else (max(rx_in, rx_out * .8), max(ry_in, ry_out * .72))
        x = cx + math.cos(ang) * rx * p
        y = cy + math.sin(ang) * ry * p + math.sin(t * 2.2 + i) * 2
        size = 7 + 7 * round(p * r.choice((1, 1, 2)))
        twinkle = .82 + .18 * math.sin(t * 3.1 + i * 1.7)
        _put(img, SMALL, size, PINKS[i % len(PINKS)], x, y, min(1.0, tt * 3) * twinkle)
    if t < 0.6:                                   # the bud it blooms from
        _put(img, BIG, 26 + 26 * (t / .6), RED, cx, cy, 1 - t / .6)
    _draw_message(img, msg, cy, min(1.0, (t - .9) / .6), W - 12, H - 44)


def _burst_pieces() -> list[tuple]:
    """The big heart (at 9x scale) broken into 3x3 shards, each with its launch velocity and brightness."""
    out = []
    s = 9
    w, h = len(BIG[0]) * s, len(BIG) * s
    for y, row in enumerate(BIG):
        for x, ch in enumerate(row):
            if ch != "X":
                continue
            for sy in range(0, s, 3):
                for sx in range(0, s, 3):
                    px, py = x * s + sx - w / 2, y * s + sy - h / 2
                    r = random.Random(len(out))
                    if r.random() < .5:                   # half the shards are plenty
                        out.append(None)
                        continue
                    dist = math.hypot(px, py) or 1
                    v = r.uniform(70, 230)
                    out.append((px, py, px / dist * v, py / dist * v - r.uniform(20, 70), r.uniform(.6, 1),
                                PINKS[(x * 7 + y * 3 + sx + sy) % len(PINKS)]))
    return [p for p in out if p]


_PIECES = _burst_pieces()
GROW = 1.8


def _burst(img: Image.Image, t: float, msg: str) -> None:
    """One heart swells with a quickening heartbeat, then explodes into shards and hearts, revealing the message."""
    W, H = img.size
    cx, cy = W / 2, H / 2
    if t < GROW:
        p = t / GROW
        width = 13 + (13 * 9 - 13) * p ** 1.7
        width *= 1 + .07 * _beat(t, 0.8 - .45 * p)       # the beat speeds up as it grows
        shake = (random.Random(int(t * 30)).uniform(-1, 1) * 2 * p ** 3)
        _put(img, BIG, width, RED, cx + shake, cy + shake * .5, .6 + .4 * p)
        return
    te = t - GROW
    if te < 0.15:                                       # flash
        ImageDraw.Draw(img).rectangle([0, 0, W, H], fill=_shade((255, 120, 170), (1 - te / .15) * .7))
    d = ImageDraw.Draw(img)
    g = 120.0
    if te < 2.2:
        fade = max(0.0, 1 - te / 2.2)
        sz = 3 if te < .8 else 2
        drop = .5 * g * te * te
        for px, py, vx, vy, bright, col in _PIECES:
            x, y = cx + px + vx * te, cy + py + vy * te + drop
            if 0 <= x < W and 0 <= y < H:
                d.rectangle([int(x), int(y), int(x) + sz - 1, int(y) + sz - 1], fill=_shade(col, fade * bright))
        for j in range(18):                              # little hearts thrown out with the shards
            r = random.Random(400 + j)
            ang = j / 18 * 2 * math.pi + r.uniform(-.2, .2)
            v = r.uniform(80, 160)
            x = cx + math.cos(ang) * v * te
            y = cy + math.sin(ang) * v * te * .7 + .5 * g * te * te
            _put(img, SMALL, 14, PINKS[j % len(PINKS)], x, y, 1 - te / 2.2)
    _drifters(img, t, 14, 11, k_all=min(1.0, max(0.0, te - .8) / 1.5) * .8)
    bounce = int(-7 * math.exp(-te * 5) * math.cos(te * 18))   # the message pops in with a little bounce
    _draw_message(img, msg, cy, min(1.0, te / .25), W - 12, H - 24, dy=bounce)


def _rain(img: Image.Image, t: float, msg: str) -> None:
    """Hearts drift down like confetti behind the message."""
    W, H = img.size
    _drifters(img, t, 34, 5, k_all=min(1.0, t / .8), down=True, speeds=(18, 40))
    _draw_message(img, msg, H / 2, min(1.0, (t - .4) / .6), W - 12, H - 30)


def _heartbeat(img: Image.Image, t: float, msg: str) -> None:
    """A big heart beating lub-dub, a ripple going out with every beat, the message underneath."""
    W, H = img.size
    block = text_block(msg, W - 16, 44)[1]
    text_cy = H - 8 - block / 2
    area = H - block - 18
    base = min(13 * 6, int(area / 11 * 13 / 1.25))
    hx, hy = W / 2, 6 + area / 2
    period = 1.1
    ph = t % period
    for back in (0, 1):                                  # ripples from this beat and the last
        age = ph + back * period
        if age < 1.4:
            rad = base / 2 + age * 70
            k = max(0.0, 1 - age / 1.4) * .5
            d = ImageDraw.Draw(img)
            d.ellipse([hx - rad * 1.3, hy - rad * .9, hx + rad * 1.3, hy + rad * .9], outline=_shade(RED, k))
    _drifters(img, t, 8, 7, k_all=.45)
    grow = min(1.0, t / .5)
    _put(img, BIG, base * grow * (1 + .16 * _beat(t, period)), RED, hx, hy)
    _draw_message(img, msg, text_cy, min(1.0, (t - .5) / .5), W - 16, 44)


_STYLE_FN = {"flourish": _flourish, "burst": _burst, "rain": _rain, "heartbeat": _heartbeat}


def frame(t: float, message: str = DEFAULT_MESSAGE, style: str = DEFAULT_STYLE, size=(256, 128)) -> Image.Image:
    """The love-tap frame `t` seconds after it started."""
    img = Image.new("RGB", size, (0, 0, 0))
    _STYLE_FN.get(style, _flourish)(img, max(0.0, t), message or DEFAULT_MESSAGE)
    return img
