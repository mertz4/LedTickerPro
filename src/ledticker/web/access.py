"""Who may do what on the dashboard, keyed by Tailscale account.

`tailscale serve` adds a `Tailscale-User-Login` header (e.g. "you@gmail.com") to every request it proxies, including
requests from people the Pi is shared with, and strips any copy a client sends itself. That only holds while the app
listens on 127.0.0.1 so the only way in is through serve, which `--auth tailscale` enforces.

Roles give a starting set of permissions; a superuser can grant anyone extra ones. Superusers listed in
LEDTICKER_SUPERUSERS (secrets.env) are fixed and can't be edited or removed from the dashboard, so nobody can lock
the owner out. Everyone else lives in data/access.json. Accounts on no list are blocked entirely.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from pathlib import Path
from typing import Mapping

PERMS = {
    "view": "See the live preview and status",
    "display": "Brightness & power",
    "control": "Switch modes, skip, show a widget",
    "edit": "Edit layouts, modes, schedule & settings",
    "props": "Player props",
    "love_tap": "Send a love tap",
    "media": "Upload & show pictures",
    "eatout": "Update the eat-out counter",
    "admin": "Manage who has access",
}
GRANTABLE = ("display", "control", "edit", "props", "love_tap", "media", "eatout")   # extras a superuser can hand out
ROLES = {
    "superuser": {"label": "Superuser", "perms": tuple(PERMS)},
    "partner": {"label": "Partner", "perms": ("view", "love_tap")},
    "roommate": {"label": "Roommate", "perms": ("view", "display", "eatout")},
}
LOGIN_HEADER = "tailscale-user-login"
NAME_HEADER = "tailscale-user-name"


@dataclass(frozen=True)
class User:
    login: str
    name: str
    role: str
    perms: frozenset[str]
    locked: bool = False   # from secrets.env / local mode: not editable in the dashboard

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def as_dict(self) -> dict:
        return {"login": self.login, "name": self.name, "role": self.role,
                "role_label": ROLES[self.role]["label"], "perms": sorted(self.perms), "locked": self.locked}


class AccessError(ValueError):
    pass


def _decode(value: str | None) -> str:
    """Tailscale RFC 2047-encodes non-ASCII names (=?utf-8?q?...?=)."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _perms(role: str, extra) -> frozenset[str]:
    return frozenset(ROLES[role]["perms"]) | {p for p in (extra or ()) if p in GRANTABLE}


@dataclass
class Access:
    path: Path | None
    superusers: frozenset[str] = frozenset()
    enabled: bool = True                     # False = --auth none: everyone is the local superuser
    users: dict[str, dict] = field(default_factory=dict)
    blocked: dict[str, dict] = field(default_factory=dict)   # recent refused logins, for one-click adding

    @classmethod
    def load(cls, path: Path | None, superusers: str | None, enabled: bool) -> "Access":
        sus = frozenset(s.strip().lower() for s in (superusers or "").split(",") if s.strip())
        a = cls(path=path, superusers=sus, enabled=enabled)
        if path and path.exists():
            try:
                a.users = {k.lower(): v for k, v in (json.loads(path.read_text(encoding="utf-8")).get("users") or {}).items()
                           if isinstance(v, dict) and v.get("role") in ROLES}
            except (OSError, ValueError):
                a.users = {}
        return a

    # ---- who is this request?
    def identify(self, headers: Mapping[str, str]) -> User | None:
        if not self.enabled:
            return User("local", "Local", "superuser", frozenset(PERMS), locked=True)
        h = {k.lower(): v for k, v in headers.items()}   # header names are case-insensitive
        login = (h.get(LOGIN_HEADER) or "").strip().lower()
        if not login:
            return None
        name = _decode(h.get(NAME_HEADER)) or login
        if login in self.superusers:
            return User(login, name, "superuser", frozenset(PERMS), locked=True)
        u = self.users.get(login)
        if u is None:
            self.blocked.pop(login, None)
            self.blocked[login] = {"name": name, "at": time.time()}
            while len(self.blocked) > 10:
                self.blocked.pop(next(iter(self.blocked)))
            return None
        self.blocked.pop(login, None)
        return User(login, u.get("name") or name, u["role"], _perms(u["role"], u.get("extra")))

    def may(self, login: str, perm: str) -> bool:
        """Does this login (still) have `perm`? Used when sending notifications later."""
        if not self.enabled:
            return True
        login = login.lower()
        if login in self.superusers:
            return True
        u = self.users.get(login)
        return u is not None and perm in _perms(u["role"], u.get("extra"))

    # ---- managing the list (superusers only)
    def listing(self) -> dict:
        return {
            "roles": {k: {"label": r["label"], "perms": list(r["perms"])} for k, r in ROLES.items()},
            "perms": PERMS, "grantable": list(GRANTABLE),
            "superusers": sorted(self.superusers),
            "users": [{"login": k, "name": v.get("name") or "", "role": v["role"],
                       "extra": [p for p in v.get("extra") or [] if p in GRANTABLE]} for k, v in sorted(self.users.items())],
            "blocked": [{"login": k, **v} for k, v in reversed(self.blocked.items())],
            "enabled": self.enabled,
        }

    def put(self, login: str, role: str, extra=(), name: str = "", by: User | None = None) -> None:
        login = login.strip().lower()
        if not login or len(login) > 200 or any(c.isspace() for c in login):
            raise AccessError("enter the person's Tailscale login, e.g. name@gmail.com")
        if role not in ROLES:
            raise AccessError(f"unknown role {role}")
        if login in self.superusers:
            raise AccessError("that account is a fixed superuser (LEDTICKER_SUPERUSERS in secrets.env)")
        if by and by.login == login and role != "superuser":
            raise AccessError("you can't take away your own superuser access")
        bad = [p for p in extra or () if p not in GRANTABLE]
        if bad:
            raise AccessError(f"can't grant {', '.join(bad)}")
        self.users[login] = {"role": role, "name": (name or self.users.get(login, {}).get("name") or
                                                    self.blocked.get(login, {}).get("name") or "")[:80],
                             "extra": sorted(set(extra or ()) - set(ROLES[role]["perms"]))}
        self.blocked.pop(login, None)
        self._save()

    def remove(self, login: str, by: User | None = None) -> None:
        login = login.strip().lower()
        if login in self.superusers:
            raise AccessError("that account is a fixed superuser (LEDTICKER_SUPERUSERS in secrets.env)")
        if by and by.login == login:
            raise AccessError("you can't remove yourself")
        if self.users.pop(login, None) is None:
            raise KeyError(login)
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": self.users}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
