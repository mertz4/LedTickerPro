"""Tiles: compact widgets for one LED module (128x64, or 128x52 above the ticker). Used in 4-up "quad" slides."""
from __future__ import annotations

from datetime import datetime, timezone

from PIL import Image, ImageDraw

from . import icons
from .draw import (BLUE, DARK, DIM, GOLD, GRAY, GREEN, ORANGE, RED, WHITE, YELLOW, arrow, badge, big_text,
                   contrast_text, paste_icon, text, text_center, text_right)
from .fonts import fit, font
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


def _primetime_tag(g: dict, rc: RC, games: list[dict]) -> str:
    same = sum(1 for x in games if x["start_utc"] == g["start_utc"])
    if same != 1:
        return ""
    dt = datetime.fromisoformat(g["start_utc"]).astimezone(rc.ctx.tz)
    return {3: "TNF", 0: "MNF"}.get(dt.weekday(), "SNF" if dt.weekday() == 6 and dt.hour >= 18 else "")


class GameTile(Screen):
    """Compact scoreboard for one game: featured NFL game (TNF/SNF/MNF aware) or my team's spotlight."""

    def __init__(self, name: str, label: str, league: str | None):
        self.name, self.label, self.league = name, label, league

    def _game(self, rc):
        if self.league:
            g = featured_game(rc, self.league)
            return (self.league, g) if g else (None, None)
        games = my_games(rc)
        if not games:
            return None, None
        now = datetime.now(timezone.utc)
        games.sort(key=lambda lg_g: spotlight_rank(lg_g[0], lg_g[1], now))
        return games[0]

    def available(self, rc):
        return self._game(rc)[1] is not None

    def render(self, img, rc, t):
        lg, g = self._game(rc)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        games = (rc.data(f"scores.{lg}") or {}).get("games") or []
        tag = _primetime_tag(g, rc, games) if lg == "nfl" else ""
        head = (tag or lg.upper()) + ("  " + (g.get("broadcast") or "").upper()[:12] if g.get("broadcast") else "")
        text(d, 2, 1, head, "small", GOLD)
        size = 22 if H >= 60 else 18
        mine = {str(x) for x in rc.ctx.section("scores", lg).get("teams", [])}
        for tm, x in ((g["away"], 2), (g["home"], W - 2 - size)):
            draw_logo(img, rc, lg, tm, x, 9, size)
            lx = x + size // 2
            text_center(d, lx, 10 + size, team_label(tm)[:7], "small", GOLD if tm["id"] in mine else WHITE)
            if tm.get("record") and H >= 52:
                text_center(d, lx, 17 + size, tm["record"], "tiny", GRAY)
        cx = W // 2
        if g["state"] == "pre":
            dt = datetime.fromisoformat(g["start_utc"]).astimezone(rc.ctx.tz)
            text_center(d, cx, 10, dt.strftime("%a").upper(), "small", GRAY)
            text_center(d, cx, 18, short_time(g["start_utc"], rc.ctx.tz), "bold", WHITE)
        else:
            a, h = g["away"], g["home"]
            ca = GRAY if g["state"] == "post" and h.get("winner") else WHITE
            cb = GRAY if g["state"] == "post" and a.get("winner") else WHITE
            text_right(d, cx - 3, 12, str(a.get("score") or 0), "bold", ca)
            text(d, cx + 4, 12, str(h.get("score") or 0), "bold", cb)
            st = "FINAL" if g["state"] == "post" else (g.get("status") or "").upper()
            text_center(d, cx, 27, st[:11], "small", GRAY if g["state"] == "post" else YELLOW)
            if g.get("possession"):
                side_x = 2 + size + 2 if g["possession"] == a["abbr"] else W - 2 - size - 6
                d.rectangle([side_x, 18, side_x + 3, 20], fill=YELLOW)
        odds = g.get("odds") or {}
        y = H - 8
        if g.get("down_distance") and g["state"] == "in":
            text_center(d, cx, y, g["down_distance"][:22], "small", RED if g.get("red_zone") else WHITE)
        elif odds.get("details"):
            ou = f" O/U {odds['over_under']:g}" if odds.get("over_under") else ""
            pre = " PRE" if g.get("odds_pregame") else ""
            text_center(d, cx, y, f"{odds['details']}{ou}{pre}", "small", GOLD)


