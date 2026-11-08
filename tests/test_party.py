"""Pregame light show: beat clock / tap tempo, scenes, immersive pinning, settings, API permissions."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ledticker.config import Context
from ledticker.display import geometry
from ledticker.render import party
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.render.modes import DEFAULT_MODES, ModeError, validate
from ledticker.store import Store
from ledticker.web.app import create_app

TZ = ZoneInfo("America/Chicago")


def test_tap_tempo_locks_bpm_and_phase():
    c = party.BeatClock()
    for i in range(5):
        c.tap(100.0 + i * 0.5)                 # 120 BPM
    assert c.bpm == pytest.approx(120) and c.tapped
    assert c.beats(102.0) == pytest.approx(0)  # the last tap is a downbeat
    assert c.beats(103.0) == pytest.approx(2)
    c.configure({"bpm": 90})                   # a live tap wins over the saved tempo...
    assert c.bpm == pytest.approx(120)
    c.reset()
    c.configure({"bpm": 90})                   # ...until reset
    assert c.bpm == 90
    c.tap(200.0); c.tap(200.1)                 # absurd taps are clamped
    assert c.bpm == 200
    c.tap(205.0)                               # a long pause starts a fresh count
    assert c._taps == [205.0]


@pytest.mark.parametrize("scene", ["party", *party.SCENES])
@pytest.mark.parametrize("size", [(256, 128), (128, 64), (256, 117)])
def test_scenes_draw(scene, size):
    for b in (0.0, 0.5, 3.9, 13.7, 33.0, 64.2):
        img = party.frame(scene, b, size, "PREGAME")
        assert img.size == size
        if b:                                  # (an auto-show scene fades in from its first beat)
            assert img.getbbox() is not None


def test_strobe_only_when_enabled():
    b = 28.0                                   # last bar of an 8-bar phrase, on the beat
    assert party.frame("lasers", b, strobe=False).getpixel((128, 10)) != (255, 255, 255)
    assert party.frame("lasers", b, strobe=True).getpixel((128, 10)) == (255, 255, 255)


def test_message_drops_every_four_bars():
    with_msg = party.frame("party", 16.2, message="GIG EM")
    without = party.frame("party", 16.2, message="")
    assert with_msg.tobytes() != without.tobytes()
    assert party.frame("party", 8.2, message="GIG EM").tobytes() == party.frame("party", 8.2, message="").tobytes()


def test_pinned_show_hides_ticker(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, Store(None), ImageCache(tmp_path), geometry(ctx), modes_path=None)
    assert {"party", "party_lasers", "party_plasma"} <= set(eng.widgets)
    eng.pin("party")
    assert eng._ticker_on() is False
    eng.render()                               # draws the full display without the strip
    assert eng.rc(eng._ticker_on()).main_h == eng.geom.height
    eng.pin("clock")                           # an ordinary widget keeps the ticker
    assert eng._ticker_on() is True and eng.rc(True).main_h < eng.geom.height


def test_party_settings_validation():
    doc = {**DEFAULT_MODES, "modes": {"m": {"slides": [{"widget": "clock"}]}}, "default": "m", "layouts": {}, "schedule": []}
    validate({**doc, "party": {"bpm": 128, "message": "LETS GO", "strobe": True}}, {"clock"})
    for bad in ({"bpm": 20}, {"message": "x" * 41}, {"strobe": "yes"}):
        with pytest.raises(ModeError):
            validate({**doc, "party": bad}, {"clock"})


def test_api_tap_needs_control(tmp_path):
    ME, RM = "me@gmail.com", "house@gmail.com"
    ctx = Context(root=tmp_path, config={}, secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    c = TestClient(create_app(ctx, auth="tailscale"))
    c.put(f"/api/access/{RM}", json={"role": "roommate"}, headers={"Tailscale-User-Login": ME}).raise_for_status()
    assert c.post("/api/party/tap", headers={"Tailscale-User-Login": RM}).status_code == 403
    assert c.post("/api/pin", json={"screen": "party"}, headers={"Tailscale-User-Login": RM}).status_code == 403
    r = c.post("/api/party/tap", headers={"Tailscale-User-Login": ME})
    assert r.status_code == 200 and "bpm" in r.json()
    c.post("/api/pin", json={"screen": "party"}, headers={"Tailscale-User-Login": ME}).raise_for_status()
    st = c.get("/api/state", headers={"Tailscale-User-Login": ME}).json()
    assert st["party"]["on"] is True and "lasers" in st["party"]["scenes"]
    c.post("/api/party/reset", headers={"Tailscale-User-Login": ME}).raise_for_status()
