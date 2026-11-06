"""Common pieces for every data source."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from ..config import Context

DEFAULT_UA = "LedTicker/0.1 (personal LED display)"


class SourceError(Exception):
    """A fetch failed; keep the last good data and retry with backoff."""


class SourceAuthError(SourceError):
    """Credentials rejected; retrying soon won't help."""


class Source(ABC):
    #: unique key, e.g. "scores.nfl"
    name: str = ""
    #: human label for the dashboard
    label: str = ""
    #: data older than this (seconds) is shown as stale
    stale_after: float = 1800
    #: longest backoff between failed attempts
    max_backoff: float = 900

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def enabled(self) -> tuple[bool, str]:
        """(enabled, reason-if-disabled)."""
        return True, ""

    def dormant_until(self, now: datetime) -> datetime | None:
        """If the source shouldn't poll yet (e.g. out of season), when to wake up. None = active."""
        return None

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> Any:
        """Fetch and return normalized data (a dataclass or JSON-able dict)."""

    def next_interval(self, data: Any) -> float:
        """Seconds until the next fetch, given the data just fetched."""
        return 300

    def summarize(self, data: dict) -> list[str]:
        """Human-readable lines for the CLI, from the JSON form of the data."""
        return [str(data)[:200]]


async def get_json(client: httpx.AsyncClient, url: str, *, params: Any = None,
                   headers: dict[str, str] | None = None, auth_hint: str = "") -> Any:
    try:
        r = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as e:
        raise SourceError(f"timeout: {url}") from e
    except httpx.HTTPError as e:
        raise SourceError(f"network error: {e}") from e
    if r.status_code in (401, 403):
        raise SourceAuthError(auth_hint or f"HTTP {r.status_code} (not authorized)")
    if r.status_code == 429:
        raise SourceError("rate limited (HTTP 429)")
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} from {r.url.host}")
    try:
        return r.json()
    except ValueError as e:
        raise SourceError(f"invalid JSON from {r.url.host}") from e


async def get_text(client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None) -> str:
    try:
        r = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise SourceError(f"network error: {e}") from e
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} from {r.url.host}")
    return r.text


def dig(d: Any, *path: Any, default: Any = None) -> Any:
    """Safe nested lookup through dicts and lists."""
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return default
        if d is None:
            return default
    return d


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def season_wait(now: datetime, start: str | None, end: str | None) -> datetime | None:
    """Given a yearly window as "MM-DD" strings (may wrap past New Year), return when the next
    window opens if `now` is outside it, else None."""
    if not start:
        return None
    s, e = _md(start), _md(end or "12-31")
    md = (now.month, now.day)
    inside = (s <= md <= e) if s <= e else (md >= s or md <= e)
    if inside:
        return None
    opens = now.replace(month=s[0], day=s[1], hour=0, minute=0, second=0, microsecond=0)
    if opens <= now:
        opens = opens.replace(year=now.year + 1)
    return opens


def to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
