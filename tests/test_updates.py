import json
import tempfile
import unittest
from pathlib import Path

from factory import updates, version
from factory.ui import views


class Versions(unittest.TestCase):
    def test_comparison(self):
        self.assertTrue(updates.newer("v0.2.0", "0.1.9"))
        self.assertTrue(updates.newer("1.0.0", "v0.9.9"))
        self.assertFalse(updates.newer("v0.1.0", "0.1.0"))
        self.assertFalse(updates.newer("v0.2.0", "dev"))          # a development build never nags
        self.assertFalse(updates.newer("latest", "0.1.0"))
        self.assertFalse(updates.newer("v1.0.0-rc1", "0.1.0"))

    def test_current_version_is_the_file(self):
        self.assertRegex(version.current(), r"^\d+\.\d+\.\d+$|^dev$")


class Fetch(unittest.TestCase):
    class R:
        def __init__(self, d): self.d = d
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n): return json.dumps(self.d).encode()

    def test_good_and_bad_answers(self):
        ok = lambda req, timeout: self.R({"tag_name": "v0.2.0", "html_url": "https://github.com/o/r/releases/tag/v0.2.0"})
        self.assertEqual(updates.fetch_latest("o/r", ok), {"tag": "v0.2.0", "url": "https://github.com/o/r/releases/tag/v0.2.0"})
        evil = lambda req, timeout: self.R({"tag_name": "v0.2.0", "html_url": "https://evil.example/x"})
        self.assertIsNone(updates.fetch_latest("o/r", evil))
        junk = lambda req, timeout: self.R({"tag_name": "nightly", "html_url": "https://github.com/o/r"})
        self.assertIsNone(updates.fetch_latest("o/r", junk))
        def boom(req, timeout): raise OSError("offline")
        self.assertIsNone(updates.fetch_latest("o/r", boom))
        self.assertIsNone(updates.fetch_latest("not a repo", ok))


class Auth(unittest.TestCase):
    def test_token_is_sent_only_when_given(self):
        seen = []
        def op(req, timeout):
            seen.append(req.get_header("Authorization"))
            return Fetch.R({"tag_name": "v0.2.0", "html_url": "https://github.com/o/r/releases/tag/v0.2.0"})
        updates.fetch_latest("o/r", op)
        updates.fetch_latest("o/r", op, token="t0k")
        self.assertEqual(seen, [None, "Bearer t0k"])


class Cache(unittest.TestCase):
    def test_refresh_then_available_and_staleness(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertTrue(updates.due(t))
            updates.refresh(t, "o/r", now=lambda: 1000.0, fetch=lambda r: {"tag": "v0.3.0", "url": "https://github.com/o/r/x"})
            self.assertFalse(updates.due(t, now=lambda: 1000.0 + 60))
            self.assertTrue(updates.due(t, now=lambda: 1000.0 + updates.CACHE_SECONDS + 1))
            self.assertEqual(updates.available(t, "0.1.0")["tag"], "v0.3.0")
            self.assertIsNone(updates.available(t, "0.3.0"))
            updates.refresh(t, "o/r", fetch=lambda r: None)       # offline: remembered, no banner
            self.assertIsNone(updates.available(t, "0.1.0"))


class Banner(unittest.TestCase):
    def tearDown(self):
        views.UPDATE.update(tag="", url="")

    def test_banner_only_for_valid_values(self):
        self.assertEqual(views.update_banner(), "")
        views.UPDATE.update(tag="v0.2.0", url="https://github.com/o/r/releases/tag/v0.2.0")
        self.assertIn("update.sh", views.update_banner())
        views.UPDATE.update(tag="<script>", url="https://github.com/o/r")
        self.assertEqual(views.update_banner(), "")
        views.UPDATE.update(tag="v0.2.0", url="javascript:alert(1)")
        self.assertEqual(views.update_banner(), "")


if __name__ == "__main__":
    unittest.main()
