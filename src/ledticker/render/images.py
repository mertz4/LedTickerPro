"""Logo/image cache: download once, rasterize SVGs, pre-scale to LED sizes. Never blocks a frame.

The renderer calls `cache.get(url, size)`. If the image is ready it returns a PIL RGBA image;
otherwise it queues a background download and returns None (the caller draws a fallback).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from pathlib import Path

import httpx
from PIL import Image, ImageFilter

log = logging.getLogger("ledticker.images")

MAX_BYTES = 3 * 1024 * 1024
RECHECK_SECONDS = 86400
FAIL_RETRY_SECONDS = 1800
ALLOWED_HOSTS = (  # only fetch images from the APIs we use (no arbitrary URLs)
    "espncdn.com", "finnhub.io", "coingecko.com",
)


def _host_ok(url: str) -> bool:
    try:
        host = httpx.URL(url).host or ""
    except Exception:
        return False
    return url.startswith("https://") and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _rasterize(data: bytes, content_type: str, px: int = 256) -> Image.Image:
    if b"<svg" in data[:2048] or "svg" in content_type:
        import resvg_py
        png = resvg_py.svg_to_bytes(svg_string=data.decode("utf-8", "replace"), width=px, height=px)
        return Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.convert("RGBA")


def trim(im: Image.Image) -> Image.Image:
    """Crop transparent margins so logos fill their box."""
    bbox = im.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    return im.crop(bbox) if bbox else im


def fit_square(im: Image.Image, size: int, sharpen: bool = True) -> Image.Image:
    im = trim(im)
    w, h = im.size
    scale = size / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    small = im.resize((nw, nh), Image.LANCZOS)
    if sharpen and size <= 48:
        rgb = small.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=2))
        rgb.putalpha(small.getchannel("A"))
        small = rgb
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(small, ((size - nw) // 2, (size - nh) // 2), small)
    # LEDs have no "semi-transparent": snap alpha so edges don't turn into dim mush
    a = out.getchannel("A").point(lambda v: 0 if v < 70 else 255)
    out.putalpha(a)
    return lift_dark(out)


def lift_dark(im: Image.Image, target: float = 85.0, max_gain: float = 2.4) -> Image.Image:
    """Black = LED off, so very dark logos (maroon, navy) vanish. Brighten them toward `target` luminance."""
    a = im.getchannel("A")
    lum = im.convert("L")
    hist = [0] * 256
    for v, al in zip(lum.tobytes(), a.tobytes()):
        if al:
            hist[v] += 1
    n = sum(hist)
    if not n:
        return im
    mean = sum(i * c for i, c in enumerate(hist)) / n
    if mean >= target:
        return im
    gain = min(max_gain, target / max(mean, 1.0))
    rgb = im.convert("RGB").point(lambda v: min(255, int(v * gain)))
    rgb.putalpha(a)
    return rgb


def dominant_color(im: Image.Image) -> tuple[int, int, int]:
    """Most common saturated-ish color in an RGBA logo (for badge backgrounds)."""
    small = im.convert("RGBA").resize((32, 32))
    counts: dict[tuple[int, int, int], int] = {}
    raw = small.tobytes()
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if a < 128:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx < 40 or (mx > 225 and mn > 225):  # skip black/white
            continue
        key = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
        counts[key] = counts.get(key, 0) + 1 + (mx - mn) // 32
    if not counts:
        return (90, 90, 100)
    return max(counts, key=counts.get)


class ImageCache:
    def __init__(self, root: Path):
        self.dir = root / "data" / "images"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[tuple[str, int], Image.Image] = {}
        self._orig: dict[str, Image.Image] = {}
        self._pending: set[str] = set()
        self._failed: dict[str, float] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._client: httpx.AsyncClient | None = None

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".png")

    def get(self, url: str | None, size: int) -> Image.Image | None:
        if not url:
            return None
        key = (url, size)
        if key in self._mem:
            return self._mem[key]
        orig = self._orig.get(url)
        if orig is None:
            p = self._path(url)
            if p.exists():
                try:
                    orig = Image.open(p).convert("RGBA")
                    self._orig[url] = orig
                    if time.time() - p.stat().st_mtime > RECHECK_SECONDS:
                        self._enqueue(url)
                except OSError:
                    orig = None
            if orig is None:
                self._enqueue(url)
                return None
        im = fit_square(orig, size)
        self._mem[key] = im
        return im

    def original(self, url: str | None) -> Image.Image | None:
        if url and self.get(url, 16) is not None:
            return self._orig.get(url)
        return None

    def _enqueue(self, url: str) -> None:
        if url in self._pending or not _host_ok(url):
            return
        if time.time() - self._failed.get(url, 0) < FAIL_RETRY_SECONDS:
            return
        self._pending.add(url)
        if self._queue is not None:
            self._queue.put_nowait(url)

    async def _download(self, url: str) -> None:
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            if not _host_ok(str(r.url)):
                raise ValueError(f"redirected to disallowed host {r.url.host}")
            if len(r.content) > MAX_BYTES:
                raise ValueError("image too large")
            im = _rasterize(r.content, r.headers.get("content-type", ""))
            im.save(self._path(url))
            self._orig[url] = im
            for k in [k for k in self._mem if k[0] == url]:
                del self._mem[k]
            log.info("cached image %s", url)
        except Exception as e:
            self._failed[url] = time.time()
            log.warning("image failed %s: %s", url, e)
        finally:
            self._pending.discard(url)

    async def run(self) -> None:
        """Background worker: downloads queued images a few at a time."""
        self._queue = asyncio.Queue()
        for u in list(self._pending):
            self._queue.put_nowait(u)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "LedTicker/0.1"}) as client:
            self._client = client
            sem = asyncio.Semaphore(4)

            async def one(u):
                async with sem:
                    await self._download(u)

            while True:
                url = await self._queue.get()
                asyncio.create_task(one(url))

    async def prefetch(self, urls: list[str]) -> None:
        """Synchronous-ish warmup for snapshots/tests: download now and wait."""
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "LedTicker/0.1"}) as client:
            self._client = client
            todo = [u for u in dict.fromkeys(urls) if u and _host_ok(u) and not self._path(u).exists()]
            await asyncio.gather(*(self._download(u) for u in todo))
