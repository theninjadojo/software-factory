"""Claude plan usage: parsing, the throttle, what a failed fetch says and how long it waits, and how it is shown."""
import email.message
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import usage

BODY = {"five_hour": {"utilization": 37.5, "resets_at": "2026-10-03T12:00:00+00:00"},
        "seven_day": {"utilization": 91, "resets_at": None}, "seven_day_opus": None}
SCOPE = {"type": "error", "error": {"type": "permission_error", "message": "OAuth token does not meet scope requirement user:profile"}}


def http_error(code: int, body=None, headers=None) -> urllib.error.HTTPError:
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    raw = json.dumps(body).encode() if isinstance(body, dict) else (body or b"")
    return urllib.error.HTTPError(usage.URL, code, "error", h, io.BytesIO(raw))


class Ok:
    """What urlopen returns: a context manager with read()."""
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class Parse(unittest.TestCase):
    def test_parse_and_summary(self):
        u = usage.parse(BODY, 1_790_000_000)
        self.assertEqual((u["session"]["used"], u["week"]["used"], u["week"]["resets"]), (37.5, 91.0, None))
        text = usage.summary(u, u["session"]["resets"] - 7500)
        self.assertIn("Session: 38% used, 62% left (resets in 2h 5m)", text)
        self.assertIn("Week: 91% used", text)
        self.assertEqual(usage.over_limit(u), ["week"])

    def test_parse_ignores_garbage(self):
        self.assertEqual(usage.parse({"five_hour": {"utilization": "x"}, "seven_day": 3}, 1.0), {"fetched": 1.0})


