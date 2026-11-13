"""Tailscale-identity roles (web/access.py + every API route) and the love tap takeover."""
from __future__ import annotations

import json
import time
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ledticker.config import Context
from ledticker.display import geometry
from ledticker.render import love
from ledticker.render.engine import Engine
from ledticker.render.images import ImageCache
from ledticker.store import Store
from ledticker.web.access import Access, AccessError
from ledticker.web.app import create_app

TZ = ZoneInfo("America/Chicago")
ME, GF, RM = "me@gmail.com", "gf@gmail.com", "house@gmail.com"


def as_(login: str | None, name: str = "") -> dict:
    h = {}
    if login:
        h["Tailscale-User-Login"] = login
    if name:
        h["Tailscale-User-Name"] = name
    return h


# ---------------------------------------------------------------- access rules

def test_roles_extras_and_blocking(tmp_path):
    a = Access.load(tmp_path / "access.json", f" {ME.upper()} ", enabled=True)
    assert a.identify({}) is None
    assert a.identify(as_("stranger@x.com", "Stranger")) is None
    assert [b["login"] for b in a.listing()["blocked"]] == ["stranger@x.com"]

    me = a.identify(as_(ME))
    assert me.role == "superuser" and me.locked and me.can("admin")

    a.put(GF, "partner", name="Her", by=me)
    gf = a.identify(as_(GF.upper()))                  # logins are case-insensitive
    assert gf.name == "Her" and gf.can("view") and gf.can("love_tap")
    assert not gf.can("display") and not gf.can("control") and not gf.can("admin")

    a.put(RM, "roommate", extra=["control"], by=me)
    rm = a.identify(as_(RM))
    assert rm.can("display") and rm.can("control") and not rm.can("edit") and not rm.can("love_tap")

    saved = json.loads((tmp_path / "access.json").read_text())   # persisted, and reloads the same
    assert saved["users"][RM]["extra"] == ["control"]
    assert Access.load(tmp_path / "access.json", ME, True).identify(as_(RM)).perms == rm.perms


def test_guard_rails(tmp_path):
    a = Access.load(tmp_path / "access.json", ME, enabled=True)
    me = a.identify(as_(ME))
    with pytest.raises(AccessError):
        a.put(ME, "roommate", by=me)                 # fixed superuser can't be edited
    with pytest.raises(AccessError):
        a.remove(ME, by=me)
    with pytest.raises(AccessError):
        a.put(GF, "partner", extra=["admin"], by=me)  # admin isn't grantable
    with pytest.raises(AccessError):
        a.put("not a login", "partner", by=me)
    a.put("second@x.com", "superuser", by=me)
    second = a.identify(as_("second@x.com"))
    with pytest.raises(AccessError):
        a.put("second@x.com", "roommate", by=second)  # can't demote yourself
    with pytest.raises(AccessError):
        a.remove("second@x.com", by=second)


def test_auth_none_is_full_control(tmp_path):
    u = Access.load(None, None, enabled=False).identify({})
    assert u.role == "superuser" and u.can("admin")


def test_rfc2047_names(tmp_path):
    a = Access.load(None, ME, enabled=True)
    assert a.identify(as_(ME, "=?utf-8?q?Jos=C3=A9?=")).name == "José"


# ---------------------------------------------------------------- the API, per role

