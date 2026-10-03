import json

from factory import db as dbm
from factory import usage

BODY = {"five_hour": {"utilization": 37.5, "resets_at": "2026-10-03T12:00:00+00:00"},
        "seven_day": {"utilization": 91, "resets_at": None}, "seven_day_opus": None}


def test_parse_and_summary():
    u = usage.parse(BODY, 1_790_000_000)
    assert u["session"]["used"] == 37.5 and u["week"]["used"] == 91.0 and u["week"]["resets"] is None
    text = usage.summary(u, u["session"]["resets"] - 7500)
    assert "Session: 38% used, 62% left (resets in 2h 5m)" in text and "Week: 91% used" in text
    assert usage.over_limit(u) == ["week"]


def test_parse_ignores_garbage():
    assert usage.parse({"five_hour": {"utilization": "x"}, "seven_day": 3}, 1.0) == {"fetched": 1.0}


def test_refresh_throttles_and_keeps_last_numbers_on_error(tmp_path, monkeypatch):
    env = tmp_path / "claude.env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=abcdefgh\n")
    db = dbm.connect(str(tmp_path / "f.db"))
    calls = []
    monkeypatch.setattr(usage, "fetch", lambda tok, now: calls.append(tok) or usage.parse(BODY, now))
    usage.refresh(db, str(env), now=1000.0)
    usage.refresh(db, str(env), now=1100.0)                 # inside the throttle window
    assert calls == ["abcdefgh"]

    def boom(tok, now):
        raise OSError("down")
    monkeypatch.setattr(usage, "fetch", boom)
    cur = usage.refresh(db, str(env), now=2000.0)
    assert cur["error"] == "OSError" and cur["session"]["used"] == 37.5
    assert "could not refresh" in usage.summary(usage.load(db), 2000.0)


def test_api_key_fetches_nothing(tmp_path):
    env = tmp_path / "claude.env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-xxxxxxxx\n")
    db = dbm.connect(str(tmp_path / "f.db"))
    assert usage.refresh(db, str(env)) is None and usage.load(db) is None
    assert "not available" in usage.summary(None)
