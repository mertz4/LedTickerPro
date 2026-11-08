"""Web Push notifications to the dashboard installed on a phone's Home Screen (iOS 16.4+, Android, desktop).

The browser makes a subscription (an endpoint at Apple's / Google's push service plus encryption keys) and posts it
here with the topics the person wants. When something happens on the display, every subscription that asked for that
topic gets an encrypted message, signed with this server's VAPID key (generated once into data/vapid.pem).

Subscriptions are per Tailscale login, so each person picks their own topics, and a topic's permission is
checked again at send time (removing someone from the Access list stops their notifications too).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("ledticker.push")

# topic -> (label, permission needed to receive it)
TOPICS = {
    "trades": ("Fantasy trades in your league", "view"),
    "weather": ("Severe weather alerts", "view"),
    "love": ("Love taps sent to the display", "admin"),
}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@dataclass
class Push:
    data_dir: Path
    contact: str                                       # VAPID "sub" claim: mailto: or https: URL
    subs: dict[str, dict] = field(default_factory=dict)   # endpoint -> {login, keys, topics, created}
    _vapid: object | None = None
    sender: Callable[[dict, str], None] | None = None  # injectable for tests; default = pywebpush

    @property
    def path(self) -> Path:
        return self.data_dir / "push.json"

    @classmethod
    def load(cls, data_dir: Path, contact: str) -> "Push":
        p = cls(data_dir=data_dir, contact=contact)
        try:
            p.subs = json.loads(p.path.read_text(encoding="utf-8")).get("subscriptions") or {}
        except (OSError, ValueError):
            p.subs = {}
        return p

    # ---- VAPID key (one per install, private key never leaves the Pi)
    def vapid(self):
        if self._vapid is None:
            from py_vapid import Vapid
            pem = self.data_dir / "vapid.pem"
            if pem.exists():
                self._vapid = Vapid.from_file(str(pem))
            else:
                v = Vapid()
                v.generate_keys()
                self.data_dir.mkdir(parents=True, exist_ok=True)
                v.save_key(str(pem))
                self._vapid = v
        return self._vapid

    def public_key(self) -> str:
        """applicationServerKey for the browser: the raw uncompressed P-256 point, base64url."""
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        return _b64url(self.vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))

    # ---- subscriptions
    def subscribe(self, login: str, sub: dict, topics: list[str], allowed: set[str]) -> dict:
        endpoint = (sub or {}).get("endpoint") or ""
        keys = (sub or {}).get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("that isn't a push subscription")
        topics = sorted(t for t in set(topics) if t in TOPICS and t in allowed)
        self.subs[endpoint] = {"login": login, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                               "topics": topics, "created": self.subs.get(endpoint, {}).get("created") or time.time()}
        self._save()
        return self.subs[endpoint]

    def unsubscribe(self, endpoint: str, login: str | None = None) -> bool:
        s = self.subs.get(endpoint)
        if s is None or (login is not None and s["login"] != login):
            return False
        del self.subs[endpoint]
        self._save()
        return True

    def mine(self, login: str) -> list[dict]:
        return [{"endpoint": e, "topics": s["topics"]} for e, s in self.subs.items() if s["login"] == login]

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"subscriptions": self.subs}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- sending
    def _send_one(self, endpoint: str, sub: dict, payload: str) -> None:
        if self.sender is not None:
            self.sender({"endpoint": endpoint, **sub}, payload)
            return
        from pywebpush import webpush
        webpush({"endpoint": endpoint, "keys": sub["keys"]}, payload, vapid_private_key=self.vapid(),
                vapid_claims={"sub": self.contact}, ttl=3600, timeout=10)

    def targets(self, topic: str, may: Callable[[str, str], bool], login: str | None = None) -> list[str]:
        """Endpoints that should get `topic`: subscribed to it, and still allowed to (permission checked now)."""
        need = TOPICS[topic][1] if topic in TOPICS else "view"
        return [e for e, s in self.subs.items()
                if (login is None or s["login"] == login)
                and (topic == "test" or topic in s["topics"])
                and may(s["login"], need)]

    async def send(self, topic: str, title: str, body: str, may: Callable[[str, str], bool],
                   login: str | None = None, tag: str | None = None) -> int:
        """Notify everyone subscribed to `topic` (or just `login`'s devices). Returns how many were delivered."""
        payload = json.dumps({"title": title, "body": body, "tag": tag or topic, "url": "/"})
        sent = 0
        for endpoint in self.targets(topic, may, login):
            sub = self.subs.get(endpoint)
            if sub is None:
                continue
            try:
                await asyncio.to_thread(self._send_one, endpoint, sub, payload)
                sent += 1
            except Exception as e:                 # WebPushException carries the push service's response
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):           # the phone dropped this subscription: forget it
                    self.unsubscribe(endpoint)
                    log.info("push subscription for %s expired; removed", sub["login"])
                else:
                    log.warning("push to %s failed: %s", sub["login"], e)
        return sent
