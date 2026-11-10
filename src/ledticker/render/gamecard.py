"""One game drawn for any section shape, shared by the spotlight and the featured-game widgets.

Shapes (see `shape`): "full" (most of the display), "wide" (a top or bottom half), "tall" (a left or right half) and
"tile" (one module). Every layout is computed from the section's real size, so a 116 px tall section (ticker on)
and a 128 px one (ticker off) are both filled. Spare room lists the other games that matter.
"""
from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from .draw import (DARK, DIM, GOLD, GRAY, RED, WHITE, YELLOW, big_text, big_width, text, text_center, text_right,
                   triangle_right)
from .fonts import fit, font, text_height, text_width
from .screens import (LEAGUE_NAMES, LEAGUE_SHORT, RC, compact_when, day_label, draw_logo, short_time, team_label)


def shape(W: int, H: int) -> str:
    wide, tall = W >= 192, H >= 96
    return "full" if wide and tall else "wide" if wide else "tall" if tall else "tile"


def role_of(f, roles=("huge", "large", "bold", "medium", "body", "small", "tiny")) -> str:
    return next((r for r in roles if font(r) is f), "small")


def primetime_tag(g: dict, rc: RC, games: list[dict]) -> str:
    """TNF / SNF / MNF for a standalone NFL game."""
    if sum(1 for x in games if x["start_utc"] == g["start_utc"]) != 1:
        return ""
    dt = datetime.fromisoformat(g["start_utc"]).astimezone(rc.ctx.tz)
    return {3: "TNF", 0: "MNF"}.get(dt.weekday(), "SNF" if dt.weekday() == 6 and dt.hour >= 18 else "")


def _mine(rc: RC, lg: str) -> set[str]:
    return {str(x) for x in rc.ctx.section("scores", lg).get("teams", [])}


def _team_col(g: dict, tm: dict, mine: set[str]):
    if g["state"] == "post" and not tm.get("winner") and any(t.get("winner") for t in (g["away"], g["home"])):
        return GRAY
    return GOLD if tm["id"] in mine else WHITE


def _status(g: dict) -> tuple[str, tuple]:
    if g["state"] == "post":
        return (g.get("status") or "FINAL").upper(), GRAY
    return (g.get("status") or "").upper(), YELLOW


def _detail(g: dict) -> tuple[str, tuple] | None:
    """The line under the score: down & distance live, the odds before kickoff, a note after."""
    odds = g.get("odds") or {}
    if g["state"] == "in" and g.get("down_distance"):
        return g["down_distance"].upper(), (RED if g.get("red_zone") else WHITE)
    if g["state"] == "post" and g.get("note"):
        return g["note"].upper(), GRAY
    if odds.get("details"):
        ou = f"  O/U {odds['over_under']:g}" if odds.get("over_under") else ""
        return f"{odds['details']}{ou}", GOLD
    return None


def _header(d, rc, lg, g, W, tag, y=1, role="body"):
    sb = rc.data(f"scores.{lg}") or {}
    name = LEAGUE_NAMES[lg] if W >= 192 else LEAGUE_SHORT[lg]
    wk = sb.get("week") if lg in ("nfl", "cfb") else None
    head = name + ((f"  WEEK {wk}" if W >= 192 else f" WK {wk}") if wk else "")
    if tag:
        head = f"{tag}  {head}" if W >= 192 else tag
    lw = text(d, 2, y, head, role, GOLD)
    if g.get("broadcast"):
        b, f = fit(g["broadcast"].upper(), W - lw - 10, (role, "small", "tiny"))
        if b and b != ".":
            text_right(d, W - 2, y + (text_height(font(role)) - text_height(f)), b, role_of(f), GRAY)


