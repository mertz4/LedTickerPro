"""Big-play celebration: a full-display takeover when one of my fantasy players makes a big play, or my NFL team
scores (the Texans get their own theme: battle red / navy crowd, a charging bull, red-white-blue fireworks).

Timeline (t = seconds since it started):
  0    - 4.6   retro stadium: night sky, light towers, a pixel crowd, and the play itself (a leaping catch, a run
               through the defense, or a field goal splitting the uprights)
  3.8  - 7.6   fireworks over the stadium and the giant TOUCHDOWN! / BIG PLAY! headline
  7.6  - end   what happened: player (headshot), the play, fantasy points, the score, and ESPN's description

The scene is drawn at half resolution and scaled up 2x on the big display (chunky, retro), fireworks at full
resolution on top so the sparks stay fine. Everything is a pure function of t (seeded randomness, no state).

Plays with mood "boo" (a rival owner's player, or a rival NFL team scoring) get the stinker instead: see below.
"""
from __future__ import annotations

import math
import random
import zlib
from functools import lru_cache

from PIL import Image, ImageDraw

from .draw import (DARK, GOLD, GRAY, WHITE, big_text, big_width, hex_color, paste_icon, readable_on_black,
                   team_fallback, text, text_center, text_right)
from .fonts import fit, font, text_width
from .screens import RC, Screen

SCENE_END = 4.6
HEADLINE_END = 7.6
FIREWORKS_START = 3.8

# ---------------------------------------------------------------- themes

TEXANS = {
    "key": "texans", "primary": (28, 52, 120), "accent": (210, 20, 48), "light": (255, 255, 255),
    "crowd": ((210, 20, 48), (255, 255, 255), (40, 70, 160), (170, 14, 36), (230, 230, 240)),
    "sparks": ((255, 40, 60), (255, 255, 255), (70, 120, 255), (255, 120, 130), (160, 190, 255)),
    "endzone": (24, 44, 104), "banner": ((200, 18, 44), (24, 44, 104)), "name": "TEXANS", "bull": True,
}
FANTASY = {
    "key": "fantasy", "primary": (255, 190, 40), "accent": (255, 120, 0), "light": (255, 255, 255),
    "crowd": ((255, 190, 40), (255, 255, 255), (70, 150, 255), (255, 80, 60), (60, 200, 110), (200, 120, 255)),
    "sparks": ((255, 210, 60), (255, 80, 60), (80, 170, 255), (90, 255, 140), (255, 120, 220), (255, 255, 255)),
    "endzone": (110, 70, 10), "banner": ((220, 140, 0), (150, 70, 0)), "name": "", "bull": False,
}
THEMES = {"HOU": TEXANS}


def team_theme(abbr: str, color: str | None, alt: str | None) -> dict:
    """A theme for one of my NFL teams: hand-made for the Texans, otherwise built from the team's colors."""
    if abbr in THEMES:
        return THEMES[abbr]
    p = readable_on_black(hex_color(color, (90, 90, 160)), 60)
    a = readable_on_black(hex_color(alt, (220, 220, 220)), 90)
    return {"key": abbr.lower(), "primary": p, "accent": a, "light": WHITE,
            "crowd": (p, a, WHITE, _mix(p, WHITE, .4), _mix(a, (0, 0, 0), .3)),
            "sparks": (a, WHITE, _mix(p, WHITE, .5), _mix(a, WHITE, .4)), "endzone": _mix(p, (0, 0, 0), .35),
            "banner": (_mix(p, WHITE, .1), _mix(p, (0, 0, 0), .4)), "name": abbr, "bull": False}


def theme_for(play: dict) -> dict:
    if play.get("team_score"):
        return team_theme(play["team_score"], play.get("team_color"), play.get("team_alt"))
    return FANTASY


def _mix(a, b, k: float) -> tuple[int, int, int]:
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def _shade(c, k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * k))) for v in c)


# ---------------------------------------------------------------- sprites
# H helmet  M facemask  J jersey  N number  P pants  S socks  B shoes  G gloves  F ball  (. = clear)

RUN_A = (
    "....HHHH....",
    "...HHHHHHM..",
    "...HHHHHMM..",
    "....HHHH....",
    "...JJJJJJ...",
    "..JJNNJJJJG.",
    ".GJJNNJJJ...",
    "...JJJJJJ...",
    "...PPPPPP...",
    "...PP..PPP..",
    "..PP....PP..",
    ".SS......SS.",
    "BB.........B",
)
RUN_B = (
    "....HHHH....",
    "...HHHHHHM..",
    "...HHHHHMM..",
    "....HHHH....",
    "...JJJJJJ...",
    "...JNNJJJG..",
    "..GJNNJJ....",
    "...JJJJJJ...",
    "...PPPPPP...",
    "....PPPP....",
    "....PPSS....",
    "....SS.SS...",
    "....BB.BB...",
)
CARRY_A = tuple(r[:10] + "F" + r[11:] if i == 5 else r for i, r in enumerate(RUN_A))
CARRY_B = tuple(r[:9] + "F" + r[10:] if i == 5 else r for i, r in enumerate(RUN_B))
JUMP = (
    ".G.......G..",
    ".JJ.....JJ..",
    "..JJHHHHJ...",
    "...HHHHHHM..",
    "...HHHHHMM..",
    "....JJJJ....",
    "...JJNNJJ...",
    "...JJNNJJ...",
    "...JJJJJJ...",
    "...PPPPPP...",
    "..PPP..PPP..",
    ".SS......SS.",
    ".BB......BB.",
)
CATCH = tuple(r.replace("G", "F", 1) if i == 0 else r for i, r in enumerate(JUMP))
KICK = (
    "....HHHH....",
    "...HHHHHHM..",
    "...HHHHHMM..",
    "....HHHH....",
    "..GJJJJJJ...",
    "...JNNJJJG..",
    "...JNNJJ....",
    "...JJJJJ....",
    "...PPPPPPP..",
    "...PP...PPSS",
    "...PP.....BB",
    "...SS.......",
    "...BB.......",
)
BULL_A = (
    "...............WW..",
    "..............WRRR.",
    ".RRRRRRRRRRRRRRRRER",
    "RRRRRRRRRRRRRRRRRRRN",
    ".RRRRRRRRRRRRRRRRRR.",
    "..RRRRRRRRRRRRRRR...",
    "..RRRRRRRRRRRRRR....",
    "..RR.RR....RR.RR....",
    ".RR...RR..RR...RR...",
    ".W.....W..W.....W...",
)
BULL_B = BULL_A[:7] + (
    "...RRRR.....RRRR....",
    "...RR.RR....RR.RR...",
    "...W...W....W...W...",
)


