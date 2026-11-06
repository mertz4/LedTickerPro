"""Custom layouts: split the display into rectangles any way except diagonally.

A layout is a binary split tree (every non-diagonal arrangement can be built by repeated splits):

    {"split": "rows", "at": 64,            # cut horizontally 64 px from the top
     "a": {"widget": "nfl_game"},          # top
     "b": {"split": "cols", "at": 128,     # bottom, cut vertically 128 px from the left
           "a": {"widget": "weather"},
           "b": {"widget": "markets"}}}

"rows" stacks a over b (a = top), "cols" puts a left of b. `at` is pixels into the region (clamped so both sides
keep at least MIN_PX). A leaf is {"widget": name}; "" or missing = empty.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_PX = 8
MAX_DEPTH = 8


@dataclass(frozen=True)
class Region:
    widget: str
    x: int
    y: int
    w: int
    h: int


class LayoutError(ValueError):
    pass


def regions(node: dict | None, x: int, y: int, w: int, h: int, depth: int = 0) -> list[Region]:
    if not node or depth > MAX_DEPTH:
        return [Region("", x, y, w, h)]
    if "split" not in node:
        return [Region(node.get("widget") or "", x, y, w, h)]
    total = h if node["split"] == "rows" else w
    at = int(node.get("at", total // 2))
    if total < 2 * MIN_PX:
        return [Region("", x, y, w, h)]
    at = max(MIN_PX, min(total - MIN_PX, at))
    if node["split"] == "rows":
        return regions(node.get("a"), x, y, w, at, depth + 1) + regions(node.get("b"), x, y + at, w, h - at, depth + 1)
    return regions(node.get("a"), x, y, at, h, depth + 1) + regions(node.get("b"), x + at, y, w - at, h, depth + 1)


def validate_tree(node: Any, widgets: set[str], where: str = "layout", depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise LayoutError(f"{where}: too many nested splits (max {MAX_DEPTH})")
    if not isinstance(node, dict):
        raise LayoutError(f"{where}: each section must be an object")
    if "split" in node:
        if node["split"] not in ("rows", "cols"):
            raise LayoutError(f"{where}: split must be 'rows' or 'cols'")
        if not isinstance(node.get("at"), (int, float)) or node["at"] < MIN_PX:
            raise LayoutError(f"{where}: split position 'at' must be a number of pixels >= {MIN_PX}")
        validate_tree(node.get("a"), widgets, where, depth + 1)
        validate_tree(node.get("b"), widgets, where, depth + 1)
        return
    w = node.get("widget") or ""
    if w and w not in widgets:
        raise LayoutError(f"{where}: unknown widget '{w}'")


def quad_tree(tiles: list[str], cols: int, rows: int, mw: int, mh: int) -> dict:
    """The old 4-up slide as a split tree (one widget per module)."""
    def row(r):
        cells = [{"widget": tiles[r * cols + c] if r * cols + c < len(tiles) else ""} for c in range(cols)]
        node = cells[-1]
        for c in range(cols - 2, -1, -1):
            node = {"split": "cols", "at": mw, "a": cells[c], "b": node}
        return node
    node = row(rows - 1)
    for r in range(rows - 2, -1, -1):
        node = {"split": "rows", "at": mh, "a": row(r), "b": node}
    return node


def tree_widgets(node: dict | None) -> list[str]:
    if not node:
        return []
    if "split" in node:
        return tree_widgets(node.get("a")) + tree_widgets(node.get("b"))
    return [node.get("widget") or ""]
