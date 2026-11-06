"""Stocks (Finnhub, free key) and crypto (CoinGecko, keyless or free demo key)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .base import Source, SourceError, get_json

NY = ZoneInfo("America/New_York")
CRYPTO_SYMBOLS = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "dogecoin": "DOGE",
                  "ripple": "XRP", "cardano": "ADA", "litecoin": "LTC"}


@dataclass
class Quote:
    symbol: str
    price: float | None
    change: float | None
    change_pct: float | None
    prev_close: float | None = None
    error: str | None = None
    logo: str | None = None       # image URL (None for ETFs / unknown)
    name: str | None = None


@dataclass
class Quotes:
    kind: str                 # stocks | crypto
    market_open: bool
    quotes: list[Quote] = field(default_factory=list)


def us_market_open(now: datetime) -> bool:
    """Regular session 9:30-16:00 ET on weekdays. Exchange holidays are not handled."""
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return False
    minutes = ny.hour * 60 + ny.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def parse_finnhub(symbol: str, d: dict) -> Quote:
    price = d.get("c")
    if not price:  # Finnhub returns zeros for unknown symbols
        return Quote(symbol=symbol, price=None, change=None, change_pct=None, error="no data (check symbol)")
    return Quote(symbol=symbol, price=round(price, 2), change=round(d.get("d") or 0, 2),
                 change_pct=round(d.get("dp") or 0, 2), prev_close=d.get("pc"))


def parse_coingecko(ids: list[str], rows: list[dict]) -> list[Quote]:
    """Rows from /coins/markets (price, 24h change, image), returned in config order."""
    by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
    out = []
    for cid in ids:
        r = by_id.get(cid)
        sym = CRYPTO_SYMBOLS.get(cid, cid.upper())
        if not r:
            out.append(Quote(symbol=sym, price=None, change=None, change_pct=None, error="unknown CoinGecko id"))
            continue
        ch, pct = r.get("price_change_24h"), r.get("price_change_percentage_24h")
        out.append(Quote(symbol=(r.get("symbol") or sym).upper(), price=r.get("current_price"),
                         change=round(ch, 2) if ch is not None else None,
                         change_pct=round(pct, 2) if pct is not None else None,
                         logo=r.get("image"), name=r.get("name")))
    return out


def _summ(q: dict) -> list[str]:
    status = "24/7" if q["kind"] == "crypto" else ("market open" if q["market_open"] else "market closed")
    lines = [f"{q['kind'].title()} ({status})"]
    for x in q["quotes"]:
        if x.get("error"):
            lines.append(f"  {x['symbol']:<7} {x['error']}")
            continue
        arrow = "▲" if (x["change"] or 0) >= 0 else "▼"
        lines.append(f"  {x['symbol']:<7} {x['price']:>12,.2f}  {arrow} {x['change']:+,.2f} ({x['change_pct']:+.2f}%)")
    return lines


class Stocks(Source):
    name = "markets.stocks"
    label = "Stocks"
    stale_after = 3 * 3600

    def __init__(self, ctx):
        super().__init__(ctx)
        self.symbols = [s.upper() for s in ctx.section("markets").get("stocks", [])]
        self._profiles: dict[str, tuple[float, str | None, str | None]] = {}  # sym -> (fetched, logo, name)

    def enabled(self):
        if not self.symbols:
            return False, "no stocks in config.toml"
        if not self.ctx.secret("FINNHUB_API_KEY"):
            return False, "FINNHUB_API_KEY missing from secrets.env (free at finnhub.io)"
        return True, ""

    async def fetch(self, client: httpx.AsyncClient) -> Quotes:
        key = self.ctx.secret("FINNHUB_API_KEY")

        hdr = {"X-Finnhub-Token": key}

        async def profile(sym: str) -> tuple[str | None, str | None]:
            """Company logo/name, refreshed daily. ETFs have no profile (logo stays None)."""
            cached = self._profiles.get(sym)
            if cached and time.time() - cached[0] < 86400:
                return cached[1], cached[2]
            try:
                p = await get_json(client, "https://finnhub.io/api/v1/stock/profile2", params={"symbol": sym}, headers=hdr)
                logo, name = (p.get("logo") or None, p.get("name") or None) if isinstance(p, dict) else (None, None)
            except SourceError:
                return (cached[1], cached[2]) if cached else (None, None)
            self._profiles[sym] = (time.time(), logo, name)
            return logo, name

        async def one(sym: str) -> Quote:
            try:
                d = await get_json(client, "https://finnhub.io/api/v1/quote", params={"symbol": sym},
                                   headers=hdr, auth_hint="Finnhub rejected FINNHUB_API_KEY")
                q = parse_finnhub(sym, d)
            except SourceError as e:
                return Quote(symbol=sym, price=None, change=None, change_pct=None, error=str(e))
            q.logo, q.name = await profile(sym)
            return q

        quotes = await asyncio.gather(*(one(s) for s in self.symbols))
        if all(q.error for q in quotes):
            raise SourceError(f"all stock quotes failed: {quotes[0].error}")
        return Quotes(kind="stocks", market_open=us_market_open(self.ctx.now()), quotes=list(quotes))

    def next_interval(self, q: Quotes) -> float:
        return 120 if q.market_open else 1800

    def summarize(self, q):
        return _summ(q)


class Crypto(Source):
    name = "markets.crypto"
    label = "Crypto"
    stale_after = 1800

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ids = [c.lower() for c in ctx.section("markets").get("crypto", [])]

    def enabled(self):
        return (True, "") if self.ids else (False, "no crypto in config.toml")

    async def fetch(self, client: httpx.AsyncClient) -> Quotes:
        headers = {}
        if key := self.ctx.secret("COINGECKO_API_KEY"):
            headers["x-cg-demo-api-key"] = key
        rows = await get_json(client, "https://api.coingecko.com/api/v3/coins/markets", headers=headers,
                              params={"vs_currency": "usd", "ids": ",".join(self.ids)})
        if not isinstance(rows, list):
            raise SourceError("unexpected CoinGecko response")
        return Quotes(kind="crypto", market_open=True, quotes=parse_coingecko(self.ids, rows))

    def next_interval(self, q) -> float:
        return 180

    def summarize(self, q):
        return _summ(q)
