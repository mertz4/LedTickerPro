"""In-memory store of the latest good data per source, plus health, snapshotted to disk.

The renderer and web API read from here; they never call external APIs directly.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


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


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.states: dict[str, SourceState] = {}
        self._listeners: list[Listener] = []

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
        self.save()

    # ---- persistence: keep last good data across restarts

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({n: dataclasses.asdict(s) for n, s in self.states.items()}, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

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
