"""Motion and live input: slide transitions, the change glint, the touchpad layer, pong, and the two-way socket."""
from __future__ import annotations

import time
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from ledticker.config import Context
from ledticker.render import pong, transitions
from ledticker.render.touchpad import TouchLayer
from ledticker.web.app import create_app

TZ = ZoneInfo("America/Chicago")
ME, GF = "me@x.com", "her@x.com"
SIZE = (256, 128)


def solid(color) -> Image.Image:
    return Image.new("RGB", SIZE, color)


# ---------------------------------------------------------------- transitions

@pytest.mark.parametrize("style", [*transitions.STYLES, "unknown"])
def test_transition_runs_from_old_to_new(style):
    old, new = solid((200, 0, 0)), solid((0, 0, 200))
    assert transitions.blend(style, old, new, 0.0).tobytes() == old.tobytes()
    assert transitions.blend(style, old, new, 1.0).tobytes() == new.tobytes()
    for p in (0.05, 0.3, 0.5, 0.8, 0.97):
        mid = transitions.blend(style, old, new, p, seed=3)
        assert mid.size == SIZE
    assert old.getpixel((5, 5)) == (200, 0, 0) and new.getpixel((5, 5)) == (0, 0, 200)   # inputs untouched
    half = transitions.blend(style, old, new, 0.5)
    assert half.tobytes() not in (old.tobytes(), new.tobytes())


def test_change_mask_finds_the_updated_number_and_ignores_motion():
    old, new = solid((0, 0, 0)), solid((0, 0, 0))
    ImageDraw.Draw(old).rectangle([10, 10, 20, 20], fill=(255, 255, 255))     # a "digit" that changes shape
    ImageDraw.Draw(new).rectangle([10, 10, 20, 26], fill=(255, 255, 255))
    ImageDraw.Draw(new).rectangle([200, 100, 210, 104], fill=(0, 255, 0))     # scrolling text elsewhere
    assert transitions.change_mask(old, old) is None
    mask = transitions.change_mask(old, new)
    assert mask.getpixel((15, 24)) == 255 and mask.getpixel((205, 102)) == 255
    assert mask.getpixel((100, 60)) == 0                                       # unlit pixels never flash
    moving = transitions.changed(solid((0, 0, 0)), new).crop((180, 0, 256, 128))
    ignore = Image.new("L", SIZE)
    ignore.paste(moving, (180, 0))
    mask = transitions.change_mask(old, new, ignore)
    assert mask.getpixel((15, 24)) == 255 and mask.getpixel((205, 102)) == 0
    assert transitions.change_mask(old, solid((90, 90, 90))) is None           # a whole new picture isn't a glint

    img = new.copy()
    transitions.glint(img, transitions.change_mask(old, new), 0.0)
    assert img.getpixel((205, 102))[0] > 200 and img.getpixel((100, 60)) == (0, 0, 0)   # green flashes to white
    late = new.copy()
    transitions.glint(late, mask, transitions.GLINT_SECONDS + 1)
    assert late.tobytes() == new.tobytes()


# ---------------------------------------------------------------- touchpad

def test_touch_trails_draw_then_fade_away():
    layer = TouchLayer(SIZE)
    base = solid((0, 0, 0))
    assert not layer.active
    layer.touch(1, 0, 0.2, 0.5)
    layer.touch(1, 0, 0.6, 0.5)
    assert layer.active
    lit = layer.draw(base, 10.0)
    assert lit.getbbox() is not None and base.getbbox() is None     # the frame passed in is left alone
    assert sum(lit.getpixel((100, 64))) > 100                       # on the stroke
    layer.lift(1, 0)
    t = 10.0
    while t < 16.0:
        t += 1 / 30
        out = layer.draw(base, t)
    assert not layer.active and out.getbbox() is None


def test_touch_forgets_a_disconnected_phone():
    layer = TouchLayer(SIZE)
    layer.touch(7, 0, 0.5, 0.5)
    layer.touch(8, 0, 0.1, 0.1)
    layer.drop(7)
    assert list(layer.fingers) == [(8, 0)]


# ---------------------------------------------------------------- pong

def run(game: pong.Pong, seconds: float, t0: float = 0.0) -> float:
    t = t0
    game.frame(t)
    for _ in range(int(seconds * 30)):
        t += 1 / 30
        assert game.frame(t).size == (game.W, game.H)
    return t


