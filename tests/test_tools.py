import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from factory import tools as T
from factory.config import RunnerCfg, default_harnesses
from test_ui_admin import AdminCase


def cfg(**over):
    rn = RunnerCfg()
    return NS(runner=rn, harnesses=default_harnesses(rn), dry_run=False, **over)


class FakeEngine:
    """Answers the container engine's commands. `versions` maps an image name to what `--version` prints."""
    def __init__(self, versions, build_ok=True, images=None):
        self.versions, self.build_ok, self.images, self.calls = dict(versions), build_ok, set(images or versions), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out, code, err = "", 0, ""
        if cmd[1:3] == ["image", "inspect"]:
            code = 0 if cmd[3] in self.images else 1
        elif cmd[1] == "build":
            code, err = (0, "") if self.build_ok else (1, "npm ERR! network")
            if code == 0:
                self.images.add(cmd[cmd.index("-t") + 1])
        elif cmd[1] == "run":
            v = self.versions.get(cmd[cmd.index("--version") - 1])
            out, code = (f"{v} (Claude Code)\n", 0) if v else ("", 126)
        elif cmd[1] == "tag":
            self.versions[cmd[3]] = self.versions.get(cmd[2])
            self.images.add(cmd[3])
        return subprocess.CompletedProcess(cmd, code, out, err)


IMG = "localhost/factory-agent:latest"
CAND, PREV = "localhost/factory-agent:candidate", "localhost/factory-agent:previous"


