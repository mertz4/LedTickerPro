"""Every widget draws at every section shape (full, tile, side-by-side, stacked, with and without the ticker),
and compact tile designs are fitted to big sections instead of sitting in a corner."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from ledticker.config import Context
from ledticker.display import geometry
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.render.layout import Region
from ledticker.store import Store

TZ = ZoneInfo("America/Chicago")
SHAPES = [(256, 117), (128, 58), (128, 117), (256, 58), (256, 128), (128, 64), (128, 128), (256, 64)]


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    root = tmp_path_factory.mktemp("shapes")
    ctx = Context(root=root, config={}, secrets={}, tz=TZ)
    return Engine(ctx, Store(None), ImageCache(root), geometry(ctx), modes_path=None)


@pytest.mark.parametrize("size", SHAPES)
def test_every_available_widget_draws_at_every_shape(eng, size):
    W, H = size
    rc = eng.rc(True)
    for name, w in eng.widgets.items():
        if not name:
            continue
        sub = eng._sub(rc, Region(name, 0, 0, W, H))
        impl = w.impl(W, H)
        if name != "ticker" and not impl.available(sub):
            continue
        cell = Image.new("RGB", size)
        eng.draw_widget(w, impl, cell, sub, 1.0)      # must not raise


def test_tile_scales_up_at_full_screen_and_centers_in_tall_halves(eng):
    w = eng.widgets["clock"]
    rc = eng.rc(True)

    def draw(W, H):
        cell = Image.new("RGB", (W, H))
        eng.draw_widget(w, w.impl(W, H), cell, eng._sub(rc, Region("clock", 0, 0, W, H)), 1.0)
        return cell.getbbox()

    small = draw(128, 58)
    full = draw(256, 117)
    assert (full[3] - full[1]) >= 1.8 * (small[3] - small[1])   # 2x, not a little clock in a big box
    tall = draw(128, 117)
    assert tall[1] > small[1] + 20                                # pushed down to the middle, not stuck at the top