def test_pong_lobby():
    g = pong.Pong(SIZE, seed=1)
    assert not g.active
    assert g.join("a", "Kade Someone") == 0
    assert g.active and g.info()["names"] == ["KADE SOMEO", "CPU"] and g.info()["cpu"] == [False, True]
    g.score = [3, 2]
    assert g.join("b", "Riley") == 1            # takes the computer's seat; the match restarts
    assert g.score == [0, 0] and g.state == "countdown"
    with pytest.raises(ValueError):
        g.join("c", "Late")
    g.leave("a")
    assert g.active and g.info()["cpu"] == [True, False]
    g.leave("b")
    assert not g.active


def test_pong_plays_to_a_winner_and_ends():
    g = pong.Pong(SIZE, seed=2)
    g.join("a", "Kade")
    v0 = g.version
    t = run(g, pong.COUNTDOWN + 0.2)
    assert g.state == "play"
    g.move("a", 0.0)                            # park the paddle in the corner: the computer wins every rally
    for _ in range(60 * 30 * 4):
        t += 1 / 30
        g.move("a", 0.0)
        g.frame(t)
        if g.state == "over":
            break
    assert g.state == "over" and g.score[1] == pong.WIN_SCORE and g.info()["winner"] == 1
    assert g.version > v0
    assert g.join("a", "Kade") == 0 and g.state == "countdown" and g.score == [0, 0]     # rematch
    g.score = [pong.WIN_SCORE, 0]
    g.state, g.timer = "over", 0.1
    run(g, 0.5, t)
    assert not g.active


def test_pong_paddle_returns_the_ball_faster():
    g = pong.Pong(SIZE, seed=3)
    g.join("a", "Kade")
    g.state = "play"
    g.bx, g.by, g.vx, g.vy = 30.0, 64.0, -pong.SPEED0, 0.0
    g.pad[0] = 60.0
    g.step(0.5)
    assert g.vx > 0 and g.speed > pong.SPEED0 and g.score == [0, 0]


def test_pong_stops_when_nobody_is_playing():
    g = pong.Pong(SIZE, seed=4)
    g.join("a", "Kade")
    g.state = "play"
    g.idle = pong.IDLE_SECONDS
    g.step(0.1)
    assert not g.active


# ---------------------------------------------------------------- engine + socket

@pytest.fixture
def client(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    c = TestClient(create_app(ctx, auth="tailscale"))
    c.put(f"/api/access/{GF}", json={"role": "partner"}, headers={"Tailscale-User-Login": ME}).raise_for_status()
    return c


def next_json(ws, kind: str) -> dict:
    for _ in range(400):
        m = ws.receive()
        if m.get("text"):
            import json
            msg = json.loads(m["text"])
            if msg["type"] == kind:
                return msg
    raise AssertionError(f"no {kind} message")


def test_socket_takes_touch_and_pong_input(client):
    eng = client.app.state.engine
    with client:
        with client.websocket_connect("/ws", headers={"Tailscale-User-Login": GF, "Tailscale-User-Name": "Bab"}) as ws:
            hello = next_json(ws, "hello")
            assert hello["play"] is True
            assert next_json(ws, "pong")["state"] == "off"
            ws.send_text("not json")                        # junk is ignored
            ws.send_json({"t": "touch", "id": 0, "x": "nope", "y": 0.5})
            ws.send_json({"t": "touch", "id": 0, "x": 0.5, "y": 0.5})
            ws.send_json({"t": "join"})
            msg = next_json(ws, "pong")
            assert msg["state"] == "countdown" and msg["you"] == 0 and msg["names"] == ["BAB", "CPU"]
            assert eng.touch.fingers and eng.pong.active
            ws.send_json({"t": "paddle", "y": 1.0})
            ws.send_json({"t": "leave"})
            assert next_json(ws, "pong")["state"] == "off"
            ws.send_json({"t": "join"})
            assert next_json(ws, "pong")["you"] == 0
        for _ in range(100):                                # hanging up leaves the game and lifts the fingers
            if not eng.pong.active:
                break
            time.sleep(0.02)
        assert not eng.pong.active and not eng.touch.fingers


def test_slides_transition_and_pong_takes_the_display(tmp_path):
    from ledticker.display import Geometry
    from ledticker.render.engine import Engine
    from ledticker.render.images import ImageCache
    from ledticker.store import Store

    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, Store(None), ImageCache(tmp_path), Geometry())
    a, b = solid((200, 0, 0)), solid((0, 0, 200))
    assert eng._transit("a", a, 0.0) is a                  # nothing before it: no transition
    eng._transit("b", b, 0.3)                              # the change starts it...
    mid = eng._transit("b", b, 0.6)                        # ...and half-way through, it's a mix of the two
    assert mid is not b and mid.tobytes() not in (a.tobytes(), b.tobytes())
    assert eng._transit("b", b, 0.4 + eng.transition_seconds) is b
    eng._transit("big_play", a, 5.0)
    assert eng._trans[1] == "flash"                        # takeovers arrive with a flash

    eng.transition = "none"
    assert eng._transit("c", b, 9.0) is b

    eng.pong.join("x", "Kade")
    frame = eng.render()
    assert eng._shown_key == "__pong__" and frame.size == SIZE
    eng.pong.stop()
    eng.render()
    assert eng._shown_key != "__pong__"


