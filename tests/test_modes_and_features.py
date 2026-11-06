import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.render.modes import DEFAULT_MODES, ModeError, ModeManager, validate
from ledticker.sources.espn_fantasy import parse_league, parse_potw, parse_trades
from ledticker.sources.espn_props import Prop, dedupe, live_value, movers, parse_box, pick_games, rank_props
from ledticker.sources.espn_props import PropsBoard
from test_fantasy import SWID, league_fixture
from test_render import make_store

TZ = ZoneInfo("America/Chicago")


# ------------------------------------------------------------------ modes

def test_schedule_picks_mode_by_day_and_time():
    mm = ModeManager(None)
    sun = datetime(2026, 11, 8, 13, 0, tzinfo=TZ)
    mon = datetime(2026, 11, 9, 19, 0, tzinfo=TZ)
    wed = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)
    assert mm.active(sun)[0] == "sunday"
    assert mm.active(mon)[0] == "weeknight"
    assert mm.active(wed) == ("sweep", "default")
    mm.doc["schedule"].insert(0, {"days": ["thu"], "from": "18:00", "to": "23:59", "mode": "sunday"})
    assert mm.active(datetime(2026, 11, 12, 19, 30, tzinfo=TZ))[0] == "sunday"
    assert mm.active(datetime(2026, 11, 12, 12, 0, tzinfo=TZ))[0] == "sweep"
    mm.override = "weeknight"
    assert mm.active(sun) == ("weeknight", "manual override")


def test_modes_saved_and_reloaded(tmp_path):
    p = tmp_path / "modes.json"
    mm = ModeManager(p)
    doc = copy.deepcopy(DEFAULT_MODES)
    doc["modes"]["thursday"] = {"label": "TNF", "slides": [{"quad": ["nfl_game", "props_mini", "fantasy_mini", ""]}]}
    mm.save(doc)
    assert ModeManager(p).doc["modes"]["thursday"]["label"] == "TNF"


def test_validate_rejects_bad_documents():
    screens = {"game", "fantasy"}
    tiles = {"weather", "markets"}
    good = {"default": "a", "modes": {"a": {"slides": [{"screen": "game"}, {"quad": ["weather", "", "markets"]}]}},
            "schedule": [{"days": ["sun"], "mode": "a"}]}
    assert validate(good, screens, tiles)
    bad = [
        {"default": "a", "modes": {}},
        {"default": "x", "modes": {"a": {"slides": [{"screen": "game"}]}}},
        {"default": "a", "modes": {"a": {"slides": [{"screen": "nope"}]}}},
        {"default": "a", "modes": {"a": {"slides": [{"quad": ["nope"]}]}}},
        {"default": "a", "modes": {"a": {"slides": [{"screen": "game", "seconds": 1}]}}},
        {"default": "a", "modes": {"a": {"slides": [{"screen": "game"}]}}, "schedule": [{"days": ["funday"], "mode": "a"}]},
        {"default": "a", "modes": {"a": {"slides": [{"screen": "game"}]}}, "schedule": [{"days": ["sun"], "mode": "a", "from": "6pm"}]},
    ]
    for doc in bad:
        with pytest.raises(ModeError):
            validate(doc, screens, tiles)


def test_default_modes_reference_real_screens_and_tiles(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, make_store(), ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "m.json")
    assert validate(copy.deepcopy(DEFAULT_MODES), set(eng.screens), set(eng.tiles))


