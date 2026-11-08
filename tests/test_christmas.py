"""Christmas theme overlay + countdown widget."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops

from ledticker.config import Context
from ledticker.display import geometry
from ledticker.render import christmas
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.render.modes import DEFAULT_MODES, ModeError, ModeManager, validate
from ledticker.render.screens import RC
from ledticker.store import Store

TZ = ZoneInfo("America/Chicago")


def test_next_christmas_rolls_over_after_the_day():
    assert christmas.next_christmas(datetime(2026, 11, 7, 12, tzinfo=TZ)).date().isoformat() == "2026-12-25"
    assert christmas.next_christmas(datetime(2026, 12, 25, 23, 59, tzinfo=TZ)).year == 2026   # still the day
    assert christmas.next_christmas(datetime(2026, 12, 26, 0, 1, tzinfo=TZ)).year == 2027


def test_theme_setting_validation_and_season():
    widgets = {"weather", "clock", "game", "christmas_countdown"}
    doc = {**DEFAULT_MODES, "modes": {"m": {"slides": [{"widget": "clock"}]}}, "default": "m", "layouts": {}, "schedule": []}
    validate({**doc, "theme": {"name": "christmas", "snow_amount": 3}}, widgets)
    for bad in ({"name": "halloween"}, {"name": "christmas", "snow": "yes"}, {"name": "christmas", "snow_amount": 9}):
        with pytest.raises(ModeError):
            validate({**doc, "theme": bad}, widgets)
    mm = ModeManager(None)
    assert mm.theme_active(datetime(2026, 12, 10, tzinfo=TZ)) is None          # off by default
    mm.doc["theme"] = {"name": "christmas"}                                     # season_only defaults on
    assert mm.theme_active(datetime(2026, 12, 10, tzinfo=TZ)) == "christmas"
    assert mm.theme_active(datetime(2026, 11, 10, tzinfo=TZ)) is None
    mm.doc["theme"]["season_only"] = False
    assert mm.theme_active(datetime(2026, 7, 4, tzinfo=TZ)) == "christmas"


def test_overlay_never_touches_lit_pixels():
    frame = Image.new("RGB", (256, 128))
    for x in range(0, 256, 3):                       # dense content everywhere, including the edges
        for y in range(0, 128, 2):
            frame.putpixel((x, y), (200, 180, 20))
    before = frame.copy()
    christmas.decorate(frame, 4.2, {"garland": True, "snow": True, "snow_amount": 3}, ticker_h=11)
    diff = ImageChops.difference(before, frame).convert("L").point(lambda v: 255 if v else 0)
    lit = before.convert("L").point(lambda v: 255 if v else 0)
    assert ImageChops.multiply(diff, lit).getbbox() is None      # changed pixels are all ones that were dark
    assert diff.getbbox() is not None                            # and it did decorate something


@pytest.mark.parametrize("size", [(256, 117), (128, 64), (128, 53), (256, 40), (128, 20)])
@pytest.mark.parametrize("now", [datetime(2026, 11, 7, 6, tzinfo=TZ), datetime(2026, 12, 24, 18, tzinfo=TZ),
                                 datetime(2026, 12, 25, 9, tzinfo=TZ)])
def test_countdown_draws_at_every_size(monkeypatch, tmp_path, size, now):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(christmas, "datetime", Fixed)
    W, H = size
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    rc = RC(ctx=ctx, store=Store(None), images=ImageCache(tmp_path), W=W, H=H, main_h=H, now=now)
    img = Image.new("RGB", size)
    christmas.ChristmasCountdown().render(img, rc, 1.5)
    assert img.getbbox() is not None


def test_engine_applies_theme_only_when_on(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, Store(None), ImageCache(tmp_path), geometry(ctx), modes_path=None)
    assert "christmas_countdown" in eng.widgets
    plain = eng.render(t=2.0, screen="christmas_countdown")
    eng.modes.doc["theme"] = {"name": "christmas", "season_only": False}
    themed = eng.render(t=2.0, screen="christmas_countdown")
    assert ImageChops.difference(plain, themed).getbbox() is not None
