"""Updating verification workers from the factory: which release a worker should move to, how it gets the files without GitHub access,
and how it installs them."""
import base64
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from factory import db as dbm
from factory import jobs, releasefiles, workerapi, workerupdate

from test_ui import UiCase
from test_workerapi import TOKEN, cfg_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                      # noqa: E402


class Decide(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.state = Path(self._t.name)
        self.db = dbm.connect(":memory:")
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 1.0, "0.20.0")

    def test_the_release_a_worker_reports_is_kept_and_junk_is_ignored(self):
        row = lambda: self.db.execute("SELECT app_version FROM workers WHERE name='mac'").fetchone()[0]
        self.assertEqual(row(), "0.20.0")
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 2.0)                       # an old worker that does not say
        self.assertEqual(row(), "0.20.0")
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 3.0, "1.2.3; drop")
        self.assertEqual(row(), "")
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 4.0, "0.21.0")
        self.assertEqual(row(), "0.21.0")

    def test_behind_means_older_than_the_factory_and_known(self):
        self.assertTrue(workerupdate.behind("0.20.0", "0.21.0"))
        self.assertFalse(workerupdate.behind("0.21.0", "0.21.0"))
        self.assertFalse(workerupdate.behind("0.22.0", "0.21.0"))
        self.assertFalse(workerupdate.behind("", "0.21.0"))                               # unknown: cannot self-update
        self.assertFalse(workerupdate.behind("0.20.0", "dev"))

    def test_nobody_updates_unless_asked_or_auto_is_on(self):
        self.assertEqual(workerupdate.wanted(self.db, self.state, "mac", "0.20.0", "0.21.0"), "")
        self.assertTrue(workerupdate.request(self.db, "mac", 5.0))
        self.assertEqual(workerupdate.wanted(self.db, self.state, "mac", "0.20.0", "0.21.0"), "v0.21.0")
        self.assertEqual(workerupdate.wanted(self.db, self.state, "other", "0.20.0", "0.21.0"), "")
        workerupdate.set_auto(self.state, True)
        self.assertEqual(workerupdate.wanted(self.db, self.state, "other", "0.20.0", "0.21.0"), "v0.21.0")
        workerupdate.set_auto(self.state, False)
        self.assertFalse(workerupdate.auto(self.state))

    def test_a_request_is_cleared_once_the_worker_has_caught_up_and_only_known_workers_can_be_asked(self):
        workerupdate.request(self.db, "mac", 5.0)
        self.assertEqual(workerupdate.wanted(self.db, self.state, "mac", "0.21.0", "0.21.0"), "")
        self.assertFalse(workerupdate.requested(self.db, "mac"))
        self.assertFalse(workerupdate.request(self.db, "ghost", 1.0))
        self.assertFalse(workerupdate.request(self.db, "../x", 1.0))


