"""Pictures uploaded from the dashboard, shown on the display (stills and animated GIFs), in any section shape.

Uploads are normalized once: turned upright (phone photos carry a rotation tag), converted to RGB and downsized
(nothing on a 256x128 display needs more), then stored in data/photos/ with an index. The widget fits each picture
to its section: "fill" crops to cover it, "fit" shows the whole picture with black bars.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps, ImageSequence

from .screens import RC, Screen

MAX_BYTES = 25 * 1024 * 1024
MAX_SIDE = 768                 # stored size: plenty for 256x128 at any crop
MAX_FRAMES = 150
FITS = ("fill", "fit")


class PhotoError(ValueError):
    pass


@dataclass
class Photo:
    id: str
    name: str
    fit: str
    animated: bool
    frames: int
    added_by: str
    added_at: float

    @property
    def file(self) -> str:
        return f"{self.id}.gif" if self.animated else f"{self.id}.png"


class PhotoLibrary:
    def __init__(self, folder: Path | None):
        self.folder = folder
        self.photos: dict[str, Photo] = {}
        self.focus: str | None = None          # a picture pinned with "Show now"; None = slideshow of all
        self.version = 0                       # bumps on any change (render caches key on it)
        if folder and (folder / "index.json").exists():
            try:
                for p in json.loads((folder / "index.json").read_text(encoding="utf-8")).get("photos") or []:
                    self.photos[p["id"]] = Photo(**p)
            except (OSError, ValueError, TypeError, KeyError):
                self.photos = {}

    def list(self) -> list[Photo]:
        return sorted(self.photos.values(), key=lambda p: p.added_at)

    def _save(self) -> None:
        self.version += 1
        if not self.folder:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "index.tmp"
        tmp.write_text(json.dumps({"photos": [vars(p) for p in self.list()]}, indent=2), encoding="utf-8")
        os.replace(tmp, self.folder / "index.json")

    # ---- add / change / remove
    def add(self, data: bytes, name: str = "", fit: str = "fill", added_by: str = "") -> Photo:
        if not data:
            raise PhotoError("that file is empty")
        if len(data) > MAX_BYTES:
            raise PhotoError("that file is too big (25 MB max)")
        try:
            src = Image.open(io.BytesIO(data))
            src.load()
        except Exception:
            raise PhotoError("couldn't read that picture. Use a JPEG, PNG, GIF or WebP "
                             "(on iPhone, share it as JPEG / 'Most Compatible' if it's HEIC)")
        pid = secrets.token_hex(6)
        animated = getattr(src, "is_animated", False) and getattr(src, "n_frames", 1) > 1
        if self.folder:
            self.folder.mkdir(parents=True, exist_ok=True)
        if animated:
            frames, durations = [], []
            for i, fr in enumerate(ImageSequence.Iterator(src)):
                if i >= MAX_FRAMES:
                    break
                im = fr.convert("RGB")
                im.thumbnail((MAX_SIDE // 2, MAX_SIDE // 2))
                frames.append(im)
                durations.append(max(20, int(fr.info.get("duration", src.info.get("duration", 100)) or 100)))
            if self.folder:
                frames[0].save(self.folder / f"{pid}.gif", save_all=True, append_images=frames[1:],
                               duration=durations, loop=0, disposal=2)
            n = len(frames)
        else:
            im = ImageOps.exif_transpose(src).convert("RGB")      # iPhone photos are stored sideways + a tag
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            if self.folder:
                im.save(self.folder / f"{pid}.png", optimize=True)
            n = 1
        if fit not in FITS:
            fit = "fill"
        photo = Photo(id=pid, name=(name or "Picture").strip()[:60], fit=fit, animated=bool(animated), frames=n,
                      added_by=added_by, added_at=time.time())
        self.photos[pid] = photo
        self._save()
        return photo

    def update(self, pid: str, fit: str | None = None, name: str | None = None) -> Photo:
        p = self.photos[pid]
        if fit is not None:
            if fit not in FITS:
                raise PhotoError("fit must be fill or fit")
            p.fit = fit
        if name is not None:
            p.name = name.strip()[:60] or p.name
        self._save()
        return p

    def remove(self, pid: str) -> None:
        p = self.photos.pop(pid)
        if self.folder:
            try:
                (self.folder / p.file).unlink()
            except OSError:
                pass
        if self.focus == pid:
            self.focus = None
        self._save()

    # ---- drawing
    def frames(self, pid: str) -> tuple[list[Image.Image], list[int]]:
        return _load_frames(str(self.folder / self.photos[pid].file), self.version)

    def picture(self, pid: str, size: tuple[int, int], t: float) -> Image.Image:
        """The picture fitted to `size` at time t (animated GIFs play on their own frame timing)."""
        p = self.photos[pid]
        frames, durations = self.frames(pid)
        i = 0
        if p.animated and len(frames) > 1:
            total = sum(durations) / 1000
            pos = (t % total) * 1000
            for i, dur in enumerate(durations):
                if pos < dur:
                    break
                pos -= dur
        return _fitted(str(self.folder / p.file), self.version, i, size, p.fit)


@lru_cache(maxsize=16)
def _load_frames(path: str, version: int) -> tuple[list[Image.Image], list[int]]:
    frames, durations = [], []
    with Image.open(path) as im:                       # closed after reading, so the file can be deleted later
        for fr in ImageSequence.Iterator(im):
            frames.append(fr.convert("RGB"))
            durations.append(max(20, int(fr.info.get("duration", 100) or 100)))
    return frames, durations


@lru_cache(maxsize=256)
def _fitted(path: str, version: int, frame: int, size: tuple[int, int], fit: str) -> Image.Image:
    im = _load_frames(path, version)[0][frame]
    W, H = size
    if fit == "fit":
        pic = ImageOps.contain(im, (W, H), Image.LANCZOS)
        out = Image.new("RGB", (W, H))
        out.paste(pic, ((W - pic.width) // 2, (H - pic.height) // 2))
    else:
        out = ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(0.5, 0.45))
    return ImageEnhance.Color(out).enhance(1.15)          # LEDs wash colors out a little; give them a push


LIBRARY = PhotoLibrary(None)                               # replaced by the engine with the real folder


class PhotosWidget(Screen):
    """Uploaded pictures: a slideshow of all of them, or the one picked with "Show now". Any section shape."""
    name, label = "photos", "Pictures"
    adaptive = True
    per_photo = 8.0

    def available(self, rc: RC) -> bool:
        return bool(LIBRARY.photos)

    def seconds(self, rc: RC) -> float:
        return min(60.0, max(self.per_photo, self.per_photo * len(LIBRARY.photos)))

    def animating(self, rc: RC, t: float) -> bool:
        pid = self._current(t)
        return bool(pid and LIBRARY.photos[pid].animated)

    def _current(self, t: float) -> str | None:
        if LIBRARY.focus in LIBRARY.photos:
            return LIBRARY.focus
        ids = [p.id for p in LIBRARY.list()]
        return ids[int(t // self.per_photo) % len(ids)] if ids else None

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        pid = self._current(t)
        if pid:
            img.paste(LIBRARY.picture(pid, (rc.W, rc.main_h), t), (0, 0))