@pytest.mark.parametrize("geom", [Geometry(128, 64, 2, 2), Geometry(128, 56, 2, 2)], ids=["128x64", "128x56"])
def test_every_tile_and_quad_renders(tmp_path, geom):
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}}}, secrets={}, tz=TZ)
    store = make_store()
    lg = parse_league(league_fixture(), swid=SWID, prev=league_fixture())
    store.register("fantasy", "fantasy", True, "", 1800)
    store.ok("fantasy", lg)
    store.register("props", "props", True, "", 1800)
    store.ok("props", PropsBoard(props=[_prop("Stroud", "PASS YDS", 246.5, 200.0, live=150.0)]))
    eng = Engine(ctx, store, ImageCache(tmp_path), geom, modes_path=tmp_path / "m.json")
    names = list(eng.tiles)
    for i in range(0, len(names), 4):
        frame = eng.render(t=3.0, screen="quad:" + ",".join(names[i:i + 4]))
        assert frame.size == (geom.width, geom.height) and frame.getbbox()
    for name in ("fantasy_standings", "fantasy_potw", "props"):
        assert name in eng.available(), name
        for t in (0.1, 6.0, 11.0):  # mid-slide-in, and later pages
            assert eng.render(t=t, screen=name).getbbox()


def test_rotation_follows_mode_and_quads(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, make_store(), ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "m.json")
    eng.modes.override = "weeknight"
    eng.render()
    assert eng.mode_name == "weeknight"
    assert eng.current == "layout:four_up_weeknight"


# ------------------------------------------------------------------ fantasy: trades + player of the week

def test_trades_from_activity_feed():
    feed = {"topics": [{"id": "t1", "date": 1793644874487, "messages": [
        {"messageTypeId": 244, "targetId": 11, "from": 1, "to": 4},
        {"messageTypeId": 244, "targetId": 22, "from": 4, "to": 1},
        {"messageTypeId": 244, "targetId": 33, "from": 4, "to": 1},
        {"messageTypeId": 178, "targetId": 99, "from": 0, "to": 4},   # an add, not part of the trade
    ]}]}
    trades = parse_trades(feed, {11: "C.J. Stroud", 22: "Isaiah Likely"}, {1: "The Vets", 4: "Mine"})
    assert len(trades) == 1
    sides = {s.team_name: s.receives for s in trades[0].sides}
    assert sides == {"Mine": ["C.J. Stroud"], "The Vets": ["Isaiah Likely", "Player 33"]}
    assert trades[0].date.startswith("2026-11-0")


def test_player_of_the_week():
    prev = league_fixture()
    for t in prev["teams"]:
        for e in t.get("roster", {}).get("entries", []):
            for s in e["playerPoolEntry"]["player"]["stats"]:
                s["scoringPeriodId"] = 8
    pw = parse_potw(prev, prev["schedule"], 8)
    assert pw.week == 8
    assert [p.name for p in pw.players] == ["Bench Guy", "QB One", "WR Two"]
    assert pw.players[0].starter is False
    assert pw.top_team_id == 4 and pw.top_team_points == 99


# ------------------------------------------------------------------ props

def _event(eid, date, state, teams=("1", "2"), short="A @ B"):
    return {"id": eid, "date": date, "shortName": short, "competitions": [{
        "status": {"type": {"state": state, "shortDetail": state}},
        "competitors": [{"team": {"id": teams[0], "abbreviation": "A"}}, {"team": {"id": teams[1], "abbreviation": "B"}}]}]}


def test_pick_games_follows_whats_on():
    now = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)  # Sunday early window
    evs = [_event("e1", "2026-11-08T18:00Z", "in", ("1", "2")), _event("e2", "2026-11-08T18:00Z", "in", ("34", "24")),
           _event("e3", "2026-11-09T01:20Z", "pre", ("27", "3"))]
    got = pick_games(evs, now, {"34"}, {}, max_games=1)
    assert got[0].id == "e2" and got[0].reason == "LIVE"          # my team's live game wins
    later = datetime(2026, 11, 9, 0, 30, tzinfo=timezone.utc)     # between windows: next up is SNF
    evs2 = [_event("e1", "2026-11-08T18:00Z", "post"), _event("e3", "2026-11-09T01:20Z", "pre", ("27", "3"))]
    got2 = pick_games(evs2, later + timedelta(hours=6), set(), {}, max_games=1)
    assert got2[0].id == "e3" and got2[0].primetime


