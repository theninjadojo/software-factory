import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import jobs, lockfiles
from factory.config import LockfileJob, Project, ProjectRepo, PublishCfg, WorkersCfg, load_raw, parse
from test_runner import RunTaskEndToEnd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W  # noqa: E402

PROJ = Project("ln", (ProjectRepo("o/web", "web", depends_on=("o/pkg",)), ProjectRepo("o/pkg", "pkgs", publish=PublishCfg(packages=("@o/ui", "@o/core")))))
AGENT = ("diff --git a/web/package.json b/web/package.json\n--- a/web/package.json\n+++ b/web/package.json\n@@ -1 +1 @@\n-a\n+b\n"
         "diff --git a/web/src/app.ts b/web/src/app.ts\n--- a/web/src/app.ts\n+++ b/web/src/app.ts\n@@ -1 +1 @@\n-x\n+y\n"
         "diff --git a/pnpm-lock.yaml b/pnpm-lock.yaml\n--- a/pnpm-lock.yaml\n+++ b/pnpm-lock.yaml\n@@ -1 +1 @@\n-l\n+m\n")


class Helpers(unittest.TestCase):
    def test_only_manifests_and_lockfiles_go_to_the_worker(self):
        out = lockfiles.only_manifests(AGENT)
        self.assertIn("web/package.json", out)
        self.assertIn("pnpm-lock.yaml", out)
        self.assertNotIn("app.ts", out)
        self.assertEqual(lockfiles.dirs_of(out), ["web"])
        self.assertEqual(lockfiles.dirs_of("diff --git a/package.json b/package.json\n"), [""])

    def test_packages_come_from_the_repos_it_depends_on(self):
        self.assertEqual(lockfiles.packages_for(PROJ, "o/web"), ["@o/ui", "@o/core"])
        self.assertEqual(lockfiles.packages_for(PROJ, "o/pkg"), [])

    def test_allowed_names(self):
        for ok in ("package.json", "web/package-lock.json", "a/b/pnpm-lock.yaml", "npm-shrinkwrap.json"):
            self.assertTrue(lockfiles.allowed(ok))
        for bad in (".npmrc", "web/.npmrc", "package.json.bak", "src/package.ts"):
            self.assertFalse(lockfiles.allowed(bad))

    def test_config_validates_lockfile_jobs(self):
        raw = load_raw("config.example.toml")
        raw["workers"] = {"enabled": True, "lockfiles": [{"repo": "your-org/shop-web"}]}
        self.assertEqual(parse(raw).workers.lockfiles, (LockfileJob("your-org/shop-web"),))
        for bad in ([{"repo": "x/unknown"}], [{"repo": "your-org/shop-web", "recipe": "Bad Name"}],
                    [{"repo": "your-org/shop-web"}, {"repo": "your-org/shop-web"}]):
            raw["workers"] = {"enabled": True, "lockfiles": bad}
            with self.assertRaises(ValueError):
                parse(raw)


class Jobs(unittest.TestCase):
    def claim(self, db, params):
        jid = jobs.enqueue(db, "o/web", 5, "a" * 40, "", "lockfile-update", "any", 0, params)
        jobs.claim(db, "w1", "linux", ["lockfile-update"], 1, 120, 900, 2)
        return jid

    def test_a_lockfile_job_keeps_its_diff_and_nothing_else_does(self):
        db = dbm.connect(":memory:")
        jid = self.claim(db, json.dumps({"purpose": "lockfile"}))
        self.assertEqual(jobs.complete(db, jid, "w1", {"status": "passed", "exit_code": 0, "log": "", "patch": "diff"}, 2), "")
        self.assertEqual(jobs.get(db, jid)["result_patch"], "diff")
        jid = self.claim(db, "")                                               # a verification job: a patch in its result is dropped
        jobs.complete(db, jid, "w1", {"status": "passed", "exit_code": 0, "log": "", "patch": "diff"}, 3)
        self.assertEqual(jobs.get(db, jid)["result_patch"], "")

    def test_a_bad_patch_fails_the_job(self):
        db = dbm.connect(":memory:")
        jid = self.claim(db, json.dumps({"purpose": "lockfile"}))
        self.assertTrue(jobs.complete(db, jid, "w1", {"status": "passed", "exit_code": 0, "log": "", "patch": ["x"]}, 2))
        self.assertEqual(jobs.get(db, jid)["status"], "error")


