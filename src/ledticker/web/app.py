"""Web server: browser preview (live frames over WebSocket) + a small control API.

One process runs data collection, the image cache, the renderer and this server, all on one asyncio loop.
Bound to 127.0.0.1 by default. With --auth tailscale every request is checked against the caller's Tailscale account
(see access.py); with --auth none (local development) everyone has full control.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from ..config import Context
from ..display import geometry
from ..render import love, party
from ..render.photos import PhotoError
from ..render.engine import Engine
from ..render.images import ImageCache
from ..render.layout import LayoutError, validate_tree
from ..render.modes import ModeError, validate
from ..scheduler import Scheduler, make_client
from ..sources.base import SourceError
from ..sources.espn_props import prop_key, search_props
from ..store import Store
from .access import PERMS, Access, AccessError, User
from .push import TOPICS, Push

log = logging.getLogger("ledticker.web")
STATIC = Path(__file__).parent / "static"


class Pin(BaseModel):
    screen: str | None = None


class ModeBody(BaseModel):
    mode: str | None = None


class LayoutPreview(BaseModel):
    layout: str | None = None
    tree: dict | None = None
    ticker: bool = True


class PropPick(BaseModel):
    game_id: str
    athlete_id: int
    market: str


class DisplayBody(BaseModel):
    brightness: int | None = None
    power: str | None = None
    auto: bool = False


class LoveBody(BaseModel):
    message: str | None = None
    style: str | None = None


class PushSubscribeBody(BaseModel):
    subscription: dict
    topics: list[str] = []


class PushEndpointBody(BaseModel):
    endpoint: str


def _trade_text(tr: dict) -> str:
    sides = [f"{s.get('team_name', '?')} gets {', '.join(s.get('receives') or []) or 'nothing'}" for s in tr.get("sides") or []]
    return " · ".join(sides)[:180]


def push_contact(ctx: Context) -> str:
    """VAPID contact claim: [push] contact, else the first superuser login that is an email address."""
    c = ctx.section("push").get("contact")
    if c:
        return c if c.startswith(("mailto:", "https://")) else f"mailto:{c}"
    for s in (ctx.secret("LEDTICKER_SUPERUSERS") or "").split(","):
        s = s.strip()
        if "@" in s and "." in s.split("@")[-1]:
            return f"mailto:{s}"
    return "mailto:ledticker@localhost"


class PhotoPatch(BaseModel):
    fit: str | None = None
    name: str | None = None


class AccessBody(BaseModel):
    role: str
    extra: list[str] = []
    name: str = ""


DENIED = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>LED Ticker</title>
<style>body{{margin:0;background:#0b0c10;color:#e7e9ee;font:15px/1.5 system-ui,sans-serif;display:grid;
place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}}div{{max-width:420px}}
code{{background:#14161d;border:1px solid #262a35;border-radius:5px;padding:1px 6px}}p{{color:#8a90a0}}</style>
</head><body><div><h1>No access</h1><p>{who}</p>
<p>Ask the owner to add you on the dashboard's Access tab.</p></div></body></html>"""


class FrameHub:
    """Fan the renderer's frames out to WebSocket clients; slow clients just skip frames."""

    def __init__(self):
        self.latest: bytes = b""
        self.seq = 0
        self.event = asyncio.Event()

    def push(self, frame) -> None:
        self.latest = frame.tobytes()  # raw RGB, W*H*3
        self.seq += 1
        self.event.set()
        self.event = asyncio.Event()

    async def wait(self, after: int) -> tuple[int, bytes]:
        while self.seq <= after:
            await self.event.wait()
        return self.seq, self.latest


