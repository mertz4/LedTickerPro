"""RSS / Atom headlines."""
from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .base import Source, SourceError, get_text

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Headline:
    source: str
    title: str
    link: str | None
    published: str | None


@dataclass
class News:
    headlines: list[Headline] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _clean(s: str | None) -> str:
    return " ".join(html.unescape(TAG_RE.sub("", s or "")).split())


def _date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _text(el) -> str:
    """All text inside an element, including inside inline HTML child tags."""
    return "" if el is None else "".join(el.itertext())


def parse_feed(source: str, xml_text: str) -> list[Headline]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):  # RSS 2.0
        title = _clean(_text(item.find("title")))
        if title:
            out.append(Headline(source, title, (item.findtext("link") or "").strip() or None, _date(item.findtext("pubDate"))))
    for entry in root.iter(f"{ATOM}entry"):  # Atom
        title = _clean(_text(entry.find(f"{ATOM}title")))
        link = entry.find(f"{ATOM}link")
        if title:
            out.append(Headline(source, title, link.get("href") if link is not None else None,
                                _date(entry.findtext(f"{ATOM}updated") or entry.findtext(f"{ATOM}published"))))
    return out


class NewsSource(Source):
    name = "news"
    label = "News"
    stale_after = 6 * 3600

    def __init__(self, ctx):
        super().__init__(ctx)
        cfg = ctx.section("news")
        self.feeds = cfg.get("feeds", [])
        self.max_items = int(cfg.get("max_items", 20))

    def enabled(self):
        return (True, "") if self.feeds else (False, "no feeds in config.toml")

    async def fetch(self, client: httpx.AsyncClient) -> News:
        async def one(feed):
            try:
                return parse_feed(feed["name"], await get_text(client, feed["url"])), None
            except (SourceError, ET.ParseError) as e:
                return [], f"{feed['name']}: {e}"

        results = await asyncio.gather(*(one(f) for f in self.feeds))
        items = [h for hs, _ in results for h in hs]
        errors = [e for _, e in results if e]
        if not items and errors:
            raise SourceError("; ".join(errors))
        seen, unique = set(), []
        for h in sorted(items, key=lambda h: h.published or "", reverse=True):
            key = h.title.lower()
            if key not in seen:
                seen.add(key)
                unique.append(h)
        return News(headlines=unique[: self.max_items], errors=errors)

    def next_interval(self, data) -> float:
        return 900

    def summarize(self, n: dict) -> list[str]:
        lines = [f"{len(n['headlines'])} headlines" + (f" ({len(n['errors'])} feed errors)" if n["errors"] else "")]
        lines += [f"  [{h['source']}] {h['title']}" for h in n["headlines"][:10]]
        lines += [f"  ! {e}" for e in n["errors"]]
        return lines