class Files(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.state = Path(self._t.name)
        self.calls = []

    def fetch(self, data):
        def f(repo, tag, name, token):
            self.calls.append((repo, tag, name, token))
            return data
        return f

    def test_a_file_is_fetched_once_with_the_token_then_served_from_the_cache(self):
        tar = gzip.compress(b"x")
        self.assertEqual(releasefiles.get(self.state, "o/r", "v0.21.0", "shikumi-worker.tar.gz", "tok", self.fetch(tar)), tar)
        self.assertEqual(releasefiles.get(self.state, "o/r", "v0.21.0", "shikumi-worker.tar.gz", "tok", self.fetch(b"other")), tar)
        self.assertEqual(self.calls, [("o/r", "v0.21.0", "shikumi-worker.tar.gz", "tok")])

    def test_only_the_four_worker_files_of_a_real_tag_and_only_if_they_check_out(self):
        g = lambda tag, name, data: releasefiles.get(self.state, "o/r", tag, name, "t", self.fetch(data))
        self.assertIsNone(g("latest", "VERSION", b"0.21.0\n"))
        self.assertIsNone(g("v0.21.0", "../../etc/passwd", b"x"))
        self.assertIsNone(g("v0.21.0", "config.example.toml", b"x"))
        self.assertIsNone(g("v0.21.0", "VERSION", b"0.20.0\n"))                           # the file does not name the tag
        self.assertIsNone(g("v0.21.0", "shikumi-worker.tar.gz", b"<html>not a tarball"))
        self.assertIsNone(g("v0.21.0", "update-worker.sh", b""))
        self.assertEqual(g("v0.21.0", "VERSION", b"0.21.0\n"), b"0.21.0\n")
        self.assertFalse(any(p.name == ".." for p in self.state.rglob("*")))

    def test_a_fetch_failure_is_none_not_an_exception(self):
        def boom(*a):
            raise OSError("down")
        self.assertIsNone(releasefiles.get(self.state, "o/r", "v0.21.0", "VERSION", "t", boom))

    def test_the_redirect_to_storage_is_followed_without_the_token(self):
        seen = []

        def get(url, headers, opener=None):
            seen.append((url, dict(headers), opener is not None and type(opener.handlers[-1]).__name__ if opener else None))
            if "/releases/tags/" in url:
                return json.dumps({"assets": [{"name": "VERSION", "id": 7}]}).encode()
            if url.endswith("/releases/assets/7"):
                import email.message, urllib.error
                m = email.message.Message()
                m["Location"] = "https://objects.example/signed?x=1"
                raise urllib.error.HTTPError(url, 302, "Found", m, None)
            return b"0.21.0\n"
        with mock.patch.object(releasefiles, "_get", get):
            self.assertEqual(releasefiles.download("o/r", "v0.21.0", "VERSION", "secret"), b"0.21.0\n")
        self.assertEqual(seen[0][1]["Authorization"], "Bearer secret")
        self.assertEqual(seen[1][1]["Authorization"], "Bearer secret")
        self.assertEqual(seen[2][0], "https://objects.example/signed?x=1")
        self.assertNotIn("Authorization", seen[2][1])                                      # the storage service refuses a request that has one
        with mock.patch.object(releasefiles, "_get", lambda *a, **k: json.dumps({"assets": []}).encode()):
            with self.assertRaises(FileNotFoundError):
                releasefiles.download("o/r", "v0.21.0", "VERSION", "t")


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = cfg_for(self.tmp)
        self.api = workerapi.Api(lambda: self.cfg, clock=lambda: 1000.0)
        self.db = dbm.connect(self.cfg.db_path)
        self.addCleanup(self.db.close)
        p = mock.patch("factory.version.current", return_value="0.21.0")
        p.start()
        self.addCleanup(p.stop)

    def call(self, path, body):
        return self.api.handle("POST", path, f"Bearer {TOKEN}", json.dumps(body).encode())

    def test_the_release_a_worker_polls_with_is_recorded(self):
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"], "version": 1, "app_version": "0.20.0"})
        self.assertEqual(self.db.execute("SELECT app_version FROM workers WHERE name='mac'").fetchone()[0], "0.20.0")

    def test_update_is_offered_only_to_a_worker_that_is_behind_and_was_asked(self):
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"], "version": 1, "app_version": "0.20.0"})
        self.assertEqual(self.call("/v1/update", {"app_version": "0.20.0"}), (200, {"update": False, "tag": "", "files": []}))
        workerupdate.request(self.db, "mac", 1.0)
        s, r = self.call("/v1/update", {"app_version": "0.20.0"})
        self.assertEqual((s, r["update"], r["tag"]), (200, True, "v0.21.0"))
        self.assertEqual(sorted(r["files"]), sorted(releasefiles.FILES))
        self.assertFalse(self.call("/v1/update", {"app_version": "0.21.0"})[1]["update"])
        self.assertFalse(self.call("/v1/update", {"app_version": "garbage"})[1]["update"])
        self.assertFalse(self.call("/v1/update", {})[1]["update"])

    def test_nothing_is_offered_by_a_development_build(self):
        workerupdate.request(self.db, "mac", 1.0)
        with mock.patch("factory.version.current", return_value="dev"):
            self.assertFalse(self.call("/v1/update", {"app_version": "0.20.0"})[1]["update"])
            self.assertEqual(self.call("/v1/release", {"tag": "vdev", "name": "VERSION"})[0], 404)

    def test_release_files_are_served_only_for_the_factorys_own_release(self):
        with mock.patch.object(releasefiles, "get", return_value=b"0.21.0\n") as get:
            s, r = self.call("/v1/release", {"tag": "v0.21.0", "name": "VERSION"})
            self.assertEqual((s, base64.b64decode(r["b64"])), (200, b"0.21.0\n"))
            self.assertEqual(get.call_args.args[1:4], (self.cfg.updates.repo, "v0.21.0", "VERSION"))
            for body in ({"tag": "v0.20.0", "name": "VERSION"}, {"tag": "v0.21.0", "name": "worker.toml"}, {"tag": "v0.21.0"}, {"name": "VERSION"}):
                self.assertEqual(self.call("/v1/release", body)[0], 404, body)
        with mock.patch.object(releasefiles, "get", return_value=None):
            self.assertEqual(self.call("/v1/release", {"tag": "v0.21.0", "name": "VERSION"})[0], 502)

    def test_it_needs_a_worker_token(self):
        for path in ("/v1/update", "/v1/release"):
            self.assertEqual(self.api.handle("POST", path, "Bearer wrong", b"{}")[0], 401)


class FakeApi:
    """Answers the worker's update questions like the factory would."""
    def __init__(self, update=True, tag="v0.21.0", files=("VERSION", "shikumi-worker.tar.gz", "setup-worker.sh", "update-worker.sh"), release=200):
        self.update, self.tag, self.files, self.release, self.calls = update, tag, list(files), release, []

    def call(self, path, body, timeout=60):
        self.calls.append((path, body))
        if path == "/v1/update":
            return 200, {"update": self.update, "tag": self.tag, "files": self.files}
        if path == "/v1/release":
            return self.release, {"name": body["name"], "b64": base64.b64encode(b"0.21.0\n" if body["name"] == "VERSION" else b"data").decode()}
        return 404, None


class SelfUpdate(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        (self.root / "scripts").mkdir()
        (self.root / "work").mkdir()
        (self.root / "VERSION").write_text("0.20.0\n")
        # a stand-in for scripts/update-worker.sh: it must be fed the files through SHIKUMI_ASSET_BASE and told not to restart the service
        (self.root / "scripts" / "update-worker.sh").write_text(
            '#!/bin/bash\nset -e\n[ "$SKIP_SERVICE" = 1 ]\nB="${SHIKUMI_ASSET_BASE#file://}"\n[ -f "$B/VERSION" ]\n'
            '[ "$1" = "v$(cat "$B/VERSION")" ]\nif [ -e FAIL ]; then echo "check failed" >&2; exit 1; fi\ncp "$B/VERSION" VERSION\n')
        self.cfg = mock.Mock(work_dir=self.root / "work")
        p = mock.patch.object(W, "app_version", return_value="0.20.0")
        p.start()
        self.addCleanup(p.stop)

    def test_the_files_come_through_the_factory_and_the_install_is_updated(self):
        api = FakeApi()
        self.assertEqual(W.self_update(self.cfg, api, "v0.21.0", api.files, self.root), "")
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.21.0")
        self.assertEqual([c[1]["name"] for c in api.calls], api.files)
        self.assertEqual(list((self.root / "work").glob("factory-update-*")), [])           # the downloads are removed

    def test_exits_so_the_service_starts_the_new_version_only_when_a_service_runs_it(self):
        state = {}
        with mock.patch.object(W, "managed", return_value=True):
            self.assertTrue(W.maybe_update(self.cfg, FakeApi(), state, 1000.0, self.root))
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.21.0")
        (self.root / "VERSION").write_text("0.20.0\n")
        with mock.patch.object(W, "managed", return_value=False):
            self.assertFalse(W.maybe_update(self.cfg, FakeApi(), {}, 1000.0, self.root))      # updated, but it keeps running: restart it by hand

    def test_it_asks_at_most_once_a_minute_and_does_nothing_unless_told_to(self):
        api, state = FakeApi(update=False), {}
        self.assertFalse(W.maybe_update(self.cfg, api, state, 1000.0, self.root))
        self.assertFalse(W.maybe_update(self.cfg, api, state, 1030.0, self.root))
        self.assertEqual(len([c for c in api.calls if c[0] == "/v1/update"]), 1)
        self.assertFalse(W.maybe_update(self.cfg, api, state, 1061.0, self.root))
        self.assertEqual(len(api.calls), 2)
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.20.0")

    def test_a_development_checkout_never_asks(self):
        api = FakeApi()
        with mock.patch.object(W, "app_version", return_value=""):
            self.assertFalse(W.maybe_update(self.cfg, api, {}, 1000.0, self.root))
        self.assertEqual(api.calls, [])

    def test_a_failed_update_leaves_the_worker_running_and_is_not_retried_for_an_hour(self):
        state, api = {}, FakeApi()
        (self.root / "FAIL").write_text("")
        with mock.patch.object(W, "managed", return_value=True):
            self.assertFalse(W.maybe_update(self.cfg, api, state, 1000.0, self.root))
            n = len(api.calls)
            self.assertFalse(W.maybe_update(self.cfg, api, state, 1100.0, self.root))          # asks again, but does not retry the same release
            self.assertEqual([c[0] for c in api.calls[n:]], ["/v1/update"])
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.20.0")
        self.assertEqual(state["failed_tag"], "v0.21.0")
        (self.root / "FAIL").unlink()
        with mock.patch.object(W, "managed", return_value=True):
            self.assertTrue(W.maybe_update(self.cfg, api, state, 1000.0 + W.UPDATE_RETRY + 1, self.root))

    def test_unexpected_files_a_refusal_or_a_missing_script_stop_it_before_anything_runs(self):
        self.assertIn("unexpected file", W.self_update(self.cfg, FakeApi(), "v0.21.0", ["../../etc/passwd"], self.root))
        self.assertIn("HTTP 502", W.self_update(self.cfg, FakeApi(release=502), "v0.21.0", ["VERSION"], self.root))
        self.assertIn("no scripts/update-worker.sh", W.self_update(self.cfg, FakeApi(), "v0.21.0", ["VERSION"], self.root / "nowhere"))
        self.assertIn("no scripts/update-worker.sh", W.self_update(self.cfg, FakeApi(), "latest; rm", ["VERSION"], self.root))
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.20.0")

    def test_managed_means_a_service_manager_started_it(self):
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "abc"}):
            self.assertTrue(W.managed())
        with mock.patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.shikumi.worker"}, clear=True):
            self.assertTrue(W.managed())
        with mock.patch.dict(os.environ, {"XPC_SERVICE_NAME": "0"}, clear=True):
            self.assertFalse(W.managed())

    def test_the_polling_worker_says_which_release_it_runs(self):
        seen = {}

        class A:
            def call(self, path, body, timeout=60):
                seen.update(body)
                return 204, None
        cfg = mock.Mock(platform="linux", recipes={"web-test": 1})
        W.poll_once(cfg, A())
        self.assertEqual(seen["app_version"], "0.20.0")


