"""Command line: python -m ledticker <command>

  sources              list data sources and whether they're enabled
  fetch [name ...]     fetch once and print (default: all enabled); --json for raw normalized data
  run                  run the scheduler continuously, saving to data/cache.json
  status               show health of each source from data/cache.json
  snapshot             render screens to PNG
  preview              data + renderer + dashboard (+ the LED panels when [matrix] enabled = true)
  matrix-test          test pattern on the real LED panels (Pi only; stop the service first)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

from . import config
from .scheduler import Scheduler, make_client, run_once
from .sources import build_sources
from .store import Store, to_jsonable, utcnow


def _select(sources, names):
    if not names:
        return sources
    out = [s for s in sources if any(s.name == n or s.name.startswith(n + ".") for n in names)]
    unknown = [n for n in names if not any(s.name == n or s.name.startswith(n + ".") for s in sources)]
    if unknown:
        sys.exit(f"unknown source(s): {', '.join(unknown)}. Try: python -m ledticker sources")
    return out


def cmd_sources(ctx, args):
    now = ctx.now()
    for s in build_sources(ctx):
        on, why = s.enabled()
        until = s.dormant_until(now) if on else None
        flag = "off " if not on else "wait" if until else "on  "
        if until:
            why = f"out of season until {until:%b %d, %Y}"
        print(f"  {flag} {s.name:<16} {s.label:<24} {why}")


async def cmd_fetch(ctx, args):
    store = Store(ctx.cache_path if args.save else None)
    store.load()
    sources = _select(build_sources(ctx, store), args.names)
    now = ctx.now()
    for s in sources:
        on, why = s.enabled()
        store.register(s.name, s.label, on, why, s.stale_after)
        until = s.dormant_until(now) if on and not args.force else None
        store.states[s.name].dormant_until = until.isoformat() if until else None
    runnable = [s for s in sources if store.states[s.name].enabled and not store.states[s.name].dormant_until]
    async with make_client() as client:
        # props reads my fantasy roster from the store, so it goes after the others
        await asyncio.gather(*(run_once(s, store, client) for s in runnable if s.name != "props"))
        await asyncio.gather(*(run_once(s, store, client) for s in runnable if s.name == "props"))
    out = {}
    for s in sources:
        st = store.states[s.name]
        if args.json:
            out[s.name] = {"health": st.health(), "error": st.last_error, "data": st.data}
            continue
        print(f"=== {s.name} [{st.health()}]")
        if not st.enabled:
            print(f"  disabled: {st.disabled_reason}\n")
            continue
        if st.dormant_until:
            print(f"  out of season until {st.dormant_until[:10]} (use --force to fetch anyway)\n")
            continue
        if st.last_error:
            print(f"  ERROR: {st.last_error}")
        if st.data is not None and not st.last_error:
            for line in s.summarize(st.data):
                print("  " + line)
        print()
    if args.json:
        print(json.dumps(out, indent=2, default=str))


def cmd_status(ctx, args):
    store = Store(ctx.cache_path)
    store.load()
    if not store.states:
        print(f"no cache yet at {ctx.cache_path}; run: python -m ledticker run")
        return
    now = utcnow()
    for name, st in sorted(store.states.items()):
        age = ""
        if st.updated_at:
            secs = int((now - datetime.fromisoformat(st.updated_at)).total_seconds())
            age = f"updated {secs // 60}m{secs % 60:02d}s ago"
        note = st.disabled_reason if not st.enabled else (st.last_error or "")
        if st.enabled and st.dormant_until:
            note = f"out of season until {st.dormant_until[:10]}"
        print(f"  {st.health():<10} {name:<16} {age:<20} {note}")


async def cmd_run(ctx, args):
    store = Store(ctx.cache_path)
    store.load()
    sched = Scheduler(ctx, store)
    if args.verbose:
        def show(name, st):
            if st.last_error:
                return
            src = next(s for s in sched.sources if s.name == name)
            print(f"--- {name} @ {datetime.now():%H:%M:%S}")
            for line in src.summarize(st.data)[:6]:
                print("   " + line)
        store.subscribe(show)
    await sched.run()


async def cmd_snapshot(ctx, args):
    """Render screens to PNG files (LED look), using cached data or a fresh fetch."""
    from pathlib import Path

    from .display import geometry
    from .render.engine import Engine
    from .render.images import ImageCache
    from .render.preview_image import image_urls, led_look

    store = Store(ctx.cache_path)
    store.load()
    if args.refresh or not store.states:
        sources = build_sources(ctx, store)
        for s in sources:
            on, why = s.enabled()
            store.register(s.name, s.label, on, why, s.stale_after)
        run = [s for s in sources if store.states[s.name].enabled and not s.dormant_until(ctx.now())]
        async with make_client() as client:
            await asyncio.gather(*(run_once(s, store, client) for s in run if s.name != "props"))
            await asyncio.gather(*(run_once(s, store, client) for s in run if s.name == "props"))
    images = ImageCache(ctx.root)
    await images.prefetch(image_urls(store))
    geom = geometry(ctx)
    eng = Engine(ctx, store, images, geom)
    names = args.screens or eng.available()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n in names:
        if n.startswith("quad:"):
            bad = [x for x in n[5:].split(",") if x and x not in eng.widgets]
            if bad:
                print(f"unknown widget(s) {', '.join(bad)}; widgets: {', '.join(eng.widgets)}")
                continue
        elif n.startswith("layout:"):
            if eng.modes.layout_tree(n[7:]) is None:
                print(f"unknown layout {n[7:]}; layouts: {', '.join(eng.modes.doc.get('layouts') or {})}")
                continue
        elif n not in eng.widgets:
            print(f"unknown widget {n}; widgets: {', '.join(eng.widgets)}  (or layout:<name>, quad:a,b,c,d)")
            continue
        for _ in range(2):  # first pass queues any missing logos; second uses them
            frame = eng.render(t=args.t, screen=n)
        path = out / (n.replace(":", "_").replace(",", "-") + ".png")
        (led_look(frame, geom, args.scale) if args.scale > 1 else frame).save(path)
        print(f"  {n:<16} -> {path}")


def cmd_preview(ctx, args):
    import uvicorn

    from .web.app import create_app
    if args.auth == "tailscale" and args.host not in ("127.0.0.1", "localhost", "::1"):
        # identity headers are only trustworthy when `tailscale serve` is the only way in
        sys.exit("--auth tailscale needs --host 127.0.0.1 (publish it with `tailscale serve`), "
                 "otherwise anyone who can reach the port could fake a login")
    if args.auth == "tailscale" and not ctx.secret("LEDTICKER_SUPERUSERS"):
        print("  WARNING: LEDTICKER_SUPERUSERS isn't set in secrets.env, so nobody can manage access.")
    # The LED panels need root to start; open them first, then become the normal user before anything is written.
    from .display import geometry
    from .matrix import drop_root, open_matrix
    matrix = open_matrix(ctx, geometry(ctx))
    drop_root(os.environ.get("LEDTICKER_RUN_AS"))
    print(f"LED preview: http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}")
    if args.host == "0.0.0.0":
        print("  (listening on all interfaces: open http://<this-computer's-IP>:%d on your phone)" % args.port)
    uvicorn.run(create_app(ctx, auth=args.auth, matrix=matrix), host=args.host, port=args.port, log_level="warning")


def cmd_matrix_test(ctx, args):
    """Bring-up: labeled quarters, then solid colors, on the real panels. Stop the service first."""
    import time

    from .display import geometry
    from .matrix import open_matrix, test_pattern
    out = open_matrix(ctx, geometry(ctx), force=True)
    if out is None:
        sys.exit("couldn't open the panels (see the error above). Stop the service first: sudo systemctl stop ledticker")
    print(f"Panels {out.size[0]}x{out.size[1]} at {args.brightness}% brightness. Ctrl+C to stop.")
    print("Phase order: labeled quarters, red, green, blue, white. Check each label shows where it says.")
    phase = 0
    try:
        while True:
            out.show(test_pattern(out.size, phase, args.brightness))
            time.sleep(args.seconds * (2 if phase % 5 == 0 else 1))
            phase += 1
    except KeyboardInterrupt:
        pass
    finally:
        out.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="ledticker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", help="project folder containing config.toml and secrets.env (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    f = sub.add_parser("fetch")
    f.add_argument("names", nargs="*", help="e.g. scores.nfl, scores (all leagues), fantasy, weather")
    f.add_argument("--json", action="store_true")
    f.add_argument("--save", action="store_true", help="also write results to the cache file")
    f.add_argument("--force", action="store_true", help="fetch even if out of season")
    r = sub.add_parser("run")
    r.add_argument("-v", "--verbose", action="store_true", help="print each update")
    sub.add_parser("status")
    sn = sub.add_parser("snapshot", help="render screens to PNG")
    sn.add_argument("screens", nargs="*", help="screen names (default: all with data)")
    sn.add_argument("-o", "--out", default="data/snapshots")
    sn.add_argument("--scale", type=int, default=4, help="LED-look magnification (1 = raw pixels)")
    sn.add_argument("--refresh", action="store_true", help="fetch fresh data first")
    sn.add_argument("-t", type=float, default=0.0, help="animation time in seconds")
    pv = sub.add_parser("preview", help="run data collection + renderer + browser preview")
    pv.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to allow your phone on the same Wi-Fi")
    pv.add_argument("--port", type=int, default=8000)
    pv.add_argument("--auth", choices=("none", "tailscale"), default="none",
                    help="tailscale: per-account roles from Tailscale identity (the Pi). none: full control (dev)")
    mt = sub.add_parser("matrix-test", help="test pattern on the real LED panels (Pi, root, service stopped)")
    mt.add_argument("--brightness", type=int, default=30, help="percent; kept low by default for bring-up")
    mt.add_argument("--seconds", type=float, default=3.0, help="how long each color shows")
    args = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO if args.cmd == "run" else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ctx = config.load(args.home)

    if args.cmd == "sources":
        cmd_sources(ctx, args)
    elif args.cmd == "fetch":
        asyncio.run(cmd_fetch(ctx, args))
    elif args.cmd == "status":
        cmd_status(ctx, args)
    elif args.cmd == "run":
        try:
            asyncio.run(cmd_run(ctx, args))
        except KeyboardInterrupt:
            print("stopped")
    elif args.cmd == "snapshot":
        asyncio.run(cmd_snapshot(ctx, args))
    elif args.cmd == "preview":
        cmd_preview(ctx, args)
    elif args.cmd == "matrix-test":
        cmd_matrix_test(ctx, args)


if __name__ == "__main__":
    main()