@lru_cache(maxsize=256)
def sprite(rows: tuple[str, ...], palette: tuple[tuple[str, tuple], ...], flip: bool = False) -> Image.Image:
    pal = dict(palette)
    w, h = max(len(r) for r in rows), len(rows)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch in pal:
                px[x, y] = (*pal[ch], 255)
    return im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im


def player_palette(jersey, helmet, number=WHITE) -> tuple:
    return (("H", helmet), ("M", (170, 170, 180)), ("J", jersey), ("N", number), ("P", (225, 225, 230)),
            ("S", jersey), ("B", (70, 70, 80)), ("G", (255, 255, 255)), ("F", (160, 85, 30)))


BULL_PAL = (("R", (205, 20, 46)), ("W", (255, 255, 255)), ("E", (255, 255, 255)), ("N", (255, 150, 160)))


def _paste(img: Image.Image, im: Image.Image, x: float, y: float) -> None:
    img.paste(im, (int(round(x)), int(round(y))), im)


# ---------------------------------------------------------------- the stadium

@lru_cache(maxsize=8)
def stadium(w: int, h: int, key: str, crowd: tuple, endzone: tuple) -> Image.Image:
    """Static backdrop at scene resolution: sky + stars, light towers, stands, field with yard lines, end zone."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    sky_h, stand_b = int(h * .2), int(h * .52)
    for y in range(sky_h + 1):                       # night sky, a touch lighter near the stadium glow
        k = y / max(1, sky_h)
        d.line([(0, y), (w, y)], fill=(int(4 + 10 * k), int(6 + 12 * k), int(18 + 26 * k)))
    for _ in range(w // 6):
        d.point((rnd.randrange(w), rnd.randrange(max(1, sky_h - 2))), fill=(120, 120, 150))
    # stands: a dark bowl with the crowd as dots in rows (rows rise toward the back)
    d.polygon([(0, sky_h), (w, sky_h), (w, stand_b), (0, stand_b)], fill=(16, 16, 26))
    for y in range(sky_h + 2, stand_b - 2, 2):
        for x in range(1 + (y // 2) % 2, w, 2):
            c = crowd[rnd.randrange(len(crowd))]
            img.putpixel((x, y), _shade(c, .55 + .25 * rnd.random()))
    d.line([(0, stand_b - 1), (w, stand_b - 1)], fill=(60, 60, 75))   # the wall
    d.line([(0, stand_b), (w, stand_b)], fill=(25, 25, 32))
    # light towers at both ends
    for tx in (int(w * .06), int(w * .94)):
        d.line([(tx, 2), (tx, stand_b - 2)], fill=(70, 70, 85))
        d.rectangle([tx - 4, 1, tx + 4, 4], fill=(90, 90, 105))
        for lx in range(tx - 3, tx + 4, 2):
            d.point((lx, 2), fill=(255, 250, 215))
            d.point((lx, 3), fill=(255, 235, 170))
    # field: mowed stripes, yard lines slanting a little for depth, the end zone on the right
    fy = stand_b + 1
    ez = int(w * .82)
    for i, x in enumerate(range(0, ez, max(4, w // 16))):
        d.rectangle([x, fy, x + max(4, w // 16) - 1, h], fill=(22, 96, 34) if i % 2 else (18, 82, 30))
        top, bot = x + 2, x - 2
        d.line([(top, fy), (bot, h)], fill=(120, 170, 125))
    d.rectangle([ez, fy, w, h], fill=endzone)
    d.line([(ez, fy), (ez - 4, h)], fill=(235, 235, 235))
    for x in range(ez + 3, w - 1, 4):               # end-zone hash pattern
        d.point((x, fy + (h - fy) // 2), fill=_shade(endzone, 1.8))
    return img


def crowd_flicker(img: Image.Image, w: int, h: int, t: float, crowd: tuple, hype: float) -> None:
    """Camera flashes and a few waving fans; `hype` (0-1) turns it up after the big moment."""
    sky_h, stand_b = int(h * .2), int(h * .52)
    rows = list(range(sky_h + 2, stand_b - 2, 2))
    if not rows:
        return
    d = ImageDraw.Draw(img)
    frame = int(t * 12)
    rnd = random.Random(frame)
    for _ in range(int(3 + 22 * hype)):
        y = rnd.choice(rows)
        x = rnd.randrange(w)
        d.point((x, y), fill=(255, 255, 255))
    if hype > 0:                                     # fans jumping: a row bounces up a pixel
        for y in rows:
            if (y // 2 + frame) % 3 == 0:
                for x in range(1 + (y // 2) % 2, w, 4):
                    c = crowd[(x * 7 + y) % len(crowd)]
                    d.point((x, y - 1), fill=c)


# ---------------------------------------------------------------- the play

def _arc(p0, p1, k: float, lift: float) -> tuple[float, float]:
    k = max(0.0, min(1.0, k))
    return p0[0] + (p1[0] - p0[0]) * k, p0[1] + (p1[1] - p0[1]) * k - lift * 4 * k * (1 - k)


def draw_ball(img, x, y, spin: int = 0) -> None:
    d = ImageDraw.Draw(img)
    x, y = int(round(x)), int(round(y))
    d.rectangle([x, y, x + 2, y + 1], fill=(160, 85, 30))
    d.point((x + 1, y + (spin % 2)), fill=(255, 255, 255))


def play_scene(img: Image.Image, t: float, play: dict, th: dict) -> None:
    """Animate the play at scene resolution. Ends with the player celebrating in the end zone."""
    w, h = img.size
    feet = int(h * .9)
    jersey = th["primary"] if th["key"] != "fantasy" else readable_on_black(hex_color(play.get("team_color")), 80)
    helmet = th["accent"] if th["key"] != "fantasy" else readable_on_black(hex_color(play.get("team_alt"), (200, 200, 210)), 80)
    pal = player_palette(jersey, helmet)
    foe = player_palette((150, 150, 160), (110, 110, 120))
    kind = play.get("kind") or "catch"
    run_frame = lambda tt: (RUN_A, RUN_B)[int(tt * 9) % 2]
    carry_frame = lambda tt: (CARRY_A, CARRY_B)[int(tt * 9) % 2]
    ez_x = w * .86
    sh = len(RUN_A)

    def celebrate(x, tt):
        hop = abs(math.sin(tt * 7)) * 5
        _paste(img, sprite(CATCH if int(tt * 3.5) % 2 else JUMP, pal), x, feet - sh - hop)

    if kind == "kick":
        kx, post = w * .2, w * .9
        cross = int(h * .64)
        d = ImageDraw.Draw(img)                       # goalposts (drawn in the scene, not the backdrop)
        d.line([(post, cross), (post, feet)], fill=(255, 220, 40))
        d.line([(post - 5, cross), (post + 5, cross)], fill=(255, 220, 40))
        for ux in (post - 5, post + 5):
            d.line([(ux, int(h * .3)), (ux, cross)], fill=(255, 220, 40))
        if t < 0.9:
            _paste(img, sprite(run_frame(t), pal), kx - 14 + 14 * t / 0.9, feet - sh)
            draw_ball(img, kx + 9, feet - 3)
        elif t < 1.4:
            _paste(img, sprite(KICK, pal), kx, feet - sh)
        else:
            celebrate(kx, t)
        if t >= 0.9:
            k = min(1.0, (t - 0.9) / 1.6)
            bx, by = _arc((kx + 10, feet - 3), (post - 1, cross - 10), k, h * .35)
            if k < 1 or int(t * 6) % 2:
                draw_ball(img, bx, by, int(t * 10))
        if 2.6 < t < SCENE_END:
            g = "GOOD!"
            text_center(ImageDraw.Draw(img), int(post) - 2, int(h * .2) + 1, g, "small",
                        th["light"] if int(t * 5) % 2 else th["accent"])
        return

    if kind == "run":
        defenders = (w * .38, w * .62)
        x = -12 + (ez_x + 12) * min(1.0, t / 3.4)
        for i, dx in enumerate(defenders):
            beaten = x > dx + 2
            dsp = sprite(RUN_A, foe, flip=True)
            if beaten:                               # dove and missed: lying on the turf
                lie = dsp.rotate(90 if i % 2 else -90, expand=True)
                _paste(img, lie, dx - 6, feet - lie.height + 1)
            else:
                lunge = max(0.0, 1 - (dx - x) / 20) * 6
                _paste(img, dsp, dx - lunge, feet - sh)
        if t < 3.4:
            _paste(img, sprite(carry_frame(t), pal), x, feet - sh)
        else:
            celebrate(ez_x - 4, t)
        return

    # catch / pass / defense: the ball comes in from the left, the player leaps for it, then takes it home
    catch_t, catch_x = 2.1, w * .52
    jump0, jump1 = catch_t - 0.35, catch_t + 0.4
    if t < jump0:
        x = -12 + (catch_x + 12) * t / jump0
        _paste(img, sprite(run_frame(t), pal), x, feet - sh)
    elif t < jump1:
        k = (t - jump0) / (jump1 - jump0)
        _paste(img, sprite(CATCH if t >= catch_t else JUMP, pal), catch_x, feet - sh - math.sin(k * math.pi) * h * .2)
    elif t < 3.9:
        x = catch_x + (ez_x - catch_x) * (t - jump1) / (3.9 - jump1)
        _paste(img, sprite(carry_frame(t), pal), x, feet - sh)
    else:
        celebrate(ez_x - 4, t)
    if 0.5 <= t < catch_t:                           # the throw: a spiral dropping in from off screen
        k = (t - 0.5) / (catch_t - 0.5)
        bx, by = _arc((-4, h * .55), (catch_x + 9, feet - sh - h * .2 - 1), k, h * .25)
        draw_ball(img, bx, by, int(t * 12))
    if catch_t <= t < catch_t + 0.25:                # "pop" on the catch
        d = ImageDraw.Draw(img)
        cx, cy = catch_x + 10, feet - sh - h * .2
        for a in range(0, 360, 45):
            r = 3 + (t - catch_t) * 20
            d.point((cx + math.cos(math.radians(a)) * r, cy + math.sin(math.radians(a)) * r), fill=(255, 255, 255))


def bull_run(img: Image.Image, t: float) -> None:
    """The Texans' bull charging across the field, kicking up dust."""
    w, h = img.size
    feet = int(h * .92)
    x = -22 + (w + 30) * (t / 3.0)
    frame = BULL_A if int(t * 8) % 2 else BULL_B
    bh = len(BULL_A)
    _paste(img, sprite(frame, BULL_PAL), x, feet - bh)
    d = ImageDraw.Draw(img)
    rnd = random.Random(int(t * 10))
    for i in range(6):                               # dust puffs behind it
        px = x - 3 - i * 3 - rnd.random() * 2
        py = feet - 1 - rnd.random() * 3 - i * .3
        d.point((px, py), fill=_shade((170, 140, 100), 1 - i / 7))


