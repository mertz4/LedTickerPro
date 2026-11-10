"""LED panel output: sends the engine's frames to the real panels through the rpi-rgb-led-matrix SPWM fork.

The panels use FM6373 (S-PWM) column drivers, which upstream hzeller/rpi-rgb-led-matrix doesn't support, so the Pi
uses kingdo9/rpi-rgb-led-matrix_pwm_experiment (built into the venv by deploy/setup-pi.sh). Its Python module is
also called `rgbmatrix` and adds `panel_type` and the `spwm_*` options.

Everything is configured in config.toml under [matrix]; with `enabled = false` (or no library, or not root) the
app runs exactly as before and only the browser preview shows frames.

Threading: SwapOnVSync blocks until the panel's next refresh, so it must not run on the asyncio loop. The engine's
sink only stores the newest frame; a dedicated thread pushes it to the panels. If the panels fall behind, frames
are skipped, never queued.

Root: on a Pi 4 the library needs root for GPIO timing. The service starts as root, opens the matrix, then
`drop_root()` switches the whole process to the normal user (LEDTICKER_RUN_AS) before anything writes to data/.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from PIL import Image

from .config import Context
from .display import Geometry

log = logging.getLogger("ledticker.matrix")

# config key -> RGBMatrixOptions attribute, with the defaults for these panels (FM6373 + DP32019B, 2x2 of 128x64,
# Triple Bonnet with two chains of two). See LED_Ticker_Project_Plan.md section 3 and the fork's spwm.md.
OPTION_DEFAULTS: dict[str, Any] = {
    "hardware_mapping": "regular",      # Adafruit Triple Bonnet / plain HUB75 wiring
    "chain_length": 2,                  # panels per output
    "parallel": 2,                      # outputs used (stacked top/bottom)
    "panel_type": "fm6373",
    "spwm_row_address_type": 0,         # 0 = DP32019B direct A-E row select
    "gpio_slowdown": 5,                 # spwm.md: 5 for a Pi 4
    "pwm_bits": 11,
    "brightness": 100,                  # the app's own brightness/schedule scales pixels on top of this
    "limit_refresh_rate_hz": 0,         # spwm.md suggests trying 60 if the image flickers or tears
    "led_rgb_sequence": "RGB",
    "pixel_mapper_config": "",          # leave empty: the two chains already stack into 256x128
    "show_refresh_rate": False,
}
SKIP_KEYS = {"enabled", "env", "extra"}


def build_options(opts: Any, cfg: dict[str, Any], geom: Geometry) -> Any:
    """Fill an RGBMatrixOptions from [matrix]. Unknown keys go through as-is (for the fork's other spwm_* knobs)."""
    values = {"rows": geom.module_h, "cols": geom.module_w, **OPTION_DEFAULTS}
    values.update({k: v for k, v in cfg.items() if k not in SKIP_KEYS})
    values.update(cfg.get("extra") or {})
    values["drop_privileges"] = False   # we drop to the app's own user ourselves (drop_root), not to "daemon"
    for key, value in values.items():
        # Look on the class, not the instance: reading a Cython string property that's still NULL (panel_type
        # before it's set) can crash the interpreter, and hasattr(instance) would call that getter.
        if not hasattr(type(opts), key):
            log.warning("[matrix] %s isn't an option this rgbmatrix build knows; ignored", key)
            continue
        setattr(opts, key, value)
    w = values["cols"] * values["chain_length"]
    h = values["rows"] * values["parallel"]
    if not values["pixel_mapper_config"] and (w, h) != (geom.width, geom.height):
        log.warning("[matrix] panels give %dx%d but [display] is %dx%d; the picture will be cropped or padded",
                    w, h, geom.width, geom.height)
    return opts


class MatrixOutput:
    """A frame sink (engine.subscribe) backed by a background thread that drives the panels."""

    def __init__(self, matrix: Any):
        self.matrix = matrix
        self.size = (int(matrix.width), int(matrix.height))
        self._canvas = matrix.CreateFrameCanvas()
        self._latest: Image.Image | None = None
        self._cond = threading.Condition()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self.frames_shown = 0

    def __call__(self, frame: Image.Image) -> None:
        """Called on the event loop for every rendered frame: just hand it over (copied, the engine may reuse it)."""
        with self._cond:
            self._latest = frame.copy()
            self._cond.notify()

    def show(self, frame: Image.Image) -> None:
        """Put one frame on the panels now (blocks until the next refresh)."""
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        if frame.size != self.size:
            fitted = Image.new("RGB", self.size)
            fitted.paste(frame, (0, 0))
            frame = fitted
        self._canvas.SetImage(frame, 0, 0)
        self._canvas = self.matrix.SwapOnVSync(self._canvas)
        self.frames_shown += 1

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="matrix", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._latest is None and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                frame, self._latest = self._latest, None
            try:
                self.show(frame)
            except Exception:
                log.exception("couldn't send a frame to the panels")

    def close(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            self.matrix.Clear()
        except Exception:
            pass


def open_matrix(ctx: Context, geom: Geometry, force: bool = False) -> MatrixOutput | None:
    """The panels per [matrix], or None (disabled, library missing, not root). Never raises: the dashboard and
    preview must keep working even if the LED side can't start."""
    cfg = ctx.section("matrix")
    if not (force or cfg.get("enabled", False)):
        return None
    for key, value in (cfg.get("env") or {}).items():   # SPWM_* tuning variables the C++ side reads at startup
        os.environ[str(key)] = str(value)
    try:
        from rgbmatrix import RGBMatrix, RGBMatrixOptions   # type: ignore[import-not-found]
    except ImportError:
        log.error("[matrix] the rgbmatrix library isn't installed in this venv. "
                  "On the Pi, re-run: bash deploy/setup-pi.sh")
        return None
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log.error("[matrix] the LED library needs root on a Pi 4. Run it through the service "
                  "(sudo systemctl restart ledticker) or with sudo")
        return None
    try:
        opts = build_options(RGBMatrixOptions(), cfg, geom)
        out = MatrixOutput(RGBMatrix(options=opts))
    except Exception:
        log.exception("[matrix] couldn't start the LED panels")
        return None
    log.warning("LED panels running: %dx%d", *out.size)
    return out


def drop_root(user: str | None) -> None:
    """If running as root, become `user` (uid, gid and groups) so files in data/ stay owned by them."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    if not user:
        log.warning("running as root and LEDTICKER_RUN_AS isn't set; staying root")
        return
    import pwd
    pw = pwd.getpwnam(user)
    os.setgroups(os.getgrouplist(pw.pw_name, pw.pw_gid))
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ["HOME"], os.environ["USER"], os.environ["LOGNAME"] = pw.pw_dir, pw.pw_name, pw.pw_name


def test_pattern(size: tuple[int, int], phase: int, brightness: int) -> Image.Image:
    """Bring-up frames. Phase 0: each 128x64 quarter a different color, labeled by where it should appear, with a
    white outline round the whole display and a 1-pixel border on every module. Then solid red, green, blue, white."""
    from PIL import ImageDraw
    w, h = size
    k = max(0, min(100, brightness)) / 100
    dim = lambda c: tuple(int(v * k) for v in c)  # noqa: E731
    solids = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    if phase % 5:
        return Image.new("RGB", size, dim(solids[phase % 5 - 1]))
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    mw, mh = w // 2, h // 2
    quads = [("TOP LEFT", (0, 0), (200, 0, 0)), ("TOP RIGHT", (mw, 0), (0, 160, 0)),
             ("BOTTOM LEFT", (0, mh), (0, 0, 220)), ("BOTTOM RIGHT", (mw, mh), (180, 120, 0))]
    for label, (x, y), color in quads:
        d.rectangle((x, y, x + mw - 1, y + mh - 1), fill=dim(color), outline=dim((90, 90, 90)))
        d.text((x + 6, y + 6), label, fill=dim((255, 255, 255)))
        d.text((x + 6, y + 20), f"x{x} y{y}", fill=dim((255, 255, 255)))
    d.rectangle((0, 0, w - 1, h - 1), outline=dim((255, 255, 255)))
    d.rectangle((0, 0, 3, 3), fill=dim((255, 255, 255)))                    # marks pixel (0, 0)
    return img