def _prop(name, market, line, opened, live=None, fan=False, team=False, aid=1):
    return Prop(game_id="g", athlete_id=aid, name=name, short_name=name, team="HOU", position="QB", headshot=None,
                market=market, line=line, open=opened, move=round(line - opened, 1) if opened is not None else 0,
                live=live, my_fantasy=fan, my_team=team)


def test_props_ranking_dedupe_and_movers():
    ps = [_prop("A", "REC", 4.5, 4.5, aid=1), _prop("A", "REC", 4.5, 4.5, aid=1),
          _prop("Mover", "RUSH YDS", 60.5, 50.5, aid=2), _prop("Mine", "PASS YDS", 250.5, 251.5, fan=True, aid=3)]
    u = dedupe(ps)
    assert len(u) == 3
    assert rank_props(u)[0].name == "Mine"
    assert movers(u)[0].name == "Mover"


def test_live_box_values():
    summary = {"boxscore": {"players": [{"statistics": [
        {"keys": ["completions/passingAttempts", "passingYards"], "athletes": [{"athlete": {"id": "7"}, "stats": ["16/26", "242"]}]},
        {"keys": ["rushingAttempts", "rushingYards"], "athletes": [{"athlete": {"id": "8"}, "stats": ["10", "55"]}]},
        {"keys": ["receptions", "receivingYards"], "athletes": [{"athlete": {"id": "8"}, "stats": ["3", "20"]}]},
    ]}]}}
    box = parse_box(summary)
    assert live_value(box, 7, ("passing", "passingYards")) == 242
    assert live_value(box, 7, ("passing", "completions")) == 16
    assert live_value(box, 8, ("rushrec", "yards")) == 75
    assert live_value(box, 99, ("receiving", "receptions")) is None


# ------------------------------------------------------------------ custom layouts + display schedule

def test_layout_regions_any_non_diagonal_split():
    from ledticker.render.layout import regions
    # top strip 16 px, then left 1/3 | right 2/3, right side stacked
    tree = {"split": "rows", "at": 16, "a": {"widget": "ticker"},
            "b": {"split": "cols", "at": 85, "a": {"widget": "weather"},
                  "b": {"split": "rows", "at": 50, "a": {"widget": "nfl_game"}, "b": {"widget": "markets"}}}}
    rs = regions(tree, 0, 0, 256, 128)
    assert [(r.widget, r.x, r.y, r.w, r.h) for r in rs] == [
        ("ticker", 0, 0, 256, 16), ("weather", 0, 16, 85, 112),
        ("nfl_game", 85, 16, 171, 50), ("markets", 85, 66, 171, 62)]
    assert sum(r.w * r.h for r in rs) == 256 * 128          # tiles the display exactly
    clamp = regions({"split": "cols", "at": 999, "a": {}, "b": {}}, 0, 0, 256, 128)
    assert clamp[1].w == 8                                  # split positions are clamped


def test_custom_layouts_render(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, make_store(), ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "m.json")
    for name in eng.modes.doc["layouts"]:
        assert eng.render(t=1.0, screen=f"layout:{name}").getbbox(), name
    eng.pin_layout({"tree": {"split": "cols", "at": 170, "a": {"widget": "weather"},
                             "b": {"split": "rows", "at": 30, "a": {"widget": "clock"}, "b": {"widget": "ticker"}}}})
    assert eng.render().getbbox() and eng.current == "layout:(preview)"


