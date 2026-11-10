"""LED panel output (ledticker/matrix.py) against a fake rgbmatrix module, so it runs anywhere."""
import os
import sys
import threading
import time
import types
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from ledticker import matrix as M
from ledticker.config import Context
from ledticker.display import Geometry

GEOM = Geometry()


class _NullStr:
    """Like the fork's Cython string properties: reading one before it's set would crash, so the getter raises."""
    def __set_name__(self, owner, name):
        self.name = "_" + name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        if not hasattr(obj, self.name):
            raise AssertionError(f"read {self.name[1:]} before it was set (would segfault in Cython)")
        return getattr(obj, self.name)

    def __set__(self, obj, value):
        setattr(obj, self.name, value)


class FakeOptions:
    """Class-level options, like the fork's RGBMatrixOptions (a subset), and nothing else."""
    rows = cols = chain_length = parallel = spwm_row_address_type = spwm_scan_rows = None
    gpio_slowdown = pwm_bits = brightness = limit_refresh_rate_hz = show_refresh_rate = drop_privileges = None
    hardware_mapping = led_rgb_sequence = None
    panel_type = _NullStr()
    pixel_mapper_config = _NullStr()


class FakeCanvas:
    def __init__(self, log):
        self.log = log

    def SetImage(self, img, x=0, y=0):
        self.log.append((img.size, img.getpixel((0, 0))))


class FakeMatrix:
    def __init__(self, options=None):
        self.options = options
        self.width = options.cols * options.chain_length
        self.height = options.rows * options.parallel
        self.shown, self.swaps, self.cleared = [], 0, False
        self.slow = 0.0

    def CreateFrameCanvas(self):
        return FakeCanvas(self.shown)

    def SwapOnVSync(self, canvas):
        time.sleep(self.slow)
        self.swaps += 1
        return canvas

    def Clear(self):
        self.cleared = True


def ctx_with(matrix_cfg):
    return Context(root=".", config={"matrix": matrix_cfg}, secrets={}, tz=ZoneInfo("America/Chicago"))


@pytest.fixture
def fake_lib(monkeypatch):
    mod = types.ModuleType("rgbmatrix")
    mod.RGBMatrix, mod.RGBMatrixOptions = FakeMatrix, FakeOptions
    monkeypatch.setitem(sys.modules, "rgbmatrix", mod)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)   # pretend root
    return mod


def test_defaults_match_the_2x2_fm6373_build():
    o = M.build_options(FakeOptions(), {}, GEOM)
    assert (o.rows, o.cols, o.chain_length, o.parallel) == (64, 128, 2, 2)
    assert o.panel_type == "fm6373" and o.spwm_row_address_type == 0
    assert o.hardware_mapping == "regular" and o.pixel_mapper_config == ""
    assert o.drop_privileges is False          # we drop to the app's own user, not the library's "daemon"


def test_config_overrides_extra_and_unknown_keys(caplog):
    o = M.build_options(FakeOptions(), {"enabled": True, "gpio_slowdown": 3, "env": {"X": 1},
                                        "extra": {"spwm_scan_rows": 64}, "no_such_option": 1}, GEOM)
    assert o.gpio_slowdown == 3 and o.spwm_scan_rows == 64
    assert not hasattr(o, "enabled") and not hasattr(o, "env") and not hasattr(o, "no_such_option")
    assert "no_such_option" in caplog.text


def test_size_mismatch_warns(caplog):
    M.build_options(FakeOptions(), {"chain_length": 1}, GEOM)
    assert "128x128" in caplog.text


def test_disabled_or_missing_library_gives_none(monkeypatch):
    assert M.open_matrix(ctx_with({"enabled": False}), GEOM) is None
    monkeypatch.setitem(sys.modules, "rgbmatrix", None)               # import fails
    assert M.open_matrix(ctx_with({"enabled": True}), GEOM) is None


def test_not_root_gives_none(fake_lib, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    assert M.open_matrix(ctx_with({"enabled": True}), GEOM) is None


def test_library_crash_gives_none(fake_lib):
    def boom(options=None):
        raise RuntimeError("no gpio")
    fake_lib.RGBMatrix = boom
    assert M.open_matrix(ctx_with({"enabled": True}), GEOM) is None


def test_open_sets_spwm_env_and_shows_frames(fake_lib, monkeypatch):
    monkeypatch.delenv("SPWM_FRAME_END_SLEEP_US", raising=False)
    out = M.open_matrix(ctx_with({"enabled": True, "env": {"SPWM_FRAME_END_SLEEP_US": 300}}), GEOM)
    assert os.environ["SPWM_FRAME_END_SLEEP_US"] == "300"
    assert out.size == (256, 128)
    out.show(Image.new("RGB", (256, 128), (9, 8, 7)))
    out.show(Image.new("RGBA", (100, 50), (1, 2, 3, 255)))           # converted and padded to the panel size
    assert out.matrix.shown == [((256, 128), (9, 8, 7)), ((256, 128), (1, 2, 3))]
    out.close()
    assert out.matrix.cleared


def test_thread_keeps_only_the_newest_frame(fake_lib):
    out = M.open_matrix(ctx_with({"enabled": True}), GEOM)
    out.matrix.slow = 0.05                                             # panels slower than the renderer
    out.start()
    for v in range(20):
        out(Image.new("RGB", (256, 128), (v, 0, 0)))
        time.sleep(0.005)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (not out.matrix.shown or out.matrix.shown[-1][1] != (19, 0, 0)):
        time.sleep(0.01)
    out.close()
    assert out.matrix.shown[-1][1] == (19, 0, 0)                       # the last frame always lands
    assert len(out.matrix.shown) < 20                                  # stale ones were skipped, not queued
    assert not any(t.name == "matrix" for t in threading.enumerate())


def test_sink_copies_the_frame(fake_lib):
    out = M.open_matrix(ctx_with({"enabled": True}), GEOM)
    img = Image.new("RGB", (256, 128), (5, 5, 5))
    out(img)
    img.putpixel((0, 0), (200, 0, 0))                                  # engine reusing its image
    out.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not out.matrix.shown:
        time.sleep(0.01)
    out.close()
    assert out.matrix.shown[0][1] == (5, 5, 5)


def test_pattern_phases_and_brightness():
    quads = M.test_pattern((256, 128), 0, 100)
    assert quads.getpixel((0, 0)) == (255, 255, 255)                   # (0,0) marker
    assert quads.getpixel((60, 50)) == (200, 0, 0)                     # top-left is red
    assert quads.getpixel((200, 110)) == (180, 120, 0)                 # bottom-right is amber
    assert M.test_pattern((256, 128), 1, 100).getpixel((5, 5)) == (255, 0, 0)
    assert M.test_pattern((256, 128), 4, 30).getpixel((5, 5)) == (76, 76, 76)


def test_drop_root_is_a_noop_when_not_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    M.drop_root("someone")                                             # doesn't touch pwd/setuid
