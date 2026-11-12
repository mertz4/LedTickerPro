# LED Ticker

Raspberry Pi–driven 256×128 RGB LED ticker (four 128×64 P2 panels). See `LED_Ticker_Project_Plan.md` for the full plan.

> **Setting it up?** Follow the [Setup Guide](https://claude.ai/artifact/1uWQYW1RwoEh2ovhmbw4Pq): Pi, Tailscale, iPhone Home Screen app, notifications and adding people.

## Status

Hardware facts, driver flags and the power/wiring plan: see `LED_Ticker_Project_Plan.md` sections 2-4.

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
  - big-play celebrations (see below)
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
- **Big plays** (Settings tab): during live NFL games the display follows ESPN's play-by-play for every game one of my fantasy players, the Texans or a rival is in, usually within about 30 seconds of the snap.
  - **My fantasy players:** a catch or run of 20+ yards, a touchdown, or a 5+ point play (all adjustable; bench players optional) takes over the display: a retro pixel stadium where the player leaps for the catch, breaks through the defense or splits the uprights, then fireworks and TOUCHDOWN! / BIG PLAY!, then the player, the play, fantasy points (from the league's own scoring), the score and ESPN's description.
  - **Texans:** every Texans score gets the Texans version: battle-red and navy crowd, a charging bull, red-white-blue fireworks.
  - **Rivals:** big plays by players on rival owners' fantasy teams (`[fantasy] rivals`, owner names as on ESPN) and any score by a rival NFL team (`[scores.nfl] rivals`, the Cowboys) get the stinker instead: a gloomy stadium, a fart cloud, a poop with flies, BOOOO!, then what happened.
  - Test buttons for all four are in Settings. Plays more than 10 minutes old (for example, while the display was off) are skipped. Push notifications topic: "Big plays".
- **Anthony's eat-out counter** (dashboard card): roommates tap **+1** each time he gets fast food, optionally saying where (quick picks for the usual spots). Each +1 plays a full-display "ORDER UP!" takeover: food rains down and a mechanical odometer rolls over to the new number, with "4th time this week". The **Eat-out counter** widget fits any section: a diner-neon sign with chasing marquee bulbs, a pixel burger, fries and soda, the total, this week and month, and how long he's been clean. **Undo last** fixes a mis-tap; superusers can set the total to count times from before the counter. Stored in `data/eatout.json`; name and takeover length in `[eatout]` in `config.toml`. Needs the "Update the eat-out counter" permission (roommates and superusers have it; it can be granted to anyone).
- **Christmas theme** (Settings tab): garland swags with twinkling lights, pine on the sides, holly in the corners and falling snow, all drawn *behind* the content so every section stays readable. It can be limited to December only. There is also a **Christmas countdown** widget (tree, days left, ticking clock, and "MERRY CHRISTMAS!" on the day) that fits any section size.

**Roles by Tailscale account** (superuser, partner and roommate) and the **love tap** are in. See *Remote access and roles* below. The LED panel output is in (`[matrix]` in `config.toml`, off by default): bring it up with `sudo .venv/bin/python -m ledticker matrix-test` on the Pi with the service stopped, then set `enabled = true`.

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
| **Partner** | Watch the live preview, send a **love tap**, use the touchpad and play pong |
| **Roommate** | Watch the live preview, change brightness and turn the display on or off, use the touchpad and play pong |

A superuser can tick extra permissions for anyone on the Access tab: brightness and power; switching modes, skipping and showing a widget; editing layouts, modes, schedule and settings; player props; love tap; touchpad and pong.

### Setup

1. **Name yourself superuser.** Add your Tailscale login to `secrets.env`. This is fixed, so nobody can remove it from the dashboard.
   ```env
   LEDTICKER_SUPERUSERS=you@gmail.com
   ```
2. **Set up the Pi.** Give the Pi read access to the repo (see *Auto-update from GitHub* below), clone it with the SSH address, copy `secrets.env` over, then run:
   ```bash
   bash deploy/setup-pi.sh
   ```
   The script installs the app as a service that starts on boot (with `--auth tailscale`), turns on auto-update, installs Tailscale and publishes the dashboard. When it prints a login URL, sign in with **your** account. If it asks you to enable HTTPS certificates, follow the link and turn them on.
3. **Share the Pi.** In the Tailscale admin console, open the Pi's machine menu and choose **Share…**:
   - your girlfriend accepts with **her own** Tailscale account (free);
   - roommates accept with a shared **house** account (e.g. a new `ourhouse.ticker@gmail.com`), or each with their own.

   People the Pi is shared with can reach only the Pi, nothing else on your tailnet.
4. **Add them on the Access tab.** Have each person open `https://ledticker.<tailnet>.ts.net` once. They'll see "No access", and their account shows up under **Recently turned away** on your Access tab. Click **Add as Partner** or **Add as Roommate**.

**Make it an app on iPhone:** open the dashboard in Safari, tap **Share**, then **Add to Home Screen**. It gets a heart icon named "Ticker" and opens full screen. Tailscale has to be on for it to load.

### Auto-update from GitHub

The Pi keeps itself on the latest `master`. Every 5 minutes a systemd timer (`ledticker-update.timer`) runs `deploy/update.sh`, which checks GitHub for a new commit. When it finds one, it pulls it, installs, runs the tests and restarts the ticker. Work on feature branches and merge into `master` when they're ready: **merging is deploying**. Nothing is pushed to the Pi and no ports are opened. The Pi only reads from GitHub.

- **Tests fail:** the Pi rolls back to the previous commit and keeps showing the old version. It doesn't retry that commit; push a fix and it tries again.
- **App doesn't come back up after the restart:** same rollback.
- **Tracked files edited on the Pi, or a different branch checked out there:** it skips the update and logs why, so nothing you did on the Pi is overwritten. `git checkout master` resumes updates.
- **`deploy/ledticker.service`, `setup-pi.sh` or the network watchdog files changed:** the update logs a note to re-run `bash deploy/setup-pi.sh`, since installed system files aren't replaced automatically.

### Network watchdog

If the router is unplugged or Wi-Fi drops, the Pi normally rejoins by itself, but its Wi-Fi chip can stay stuck until a reboot. `deploy/setup-pi.sh` installs a watchdog (`ledticker-netwatch.timer`, every minute) that handles this: after 3 minutes offline it restarts Wi-Fi, and keeps trying every 3 minutes; after 15 minutes offline it reboots the Pi, once per outage, so a router that is really down doesn't cause a reboot loop. Logs: `journalctl -t ledticker-netwatch`.

**Private repo: give the Pi a read-only deploy key** (once, before cloning):

```bash
ssh-keygen -t ed25519 -C ledticker-pi -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

On GitHub, open the repo's **Settings → Deploy keys → Add deploy key**, paste the key and leave **Allow write access** off. Then clone with the SSH address (answer `yes` to the GitHub fingerprint prompt the first time):

```bash
git clone git@github.com:mertz4/LedTickerPro.git
```

Already cloned with `https://`? Switch it: `git remote set-url origin git@github.com:mertz4/LedTickerPro.git`.

| | |
|---|---|
| Update now | `sudo systemctl start ledticker-update` |
| Update log | `journalctl -u ledticker-update` |
| Next check | `systemctl list-timers ledticker-update.timer` |
| Pause auto-update | `sudo systemctl disable --now ledticker-update.timer` |

### Notifications

On the iPhone Home Screen app, open the **Notifications** card, pick topics and tap **Turn on notifications**. Topics are fantasy trades, big plays (Texans scores and rival stinkers too), the eat-out counter, severe weather alerts, and love taps (superusers only). Each person chooses their own, per device. iPhone needs iOS 16.4 or later, and notifications only work from the Home Screen app, not a Safari tab. The Pi signs messages with a key it creates once (`data/vapid.pem`). Set `[push] contact` in `config.toml` to override the contact address sent with them, which defaults to the first superuser's email.

### Love tap

The Partner role (or anyone you grant it to) gets a Love tap card: an optional message (up to 60 characters) and an animation style. **Flourish** is a wreath of hearts blooming around the message. **Burst** is one heart that swells and explodes. **Rain** is hearts falling. **Heartbeat** is a big beating heart. It takes over the whole display for `[love] seconds`, then everything resumes where it was. If the display is off, it waits and plays when the display turns back on. Defaults are in `config.toml` under `[love]`: the message is "bab misses you" and the style is Burst.

### Touchpad, pong and transitions

The **Touchpad & pong** card (the "Touchpad & pong" permission; partners, roommates and superusers have it) sends live input over the same WebSocket that streams the preview. Drag on the pad and your finger leaves a glowing trail and sparks on top of whatever is showing; several fingers and several phones work at once. **Play pong** takes over the display: the first person plays the computer, a second person takes the computer's place and the match restarts, first to 7. The game ends when everyone leaves or nobody touches a paddle for a minute.

Slides no longer cut: each change plays a transition (push, dissolve, split, wipe, melt, iris, blinds, cycling in turn), takeovers flash in, and a number that just updated (a score, a price) flashes white and settles. `[render] transition`, `transition_seconds` and `glint` in `config.toml` change or turn these off.

### AirPlay screen mirroring

Off by default. To turn it on, set `[airplay] enabled = true` in `config.toml` on the Pi and run `bash deploy/setup-pi.sh` again: it installs the receiver ([UxPlay](https://github.com/FDH2/UxPlay)) and restarts the ticker. Then on an iPhone on the **same Wi-Fi** as the Pi: Control Centre, Screen Mirroring, **LED Ticker**. The phone's screen takes over the panels until you stop mirroring; **Next** on the dashboard hides it straight away.

- The panels are 256x128, so video, photos and games look right and text doesn't. `fit = "fill"` crops the picture to cover the panels, `"fit"` shows all of it with bars, and `rotate = "left"` suits a phone held upright.
- It doesn't work over Tailscale (AirPlay only finds devices on the local network), protected video (Netflix and the like) shows black, and there is no sound.
- Anyone on the Wi-Fi can mirror. Add `extra_args = ["-pin"]` to require a code, which UxPlay prints in `journalctl -u ledticker`.
- Memory: nothing runs while it's off. While on, the receiver asks the phone for a small picture (`size`), keeps at most two frames queued, and is restarted if it passes `max_ram_mb`. The dashboard's `/api/state` reports its current use under `airplay.ram_mb`.

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
