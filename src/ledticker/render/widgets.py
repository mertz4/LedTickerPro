"""Widget catalog: anything you can put in a layout section. Each widget picks its full-screen design when the
section is big enough and its compact tile design otherwise, so the same widget works in any section size."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from .draw import DIM
from .screens import RC, Screen, all_screens
from .tiles import all_tiles


@dataclass
class Widget:
    name: str
    label: str
    full: Screen | None = None
    tile: Screen | None = None
    min_w: int = 192        # smallest section that gets the full design
    min_h: int = 96
    immersive: bool = False   # shown full screen, it takes the whole display (no bottom ticker)

    def impl(self, w: int, h: int) -> Screen:
        if self.full and (self.tile is None or (w >= self.min_w and h >= self.min_h)):
            return self.full
        return self.tile or self.full

    def available(self, rc: RC) -> bool:
        return self.impl(rc.W, rc.main_h).available(rc)

    def seconds(self, rc: RC) -> float:
        return self.impl(rc.W, rc.main_h).seconds(rc)


class TickerWidget(Screen):
    """The scrolling news/markets/scores strip as a placeable section (any width, ~11 px tall)."""
    name, label = "ticker", "Scrolling ticker strip"
    adaptive = True

    def __init__(self):
        from .engine import Ticker  # local import: engine imports this module
        self.ticker = Ticker()

    def render(self, img: Image.Image, rc: RC, t: float) -> None:
        cfg = rc.ctx.section("render")
        self.ticker.speed = float(cfg.get("ticker_speed", self.ticker.speed))
        self.ticker.items = tuple(cfg.get("ticker", self.ticker.items))
        self.ticker.paste(img, rc, t, rc.W, max(0, (rc.main_h - 11) // 2))   # strip is TICKER_H (11) tall


class EmptyWidget(Screen):
    name, label = "", "(empty)"

    def available(self, rc):
        return False

    def render(self, img, rc, t):
        ImageDraw.Draw(img).rectangle([0, 0, rc.W - 1, rc.main_h - 1], outline=(14, 14, 18))


# screen name -> its compact tile twin
PAIRS = {"weather": "weather", "markets": "markets", "fantasy": "fantasy_mini", "fantasy_standings": "standings_mini",
         "fantasy_potw": "potw_mini", "props": "props_mini", "game": "my_game"}
MIN_SIZES = {"weather": (192, 100), "fantasy": (200, 110), "fantasy_standings": (200, 90), "props": (200, 60),
             "game": (200, 100), "fantasy_potw": (200, 96), "markets": (200, 80)}


def all_widgets() -> dict[str, Widget]:
    screens, tiles = all_screens(), all_tiles()
    out: dict[str, Widget] = {}
    for name, s in screens.items():
        tile = tiles.get(PAIRS.get(name, ""))
        mw, mh = MIN_SIZES.get(name, (160, 70))
        out[name] = Widget(name, s.label, full=s, tile=tile, min_w=mw, min_h=mh)
    for name, t in tiles.items():
        if name not in out:
            out[name] = Widget(name, t.label, tile=t)
    tw = TickerWidget()
    out["ticker"] = Widget("ticker", tw.label, tile=tw)
    from .christmas import ChristmasCountdown
    cd = ChristmasCountdown()
    out[cd.name] = Widget(cd.name, cd.label, tile=cd)   # one design that adapts to any section size
    from .photos import PhotosWidget
    ph = PhotosWidget()
    out[ph.name] = Widget(ph.name, ph.label, tile=ph)
    from .tally import EatOutAlert, EatOutCounter
    eo = EatOutCounter()
    out[eo.name] = Widget(eo.name, eo.label, tile=eo)   # one design that adapts to any section size
    ea = EatOutAlert()
    out[ea.name] = Widget(ea.name, ea.label, full=ea, immersive=True)
    from .bigplay import BigPlayScreen
    bp = BigPlayScreen()
    out[bp.name] = Widget(bp.name, bp.label, full=bp, immersive=True)
    from .party import all_party
    for ps in all_party():
        out[ps.name] = Widget(ps.name, ps.label, tile=ps, immersive=True)
    return out
