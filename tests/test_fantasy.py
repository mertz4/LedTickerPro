from datetime import datetime

from ledticker.sources.espn_fantasy import in_nfl_window, parse_league

SWID = "{AAAA-1111}"


def player(name, pos, pro, week, actual=None, proj=None, injury="ACTIVE"):
    stats = []
    if actual is not None:
        stats.append({"scoringPeriodId": week, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": actual})
    if proj is not None:
        stats.append({"scoringPeriodId": week, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": proj})
    return {"fullName": name, "defaultPositionId": pos, "proTeamId": pro, "injuryStatus": injury, "stats": stats}


def entry(slot, p):
    return {"lineupSlotId": slot, "injuryStatus": p["injuryStatus"], "playerPoolEntry": {"player": p}}


def team(tid, name, w, l, pf, owners, roster=None):
    t = {"id": tid, "name": name, "abbrev": name[:4].upper(), "owners": owners,
         "record": {"overall": {"wins": w, "losses": l, "ties": 0, "pointsFor": pf, "pointsAgainst": 0}}}
    if roster is not None:
        t["roster"] = {"entries": roster}
    return t


def league_fixture():
    wk = 9
    mine = [entry(0, player("QB One", 1, 34, wk, actual=20.5, proj=18.0)),
            entry(2, player("RB One", 2, 1, wk, actual=None, proj=15.0, injury="QUESTIONABLE")),
            entry(20, player("Bench Guy", 3, 12, wk, actual=30.0, proj=9.0))]
    other = [entry(4, player("WR Two", 3, 30, wk, actual=12.0, proj=11.0))]
    return {
        "seasonId": 2026, "scoringPeriodId": wk,
        "settings": {"name": "Test League"},
        "status": {"currentMatchupPeriod": wk, "finalScoringPeriod": 17},
        "teams": [team(1, "Alpha", 6, 2, 900.0, ["{BBBB}"], other),
                  team(4, "Mine", 2, 6, 790.0, [SWID.lower()], mine),
                  team(5, "Gamma", 6, 2, 950.0, ["{CCCC}"], [])],
        "schedule": [
            {"id": 50, "matchupPeriodId": 8, "home": {"teamId": 4, "totalPoints": 99}, "away": {"teamId": 1, "totalPoints": 80}},
            {"id": 60, "matchupPeriodId": 9, "winner": "UNDECIDED",
             "home": {"teamId": 4, "totalPoints": 0.0, "totalProjectedPoints": 110.0},
             "away": {"teamId": 1, "totalPoints": 0.0, "totalProjectedPoints": 120.0}},
            {"id": 61, "matchupPeriodId": 9, "home": {"teamId": 5, "totalPoints": 0.0}},
        ],
    }


def test_parse_league():
    lg = parse_league(league_fixture(), swid=SWID)
    assert lg.name == "Test League" and lg.week == 9 and lg.my_team_id == 4
    # standings: wins desc, then points for
    assert [t.name for t in lg.teams] == ["Gamma", "Alpha", "Mine"]
    assert [t.standing for t in lg.teams] == [1, 2, 3]
    assert len(lg.matchups) == 2 and lg.matchups[0].mine
    m = lg.matchups[0]
    # live starter points win over ESPN's lagging 0.0 total; bench points excluded
    assert m.home.points == 20.5 and m.away.points == 12.0
    assert m.home.projected == 110.0
    assert lg.matchups[1].away is None  # bye
    starters = [p for p in lg.my_roster if p.starter]
    assert [p.slot for p in starters] == ["QB", "RB"] and starters[0].nfl_team == "HOU"
    assert starters[1].injury == "QUESTIONABLE" and starters[1].points is None
    assert lg.my_roster[-1].slot == "BE"


def test_team_override():
    assert parse_league(league_fixture(), swid=None, my_team_override="1").my_team_id == 1
    assert parse_league(league_fixture(), swid=None).my_team_id is None


def test_nfl_windows():
    assert in_nfl_window(datetime(2026, 11, 8, 12, 0))      # Sunday noon
    assert in_nfl_window(datetime(2026, 11, 9, 20, 0))      # Monday night
    assert not in_nfl_window(datetime(2026, 11, 10, 20, 0))  # Tuesday