class Request(unittest.TestCase):
    def cfg(self):
        from test_roles import CFG
        return replace(CFG, repos=["o/web", "o/pkg"], projects=(PROJ,),
                       workers=WorkersCfg(enabled=True, max_wait_seconds=60, lockfiles=(LockfileJob("o/web"),)))

    def run_request(self, result):
        db, t = dbm.connect(":memory:"), [0.0]

        def sleep(_):                                          # the worker takes the job and answers while the orchestrator waits
            t[0] += 2
            job = jobs.claim(db, "w1", "linux", ["lockfile-update"], t[0], 120, 900, 2)
            if job:
                self.sent = job
                jobs.complete(db, job["id"], "w1", result, t[0])
        out = lockfiles.request(self.cfg(), "o/web", 5, "a" * 40, AGENT, ["@o/ui"], db, sleep, lambda: t[0])
        return out

    def test_sends_only_manifests_and_returns_the_worker_diff(self):
        diff, note = self.run_request({"status": "passed", "exit_code": 0, "log": "", "patch": "the diff"})
        self.assertEqual(diff, "the diff")
        self.assertIn("refreshed on w1", note)
        self.assertNotIn("app.ts", self.sent["patch"])
        self.assertEqual(json.loads(self.sent["params"]), {"purpose": "lockfile", "packages": ["@o/ui"], "dirs": ["web"]})

    def test_a_failure_is_a_note_not_a_diff(self):
        diff, note = self.run_request({"status": "failed", "exit_code": 1, "log": "ERR @mallory"})
        self.assertEqual(diff, "")
        self.assertIn("was not refreshed (failed on w1", note)
        self.assertNotIn("mallory", note)                     # the worker's log stays out of the PR

    def test_no_job_configured_does_nothing(self):
        self.assertEqual(lockfiles.request(replace(self.cfg(), workers=WorkersCfg()), "o/web", 5, "a" * 40, AGENT, []), ("", ""))


class Runner(RunTaskEndToEnd):
    LOCK = "diff --git a/lock.txt b/lock.txt\n"

    def cfg_for(self, cfg):
        proj = Project("proj", (ProjectRepo("o/web", "web", depends_on=("o/mobile",)), ProjectRepo("o/mobile", "pkgs", publish=PublishCfg(packages=("@o/ui",)))))
        return replace(cfg, projects=(proj,), workers=WorkersCfg(enabled=True, lockfiles=(LockfileJob("o/web"),)))

    def build(self, worker_diff, note="the lockfile was refreshed on w1 for the published packages"):
        from factory import runner

        def edits(work):
            (work / "web" / "package.json").write_text('{"dependencies": {"@o/ui": "^2.0.0"}}\n')
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, edits)
        seen = {}

        def fake_request(cfg, repo, num, sha, patch, packages):
            seen.update(repo=repo, packages=packages, patch=patch)
            return worker_diff, note
        with p1, p2, mock.patch.object(runner.lockfiles, "request", fake_request):
            res = runner.run_task(self.cfg_for(cfg), gh, "o/web", {"number": 5, "title": "Use v2", "body": "x"}, cfg.routes["low"],
                                  only=("o/web",), released="published")
        return res, gh, bare, g, t, seen

    def branch_files(self, bare, g, t, head):
        return g("--git-dir", str(bare["o/web"]), "show", "--stat", "--format=", head, cwd=t).stdout

    def test_the_worker_lockfile_joins_the_commit(self):
        lock = ("diff --git a/package-lock.json b/package-lock.json\nnew file mode 100644\n--- /dev/null\n+++ b/package-lock.json\n"
                "@@ -0,0 +1 @@\n+{\"lockfileVersion\": 3}\n")
        res, gh, bare, g, t, seen = self.build(lock)
        self.assertEqual(res.status, "pr", res.detail)
        self.assertEqual((seen["repo"], seen["packages"]), ("o/web", ["@o/ui"]))
        files = self.branch_files(bare, g, t, gh.prs[0][1])
        self.assertIn("package-lock.json", files)
        self.assertIn("package.json", files)
        self.assertIn("Lockfile: the lockfile was refreshed on w1", gh.prs[0][2])

    def test_a_worker_diff_outside_manifests_is_refused_and_the_pr_says_so(self):
        evil = "diff --git a/.npmrc b/.npmrc\nnew file mode 100644\n--- /dev/null\n+++ b/.npmrc\n@@ -0,0 +1 @@\n+registry=http://evil\n"
        res, gh, bare, g, t, _ = self.build(evil)
        self.assertEqual(res.status, "pr", res.detail)
        self.assertNotIn(".npmrc", self.branch_files(bare, g, t, gh.prs[0][1]))
        self.assertIn("refused: it changed .npmrc", gh.prs[0][2])

    def test_a_failed_refresh_still_opens_the_pr(self):
        res, gh, *_ = self.build("", "the lockfile was not refreshed (failed on w1; the log is on the Workers page, job 3)")
        self.assertEqual(res.status, "pr", res.detail)
        self.assertIn("Lockfile: the lockfile was not refreshed", gh.prs[0][2])


class Worker(unittest.TestCase):
    def test_lockfile_diff_holds_only_what_the_recipe_changed_in_manifests_and_lockfiles(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            (d / "web").mkdir()
            (d / "web" / "package.json").write_text("{}\n")
            (d / "web" / "app.ts").write_text("a\n")
            before = W.snapshot(d)
            (d / "web" / "pnpm-lock.yaml").write_text("lock\n")          # what a lockfile recipe writes
            (d / "web" / "app.ts").write_text("changed by the recipe\n")  # anything else is not sent
            (d / ".npmrc").write_text("x\n")
            diff = W.lockfile_diff(d, before)
            self.assertIn("web/pnpm-lock.yaml", diff)
            self.assertNotIn("app.ts", diff)
            self.assertNotIn(".npmrc", diff)
            self.assertTrue(W.is_lockfile({"params": {"purpose": "lockfile"}}))
            self.assertFalse(W.is_lockfile({"params": None}))


if __name__ == "__main__":
    unittest.main()
