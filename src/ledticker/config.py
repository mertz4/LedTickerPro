"""Load config.toml (settings) and secrets.env (credentials)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@dataclass
class Context:
    root: Path
    config: dict[str, Any]
    secrets: dict[str, str] = field(repr=False)
    tz: ZoneInfo

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def secret(self, key: str, default: str | None = None) -> str | None:
        """Environment variables override secrets.env."""
        return os.environ.get(key) or self.secrets.get(key) or default

    def section(self, *path: str) -> dict[str, Any]:
        node: Any = self.config
        for p in path:
            node = node.get(p, {}) if isinstance(node, dict) else {}
        return node if isinstance(node, dict) else {}

    @property
    def cache_path(self) -> Path:
        return self.root / self.section("general").get("cache_file", "data/cache.json")


def load(root: str | Path | None = None) -> Context:
    root_path = Path(root or os.environ.get("LEDTICKER_HOME") or Path.cwd()).resolve()
    cfg_file = root_path / "config.toml"
    config = tomllib.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    secrets = parse_env_file(root_path / "secrets.env")
    tz = ZoneInfo(config.get("general", {}).get("timezone", "America/Chicago"))
    return Context(root=root_path, config=config, secrets=secrets, tz=tz)
