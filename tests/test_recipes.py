import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                       # noqa: E402
from test_render import png                                              # noqa: E402

RECIPE = Path(__file__).resolve().parent.parent / "worker" / "recipes" / "web-test.sh"


class WebRecipe(unittest.TestCase):
    """Runs the real script with stub package managers that log what they were asked and fail on request (FAIL_ON='npm test')."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin, self.proj, self.log = self.tmp / "bin", self.tmp / "proj", self.tmp / "calls.log"
        self.bin.mkdir()
        self.proj.mkdir()
        stub = '#!/bin/sh\necho "$(basename "$0") $*" >> "$CALLS"\n[ "$FAIL_ON" = "$(basename "$0") $*" ] && { echo "boom from $0 $*" >&2; exit 1; }\nexit 0\n'
        for name in ("npm", "npx", "pnpm", "yarn", "corepack"):
            f = self.bin / name
            f.write_text(stub)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def project(self, scripts=None, lock="package-lock.json", playwright=False):
        (self.proj / "package.json").write_text(json.dumps({"scripts": scripts or {}}))
        if lock:
            (self.proj / lock).write_text("")
        if playwright:
            (self.proj / "playwright.config.ts").write_text("export default {}")

    def run_recipe(self, *args, fail_on="", cwd=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.log), "FAIL_ON": fail_on, "HOME": str(self.tmp)}
        r = subprocess.run([str(RECIPE), *args], cwd=cwd or self.proj, env=env, capture_output=True, text=True, timeout=60)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r.returncode, r.stdout + r.stderr, calls

    def test_npm_project_installs_builds_and_tests(self):
        self.project({"build": "tsc", "test": "vitest run"})
        code, out, calls = self.run_recipe()
        self.assertEqual((code, calls), (0, ["npm ci --no-audit --no-fund", "npm run build", "npm test"]), out)
        self.assertIn("all steps passed", out)

    def test_package_manager_follows_the_lockfile(self):
        self.project({"test": "x"}, lock="pnpm-lock.yaml")
        self.assertEqual(self.run_recipe()[2], ["corepack pnpm install --frozen-lockfile", "pnpm test"])
        (self.log).unlink()
        (self.proj / "pnpm-lock.yaml").unlink()
        (self.proj / "yarn.lock").write_text("")
        self.assertEqual(self.run_recipe()[2], ["yarn install --frozen-lockfile", "yarn test"])

    def test_no_lockfile_still_runs_but_says_so(self):
        self.project({"test": "x"}, lock=None)
        code, out, calls = self.run_recipe()
        self.assertEqual((code, calls[0]), (0, "npm install --no-audit --no-fund"))
        self.assertIn("no lockfile", out)

    def test_npm_placeholder_test_script_is_not_a_test(self):
        self.project({"test": 'echo "Error: no test specified" && exit 1'})
        code, out, calls = self.run_recipe()
        self.assertEqual(code, 2)                                          # nothing to verify is a recipe problem, not a pass
        self.assertEqual(calls, ["npm ci --no-audit --no-fund"])
        self.assertIn("nothing to run", out)

    def test_failing_step_fails_the_recipe_and_stops_there(self):
        self.project({"build": "b", "test": "t"})
        code, out, calls = self.run_recipe(fail_on="npm test")
        self.assertEqual(code, 1)
        self.assertIn("boom from", out)
        self.assertIn("FAILED: npm test", out)
        self.project({"build": "b", "test": "t"})
        self.log.unlink()
        code, out, calls = self.run_recipe(fail_on="npm run build")
        self.assertEqual((code, "npm test" in calls), (1, False))          # a failed build never reaches the tests

    def test_install_failure_fails_before_anything_else(self):
        self.project({"test": "t"})
        code, _, calls = self.run_recipe(fail_on="npm ci --no-audit --no-fund")
        self.assertEqual((code, calls), (1, ["npm ci --no-audit --no-fund"]))

    def test_playwright_runs_when_configured_and_can_be_skipped(self):
        self.project({"test": "t"}, playwright=True)
        code, out, calls = self.run_recipe()
        self.assertEqual(calls[-2:], ["npx --no-install playwright install chromium", "npx --no-install playwright test --reporter=line"])
        self.log.unlink()
        self.assertFalse(any("playwright" in c for c in self.run_recipe("--no-playwright")[2]))
        self.log.unlink()
        code, _, _ = self.run_recipe(fail_on="npx --no-install playwright test --reporter=line")
        self.assertEqual(code, 1)

    def test_playwright_only_project_is_enough(self):
        self.project({}, playwright=True)
        code, _, calls = self.run_recipe()
        self.assertEqual(code, 0)

    def test_subfolder_option_and_its_validation(self):
        (self.proj / "web").mkdir()
        (self.proj / "web" / "package.json").write_text(json.dumps({"scripts": {"test": "t"}}))
        (self.proj / "web" / "package-lock.json").write_text("")
        self.assertEqual(self.run_recipe("--dir", "web")[0], 0)
        for bad in ("../x", "/etc", "", "a/../.."):
            self.assertEqual(self.run_recipe("--dir", bad)[0], 2, bad)
        self.assertEqual(self.run_recipe("--dir", "missing")[0], 2)
        self.assertEqual(self.run_recipe("--surprise")[0], 2)

    def test_not_a_node_project_is_a_recipe_problem(self):
        code, out, _ = self.run_recipe()
        self.assertEqual(code, 2)
        self.assertIn("no package.json", out)

    def test_hostile_package_json_cannot_break_the_script(self):
        (self.proj / "package.json").write_text("{ not json")
        (self.proj / "package-lock.json").write_text("")
        self.assertEqual(self.run_recipe()[0], 2)                          # unreadable scripts: nothing to run


class WorkerSideLimits(unittest.TestCase):
    def test_png_fits_matches_the_orchestrators_limits(self):
        from factory.render import png_ok
        for w, h in ((20, 20), (1600, 6000), (1601, 100), (100, 6001), (15, 100), (390, 844)):
            with self.subTest(size=(w, h)):
                self.assertEqual(W.png_fits(png(w, h)), png_ok(png(w, h)))
        self.assertFalse(W.png_fits(b"junk"))

    def test_oversized_screenshot_is_dropped_not_fatal(self):
        root = Path(tempfile.mkdtemp())
        (root / "test-results").mkdir()
        (root / "test-results" / "ok.png").write_bytes(png(390, 844))
        (root / "test-results" / "fullpage.png").write_bytes(png(1920, 9000))
        got = W.collect_artifacts(W.Recipe("t", {"command": ["x"], "artifacts": ["test-results/**/*.png"]}), root)
        self.assertEqual([a["name"] for a in got], ["ok"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(__import__("shutil").which("npm") and __import__("shutil").which("git"), "needs npm and git")
class RealNpm(unittest.TestCase):
    """The worker clones a repo, applies a patch, and runs the real recipe with real npm (no dependencies, so no network is needed)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "remote" / "o" / "r"
        self.src.mkdir(parents=True)
        run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=self.src, check=True, capture_output=True, text=True)
        (self.src / "package.json").write_text(json.dumps({"name": "t", "version": "1.0.0", "scripts": {
            "test": "node -e \"process.exit(require('fs').readFileSync('answer.txt','utf8').trim()==='42'?0:1)\""}}))
        (self.src / "package-lock.json").write_text(json.dumps({"name": "t", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
                                                               "packages": {"": {"name": "t", "version": "1.0.0"}}}))
        (self.src / "answer.txt").write_text("41\n")
        run("init", "-q")
        run("add", ".")
        run("commit", "-qm", "i")
        self.sha = run("rev-parse", "HEAD").stdout.strip()
        (self.tmp / "token").write_text("x")
        self.cfg = W.Config({"server": "http://x", "token_file": str(self.tmp / "token"), "platform": "linux", "work_dir": str(self.tmp / "work"),
                             "git_url": str(self.tmp / "remote" / "{repo}"), "recipes": {"web-test": {"command": [str(RECIPE)], "timeout_seconds": 120}}})

    class NoApi:
        def call(self, *a, **k):
            return 200, {"cancel": False}

    def go(self, patch):
        return W.execute(self.cfg, self.NoApi(), {"id": 1, "repo": "o/r", "base_sha": self.sha, "patch": patch, "recipe": "web-test", "lease_seconds": 120})

    def test_the_patch_decides_the_verdict(self):
        fix = "--- a/answer.txt\n+++ b/answer.txt\n@@ -1 +1 @@\n-41\n+42\n"
        good = self.go(fix)
        self.assertEqual((good["status"], good["exit_code"]), ("passed", 0), good["log"])
        bad = self.go("--- a/answer.txt\n+++ b/answer.txt\n@@ -1 +1 @@\n-41\n+43\n")
        self.assertEqual((bad["status"], bad["exit_code"]), ("failed", 1))
        self.assertIn("FAILED: npm test", bad["log"])
