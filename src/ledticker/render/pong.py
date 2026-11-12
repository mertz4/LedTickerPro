"""Pong on the whole display, played from phones: drag on the dashboard's pad to move your paddle.

The first person to join plays the computer; a second person takes the computer's side and the match restarts.
First to WIN_SCORE. The game ends by itself when everyone leaves or nobody has touched a paddle for a while.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw

from .draw import big_text, text, text_center, text_right

WIN_SCORE = 7
PAD_W, PAD_H, PAD_X = 3, 24, 5      # paddle size, and its gap from the side edge
BALL = 3
SPEED0, SPEED_MAX, SPEED_UP = 120.0, 250.0, 1.06      # px/s; each hit is a little faster
MAX_ANGLE = math.radians(55)
CPU_SPEED = 92.0
COUNTDOWN, POINT_PAUSE, OVER_SECONDS, IDLE_SECONDS = 3.0, 1.3, 7.0, 60.0
COLORS = ((0, 220, 255), (255, 60, 200))      # left, right
NET = (34, 34, 50)
GOLD = (255, 200, 60)


def _shade(c, k: float) -> tuple[int, int, int]:
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


class Pong:
    def __init__(self, size: tuple[int, int] = (256, 128), seed: int | None = None):
        self.W, self.H = size
        self.rnd = random.Random(seed)
        self.state = "off"                # off | countdown | play | point | over
        self.players: list[dict | None] = [None, None]     # {"id", "name"}; None = the computer
        self.version = 0                  # bumps whenever info() changes, so phones know to refresh
        self._reset()

    # ------------------------------------------------------------- lobby

    def _reset(self) -> None:
        self.score = [0, 0]
        self.pad = [self.H / 2, self.H / 2]
        self.hit_flash = [0.0, 0.0]
        self.timer = COUNTDOWN
        self.idle = 0.0
        self.serve_to = self.rnd.choice((0, 1))
        self.last_scorer: int | None = None
        self.sparks: list[list] = []
        self.shake = 0.0
        self.trail = Image.new("RGB", (self.W, self.H))
        self.cpu_aim = 0.0
        self._t: float | None = None
        self._center_ball()

    def _center_ball(self) -> None:
        self.bx, self.by = self.W / 2, self.H / 2
        self.speed = SPEED0
        a = self.rnd.uniform(-0.45, 0.45)
        self.vx = math.cos(a) * self.speed * (-1 if self.serve_to == 0 else 1)
        self.vy = math.sin(a) * self.speed
        self.ball_color = (255, 255, 255)

    @property
    def active(self) -> bool:
        return self.state != "off"

    def side_of(self, pid) -> int | None:
        for i, p in enumerate(self.players):
            if p and p["id"] == pid:
                return i
        return None

    def join(self, pid, name: str) -> int:
        """Sit down at the table. Returns the side (0 left, 1 right). Raises ValueError if both seats are taken.
        Joining again once a match is over starts a rematch."""
        side = self.side_of(pid)
        if side is None:
            if self.state == "off":
                self.players = [None, None]
                side = 0
            elif None in self.players:
                side = self.players.index(None)
            else:
                raise ValueError("two people are already playing")
            self.players[side] = {"id": pid, "name": " ".join(name.split())[:10].upper() or "PLAYER"}
        elif self.state != "over":
            return side
        self._reset()
        self.state = "countdown"
        self.version += 1
        return side

    def leave(self, pid) -> None:
        side = self.side_of(pid)
        if side is None:
            return
        self.players[side] = None
        if not any(self.players):
            self.stop()
        self.version += 1

    def stop(self) -> None:
        if self.state != "off":
            self.state = "off"
            self.players = [None, None]
            self.version += 1

    def move(self, pid, y: float) -> None:
        """Put a player's paddle at y (0 = top of the display, 1 = bottom)."""
        side = self.side_of(pid)
        if side is None or y != y:
            return
        self.pad[side] = min(self.H - PAD_H / 2, max(PAD_H / 2, y * self.H))
        self.idle = 0.0

    def name(self, side: int) -> str:
        p = self.players[side]
        return p["name"] if p else "CPU"

    def info(self) -> dict:
        return {"state": self.state, "score": list(self.score), "names": [self.name(0), self.name(1)],
                "cpu": [p is None for p in self.players], "win": WIN_SCORE,
                "winner": (0 if self.score[0] > self.score[1] else 1) if self.state == "over" else None}

    # ------------------------------------------------------------- simulation

    def _burst(self, x: float, y: float, color, n: int, speed: float) -> None:
        for _ in range(n):
            a = self.rnd.uniform(0, math.tau)
            v = self.rnd.uniform(0.25, 1.0) * speed
            self.sparks.append([x, y, math.cos(a) * v, math.sin(a) * v, self.rnd.uniform(0.3, 0.8), color])
        del self.sparks[:-300]

    def _cpu(self, side: int, dt: float) -> None:
        coming = (self.vx < 0) == (side == 0) and self.state == "play"
        target = self.by + self.cpu_aim if coming else self.H / 2
        step = CPU_SPEED * dt
        d = target - self.pad[side]
        self.pad[side] += max(-step, min(step, d))
        self.pad[side] = min(self.H - PAD_H / 2, max(PAD_H / 2, self.pad[side]))

    def _bounce(self, side: int) -> None:
        rel = max(-1.0, min(1.0, (self.by - self.pad[side]) / (PAD_H / 2)))
        self.speed = min(SPEED_MAX, self.speed * SPEED_UP)
        a = rel * MAX_ANGLE
        self.vx = math.cos(a) * self.speed * (1 if side == 0 else -1)
        self.vy = math.sin(a) * self.speed
        self.hit_flash[side] = 0.12
        self.ball_color = COLORS[side]
        self.cpu_aim = self.rnd.uniform(-10, 10)
        self._burst(self.bx, self.by, COLORS[side], 10, 55)

    def _goal(self, scorer: int) -> None:
        self.score[scorer] += 1
        self.last_scorer = scorer
        self.serve_to = 1 - scorer            # the side that was scored on receives the next serve
        self.shake = 0.45
        self._burst(self.bx, min(self.H - 2.0, max(2.0, self.by)), COLORS[scorer], 60, 120)
        self.state, self.timer = "point", POINT_PAUSE
        self.version += 1

    def _move_ball(self, dt: float) -> None:
        r = BALL / 2
        self.bx += self.vx * dt
        self.by += self.vy * dt
        if self.by < r:
            self.by, self.vy = 2 * r - self.by, abs(self.vy)
        elif self.by > self.H - r:
            self.by, self.vy = 2 * (self.H - r) - self.by, -abs(self.vy)
        face_l, face_r = PAD_X + PAD_W, self.W - PAD_X - PAD_W
        reach = PAD_H / 2 + r + 1
        if self.vx < 0 and face_l - PAD_W - 2 <= self.bx - r <= face_l and abs(self.by - self.pad[0]) <= reach:
            self.bx = face_l + r
            self._bounce(0)
        elif self.vx > 0 and face_r <= self.bx + r <= face_r + PAD_W + 2 and abs(self.by - self.pad[1]) <= reach:
            self.bx = face_r - r
            self._bounce(1)
        elif self.bx < -BALL:
            self._goal(1)
        elif self.bx > self.W + BALL:
            self._goal(0)

    def step(self, dt: float) -> None:
        if self.state == "off":
            return
        for i in (0, 1):
            self.hit_flash[i] = max(0.0, self.hit_flash[i] - dt)
            if self.players[i] is None:
                self._cpu(i, dt)
        self.shake = max(0.0, self.shake - dt)
        for s in self.sparks:
            s[4] -= dt
            s[0] += s[2] * dt
            s[1] += s[3] * dt
        self.sparks = [s for s in self.sparks if s[4] > 0]
        if self.state == "play":
            self.idle += dt
            if self.idle > IDLE_SECONDS:
                self.stop()
                return
            left = dt
            while left > 1e-6 and self.state == "play":    # small steps, so a fast ball can't skip through a paddle
                h = min(left, 1 / 240)
                self._move_ball(h)
                left -= h
            return
        self.timer -= dt
        if self.timer > 0:
            return
        if self.state == "countdown":
            self.state = "play"
        elif self.state == "point":
            if max(self.score) >= WIN_SCORE:
                self.state, self.timer = "over", OVER_SECONDS
            else:
                self._center_ball()
                self.state = "play"
        elif self.state == "over":
            self.stop()
            return
        self.version += 1

    # ------------------------------------------------------------- drawing

    def frame(self, t: float) -> Image.Image:
        """Advance the game to time t and draw it."""
        dt = 0.0 if self._t is None else min(0.1, max(0.0, t - self._t))
        self._t = t
        self.step(dt)
        W, H = self.W, self.H
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img)
        for y in range(2, H, 8):                               # the net
            d.line([(W // 2, y), (W // 2, y + 3)], fill=NET)
        d.line([(0, 0), (W - 1, 0)], fill=NET)
        d.line([(0, H - 1), (W - 1, H - 1)], fill=NET)
        pulse = self.timer / POINT_PAUSE if self.state == "point" else 0.0
        for i in (0, 1):                                       # scores sit dim behind the play
            k = 0.3 + (0.7 * pulse if self.last_scorer == i and pulse else 0)
            big_text(img, W // 2 + (-44 if i == 0 else 44), 14, str(self.score[i]), "huge", 2, _shade(COLORS[i], k), "c")
        text(d, 12, 3, self.name(0), "tiny", _shade(COLORS[0], 0.7))
        text_right(d, W - 12, 3, self.name(1), "tiny", _shade(COLORS[1], 0.7))

        if dt:                                                 # the ball's comet trail
            k = 0.5 ** (dt / 0.09)
            self.trail = self.trail.point([int(v * k) for v in range(256)] * 3)
        if self.state == "play":
            bx, by = int(self.bx - BALL / 2), int(self.by - BALL / 2)
            ImageDraw.Draw(self.trail).rectangle([bx, by, bx + BALL - 1, by + BALL - 1], fill=_shade(self.ball_color, 0.8))
        img = ImageChops.add(img, self.trail)
        d = ImageDraw.Draw(img)

        for i in (0, 1):
            x = PAD_X if i == 0 else W - PAD_X - PAD_W
            y = int(self.pad[i] - PAD_H / 2)
            col = (255, 255, 255) if self.hit_flash[i] > 0 else COLORS[i]
            d.rectangle([x, y, x + PAD_W - 1, y + PAD_H - 1], fill=col)
        if self.state == "play":
            bx, by = int(self.bx - BALL / 2), int(self.by - BALL / 2)
            d.rectangle([bx, by, bx + BALL - 1, by + BALL - 1], fill=(255, 255, 255))
        put = img.load()
        for x, y, _, _, life, c in self.sparks:
            if 0 <= x < W and 0 <= y < H:
                put[int(x), int(y)] = _shade(c, min(1.0, life * 2.5))

        if self.state == "countdown":
            n = max(1, math.ceil(self.timer))
            frac = self.timer - math.floor(self.timer)         # 1 -> 0 across each number
            big_text(img, W // 2, H // 2 - 22, str(n), "huge", 2, _shade(GOLD, 0.45 + 0.55 * frac), "c")
            text_center(d, W // 2, H - 20, f"FIRST TO {WIN_SCORE}", "body", (150, 150, 160))
        elif self.state == "over":
            w = 0 if self.score[0] > self.score[1] else 1
            if self.rnd.random() < 0.25:
                self._burst(self.rnd.uniform(20, W - 20), self.rnd.uniform(15, H - 40), COLORS[w], 26, 70)
            blink = int(self.timer * 3) % 2 == 0
            big_text(img, W // 2, H // 2 - 4, self.name(w), "bold8", 2, COLORS[w] if blink else (255, 255, 255), "c")
            text_center(d, W // 2, H // 2 + 26, "WINS!", "bold", GOLD)
        if self.shake > 0:                                     # a goal rattles the whole table
            amp = int(round(self.shake / 0.45 * 3))
            if amp:
                img = ImageChops.offset(img, self.rnd.randint(-amp, amp), self.rnd.randint(-amp, amp))
        return img
