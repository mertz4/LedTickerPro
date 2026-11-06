"""Physical display geometry: identical modules arranged in a grid.

Default: four 128x64 modules, 2 across x 2 high = 256x128. Change [display] in config.toml if the
delivered modules differ; every layout is computed from these numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Context


@dataclass(frozen=True)
class Geometry:
    module_w: int = 128
    module_h: int = 64
    cols: int = 2
    rows: int = 2

    @property
    def width(self) -> int:
        return self.module_w * self.cols

    @property
    def height(self) -> int:
        return self.module_h * self.rows

    @property
    def seams_x(self) -> list[int]:
        return [self.module_w * i for i in range(1, self.cols)]

    @property
    def seams_y(self) -> list[int]:
        return [self.module_h * i for i in range(1, self.rows)]

    def as_dict(self) -> dict:
        return {"module_w": self.module_w, "module_h": self.module_h, "cols": self.cols, "rows": self.rows,
                "width": self.width, "height": self.height, "seams_x": self.seams_x, "seams_y": self.seams_y}


def geometry(ctx: Context) -> Geometry:
    d = ctx.section("display")
    return Geometry(module_w=int(d.get("module_width", 128)), module_h=int(d.get("module_height", 64)),
                    cols=int(d.get("modules_across", 2)), rows=int(d.get("modules_down", 2)))
