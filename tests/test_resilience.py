"""The frame loop survives a bad frame, and a background task that does crash stops the app (so it restarts)."""
from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import pytest

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render import engine as engine_mod
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.store import Store
from ledticker.web.app import supervise

TZ = ZoneInfo("America/Chicago")


def make_engine(tmp_path) -> Engine:
    eng = Engine(Context(root=tmp_path, config={}, secrets={}, tz=TZ), Store(None), ImageCache(tmp_path), Geometry())
    eng.fps = 200
    return eng


async def test_frame_loop_skips_a_bad_frame(tmp_path):
    eng = make_engine(tmp_path)
    real, calls, shown = eng.render, [], []

    def render():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real()

    eng.render = render
    eng.subscribe(shown.append)
    loop = asyncio.create_task(eng.run())
    for _ in range(400):
        if len(shown) >= 3:
            break
        await asyncio.sleep(0.01)
    assert not loop.done()                     # the failed frame didn't end the loop...
    assert len(shown) >= 3 and len(calls) > len(shown)   # ...and frames kept coming after it
    loop.cancel()
    await asyncio.gather(loop, return_exceptions=True)


async def test_frame_loop_gives_up_when_every_frame_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "RENDER_GIVE_UP", 0.05)
    eng = make_engine(tmp_path)
    shown = []

    def render():
        raise RuntimeError("always")

    eng.render = render
    eng.subscribe(shown.append)
    with pytest.raises(RuntimeError, match="always"):
        await asyncio.wait_for(eng.run(), 5)
    assert shown == []


async def test_supervise_only_reacts_to_a_crash():
    fatal = []

    async def crash():
        raise RuntimeError("dead")

    async def finish():
        return None

    async def forever():
        await asyncio.sleep(3600)

    tasks = [asyncio.create_task(c(), name=n) for n, c in (("crash", crash), ("finish", finish), ("forever", forever))]
    for t in tasks:
        supervise(t, lambda t=t: fatal.append(t.get_name()))
    await asyncio.sleep(0.01)
    tasks[2].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)                     # let the done callbacks run
    assert fatal == ["crash"]


def test_airplay_logs_when_frames_start_and_stop(tmp_path, caplog):
    from ledticker import airplay

    mirror = airplay.Mirror((4, 2))
    rx = airplay.Receiver({}, mirror, tmp_path)
    with caplog.at_level("WARNING", logger="ledticker.airplay"):
        rx.note_flow()                                   # nothing yet: nothing logged
        mirror.feed(bytes(mirror.frame_bytes))
        rx.note_flow()                                   # arriving
        mirror.feed(bytes(mirror.frame_bytes))
        rx.note_flow()                                   # still arriving: no new line
        rx.note_flow()                                   # stopped
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2 and "arriving (1 so far)" in lines[0] and "stopped (2 so far)" in lines[1]


def test_frame_delta_rebuilds_the_frame_exactly():
    import os

    from ledticker.web.app import frame_delta

    old, new = os.urandom(256 * 128 * 3), os.urandom(256 * 128 * 3)
    diff = frame_delta(new, old)
    assert len(diff) == len(new)
    assert bytes(a ^ b for a, b in zip(old, diff)) == new       # what the dashboard does with it
    assert frame_delta(new, new) == bytes(len(new))             # no change: all zeros
    assert frame_delta(new, bytes(len(new))) == new             # first frame, from black: the frame itself
