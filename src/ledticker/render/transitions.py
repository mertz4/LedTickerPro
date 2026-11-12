"""Motion whenever the picture changes: transitions between slides, and a glint on the numbers that just updated.

Everything here is whole-image Pillow work (masks, pastes, blends), so it runs at the full frame rate on a Pi.
"""
from __future__ import annotations

import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# styles the rotation cycles through ("auto"); "flash" is kept for takeovers
AUTO = ("push_left", "dissolve", "split", "wipe", "melt", "iris", "blinds", "push_up")
STYLES = AUTO + ("flash",)
EDGE = (255, 214, 120)      # the bright leading edge of a wipe / iris
GLINT = (255, 255, 255)
GLINT_SECONDS = 0.7


def ease(p: float) -> float:
    """Ease in and out (smoothstep)."""
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


@lru_cache(maxsize=4)
def _ramp(size: tuple[int, int]) -> Image.Image:
    """Left-to-right 0..255 gradient with a slight diagonal lean."""
    W, H = size
    row = Image.linear_gradient("L").rotate(90, expand=True).resize((W + H // 2, 1))
    out = Image.new("L", size)
    for y in range(H):
        out.paste(row.crop((y // 2, 0, y // 2 + W, 1)), (0, y))
    return out


@lru_cache(maxsize=8)
def _noise(size: tuple[int, int], seed: int) -> Image.Image:
    """Random thresholds in 2x2 blocks (single pixels read as static on an LED panel; blocks read as a dissolve)."""
    W, H = size
    sw, sh = (W + 1) // 2, (H + 1) // 2
    rnd = random.Random(seed)
    small = Image.frombytes("L", (sw, sh), bytes(rnd.randrange(256) for _ in range(sw * sh)))
    return small.resize((sw * 2, sh * 2), Image.NEAREST).crop((0, 0, W, H))


@lru_cache(maxsize=8)
def _melt_delays(n: int, seed: int) -> tuple[float, ...]:
    """Per-column start delays, 0..0.4: a random walk, so neighbouring columns let go together."""
    rnd = random.Random(seed)
    d, v = [], rnd.uniform(0.1, 0.3)
    for _ in range(n):
        v = min(0.4, max(0.0, v + rnd.uniform(-0.06, 0.06)))
        d.append(v)
    return tuple(d)


def _threshold(src: Image.Image, level: float, soft: float = 24.0) -> Image.Image:
    """Mask that is 255 where src < level (0..255+soft), with a soft edge."""
    lut = [int(max(0.0, min(1.0, (level - v) / soft)) * 255) for v in range(256)]
    return src.point(lut)


def blend(style: str, old: Image.Image, new: Image.Image, p: float, seed: int = 0) -> Image.Image:
    """The frame at progress p (0 = all old, 1 = all new). Never modifies old or new."""
    W, H = new.size
    if old.size != new.size or p >= 1:
        return new.copy()
    if p <= 0:
        return old.copy()
    e = ease(p)
    if style == "push_left":
        out = Image.new("RGB", (W, H))
        dx = int(round(e * W))
        out.paste(old, (-dx, 0))
        out.paste(new, (W - dx, 0))
        return out
    if style == "push_up":
        out = Image.new("RGB", (W, H))
        dy = int(round(e * H))
        out.paste(old, (0, -dy))
        out.paste(new, (0, H - dy))
        return out
    if style == "split":            # the old picture parts in the middle, like doors
        out = new.copy()
        dy = int(round(e * (H // 2 + 1)))
        out.paste(old.crop((0, 0, W, H // 2)), (0, -dy))
        out.paste(old.crop((0, H // 2, W, H)), (0, H // 2 + dy))
        d = ImageDraw.Draw(out)
        k = 1 - e
        edge = tuple(int(c * k) for c in EDGE)
        d.line([(0, H // 2 - dy), (W - 1, H // 2 - dy)], fill=edge)
        d.line([(0, H // 2 + dy), (W - 1, H // 2 + dy)], fill=edge)
        return out
    if style == "wipe":             # leaning wipe with a glowing front edge
        soft = 24.0
        level = e * (255 + soft)
        out = Image.composite(new, old, _threshold(_ramp((W, H)), level, soft))
        front = _threshold(_ramp((W, H)), level + 6, 6)
        front = ImageChops.subtract(front, _threshold(_ramp((W, H)), level - 4, 6))
        out.paste(Image.new("RGB", (W, H), EDGE), (0, 0), front.point(lambda v: int(v * 0.8)))
        return out
    if style == "dissolve":
        return Image.composite(new, old, _threshold(_noise((W, H), seed & 7), p * (255 + 40), 40))
    if style == "melt":             # columns of the old picture slide off the bottom, one after another
        out = new.copy()
        strip = 2
        delays = _melt_delays((W + strip - 1) // strip, seed & 7)
        for i, dl in enumerate(delays):
            k = (p - dl) / 0.6
            if k <= 0:
                off = 0
            elif k >= 1:
                continue
            else:
                off = int(k * k * H)
            x = i * strip
            out.paste(old.crop((x, 0, min(W, x + strip), H - off)), (x, off))
        return out
    if style == "iris":             # a circle opens from the middle
        r = e * ((W / 2) ** 2 + (H / 2) ** 2) ** 0.5
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill=255)
        out = Image.composite(new, old, mask)
        if r > 2:
            k = 1 - e
            ImageDraw.Draw(out).ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r],
                                        outline=tuple(int(c * k) for c in EDGE))
        return out
    if style == "blinds":           # venetian blinds, the top ones opening first
        bar = 8
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        n = (H + bar - 1) // bar
        for i in range(n):
            k = min(1.0, max(0.0, p * 1.6 - 0.6 * i / max(1, n - 1)))
            h = int(round(ease(k) * bar))
            if h:
                d.rectangle([0, i * bar, W - 1, i * bar + h - 1], fill=255)
        return Image.composite(new, old, mask)
    if style == "flash":            # white-out, then the new picture fades up out of it
        white = Image.new("RGB", (W, H), (255, 255, 255))
        if p < 0.25:
            return Image.blend(old, white, p / 0.25)
        return Image.blend(white, new, ease((p - 0.25) / 0.75))
    return Image.blend(old, new, e)     # unknown style: plain cross-fade


# ------------------------------------------------------------------ data changes

_BIN = [0] * 13 + [255] * 243
_LIT = [0] * 25 + [255] * 231


def changed(a: Image.Image, b: Image.Image) -> Image.Image:
    """Mask (L, 0/255) of the pixels that differ between two frames."""
    return ImageChops.difference(a, b).convert("L").point(_BIN)


def change_mask(old: Image.Image, new: Image.Image, moving: Image.Image | None = None,
                max_fraction: float = 0.3) -> Image.Image | None:
    """Mask of the lit pixels to glint after a data update, or None if nothing (or nearly everything) changed.
    `moving` marks pixels that animate by themselves (scrolling text, clocks), which are left alone."""
    diff = changed(old, new)
    if moving is not None:
        diff = ImageChops.subtract(diff, moving.filter(ImageFilter.MaxFilter(3)))
    if diff.getbbox() is None:
        return None
    if diff.histogram()[255] > max_fraction * diff.width * diff.height:
        return None
    # widen to whole glyphs (a 3 turning into an 8 should flash as a digit, not as two specks), lit pixels only
    return ImageChops.multiply(diff.filter(ImageFilter.MaxFilter(7)), new.convert("L").point(_LIT))


def glint(img: Image.Image, mask: Image.Image, age: float) -> None:
    """Flash the changed pixels white and let them settle (draws on img). age: seconds since the change."""
    if not 0 <= age < GLINT_SECONDS or mask.size != img.size:
        return
    k = (1 - age / GLINT_SECONDS) ** 2 * 0.9
    img.paste(Image.new("RGB", img.size, GLINT), (0, 0), mask.point(lambda v: int(v * k)))
