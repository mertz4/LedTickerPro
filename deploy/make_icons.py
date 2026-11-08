"""App icon for LED Ticker Pro: a retro LED scoreboard sign — glowing dot-matrix text behind glass in a bolted metal
bezel ("heavy": solid amber rails, PRO with a drop shadow). Also renders a sheet showing it at real phone sizes.

Usage:  python deploy/make_icons.py <out_dir> [--install NAME]
  -> heavy-180.png / heavy-512.png, and sheet.png (512, Home Screen 120, Spotlight 60, Settings 40)
  --install heavy copies it to src/ledticker/web/static/icon-{180,512}.png
"""
from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ledticker.render.fonts import clean, font  # noqa: E402

G = 32                                    # LEDs across the face
AMBER, RED, GREEN, WHITE = (255, 168, 28), (255, 42, 36), (50, 255, 110), (255, 240, 220)
S = 2048                                  # supersampled canvas; downsampled at the end for clean edges


# ---------------------------------------------------------------- the LED face (a G x G grid of colors)

def grid():
    return [[None] * G for _ in range(G)]


def put_text(g, x, y, s, role, color, scale=1, bold=False):
    f = font(role)
    s = clean(s)
    l, t, r, b = f.getbbox(s or " ")
    im = Image.new("L", (max(1, r - min(l, 0)) + 2, max(1, b - t) + 1))
    ImageDraw.Draw(im).text((-min(l, 0), -t), s, font=f, fill=255)
    px = im.load()
    for yy in range(im.height):
        for xx in range(im.width):
            on = px[xx, yy] > 100 or (bold and xx > 0 and px[xx - 1, yy] > 100)   # bold: smear one dot right
            if on:
                for dy in range(scale):
                    for dx in range(scale):
                        X, Y = x + xx * scale + dx, y + yy * scale + dy
                        if 0 <= X < G and 0 <= Y < G:
                            g[Y][X] = color


def text_w(s, role, scale=1, bold=False):
    return (int(font(role).getlength(clean(s))) + (1 if bold else 0)) * scale


def shadow(g, only=None, dx=1, dy=1):
    """A dim copy of lit dots (all, or just one color), offset down-right, under the originals: gives depth."""
    lit = [(x, y, g[y][x]) for y in range(G) for x in range(G) if g[y][x] is not None and (only is None or g[y][x] == only)]
    for x, y, c in lit:
        X, Y = x + dx, y + dy
        if 0 <= X < G and 0 <= Y < G and g[Y][X] is None:
            g[Y][X] = tuple(int(v * .28) for v in c)


def rule(g, y, color, step=2, x0=1, x1=None):
    for x in range(x0, (G - 1 if x1 is None else x1)):
        if (x - x0) % step == 0:
            g[y][x] = color


# ---------------------------------------------------------------- rendering

def lerp(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def metal(size, top=(92, 94, 100), bottom=(30, 31, 35)):
    """Brushed metal: vertical gradient plus fine horizontal streaks."""
    im = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(im)
    for y in range(size):
        c = lerp(top, bottom, (y / size) ** .9)
        streak = int(6 * math.sin(y * .9) * math.sin(y * .13))
        d.line([(0, y), (size, y)], fill=tuple(max(0, min(255, v + streak)) for v in c))
    return im


def bolt(d, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 18, 20))                       # recess
    r2 = r * .82
    pts = [(cx + r2 * math.cos(math.pi / 3 * i + .3), cy + r2 * math.sin(math.pi / 3 * i + .3)) for i in range(6)]
    d.polygon(pts, fill=(150, 152, 158))                                                   # hex head
    d.polygon([(cx + (px - cx) * .7, cy + (py - cy) * .7) for px, py in pts], fill=(112, 114, 120))
    d.ellipse([cx - r * .55, cy - r * .62, cx - r * .05, cy - r * .2], fill=(205, 207, 212))  # glint


