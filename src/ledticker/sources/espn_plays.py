"""Big-play listener: watches the live play-by-play of every NFL game one of my fantasy players (or one of my NFL
teams, [scores.nfl] teams) is in, and reports each play worth celebrating: one of my fantasy players doing something
big (a long gain, a touchdown, a chunk of points), or my NFL team scoring at all (shown with that team's theme).

Rivals get the opposite: a big play by someone on a rival owner's fantasy team ([fantasy] rivals), or a score by a
rival NFL team ([scores.nfl] rivals, e.g. the Cowboys), is reported with mood "boo" (the renderer plays the stinker).

ESPN's core plays feed tags every play with its participants by athlete id (passer, receiver, rusher, kicker,
scorer, ...), and fantasy player ids are the same ESPN athlete ids, so matching is exact. Only the newest page of
each game's feed is fetched on each poll (a whole game's feed is ~1 MB).

The source reports every candidate with a low floor; the renderer applies the thresholds from the dashboard's
Settings (yards / points / touchdowns), so changing them doesn't need a refetch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .base import Source, SourceError, dig, get_json
from .espn_fantasy import EspnFantasy, headshot_url, in_nfl_window

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PLAYS = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{eid}/competitions/{eid}/plays"
PAGE = 25
ATHLETE = re.compile(r"/athletes/(-?\d+)")
TEAM = re.compile(r"/teams/(\d+)")

# ESPN fantasy scoring stat ids -> default points (a league's own settings override these)
DEFAULT_SCORING = {"3": 0.04, "4": 4.0, "24": 0.1, "25": 6.0, "42": 0.1, "43": 6.0, "53": 1.0,
                   "80": 3.0, "77": 4.0, "198": 5.0, "201": 6.0}
DST_TD_TYPES = ("Interception Return Touchdown", "Fumble Return Touchdown", "Kickoff Return Touchdown",
                "Punt Return Touchdown", "Blocked Punt Touchdown", "Blocked Field Goal Touchdown",
                "Missed Field Goal Return Touchdown", "Defensive Touchdown")
SKIP_TYPES = ("Penalty", "Timeout", "End Period", "End of Half", "End of Game", "Two-minute warning",
              "Official Timeout", "Coin Toss")

FLOOR_YARDS = 15     # candidates reported to the renderer (its thresholds are at or above these)
FLOOR_POINTS = 3.0
KEEP = timedelta(hours=6)


@dataclass
class PlayStar:
    player_id: int
    name: str
    position: str
    slot: str
    role: str            # "receiver" | "rusher" | "passer" | "kicker" | "dst"
    points: float
    starter: bool
    headshot: str | None


@dataclass
class BigPlay:
    id: str
    game_id: str
    game: str            # "HOU @ LAC"
    when: str            # ISO UTC (ESPN's wall clock for the play, else when it was seen)
    kind: str            # "catch" | "run" | "pass" | "kick" | "defense"
    headline: str        # "63 YD TD CATCH"
    yards: int
    touchdown: bool
    points: float        # fantasy points for my players on the play (sum)
    text: str            # ESPN's play description
    team: str            # NFL team abbreviation of the play's team (for D/ST: the defense)
    team_color: str | None
    team_alt: str | None
    period: int
    clock: str
    score: str           # "HOU 21  LAC 10" after the play
    players: list[PlayStar] = field(default_factory=list)   # my fantasy players on the play (may be empty)
    team_score: str | None = None   # my NFL team scored on this play: its abbreviation ("HOU")
    team_points: int = 0            # how many it scored (7 = TD + PAT)
    mood: str = "cheer"             # "cheer" (mine) | "boo" (a rival's player, or a rival NFL team scoring)
    rival_team: str | None = None   # boo plays by a rival's fantasy player: that fantasy team's name
    rival_owner: str | None = None  # ... and its owner


@dataclass
class PlaysFeed:
    plays: list[BigPlay] = field(default_factory=list)   # newest first
    watching: list[str] = field(default_factory=list)    # live games being followed, "HOU @ LAC"
    note: str = ""


def scoring_table(league: dict | None) -> dict[str, float]:
    return {**DEFAULT_SCORING, **{str(k): float(v) for k, v in ((league or {}).get("scoring") or {}).items()}}


def fg_points(yards: int, sc: dict[str, float]) -> float:
    if yards >= 60:
        return sc.get("201", sc.get("198", 5.0))
    if yards >= 50:
        return sc.get("198", 5.0)
    if yards >= 40:
        return sc.get("77", 4.0)
    return sc.get("80", 3.0)


def _game_info(event: dict) -> dict:
    comp = event["competitions"][0]
    teams = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        teams[str(t.get("id"))] = {"abbr": t.get("abbreviation", ""), "color": t.get("color"),
                                   "alt": t.get("alternateColor"), "side": c.get("homeAway"),
                                   "score": int(c.get("score") or 0) if str(c.get("score") or "0").isdigit() else 0}
    return {"id": str(event["id"]), "label": event.get("shortName", ""), "teams": teams,
            "state": dig(comp, "status", "type", "state", default="pre")}


def _score(play: dict, game: dict) -> str:
    by_side = {v["side"]: v["abbr"] for v in game["teams"].values()}
    a, h = play.get("awayScore"), play.get("homeScore")
    if a is None or h is None:
        return ""
    return f"{by_side.get('away', 'AWAY')} {a}  {by_side.get('home', 'HOME')} {h}"


def parse_play(play: dict, game: dict, roster: dict[int, dict], sc: dict[str, float],
               seen_at: str | None = None) -> BigPlay | None:
    """A BigPlay if one of my players did something at or above the floor on this play, else None."""
    typ = dig(play, "type", "text", default="") or ""
    txt = play.get("text") or ""
    if typ in SKIP_TYPES or "NO PLAY" in txt.upper() or "Two-Point" in typ or "TWO-POINT CONVERSION" in txt[:40].upper():
        return None
    yards = int(play.get("statYardage") or 0)
    td = ("Touchdown" in typ or "TOUCHDOWN" in txt.upper()) and "NULLIFIED" not in txt.upper()
    team_id = (TEAM.search(dig(play, "team", "$ref", default="") or "") or [None, None])[1]
    roles: dict[int, set[str]] = {}
    for p in play.get("participants") or []:
        m = ATHLETE.search(dig(p, "athlete", "$ref", default="") or "")
        if m:
            roles.setdefault(int(m.group(1)), set()).add(p.get("type") or "")
    turnover = "Interception" in typ or "Fumble Recovery (Opponent)" in typ or "INTERCEPTED" in txt.upper()
    stars: list[PlayStar] = []

    def star(pid, role, pts):
        r = roster[pid]
        stars.append(PlayStar(player_id=pid, name=r["name"], position=r.get("position", ""), slot=r.get("slot", ""),
                              role=role, points=round(pts, 2), starter=bool(r.get("starter")),
                              headshot=headshot_url(pid, r.get("nfl_team", ""))))

    for pid, rs in roles.items():
        if pid not in roster or "fumbler" in rs:
            continue
        if "receiver" in rs and not turnover and typ != "Pass Incompletion":
            star(pid, "receiver", yards * sc["42"] + sc["53"] + (sc["43"] if td and "scorer" in rs else 0))
        elif "rusher" in rs and not turnover:
            star(pid, "rusher", yards * sc["24"] + (sc["25"] if td and "scorer" in rs else 0))
        elif "passer" in rs and not turnover and ("Pass Reception" in typ or "Passing Touchdown" in typ):
            star(pid, "passer", yards * sc["3"] + (sc["4"] if td else 0))
        elif "kicker" in rs and typ == "Field Goal Good":
            star(pid, "kicker", fg_points(yards, sc))
    # my D/ST scoring a defensive / return touchdown
    dst = {-16000 - int(r["nfl_team_id"]): r for r in roster.values() if r.get("position") == "D/ST" and r.get("nfl_team_id")}
    if team_id and typ in DST_TD_TYPES:
        pid = -16000 - int(team_id)
        if pid in dst:
            extra = 2.0 if ("Interception" in typ or "Fumble" in typ) else 0.0
            star(pid, "dst", 6.0 + extra)
    if not stars:
        return None
    pts = round(sum(s.points for s in stars), 2)
    main = max(stars, key=lambda s: ({"receiver": 3, "rusher": 3, "dst": 3, "kicker": 2, "passer": 1}[s.role], s.points))
    big_yards = main.role in ("receiver", "rusher") and yards >= FLOOR_YARDS
    if not (td or big_yards or pts >= FLOOR_POINTS):
        return None
    kind = {"receiver": "catch", "rusher": "run", "passer": "pass", "kicker": "kick", "dst": "defense"}[main.role]
    if kind == "pass" and any(s.role == "receiver" for s in stars):
        kind = "catch"
    headline = {
        "catch": f"{yards} YD {'TD ' if td else ''}CATCH",
        "run": f"{yards} YD {'TD ' if td else ''}RUN",
        "pass": f"{yards} YD {'TD ' if td else ''}PASS",
        "kick": f"{yards} YD FIELD GOAL",
        "defense": typ.upper().replace(" TOUCHDOWN", " TD").replace("INTERCEPTION RETURN", "PICK-SIX"),
    }[kind]
    if headline.startswith("PICK-SIX"):
        headline = "PICK-SIX"
    t = game["teams"].get(team_id or "", {})
    stars.sort(key=lambda s: s is not main)
    return BigPlay(
        id=str(play.get("id")), game_id=game["id"], game=game["label"],
        when=_iso(play.get("wallclock")) or seen_at or datetime.now(timezone.utc).isoformat(),
        kind=kind, headline=headline, yards=yards, touchdown=td, points=pts, text=" ".join(txt.split()),
        team=t.get("abbr") or roster[main.player_id].get("nfl_team", ""), team_color=t.get("color"),
        team_alt=t.get("alt"), period=int(dig(play, "period", "number", default=0) or 0),
        clock=dig(play, "clock", "displayValue", default="") or "", score=_score(play, game), players=stars)


def team_score_play(play: dict, game: dict, team_id: str, gained: int, seen_at: str | None = None) -> BigPlay:
    """My NFL team scored `gained` points on this play (worked out from the score change, so defensive scores,
    safeties and the extra point all count)."""
    typ = dig(play, "type", "text", default="") or ""
    txt = " ".join((play.get("text") or "").split())
    yards = int(play.get("statYardage") or 0)
    if gained >= 6:
        kind = "catch" if "Pass" in typ else "run" if "Rush" in typ else "defense"
        headline = "TOUCHDOWN"
    elif "Field Goal" in typ or gained == 3:
        kind, headline = "kick", (f"{yards} YD FIELD GOAL" if yards else "FIELD GOAL")
    elif "Safety" in typ or gained == 2:
        kind, headline = "defense", "SAFETY"
    else:
        kind, headline = "kick", "SCORE"
    t = game["teams"].get(team_id, {})
    return BigPlay(
        id=str(play.get("id")), game_id=game["id"], game=game["label"],
        when=_iso(play.get("wallclock")) or seen_at or datetime.now(timezone.utc).isoformat(),
        kind=kind, headline=headline, yards=yards, touchdown=gained >= 6, points=0.0, text=txt,
        team=t.get("abbr", ""), team_color=t.get("color"), team_alt=t.get("alt"),
        period=int(dig(play, "period", "number", default=0) or 0),
        clock=dig(play, "clock", "displayValue", default="") or "", score=_score(play, game),
        team_score=t.get("abbr", ""), team_points=gained)


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


class EspnBigPlays(Source):
    """Polls only while a game with one of my fantasy players is live (fast), otherwise checks back slowly."""
    name = "big_plays"
    label = "NFL big plays (my fantasy players)"
    stale_after = 1800

    def __init__(self, ctx):
        super().__init__(ctx)
        self.store = None                         # set by build_sources: my roster + scoring come from "fantasy"
        self.poll_seconds: float | None = None    # from the dashboard's Settings
        self._cursor: dict[str, int] = {}         # game id -> plays already looked at
        self._scores: dict[str, dict[str, int]] = {}   # game id -> {"away": n, "home": n} as of the last play seen
        self._seen: dict[str, set[str]] = {}      # game id -> play ids already looked at
        self._found: dict[str, BigPlay] = {}      # play id -> big play (kept a few hours)
        self._live = False

    def enabled(self):
        return EspnFantasy(self.ctx).enabled()

    def _roster(self) -> tuple[dict[int, dict], dict | None]:
        lg = self.store.get("fantasy") if self.store else None
        return {int(p["player_id"]): p for p in (lg or {}).get("my_roster") or [] if p.get("player_id")}, lg

    @staticmethod
    def rivals(lg: dict | None) -> dict[int, dict]:
        """Rival owners' players, each tagged with its fantasy team name and owner."""
        teams = {t["id"]: t for t in (lg or {}).get("teams") or []}
        out = {}
        for p in (lg or {}).get("rival_roster") or []:
            if p.get("player_id"):
                t = teams.get(p.get("fantasy_team_id"), {})
                out[int(p["player_id"])] = {**p, "fantasy_team": t.get("name"), "owner": t.get("owner")}
        return out

    async def _new_plays(self, client: httpx.AsyncClient, gid: str) -> list[dict]:
        """Plays added to a game's feed since the last poll. The first look at a game only sets the cursor:
        plays from before the display started watching aren't celebrated."""
        url = PLAYS.format(eid=gid)
        if gid not in self._cursor:
            head = await get_json(client, url, params={"limit": "1"})
            self._cursor[gid] = int(head.get("count") or 0)
            return []
        start = max(0, self._cursor[gid] - 3)     # a little overlap: ESPN sometimes inserts a play late
        page = start // PAGE + 1
        out: list[dict] = []
        while True:
            d = await get_json(client, url, params={"limit": str(PAGE), "page": str(page)})
            items = d.get("items") or []
            base = (page - 1) * PAGE
            out += [p for i, p in enumerate(items) if base + i >= start]
            count = int(d.get("count") or 0)
            if page >= int(d.get("pageCount") or 1) or not items or page - (start // PAGE + 1) >= 6:
                break
            page += 1
        self._cursor[gid] = max(self._cursor[gid], count)
        return out

    async def fetch(self, client: httpx.AsyncClient) -> PlaysFeed:
        roster, lg = self._roster()
        rivals = self.rivals(lg)
        nfl = self.ctx.section("scores", "nfl")
        mine = {str(t) for t in nfl.get("teams", [])}
        foes = {str(t) for t in nfl.get("rivals", [])} - mine
        if not roster and not mine and not foes:
            return PlaysFeed(plays=self._recent(), note="waiting for my fantasy roster")
        sc = scoring_table(lg)
        teams = ({str(p["nfl_team_id"]) for p in (*roster.values(), *rivals.values()) if p.get("nfl_team_id")}
                 | mine | foes)
        sb = await get_json(client, SCOREBOARD)
        games = [_game_info(e) for e in sb.get("events") or []]
        live = [g for g in games if g["state"] == "in" and teams & set(g["teams"])]
        self._live = bool(live)
        now = datetime.now(timezone.utc).isoformat()
        for g in live:
            gid = g["id"]
            first = gid not in self._cursor
            try:
                plays = await self._new_plays(client, gid)
            except SourceError:
                continue            # one game's feed hiccuping shouldn't stop the others
            if first:               # start counting from the score right now
                self._scores[gid] = {v["side"]: v["score"] for v in g["teams"].values()}
            for bp in self.scan(plays, g, roster, sc, mine, now, rivals=rivals, foes=foes):
                self._found[bp.id] = bp
        for g in games:             # forget finished games
            if g["state"] == "post":
                for d in (self._cursor, self._scores, self._seen):
                    d.pop(g["id"], None)
        return PlaysFeed(plays=self._recent(), watching=[g["label"] for g in live],
                         note="" if live else "no live games with my players")

    def scan(self, plays: list[dict], g: dict, roster: dict[int, dict], sc: dict[str, float], mine: set[str],
             now: str | None = None, rivals: dict[int, dict] | None = None,
             foes: set[str] | None = None) -> list[BigPlay]:
        """New plays in feed order: my fantasy players' big plays and my team's scores (from the score change);
        if nothing of mine happened on a play, rivals' big plays and rival NFL teams' scores (mood "boo")."""
        gid, out = g["id"], []
        seen, last = self._seen.setdefault(gid, set()), self._scores.setdefault(gid, {})
        my_sides = {v["side"]: tid for tid, v in g["teams"].items() if tid in mine}
        foe_sides = {v["side"]: tid for tid, v in g["teams"].items() if tid in (foes or set())}
        for p in plays:
            pid = str(p.get("id"))
            if pid in seen:
                continue
            seen.add(pid)
            bp = parse_play(p, g, roster, sc, seen_at=now) if roster else None
            boo = None
            if bp is None and rivals:
                boo = parse_play(p, g, rivals, sc, seen_at=now)
                if boo:
                    star = rivals[boo.players[0].player_id]
                    boo.mood, boo.rival_team, boo.rival_owner = "boo", star.get("fantasy_team"), star.get("owner")
            # only scoring plays move the tally: ESPN files timeouts carrying the new score ahead of the play itself
            for side, key in (("away", "awayScore"), ("home", "homeScore")):
                v = p.get(key)
                if v is None or not p.get("scoringPlay"):
                    continue
                gained = int(v) - last.get(side, int(v))
                last[side] = max(last.get(side, 0), int(v))
                if gained > 0 and side in my_sides:
                    ts = team_score_play(p, g, my_sides[side], gained, seen_at=now)
                    if bp:
                        bp.team_score, bp.team_points = ts.team_score, ts.team_points
                    else:
                        bp = ts
                elif gained > 0 and side in foe_sides:
                    ts = team_score_play(p, g, foe_sides[side], gained, seen_at=now)
                    if boo:
                        boo.team_score, boo.team_points = ts.team_score, ts.team_points
                    elif not bp:
                        ts.mood = "boo"
                        boo = ts
            if bp:
                out.append(bp)
            elif boo:
                out.append(boo)
        return out

    def _recent(self) -> list[BigPlay]:
        cut = (datetime.now(timezone.utc) - KEEP).isoformat()
        self._found = {k: v for k, v in self._found.items() if v.when >= cut}
        return sorted(self._found.values(), key=lambda p: p.when, reverse=True)

    def next_interval(self, data) -> float:
        if self._live:
            return max(8.0, float(self.poll_seconds or 15))
        return 120 if in_nfl_window(self.ctx.now()) else 600

    def summarize(self, data: dict) -> list[str]:
        lines = [("watching: " + ", ".join(data.get("watching") or [])) if data.get("watching") else data.get("note", "")]
        for p in (data.get("plays") or [])[:8]:
            who = ", ".join(s["name"] for s in p["players"])
            lines.append(f"  {p['when'][11:16]} {'BOO' if p.get('mood') == 'boo' else '   '} {p['headline']:<18} {who:<28} +{p['points']:.1f}  {p['game']}")
        return lines