def other_line(d, rc, lg2: str, g2: dict, x: int, y: int, W: int, show_league: bool, logos_img=None) -> None:
    """One compact line for another game: [league] AWAY @ HOME [score]  ....  WHEN."""
    tz = rc.ctx.tz
    if g2["state"] == "in":
        when, wc = (g2.get("status") or "").upper().split(" - ")[-1], YELLOW
    elif g2["state"] == "post":
        when, wc = "FINAL", GRAY
    else:
        when, wc = f"{day_label(g2['start_utc'], tz, rc.now)} {short_time(g2['start_utc'], tz)}", GRAY
    ww = text_right(d, x + W - 1, y, when[:12], "small", wc)
    lx = x
    if show_league:
        lx += text(d, lx, y, LEAGUE_SHORT[lg2], "small", DIM) + 4
    if logos_img is not None:
        for tm in (g2["away"], g2["home"]):
            draw_logo(logos_img, rc, lg2, tm, lx, y - 1, 8)
            lx += 10
    room = x + W - 1 - ww - 4 - lx
    sep = " VS " if g2.get("neutral_site") else " @ "
    full = team_label(g2["away"]) + sep + team_label(g2["home"])
    short = g2["away"]["abbr"] + ("@" if sep == " @ " else "v") + g2["home"]["abbr"]
    if g2["state"] != "pre":
        sc = f" {g2['away'].get('score') or 0}-{g2['home'].get('score') or 0}"
        full, short = full + sc, short + sc
    s, _ = fit(full if text_width(full, font("small")) <= room else short, room, ("small",))
    text(d, lx, y, s, "small", WHITE)


# ------------------------------------------------------------------------------------------------ full

