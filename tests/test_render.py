import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render import icons
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache, fit_square, lift_dark
from ledticker.sources.espn_fantasy import parse_league
from ledticker.sources.espn_scores import parse_scoreboard
from ledticker.sources.markets import Quote, Quotes
from ledticker.sources.nws import Weather, parse_alerts, parse_hourly, parse_obs, parse_periods
from ledticker.store import Store
from test_fantasy import SWID, league_fixture

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def make_store(with_alert=False) -> Store:
    st = Store(None)

    def put(name, data):
        st.register(name, name, True, "", 1800)
        st.ok(name, data)

    put("scores.nfl", parse_scoreboard("nfl", load("espn_nfl.json"), my_teams={"34"}, filt="all", max_big=8, tz=TZ, now=NOW))
    put("scores.cfb", parse_scoreboard("cfb", load("espn_cfb.json"), my_teams={"245"}, filt="big", max_big=8, tz=TZ, now=NOW))
    put("fantasy", parse_league(league_fixture(), swid=SWID))
    w = Weather(location="College Station, TX", current=parse_obs(load("nws_obs.json"), "KCLL"),
                periods=parse_periods(load("nws_forecast.json")), hourly=parse_hourly(load("nws_hourly.json")),
                alerts=parse_alerts(load("nws_alerts_sample.json")) if with_alert else [])
    put("weather", w)
    put("markets.stocks", Quotes("stocks", False, [Quote("SPY", 733.0, -33.8, -4.41), Quote("TSLA", 343.69, -41.5, -10.77)]))
    put("markets.crypto", Quotes("crypto", True, [Quote("BTC", 75551.0, -4882.0, -6.07)]))
    return st


@pytest.mark.parametrize("geom", [Geometry(128, 64, 2, 2), Geometry(128, 56, 2, 2)], ids=["128x64", "128x56"])
@pytest.mark.parametrize("alert", [False, True])
def test_every_screen_renders_without_network(tmp_path, geom, alert):
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}, "cfb": {"teams": ["245"]}}},
                  secrets={}, tz=TZ)
    eng = Engine(ctx, make_store(alert), ImageCache(tmp_path), geom)
    avail = eng.available()
    assert {"fantasy", "fantasy_scores", "weather", "board.cfb", "board.nfl", "markets"} <= set(avail)
    assert ("alert" in avail) == alert
    for name in eng.screens:
        if name not in avail:
            continue
        frame = eng.render(t=1.0, screen=name)
        assert frame.size == (geom.width, geom.height)
        # something was drawn, and no "render error" fallback (red text in the top-left)
        assert frame.getbbox() is not None
        px = frame.load()
        assert not any(px[x, 6] == (255, 60, 50) for x in range(4, 60)), f"{name} hit its error fallback"


def test_rotation_skips_screens_without_data(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    st = Store(None)
    eng = Engine(ctx, st, ImageCache(tmp_path), Geometry())
    eng.render(t=0)
    assert eng.current == "__empty__"


def test_icon_mapping_covers_all_nws_codes():
    for code, name in icons.NWS_MAP.items():
        assert name in icons.ICONS
        assert icons.icon_for(f"https://api.weather.gov/icons/land/night/{code},20?size=medium") == (name, True)
    assert icons.icon_for(None, "Slight Chance Rain Showers") == ("rain", False)
    assert icons.icon_for(None, "something new") == ("overcast", False)
    for name in icons.ICONS:
        for size in (16, 32, 48):
            im = icons.get(name, size, False, 0)
            assert im.size == (size, size) and im.getbbox() is not None


def test_logo_processing():
    from PIL import Image
    maroon = Image.new("RGBA", (200, 100), (80, 0, 0, 255))
    out = fit_square(maroon, 32)
    assert out.size == (32, 32)
    r, g, b, a = out.getpixel((16, 16))
    assert r > 80 and a == 255                              # dark logo was brightened
    assert out.getpixel((16, 2))[3] == 0                    # letterboxed area stays transparent
    bright = Image.new("RGBA", (10, 10), (250, 250, 250, 255))
    assert lift_dark(bright).getpixel((5, 5)) == (250, 250, 250, 255)
