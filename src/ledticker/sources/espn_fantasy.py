"""ESPN Fantasy Football league (unofficial API). Private leagues need espn_s2 + SWID cookies."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from .base import Source, SourceAuthError, SourceError, dig, get_json

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"

AUTH_HINT = ("ESPN rejected the league request. If the league is private, the espn_s2/SWID cookies "
             "are missing or expired: re-copy espn_s2 from your browser into secrets.env.")

SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 20: "BE", 21: "IR", 23: "FLEX"}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH_SLOTS = {20, 21}
# fantasy proTeamId == ESPN NFL team id
NFL_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB",
    10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


@dataclass
class FantasyTeam:
    id: int
    name: str
    abbrev: str | None
    logo: str | None
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    seed: int | None
    standing: int            # 1 = first place (by seed, else W-L then PF)
    mine: bool


@dataclass
class Side:
    team_id: int
    points: float
    projected: float | None


@dataclass
class Matchup:
    id: int
    week: int
    home: Side
    away: Side | None        # None = bye
    winner: str | None       # HOME | AWAY | TIE | UNDECIDED
    playoff: bool
    mine: bool


@dataclass
class RosterPlayer:
    name: str
    position: str
    slot: str
    starter: bool
    nfl_team: str
    nfl_team_id: int
    injury: str | None       # ACTIVE, QUESTIONABLE, OUT, ...
    points: float | None     # this week
    projected: float | None  # this week


@dataclass
class League:
    name: str
    season: int
    week: int
    final_week: int | None
    my_team_id: int | None
    teams: list[FantasyTeam]
    matchups: list[Matchup]
    my_roster: list[RosterPlayer] = field(default_factory=list)


def _week_stat(player: dict, week: int, source: int) -> float | None:
    """statSourceId 0 = actual, 1 = projected; statSplitTypeId 1 = single week."""
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == source and s.get("statSplitTypeId") == 1:
            v = s.get("appliedTotal")
            return round(float(v), 2) if v is not None else None
    return None


def _roster(team: dict, week: int) -> list[RosterPlayer]:
    out = []
    for e in dig(team, "roster", "entries", default=[]):
        p = dig(e, "playerPoolEntry", "player", default={})
        slot = e.get("lineupSlotId")
        pro = p.get("proTeamId", 0)
        out.append(RosterPlayer(
            name=p.get("fullName", "?"),
            position=POSITIONS.get(p.get("defaultPositionId"), "?"),
            slot=SLOTS.get(slot, str(slot)),
            starter=slot not in BENCH_SLOTS,
            nfl_team=NFL_TEAMS.get(pro, str(pro)),
            nfl_team_id=pro,
            injury=e.get("injuryStatus") or p.get("injuryStatus"),
            points=_week_stat(p, week, 0),
            projected=_week_stat(p, week, 1),
        ))
    order = list(SLOTS.values())
    out.sort(key=lambda r: (not r.starter, order.index(r.slot) if r.slot in order else 99))
    return out


def _side(s: dict | None, live_points: dict[int, float]) -> Side | None:
    if not s:
        return None
    tid = s.get("teamId")
    pts = s.get("totalPointsLive")
    if pts is None:
        pts = s.get("totalPoints") or 0.0
        # Before ESPN finalizes a week, totalPoints can lag; prefer the live sum of starters if larger.
        pts = max(pts, live_points.get(tid, 0.0))
    proj = s.get("totalProjectedPointsLive")
    if proj is None:
        proj = s.get("totalProjectedPoints")
    return Side(team_id=tid, points=round(float(pts), 2), projected=round(float(proj), 2) if proj is not None else None)


def parse_league(d: dict, *, swid: str | None, my_team_override: Any = "auto") -> League:
    week = d.get("scoringPeriodId") or dig(d, "status", "currentMatchupPeriod") or 1
    matchup_period = dig(d, "status", "currentMatchupPeriod") or week

    my_id = None
    if my_team_override not in (None, "", "auto"):
        my_id = int(my_team_override)
    elif swid:
        for t in d.get("teams", []):
            if swid.upper() in [o.upper() for o in t.get("owners", [])]:
                my_id = t["id"]

    raw_teams = d.get("teams", [])
    live_points: dict[int, float] = {}
    for t in raw_teams:
        if dig(t, "roster", "entries"):
            live_points[t["id"]] = round(sum(p.points or 0 for p in _roster(t, week) if p.starter), 2)

    teams = []
    for t in raw_teams:
        rec = dig(t, "record", "overall", default={})
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        teams.append(FantasyTeam(
            id=t["id"], name=name.strip(), abbrev=t.get("abbrev"), logo=t.get("logo"),
            wins=rec.get("wins", 0), losses=rec.get("losses", 0), ties=rec.get("ties", 0),
            points_for=round(rec.get("pointsFor", 0.0), 2), points_against=round(rec.get("pointsAgainst", 0.0), 2),
            seed=t.get("playoffSeed") or None, standing=0, mine=t["id"] == my_id,
        ))
    if all(t.seed for t in teams):
        teams.sort(key=lambda t: t.seed)
    else:
        teams.sort(key=lambda t: (-(t.wins + 0.5 * t.ties), -t.points_for))
    for i, t in enumerate(teams, 1):
        t.standing = i

    matchups = []
    for m in d.get("schedule", []):
        if m.get("matchupPeriodId") != matchup_period:
            continue
        home, away = _side(m.get("home"), live_points), _side(m.get("away"), live_points)
        if not home:
            continue
        matchups.append(Matchup(
            id=m.get("id"), week=matchup_period, home=home, away=away, winner=m.get("winner"),
            playoff=(m.get("playoffTierType") or "NONE") != "NONE",
            mine=my_id in (home.team_id, away.team_id if away else None),
        ))
    matchups.sort(key=lambda m: not m.mine)

    my_roster = []
    if my_id is not None:
        mine = next((t for t in raw_teams if t["id"] == my_id), None)
        if mine:
            my_roster = _roster(mine, week)

    return League(
        name=dig(d, "settings", "name", default="Fantasy League"), season=d.get("seasonId"),
        week=matchup_period, final_week=dig(d, "status", "finalScoringPeriod"),
        my_team_id=my_id, teams=teams, matchups=matchups, my_roster=my_roster,
    )


def in_nfl_window(now: datetime) -> bool:
    """Rough NFL game windows in US Central time: Thu/Mon night, all day Sunday (and late-season Saturdays)."""
    wd, h = now.weekday(), now.hour  # Mon=0 ... Sun=6
    if wd == 6:
        return 8 <= h <= 23
    if wd in (0, 3):
        return 19 <= h <= 23
    if wd == 5 and now.month in (12, 1):
        return 11 <= h <= 23
    return False


class EspnFantasy(Source):
    name = "fantasy"
    label = "ESPN Fantasy"
    stale_after = 3 * 3600

    def __init__(self, ctx):
        super().__init__(ctx)
        self.cfg = ctx.section("fantasy")

    def enabled(self):
        if not self.cfg.get("enabled", True):
            return False, "disabled in config.toml"
        if not self.ctx.secret("ESPN_LEAGUE_ID"):
            return False, "ESPN_LEAGUE_ID missing from secrets.env"
        return True, ""

    async def fetch(self, client: httpx.AsyncClient) -> League:
        url = URL.format(season=self.cfg.get("season", self.ctx.now().year), league=self.ctx.secret("ESPN_LEAGUE_ID"))
        headers = {}
        s2, swid = self.ctx.secret("ESPN_S2"), self.ctx.secret("ESPN_SWID")
        if s2 and swid:
            headers["Cookie"] = f"espn_s2={s2}; SWID={swid}"
        params = [("view", v) for v in ("mTeam", "mRoster", "mMatchupScore", "mSettings")]
        d = await get_json(client, url, params=params, headers=headers, auth_hint=AUTH_HINT)
        if not isinstance(d, dict) or "teams" not in d:
            raise SourceError("unexpected fantasy response (no teams)")
        if not d["teams"] or "name" not in d["teams"][0] and "location" not in d["teams"][0]:
            # ESPN returns a stripped league (no names) when not authorized for a private league
            raise SourceAuthError(AUTH_HINT)
        return parse_league(d, swid=swid, my_team_override=self.cfg.get("my_team_id", "auto"))

    def next_interval(self, data) -> float:
        return 60 if in_nfl_window(self.ctx.now()) else 1800

    def summarize(self, lg: dict) -> list[str]:
        names = {t["id"]: t["name"] for t in lg["teams"]}
        lines = [f"{lg['name']} — week {lg['week']}" + (f" of {lg['final_week']}" if lg.get("final_week") else "")]
        lines.append(" Standings:")
        for t in lg["teams"]:
            rec = f"{t['wins']}-{t['losses']}" + (f"-{t['ties']}" if t["ties"] else "")
            lines.append(f"  {'*' if t['mine'] else ' '}{t['standing']:>2}. {t['name']:<30} {rec:<6} PF {t['points_for']:.1f}")
        lines.append(" Matchups:")
        for m in lg["matchups"]:
            a, h = m.get("away"), m["home"]
            if a:
                proj = ""
                if a.get("projected") is not None and h.get("projected") is not None:
                    proj = f"   (proj {a['projected']:.1f}-{h['projected']:.1f})"
                lines.append(f"  {'*' if m['mine'] else ' '} {names.get(a['team_id'])} {a['points']:.1f} @ "
                             f"{names.get(h['team_id'])} {h['points']:.1f}{proj}")
            else:
                lines.append(f"    {names.get(h['team_id'])} — bye")
        if lg.get("my_roster"):
            lines.append(" My lineup:")
            for p in lg["my_roster"]:
                if not p["starter"]:
                    continue
                inj = f" [{p['injury']}]" if p.get("injury") not in (None, "ACTIVE", "NORMAL") else ""
                pts = f"{p['points']:.1f}" if p.get("points") is not None else "-"
                proj = f"{p['projected']:.1f}" if p.get("projected") is not None else "-"
                lines.append(f"   {p['slot']:<5} {p['name']:<24} {p['nfl_team']:<4} {pts:>5} (proj {proj}){inj}")
        return lines
