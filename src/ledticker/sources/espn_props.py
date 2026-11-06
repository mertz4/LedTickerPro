"""NFL player props (DraftKings lines via ESPN's core odds API, unofficial) tracked against live box scores.

Follows "the game that matters right now": a live primetime game (TNF/SNF/MNF), otherwise live games ranked by
my teams and my fantasy starters, otherwise the next kickoff window.

ESPN does not publish bet counts or handle, so "most bet" isn't available. As a stand-in, `move` is the line
change since open: big moves usually mean money came in on one side ("hot" props).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .base import Source, SourceError, dig, get_json

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{tid}/roster"
PROPS = ("https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{eid}/competitions/{eid}"
         "/odds/{provider}/propBets")
PROVIDER = "100"  # DraftKings

# prop type name -> (short label, how to read the live stat from the box score)
MARKETS = {
    "Total Passing Yards (incl. overtime)": ("PASS YDS", ("passing", "passingYards")),
    "Total Pass Completions (incl. overtime)": ("CMP", ("passing", "completions")),
    "Total Rushing Yards (incl. overtime)": ("RUSH YDS", ("rushing", "rushingYards")),
    "Total Receiving Yards (incl. overtime)": ("REC YDS", ("receiving", "receivingYards")),
    "Total Receptions (incl. overtime)": ("REC", ("receiving", "receptions")),
    "Total Rushing Plus Receiving Yards (incl. overtime)": ("RUSH+REC", ("rushrec", "yards")),
}
ATHLETE_ID = re.compile(r"/athletes/(\d+)")


@dataclass
class PropGame:
    id: str
    label: str            # "HOU @ LAC"
    state: str            # pre | in | post
    status: str
    start_utc: str
    primetime: bool       # only game in its kickoff window (TNF / SNF / MNF)
    reason: str           # why it's featured


@dataclass
class Prop:
    game_id: str
    athlete_id: int
    name: str
    short_name: str
    team: str
    position: str
    headshot: str | None
    market: str           # "PASS YDS"
    line: float
    open: float | None
    move: float           # line - open
    live: float | None    # current stat in the game (None before kickoff)
    my_fantasy: bool      # on my fantasy roster (starter or bench)
    my_team: bool         # plays for one of my NFL teams
    pinned: bool = False  # added by hand from the dashboard's prop search
    game: str = ""        # "HOU @ LAC"


@dataclass
class PropsBoard:
    games: list[PropGame] = field(default_factory=list)
    props: list[Prop] = field(default_factory=list)
    movers: list[Prop] = field(default_factory=list)   # biggest line moves since open ("where the money went")
    note: str = ""


def movers(props: list[Prop], n: int = 12) -> list[Prop]:
    moved = [p for p in props if p.open is not None and p.move]
    moved.sort(key=lambda p: (-abs(p.move) / max(p.line, 1), -abs(p.move)))
    return moved[:n]


def kickoff_windows(events: list[dict]) -> dict[str, int]:
    """Number of games sharing each kickoff time (1 = standalone/primetime)."""
    counts: dict[str, int] = {}
    for e in events:
        counts[e["date"]] = counts.get(e["date"], 0) + 1
    return counts


def pick_games(events: list[dict], now: datetime, my_team_ids: set[str], fantasy_team_ids: dict[str, int],
               max_games: int = 2) -> list[PropGame]:
    """Choose what's relevant right now. fantasy_team_ids: NFL team id -> number of my fantasy players on it."""
    windows = kickoff_windows(events)

    def info(e):
        comp = e["competitions"][0]
        teams = [c["team"]["id"] for c in comp["competitors"]]
        state = dig(comp, "status", "type", "state", default="pre")
        start = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        return comp, teams, state, start

    live, upcoming, recent = [], [], []
    for e in events:
        comp, teams, state, start = info(e)
        if state == "in":
            live.append(e)
        elif state == "pre":
            upcoming.append(e)
        elif state == "post" and now - start < timedelta(hours=5):
            recent.append(e)

    if live:
        pool, why = live, "LIVE"
    elif recent and not any(info(e)[3] - now < timedelta(hours=1) for e in upcoming):
        pool, why = recent, "FINAL"          # just ended: show how the props finished for a bit
    elif upcoming:
        # the next game day's slate (e.g. all of Sunday), ranked by relevance below
        first = min(info(e)[3] for e in upcoming)
        pool, why = [e for e in upcoming if info(e)[3] - first < timedelta(hours=16)], "NEXT UP"
    else:
        pool, why = [], ""

    def score(e):
        comp, teams, state, start = info(e)
        s = 0
        s += 100 * len(my_team_ids & set(teams))
        s += 10 * sum(fantasy_team_ids.get(t, 0) for t in teams)
        s += 5 if windows.get(e["date"]) == 1 else 0
        return -s, start

    out = []
    for e in sorted(pool, key=score)[:max_games]:
        comp, teams, state, start = info(e)
        prime = windows.get(e["date"]) == 1
        local = start.astimezone().strftime("%a").upper()
        tag = {"THU": "TNF", "MON": "MNF", "SUN": "SNF" if start.astimezone().hour >= 18 else "", "SAT": ""}.get(local, "")
        reason = f"{why}{' ' + tag if prime and tag else ''}"
        out.append(PropGame(id=e["id"], label=e.get("shortName", ""), state=state,
                            status=dig(comp, "status", "type", "shortDetail", default=""),
                            start_utc=start.isoformat(), primetime=prime, reason=reason))
    return out


