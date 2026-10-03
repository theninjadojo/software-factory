import base64
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                    # noqa: E402
from test_render import png                                            # noqa: E402


def git(*a, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


def make_cfg(tmp: Path, recipes: dict, git_url: str = "") -> W.Config:
    (tmp / "token").write_text("secret\n")
    return W.Config({"server": "http://x", "token_file": str(tmp / "token"), "platform": "macos", "work_dir": str(tmp / "work"),
                     "git_url": git_url or str(tmp / "remote" / "{repo}"), "recipes": recipes})


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "remote" / "o" / "r"
        self.src.mkdir(parents=True)
        git("init", "-q", cwd=self.src)
        (self.src / "a.txt").write_text("one\n")
        git("add", ".", cwd=self.src)
        git("commit", "-qm", "init", cwd=self.src)
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.src, capture_output=True, text=True).stdout.strip()
        self.patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"

    def job(self, recipe="t", **kw):
        return {"id": 1, "repo": "o/r", "base_sha": self.sha, "patch": self.patch, "recipe": recipe, "lease_seconds": 120, **kw}


class NoApi:
    def call(self, *a, **k):
        return 200, {"cancel": False}


class Recipes(unittest.TestCase):
    def test_recipe_validation(self):
        for raw in ({}, {"command": "make test"}, {"command": []}, {"command": ["a"], "timeout_seconds": 1},
                    {"command": ["a"], "artifacts": ["/etc/*"]}, {"command": ["a"], "artifacts": ["../x/*.png"]}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                W.Recipe("t", raw)
        W.Recipe("t", {"command": ["make", "test"], "artifacts": ["build/*.png"]})

    def test_names_must_be_plain(self):
        with self.assertRaises(ValueError):
            make_cfg(Path(tempfile.mkdtemp()), {"Bad Name": {"command": ["true"]}})


class Prepare(Fixture):
    def test_clone_checkout_apply(self):
        cfg, dest = make_cfg(self.tmp, {}), self.tmp / "out"
        self.assertEqual(W.prepare(cfg, self.job(), dest), "")
        self.assertEqual((dest / "a.txt").read_text(), "one\ntwo\n")

    def test_patch_that_does_not_apply_is_an_error(self):
        cfg = make_cfg(self.tmp, {})
        self.assertIn("git apply failed", W.prepare(cfg, self.job(patch="--- a/zzz\n+++ b/zzz\n@@ -1 +1 @@\n-x\n+y\n"), self.tmp / "o2"))

    def test_malformed_jobs_are_refused_before_git_runs(self):
        cfg = make_cfg(self.tmp, {})
        for bad in ({"repo": "../../etc"}, {"repo": "o/r; rm -rf"}, {"base_sha": "HEAD"}, {"base_sha": "--upload-pack=x"}, {"patch": 5},
                    {"patch": "x" * (W.MAX_PATCH + 1)}):
            with self.subTest(bad=bad):
                self.assertEqual(W.prepare(cfg, self.job(**bad), self.tmp / "never"), "the job is malformed")
        self.assertFalse((self.tmp / "never").exists())

    def test_repo_hooks_do_not_run(self):
        hook = self.src / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {self.tmp}/HOOK_RAN\n")
        hook.chmod(0o755)
        W.prepare(make_cfg(self.tmp, {}), self.job(), self.tmp / "out")
        self.assertFalse((self.tmp / "HOOK_RAN").exists())


class Execute(Fixture):
    def run_job(self, command, **kw):
        cfg = make_cfg(self.tmp, {"t": {"command": command, **kw}})
        return W.execute(cfg, NoApi(), self.job())

    def test_passing_recipe_sees_the_patched_tree_and_returns_screenshots(self):
        script = 'grep -q two a.txt && mkdir -p build && cp "$SHOT" build/home.png'
        shot = self.tmp / "shot.png"
        shot.write_bytes(png(20, 20))
        import os
        os.environ["SHOT"] = str(shot)
        self.addCleanup(os.environ.pop, "SHOT", None)
        res = self.run_job(["sh", "-c", script], artifacts=["build/*.png"], env_passthrough=["SHOT"])
        self.assertEqual((res["status"], res["exit_code"]), ("passed", 0), res["log"])
        self.assertEqual([a["name"] for a in res["artifacts"]], ["home"])
        self.assertEqual(base64.b64decode(res["artifacts"][0]["png_b64"]), shot.read_bytes())

    def test_failing_recipe_is_failed_with_its_output(self):
        res = self.run_job(["sh", "-c", "echo BOOM; exit 3"])
        self.assertEqual((res["status"], res["exit_code"]), ("failed", 3))
        self.assertIn("BOOM", res["log"])

    def test_timeout_kills_the_recipe(self):
        cfg = make_cfg(self.tmp, {"t": {"command": ["sh", "-c", "sleep 60"], "timeout_seconds": 10}})
        cfg.recipes["t"].timeout = 1                                   # the config floor is 10s; shorten it for the test
        res = W.execute(cfg, NoApi(), self.job())
        self.assertEqual(res["status"], "failed")
        self.assertIn("timed out", res["log"])

    def test_environment_is_scrubbed(self):
        import os
        os.environ["FACTORY_SECRET"] = "hunter2"
        self.addCleanup(os.environ.pop, "FACTORY_SECRET", None)
        res = self.run_job(["sh", "-c", "echo secret=[$FACTORY_SECRET]"])
        self.assertIn("secret=[]", res["log"])

    def test_unknown_recipe_is_an_error(self):
        cfg = make_cfg(self.tmp, {})
        res = W.execute(cfg, NoApi(), self.job(recipe="other"))
        self.assertEqual(res["status"], "error")

    def test_unapplicable_patch_is_an_error_not_a_test_failure(self):
        cfg = make_cfg(self.tmp, {"t": {"command": ["true"]}})
        res = W.execute(cfg, NoApi(), self.job(patch="--- a/zzz\n+++ b/zzz\n@@ -1 +1 @@\n-x\n+y\n"))
        self.assertEqual(res["status"], "error")

    def test_cancel_stops_the_recipe_and_reports_nothing(self):
        class Cancelling:
            def call(self, path, body, timeout=60):
                return 200, {"cancel": True}
        cfg = make_cfg(self.tmp, {"t": {"command": ["sh", "-c", "sleep 60"]}})
        job = self.job(lease_seconds=15)                               # heartbeat every 5s
        self.assertIsNone(W.execute(cfg, Cancelling(), job))

    def test_workspace_is_removed_afterwards(self):
        self.run_job(["true"])
        self.assertEqual(list((self.tmp / "work").iterdir()), [])


class Artifacts(unittest.TestCase):
    def test_only_real_pngs_inside_the_checkout(self):
        root = Path(tempfile.mkdtemp())
        (root / "out").mkdir()
        (root / "out" / "good.png").write_bytes(png(20, 20))
        (root / "out" / "fake.png").write_bytes(b"not a png")
        outside = Path(tempfile.mkdtemp()) / "secret.png"
        outside.write_bytes(png(20, 20))
        (root / "out" / "link.png").symlink_to(outside)
        got = W.collect_artifacts(W.Recipe("t", {"command": ["x"], "artifacts": ["out/*.png"]}), root)
        self.assertEqual([a["name"] for a in got], ["good"])


if __name__ == "__main__":
    unittest.main()
