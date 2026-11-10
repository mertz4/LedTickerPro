"""Eat-out counter: how many times the roommate ([eatout] name, "Anthony") has gotten fast food / eaten out.

Roommates tap +1 on the dashboard (optionally saying where), which saves the time in data/eatout.json and plays a
short full-display "ORDER UP!" takeover: food raining down, the odometer rolling over to the new number. The
widget (any section size) shows the total on a diner-neon sign with chasing marquee bulbs, a pixel burger, fries and
a soda, plus this week / this month and how long he's been clean.
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from .draw import big_text, big_width, text, text_center
from .fonts import fit, font, text_width
from .screens import RC, Screen

RED = (255, 52, 40)
MUSTARD = (255, 200, 0)
CREAM = (255, 238, 200)
TEAL = (40, 220, 200)
DIMRED = (90, 14, 10)
DIMYEL = (80, 60, 0)
MAX_PLACE = 30
ORDINAL = {1: "ST", 2: "ND", 3: "RD"}


def ordinal(n: int) -> str:
    return f"{n}{'TH' if 10 <= n % 100 <= 20 else ORDINAL.get(n % 10, 'TH')}"


# ---------------------------------------------------------------- the data

class Counter:
    """Every eat-out, newest last: {"at": ISO UTC, "by": who tapped, "place": optional}. `base` is a starting count
    for times before the counter existed."""

    def __init__(self, path: Path | None, name: str = "Anthony"):
        self.path, self.name = path, name
        self.events: list[dict] = []
        self.base = 0
        self._lock = threading.Lock()
        if path and path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self.events = [e for e in d.get("events") or [] if e.get("at")]
                self.base = max(0, int(d.get("base") or 0))
            except (OSError, ValueError, TypeError):
                pass

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"name": self.name, "base": self.base, "events": self.events}, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def total(self) -> int:
        return self.base + len(self.events)

    def add(self, place: str | None = None, by: str = "", now: datetime | None = None) -> dict:
        place = " ".join((place or "").split())[:MAX_PLACE] or None
        e = {"at": (now or datetime.now(timezone.utc)).isoformat(), "by": by, "place": place}
        with self._lock:
            self.events.append(e)
            self._save()
        return e

    def undo(self) -> dict | None:
        with self._lock:
            if not self.events:
                return None
            e = self.events.pop()
            self._save()
        return e

    def set_base(self, total: int) -> None:
        """Make the total read `total` (keeps the logged times; the difference goes into the starting count)."""
        with self._lock:
            self.base = max(0, int(total) - len(self.events))
            self._save()

    def stats(self, tz, now: datetime | None = None) -> dict:
        now = (now or datetime.now(timezone.utc)).astimezone(tz)
        times = [datetime.fromisoformat(e["at"]).astimezone(tz) for e in self.events]
        week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last = self.events[-1] if self.events else None
        places: dict[str, int] = {}
        for e in self.events:
            if e.get("place"):
                k = e["place"].upper()
                places[k] = places.get(k, 0) + 1
        fav = max(places.items(), key=lambda kv: kv[1]) if places else None
        return {
            "name": self.name, "total": self.total, "base": self.base,
            "week": sum(t >= week0 for t in times), "month": sum(t >= month0 for t in times),
            "today": sum(t.date() == now.date() for t in times),
            "last": last, "since": (now - times[-1]).total_seconds() if times else None,
            "favorite": fav[0] if fav and fav[1] >= 2 else None,
            "recent": list(reversed(self.events[-8:])),
        }


COUNTER: Counter | None = None   # set by the engine


def fitted(s: str, max_w: int, roles: tuple[str, ...]) -> tuple[str, str]:
    """(text, role): the biggest role from `roles` the text fits in, else the smallest with the text cut."""
    s, f = fit(s, max_w, roles)
    return s, next(r for r in roles if font(r) is f)


def ago(secs: float | None) -> str:
    if secs is None:
        return ""
    if secs < 90:
        return "JUST NOW"
    if secs < 3600:
        return f"{int(secs // 60)} MIN AGO"
    if secs < 86400:
        return f"{int(secs // 3600)}H AGO"
    d = int(secs // 86400)
    return f"{d} DAY{'S' if d != 1 else ''} AGO"


def clean_line(st: dict) -> str:
    s = st.get("since")
    if s is None:
        return "NO FAST FOOD LOGGED YET"
    if s < 86400:
        return f"LAST ONE {ago(s)}"
    d = int(s // 86400)
    return f"CLEAN {d} DAY{'S' if d != 1 else ''}"


# ---------------------------------------------------------------- sprites

BURGER = (
    "....TTTTTTTT....",
    "..TTTSTTTTSTTT..",
    ".TTTTTTTSTTTTTT.",
    "TTSTTTTTTTTTSTTT",
    "TTTTTTTTTTTTTTTT",
    "GLGGLGGLGGLGGLGG",
    "YYYYYYYYYYYYYYYY",
    ".Y..Y......Y....",
    "PPPPPPPPPPPPPPPP",
    "PQPPPPQPPPPPQPPP",
    "PPPPPPPPPPPPPPPP",
    "TTTTTTTTTTTTTTTT",
    ".TTTTTTTTTTTTTT.",
)
FRIES = (
    ".Y..Y.Y..",
    ".YY.Y.YY.",
    "..YYYYY.Y",
    ".YYYYYYY.",
    "RRRRRRRRR",
    "RRWWWWWRR",
    "RRRWWWRRR",
    ".RRRRRRR.",
    ".RRRRRRR.",
    "..RRRRR..",
)
SODA = (
    ".....W..",
    "....W...",
    ".LLLLLL.",
    "RRRRRRRR",
    ".RWWWWR.",
    ".RRWWRR.",
    ".RRRRRR.",
    ".RRRRRR.",
    "..RRRR..",
)
PAL = (("T", (225, 140, 45)), ("S", (255, 240, 205)), ("G", (70, 200, 60)), ("L", (130, 235, 90)),
       ("Y", (255, 205, 30)), ("P", (125, 62, 28)), ("Q", (90, 42, 18)), ("R", (230, 30, 30)),
       ("W", (255, 255, 255)))
FOODS = (BURGER, FRIES, SODA)


@lru_cache(maxsize=64)
def sprite(rows: tuple[str, ...], scale: int = 1) -> Image.Image:
    pal = dict(PAL)
    w, h = max(len(r) for r in rows), len(rows)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch in pal:
                px[x, y] = (*pal[ch], 255)
    return im if scale == 1 else im.resize((w * scale, h * scale), Image.NEAREST)


@lru_cache(maxsize=64)
def dimmed(rows: tuple[str, ...], scale: int, k: float) -> Image.Image:
    im = sprite(rows, scale)
    if k >= 1:
        return im
    r, g, b, a = im.split()
    return Image.merge("RGBA", (*(ch.point(lambda v: int(v * k)) for ch in (r, g, b)), a))


def paste(img: Image.Image, im: Image.Image, x: float, y: float) -> None:
    img.paste(im, (int(round(x)), int(round(y))), im)


# ---------------------------------------------------------------- pieces

def marquee(img: Image.Image, x0: int, y0: int, x1: int, y1: int, t: float, step: int = 4) -> None:
    """A sign border of bulbs with a chase running around it."""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x1, y1], radius=3, outline=DIMRED)
    pts = []
    for x in range(x0 + 2, x1 - 1, step):
        pts.append((x, y0))
    for y in range(y0 + 2, y1 - 1, step):
        pts.append((x1, y))
    for x in range(x1 - 2, x0 + 1, -step):
        pts.append((x, y1))
    for y in range(y1 - 2, y0 + 1, -step):
        pts.append((x0, y))
    phase = int(t * 8)
    for i, p in enumerate(pts):
        d.point(p, fill=MUSTARD if (i + phase) % 3 == 0 else DIMYEL)


def neon(img: Image.Image, cx: int, y: int, s: str, role: str, t: float, color=RED, seed: int = 3) -> None:
    """Neon lettering with a soft glow; now and then one letter flickers out like a tired diner sign."""
    s = s.upper()
    f = font(role)
    w = text_width(s, f)
    x = cx - w // 2
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed + int(t * 3))
    dead = rnd.randrange(len(s)) if s and rnd.random() < .25 else -1
    glow = tuple(int(c * .28) for c in color)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        text(d, x + dx, y + dy, s, role, glow)
    cx_ = x
    for i, ch in enumerate(s):
        text(d, cx_, y, ch, role, tuple(int(c * .3) for c in color) if i == dead else color)
        cx_ += text_width(ch, f)


def odometer(img: Image.Image, cx: int, y: int, value: int, roll: float, scale: int, prev: int | None = None) -> int:
    """Mechanical counter: each digit on its own dark wheel. `roll` 0..1 animates prev -> value. Returns width."""
    digits = max(2, len(str(value)))
    s, p = str(value).rjust(digits, "0"), str(prev if prev is not None else value).rjust(digits, "0")
    cw = big_width("0", "huge", scale) + 4 * scale
    ch = 20 * scale + 6 * scale
    x0 = cx - (cw * digits + 2 * (digits - 1)) // 2
    for i, (a, b) in enumerate(zip(p, s)):
        x = x0 + i * (cw + 2)
        cell = Image.new("RGB", (cw, ch), (28, 24, 22))
        cd = ImageDraw.Draw(cell)
        cd.rectangle([0, 0, cw - 1, ch - 1], outline=(70, 60, 50))
        if a != b and roll < 1:                       # this wheel turns: old digit slides up, new one comes in
            k = 1 - (1 - roll) ** 3
            off = int(k * ch)
            big_text(cell, cw // 2, 3 * scale - off, a, "huge", scale, CREAM, anchor="c")
            big_text(cell, cw // 2, 3 * scale - off + ch, b, "huge", scale, MUSTARD, anchor="c")
        else:
            big_text(cell, cw // 2, 3 * scale, b, "huge", scale, CREAM if roll >= 1 or a == b else MUSTARD, anchor="c")
        cd.line([(1, ch // 2), (cw - 2, ch // 2)], fill=(12, 10, 10))   # the seam across the wheel
        img.paste(cell, (x, y))
    return cw * digits + 2 * (digits - 1)


def food_rain(img: Image.Image, t: float, seed: int = 5, every: float = .14, dim: float = 1.0, scale: int = 1) -> None:
    W, H = img.size
    fall = 2.2
    first = max(0, int((t - fall) / every))
    for k in range(first, int(t / every) + 1):
        r = random.Random(seed * 97 + k)
        age = t - (k * every + r.uniform(0, every))
        if not 0 <= age <= fall:
            continue
        sp = dimmed(FOODS[r.randrange(3)], scale, round(dim, 2))
        x = r.uniform(-4, W - sp.width + 4)
        y = -sp.height + (H + sp.height) * (age / fall) ** 1.2
        paste(img, sp, x + math.sin(age * 3 + k) * 3, y)


# ---------------------------------------------------------------- the widget

class EatOutCounter(Screen):
    """The counter on a diner sign. Lays itself out for any section: full screen, a tile, or a thin strip."""
    name, label = "eatout", "Anthony's eat-out counter"
    duration = 12.0
    adaptive = True

    def available(self, rc: RC) -> bool:
        return COUNTER is not None

    def animating(self, rc: RC, t: float) -> bool:
        return True                                    # bulbs chase, burger bobs

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        st = COUNTER.stats(rc.ctx.tz)
        W, H = rc.W, rc.main_h
        d = ImageDraw.Draw(img)
        who = st["name"].upper()
        if H < 28:                                     # a strip: one line
            msg = f"{who} HAS EATEN OUT {st['total']} TIMES  ·  {clean_line(st)}"
            msg, f = fit(msg, W - 4, ("body", "small", "tiny"))
            role = next(r for r in ("body", "small", "tiny") if font(r) is f)
            text_center(d, W // 2, max(0, (H - 7) // 2), msg, role, MUSTARD)
            return
        big = W >= 200 and H >= 96
        marquee(img, 0, 0, W - 1, H - 1, t, 4 if big else 3)
        # sign
        title = f"{who}'S FAST FOOD COUNTER" if big else f"{who} ATE OUT"
        title, f = fit(title, W - 10, ("bold", "body", "small") if big else ("body", "small", "tiny"))
        role = next(r for r in ("bold", "body", "small", "tiny") if font(r) is f)
        neon(img, W // 2, 4 if big else 3, title, role, t)
        ty = (4 if big else 3) + (13 if role == "bold" else 7 if role == "body" else 6) + 3
        # food column on the left
        fs = 3 if big and H >= 110 else 2 if big else 1
        bob = int(math.sin(t * 3) * fs)
        burger = sprite(BURGER, fs)
        bx, by = 6, ty + (2 if big else 1)
        paste(img, burger, bx, by + bob)
        paste(img, sprite(FRIES, fs), bx, by + burger.height + 3 * fs)
        paste(img, sprite(SODA, fs), bx + sprite(FRIES, fs).width + 2 * fs, by + burger.height + 4 * fs - bob)
        left = bx + max(burger.width, sprite(FRIES, fs).width + 2 * fs + sprite(SODA, fs).width) + 4
        # odometer + stats on the right
        scale = 3 if big else 1
        stats_h = 16 if big else 7
        while scale > 1 and ty + 26 * scale + stats_h > H - 4:
            scale -= 1
        col_cx = left + (W - 4 - left) // 2
        odometer(img, col_cx, ty + 1, st["total"], 1.0, scale)
        oy = ty + 1 + 26 * scale + (4 if big else 2)
        if big:
            line1 = f"THIS WEEK {st['week']}   THIS MONTH {st['month']}"
            text_center(d, col_cx, oy, *fitted(line1, W - left - 8, ("body", "small")), TEAL)
            last = st.get("last") or {}
            line2 = clean_line(st) + (f" · {last['place'].upper()}" if last.get("place") and (st["since"] or 0) < 86400 * 3
                                      else (f" · FAV: {st['favorite']}" if st.get("favorite") else ""))
            text_center(d, col_cx, oy + 10, *fitted(line2, W - left - 8, ("small", "tiny")), CREAM)
            ry = oy + 19
            recent = [f"{(e.get('place') or '?').upper()} {ago((rc.now - datetime.fromisoformat(e['at'])).total_seconds())}"
                      for e in st["recent"][1:4]]
            if recent and ry + 6 <= H - 3:              # room to spare: the ones before it
                text_center(d, col_cx, ry, *fitted("BEFORE THAT: " + "  ·  ".join(recent), W - left - 8,
                                                    ("small", "tiny")), (150, 140, 120))
        elif oy + 6 <= H - 2:
            text_center(d, col_cx, oy, *fitted(f"WK {st['week']} · {clean_line(st)}", W - left - 6, ("small", "tiny")),
                        TEAL)


class EatOutAlert(Screen):
    """Takeover when someone taps +1: ORDER UP!, food rain, the odometer rolling to the new number, where he went."""
    name, label = "eatout_alert", "Eat-out counter: +1 (takeover)"
    hidden = True

    def __init__(self):
        self.event: dict | None = None

    def available(self, rc):
        return self.event is not None

    def animating(self, rc, t):
        return True

    def render(self, img, rc, t):
        e = self.event
        if not e:
            return
        W, H = rc.W, rc.main_h
        d = ImageDraw.Draw(img)
        s = 2 if H >= 96 else 1
        n, who = int(e.get("total") or 0), (e.get("name") or "ANTHONY").upper()
        food_rain(img, t, seed=n, dim=1.0 if t < 3 else .35, scale=s)
        marquee(img, 0, 0, W - 1, H - 1, t, 4 if s > 1 else 3)
        if t < 2.6:                                    # ORDER UP! flashing like the kitchen bell
            flash = int(t * 5) % 2 == 0
            word = "ORDER UP!"
            sc = 2 if big_width(word, "huge", 2) <= W - 10 and s > 1 else 1
            y = H // 2 - 10 * sc - 6
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (2, 2)):
                big_text(img, W // 2 + dx, y + dy, word, "huge", sc, (0, 0, 0), anchor="c")
            big_text(img, W // 2, y, word, "huge", sc, RED if flash else MUSTARD, anchor="c")
            neon(img, W // 2, y + 20 * sc + 6, f"{who} ATE OUT AGAIN", "large" if s > 1 else "body", t, MUSTARD)
            return
        k = t - 2.6
        title = f"{who}'S FAST FOOD COUNTER"
        title, f = fit(title, W - 10, ("bold", "body", "small") if s > 1 else ("body", "small", "tiny"))
        role = next(r for r in ("bold", "body", "small", "tiny") if font(r) is f)
        neon(img, W // 2, 4 if s > 1 else 3, title, role, t)
        ty = (4 if s > 1 else 3) + (13 if role == "bold" else 7) + (5 if s > 1 else 2)
        scale = 2 if s > 1 else 1
        roll = min(1.0, max(0.0, (k - 0.3) / 1.2))
        ow = odometer(img, W // 2, ty, n, roll, scale, prev=max(0, n - 1))
        if roll >= 1 and int(k * 4) % 2 == 0:          # +1 pops next to the counter
            big_text(img, W // 2 + ow // 2 + 6 * scale, ty + 4 * scale, "+1", "large", 1, RED)
        y = ty + 26 * scale + (6 if s > 1 else 2)
        if k > 1.6:
            week = int(e.get("week") or 0)
            line = f"{ordinal(week)} TIME THIS WEEK" if week > 1 else "FIRST ONE THIS WEEK"
            text_center(d, W // 2, y, *fitted(line, W - 10, ("large", "bold", "body") if s > 1 else ("small", "tiny")),
                        MUSTARD)
            y += 20 if s > 1 else 8
        if k > 2.4 and y + 8 <= H - 3:
            where = f"AT {e['place'].upper()}" if e.get("place") else ""
            by = f"LOGGED BY {e['by'].upper()}" if e.get("by") and s > 1 else ""
            line = "  ·  ".join(v for v in (where, by) if v)
            if line:
                text_center(d, W // 2, y, *fitted(line, W - 10, ("body", "small", "tiny") if s > 1 else ("tiny",)),
                            CREAM)
