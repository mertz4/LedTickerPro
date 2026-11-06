"""Hand-picked player props: search, the saved picks file, and the props source always tracking them."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from ledticker.config import Context
from ledticker.scheduler import Scheduler
from ledticker.sources.espn_props import EspnProps, PropWatch, prop_key, search_props
from ledticker.store import Store
from test_modes_and_features import _prop

PASS = "Total Passing Yards (incl. overtime)"
REC = "Total Receiving Yards (incl. overtime)"


def event(eid, state, teams, short, start):
    return {"id": eid, "date": start.strftime("%Y-%m-%dT%H:%MZ"), "shortName": short, "competitions": [{
        "status": {"type": {"state": state, "shortDetail": state}},
        "competitors": [{"team": {"id": t, "abbreviation": a}} for t, a in teams]}]}


class FakeEspn:
    """Two games: my Texans live (auto-picked) and an unrelated game later (only fetched for my picks)."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.events = [event("e1", "in", [("34", "HOU"), ("11", "IND")], "IND @ HOU", now - timedelta(hours=1)),
                       event("e2", "pre", [("14", "LAR"), ("25", "SF")], "SF @ LAR", now + timedelta(hours=3))]
        self.rosters = {"34": ("HOU", [("7", "C.J. Stroud", "QB")]), "11": ("IND", []),
                        "14": ("LAR", [("9", "Puka Nacua", "WR")]), "25": ("SF", [])}
        self.props = {"e1": [(PASS, 7, 245.5)], "e2": [(REC, 9, 88.5), (PASS, 9, 0.5)]}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "scoreboard" in u:
            return httpx.Response(200, json={"events": self.events})
        if "/roster" in u:
            abbr, people = self.rosters[u.split("/teams/")[1].split("/")[0]]
            return httpx.Response(200, json={"team": {"abbreviation": abbr}, "athletes": [{"items": [
                {"id": i, "displayName": n, "shortName": n, "position": {"abbreviation": pos}} for i, n, pos in people]}]})
        if "propBets" in u:
            eid = u.split("/events/")[1].split("/")[0]
            return httpx.Response(200, json={"pageCount": 1, "items": [
                {"type": {"name": m}, "current": {"target": {"value": v}}, "open": {"target": {"value": v}},
                 "athlete": {"$ref": f"http://x/athletes/{a}?lang=en"}} for m, a, v in self.props[eid]]})
        if "summary" in u:
            return httpx.Response(200, json={"boxscore": {"players": [{"statistics": [
                {"keys": ["receptions", "receivingYards"], "athletes": [{"athlete": {"id": "9"}, "stats": ["4", "51"]}]},
                {"keys": ["completions/passingAttempts", "passingYards"],
                 "athletes": [{"athlete": {"id": "7"}, "stats": ["10/15", "120"]}]}]}]}})
        return httpx.Response(404)


def source(tmp_path, fake):
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}}, "props": {"max_games": 1}},
                  secrets={}, tz=timezone.utc)
    src = EspnProps(ctx)
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return src, client


def test_search_matches_every_word():
    cat = [_prop("C.J. Stroud", "PASS YDS", 245.5, 240.5, aid=1), _prop("Nico Collins", "REC YDS", 70.5, 70.5, aid=2),
           _prop("Nico Collins", "REC", 5.5, 5.5, aid=2)]
    assert [p.market for p in search_props(cat, "collins")] == ["REC YDS", "REC"]   # stat order, not alphabetical
    assert [p.name for p in search_props(cat, "hou yds")] == ["C.J. Stroud", "Nico Collins"]
    assert search_props(cat, "  ") == []


def test_watch_file_persists_and_prunes(tmp_path):
    w = PropWatch(tmp_path / "w.json")
    kick = datetime.now(timezone.utc) - timedelta(hours=1)
    w.add({"game_id": "g", "athlete_id": 7, "name": "C.J. Stroud", "market": "PASS YDS", "line": 245.5}, kick.isoformat())
    w2 = PropWatch(tmp_path / "w.json")
    assert list(w2.entries) == [prop_key("g", 7, "PASS YDS")]
    w2.prune(kick + timedelta(hours=13))
    assert not PropWatch(tmp_path / "w.json").entries
    assert not w.remove("nope")


def test_picks_from_any_game_are_tracked_first_and_survive_a_pulled_line(tmp_path):
    fake = FakeEspn()
    src, client = source(tmp_path, fake)

    async def go():
        cat = await src.catalog(client)
        assert {(p.name, p.market) for p in cat} == {("C.J. Stroud", "PASS YDS"), ("Puka Nacua", "REC YDS"),
                                                      ("Puka Nacua", "PASS YDS")}
        puka = next(p for p in search_props(cat, "nacua rec"))
        src.watch.add(vars(puka), src.start_of(puka.game_id))

        board = await src.fetch(client)
        assert board.props[0].name == "Puka Nacua" and board.props[0].pinned and board.props[0].game == "SF @ LAR"
        assert [p.name for p in board.props] == ["Puka Nacua", "C.J. Stroud"]   # e2's other lines aren't pulled in
        assert board.props[1].live == 120.0                                       # the live game still tracks

        fake.props["e2"] = []                                    # sportsbook pulls the line at kickoff
        fake.events[1]["competitions"][0]["status"]["type"]["state"] = "in"
        board = await src.fetch(client)
        p = board.props[0]
        assert p.pinned and p.line == 88.5 and p.live == 51.0     # kept the last line, live stat from the box score
        assert src.next_interval(board) == 90
        await client.aclose()

    asyncio.run(go())


def test_scheduler_refresh_wakes_a_sleeping_source():
    async def go():
        sched = Scheduler(Context(root=".", config={}, secrets={}, tz=timezone.utc), Store(None), sources=[])
        task = asyncio.create_task(sched._sleep("props", 60))
        await asyncio.sleep(0)
        assert sched.refresh("props")
        await asyncio.wait_for(task, 1)          # woke immediately instead of after 60 s
        assert not sched.refresh("unknown")

    asyncio.run(go())