# ---------------------------------------------------------------- fireworks

def fireworks(img: Image.Image, t: float, colors: tuple, seed: int, every: float = 0.32, dim: float = 1.0,
              top: float = .1, bottom: float = .45) -> None:
    """Rockets launch every `every` s from the bottom, burst into sparks that fall and fade. t = time since start."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    big = H >= 100
    rise, life = 0.55, 1.6
    first = max(0, int((t - rise - life) / every))
    for k in range(first, int(t / every) + 1):
        r = random.Random(seed * 7919 + k)
        t0 = k * every + r.uniform(0, every * .6)
        age = t - t0
        if age < 0:
            continue
        sx = r.uniform(.08, .92) * W
        ex, ey = sx + r.uniform(-.05, .05) * W, r.uniform(top, bottom) * H
        col = colors[r.randrange(len(colors))]
        col2 = colors[r.randrange(len(colors))]
        if age < rise:                               # the rocket going up, with a short trail
            k2 = age / rise
            k2 = 1 - (1 - k2) ** 2
            x, y = sx + (ex - sx) * k2, H + (ey - H) * k2
            for j in range(4):
                d.point((x, y + j * (2 if big else 1)), fill=_shade((255, 220, 160), dim * (1 - j / 4)))
            continue
        a = age - rise
        if a > life:
            continue
        n = r.randrange(30, 48)
        speed = r.uniform(.22, .34) * H
        fade = (1 - a / life) ** 1.4 * dim
        if a < 0.08:                                 # the flash
            rr = 3 if big else 2
            d.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], fill=_shade((255, 255, 255), dim))
        g = H * .35
        for i in range(n):
            ang = 2 * math.pi * i / n + r.uniform(-.08, .08)
            v = speed * r.uniform(.7, 1.0)
            drag = (1 - math.exp(-2.2 * a)) / 2.2
            x = ex + math.cos(ang) * v * drag
            y = ey + math.sin(ang) * v * drag + .5 * g * a * a
            if 0 <= x < W and 0 <= y < H:
                c = col if i % 3 else col2
                tw = 1.0 if (i + int(a * 20)) % 5 else 0.5   # a little twinkle
                if big:
                    d.rectangle([x, y, x + 1, y + 1], fill=_shade(c, fade * tw))
                    if a > .15:                      # a short tail back toward the burst
                        d.point((x - math.cos(ang) * 2, y - math.sin(ang) * 2), fill=_shade(c, fade * .4))
                else:
                    d.point((x, y), fill=_shade(c, fade * tw))


# ---------------------------------------------------------------- headline + details

def outlined(img, x, y, s, role, scale, fill, outline=(0, 0, 0)) -> None:
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (2, 2)):
        big_text(img, x + dx, y + dy, s, role, scale, outline, anchor="c")
    big_text(img, x, y, s, role, scale, fill, anchor="c")


def headline_words(play: dict, th: dict) -> tuple[str, str]:
    """(big word, line under it)."""
    stars = play.get("players") or []
    td = play.get("touchdown")
    if play.get("team_score") and th["key"] != "fantasy":
        word = {"TOUCHDOWN": "TOUCHDOWN!", "SAFETY": "SAFETY!"}.get(play.get("headline"), "TOUCHDOWN!" if td else
                                                                    "FIELD GOAL!" if play.get("kind") == "kick" else "SCORE!")
        return word, (th["name"] + (f"  +{play['team_points']}" if play.get("team_points") else "")).strip()
    who = stars[0]["name"].upper() if stars else ""
    return ("TOUCHDOWN!" if td else "BIG PLAY!"), who


def _best_scale(s: str, role: str, W: int, want: int) -> int:
    for sc in range(want, 0, -1):
        if big_width(s, role, sc) <= W - 6:
            return sc
    return 1


def _fpts(v: float) -> str:
    return f"+{v:.1f} FPTS" if v else ""


def _role_of(f) -> str:
    return next(r for r in ("huge", "large", "bold", "medium", "body", "small", "tiny") if font(r) is f)


ROLE_H = {"huge": 20, "large": 18, "bold": 13, "medium": 10, "body": 7, "small": 6, "tiny": 5}


def card(img: Image.Image, rc: RC, play: dict, th: dict, t: float, boo: bool = False) -> None:
    """What happened, filling the whole display (like the breaking-trade alert): a flashing banner, a big headshot
    (or team logo) down the left, the name, the play in giant letters, the points, then the score and ESPN's
    description scrolling along the bottom. Same layout for cheers and stinkers, different colors and words."""
    W, H = rc.W, rc.main_h
    d = ImageDraw.Draw(img)
    big = H >= 96
    stars = play.get("players") or []
    star = stars[0] if stars else None
    on = int(t * 2) % 2 == 0
    tag = "body" if big else "small"

    # banner
    bar_h = 14 if big else 9
    d.rectangle([0, 0, W - 1, bar_h - 1], fill=th["banner"][0] if on else th["banner"][1])
    team_word = th["name"] if play.get("team_score") and th["key"] != "fantasy" else ""
    if boo:
        head = (f"STINKER: {play['rival_team'].upper()}" if play.get("rival_team")
                else f"UGH. {th['name'] or play.get('team') or ''} SCORED")
        right = (f"+{play.get('points') or 0:.1f} FOR THEM" if star and play.get("points") else
                 f"+{play['team_points']}" if play.get("team_points") else "")
    else:
        head = ((team_word + " ") if team_word else "") + ("TOUCHDOWN" if play.get("touchdown") else
                                                           (play.get("headline") if team_word else "BIG PLAY"))
        right = (_fpts(play.get("points") or 0) if star else
                 f"+{play['team_points']}" if play.get("team_points") else "")
    rw = text_width(right, font(tag))
    text(d, 3, 3 if big else 1, fit(head, W - rw - 12, (tag,))[0], tag, WHITE)
    text_right(d, W - 3, 3 if big else 1, right, tag, th["light"] if boo else (GOLD if on else WHITE))

    # bottom: score line + scrolling description
    desc_h = 9 if big else 6
    line_h = 11 if big else 8
    ly = H - desc_h - line_h
    d.line([(0, ly - 2), (W - 1, ly - 2)], fill=DARK)
    gl = " ".join(v for v in (f"Q{play['period']}" if play.get("period") else "", play.get("clock") or "") if v)
    text(d, 3, ly, play.get("score") or play.get("game") or "", tag, WHITE)
    text_right(d, W - 3, ly, gl, tag, GRAY)
    desc = (play.get("text") or "").upper()
    if desc:
        role = "small" if big else "tiny"
        fw = text_width(desc, font(role))
        ty = H - desc_h + (1 if big else 0)
        if fw <= W - 6:
            text(d, 3, ty, desc, role, (200, 200, 210))
        else:
            text(d, W - int(max(0.0, t - 1.0) * 34) % (fw + W), ty, desc, role, (200, 200, 210))

    # the middle: everything between the banner and the score line
    top, bottom = bar_h + 3, ly - 4
    size = max(16, min(bottom - top, W // 3))
    url = star.get("headshot") if star else \
        f"https://a.espncdn.com/i/teamlogos/nfl/500/{(play.get('team') or 'nfl').lower()}.png"
    im = rc.images.get(url, size)
    hy = top + (bottom - top - size) // 2
    if not paste_icon(img, im, 3, hy):
        team_fallback(img, 3, hy, size, play.get("team") or "?",
                      th["primary"] if (boo or not star) else hex_color(play.get("team_color")))
    if boo and t % 2.4 < 1.6:                        # a poop sits on their face now and then
        pp = _poop(2 if big else 1)
        _paste(img, pp, 3 + size - pp.width + 4, hy + size - pp.height + 3)
        flies(img, 3 + size - pp.width // 2 + 4, hy + size - pp.height, t, 2, 6 if big else 4)

    x = size + 9
    wleft = W - x - 3
    if star:
        name = star["name"].upper()
        owner = (play.get("rival_owner") or "").upper() if boo else ""
        sub = " · ".join(v for v in (star.get("position"), play.get("team"), owner,
                                     "BENCH" if not star.get("starter") and not boo else "") if v)
    else:
        name = {"texans": "HOUSTON TEXANS", "stink": {"DAL": "DALLAS COWBOYS"}.get(play.get("team") or "", "")}.get(
            th["key"], "") or (play.get("team") or "").upper()
        sub = play.get("game") or ""
    hl = play.get("headline") or ""
    extra = ""
    if star and play.get("points"):
        extra = f"+{play['points']:.1f} FOR THEM" if boo else _fpts(play["points"])
    elif play.get("team_points"):
        extra = f"+{play['team_points']} POINTS"
    also = [f"+ {s2['name'].upper()} {_fpts(s2['points'])}" for s2 in stars[1:2]]

    # biggest fonts that fit the column, stepping down until the whole stack fits the height
    name_roles = ("large", "bold", "medium", "body", "small") if big else ("bold", "body", "small", "tiny")
    hl_roles = ("huge", "large", "bold", "medium", "body") if big else ("large", "bold", "body", "small")
    pts_roles = ("large", "bold", "medium", "body") if big else ("body", "small", "tiny")
    for step in range(4):
        nm, nf = fit(name, wleft, name_roles[min(step, len(name_roles) - 1):])
        hh, hf = fit(hl, wleft, hl_roles[min(step, len(hl_roles) - 1):])
        ex, ef = fit(extra, wleft, pts_roles[min(step, len(pts_roles) - 1):]) if extra else ("", None)
        rows = [(nm, _role_of(nf), 3), (sub, "small" if big else "tiny", 4), (hh, _role_of(hf), 4)]
        if ex:
            rows.append((ex, _role_of(ef), 3))
        rows += [(a, "small" if big else "tiny", 2) for a in also]
        need = sum(ROLE_H[r] + gap for _, r, gap in rows if _)
        if need <= bottom - top:
            break
    while need > bottom - top and (also or sub):      # still too tall: drop the extras, least important first
        if also:
            also = also[:-1]
            rows = rows[:-1]
        else:
            sub = ""
            rows[1] = ("", rows[1][1], rows[1][2])
        need = sum(ROLE_H[r] + gap for _, r, gap in rows if _)
    y = top + max(0, (bottom - top - need) // 2)
    name_col = (190, 160, 120) if boo else (th["accent"] if th["key"] == "texans" and not star else WHITE)
    hl_col = th["accent"] if boo else (GOLD if th["key"] == "fantasy" else th["light"])
    ex_col = th["light"] if boo else GOLD
    colors = [name_col, GRAY, hl_col, ex_col] + [GOLD] * len(also)
    if not ex:
        colors.pop(3)
    for (s_, role, gap), col in zip(rows, colors):
        if not s_:
            continue
        if role == "huge":
            big_text(img, x, y, s_, "huge", 1, col)
        else:
            text(d, x, y, s_, role, col)
        y += ROLE_H[role] + gap


def details(img: Image.Image, rc: RC, play: dict, th: dict, t: float) -> None:
    card(img, rc, play, th, t)


def frame(img: Image.Image, rc: RC, play: dict, t: float) -> None:
    W, H = rc.W, rc.main_h
    th = theme_for(play)
    s = 2 if H >= 96 else 1
    seed = zlib.crc32(str(play.get("id")).encode()) % 10007
    if t < HEADLINE_END:
        w, h = W // s, H // s
        scene = stadium(w, h, th["key"], tuple(th["crowd"]), tuple(th["endzone"])).copy()
        st = min(t, SCENE_END + 3.0)
        hype = 0.0 if t < 2.1 else min(1.0, (t - 2.1) * 2)
        crowd_flicker(scene, w, h, t, tuple(th["crowd"]), hype)
        play_scene(scene, st, play, th)
        if th.get("bull") and t >= SCENE_END - 0.2:
            bull_run(scene, t - (SCENE_END - 0.2))
        if t >= FIREWORKS_START:                     # house lights down for the show
            k = min(1.0, (t - FIREWORKS_START) / 0.5)
            scene = scene.point(lambda v: int(v * (1 - .5 * k)))
        img.paste(scene.resize((w * s, h * s), Image.NEAREST) if s > 1 else scene, (0, 0))
        if t >= FIREWORKS_START:
            fireworks(img, t - FIREWORKS_START, th["sparks"], seed)
        if t >= SCENE_END:
            word, sub = headline_words(play, th)
            k = t - SCENE_END
            sc = _best_scale(word, "huge", W, 2 if s > 1 else 1)
            pop = k < 0.15                           # slams in one size bigger for a moment
            if pop and big_width(word, "huge", sc + 1) <= W + 40:
                sc += 1
            flash = int(k * 6) % 2 == 0
            col = (th["light"] if flash else th["accent"]) if th["key"] != "fantasy" else (GOLD if flash else WHITE)
            yh = int(H * .2)
            outlined(img, W // 2, yh, word, "huge", sc, col)
            if sub:
                f_role = "large" if s > 1 and big_width(sub, "large", 1) <= W - 8 else "body"
                outlined(img, W // 2, yh + 20 * sc + 4, sub, f_role, 1,
                         th["accent"] if th["key"] == "texans" else WHITE)
        return
    k = t - HEADLINE_END
    fireworks(img, t - FIREWORKS_START, th["sparks"], seed, every=0.9, dim=0.45, top=.05, bottom=.3)
    details(img, rc, play, th, k)


# ---------------------------------------------------------------- the stinker (rivals)
# A rival's player makes a big play, or a rival NFL team scores: gloomy stadium, the play, then the player lets one
# rip (a green cloud, "PFFRRT!"), a poop drops in with flies buzzing around it, and it rains poop over the details.

STINK = {
    "key": "stink", "primary": (130, 80, 34), "accent": (120, 210, 50), "light": (190, 230, 120),
    "crowd": ((90, 90, 96), (70, 70, 80), (110, 100, 80), (60, 70, 60)),
    "endzone": (50, 40, 26), "banner": ((110, 66, 24), (60, 92, 22)), "name": "", "bull": False,
}
# B body, D darker swirl line, W eye white, K pupil, M mouth
POOP = (
    "......BB......",
    ".....BBBD.....",
    "....DBBBBB....",
    "...BBBBBBBD...",
    "..DDBBBBBBBB..",
    "..BWWBBBBWWB..",
    ".BBWKBBBBKWBB.",
    ".DBBBBBBBBBBD.",
    "BBBMBBBBBBMBBB",
    "BBBBMMMMMMBBBB",
    "DBBBBBBBBBBBBD",
    ".BBBBBBBBBBBB.",
)
POOP_PAL = (("B", (140, 82, 30)), ("D", (92, 52, 18)), ("W", (255, 255, 255)), ("K", (20, 20, 20)),
            ("M", (255, 255, 255)))
MINI_POOP = (
    "..B..",
    ".BBD.",
    "DBBBB",
    "BBBBB",
)


def stink_theme(play: dict) -> dict:
    name = (play.get("rival_team") or "").upper() if play.get("rival_team") else (play.get("team_score") or "")
    if play.get("team_score") == "DAL" and not play.get("rival_team"):
        name = "COWBOYS"
    return {**STINK, "name": name}


@lru_cache(maxsize=64)
def _poop(scale: int) -> Image.Image:
    im = sprite(POOP, POOP_PAL)
    return im if scale == 1 else im.resize((im.width * scale, im.height * scale), Image.NEAREST)


def fart_cloud(img: Image.Image, cx: float, cy: float, age: float, size: float) -> None:
    """A green cloud puffing out and drifting up, with wavy stink lines rising off it."""
    if age < 0:
        return
    d = ImageDraw.Draw(img)
    grow = min(1.0, age / 0.6)
    fade = max(0.0, 1 - max(0.0, age - 1.6) / 1.2)
    if fade <= 0:
        return
    rnd = random.Random(11)
    for i in range(9):                               # overlapping puffs, darker at the core
        ox, oy = rnd.uniform(-1, 1) * size * .9, rnd.uniform(-.6, .4) * size
        r = size * (.35 + .3 * rnd.random()) * grow
        x, y = cx + size * .7 * grow + ox * grow, cy + oy * grow - age * 3
        col = _shade(((80, 150, 30), (110, 190, 50), (150, 210, 80))[i % 3], fade)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    for j in range(3):                               # stink lines
        bx = cx + j * size * .6
        for k in range(8):
            yy = cy - size * .9 - k * 2 - age * 4
            xx = bx + math.sin(k * .9 + age * 8 + j) * 1.5
            d.point((xx, yy), fill=_shade((150, 220, 90), fade * (1 - k / 9)))


def flies(img: Image.Image, cx: float, cy: float, t: float, n: int = 3, r: float = 10) -> None:
    d = ImageDraw.Draw(img)
    for i in range(n):
        a = t * (5 + i) + i * 2.1
        x = cx + math.cos(a) * r * (1 + .3 * i)
        y = cy + math.sin(a * 1.3) * r * .5
        d.point((x, y), fill=(40, 40, 40) if img.getpixel((int(x) % img.width, int(y) % img.height)) != (0, 0, 0)
                else (110, 110, 110))
        if int(t * 20 + i) % 2:
            d.point((x - 1, y - 1), fill=(200, 200, 220))
            d.point((x + 1, y - 1), fill=(200, 200, 220))


def poop_rain(img: Image.Image, t: float, seed: int, dim: float = 1.0, every: float = 0.18) -> None:
    W, H = img.size
    s = 2 if H >= 96 else 1
    mini = sprite(MINI_POOP, (("B", _shade((140, 82, 30), dim)), ("D", _shade((92, 52, 18), dim))))
    if s > 1:
        mini = mini.resize((mini.width * 2, mini.height * 2), Image.NEAREST)
    fall = 1.8
    first = max(0, int((t - fall) / every))
    for k in range(first, int(t / every) + 1):
        r = random.Random(seed * 31 + k)
        age = t - (k * every + r.uniform(0, every))
        if not 0 <= age <= fall:
            continue
        x = r.uniform(0, W - 8)
        y = -10 + (H + 20) * (age / fall) ** 1.3
        _paste(img, mini, x + math.sin(age * 4 + k) * 2, y)


def stink_headline(play: dict, th: dict) -> tuple[str, str]:
    if play.get("rival_team"):
        owner = (play.get("rival_owner") or "").split(" ")[0].upper()
        return "BOOOO!", (f"{owner}'S GUY" if owner else th["name"]) + f"  {play.get('headline') or ''}".rstrip()
    word = "STINKS!" if not play.get("touchdown") else "BOOOO!"
    return word, f"{th['name'] or play.get('team') or ''} {play.get('headline') or 'SCORED'}".strip()


def stink_details(img: Image.Image, rc: RC, play: dict, th: dict, t: float) -> None:
    card(img, rc, play, th, t, boo=True)


def stink_frame(img: Image.Image, rc: RC, play: dict, t: float) -> None:
    W, H = rc.W, rc.main_h
    th = stink_theme(play)
    s = 2 if H >= 96 else 1
    seed = zlib.crc32(str(play.get("id")).encode()) % 10007
    if t >= HEADLINE_END:
        poop_rain(img, t, seed, dim=.35, every=.5)
        stink_details(img, rc, play, th, t - HEADLINE_END)
        return
    w, h = W // s, H // s
    scene = stadium(w, h, "stink", tuple(th["crowd"]), tuple(th["endzone"])).copy()
    scene = scene.point(lambda v: int(v * .7))       # the lights dim: nobody here is happy
    crowd_flicker(scene, w, h, t, tuple(th["crowd"]), 0.0)
    feet = int(h * .9)
    play_t = min(t, 3.2)
    jersey = readable_on_black(hex_color(play.get("team_color"), (120, 120, 130)), 80)
    helmet = readable_on_black(hex_color(play.get("team_alt"), (180, 180, 190)), 80)
    rth = {**th, "key": "rival", "primary": jersey, "accent": helmet}
    if t < 3.2:
        play_scene(scene, play_t * (4.0 / 3.2), play, rth)  # the play, a bit quicker
    else:                                            # stops, turns, lets one rip
        px = w * .5
        bent = sprite(KICK if int(t * 4) % 2 else RUN_B, player_palette(jersey, helmet), flip=True)
        fart_cloud(scene, px + bent.width - 2, feet - bent.height * .45, t - 3.3, h * .14)
        _paste(scene, bent, px, feet - bent.height)
        if 3.4 <= t < 5.4:
            k = t - 3.4
            word = "PFFRRT!"
            col = (150, 230, 80) if int(k * 8) % 2 else (90, 170, 40)
            text_center(ImageDraw.Draw(scene), int(px + bent.width + h * .2), max(1, int(feet - h * .55 - k * 3)),
                        word, "body" if w >= 100 else "small", col)
    img.paste(scene.resize((w * s, h * s), Image.NEAREST) if s > 1 else scene, (0, 0))
    if t >= SCENE_END:                               # the poop drops in and squishes
        k = t - SCENE_END
        scale = 4 if s > 1 else 2
        while scale > 1 and len(POOP) * scale > H * .5:
            scale -= 1
        p = _poop(scale)
        land = H * .92 - p.height
        y = -p.height + (land + p.height) * min(1.0, k / 0.45) ** 2
        squish = k >= 0.45 and k < 0.6
        if squish:
            p = p.resize((int(p.width * 1.15), int(p.height * .85)), Image.NEAREST)
            y = H * .92 - p.height
        cx = W * .5
        _paste(img, p, cx - p.width / 2, y)
        if k > 0.45:
            flies(img, cx, y + p.height * .2, k, 4, p.width * .55)
            poop_rain(img, k - 0.45, seed)
            word, sub = stink_headline(play, th)
            sc = _best_scale(word, "huge", W, 2 if s > 1 else 1)
            outlined(img, W // 2, int(H * .06), word, "huge", sc, (150, 230, 80) if int(k * 5) % 2 else (190, 120, 50))
            if sub:
                f_role = "large" if s > 1 and big_width(sub, "large", 1) <= W - 8 else "body"
                if big_width(sub, f_role, 1) > W - 4:
                    f_role = "small"
                outlined(img, W // 2, int(H * .06) + 20 * sc + 3, sub, f_role, 1, WHITE)


class BigPlayScreen(Screen):
    """Takeover shown by the engine (never in a rotation). The engine sets `play` before switching to it."""
    name, label = "big_play", "Big play celebration (takeover)"
    hidden = True
    duration = 20.0

    def __init__(self):
        self.play: dict | None = None

    def available(self, rc):
        return self.play is not None

    def animating(self, rc, t):
        return True

    def render(self, img, rc, t):
        if not self.play:
            return
        if self.play.get("mood") == "boo":
            stink_frame(img, rc, self.play, t)
        else:
            frame(img, rc, self.play, t)


# ---------------------------------------------------------------- examples for the dashboard's test buttons

def sample_play(which: str, rc: RC) -> dict:
    """A made-up play of each kind (fantasy | team | rival | rival_team), using real names from the league."""
    from datetime import datetime, timezone
    lg = rc.data("fantasy") or {}
    now = datetime.now(timezone.utc).isoformat()
    base = {"id": "sample", "when": now, "period": 2, "clock": "4:28", "players": [], "points": 0.0,
            "team_score": None, "team_points": 0, "mood": "cheer"}

    def star(p, role, pts):
        pid = int(p.get("player_id") or 0)
        url = (f"https://a.espncdn.com/i/teamlogos/nfl/500/{p.get('nfl_team', '').lower()}.png" if pid < 0 else
               f"https://a.espncdn.com/i/headshots/nfl/players/full/{pid}.png")
        return {"player_id": pid, "name": p["name"], "position": p.get("position", ""), "slot": p.get("slot", ""),
                "role": role, "points": pts, "starter": True, "headshot": url}

    def pick(roster):
        skill = [p for p in roster if p.get("starter") and p.get("position") in ("WR", "TE", "RB")]
        return (skill or [p for p in roster if p.get("position") not in ("D/ST", "K")] or [None])[0]

    if which in ("fantasy", "rival"):
        roster = lg.get("my_roster" if which == "fantasy" else "rival_roster") or []
        p = pick(roster) or {"name": "Nico Collins", "position": "WR", "nfl_team": "HOU", "player_id": 4258173}
        team = p.get("nfl_team") or "HOU"
        play = {**base, "kind": "catch", "headline": "42 YD TD CATCH", "yards": 42, "touchdown": True,
                "points": 11.2, "team": team, "team_color": None, "team_alt": None,
                "text": f"(Shotgun) Pass deep right to {p['name']} for 42 yards, TOUCHDOWN.",
                "score": f"{team} 21  OPP 10", "game": f"{team} @ OPP", "players": [star(p, "receiver", 11.2)]}
        if which == "rival":
            t = next((x for x in lg.get("teams") or [] if x.get("id") == p.get("fantasy_team_id")), {})
            play.update(mood="boo", rival_team=t.get("name") or "Rival team", rival_owner=t.get("owner") or "")
        return play
    if which == "rival_team":
        return {**base, "kind": "run", "headline": "TOUCHDOWN", "yards": 4, "touchdown": True, "team": "DAL",
                "team_color": "002a5c", "team_alt": "b0b7bc", "text": "Up the middle for 4 yards, TOUCHDOWN.",
                "score": "DAL 14  OPP 3", "game": "DAL @ OPP", "team_score": "DAL", "team_points": 7, "mood": "boo"}
    return {**base, "kind": "catch", "headline": "TOUCHDOWN", "yards": 35, "touchdown": True, "team": "HOU",
            "team_color": "021018", "team_alt": "eb0028",
            "text": "(Shotgun) C.Stroud pass deep left to N.Collins for 35 yards, TOUCHDOWN.",
            "score": "HOU 14  OPP 7", "game": "HOU @ OPP", "team_score": "HOU", "team_points": 7}
