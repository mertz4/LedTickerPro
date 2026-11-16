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
