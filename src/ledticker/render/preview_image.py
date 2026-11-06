"""Turn a W x H frame into an enlarged 'looks like LEDs' image (for snapshots / docs)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from ..display import Geometry


def led_look(frame: Image.Image, geom: Geometry, scale: int = 4, seams: bool = True, glow: bool = True) -> Image.Image:
    W, H = frame.size
    big = frame.resize((W * scale, H * scale), Image.NEAREST)
    if scale >= 3:
        mask = Image.new("L", (scale, scale), 0)
        r = scale * 0.42
        c = (scale - 1) / 2
        ImageDraw.Draw(mask).ellipse([c - r, c - r, c + r, c + r], fill=255)
        tile = Image.new("L", big.size)
        for y in range(0, big.height, scale):
            for x in range(0, big.width, scale):
                tile.paste(mask, (x, y))
        black = Image.new("RGB", big.size, (8, 8, 10))
        dots = Image.composite(big, black, tile)
        if glow:
            halo = big.filter(ImageFilter.GaussianBlur(scale * 0.6))
            dots = Image.blend(halo, dots, 0.82)
            dots = Image.composite(dots, Image.blend(black, halo, 0.35), tile)
        big = dots
    if seams:
        d = ImageDraw.Draw(big)
        for x in geom.seams_x:
            d.line([(x * scale, 0), (x * scale, big.height)], fill=(60, 20, 20), width=1)
        for y in geom.seams_y:
            d.line([(0, y * scale), (big.width, y * scale)], fill=(60, 20, 20), width=1)
    return big


def image_urls(store) -> list[str]:
    """All logo URLs referenced by current data (for prefetching)."""
    from .screens import ESPN_LOGO_PATH, team_logo_url
    urls: list[str] = []
    for name, st in store.states.items():
        data = st.data or {}
        if name.startswith("scores."):
            lg = name.split(".", 1)[1]
            for g in (data.get("games") or []) + (data.get("next_games") or []):
                for side in ("away", "home"):
                    u = team_logo_url(lg, g[side])
                    if u:
                        urls.append(u)
        elif name == "fantasy":
            urls += [t.get("logo") for t in data.get("teams", []) if t.get("logo")]
            people = ((data.get("potw") or {}).get("players") or []) + (data.get("week_leaders") or [])
            urls += [p.get("headshot") for p in people if p.get("headshot")]
        elif name == "props":
            urls += [p.get("headshot") for p in (data.get("props") or [])[:20] if p.get("headshot")]
        elif name.startswith("markets."):
            urls += [q.get("logo") for q in data.get("quotes", []) if q.get("logo")]
    return urls