class Refresh(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.env = self.dir / "claude.env"
        self.token("sk-ant-oat01-first")
        self.db = dbm.connect(str(self.dir / "f.db"))
        self.addCleanup(self.db.close)
        self.answers, self.calls = [], []
        p = mock.patch.object(usage.urllib.request, "urlopen", self.urlopen)
        p.start()
        self.addCleanup(p.stop)

    def token(self, value):
        self.env.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={value}\n")

    def urlopen(self, req, timeout=None):
        self.calls.append(req.get_header("Authorization"))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return Ok(a)

    def refresh(self, now):
        return usage.refresh(self.db, str(self.env), now=now)

    def test_throttled_and_the_last_numbers_kept_when_it_cannot_reach_anthropic(self):
        self.answers = [BODY, OSError("down")]
        self.refresh(1000.0)
        self.refresh(1100.0)                                       # inside the throttle window
        self.assertEqual(len(self.calls), 1)
        cur = self.refresh(2000.0)
        self.assertEqual((cur["error"], cur["session"]["used"]), ("unreachable", 37.5))
        self.assertIn("could not refresh: api.anthropic.com could not be reached", usage.summary(usage.load(self.db), 2000.0))
        self.assertNotIn("sk-ant", json.dumps(usage.load(self.db)))  # only a fingerprint of the token is kept

    def test_a_token_that_cannot_read_usage_says_why_once_and_waits_an_hour(self):
        self.answers = [http_error(403, SCOPE), http_error(403, SCOPE)]
        with self.assertLogs("factory.usage", "WARNING") as logs:
            cur = self.refresh(1000.0)
        self.assertEqual(logs.output, ["WARNING:factory.usage:usage fetch failed: HTTP 403 permission_error: OAuth token does not meet "
                                       "scope requirement user:profile; next try in 60 min"])
        self.assertEqual((cur["error"], cur["detail"], cur["retry_at"]), ("HTTP 403", SCOPE["error"]["type"] + ": " + SCOPE["error"]["message"], 4600.0))
        self.assertIn("claude setup-token", cur["reason"])
        self.assertIn("Claude usage unavailable: This subscription token cannot read plan usage (403)", usage.summary(cur))
        self.refresh(1000.0 + usage.REFRESH_SECONDS + 1)           # the old 5-minute throttle would ask again here
        self.assertEqual(len(self.calls), 1)
        with self.assertNoLogs("factory.usage", "WARNING"):        # the same failure again: not logged again
            self.refresh(4601.0)
        self.assertEqual(len(self.calls), 2)

    def test_a_new_token_is_tried_at_once_and_recovery_is_logged(self):
        self.answers = [http_error(403, SCOPE), BODY]
        self.refresh(1000.0)
        self.token("sk-ant-oat01-second")
        with self.assertLogs("factory.usage", "INFO") as logs:
            cur = self.refresh(1001.0)
        self.assertEqual(logs.output, ["INFO:factory.usage:usage fetch works again"])
        self.assertEqual(self.calls, ["Bearer sk-ant-oat01-first", "Bearer sk-ant-oat01-second"])
        self.assertNotIn("error", cur)
        self.assertEqual(cur["session"]["used"], 37.5)

    def test_a_rate_limit_waits_as_long_as_anthropic_asks(self):
        self.answers = [http_error(429, headers={"Retry-After": "120"}), http_error(429, headers={"Retry-After": "soon"}),
                        http_error(429, headers={"Retry-After": "999999"})]
        self.assertEqual(self.refresh(1000.0)["retry_at"], 1120.0)
        self.assertEqual(self.refresh(1121.0)["retry_at"], 1121.0 + usage.LIMITED_RETRY)
        self.assertEqual(self.refresh(1121.0 + usage.LIMITED_RETRY)["retry_at"], 1121.0 + usage.LIMITED_RETRY + 3600)

    def test_other_failures(self):
        self.answers = [http_error(401), http_error(502, b"<html>bad gateway</html>"), {"unexpected": 1}]
        cur = self.refresh(1000.0)
        self.assertEqual((cur["error"], cur["retry_at"]), ("HTTP 401", 1000.0 + usage.REFUSED_RETRY))
        self.assertIn("may have expired", cur["reason"])
        self.assertNotIn("detail", cur)
        cur = self.refresh(1000.0 + usage.REFUSED_RETRY)
        self.assertEqual((cur["error"], cur["retry_at"] - (1000.0 + usage.REFUSED_RETRY)), ("HTTP 502", usage.REFRESH_SECONDS))
        cur = self.refresh(cur["retry_at"])
        self.assertEqual(cur["error"], "bad response")

    def test_anthropics_text_is_one_short_printable_line(self):
        evil = {"error": {"type": "x\x1b[2J", "message": "line one\nline two " + "y" * 400}}
        self.answers = [http_error(403, evil)]
        detail = self.refresh(1000.0)["detail"]
        self.assertTrue(detail.startswith("x[2J: line one line two y"))
        self.assertLessEqual(len(detail), 200)
        self.assertFalse(any(not c.isprintable() for c in detail))

    def test_an_api_key_fetches_nothing(self):
        self.env.write_text("ANTHROPIC_API_KEY=sk-ant-xxxxxxxx\n")
        self.assertIsNone(self.refresh(1000.0))
        self.assertIsNone(usage.load(self.db))
        self.assertEqual(self.calls, [])
        self.assertIn("not available", usage.summary(None))


class Card(unittest.TestCase):
    def card(self, u):
        from factory.ui import board
        return board.usage_card({"status": {"claude_usage": {"value": json.dumps(u)}}}, 1000.0)

    def test_the_floor_says_why_there_are_no_numbers(self):
        html = self.card({"fetched": 1.0, "error": "HTTP 403", "reason": "This subscription token cannot read plan usage (403) <b>."})
        self.assertIn("Claude plan usage", html)
        self.assertIn("Unavailable. This subscription token cannot read plan usage (403) &lt;b&gt;.", html)
        html = self.card({"fetched": 1.0, "session": {"used": 40, "resets": None}, "error": "unreachable", "reason": "Could not reach it."})
        self.assertIn('value="40"', html)
        self.assertIn("Could not refresh; these are the last known numbers. Could not reach it.", html)
        self.assertEqual(self.card({"fetched": 1.0}), "")


if __name__ == "__main__":
    unittest.main()
