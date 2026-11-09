# LED Ticker

Raspberry Pi–driven 256×128 RGB LED ticker (four 128×64 P2 panels). See `LED_Ticker_Project_Plan.md` for the full plan.

> **Setting it up?** Follow the [Setup Guide](https://claude.ai/artifact/1uWQYW1RwoEh2ovhmbw4Pq): Pi, Tailscale, iPhone Home Screen app, notifications and adding people.

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
- **Pictures** (Pictures tab): upload photos or animated GIFs from your phone or computer. Show one now, or run them as a slideshow. **Fill** crops to the space and **Fit** shows the whole picture. Phone photos are turned upright automatically. Files are stored in `data/photos/`. Uploading needs the "Upload & show pictures" permission.
- **Any widget in any section shape**: full screen, a quarter tile, side-by-side halves or stacked halves. Compact tile designs are scaled 2x at full screen and centered in tall halves; list widgets use the extra rows.
- **Pregame light show** (Pregame bar on the dashboard): a full-screen DJ show with lasers, an LED equalizer, a neon tunnel, a hyperspace warp and plasma. The auto show changes scene every 8 bars and drops your message ("PREGAME") on the beat every 4. **Tap tempo** locks the lights to the song, since there's no microphone. Strobe is opt-in in Settings. Each scene is also a widget you can put in a layout or mode.
- **Christmas theme** (Settings tab): garland swags with twinkling lights, pine on the sides, holly in the corners and falling snow, all drawn *behind* the content so every section stays readable. It can be limited to December only. There is also a **Christmas countdown** widget (tree, days left, ticking clock, and "MERRY CHRISTMAS!" on the day) that fits any section size.

**Roles by Tailscale account** (superuser, partner and roommate) and the **love tap** are in. See *Remote access and roles* below. The LED hardware output isn't built yet.

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
python -m ledticker preview --host 0.0.0.0   # let your phone on the same Wi-Fi open it (no login: full control!)
python -m ledticker preview --auth tailscale # roles from Tailscale identity (what the Pi runs)
python -m ledticker snapshot             # render every screen to data/snapshots/*.png (LED look)
python -m ledticker snapshot weather --scale 1   # raw 256x128 pixels
python -m pytest                         # tests (use saved API responses; no network)
```

## Remote access and roles (Tailscale)

Step-by-step version, with the app icons: [Setup Guide](https://claude.ai/artifact/1uWQYW1RwoEh2ovhmbw4Pq).

The dashboard is reached through Tailscale, and **who you are is your Tailscale account**. There are no passwords. No router ports are opened. The app only listens on `127.0.0.1`, and `tailscale serve` publishes it over HTTPS to your tailnet. Serve tags every request with the caller's account (`Tailscale-User-Login`), and the app checks that account's permissions on every action. Accounts that aren't on the list get a "No access" page.

| Role | Can do by default |
|---|---|
| **Superuser** | Everything, including the **Access** tab where you add people and grant extras |
| **Partner** | Watch the live preview and send a **love tap** |
| **Roommate** | Watch the live preview, change brightness and turn the display on or off |

A superuser can tick extra permissions for anyone on the Access tab: brightness and power; switching modes, skipping and showing a widget; editing layouts, modes, schedule and settings; player props; love tap.

### Setup

1. **Name yourself superuser.** Add your Tailscale login to `secrets.env`. This is fixed, so nobody can remove it from the dashboard.
   ```env
   LEDTICKER_SUPERUSERS=you@gmail.com
   ```
2. **Set up the Pi.** Clone the repo onto the Pi, copy `secrets.env` over, then run:
   ```bash
   bash deploy/setup-pi.sh
   ```
   The script installs the app as a service that starts on boot (with `--auth tailscale`), installs Tailscale and publishes the dashboard. When it prints a login URL, sign in with **your** account. If it asks you to enable HTTPS certificates, follow the link and turn them on.
3. **Share the Pi.** In the Tailscale admin console, open the Pi's machine menu and choose **Share…**:
   - your girlfriend accepts with **her own** Tailscale account (free);
   - roommates accept with a shared **house** account (e.g. a new `ourhouse.ticker@gmail.com`), or each with their own.

   People the Pi is shared with can reach only the Pi, nothing else on your tailnet.
4. **Add them on the Access tab.** Have each person open `https://ledticker.<tailnet>.ts.net` once. They'll see "No access", and their account shows up under **Recently turned away** on your Access tab. Click **Add as Partner** or **Add as Roommate**.

**Make it an app on iPhone:** open the dashboard in Safari, tap **Share**, then **Add to Home Screen**. It gets a heart icon named "Ticker" and opens full screen. Tailscale has to be on for it to load.

### Notifications

On the iPhone Home Screen app, open the **Notifications** card, pick topics and tap **Turn on notifications**. Topics are fantasy trades, severe weather alerts, and love taps (superusers only). Each person chooses their own, per device. iPhone needs iOS 16.4 or later, and notifications only work from the Home Screen app, not a Safari tab. The Pi signs messages with a key it creates once (`data/vapid.pem`). Set `[push] contact` in `config.toml` to override the contact address sent with them, which defaults to the first superuser's email.

### Love tap

The Partner role (or anyone you grant it to) gets a Love tap card: an optional message (up to 60 characters) and an animation style. **Flourish** is a wreath of hearts blooming around the message. **Burst** is one heart that swells and explodes. **Rain** is hearts falling. **Heartbeat** is a big beating heart. It takes over the whole display for `[love] seconds`, then everything resumes where it was. If the display is off, it waits and plays when the display turns back on. Defaults are in `config.toml` under `[love]`: the message is "bab misses you" and the style is Burst.

### Housekeeping

- To revoke someone, click **Remove** on the Access tab, and unshare the Pi in the admin console. When a roommate moves out, also change the house account's password.
- Don't use `tailscale funnel`. It makes the page public, where there's no Tailscale identity to check.
- Locally, `preview` defaults to `--auth none`, which gives full control with no login, for development. `--auth tailscale` refuses to listen on anything but `127.0.0.1`, because anyone who could reach the port directly could fake the identity header.
- Logs: `journalctl -u ledticker -f`. Restart: `sudo systemctl restart ledticker`.

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
