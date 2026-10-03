import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import runner, verify
from factory.config import WorkerCheck, WorkersCfg
import test_runner



def edits(work):
    (work / "web" / "a.txt").write_text("two\n")
    (work / "mobile" / "b.txt").write_text("new\n")


class WorkerGate(unittest.TestCase):
    def build(self, t, mode, report):
        cfg, gh, bare, p1, p2, g = test_runner.RunTaskEndToEnd()._setup(t, edits)
        cfg = replace(cfg, db_path=str(Path(t) / "f.db"),
                      workers=WorkersCfg(enabled=True, mode=mode, checks=(WorkerCheck("o/web", "ios-test", "macos"),)))
        seen = []

        def gate(c, repo, issue, sha, patch, **kw):
            seen.append((repo, issue, sha, patch))
            return report
        with p1, p2, mock.patch.object(verify, "gate", gate):
            res = runner.run_task(cfg, gh, "o/web", {"number": 5, "title": "t", "body": ""}, cfg.routes["low"])
        return res, gh, bare, seen, g

    def test_failed_check_blocks_the_push(self):
        rep = verify.Report().fail("o/web: check 'ios-test' failed on mac.\n```\nboom\n```")
        rep.images.append({"kind": "verify", "name": "ios-test-home", "png": b"x"})
        with tempfile.TemporaryDirectory() as t:
            res, gh, bare, seen, g = self.build(t, "block", rep)
            self.assertEqual(res.status, "failed")
            self.assertIn("worker verification failed", res.detail)
            self.assertIn("boom", res.detail)
            self.assertEqual(gh.prs, [])                                           # nothing pushed, no PR
            for b in bare.values():
                self.assertNotIn("factory/", g("--git-dir", str(b), "branch", "--list", cwd=t).stdout)
            self.assertEqual([i["name"] for i in res.images], ["ios-test-home"])    # the screenshots are kept for the UI

    def test_only_repos_with_checks_are_sent_and_the_job_has_the_base_commit(self):
        with tempfile.TemporaryDirectory() as t:
            res, gh, bare, seen, g = self.build(t, "block", verify.Report())
            self.assertEqual(res.status, "pr", res.detail)
            self.assertEqual([s[0] for s in seen], ["o/web"])                      # o/mobile has no check
            repo, issue, sha, patch = seen[0]
            self.assertEqual(issue, 5)
            self.assertEqual(sha, g("--git-dir", str(bare["o/web"]), "rev-parse", "main", cwd=t).stdout.strip())
            self.assertIn("+two", patch)

    def test_warn_mode_pushes_and_says_so(self):
        rep = verify.Report().fail("o/web: check 'ios-test' could not run.\n```\nno worker\n```")
        with tempfile.TemporaryDirectory() as t:
            res, gh, bare, seen, g = self.build(t, "warn", rep)
            self.assertEqual(res.status, "pr", res.detail)
            web = next(body for r, _, body in gh.prs if r == "o/web")
            self.assertIn("Worker verification did not pass", web)
            self.assertIn("no worker", web)
            self.assertNotIn("Worker verification", next(body for r, _, body in gh.prs if r == "o/mobile"))

    def test_disabled_workers_change_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = test_runner.RunTaskEndToEnd()._setup(t, edits)
            with p1, p2, mock.patch.object(verify, "gate", side_effect=AssertionError("must not be called")):
                res = runner.run_task(cfg, gh, "o/web", {"number": 5, "title": "t", "body": ""}, cfg.routes["low"])
            self.assertEqual(res.status, "pr", res.detail)


if __name__ == "__main__":
    unittest.main()