def parse_box(summary: dict) -> dict[int, dict[str, float]]:
    """athlete id -> {passingYards, completions, rushingYards, receivingYards, receptions, rushrec.yards}."""
    out: dict[int, dict[str, float]] = {}
    for team in dig(summary, "boxscore", "players", default=[]):
        for cat in team.get("statistics") or []:
            keys = cat.get("keys") or []
            for a in cat.get("athletes") or []:
                aid = int(dig(a, "athlete", "id", default=0) or 0)
                row = out.setdefault(aid, {})
                for k, v in zip(keys, a.get("stats") or []):
                    try:
                        if k == "completions/passingAttempts":
                            row["completions"] = float(str(v).split("/")[0])
                        else:
                            row[k] = float(v)
                    except (TypeError, ValueError):
                        continue
    for row in out.values():
        if "rushingYards" in row or "receivingYards" in row:
            row["rushrec.yards"] = row.get("rushingYards", 0) + row.get("receivingYards", 0)
    return out


def live_value(box: dict[int, dict[str, float]], aid: int, how: tuple[str, str]) -> float | None:
    row = box.get(aid)
    if row is None:
        return None
    cat, key = how
    if cat == "rushrec":
        return row.get("rushrec.yards")
    return row.get(key)


def parse_props(game_id: str, items: list[dict], people: dict[int, dict], box: dict | None,
                fantasy_ids: set[int], my_team_abbrs: set[str]) -> list[Prop]:
    out = []
    for it in items:
        name = dig(it, "type", "name", default="")
        if name not in MARKETS:
            continue
        line = dig(it, "current", "target", "value")
        if line is None:
            continue
        m = ATHLETE_ID.search(dig(it, "athlete", "$ref", default=""))
        if not m:
            continue
        aid = int(m.group(1))
        who = people.get(aid, {})
        label, how = MARKETS[name]
        opened = dig(it, "open", "target", "value")
        out.append(Prop(
            game_id=game_id, athlete_id=aid, name=who.get("name", f"#{aid}"), short_name=who.get("short", ""),
            team=who.get("team", ""), position=who.get("pos", ""), headshot=who.get("headshot"),
            market=label, line=float(line), open=float(opened) if opened is not None else None,
            move=round(float(line) - float(opened), 1) if opened is not None else 0.0,
            live=live_value(box, aid, how) if box else None,
            my_fantasy=aid in fantasy_ids, my_team=who.get("team", "") in my_team_abbrs,
        ))
    return out


