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


def test_ticker_moves_the_same_distance_every_frame_despite_jitter():
    from ledticker.render.engine import Ticker

    tk = Ticker(speed=60, fps=30)
    tk.advance(0.0)
    steps, t, last = [], 0.0, 0.0
    for late in (0.0, 0.006, -0.005, 0.009, -0.008, 0.004):      # frames a few ms early or late, as on the Pi
        t += 1 / 30 + late
        now = tk.advance(t)
        steps.append(round(now - last, 6))
        last = now
    assert steps == [2.0] * 6                                      # 2 pixels each time, never 1 then 3
    before = tk.offset
    tk.advance(t)                                                  # drawn twice in one frame: no extra movement
    assert tk.offset == before
    tk.advance(t + 0.2)                                            # a real stall still catches up by the clock
    assert abs(tk.offset - before - 12.0) < 1e-6


def test_airplay_counts_only_the_phones_connections():
    from ledticker import airplay

    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    tcp = head + (
        "   0: 00000000:1B58 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 5001 1 x 100 0 0 10 0\n"
        "   1: 0A00000C:1B58 0A000022:D431 01 00000000:00000000 02:000AB1C2 00000000  1000        0 5002 2 x 20 4 30 10 -1\n"
        "   2: 0A00000C:0016 0A000063:C001 01 00000000:00000000 02:00000000 00000000     0        0 7777 2 x 20 4 30 10 -1\n")
    tcp6 = head + (
        "   0: 00000000000000000000000000000000:1BBC 00000000000000000000000000000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000  1000        0 5003 1 x 100 0 0 10 0\n"
        "   1: 0000000000000000FFFF00000A00000C:1BBC 0000000000000000FFFF00000A000022:D440 01 00000000:00000000 "
        "02:00000000 00000000  1000        0 5004 2 x 20 4 30 10 -1\n")
    mine = {"5001", "5002", "5003", "5004"}          # the receiver's sockets; 7777 is someone else's (sshd)
    assert airplay.established([tcp, tcp6], mine) == 2            # listening sockets and other programs don't count
    assert airplay.established([tcp, tcp6], {"5001", "5003"}) == 0


def test_display_returns_as_soon_as_the_phone_disconnects(tmp_path):
    from ledticker import airplay

    eng = make_engine(tmp_path)
    eng.transition = "none"
    rx = airplay.Receiver({}, eng.mirror, tmp_path)
    rx.note_phone(0)                                               # nobody yet
    rx.note_phone(2)                                               # the phone connects...
    eng.mirror.feed(bytes([0, 200, 0, 0]) * (256 * 128))          # ...and sends a frame
    rx.note_phone(2)
    eng.render()
    assert eng._shown_key == "__airplay__"
    rx.note_phone(2)                                               # a still screen: connected, no new frames
    eng.render()
    assert eng._shown_key == "__airplay__"                         # stays up
    rx.note_phone(0)                                               # the phone stops mirroring
    eng.render()
    assert eng._shown_key != "__airplay__"                         # back to the rotation at once, not 30 s later
    rx.note_phone(None)                                            # where connections can't be counted: no effect
    assert eng.mirror.live is False
    rx.note_phone(1)
    eng.mirror.feed(bytes([0, 0, 200, 0]) * (256 * 128))          # the next session shows again
    eng.render()
    assert eng._shown_key == "__airplay__"


async def test_cache_is_written_off_the_main_thread_and_batched(tmp_path, monkeypatch):
    import json
    import threading

    from ledticker import store as store_mod

    monkeypatch.setattr(store_mod, "SAVE_DELAY", 0.05)
    writers = []
    st = Store(tmp_path / "cache.json")
    real = st._write
    st._write = lambda payload: (writers.append(threading.current_thread()), real(payload))[1]
    st.register("a", "A", True, "", 60)
    for i in range(5):
        st.ok("a", {"n": i})                                       # five updates in a row...
    assert not (tmp_path / "cache.json").exists()                  # ...nothing written on the spot
    for _ in range(100):
        if (tmp_path / "cache.json").exists():
            break
        await asyncio.sleep(0.02)
    assert len(writers) == 1 and writers[0] is not threading.main_thread()   # one write, on another thread
    assert json.loads((tmp_path / "cache.json").read_text())["a"]["data"] == {"n": 4}
    fresh = Store(tmp_path / "cache.json")
    fresh.load()
    assert fresh.get("a") == {"n": 4}
