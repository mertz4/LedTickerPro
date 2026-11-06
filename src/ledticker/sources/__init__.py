"""Data source registry."""
from __future__ import annotations

from ..config import Context
from .base import Source, SourceAuthError, SourceError
from .espn_fantasy import EspnFantasy
from .espn_scores import LEAGUES, EspnScores
from .lastfm import LastFm
from .markets import Crypto, Stocks
from .news import NewsSource
from .nws import Nws

__all__ = ["Source", "SourceError", "SourceAuthError", "build_sources"]


def build_sources(ctx: Context) -> list[Source]:
    sources: list[Source] = [EspnScores(ctx, lg) for lg in LEAGUES if lg in ctx.section("scores")]
    sources += [EspnFantasy(ctx), Nws(ctx), Stocks(ctx), Crypto(ctx), NewsSource(ctx), LastFm(ctx)]
    return sources
