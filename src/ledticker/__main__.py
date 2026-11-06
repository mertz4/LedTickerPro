"""Command line: python -m ledticker <command>

  sources              list data sources and whether they're enabled
  fetch [name ...]     fetch once and print (default: all enabled); --json for raw normalized data
  run                  run the scheduler continuously, saving to data/cache.json
  status               show health of each source from data/cache.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
    sources = _select(build_sources(ctx), args.names)
    store = Store(ctx.cache_path if args.save else None)
    store.load()
    now = ctx.now()
    for s in sources:
        on, why = s.enabled()
        store.register(s.name, s.label, on, why, s.stale_after)
        until = s.dormant_until(now) if on and not args.force else None
        store.states[s.name].dormant_until = until.isoformat() if until else None
    runnable = [s for s in sources if store.states[s.name].enabled and not store.states[s.name].dormant_until]
    async with make_client() as client:
        await asyncio.gather(*(run_once(s, store, client) for s in runnable))
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


if __name__ == "__main__":
    main()
