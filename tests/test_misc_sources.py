from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ledticker.sources.base import SourceError
from ledticker.sources.lastfm import parse_recent
from ledticker.sources.markets import parse_coingecko, parse_finnhub, us_market_open
from ledticker.sources.news import parse_feed

TZ = ZoneInfo("America/Chicago")

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
<item><title>Aggies &amp; Gamecocks <b>preview</b></title><link>https://e.x/1</link>
<pubDate>Thu, 05 Nov 2026 20:00:00 GMT</pubDate></item>
<item><title>Second story</title><link>https://e.x/2</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom story</title><link href="https://a.x/1"/><updated>2026-11-05T12:00:00Z</updated></entry></feed>"""

def test_rss_and_atom():
    items = parse_feed("ESPN", RSS)
    assert items[0].title == "Aggies & Gamecocks preview" and items[0].published.startswith("2026-11-05T20:00")
    assert items[1].published is None
    atom = parse_feed("A", ATOM)
    assert atom[0].title == "Atom story" and atom[0].link == "https://a.x/1"


def test_markets():
    q = parse_finnhub("SPY", {"c": 600.123, "d": -3.2, "dp": -0.53, "pc": 603.3})
    assert q.price == 600.12 and q.change_pct == -0.53 and q.error is None
    assert parse_finnhub("NOPE", {"c": 0, "d": None, "dp": None}).error
    qs = parse_coingecko(["bitcoin", "bogus"], {"bitcoin": {"usd": 110.0, "usd_24h_change": 10.0}})
    assert qs[0].symbol == "BTC" and qs[0].change == 10.0 and qs[1].error
    assert us_market_open(datetime(2026, 11, 5, 10, 0, tzinfo=TZ))        # 11:00 ET Thursday
    assert not us_market_open(datetime(2026, 11, 7, 10, 0, tzinfo=TZ))    # Saturday
    assert not us_market_open(datetime(2026, 11, 5, 16, 0, tzinfo=TZ))    # 17:00 ET


def test_lastfm():
    playing = {"recenttracks": {"track": [{"name": "Song", "artist": {"#text": "Band"}, "album": {"#text": "LP"},
                                           "image": [{"#text": "s.jpg"}, {"#text": "xl.jpg"}],
                                           "@attr": {"nowplaying": "true"}}]}}
    np = parse_recent(playing)
    assert np.playing and np.title == "Song" and np.art_url == "xl.jpg"
    past = {"recenttracks": {"track": {"name": "Old", "artist": {"#text": "B"}, "album": {"#text": ""},
                                       "image": [], "date": {"#text": "05 Nov 2026, 20:00"}}}}
    np = parse_recent(past)
    assert not np.playing and np.album is None and np.last_played
    with pytest.raises(SourceError):
        parse_recent({"error": 10, "message": "Invalid API key"})
