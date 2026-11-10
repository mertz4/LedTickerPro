"""Big plays: my fantasy players' big plays (fireworks), Texans scores (Texans theme), and the stinker for rival
owners' players and rival NFL teams. Detection runs on a real game's play-by-play (HOU @ LAC, 2026 week 10)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render.bigplay import BigPlayScreen, sample_play
from ledticker.render.engine import BIG_PLAY, BREAKING, Engine
from ledticker.render.images import ImageCache
from ledticker.render.modes import ModeError, validate, DEFAULT_MODES
from ledticker.render.screens import RC
from ledticker.render.widgets import all_widgets
from ledticker.sources.espn_fantasy import parse_league
from ledticker.sources.espn_plays import EspnBigPlays, _game_info, scoring_table
from test_fantasy import SWID, entry, league_fixture, player
from test_render import TZ, make_store

FIX = Path(__file__).parent / "fixtures"
GAME = "401873046"
STROUD, DICKER, COLLINS = 4432577, 4362081, 4258173


def plays():
    return json.loads((FIX / "espn_nfl_plays.json").read_text(encoding="utf-8"))["items"]


def game():
    sb = json.loads((FIX / "espn_nfl_live_sb.json").read_text(encoding="utf-8"))
    return _game_info(sb["events"][0])


def rp(pid, name, pos, slot, team_abbr, team_id, starter=True, ft=None):
    return {"player_id": pid, "name": name, "position": pos, "slot": slot, "starter": starter, "nfl_team": team_abbr,
            "nfl_team_id": team_id, "fantasy_team_id": ft}


def scan(roster, mine=frozenset(), rivals=None, foes=frozenset()):
    src = EspnBigPlays.__new__(EspnBigPlays)
    src._seen, src._scores = {}, {GAME: {"away": 0, "home": 0}}
    return src.scan(plays(), game(), roster, scoring_table(None), set(mine), rivals=rivals, foes=set(foes))


def test_fantasy_big_plays_and_texans_scores_from_real_play_by_play():
    roster = {STROUD: rp(STROUD, "C.J. Stroud", "QB", "QB", "HOU", 34),
              DICKER: rp(DICKER, "Cameron Dicker", "K", "BE", "LAC", 24, starter=False)}
    found = scan(roster, mine={"34"})
    tds = [p for p in found if p.players and p.players[0].name == "C.J. Stroud"]
    assert [p.yards for p in tds] == [17, 63, 12, 13, 63]
    assert all(p.touchdown and p.kind == "pass" and p.team_score == "HOU" and p.team_points == 7 for p in tds)
    assert tds[1].points == pytest.approx(63 * 0.04 + 4)
    fg52 = next(p for p in found if p.headline == "52 YD FIELD GOAL")
    assert fg52.points == 5.0 and not fg52.players[0].starter and fg52.team_score is None
    # Texans field goals with none of my players on them still count (as team scores)
    hou_fgs = [p for p in found if p.team_score == "HOU" and not p.players]
    assert [p.team_points for p in hou_fgs] == [3, 3, 3]
    assert hou_fgs[-1].score == "HOU 44  LAC 22"
    assert all(p.mood == "cheer" for p in found)


def test_receiver_gets_yards_reception_and_td():
    collins = {COLLINS: rp(COLLINS, "Nico Collins", "WR", "WR", "HOU", 34)}
    found = scan(collins)
    assert [p.headline for p in found][:1] == ["19 YD CATCH"]           # 15+ yards: reported (the bar is set later)
    td = next(p for p in found if p.touchdown)
    assert td.kind == "catch" and td.headline == "13 YD TD CATCH"
    assert td.players[0].points == pytest.approx(1.3 + 1 + 6)


def test_rivals_get_the_stinker():
    rivals = {DICKER: {**rp(DICKER, "Cameron Dicker", "K", "K", "LAC", 24, ft=12), "fantasy_team": "The Vets",
                       "owner": "Michael Frain"}}
    found = scan({}, mine={"34"}, rivals=rivals, foes={"24"})
    boo = [p for p in found if p.mood == "boo"]
    kicks = [p for p in boo if p.players]
    assert {p.yards for p in kicks} == {52, 33, 43}
    assert kicks[0].rival_team == "The Vets" and kicks[0].rival_owner == "Michael Frain"
    assert kicks[0].team_score == "LAC"           # rival player + rival team: one stinker, not two
    # the Chargers' touchdowns (no fantasy player of anyone's involved) are rival-team stinkers
    lac_tds = [p for p in boo if not p.players and p.touchdown]
    assert lac_tds and all(p.team_score == "LAC" for p in lac_tds)
    # my team's scores never become stinkers
    assert all(p.team_score != "HOU" for p in boo)


def test_league_owners_and_rival_roster():
    d = league_fixture()
    d["members"] = [{"id": "{BBBB}", "firstName": "Michael", "lastName": "Frain"},
                    {"id": "{CCCC}", "firstName": "Riley", "lastName": "Fuller"}]
    d["teams"][2]["roster"] = {"entries": [entry(0, player("Rival QB", 1, 6, 9, actual=3.0))]}
    lg = parse_league(d, swid=SWID, rivals=["michael frain", "Riley  Fuller"])
    assert {t.name: t.owner for t in lg.teams}["Alpha"] == "Michael Frain"
    assert sorted(p.name for p in lg.rival_roster) == ["Rival QB", "WR Two"]
    assert {p.fantasy_team_id for p in lg.rival_roster} == {1, 5}
    assert parse_league(d, swid=SWID).rival_roster == []


BR = DEFAULT_MODES["breaking"]


def play(**kw):
    p = {"id": "p1", "when": datetime.now(timezone.utc).isoformat(), "yards": 8, "touchdown": False, "points": 2.0,
         "players": [{"name": "X", "role": "receiver", "points": 2.0, "starter": True}], "mood": "cheer",
         "team_score": None}
    p.update(kw)
    return p


@pytest.mark.parametrize("kw,shown", [
    ({}, False),
    ({"yards": 20}, True),                                              # 20+ yard catch
    ({"touchdown": True}, True),
    ({"points": 5.2, "players": [{"name": "Q", "role": "passer", "points": 5.2, "starter": True}]}, True),
    ({"yards": 30, "players": [{"name": "Q", "role": "passer", "points": 1.2, "starter": True}]}, False),
    ({"touchdown": True, "players": [{"name": "B", "role": "rusher", "points": 7, "starter": False}]}, False),  # bench
    ({"players": [], "team_score": "HOU", "team_points": 3}, True),     # any Texans score
    ({"touchdown": True, "mood": "boo"}, True),
])
def test_thresholds(kw, shown):
    assert bool(Engine.play_qualifies(play(**kw), BR)) is shown


def test_settings_turn_kinds_off():
    assert not Engine.play_qualifies(play(touchdown=True), {**BR, "big_plays": False})
    assert not Engine.play_qualifies(play(players=[], team_score="HOU"), {**BR, "team_scores": False})
    assert not Engine.play_qualifies(play(touchdown=True, mood="boo"), {**BR, "rival_plays": False})
    assert not Engine.play_qualifies(play(players=[], team_score="DAL", mood="boo"), {**BR, "rival_scores": False})
    assert Engine.play_qualifies(play(yards=12), {**BR, "min_yards": 10})


def engine(tmp_path, plays_list):
    st = make_store()
    st.register("big_plays", "big plays", True, "", 1800)
    st.ok("big_plays", {"plays": plays_list})
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}, "cfb": {"teams": ["245"]}}},
                  secrets={}, tz=TZ)
    return Engine(ctx, st, ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "modes.json")


def test_big_play_takes_over_then_resumes(tmp_path):
    eng = engine(tmp_path, [play(id="new", touchdown=True)])
    eng._check_interrupts = lambda rc: None
    eng.render(t=0)
    before = eng.current
    del eng._check_interrupts
    eng._last_check = 0
    eng.render()
    assert eng.current == BIG_PLAY and eng._ticker_on() is False
    assert eng.widgets[BIG_PLAY].full.play["id"] == "new"
    eng._breaking_until = 0
    eng.render()
    assert eng.current == before and eng.widgets[BIG_PLAY].full.play is None
    eng._last_check = 0
    eng.render()
    assert eng.current == before                 # seen: doesn't replay


def test_stale_and_small_plays_are_skipped(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    eng = engine(tmp_path, [play(id="old", touchdown=True, when=old), play(id="small")])
    eng.render(t=0)
    assert eng.current not in (BIG_PLAY, BREAKING)
    assert {"play:old", "play:small"} <= eng._seen


@pytest.mark.parametrize("which", ["fantasy", "team", "rival", "rival_team"])
def test_test_buttons_and_next(tmp_path, which):
    eng = engine(tmp_path, [])
    eng.render(t=0)
    eng.big_play_test(which)
    eng.render()
    assert eng.current == BIG_PLAY
    assert (eng.widgets[BIG_PLAY].full.play.get("mood") == "boo") == which.startswith("rival")
    eng.skip()
    eng.render()
    assert eng.current != BIG_PLAY


@pytest.mark.parametrize("geom", [(256, 128), (256, 112), (128, 64)])
@pytest.mark.parametrize("which", ["fantasy", "team", "rival", "rival_team"])
@pytest.mark.parametrize("kind", ["catch", "run", "kick", "defense"])
def test_every_frame_renders(tmp_path, geom, which, kind):
    W, H = geom
    rc = RC(ctx=Context(root=tmp_path, config={}, secrets={}, tz=TZ), store=make_store(), images=ImageCache(tmp_path),
            W=W, H=H, main_h=H, now=datetime.now(timezone.utc))
    scr = BigPlayScreen()
    scr.play = {**sample_play(which, rc), "kind": kind}
    lit = []
    for t in (0, 0.7, 1.9, 2.3, 3.0, 3.5, 4.0, 4.7, 5.2, 6.0, 7.0, 8.0, 11.0, 19.5):
        img = Image.new("RGB", (W, H))
        scr.render(img, rc, t)
        lit.append(img.getbbox() is not None)
    assert all(lit)


def test_settings_validate():
    doc = json.loads(json.dumps(DEFAULT_MODES))
    widgets = set(all_widgets())
    validate(doc, widgets)
    for bad in ({"min_yards": 2}, {"play_seconds": 500}, {"bench": "yes"}):
        d = json.loads(json.dumps(DEFAULT_MODES))
        d["breaking"].update(bad)
        with pytest.raises(ModeError):
            validate(d, widgets)


def test_takeover_is_full_screen_however_shown(tmp_path):
    eng = engine(tmp_path, [])
    eng.render(t=0)
    assert eng._ticker_on() is True
    eng.big_play_test("team")
    eng.render()
    assert eng._ticker_on() is False                     # live takeover: no ticker, no clock
    eng.skip()
    eng.render()
    eng.pin(BIG_PLAY)
    assert eng._ticker_on() is False                     # pinned from the dashboard: still the whole display
    eng.pin(None)
    eng.widgets[BIG_PLAY].full.play = sample_play("team", eng.rc())
    full = eng.render(t=5.0, screen=BIG_PLAY)            # snapshots too
    r, g, b = full.getpixel((eng.geom.width // 3, eng.geom.height - 3))
    assert g > r and g > b                               # the field runs to the bottom edge: no ticker strip