# ---------------------------------------------------------------- AirPlay mirroring

def test_mirror_keeps_the_newest_frame_and_times_out():
    import io

    from ledticker import airplay

    m = airplay.Mirror((4, 2), idle_seconds=30)
    assert not m.active() and m.image() is None
    red, blue = bytes([255, 0, 0, 0]) * 8, bytes([0, 0, 255, 0]) * 8
    assert m.pump(io.BytesIO(red + blue + red[:5])) == 2        # the cut-off third frame is dropped
    assert not m.active()                                      # ...and the stream closing ends the session
    m.feed(red, now=100.0)
    m.feed(b"short", now=100.0)                                # wrong size: ignored
    assert m.active(now=101.0) and m.image().getpixel((3, 1)) == (255, 0, 0)
    m.feed(blue, now=102.0)
    assert m.image().getpixel((0, 0)) == (0, 0, 255)
    assert not m.active(now=140.0)                             # nothing new for idle_seconds
    m.feed(red, now=150.0)
    m.dismiss()                                                # Next on the dashboard
    m.feed(blue, now=151.0)
    assert not m.active(now=151.5)
    m.feed(red, now=200.0)                                     # a later session shows again
    assert m.active(now=200.5)


def test_airplay_command_line(tmp_path):
    from ledticker import airplay

    pipe = tmp_path / "airplay.pipe"
    cmd = airplay.command({}, SIZE, pipe)
    assert cmd[:4] == ["uxplay", "-n", "LED Ticker", "-nh"] and cmd[-2] == "-vs"
    assert "-as" in cmd and cmd[cmd.index("-as") + 1] == "0"   # no audio
    vs = cmd[-1]
    assert vs.startswith("aspectratiocrop aspect-ratio=256/128 ! videoscale")
    assert "format=RGBx,width=256,height=128" in vs and "leaky=downstream max-size-buffers=2" in vs
    assert vs.endswith(f"filesink location={pipe} buffer-mode=unbuffered")
    cmd = airplay.command({"fit": "fit", "rotate": "left", "decoder": "software", "name": "Wall",
                           "extra_args": ["-pin"]}, SIZE, pipe)
    assert cmd[2] == "Wall" and "-avdec" in cmd and "-pin" in cmd
    assert cmd[cmd.index("-r") + 1] == "L" and cmd[-1].startswith("videoscale add-borders=true")


def test_mirroring_takes_the_display_until_dismissed(tmp_path):
    from ledticker.display import Geometry
    from ledticker.render.engine import Engine
    from ledticker.render.images import ImageCache
    from ledticker.store import Store

    eng = Engine(Context(root=tmp_path, config={}, secrets={}, tz=TZ), Store(None), ImageCache(tmp_path), Geometry())
    eng.transition = "none"
    eng.render()
    eng.mirror.feed(bytes([0, 200, 0, 0]) * (256 * 128))
    assert eng.render().getpixel((128, 64)) == (0, 200, 0) and eng._shown_key == "__airplay__"
    eng.skip()
    eng.render()
    assert eng._shown_key != "__airplay__"


def test_airplay_is_off_by_default(client):
    with client:
        st = client.get("/api/state", headers={"Tailscale-User-Login": ME}).json()["airplay"]
    assert st["enabled"] is False and st["active"] is False
