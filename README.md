# LED Ticker

Raspberry Pi–driven 256×128 RGB LED ticker (four 128×64 P2 panels). See `LED_Ticker_Project_Plan.md` for the full plan.

## Status

**Data collection layer is done.** These sources fetch, normalize, cache last-good data, and report health:

| Source | Name | Needs |
|---|---|---|
| NFL / CFB / NBA / NCAAB / MLB / college baseball scores + odds | `scores.<league>` | nothing |
| ESPN fantasy league | `fantasy` | `ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID` |
| NWS weather + alerts | `weather` | nothing (set a contact in `nws.user_agent`) |
| Crypto | `markets.crypto` | nothing (`COINGECKO_API_KEY` optional) |
| Stocks | `markets.stocks` | `FINNHUB_API_KEY` (free at finnhub.io) |
| News (RSS) | `news` | nothing |
| Apple Music now playing (*deferred*) | `music` | off for now; `music.enabled`, `music.lastfm_user`, `LASTFM_API_KEY` |

The rendering and the web dashboard are not built yet.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows
# .venv/bin/python -m pip install -e ".[dev]"       # Pi / Linux / macOS
```

- `config.toml` holds teams, location, watchlist and feeds. It is safe to commit.
- `secrets.env` holds cookies and API keys. It is git-ignored and must never be committed.

```env
ESPN_LEAGUE_ID=...
ESPN_S2=...
ESPN_SWID={...}
FINNHUB_API_KEY=...
LASTFM_API_KEY=...
```

## Commands

```bash
python -m ledticker sources              # which sources are on/off and why
python -m ledticker fetch                # fetch everything once and print it
python -m ledticker fetch scores.cfb     # one source (or "scores" for every league)
python -m ledticker fetch fantasy --json # normalized JSON (what the renderer will consume)
python -m ledticker run -v               # run continuously; writes data/cache.json
python -m ledticker status               # health of each source from the cache
python -m pytest                         # tests (use saved API responses; no network)
```

## Layout

```text
src/ledticker/
  config.py          config.toml + secrets.env loader
  store.py           last-good data + health per source, persisted to data/cache.json
  scheduler.py       runs each source on its own adaptive interval with backoff
  sources/
    espn_scores.py   scoreboards, odds, "big games" ranking
    espn_fantasy.py  league, standings, matchups, my lineup
    nws.py           observations, forecast, hourly, alerts
    markets.py       Finnhub stocks, CoinGecko crypto
    news.py          RSS/Atom
    lastfm.py        now playing
```