def _full(img, d, rc, lg, g, others, tag):
    W, H = rc.W, rc.main_h
    mine = _mine(rc, lg)
    _header(d, rc, lg, g, W, tag)
    d.line([(0, 10), (W - 1, 10)], fill=DARK)
    n_oth = min(len(others), 4, max(0, (H - 11 - 78 - 3) // 9))
    y0, y1 = 12, H - (n_oth * 9 + 3 if n_oth else 0)
    bh = y1 - y0
    cx = W // 2

    # team columns: logo, name, record, centered in the block
    logo = max(28, min(50, bh - 26))
    for tm, x in ((g["away"], 6), (g["home"], W - 6 - logo)):
        has_rec = bool(tm.get("record"))
        col_h = logo + 3 + 13 + (9 if has_rec else 0)
        ty = y0 + max(0, (bh - col_h) // 2)
        draw_logo(img, rc, lg, tm, x, ty, logo)
        lc = x + logo // 2
        label, f = fit(team_label(tm), 74, ("bold", "body"))
        text_center(d, lc, ty + logo + 3 + (13 - text_height(f)), label, role_of(f), _team_col(g, tm, mine))
        if has_rec:
            text_center(d, lc, ty + logo + 18, tm["record"], "small", GRAY)
        if g["state"] == "in" and g.get("possession") == tm["abbr"]:
            py = ty + logo // 2 - 3
            if x < cx:
                triangle_right(d, x + logo + 3, py, 7, YELLOW)
            else:
                d.polygon([(x - 4, py), (x - 4, py + 6), (x - 7, py + 3)], fill=YELLOW)

    # center column: built as a stack, then centered vertically in the block
    stack: list[tuple[int, callable]] = []
    if g["state"] == "pre":
        tz = rc.ctx.tz
        local = datetime.fromisoformat(g["start_utc"]).astimezone(tz)
        hm, ap = local.strftime("%I:%M").lstrip("0"), local.strftime("%p")
        stack.append((8, lambda y: text_center(d, cx, y, f"{day_label(g['start_utc'], tz, rc.now)} {local:%b %d}".upper(), "body", GRAY)))
        scale = 2 if big_width(hm, "huge", 2) <= W - 2 * (logo + 16) else 1
        stack.append((20 * scale, lambda y: big_text(img, cx, y, hm, "huge", scale, WHITE, anchor="c")))
        stack.append((8, lambda y: text_center(d, cx, y, ap, "body", GRAY)))
    else:
        a, h = g["away"], g["home"]
        sa, sh = str(a.get("score") or 0), str(h.get("score") or 0)
        scale = 2 if big_width(sa + sh, "huge", 2) + 18 <= W - 2 * (logo + 14) else 1
        stack.append((20 * scale, lambda y: (big_text(img, cx - 8, y, sa, "huge", scale, _team_col(g, a, set()), anchor="r"),
                                             big_text(img, cx + 9, y, sh, "huge", scale, _team_col(g, h, set()), anchor="l"),
                                             d.rectangle([cx - 2, y + 9 * scale, cx + 2, y + 9 * scale + 2], fill=DIM))))
        st, sc = _status(g)
        s, f = fit(st, W - 2 * (logo + 12), ("bold", "body", "small"))
        stack.append((13, lambda y: text_center(d, cx, y + 13 - text_height(f), s, role_of(f), sc)))
        if g["state"] == "in" and g.get("outs") is not None and not g.get("down_distance"):
            stack.append((16, lambda y: bases(d, cx, y, g)))
    det = _detail(g)
    if det:
        s2, f2 = fit(det[0], W - 2 * (logo + 10), ("body", "small", "tiny"))
        stack.append((8, lambda y: text_center(d, cx, y, s2, role_of(f2), det[1])))
    gap = 3
    total = sum(hh for hh, _ in stack) + gap * (len(stack) - 1)
    while total > bh and len(stack) > 2:          # drop the least important line until it fits
        hh, _ = stack.pop()
        total -= hh + gap
    y = y0 + max(0, (bh - total) // 2)
    for hh, draw in stack:
        draw(y)
        y += hh + gap

    if n_oth:
        d.line([(0, y1), (W - 1, y1)], fill=DARK)
        multi = len({lg2 for lg2, _ in others[:n_oth]} | {lg}) > 1
        for i, (lg2, g2) in enumerate(others[:n_oth]):
            other_line(d, rc, lg2, g2, 3, y1 + 3 + i * 9, W - 6, multi, img)


def bases(d, cx, y, g):
    on = set((g.get("bases") or "").split("-"))
    for key, (dx, dy) in {"2": (0, 0), "1": (6, 6), "3": (-6, 6)}.items():
        pts = [(cx + dx, y + dy), (cx + dx + 4, y + dy + 4), (cx + dx, y + dy + 8), (cx + dx - 4, y + dy + 4)]
        d.polygon(pts, fill=YELLOW if key in on else None, outline=GRAY)
    outs = g.get("outs") or 0
    for i in range(3):
        d.ellipse([cx + 16 + i * 6, y + 6, cx + 20 + i * 6, y + 10], fill=RED if i < outs else DARK)
    text(d, cx - 34, y + 4, f"{g.get('balls', 0)}-{g.get('strikes', 0)}", "body", WHITE)


# ------------------------------------------------------------------------------------------------ wide strip

def _wide(img, d, rc, lg, g, others, tag):
    """A top/bottom half (~256x58): teams at the edges with their scores toward the middle, status underneath."""
    W, H = rc.W, rc.main_h
    mine = _mine(rc, lg)
    _header(d, rc, lg, g, W, tag, role="small")
    d.line([(0, 8), (W - 1, 8)], fill=DARK)
    foot = 9
    y0, y1 = 10, H - foot
    bh = y1 - y0
    logo = max(14, min(44, bh - 2))
    cx = W // 2
    pre = g["state"] == "pre"
    score_role = "huge" if bh >= 22 else "bold"
    for tm, left in ((g["away"], True), (g["home"], False)):
        x = 2 if left else W - 2 - logo
        draw_logo(img, rc, lg, tm, x, y0 + (bh - logo) // 2, logo)
        col = _team_col(g, tm, mine)
        nx = x + logo + 5 if left else x - 5
        score = "" if pre else str(tm.get("score") or 0)
        sw = text_width(score, font(score_role)) if score else 0
        room = (cx - 10 - sw - 4 - nx) if left else (nx - (cx + 10 + sw + 4))
        if pre:
            room = (cx - 24 - nx) if left else (nx - (cx + 24))
        label, f = fit(team_label(tm), room, ("bold", "body", "small"))
        rec = tm.get("record") or ""
        blk = text_height(f) + (9 if rec else 0)
        ty = y0 + (bh - blk) // 2
        (text if left else text_right)(d, nx, ty, label, role_of(f), col)
        if rec:
            (text if left else text_right)(d, nx, ty + text_height(f) + 2, rec, "small", GRAY)
        if score:
            sy = y0 + (bh - text_height(font(score_role))) // 2
            if left:
                text_right(d, cx - 10, sy, score, score_role, col)
            else:
                text(d, cx + 10, sy, score, score_role, col)
            if g["state"] == "in" and g.get("possession") == tm["abbr"]:
                px = cx - 10 - sw - 6 if left else cx + 10 + sw + 3
                d.rectangle([px, sy + 8, px + 2, sy + 10], fill=YELLOW)
    if pre:
        local = datetime.fromisoformat(g["start_utc"]).astimezone(rc.ctx.tz)
        hm = local.strftime("%I:%M").lstrip("0")
        top = y0 + (bh - 30) // 2
        text_center(d, cx, top, day_label(g["start_utc"], rc.ctx.tz, rc.now), "small", GRAY)
        big_text(img, cx, top + 8, hm, "large", 1, WHITE, anchor="c")
        text_center(d, cx, top + 27, local.strftime("%p"), "tiny", GRAY)
    else:
        text_center(d, cx, y0 + (bh - 7) // 2 - 1, "-", "body", DIM)
    # footer: status on the left, detail on the right (or centered when there's only one)
    d.line([(0, y1), (W - 1, y1)], fill=DARK)
    fy = y1 + 2
    det = _detail(g)
    if pre:   # the kickoff time is already big in the middle: the footer carries the date and the line
        local = datetime.fromisoformat(g["start_utc"]).astimezone(rc.ctx.tz)
        lw = text(d, 2, fy, f"{local:%a %b} {local.day}".upper(), "small", GRAY)
        if det:
            s, f = fit(det[0], W - lw - 12, ("small", "tiny"))
            text_right(d, W - 2, fy, s, role_of(f), det[1])
    else:
        st, sc = _status(g)
        lw = text(d, 2, fy, st[:22], "small", sc)
        if det:
            s, f = fit(det[0], W - lw - 12, ("small", "tiny"))
            text_right(d, W - 2, fy, s, role_of(f), det[1])


# ------------------------------------------------------------------------------------------------ tall column

def _tall(img, d, rc, lg, g, others, tag):
    """A left/right half (~128x116): both teams side by side up top, each with its score underneath."""
    W, H = rc.W, rc.main_h
    mine = _mine(rc, lg)
    _header(d, rc, lg, g, W, tag)
    d.line([(0, 9), (W - 1, 9)], fill=DARK)
    colw = W // 2
    pre = g["state"] == "pre"
    det = _detail(g)
    has_rec = any(tm.get("record") for tm in (g["away"], g["home"]))
    # vertical plan, top to bottom: logo, name, record, (score), status or kickoff, detail
    def content(lg_px):
        return lg_px + 2 + 13 + (9 if has_rec else 0) + (17 if pre else 4 + 20 + 3 + 9) + (9 if det else 0)
    logo = max(20, min(40, (W - 28) // 2))
    while logo > 24 and content(logo) > H - 12:
        logo -= 2
    n_oth = min(len(others), 4, max(0, (H - 12 - content(logo) - 3) // 9))
    y1 = H - (n_oth * 9 + 3 if n_oth else 0)
    y = 11 + max(0, (y1 - 11 - content(logo)) // 2)
    name_y = y + logo + 2
    rec_y = name_y + 13
    below = rec_y + (9 if has_rec else 0)
    for k, tm in enumerate((g["away"], g["home"])):
        lc = k * colw + colw // 2
        draw_logo(img, rc, lg, tm, lc - logo // 2, y, logo)
        col = _team_col(g, tm, mine)
        label, f = fit(team_label(tm), colw - 4, ("bold", "body", "small"))
        text_center(d, lc, name_y + (13 - text_height(f)), label, role_of(f), col)
        if tm.get("record"):
            text_center(d, lc, rec_y, tm["record"], "small", GRAY)
        if not pre:
            sy = below + 4
            sw = big_text(img, lc, sy, str(tm.get("score") or 0), "huge", 1, col, anchor="c")
            if g["state"] == "in" and g.get("possession") == tm["abbr"]:
                d.rectangle([lc + sw // 2 + 3, sy + 8, lc + sw // 2 + 5, sy + 10], fill=YELLOW)
    text_center(d, colw, y + logo // 2 - 3, "VS" if g.get("neutral_site") else "@", "body", DIM)
    if pre:
        when = f"{day_label(g['start_utc'], rc.ctx.tz, rc.now)} {short_time(g['start_utc'], rc.ctx.tz)}"
        s, f = fit(when, W - 4, ("bold", "body"))
        text_center(d, colw, below + 2, s, role_of(f), WHITE)
        ny = below + 17
    else:
        st, sc = _status(g)
        s, f = fit(st, W - 4, ("body", "small"))
        ny = below + 4 + 20 + 3
        text_center(d, colw, ny, s, role_of(f), sc)
        ny += 9
    if det and ny + 7 <= y1:
        s, f = fit(det[0], W - 4, ("body", "small", "tiny"))
        text_center(d, colw, ny, s, role_of(f), det[1])
    if n_oth:
        d.line([(0, y1), (W - 1, y1)], fill=DARK)
        multi = len({lg2 for lg2, _ in others[:n_oth]} | {lg}) > 1
        for i, (lg2, g2) in enumerate(others[:n_oth]):
            other_line(d, rc, lg2, g2, 2, y1 + 3 + i * 9, W - 4, multi, img)


# ------------------------------------------------------------------------------------------------ module tile

def _tile(img, d, rc, lg, g, others, tag):
    """One module (~128x58): logos at the sides, score or kickoff in the middle, detail along the bottom."""
    W, H = rc.W, rc.main_h
    mine = _mine(rc, lg)
    head = (tag or LEAGUE_SHORT[lg]) + ("  " + (g.get("broadcast") or "").upper()[:12] if g.get("broadcast") else "")
    text(d, 2, 1, head, "small", GOLD)
    size = 22 if H >= 60 else 20
    for tm, x, left in ((g["away"], 2, True), (g["home"], W - 2 - size, False)):
        draw_logo(img, rc, lg, tm, x, 9, size)
        lx = x + size // 2
        label, f = fit(team_label(tm), 40, ("small", "tiny"))
        lw = text_width(label, f)
        # centered under the logo, but never past the section's edge ("#13 TENN" is wider than a 20 px logo)
        tx = max(1, lx - lw // 2) if left else min(W - 1 - lw, lx - lw // 2)
        text(d, tx, 10 + size, label, role_of(f), _team_col(g, tm, mine))
        if tm.get("record") and H >= 52:
            text_center(d, lx, 17 + size, tm["record"], "tiny", GRAY)
    cx = W // 2
    if g["state"] == "pre":
        text_center(d, cx, 11, day_label(g["start_utc"], rc.ctx.tz, rc.now), "small", GRAY)
        text_center(d, cx, 19, short_time(g["start_utc"], rc.ctx.tz), "bold", WHITE)
    else:
        a, h = g["away"], g["home"]
        text_right(d, cx - 3, 12, str(a.get("score") or 0), "bold", _team_col(g, a, set()))
        text(d, cx + 4, 12, str(h.get("score") or 0), "bold", _team_col(g, h, set()))
        st, sc = _status(g)
        text_center(d, cx, 27, fit(st, W - 2 * size - 8, ("small", "tiny"))[0], "small", sc)
        if g.get("possession"):
            side_x = 2 + size + 2 if g["possession"] == a["abbr"] else W - 2 - size - 6
            d.rectangle([side_x, 18, side_x + 3, 20], fill=YELLOW)
    det = _detail(g)
    if det and H >= 46:
        s, f = fit(det[0], W - 4, ("small", "tiny"))
        text_center(d, cx, H - 8, s, role_of(f), det[1])


def draw_game(img: Image.Image, rc: RC, lg: str, g: dict, others: list[tuple[str, dict]] | None = None,
              tag: str = "") -> None:
    d = ImageDraw.Draw(img)
    {"full": _full, "wide": _wide, "tall": _tall, "tile": _tile}[shape(rc.W, rc.main_h)](
        img, d, rc, lg, g, list(others or []), tag)
