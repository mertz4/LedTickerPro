"""Bottom ticker: live games, weather and markets, and smooth scrolling across live score updates."""
import copy

from PIL import Image

from ledticker.config import Context
from ledticker.render.engine import Ticker
from ledticker.render.images import ImageCache
from ledticker.render.screens import RC
from test_render import NOW, TZ, make_store


def rc_for(store, tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    return RC(ctx=ctx, store=store, images=ImageCache(tmp_path), W=256, H=128, main_h=116, now=NOW)


def live(store, mine: bool, score=(7, 3)):
    sb = copy.deepcopy(store.get("scores.nfl"))
    g = sb["games"][0]
    g.update(state="in", status="Q2 4:12", mine=mine)
    g["away"]["score"], g["home"]["score"] = score
    sb["games"] = [g]
    store.ok("scores.nfl", sb)
    return g


def test_default_contents_games_weather_markets(tmp_path):
    st = make_store()
    for sb in ("scores.nfl", "scores.cfb"):   # nothing live
        d = copy.deepcopy(st.get(sb))
        for g in d["games"]:
            g["state"] = "post"
        st.ok(sb, d)
    st.register("news", "news", True, "", 1800)
    st.ok("news", {"headlines": [{"source": "NPR", "title": "Something happened"}]})
    segs = [s for s, _ in Ticker().segments(rc_for(st, tmp_path))]
    assert not any(s.startswith(("NFL", "CFB")) for s in segs)        # no games on: no game segments
    assert any("COLLEGE STATION" in s and "F" in s for s in segs)     # weather
    assert any(s.startswith("SPY") for s in segs) and any(s.startswith("BTC") for s in segs)
    assert not any("NPR" in s for s in segs)                           # news is opt-in now

    live(st, mine=False)
    segs = [s for s, _ in Ticker().segments(rc_for(st, tmp_path))]
    assert segs[0].startswith("NFL") and "Q2 4:12" in segs[0]          # live games lead the ticker


def test_items_are_configurable(tmp_path):
    st = make_store()
    st.register("news", "news", True, "", 1800)
    st.ok("news", {"headlines": [{"source": "NPR", "title": "Something happened"}]})
    segs = [s for s, _ in Ticker(items=["news"]).segments(rc_for(st, tmp_path))]
    assert segs == ["NPR: Something happened"]


def test_score_update_does_not_jump_the_scroll(tmp_path):
    st = make_store()
    live(st, mine=True, score=(7, 3))
    rc = rc_for(st, tmp_path)
    tk = Ticker(speed=36)
    img = Image.new("RGB", (256, 128))
    for i in range(31):
        tk.draw(img, rc, i / 30, 222)
    before = tk.offset
    live(st, mine=True, score=(14, 3))                                 # touchdown: strip rebuilt
    tk.draw(img, rc, 31 / 30, 222)
    assert abs(tk.offset - (before + 36 / 30)) < 0.01                  # kept scrolling from the same spot
