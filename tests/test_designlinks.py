import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import designlinks as D
from factory.config import Config, DesignLinksCfg, RunnerCfg
from factory.render import PNG_MAGIC

HOSTS = ("claude.ai",)
PNG = PNG_MAGIC + b"\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") + (100).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"x" * 30
OK = (200, {"content-type": "text/html; charset=utf-8"}, b"<html>hi</html>")


class Urls(unittest.TestCase):
    def test_only_listed_https_hosts(self):
        self.assertEqual(D.check_url("https://claude.ai/design/a?x=1#frag", HOSTS), "https://claude.ai/design/a?x=1")
        for bad in ("http://claude.ai/a", "https://evil.com/a", "https://claude.ai.evil.com/a", "https://claude.ai:8443/a",
                    "https://user:pw@claude.ai/a", "https://claude.ai@evil.com/a", "https://clаude.ai/a", "https://claude.ai/a b",
                    "https://claude.ai\\@evil.com/", "ftp://claude.ai/a"):
            self.assertIsNone(D.check_url(bad, HOSTS), bad)

    def test_extract_limits_and_dedupes(self):
        body = ("see https://claude.ai/a, and https://claude.ai/a again, https://evil.com/x https://claude.ai/b) https://claude.ai/c "
                "https://claude.ai/d")
        self.assertEqual(D.extract(body, HOSTS, 3), ["https://claude.ai/a", "https://claude.ai/b", "https://claude.ai/c"])
        self.assertEqual(D.extract("", HOSTS, 3), [])

    def test_public_ip(self):
        for a in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "::ffff:127.0.0.1", "fe80::1", "0.0.0.0"):
            self.assertFalse(D.public_ip(a), a)
        self.assertTrue(D.public_ip("93.184.216.34"))

    def test_resolve_refuses_a_private_answer(self):
        info = lambda ip: [(2, 1, 6, "", (ip, 443))]
        with mock.patch("socket.getaddrinfo", return_value=info("127.0.0.1")):
            with self.assertRaises(ValueError):
                D.resolve("claude.ai")
        with mock.patch("socket.getaddrinfo", return_value=info("93.184.216.34") + info("10.0.0.1")):
            with self.assertRaises(ValueError):
                D.resolve("claude.ai")
        with mock.patch("socket.getaddrinfo", return_value=info("93.184.216.34")):
            self.assertEqual(D.resolve("claude.ai"), "93.184.216.34")


class Fetch(unittest.TestCase):
    def go(self, *answers, url="https://claude.ai/a", max_bytes=1000):
        seq, urls = list(answers), []

        def get(u, n):
            urls.append(u)
            a = seq.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        return D.fetch(url, HOSTS, max_bytes, get), urls

    def test_ok(self):
        (st, _, body), _ = self.go(OK)
        self.assertEqual((st, body), ("ok", b"<html>hi</html>"))

    def test_redirect_is_rechecked(self):
        (st, _, body), urls = self.go((302, {"location": "/b"}, b""), OK)
        self.assertEqual((st, urls[-1]), ("ok", "https://claude.ai/b"))
        (st, _, body), urls = self.go((302, {"location": "https://evil.com/x"}, b""), OK)
        self.assertEqual((st, body, urls), ("refused", None, ["https://claude.ai/a"]))
        (st, _, _), _ = self.go(*[(302, {"location": "/b"}, b"")] * 5)
        self.assertEqual(st, "failed")

    def test_refusals(self):
        self.assertEqual(self.go(ValueError())[0][0], "refused")                      # not a public address
        self.assertEqual(self.go(OSError())[0][0], "failed")
        self.assertEqual(self.go((200, {"content-type": "text/html"}, b"x" * 1001))[0][0], "too-large")
        self.assertEqual(self.go((200, {"content-type": "application/zip"}, b"PK"))[0][0], "refused")
        self.assertEqual(self.go((200, {"content-type": "text/html"}, b"a\x00b"))[0][0], "refused")
        self.assertEqual(self.go((200, {"content-type": "text/html"}, b"\xff\xfe"))[0][0], "refused")
        self.assertEqual(self.go((404, {}, b"secret remote text"))[0][:2], ("failed", "the link answered HTTP 404"))
        self.assertEqual(self.go(OK, url="http://claude.ai/a")[0][0], "refused")


class Refresh(unittest.TestCase):
    def setUp(self):
        t = tempfile.mkdtemp()
        self.db = dbm.connect(str(Path(t) / "f.db"))
        self.cfg = Config.__new__(Config)
        object.__setattr__(self.cfg, "design_links", DesignLinksCfg(enabled=True, hosts=HOSTS))
        self.calls = []

    def run_refresh(self, body, now=1000.0, get=None):
        def default_get(u, n):
            self.calls.append(u)
            return OK
        return D.refresh(self.db, self.cfg, RunnerCfg(), "o/r", 5, body, get or default_get,
                         render=lambda rn, docs: {k: PNG for k in docs}, now=lambda: now)

    def test_fetches_stores_renders_and_reuses_within_ttl(self):
        got = self.run_refresh("https://claude.ai/a https://evil.com/b")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["html"], got[0]["png"]), (OK[2], PNG))
        self.assertEqual(self.calls, ["https://claude.ai/a"])
        self.run_refresh("https://claude.ai/a", now=2000.0)
        self.assertEqual(len(self.calls), 1)
        self.run_refresh("https://claude.ai/a", now=1000.0 + 25 * 3600)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([i["has_png"] for i in dbm.design_imports(self.db, "o/r", 5)], [1])
        got = dbm.design_imports(self.db, "o/r", 5)
        self.assertEqual(dbm.design_import(self.db, got[0]["id"])["html"], OK[2])
        self.assertIsNone(dbm.design_import(self.db, 999))

    def test_a_failed_fetch_leaves_no_content(self):
        got = self.run_refresh("https://claude.ai/a", get=lambda u, n: (500, {}, b"x"))
        self.assertEqual(got, [])
        self.assertEqual(dbm.design_imports(self.db, "o/r", 5), [])
        self.assertEqual(self.db.execute("SELECT status, html FROM design_imports").fetchone(), ("failed", None))

    def test_a_blanked_restore_is_fetched_again(self):
        self.run_refresh("https://claude.ai/a")
        self.db.execute("UPDATE design_imports SET html='', png=''")
        self.assertEqual(dbm.design_imports(self.db, "o/r", 5), [])
        self.assertEqual(len(self.run_refresh("https://claude.ai/a", now=1001.0)), 1)

    def test_a_bad_png_is_not_stored(self):
        D.refresh(self.db, self.cfg, RunnerCfg(), "o/r", 5, "https://claude.ai/a", lambda u, n: OK,
                  render=lambda rn, docs: {k: b"not a png" for k in docs}, now=lambda: 1.0)
        self.assertEqual(self.db.execute("SELECT png FROM design_imports").fetchone()[0], None)


class Views(unittest.TestCase):
    def test_links_use_only_integer_ids(self):
        from factory.ui import views
        out = views.import_links([{"id": 7, "has_png": 1}, {"id": "7;<x>", "has_png": 0}, {"has_png": 1}])
        self.assertIn("/design-import?id=7", out)
        self.assertIn("/design-import/png?id=7", out)
        self.assertNotIn("<x>", out)
        self.assertEqual(views.import_links([]), "")


class Config_(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(DesignLinksCfg().enabled)


if __name__ == "__main__":
    unittest.main()