@pytest.fixture
def client(tmp_path):
    ctx = Context(root=tmp_path, config={}, secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    app = create_app(ctx, auth="tailscale")
    c = TestClient(app)            # no `with`: lifespan (schedulers, network) doesn't start
    c.put(f"/api/access/{GF}", json={"role": "partner", "name": "Her"}, headers=as_(ME)).raise_for_status()
    c.put(f"/api/access/{RM}", json={"role": "roommate"}, headers=as_(ME)).raise_for_status()
    return c


def test_strangers_are_blocked(client):
    assert client.get("/", headers=as_("rando@x.com")).status_code == 403
    assert "rando@x.com" in client.get("/", headers=as_("rando@x.com")).text
    assert client.get("/").status_code == 403                      # no Tailscale identity at all
    assert client.get("/api/state").status_code == 403
    assert client.get("/api/frame.png").status_code == 403
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_partner_can_watch_and_love_tap_only(client):
    h = as_(GF)
    assert client.get("/", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).json()["role"] == "partner"
    assert client.get("/api/state", headers=h).status_code == 200
    assert client.post("/api/love", json={"message": "hi", "style": "burst"}, headers=h).status_code == 200
    assert client.post("/api/love", json={"style": "nope"}, headers=h).status_code == 400
    for method, path, body in [("post", "/api/display", {"power": "off"}), ("post", "/api/mode", {"mode": None}),
                               ("post", "/api/next", {}), ("put", "/api/modes", {}), ("get", "/api/access", None),
                               ("get", "/api/props/picks", None)]:
        r = getattr(client, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path


def test_roommate_display_only_until_granted(client):
    h = as_(RM)
    assert client.post("/api/display", json={"brightness": 40}, headers=h).status_code == 200
    assert client.post("/api/love", json={}, headers=h).status_code == 403
    assert client.post("/api/mode", json={"mode": None}, headers=h).status_code == 403
    client.put(f"/api/access/{RM}", json={"role": "roommate", "extra": ["control"]}, headers=as_(ME)).raise_for_status()
    assert client.post("/api/mode", json={"mode": None}, headers=h).status_code == 200
    assert client.put(f"/api/access/{GF}", json={"role": "roommate"}, headers=h).status_code == 403   # not an admin


def test_superuser_sees_turned_away_accounts(client):
    client.get("/", headers=as_("newphone@x.com", "New Phone"))
    blocked = client.get("/api/access", headers=as_(ME)).json()["blocked"]
    assert blocked[0]["login"] == "newphone@x.com" and blocked[0]["name"] == "New Phone"


def test_under_construction_lets_only_superusers_in(tmp_path):
    ctx = Context(root=tmp_path, config={"site": {"under_construction": True}},
                  secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    c = TestClient(create_app(ctx, auth="tailscale"))
    c.put(f"/api/access/{GF}", json={"role": "partner"}, headers=as_(ME)).raise_for_status()

    for h in (as_(GF), as_("rando@x.com"), {}):                    # partner, stranger, no identity
        r = c.get("/", headers=h)
        assert r.status_code == 503 and "under construction" in r.text.lower()
        assert c.get("/api/state", headers=h).status_code == 503
        with pytest.raises(Exception):
            with c.websocket_connect("/ws", headers=h):
                pass
    assert c.post("/api/love", json={}, headers=as_(GF)).status_code == 503

    assert c.get("/", headers=as_(ME)).status_code == 200            # the superuser gets the real dashboard
    assert "under construction" not in c.get("/", headers=as_(ME)).text.lower()
    assert c.get("/api/state", headers=as_(ME)).status_code == 200
    assert c.get("/manifest.webmanifest").status_code == 200        # Home Screen icon still works for everyone
    assert "rando@x.com" in [b["login"] for b in c.get("/api/access", headers=as_(ME)).json()["blocked"]]


# ---------------------------------------------------------------- love tap in the engine

def make_engine(tmp_path):
    ctx = Context(root=tmp_path, config={"love": {"seconds": 5}}, secrets={}, tz=TZ)
    return Engine(ctx, Store(None), ImageCache(tmp_path), geometry(ctx), modes_path=tmp_path / "modes.json")


def test_love_tap_takes_over_then_resumes(tmp_path):
    eng = make_engine(tmp_path)
    eng.render()
    before = eng.current
    assert eng.love_tap("", "rain", "Her") is False
    frame = eng.render()
    assert eng.love_active == {"message": love.DEFAULT_MESSAGE, "style": "rain", "from": "Her"}
    assert frame.size == (eng.geom.width, eng.geom.height)
    eng.skip()                                   # Next dismisses it
    eng.render()
    assert eng.love_active is None and eng.current == before


def test_love_tap_waits_while_display_off(tmp_path):
    eng = make_engine(tmp_path)
    eng.modes.display_override["power"] = "off"
    eng.render()
    assert eng.love_tap("x", None, "Her") is True
    eng.render()
    assert eng.love_active is None               # held
    eng.modes.display_override.clear()
    eng.render()
    assert eng.love_active["message"] == "x" and eng.love_active["style"] == love.DEFAULT_STYLE


def test_love_frames_every_style(tmp_path):
    long = "I love you so much, come home soon and bring snacks please"
    for style in love.STYLES:
        for t in (.2, .5, 1.9, 2.5, 7):              # (t=0 may be fully black: some styles fade in)
            for msg in ("", long):
                img = love.frame(t, msg, style)
                assert img.size == (256, 128) and img.getbbox() is not None
    lines, *_ = love.layout(long, 244, 84)
    assert " ".join(lines) == long               # wraps without dropping words


def test_app_icon_and_manifest_need_no_identity(client):
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert client.get("/icon-180.png").headers["content-type"] == "image/png"
    assert client.get("/icon-512.png").status_code == 200
    assert client.get("/icon-999.png").status_code == 404


def test_under_construction_can_stay_open_to_a_role(tmp_path):
    ctx = Context(root=tmp_path, config={"site": {"under_construction": True, "construction_open_to": ["partner"]}},
                  secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    c = TestClient(create_app(ctx, auth="tailscale"))
    c.put(f"/api/access/{GF}", json={"role": "partner"}, headers=as_(ME)).raise_for_status()
    c.put("/api/access/house@x.com", json={"role": "roommate"}, headers=as_(ME)).raise_for_status()

    assert c.get("/", headers=as_(GF)).status_code == 200            # the partner gets the real dashboard
    assert c.get("/api/state", headers=as_(GF)).status_code == 200
    assert c.get("/", headers=as_("house@x.com")).status_code == 503  # roommates still see the construction page
    assert c.get("/api/state", headers=as_("house@x.com")).status_code == 503
