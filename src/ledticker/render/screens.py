"""Screens: each draws one full layout from cached data. Layouts scale from the display geometry.

Frame = main area (top) + a global scrolling ticker strip (bottom, drawn by the engine).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image, ImageDraw

from ..config import Context
from ..store import Store
from . import icons
from .draw import (BLUE, DARK, DIM, GOLD, GRAY, GREEN, MAROON, ORANGE, RED, WHITE, YELLOW, arrow, badge, big_text,
                   big_width, contrast_text, dim, hex_color, paste_icon, readable_on_black, team_fallback, text, text_center,
                   text_right, triangle_right)
from .fonts import fit, font, text_height, text_width
from .images import ImageCache, dominant_color

LEAGUE_ORDER = ["nfl", "cfb", "nba", "ncaab", "mlb", "cbase"]
LEAGUE_NAMES = {"nfl": "NFL", "cfb": "COLLEGE FOOTBALL", "nba": "NBA", "ncaab": "COLLEGE BASKETBALL",
                "mlb": "MLB", "cbase": "COLLEGE BASEBALL"}
LEAGUE_SHORT = {"nfl": "NFL", "cfb": "CFB", "nba": "NBA", "ncaab": "CBB", "mlb": "MLB", "cbase": "CBASE"}
ESPN_LOGO_PATH = {"nfl": "nfl", "nba": "nba", "mlb": "mlb", "cfb": "ncaa", "ncaab": "ncaa", "cbase": "ncaa"}


@dataclass
class RC:
    """Render context passed to every screen."""
    ctx: Context
    store: Store
    images: ImageCache
    W: int
    H: int
    main_h: int
    now: datetime

    def data(self, name: str) -> Any:
        return self.store.get(name)


def team_logo_url(league: str, team: dict) -> str | None:
    if team.get("logo"):
        return team["logo"]
    path = ESPN_LOGO_PATH.get(league, league)
    key = team.get("id") if path == "ncaa" else (team.get("abbr") or "").lower()
    return f"https://a.espncdn.com/i/teamlogos/{path}/500/{key}.png" if key else None


def draw_logo(img, rc: RC, league: str, team: dict, x: int, y: int, size: int) -> None:
    im = rc.images.get(team_logo_url(league, team), size)
    if not paste_icon(img, im, x, y):
        team_fallback(img, x, y, size, team.get("abbr") or "?", hex_color(team.get("color")))


def short_time(iso: str, tz) -> str:
    dt = datetime.fromisoformat(iso).astimezone(tz)
    h = dt.strftime("%I").lstrip("0")
    return f"{h}:{dt:%M}{'A' if dt.hour < 12 else 'P'}" if dt.minute else f"{h}{'A' if dt.hour < 12 else 'P'}"


def day_label(iso: str, tz, now: datetime) -> str:
    dt = datetime.fromisoformat(iso).astimezone(tz)
    if dt.date() == now.astimezone(tz).date():
        return "TODAY"
    if dt.date() == (now.astimezone(tz) + timedelta(days=1)).date():
        return "TMRW"
    return dt.strftime("%a").upper()


def compact_when(iso: str, tz, now: datetime) -> str:
    """'7P' today, otherwise 'SAT 2:30P' (fits a 9-char slot)."""
    dt = datetime.fromisoformat(iso).astimezone(tz)
    if dt.date() == now.astimezone(tz).date():
        return short_time(iso, tz)
    return f"{dt:%a}".upper() + " " + short_time(iso, tz)


def role_of_font(f) -> str:
    """The role name of a font returned by fit()."""
    for r in ("huge", "large", "bold", "medium", "body", "small", "tiny"):
        if font(r) is f:
            return r
    return "small"


def team_label(t: dict) -> str:
    return f"#{t['rank']} {t['abbr']}" if t.get("rank") else t["abbr"]


class Screen:
    name = ""
    label = ""
    duration = 12.0

    def available(self, rc: RC) -> bool:
        return True

    def seconds(self, rc: RC) -> float:
        return self.duration

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        raise NotImplementedError


# ====================================================================== my-team spotlight

def my_games(rc: RC) -> list[tuple[str, dict]]:
    out = []
    for lg in LEAGUE_ORDER:
        sb = rc.data(f"scores.{lg}")
        if not sb:
            continue
        seen = set()
        for g in (sb.get("games") or []) + (sb.get("next_games") or []):
            if g.get("mine") and g["id"] not in seen:
                seen.add(g["id"])
                out.append((lg, g))
    return out


def spotlight_rank(lg: str, g: dict, now: datetime) -> tuple:
    start = datetime.fromisoformat(g["start_utc"])
    hours = (start - now).total_seconds() / 3600
    if g["state"] == "in":
        bucket = 0
    elif g["state"] == "post" and hours > -18:
        bucket = 2
    elif g["state"] == "pre" and hours < 36:
        bucket = 1
    elif g["state"] == "pre":
        bucket = 3
    else:
        bucket = 4
    return (bucket, abs(hours), LEAGUE_ORDER.index(lg))


class GameSpotlight(Screen):
    name, label = "game", "My team spotlight"
    duration = 15

    def pick(self, rc: RC):
        games = my_games(rc)
        if not games:
            return None, []
        now = datetime.now(timezone.utc)
        games.sort(key=lambda lg_g: spotlight_rank(lg_g[0], lg_g[1], now))
        return games[0], games[1:]

    def available(self, rc):
        return self.pick(rc)[0] is not None

    def render(self, img, rc, t):
        from .gamecard import draw_game, primetime_tag
        (lg, g), others = self.pick(rc)
        games = (rc.data(f"scores.{lg}") or {}).get("games") or []
        draw_game(img, rc, lg, g, others, primetime_tag(g, rc, games) if lg == "nfl" else "")


# ====================================================================== league board (paged)

class LeagueBoard(Screen):
    """The league's slate. Big sections get a paged grid; small ones (a quadrant) get one game at a time, each
    raining in pixel by pixel and crumbling away for the next."""
    per_page = 8
    page_seconds = 8.0
    hold = 6.0          # single-game mode: seconds each game is shown
    fx_in, fx_out = 0.9, 1.1

    def __init__(self, league: str):
        self.league = league
        self.name = f"board.{league}"
        self.label = f"{LEAGUE_NAMES[league].title()} board"
        self._cards: dict = {}
        self._base, self._last_t = 0, 0.0   # where the one-at-a-time rotation left off

    def _games(self, rc):
        sb = rc.data(f"scores.{self.league}")
        return (sb or {}).get("games") or []

    def _grid(self, rc) -> tuple[int, int]:
        """(columns, rows) of the paged grid for this section size: one ~128 px column per module width, and as
        many 22 px rows as fit, but no more rows than the games need (the rows then stretch to fill)."""
        cols = max(1, rc.W // 120)
        fit_rows = max(1, (rc.main_h - 11) // 22)
        need = -(-len(self._games(rc)) // cols) if self._games(rc) else 1
        return cols, max(1, min(fit_rows, need))

    def _single(self, rc) -> bool:
        """One game at a time (with the pixel rain) only in a small section; a tall or wide one gets the list."""
        return (rc.W < 192 and rc.main_h < 80) or (rc.main_h - 11) // 22 < 1 or rc.W < 100

    def available(self, rc):
        sb = rc.data(f"scores.{self.league}")
        st = rc.store.states.get(f"scores.{self.league}")
        if not sb or (st and st.dormant_until) or sb.get("offseason"):
            return False
        return len(self._games(rc)) > 1

    def _period(self):
        return self.fx_in + self.hold + self.fx_out

    def seconds(self, rc):
        n = len(self._games(rc))
        if self._single(rc):
            return min(n, 6) * self._period()
        cols, rows = self._grid(rc)
        return max(1, -(-n // (cols * rows))) * self.page_seconds

    def _pages(self, rc) -> int:
        cols, rows = self._grid(rc)
        return max(1, -(-len(self._games(rc)) // (cols * rows)))

    # ---- one game at a time
    def _ordered(self, rc):
        """Live games first, then upcoming, then finals (kickoff order inside each)."""
        rank = {"in": 0, "pre": 1, "post": 2}
        return sorted(self._games(rc), key=lambda g: (rank.get(g["state"], 3), g["start_utc"]))

    def _slot(self, t: float, n: int) -> tuple[int, float]:
        if t + 0.5 < self._last_t:   # the slide restarted: carry on with the next game, not game 1 again
            self._base += int(self._last_t // self._period()) + 1
        self._last_t = t
        return (self._base + int(t // self._period())) % n, t % self._period()

    def animating(self, rc, t: float) -> bool:
        if not self._single(rc):
            return False
        phase = t % self._period()
        return phase < self.fx_in or phase > self.fx_in + self.hold

    def _card(self, rc, g, idx, n):
        from .fx import PixelCard
        key = (repr(g), idx, n, rc.W, rc.main_h)
        pc = self._cards.get(key)
        if pc is None:
            if len(self._cards) > 48:
                self._cards.clear()
            card = Image.new("RGB", (rc.W, rc.main_h))
            self._draw_one(card, rc, g, idx, n)
            pc = self._cards[key] = PixelCard(card, seed=hash(g["id"]) & 0xFFFF)
        return pc

    def _draw_one(self, img, rc, g, idx, n):
        d = ImageDraw.Draw(img)
        W, H, lg = rc.W, rc.main_h, self.league
        sb = rc.data(f"scores.{lg}") or {}
        head = LEAGUE_SHORT[lg] + (f" WK {sb['week']}" if sb.get("week") else "")
        text(d, 2, 1, head, "small", GOLD)
        text_right(d, W - 2, 1, f"{idx + 1}/{n}", "tiny", DIM)
        d.line([(0, 8), (W - 1, 8)], fill=DARK)
        mine = {str(x) for x in rc.ctx.section("scores", lg).get("teams", [])}
        state = g["state"]
        row_h = max(12, (H - 18) // 2)
        logo = min(row_h - 2, 20)
        big = row_h >= 20
        for j, tm in enumerate((g["away"], g["home"])):
            y = 10 + j * row_h
            draw_logo(img, rc, lg, tm, 2, y + (row_h - logo) // 2, logo)
            col = GOLD if tm["id"] in mine else WHITE
            if state == "post" and not tm.get("winner"):
                col = GRAY
            ty = y + (row_h - (13 if big else 7)) // 2
            label, f = fit(team_label(tm), W - logo - 30, ("bold", "body") if big else ("body", "small"))
            tw = text(d, logo + 6, ty + (13 - text_height(f) if big else 0), label,
                      "bold" if f is font("bold") else "body" if f is font("body") else "small", col)
            if tm.get("record"):
                text(d, logo + 6 + tw + 3, ty + (5 if big else 1), tm["record"], "tiny", DIM)
            if state != "pre":
                text_right(d, W - 3, ty, str(tm.get("score") or 0), "bold" if big else "body", col)
            if state == "in" and g.get("possession") == tm["abbr"]:
                d.rectangle([W - 3 - text_width(str(tm.get("score") or 0), font("bold" if big else "body")) - 6,
                             ty + 4, W - 3 - text_width(str(tm.get("score") or 0), font("bold" if big else "body")) - 4,
                             ty + 6], fill=YELLOW)
        by = H - 7
        d.line([(0, by - 2), (W - 1, by - 2)], fill=DARK)
        if state == "pre":
            text(d, 2, by, compact_when(g["start_utc"], rc.ctx.tz, rc.now), "small", GRAY)
            odds = (g.get("odds") or {}).get("details")
            if odds:
                text_right(d, W - 2, by, odds[:12], "small", GOLD)
        elif state == "in":
            parts = (g.get("status") or "").upper().split(" - ")
            text(d, 2, by, parts[-1][:14], "small", YELLOW)
            extra = g.get("down_distance") or (parts[0] if len(parts) > 1 else "")
            if extra:
                text_right(d, W - 2, by, extra.upper()[:14], "small", WHITE)
        else:
            text(d, 2, by, (g.get("status") or "FINAL").upper()[:14], "small", GRAY)
            if g.get("note"):
                text_right(d, W - 2, by, g["note"].upper()[:16], "tiny", DIM)

    def _render_single(self, img, rc, t):
        games = self._ordered(rc)
        idx, phase = self._slot(t, len(games))
        pc = self._card(rc, games[idx], idx, len(games))
        if phase < self.fx_in:
            frame = pc.assemble(phase, self.fx_in)
        elif phase <= self.fx_in + self.hold:
            frame = pc.card
        else:
            frame = pc.crumble(phase - self.fx_in - self.hold, self.fx_out)
        img.paste(frame, (0, 0))

    # ---- paged grid
    def render(self, img, rc, t):
        if self._single(rc):
            return self._render_single(img, rc, t)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        games = self._ordered(rc)
        cols, rows = self._grid(rc)
        per_page = cols * rows
        pages = self._pages(rc)
        page = int(t // self.page_seconds) % pages
        sb = rc.data(f"scores.{self.league}") or {}
        name = LEAGUE_NAMES[self.league] if W >= 192 else LEAGUE_SHORT[self.league]
        week = (f"  WEEK {sb['week']}" if W >= 192 else f" WK {sb['week']}") if sb.get("week") else ""
        text(d, 2, 1, name + week, "body", GOLD)
        if pages > 1:
            text_right(d, W - 2, 1, f"{page + 1}/{pages}", "body", GRAY)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)

        cw = W // cols
        top, avail = 11, H - 11
        mine = {str(x) for x in rc.ctx.section("scores", self.league).get("teams", [])}
        shown = games[page * per_page:(page + 1) * per_page]
        # one status-column width for the whole page, wide enough for its longest line ("SAT 11:45A", "TENN -2.5")
        sw = max([text_width(s, font("small")) for g in shown for s in self._status_lines(g, rc)] or [20])
        for i, g in enumerate(shown):
            r, c = divmod(i, cols)
            y0 = top + r * avail // rows
            y1 = top + (r + 1) * avail // rows
            self._cell(img, d, rc, g, c * cw, y0, cw, y1 - y0, mine, sw)
        for c in range(1, cols):
            d.line([(c * cw - 1, top), (c * cw - 1, H - 1)], fill=DARK)

    @staticmethod
    def _status_lines(g, rc) -> list[str]:
        if g["state"] == "pre":
            odds = (g.get("odds") or {}).get("details")
            return [compact_when(g["start_utc"], rc.ctx.tz, rc.now)] + ([odds.replace(" ", "")[:10]] if odds else [])
        if g["state"] == "in":
            parts = (g.get("status") or "").upper().split(" - ")      # "2ND - 5:21": quarter on top, clock under
            return [p[:10] for p in parts[:2]]
        return [(g.get("status") or "FINAL").upper()[:10]]

    def _cell(self, img, d, rc, g, x, y, w, h, mine, status_w):
        """One game in a grid cell of any height: two team rows centered, logos and type grow with the cell."""
        lg, state = self.league, g["state"]
        row = (h - 1) // 2                       # height of each team row
        big = row >= 14
        logo = min(row - 1, 13 if big else 11)
        role, th = ("medium", 10) if big else ("body", 7)
        y_top = y + (h - 1 - 2 * row) // 2       # leftover pixels split above and below
        status_x = x + w - status_w - 3
        score_r = status_x - 5
        for j, tm in enumerate((g["away"], g["home"])):
            ry = y_top + j * row
            draw_logo(img, rc, lg, tm, x + 2, ry + (row - logo) // 2, logo)
            col = GOLD if tm["id"] in mine else WHITE
            if state == "post" and not tm.get("winner"):
                col = GRAY
            ty = ry + (row - th) // 2
            nx = x + logo + 5
            score = str(tm.get("score") or 0) if state != "pre" else ""
            room = score_r - (text_width(score, font(role)) + 4 if score else 0) - nx
            label, f = fit(team_label(tm), room, (role, "body", "small"))
            tw = text(d, nx, ty + (th - text_height(f)) // 2, label,
                      {id(font("medium")): "medium", id(font("body")): "body"}.get(id(f), "small"), col)
            rec = tm.get("record") or ""
            if rec and nx + tw + 4 + text_width(rec, font("tiny")) <= score_r - (text_width(score, font(role)) + 4 if score else 0):
                text(d, nx + tw + 3, ty + th - 6, rec, "tiny", DIM)
            if score:
                text_right(d, score_r, ty, score, role, col)
            if state == "in" and g.get("possession") == tm["abbr"]:
                d.rectangle([score_r + 2, ty + th // 2 - 1, score_r + 3, ty + th // 2 + 1], fill=YELLOW)
        lines = self._status_lines(g, rc)
        cols_ = {"pre": (GRAY, GOLD), "in": (YELLOW, YELLOW)}.get(state, (GRAY, GRAY))
        for j, s in enumerate(lines[:2]):
            ry = y_top + j * row
            text(d, status_x, ry + (row - 6) // 2, s, "small", cols_[j])
        d.line([(x, y + h - 1), (x + w - 1, y + h - 1)], fill=DARK)


# ====================================================================== fantasy

def _fantasy_logo(img, rc, team: dict, x, y, size):
    im = rc.images.get(team.get("logo"), size)
    if not paste_icon(img, im, x, y):
        team_fallback(img, x, y, size, (team.get("abbrev") or team["name"][:3]).upper(), (90, 60, 160))


class FantasyMatchup(Screen):
    name, label = "fantasy", "Fantasy: my matchup"
    duration = 15

    def _mine(self, rc):
        lg = rc.data("fantasy")
        if not lg or lg.get("my_team_id") is None:
            return None, None
        m = next((m for m in lg["matchups"] if m["mine"]), None)
        return lg, m

    def available(self, rc):
        return self._mine(rc)[1] is not None

    def render(self, img, rc, t):
        lg, m = self._mine(rc)
        d = ImageDraw.Draw(img)
        W = rc.W
        teams = {tm["id"]: tm for tm in lg["teams"]}
        me_side = m["home"] if m["home"]["team_id"] == lg["my_team_id"] else m["away"]
        op_side = m["away"] if me_side is m["home"] else m["home"]
        me, op = teams[me_side["team_id"]], teams[op_side["team_id"]] if op_side else None

        text(d, 2, 1, f"{lg['name'].upper()}  WEEK {lg['week']}", "body", GOLD)
        text_right(d, W - 2, 1, "FANTASY", "body", GRAY)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)

        for side, tm, left in ((me_side, me, True), (op_side, op, False)):
            if not tm:
                continue
            lx = 4 if left else W - 4 - 34
            _fantasy_logo(img, rc, tm, lx, 13, 34)
            pts = f"{side['points']:.1f}"
            if left:
                big_text(img, 44, 14, pts, "large", 1, WHITE)
                if side.get("projected") is not None:
                    text(d, 44, 35, f"PROJ {side['projected']:.1f}", "small", GRAY)
            else:
                big_text(img, W - 44, 14, pts, "large", 1, WHITE, anchor="r")
                if side.get("projected") is not None:
                    text_right(d, W - 44, 35, f"PROJ {side['projected']:.1f}", "small", GRAY)
            name, f = fit(tm["name"], 120, ("body", "small", "tiny"))
            role = {id(font("body")): "body", id(font("small")): "small"}.get(id(f), "tiny")
            rec = f"{tm['wins']}-{tm['losses']}" + (f"-{tm['ties']}" if tm["ties"] else "")
            if left:
                text(d, 4, 50, name, role, GOLD)
                text(d, 4, 58, f"{rec}  #{tm['standing']}", "small", GRAY)
            else:
                text_right(d, W - 4, 50, name, role, WHITE)
                text_right(d, W - 4, 58, f"#{tm['standing']}  {rec}", "small", GRAY)
        text_center(d, W // 2, 20, "VS", "body", DIM)

        # live (or projected) share bar
        a, b = me_side["points"], (op_side or {}).get("points", 0)
        label = "LIVE"
        if a + b == 0:
            a, b, label = me_side.get("projected") or 1, (op_side or {}).get("projected") or 1, "PROJ"
        share = a / (a + b) if a + b else 0.5
        bx0, bx1, by = 4, W - 5, 67
        split = int(bx0 + (bx1 - bx0) * share)
        d.rectangle([bx0, by, split, by + 3], fill=GREEN if share >= 0.5 else ORANGE)
        d.rectangle([split + 1, by, bx1, by + 3], fill=DIM)
        text_center(d, W // 2, 60, f"{label} {share * 100:.0f}%", "tiny", WHITE)

        # my starters, two columns
        starters = [p for p in lg.get("my_roster", []) if p["starter"]]
        y0, col_w = 74, W // 2
        rh = max(8, min(10, (rc.main_h - y0) // 5))    # rows spread out when the ticker is off
        for i, p in enumerate(starters[:10]):
            c, r = divmod(i, 5)
            x, y = 3 + c * col_w, y0 + r * rh
            inj = p.get("injury") or ""
            col = RED if inj in ("OUT", "INJURY_RESERVE", "SUSPENSION") else ORANGE if inj in ("QUESTIONABLE", "DOUBTFUL") else WHITE
            text(d, x, y, p["slot"][:4], "small", GRAY)
            last = p["name"].split(" ", 1)[-1] if p["position"] != "D/ST" else p["name"].replace(" D/ST", "")
            label = f"{p['name'][0]}.{last}" if p["position"] != "D/ST" else last + " D"
            text(d, x + 22, y, label[:14], "small", col)
            pts = f"{p['points']:.1f}" if p.get("points") is not None else "-"
            text_right(d, x + col_w - 22, y, pts, "small", WHITE)
            text_right(d, x + col_w - 5, y, p["nfl_team"], "small", DIM)


class FantasyScoreboard(Screen):
    name, label = "fantasy_scores", "Fantasy: league scoreboard"
    duration = 12

    def available(self, rc):
        lg = rc.data("fantasy")
        return bool(lg and lg.get("matchups"))

    page_seconds = 6.0

    def _layout(self, rc, n):
        """(columns, rows per column, pages): a wide strip shows two columns; anything that doesn't fit pages."""
        cols = 2 if rc.W >= 192 and rc.main_h < 96 else 1
        fit_rows = max(1, (rc.main_h - 11) // 12)
        pages = max(1, -(-n // (cols * fit_rows)))
        per = -(-n // pages)
        return cols, -(-per // cols), pages, per

    def seconds(self, rc):
        lg = rc.data("fantasy") or {}
        return max(self.duration, self._layout(rc, len(lg.get("matchups") or []))[2] * self.page_seconds)

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        teams = {tm["id"]: tm for tm in lg["teams"]}
        matchups = sorted(lg["matchups"], key=lambda m: not m["mine"])      # mine first
        cols, rows, pages, per = self._layout(rc, len(matchups))
        page = int(t // self.page_seconds) % pages
        text(d, 2, 1, f"WK {lg['week']} SCORES" if W < 200 else f"WEEK {lg['week']} SCOREBOARD", "body", GOLD)
        right = f"{page + 1}/{pages}" if pages > 1 else ("" if W < 200 else lg["name"].upper()[:20])
        if right:
            text_right(d, W - 2, 1, right, "body", GRAY)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)
        cw = W // cols
        narrow = cw < 200                    # a column under ~200 px: abbreviations instead of team names
        name_w = cw // 2 - 8 - 19 - 26       # room between the logo and the score on each side
        rh = (H - 11) // rows
        lsz = 14 if rh >= 15 else 12 if rh >= 13 else 10
        for i, m in enumerate(matchups[page * per:(page + 1) * per]):
            c, r = divmod(i, rows)
            x0, y = c * cw, 11 + r * rh
            if m["mine"]:
                d.rectangle([x0, y, x0 + cw - 2, y + rh - 2], fill=(40, 30, 10))
            sides = [m.get("away"), m["home"]]
            mid = x0 + cw // 2
            for j, s in enumerate(sides):
                if not s:
                    continue
                tm = teams[s["team_id"]]
                left = j == 0
                lx = x0 + 2 if left else x0 + cw - 2 - lsz
                _fantasy_logo(img, rc, tm, lx, y + (rh - 1 - lsz) // 2, lsz)
                other = sides[1 - j]
                lead = other is not None and s["points"] > other["points"]
                if narrow:
                    name, _ = fit((tm.get("abbrev") or tm["name"]).upper(), max(8, name_w + 10), ("small", "tiny"))
                else:
                    name, _ = fit(tm["name"], name_w, ("small", "tiny"))
                ty = y + (rh - 1 - 7) // 2
                col = GOLD if tm.get("mine") else WHITE
                if left:
                    text(d, x0 + lsz + 5, ty, name, "small", col)
                    text_right(d, mid - 8, ty, f"{s['points']:.1f}", "body", GREEN if lead else WHITE)
                else:
                    text_right(d, x0 + cw - lsz - 5, ty, name, "small", col)
                    text(d, mid + 8, ty, f"{s['points']:.1f}", "body", GREEN if lead else WHITE)
            text_center(d, mid, y + (rh - 1 - 7) // 2, "@" if sides[0] else "BYE", "small", DIM if sides[0] else GRAY)
        for c in range(1, cols):
            d.line([(c * cw - 1, 11), (c * cw - 1, H - 1)], fill=DARK)


# ====================================================================== weather

class WeatherScreen(Screen):
    name, label = "weather", "Weather"
    duration = 12

    def available(self, rc):
        w = rc.data("weather")
        return bool(w and (w.get("current") or w.get("periods")))

    def render(self, img, rc, t):
        w = rc.data("weather")
        d = ImageDraw.Draw(img)
        W, tz = rc.W, rc.ctx.tz
        fr = icons.frame_at(t)
        cur = w.get("current") or {}
        periods = w.get("periods") or []
        today = periods[0] if periods else {}
        name, night = icons.icon_for(cur.get("icon") or today.get("icon"), cur.get("conditions") or today.get("short"))
        paste_icon(img, icons.get(name, 48, night, fr), 4, 4)

        temp = cur.get("temp_f")
        if temp is None and today:
            temp = today.get("temp_f")
        tw = big_text(img, 60, 4, f"{temp}" if temp is not None else "--", "huge", 2, WHITE)
        d.ellipse([60 + tw + 2, 6, 60 + tw + 7, 11], outline=WHITE, width=2)
        cond, f = fit((cur.get("conditions") or today.get("short") or "").upper(), 110, ("body", "small"))
        text(d, 60, 48, cond, "body" if f is font("body") else "small", GRAY)

        hi = next((p["temp_f"] for p in periods if p.get("is_day")), None)
        lo = next((p["temp_f"] for p in periods if not p.get("is_day")), None)
        rx = 176
        loc, lf = fit((w.get("location") or "").split(",")[0].upper(), W - rx - 3, ("small", "tiny"))
        text(d, rx, 4, loc, "small" if lf is font("small") else "tiny", GOLD)
        rows = []
        if hi is not None or lo is not None:
            rows.append(("HI/LO", f"{hi if hi is not None else '-'}/{lo if lo is not None else '-'}"))
        if cur.get("feels_like_f") is not None and cur.get("feels_like_f") != temp:
            rows.append(("FEELS", f"{cur['feels_like_f']}"))
        if cur.get("wind_mph") is not None:
            rows.append(("WIND", f"{cur.get('wind_dir') or ''} {cur['wind_mph']}"))
        if cur.get("humidity") is not None:
            rows.append(("HUM", f"{cur['humidity']}%"))
        if periods:
            rows.append(("RAIN", f"{max(p['precip_pct'] for p in periods[:2])}%"))
        for i, (k, v) in enumerate(rows[:5]):
            text(d, rx, 13 + i * 9, k, "small", GRAY)
            text_right(d, W - 3, 13 + i * 9, v, "small", WHITE)

        # hourly strip: fills whatever height is left (bigger icons when the ticker is off)
        alerts = w.get("alerts") or []
        d.line([(0, 60), (W - 1, 60)], fill=DARK)
        hours = (w.get("hourly") or [])[:8]
        bottom = rc.main_h - (12 if alerts else 0)
        avail = bottom - 62
        isz = 24 if avail >= 64 else 20 if avail >= 58 else 16
        block = 7 + 2 + isz + 2 + 8 + 2 + 7
        y = 62 + max(0, (avail - block) // 2)
        if hours:
            sw = W // len(hours)
            for i, h in enumerate(hours):
                cx = i * sw + sw // 2
                text_center(d, cx, y, short_time(h["time"], tz), "small", GRAY)
                nm, nt = icons.icon_for(h.get("icon"), h.get("short"), h.get("is_day"))
                paste_icon(img, icons.get(nm, isz, nt, fr), cx - isz // 2, y + 9)
                ty = y + 9 + isz + 2
                tw = text_center(d, cx, ty, f"{h['temp_f']}", "body", WHITE)
                d.ellipse([cx + tw // 2 + 1, ty, cx + tw // 2 + 3, ty + 2], outline=WHITE)
                if h.get("precip_pct"):
                    text_center(d, cx, ty + 10, f"{h['precip_pct']}%", "small", BLUE)

        if alerts:
            a = alerts[0]
            on = int(t * 2) % 2 == 0
            col = RED if a["level"] == "warning" else ORANGE if a["level"] == "watch" else YELLOW
            d.rectangle([0, rc.main_h - 11, W - 1, rc.main_h - 1], fill=col if on else dim(col, 0.4))
            text_center(d, W // 2, rc.main_h - 9, a["event"].upper()[:40], "body", contrast_text(col))


class AlertScreen(Screen):
    """Full-screen severe weather alert; the engine puts this first whenever a warning is active."""
    name, label = "alert", "Weather alert"
    duration = 10

    def available(self, rc):
        w = rc.data("weather") or {}
        return any(a["level"] in ("warning", "watch") for a in w.get("alerts") or [])

    def render(self, img, rc, t):
        from .tiles import wrap
        w = rc.data("weather")
        a = next(a for a in w["alerts"] if a["level"] in ("warning", "watch"))
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        col = RED if a["level"] == "warning" else ORANGE
        on = int(t * 2) % 2 == 0
        bw = 3 if min(W, H) >= 90 else 2
        d.rectangle([0, 0, W - 1, H - 1], outline=col if on else dim(col, 0.4), width=bw)
        pad = bw + 3
        name = "tornado" if "tornado" in a["event"].lower() else "severe"
        stacked = W < 192 and H >= 96                     # a tall half: icon on top, text under it
        isz = max(20, min(48, (H - 2 * pad) if not stacked else 40, (W - 2 * pad) // 3 if not stacked else W))
        tx = pad + (0 if stacked else isz + 6)
        tw = W - pad - tx
        ev = a["event"].upper()
        # the event in the biggest type that fits in two lines
        role, lines = "small", wrap(ev, font("small"), tw)
        for r in (("huge",) if H >= 96 and not stacked else ()) + ("large", "bold", "body"):
            ls = wrap(ev, font(r), tw)
            if len(ls) <= 2 and all(text_width(x, font(r)) <= tw for x in ls):
                role, lines = r, ls
                break
        lh = text_height(font(role)) + 2
        until = ""
        if a.get("ends"):
            ends = datetime.fromisoformat(a["ends"]).astimezone(rc.ctx.tz)
            until = f"UNTIL {ends.strftime('%I:%M %p').lstrip('0')} {ends:%a}".upper()
        until, uf = fit(until, tw, ("body", "small", "tiny")) if until else ("", font("body"))
        head_h = len(lines) * lh + (text_height(uf) + 3 if until else 0)
        if stacked:
            y = pad + 1
            paste_icon(img, icons.get(name, isz, False, icons.frame_at(t)), (W - isz) // 2, y)
            y += isz + 3
            for ln in lines:
                text_center(d, W // 2, y, ln, role, col)
                y += lh
            if until:
                text_center(d, W // 2, y, until, role_of_font(uf), WHITE)
                y += text_height(uf) + 4
            hx, hw = pad, W - 2 * pad
        else:
            hl_lines = len(wrap(a.get("headline") or "", font("small"), W - 2 * pad)) if a.get("headline") else 0
            fit_hl = max(0, min(hl_lines, (H - 2 * pad - max(isz, head_h) - 4) // 8))
            content = max(isz, head_h) + (4 + fit_hl * 8 if fit_hl else 0)
            top = pad + max(0, (H - 2 * pad - content) // 2)
            paste_icon(img, icons.get(name, isz, False, icons.frame_at(t)), pad, top + max(0, (head_h - isz) // 2))
            y = top + max(0, (isz - head_h) // 2)
            for ln in lines:
                text(d, tx, y, ln, role, col)
                y += lh
            if until:
                text(d, tx, y + 1, until, role_of_font(uf), WHITE)
                y += text_height(uf) + 4
            y = max(y, top + isz + 4)
            hx, hw = pad, W - 2 * pad
        # the NWS headline, word-wrapped into whatever's left
        if a.get("headline"):
            for ln in wrap(a["headline"], font("small"), hw):
                if y + 7 > H - pad:
                    break
                text(d, hx, y, ln, "small", GRAY)
                y += 8


# ====================================================================== markets

_BADGE_CACHE: dict[str, tuple] = {}


class MarketsScreen(Screen):
    name, label = "markets", "Markets"
    duration = 12

    def available(self, rc):
        return bool(rc.data("markets.stocks") or rc.data("markets.crypto"))

    def _badge_colors(self, rc, sym: str, logo_url: str | None):
        cfg = rc.ctx.section("markets", "badges").get(sym) or {}
        if cfg.get("bg"):
            bg = hex_color(cfg["bg"])
            return bg, hex_color(cfg.get("fg")) if cfg.get("fg") else contrast_text(bg)
        if logo_url in _BADGE_CACHE:
            return _BADGE_CACHE[logo_url]
        orig = rc.images.original(logo_url)
        if orig is not None:
            bg = dominant_color(orig)
            _BADGE_CACHE[logo_url] = (bg, contrast_text(bg))
            return _BADGE_CACHE[logo_url]
        return (70, 72, 84), WHITE

    def render(self, img, rc, t):
        d = ImageDraw.Draw(img)
        W = rc.W
        st = rc.data("markets.stocks") or {}
        cr = rc.data("markets.crypto") or {}
        status = "MARKET OPEN" if st.get("market_open") else "MARKET CLOSED"
        text(d, 2, 1, "MARKETS", "body", GOLD)
        text_right(d, W - 2, 1, status, "body", GREEN if st.get("market_open") else GRAY)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)
        quotes = [q for q in (st.get("quotes") or []) if not q.get("error")] + \
                 [q for q in (cr.get("quotes") or []) if not q.get("error")]
        cols = 2 if W >= 200 else 1
        cw = W // cols
        rows = -(-len(quotes) // cols) if quotes else 1
        rh = max(12, min(20, (rc.main_h - 11) // rows))
        lsz = 14 if rh >= 18 else 12
        for i, q in enumerate(quotes):
            c, r = divmod(i, rows)
            x, y = c * cw, 11 + r * rh
            if y + rh > rc.main_h:
                break
            logo = rc.images.get(q.get("logo"), lsz)
            lx = x + 2
            if logo is not None:
                paste_icon(img, logo, lx, y + (rh - lsz) // 2)
            bx = lx + lsz + 3
            bg, fg = self._badge_colors(rc, q["symbol"], q.get("logo"))
            badge(img, bx, y + (rh - 11) // 2, q["symbol"], bg, fg, "body", h=11, min_w=34)
            price = q["price"]
            ptxt = f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"
            ty = y + (rh - 7) // 2
            text_right(d, x + 86, ty, ptxt, "body", WHITE)
            up = (q.get("change") or 0) >= 0
            pct = f"{abs(q.get('change_pct') or 0):.2f}%"
            pw = text_right(d, x + cw - 3, ty, pct, "body", GREEN if up else RED)
            arrow(d, x + cw - 3 - pw - 7, ty + 1, up, 5)
        for c in range(1, cols):
            d.line([(c * cw - 1, 11), (c * cw - 1, rc.main_h - 1)], fill=DARK)


# ====================================================================== fantasy standings

class FantasyStandings(Screen):
    """Leaderboard: playoff seeds on the left (the column split is the playoff line), the rest on the right."""
    name, label = "fantasy_standings", "Fantasy: standings & playoff race"
    duration = 12

    def available(self, rc):
        lg = rc.data("fantasy")
        return bool(lg and lg.get("teams"))

    def render(self, img, rc, t):
        lg = rc.data("fantasy")
        d = ImageDraw.Draw(img)
        W = rc.W
        teams = lg["teams"]
        cut = lg.get("playoff_teams") or (len(teams) + 1) // 2
        text(d, 2, 1, f"{lg['name'].upper()} STANDINGS", "body", GOLD)
        text_right(d, W - 2, 1, f"TOP {cut} MAKE PLAYOFFS", "small", GREEN)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)
        last_in = teams[cut - 1] if len(teams) >= cut else None
        rows = max(1, cut, len(teams) - cut)
        cw = W // 2
        rh = max(12, (rc.main_h - 11) // rows)
        for i, tm in enumerate(teams):
            inside = i < cut
            c, r = (0, i) if inside else (1, i - cut)
            x, y = c * cw, 11 + r * rh
            if tm.get("mine"):
                d.rectangle([x, y, x + cw - 2, y + rh - 2], fill=(40, 30, 10))
            text(d, x + 2, y + 2, f"{tm['standing']:>2}", "small", GREEN if inside else GRAY)
            _fantasy_logo(img, rc, tm, x + 12, y + max(0, (rh - 12) // 2), 12)
            name, f = fit(tm["name"], cw - (54 if not inside else 30), ("small", "tiny"))
            role = "small" if f is font("small") else "tiny"
            text(d, x + 27, y + 1, name, role, GOLD if tm.get("mine") else WHITE)
            rec = f"{tm['wins']}-{tm['losses']}" + (f"-{tm['ties']}" if tm["ties"] else "")
            sub = f"{rec}  {tm['points_for']:.0f} PF"
            text(d, x + 27, y + 8, sub, "tiny", GRAY)
            if tm.get("streak"):
                text_right(d, x + cw - 4, y + 8, tm["streak"], "tiny", GREEN if tm["streak"][0] == "W" else RED)
            if not inside and last_in:
                gb = ((last_in["wins"] - last_in["losses"]) - (tm["wins"] - tm["losses"])) / 2
                if gb > 0:
                    text_right(d, x + cw - 4, y + 1, f"{gb:g} GB", "tiny", ORANGE)
        d.line([(cw - 1, 11), (cw - 1, rc.main_h - 1)], fill=GREEN)
        text_center(d, cw // 2, rc.main_h - 6, "", "tiny")


# ====================================================================== player of the week (sweeping)

def _ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


class PlayerOfWeek(Screen):
    """Top fantasy performers of last week, each sliding in from the right, then the top team."""
    name, label = "fantasy_potw", "Fantasy: player of the week"
    page_seconds = 5.0
    slide_seconds = 0.45

    def _pages(self, rc):
        lg = rc.data("fantasy") or {}
        pw = lg.get("potw") or {}
        pages = [("player", i, p) for i, p in enumerate(pw.get("players") or [])]
        if pw.get("top_team_id") is not None:
            pages.append(("team", 0, None))
        if lg.get("week_leaders"):
            pages.append(("leaders", 0, None))
        return lg, pw, pages

    def available(self, rc):
        return bool(self._pages(rc)[2])

    def seconds(self, rc):
        return len(self._pages(rc)[2]) * self.page_seconds

    def render(self, img, rc, t):
        lg, pw, pages = self._pages(rc)
        W, H = rc.W, rc.main_h
        idx = int(t // self.page_seconds) % len(pages)
        into = t - (t // self.page_seconds) * self.page_seconds
        offset = int(W * (1 - _ease(into / self.slide_seconds)))
        layer = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(layer)
        kind, i, p = pages[idx]
        teams = {x["id"]: x for x in lg["teams"]}
        hdr = f"PLAYER OF THE WEEK  WEEK {pw.get('week')}" if kind != "leaders" else f"WEEK {lg['week']} LEADERS (LIVE)"
        if kind == "player":
            text(d, 2, 1, hdr, "body", GOLD)
            text_right(d, W - 2, 1, f"#{i + 1} OF {len(pw['players'])}", "body", GRAY)
            # the headshot takes the panel's height; everything else stacks in the column beside it
            hs = max(48, min(H - 14, W // 2 - 20))
            hy = 11 + (H - 11 - hs) // 2
            head = rc.images.get(p.get("headshot"), hs)
            if not paste_icon(layer, head, 4, hy):
                d.rectangle([4, hy, 3 + hs, hy + hs - 1], outline=DIM)
            x = hs + 12
            owner = teams.get(p.get("fantasy_team_id"), {})
            mine = p.get("fantasy_team_id") == lg.get("my_team_id")
            name, f = fit(p["name"].upper(), W - x - 2, ("large", "bold", "body"))
            nr = role_of_font(f)
            own, of = fit(owner.get("name", "FREE AGENT").upper(), W - x - 2, ("body", "small", "tiny"))
            stack = [(text_height(f), lambda y: text(d, x, y, name, nr, WHITE)),
                     (8, lambda y: text(d, x, y, f"{p['position']}  {p['nfl_team']}", "body", GRAY)),
                     (20, lambda y: text(d, x + 4 + big_text(layer, x, y, f"{p['points']:.1f}", "huge", 1, GREEN),
                                         y + 11, "PTS", "body", GREEN)),
                     (6, lambda y: text(d, x, y, "OWNER", "tiny", DIM)),
                     (8, lambda y: text(d, x, y, own, role_of_font(of), GOLD if mine else WHITE)),
                     (7, lambda y: text(d, x, y, "STARTED" if p.get("starter") else "LEFT ON THE BENCH!", "small",
                                        GREEN if p.get("starter") else RED))]
            gap = max(3, min(8, (H - 13 - sum(h for h, _ in stack)) // (len(stack) + 1)))
            y = 11 + max(0, (H - 11 - sum(h for h, _ in stack) - gap * (len(stack) - 1)) // 2)
            for hh, draw in stack:
                draw(y)
                y += hh + gap
        elif kind == "team":
            text(d, 2, 1, f"TOP SCORE  WEEK {pw.get('week')}", "body", GOLD)
            tm = teams.get(pw["top_team_id"], {"name": "?", "id": 0})
            ls = max(40, min(80, H - 20))
            ly = 11 + (H - 11 - ls) // 2
            _fantasy_logo(layer, rc, tm, 6, ly, ls)
            x = ls + 16
            name, f = fit(tm["name"].upper(), W - x - 2, ("large", "bold", "body", "small"))
            blk = text_height(f) + 6 + 40 + 6 + 8
            y = 11 + (H - 11 - blk) // 2
            text(d, x, y, name, role_of_font(f), GOLD if tm.get("mine") else WHITE)
            big_text(layer, x, y + text_height(f) + 6, f"{pw['top_team_points']:.1f}", "huge", 2, GREEN)
            rec = f"{tm.get('wins', 0)}-{tm.get('losses', 0)}" if "wins" in tm else ""
            text(d, x, y + text_height(f) + 52, f"POINTS  {rec}".strip(), "body", GRAY)
        else:
            text(d, 2, 1, hdr, "body", GOLD)
            leaders = lg["week_leaders"][:max(3, min(5, (H - 12) // 24))]
            rh = (H - 12) // max(1, len(leaders))
            hs = max(20, min(30, rh - 4))
            for j, q in enumerate(leaders):
                y = 12 + j * rh + (rh - hs) // 2
                paste_icon(layer, rc.images.get(q.get("headshot"), hs), 6, y)
                tx = hs + 12
                pw_ = text_right(d, W - 4, y + (hs - 13) // 2, f"{q['points']:.1f}", "bold", GREEN)
                nm, f = fit(q["name"].upper(), W - 8 - pw_ - tx, ("body", "small"))
                text(d, tx, y + hs // 2 - 9, nm, role_of_font(f), WHITE)
                own = teams.get(q.get("fantasy_team_id"), {}).get("name", "")
                sub, sf = fit(f"{q['position']} {q['nfl_team']}  {own}", W - 8 - pw_ - tx, ("small", "tiny"))
                text(d, tx, y + hs // 2 + 1, sub, role_of_font(sf), GRAY)
        img.paste(layer, (offset, 0))


# ====================================================================== fantasy trades

TRADE_ALERT_HOURS = 24


def all_trades(rc: RC) -> list[dict]:
    """Every trade in the league feed, newest first (from the fast trade listener)."""
    return ((rc.data("fantasy_trades") or {}).get("trades")) or []


def recent_trades(rc: RC, hours: float = TRADE_ALERT_HOURS) -> list[dict]:
    now = datetime.now(timezone.utc)
    out = []
    for tr in all_trades(rc):
        try:
            when = datetime.fromisoformat(tr["date"])
        except (KeyError, ValueError):
            continue
        if (now - when).total_seconds() < hours * 3600:
            out.append(tr)
    return out


def _ago(iso: str, now: datetime) -> str:
    try:
        s = (now - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if s < 120:
        return "JUST NOW"
    if s < 3600:
        return f"{int(s // 60)} MIN AGO"
    if s < 86400:
        return f"{int(s // 3600)} HR AGO"
    return f"{int(s // 86400)} DAYS AGO"


def _draw_trade(img, rc: RC, tr: dict, top: int, bottom: int) -> None:
    """Both sides of a trade in columns: logo, team, RECEIVES, the players."""
    teams = {x["id"]: x for x in (rc.data("fantasy") or {}).get("teams") or []}
    d = ImageDraw.Draw(img)
    W = rc.W
    sides = tr["sides"][:2]
    cw = W // max(1, len(sides))
    span = bottom - top
    most = max((len(s["receives"]) for s in sides), default=1)
    narrow = cw < 100                     # a tall half: team name goes under the logo, not beside it
    short = span < 56                     # a strip: one compact team line, then the players
    # where the player list starts (same for both sides), then ONE type size for both sides: the biggest in
    # which every name fits its column and the longer list fits the height
    if short:
        logo, y_list = 12, top + 15
    elif narrow:
        logo = max(20, min(32, span // 4))
        y_list = top + logo + 20
    else:
        logo = max(24, min(36, span // 3))
        y_list = top + logo + 5
    all_names = [pl.upper() for s in sides for pl in s["receives"]] or [""]
    prole = next((r for r in ("bold", "medium", "body", "small")
                  if all(text_width(nm, font(r)) <= cw - 10 for nm in all_names)
                  and most * (text_height(font(r)) + 3) <= bottom - y_list), "small")
    for k, s in enumerate(sides):
        x = k * cw
        tm = teams.get(s["team_id"], {"name": s["team_name"], "id": s["team_id"]})
        col = GOLD if tm.get("mine") else WHITE
        if short:
            _fantasy_logo(img, rc, tm, x + 3, top, logo)
            name, f = fit(s["team_name"].upper(), cw - logo - 10, ("body", "small", "tiny"))
            text(d, x + logo + 6, top + (logo - text_height(f)) // 2, name, role_of_font(f), col)
        elif narrow:
            _fantasy_logo(img, rc, tm, x + (cw - logo) // 2, top, logo)
            name, f = fit(s["team_name"].upper(), cw - 4, ("body", "small", "tiny"))
            text_center(d, x + cw // 2, top + logo + 2, name, role_of_font(f), col)
            text_center(d, x + cw // 2, top + logo + 11, "RECEIVES", "tiny", GRAY)
        else:
            _fantasy_logo(img, rc, tm, x + 4, top + 1, logo)
            name, f = fit(s["team_name"].upper(), cw - logo - 10, ("bold", "body", "small", "tiny"))
            nh = text_height(f)
            ny = top + 1 + max(0, (logo - nh - 9) // 2)
            text(d, x + logo + 8, ny, name, role_of_font(f), col)
            text(d, x + logo + 8, ny + nh + 3, "RECEIVES", "small", GRAY)
        y = y_list
        names = [pl.upper() for pl in s["receives"]] or [""]
        th = text_height(font(prole))
        step = th + (3 if not short else 2)
        # center the list in the space left, aligned with the other side (both use the longest side's height)
        block = most * step - (step - th)
        y += max(0, (bottom - y - block) // 2) if not short else 0
        for nm in names:
            if y + th > bottom:
                break
            s2, _ = fit(nm, cw - 10, (prole,))
            if narrow:
                text_center(d, x + cw // 2, y, s2, prole, WHITE)
            else:
                text(d, x + 6, y, s2, prole, WHITE)
            y += step
    if len(sides) > 1:
        d.line([(cw - 1, top), (cw - 1, bottom)], fill=DARK)


class TradeAlert(Screen):
    """The league's most recent trade, however old: add it to a mode if you want it in the rotation."""
    name, label = "trade", "Fantasy: latest trade"
    duration = 15

    def available(self, rc):
        return bool(all_trades(rc))

    def render(self, img, rc, t):
        tr = all_trades(rc)[0]
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        text(d, 2, 1, "LATEST TRADE", "body", GOLD)
        text_right(d, W - 2, 2, _ago(tr["date"], rc.now), "small", GRAY)
        d.line([(0, 10), (W - 1, 10)], fill=DARK)
        if H < 80:      # a strip: "7 DAYS AGO" up top is enough, the players need the footer's room
            _draw_trade(img, rc, tr, 13, H - 1)
            return
        _draw_trade(img, rc, tr, 14, H - 11)
        when = datetime.fromisoformat(tr["date"]).astimezone(rc.ctx.tz)
        text_center(d, W // 2, H - 8, when.strftime("%a %b %d  %I:%M %p").upper(), "small", GRAY)


class BreakingTrade(Screen):
    """Full-display takeover the moment a new trade lands. Not a playlist widget: the engine shows it on its own
    (and it can be turned off in the dashboard's Settings tab). The engine sets `trade` before showing it."""
    name, label = "trade_breaking", "Fantasy: BREAKING trade (takeover)"
    hidden = True
    intro = 3.0      # seconds of the flashing BREAKING NEWS card before the details

    def __init__(self):
        self.trade: dict | None = None

    def _trade(self, rc):
        if self.trade:
            return self.trade
        trades = all_trades(rc)
        return trades[0] if trades else None

    def available(self, rc):
        return self._trade(rc) is not None

    def render(self, img, rc, t):
        tr = self._trade(rc)
        d = ImageDraw.Draw(img)
        W, H = rc.W, rc.main_h
        lg_name = ((rc.data("fantasy") or {}).get("name") or "FANTASY LEAGUE").upper()
        if tr.get("status") == "pending review":
            return self._pending(img, d, rc, tr, t, lg_name)
        if t < self.intro:
            flash = int(t * 4) % 2 == 0
            d.rectangle([0, 0, W - 1, H - 1], fill=RED if flash else (90, 8, 6))
            scale = 2 if big_width("BREAKING", "huge", 2) <= W - 8 and H >= 90 else 1
            bh = 20 * scale
            y = max(2, (H - bh - 32) // 2)
            big_text(img, W // 2, y, "BREAKING", "huge", scale, WHITE if flash else YELLOW, anchor="c")
            text_center(d, W // 2, y + bh + 6, "FANTASY TRADE", "large" if W >= 160 else "body", WHITE)
            text_center(d, W // 2, y + bh + 27, lg_name[:40], "small", (255, 210, 200))
            return
        on = int(t * 2) % 2 == 0
        d.rectangle([0, 0, W - 1, 13], fill=RED if on else (140, 16, 12))
        text_center(d, W // 2, 3, "BREAKING NEWS  -  TRADE ALERT" if W >= 160 else "TRADE ALERT", "body", WHITE)
        _draw_trade(img, rc, tr, 18, H - 12)
        d.line([(0, H - 11), (W - 1, H - 11)], fill=DARK)
        text(d, 2, H - 8, lg_name[:30], "small", GOLD)
        text_right(d, W - 2, H - 8, _ago(tr["date"], rc.now), "small", RED if on else WHITE)

    def _pending(self, img, d, rc, tr, t, lg_name):
        """The heads-up version: a trade was accepted and is waiting out the league's review period."""
        W, H = rc.W, rc.main_h
        on = int(t * 1.5) % 2 == 0
        d.rectangle([0, 0, W - 1, 13], fill=(200, 120, 0) if on else (130, 76, 0))
        text_center(d, W // 2, 3, "TRADE ACCEPTED  -  UNDER REVIEW" if W >= 200 else "TRADE PENDING", "body", WHITE)
        _draw_trade(img, rc, tr, 18, H - 12)
        d.line([(0, H - 11), (W - 1, H - 11)], fill=DARK)
        text(d, 2, H - 8, lg_name[:30], "small", GOLD)
        right = "VETO VOTES OPEN"
        if tr.get("process_date"):
            left = (datetime.fromisoformat(tr["process_date"]) - rc.now).total_seconds()
            if left > 0:
                right = f"PROCESSES IN {int(left // 3600)}H {int(left % 3600 // 60)}M"
        text_right(d, W - 2, H - 8, right, "small", ORANGE)


def all_pending(rc: RC) -> list[dict]:
    """Accepted trades still in the league's review period."""
    return ((rc.data("fantasy_trades") or {}).get("pending")) or []


# ====================================================================== player props

class PropsScreen(Screen):
    """Live player props for the game(s) that matter now; my fantasy players first, then line movers."""
    name, label = "props", "NFL player props"
    page_seconds = 8.0

    def _pages(self, rc):
        b = rc.data("props") or {}
        props, mv = b.get("props") or [], b.get("movers") or []
        rows = max(1, (rc.main_h - 11) // 9)
        pages = [("props", props[i:i + rows]) for i in range(0, min(len(props), rows * 3), rows)]
        if mv:
            pages.append(("movers", mv[:rows]))
        return b, pages

    def available(self, rc):
        return bool(self._pages(rc)[1])

    def seconds(self, rc):
        return len(self._pages(rc)[1]) * self.page_seconds

    def render(self, img, rc, t):
        b, pages = self._pages(rc)
        d = ImageDraw.Draw(img)
        W = rc.W
        kind, rows = pages[int(t // self.page_seconds) % len(pages)]
        g = (b.get("games") or [{}])[0]
        head = "PLAYER PROPS" if kind == "props" else "BIGGEST LINE MOVES"
        text(d, 2, 1, head, "body", GOLD)
        gl = b.get("games") or []
        games = (f"{gl[0]['reason']} {gl[0]['label']}" + (f" +{len(gl) - 1}" if len(gl) > 1 else "")) if gl else ""
        text_right(d, W - 2, 1, games[:34], "small", RED if g.get("state") == "in" else GRAY)
        d.line([(0, 9), (W - 1, 9)], fill=DARK)
        rh = max(9, min(12, (rc.main_h - 11) // max(1, len(rows))))    # spread out when there's height to spare
        for i, p in enumerate(rows):
            y = 11 + i * rh
            tag, tcol = (("*", BLUE) if p.get("pinned") else ("F", GOLD) if p["my_fantasy"] else
                         ("T", ORANGE) if p["my_team"] else ("", DIM))
            text(d, 2, y, tag, "small", tcol)
            who = p.get("short_name") or p["name"]
            text(d, 9, y, who.upper()[:16], "small",
                 BLUE if p.get("pinned") else GOLD if p["my_fantasy"] else WHITE)
            text(d, 80, y, p["team"], "tiny", DIM)
            text(d, 96, y, p["market"], "tiny", GRAY)
            text_right(d, 160, y, f"{p['line']:g}", "small", WHITE)
            if p.get("live") is not None:
                frac = min(1.0, p["live"] / max(p["line"], 0.5))
                over = p["live"] > p["line"]
                d.rectangle([166, y + 1, 222, y + 5], outline=DIM)
                d.rectangle([167, y + 2, 167 + int(54 * frac), y + 4], fill=GREEN if over else YELLOW)
                text_right(d, W - 2, y, f"{p['live']:g}", "small", GREEN if over else WHITE)
            else:
                mv = p.get("move") or 0
                if mv:
                    arrow(d, 168, y + 1, mv > 0, 5)
                    text(d, 175, y, f"{mv:+g} SINCE OPEN", "tiny", GREEN if mv > 0 else RED)
                else:
                    text(d, 168, y, "OPENING LINE", "tiny", DIM)


def all_screens() -> dict[str, Screen]:
    screens: list[Screen] = [AlertScreen(), TradeAlert(), BreakingTrade(), GameSpotlight(), FantasyMatchup(), FantasyScoreboard(),
                             FantasyStandings(), PlayerOfWeek(), WeatherScreen(), LeagueBoard("cfb"),
                             LeagueBoard("nfl"), MarketsScreen(), PropsScreen()]
    return {s.name: s for s in screens}
