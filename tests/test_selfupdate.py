"""Updating from the UI: the transient unit that runs update-native.sh, the nightly timer and its mode, and the Updates page."""
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from factory import selfupdate as SU
from factory import updates

from test_ui import UiCase


class Recorder:
    """A stand-in for subprocess.run that records each command and answers from a table (default: success)."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc, err = next(((v if isinstance(v, tuple) else (v, "")) for k, v in self.answers.items() if k in " ".join(cmd)), (0, ""))
        return subprocess.CompletedProcess(cmd, rc, "", err)


def install(root: Path) -> Path:
    script = root / "app" / "deploy" / "update-native.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    (root / "app" / "deploy" / "systemd").mkdir()
    for f in SU.SERVICE_FILES:
        (root / "app" / "deploy" / "systemd" / f).write_text(f"unit {f}\n")
    (root / "state").mkdir(exist_ok=True)
    return script


class Core(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.state = self.root / "state"
        self.which = lambda name: "/usr/bin/" + name

    def test_only_an_install_with_the_script_and_systemd_is_native(self):
        self.assertFalse(SU.native(self.state, self.which))
        install(self.root)
        self.assertTrue(SU.native(self.state, self.which))
        self.assertFalse(SU.native(self.state, lambda n: None))

    def test_the_update_runs_as_its_own_unit_so_restarting_the_ui_cannot_kill_it(self):
        install(self.root)
        run = Recorder({"is-active": 3})                       # not running
        with mock.patch.object(SU, "native", return_value=True):
            self.assertEqual(SU.start(self.state, "v0.21.0", run), "")
        cmd = run.calls[-1]
        self.assertEqual(cmd[:4], ["systemd-run", "--user", "--collect", "--unit"])
        self.assertIn(SU.UNIT, cmd)
        self.assertEqual(cmd[-2:], [str(self.root / "app/deploy/update-native.sh"), "v0.21.0"])
        self.assertIn(f"StandardOutput=truncate:{self.state / SU.LOG}", cmd)

    def test_a_tag_is_checked_before_it_reaches_a_command_line(self):
        install(self.root)
        run = Recorder()
        for bad in ("latest", "v1.2", "v1.2.3; rm -rf /", "../v1.2.3", "V1.2.3"):
            self.assertIn("release tag", SU.start(self.state, bad, run), bad)
        self.assertEqual(run.calls, [])

    def test_one_update_at_a_time_and_not_on_other_installs(self):
        install(self.root)
        with mock.patch.object(SU, "native", return_value=True):
            self.assertIn("already running", SU.start(self.state, "v0.21.0", Recorder({"is-active": 0})))
        self.assertIn("cannot update itself", SU.start(self.root / "elsewhere" / "state", "v0.21.0", Recorder()))

    def test_a_failure_to_start_says_why(self):
        install(self.root)
        run = Recorder({"is-active": 3, "systemd-run": (1, "Failed to connect to bus")})
        with mock.patch.object(SU, "native", return_value=True):
            self.assertIn("Failed to connect to bus", SU.start(self.state, "", run))

    def test_log_tail_is_cleaned(self):
        (self.state).mkdir(exist_ok=True)
        (self.state / SU.LOG).write_bytes(b"a\x00b\x1b[0m\nNow on v0.21.0.\n")
        self.assertNotIn("\x00", SU.log_tail(self.state))
        self.assertIn("Now on v0.21.0.", SU.log_tail(self.state))
        self.assertEqual(SU.log_tail(self.root / "none" / "state"), "")

    def test_auto_update_modes(self):
        install(self.root)
        home = self.root / "home"
        run = Recorder()
        with mock.patch.object(SU, "native", return_value=True):
            self.assertEqual(SU.set_auto(self.state, "all", run, home), "")
            self.assertEqual((self.state / SU.AUTO_FILE).read_text().strip(), "all")
            self.assertEqual((home / ".config/systemd/user/shikumi-update.timer").read_text(), "unit shikumi-update.timer\n")
            self.assertIn(["systemctl", "--user", "enable", "--now", SU.TIMER], run.calls)
            self.assertEqual(SU.auto_mode(self.state, Recorder({"is-enabled": 0})), "all")
            self.assertEqual(SU.auto_mode(self.state, Recorder({"is-enabled": 1})), "off")
            run.calls.clear()
            self.assertEqual(SU.set_auto(self.state, "off", run, home), "")
            self.assertIn(["systemctl", "--user", "disable", "--now", SU.TIMER], run.calls)
            self.assertIn("Pick off", SU.set_auto(self.state, "weekly", run, home))
        (self.state / SU.AUTO_FILE).unlink()
        with mock.patch.object(SU, "native", return_value=True):
            self.assertEqual(SU.auto_mode(self.state, Recorder({"is-enabled": 0})), "patch")      # no file: the patch-only default


class Page(UiCase):
    def setUp(self):
        super().setUp()
        p = mock.patch("factory.updates.refresh")          # the page-view release check must not race the cache these tests write (nor reach GitHub)
        p.start()
        self.addCleanup(p.stop)
        self.cookie, self.csrf = self.session()
        self.state_dir = Path(self.db_path).parent

    def known(self, tag="v9.9.9"):
        (self.state_dir / "update_check.json").write_text(json.dumps({"checked": time.time(), "latest": {"tag": tag, "url": f"https://github.com/x/y/releases/tag/{tag}"}}))

    def get(self, path="/updates"):
        return self.req("GET", path, cookie=self.cookie)

    def post(self, path, **f):
        return self.req("POST", path, urlencode({"csrf": self.csrf, **f}), cookie=self.cookie)

    def test_a_non_native_install_says_how_to_update_instead(self):
        self.known()
        html = self.get()[2]
        self.assertIn("v9.9.9</strong> is available", html)
        self.assertIn("cannot update itself from here", html)
        self.assertNotIn(">Update to v9.9.9<", html)
        self.assertNotIn("Update by itself", html)
        self.assertIn('href="/updates"', html)                                           # in the settings list
        self.assertEqual(self.post("/updates/apply", tag="v9.9.9")[0], 303)
        self.assertIn("This install cannot update itself", self.get()[2])

    def test_a_native_install_offers_update_now_and_the_nightly_modes(self):
        self.known()
        with mock.patch.object(SU, "native", return_value=True), mock.patch.object(SU, "running", return_value=False), \
                mock.patch.object(SU, "auto_mode", return_value="all"):
            html = self.get()[2]
        self.assertIn(">Update to v9.9.9<", html)
        self.assertIn('name="mode" value="all" checked', html)
        self.assertIn("Every release", html)

    def test_update_now_starts_it_only_for_the_release_that_is_offered(self):
        self.known()
        with mock.patch.object(SU, "start", return_value="") as start:
            self.assertEqual(self.post("/updates/apply", tag="v1.2.3")[0], 303)
            start.assert_not_called()
            self.assertIn("no longer the newest", self.get()[2])
            self.post("/updates/apply", tag="v9.9.9")
            start.assert_called_once_with(self.state_dir, "v9.9.9")
        with mock.patch.object(SU, "start", return_value="An update is already running."):
            self.post("/updates/apply", tag="v9.9.9")
            self.assertIn("already running", self.get()[2])

    def test_check_now_reads_the_release_with_the_token_and_says_what_it_found(self):
        seen = {}

        def fake(state, repo, token="", **kw):
            seen["token"] = token
            (Path(state) / "update_check.json").write_text(json.dumps({"checked": time.time(), "latest": {"tag": "v9.9.9", "url": "https://github.com/x/y/r"}}))
        tf = self.root / "secrets" / "github_token"
        tf.parent.mkdir(exist_ok=True)
        tf.write_text("ghp_privaterepo\n")
        with mock.patch("factory.updates.refresh", fake):
            self.assertEqual(self.post("/updates/check")[0], 303)
        self.assertEqual(seen["token"], "ghp_privaterepo")
        self.assertIn("v9.9.9 is available", self.get()[2])

    def test_check_now_says_so_when_github_cannot_be_read(self):
        def fake(state, repo, token="", **kw):
            (Path(state) / "update_check.json").write_text(json.dumps({"checked": time.time(), "latest": None}))
        with mock.patch("factory.updates.refresh", fake):
            self.post("/updates/check")
        self.assertIn("Could not read the releases", self.get()[2])

    def test_auto_update_is_saved_through_the_unit_helpers(self):
        with mock.patch.object(SU, "set_auto", return_value="") as sa:
            self.post("/updates/auto", mode="all")
            sa.assert_called_once_with(self.state_dir, "all")
            self.assertIn("Every new release will install itself", self.get()[2])
        with mock.patch.object(SU, "set_auto", return_value="Pick off, patch releases or every release."):
            self.post("/updates/auto", mode="weekly")
            self.assertIn("Pick off", self.get()[2])

    def test_the_running_update_shows_its_log_escaped_and_refreshes_itself(self):
        (self.state_dir / SU.LOG).write_text("Updating: v0.20.0 -> v9.9.9\n<script>x</script>\n")
        with mock.patch.object(SU, "running", return_value=True):
            frag = self.get("/updates/status")[2]
            page = self.get()[2]
        self.assertIn("Updating to v9.9.9", frag)
        self.assertIn("Step 1 of 6", frag)          # no marker yet
        self.assertIn("&lt;script&gt;", frag)
        self.assertNotIn("<script>x", frag)
        self.assertIn('data-src="/updates/status"', page)
        with mock.patch.object(SU, "running", return_value=False):
            self.assertIn("did not finish", self.get("/updates/status")[2])
            (self.state_dir / SU.LOG).write_text("Now on v9.9.9.\n")
            self.assertIn("finished", self.get("/updates/status")[2])

    def test_stage_markers_drive_the_step_list_and_hostile_lines_do_not(self):
        from factory.ui import updatespage as UP
        log = ("Updating: v0.20.0 -> v9.9.9\n##STAGE 1/6 pause\n##STAGE 2/6 download\n"
               "##STAGE 9/6 x\n  ##STAGE 6/6 health\n##STAGE 5/6 <b>x</b>\n")
        self.assertEqual(UP.parse_log(log)[:2], (2, "v9.9.9"))
        (self.state_dir / SU.LOG).write_text(log)
        with mock.patch.object(SU, "running", return_value=True):
            frag = self.get("/updates/status")[2]
        self.assertIn("Step 2 of 6: <strong>Download and check the release</strong>", frag)
        self.assertIn('aria-valuenow="2"', frag)
        self.assertNotIn("<b>x</b>", frag)
        with mock.patch.object(SU, "running", return_value=False):
            (self.state_dir / SU.LOG).write_text("##STAGE 2/6 download\nCHECK FAILED on v9.9.9: it does not start on this host, nothing was changed\n")
            frag = self.get("/updates/status")[2]
            self.assertIn("did not finish", frag)
            self.assertIn("✕", frag)
            (self.state_dir / SU.LOG).write_text("##STAGE 6/6 health\nThe new version did not come up healthy: rolling back to v0.20.0\n")
            self.assertIn("rolled back", self.get("/updates/status")[2])

    def test_everything_needs_a_session_and_a_csrf_token(self):
        for path in ("/updates", "/updates/status"):
            self.assertEqual(self.req("GET", path)[0], 303)
        for path in ("/updates/check", "/updates/apply", "/updates/auto"):
            self.assertEqual(self.req("POST", path, "tag=v9.9.9")[0], 303, path)
            self.assertEqual(self.req("POST", path, "tag=v9.9.9", cookie=self.cookie)[0], 403, path)

    def test_the_banner_links_to_the_page(self):
        self.known()
        self.app.refresh_update_notice()
        html = self.req("GET", "/runs", cookie=self.cookie)[2]
        self.assertIn('<a href="/updates">Update</a>', html)


if __name__ == "__main__":
    unittest.main()