def dedupe(props: list[Prop]) -> list[Prop]:
    """ESPN lists over and under as separate entries with the same line."""
    seen, out = set(), []
    for p in props:
        k = (p.game_id, p.athlete_id, p.market)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def prop_key(game_id, athlete_id, market) -> str:
    return f"{game_id}:{int(athlete_id)}:{market}"


class PropWatch:
    """Props picked by hand in the dashboard (data/props_watch.json). Each entry keeps the last line seen, so a
    pick keeps tracking live even if the sportsbook pulls the line mid-game. Entries drop off 12 h after kickoff."""
    KEEP_AFTER_KICKOFF = timedelta(hours=12)

    def __init__(self, path: Path | None):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.entries = {prop_key(e["game_id"], e["athlete_id"], e["market"]): e
                                for e in json.loads(path.read_text(encoding="utf-8"))}
            except (OSError, ValueError, KeyError, TypeError):
                self.entries = {}

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self.entries.values()), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, prop: dict, start_utc: str) -> dict:
        e = {k: prop.get(k) for k in ("game_id", "athlete_id", "name", "short_name", "team", "position",
                                      "headshot", "market", "line", "open", "game")}
        e["start_utc"] = start_utc
        self.entries[prop_key(e["game_id"], e["athlete_id"], e["market"])] = e
        self._save()
        return e

    def remove(self, key: str) -> bool:
        gone = self.entries.pop(key, None) is not None
        if gone:
            self._save()
        return gone

    def prune(self, now: datetime) -> None:
        old = [k for k, e in self.entries.items()
               if e.get("start_utc") and now - datetime.fromisoformat(e["start_utc"]) > self.KEEP_AFTER_KICKOFF]
        for k in old:
            del self.entries[k]
        if old:
            self._save()

    def remember_line(self, p: "Prop") -> None:
        e = self.entries.get(prop_key(p.game_id, p.athlete_id, p.market))
        if e is not None:
            e["line"] = p.line

    def game_ids(self) -> set[str]:
        return {e["game_id"] for e in self.entries.values()}

    def as_props(self, keys: set[str], boxes: dict[str, dict | None]) -> list["Prop"]:
        """Props for picks whose line isn't posted right now (kept from the last line seen)."""
        out = []
        for k in sorted(keys):
            e = self.entries[k]
            how = next((h for lbl, h in MARKETS.values() if lbl == e["market"]), None)
            box = boxes.get(e["game_id"])
            out.append(Prop(
                game_id=e["game_id"], athlete_id=int(e["athlete_id"]), name=e.get("name") or "",
                short_name=e.get("short_name") or "", team=e.get("team") or "", position=e.get("position") or "",
                headshot=e.get("headshot"), market=e["market"], line=float(e.get("line") or 0), open=e.get("open"),
                move=0.0, live=live_value(box, int(e["athlete_id"]), how) if box and how else None,
                my_fantasy=False, my_team=False, pinned=True, game=e.get("game") or ""))
        return out


def search_props(catalog: list[Prop], query: str, limit: int = 60) -> list[Prop]:
    """Every word must match the player's name, team, position or market ("stroud", "hou rec", "puka yds")."""
    words = query.lower().split()
    if not words:
        return []
    hits = [p for p in catalog
            if all(w in f"{p.name} {p.short_name} {p.team} {p.market} {p.position}".lower() for w in words)]
    order = {lbl: i for i, (lbl, _) in enumerate(MARKETS.values())}
    hits.sort(key=lambda p: (p.name, order.get(p.market, 99)))
    return hits[:limit]


def rank_props(props: list[Prop]) -> list[Prop]:
    """My hand-picked props first, then my fantasy guys, then my team, then the biggest line movers, then the
    headline markets."""
    market_weight = {"PASS YDS": 3, "RUSH YDS": 2, "REC YDS": 2, "RUSH+REC": 1, "REC": 1, "CMP": 0}

    def key(p: Prop):
        rel_move = abs(p.move) / max(p.line, 1)
        return (not p.pinned, not p.my_fantasy, not p.my_team, -round(rel_move, 3),
                -market_weight.get(p.market, 0), -p.line)
    return sorted(props, key=key)


