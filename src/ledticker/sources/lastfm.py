"""Now playing via Last.fm (iPhone Apple Music -> scrobbler app -> Last.fm -> here)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import Source, SourceError, dig, get_json


@dataclass
class NowPlaying:
    playing: bool
    title: str | None
    artist: str | None
    album: str | None
    art_url: str | None
    last_played: str | None   # ISO-ish UTC string when not currently playing


def parse_recent(d: dict) -> NowPlaying:
    if "error" in d:
        raise SourceError(f"Last.fm error {d.get('error')}: {d.get('message')}")
    tracks = dig(d, "recenttracks", "track", default=[])
    if isinstance(tracks, dict):
        tracks = [tracks]
    if not tracks:
        return NowPlaying(False, None, None, None, None, None)
    t = tracks[0]
    images = [i.get("#text") for i in t.get("image", []) if i.get("#text")]
    return NowPlaying(
        playing=dig(t, "@attr", "nowplaying") == "true",
        title=t.get("name"), artist=dig(t, "artist", "#text"), album=dig(t, "album", "#text") or None,
        art_url=images[-1] if images else None, last_played=dig(t, "date", "#text"),
    )


class LastFm(Source):
    name = "music"
    label = "Now playing (Last.fm)"
    stale_after = 600

    def __init__(self, ctx):
        super().__init__(ctx)
        self.user = ctx.section("music").get("lastfm_user")

    def enabled(self):
        if not self.ctx.section("music").get("enabled", False):
            return False, "deferred (future possibility); set music.enabled = true to use"
        if not self.user:
            return False, "music.lastfm_user not set in config.toml"
        if not self.ctx.secret("LASTFM_API_KEY"):
            return False, "LASTFM_API_KEY missing from secrets.env"
        return True, ""

    async def fetch(self, client: httpx.AsyncClient) -> NowPlaying:
        d = await get_json(client, "https://ws.audioscrobbler.com/2.0/", params={
            "method": "user.getrecenttracks", "user": self.user, "api_key": self.ctx.secret("LASTFM_API_KEY"),
            "format": "json", "limit": "1",
        })
        return parse_recent(d)

    def next_interval(self, np: NowPlaying) -> float:
        return 15 if np.playing else 30

    def summarize(self, np: dict) -> list[str]:
        if np["playing"]:
            return [f"Playing: {np['title']} — {np['artist']}" + (f" ({np['album']})" if np["album"] else "")]
        if np["title"]:
            return [f"Not playing. Last: {np['title']} — {np['artist']} at {np['last_played']}"]
        return ["Nothing scrobbled yet"]
