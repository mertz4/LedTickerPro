"""Pixel transitions for cards: one crumbles into falling sand, the next one rains into place.

Works on the lit pixels of a pre-rendered card (most of a card is black), so it is cheap enough to run at the full
frame rate in plain Python, even on a Pi.
"""
from __future__ import annotations

import random

from PIL import Image


class PixelCard:
    def __init__(self, card: Image.Image, seed: int = 0):
        self.card = card
        W, H = self.size = card.size
        rnd = random.Random(seed)
        px = card.load()
        # (x, y, color, scatter 0..1, sideways drift px/s)
        self.pts = [(x, y, px[x, y], rnd.random(), rnd.uniform(-12, 12))
                    for y in range(H) for x in range(W) if sum(px[x, y]) > 30]

    def _gravity(self, fall_time: float) -> float:
        """Strong enough that a pixel at the very top clears the bottom edge in `fall_time` seconds."""
        return 2.2 * self.size[1] / max(0.05, fall_time) ** 2

    def crumble(self, t: float, dur: float) -> Image.Image:
        """0 <= t <= dur. The bottom gives way first and the rest follows, a bit like a building coming down."""
        W, H = self.size
        out = Image.new("RGB", self.size)
        put = out.load()
        g = self._gravity(dur * 0.4)
        for x, y, c, r, vx in self.pts:
            delay = (1 - y / H) * dur * 0.35 + r * dur * 0.25
            dt = t - delay
            if dt <= 0:
                put[x, y] = c
                continue
            ny = int(y + 0.5 * g * dt * dt)
            nx = int(x + vx * dt)
            if 0 <= nx < W and ny < H:
                k = max(0.35, 1 - dt / dur)   # dims a little as it falls
                put[nx, ny] = (int(c[0] * k), int(c[1] * k), int(c[2] * k))
        return out

    def assemble(self, t: float, dur: float) -> Image.Image:
        """0 <= t <= dur. Pixels drop in from above and land in place, bottom rows first."""
        W, H = self.size
        out = Image.new("RGB", self.size)
        put = out.load()
        fall = dur * 0.45
        g = self._gravity(fall)
        for x, y, c, r, _ in self.pts:
            land = fall + (1 - y / H) * dur * 0.3 + r * dur * 0.25
            left = land - t
            if left <= 0:
                put[x, y] = c
                continue
            ny = int(y - 0.5 * g * left * left)
            if ny >= 0:
                put[x, ny] = c
        return out
