"""Tiles: compact widgets for one LED module (128x64, or 128x52 above the ticker). Used in 4-up "quad" slides."""
from __future__ import annotations

from datetime import datetime, timezone

from PIL import Image, ImageDraw

from . import icons
from .draw import (BLUE, DARK, DIM, GOLD, GRAY, GREEN, ORANGE, RED, WHITE, YELLOW, arrow, badge, big_text,
                   contrast_text, paste_icon, text, text_center, text_right)
from .fonts import fit, font, text_height, text_width
from .screens import (RC, Screen, _fantasy_logo, compact_when, draw_logo, my_games, short_time, spotlight_rank,
                      team_label)


def _role(f) -> str:
    for r in ("bold", "body", "small", "tiny"):
        if f is font(r):
            return r
    return "small"


def featured_game(rc: RC, league: str = "nfl"):
    """The game that matters now: live (mine/standalone first), else next up, else the latest final."""
    sb = rc.data(f"scores.{league}") or {}
    games = sb.get("games") or []
    if not games:
        return None
    starts: dict[str, int] = {}
    for g in games:
        starts[g["start_utc"]] = starts.get(g["start_utc"], 0) + 1
    live = [g for g in games if g["state"] == "in"]
    if live:
        live.sort(key=lambda g: (not g.get("mine"), starts[g["start_utc"]] != 1, g["start_utc"]))
        return live[0]
    pre = sorted((g for g in games if g["state"] == "pre"), key=lambda g: g["start_utc"])
    post = sorted((g for g in games if g["state"] == "post"), key=lambda g: g["start_utc"], reverse=True)
    now = datetime.now(timezone.utc)
    if post and (not pre or (now - datetime.fromisoformat(post[0]["start_utc"])).total_seconds() < 6 * 3600):
        return post[0]   # just finished (e.g. Tuesday morning after MNF)
    return pre[0] if pre else (post[0] if post else None)


def league_others(rc: RC, lg: str, g: dict) -> list[tuple[str, dict]]:
    """The rest of the league's slate after the featured game: live, then next up, then latest finals."""
    rank = {"in": 0, "pre": 1, "post": 2}
    games = [x for x in (rc.data(f"scores.{lg}") or {}).get("games") or [] if x["id"] != g["id"]]
    games.sort(key=lambda x: (rank.get(x["state"], 3), x["start_utc"] if x["state"] != "post" else ""))
    return [(lg, x) for x in games]


class GameTile(Screen):
    """One game for any section shape: the featured NFL / CFB game (TNF/SNF/MNF aware) or my team's spotlight.
    Spare room (a full or tall section) lists the other games that matter."""
    adaptive = True     # draws its own design for every section shape (gamecard.py)

    def __init__(self, name: str, label: str, league: str | None):
        self.name, self.label, self.league = name, label, league

    def _game(self, rc):
        if self.league:
            g = featured_game(rc, self.league)
            return (self.league, g, league_others(rc, self.league, g) if g else []) if g else (None, None, [])
        games = my_games(rc)
        if not games:
            return None, None, []
        now = datetime.now(timezone.utc)
        games.sort(key=lambda lg_g: spotlight_rank(lg_g[0], lg_g[1], now))
        return games[0][0], games[0][1], games[1:]

    def available(self, rc):
        return self._game(rc)[1] is not None

    def render(self, img, rc, t):
        from .gamecard import draw_game, primetime_tag
        lg, g, others = self._game(rc)
        games = (rc.data(f"scores.{lg}") or {}).get("games") or []
        draw_game(img, rc, lg, g, others, primetime_tag(g, rc, games) if lg == "nfl" else "")


