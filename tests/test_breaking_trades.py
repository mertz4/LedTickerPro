"""Breaking-news takeover for new fantasy trades, the latest-trade widget and the setting that turns it off."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from ledticker.config import Context
from ledticker.display import Geometry
from ledticker.render.engine import BREAKING, Engine
from ledticker.render.images import ImageCache
from ledticker.render.modes import ModeError, ModeManager, validate
from ledticker.sources.espn_fantasy import trade_player_ids
from test_render import TZ, make_store


def trade(tid: str, age: timedelta) -> dict:
    when = (datetime.now(timezone.utc) - age).isoformat()
    return {"id": tid, "date": when, "status": "processed", "week": None, "sides": [
        {"team_id": 1, "team_name": "The Vets", "receives": ["C.J. Stroud", "Isaiah Likely"]},
        {"team_id": 4, "team_name": "Mine", "receives": ["Puka Nacua"]}]}


def engine(tmp_path, trades, breaking=None, geom=None):
    st = make_store()
    st.register("fantasy_trades", "trades", True, "", 1800)
    st.ok("fantasy_trades", {"trades": trades})
    ctx = Context(root=tmp_path, config={"scores": {"nfl": {"teams": ["34"]}, "cfb": {"teams": ["245"]}}},
                  secrets={}, tz=TZ)
    eng = Engine(ctx, st, ImageCache(tmp_path), geom or Geometry(), modes_path=tmp_path / "modes.json")
    if breaking is not None:
        eng.modes.doc["breaking"] = breaking
    return eng


def test_new_trade_takes_over_whole_display_then_resumes(tmp_path):
    eng = engine(tmp_path, [trade("t1", timedelta(minutes=2))])
    eng._check_interrupts = lambda rc: None      # hold the trade back for the first frame
    eng.render(t=0)
    before = eng.current
    assert before not in (None, BREAKING)
    del eng._check_interrupts
    eng._last_check = 0
    eng.render()
    assert eng.current == BREAKING and eng._ticker_on() is False
    assert eng.widgets[BREAKING].full.trade["id"] == "t1"
    eng._breaking_until = 0                      # time's up
    eng.render()
    assert eng.current == before and eng._ticker_on() is True
    eng._last_check = 0
    eng.render()                                 # already seen: doesn't break in again
    assert eng.current == before


def test_takeover_can_be_turned_off(tmp_path):
    eng = engine(tmp_path, [trade("t1", timedelta(minutes=2))], breaking={"trades": False})
    eng.render(t=0)
    assert eng.current != BREAKING
    assert "trade:t1" in eng._seen               # no backlog if it's turned back on later


def test_old_trade_no_takeover_but_latest_trade_widget_shows_it(tmp_path):
    eng = engine(tmp_path, [trade("old", timedelta(days=20))])
    eng.render(t=0)
    assert eng.current != BREAKING
    assert "trade" in eng.available()
    assert "trade" not in eng.modes.doc["modes"]["sweep"]["slides"]   # opt-in: not in the default rotation


def test_test_button_and_next_dismisses(tmp_path):
    eng = engine(tmp_path, [trade("old", timedelta(days=20))])
    eng.render(t=0)
    eng.breaking_test()
    eng.render()
    assert eng.current == BREAKING
    eng.skip()
    eng.render()
    assert eng.current != BREAKING


@pytest.mark.parametrize("geom", [Geometry(128, 64, 2, 2), Geometry(128, 56, 2, 2), Geometry(64, 32, 2, 2)])
@pytest.mark.parametrize("t", [0.5, 5.0])
def test_trade_screens_render(tmp_path, geom, t):
    eng = engine(tmp_path, [trade("t1", timedelta(minutes=2))], geom=geom)
    for name in ("trade", BREAKING):
        frame = eng.render(t=t, screen=name)
        assert frame.getbbox() is not None


def test_hidden_from_widget_pickers_and_validation(tmp_path):
    eng = engine(tmp_path, [])
    assert getattr(eng.widgets[BREAKING].full, "hidden", False)
    doc = ModeManager(None).doc
    for bad in ({"trades": "yes"}, {"seconds": 1}, "on"):
        doc["breaking"] = bad
        with pytest.raises(ModeError):
            validate(doc, set(eng.widgets))
    doc["breaking"] = {"trades": False, "seconds": 45}
    validate(doc, set(eng.widgets))


def test_trade_player_ids_newest_first():
    feed = {"topics": [
        {"id": "b", "messages": [{"messageTypeId": 244, "targetId": 5, "to": 1}, {"messageTypeId": 178, "targetId": 9}]},
        {"id": "a", "messages": [{"messageTypeId": 244, "targetId": 3, "to": 2}, {"messageTypeId": 244, "targetId": 5}]}]}
    assert trade_player_ids(feed) == [5, 3]


# ------------------------------------------------------------------ pending trades, hold while off, poll setting

from ledticker.sources.espn_fantasy import EspnTrades, pending_trades


def test_pending_trades_from_review_list():
    d = {"pendingTransactions": [
        # two other teams, accepted and waiting out the review period
        {"id": "p1", "type": "TRADE_ACCEPT", "status": "PENDING", "teamId": 1, "acceptedDate": 1793749920688,
         "processDate": 1793836320688, "items": [{"playerId": 11, "fromTeamId": 2, "toTeamId": 1},
                                                 {"playerId": 22, "fromTeamId": 1, "toTeamId": 2}]},
        # my own offer nobody has answered: not a trade yet
        {"id": "p2", "type": "TRADE_PROPOSAL", "status": "PENDING", "teamId": 4,
         "items": [{"playerId": 33, "fromTeamId": 4, "toTeamId": 3}]},
        {"id": "p3", "type": "TRADE_PROPOSAL", "status": "CANCELED", "items": [{"playerId": 1, "toTeamId": 1}]},
        {"id": "w", "type": "WAIVER", "status": "PENDING", "items": [{"playerId": 5, "toTeamId": 4}]},
    ]}
    out = pending_trades(d, {11: "C.J. Stroud", 22: "Puka Nacua"}, {1: "Vets", 2: "Kids"}, my_team_id=4)
    assert [t.id for t in out] == ["p1"]
    assert {s.team_name: s.receives for s in out[0].sides} == {"Vets": ["C.J. Stroud"], "Kids": ["Puka Nacua"]}
    assert out[0].status == "pending review" and out[0].process_date.startswith("2026-")


def test_pending_heads_up_is_shorter_and_can_be_turned_off(tmp_path):
    p = {**trade("p1", timedelta(minutes=1)), "status": "pending review"}
    eng = engine(tmp_path, [], breaking={"trades": True, "pending": True, "pending_seconds": 12})
    eng.store.ok("fantasy_trades", {"trades": [], "pending": [p]})
    eng.render()
    assert eng.current == BREAKING and eng.widgets[BREAKING].full.trade["status"] == "pending review"
    assert 11 < eng._breaking_until - time.monotonic() <= 12
    for t in (0.5, 5.0):
        assert eng.render(t=t, screen=BREAKING).getbbox()

    off = engine(tmp_path / "b", [], breaking={"pending": False})
    off.store.ok("fantasy_trades", {"trades": [], "pending": [p]})
    off.render()
    assert off.current != BREAKING and "pending:p1" in off._seen


def test_alert_waits_while_display_is_off(tmp_path):
    eng = engine(tmp_path, [trade("t1", timedelta(minutes=2))])
    eng.modes.display_override["power"] = "off"
    eng.render()
    assert eng.current != BREAKING and len(eng._breaking_q) == 1     # saved for later
    eng.modes.display_override.clear()
    eng.render()
    assert eng.current == BREAKING                                    # plays once the display is back on
    eng.modes.display_override["brightness"] = 0                      # goes dark mid-alert
    eng.render()
    assert eng.current != BREAKING and eng._breaking_q[0]["id"] == "t1"   # will replay in full


def test_alert_plays_while_off_when_hold_is_unchecked(tmp_path):
    eng = engine(tmp_path, [trade("t1", timedelta(minutes=2))], breaking={"hold_while_off": False})
    eng.modes.display_override["power"] = "off"
    eng.render()
    assert eng.current == BREAKING


def test_poll_rate_from_settings_overrides_config(tmp_path):
    ctx = Context(root=tmp_path, config={"fantasy": {"trade_poll_seconds": 30}}, secrets={}, tz=TZ)
    src = EspnTrades(ctx)
    assert src.next_interval(None) == 30
    src.poll_seconds = 12
    assert src.next_interval(None) == 12
    src.poll_seconds = 2
    assert src.next_interval(None) == 10    # never faster than every 10 s


def test_validate_new_breaking_options(tmp_path):
    eng = engine(tmp_path, [])
    doc = ModeManager(None).doc
    for bad in ({"pending": "yes"}, {"hold_while_off": 1}, {"poll_seconds": 5}, {"pending_seconds": 500}):
        doc["breaking"] = bad
        with pytest.raises(ModeError):
            validate(doc, set(eng.widgets))
