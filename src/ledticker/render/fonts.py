"""Pixel (bitmap) fonts: the same BDF fonts rpi-rgb-led-matrix ships, converted for Pillow on first use."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import BdfFontFile, ImageFont

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "fonts"
BDF_DIR = ASSETS / "bdf"
PIL_DIR = ASSETS / "pil"

# role -> BDF name. Heights are cap-to-baseline-ish pixel sizes.
ROLES = {
    "tiny": "tom-thumb",   # 3x5
    "small": "4x6",
    "body": "5x7",
    "body8": "5x8",
    "medium": "6x10",
    "bold": "6x13B",
    "bold7": "7x13B",
    "bold8": "8x13B",
    "large": "9x18B",
    "huge": "10x20",
}


def _convert(name: str) -> Path:
    PIL_DIR.mkdir(parents=True, exist_ok=True)
    out = PIL_DIR / f"{name}.pil"
    if not out.exists():
        with open(BDF_DIR / f"{name}.bdf", "rb") as fp:
            BdfFontFile.BdfFontFile(fp).save(str(PIL_DIR / name))
    return out


@lru_cache(maxsize=None)
def font(role: str) -> ImageFont.ImageFont:
    name = ROLES.get(role, role)
    try:
        return ImageFont.load(str(_convert(name)))
    except (OSError, FileNotFoundError):
        return ImageFont.load_default_imagefont()


_ASCII = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    "•": "·", "…": "...", " ": " ", " ": " ", "​": "",
    "▲": "^", "▼": "v",
})


def clean(s: str) -> str:
    """Bitmap fonts only cover Latin-1: map common typography to ASCII, drop the rest."""
    s = (s or "").translate(_ASCII)
    return s.encode("latin-1", "replace").decode("latin-1")


def text_width(text: str, f: ImageFont.ImageFont) -> int:
    return int(f.getlength(clean(text)))


def text_height(f: ImageFont.ImageFont) -> int:
    l, t, r, b = f.getbbox("Ag")
    return b - t


def fit(text: str, max_w: int, roles: tuple[str, ...]) -> tuple[str, ImageFont.ImageFont]:
    """Largest font from `roles` that fits; otherwise the last font with the text truncated."""
    text = clean(text)
    for r in roles:
        f = font(r)
        if text_width(text, f) <= max_w:
            return text, f
    f = font(roles[-1])
    while text and text_width(text + ".", f) > max_w:
        text = text[:-1]
    return (text.rstrip() + ".") if text else "", f
