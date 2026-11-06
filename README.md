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
| NFL player props + live tracking | `props` | nothing (DraftKings lines via ESPN) |
| Apple Music now playing (*deferred*) | `music` | off for now; `music.enabled`, `music.lastfm_user`, `LASTFM_API_KEY` |

**Renderer and browser preview are working.**

- **Full screens:**
  - my-team spotlight
  - fantasy matchup, league scoreboard, standings with the playoff line, and player of the week (slides in)
  - breaking fantasy trade alert
  - weather (custom pixel icons) and weather alert takeover
  - college football and NFL boards (records and spreads)
  - markets (branded badges)
  - NFL player props: live tracker plus biggest line moves
- **Tiles**, one per 128x64 module, for 4-up slides:
  - weather and markets
  - NFL featured game (TNF/SNF/MNF aware), CFB game and my-team game
  - fantasy matchup and standings
  - props, player of the week, news and clock
- **Custom layouts:** split the display any way except diagonally (repeated top/bottom or left/right splits, drag to resize, snaps to module seams). Put any widget in any section. Each widget uses its full design in big sections and its compact design in small ones. Layouts are saved by name.
- **Modes:** playlists of saved layouts or single full-screen widgets.
- **Schedule:** rules match days of the week, specific dates or date ranges, plus optional times (overnight ranges work). Each rule can set the mode, the brightness and/or power on/off. Brightness and power can also be set by hand from the page.
- Everything is edited on the preview page (Layouts / Modes / Schedule tabs) and stored in `data/modes.json`.
- **Bottom ticker:** trades, live scores, markets and news scroll along the bottom with a clock.

The phone control dashboard (auth, settings) and the LED hardware output are not built yet.

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
python -m ledticker preview              # everything + browser preview at http://localhost:8000
python -m ledticker preview --host 0.0.0.0   # let your phone on the same Wi-Fi open it (no login yet!)
python -m ledticker snapshot             # render every screen to data/snapshots/*.png (LED look)
python -m ledticker snapshot weather --scale 1   # raw 256x128 pixels
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
  display.py         module size/grid -> canvas size + seams ([display] in config.toml)
  render/
    engine.py        playlist rotation, bottom ticker + clock, 30 fps frame loop
    screens.py       spotlight, fantasy, weather, boards, markets, alert
    icons.py         pixel-art weather icons (16/32/48, day/night, animated)
    images.py        logo cache: download once, SVG->pixels, LED-size, brighten dark logos
    fonts.py         public-domain BDF pixel fonts (same ones the LED library uses)
    draw.py          text, big digits, arrows, badges
  web/
    app.py           FastAPI: WebSocket frame stream + control API
    static/index.html  LED-look preview (dots, glow, panel seams, actual size)
assets/fonts/bdf/    pixel fonts (public domain; tom-thumb is MIT)
```