def test_schedule_dates_overnight_brightness_and_power():
    mm = ModeManager(None)
    mm.doc["schedule"] = [
        {"dates": ["2026-11-26"], "mode": "sweep"},
        {"date_from": "2026-12-20", "date_to": "2027-01-02", "mode": "weeknight"},
        {"days": ["sun"], "mode": "sunday"},
        {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "from": "23:00", "to": "07:00", "brightness": 20},
        {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "01:00", "to": "06:30", "power": "off"},
    ]
    validate(mm.doc, {"alert", "trade", "game", "fantasy", "weather", "board.cfb", "fantasy_scores",
                      "fantasy_standings", "markets", "board.nfl", "props", "fantasy_potw", "nfl_game",
                      "fantasy_mini", "props_mini", "standings_mini"})
    assert mm.active(datetime(2026, 11, 26, 12, 0, tzinfo=TZ))[0] == "sweep"          # Thanksgiving date rule
    assert mm.active(datetime(2026, 12, 27, 12, 0, tzinfo=TZ))[0] == "weeknight"      # inside date range (a Sunday!)
    assert mm.active(datetime(2026, 11, 29, 12, 0, tzinfo=TZ))[0] == "sunday"
    noon = mm.display(datetime(2026, 11, 10, 12, 0, tzinfo=TZ))
    assert noon["brightness"] == 100 and noon["power"] == "on"
    late = mm.display(datetime(2026, 11, 10, 23, 30, tzinfo=TZ))
    assert late["brightness"] == 20 and late["power"] == "on"
    tue_3am = mm.display(datetime(2026, 11, 10, 3, 0, tzinfo=TZ))
    assert tue_3am["brightness"] == 20 and tue_3am["power"] == "off"
    sat_3am = mm.display(datetime(2026, 11, 14, 3, 0, tzinfo=TZ))                     # Mon-Fri mornings only
    assert sat_3am["power"] == "on" and mm.display(datetime(2026, 11, 13, 3, 0, tzinfo=TZ))["power"] == "off"
    sun_3am = mm.display(datetime(2026, 11, 15, 3, 0, tzinfo=TZ))                     # Sat night: no power rule
    assert sun_3am["power"] == "on"
    mm.display_override = {"brightness": 60}
    assert mm.display(datetime(2026, 11, 10, 23, 30, tzinfo=TZ))["brightness"] == 60


def test_brightness_and_power_applied_to_output(tmp_path):
    from PIL import Image
    ctx = Context(root=tmp_path, config={}, secrets={}, tz=TZ)
    eng = Engine(ctx, make_store(), ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "m.json")
    frame = Image.new("RGB", (256, 128), (200, 100, 50))
    eng.display_state = {"brightness": 50, "power": "on"}
    assert eng.output(frame).getpixel((0, 0)) == (100, 50, 25)
    eng.display_state = {"brightness": 100, "power": "off"}
    assert eng.output(frame).getbbox() is None


def test_old_modes_file_is_upgraded():
    from ledticker.render.modes import upgrade
    old = {"default": "a", "schedule": [], "modes": {"a": {"slides": [{"screen": "game"}, {"quad": ["weather"]}]}}}
    new = upgrade(old)
    assert new["version"] == 2 and new["layouts"] and new["modes"]["a"]["slides"][0] == {"widget": "game"}


def test_validate_rejects_bad_layouts_and_rules():
    w = {"game", "weather"}
    base = lambda **kw: {"default": "a", "modes": {"a": {"slides": [{"widget": "game"}]}}, **kw}
    with pytest.raises(ModeError):
        validate(base(layouts={"x": {"tree": {"split": "diagonal", "at": 10, "a": {}, "b": {}}}}), w)
    with pytest.raises(ModeError):
        validate(base(layouts={"x": {"tree": {"widget": "nope"}}}), w)
    with pytest.raises(ModeError):
        validate({"default": "a", "modes": {"a": {"slides": [{"layout": "missing"}]}}}, w)
    with pytest.raises(ModeError):
        validate(base(schedule=[{"days": ["mon"]}]), w)                       # rule does nothing
    with pytest.raises(ModeError):
        validate(base(schedule=[{"days": ["mon"], "brightness": 150}]), w)
    with pytest.raises(ModeError):
        validate(base(schedule=[{"dates": ["11/26/2026"], "power": "off"}]), w)
    with pytest.raises(ModeError):
        validate(base(schedule=[{"power": "off"}]), w)                        # no days or dates
