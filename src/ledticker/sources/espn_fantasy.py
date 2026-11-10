"""ESPN Fantasy Football league (unofficial API). Private leagues need espn_s2 + SWID cookies."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import Source, SourceAuthError, SourceError, dig, get_json

log = logging.getLogger("ledticker.fantasy")

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
    streak: str | None = None  # "W3" / "L1"
    owner: str | None = None   # "First Last" of the (first) owner


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
    player_id: int = 0       # == ESPN athlete id (used to match props / headshots)
    fantasy_team_id: int | None = None


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
    playoff_teams: int | None = None
    trades: list["Trade"] = field(default_factory=list)
    potw: "PlayerOfWeek | None" = None                    # last completed week
    week_leaders: list["TopPlayer"] = field(default_factory=list)  # current week, live
    scoring: dict[str, float] = field(default_factory=dict)        # ESPN stat id -> points (league settings)
    rival_roster: list[RosterPlayer] = field(default_factory=list) # rosters of the rival owners ([fantasy] rivals)


@dataclass
class TradeSide:
    team_id: int
    team_name: str
    receives: list[str]


@dataclass
class Trade:
    id: str
    date: str                # ISO UTC
    status: str              # "processed" | "pending review"
    week: int | None
    sides: list[TradeSide]
    process_date: str | None = None   # pending trades: when the review period ends, if ESPN says


@dataclass
class TopPlayer:
    player_id: int
    name: str
    position: str
    nfl_team: str
    points: float
    fantasy_team_id: int | None
    starter: bool
    headshot: str | None


@dataclass
class PlayerOfWeek:
    week: int
    players: list[TopPlayer]         # top 3 rostered performers that week
    top_team_id: int | None
    top_team_points: float | None


def headshot_url(pid: int, nfl_team: str) -> str:
    if pid < 0:  # D/ST "players" have negative ids: use the team logo
        return f"https://a.espncdn.com/i/teamlogos/nfl/500/{nfl_team.lower()}.png"
    return f"https://a.espncdn.com/i/headshots/nfl/players/full/{pid}.png"


def _players(raw_teams: list[dict], week: int) -> list[TopPlayer]:
    out = []
    for t in raw_teams:
        for e in dig(t, "roster", "entries", default=[]):
            p = dig(e, "playerPoolEntry", "player", default={})
            pts = _week_stat(p, week, 0)
            if pts is None:
                continue
            pro = NFL_TEAMS.get(p.get("proTeamId", 0), "FA")
            pid = p.get("id", e.get("playerId", 0)) or 0
            out.append(TopPlayer(player_id=pid, name=p.get("fullName", "?"),
                                 position=POSITIONS.get(p.get("defaultPositionId"), "?"), nfl_team=pro,
                                 points=pts, fantasy_team_id=t["id"],
                                 starter=e.get("lineupSlotId") not in BENCH_SLOTS, headshot=headshot_url(pid, pro)))
    out.sort(key=lambda x: -x.points)
    return out


ATHLETE_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes"
TRADE_MSG = 244  # activity-feed message type for "player traded"; targetId = player, from/to = team ids

TRADE_FEED_FILTER = {"topics": {
    "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]}, "limit": 25, "limitPerMessageSet": {"value": 25},
    "offset": 0, "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
    "sortFor": {"sortPriority": 2, "sortAsc": False}, "filterIncludeMessageTypeIds": {"value": [TRADE_MSG]},
}}


def trade_player_ids(feed: dict | None) -> list[int]:
    """Traded player ids, newest trade first (so a brand-new trade's names are looked up first)."""
    out: dict[int, None] = {}
    for tp in (feed or {}).get("topics") or []:
        for m in tp.get("messages") or []:
            if m.get("messageTypeId") == TRADE_MSG and m.get("targetId") is not None:
                out.setdefault(m["targetId"], None)
    return list(out)


def parse_trades(feed: dict | None, names: dict, team_names: dict[int, str]) -> list[Trade]:
    """Processed trades from the league activity feed (the transaction log hides other teams' proposals)."""
    out = []
    for tp in (feed or {}).get("topics") or []:
        gets: dict[int, list[str]] = {}
        for m in tp.get("messages") or []:
            if m.get("messageTypeId") != TRADE_MSG or m.get("to") is None:
                continue
            pid = m.get("targetId")
            gets.setdefault(m["to"], []).append(names.get(pid, f"Player {pid}"))
        if not gets:
            continue
        ms = tp.get("date") or 0
        out.append(Trade(
            id=str(tp.get("id")), week=None, status="processed",
            date=datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else "",
            sides=[TradeSide(k, team_names.get(k, f"Team {k}"), v) for k, v in gets.items()],
        ))
    out.sort(key=lambda x: x.date, reverse=True)
    return out


def parse_potw(prev: dict | None, schedule: list[dict], week: int) -> PlayerOfWeek | None:
    if not prev or week < 1:
        return None
    players = _players(prev.get("teams") or [], week)[:3]
    best_id, best_pts = None, None
    for m in schedule:
        if m.get("matchupPeriodId") != week:
            continue
        for side in (m.get("home"), m.get("away")):
            if side and (best_pts is None or (side.get("totalPoints") or 0) > best_pts):
                best_id, best_pts = side.get("teamId"), round(side.get("totalPoints") or 0, 2)
    if not players and best_id is None:
        return None
    return PlayerOfWeek(week=week, players=players, top_team_id=best_id, top_team_points=best_pts)


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
            player_id=p.get("id", e.get("playerId", 0)) or 0,
            fantasy_team_id=team.get("id"),
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


def _norm(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def parse_league(d: dict, *, swid: str | None, my_team_override: Any = "auto",
                 trades_feed: dict | None = None, prev: dict | None = None,
                 extra_names: dict[int, str] | None = None, rivals: list[str] | None = None) -> League:
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

    members = {str(m.get("id", "")).upper(): f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
               for m in d.get("members") or []}
    owner = {t["id"]: next((members[o.upper()] for o in t.get("owners") or [] if o.upper() in members), None)
             for t in raw_teams}
    teams = []
    for t in raw_teams:
        rec = dig(t, "record", "overall", default={})
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
        teams.append(FantasyTeam(
            id=t["id"], name=name.strip(), abbrev=t.get("abbrev"), logo=t.get("logo"),
            wins=rec.get("wins", 0), losses=rec.get("losses", 0), ties=rec.get("ties", 0),
            points_for=round(rec.get("pointsFor", 0.0), 2), points_against=round(rec.get("pointsAgainst", 0.0), 2),
            seed=t.get("playoffSeed") or None, standing=0, mine=t["id"] == my_id,
            streak=(f"{rec['streakType'][0]}{rec['streakLength']}"
                    if rec.get("streakType") in ("WIN", "LOSS", "TIE") and rec.get("streakLength") else None),
            owner=owner.get(t["id"]) or None,
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

    want = {_norm(r) for r in rivals or []}
    rival_roster = [p for t in raw_teams if t["id"] != my_id and _norm(owner.get(t["id"]) or "") in want
                    for p in _roster(t, week)]

    names = dict(extra_names or {})
    for t in raw_teams:
        for e in dig(t, "roster", "entries", default=[]):
            p = dig(e, "playerPoolEntry", "player", default={})
            names[p.get("id", e.get("playerId"))] = p.get("fullName", "?")
    return League(
        name=dig(d, "settings", "name", default="Fantasy League"), season=d.get("seasonId"),
        week=matchup_period, final_week=dig(d, "status", "finalScoringPeriod"),
        my_team_id=my_id, teams=teams, matchups=matchups, my_roster=my_roster,
        playoff_teams=dig(d, "settings", "scheduleSettings", "playoffTeamCount"),
        trades=parse_trades(trades_feed, names, {t.id: t.name for t in teams}),
        potw=parse_potw(prev, d.get("schedule", []), matchup_period - 1),
        week_leaders=[p for p in _players(raw_teams, week) if p.points > 0][:3],
        scoring={str(i["statId"]): float(i["points"]) for i in dig(d, "settings", "scoringSettings", "scoringItems", default=[])
                 if i.get("statId") is not None and i.get("points") is not None},
        rival_roster=rival_roster,
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
        self._prev: tuple[int, dict, float] | None = None

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
        if not d["teams"] or ("name" not in d["teams"][0] and "location" not in d["teams"][0]):
            # ESPN returns a stripped league (no names) when not authorized for a private league
            raise SourceAuthError(AUTH_HINT)
        week = d.get("scoringPeriodId") or 1

        # trades come from EspnTrades (its own fast poll of the activity feed)

        # last completed week's points for player of the week (cached: it doesn't change once final)
        prev_week = (dig(d, "status", "currentMatchupPeriod") or week) - 1
        if prev_week >= 1 and (self._prev is None or self._prev[0] != prev_week
                               or time.time() - self._prev[2] > 6 * 3600):
            try:
                doc = await get_json(client, url, headers=headers,
                                     params=[("view", "mRoster"), ("scoringPeriodId", str(prev_week))])
                self._prev = (prev_week, doc, time.time())
            except SourceError:
                pass
        prev = self._prev[1] if self._prev and self._prev[0] == prev_week else None
        return parse_league(d, swid=swid, my_team_override=self.cfg.get("my_team_id", "auto"), prev=prev,
                            rivals=list(self.cfg.get("rivals") or []))

    def next_interval(self, data) -> float:
        return 60 if in_nfl_window(self.ctx.now()) else 300

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


def _iso(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


PENDING_DONE = ("CANCELED", "EXECUTED", "VETOED", "REJECTED", "DECLINED", "FAILED", "EXPIRED")


def pending_trades(d: dict | None, names: dict, team_names: dict[int, str], my_team_id: int | None) -> list[Trade]:
    """Accepted trades waiting out the league's review period (mPendingTransactions).

    ESPN only shows other teams' offers once they're accepted, so any pending trade between two other teams counts.
    For trades involving my team, an offer still waiting on an answer is skipped until it's accepted."""
    out, seen = [], set()
    for t in (d or {}).get("pendingTransactions") or []:
        typ, st = str(t.get("type") or ""), str(t.get("status") or "")
        if "TRADE" not in typ or st.startswith(PENDING_DONE):
            continue
        items = [it for it in t.get("items") or [] if it.get("toTeamId") is not None and it.get("playerId") is not None]
        teams_in = {it["toTeamId"] for it in items} | {it.get("fromTeamId") for it in items} | {t.get("teamId")}
        if typ == "TRADE_PROPOSAL" and st != "ACCEPTED" and my_team_id in teams_in:
            continue   # an offer I made or got that nobody has accepted yet
        key = str(t.get("relatedTransactionId") or t.get("id"))
        if key in seen or not items:
            continue
        seen.add(key)
        gets: dict[int, list[str]] = {}
        for it in items:
            gets.setdefault(it["toTeamId"], []).append(names.get(it["playerId"], f"Player {it['playerId']}"))
        out.append(Trade(id=key, date=_iso(t.get("acceptedDate") or t.get("proposedDate")) or "",
                         status="pending review", week=t.get("scoringPeriodId"),
                         sides=[TradeSide(k, team_names.get(k, f"Team {k}"), v) for k, v in gets.items()],
                         process_date=_iso(t.get("processDate"))))
    return out


def pending_player_ids(d: dict | None) -> list[int]:
    return [it["playerId"] for t in (d or {}).get("pendingTransactions") or [] for it in t.get("items") or []
            if it.get("playerId") is not None]


@dataclass
class TradeFeed:
    trades: list[Trade]
    pending: list[Trade] = field(default_factory=list)   # accepted, still in the review period


class EspnTrades(Source):
    """Trade listener. ESPN has no push/webhook for leagues, so this polls just the league activity feed
    (one small request) every `trade_poll_seconds`; the renderer breaks in when a new trade id appears."""
    name = "fantasy_trades"
    label = "ESPN Fantasy trades"
    stale_after = 1800

    def __init__(self, ctx):
        super().__init__(ctx)
        self.cfg = ctx.section("fantasy")
        self.store = None                  # set by build_sources: team names come from the league data
        self._names: dict[int, str] = {}   # player id -> name, looked up once each
        self.poll_seconds: float | None = None   # set from the dashboard's Settings (overrides config.toml)
        self._shapes: set = set()

    def enabled(self):
        return EspnFantasy(self.ctx).enabled()

    async def _name(self, client: httpx.AsyncClient, pid: int) -> None:
        if pid < 0:  # D/ST: -16000 - proTeamId
            self._names[pid] = f"{NFL_TEAMS.get(-pid - 16000, '?')} D/ST"
            return
        try:
            a = await get_json(client, f"{ATHLETE_URL}/{pid}")
            if a.get("displayName"):
                self._names[pid] = a["displayName"]
        except SourceError:
            pass

    async def fetch(self, client: httpx.AsyncClient) -> TradeFeed:
        url = URL.format(season=self.cfg.get("season", self.ctx.now().year), league=self.ctx.secret("ESPN_LEAGUE_ID"))
        headers = {"x-fantasy-filter": json.dumps(TRADE_FEED_FILTER)}
        s2, swid = self.ctx.secret("ESPN_S2"), self.ctx.secret("ESPN_SWID")
        if s2 and swid:
            headers["Cookie"] = f"espn_s2={s2}; SWID={swid}"
        feed = await get_json(client, url + "/communication/", params={"view": "kona_league_communication"},
                              headers=headers, auth_hint=AUTH_HINT)
        if not isinstance(feed, dict):
            raise SourceError("unexpected trade feed response")
        pend = None
        try:   # accepted trades in the review period (bonus: never fail the processed-trade feed over it)
            pend = await get_json(client, url, params={"view": "mPendingTransactions"},
                                  headers={k: v for k, v in headers.items() if k == "Cookie"})
            for t in pend.get("pendingTransactions") or []:
                shape = (t.get("type"), t.get("status"))
                if shape not in self._shapes:   # so the format can be checked against a real pending trade
                    self._shapes.add(shape)
                    log.info("pending transaction seen: type=%s status=%s keys=%s", *shape, sorted(t))
        except SourceError:
            pend = None
        pend_ids = pending_player_ids(pend)
        missing = [pid for pid in pend_ids + trade_player_ids(feed) if pid not in self._names]
        for pid in list(dict.fromkeys(missing))[:20]:   # the rest get filled in on later polls
            await self._name(client, pid)
        lg = (self.store.get("fantasy") if self.store else None) or {}
        teams = {t["id"]: t["name"] for t in lg.get("teams") or []}
        return TradeFeed(parse_trades(feed, self._names, teams),
                         pending_trades(pend, self._names, teams, lg.get("my_team_id")))

    def next_interval(self, data) -> float:
        return max(10.0, float(self.poll_seconds or self.cfg.get("trade_poll_seconds", 30)))

    def summarize(self, data: dict) -> list[str]:
        trades = data.get("trades") or []
        if not trades:
            return ["no trades yet this season"]
        return [f"{t['date'][:16]}  " + " / ".join(f"{s['team_name']} gets {', '.join(s['receives'])}"
                                                   for s in t["sides"]) for t in trades[:5]]
