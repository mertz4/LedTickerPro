"""In-memory store of the latest good data per source, plus health, snapshotted to disk.

The renderer and web API read from here; they never call external APIs directly.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


log = logging.getLogger("ledticker.store")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


@dataclass
class SourceState:
    name: str
    label: str = ""
    enabled: bool = True
    disabled_reason: str = ""
    data: Any = None
    updated_at: str | None = None      # last successful fetch
    last_attempt: str | None = None
    last_error: str | None = None
    error_count: int = 0
    auth_error: bool = False
    stale_after: float = 1800
    next_run: str | None = None
    dormant_until: str | None = None   # out of season: not polling until this time

    def health(self, now: datetime | None = None) -> str:
        if not self.enabled:
            return "disabled"
        if self.dormant_until:
            return "dormant"
        if self.auth_error:
            return "auth_error"
        if self.updated_at is None:
            return "error" if self.last_error else "pending"
        age = ((now or utcnow()) - datetime.fromisoformat(self.updated_at)).total_seconds()
        if age > self.stale_after:
            return "stale"
        return "degraded" if self.last_error else "ok"


Listener = Callable[[str, SourceState], None]
_FIELDS = tuple(f.name for f in dataclasses.fields(SourceState))
SAVE_DELAY = 2.0      # seconds the running app waits after an update before writing the cache file


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.states: dict[str, SourceState] = {}
        self._listeners: list[Listener] = []
        self._save_due = False
        self._write_lock = threading.Lock()

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def register(self, name: str, label: str, enabled: bool, reason: str, stale_after: float) -> SourceState:
        st = self.states.get(name) or SourceState(name=name)
        st.label, st.enabled, st.disabled_reason, st.stale_after = label, enabled, reason, stale_after
        self.states[name] = st
        return st

    def get(self, name: str) -> Any:
        st = self.states.get(name)
        return st.data if st else None

    def ok(self, name: str, data: Any) -> None:
        st = self.states[name]
        now = utcnow().isoformat()
        st.data, st.updated_at, st.last_attempt = to_jsonable(data), now, now
        st.last_error, st.error_count, st.auth_error = None, 0, False
        st.dormant_until = None
        self._emit(name, st)

    def dormant(self, name: str, until: datetime) -> None:
        st = self.states[name]
        st.dormant_until = until.isoformat()
        self._emit(name, st)

    def fail(self, name: str, error: str, auth: bool = False) -> None:
        st = self.states[name]
        st.last_attempt, st.last_error = utcnow().isoformat(), error
        st.error_count += 1
        st.auth_error = auth
        self._emit(name, st)

    def _emit(self, name: str, st: SourceState) -> None:
        for fn in self._listeners:
            try:
                fn(name, st)
            except Exception:  # a broken listener must never stop data collection
                pass
        self._save_soon()

    # ---- persistence: keep last good data across restarts

    def _save_soon(self) -> None:
        """Save after an update. In the running app that must not happen right here: this is the same thread that
        draws every frame, and writing the file stalls it long enough to make the ticker hitch. So the write is
        put off a moment (several updates in a row become one write) and done on a background thread.
        Outside the app (the command line, most tests) there is nothing to stall, so it saves at once."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        if not self.path or self._save_due:
            return
        self._save_due = True
        loop.call_later(SAVE_DELAY, self._save_in_background, loop)

    def _save_in_background(self, loop: asyncio.AbstractEventLoop) -> None:
        self._save_due = False
        payload = self._payload()            # built here, on the app's thread, so it is one consistent picture
        loop.run_in_executor(None, self._write, payload)

    def _payload(self) -> str:
        # compact on purpose: json's fast encoder is only used without indentation
        return json.dumps({n: {f: getattr(s, f) for f in _FIELDS} for n, s in self.states.items()})

    def save(self) -> None:
        if self.path:
            self._write(self._payload())

    def _write(self, payload: str) -> None:
        with self._write_lock:               # one writer at a time: they share the temporary file
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            # Windows can briefly lock the target (antivirus, indexer, an open reader): retry, then give up
            # quietly. The in-memory store stays correct either way; this file is only a restart convenience.
            for attempt in range(5):
                try:
                    tmp.write_text(payload, encoding="utf-8")
                    os.replace(tmp, self.path)
                    return
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
                except OSError as e:
                    log.warning("could not save cache: %s", e)
                    return
            log.warning("could not save cache (file locked); will retry on next update")

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        fields = {f.name for f in dataclasses.fields(SourceState)}
        for name, s in raw.items():
            self.states[name] = SourceState(**{k: v for k, v in s.items() if k in fields})