class Page(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + "\n[workers]\nenabled = true\n")
        self.cookie, self.csrf = self.session()
        jobs.touch_worker(self.db, "mac", "macos", ["web-test"], 1, time.time(), "0.19.0")
        jobs.touch_worker(self.db, "old", "linux", ["web-test"], 1, time.time())
        self.db.commit()
        pp = mock.patch("factory.version.current", return_value="0.21.0")
        pp.start()
        self.addCleanup(pp.stop)

    def page(self):
        return self.req("GET", "/workers", cookie=self.cookie)[2]

    def post(self, path, **f):
        return self.req("POST", path, urlencode({"csrf": self.csrf, **f}), cookie=self.cookie)

    def test_the_workers_page_shows_each_workers_release_and_a_button_for_one_that_is_behind(self):
        html = self.page()
        self.assertIn('data-l="Version">0.19.0 <form', html)
        self.assertIn(">Update to 0.21.0<", html)
        self.assertIn('data-l="Version">—<', html)                                          # a worker that does not report a release

    def test_the_button_asks_and_the_page_says_updating(self):
        s, h, _ = self.post("/workers/update", worker="mac")
        self.assertEqual((s, h["Location"]), (303, "/workers"))
        self.assertTrue(workerupdate.requested(self.db, "mac"))
        html = self.page()
        self.assertIn("updates the next time it is idle", html)
        self.assertIn("updating", html)
        self.assertNotIn(">Update to 0.21.0<", html)

    def test_only_known_workers_can_be_asked(self):
        self.post("/workers/update", worker="ghost")
        self.assertFalse(workerupdate.requested(self.db, "ghost"))
        self.assertIn("not a known worker", self.page())

    def test_automatic_updates_are_a_switch(self):
        state = Path(self.db_path).parent
        self.post("/workers/auto-update", on="1")
        self.assertTrue(workerupdate.auto(state))
        self.assertIn("checked> Update workers by themselves", self.page())
        self.post("/workers/auto-update")
        self.assertFalse(workerupdate.auto(state))

    def test_it_needs_a_session_and_a_csrf_token(self):
        for path in ("/workers/update", "/workers/auto-update"):
            self.assertEqual(self.req("POST", path, "worker=mac")[0], 303)
            self.assertEqual(self.req("POST", path, "worker=mac", cookie=self.cookie)[0], 403)
        self.assertFalse(workerupdate.requested(self.db, "mac"))


if __name__ == "__main__":
    unittest.main()
