import json
from pathlib import Path

from ledticker.sources.nws import alert_level, c_to_f, compass, parse_alerts, parse_hourly, parse_obs, parse_periods

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_conversions():
    assert c_to_f(0) == 32 and c_to_f(100) == 212 and c_to_f(None) is None
    assert compass(0) == "N" and compass(180) == "S" and compass(350) == "N" and compass(225) == "SW"


def test_observation():
    cur = parse_obs(load("nws_obs.json"), "KCLL")
    assert cur.station == "KCLL"
    assert cur.temp_f is not None and -30 < cur.temp_f < 130
    assert cur.feels_like_f is not None


def test_forecast_and_hourly():
    periods = parse_periods(load("nws_forecast.json"))
    hours = parse_hourly(load("nws_hourly.json"))
    assert 1 <= len(periods) <= 14 and periods[0].name
    assert len(hours) == 24 and all(isinstance(h.temp_f, int) for h in hours)


def test_alerts_sorted_by_level():
    alerts = parse_alerts(load("nws_alerts_sample.json"))
    assert [a.level for a in alerts] == ["warning", "watch", "advisory"]
    assert alerts[0].event == "Tornado Warning" and alerts[0].ends
    assert alert_level("Special Weather Statement") == "statement"
