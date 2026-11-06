"""Data source registry."""
from __future__ import annotations

from ..config import Context
from .base import Source, SourceAuthError, SourceError
from .espn_fantasy import EspnFantasy, EspnTrades
from .espn_props import EspnProps
from .espn_scores import LEAGUES, EspnScores
from .lastfm import LastFm
from .markets import Crypto, Stocks
from .news import NewsSource
from .nws import Nws

__all__ = ["Source", "SourceError", "SourceAuthError", "build_sources"]


def build_sources(ctx: Context, store=None) -> list[Source]:
    sources: list[Source] = [EspnScores(ctx, lg) for lg in LEAGUES if lg in ctx.section("scores")]
    sources += [EspnFantasy(ctx), EspnTrades(ctx), EspnProps(ctx), Nws(ctx), Stocks(ctx), Crypto(ctx), NewsSource(ctx), LastFm(ctx)]
    for s in sources:
        if hasattr(s, "store"):
            s.store = store   # read-only access to other sources' data (props reads my fantasy roster)
    return sources
