"""Phone-as-touchpad: fingers on the dashboard's pad leave glowing trails and sparks on top of whatever is showing.

Touches arrive over the WebSocket between frames; each one is drawn straight into a glow buffer that fades by
itself, so a fast swipe stays one continuous stroke however many frames it spans.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw

COLORS = ((0, 220, 255), (255, 60, 200), (120, 255, 60), (255, 170, 0), (150, 110, 255), (255, 70, 70))
HALF_LIFE = 0.35        # seconds for a trail to fade to half brightness
GRAVITY = 70.0          # px/s^2 on sparks
MAX_SPARKS = 260
IDLE_AFTER = 2.5        # seconds without input before the layer switches itself off


class TouchLayer:
    def __init__(self, size: tuple[int, int]):
        self.size = size
        self.buf = Image.new("RGB", size)
        self.fingers: dict[tuple, dict] = {}     # (connection, pointer) -> {x, y, color}
        self.sparks: list[list] = []             # [x, y, vx, vy, life, color]
        self._rnd = random.Random()
        self._t: float | None = None
        self._fresh = False
        self._last_input = -1e9

    @property
    def active(self) -> bool:
        return bool(self.fingers or self.sparks or self._fresh
                    or (self._t is not None and self._t - self._last_input < IDLE_AFTER))

    def _spark(self, x: float, y: float, color, n: int, speed: float) -> None:
        for _ in range(n):
            if len(self.sparks) >= MAX_SPARKS:
                self.sparks.pop(0)
            a = self._rnd.uniform(0, math.tau)
            v = self._rnd.uniform(0.3, 1.0) * speed
            self.sparks.append([x, y, math.cos(a) * v, math.sin(a) * v - 12, self._rnd.uniform(0.35, 0.9), color])

    def touch(self, conn: int, pointer: int, x: float, y: float) -> None:
        """A finger went down or moved. x, y are 0..1 across the display."""
        W, H = self.size
        px, py = min(W - 1.0, max(0.0, x * W)), min(H - 1.0, max(0.0, y * H))
        key = (conn, pointer)
        f = self.fingers.get(key)
        self._fresh = True
        if f is None:
            if len(self.fingers) >= 12:
                return
            color = COLORS[(conn + pointer) % len(COLORS)]
            self.fingers[key] = {"x": px, "y": py, "color": color}
            self._spark(px, py, color, 22, 60)        # a tap pops
            return
        d = ImageDraw.Draw(self.buf)
        d.line([(f["x"], f["y"]), (px, py)], fill=f["color"], width=2)
        dist = math.hypot(px - f["x"], py - f["y"])
        self._spark(px, py, f["color"], min(6, int(dist / 5)), 28)
        f["x"], f["y"] = px, py

    def lift(self, conn: int, pointer: int) -> None:
        f = self.fingers.pop((conn, pointer), None)
        if f:
            self._spark(f["x"], f["y"], f["color"], 10, 40)
            self._fresh = True

    def drop(self, conn: int) -> None:
        """A phone disconnected: forget its fingers."""
        for key in [k for k in self.fingers if k[0] == conn]:
            del self.fingers[key]

    def draw(self, img: Image.Image, t: float) -> Image.Image:
        """The frame with the trails added on top (a new image)."""
        dt = 0.0 if self._t is None else min(0.1, max(0.0, t - self._t))
        self._t = t
        if self._fresh:
            self._fresh, self._last_input = False, t
        W, H = self.size
        if dt:
            k = 0.5 ** (dt / HALF_LIFE)
            self.buf = self.buf.point([int(v * k) for v in range(256)] * 3)
        put = self.buf.load()
        alive = []
        for s in self.sparks:
            s[4] -= dt
            if s[4] <= 0:
                continue
            s[3] += GRAVITY * dt
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            x, y = int(s[0]), int(s[1])
            if 0 <= x < W and 0 <= y < H:
                b = min(1.0, s[4] * 2.5)
                c = s[5]
                put[x, y] = (int(c[0] * b), int(c[1] * b), int(c[2] * b))
                alive.append(s)
        self.sparks = alive
        if img.size != self.size:
            return img
        out = ImageChops.add(img, self.buf)
        d = ImageDraw.Draw(out)
        for f in self.fingers.values():          # the fingertip itself: a white core in a coloured ring
            x, y = int(f["x"]), int(f["y"])
            d.ellipse([x - 3, y - 3, x + 3, y + 3], outline=f["color"])
            d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=(255, 255, 255))
        if not self.fingers and not self.sparks and t - self._last_input >= IDLE_AFTER:
            self.buf = Image.new("RGB", self.size)   # fully faded: start clean next time
        return out
