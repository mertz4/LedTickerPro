import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ledticker.sources.espn_scores import importance, parse_scoreboard

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_nfl_full_slate_with_odds():
    sb = parse_scoreboard("nfl", load("espn_nfl.json"), my_teams={"34"}, filt="all", max_big=8, tz=TZ, now=NOW)
    assert sb.week == 9 and sb.total_games == 15 and len(sb.games) == 15
    assert set(sb.byes) == {"TEN", "PIT"}
    assert not sb.offseason
    hou = next(g for g in sb.games if g.mine)
    assert (hou.away.abbr, hou.home.abbr) == ("HOU", "LAC")
    assert hou.odds and hou.odds.favorite == "HOU" and hou.odds.details == "HOU -6"
    assert hou.odds.over_under == 40.5 and hou.odds.ml_away.startswith("-")
    assert hou.start_local == "Sun Nov 8 3:05 PM"
    final = next(g for g in sb.games if g.state == "post")
    assert final.odds is None and final.away.score == 33 and final.home.score == 10
    assert [g.start_utc for g in sb.games] == sorted(g.start_utc for g in sb.games)


def test_cfb_big_games_filter():
    sb = parse_scoreboard("cfb", load("espn_cfb.json"), my_teams={"245"}, filt="big", max_big=8, tz=TZ, now=NOW)
    assert sb.total_games > 50
    assert len(sb.games) == 8
    assert any(g.mine and "TA&M" in (g.away.abbr, g.home.abbr) for g in sb.games)
    for g in sb.games:
        if not g.mine:
            assert g.away.rank and g.home.rank, f"{g.away.abbr}@{g.home.abbr} should be ranked vs ranked"


def test_live_basketball_has_clock():
    sb = parse_scoreboard("nba", load("espn_nba.json"), my_teams={"10"}, filt="all", max_big=8, tz=TZ, now=NOW)
    live = [g for g in sb.games if g.state == "in"]
    assert live and live[0].clock and live[0].period == 4


def test_offseason_detected_for_stale_results():
    d = load("espn_nba.json")
    for e in d["events"]:
        e["date"] = "2026-06-01T00:00Z"
        e["competitions"][0]["status"]["type"]["state"] = "post"
    sb = parse_scoreboard("nba", d, my_teams=set(), filt="all", max_big=8, tz=TZ, now=NOW)
    assert sb.offseason


def test_importance_ordering():
    sb = parse_scoreboard("cfb", load("espn_cfb.json"), my_teams={"245"}, filt="all", max_big=99, tz=TZ, now=NOW)
    by = {(g.away.abbr, g.home.abbr): g for g in sb.games}
    top10 = by[("UGA", "MISS")]       # #2 @ #10, SEC
    ranked_unranked = by[("TCU", "ARIZ")]
    assert top10.importance > ranked_unranked.importance > 0
    assert by[("TA&M", "SC")].importance == 1000
    g = top10
    g.state, g.period, g.away.score, g.home.score = "in", 4, 21, 17
    assert importance(g, "football", "8") > top10.importance - 1


def test_mine_filter_only_my_teams():
    sb = parse_scoreboard("nfl", load("espn_nfl.json"), my_teams={"34"}, filt="mine", max_big=8, tz=TZ, now=NOW)
    assert len(sb.games) == 1 and sb.games[0].mine and sb.total_games == 15
    sb = parse_scoreboard("nba", load("espn_nba.json"), my_teams={"10"}, filt="mine", max_big=8, tz=TZ, now=NOW)
    assert sb.games == []  # Rockets not playing in this fixture


def test_season_window():
    from ledticker.sources.base import season_wait
    nov = datetime(2026, 11, 5, 12, 0, tzinfo=TZ)
    assert season_wait(nov, "03-01", "06-30") == datetime(2027, 3, 1, tzinfo=TZ)
    assert season_wait(nov, "04-15", "11-10") is None                     # World Series window
    assert season_wait(datetime(2026, 12, 1, tzinfo=TZ), "04-15", "11-10") == datetime(2027, 4, 15, tzinfo=TZ)
    assert season_wait(datetime(2027, 1, 20, tzinfo=TZ), "04-15", "11-10") == datetime(2027, 4, 15, tzinfo=TZ)
    assert season_wait(datetime(2027, 1, 20, tzinfo=TZ), "09-01", "02-15") is None   # wraps New Year
    assert season_wait(nov, None, None) is None
