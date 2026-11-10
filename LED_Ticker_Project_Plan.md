# LED Ticker — Project Vision, Features, and Technical Plan

**Status:** Planning  
**Last updated:** November 5, 2026  
**Purpose:** Project brief for a Raspberry Pi–powered 256×128 RGB LED ticker, written so it can be given to an AI coding assistant as context.  
**Scope:** The LED display only. Home-server work, live TV/DIRECTV and general "subscription integrations" are **out of scope** (see §15).  
**Important:** This is a living brief. None of the hardware or integrations listed here have been tested yet.

---

## 1. Project vision

Build a network-connected **RGB LED ticker** that shows a specific set of things:

- **Sports scores** for the NFL, college football, the NBA, college basketball and MLB, from ESPN's public endpoints.
- **My ESPN fantasy football league:** live matchup scores, standings and roster status.
- **Weather** from the National Weather Service (current conditions, forecast, severe alerts).
- **Apple Music now playing** from my iPhone. *Future possibility, not needed right now (see §6.4).*
- **Stocks / crypto** watchlist.
- **Clock and news headlines.**
- **Images, GIFs and pixel art** uploaded from my phone.

It is controlled from a **phone-friendly web dashboard** hosted on the Pi. The Pi is a **dedicated ticker host**.

### Guiding goals

1. **Specific first:** Build the sources above well before adding a generic plugin system.
2. **Responsive:** Smooth scrolling and animation, with no stutter caused by network calls.
3. **Remotely controllable:** Change modes and settings from a phone.
4. **Self-hosted and free:** Use only free APIs and run without a paid hosting subscription.
5. **Maintainable:** Keep the web API, data fetchers, scheduler and renderer separate.
6. **Reliable:** Boot cleanly, recover from faults, and keep the last good data when an API is down.

---

## 2. Physical display

### 2×2 array (four panels)

| Item | Plan / information |
|---|---|
| Panel vendor | LED Display Supply |
| Product link | https://www.leddisplaysupply.com/product/indoor-p2-led-screen-module-256x128mm/ |
| Panel type | Indoor P2 RGB LED module |
| Individual physical size | 256 × 128 mm |
| Individual resolution | 128 × 64 pixels |
| Quantity | 4 modules |
| Layout | 2 across × 2 high |
| **Combined resolution** | **256 × 128 pixels** (32,768 total) |
| Combined physical size | 512 × 256 mm ≈ 20.2 × 10.1 in |
| Aspect ratio | 2:1 |

```text
┌────────────────┬────────────────┐
│  Panel 1       │  Panel 2       │
│  128 × 64      │  128 × 64      │
├────────────────┼────────────────┤
│  Panel 3       │  Panel 4       │
│  128 × 64      │  128 × 64      │
└────────────────┴────────────────┘
Overall: 256 × 128 pixels
```

### Delivered panels (confirmed from photos)

| Item | Confirmed |
|---|---|
| PCB silkscreen | **P2-1515-128X64-32S-S2**: 128 × 64, 1/32 scan, HUB75E |
| Sticker | J-P2-20241130-6373-32019 |
| Column driver | **FM6373** (chip UB4 marked "6373", lot NKB387000B). An S-PWM driver. |
| Row driver | **DP32019B** (chip Q1, date code 4H2802), direct row selection |
| HUB75E pinout | Printed on the board. **E is on pin 8**, so set the Triple Bonnet's E-line jumper to pin 8. |
| Data direction | In = **JIN**, out = **JOUT**. Arrows on the PCB show the direction; mount all 4 panels the same way. |
| Power connector | POWER1, 4-pin: GND, GND, VCC, VCC |
| Data ribbons | 16-conductor 28 AWG UL2651 (data only) |
| Mounting | 8 brass threaded inserts per panel frame for mount posts/screws (likely M3, unconfirmed) |

Upstream `rpi-rgb-led-matrix` does **not** support the FM6373. See §3 for the driver.

---

## 3. Raspberry Pi and controller

### Core computer

- **Pi 4 or Pi 5**, 4 GB is plenty for a dedicated ticker. A **Pi 4 is preferred**: on a Pi 5 the SPWM fork (below) runs only in RIO mode (`--led-rp1-pio=0`).
- A 32 GB microSD is enough. Media is small at 256×128.
- Ethernet is preferred. Wi-Fi works.

### LED library

