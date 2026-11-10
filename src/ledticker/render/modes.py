"""Saved layouts, modes (playlists of slides) and the schedule (timer / calendar) that also controls brightness and
power. Everything lives in data/modes.json and is edited from the dashboard.

    {
      "version": 2,
      "display_default": {"brightness": 100, "power": "on"},
      "layouts": {                       # saved custom layouts (split trees, see layout.py)
        "game_over_two": {"label": "Game on top, weather + stocks below",
                          "tree": {"split": "rows", "at": 64, "a": {"widget": "nfl_game"},
                                   "b": {"split": "cols", "at": 128, "a": {"widget": "weather"},
                                         "b": {"widget": "markets"}}}}
      },
      "modes": {                         # playlists; a slide is {"layout": name} or {"widget": name}
        "weeknight": {"label": "Weeknight", "ticker": true,
                      "slides": [{"layout": "game_over_two", "seconds": 30}, {"widget": "fantasy"}]}
      },
      "default": "weeknight",
      "breaking": {"trades": true, "seconds": 30},   # full-display takeover when a new league trade lands
      "schedule": [                      # first matching rule wins, per setting (mode / brightness / power)
        {"days": ["sun"], "mode": "sunday"},
        {"dates": ["2026-11-26"], "mode": "sweep"},                     # a specific calendar day
        {"date_from": "2026-12-20", "date_to": "2027-01-02", "mode": "sweep"},
        {"days": ["mon","tue","wed","thu","fri","sat","sun"], "from": "23:00", "to": "07:00", "brightness": 20},
        {"days": ["mon","tue","wed","thu","fri"], "from": "01:00", "to": "06:30", "power": "off"}
      ]
    }
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

from .layout import LayoutError, quad_tree, validate_tree

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAYS = list(DAYS)


def _quad(a, b, c, d):
    return quad_tree([a, b, c, d], 2, 2, 128, 64)


DEFAULT_MODES: dict[str, Any] = {
    "version": 2,
    "display_default": {"brightness": 100, "power": "on"},
    "layouts": {
        "four_up_weeknight": {"label": "4-up: weather, stocks, NFL game, fantasy",
                              "tree": _quad("weather", "markets", "nfl_game", "fantasy_mini")},
        "four_up_props": {"label": "4-up: weather, stocks, props, standings",
                          "tree": _quad("weather", "markets", "props_mini", "standings_mini")},
        "four_up_sunday": {"label": "4-up: fantasy, NFL game, props, standings",
                           "tree": _quad("fantasy_mini", "nfl_game", "props_mini", "standings_mini")},
        "game_top_split_bottom": {"label": "NFL game across the top, weather + stocks below",
                                  "tree": {"split": "rows", "at": 64, "a": {"widget": "nfl_game"},
                                           "b": {"split": "cols", "at": 128, "a": {"widget": "weather"},
                                                 "b": {"widget": "markets"}}}},
        "fantasy_left_stack_right": {"label": "Fantasy on the left, game + props stacked right",
                                     "tree": {"split": "cols", "at": 128, "a": {"widget": "fantasy"},
                                              "b": {"split": "rows", "at": 58, "a": {"widget": "nfl_game"},
                                                    "b": {"widget": "props"}}}},
    },
    "modes": {
        "sweep": {
            "label": "Sweep (everything)", "ticker": True,
            "slides": [{"widget": w} for w in (
                "alert", "game", "fantasy", "weather", "board.cfb", "fantasy_scores", "fantasy_standings",
                "markets", "board.nfl", "props", "fantasy_potw")] + [{"layout": "game_top_split_bottom"}],
        },
        "sunday": {
            "label": "NFL Sunday", "ticker": True,
            "slides": [{"widget": "alert"}, {"widget": "fantasy", "seconds": 15},
                       {"widget": "board.nfl"}, {"widget": "fantasy_scores"}, {"widget": "props"},
                       {"widget": "game"}, {"layout": "four_up_sunday", "seconds": 20},
                       {"layout": "fantasy_left_stack_right", "seconds": 20}, {"widget": "fantasy_potw"}],
        },
        "weeknight": {
            "label": "Weeknight (Mon/Tue)", "ticker": True,
            "slides": [{"widget": "alert"},
                       {"layout": "four_up_weeknight", "seconds": 30},
                       {"layout": "four_up_props", "seconds": 20},
                       {"layout": "game_top_split_bottom", "seconds": 20}],
        },
    },
    "default": "sweep",
    "breaking": {"trades": True, "seconds": 30, "pending": True, "pending_seconds": 15, "hold_while_off": True,
                 "poll_seconds": 30,
                 # big plays: my fantasy players (fireworks), my NFL team scoring (its own theme), and the stinker
                 # for rival owners' players / rival NFL teams scoring
                 "big_plays": True, "team_scores": True, "rival_plays": True, "rival_scores": True,
                 "play_seconds": 20, "min_yards": 20, "min_points": 5, "touchdowns": True, "bench": False,
                 "play_poll_seconds": 15},
    # seasonal overlay on every frame. name: "off" | "christmas"; season_only: just in December
    "theme": {"name": "off", "season_only": True, "garland": True, "snow": True, "snow_amount": 2},
    # pregame / party light show: default tempo (tap tempo overrides it live), the drop message, opt-in strobe
    "party": {"bpm": 124, "message": "PREGAME", "strobe": False},
    "schedule": [
        {"days": ["sun"], "mode": "sunday"},
        {"days": ["mon", "tue"], "mode": "weeknight"},
        {"days": ALL_DAYS, "from": "23:00", "to": "07:00", "brightness": 25},
    ],
}


def _hm(s: str | None, default: dtime) -> dtime:
    if not s:
        return default
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _date(s: str) -> date:
    return date.fromisoformat(s)


class ModeError(ValueError):
    pass


def rule_matches(rule: dict, now: datetime) -> bool:
    d = now.date()
    if rule.get("dates"):
        if d.isoformat() not in rule["dates"]:
            # an overnight window that started on a listed date still counts after midnight
            if not (_hm(rule.get("from"), dtime.min) > _hm(rule.get("to"), dtime.max)
                    and date.fromordinal(d.toordinal() - 1).isoformat() in rule["dates"]
                    and now.time() <= _hm(rule.get("to"), dtime.max)):
                return False
    if rule.get("date_from") and d < _date(rule["date_from"]):
        return False
    if rule.get("date_to") and d > _date(rule["date_to"]):
        return False
    start, end = _hm(rule.get("from"), dtime.min), _hm(rule.get("to"), dtime.max)
    t = now.time()
    if start <= end:
        in_time = start <= t <= end
        day_ok = DAYS[now.weekday()]
    else:  # overnight window, e.g. 23:00-07:00: the early-morning part belongs to the previous day's rule
        in_time = t >= start or t <= end
        day_ok = DAYS[now.weekday()] if t >= start else DAYS[(now.weekday() - 1) % 7]
    if rule.get("days") and day_ok not in rule["days"]:
        return False
    return in_time


def _describe(rule: dict) -> str:
    parts = []
    if rule.get("dates"):
        parts.append(", ".join(rule["dates"][:3]) + ("…" if len(rule["dates"]) > 3 else ""))
    if rule.get("date_from") or rule.get("date_to"):
        parts.append(f"{rule.get('date_from', '…')} to {rule.get('date_to', '…')}")
    if rule.get("days") and sorted(rule["days"]) != sorted(ALL_DAYS):
        parts.append("/".join(d.title() for d in rule["days"]))
    if rule.get("from") or rule.get("to"):
        parts.append(f"{rule.get('from', '00:00')}-{rule.get('to', '23:59')}")
    return "schedule: " + (" ".join(parts) or "always")


THEMES = ("off", "christmas")


def validate(doc: Any, widgets: set[str], tiles: set[str] | None = None) -> dict:
    """Check a document from the dashboard; raises ModeError with a readable message.
    (`tiles` is accepted for older callers; all tiles are widgets.)"""
    widgets = set(widgets) | set(tiles or ())
    if not isinstance(doc, dict) or not isinstance(doc.get("modes"), dict) or not doc["modes"]:
        raise ModeError("modes document needs a non-empty 'modes' object")
    layouts = doc.get("layouts") or {}
    if not isinstance(layouts, dict):
        raise ModeError("'layouts' must be an object")
    for name, lay in layouts.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ModeError(f"bad layout name {name!r}")
        if not isinstance(lay, dict) or "tree" not in lay:
            raise ModeError(f"layout '{name}' needs a 'tree'")
        try:
            validate_tree(lay["tree"], widgets, f"layout '{name}'")
        except LayoutError as e:
            raise ModeError(str(e))
    for name, m in doc["modes"].items():
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ModeError(f"bad mode name {name!r}")
        if not isinstance(m, dict) or not isinstance(m.get("slides"), list) or not m["slides"]:
            raise ModeError(f"mode '{name}' needs at least one slide")
        for i, s in enumerate(m["slides"]):
            where = f"mode '{name}' slide {i + 1}"
            if not isinstance(s, dict):
                raise ModeError(f"{where} is not an object")
            w = s.get("widget") or s.get("screen")
            if w is not None:
                if w not in widgets:
                    raise ModeError(f"{where}: unknown widget '{w}'")
            elif "layout" in s:
                if s["layout"] not in layouts:
                    raise ModeError(f"{where}: unknown layout '{s['layout']}'")
            elif "quad" in s:
                q = s["quad"]
                if not isinstance(q, list) or not q or any(t and t not in widgets for t in q):
                    raise ModeError(f"{where}: quad needs widget names")
            else:
                raise ModeError(f"{where} needs a 'layout' or a 'widget'")
            secs = s.get("seconds")
            if secs is not None and not (isinstance(secs, (int, float)) and 3 <= secs <= 3600):
                raise ModeError(f"{where}: seconds must be 3-3600")
    if doc.get("default") not in doc["modes"]:
        raise ModeError("'default' must name one of the modes")
    br = doc.get("breaking")
    if br is not None:
        if not isinstance(br, dict):
            raise ModeError("'breaking' must be an object")
        for k in ("trades", "pending", "hold_while_off", "big_plays", "team_scores", "rival_plays", "rival_scores",
                  "touchdowns", "bench"):
            if k in br and not isinstance(br[k], bool):
                raise ModeError(f"breaking {k} must be true or false")
        for k, lo, hi in (("seconds", 5, 300), ("pending_seconds", 5, 120), ("poll_seconds", 10, 300),
                          ("play_seconds", 8, 120), ("min_yards", 5, 99), ("min_points", 1, 50),
                          ("play_poll_seconds", 8, 120)):
            v = br.get(k)
            if v is not None and not (isinstance(v, (int, float)) and lo <= v <= hi):
                raise ModeError(f"breaking-news {k.replace('_', ' ')} must be {lo}-{hi}")
    th = doc.get("theme")
    if th is not None:
        if not isinstance(th, dict):
            raise ModeError("'theme' must be an object")
        if th.get("name", "off") not in THEMES:
            raise ModeError(f"unknown theme {th.get('name')!r}")
        for k in ("season_only", "garland", "snow"):
            if k in th and not isinstance(th[k], bool):
                raise ModeError(f"theme {k} must be true or false")
        if "snow_amount" in th and th["snow_amount"] not in (1, 2, 3):
            raise ModeError("snow amount must be 1, 2 or 3")
    pa = doc.get("party")
    if pa is not None:
        if not isinstance(pa, dict):
            raise ModeError("'party' must be an object")
        if "bpm" in pa and not (isinstance(pa["bpm"], (int, float)) and 60 <= pa["bpm"] <= 200):
            raise ModeError("party BPM must be 60-200")
        if "message" in pa and not (isinstance(pa["message"], str) and len(pa["message"]) <= 40):
            raise ModeError("party message must be text, up to 40 characters")
        if "strobe" in pa and not isinstance(pa["strobe"], bool):
            raise ModeError("party strobe must be true or false")
    dd = doc.get("display_default") or {}
    if "brightness" in dd and not (isinstance(dd["brightness"], (int, float)) and 0 <= dd["brightness"] <= 100):
        raise ModeError("default brightness must be 0-100")
    for i, rule in enumerate(doc.get("schedule") or []):
        where = f"schedule rule {i + 1}"
        if not isinstance(rule, dict):
            raise ModeError(f"{where} is not an object")
        if not any(k in rule for k in ("mode", "brightness", "power")):
            raise ModeError(f"{where}: set a mode, a brightness, or power on/off")
        if "mode" in rule and rule["mode"] not in doc["modes"]:
            raise ModeError(f"{where}: unknown mode '{rule['mode']}'")
        if "brightness" in rule and not (isinstance(rule["brightness"], (int, float)) and 0 <= rule["brightness"] <= 100):
            raise ModeError(f"{where}: brightness must be 0-100")
        if "power" in rule and rule["power"] not in ("on", "off"):
            raise ModeError(f"{where}: power must be 'on' or 'off'")
        days = rule.get("days") or []
        if any(d not in DAYS for d in days):
            raise ModeError(f"{where}: days must be from {DAYS}")
        if not days and not rule.get("dates") and not rule.get("date_from") and not rule.get("date_to"):
            raise ModeError(f"{where}: pick days of the week or calendar dates")
        try:
            for d in rule.get("dates") or []:
                _date(d)
            for k in ("date_from", "date_to"):
                if rule.get(k):
                    _date(rule[k])
        except (TypeError, ValueError):
            raise ModeError(f"{where}: dates must look like 2026-11-26")
        try:
            _hm(rule.get("from"), dtime.min)
            _hm(rule.get("to"), dtime.max)
        except (ValueError, AttributeError):
            raise ModeError(f"{where}: times must look like 18:30")
    return doc


def upgrade(doc: dict) -> dict:
    """Bring an older modes.json forward (v1 had no layouts / display settings)."""
    if doc.get("version") == 2:
        return doc
    out = copy.deepcopy(doc)
    out["version"] = 2
    out.setdefault("display_default", copy.deepcopy(DEFAULT_MODES["display_default"]))
    layouts = out.setdefault("layouts", {})
    for k, v in DEFAULT_MODES["layouts"].items():
        layouts.setdefault(k, copy.deepcopy(v))
    for m in out.get("modes", {}).values():
        for s in m.get("slides", []):
            if "screen" in s:
                s["widget"] = s.pop("screen")
    return out


class ModeManager:
    def __init__(self, path: Path | None):
        self.path = path
        self.override: str | None = None
        self.display_override: dict[str, Any] = {}      # manual brightness/power from the dashboard
        self.doc = copy.deepcopy(DEFAULT_MODES)
        if path and path.exists():
            try:
                self.doc = upgrade(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def save(self, doc: dict) -> None:
        self.doc = doc
        if self.override and self.override not in doc["modes"]:
            self.override = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def _first(self, key: str, now: datetime) -> tuple[Any, str] | None:
        for rule in self.doc.get("schedule") or []:
            if key in rule and (key != "mode" or rule["mode"] in self.doc.get("modes", {})):
                try:
                    if rule_matches(rule, now):
                        return rule[key], _describe(rule)
                except (ValueError, TypeError):
                    continue
        return None

    def active(self, now: datetime) -> tuple[str, str]:
        """(mode name, why) for the given local time."""
        modes = self.doc.get("modes", {})
        if self.override in modes:
            return self.override, "manual override"
        hit = self._first("mode", now)
        if hit:
            return hit
        default = self.doc.get("default")
        if default in modes:
            return default, "default"
        return next(iter(modes)), "first mode"

    def display(self, now: datetime) -> dict[str, Any]:
        """{'brightness': 0-100, 'power': 'on'|'off', 'why': {...}} after schedule + manual override."""
        dd = self.doc.get("display_default") or {}
        out = {"brightness": dd.get("brightness", 100), "power": dd.get("power", "on"),
               "why": {"brightness": "default", "power": "default"}}
        for key in ("brightness", "power"):
            if key in self.display_override:
                out[key], out["why"][key] = self.display_override[key], "manual"
                continue
            hit = self._first(key, now)
            if hit:
                out[key], out["why"][key] = hit
        return out

    def breaking(self) -> dict[str, Any]:
        """Breaking-news takeover settings, with defaults for files saved before the setting existed."""
        return {**DEFAULT_MODES["breaking"], **(self.doc.get("breaking") or {})}

    def party(self) -> dict[str, Any]:
        """Party settings, with defaults for files saved before the setting existed."""
        return {**DEFAULT_MODES["party"], **(self.doc.get("party") or {})}

    def theme(self) -> dict[str, Any]:
        """Theme settings, with defaults for files saved before the setting existed."""
        return {**DEFAULT_MODES["theme"], **(self.doc.get("theme") or {})}

    def theme_active(self, now: datetime) -> str | None:
        """The theme to draw right now, or None."""
        th = self.theme()
        if th["name"] == "christmas" and (not th["season_only"] or now.month == 12):
            return "christmas"
        return None

    def mode(self, name: str) -> dict:
        return self.doc["modes"][name]

    def layout_tree(self, name: str) -> dict | None:
        lay = (self.doc.get("layouts") or {}).get(name)
        return lay.get("tree") if lay else None