class Update(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def test_update_builds_a_candidate_checks_it_then_moves_the_tag(self):
        eng = FakeEngine({IMG: "2.1.0", CAND: "2.2.0"}, images={IMG})
        ok, msg = T.update_one(cfg(), self.state, "claude-code", eng, now=lambda: 1234)
        self.assertTrue(ok, msg)
        self.assertIn("2.1.0 to 2.2.0", msg)
        build = next(c for c in eng.calls if c[1] == "build")
        self.assertIn("TOOL_REFRESH=1234", build)                               # what busts the cached npm layer
        self.assertEqual(eng.versions[IMG], "2.2.0")
        self.assertEqual(eng.versions[PREV], "2.1.0")                           # the old image is kept for a rollback
        self.assertEqual(eng.calls[-1][1:], ["image", "prune", "-f"])         # and the one it replaced does not pile up

    def test_a_new_cli_that_does_not_start_changes_nothing(self):
        eng = FakeEngine({IMG: "2.1.0"}, images={IMG})                          # the candidate prints no version
        ok, msg = T.update_one(cfg(), self.state, "claude-code", eng)
        self.assertFalse(ok)
        self.assertIn("does not start", msg)
        self.assertEqual(eng.versions[IMG], "2.1.0")
        self.assertNotIn(PREV, eng.versions)

    def test_a_failed_build_changes_nothing(self):
        eng = FakeEngine({IMG: "2.1.0"}, build_ok=False)
        ok, msg = T.update_one(cfg(), self.state, "claude-code", eng)
        self.assertFalse(ok)
        self.assertIn("build failed", msg)
        self.assertEqual(eng.versions[IMG], "2.1.0")

    def test_already_current_keeps_the_image_and_makes_no_previous(self):
        eng = FakeEngine({IMG: "2.1.0", CAND: "2.1.0"}, images={IMG})
        ok, msg = T.update_one(cfg(), self.state, "claude-code", eng)
        self.assertTrue(ok)
        self.assertIn("already on the newest", msg)
        self.assertNotIn(PREV, eng.versions)

    def test_a_missing_or_custom_image_is_left_alone(self):
        ok, msg = T.update_one(cfg(), self.state, "codex", FakeEngine({}))
        self.assertFalse(ok)
        self.assertIn("not built here", msg)
        c = cfg()
        c.harnesses["codex"] = c.harnesses["codex"].__class__(**{**c.harnesses["codex"].__dict__, "image": "registry.example/mine:1"})
        eng = FakeEngine({"registry.example/mine:1": "1.0.0"})
        ok, msg = T.update_one(c, self.state, "codex", eng)
        self.assertFalse(ok)
        self.assertIn("custom image", msg)
        self.assertFalse([x for x in eng.calls if x[1] == "build"])

    def test_nothing_from_a_request_reaches_the_command_line_but_known_names(self):
        self.assertEqual(T.request(self.state, ["claude-code", "; rm -rf /", "claude-code", "codex"]), ["claude-code", "codex"])
        self.assertEqual(T.requested(self.state), ["claude-code", "codex"])
        self.assertEqual(T.request(self.state, ["nope"]), [])


class Tick(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def run_tick(self, eng, **kw):
        ran = []
        started = T.tick(cfg(), self.state, eng, spawn=lambda f: (ran.append(1), f()), **kw)
        return started, bool(ran)

    def test_a_request_is_consumed_and_the_result_recorded(self):
        eng = FakeEngine({IMG: "2.1.0", CAND: "2.2.0"}, images={IMG})
        T.request(self.state, ["claude-code"])
        self.assertEqual(self.run_tick(eng), (True, True))
        self.assertEqual(T.requested(self.state), [])
        st = T.read(self.state)
        self.assertTrue(st["last"]["ok"])
        self.assertEqual(st["building"], "")
        self.assertEqual(st["installed"]["claude-code"]["version"], "2.2.0")

    def test_idle_with_a_recent_scan_does_nothing(self):
        T._update_state(self.state, scanned=1000)
        self.assertEqual(self.run_tick(FakeEngine({}), now=lambda: 1001), (False, False))

    def test_first_start_scans_the_installed_versions(self):
        self.assertEqual(self.run_tick(FakeEngine({IMG: "2.1.0"})), (True, True))
        self.assertEqual(T.read(self.state)["installed"], {"claude-code": {"version": "2.1.0", "managed": True}})

    def test_dry_run_never_builds(self):
        T.request(self.state, ["claude-code"])
        c = cfg()
        c.dry_run = True
        self.assertFalse(T.tick(c, self.state, FakeEngine({}), spawn=lambda f: f()))


class Versions(unittest.TestCase):
    def test_comparison(self):
        self.assertTrue(T.outdated("2.1.0", "2.1.1"))
        self.assertTrue(T.outdated("0.9.9", "0.10.0"))
        self.assertFalse(T.outdated("2.1.0", "2.1.0"))
        self.assertFalse(T.outdated("", "2.1.0"))

    def test_registry_answer_is_checked(self):
        class R:
            def __init__(s, d): s.d = d
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s, n): return json.dumps(s.d).encode()
        seen = []
        def opener(req, timeout):
            seen.append(req.full_url)
            return R({"version": "2.3.4"})
        self.assertEqual(T.latest_version("@anthropic-ai/claude-code", opener), "2.3.4")
        self.assertEqual(seen, ["https://registry.npmjs.org/@anthropic-ai%2Fclaude-code/latest"])
        self.assertEqual(T.latest_version("x", lambda r, timeout: R({"version": "latest; rm"})), "")
        def boom(r, timeout): raise OSError("offline")
        self.assertEqual(T.latest_version("x", boom), "")

    def test_base_name(self):
        self.assertEqual(T._base("localhost/factory-agent:latest"), "localhost/factory-agent")
        self.assertEqual(T._base("host:5000/img"), "host:5000/img")
        self.assertEqual(T._base("host:5000/img:1"), "host:5000/img")


class Page(AdminCase):
    def test_harnesses_page_shows_versions_and_the_update_button_only_requests(self):
        T._update_state(self.state, installed={"claude-code": {"version": "2.1.0", "managed": True}, "codex": {"version": "0.5.0", "managed": False}})
        (self.state / T.LATEST).write_text(json.dumps({"checked": 9e9, "latest": {"claude-code": "2.2.0", "codex": "0.6.0"}}))
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/harnesses", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("Agent tools", "2.1.0", "2.2.0", "update available", "custom image", "Update Claude Code"):
            self.assertIn(needle, html)
        self.assertNotIn("Update Codex CLI", html)                    # a custom image is not rebuilt from here
        self.assertEqual(self.req("POST", "/tools/update", "name=claude-code", cookie=cookie)[0], 403)      # no CSRF token
        s, h, _ = self.post(cookie, csrf, "/tools/update", {"name": "claude-code"})
        self.assertEqual((s, h["Location"]), (303, "/harnesses"))
        self.assertEqual(T.requested(self.state), ["claude-code"])
        self.assertIn("queued", self.req("GET", "/harnesses", cookie=cookie)[2])

    def test_unknown_tool_is_refused(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/tools/update", {"name": "../../etc"})
        self.assertEqual(T.requested(self.state), [])


if __name__ == "__main__":
    unittest.main()