Upstream **`hzeller/rpi-rgb-led-matrix`** (https://github.com/hzeller/rpi-rgb-led-matrix) does **not** support the FM6373 driver on these panels. Use the experimental fork **`kingdo9/rpi-rgb-led-matrix_pwm_experiment`** (https://github.com/kingdo9/rpi-rgb-led-matrix_pwm_experiment), which lists FM6373 + DP32019B direct row selection.

Bring-up flags:

```bash
--led-panel-type=fm6373 --led-spwm-row-addr-type=0 --led-rows=64 --led-cols=128
--led-chain=2 --led-parallel=2 --led-gpio-mapping=regular --led-slowdown-gpio=2
```

- Do **not** use `--led-pixel-mapper=StackToRow`. The two parallel chains should stay stacked top/bottom for the 2×2.
- If the image needs tuning, follow the fork's SPWM tuning guide (`spwm.md`).
- **Bring-up order:** the fork's demo (`sudo examples-api-use/demo -D0` + the flags above) on **one** panel first, then all four.
- **TODO:** check whether the fork's Python bindings expose `panel_type` and the `spwm_*` options. If they don't, the display layer needs a small patch or a C++ shim. Don't implement yet.

### Controller options

**Option A: Adafruit RGB Matrix Bonnet (single output).** All four panels go on one chain of 32,768 pixels. It's simpler, but leaves less refresh headroom.

**Option B: multi-output adapter (preferred).** Run two chains of two panels each (16,384 pixels per chain) for better refresh headroom. Candidates are the **Adafruit Triple LED Matrix Bonnet** and the **ElectroDragon active-3**. Check E-line support, jumpers and Pi 4/5 support. For these panels the Triple Bonnet's E-line jumper goes on **pin 8**.

Bring-up flags for the FM6373 panels are under **LED library** above.

**Option C: commercial LED controller.** Only if driving the panels directly from the Pi turns out to be inadequate.

### Refresh rate vs. content FPS

The **refresh rate** (Hz) is how often the panels are electrically refreshed. The **content FPS** is how often a new frame is drawn. The initial target is **~30 FPS content**. Benchmark both.

---

## 4. Power and wiring

### Wiring layout

- **AC side:** grounded 3-prong cord → Mean Well L / N / ground with fork terminals. The ground wire **must** be connected. Add strain relief and keep the Mean Well's AC terminal cover closed.
- **Optional IEC inlet (decision pending):** a fused, switched IEC C14 inlet on the frame. If used, the existing cord becomes the internal jumper (1/4" female quick-disconnects on the inlet side).
- **5 V side:** blade fuse block with a negative bus. Mean Well V+ → block input, V- → negative bus (12 AWG for these two short leads). One fused output per panel harness. Negative wires are **never** fused.
- **Panel harnesses:** each red/black lead splits into **two** 4-pin plugs, so one harness powers 2 panels (up to 14.4 A). The ends are already insulated forks marked "2-4" / "16-14" (M4 / #8 stud, for 16-14 AWG wire). The wire is likely 16 AWG (confirm from the insulation print). No DC-side terminals need to be bought.
- **Fuse size per harness:** **15 A** ATO/ATC if the harness wire is 16 AWG or thicker. Otherwise **10 A**, with brightness capped at about 60-70%.
- **HUB75 ribbons carry data only:** bonnet out 1 → panel A → panel B, bonnet out 2 → panel C → panel D (4 ribbons total).
- **Pi power:** the Triple Bonnet has **no** power input and can't power the Pi. The Pi gets its own USB-C supply (official: 27 W for a Pi 5, 15 W for a Pi 4). That's two wall plugs in total; put both on one power strip.

### Turning it off

- Day to day, the software schedule and the power toggle blank the display.
- To cut power fully, shut the Pi down first (`sudo shutdown now`), or keep the Pi on a separate outlet.
- **Hardware brightness cap (not implemented yet):** brightness is clamped to 0-100 in `Engine.output()` (`src/ledticker/render/engine.py`), which every frame passes through, and validated in the manual brightness endpoint (`src/ledticker/web/app.py`). A hardware maximum (e.g. 60-70% with 10 A fuses) would go in `Engine.output()` as a config value, scaling every brightness the schedule or dashboard sets.

### Frame (plan)

- **2020 aluminum extrusion, two layers.** Layer 1 is a 512 × 256 mm frame holding the panels, with cross bars where the panel mounting holes are (measure first).
- The hardware (Pi, fuse block, Mean Well) sits on thin plates bolted to the back of layer 1 with M5 T-nuts.
- 60-70 mm standoff legs, then layer 2 (a plain rectangle) hangs on the wall on a French cleat. Total depth is about 115-125 mm.
- Leave the top and bottom open for airflow. Put the fuse block and the microSD slot near an open edge. Leave slack in all cables.
- If the panel mounts are magnetic, add steel strips on the extrusion, or use M3 screws in the brass inserts instead.

### Shopping list

- [ ] microSD card, 32 GB, A1/A2 (**micro**SD, not full-size)
- [ ] Pi USB-C power supply (once the Pi model is chosen)
- [ ] Blade fuse block with negative bus + 15 A (or 10 A) ATO/ATC fuses and spares + ~1 ft 12 AWG red/black
- [ ] Fork terminals for the AC cord only (18-14 AWG, sized to the Mean Well screws, TBD)
- [ ] Zip ties / cable clamp for strain relief
- [ ] Ratcheting crimper and multimeter (if not owned)
- [ ] Optional: fused, switched IEC C14 inlet + C13 cord
- [ ] 2020 extrusion, corner brackets, M5 T-nuts, mounting plates, M2.5 standoffs for the Pi

### First power-up checklist

- Do all wiring unplugged, then tug-test every crimp.
- Set the Mean Well to 5.0-5.1 V with a meter before connecting the panels.
- Set the Triple Bonnet's E-line jumper to **pin 8** before powering on.
- Start at about 30% brightness and check that wires and connectors aren't warm after a few minutes.
- Don't seal the Mean Well in a closed box. It needs airflow.

---

## 5. Phone web dashboard

A mobile-first **PWA** served from the Pi.

### Essential controls

- Display on/off (software blanking) and brightness, with a night schedule.
- Mode select: **Dashboard rotation, Sports, Fantasy, Weather, Music, Stocks, Clock, Message, Media**.
- Send a custom scrolling message (font, color, speed, direction).
- Upload images and GIFs, browse the media library and build playlists.
- **Live 256×128 preview** in the browser.
- Configure each data source: teams, league ID, location, watchlist and refresh rates.
- Status: online, active mode, CPU temperature, **last successful fetch per data source**, and errors.
- Restart the display process (authenticated).

### Later

- A layout editor (full screen vs. split regions).
- Saved presets and themes.
- Priority and interrupt rules (see §9).

### Access

- **At home:** `http://<pi-ip>:8000` or an mDNS hostname.
- **Away:** **Tailscale** on the Pi and the phone (free Personal plan). Don't open any router ports.
- Use app-level auth even on Tailscale. API secrets (ESPN cookies, API keys) stay on the Pi and never go to the browser.

---

## 6. Data sources (specific)

Each source runs as an **independent fetcher**. It polls its API, normalizes the data into a small internal model, caches the last good result, and emits events. Renderers only ever read from that cache.

### 6.1 Sports scores: ESPN public site API

These are **unofficial, undocumented** JSON endpoints. They need no key, but they have no SLA and can change. Wrap them behind one adapter module so a breaking change only touches one file.

Scoreboard endpoints (`https://site.api.espn.com/apis/site/v2/sports/...`):

| League | Path | Notes |
|---|---|---|
| NFL | `football/nfl/scoreboard` | `?week=N&seasontype=2` (2 = regular season, 3 = postseason) |
| College football | `football/college-football/scoreboard` | Defaults to a featured subset. Use `?groups=80` for all FBS games, or filter to my teams |
| NBA | `basketball/nba/scoreboard` | `?dates=YYYYMMDD` |
| Men's college basketball | `basketball/mens-college-basketball/scoreboard` | `?groups=50` for all D-I. Optionally filter by conference |
| MLB | `baseball/mlb/scoreboard` | `?dates=YYYYMMDD`. Off-season Nov–Mar, so handle empty days |
| College baseball | `baseball/college-baseball/scoreboard` | `?dates=YYYYMMDD`. For Texas A&M. Season is Feb–June |

**My teams.** All IDs below were verified against ESPN's `/teams/{id}` endpoint on Nov 5, 2026. ESPN numbers college baseball teams separately, so A&M baseball is **123**, not 245.

| Team | League path | ESPN ID |
|---|---|---|
| Houston Texans | `football/nfl` | 34 |
| Houston Rockets | `basketball/nba` | 10 |
| Houston Astros | `baseball/mlb` | 18 |
| Texas A&M football, men's basketball | `football/college-football`, `basketball/mens-college-basketball` | 245 |
| Texas A&M baseball | `baseball/college-baseball` | 123 |

**College football "big games" filter.** Texas A&M games always show. Beyond those, show only the most important games of the week, which I'm not following team by team:

1. Pull `?groups=80` (all FBS). Each competitor has `curatedRank.current`, where 99 means unranked.
2. Rank the games:
   - Both teams in the AP Top 25 scores highest, with a bonus when both are in the top 10.
   - SEC games with a ranked team come next.
   - A ranked team in a close game late (4th quarter, within 8 points) gets bumped up as an upset alert.
   - Conference championships, CFP and bowl games always qualify.
3. Show the top ~5–8 games in the CFB rotation and drop the rest.
4. ESPN's default scoreboard (no `groups` parameter) is already a featured list, which makes a good fallback.

Use the same idea for men's college basketball: Texas A&M plus ranked-vs-ranked games, so the ticker isn't flooded by roughly 150 D-I games a night.

Other useful endpoints:

- Team info and logo: `.../{sport}/{league}/teams/{teamId}`
- Team schedule: `.../{sport}/{league}/teams/{teamId}/schedule`
- Game detail (play-by-play, box score): `.../{sport}/{league}/summary?event={eventId}`

Fields to use from the scoreboard: team abbreviations, colors (`team.color` / `alternateColor`), logo URLs, score, `status.type.state` (`pre` / `in` / `post`), clock, period, possession and down-and-distance for football (`situation`), inning and outs for baseball, rankings for college teams, and the broadcast network.

**Polling:**

- Every **~20–30 s** while a followed game is `in` progress.
- Every **5–15 min** otherwise.
- Once a day for schedules.

**Display ideas (256×128):**

- **Scoreboard card:** two team logos (downscaled, cached locally), large scores, clock/period, possession indicator, and down-and-distance.
- **Multi-game ticker:** a bottom-row scroll of every game in a league.
- **Countdown** to the next game for my teams.
- **Score-change alert:** flash in team colors, then show the scoring play.

### 6.2 Fantasy football: ESPN Fantasy (my league)

Also **unofficial**. Base URL:

```text
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{leagueId}
```

Add `view` query parameters (repeatable), for example:

- `mTeam`: team names, owners, records.
- `mMatchup` and `mMatchupScore`: weekly matchups and live scores. Combine with `scoringPeriodId={week}`.
- `mRoster`: rosters, lineup slots, injury status.
- `mBoxscore`: per-player points for a matchup.
- `mStandings` and `mSettings`: standings and league settings.

**Auth:** A **public** league needs only the league ID. A **private** league also needs two cookies from a logged-in browser session, `espn_s2` and `SWID`, sent as a `Cookie` header. Store them only in the Pi's secrets file. They expire periodically, so the dashboard should show a clear "ESPN cookie expired, re-paste it" error.

**How to get or refresh the cookies:**

1. In a normal (not incognito) desktop browser, go to **fantasy.espn.com** and log in.
2. Press **F12** to open DevTools.
3. Open the **Application** tab (Chrome/Edge) or the **Storage** tab (Firefox).
4. Go to **Cookies** → `https://fantasy.espn.com`.
5. Find **`espn_s2`** (a long string) and **`SWID`** (`{...}`, including the braces), and copy their values.
6. Paste them into the ticker's settings page, or into `secrets.env` on the Pi.
7. Closing the tab afterward is fine. **Don't click "Log out"** on ESPN, because that may invalidate the cookie.

`SWID` almost never changes. `espn_s2` usually lasts months, roughly a season, and is the one you'll usually need to replace.

**Shortcut:** The Python library [`espn-api`](https://github.com/cwendt94/espn-api) already wraps these endpoints (`League(league_id, year, espn_s2, swid)`). Try it first and fall back to raw requests if needed.

**Polling:**

- Every **~60 s** during NFL game windows (Thu night, Sun, Mon night).
- Every **30–60 min** otherwise.

**Display ideas:**

- **My matchup:** my team vs. opponent, live score, projected score, and a win-probability bar if available.
- **League scoreboard:** every matchup this week, scrolling.
- **Standings** table.
- **Player alerts:** one of my starters scores, or a starter is listed OUT / on bye in my lineup.
- **Close-game alert:** late on Monday night with the matchup within X points.

### 6.3 Weather: National Weather Service API

Free, official, **no key**. Base URL: `https://api.weather.gov`.

**Required:** Every request must send a `User-Agent` header that identifies the app and a contact, e.g. `LedTicker/0.1 (contact: <your email or site>)`. Put the contact in config. Don't hard-code it.

Flow:

1. `GET /points/{lat},{lon}` once, then cache the result. It returns the URLs for `forecast`, `forecastHourly`, `observationStations` and the grid office/x/y.
2. `GET` the **forecast** (12-hour periods) and **forecastHourly** URLs.
3. Current conditions: `GET /stations/{stationId}/observations/latest`, using the nearest station from step 1. Values are metric (°C, km/h), so convert them.
4. Alerts: `GET /alerts/active?point={lat},{lon}`.

**Polling:**

- Forecast every **30–60 min**.
- Observations every **10–15 min**.
- **Alerts every 2–5 min.**

Respect `Cache-Control` and retry 5xx responses with backoff, since NWS occasionally returns them.

**Display ideas:**

- Current temp, conditions icon (custom pixel-art set, see §7.1A), high/low, wind.
- Hourly strip (next 12 h temperatures and precipitation chance).
- **Severe alert interrupt:** a red/orange banner with the event name (e.g. "Tornado Warning") that repeats until the alert expires. Warnings get higher priority than watches.

### 6.4 Apple Music now playing (iPhone): future possibility

> **Status (Nov 5, 2026): deferred.** Not needed right now. The Last.fm fetcher is already built (`sources/lastfm.py`) but is switched off with `music.enabled = false`. To turn it on later: create a Last.fm account, install an iPhone scrobbler app (one that logs each song to Last.fm), add `LASTFM_API_KEY` to `secrets.env`, set `music.lastfm_user`, and set `enabled = true`. Then measure how quickly song changes show up.

Apple's web APIs **do not expose live playback state from the iPhone Music app**. Approaches, in order:

**A. iPhone scrobbler → Last.fm → Pi (try first).** Use an iOS app that scrobbles Apple Music to Last.fm. The Pi polls `user.getRecentTracks` (free API key), and the first track having `@attr.nowplaying="true"` is the current song. Artwork comes from the Last.fm response or an iTunes Search API lookup. **Measure the real latency and reliability** with the phone locked and unlocked.

**B. Pi as an AirPlay receiver (Shairport Sync).** This gives real metadata, artwork **and audio**, but only while audio is routed to the Pi, not to AirPods.

**C. Native iOS companion app.** Uncertain because of iOS background limits. Only revisit it if A and B aren't good enough.

**Widget:** album art on the left, then title/artist/album (scrolling if long). Show progress only if the data is reliable. Switch to music mode automatically and revert after X minutes idle.

**Visualizer:** A real FFT/VU visualizer needs actual audio, which only approach B provides. Without it, any bouncing bars are a **simulated** effect and should be labeled that way in settings.

### 6.5 Stocks / crypto

Watchlist ticker showing symbol, price, change and %. Use green/red color.

| Data | Candidate source | Notes |
|---|---|---|
| Crypto | **CoinGecko** `/simple/price` | Free, generous limits; free demo key recommended |
| Stocks | **Finnhub** `/quote` | Free key, ~60 calls/min; real-time-ish US quotes |
| Fallback | Alpha Vantage | Free tier is very limited (~25 calls/day) |

**Watchlist:**

- **Core:** SPY, BRK.B, VGT.
- **Major stocks (default, editable):** AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA.

Use `BRK.B` (with a period) when asking Finnhub for Berkshire, and check that ETF quotes like SPY and VGT come back on the free tier.

**Polling:** Stocks every **1–2 min** during market hours only (9:30–16:00 ET, weekdays). Crypto every **1–5 min**, all the time.

Optional alerts trigger when a symbol moves ±X% on the day.

### 6.6 Clock and news

- **Clock/date:** local time, with configurable big-digit fonts. Can share the screen with weather.
- **News:** Pull from RSS feeds (configurable list, e.g. AP and ESPN headlines) as a scrolling headline ticker. Poll every **15–30 min**.

---

## 7. Images, GIFs and pixel art

**Inputs:**

1. Upload from phone (PNG/JPG/WebP/GIF).
2. An allowed image URL. Restrict fetching to prevent SSRF.
3. The local library on the SD card.

**Pipeline:** upload → validate type/size → decode (Pillow) → fit to 256×128 or a widget region → pre-render frames → cache → renderer.

- **Fit modes:** contain (letterbox), cover (crop), stretch.
- **Pixel art:** nearest-neighbor scaling only, so the art stays crisp.
- **GIFs:** respect per-frame durations, with loop control and playlists with timed rotation.
- **Pixel assets the project needs:** weather icons, small team logos (cached from ESPN and downscaled), and fonts.

### 7.1 Graphics assets (decided Nov 5, 2026)

Three kinds of graphics. Each one is designed for, or reduced to, actual LED pixel sizes.

#### A. Custom pixel-art weather icons

NWS icons look muddy when shrunk, so the ticker uses its **own hand-drawn pixel-art set**.

- **Sizes:**
  - **16×16** for inline use in the ticker line or a corner widget.
  - **32×32** for the main weather card.
- **Day and night variants** where it matters, e.g. sun vs. moon, partly sunny vs. partly cloudy at night.
- **Light animation** of 2–4 frames on selected icons: rain falling, snow drifting, a lightning flash, sun rays pulsing, fog sliding. Animation is optional per icon and can be turned off globally.
- **Stored as code, not image files.** Each icon is a small palette plus an ASCII pixel grid in `assets/weather_icons.py`, so icons are easy to edit, review in git and recolor.
- **Mapping:** the NWS forecast `icon` URL carries a condition code (`.../icons/land/day/tsra_hi,40`), which maps to our icon. `shortForecast` text is the fallback.

| Our icon | NWS condition codes |
|---|---|
| clear (sun / moon) | `skc`, `hot` (sun), `cold` (with a cold tint) |
| partly cloudy | `few`, `sct` |
| mostly cloudy | `bkn` |
| overcast | `ovc` |
| windy | `wind_skc`, `wind_few`, `wind_sct`, `wind_bkn`, `wind_ovc` |
| rain | `rain`, `rain_showers`, `rain_showers_hi` |
| thunderstorm | `tsra`, `tsra_sct`, `tsra_hi` |
| snow | `snow`, `blizzard` |
| sleet / freezing rain | `sleet`, `fzra`, `rain_snow`, `rain_sleet`, `snow_sleet`, `rain_fzra`, `snow_fzra` |
| fog / haze | `fog`, `haze`, `smoke`, `dust` |
| severe | `tornado`, `hurricane`, `tropical_storm` (paired with the alert banner) |

- **Acceptance:** every NWS code maps to an icon (unknown codes fall back to "cloudy"). Each icon must still be recognizable when viewed at 1:1 LED scale in the browser preview.

#### B. Branded ticker badges (stocks and crypto)

Every symbol in the stock/crypto ticker gets a **branded text badge**: the symbol in bold pixel type on a rounded tile in the brand's colors, with a logo icon beside it when one exists.

```text
 ┌──────┐                ┌───────┐
 │ AAPL │ 344.17 ▼1.53%   │ SPY   │ 733.00 ▼4.41%   ...
 └──────┘                └───────┘
  logo + badge             badge only (ETFs have no logo)
```

- **Logos:**
  - Stocks use Finnhub `/stock/profile2` (PNG, free tier).
  - Crypto uses CoinGecko coin images.
  - ETFs (SPY, VGT) have **no logo** from Finnhub, so they get the badge only.
- **Colors:** each symbol has a configurable background and text color in `config.toml`. Default colors are pulled from the logo's dominant color, and any symbol can be overridden:

  ```toml
  [markets.badges]
  SPY   = { bg = "#1B3A6B", fg = "#FFFFFF" }
  BRK.B = { bg = "#00205B", fg = "#FFFFFF" }
  VGT   = { bg = "#96151D", fg = "#FFFFFF" }
  ```

  These colors are placeholders; pick whatever looks right on the LEDs.
- **Price movement:** shown in green/red with ▲/▼ next to the badge. Optionally flash the badge border on a big move (±X%).
- **Fallback:** a symbol with no logo and no configured colors gets a neutral gray badge, so the ticker never shows a blank.

#### C. Logo image cache (sports, fantasy, stocks, crypto)

| Image | Source | Notes |
|---|---|---|
| NFL / NBA / college team logos | ESPN `team.logo` (PNG) | ESPN's resizer can pre-shrink (`/combiner/i?img=...&w=64&h=64`) |
| Fantasy team logos | ESPN Fantasy `team.logo` | **SVG**, so it must be rasterized (e.g. `resvg`) |
| Player headshots | `a.espncdn.com/i/headshots/nfl/players/full/{playerId}.png` | Fantasy player ID equals the ESPN athlete ID. Use sparingly (e.g. a "player of the week" moment) |
| D/ST | NFL team logo | |
| Company logos | Finnhub `profile2.logo` | Not available for ETFs |
| Crypto icons | CoinGecko `image` | |

- Download each image **once**, store it on the Pi (`data/images/`), and re-check rarely (daily) or when its URL changes, e.g. a fantasy owner uploads a new logo.
- Pre-render **16 / 24 / 32 / 48 px** versions with high-quality downscaling, plus a sharpening pass and optional 1-px dark outline so logos pop against black.
- The renderer only reads from the local cache and never downloads during a frame.
- If an image fails, fall back to a text badge (team abbreviation in team colors, from ESPN `color` / `alternateColor`).

---

## 8. Scrolling and animation

- Direction, speed in **pixels per second** (`position += velocity * dt`), loop vs. once, and pause at the ends.
- Transitions: fade, slide, wipe, scroll-and-pause.
- Font, color, size, shadow, spacing and alignment.
- Draw everything into an offscreen buffer and swap with vsync (`SwapOnVSync`).
- A **single central render loop** owns the panels. Widgets never touch GPIO.

---

## 9. Automations and priorities

| Trigger | Action | Priority |
|---|---|---|
| NWS **warning** for my point | Alert banner until it expires | Critical |
| My team scores / game goes final | Flash and show the score card for ~15 s | High |
| My team's game starts | Switch to the live scoreboard | High |
| A fantasy starter scores big / my matchup is close late | Fantasy alert card | High |
| A fantasy starter is OUT in my lineup before kickoff | Warning card | Medium |
| Apple Music starts playing (if enabled later) | Now Playing mode | Medium |
| Music idle for X min | Return to the previous mode | — |
| NWS **watch/advisory** | Small banner | Medium |
| Stock moves ±X% | Brief ticker highlight | Low |
| Night schedule | Dim, clock only | — |
| Manual button in the phone app | Override immediately | Manual |

**Rules:**

- Higher priority interrupts lower priority. An interrupt has a duration, then the display returns to the previous mode.
- Debounce and rate-limit alerts.
- A do-not-disturb mode is available.
- When a source is down, show its last good data marked as stale.

---

## 10. Software architecture

| Component | Technology |
|---|---|
| Frontend | React + Tailwind, mobile-first PWA |
| Backend API | Python + FastAPI |
| Live updates / preview | WebSockets |
| Settings & cache | SQLite, plus a `secrets.env` for keys/cookies |
| HTTP fetching | `httpx` (async) with per-source backoff |
| Imaging / fonts | Pillow (BDF/pixel fonts) |
| Renderer | Python bindings for `rpi-rgb-led-matrix` |
| Audio (later, AirPlay only) | NumPy FFT/RMS |
| Startup | systemd services |
| Remote access | Tailscale |

```text
        PHONE (PWA)
            │  HTTP / WebSocket
   ┌────────▼─────────┐
   │ FastAPI backend  │──── SQLite (settings, media, cache)
   └────────┬─────────┘
            │
  ┌─────────┴──────────────────────────────────────────┐
  │                 Data fetchers                      │
  │  ESPN scores │ ESPN fantasy │ NWS │ Last.fm        │
  │  Finnhub/CoinGecko │ RSS                           │
  └─────────┬──────────────────────────────────────────┘
            ▼
    Normalized cache + events
            ▼
   Scheduler / priority engine
            ▼
   Layout manager → Central renderer (256×128 frames)
            │                      │
            ▼                      ▼
   rpi-rgb-led-matrix        Browser preview
            ▼
      2×2 LED panels
```

### Separation rules

- **Fetchers** never block rendering. Each one has its own interval, timeout, backoff and "last success" timestamp.
- **Normalized models**, e.g. `Game`, `FantasyMatchup`, `WeatherNow`, `WeatherAlert`, `NowPlaying`, `Quote` and `Headline`, so renderers don't parse raw API JSON.
- **The renderer** owns the hardware and runs at a steady cadence. It outputs the same frames to the LEDs and to the browser preview.
- **A hardware-free mode** renders to the preview only, so development can happen on a laptop.

### Illustrative API

```http
GET    /api/status                 # incl. per-source health
POST   /api/display/mode
PATCH  /api/display/settings
POST   /api/message
POST   /api/media/upload
GET    /api/media
POST   /api/media/{id}/play
GET    /api/sources                # config for sports/fantasy/weather/...
PATCH  /api/sources/{name}
WS     /ws/preview
```

### Security

- Auth on all control endpoints.
- Secrets (ESPN `espn_s2`/`SWID`, Finnhub/CoinGecko/Last.fm keys) live in `secrets.env` on the Pi, are git-ignored, and are never sent to the frontend.
- Validate uploads, limit their size and block SSRF.
- Expose the dashboard only on the LAN and over Tailscale.

---

## 11. Configuration (fill in)

```yaml
location:
  name: "College Station, TX"
  lat: 30.6280
  lon: -96.3344
  timezone: "America/Chicago"
  # NWS: grid HGX 26,133; nearest obs station KCLL (Easterwood Airport). Verified 2026-11-05

nws:
  user_agent: "LedTicker/0.1 (contact: TODO)"

sports:
  nfl:     { teams: [34],  show_all_games: true }        # Texans
  nba:     { teams: [10] }                               # Rockets
  mlb:     { teams: [18] }                               # Astros
  cfb:     { teams: [245], groups: 80, big_games: true, max_featured: 8 }  # Texas A&M + big games
  ncaab:   { teams: [245], groups: 50, big_games: true } # Texas A&M men's basketball
  college_baseball: { teams: [123] }                     # Texas A&M baseball (different ID!)
  # team IDs verified against ESPN /teams/{id} on 2026-11-05

fantasy:
  platform: espn
  league_id: (in secrets.env as ESPN_LEAGUE_ID)
  season: 2026
  my_team_id: auto       # found by matching SWID to team owners; override only if needed
  private: true          # if true, espn_s2 + SWID in secrets.env

music:
  lastfm_user: TODO      # LASTFM_API_KEY in secrets.env

markets:
  stocks: [SPY, BRK.B, VGT, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA]  # FINNHUB_API_KEY in secrets.env
  crypto: [bitcoin, ethereum]


news:
  rss: [TODO]
```

---

## 12. Roadmap

### Phase 0: Verify hardware

- [ ] Pick the Pi model.
- [x] Confirm the panel driver IC, scan rate, addressing and connectors. FM6373 + DP32019B, 1/32 scan, HUB75E with E on pin 8 (§2).
- [ ] Build the `kingdo9/rpi-rgb-led-matrix_pwm_experiment` fork and run its demo on one panel, then all four (§3).
- [ ] Check whether the fork's Python bindings expose `panel_type` and the `spwm_*` options (§3).
- [ ] Choose the adapter (multi-output preferred).
- [ ] Settle the power supply, wiring and fusing. Design decided (see §4): one fused output per 2-panel harness on a blade fuse block. The fuse block, fuses and 12 AWG leads still need to be bought and installed.
- [ ] Run an RGB test on all four panels and check the mapping.
- [ ] Measure the refresh rate.

### Phase 1: Software MVP (no LEDs needed)

- [x] Backend skeleton and 256×128 browser preview (`python -m ledticker preview`): LED dots, glow, module seams, actual-size view, screen pinning. Done Nov 6, 2026.
- [x] Display geometry is configurable (`[display]` module size and grid). All layouts scale from it.
- [ ] Text rendering, clock, scrolling, custom message.
- [ ] Settings page (mode, speed, color, brightness). Persistent config.

### Phase 2: Core data sources

- [x] **Data collection layer** (`src/ledticker/`): fetchers, normalized models, last-good cache, adaptive scheduler, per-source health. Done Nov 5, 2026.
- [x] **NWS weather:** points lookup, forecast, observations, alerts.
- [x] **ESPN scores:** all six leagues, odds, "big games" filter, my-teams-only filter, season windows.
- [x] **ESPN fantasy:** standings, matchups with live points, my lineup. Cookie expiry is reported as `auth_error`.
- [x] **Stocks (Finnhub) and crypto (CoinGecko).**
- [ ] Per-source health shown on the dashboard.

### Phase 2a: Modes, fantasy extras, props (added Nov 6, 2026)

- [x] **Modes and schedule:** named playlists of full-screen or 4-up slides (one tile per LED module). A weekly schedule picks the mode (Sunday = fantasy + NFL slate; Mon/Tue = 4-up weather / markets / featured NFL game / fantasy), with a manual override and a "Sweep" mode that rotates through everything. Saved custom modes are created and edited in the preview page (`data/modes.json`, validated server-side).
- [x] **Fantasy standings:** seeds, records, streaks, points for, playoff line (from league settings) and games behind.
- [x] **Breaking trade alert:** trades come from the league activity feed, because the transaction log hides other teams' proposals. A new trade interrupts whatever is on screen once, stays in rotation and the ticker for 24 h, and is remembered in `data/seen_alerts.json` so it doesn't re-alert after a restart.
- [x] **Player of the week:** last week's top 3 rostered performers (with headshot, owner, started/benched), the top team score, and this week's live leaders, each sliding in.
- [x] **Odds and records everywhere:** pre-game spread and O/U are remembered and shown during live games (ESPN drops odds at kickoff). Board cells show team records.
- [x] **NFL player props:**
  - DraftKings lines via ESPN's core odds API, tracked against the live box score.
  - Follows the game that matters now: a live primetime game, otherwise live games ranked by my teams and my fantasy starters, otherwise the next game day's slate.
  - Ordered with my fantasy players first, then my team, then the biggest line movers.
  - **Bet counts and popularity are not available** from any free source. Line movement since open is the stand-in for "where the money went".

### Phase 2a+: Custom layouts, calendar schedule, brightness/power (added Nov 6, 2026)

- [x] **Custom layouts:** the display can be split any way except diagonally (a binary split tree: each section is cut top/bottom or left/right, recursively). Each section holds any widget. The page has a visual editor: click to select or split, drag dividers (snap to half and to module seams), size presets, merge. Layouts are saved by name.
- [x] **Size-aware widgets:** each widget picks its full design when its section is big enough and its compact tile design otherwise. The scrolling ticker is also a placeable widget.
- [x] **Calendar schedule:** rules match weekdays, specific dates (e.g. 2026-11-26) or date ranges, with optional time windows including overnight. For each setting (mode, brightness 0-100, power on/off) the first matching rule wins. Defaults are set separately.
- [x] **Manual display control:** power toggle and brightness slider on the page, with "Auto" to return to the schedule. The preview shows true dimming. On the Pi this maps to the matrix brightness and blanking.

### Phase 2b: Graphics assets (§7.1)

- [x] Pixel-art weather icon set: 16/32/48 px, day/night, every NWS code mapped, 4-frame animation. Icons are drawn from shapes in code (`render/icons.py`) rather than ASCII grids.
- [x] Logo image cache: download once (allow-listed hosts only), rasterize fantasy SVGs, LED-size scaling, automatic brightening of dark logos, team-color fallback disc.
- [x] Branded ticker badges: color from config or the logo's dominant color, logo when available, ETFs badge-only.
- [ ] Review every icon, logo and badge at 1:1 LED scale in the browser preview.

### Phase 3: Media

- [ ] Image/GIF upload, fit modes, library, playlists.
- [ ] Nearest-neighbor pixel-art mode.

### Phase 4: More widgets

- [ ] Stocks/crypto ticker screen (uses the branded badges from §7.1).
- [ ] RSS news.

### Phase 5: Apple Music (deferred, future possibility)

- [ ] Prototype the scrobbler → Last.fm flow. Measure latency with the phone locked and unlocked.
- [ ] Now Playing widget with artwork and idle fallback.
- [ ] Optional: Shairport Sync and a real FFT visualizer.

### Phase 6: Automation and remote access

- [ ] Priority/interrupt engine (§9).
- [ ] Night schedule and DND.
- [ ] Tailscale and an authenticated PWA.

### Later (optional)

- [ ] Layout editor and presets.
- [ ] Pixel drawing editor.
- [ ] Mini-games controlled from the phone.

---

## 13. Open questions

1. Pi 4 or Pi 5? A Pi 4 is preferred for the SPWM fork (§3).
2. ~~Controller board / E-line~~ Decided: Adafruit Triple LED Matrix Bonnet, E-line jumper on pin 8 (§2).
3. ~~Panel driver IC and addressing~~ Decided: FM6373 (S-PWM) + DP32019B direct row selection; needs the SPWM fork (§3).
4. Power and mounting, still open:
    - Exact Mean Well model and terminal screw size (needs a label photo).
    - Harness wire gauge (confirm from the insulation print) → 15 A vs 10 A fuses.
    - Mount hardware that came with the panels (magnetic posts vs screws).
    - IEC inlet: yes/no.
    - Wall vs desk mounting.
5. ~~My teams~~ Decided: Texans, Rockets, Astros, and Texas A&M football, men's basketball and baseball. ESPN IDs still need verifying.
6. ~~Weather location~~ Decided: College Station, TX.
7. **ESPN fantasy league ID.** Private league; cookies are saved in `secrets.env`. My team is found automatically from SWID.
8. ~~Stock watchlist~~ Decided (§6.5). Which crypto, if any, beyond BTC/ETH?
9. Which iOS scrobbler app for Last.fm? What latency is acceptable?
10. Should the default view be a full-screen rotation or a split layout (e.g. scores on top, ticker on the bottom)?
11. Private repo during development? What license and permissions are needed before any public release?

---

## 14. Notes for an AI coding assistant

- Start with a **hardware-independent renderer and browser preview**.
- Build **specific, typed fetchers** for ESPN scores, ESPN fantasy and NWS before any generic plugin framework.
- ESPN site and fantasy endpoints are **unofficial**. Isolate them in adapters, handle missing fields defensively, and never crash the renderer on a schema change.
- NWS **requires a User-Agent**. Cache the `/points` lookup.
- Never commit secrets. Read `espn_s2`, `SWID` and API keys from `secrets.env`.
- Network calls run outside the render loop. Use time-based animation and offscreen buffers.
- Treat iPhone Apple Music sync as experimental. Label simulated visualizers as simulated.
- Don't assume the matrix library works with the panels until that's verified.
- Make milestones incremental and testable, each with acceptance criteria.

### First milestone / definition of done

On a laptop with no LED panels: run the backend and frontend, open the dashboard on a phone, send a custom message, change its speed and color, and see in the 256×128 simulated canvas:

- current NWS weather for the configured location,
- a live or most recent ESPN NFL scoreboard, and
- my ESPN fantasy matchup score.

---

## 15. Out of scope (removed from earlier drafts)

- Home-server services: Samba, Jellyfin, Pi-hole, Home Assistant, personal cloud, external SSD media storage.
- Live TV / DIRECTV / HDMI capture / streaming video.
- YouTube, Twitch, Gmail, Discord and GitHub integrations.
- Calendar.
- NHL and soccer scores.

---

## 16. Useful links

- P2 modules: https://www.leddisplaysupply.com/product/indoor-p2-led-screen-module-256x128mm/
- rpi-rgb-led-matrix: https://github.com/hzeller/rpi-rgb-led-matrix
- Adafruit Triple RGB Matrix Bonnet: https://www.adafruit.com/product/6358
- ESPN site API (example): https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard
- ESPN fantasy Python wrapper: https://github.com/cwendt94/espn-api
- NWS API docs: https://www.weather.gov/documentation/services-web-api
- Last.fm API: https://www.last.fm/api
- Shairport Sync: https://github.com/mikebrady/shairport-sync
- Finnhub: https://finnhub.io/docs/api
- CoinGecko: https://docs.coingecko.com/
- Tailscale: https://tailscale.com/
- FastAPI: https://fastapi.tiangolo.com/
- Pillow: https://pillow.readthedocs.io/
