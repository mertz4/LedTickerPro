"""Uploaded pictures: normalizing (EXIF rotation, GIF frames), fit modes at every section shape, API + permissions."""
from __future__ import annotations

import io
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ledticker.config import Context
from ledticker.render import photos
from ledticker.render.photos import PhotoError, PhotoLibrary
from ledticker.web.app import create_app

TZ = ZoneInfo("America/Chicago")
ME, RM = "me@gmail.com", "house@gmail.com"
SHAPES = [(256, 117), (128, 58), (128, 117), (256, 58), (256, 128), (128, 64)]


def png(w=400, h=300, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def rotated_jpeg() -> bytes:
    """A phone-style photo: stored landscape (red top half, blue bottom) with an EXIF 'rotate 90' tag."""
    im = Image.new("RGB", (300, 200), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 300, 100))
    exif = Image.Exif()
    exif[0x0112] = 6                              # orientation: rotate 90 CW to view
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def gif() -> bytes:
    frames = [Image.new("RGB", (64, 32), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


def test_add_normalizes_and_persists(tmp_path):
    lib = PhotoLibrary(tmp_path)
    p = lib.add(rotated_jpeg(), "phone pic", "fit", "me")
    with Image.open(tmp_path / p.file) as stored:
        assert stored.size == (200, 300)          # turned upright
    a = lib.add(gif(), "dance")
    assert a.animated and a.frames == 3
    frames = [lib.picture(a.id, (64, 32), t).getpixel((32, 16)) for t in (0.05, 0.15, 0.25)]
    assert frames[0][0] > 200 and frames[1][1] > 200 and frames[2][2] > 200   # it plays
    again = PhotoLibrary(tmp_path)                # index survives a restart
    assert [x.name for x in again.list()] == ["phone pic", "dance"]
    with pytest.raises(PhotoError):
        lib.add(b"not an image")
    lib.remove(p.id)
    assert not (tmp_path / p.file).exists() and p.id not in PhotoLibrary(tmp_path).photos


@pytest.mark.parametrize("size", SHAPES)
def test_fill_and_fit_at_every_shape(tmp_path, size):
    lib = PhotoLibrary(tmp_path)
    p = lib.add(png(400, 400, (0, 200, 0)))       # square picture
    fill = lib.picture(p.id, size, 0)
    assert fill.size == size and fill.getpixel((0, 0))[1] > 150 and fill.getpixel((size[0] - 1, size[1] - 1))[1] > 150
    lib.update(p.id, fit="fit")
    fit = lib.picture(p.id, size, 0)
    assert fit.size == size
    W, H = size
    if W > H:                                     # a square in a wide section: black bars left and right
        assert fit.getpixel((0, H // 2)) == (0, 0, 0) and fit.getpixel((W // 2, H // 2))[1] > 150


def test_widget_slideshow_and_focus(tmp_path):
    lib = photos.LIBRARY = PhotoLibrary(tmp_path)
    w = photos.PhotosWidget()
    a = lib.add(png(color=(255, 0, 0)))
    b = lib.add(png(color=(0, 0, 255)))
    assert w._current(0) == a.id and w._current(w.per_photo + 1) == b.id
    lib.focus = b.id
    assert w._current(0) == b.id


def test_api_upload_show_and_permissions(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    c = TestClient(create_app(ctx, auth="tailscale"))
    me, rm = {"Tailscale-User-Login": ME}, {"Tailscale-User-Login": RM}
    c.put(f"/api/access/{RM}", json={"role": "roommate"}, headers=me).raise_for_status()

    assert c.post("/api/photos?name=x", content=png(), headers=rm).status_code == 403
    r = c.post("/api/photos?name=Tailgate&fit=fit", content=png(), headers=me)
    assert r.status_code == 200 and r.json()["photo"]["name"] == "Tailgate"
    pid = r.json()["photo"]["id"]
    assert c.post("/api/photos?name=bad", content=b"nope", headers=me).status_code == 400
    assert c.get("/api/photos", headers=rm).json()["photos"][0]["id"] == pid          # everyone can look
    assert c.get(f"/api/photos/{pid}/preview.png", headers=rm).headers["content-type"] == "image/png"

    c.post(f"/api/photos/show?pid={pid}", headers=me).raise_for_status()
    st = c.get("/api/state", headers=me).json()
    assert st["pinned"] == "photos"
    c.patch(f"/api/photos/{pid}", json={"fit": "fill", "name": "Tailgate 2"}, headers=me).raise_for_status()
    assert c.patch(f"/api/photos/{pid}", json={"fit": "stretch"}, headers=me).status_code == 400

    c.put(f"/api/access/{RM}", json={"role": "roommate", "extra": ["media"]}, headers=me).raise_for_status()
    assert c.post("/api/photos/stop", headers=rm).status_code == 200                  # granted: now allowed
    c.delete(f"/api/photos/{pid}", headers=rm).raise_for_status()
    assert c.get("/api/photos", headers=me).json()["photos"] == []
