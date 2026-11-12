"""AirPlay screen mirroring: an iPhone on the same Wi-Fi picks the display under Screen Mirroring and its screen
takes over the panels.

The receiving is done by UxPlay (https://github.com/FDH2/UxPlay), run as a child process. Instead of a monitor,
its video goes through a few GStreamer elements we append (scale to the panel, one small frame queue) into a named
pipe, and a reader thread here keeps only the newest frame. Nothing is stored and nothing is decoded in Python.

Kept light on purpose ([airplay] in config.toml, off by default):
  - the phone is asked for a small picture (`size`), so there is little to decode
  - the frame queue in front of the pipe holds 2 frames and drops the rest, so a slow reader can't pile up memory
  - audio is off (the LED library needs the Pi's onboard sound disabled anyway)
  - a watchdog restarts UxPlay if its memory passes `max_ram_mb`
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from PIL import Image

log = logging.getLogger("ledticker.airplay")

BYTES_PER_PIXEL = 4        # RGBx: rows are never padded, whatever the panel width
FITS = ("fill", "fit")
ROTATIONS = {"left": ["-r", "L"], "right": ["-r", "R"], "flip": ["-f", "I"]}   # quarter turns, or upside down
DECODERS = {"auto": [], "hardware": ["-v4l2"], "software": ["-avdec"]}


class Mirror:
    """The newest mirrored frame, and whether mirroring is on screen right now."""

    def __init__(self, size: tuple[int, int], idle_seconds: float = 30.0):
        self.size = size
        self.frame_bytes = size[0] * size[1] * BYTES_PER_PIXEL
        self.idle_seconds = idle_seconds
        self.enabled = False                 # set by the Receiver once it is actually running
        self.name = ""
        self._lock = threading.Lock()
        self._raw: bytes | None = None
        self._at = 0.0
        self._seq = 0
        self._img: tuple[int, Image.Image] | None = None
        self._dismissed = False

    def feed(self, raw: bytes, now: float | None = None) -> None:
        """A whole frame arrived (called from the reader thread)."""
        if len(raw) != self.frame_bytes:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._dismissed and now - self._at > self.idle_seconds:
                self._dismissed = False      # a new session after a pause: show it again
            self._raw, self._at = raw, now
            self._seq += 1

    def end(self) -> None:
        """The stream closed (the phone stopped mirroring)."""
        with self._lock:
            self._raw, self._img, self._dismissed = None, None, False

    def dismiss(self) -> None:
        """Hide this mirroring session (the dashboard's Next button); the next session shows again."""
        with self._lock:
            self._dismissed = True

    def active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._raw is not None and not self._dismissed and now - self._at < self.idle_seconds

    def image(self) -> Image.Image | None:
        with self._lock:
            if self._raw is None:
                return None
            if self._img is None or self._img[0] != self._seq:
                self._img = (self._seq, Image.frombuffer("RGBX", self.size, self._raw, "raw", "RGBX", 0, 1).convert("RGB"))
            return self._img[1]

    def pump(self, stream) -> int:
        """Read frames from a byte stream until it closes. Returns how many whole frames came through."""
        n, buf, got = self.frame_bytes, bytearray(self.frame_bytes), 0
        view, frames = memoryview(buf), 0
        while True:
            k = stream.readinto(view[got:])
            if not k:                        # the writer closed: a partial frame is dropped
                break
            got += k
            if got == n:
                self.feed(bytes(buf))
                got, frames = 0, frames + 1
        self.end()
        return frames


def sink(size: tuple[int, int], fit: str, pipe: Path) -> str:
    """What replaces UxPlay's video window: scale to the panel and write raw frames to the pipe.
    fit: the picture is letterboxed inside the panel; fill: it is cropped to cover the panel."""
    W, H = size
    caps = f"video/x-raw,format=RGBx,width={W},height={H},pixel-aspect-ratio=1/1"
    head = f"aspectratiocrop aspect-ratio={W}/{H} ! videoscale" if fit == "fill" else "videoscale add-borders=true"
    return (f"{head} ! videoconvert ! {caps} ! queue leaky=downstream max-size-buffers=2 max-size-bytes=0 "
            f"max-size-time=0 ! filesink location={pipe} buffer-mode=unbuffered")


def command(cfg: dict, size: tuple[int, int], pipe: Path) -> list[str]:
    """The UxPlay command line for [airplay]."""
    fit = cfg.get("fit") if cfg.get("fit") in FITS else "fill"
    args = [str(cfg.get("binary") or "uxplay"), "-n", str(cfg.get("name") or "LED Ticker"), "-nh",
            "-s", str(cfg.get("size") or "800x400"), "-fps", str(int(cfg.get("fps", 30))),
            "-vsync", "no", "-as", "0"]
    args += DECODERS.get(str(cfg.get("decoder", "auto")), [])
    if cfg.get("rotate") in ROTATIONS:
        args += ROTATIONS[cfg["rotate"]]
    args += [str(a) for a in cfg.get("extra_args") or []]
    return args + ["-vs", sink(size, fit, pipe)]


def rss_mb(pid: int) -> float | None:
    """Resident memory of a process in MB (Linux), or None if it can't be read."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class Receiver:
    """Runs UxPlay and feeds its frames to a Mirror. Does nothing unless [airplay] enabled = true."""

    def __init__(self, cfg: dict, mirror: Mirror, data_dir: Path):
        self.cfg, self.mirror = cfg, mirror
        self.pipe = data_dir / "airplay.pipe"
        self.max_ram_mb = float(cfg.get("max_ram_mb", 300))
        self.ram_mb: float | None = None
        self.problem = ""
        self._stop = threading.Event()

    def _read_forever(self) -> None:
        while not self._stop.is_set():
            try:
                with open(self.pipe, "rb", buffering=0) as f:    # waits here until UxPlay opens the other end
                    self.mirror.pump(f)
            except OSError as e:
                log.warning("airplay pipe: %s", e)
                time.sleep(2)

    def ready(self) -> bool:
        """Can mirroring run here? Sets `problem` when it can't."""
        binary = str(self.cfg.get("binary") or "uxplay")
        if not hasattr(os, "mkfifo"):
            self.problem = "AirPlay mirroring only runs on the Pi (Linux)"
        elif shutil.which(binary) is None:
            self.problem = f"{binary} isn't installed (run deploy/setup-pi.sh again)"
        else:
            self.problem = ""
        return not self.problem

    async def run(self) -> None:
        if not self.cfg.get("enabled", False):
            return
        if not self.ready():
            log.warning("[airplay] %s", self.problem)
            return
        self.pipe.parent.mkdir(parents=True, exist_ok=True)
        if self.pipe.exists():
            self.pipe.unlink()
        os.mkfifo(self.pipe, 0o600)
        threading.Thread(target=self._read_forever, name="airplay", daemon=True).start()
        cmd = command(self.cfg, self.mirror.size, self.pipe)
        self.mirror.enabled, self.mirror.name = True, cmd[2]
        wait = 5.0
        try:
            while True:
                started = time.monotonic()
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT)
                log.warning("[airplay] receiver started as \"%s\" (pid %s)", cmd[2], proc.pid)
                try:
                    await asyncio.gather(self._log_output(proc), self._watch_ram(proc))
                finally:
                    await self._kill(proc)
                self.mirror.end()
                # a receiver that dies straight away is retried more and more slowly; a long run resets that
                wait = 5.0 if time.monotonic() - started > 60 else min(300.0, wait * 2)
                log.warning("[airplay] receiver stopped (exit %s); restarting in %.0f s", proc.returncode, wait)
                await asyncio.sleep(wait)
        finally:
            self._stop.set()
            self.mirror.enabled = False
            try:
                self.pipe.unlink()
            except OSError:
                pass

    async def _log_output(self, proc) -> None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                (log.warning if "ERROR" in text or "WARNING" in text else log.info)("uxplay: %s", text[:300])

    async def _watch_ram(self, proc) -> None:
        """Restart the receiver if its memory passes the cap. Returns when the process is gone."""
        while proc.returncode is None:
            self.ram_mb = rss_mb(proc.pid)
            if self.ram_mb is not None and self.max_ram_mb > 0 and self.ram_mb > self.max_ram_mb:
                log.warning("[airplay] receiver is using %.0f MB (cap %.0f): restarting it", self.ram_mb, self.max_ram_mb)
                await self._kill(proc)
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _kill(proc) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
