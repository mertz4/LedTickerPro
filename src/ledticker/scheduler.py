"""Runs every enabled source on its own adaptive interval, with backoff, never blocking the others."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx

from .config import Context
from .sources import Source, SourceAuthError, SourceError, build_sources
from .sources.base import DEFAULT_UA
from .store import Store, utcnow

log = logging.getLogger("ledticker.scheduler")

FETCH_TIMEOUT = 60
AUTH_RETRY = 1800


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True,
                             headers={"User-Agent": DEFAULT_UA},
                             limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))


async def run_once(src: Source, store: Store, client: httpx.AsyncClient) -> float:
    """Fetch one source, record the result, return seconds until it should run again."""
    st = store.states[src.name]
    try:
        data = await asyncio.wait_for(src.fetch(client), FETCH_TIMEOUT)
    except SourceAuthError as e:
        store.fail(src.name, str(e), auth=True)
        log.warning("%s: auth error: %s", src.name, e)
        return AUTH_RETRY
    except (SourceError, asyncio.TimeoutError, httpx.HTTPError) as e:
        store.fail(src.name, str(e) or type(e).__name__)
        delay = min(src.max_backoff, 15 * 2 ** (st.error_count - 1))
        log.warning("%s: %s (retry in %ss)", src.name, e or type(e).__name__, int(delay))
        return delay
    except Exception as e:  # parser bug or API schema change: keep last good data
        store.fail(src.name, f"{type(e).__name__}: {e}")
        log.exception("%s: unexpected error", src.name)
        return min(src.max_backoff, 60 * 2 ** (st.error_count - 1))
    store.ok(src.name, data)
    try:
        return max(5.0, float(src.next_interval(data)))
    except Exception:
        log.exception("%s: next_interval failed", src.name)
        return 300


class Scheduler:
    def __init__(self, ctx: Context, store: Store, sources: list[Source] | None = None):
        self.ctx, self.store = ctx, store
        self.sources = sources if sources is not None else build_sources(ctx)
        self._tasks: list[asyncio.Task] = []
        for s in self.sources:
            on, why = s.enabled()
            store.register(s.name, s.label, on, why, s.stale_after)

    async def _loop(self, src: Source, client: httpx.AsyncClient) -> None:
        while True:
            now = self.ctx.now()
            until = src.dormant_until(now)
            if until:
                if self.store.states[src.name].dormant_until != until.isoformat():
                    self.store.dormant(src.name, until)
                    log.info("%s: out of season, waiting until %s", src.name, until.date())
                # re-check at least every 6h so config changes / clock jumps are picked up
                await asyncio.sleep(min((until - now).total_seconds(), 6 * 3600))
                continue
            delay = await run_once(src, self.store, client)
            if src.dormant_until(self.ctx.now()):
                continue  # this fetch revealed the season is over; go dormant now
            self.store.states[src.name].next_run = (utcnow() + timedelta(seconds=delay)).isoformat()
            log.info("%s: next in %ss", src.name, int(delay))
            await asyncio.sleep(delay)

    async def run(self) -> None:
        async with make_client() as client:
            active = [s for s in self.sources if self.store.states[s.name].enabled]
            for s in self.sources:
                if not self.store.states[s.name].enabled:
                    log.info("%s: disabled (%s)", s.name, self.store.states[s.name].disabled_reason)
            self._tasks = [asyncio.create_task(self._loop(s, client), name=s.name) for s in active]
            try:
                await asyncio.gather(*self._tasks)
            finally:
                for t in self._tasks:
                    t.cancel()
