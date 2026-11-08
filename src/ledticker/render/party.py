"""Pregame / party mode: DJ-style light show scenes driven by a beat clock (tap tempo from the dashboard).

There's no microphone, so the music's tempo comes from the dashboard: tap along and the beat clock locks to it
(BPM + phase). Every scene is a pure function of the beat position, so it renders the same at any frame rate.

Scenes: lasers, equalizer, tunnel, warp, plasma. "party" (the auto show) changes scene every 8 bars and drops the
message on the beat every 4 bars. Strobe flashes are opt-in (off by default).
"""
from __future__ import annotations

import colorsys
import math
import random
import time
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .screens import RC, Screen

SCENES = {"lasers": "Lasers", "equalizer": "Equalizer", "tunnel": "Neon tunnel", "warp": "Hyperspace warp",
          "plasma": "Plasma"}
AUTO_ORDER = ("lasers", "equalizer", "tunnel", "warp", "plasma")
BARS_PER_SCENE = 8
DEFAULTS = {"bpm": 124, "message": "PREGAME", "strobe": False}


# ---------------------------------------------------------------- beat clock

class BeatClock:
    """Tempo + phase. tap() locks both to taps; configure() takes the saved settings."""

    def __init__(self):
        self.bpm = float(DEFAULTS["bpm"])
        self.anchor = 0.0                 # monotonic time of a beat
        self.tapped = False               # a live tap tempo overrides the saved BPM until reset
        self.message = DEFAULTS["message"]
        self.strobe = DEFAULTS["strobe"]
        self._taps: list[float] = []

    def configure(self, settings: dict) -> None:
        self.message = str(settings.get("message", DEFAULTS["message"]))[:40]
        self.strobe = bool(settings.get("strobe", False))
        if not self.tapped:
            self.bpm = float(settings.get("bpm") or DEFAULTS["bpm"])

    def tap(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if self._taps and now - self._taps[-1] > 2.0:     # a pause starts a fresh count
            self._taps = []
        self._taps = (self._taps + [now])[-8:]
        if len(self._taps) >= 2:
            gaps = [b - a for a, b in zip(self._taps, self._taps[1:])]
            self.bpm = max(60.0, min(200.0, 60.0 / (sum(gaps) / len(gaps))))
            self.tapped = True
        self.anchor = now                                  # the beat lands on the tap
        return self.bpm

    def reset(self) -> None:
        self.tapped, self._taps = False, []

    def beats(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        return (now - self.anchor) * self.bpm / 60.0


CLOCK = BeatClock()


# ---------------------------------------------------------------- helpers

@lru_cache(maxsize=8192)
def hue(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _hue(h: float, s: float = 1.0, v: float = 1.0):
    return hue(round(h % 1.0, 3), round(s, 2), round(max(0.0, min(1.0, v)), 2))


def pulse(b: float, sharp: float = 5.0) -> float:
    """1 on the beat, decaying to ~0 before the next."""
    return math.exp(-(b % 1.0) * sharp)


def _scale(img: Image.Image, k: float) -> Image.Image:
    if k >= 0.99:
        return img
    k = max(0.0, k)
    return img.point(lambda v: int(v * k))


def _glow(layer: Image.Image, radius: float = 2.0, amount: int = 200) -> Image.Image:
    """Bloom: blurred copy added under the sharp lines (lasers in haze, neon)."""
    blur = layer.filter(ImageFilter.GaussianBlur(radius))
    blur = ImageChops.multiply(blur, Image.new("RGB", layer.size, (amount, amount, amount)))
    return ImageChops.add(ImageChops.add(blur, blur), layer)


# ---------------------------------------------------------------- scenes

def lasers(W: int, H: int, b: float) -> Image.Image:
    layer = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(layer)
    bar = int(b // 4)
    p = pulse(b, 4)
    reach = W + H
    emitters = (0.18, 0.5, 0.82) if bar % 2 == 0 else (0.1, 0.36, 0.64, 0.9)
    for k, fx in enumerate(emitters):
        ex, ey = W * fx, H + 2
        base = -math.pi / 2 + 0.75 * math.sin(b * math.pi / 8 + k * 1.7) * (1 if k % 2 else -1)
        spread = 0.1 + 0.08 * p + 0.05 * math.sin(b * math.pi / 4)
        col = _hue(bar * 0.13 + k / len(emitters), 1.0, 0.75 + 0.25 * p)
        for j in range(-2, 3):
            a = base + j * spread
            d.line([(ex, ey), (ex + math.cos(a) * reach, ey + math.sin(a) * reach)], fill=col, width=1)
        d.ellipse([ex - 3, ey - 5, ex + 3, ey + 1], fill=(255, 255, 255))
    if bar % 4 == 3:                                   # horizontal scanners on every 4th bar
        for i in range(3):
            y = (H * 0.15) + (H * 0.6) * (0.5 + 0.5 * math.sin(b * math.pi / 2 + i * 2.1))
            d.line([(0, y), (W, y + (i - 1) * 6)], fill=_hue(bar * 0.13 + 0.5 + i * 0.1), width=1)
    img = _glow(layer, 2.2, 150)
    haze = Image.new("RGB", (W, H))                    # faint floor haze that breathes with the beat
    hd = ImageDraw.Draw(haze)
    for y in range(H // 2, H):
        v = int(18 * ((y - H / 2) / (H / 2)) * (0.6 + 0.4 * p))
        hd.line([(0, y), (W, y)], fill=(v // 2, v // 3, v))
    return ImageChops.add(img, haze)


def _eq_level(i: int, n: int, b: float) -> float:
    low = 1 - i / n                                    # bass on the left kicks hardest
    kick = pulse(b, 6) * (0.35 + 0.55 * low)
    wob = 0.5 + 0.25 * math.sin(b * 1.7 + i * 0.9) + 0.15 * math.sin(b * 3.1 + i * 2.3) + 0.1 * math.sin(b * 0.6 + i * 0.4)
    return max(0.05, min(1.0, 0.55 * wob + kick))


def equalizer(W: int, H: int, b: float) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    bw, gap = (6, 2) if W >= 128 else (3, 1)
    n = W // (bw + gap)
    x0 = (W - n * (bw + gap) + gap) // 2
    seg = 3                                            # LED segments: 2 px lit, 1 px gap
    bar = int(b // 4)
    for i in range(n):
        lvl = _eq_level(i, n, b)
        h = int(lvl * (H - 6))
        x = x0 + i * (bw + gap)
        for y in range(H - 2, H - 2 - h, -seg):
            frac = (H - 2 - y) / H
            col = _hue(0.33 - 0.33 * frac, 1.0, 1.0) if bar % 2 == 0 else _hue(0.55 + 0.45 * i / n + frac * 0.1)
            d.rectangle([x, y - 1, x + bw - 1, y], fill=col)
        peak = max(_eq_level(i, n, b - k * 0.12) - k * 0.03 for k in range(8))   # caps fall slowly
        py = H - 2 - int(min(1.0, peak) * (H - 6)) - 2
        d.rectangle([x, py, x + bw - 1, py], fill=(255, 255, 255))
    return img


def tunnel(W: int, H: int, b: float) -> Image.Image:
    layer = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(layer)
    cx, cy = W / 2 + 10 * math.sin(b * math.pi / 8), H / 2 + 5 * math.cos(b * math.pi / 6)
    maxr = math.hypot(W, H) / 2
    n = 10
    p = pulse(b, 3)
    diamond = int(b // 8) % 2 == 1
    for i in range(n):
        f = ((b / 2 + i / n) % 1.0)                    # ring position 0 (center) .. 1 (edge), one trip per 2 beats
        r = (f ** 2) * maxr
        if r < 1:
            continue
        col = _hue(i / n + b * 0.05, 1.0, min(1.0, 0.25 + f * 0.9 + 0.3 * p))
        w = 1 if f < 0.5 else 2
        if diamond:
            d.polygon([(cx, cy - r), (cx + r * 1.6, cy), (cx, cy + r), (cx - r * 1.6, cy)], outline=col, width=w)
        else:
            d.rounded_rectangle([cx - r * 1.6, cy - r, cx + r * 1.6, cy + r], radius=max(1, int(r * .4)), outline=col, width=w)
    return _glow(layer, 1.6, 170)


_STARS = [(random.Random(i).uniform(0, math.tau), random.Random(i * 7 + 1).random(), random.Random(i * 13 + 2).uniform(.6, 1.4))
          for i in range(140)]


def warp(W: int, H: int, b: float) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2
    maxr = math.hypot(W, H) / 2
    p = pulse(b, 4)
    speed = 0.25 + 0.35 * p
    tint = int(b // 8) % 3
    for a, off, sp in _STARS:
        f = (off + b * 0.18 * sp + p * 0.02) % 1.0
        r1 = (f ** 2.2) * maxr * 1.1
        r0 = r1 * (1 - speed * 0.5 * (0.4 + f))
        k = min(1.0, f * 1.6)
        col = ((int(255 * k), int(255 * k), int(255 * k)), _hue(0.55, 0.6, k), _hue(0.85, 0.6, k))[tint]
        ca, sa = math.cos(a) * 1.5, math.sin(a)
        d.line([(cx + ca * r0, cy + sa * r0), (cx + ca * r1, cy + sa * r1)], fill=col, width=1 if f < .7 else 2)
    return img


_RAINBOW = [c for i in range(256) for c in hue(i / 256)]   # palette: index -> fully saturated hue


@lru_cache(maxsize=8)
def _plasma_grid(sw: int, sh: int) -> tuple:
    dist = tuple(math.hypot(x - sw / 2, y - sh / 2) * 0.35 for y in range(sh) for x in range(sw))
    return dist


def plasma(W: int, H: int, b: float) -> Image.Image:
    """Classic demo-scene plasma at quarter resolution, scaled up into chunky LED pixels.
    Row/column terms are computed once per frame and colors come from a palette, so it's cheap on a Pi."""
    sw, sh = max(8, W // 4), max(4, H // 4)
    t = b * 0.35
    p = pulse(b, 4)
    sx = [math.sin(x * 0.22 + t) for x in range(sw)]
    sy = [math.sin(y * 0.31 + t * 1.3) for y in range(sh)]
    sxy = [math.sin(i * 0.13 + t * 0.7) for i in range(sw + sh)]
    dist = _plasma_grid(sw, sh)
    off = (b * 0.02) % 1.0
    sin, t2 = math.sin, t * 2
    idx = bytearray(sw * sh)
    i = 0
    for y in range(sh):
        ry = sy[y]
        for x in range(sw):
            v = sx[x] + ry + sxy[x + y] + sin(dist[i] - t2)
            idx[i] = int(((v * 0.12 + off) % 1.0) * 255)
            i += 1
    small = Image.frombytes("P", (sw, sh), bytes(idx))
    small.putpalette(_RAINBOW)
    img = small.convert("RGB").resize((W, H), Image.NEAREST)
    return _scale(img, 0.8 + 0.2 * p)


SCENE_FN = {"lasers": lasers, "equalizer": equalizer, "tunnel": tunnel, "warp": warp, "plasma": plasma}


# ---------------------------------------------------------------- the show

def _message(img: Image.Image, msg: str, b: float, bar_hue: float) -> None:
    """The drop: the message slams in on the downbeat of every 4th bar and holds for one bar."""
    if not msg.strip():
        return
    pos = b % 16
    if pos >= 4:
        return
    from .love import _line_img, layout   # same chunky outlined text renderer as the love tap
    W, H = img.size
    lines, role, scale, pitch = layout(msg, W - 8, H - 8)
    k = 1.0 if pos < 3 else 1 - (pos - 3)              # fade out across the last beat
    col = _hue(bar_hue + 0.5, 0.35, k) if pulse(b, 8) < 0.5 else (int(255 * k),) * 3
    block = len(lines) * pitch - 1
    shake = int(3 * pulse(b, 10)) * (1 if int(b) % 2 else -1)
    y0 = (H - block) // 2
    for i, s in enumerate(lines):
        im = _line_img(s, role, scale, col)
        img.paste(im, ((W - im.width) // 2 + shake, y0 + i * pitch - 1), im)


def frame(scene: str, b: float, size=(256, 128), message: str = "", strobe: bool = False) -> Image.Image:
    """One frame of a scene ("party" = the auto show) at beat position b."""
    W, H = size
    show = scene == "party"
    if show:
        scene = AUTO_ORDER[int(b // (4 * BARS_PER_SCENE)) % len(AUTO_ORDER)]
    img = SCENE_FN.get(scene, lasers)(W, H, b)
    if show:
        into = b % (4 * BARS_PER_SCENE)
        if into < 1:                                    # each new scene fades up over its first beat
            img = _scale(img, 0.3 + 0.7 * into)
        _message(img, message, b, int(b // 4) * 0.13)
    if strobe and (b % 32) >= 28 and (b * 2) % 1 < 0.15:   # build-up strobe in the last bar of every 8
        img = Image.new("RGB", size, (255, 255, 255))
    return img


class PartyScene(Screen):
    """A party scene as a widget. Fills any section; immersive (no bottom ticker) when shown full screen."""
    duration = 60.0
    immersive = True

    def __init__(self, scene: str):
        self.scene = scene
        self.name = "party" if scene == "party" else f"party_{scene}"
        self.label = "Pregame: auto DJ show" if scene == "party" else f"Pregame: {SCENES[scene].lower()}"

    def animating(self, rc: RC, t: float) -> bool:
        return True

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        f = frame(self.scene, CLOCK.beats(), (rc.W, rc.main_h), CLOCK.message, CLOCK.strobe)
        img.paste(f, (0, 0))


def all_party() -> list[PartyScene]:
    return [PartyScene("party")] + [PartyScene(s) for s in SCENES]