class EspnProps(Source):
    name = "props"
    label = "NFL player props"
    stale_after = 1800

    def __init__(self, ctx):
        super().__init__(ctx)
        self.cfg = ctx.section("props")
        self.store = None                      # set by the scheduler; used to read my fantasy roster
        self._people: dict[str, tuple[float, dict[int, dict]]] = {}  # NFL team id -> (fetched, athletes)
        self._events: list[dict] = []
        self._catalog: tuple[float, list[Prop]] | None = None
        self.watch = PropWatch(ctx.root / "data" / "props_watch.json")

    def enabled(self):
        return (True, "") if self.cfg.get("enabled", True) else (False, "disabled in config.toml")

    def _fantasy(self) -> tuple[set[str], set[int]]:
        """(NFL team ids with my fantasy starters, all my fantasy player ids)."""
        lg = self.store.get("fantasy") if self.store else None
        if not lg:
            return set(), set()
        roster = lg.get("my_roster") or []
        ids = {p["player_id"] for p in roster if p.get("player_id")}
        teams = {str(p["nfl_team_id"]) for p in roster if p.get("starter") and p.get("nfl_team_id")}
        return teams, ids

    async def _roster(self, client, tid: str) -> dict[int, dict]:
        cached = self._people.get(tid)
        if cached and time.time() - cached[0] < 12 * 3600:
            return cached[1]
        d = await get_json(client, ROSTER.format(tid=tid))
        abbr = dig(d, "team", "abbreviation", default="")
        people = {}
        for grp in d.get("athletes") or []:
            for a in grp.get("items") or []:
                people[int(a["id"])] = {"name": a.get("displayName", ""), "short": a.get("shortName", ""),
                                        "pos": dig(a, "position", "abbreviation", default=""), "team": abbr,
                                        "headshot": dig(a, "headshot", "href")}
        self._people[tid] = (time.time(), people)
        return people

    async def _props_items(self, client, eid: str) -> list[dict]:
        url = PROPS.format(eid=eid, provider=PROVIDER)
        first = await get_json(client, url, params={"limit": "500", "page": "1"})
        items = list(first.get("items") or [])
        pages = int(first.get("pageCount") or 1)
        rest = await asyncio.gather(*(get_json(client, url, params={"limit": "500", "page": str(p)})
                                      for p in range(2, min(pages, 6) + 1)), return_exceptions=True)
        for r in rest:
            if isinstance(r, dict):
                items += r.get("items") or []
        return items

    async def _game(self, client, event: dict, fantasy_ids: set[int], my_teams: set[str],
                    with_box: bool) -> tuple[list[Prop], dict | None]:
        """All posted props for one game (+ its box score when live/final)."""
        comp = event["competitions"][0]
        my_abbrs = {c["team"]["abbreviation"] for c in comp["competitors"] if c["team"]["id"] in my_teams}
        people: dict[int, dict] = {}
        for c in comp["competitors"]:
            try:
                people.update(await self._roster(client, c["team"]["id"]))
            except SourceError:
                pass
        try:
            items = await self._props_items(client, event["id"])
        except SourceError:
            items = []
        box = None
        state = dig(comp, "status", "type", "state", default="pre")
        if with_box and state in ("in", "post"):
            try:
                box = parse_box(await get_json(client, SUMMARY, params={"event": event["id"]}))
            except SourceError:
                box = None
        props = parse_props(event["id"], items, people, box, fantasy_ids, my_abbrs)
        for p in props:
            p.game = event.get("shortName", "")
        return props, box

    async def fetch(self, client: httpx.AsyncClient) -> PropsBoard:
        sb = await get_json(client, SCOREBOARD)
        events = sb.get("events") or []
        self._events = events
        now = datetime.now(timezone.utc)
        self.watch.prune(now)
        my_teams = {str(t) for t in self.ctx.section("scores", "nfl").get("teams", [])}
        fantasy_teams, fantasy_ids = self._fantasy()
        fantasy_count = {t: 1 for t in fantasy_teams}
        games = pick_games(events, now, my_teams, fantasy_count, max_games=int(self.cfg.get("max_games", 2)))
        by_id = {e["id"]: e for e in events}
        picked = [g.id for g in games]
        watched = sorted((self.watch.game_ids() & set(by_id)) - set(picked))
        if not games and not watched:
            return PropsBoard(note="no upcoming NFL games")

        all_props: list[Prop] = []
        boxes: dict[str, dict | None] = {}
        for gid in picked + watched:
            props, boxes[gid] = await self._game(client, by_id[gid], fantasy_ids, my_teams, with_box=True)
            if gid in watched:   # a game only fetched for my picks: keep just those
                props = [p for p in props if prop_key(p.game_id, p.athlete_id, p.market) in self.watch.entries]
            all_props += props

        unique = dedupe(all_props)
        seen = set()
        for p in unique:
            k = prop_key(p.game_id, p.athlete_id, p.market)
            if k in self.watch.entries:
                p.pinned = True
                self.watch.remember_line(p)
                seen.add(k)
        unique += self.watch.as_props({k for k, e in self.watch.entries.items()
                                       if k not in seen and e["game_id"] in boxes}, boxes)
        if not unique:
            return PropsBoard(games=games, note="no prop lines posted yet")
        ranked = rank_props(unique)
        pinned = [p for p in ranked if p.pinned]
        rest = [p for p in ranked if not p.pinned][: max(0, int(self.cfg.get("max_props", 40)) - len(pinned))]
        if not games:   # only my picks' games are on: still label the board
            games = pick_games([by_id[i] for i in watched], now, my_teams, {}, max_games=len(watched))
        return PropsBoard(games=games, props=pinned + rest, movers=movers(unique))

    async def catalog(self, client: httpx.AsyncClient, max_age: float = 900) -> list[Prop]:
        """Every posted prop for this week's games that haven't finished (for the dashboard search). Cached."""
        if self._catalog and time.time() - self._catalog[0] < max_age:
            return self._catalog[1]
        events = (await get_json(client, SCOREBOARD)).get("events") or []
        self._events = events
        open_games = [e for e in events
                      if dig(e, "competitions", 0, "status", "type", "state", default="pre") != "post"]
        _, fantasy_ids = self._fantasy()
        my_teams = {str(t) for t in self.ctx.section("scores", "nfl").get("teams", [])}
        sem = asyncio.Semaphore(4)

        async def one(e):
            async with sem:
                return (await self._game(client, e, fantasy_ids, my_teams, with_box=False))[0]

        found = await asyncio.gather(*(one(e) for e in open_games), return_exceptions=True)
        props = dedupe([p for r in found if isinstance(r, list) for p in r])
        self._catalog = (time.time(), props)
        return props

    def start_of(self, game_id: str) -> str:
        e = next((e for e in self._events if e["id"] == game_id), None)
        if e:
            return datetime.fromisoformat(e["date"].replace("Z", "+00:00")).isoformat()
        return datetime.now(timezone.utc).isoformat()

    def next_interval(self, b: PropsBoard) -> float:
        live_ids = {e["id"] for e in self._events
                    if dig(e, "competitions", 0, "status", "type", "state", default="") == "in"}
        if any(g.state == "in" for g in b.games) or (self.watch.game_ids() & live_ids):
            return float(self.cfg.get("live_interval", 90))
        return 900

    def summarize(self, b: dict) -> list[str]:
        lines = [" | ".join(f"{g['reason']}: {g['label']} ({g['status']})" for g in b["games"]) or b.get("note", "")]
        for p in b["props"][:15]:
            tag = "*" if p.get("pinned") else "F" if p["my_fantasy"] else "T" if p["my_team"] else " "
            live = f"{p['live']:g}" if p.get("live") is not None else "-"
            mv = f"{p['move']:+g}" if p["move"] else ""
            lines.append(f"  {tag} {p['name']:<22} {p['team']:<4} {p['market']:<9} {p['line']:>6g} {mv:>6}  live {live}")
        return lines
