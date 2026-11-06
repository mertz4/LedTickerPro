"""Web server: browser preview (live frames over WebSocket) + a small control API.

One process runs data collection, the image cache, the renderer and this server, all on one asyncio loop.
Bound to 127.0.0.1 by default. There is no login yet, so only use --host 0.0.0.0 on a trusted home network.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..config import Context
from ..display import geometry
from ..render.engine import Engine
from ..render.images import ImageCache
from ..render.layout import LayoutError, validate_tree
from ..render.modes import ModeError, validate
from ..scheduler import Scheduler, make_client
from ..sources.base import SourceError
from ..sources.espn_props import prop_key, search_props
from ..store import Store

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


def create_app(ctx: Context) -> FastAPI:
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

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    async def state():
        return {
            "geometry": geom.as_dict(),
            "current": engine.current,
            "pinned": engine.pinned,
            "playlist": engine.playlist,
            "available": engine.available(),
            "screens": {n: s.label for n, s in engine.screens.items()},
            "widgets": {n: w.label for n, w in engine.widgets.items() if not getattr(w.full, "hidden", False)},
            "breaking": engine.modes.breaking(),
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

    @app.post("/api/pin")
    async def pin(body: Pin):
        try:
            engine.pin(body.screen)
        except KeyError:
            raise HTTPException(404, f"unknown screen {body.screen}")
        return {"pinned": engine.pinned}

    @app.post("/api/next")
    async def next_screen():
        engine.pinned, engine.pinned_slide = None, None
        engine.skip()
        return {"current": engine.current}

    @app.post("/api/preview_layout")
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

    @app.post("/api/breaking/test")
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

    @app.get("/api/props/search")
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

    @app.get("/api/props/picks")
    async def props_picks():
        return {"picks": picks(props_source())}

    @app.post("/api/props/picks")
    async def props_add(body: PropPick):
        src = props_source()
        k = prop_key(body.game_id, body.athlete_id, body.market)
        hit = next((p for p in await catalog(src) if prop_key(p.game_id, p.athlete_id, p.market) == k), None)
        if hit is None:
            raise HTTPException(404, "that prop line isn't posted any more")
        src.watch.add(vars(hit), src.start_of(hit.game_id))
        sched.refresh("props")
        return {"picks": picks(src)}

    @app.delete("/api/props/picks/{key}")
    async def props_remove(key: str):
        src = props_source()
        if not src.watch.remove(key):
            raise HTTPException(404, "not in your picks")
        sched.refresh("props")
        return {"picks": picks(src)}

    @app.post("/api/display")
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
    @app.get("/api/modes")
    async def get_modes():
        return engine.modes.doc

    @app.put("/api/modes")
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

    @app.post("/api/mode")
    async def set_mode(body: ModeBody):
        try:
            engine.set_mode(body.mode)
        except KeyError:
            raise HTTPException(404, f"unknown mode {body.mode}")
        engine.pinned, engine.pinned_slide = None, None
        return {"override": engine.modes.override}

    @app.get("/api/frame.png")
    async def frame_png():
        frame = engine.last_frame or engine.render()
        buf = io.BytesIO()
        frame.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
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