class WeatherTile(Screen):
    name, label = "weather", "Weather"

    def available(self, rc):
        w = rc.data("weather")
        return bool(w and (w.get("current") or w.get("periods")))

    def render(self, img, rc, t):
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
    grows_tall = True   # a list: a taller section shows more rows
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
        rows = max(1, (H - 1) // 10)
        pages = -(-len(qs) // rows)
        page = int(t // self.page_seconds) % pages
        colors = MarketsScreen()
        for i, q in enumerate(qs[page * rows:(page + 1) * rows]):
            y = 1 + i * 10
            bg, fg = colors._badge_colors(rc, q["symbol"], q.get("logo"))
            badge(img, 1, y, q["symbol"], bg, fg, "small", h=9, pad=2, min_w=30)
            p = q["price"]
            text_right(d, 76, y + 2, f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}", "small", WHITE)
            up = (q.get("change") or 0) >= 0
            pw = text_right(d, W - 2, y + 2, f"{abs(q.get('change_pct') or 0):.2f}%", "small", GREEN if up else RED)
            arrow(d, W - 2 - pw - 6, y + 2, up, 5)


class FantasyMiniTile(Screen):
    name, label = "fantasy_mini", "Fantasy: my matchup (tile)"

    def _m(self, rc):
        lg = rc.data("fantasy")
        if not lg or lg.get("my_team_id") is None:
            return None, None
        return lg, next((m for m in lg["matchups"] if m["mine"]), None)

    def available(self, rc):
        return self._m(rc)[1] is not None

    def render(self, img, rc, t):
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
    grows_tall = True   # a list: a taller section shows more rows
    name, label = "standings_mini", "Fantasy: standings (tile)"

    def available(self, rc):
        lg = rc.data("fantasy")
        return bool(lg and lg.get("teams"))

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams = lg["teams"]
        cut = lg.get("playoff_teams") or 6
        rows = -(-len(teams) // 2)
        rh = max(6, (H - 1) // max(1, rows))
        for i, tm in enumerate(teams):
            c, r = divmod(i, rows)
            x, y = c * (W // 2) + 1, 1 + r * rh
            col = GOLD if tm.get("mine") else (WHITE if tm["standing"] <= cut else GRAY)
            text(d, x, y, f"{tm['standing']:>2}", "tiny", GREEN if tm["standing"] <= cut else DIM)
            text(d, x + 9, y, (tm.get("abbrev") or tm["name"][:4]).upper()[:5], "tiny", col)
            text_right(d, x + W // 2 - 3, y, f"{tm['wins']}-{tm['losses']}", "tiny", col)
        d.line([(W // 2 - 1, 0), (W // 2 - 1, H - 1)], fill=DARK)


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


class NewsTile(Screen):
    grows_tall = True   # a list: a taller section shows more rows
    name, label = "news", "News headlines (tile)"
    page_seconds = 6.0

    def available(self, rc):
        return bool((rc.data("news") or {}).get("headlines"))

    def render(self, img, rc, t):
        hl = rc.data("news")["headlines"]
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        h = hl[int(t // self.page_seconds) % len(hl)]
        text(d, 2, 1, h["source"].upper(), "small", GOLD)
        words, lines, cur = h["title"].upper().split(), [], ""
        f = font("small")
        for wd in words:
            trial = (cur + " " + wd).strip()
            if f.getlength(trial) <= W - 4:
                cur = trial
            else:
                lines.append(cur)
                cur = wd
        lines.append(cur)
        for i, ln in enumerate(lines[: max(1, (H - 10) // 8)]):
            text(d, 2, 10 + i * 8, ln, "small", WHITE)


class ClockTile(Screen):
    name, label = "clock", "Clock (tile)"

    def render(self, img, rc, t):
        d = ImageDraw.Draw(img)
        now = datetime.now(rc.ctx.tz)
        big_text(img, rc.W // 2, 6, now.strftime("%I:%M").lstrip("0"), "huge", 1, WHITE, anchor="c")
        text_center(d, rc.W // 2, 30, now.strftime("%a %b %d").upper(), "body", GOLD)


class PotwMiniTile(Screen):
    name, label = "potw_mini", "Player of the week (tile)"
    page_seconds = 5.0

    def available(self, rc):
        lg = rc.data("fantasy") or {}
        return bool((lg.get("potw") or {}).get("players"))

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        pw = lg["potw"]
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        i = int(t // self.page_seconds) % len(pw["players"])
        p = pw["players"][i]
        text(d, 2, 1, f"WK {pw['week']} TOP #{i + 1}", "small", GOLD)
        head = rc.images.get(p.get("headshot"), 34)
        paste_icon(img, head, 2, 10)
        name, f = fit(p["name"].upper(), W - 42, ("small", "tiny"))
        text(d, 40, 11, name, _role(f), WHITE)
        text(d, 40, 19, f"{p['position']} {p['nfl_team']}", "tiny", GRAY)
        text(d, 40, 27, f"{p['points']:.1f}", "bold", GREEN)
        owner = next((x["name"] for x in lg["teams"] if x["id"] == p.get("fantasy_team_id")), "")
        if H >= 52:
            o, f2 = fit(owner.upper(), W - 4, ("tiny",))
            text(d, 2, H - 7, o, "tiny", GOLD if p.get("fantasy_team_id") == lg.get("my_team_id") else GRAY)


def all_tiles() -> dict[str, Screen]:
    tiles: list[Screen] = [
        WeatherTile(), MarketsTile(),
        GameTile("nfl_game", "NFL featured game (TNF/SNF/MNF aware)", "nfl"),
        GameTile("cfb_game", "College football featured game", "cfb"),
        GameTile("my_game", "My team spotlight (tile)", None),
        FantasyMiniTile(), StandingsMiniTile(), PropsMiniTile(), PotwMiniTile(), NewsTile(), ClockTile(),
    ]
    return {t.name: t for t in tiles}