class WeatherTile(Screen):
    name, label = "weather", "Weather"

    def available(self, rc):
        w = rc.data("weather")
        return bool(w and (w.get("current") or w.get("periods")))

    adaptive = True     # own designs for a wide strip, a tall column and a module (full screen = WeatherScreen)

    def render(self, img, rc, t):
        from .gamecard import shape
        sh = shape(rc.W, rc.main_h)
        if sh == "wide":
            return self._wide(img, rc, t)
        if sh in ("tall", "full"):
            return self._tall(img, rc, t)
        return self._tile(img, rc, t)

    @staticmethod
    def _now(w):
        cur = w.get("current") or {}
        periods = w.get("periods") or []
        p0 = periods[0] if periods else {}
        temp = cur.get("temp_f") if cur.get("temp_f") is not None else p0.get("temp_f")
        hi = next((p["temp_f"] for p in periods if p.get("is_day")), None)
        lo = next((p["temp_f"] for p in periods if not p.get("is_day")), None)
        cond = (cur.get("conditions") or p0.get("short") or "").upper()
        name, night = icons.icon_for(cur.get("icon") or p0.get("icon"), cur.get("conditions") or p0.get("short"))
        rain = max((p["precip_pct"] for p in periods[:2]), default=0)
        return cur, temp, hi, lo, cond, name, night, rain

    def _temp(self, img, d, x, y, temp, role, scale=1):
        tw = big_text(img, x, y, f"{temp}" if temp is not None else "--", role, scale, WHITE)
        r = 2 if role == "huge" else 1
        d.ellipse([x + tw + 1, y + 1, x + tw + 2 + 2 * r, y + 2 + 2 * r], outline=WHITE)
        return tw

    def _hours(self, img, d, rc, w, x0, y, width, n, isz, t, precip=True):
        hours = (w.get("hourly") or [])[1:1 + n]
        if not hours:
            return
        sw = width // len(hours)
        fr = icons.frame_at(t)
        for i, h in enumerate(hours):
            cx = x0 + i * sw + sw // 2
            text_center(d, cx, y, short_time(h["time"], rc.ctx.tz), "tiny" if sw < 26 else "small", GRAY)
            nm, nt = icons.icon_for(h.get("icon"), h.get("short"), h.get("is_day"))
            paste_icon(img, icons.get(nm, isz, nt, fr), cx - isz // 2, y + 8)
            text_center(d, cx, y + 10 + isz, f"{h['temp_f']}", "small", WHITE)
            if precip and h.get("precip_pct"):
                text_center(d, cx, y + 18 + isz, f"{h['precip_pct']}%", "tiny", BLUE)

    def _wide(self, img, rc, t):
        """Top/bottom half: now on the left (icon, temperature, conditions), the next hours on the right."""
        w = rc.data("weather")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        cur, temp, hi, lo, cond, name, night, rain = self._now(w)
        isz = max(24, min(44, H - 12))
        paste_icon(img, icons.get(name, isz, night, icons.frame_at(t)), 2, (H - isz) // 2)
        x = isz + 6
        role = "huge" if H >= 52 else "large"
        self._temp(img, d, x, 3, temp, role)
        loc, lf = fit((w.get("location") or "").split(",")[0].upper(), 70, ("small", "tiny"))
        ty = 3 + text_height(font(role)) + 3
        c, cf = fit(cond, 128 - x - 2, ("body", "small", "tiny"))
        text(d, x, ty, c, _role(cf) if cf is not font("body") else "body", GRAY)
        text(d, x, ty + 9, f"H{hi if hi is not None else '-'}  L{lo if lo is not None else '-'}", "small", WHITE)
        if rain:
            text(d, x + 44, ty + 9, f"{rain}%", "small", BLUE)
        n = max(3, min(6, (W - 132) // 22))
        d.line([(129, 3), (129, H - 4)], fill=DARK)
        self._hours(img, d, rc, w, 132, max(1, (H - 34) // 2), W - 134, n, 16, t, precip=H >= 50)
        self._alert_border(d, w, W, H, t)

    def _tall(self, img, rc, t):
        """Left/right half: now up top, a details row, then the next hours as a column of rows."""
        w = rc.data("weather")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        cur, temp, hi, lo, cond, name, night, rain = self._now(w)
        paste_icon(img, icons.get(name, 36, night, icons.frame_at(t)), 2, 2)
        self._temp(img, d, 42, 3, temp, "huge")
        loc, lf = fit((w.get("location") or "").split(",")[0].upper(), W - 44, ("small", "tiny"))
        text(d, 42, 26, loc, _role(lf), GOLD)
        c, cf = fit(cond, W - 4, ("body", "small", "tiny"))
        text(d, 2, 41, c, "body" if cf is font("body") else _role(cf), WHITE)
        stats = [("HI", f"{hi}" if hi is not None else "-"), ("LO", f"{lo}" if lo is not None else "-")]
        if cur.get("wind_mph") is not None:
            stats.append(("WIND", f"{cur.get('wind_dir') or ''}{cur['wind_mph']}"))
        if cur.get("humidity") is not None:
            stats.append(("HUM", f"{cur['humidity']}%"))
        cw = W // 2
        for i, (k, v) in enumerate(stats[:4]):
            x, y = (i % 2) * cw + 2, 51 + (i // 2) * 8
            text(d, x, y, k, "small", GRAY)
            text_right(d, x + cw - 6, y, v, "small", WHITE)
        y0 = 51 + 8 * ((len(stats[:4]) + 1) // 2) + 2
        d.line([(0, y0), (W - 1, y0)], fill=DARK)
        hours = (w.get("hourly") or [])[1:]
        rh = 12
        n = max(0, (H - y0 - 2) // rh)
        top = y0 + 2 + max(0, (H - y0 - 2 - n * rh) // 2)
        fr = icons.frame_at(t)
        for i, h in enumerate(hours[:n]):
            y = top + i * rh
            text(d, 2, y + 3, short_time(h["time"], rc.ctx.tz), "small", GRAY)
            nm, nt = icons.icon_for(h.get("icon"), h.get("short"), h.get("is_day"))
            paste_icon(img, icons.get(nm, 12, nt, fr), 20, y)
            tw = text_right(d, W - 24, y + 2, f"{h['temp_f']}", "body", WHITE)
            s, f = fit((h.get("short") or "").upper(), W - 24 - tw - 4 - 35, ("tiny",))
            text(d, 35, y + 3, s, "tiny", DIM)
            if h.get("precip_pct"):
                text_right(d, W - 2, y + 3, f"{h['precip_pct']}%", "small", BLUE)
        self._alert_border(d, w, W, H, t)

    @staticmethod
    def _alert_border(d, w, W, H, t):
        alerts = w.get("alerts") or []
        if alerts and int(t * 2) % 2 == 0:
            col = RED if alerts[0]["level"] == "warning" else ORANGE
            d.rectangle([0, 0, W - 1, H - 1], outline=col)

    def _tile(self, img, rc, t):
        w = rc.data("weather")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        cur = w.get("current") or {}
        periods = w.get("periods") or []
        p0 = periods[0] if periods else {}
        nm, night = icons.icon_for(cur.get("icon") or p0.get("icon"), cur.get("conditions") or p0.get("short"))
        paste_icon(img, icons.get(nm, 32, night, icons.frame_at(t)), 2, 2)
        temp = cur.get("temp_f") if cur.get("temp_f") is not None else p0.get("temp_f")
        tw = big_text(img, 38, 3, f"{temp}" if temp is not None else "--", "large", 1, WHITE)
        d.ellipse([38 + tw + 1, 4, 38 + tw + 4, 7], outline=WHITE)
        hi = next((p["temp_f"] for p in periods if p.get("is_day")), None)
        lo = next((p["temp_f"] for p in periods if not p.get("is_day")), None)
        text(d, 38, 24, f"H{hi if hi is not None else '-'} L{lo if lo is not None else '-'}", "small", GRAY)
        rain = max((p["precip_pct"] for p in periods[:2]), default=0)
        if rain:
            text_right(d, W - 2, 24, f"{rain}%", "small", BLUE)
        if cur.get("wind_mph") is not None:
            text_right(d, W - 2, 4, f"{cur.get('wind_dir') or ''}{cur['wind_mph']}", "small", GRAY)
        cond, f = fit((cur.get("conditions") or p0.get("short") or "").upper(), W - 4, ("small", "tiny"))
        text(d, 2, 36, cond, _role(f), WHITE)
        hours = (w.get("hourly") or [])[1:1 + max(1, min(8, W // 32))]   # as many as fit (~32 px each)
        if hours and H >= 52:
            sw = W // len(hours)
            for i, h in enumerate(hours):
                text_center(d, i * sw + sw // 2, H - 8, f"{short_time(h['time'], rc.ctx.tz)} {h['temp_f']}", "tiny", GRAY)
        alerts = w.get("alerts") or []
        if alerts:
            col = RED if alerts[0]["level"] == "warning" else ORANGE
            if int(t * 2) % 2 == 0:
                d.rectangle([0, 0, W - 1, H - 1], outline=col)


class MarketsTile(Screen):
    adaptive = True     # a list: taller sections show more rows, wider ones more columns
    name, label = "markets", "Markets"
    page_seconds = 4.0

    def _quotes(self, rc):
        out = []
        for n in ("markets.stocks", "markets.crypto"):
            out += [q for q in ((rc.data(n) or {}).get("quotes") or []) if not q.get("error")]
        return out

    def available(self, rc):
        return bool(self._quotes(rc))

    def render(self, img, rc, t):
        from .screens import MarketsScreen
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        qs = self._quotes(rc)
        cols = max(1, W // 120)                       # a wide strip gets side-by-side columns
        cw = W // cols
        # 10 px rows normally; squeeze to 9 when that needs fewer pages (all 12 quotes on one page, say)
        fit_rows = max(1, (H - 1) // 10)
        tight = max(1, (H - 1) // 9)
        if -(-len(qs) // (tight * cols)) < -(-len(qs) // (fit_rows * cols)):
            fit_rows = tight
        pages = -(-len(qs) // (fit_rows * cols))
        per = -(-len(qs) // pages)                    # spread evenly: no page with a lone quote
        rows = -(-per // cols)
        rh = max(9, min(13, (H - 1) // rows))
        bh = min(9, rh - 1)
        top = max(1, (H - rows * rh) // 2 + 1)
        page = int(t // self.page_seconds) % pages
        colors = MarketsScreen()
        for i, q in enumerate(qs[page * per:(page + 1) * per]):
            c, r = divmod(i, rows)
            x, y = c * cw, top + r * rh + (rh - bh) // 2
            bg, fg = colors._badge_colors(rc, q["symbol"], q.get("logo"))
            badge(img, x + 1, y, q["symbol"], bg, fg, "small", h=bh, pad=2, min_w=30)
            ty = y + (bh - 6) // 2
            p = q["price"]
            text_right(d, x + 76, ty, f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}", "small", WHITE)
            up = (q.get("change") or 0) >= 0
            pw = text_right(d, x + cw - 3, ty, f"{abs(q.get('change_pct') or 0):.2f}%", "small", GREEN if up else RED)
            arrow(d, x + cw - 3 - pw - 6, ty, up, 5)
        for c in range(1, cols):
            d.line([(c * cw - 1, 1), (c * cw - 1, H - 2)], fill=DARK)


class FantasyMiniTile(Screen):
    name, label = "fantasy_mini", "Fantasy: my matchup (tile)"

    def _m(self, rc):
        lg = rc.data("fantasy")
        if not lg or lg.get("my_team_id") is None:
            return None, None
        return lg, next((m for m in lg["matchups"] if m["mine"]), None)

    def available(self, rc):
        return self._m(rc)[1] is not None

    adaptive = True     # own designs for a wide strip, a tall column and a module

    def render(self, img, rc, t):
        from .gamecard import shape
        sh = shape(rc.W, rc.main_h)
        if sh == "full":
            from .screens import FantasyMatchup
            return FantasyMatchup().render(img, rc, t)
        return {"wide": self._wide, "tall": self._tall}.get(sh, self._tile)(img, rc, t)

    def _sides(self, lg, m):
        teams = {x["id"]: x for x in lg["teams"]}
        me = m["home"] if m["home"]["team_id"] == lg["my_team_id"] else m["away"]
        op = m["away"] if me is m["home"] else m["home"]
        return teams, me, op

    @staticmethod
    def _share(me, op):
        a, b, label = me["points"], (op or {}).get("points", 0), "LIVE"
        if a + b == 0:
            a, b, label = me.get("projected") or 1, (op or {}).get("projected") or 1, "PROJ"
        return (a / (a + b) if a + b else 0.5), label

    @staticmethod
    def _bar(d, x0, x1, y, h, share):
        split = int(x0 + (x1 - x0) * share)
        d.rectangle([x0, y, split, y + h - 1], fill=GREEN if share >= 0.5 else ORANGE)
        d.rectangle([split + 1, y, x1, y + h - 1], fill=DIM)

    @staticmethod
    def _left(lg):
        left = [p for p in lg.get("my_roster", []) if p["starter"] and p.get("points") is None]
        return f"{len(left)} PLAYERS LEFT" if left else "ALL PLAYED"

    def _wide(self, img, rc, t):
        """Top/bottom half: each team at its edge with logo, name, record and big score; the share bar below."""
        lg, m = self._m(rc)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams, me, op = self._sides(lg, m)
        foot = 13
        logo = max(16, min(32, H - foot - 4))
        cx = W // 2
        for side, left in ((me, True), (op, False)):
            if not side:
                continue
            tm = teams[side["team_id"]]
            x = 2 if left else W - 2 - logo
            ly = (H - foot - logo) // 2
            _fantasy_logo(img, rc, tm, x, ly, logo)
            nx = x + logo + 4 if left else x - 4
            pts = f"{side['points']:.1f}"
            room = (cx - 4 - nx) if left else (nx - (cx + 4))      # the name gets the whole top line
            name, f = fit(tm["name"], room, ("body", "small", "tiny"))
            rec = f"{tm['wins']}-{tm['losses']}" + (f"-{tm['ties']}" if tm["ties"] else "") + f"  #{tm['standing']}"
            proj = f"PROJ {side['projected']:.1f}" if side.get("projected") is not None else ""
            put = text if left else text_right
            put(d, nx, ly + 1, name, _role(f) if f is not font("body") else "body", GOLD if left else WHITE)
            put(d, nx, ly + 11, rec, "small", GRAY)
            if proj and logo >= 26:
                put(d, nx, ly + 19, proj, "tiny", DIM)
            sy = max(ly + 10, ly + logo - 18)                       # score sits under the name, beside the record
            if left:
                text_right(d, cx - 6, sy, pts, "large", WHITE)
            else:
                text(d, cx + 6, sy, pts, "large", WHITE)
        share, label = self._share(me, op)
        by = H - foot + 2
        self._bar(d, 2, W - 3, by, 3, share)
        text(d, 2, by + 5, f"FANTASY WK {lg['week']}", "tiny", GOLD)
        text_center(d, cx, by + 5, f"{label} {share * 100:.0f}%", "tiny", WHITE)
        text_right(d, W - 2, by + 5, self._left(lg), "tiny", GRAY)

    def _tall(self, img, rc, t):
        """Left/right half: the two teams stacked as rows, the share bar, then my starters with their points."""
        lg, m = self._m(rc)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams, me, op = self._sides(lg, m)
        text(d, 2, 1, f"FANTASY WK {lg['week']}", "small", GOLD)
        share, label = self._share(me, op)
        text_right(d, W - 2, 1, f"{label} {share * 100:.0f}%", "small", GRAY)
        y = 10
        for side, mine in ((me, True), (op, False)):
            if not side:
                continue
            tm = teams[side["team_id"]]
            _fantasy_logo(img, rc, tm, 2, y, 18)
            pts = f"{side['points']:.1f}"
            pw = text_right(d, W - 2, y + 3, pts, "bold", WHITE)
            name, f = fit(tm["name"], W - 26 - pw - 4, ("body", "small", "tiny"))
            text(d, 23, y + 1, name, "body" if f is font("body") else _role(f), GOLD if mine else WHITE)
            if side.get("projected") is not None:
                text(d, 23, y + 10, f"PROJ {side['projected']:.1f}", "tiny", GRAY)
            y += 21
        self._bar(d, 2, W - 3, y, 3, share)
        y += 6
        starters = [p for p in lg.get("my_roster", []) if p["starter"]]
        rows = max(0, (H - y - 1) // 8)
        if rows < len(starters) and rows:
            rows -= 1                                   # keep a line for "N PLAYERS LEFT"
        for i, p in enumerate(starters[:rows]):
            ry = y + i * 8
            inj = p.get("injury") or ""
            col = RED if inj in ("OUT", "INJURY_RESERVE", "SUSPENSION") else ORANGE if inj in ("QUESTIONABLE", "DOUBTFUL") else WHITE
            text(d, 2, ry, p["slot"][:4], "small", GRAY)
            pts = f"{p['points']:.1f}" if p.get("points") is not None else (f"{p['projected']:.0f}P" if p.get("projected") else "-")
            pw = text_right(d, W - 2, ry, pts, "small", WHITE if p.get("points") is not None else DIM)
            last = p["name"].split(" ", 1)[-1] if p["position"] != "D/ST" else p["name"].replace(" D/ST", " D")
            nm = f"{p['name'][0]}.{last}" if p["position"] != "D/ST" else last
            s, f = fit(nm, W - 24 - pw - 4, ("small", "tiny"))
            text(d, 22, ry, s, _role(f), col)
        if rows < len(starters):
            text_center(d, W // 2, H - 7, self._left(lg), "tiny", GRAY)

    def _tile(self, img, rc, t):
        lg, m = self._m(rc)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams = {x["id"]: x for x in lg["teams"]}
        me = m["home"] if m["home"]["team_id"] == lg["my_team_id"] else m["away"]
        op = m["away"] if me is m["home"] else m["home"]
        text(d, 2, 1, f"FANTASY WK {lg['week']}", "small", GOLD)
        for side, x, align in ((me, 2, "l"), (op, W - 18, "r")):
            if not side:
                continue
            tm = teams[side["team_id"]]
            _fantasy_logo(img, rc, tm, x, 9, 16)
            name = (tm.get("abbrev") or tm["name"][:4]).upper()
            pts = f"{side['points']:.1f}"
            if align == "l":
                text(d, 20, 9, name, "small", GOLD)
                text(d, 20, 17, pts, "bold", WHITE)
                if side.get("projected") is not None:
                    text(d, 2, 32, f"P {side['projected']:.1f}", "tiny", GRAY)
            else:
                text_right(d, W - 20, 9, name, "small", WHITE)
                text_right(d, W - 20, 17, pts, "bold", WHITE)
                if side.get("projected") is not None:
                    text_right(d, W - 2, 32, f"P {side['projected']:.1f}", "tiny", GRAY)
        a, b = me["points"], (op or {}).get("points", 0)
        if a + b == 0:
            a, b = me.get("projected") or 1, (op or {}).get("projected") or 1
        share = a / (a + b) if a + b else 0.5
        y = 40
        split = int(2 + (W - 5) * share)
        d.rectangle([2, y, split, y + 2], fill=GREEN if share >= 0.5 else ORANGE)
        d.rectangle([split + 1, y, W - 3, y + 2], fill=DIM)
        if H >= 52:
            left = [p for p in lg.get("my_roster", []) if p["starter"] and p.get("points") is None]
            msg = f"{len(left)} PLAYERS LEFT" if left else "ALL PLAYED"
            text_center(d, W // 2, H - 7, msg, "tiny", GRAY)


class StandingsMiniTile(Screen):
    adaptive = True     # rows grow into a taller section (logos, points-for), names into a wider one
    name, label = "standings_mini", "Fantasy: standings (tile)"

    def available(self, rc):
        lg = rc.data("fantasy")
        return bool(lg and lg.get("teams"))

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        if rc.W >= 192 and rc.main_h >= 96:
            from .screens import FantasyStandings
            return FantasyStandings().render(img, rc, t)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams = lg["teams"]
        cut = lg.get("playoff_teams") or 6
        rows = -(-len(teams) // 2)
        cw = W // 2
        rh = max(6, (H - 1) // max(1, rows))
        top = 1 + ((H - 1) - rows * rh) // 2
        rich = rh >= 14                                   # two-line rows with a logo
        for i, tm in enumerate(teams):
            c, r = divmod(i, rows)
            x, y = c * cw + 1, top + r * rh
            inside = tm["standing"] <= cut
            col = GOLD if tm.get("mine") else (WHITE if inside else GRAY)
            rec = f"{tm['wins']}-{tm['losses']}" + (f"-{tm['ties']}" if tm["ties"] else "")
            if tm.get("mine"):
                d.rectangle([x - 1, y - 1, x + cw - 3, y + rh - 2], fill=(40, 30, 10))
            if rich:
                lsz = min(14, rh - 3)
                text(d, x, y + (rh - 7) // 2, f"{tm['standing']:>2}", "small", GREEN if inside else DIM)
                _fantasy_logo(img, rc, tm, x + 10, y + (rh - 1 - lsz) // 2, lsz)
                nx = x + 12 + lsz
                ty = y + (rh - 1 - 13) // 2
                rw = text_right(d, x + cw - 4, ty, rec, "small", col)
                nm, f = fit((tm.get("abbrev") or tm["name"]).upper() if cw < 100 else tm["name"],
                            x + cw - 4 - rw - 3 - nx, ("small", "tiny"))
                text(d, nx, ty, nm, _role(f), col)
                sw = text_right(d, x + cw - 4, ty + 7, tm.get("streak") or "", "tiny",
                                GREEN if (tm.get("streak") or "W")[0] == "W" else RED)
                pf = f"{tm['points_for']:.0f} PF"
                if nx + text_width(pf, font("tiny")) + 3 > x + cw - 4 - sw:
                    pf = f"{tm['points_for']:.0f}"
                text(d, nx, ty + 7, pf, "tiny", DIM)
            else:
                ty = y + (rh - 6) // 2
                text(d, x, ty, f"{tm['standing']:>2}", "tiny", GREEN if inside else DIM)
                rw = text_right(d, x + cw - 3, ty, rec, "tiny", col)
                full = cw >= 110                           # a wide strip has room for the real names
                nm, f = fit(tm["name"] if full else (tm.get("abbrev") or tm["name"][:4]).upper()[:5],
                            x + cw - 3 - rw - 4 - (x + 9), ("tiny",))
                text(d, x + 9, ty, nm, "tiny", col)
        # the column split is the playoff line when the top half of the league makes it
        d.line([(cw - 1, 0), (cw - 1, H - 1)], fill=GREEN if cut == rows else DARK)


class PropsMiniTile(Screen):
    grows_tall = True   # a list: a taller section shows more rows
    name, label = "props_mini", "Player props (tile)"
    page_seconds = 5.0

    def available(self, rc):
        b = rc.data("props")
        return bool(b and b.get("props"))

    def render(self, img, rc, t):
        b = rc.data("props")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        g = (b.get("games") or [{}])[0]
        text(d, 2, 1, f"PROPS {g.get('reason', '')}", "small", GOLD)
        text_right(d, W - 2, 1, g.get("label", "")[:11], "small", GRAY)
        rows = max(1, (H - 9) // 8)
        props = b["props"]
        pages = -(-len(props) // rows)
        page = int(t // self.page_seconds) % pages
        for i, p in enumerate(props[page * rows:(page + 1) * rows]):
            y = 9 + i * 8
            who = (p.get("short_name") or p["name"]).split(" ")[-1].upper()[:9]
            col = BLUE if p.get("pinned") else GOLD if p["my_fantasy"] else (ORANGE if p["my_team"] else WHITE)
            text(d, 1, y, who, "tiny", col)
            text(d, 40, y, p["market"][:8], "tiny", GRAY)
            text_right(d, 96, y, f"{p['line']:g}", "tiny", WHITE)
            if p.get("live") is not None:
                over = p["live"] > p["line"]
                text_right(d, W - 2, y, f"{p['live']:g}", "tiny", GREEN if over else YELLOW)
            elif p.get("move"):
                text_right(d, W - 2, y, f"{p['move']:+g}", "tiny", GREEN if p["move"] > 0 else RED)


def wrap(s: str, f, width: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if f.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


class NewsTile(Screen):
    """The current headline as big as the section allows; leftover room lists the next ones."""
    adaptive = True
    name, label = "news", "News headlines"
    page_seconds = 6.0

    def available(self, rc):
        return bool((rc.data("news") or {}).get("headlines"))

    def render(self, img, rc, t):
        hl = rc.data("news")["headlines"]
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        idx = int(t // self.page_seconds) % len(hl)
        h = hl[idx]
        text(d, 2, 1, h["source"].upper(), "small", GOLD)
        text_right(d, W - 2, 1, f"{idx + 1}/{len(hl)}", "small", DIM)
        title = h["title"].upper()
        body_h = H - 10
        # the headline may use up to ~60% of a big section (the rest lists what's next), all of a small one
        budget = body_h if body_h < 60 else int(body_h * 0.6)
        role, lines = "small", wrap(title, font("small"), W - 4)
        for r in ("large", "bold", "body", "small"):
            ls = wrap(title, font(r), W - 4)
            if len(ls) * (text_height(font(r)) + 2) <= budget:
                role, lines = r, ls
                break
        lh = text_height(font(role)) + 2
        lines = lines[:max(1, body_h // lh)]
        for i, ln in enumerate(lines):
            text(d, 2, 10 + i * lh, ln, role, WHITE)
        y = 10 + len(lines) * lh + 2
        rest = [hl[(idx + k) % len(hl)] for k in range(1, len(hl))]
        if y + 10 <= H and rest:
            d.line([(0, y), (W - 1, y)], fill=DARK)
            y += 3
            for nxt in rest:
                if y + 7 > H:
                    break
                src = nxt["source"].upper().replace("ESPN ", "")
                sw = text(d, 2, y, src, "tiny", GOLD) + 3
                s, f = fit(nxt["title"].upper(), W - 4 - sw, ("small",))
                text(d, 2 + sw, y, s, "small", GRAY)
                y += 9


class ClockTile(Screen):
    """Time as big as the section allows, the date under it, and the current weather when there's room."""
    adaptive = True
    name, label = "clock", "Clock"

    def render(self, img, rc, t):
        from .draw import big_width
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        now = datetime.now(rc.ctx.tz)
        hm = now.strftime("%I:%M").lstrip("0")
        date = now.strftime("%A %b %d").upper() if W >= 192 else now.strftime("%a %b %d").upper()
        w = rc.data("weather") or {}
        cur = w.get("current") or {}
        has_wx = cur.get("temp_f") is not None
        date_role = "bold" if H >= 80 else "body"
        dh = text_height(font(date_role))
        side_wx = has_wx and W >= 192 and H < 70           # a wide strip: weather beside the time, not under it
        wx_h = 18 if has_wx and H >= 70 else 0
        cw = W * 2 // 3 if side_wx else W                  # width the time and date are centered in
        scale = 1
        for k in (4, 3, 2):
            if big_width(hm, "huge", k) + 18 <= cw - 6 and 20 * k + 4 + dh + (wx_h + 4 if wx_h else 0) <= H - 4:
                scale = k
                break
        total = 20 * scale + 4 + dh + (wx_h + 4 if wx_h else 0)
        y = max(1, (H - total) // 2)
        tw = big_text(img, cw // 2 - 8, y, hm, "huge", scale, WHITE, anchor="c")
        text(d, cw // 2 - 8 + tw // 2 + 3, y + 20 * scale - 8, now.strftime("%p"), "body", GRAY)
        y += 20 * scale + 4
        date, f = fit(date, cw - 4, (date_role, "body", "small"))
        text_center(d, cw // 2, y, date, _role(f) if f not in (font("bold"), font("body")) else
                    ("bold" if f is font("bold") else "body"), GOLD)
        if side_wx:
            nm, night = icons.icon_for(cur.get("icon"), cur.get("conditions"))
            x0, isz = cw + 4, min(32, H - 22)
            d.line([(cw, 4), (cw, H - 5)], fill=DARK)
            blk = isz + 2 + 7
            wy = (H - blk) // 2
            paste_icon(img, icons.get(nm, isz, night, icons.frame_at(t)), x0 + 2, wy)
            tw2 = big_text(img, x0 + isz + 6, wy + (isz - 18) // 2, f"{cur['temp_f']}", "large", 1, WHITE)
            d.ellipse([x0 + isz + 7 + tw2, wy + (isz - 18) // 2, x0 + isz + 10 + tw2, wy + (isz - 18) // 2 + 3], outline=WHITE)
            c, cf = fit((cur.get("conditions") or "").upper(), W - x0 - 2, ("small", "tiny"))
            text_center(d, (x0 + W) // 2, wy + isz + 2, c, _role(cf), GRAY)
            return
        if wx_h:
            y += dh + 4
            nm, night = icons.icon_for(cur.get("icon"), cur.get("conditions"))
            cond = (cur.get("conditions") or "").upper()
            label = f"{cur['temp_f']}  {cond}"
            s, sf = fit(label, W - 26, ("body", "small"))
            lw = 18 + text_width(s, sf)
            x = (W - lw) // 2
            paste_icon(img, icons.get(nm, 16, night, icons.frame_at(t)), x, y)
            text(d, x + 18, y + (16 - text_height(sf)) // 2, s, "body" if sf is font("body") else "small", WHITE)


class PotwMiniTile(Screen):
    name, label = "potw_mini", "Player of the week (tile)"
    page_seconds = 5.0

    def available(self, rc):
        lg = rc.data("fantasy") or {}
        return bool((lg.get("potw") or {}).get("players"))

    adaptive = True     # the headshot and type grow with the section

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        W, H = rc.W, rc.main_h
        if W >= 192 and H >= 96:
            from .screens import PlayerOfWeek
            return PlayerOfWeek().render(img, rc, t)
        pw = lg["potw"]
        d = ImageDraw.Draw(img)
        i = int(t // self.page_seconds) % len(pw["players"])
        p = pw["players"][i]
        owner = next((x["name"] for x in lg["teams"] if x["id"] == p.get("fantasy_team_id")), "FREE AGENT")
        ocol = GOLD if p.get("fantasy_team_id") == lg.get("my_team_id") else GRAY
        head_txt = f"WK {pw['week']} TOP #{i + 1}"
        if H >= 96:                                   # tall column: headshot on top, details stacked under it
            text(d, 2, 1, head_txt, "small", GOLD)
            text_right(d, W - 2, 1, f"{i + 1}/{len(pw['players'])}", "small", DIM)
            hs = max(32, min(W - 40, H - 68))         # leaves room for name, position, points and owner
            paste_icon(img, rc.images.get(p.get("headshot"), hs), (W - hs) // 2, 9)
            y = 9 + hs + 2
            name, f = fit(p["name"].upper(), W - 4, ("bold", "body", "small"))
            text_center(d, W // 2, y + 13 - text_height(f), name, _role(f) if f is not font("bold") else "bold", WHITE)
            text_center(d, W // 2, y + 15, f"{p['position']}  {p['nfl_team']}", "small", GRAY)
            big_text(img, W // 2, y + 24, f"{p['points']:.1f}", "huge", 1, GREEN, anchor="c")
            o, f2 = fit(owner.upper(), W - 4, ("small", "tiny"))
            if y + 47 + 7 <= H:
                text_center(d, W // 2, y + 47, o, _role(f2), ocol)
            return
        hs = max(28, min(H - 10, 56 if W >= 192 else 44))
        paste_icon(img, rc.images.get(p.get("headshot"), hs), 2, 9 + (H - 9 - hs) // 2)
        x = hs + 6
        text(d, 2, 1, head_txt, "small", GOLD)
        if W >= 192:                                  # wide strip: big name and points beside the headshot
            text_right(d, W - 2, 1, f"{i + 1}/{len(pw['players'])}", "small", DIM)
            pts = f"{p['points']:.1f}"
            pw_ = big_text(img, W - 4, 10 + (H - 10 - 20) // 2, pts, "huge", 1, GREEN, anchor="r")
            name, f = fit(p["name"].upper(), W - x - pw_ - 10, ("large", "bold", "body"))
            role = "large" if f is font("large") else "bold" if f is font("bold") else "body"
            nh = text_height(f)
            y = 9 + (H - 9 - (nh + 3 + 8 + 2 + 7)) // 2
            text(d, x, y, name, role, WHITE)
            text(d, x, y + nh + 3, f"{p['position']}  {p['nfl_team']}", "body", GRAY)
            o, f2 = fit(owner.upper(), W - x - pw_ - 10, ("small", "tiny"))
            text(d, x, y + nh + 13, o, _role(f2), ocol)
            return
        name, f = fit(p["name"].upper(), W - x - 2, ("small", "tiny"))
        text(d, x, 11, name, _role(f), WHITE)
        text(d, x, 19, f"{p['position']} {p['nfl_team']}", "tiny", GRAY)
        text(d, x, 27, f"{p['points']:.1f}", "bold", GREEN)
        if H >= 52:
            o, f2 = fit(owner.upper(), W - x - 2, ("tiny",))
            text(d, x, H - 8, o, "tiny", ocol)


def all_tiles() -> dict[str, Screen]:
    tiles: list[Screen] = [
        WeatherTile(), MarketsTile(),
        GameTile("nfl_game", "NFL featured game (TNF/SNF/MNF aware)", "nfl"),
        GameTile("cfb_game", "College football featured game", "cfb"),
        GameTile("my_game", "My team spotlight (tile)", None),
        FantasyMiniTile(), StandingsMiniTile(), PropsMiniTile(), PotwMiniTile(), NewsTile(), ClockTile(),
    ]
    return {t.name: t for t in tiles}