def render(g, size, bezel=.085, glass=True, bloom=1.0):
    W = S
    img = metal(W)
    d = ImageDraw.Draw(img)
    m = int(W * bezel)
    # bevel: light top-left lip, dark bottom-right lip around the window
    d.rounded_rectangle([m - 14, m - 14, W - m + 14, W - m + 14], radius=int(W * .055), fill=(14, 14, 16))
    d.rounded_rectangle([m - 14, m - 14, W - m + 14, W - m + 14], radius=int(W * .055), outline=(140, 142, 148), width=6)
    d.rounded_rectangle([m - 8, m - 8, W - m + 8, W - m + 8], radius=int(W * .05), outline=(8, 8, 9), width=10)
    face = (6, 6, 8)
    d.rounded_rectangle([m, m, W - m, W - m], radius=int(W * .045), fill=face)

    inner = W - 2 * m - int(W * .035)
    cell = inner / G
    o = (W - inner) / 2
    lit = Image.new("RGB", (W, W))
    ld = ImageDraw.Draw(lit)
    r = cell * .41
    for y in range(G):
        for x in range(G):
            cx, cy = o + (x + .5) * cell, o + (y + .5) * cell
            c = g[y][x]
            if c is None:                                             # unlit LED: dark lens with a faint rim
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(22, 20, 20))
                d.ellipse([cx - r * .55, cy - r * .7, cx + r * .1, cy - r * .2], fill=(34, 32, 32))
            else:
                ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
                bright = sum(c) > 200
                if bright:                                            # white-hot core
                    core = lerp(c, (255, 255, 255), .55)
                    ld.ellipse([cx - r * .5, cy - r * .5, cx + r * .4, cy + r * .4], fill=core)
                    ld.ellipse([cx - r * .45, cy - r * .55, cx - r * .05, cy - r * .2], fill=lerp(c, (255, 255, 255), .85))
    wide = lit.filter(ImageFilter.GaussianBlur(cell * 1.6))
    near = lit.filter(ImageFilter.GaussianBlur(cell * .45))
    k = lambda im, v: ImageChops.multiply(im, Image.new("RGB", (W, W), (v, v, v)))
    window = Image.new("L", (W, W))
    ImageDraw.Draw(window).rounded_rectangle([m, m, W - m, W - m], radius=int(W * .045), fill=255)
    glow = ImageChops.add(k(wide, int(150 * bloom)), k(near, int(190 * bloom)))
    glow = Image.composite(glow, Image.new("RGB", (W, W)), window)            # light stays behind the glass
    img = ImageChops.add(img, glow)
    img = ImageChops.lighter(img, lit)
    if glass:                                                                 # a soft diagonal reflection
        sheen = Image.new("L", (W, W))
        sd = ImageDraw.Draw(sheen)
        sd.polygon([(m, m), (W * .62, m), (m, W * .55)], fill=34)
        sheen = sheen.filter(ImageFilter.GaussianBlur(W * .02))
        sheen = ImageChops.multiply(sheen, window)
        img = Image.composite(Image.new("RGB", (W, W), (255, 255, 255)), img, sheen)
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- designs (all variations on the scoreboard)

def scoreboard_heavy():
    """Bolder: solid amber rails and PRO with a drop shadow."""
    g = grid()
    put_text(g, (G - text_w("TICKER", "tiny")) // 2, 3, "TICKER", "tiny", AMBER)
    rule(g, 10, AMBER, step=1, x0=2, x1=G - 2)
    put_text(g, (G - text_w("PRO", "body", 2)) // 2 + 1, 13, "PRO", "body", RED, 2)
    shadow(g, only=RED)
    rule(g, 29, AMBER, step=1, x0=2, x1=G - 2)
    return g


DESIGNS = {
    "heavy": (scoreboard_heavy, {"glass": True}),
}


def sheet(rendered: dict[str, Image.Image], out: Path) -> None:
    """Each design at 512, then on a phone-wallpaper strip at Home Screen (120), Spotlight (60), Settings (40)."""
    col = 560
    sh = Image.new("RGB", (col * len(rendered) + 20, 810), (28, 30, 38))
    d = ImageDraw.Draw(sh)
    try:
        lab = ImageFont.truetype("segoeuib.ttf", 28)
        small = ImageFont.truetype("segoeui.ttf", 20)
    except OSError:
        lab = small = ImageFont.load_default()
    for i, (name, big) in enumerate(rendered.items()):
        x0 = 20 + i * col
        mask = Image.new("L", (512, 512))
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, 511, 511], radius=114, fill=255)   # iOS-style corners
        sh.paste(big, (x0, 20), mask)
        d.text((x0, 548), name, font=lab, fill=(240, 240, 245))
        # wallpaper strip
        strip = Image.new("RGB", (512, 210))
        sd = ImageDraw.Draw(strip)
        for yy in range(210):
            sd.line([(0, yy), (512, yy)], fill=lerp((60, 70, 140), (150, 70, 110), yy / 210))
        sh.paste(strip, (x0, 592))
        xx = x0 + 24
        for sz, tag in ((120, "Home Screen"), (60, "Spotlight"), (40, "Settings")):
            ic = big.resize((sz, sz), Image.LANCZOS)
            mk = Image.new("L", (sz, sz))
            ImageDraw.Draw(mk).rounded_rectangle([0, 0, sz - 1, sz - 1], radius=int(sz * .225), fill=255)
            sh.paste(ic, (xx, 612 + (120 - sz) // 2), mk)
            d.text((xx, 770), tag, font=small, fill=(235, 235, 240))
            xx += sz + 56
        d.text((x0 + 24 + 34, 736), "Ticker", font=small, fill=(255, 255, 255))
    sh.save(out / "sheet.png")


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for name, (make, opts) in DESIGNS.items():
        g = make()
        big = render(g, 512, **opts)
        big.save(out / f"{name}-512.png", optimize=True)
        render(g, 180, **opts).save(out / f"{name}-180.png", optimize=True)
        rendered[name] = big
    sheet(rendered, out)
    if "--install" in argv:
        name = argv[argv.index("--install") + 1]
        static = Path(__file__).resolve().parents[1] / "src" / "ledticker" / "web" / "static"
        for sz in (180, 512):
            shutil.copy(out / f"{name}-{sz}.png", static / f"icon-{sz}.png")
        print(f"installed {name}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
