"""Home Screen push notifications: subscriptions, per-permission topics, real VAPID signing, API wiring."""
from __future__ import annotations

import base64
import json
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from ledticker.config import Context
from ledticker.web.app import create_app, push_contact
from ledticker.web.push import Push

TZ = ZoneInfo("America/Chicago")
ME, GF, RM = "me@gmail.com", "gf@gmail.com", "house@gmail.com"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def fake_sub(n: int) -> dict:
    """A browser-shaped subscription with a real P-256 key, so encryption can run for real."""
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {"endpoint": f"https://web.push.apple.com/device{n}", "keys": {"p256dh": b64(pub), "auth": b64(b"0123456789abcdef")}}


def hdr(login):
    return {"Tailscale-User-Login": login}


async def test_topics_respect_permissions_and_dead_endpoints_are_dropped(tmp_path):
    p = Push.load(tmp_path, "mailto:x@y.com")
    sent = []
    p.sender = lambda sub, payload: sent.append((sub["endpoint"], json.loads(payload)))
    p.subscribe(ME, fake_sub(1), ["trades", "love"], {"trades", "weather", "love"})
    p.subscribe(GF, fake_sub(2), ["trades", "love"], {"trades", "weather"})       # love not allowed for her
    assert p.subs["https://web.push.apple.com/device2"]["topics"] == ["trades"]
    with pytest.raises(ValueError):
        p.subscribe(GF, {"endpoint": "http://evil", "keys": {}}, ["trades"], {"trades"})

    may = lambda login, perm: perm == "view" or login == ME
    assert await p.send("trades", "T", "b", may) == 2
    assert await p.send("love", "L", "b", may) == 1 and sent[-1][0].endswith("device1")
    assert await p.send("trades", "T", "b", lambda lg, perm: lg == ME) == 1     # access revoked → not sent

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()
    def boom(sub, payload):
        raise Gone("gone")
    p.sender = boom
    assert await p.send("trades", "T", "b", may) == 0
    assert p.subs == {}                                                          # both pruned
    assert json.loads((tmp_path / "push.json").read_text())["subscriptions"] == {}


def test_real_vapid_signing_and_encryption(tmp_path, monkeypatch):
    p = Push.load(tmp_path, "mailto:me@gmail.com")
    key = p.public_key()
    assert len(base64.urlsafe_b64decode(key + "==")) == 65 and (tmp_path / "vapid.pem").exists()
    assert Push.load(tmp_path, "x").public_key() == key                          # stable across restarts
    seen = {}

    class Resp:
        status_code, text, headers = 201, "", {}

    def post(url, data=None, headers=None, timeout=None, **kw):
        seen.update(url=url, headers=headers, body=data)
        return Resp()

    import requests
    monkeypatch.setattr(requests, "post", post)
    sub = fake_sub(1)
    p._send_one(sub["endpoint"], {"keys": sub["keys"]}, json.dumps({"title": "hi"}))
    assert seen["url"] == sub["endpoint"]
    assert seen["headers"]["authorization"].startswith("vapid t=") and key in seen["headers"]["authorization"]
    assert seen["headers"]["content-encoding"] == "aes128gcm" and len(seen["body"]) > 20


def test_contact_claim():
    ctx = Context(root=".", config={}, secrets={"LEDTICKER_SUPERUSERS": "name@github, me@gmail.com"}, tz=TZ)
    assert push_contact(ctx) == "mailto:me@gmail.com"
    ctx = Context(root=".", config={"push": {"contact": "https://example.org"}}, secrets={}, tz=TZ)
    assert push_contact(ctx) == "https://example.org"


def test_api(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    app = create_app(ctx, auth="tailscale")
    c = TestClient(app)
    c.put(f"/api/access/{GF}", json={"role": "partner"}, headers=hdr(ME)).raise_for_status()
    sent = []
    app.state.push.sender = lambda sub, payload: sent.append((sub["login"], json.loads(payload)))

    assert c.get("/sw.js").headers["content-type"].startswith("application/javascript")   # no identity needed
    assert c.get("/api/push").status_code == 403
    assert set(c.get("/api/push", headers=hdr(GF)).json()["topics"]) == {"trades", "big_plays", "eatout", "weather"}
    assert set(c.get("/api/push", headers=hdr(ME)).json()["topics"]) == {"trades", "big_plays", "eatout", "weather", "love"}

    assert c.post("/api/push/test", headers=hdr(ME)).status_code == 404                     # nothing subscribed yet
    c.post("/api/push/subscribe", json={"subscription": fake_sub(1), "topics": ["love", "trades"]}, headers=hdr(ME)).raise_for_status()
    c.post("/api/push/subscribe", json={"subscription": fake_sub(2), "topics": ["love", "trades"]}, headers=hdr(GF)).raise_for_status()
    assert c.get("/api/push", headers=hdr(GF)).json()["mine"][0]["topics"] == ["trades"]
    assert c.post("/api/push/test", headers=hdr(ME)).json() == {"sent": 1}

    sent.clear()
    c.post("/api/love", json={"message": "hi you"}, headers=hdr(GF)).raise_for_status()
    assert [(lg, m["title"], m["body"]) for lg, m in sent] == [(ME, "Love tap from gf@gmail.com", "“hi you”")]

    other = c.get("/api/push", headers=hdr(GF)).json()["mine"][0]["endpoint"]
    c.post("/api/push/unsubscribe", json={"endpoint": other}, headers=hdr(ME))               # not his device: no-op
    assert c.get("/api/push", headers=hdr(GF)).json()["mine"]
