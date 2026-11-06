"""ESPN public scoreboard (unofficial, undocumented) for NFL/CFB/NBA/NCAAB/MLB/college baseball.

One Source instance per league so each polls on its own cadence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .base import Source, SourceError, dig, get_json, season_wait, to_int

BASE = "https://site.api.espn.com/apis/site/v2/sports"

LEAGUES: dict[str, dict[str, Any]] = {
    "nfl":   {"path": "football/nfl", "label": "NFL", "sport": "football", "params": {}},
    "cfb":   {"path": "football/college-football", "label": "CFB", "sport": "football",
              "params": {"groups": "80", "limit": "300"}, "sec": "8"},
    "nba":   {"path": "basketball/nba", "label": "NBA", "sport": "basketball", "params": {}},
    "ncaab": {"path": "basketball/mens-college-basketball", "label": "NCAAB", "sport": "basketball",
              "params": {"groups": "50", "limit": "400"}, "sec": "23"},
    "mlb":   {"path": "baseball/mlb", "label": "MLB", "sport": "baseball", "params": {}},
    "cbase": {"path": "baseball/college-baseball", "label": "College Baseball", "sport": "baseball",
              "params": {"limit": "400"}},
}

BIG_KEYWORDS = ("championship", "playoff", "bowl", "final four", "tournament", "world series", "college world series")


@dataclass
class TeamLine:
    id: str
    abbr: str
    name: str
    short_name: str | None
    score: int | None
    record: str | None
    rank: int | None
    conference_id: str | None
    color: str | None
    alt_color: str | None
    logo: str | None
    winner: bool | None


@dataclass
class Odds:
    provider: str | None
    details: str | None          # "HOU -6"
    spread: float | None         # absolute value; favorite named in `favorite`
    over_under: float | None
    favorite: str | None         # team abbr
    ml_home: str | None
    ml_away: str | None
    ml_home_open: str | None
    ml_away_open: str | None


@dataclass
class Game:
    league: str
    id: str
    start_utc: str
    start_local: str             # "Sun Nov 8 3:05 PM"
    state: str                   # pre | in | post
    status: str                  # "Final", "Q3 4:12", "Sun, November 8th at 3:05 PM EST"
    period: int | None
    clock: str | None
    neutral_site: bool
    conference_game: bool
    note: str | None             # bowl / championship / series headline
    broadcast: str | None
    away: TeamLine
    home: TeamLine
    odds: Odds | None
    mine: bool = False
    importance: int = 0
    # football live
    possession: str | None = None
    down_distance: str | None = None
    red_zone: bool = False
    odds_pregame: bool = False   # odds shown are the remembered pre-game line (ESPN drops odds at kickoff)
    # baseball live
    balls: int | None = None
    strikes: int | None = None
    outs: int | None = None
    bases: str | None = None     # "1-3" = runners on first and third


@dataclass
class Scoreboard:
    league: str
    label: str
    season: int | None
    week: int | None
    day: str | None
    byes: list[str]
    offseason: bool
    total_games: int
    games: list[Game] = field(default_factory=list)
    next_games: list[Game] = field(default_factory=list)   # my teams' next game when not playing today


# ---------------------------------------------------------------- parsing

def _team(c: dict) -> TeamLine:
    t = c.get("team", {})
    recs = c.get("records") or []
    overall = next((r.get("summary") for r in recs if r.get("type") == "total"), None)
    rank = dig(c, "curatedRank", "current")
    return TeamLine(
        id=str(t.get("id")), abbr=t.get("abbreviation") or "?", name=t.get("displayName") or "",
        short_name=t.get("shortDisplayName"),
        score=to_int(c.get("score")),
        record=overall or (recs[0].get("summary") if recs else None),
        rank=rank if isinstance(rank, int) and 1 <= rank <= 25 else None,
        conference_id=t.get("conferenceId"),
        color=t.get("color"), alt_color=t.get("alternateColor"), logo=t.get("logo"),
        winner=c.get("winner"),
    )


def _odds(comp: dict, home: TeamLine, away: TeamLine) -> Odds | None:
    o = dig(comp, "odds", 0)
    if not o:
        return None
    if dig(o, "homeTeamOdds", "favorite"):
        fav = home.abbr
    elif dig(o, "awayTeamOdds", "favorite"):
        fav = away.abbr
    else:
        fav = None
    ml = o.get("moneyline") or {}
    spread = o.get("spread")
    return Odds(
        provider=dig(o, "provider", "name"), details=o.get("details"),
        spread=abs(spread) if isinstance(spread, (int, float)) else None,
        over_under=o.get("overUnder"), favorite=fav,
        ml_home=dig(ml, "home", "close", "odds"), ml_away=dig(ml, "away", "close", "odds"),
        ml_home_open=dig(ml, "home", "open", "odds"), ml_away_open=dig(ml, "away", "open", "odds"),
    )


def _fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local:%a %b} {local.day} {local.strftime('%I:%M %p').lstrip('0')}"


def parse_event(league: str, e: dict, tz: ZoneInfo) -> Game:
    comp = e["competitions"][0]
    cs = comp.get("competitors", [])
    home = _team(next(c for c in cs if c.get("homeAway") == "home"))
    away = _team(next(c for c in cs if c.get("homeAway") == "away"))
    st = comp.get("status") or e.get("status") or {}
    state = dig(st, "type", "state", default="pre")
    start = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
    names = [n for b in (comp.get("broadcasts") or []) for n in b.get("names", [])]
    g = Game(
        league=league, id=str(e["id"]), start_utc=start.isoformat(), start_local=_fmt_local(start, tz),
        state=state, status=dig(st, "type", "shortDetail", default=""),
        period=st.get("period"), clock=st.get("displayClock") if state == "in" else None,
        neutral_site=bool(comp.get("neutralSite")), conference_game=bool(comp.get("conferenceCompetition")),
        note=dig(comp, "notes", 0, "headline"), broadcast="/".join(dict.fromkeys(names)) or None,
        away=away, home=home, odds=_odds(comp, home, away),
    )
    sit = comp.get("situation") or {}
    if state == "in" and sit:
        if "downDistanceText" in sit or "possession" in sit:
            pid = str(sit.get("possession")) if sit.get("possession") is not None else None
            g.possession = home.abbr if pid == home.id else away.abbr if pid == away.id else None
            g.down_distance = sit.get("downDistanceText")
            g.red_zone = bool(sit.get("isRedZone"))
        if "outs" in sit:
            g.balls, g.strikes, g.outs = sit.get("balls"), sit.get("strikes"), sit.get("outs")
            g.bases = "-".join(str(i) for i, k in ((1, "onFirst"), (2, "onSecond"), (3, "onThird"))
                               if sit.get(k)) or None
    return g


def importance(g: Game, sport: str, sec_id: str | None) -> int:
    """How 'big' a game is. My teams always rank highest."""
    if g.mine:
        return 1000
    score = 0
    ranks = [t.rank for t in (g.away, g.home) if t.rank]
    if len(ranks) == 2:
        score += 100 + sum(26 - r for r in ranks)
        if max(ranks) <= 10:
            score += 50
    elif len(ranks) == 1:
        score += 30 + (26 - ranks[0])
    if sec_id and g.conference_game and ranks and sec_id in (g.away.conference_id, g.home.conference_id):
        score += 20
    if g.note and any(k in g.note.lower() for k in BIG_KEYWORDS):
        score += 200
    if g.state == "in" and ranks and g.away.score is not None and g.home.score is not None:
        late_period, close_margin = {"football": (4, 8), "basketball": (2, 6), "baseball": (7, 2)}.get(sport, (4, 8))
        if (g.period or 0) >= late_period and abs(g.away.score - g.home.score) <= close_margin:
            score += 60  # upset / nail-biter
    return score


def parse_scoreboard(league: str, d: dict, *, my_teams: set[str], filt: str, max_big: int,
                     tz: ZoneInfo, now: datetime | None = None) -> Scoreboard:
    spec = LEAGUES[league]
    games = [parse_event(league, e, tz) for e in d.get("events", [])]
    for g in games:
        g.mine = bool(my_teams & {g.away.id, g.home.id})
        g.importance = importance(g, spec["sport"], spec.get("sec"))

    now = now or datetime.now(timezone.utc)
    offseason = bool(games) and all(g.state == "post" for g in games) and \
        max(datetime.fromisoformat(g.start_utc) for g in games) < now - timedelta(days=3)

    if filt == "big":
        chosen = [g for g in games if g.mine or g.importance >= 30]
        chosen.sort(key=lambda g: -g.importance)
        chosen = chosen[:max_big]
    elif filt == "mine":
        chosen = [g for g in games if g.mine]
    else:
        chosen = games
    chosen.sort(key=lambda g: (g.start_utc, g.id))

    return Scoreboard(
        league=league, label=spec["label"], season=dig(d, "season", "year"), week=dig(d, "week", "number"),
        day=dig(d, "day", "date"),
        byes=[t.get("abbreviation") for t in dig(d, "week", "teamsOnBye", default=[])],
        offseason=offseason, total_games=len(games), games=chosen,
    )


# ---------------------------------------------------------------- source

class EspnScores(Source):
    stale_after = 900

    def __init__(self, ctx, league: str):
        super().__init__(ctx)
        self.league = league
        self.spec = LEAGUES[league]
        self.cfg = ctx.section("scores", league)
        self.name = f"scores.{league}"
        self.label = f"{self.spec['label']} scores"
        self._season_over_until: datetime | None = None
        self._odds: dict[str, Odds] = {}   # game id -> last pre-game line seen

    def enabled(self):
        return (True, "") if self.cfg.get("enabled", True) else (False, "disabled in config.toml")

    def dormant_until(self, now: datetime) -> datetime | None:
        wait = season_wait(now, self.cfg.get("season_start"), self.cfg.get("season_end"))
        if wait:
            return wait
        # season ended early (e.g. World Series finished before season_end): sleep until next season_start
        if self._season_over_until and now < self._season_over_until:
            return self._season_over_until
        self._season_over_until = None
        return None

    @property
    def my_teams(self) -> set[str]:
        return {str(t) for t in self.cfg.get("teams", [])}

    async def _next_games(self, client: httpx.AsyncClient) -> list[Game]:
        out = []
        for tid in sorted(self.my_teams):
            try:
                d = await get_json(client, f"{BASE}/{self.spec['path']}/teams/{tid}")
            except SourceError:
                continue  # nice-to-have; never fail the scoreboard over it
            for e in dig(d, "team", "nextEvent", default=[])[:1]:
                try:
                    g = parse_event(self.league, e, self.ctx.tz)
                except (KeyError, StopIteration, ValueError):
                    continue
                g.mine = True
                out.append(g)
        return sorted(out, key=lambda g: g.start_utc)

    async def fetch(self, client: httpx.AsyncClient) -> Scoreboard:
        d = await get_json(client, f"{BASE}/{self.spec['path']}/scoreboard", params=self.spec["params"] or None)
        filt = self.cfg.get("filter", "all")
        sb = parse_scoreboard(self.league, d, my_teams=self.my_teams, filt=filt,
                              max_big=int(self.cfg.get("max_big", 8)), tz=self.ctx.tz)
        if filt == "mine" and not any(g.state in ("pre", "in") for g in sb.games):
            sb.next_games = await self._next_games(client)
        self._remember_odds(sb.games + sb.next_games)
        if sb.offseason and self.cfg.get("season_start"):
            now = self.ctx.now()
            # a one-day window on season_start yields the next season_start date
            self._season_over_until = season_wait(now, self.cfg["season_start"], self.cfg["season_start"])
        return sb

    def _remember_odds(self, games: list[Game]) -> None:
        for g in games:
            if g.odds and g.state == "pre":
                self._odds[g.id] = g.odds
            elif not g.odds and g.id in self._odds:
                g.odds, g.odds_pregame = self._odds[g.id], True
        if len(self._odds) > 400:  # keep memory bounded across a season
            for k in list(self._odds)[:200]:
                del self._odds[k]

    def next_interval(self, sb: Scoreboard) -> float:
        if sb.offseason:
            return 6 * 3600
        if not sb.games:
            return 3600  # none of my teams play today
        if any(g.state == "in" for g in sb.games):
            return float(self.cfg.get("live_interval", 20))
        now = datetime.now(timezone.utc)
        soon = [g for g in sb.games if g.state == "pre"
                and datetime.fromisoformat(g.start_utc) - now < timedelta(minutes=15)]
        if soon:
            return 60
        return float(self.cfg.get("idle_interval", 300))

    def summarize(self, sb: dict) -> list[str]:
        head = sb["label"]
        if sb.get("week"):
            head += f" week {sb['week']}"
        head += f": {len(sb['games'])} of {sb['total_games']} games"
        if sb.get("offseason"):
            head += " (off-season)"
        if sb.get("byes"):
            head += f" | byes: {', '.join(sb['byes'])}"
        lines = [head]
        for g in sb["games"]:
            def tm(t):
                return f"#{t['rank']} {t['abbr']}" if t.get("rank") else t["abbr"]
            star = "*" if g["mine"] else " "
            at = "vs" if g["neutral_site"] else "@"
            mu = f"{tm(g['away']):>9} {at:>2} {tm(g['home']):<9}"
            if g["state"] == "pre":
                odds = g.get("odds") or {}
                line = f"{odds.get('details') or '':<10} O/U {odds.get('over_under') or '-'}" if odds else ""
                lines.append(f" {star} {g['start_local']:<19} {mu} {line:<22} {g.get('broadcast') or ''}")
            else:
                extra = ""
                if g.get("down_distance"):
                    extra = f"  [{g['possession']} ball, {g['down_distance']}{' RZ' if g['red_zone'] else ''}]"
                elif g.get("outs") is not None:
                    extra = f"  [{g['balls']}-{g['strikes']}, {g['outs']} out, on: {g['bases'] or 'none'}]"
                lines.append(f" {star} {g['status']:<19} {mu} {g['away']['score']}-{g['home']['score']}{extra}")
            if g.get("note"):
                lines.append(f"      {g['note']}")
        for g in sb.get("next_games") or []:
            at = "vs" if g["neutral_site"] else "@"
            lines.append(f"   next: {g['away']['abbr']} {at} {g['home']['abbr']}  {g['start_local']}"
                         + (f"  {g['broadcast']}" if g.get("broadcast") else ""))
        return lines
