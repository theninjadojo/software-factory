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

    def test_a_beta_sorts_below_its_release(self):
        self.assertTrue(updates.newer("v0.40.0-beta.1", "0.39.9"))
        self.assertTrue(updates.newer("v0.40.0-beta.10", "0.40.0-beta.9"))     # numeric, not alphabetical
        self.assertTrue(updates.newer("v0.40.0", "0.40.0-beta.3"))
        self.assertFalse(updates.newer("v0.40.0-beta.3", "0.40.0"))
        self.assertFalse(updates.newer("v0.40.0-beta", "0.39.0"))
        self.assertTrue(updates.is_beta("v1.0.0-beta.2"))
        self.assertFalse(updates.is_beta("1.0.0"))
        self.assertFalse(updates.is_beta("dev"))

    def test_current_version_is_the_file(self):
        self.assertRegex(version.current(), r"^\d+\.\d+\.\d+(-beta\.\d+)?$|^dev$")


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


    def test_stable_never_takes_a_beta_and_beta_takes_the_highest_of_the_list(self):
        seen = []
        def op(d):
            def f(req, timeout):
                seen.append(req.full_url)
                return self.R(d)
            return f
        beta = {"tag_name": "v0.3.0-beta.1", "html_url": "https://github.com/o/r/releases/tag/v0.3.0-beta.1"}
        self.assertIsNone(updates.fetch_latest("o/r", op(beta)))
        rel = lambda t, **k: {"tag_name": t, "html_url": f"https://github.com/o/r/releases/tag/{t}", **k}
        listed = [rel("v0.2.0"), rel("v0.3.0-beta.2"), rel("v0.3.0-beta.10"), rel("v0.4.0", draft=True), rel("nightly"),
                  {"tag_name": "v9.0.0", "html_url": "https://evil.example/x"}]
        self.assertEqual(updates.fetch_latest("o/r", op(listed), channel="beta")["tag"], "v0.3.0-beta.10")
        self.assertEqual(updates.fetch_latest("o/r", op(listed + [rel("v0.3.0")]), channel="beta")["tag"], "v0.3.0")
        self.assertIsNone(updates.fetch_latest("o/r", op({"message": "x"}), channel="beta"))
        self.assertEqual(seen[0], "https://api.github.com/repos/o/r/releases/latest")
        self.assertEqual(seen[1], "https://api.github.com/repos/o/r/releases?per_page=30")


class Channel(unittest.TestCase):
    def test_the_channel_is_a_state_file_and_switching_forgets_the_cached_check(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(updates.channel(t), "stable")
            asked = []
            fetch = lambda repo, **kw: asked.append(kw) or {"tag": "v0.3.0-beta.1", "url": "https://github.com/o/r/x"}
            updates.refresh(t, "o/r", fetch=fetch)
            self.assertTrue(updates.set_channel(t, "beta"))
            self.assertEqual((Path(t) / "UPDATE_CHANNEL").read_text().strip(), "beta")
            self.assertEqual(updates.channel(t), "beta")
            self.assertTrue(updates.due(t))                                    # the stable answer is gone
            updates.refresh(t, "o/r", fetch=fetch, token="k")
            self.assertEqual(asked, [{}, {"token": "k", "channel": "beta"}])
            self.assertEqual(updates.available(t, "0.2.0")["tag"], "v0.3.0-beta.1")
            self.assertFalse(updates.set_channel(t, "nightly"))
            self.assertTrue(updates.set_channel(t, "stable"))
            self.assertFalse((Path(t) / "UPDATE_CHANNEL").exists())
            (Path(t) / "UPDATE_CHANNEL").write_text("weird\n")
            self.assertEqual(updates.channel(t), "stable")


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
        views.UPDATE.update(tag="", url="")                                   # another test's page view may have set it
        self.assertEqual(views.update_banner(), "")
        views.UPDATE.update(tag="v0.2.0", url="https://github.com/o/r/releases/tag/v0.2.0")
        self.assertIn('href="/updates"', views.update_banner())
        views.UPDATE.update(tag="<script>", url="https://github.com/o/r")
        self.assertEqual(views.update_banner(), "")
        views.UPDATE.update(tag="v0.2.0", url="javascript:alert(1)")
        self.assertEqual(views.update_banner(), "")


if __name__ == "__main__":
    unittest.main()
