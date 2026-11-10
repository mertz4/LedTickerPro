"""Anthony's eat-out counter: the saved tally, the +1 takeover, the widget at every size and who may tap +1."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render import tally
from ledticker.render.engine import EATOUT, Engine
from ledticker.render.images import ImageCache
from ledticker.render.screens import RC
from ledticker.store import Store
from ledticker.web.app import create_app
from test_push import GF, ME, RM, hdr
from test_render import make_store

TZ = ZoneInfo("America/Chicago")


def test_counter_saves_undoes_and_counts(tmp_path):
    path = tmp_path / "eatout.json"
    c = tally.Counter(path, "Anthony")
    now = datetime(2026, 11, 11, 18, 0, tzinfo=TZ)                    # a Wednesday
    c.add("Whataburger", "Kade", now - timedelta(days=12))            # Oct 30: last month
    c.add("  whataburger ", "Kade", now - timedelta(days=1))
    c.add(None, "Sam", now - timedelta(hours=2))
    st = c.stats(TZ, now)
    assert (st["total"], st["week"], st["month"], st["today"]) == (3, 2, 2, 1)
    assert st["favorite"] == "WHATABURGER" and tally.clean_line(st) == "LAST ONE 2H AGO"
    c2 = tally.Counter(path)                                            # survives a restart
    assert c2.total == 3 and c2.events[1]["place"] == "whataburger"
    c2.set_base(20)                                                     # times from before the counter
    assert c2.total == 20 and tally.Counter(path).total == 20
    assert c2.undo()["by"] == "Sam" and c2.total == 19
    assert tally.clean_line(c2.stats(TZ, now + timedelta(days=2))) == "CLEAN 3 DAYS"


@pytest.mark.parametrize("n,s", [(1, "1ST"), (2, "2ND"), (3, "3RD"), (4, "4TH"), (11, "11TH"), (22, "22ND")])
def test_ordinal(n, s):
    assert tally.ordinal(n) == s


def engine(tmp_path):
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}}, "eatout": {"name": "Anthony"}},
                  secrets={}, tz=TZ)
    return Engine(ctx, make_store(), ImageCache(tmp_path), Geometry(), modes_path=tmp_path / "modes.json")


def test_plus_one_takes_over_the_whole_display_then_resumes(tmp_path):
    eng = engine(tmp_path)
    eng.render(t=0)
    before = eng.current
    ev, held = eng.eatout_add("Sonic", "Kade")
    assert ev["total"] == 1 and ev["week"] == 1 and not held
    eng.render()
    assert eng.current == EATOUT and eng._ticker_on() is False
    assert eng.widgets[EATOUT].full.event["place"] == "Sonic"
    eng.skip()                                                          # Next dismisses it
    eng.render()
    assert eng.current == before
    assert "eatout" in eng.available()                                  # the counter widget, for any mode


@pytest.mark.parametrize("size", [(256, 128), (256, 116), (128, 64), (128, 56), (64, 32), (256, 11)])
def test_widget_and_alert_render_any_size(tmp_path, size):
    W, H = size
    tally.COUNTER = c = tally.Counter(None, "Anthony")
    c.add("Raising Cane's", "Kade")
    rc = RC(ctx=Context(root=tmp_path, config={}, secrets={}, tz=TZ), store=Store(None), images=ImageCache(tmp_path),
            W=W, H=H, main_h=H, now=datetime.now(timezone.utc))
    img = Image.new("RGB", size)
    tally.EatOutCounter().render(img, rc, 1.0)
    assert img.getbbox() is not None
    if H >= 32:
        al = tally.EatOutAlert()
        al.event = {"total": 12, "week": 3, "name": "Anthony", "place": "Sonic", "by": "Kade"}
        for t in (0.2, 2.0, 3.0, 4.0, 6.0, 11.5):
            img = Image.new("RGB", size)
            al.render(img, rc, t)
            assert img.getbbox() is not None


def test_api_permissions(tmp_path):
    ctx = Context(root=tmp_path, config={"eatout": {"name": "Anthony"}},
                  secrets={"LEDTICKER_SUPERUSERS": ME}, tz=TZ)
    app = create_app(ctx, auth="tailscale")
    c = TestClient(app)
    c.put(f"/api/access/{GF}", json={"role": "partner"}, headers=hdr(ME)).raise_for_status()
    c.put(f"/api/access/{RM}", json={"role": "roommate"}, headers=hdr(ME)).raise_for_status()
    r = c.post("/api/eatout", json={"place": "Whataburger"}, headers=hdr(RM))       # roommates can +1
    assert r.status_code == 200 and r.json()["total"] == 1
    assert c.post("/api/eatout", json={}, headers=hdr(GF)).status_code == 403      # partner can't (unless granted)
    assert c.get("/api/eatout", headers=hdr(GF)).json()["total"] == 1               # but can see it
    assert c.put("/api/eatout/total", json={"total": 30}, headers=hdr(RM)).status_code == 403
    assert c.put("/api/eatout/total", json={"total": 30}, headers=hdr(ME)).json()["total"] == 30
    assert c.delete("/api/eatout/last", headers=hdr(RM)).json()["total"] == 29
    assert c.delete("/api/eatout/last", headers=hdr(RM)).status_code == 404        # nothing left to undo