def create_app(ctx: Context, auth: str = "none") -> FastAPI:
    """auth: "tailscale" checks every request's Tailscale identity; "none" gives everyone full control."""
    if auth not in ("none", "tailscale"):
        raise ValueError(f"unknown auth mode {auth}")
    access = Access.load(ctx.root / "data" / "access.json", ctx.secret("LEDTICKER_SUPERUSERS"), auth == "tailscale")
    if access.enabled and not access.superusers:
        log.warning("no LEDTICKER_SUPERUSERS set in secrets.env: nobody can manage access")

    def need(perm: str):
        def check(request: Request) -> User:
            user = access.identify(request.headers)
            if user is None:
                raise HTTPException(403, "your Tailscale account doesn't have access to this display")
            if not user.can(perm):
                raise HTTPException(403, f"you don't have permission for: {PERMS[perm].lower()}")
            return user
        return Depends(check)

    push = Push.load(ctx.root / "data", push_contact(ctx))
    store = Store(ctx.cache_path)
    store.load()
    sched = Scheduler(ctx, store)
    images = ImageCache(ctx.root)
    geom = geometry(ctx)
    engine = Engine(ctx, store, images, geom)
    hub = FrameHub()

    def sync_trade_poll() -> None:
        """The trade listener's poll rate lives in the dashboard's Settings."""
        src = sched.source("fantasy_trades")
        if src is not None:
            src.poll_seconds = float(engine.modes.breaking().get("poll_seconds") or 30)

    sync_trade_poll()

    def notify(topic: str, title: str, body: str, login: str | None = None, skip: str | None = None) -> None:
        """Fire-and-forget push from inside the render loop (no loop running = tests/snapshots: skip)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        may = (lambda lg, perm: access.may(lg, perm) and lg != skip)
        loop.create_task(push.send(topic, title, body, may, login=login))

    def on_event(kind: str, data: dict) -> None:
        if kind == "trade":
            notify("trades", "Trade went through", _trade_text(data))
        elif kind == "trade_pending":
            notify("trades", "Trade accepted (in review)", _trade_text(data))
        elif kind == "weather":
            notify("weather", data.get("event") or "Weather alert", (data.get("headline") or "")[:180])

    engine.on_event = on_event

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.subscribe(hub.push)
        tasks = [asyncio.create_task(c, name=n) for n, c in
                 (("scheduler", sched.run()), ("images", images.run()), ("renderer", engine.run()))]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="LED Ticker", lifespan=lifespan)
    app.state.push, app.state.engine, app.state.access = push, engine, access

    @app.get("/")
    async def index(request: Request):
        user = access.identify(request.headers)
        if user is None:
            login = request.headers.get("tailscale-user-login")
            who = (f"You're signed in to Tailscale as <code>{html.escape(login)}</code>, which isn't on the list."
                   if login else "This page has to be opened through Tailscale.")
            return HTMLResponse(DENIED.format(who=who), status_code=403, headers={"Cache-Control": "no-store"})
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    # app icon + manifest for "Add to Home Screen": no identity needed (iOS fetches them itself; nothing private)
    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icon-{size}.png")
    async def icon(size: int):
        if size not in (180, 512):
            raise HTTPException(404)
        return FileResponse(STATIC / f"icon-{size}.png", media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/sw.js")
    async def service_worker():
        """Service worker for push notifications. Served from the root so it controls the whole app."""
        return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    # ---- push notifications (each person manages their own devices)
    def my_topics(user: User) -> dict[str, str]:
        return {k: label for k, (label, perm) in TOPICS.items() if user.can(perm)}

    @app.get("/api/push")
    async def push_info(user: User = need("view")):
        try:
            key = push.public_key()
        except Exception as e:   # pywebpush / cryptography missing or broken
            raise HTTPException(503, f"notifications aren't available on this server: {e}")
        return {"public_key": key, "topics": my_topics(user), "mine": push.mine(user.login)}

    @app.post("/api/push/subscribe")
    async def push_subscribe(body: PushSubscribeBody, user: User = need("view")):
        try:
            push.subscribe(user.login, body.subscription, body.topics, set(my_topics(user)))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"mine": push.mine(user.login)}

    @app.post("/api/push/unsubscribe")
    async def push_unsubscribe(body: PushEndpointBody, user: User = need("view")):
        push.unsubscribe(body.endpoint, user.login)
        return {"mine": push.mine(user.login)}

    @app.post("/api/push/test")
    async def push_test(user: User = need("view")):
        sent = await push.send("test", "LED Ticker", "Notifications are working.", access.may, login=user.login)
        if not sent:
            raise HTTPException(404, "no notification-enabled devices for your account yet")
        return {"sent": sent}

    @app.get("/api/me")
    async def me(user: User = need("view")):
        return {**user.as_dict(), "auth": auth, "perm_labels": PERMS}

    # ---- access list (superusers)
    @app.get("/api/access", dependencies=[need("admin")])
    async def access_list():
        return access.listing()

    @app.put("/api/access/{login}")
    async def access_put(login: str, body: AccessBody, user: User = need("admin")):
        try:
            access.put(login, body.role, body.extra, body.name, by=user)
        except AccessError as e:
            raise HTTPException(400, str(e))
        return access.listing()

    @app.delete("/api/access/{login}")
    async def access_delete(login: str, user: User = need("admin")):
        try:
            access.remove(login, by=user)
        except AccessError as e:
            raise HTTPException(400, str(e))
        except KeyError:
            raise HTTPException(404, "not on the list")
        return access.listing()

    # ---- love tap
    @app.post("/api/love")
    async def love_tap(body: LoveBody, user: User = need("love_tap")):
        if body.style is not None and body.style not in love.STYLES:
            raise HTTPException(400, f"unknown style {body.style}")
        held = engine.love_tap(body.message, body.style, user.name)
        msg = " ".join((body.message or "").split())[:love.MAX_MESSAGE] or engine.love_default_message
        notify("love", f"Love tap from {user.name}", f"“{msg}”" + (" (waiting: the display is off)" if held else ""),
               skip=user.login)
        return {"held": held}

    @app.get("/api/state", dependencies=[need("view")])
    async def state():
        return {
            "love": engine.love_active,
            "love_styles": love.STYLES,
            "love_default": {"message": engine.love_default_message, "style": engine.love_default_style,
                             "seconds": engine.love_seconds, "max": love.MAX_MESSAGE},
            "geometry": geom.as_dict(),
            "current": engine.current,
            "pinned": engine.pinned,
            "playlist": engine.playlist,
            "available": engine.available(),
            "screens": {n: s.label for n, s in engine.screens.items()},
            "widgets": {n: w.label for n, w in engine.widgets.items() if not getattr(w.full, "hidden", False)},
            "breaking": engine.modes.breaking(),
            "theme_active": engine.modes.theme_active(datetime.now(ctx.tz)),
            "party": {"bpm": round(party.CLOCK.bpm, 1), "tapped": party.CLOCK.tapped,
                      "scenes": {"party": "Auto DJ show", **party.SCENES},
                      "on": bool(engine.pinned and engine.pinned.startswith("party"))},
            "available_tiles": engine.available_tiles(),
            "mode": engine.mode_name,
            "mode_reason": engine.mode_reason,
            "mode_override": engine.modes.override,
            "modes": {n: m.get("label", n) for n, m in engine.modes.doc["modes"].items()},
            "layouts": {n: lay.get("label", n) for n, lay in (engine.modes.doc.get("layouts") or {}).items()},
            "previewing_layout": engine.pinned_slide is not None,
            "display": engine.display_state,
            "display_manual": engine.modes.display_override,
            "fps": engine.fps,
            "sources": [{
                "name": n, "label": s.label, "health": s.health(), "updated_at": s.updated_at,
                "error": s.last_error, "dormant_until": s.dormant_until,
                "disabled_reason": s.disabled_reason if not s.enabled else None,
            } for n, s in sorted(store.states.items())],
        }

    @app.post("/api/pin", dependencies=[need("control")])
    async def pin(body: Pin):
        try:
            engine.pin(body.screen)
        except KeyError:
            raise HTTPException(404, f"unknown screen {body.screen}")
        return {"pinned": engine.pinned}

    @app.post("/api/next", dependencies=[need("control")])
    async def next_screen():
        engine.pinned, engine.pinned_slide = None, None
        engine.skip()
        return {"current": engine.current}

    @app.post("/api/preview_layout", dependencies=[need("control")])
    async def preview_layout(body: LayoutPreview):
        """Show a saved layout, or the unsaved one being edited, on the display (null = back to rotation)."""
        if body.layout is None and body.tree is None:
            engine.pin_layout(None)
            return {"previewing": False}
        if body.layout is not None and engine.modes.layout_tree(body.layout) is None:
            raise HTTPException(404, f"unknown layout {body.layout}")
        if body.tree is not None:
            try:
                validate_tree(body.tree, set(engine.widgets))
            except LayoutError as e:
                raise HTTPException(400, str(e))
        slide = {"layout": body.layout} if body.layout is not None else {"tree": body.tree}
        slide["ticker"] = body.ticker
        engine.pin_layout(slide)
        return {"previewing": True}

    @app.post("/api/breaking/test", dependencies=[need("edit")])
    async def breaking_test(pending: bool = False):
        """Play the breaking-trade takeover now with the latest trade (?pending=true: the under-review version)."""
        try:
            tr = engine.breaking_test(pending)
        except LookupError as e:
            raise HTTPException(404, str(e))
        return {"trade": tr["id"], "held": engine._dark() and engine.modes.breaking().get("hold_while_off", True)}

    # ---- player props: search this week's lines and pin picks to the props widgets
    def props_source():
        src = sched.source("props")
        if src is None or not store.states.get("props") or not store.states["props"].enabled:
            raise HTTPException(503, "player props are turned off ([props] enabled in config.toml)")
        return src

    async def catalog(src):
        try:
            async with make_client() as client:
                return await src.catalog(client)
        except SourceError as e:
            raise HTTPException(502, f"couldn't load prop lines from ESPN: {e}")

    def picks(src) -> list[dict]:
        out = []
        for k, e in src.watch.entries.items():
            live = next((p for p in (store.get("props") or {}).get("props") or []
                         if prop_key(p["game_id"], p["athlete_id"], p["market"]) == k), None)
            out.append({**e, "key": k, "live": live.get("live") if live else None,
                        "line": live["line"] if live else e.get("line")})
        return sorted(out, key=lambda e: (e.get("start_utc") or "", e.get("name") or ""))

    @app.get("/api/props/search", dependencies=[need("props")])
    async def props_search(q: str = ""):
        src = props_source()
        if len(q.strip()) < 2:
            return {"results": [], "picks": picks(src)}
        hits = search_props(await catalog(src), q)
        return {"results": [{"key": prop_key(p.game_id, p.athlete_id, p.market), "game_id": p.game_id,
                             "athlete_id": p.athlete_id, "name": p.name, "team": p.team, "position": p.position,
                             "market": p.market, "line": p.line, "move": p.move, "game": p.game,
                             "picked": prop_key(p.game_id, p.athlete_id, p.market) in src.watch.entries}
                            for p in hits], "picks": picks(src)}

    @app.get("/api/props/picks", dependencies=[need("props")])
    async def props_picks():
        return {"picks": picks(props_source())}

    @app.post("/api/props/picks", dependencies=[need("props")])
    async def props_add(body: PropPick):
        src = props_source()
        k = prop_key(body.game_id, body.athlete_id, body.market)
        hit = next((p for p in await catalog(src) if prop_key(p.game_id, p.athlete_id, p.market) == k), None)
        if hit is None:
            raise HTTPException(404, "that prop line isn't posted any more")
        src.watch.add(vars(hit), src.start_of(hit.game_id))
        sched.refresh("props")
        return {"picks": picks(src)}

    @app.delete("/api/props/picks/{key}", dependencies=[need("props")])
    async def props_remove(key: str):
        src = props_source()
        if not src.watch.remove(key):
            raise HTTPException(404, "not in your picks")
        sched.refresh("props")
        return {"picks": picks(src)}

    @app.post("/api/display", dependencies=[need("display")])
    async def display(body: DisplayBody):
        """Manual brightness / power. auto=true goes back to the schedule."""
        ov = engine.modes.display_override
        if body.auto:
            ov.clear()
        if body.brightness is not None:
            if not 0 <= body.brightness <= 100:
                raise HTTPException(400, "brightness must be 0-100")
            ov["brightness"] = body.brightness
        if body.power is not None:
            if body.power not in ("on", "off"):
                raise HTTPException(400, "power must be on or off")
            ov["power"] = body.power
        return {"manual": ov}

    # ---- modes: saved custom layouts + schedule
    @app.get("/api/modes", dependencies=[need("view")])
    async def get_modes():
        return engine.modes.doc

    @app.put("/api/modes", dependencies=[need("edit")])
    async def put_modes(doc: dict):
        doc.setdefault("version", 2)
        try:
            validate(doc, set(engine.widgets))
        except ModeError as e:
            raise HTTPException(400, str(e))
        engine.modes.save(doc)
        engine.mode_name = None   # re-evaluate the schedule with the new document
        sync_trade_poll()
        sched.refresh("fantasy_trades")
        if engine.pinned_slide and engine.pinned_slide.get("layout") not in (None, *doc.get("layouts", {})):
            engine.pin_layout(None)
        return {"ok": True}

    @app.post("/api/mode", dependencies=[need("control")])
    async def set_mode(body: ModeBody):
        try:
            engine.set_mode(body.mode)
        except KeyError:
            raise HTTPException(404, f"unknown mode {body.mode}")
        engine.pinned, engine.pinned_slide = None, None
        return {"override": engine.modes.override}

    # ---- pictures: upload from the dashboard (raw body, the browser sends the file as-is), show, fit, delete
    def photo_json(p):
        return {"id": p.id, "name": p.name, "fit": p.fit, "animated": p.animated, "added_by": p.added_by,
                "added_at": p.added_at}

    def photo_list():
        lib = engine.photos
        return {"photos": [photo_json(p) for p in lib.list()], "focus": lib.focus,
                "showing": engine.pinned == "photos"}

    @app.get("/api/photos", dependencies=[need("view")])
    async def photos_list():
        return photo_list()

    @app.get("/api/photos/{pid}/preview.png", dependencies=[need("view")])
    async def photo_preview(pid: str):
        if pid not in engine.photos.photos:
            raise HTTPException(404, "no such picture")
        im = engine.photos.picture(pid, (geom.width, geom.height), 0.0)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/photos")
    async def photo_upload(request: Request, name: str = "", fit: str = "fill", user: User = need("media")):
        data = await request.body()
        try:
            p = await asyncio.to_thread(engine.photos.add, data, name, fit, user.name)
        except PhotoError as e:
            raise HTTPException(400, str(e))
        return {"photo": photo_json(p), **photo_list()}

    @app.patch("/api/photos/{pid}", dependencies=[need("media")])
    async def photo_patch(pid: str, body: PhotoPatch):
        try:
            engine.photos.update(pid, body.fit, body.name)
        except KeyError:
            raise HTTPException(404, "no such picture")
        except PhotoError as e:
            raise HTTPException(400, str(e))
        return photo_list()

    @app.delete("/api/photos/{pid}", dependencies=[need("media")])
    async def photo_delete(pid: str):
        try:
            engine.photos.remove(pid)
        except KeyError:
            raise HTTPException(404, "no such picture")
        if engine.pinned == "photos" and not engine.photos.photos:
            engine.pin(None)
        return photo_list()

    @app.post("/api/photos/show", dependencies=[need("media")])
    async def photo_show(pid: str | None = None):
        """Show one picture full screen (?pid=...), or the slideshow of all of them (no pid)."""
        if pid is not None and pid not in engine.photos.photos:
            raise HTTPException(404, "no such picture")
        if not engine.photos.photos:
            raise HTTPException(404, "upload a picture first")
        engine.photos.focus = pid
        engine.pin("photos")
        return photo_list()

    @app.post("/api/photos/stop", dependencies=[need("media")])
    async def photo_stop():
        engine.photos.focus = None
        if engine.pinned == "photos":
            engine.pin(None)
        return photo_list()

    # ---- pregame: tap tempo (the show itself is the "party" widgets, pinned like any widget)
    @app.post("/api/party/tap", dependencies=[need("control")])
    async def party_tap():
        return {"bpm": round(party.CLOCK.tap(), 1), "tapped": party.CLOCK.tapped}

    @app.post("/api/party/reset", dependencies=[need("control")])
    async def party_reset():
        party.CLOCK.reset()
        party.CLOCK.configure(engine.modes.party())
        return {"bpm": round(party.CLOCK.bpm, 1), "tapped": False}

    @app.get("/api/frame.png", dependencies=[need("view")])
    async def frame_png():
        frame = engine.last_frame or engine.render()
        buf = io.BytesIO()
        frame.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        user = access.identify(sock.headers)
        if user is None or not user.can("view"):
            await sock.close(code=1008)   # policy violation; before accept this is an HTTP 403
            return
        await sock.accept()
        try:
            fps = float(sock.query_params.get("fps", engine.fps))
        except ValueError:
            fps = engine.fps
        # at or above the renderer's rate: send every frame (no throttle, so timer jitter can't drop frames)
        period = 0.0 if fps >= engine.fps else 1.0 / max(1.0, fps)
        await sock.send_json({"type": "hello", "geometry": geom.as_dict()})
        seq, last_sent = 0, 0.0
        try:
            while True:
                seq, data = await hub.wait(seq)
                now = time.monotonic()
                if now - last_sent < period * 0.9:
                    continue
                last_sent = now
                await sock.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app
