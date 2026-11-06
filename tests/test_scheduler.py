from pathlib import Path

from ledticker.config import Context
from ledticker.scheduler import run_once
from ledticker.sources.base import Source, SourceAuthError, SourceError
from ledticker.store import Store
from zoneinfo import ZoneInfo


def ctx():
    return Context(root=Path("."), config={}, secrets={}, tz=ZoneInfo("America/Chicago"))


class Flaky(Source):
    name, label = "flaky", "Flaky"

    def __init__(self, c, results):
        super().__init__(c)
        self.results = list(results)

    async def fetch(self, client):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def next_interval(self, data):
        return 42


async def test_keeps_last_good_data_and_backs_off(tmp_path):
    store = Store(tmp_path / "cache.json")
    src = Flaky(ctx(), [{"v": 1}, SourceError("boom"), SourceError("boom"), SourceAuthError("expired"), {"v": 2}])
    store.register(src.name, src.label, True, "", 1800)

    assert await run_once(src, store, None) == 42
    assert store.get("flaky") == {"v": 1} and store.states["flaky"].health() == "ok"

    assert await run_once(src, store, None) == 15
    assert await run_once(src, store, None) == 30           # exponential backoff
    assert store.get("flaky") == {"v": 1}                   # last good data kept
    assert store.states["flaky"].health() == "degraded"

    assert await run_once(src, store, None) == 1800         # auth errors wait longer
    assert store.states["flaky"].health() == "auth_error"

    await run_once(src, store, None)
    assert store.get("flaky") == {"v": 2} and store.states["flaky"].error_count == 0

    reloaded = Store(tmp_path / "cache.json")
    reloaded.load()
    assert reloaded.get("flaky") == {"v": 2}


async def test_parser_crash_is_contained(tmp_path):
    store = Store(None)
    src = Flaky(ctx(), [KeyError("schema changed")])
    store.register(src.name, src.label, True, "", 1800)
    delay = await run_once(src, store, None)
    assert delay == 60 and "KeyError" in store.states["flaky"].last_error
