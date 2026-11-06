"""Small drawing toolkit for LED layouts: pixel text, scaled text, arrows, colors, badges."""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .fonts import clean, font, text_width

WHITE = (255, 255, 255)
GRAY = (150, 150, 160)
DIM = (90, 90, 100)
DARK = (35, 35, 45)
GREEN = (40, 220, 90)
RED = (255, 60, 50)
YELLOW = (255, 210, 0)
ORANGE = (255, 140, 0)
BLUE = (70, 150, 255)
MAROON = (128, 20, 30)          # Texas A&M-ish accent
GOLD = (255, 190, 40)


def hex_color(s: str | None, default=(120, 120, 130)) -> tuple[int, int, int]:
    if not s:
        return default
    s = s.strip().lstrip("#")
    if len(s) != 6:
        return default
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return default


def luminance(c) -> float:
    r, g, b = c[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def readable_on_black(c, min_lum=70) -> tuple[int, int, int]:
    """Brighten very dark team colors so they show up on an LED (black = off)."""
    r, g, b = c[:3]
    lum = luminance(c)
    if lum >= min_lum:
        return (r, g, b)
    if lum < 1:
        return (min_lum,) * 3
    k = min_lum / lum
    return tuple(min(255, int(v * k + 20)) for v in (r, g, b))


def contrast_text(bg) -> tuple[int, int, int]:
    return (0, 0, 0) if luminance(bg) > 150 else WHITE


def dim(c, k=0.5):
    return tuple(int(v * k) for v in c[:3])


def text(d: ImageDraw.ImageDraw, x: int, y: int, s: str, role: str = "body", fill=WHITE) -> int:
    """Draw pixel text with its top-left at (x, y). Returns the width drawn."""
    f = font(role)
    s = clean(s)
    l, t, r, b = f.getbbox("Ag")
    d.text((x, y - t), s, font=f, fill=fill)
    return text_width(s, f)


def text_right(d, right_x: int, y: int, s: str, role="body", fill=WHITE) -> int:
    w = text_width(s, font(role))
    text(d, right_x - w, y, s, role, fill)
    return w


def text_center(d, cx: int, y: int, s: str, role="body", fill=WHITE) -> int:
    w = text_width(s, font(role))
    text(d, cx - w // 2, y, s, role, fill)
    return w


@lru_cache(maxsize=256)
def _scaled_text(s: str, role: str, scale: int, fill: tuple) -> Image.Image:
    f = font(role)
    s = clean(s)
    l, t, r, b = f.getbbox(s or " ")
    w, h = max(1, r - min(l, 0)), max(1, b - t)
    small = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(small).text((-min(l, 0), -t), s, font=f, fill=fill)
    return small.resize((w * scale, h * scale), Image.NEAREST)


def big_text(img: Image.Image, x: int, y: int, s: str, role="huge", scale=2, fill=WHITE, anchor="l") -> int:
    """Chunky scaled pixel text (e.g. scores). anchor: l / c / r on x."""
    im = _scaled_text(s, role, scale, tuple(fill))
    if anchor == "c":
        x -= im.width // 2
    elif anchor == "r":
        x -= im.width
    img.paste(im, (x, y), im)
    return im.width


def big_width(s: str, role="huge", scale=2) -> int:
    return _scaled_text(s, role, scale, WHITE).width


def arrow(d, x: int, y: int, up: bool, size: int = 5, fill=None):
    """Solid triangle arrow with its box at (x, y, x+size, y+size)."""
    fill = fill or (GREEN if up else RED)
    h = (size + 1) // 2
    if up:
        d.polygon([(x, y + h + 1), (x + size - 1, y + h + 1), (x + (size - 1) / 2, y + 1)], fill=fill)
    else:
        d.polygon([(x, y + 1), (x + size - 1, y + 1), (x + (size - 1) / 2, y + h + 1)], fill=fill)


def triangle_right(d, x, y, h, fill):
    d.polygon([(x, y), (x, y + h - 1), (x + (h - 1) // 2, y + (h - 1) // 2)], fill=fill)


def badge(img: Image.Image, x: int, y: int, label: str, bg, fg=None, role="body", h: int = 11,
          pad: int = 3, min_w: int = 0) -> int:
    """Rounded tile with centered text. Returns width."""
    d = ImageDraw.Draw(img)
    f = font(role)
    tw = text_width(label, f)
    w = max(min_w, tw + pad * 2)
    d.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=2, fill=tuple(bg))
    l, t, r, b = f.getbbox("A")
    th = b - t
    text(d, x + (w - tw) // 2, y + (h - th) // 2, label, role, fg or contrast_text(bg))
    return w


def paste_icon(img: Image.Image, im: Image.Image | None, x: int, y: int) -> bool:
    if im is None:
        return False
    img.paste(im, (x, y), im)
    return True


def team_fallback(img: Image.Image, x: int, y: int, size: int, abbr: str, color) -> None:
    """When a logo isn't cached yet: abbreviation on a team-colored disc."""
    d = ImageDraw.Draw(img)
    c = readable_on_black(color, 60)
    d.ellipse([x, y, x + size - 1, y + size - 1], fill=c)
    role = "bold" if size >= 40 else "body" if size >= 20 else "tiny"
    text_center(d, x + size // 2, y + size // 2 - (6 if role == "bold" else 3), abbr[:4], role, contrast_text(c))
