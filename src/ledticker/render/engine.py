"""Central renderer: modes (scheduled playlists of slides), custom layouts (any non-diagonal split), interrupts,
bottom ticker, scheduled brightness / power, frame loop. The same frames feed the browser preview now and the LED
matrix later."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from ..config import Context
from ..display import Geometry
from ..store import Store
from .draw import BLUE, DARK, DIM, GOLD, GRAY, GREEN, ORANGE, RED, WHITE, YELLOW, text, text_center, text_right
from .fonts import font, text_width
from .images import ImageCache
from . import christmas, love, party, photos, tally
from .layout import Region, quad_tree, regions
from .modes import ModeManager
from .screens import RC, LEAGUE_ORDER, LEAGUE_SHORT, Screen, all_pending, all_screens, all_trades, my_games, recent_trades, team_label
from .tiles import all_tiles
from .bigplay import sample_play

log = logging.getLogger("ledticker.render")

TICKER_H = 11
LAYOUT_SECONDS = 20.0
BREAKING = "trade_breaking"   # the full-display takeover screen
BIG_PLAY = "big_play"         # the big-play celebration / rival stinker takeover
EATOUT = "eatout_alert"       # the eat-out counter's +1 takeover
TAKEOVERS = {BREAKING: "trade", BIG_PLAY: "play", EATOUT: "event"}   # takeover screen -> attribute holding its item
PLAY_MAX_AGE = 600            # seconds: a big play older than this (e.g. held while the display was off) is skipped
FrameSink = Callable[[Image.Image], None]


def slide_key(slide: dict) -> str:
    if slide.get("widget") or slide.get("screen"):
        return slide.get("widget") or slide.get("screen")
    if "layout" in slide:
        return "layout:" + slide["layout"]
    if "tree" in slide:
        return "layout:(preview)"
    return "quad:" + ",".join(slide.get("quad") or [])


TICKER_ITEMS = ("games", "weather", "stocks", "crypto")   # default contents, in order ([render] ticker)


def _game_seg(lg: str, g: dict) -> str:
    a, h = g["away"], g["home"]
    s = f"{LEAGUE_SHORT[lg]} {team_label(a)} {a.get('score') or 0}  {team_label(h)} {h.get('score') or 0}  {g['status']}"
    if g.get("down_distance"):
        s += f"  {g['down_distance']}"
    return s


class Ticker:
    """Pre-renders a long strip of segments and scrolls a window across it. The scroll position carries over when
    the strip is rebuilt (a score changes), so live updates slide in without the strip jumping."""

    def __init__(self, speed: float = 36.0, items: tuple[str, ...] | list[str] = TICKER_ITEMS):
        self.speed = speed
        self.items = tuple(items)
        self.sig = None
        self.strip: Image.Image | None = None
        self.offset = 0.0            # pixels scrolled so far
        self._t: float | None = None

    def _games(self, rc: RC) -> list[tuple[str, tuple]]:
        """Every live game the score sources track (per their filters): my teams first, in gold."""
        mine, other = [], []
        for lg in LEAGUE_ORDER:
            for g in (rc.data(f"scores.{lg}") or {}).get("games") or []:
                if g.get("state") == "in":
                    (mine if g.get("mine") else other).append((_game_seg(lg, g), GOLD if g.get("mine") else YELLOW))
        return mine + other

    def _weather(self, rc: RC) -> list[tuple[str, tuple]]:
        w = rc.data("weather") or {}
        segs = [(f"WEATHER ALERT: {a['event'].upper()}", RED) for a in w.get("alerts") or []
                if a.get("level") in ("warning", "watch")]
        c, parts = w.get("current") or {}, []
        if c.get("temp_f") is not None:
            now = f"{c['temp_f']}F"
            if c.get("conditions"):
                now += f" {c['conditions'].upper()}"
            if c.get("feels_like_f") is not None and abs(c["feels_like_f"] - c["temp_f"]) >= 3:
                now += f" (FEELS {c['feels_like_f']}F)"
            parts.append(now)
        for pd in (w.get("periods") or [])[:2]:
            rain = f" {pd['precip_pct']}% RAIN" if (pd.get("precip_pct") or 0) >= 20 else ""
            parts.append(f"{pd['name'].upper()} {'HI' if pd.get('is_day') else 'LO'} {pd['temp_f']}F{rain}")
        if parts:
            where = (w.get("location") or "").split(",")[0].upper()
            segs.append(((where + ": " if where else "") + "  /  ".join(parts), BLUE))
        return segs

    def _quotes(self, rc: RC, name: str) -> list[tuple[str, tuple]]:
        segs = []
        for q in ((rc.data(name) or {}).get("quotes") or [])[:12]:
            if q.get("error"):
                continue
            up = (q.get("change_pct") or 0) >= 0
            segs.append((f"{q['symbol']} {q['price']:,.2f} {'+' if up else '-'}{abs(q.get('change_pct') or 0):.2f}%",
                         GREEN if up else RED))
        return segs

    def segments(self, rc: RC) -> list[tuple[str, tuple]]:
        segs: list[tuple[str, tuple]] = []
        for item in self.items:
            if item == "games":
                segs += self._games(rc)
            elif item == "weather":
                segs += self._weather(rc)
            elif item == "stocks":
                segs += self._quotes(rc, "markets.stocks")
            elif item == "crypto":
                segs += self._quotes(rc, "markets.crypto")
            elif item == "news":
                segs += [(f"{h['source'].upper()}: {h['title']}", WHITE)
                         for h in ((rc.data("news") or {}).get("headlines") or [])[:12]]
            elif item == "trades":
                for tr in recent_trades(rc):
                    deal = " / ".join(f"{s['team_name']} GETS {', '.join(s['receives'])}" for s in tr["sides"])
                    segs.append((f"FANTASY TRADE: {deal}", ORANGE))
        return segs or [("LED TICKER", GRAY)]

    def build(self, rc: RC) -> Image.Image:
        segs = self.segments(rc)
        sig = tuple(segs)
        if sig == self.sig and self.strip is not None:
            return self.strip
        f = font("body")
        gap = "   ·   "
        widths = [text_width(s, f) + text_width(gap, f) for s, _ in segs]
        strip = Image.new("RGB", (max(1, sum(widths)), TICKER_H), (0, 0, 0))
        d = ImageDraw.Draw(strip)
        x = 0
        for (s, col), w in zip(segs, widths):
            x += text(d, x, 2, s, "body", col)
            x += text(d, x, 2, gap, "body", DIM)
        if self.strip is not None:
            self.offset %= self.strip.width   # stay at the same spot in the current lap
        self.sig, self.strip = sig, strip
        return strip

    def advance(self, t: float) -> float:
        """Scroll by the time since the last frame (clamped, so slide restarts / snapshots don't jump)."""
        if self._t is not None:
            self.offset += min(0.5, max(0.0, t - self._t)) * self.speed
        self._t = t
        return self.offset

    def paste(self, img: Image.Image, rc: RC, t: float, width: int, y: int) -> None:
        strip = self.build(rc)
        total = strip.width
        x = -(int(self.advance(t)) % total)
        while x < width:
            img.paste(strip.crop((max(0, -x), 0, min(total, width - x), TICKER_H)), (max(0, x), y))
            x += total

    def draw(self, img: Image.Image, rc: RC, t: float, width: int) -> None:
        self.paste(img, rc, t, width, rc.H - TICKER_H)


class Engine:
    def __init__(self, ctx: Context, store: Store, images: ImageCache, geom: Geometry,
                 modes_path: Path | None = None):
        from .widgets import all_widgets
        self.ctx, self.store, self.images, self.geom = ctx, store, images, geom
        cfg = ctx.section("render")
        self.screens: dict[str, Screen] = all_screens()
        self.tiles: dict[str, Screen] = all_tiles()
        self.widgets = all_widgets()
        photos.LIBRARY = self.photos = photos.PhotoLibrary(ctx.root / "data" / "photos")
        ecfg = ctx.section("eatout")
        tally.COUNTER = self.eatout = tally.Counter(ctx.root / "data" / "eatout.json", str(ecfg.get("name") or "Anthony"))
        self.eatout_alert = bool(ecfg.get("alert", True))
        self.eatout_seconds = float(ecfg.get("seconds", 12))
        self.modes = ModeManager(modes_path if modes_path is not None else ctx.root / "data" / "modes.json")
        self.fps = float(cfg.get("fps", 30))
        self.ticker = Ticker(float(cfg.get("ticker_speed", 36)), cfg.get("ticker", TICKER_ITEMS))
        self.pinned: str | None = None            # a widget name pinned from the dashboard
        self.pinned_slide: dict | None = None     # or a whole layout being previewed
        self.mode_name: str | None = None
        self.mode_reason = ""
        self.slide_index = -1
        self.current: str | None = None           # key of the slide on screen
        self.started = time.monotonic()
        self.display_state: dict[str, Any] = {"brightness": 100, "power": "on", "why": {}}
        self._interrupts: list[str] = []
        self._interrupt_return: int | None = None
        self._breaking_q: list[dict] = []           # new trades waiting to break in
        self._breaking_until: float | None = None   # monotonic time the takeover on screen ends
        self._breaking_prev: str | None = None      # what was on screen before it, to resume
        self._breaking_widget = BREAKING             # which takeover screen is up (trade or big play)
        lcfg = ctx.section("love")
        self.love_seconds = float(lcfg.get("seconds", 20))
        self.love_default_message = str(lcfg.get("default_message") or love.DEFAULT_MESSAGE)
        self.love_default_style = lcfg.get("style") if lcfg.get("style") in love.STYLES else love.DEFAULT_STYLE
        self._love_q: list[dict] = []               # love taps waiting (e.g. sent while the display was off)
        self._love: dict | None = None              # the one on screen: message, style, start, until
        self._seen_path = ctx.root / "data" / "seen_alerts.json"
        self._seen = self._load_seen()
        # events for notifications. A fresh install has seen nothing yet: its first pass only records what's there
        self.on_event: Callable[[str, dict], None] | None = None
        self._announce = bool(self._seen)
        self._main_cache: tuple | None = None
        self._sinks: list[FrameSink] = []
        self.last_frame: Image.Image | None = None
        self._last_check = 0.0

    # ------------------------------------------------------------- helpers

    def rc(self, ticker: bool = True) -> RC:
        H = self.geom.height
        return RC(ctx=self.ctx, store=self.store, images=self.images, W=self.geom.width, H=H,
                  main_h=H - TICKER_H - 1 if ticker else H, now=datetime.now(timezone.utc))

    def _sub(self, rc: RC, r: Region) -> RC:
        return RC(ctx=rc.ctx, store=rc.store, images=rc.images, W=r.w, H=r.h, main_h=r.h, now=rc.now)

    @property
    def playlist(self) -> list[str]:
        name, _ = self.modes.active(datetime.now(self.ctx.tz))
        return [slide_key(s) for s in self.modes.mode(name)["slides"]]

    def available(self, rc: RC | None = None) -> list[str]:
        """Widgets that currently have data (at full-display size)."""
        rc = rc or self.rc()
        out = []
        for n, w in self.widgets.items():
            try:
                if w.available(rc):
                    out.append(n)
            except Exception:
                log.exception("available() failed for %s", n)
        return out

    def available_tiles(self, rc: RC | None = None) -> list[str]:
        rc = rc or self.rc()
        sub = RC(ctx=rc.ctx, store=rc.store, images=rc.images, W=self.geom.module_w, H=self.geom.module_h,
                 main_h=self.geom.module_h, now=rc.now)
        out = []
        for n, w in self.widgets.items():
            try:
                if w.available(sub):
                    out.append(n)
            except Exception:
                log.exception("available() failed for %s", n)
        return out

    def tree_for(self, slide: dict) -> dict | None:
        w = slide.get("widget") or slide.get("screen")
        if w:
            return {"widget": w}
        if "tree" in slide:
            return slide["tree"]
        if "layout" in slide:
            return self.modes.layout_tree(slide["layout"])
        if "quad" in slide:
            g = self.geom
            return quad_tree(slide["quad"], g.cols, g.rows, g.module_w, g.module_h)
        return None

    def _slide_ok(self, slide: dict, rc: RC) -> bool:
        tree = self.tree_for(slide)
        if not tree:
            return False
        try:
            for r in regions(tree, 0, 0, rc.W, rc.main_h):
                w = self.widgets.get(r.widget)
                if w and r.widget != "ticker" and w.available(self._sub(rc, r)):
                    return True
        except Exception:
            log.exception("slide check failed for %s", slide_key(slide))
        return False

    def _slide_seconds(self, slide: dict, rc: RC) -> float:
        if slide.get("seconds"):
            return float(slide["seconds"])
        w = slide.get("widget") or slide.get("screen")
        if w and w in self.widgets:
            return self.widgets[w].seconds(rc)
        return LAYOUT_SECONDS

    # ------------------------------------------------------------- interrupts (breaking news)

    def _load_seen(self) -> set[str]:
        try:
            return set(json.loads(self._seen_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    def _save_seen(self) -> None:
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            self._seen_path.write_text(json.dumps(sorted(self._seen)[-500:]), encoding="utf-8")
        except OSError:
            pass

    def _check_interrupts(self, rc: RC) -> None:
        if time.monotonic() - self._last_check < 2:
            return
        self._last_check = time.monotonic()
        fresh = False
        br = self.modes.breaking()
        takeover = br.get("trades", True)
        for tr in reversed(recent_trades(rc)):   # oldest first, so several new trades air in order
            key = f"trade:{tr['id']}"
            if key not in self._seen:
                self._seen.add(key)   # marked seen even when the takeover is off: no backlog if turned on later
                if takeover:
                    self._breaking_q.append(tr)
                self._emit("trade", tr)
                fresh = True
        for tr in all_pending(rc):   # accepted, waiting out the review period: the heads-up version
            key = f"pending:{tr['id']}"
            if key not in self._seen:
                self._seen.add(key)
                if takeover and br.get("pending", True):
                    self._breaking_q.append(tr)
                self._emit("trade_pending", tr)
                fresh = True
        for pl in reversed((rc.data("big_plays") or {}).get("plays") or []):   # oldest first
            key = f"play:{pl['id']}"
            if key not in self._seen:
                self._seen.add(key)
                show = self.play_qualifies(pl, br)
                if show:
                    self._breaking_q.append({**show, "_kind": "play"})
                    self._emit("big_play", show)
                fresh = True
        for a in (rc.data("weather") or {}).get("alerts") or []:
            key = f"wx:{a.get('id')}"
            if a.get("level") in ("warning", "watch") and key not in self._seen:
                self._seen.add(key)
                self._interrupts.append("alert")
                self._emit("weather", a)
                fresh = True
        if fresh:
            self._save_seen()
        self._announce = True

    def _emit(self, kind: str, data: dict) -> None:
        if self._announce and self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                log.exception("event handler failed")

    @staticmethod
    def play_qualifies(pl: dict, br: dict) -> dict | None:
        """The play as it should be shown under the Settings thresholds, or None. Bench players only count when
        "bench" is on; a play shows if it's my NFL team scoring (or a rival team, for the stinker), or one of the
        players on it hit the yards / points / touchdown bar."""
        boo = pl.get("mood") == "boo"
        if pl.get("team_score") and br.get("rival_scores" if boo else "team_scores", True):
            return pl
        if not br.get("rival_plays" if boo else "big_plays", True):
            return None
        stars = [s for s in pl.get("players") or [] if s.get("starter") or br.get("bench", False)]
        if not stars:
            return None
        pts = sum(s.get("points") or 0 for s in stars)
        carrier = any(s.get("role") in ("receiver", "rusher") for s in stars)
        if ((pl.get("touchdown") and br.get("touchdowns", True)) or pts >= float(br.get("min_points", 5))
                or (carrier and (pl.get("yards") or 0) >= int(br.get("min_yards", 20)))):
            return {**pl, "players": stars, "points": round(pts, 2)}
        return None

    def big_play_test(self, which: str = "fantasy") -> dict:
        """Play a celebration now (dashboard test buttons): the latest real one of that kind if there is one,
        otherwise a made-up example. which: fantasy | team | rival | rival_team."""
        boo = which.startswith("rival")
        team = which in ("team", "rival_team")
        for pl in (self.rc().data("big_plays") or {}).get("plays") or []:
            if (pl.get("mood") == "boo") == boo and bool(pl.get("team_score")) == team and (team or pl.get("players")):
                tr = {**pl, "_kind": "play", "_test": True}
                break
        else:
            tr = {**sample_play(which, self.rc()), "_kind": "play", "_test": True}
        tr["id"] = f"test-{time.time():.0f}"
        self._breaking_q.append(tr)
        return tr

    def breaking_test(self, pending: bool = False) -> dict:
        """Run the takeover now with the league's latest trade (dashboard test buttons). pending=True shows the
        "accepted, under review" version of it."""
        trades = all_trades(self.rc())
        if not trades:
            raise LookupError("no trades in the league yet")
        tr = dict(trades[0])
        if pending:
            tr.update(status="pending review", date=datetime.now(timezone.utc).isoformat(),
                      process_date=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat())
        self._breaking_q.append(tr)
        return tr

    # ------------------------------------------------------------- love tap

    def love_tap(self, message: str | None = None, style: str | None = None, sender: str = "") -> bool:
        """Queue a love tap. Returns True if it's being held because the display is off right now."""
        msg = " ".join((message or "").split())[:love.MAX_MESSAGE] or self.love_default_message
        style = style if style in love.STYLES else self.love_default_style
        if len(self._love_q) >= 3:      # don't let a pile of taps lock up the display
            self._love_q.pop(0)
        self._love_q.append({"message": msg, "style": style, "from": sender})
        log.info("love tap from %s (%s): %s", sender or "?", style, msg)
        return self._dark()

    @property
    def love_active(self) -> dict | None:
        return {k: self._love[k] for k in ("message", "style", "from")} if self._love else None

    def _love_step(self) -> dict | None:
        """The love tap on screen right now (starting the next queued one if it's time), else None.
        Taps wait while the display is off, and one that was cut off by the display going dark replays in full."""
        now = time.monotonic()
        if self._dark():
            if self._love is not None:
                self._love_q.insert(0, {k: self._love[k] for k in ("message", "style", "from")})
                self._end_love(now)
            return None
        if self._love is not None and now >= self._love["until"]:
            self._end_love(now)
        if self._love is None and self._love_q:
            nxt = self._love_q.pop(0)
            self._love = {**nxt, "start": now, "until": now + self.love_seconds}
        return self._love

    def _end_love(self, now: float) -> None:
        """Resume what it covered: the slide gets a fresh timer and a breaking alert gets its time back."""
        paused = now - self._love["start"]
        self._love = None
        if self._breaking_until is not None:
            self._breaking_until += paused
        self.started += paused
        self._main_cache = None

    def _dark(self) -> bool:
        ds = self.display_state
        return ds.get("power") == "off" or int(ds.get("brightness", 100) or 0) <= 0

    def _takeover(self, which: str) -> Screen:
        return self.widgets[which].full

    def _takeover_item(self) -> dict | None:
        return getattr(self._takeover(self._breaking_widget), TAKEOVERS[self._breaking_widget])

    def _clear_takeover(self) -> None:
        for which, attr in TAKEOVERS.items():
            setattr(self._takeover(which), attr, None)

    # ------------------------------------------------------------- eat-out counter

    def eatout_add(self, place: str | None = None, by: str = "") -> tuple[dict, bool]:
        """Log one eat-out and (unless turned off) play the +1 takeover. Returns (event with totals, held)."""
        e = self.eatout.add(place, by)
        st = self.eatout.stats(self.ctx.tz)
        ev = {**e, "total": st["total"], "week": st["week"], "name": st["name"]}
        if self.eatout_alert:
            self._breaking_q.append({**ev, "_kind": "tally"})
        return ev, self._dark() and self.modes.breaking().get("hold_while_off", True)

    def _start_breaking(self, tr: dict) -> bool:
        """Put an alert on screen. False if it's a big play that has gone stale while it waited."""
        br = self.modes.breaking()
        if tr.get("_kind") == "tally":
            which, secs = EATOUT, self.eatout_seconds
        elif tr.get("_kind") == "play":
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(tr["when"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = 0
            if age > PLAY_MAX_AGE and not tr.get("_test"):
                return False
            which, secs = BIG_PLAY, br.get("play_seconds", 20)
        else:
            which = BREAKING
            secs = br.get("pending_seconds", 15) if tr.get("status") == "pending review" else br.get("seconds", 30)
        if self._breaking_until is None:
            self._breaking_prev = self.current
        self._clear_takeover()
        setattr(self._takeover(which), TAKEOVERS[which], tr)
        self._breaking_widget = which
        self._switch(which)
        self._breaking_until = time.monotonic() + float(secs)
        return True

    def _breaking_slide(self) -> dict | None:
        """The takeover slide while one is on (or starting), else None. Ends it and resumes when time is up.
        While the display is off (or at 0% brightness) alerts wait, so one that lands overnight plays in the morning."""
        if self._dark() and self.modes.breaking().get("hold_while_off", True):
            if self._breaking_until is not None:   # went dark mid-alert: replay it in full later
                self._breaking_q.insert(0, self._takeover_item())
                self._breaking_until = None
                self._clear_takeover()
                self._switch(self._breaking_prev)
                self._breaking_prev = None
            return None
        if self._breaking_until is not None:
            if time.monotonic() < self._breaking_until:
                return {"widget": self._breaking_widget}
            self._breaking_until = None
        while self._breaking_q:
            if self._start_breaking(self._breaking_q.pop(0)):
                return {"widget": self._breaking_widget}
        if self._breaking_prev is not None or self._takeover_item() is not None:
            self._clear_takeover()
            self._switch(self._breaking_prev)   # pick up the slide / pin we interrupted
            self._breaking_prev = None
        return None

    # ------------------------------------------------------------- playlist control

    def pin(self, name: str | None) -> None:
        if name is not None and name not in self.widgets:
            raise KeyError(name)
        self.pinned, self.pinned_slide = name, None
        if name:
            self._switch(name)

    def pin_layout(self, slide: dict | None) -> None:
        """Show a saved layout (or an unsaved tree from the editor) until unpinned."""
        self.pinned_slide, self.pinned = slide, None
        if slide:
            self._switch(slide_key(slide))

    def set_mode(self, name: str | None) -> None:
        if name is not None and name not in self.modes.doc["modes"]:
            raise KeyError(name)
        self.modes.override = name
        self.mode_name = None  # force re-evaluation (resets the slide position)

    def skip(self) -> None:
        if self._love is not None:            # "Next" dismisses a love tap first
            self._love["until"] = time.monotonic()
            return
        if self._breaking_until is not None:   # "Next" dismisses breaking news
            self._breaking_until = time.monotonic()
            return
        self._advance(self.rc(self._ticker_on()))

    def _switch(self, key: str) -> None:
        self.current, self.started = key, time.monotonic()
        self._main_cache = None

    def _slides(self) -> list[dict]:
        return self.modes.mode(self.mode_name)["slides"] if self.mode_name else []

    def _advance(self, rc: RC) -> None:
        slides = self._slides()
        n = len(slides)
        for step in range(1, n + 1):
            i = (self.slide_index + step) % n
            if self._slide_ok(slides[i], rc):
                self.slide_index = i
                self._switch(slide_key(slides[i]))
                return
        self.slide_index = -1
        self._switch("__empty__")

    def _current_slide(self) -> dict | None:
        if self.pinned_slide:
            return self.pinned_slide
        if self.pinned:
            return {"widget": self.pinned}
        slides = self._slides()
        if self.slide_index < 0:
            return {"widget": self.current} if self.current in self.widgets else None  # an interrupt
        if self.slide_index < len(slides):
            return slides[self.slide_index]
        return None

    def _pick(self) -> dict | None:
        now_local = datetime.now(self.ctx.tz)
        self.display_state = self.modes.display(now_local)
        name, why = self.modes.active(now_local)
        if name != self.mode_name:
            self.mode_name, self.slide_index = name, -1
            if self._breaking_until is not None:
                self._breaking_prev = None      # resume into the new mode's first slide
            elif not (self.pinned or self.pinned_slide):
                self.current = None
        self.mode_reason = why
        rc = self.rc(self._ticker_on())
        self._check_interrupts(rc)
        breaking = self._breaking_slide()   # trades take over everything, pins and previews included
        if breaking:
            return breaking
        if self.pinned_slide or self.pinned:
            return self._current_slide()
        if self._interrupts and self.current != "alert":
            nxt = self._interrupts.pop(0)
            if self.widgets[nxt].available(rc):
                self._interrupt_return = self.slide_index
                self.slide_index = -1
                self._switch(nxt)
                return {"widget": nxt}
        slide = self._current_slide()
        elapsed = time.monotonic() - self.started
        if slide is None or not self._slide_ok(slide, rc) or elapsed >= self._slide_seconds(slide, rc):
            if self.slide_index < 0 and self._interrupt_return is not None:
                self.slide_index = self._interrupt_return   # resume the playlist after an interrupt
                self._interrupt_return = None
            self._advance(rc)
            slide = self._current_slide()
        return slide

    def _ticker_on(self) -> bool:
        if self._breaking_until is not None:
            return False   # breaking news gets the entire display
        if self.pinned and getattr(self.widgets.get(self.pinned), "immersive", False):
            return False   # the party show (and anything immersive) gets the whole display
        if self.pinned or self.pinned_slide or not self.mode_name:
            return True if not self.pinned_slide else bool(self.pinned_slide.get("ticker", True))
        return bool(self.modes.mode(self.mode_name).get("ticker", True))

    # ------------------------------------------------------------- drawing

    def _draw_tree(self, img: Image.Image, rc: RC, tree: dict, t: float) -> None:
        regs = regions(tree, 0, 0, rc.W, rc.main_h)
        single = len(regs) == 1
        for r in regs:
            if r.w < 8 or r.h < 8:
                continue
            cell = Image.new("RGB", (r.w, r.h), (0, 0, 0))
            sub = self._sub(rc, r)
            w = self.widgets.get(r.widget)
            try:
                impl = w.impl(r.w, r.h) if w else None
                if impl is not None and (r.widget == "ticker" or impl.available(sub)):
                    self.draw_widget(w, impl, cell, sub, t)
                elif w:
                    cd = ImageDraw.Draw(cell)
                    text(cd, 3, 3, w.label.upper()[: max(4, r.w // 5)], "tiny", GOLD)
                    text(cd, 3, 11, "NO DATA RIGHT NOW", "tiny", GRAY)
                elif not single:
                    ImageDraw.Draw(cell).rectangle([0, 0, r.w - 1, r.h - 1], outline=(16, 16, 22))
            except Exception as e:
                log.exception("widget %s failed", r.widget)
                cd = ImageDraw.Draw(cell)
                text(cd, 2, 2, f"{r.widget}: error", "small", RED)
                text(cd, 2, 10, str(e)[:30], "tiny", GRAY)
            img.paste(cell, (r.x, r.y))
        if not single:  # faint divider lines between sections
            d = ImageDraw.Draw(img)
            for r in regs:
                if r.x > 0:
                    d.line([(r.x, r.y), (r.x, r.y + r.h - 1)], fill=(20, 20, 28))
                if r.y > 0:
                    d.line([(r.x, r.y), (r.x + r.w - 1, r.y)], fill=(20, 20, 28))

    def draw_widget(self, w, impl, cell: Image.Image, sub: RC, t: float) -> None:
        """Draw one widget into its section, whatever the section's shape.

        Widgets with a full design (or that adapt themselves, `adaptive = True`) draw straight in. A compact tile
        design (made for one module, e.g. 128x64) is fitted instead:
          - section at least twice the module both ways (full screen): drawn at half size and scaled up 2x
          - section much taller than a module but not wider (side-by-side halves): drawn at module height and
            centered, unless the tile is a list that fills extra rows (`grows_tall = True`)
          - anything else (a module, stacked halves): drawn straight in; tiles stretch across extra width."""
        W, H = cell.size
        mw, mh = self.geom.module_w, self.geom.module_h
        tile = impl is not w.full and not getattr(impl, "adaptive", False)
        if tile and W >= 2 * mw and H >= 2 * (mh - 8):
            sw, sh = W // 2, H // 2
            small = Image.new("RGB", (sw, sh))
            impl.render(small, RC(ctx=sub.ctx, store=sub.store, images=sub.images, W=sw, H=sh, main_h=sh, now=sub.now), t)
            cell.paste(small.resize((sw * 2, sh * 2), Image.NEAREST), ((W - sw * 2) // 2, (H - sh * 2) // 2))
        elif tile and H >= mh + 16 and W < 2 * mw and not getattr(impl, "grows_tall", False):
            nh = min(H, mh)
            small = Image.new("RGB", (W, nh))
            impl.render(small, RC(ctx=sub.ctx, store=sub.store, images=sub.images, W=W, H=nh, main_h=nh, now=sub.now), t)
            cell.paste(small, (0, (H - nh) // 2))
        else:
            impl.render(cell, sub, t)

    def _animating(self, slide: dict | None, rc: RC, t: float) -> bool:
        """True while a section on screen is mid-transition: redraw at the full frame rate instead of 8 fps."""
        tree = self.tree_for(slide) if slide else None
        if not tree:
            return False
        try:
            for r in regions(tree, 0, 0, rc.W, rc.main_h):
                w = self.widgets.get(r.widget)
                impl = w.impl(r.w, r.h) if w else None
                if impl is not None and hasattr(impl, "animating") and impl.animating(self._sub(rc, r), t):
                    return True
        except Exception:
            log.exception("animating() failed")
        return False

    def render(self, t: float | None = None, screen: str | None = None) -> Image.Image:
        """Render a frame. `screen` forces a widget, 'layout:<name>' or 'quad:a,b,c,d' (used by snapshots)."""
        t = time.monotonic() if t is None else t
        party.CLOCK.configure(self.modes.party())
        if screen:
            if screen.startswith("quad:"):
                slide = {"quad": screen.split(":", 1)[1].split(",")}
            elif screen.startswith("layout:"):
                slide = {"layout": screen.split(":", 1)[1]}
            else:
                slide = {"widget": screen}
            # takeovers (breaking trade, big plays) and immersive widgets get the whole display, as they do live
            full = screen in TAKEOVERS or getattr(self.widgets.get(screen), "immersive", False)
            ticker_on, local_t, key0 = not full, t, slide_key(slide)
        else:
            self.display_state = self.modes.display(datetime.now(self.ctx.tz))
            lv = self._love_step()
            if lv is not None:             # a love tap takes the entire display, over everything else
                return love.frame(t - lv["start"], lv["message"], lv["style"], (self.geom.width, self.geom.height))
            slide = self._pick()
            ticker_on, local_t, key0 = self._ticker_on(), t - self.started, self.current
        rc = self.rc(ticker_on)
        W, H = rc.W, rc.H
        tick = int(local_t * (self.fps if self._animating(slide, rc, local_t) else 8))
        key = (key0, tick, self.store_version(), ticker_on, id(self.pinned_slide))
        if self._main_cache is None or self._main_cache[0] != key or screen:
            main = Image.new("RGB", (W, H), (0, 0, 0))
            try:
                tree = self.tree_for(slide) if slide else None
                if tree is None:
                    text(ImageDraw.Draw(main), 4, 4, "WAITING FOR DATA...", "body", GRAY)
                else:
                    self._draw_tree(main, rc, tree, local_t)
            except Exception as e:
                log.exception("slide %s failed", key0)
                d = ImageDraw.Draw(main)
                text(d, 4, 4, f"{key0}: render error", "body", RED)
                text(d, 4, 14, str(e)[:60], "small", GRAY)
            self._main_cache = (key, main)
        img = self._main_cache[1].copy()
        if ticker_on:
            d = ImageDraw.Draw(img)
            d.line([(0, H - TICKER_H - 1), (W - 1, H - TICKER_H - 1)], fill=DARK)
            clock_w = 34
            self.ticker.draw(img, rc, t, W - clock_w)
            d.rectangle([W - clock_w, H - TICKER_H, W - 1, H - 1], fill=(0, 0, 0))
            d.line([(W - clock_w, H - TICKER_H), (W - clock_w, H - 1)], fill=DARK)
            text_right(d, W - 2, H - TICKER_H + 2, datetime.now(self.ctx.tz).strftime("%I:%M").lstrip("0"), "body", GOLD)
        if self.modes.theme_active(datetime.now(self.ctx.tz)) == "christmas":
            try:
                christmas.decorate(img, t, self.modes.theme(), TICKER_H if ticker_on else 0)
            except Exception:
                log.exception("christmas overlay failed")
        return img

    def output(self, frame: Image.Image) -> Image.Image:
        """Apply scheduled / manual brightness and power. On the Pi this becomes the matrix brightness setting;
        here we scale the pixels so the preview shows what you'd actually see."""
        ds = self.display_state
        if ds.get("power") == "off":
            return Image.new("RGB", frame.size, (0, 0, 0))
        b = max(0, min(100, int(ds.get("brightness", 100))))
        if b >= 100:
            return frame
        k = b / 100
        return frame.point(lambda v: int(v * k))

    def store_version(self) -> str:
        return "|".join(f"{n}:{s.updated_at}" for n, s in sorted(self.store.states.items()))

    def subscribe(self, sink: FrameSink) -> Callable[[], None]:
        self._sinks.append(sink)
        return lambda: self._sinks.remove(sink) if sink in self._sinks else None

    async def run(self) -> None:
        period = 1.0 / self.fps
        nxt = time.monotonic()
        while True:
            frame = self.output(self.render())
            self.last_frame = frame
            for sink in list(self._sinks):
                try:
                    sink(frame)
                except Exception:
                    log.exception("frame sink failed")
            nxt += period
            delay = nxt - time.monotonic()
            if delay < -0.5:
                nxt = time.monotonic()
                delay = 0
            await asyncio.sleep(max(0.0, delay))
