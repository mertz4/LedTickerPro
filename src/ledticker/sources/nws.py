"""National Weather Service (api.weather.gov): current obs, forecast, hourly, active alerts. No key needed."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from .base import Source, SourceError, dig, get_json

API = "https://api.weather.gov"


@dataclass
class Current:
    station: str
    observed: str | None
    temp_f: int | None
    feels_like_f: int | None
    conditions: str | None
    humidity: int | None
    wind_mph: int | None
    wind_gust_mph: int | None
    wind_dir: str | None
    icon: str | None


@dataclass
class Period:
    name: str
    start: str
    is_day: bool
    temp_f: int
    precip_pct: int
    short: str
    wind: str
    icon: str | None


@dataclass
class Hour:
    time: str
    temp_f: int
    precip_pct: int
    short: str
    icon: str | None = None
    is_day: bool = True


@dataclass
class Alert:
    id: str
    event: str               # "Tornado Warning"
    severity: str | None     # Extreme / Severe / Moderate / Minor
    urgency: str | None
    level: str               # warning | watch | advisory | statement
    headline: str | None
    onset: str | None
    ends: str | None


@dataclass
class Weather:
    location: str
    current: Current | None
    periods: list[Period] = field(default_factory=list)
    hourly: list[Hour] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


def c_to_f(c: float | None) -> int | None:
    return None if c is None else round(c * 9 / 5 + 32)


def kmh_to_mph(k: float | None) -> int | None:
    return None if k is None else round(k * 0.621371)


def compass(deg: float | None) -> str | None:
    if deg is None:
        return None
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def _val(props: dict, key: str) -> float | None:
    return dig(props, key, "value")


def parse_obs(obs: dict, station: str) -> Current:
    p = obs.get("properties", obs)
    temp = _val(p, "temperature")
    feels = _val(p, "heatIndex") if _val(p, "heatIndex") is not None else _val(p, "windChill")
    rh = _val(p, "relativeHumidity")
    return Current(
        station=station, observed=p.get("timestamp"),
        temp_f=c_to_f(temp), feels_like_f=c_to_f(feels) if feels is not None else c_to_f(temp),
        conditions=p.get("textDescription") or None,
        humidity=round(rh) if rh is not None else None,
        wind_mph=kmh_to_mph(_val(p, "windSpeed")), wind_gust_mph=kmh_to_mph(_val(p, "windGust")),
        wind_dir=compass(_val(p, "windDirection")), icon=p.get("icon"),
    )


def parse_periods(fc: dict, limit: int = 14) -> list[Period]:
    return [Period(
        name=x["name"], start=x["startTime"], is_day=x["isDaytime"], temp_f=x["temperature"],
        precip_pct=dig(x, "probabilityOfPrecipitation", "value", default=0) or 0,
        short=x["shortForecast"], wind=f"{x.get('windDirection', '')} {x.get('windSpeed', '')}".strip(),
        icon=x.get("icon"),
    ) for x in dig(fc, "properties", "periods", default=[])[:limit]]


def parse_hourly(fc: dict, limit: int = 24) -> list[Hour]:
    return [Hour(
        time=x["startTime"], temp_f=x["temperature"],
        precip_pct=dig(x, "probabilityOfPrecipitation", "value", default=0) or 0, short=x["shortForecast"],
        icon=x.get("icon"), is_day=x.get("isDaytime", True),
    ) for x in dig(fc, "properties", "periods", default=[])[:limit]]


def alert_level(event: str) -> str:
    e = event.lower()
    for lvl in ("warning", "watch", "advisory"):
        if lvl in e:
            return lvl
    return "statement"


LEVEL_ORDER = {"warning": 0, "watch": 1, "advisory": 2, "statement": 3}


def parse_alerts(a: dict) -> list[Alert]:
    out = []
    for f in a.get("features", []):
        p = f.get("properties", {})
        ev = p.get("event", "Alert")
        out.append(Alert(
            id=p.get("id") or f.get("id", ""), event=ev, severity=p.get("severity"), urgency=p.get("urgency"),
            level=alert_level(ev), headline=p.get("headline"), onset=p.get("onset") or p.get("effective"),
            ends=p.get("ends") or p.get("expires"),
        ))
    out.sort(key=lambda x: LEVEL_ORDER[x.level])
    return out


class Nws(Source):
    """Polls alerts every run (2 min); observations every 10 min; forecasts every 30 min."""
    name = "weather"
    label = "NWS weather"
    stale_after = 3600
    OBS_EVERY = 600
    FORECAST_EVERY = 1800
    POINTS_EVERY = 86400

    def __init__(self, ctx):
        super().__init__(ctx)
        loc = ctx.section("location")
        self.lat, self.lon = loc.get("lat"), loc.get("lon")
        self.loc_name = loc.get("name", "")
        self.headers = {"User-Agent": ctx.section("nws").get("user_agent", "LedTicker/0.1"),
                        "Accept": "application/geo+json"}
        self._points: dict[str, Any] | None = None
        self._station: str | None = None
        self._t: dict[str, float] = {}
        self._last = Weather(location=self.loc_name, current=None)

    def enabled(self):
        if self.lat is None or self.lon is None:
            return False, "location.lat/lon missing from config.toml"
        return True, ""

    def _due(self, key: str, every: float) -> bool:
        return time.monotonic() - self._t.get(key, -1e12) >= every

    async def _get(self, client, url, **kw):
        return await get_json(client, url, headers=self.headers, **kw)

    async def fetch(self, client: httpx.AsyncClient) -> Weather:
        if self._points is None or self._due("points", self.POINTS_EVERY):
            p = (await self._get(client, f"{API}/points/{self.lat:.4f},{self.lon:.4f}"))["properties"]
            stations = await self._get(client, p["observationStations"])
            feats = stations.get("features") or []
            if not feats:
                raise SourceError("NWS returned no observation stations")
            self._points, self._station = p, feats[0]["properties"]["stationIdentifier"]
            self._t["points"] = time.monotonic()
            self.loc_name = self.loc_name or dig(p, "relativeLocation", "properties", "city", default="")

        w = self._last
        w.location = self.loc_name
        w.alerts = parse_alerts(await self._get(client, f"{API}/alerts/active",
                                                params={"point": f"{self.lat:.4f},{self.lon:.4f}"}))
        if self._due("obs", self.OBS_EVERY):
            w.current = parse_obs(await self._get(client, f"{API}/stations/{self._station}/observations/latest"),
                                  self._station)
            self._t["obs"] = time.monotonic()
        if self._due("forecast", self.FORECAST_EVERY):
            w.periods = parse_periods(await self._get(client, self._points["forecast"]))
            w.hourly = parse_hourly(await self._get(client, self._points["forecastHourly"]))
            self._t["forecast"] = time.monotonic()
        self._last = w
        return w

    def next_interval(self, data) -> float:
        return 120

    def summarize(self, w: dict) -> list[str]:
        lines = [w["location"]]
        c = w.get("current")
        if c:
            gust = f" G{c['wind_gust_mph']}" if c.get("wind_gust_mph") else ""
            lines.append(f"  Now: {c['temp_f']}°F {c['conditions'] or ''} | feels {c['feels_like_f']}°F | "
                         f"{c['humidity']}% RH | wind {c['wind_dir'] or ''} {c['wind_mph']}{gust} mph ({c['station']})")
        for p in w.get("periods", [])[:4]:
            lines.append(f"  {p['name']:<16} {p['temp_f']:>3}°F {p['precip_pct']:>3}%  {p['short']}")
        if w.get("hourly"):
            lines.append("  Hourly: " + "  ".join(
                f"{datetime.fromisoformat(h['time']).strftime('%I%p').lstrip('0').lower()} {h['temp_f']}°"
                for h in w["hourly"][:8]))
        lines.append("  Alerts: " + (", ".join(f"{a['event']} (until {a['ends']})" for a in w["alerts"]) or "none"))
        return lines
