import unittest

from factory import db as dbm
from factory import subtasks
from factory.github import GitHub
from factory.ui import views


class FakeGH:
    def __init__(self):
        self.created, self.updated, self.linked = [], [], []
        self.state = "open"

    def create_issue(self, repo, title, body):
        self.created.append((repo, title, body))
        return {"number": 50 + len(self.created), "id": 900 + len(self.created)}

    def add_sub_issue(self, repo, parent, sub_id):
        self.linked.append((repo, parent, sub_id))

    def update_issue(self, repo, n, body=None, state=None):
        self.updated.append((repo, n, body, state))
        self.state = state or self.state

    def get_issue(self, repo, n):
        return {"state": self.state}


def run(db, kind, stage, status, issue_repo="o/r", issue=4, pr_urls="", model="opus"):
    rid = dbm.start_run(db, kind, issue_repo, issue, "EVIL <b>title</b> @everyone", "claude-code", model, "high", stage=stage)
    if status != "running":
        dbm.finish_run(db, rid, status, pr_urls=pr_urls)
    return rid


class Steps(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")

    def test_steps_group_retries_and_latest_run_decides(self):
        run(self.db, "stage", "analyst", "stage")
        run(self.db, "stage", "architect", "failed")
        run(self.db, "stage", "architect", "stage")
        run(self.db, "build", None, "running")
        steps = dbm.steps_for_ticket(self.db, "o/r", 4)
        self.assertEqual([(s["step"], s["status"], s["attempts"]) for s in steps],
                         [("analyze", "done", 1), ("architect", "done", 2), ("implement", "running", 1)])
        self.assertEqual(dbm.step_counts(self.db, [{"repo": "o/r", "issue": 4}]), {("o/r", 4): (2, 3)})

    def test_rate_limited_is_queued_and_no_change_is_failed(self):
        run(self.db, "build", None, "rate-limited")
        run(self.db, "stage", "designer", "no-change")
        st = {s["step"]: s["status"] for s in dbm.steps_for_ticket(self.db, "o/r", 4)}
        self.assertEqual(st, {"design": "failed", "implement": "queued"})

    def test_old_cross_repo_fix_rows_are_attributed_to_the_ticket(self):
        dbm.watch_pr(self.db, "o/other", 4, "o/r", 4)
        run(self.db, "fix", None, "pr", issue_repo="o/other", issue=4)
        self.assertEqual([s["step"] for s in dbm.steps_for_ticket(self.db, "o/r", 4)], ["ci-fix"])


class Sync(unittest.TestCase):
    def setUp(self):
        self.db, self.gh = dbm.connect(":memory:"), FakeGH()

    def test_creates_links_then_closes_when_done(self):
        rid = run(self.db, "stage", "analyst", "running")
        subtasks.sync(self.gh, self.db, "o/r", 4, "analyze")
        self.assertEqual(len(self.gh.created), 1)
        self.assertEqual(self.gh.linked, [("o/r", 4, 901)])
        dbm.finish_run(self.db, rid, "stage")
        subtasks.sync(self.gh, self.db, "o/r", 4, "analyze")
        self.assertEqual(len(self.gh.created), 1)
        self.assertEqual(self.gh.updated[-1][3], "closed")

    def test_manual_close_is_respected(self):
        run(self.db, "stage", "analyst", "running")
        subtasks.sync(self.gh, self.db, "o/r", 4, "analyze")
        self.gh.state = "closed"                                  # a person closed it
        run(self.db, "stage", "analyst", "stage")
        subtasks.sync(self.gh, self.db, "o/r", 4, "analyze")
        self.assertEqual(self.gh.updated, [])
        self.assertEqual(dbm.get_step_issue(self.db, "o/r", 4, "analyze")["hands_off"], 1)

    def test_github_errors_never_raise(self):
        class Broken(FakeGH):
            def create_issue(self, *a):
                raise OSError("down")
        run(self.db, "stage", "analyst", "running")
        subtasks.sync(Broken(), self.db, "o/r", 4, "analyze")
        self.assertIn("down", dbm.get_step_issue(self.db, "o/r", 4, "analyze")["sync_error"])

    def test_body_uses_only_factory_fields(self):
        run(self.db, "stage", "analyst", "stage", pr_urls="https://github.com/o/r/pull/1 javascript:alert(1)", model="x\n@evil")
        subtasks.sync(self.gh, self.db, "o/r", 4, "analyze")
        _, title, body = self.gh.created[0]
        self.assertTrue(title.startswith("[factory] "))
        self.assertNotIn("EVIL", title + body)
        self.assertNotIn("@evil", body)
        self.assertNotIn("javascript:", body)
        self.assertIn("https://github.com/o/r/pull/1", body)

    def test_client_refuses_unprefixed_titles(self):
        with self.assertRaises(ValueError):
            GitHub("t").create_issue("o/r", "Anything", "x")


class View(unittest.TestCase):
    def test_pipeline_escapes_and_checks_links(self):
        steps = [{"step": "analyze", "status": "done", "attempts": 1, "run_id": 1, "run_ids": [1], "role": "<script>x</script>",
                  "harness": "h", "model": "m", "effort": "e", "pr_urls": "https://evil.example/x https://github.com/o/r/pull/2"}]
        out = views.ticket_detail("o/r", 4, steps)
        self.assertNotIn("<script>", out)
        self.assertNotIn("evil.example", out)
        self.assertIn("1 of 1 steps done", out)
        self.assertIn("No pipeline steps yet", views.ticket_detail("o/r", 4, []))


if __name__ == "__main__":
    unittest.main()
