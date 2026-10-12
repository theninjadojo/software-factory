"""Writing a local ticket's outcome back to the GitHub issue it was imported from (origin.py)."""
import dataclasses
import unittest

from factory import cleanup, db as dbm, origin, tracker
from test_tracker import Base, FakeGH, L, issue


class GH(FakeGH):
    def __init__(self, *a, merged=(), fail=False, **kw):
        super().__init__(*a, **kw)
        self.merged, self.fail = set(merged), fail

    def comment(self, repo, n, body):
        if self.fail:
            raise OSError("boom")
        super().comment(repo, n, body)

    def get_pr(self, repo, n):
        return {"merged": n in self.merged, "state": "closed" if n in self.merged else "open"}


class Case(Base):
    def setUp(self):
        super().setUp()
        self.cfg = dataclasses.replace(self.cfg, dry_run=False, db_path=self.path)
        self.local = L + 1

    def imported(self, gh=None):
        gh = gh or GH({5: issue(5)})
        tracker.request_import(self.path, self.repo, [5], close=False)
        self.run_imports(gh)
        gh.calls.clear()
        return gh

    def build_prs(self, *numbers):
        for n in numbers:
            dbm.track_pr(self.db, self.repo, n, self.repo, self.local)

    def comments(self, gh):
        return [c[2] for c in gh.calls if c[0] == "comment"]


class Note(Case):
    def test_a_ticket_made_in_the_ui_has_no_original_and_nothing_is_written(self):
        gh = GH({})
        t = tracker.LocalTracker(self.db)
        n = t.create(self.repo, "t", "b", author="ui")
        self.assertIsNone(origin.origin(self.db, self.repo, n))
        self.assertFalse(origin.note(self.cfg, gh, self.db, self.repo, n, "pr:x", "Opened x"))
        self.assertEqual(gh.calls, [])
        self.assertEqual(origin.refs(self.cfg, self.repo, n), "")

    def test_a_github_issue_number_is_not_a_local_ticket(self):
        self.assertIsNone(origin.origin(self.db, self.repo, 5))

    def test_the_original_is_found_from_the_local_number(self):
        self.imported()
        self.assertEqual(origin.origin(self.db, self.repo, self.local), 5)

    def test_a_note_is_posted_once_per_kind(self):
        gh = self.imported()
        self.assertTrue(origin.note(self.cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u for review."))
        self.assertTrue(origin.note(self.cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u for review."))
        origin.note(self.cfg, gh, self.db, self.repo, self.local, "pr:v", "Opened v for review.")
        self.assertEqual(self.comments(gh), ["Opened u for review.", "Opened v for review."])
        self.assertEqual({c[1] for c in gh.calls}, {5})                  # on the original issue, never the local number

    def test_the_link_to_the_ticket_is_added_when_the_ui_address_is_known(self):
        gh = self.imported()
        cfg = dataclasses.replace(self.cfg, telegram_ui_url="https://f.example/")
        origin.note(cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u.")
        self.assertIn(f"https://f.example/labels/issue?repo={self.repo}&n={self.local}", self.comments(gh)[0])

    def test_a_github_failure_is_not_recorded_and_is_retried(self):
        gh = self.imported()
        gh.fail = True
        self.assertFalse(origin.note(self.cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u."))
        gh.fail = False
        self.assertTrue(origin.note(self.cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u."))
        self.assertEqual(self.comments(gh), ["Opened u."])

    def test_dry_run_writes_nothing(self):
        gh = self.imported()
        cfg = dataclasses.replace(self.cfg, dry_run=True)
        self.assertFalse(origin.note(cfg, gh, self.db, self.repo, self.local, "pr:u", "Opened u."))
        origin.tick(cfg, gh, self.db, lambda r, n: False)
        self.assertEqual(gh.calls, [])

    def test_refs_names_the_original_issue_and_never_closes_it(self):
        self.imported()
        text = origin.refs(self.cfg, self.repo, self.local)
        self.assertIn(f"Refs {self.repo}#5", text)
        self.assertNotIn("Close", text)


class Tick(Case):
    def run_tick(self, gh):
        origin.tick(self.cfg, gh, self.db, lambda r, n: False, self.emit)

    def test_the_original_is_closed_once_every_build_pr_has_merged(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11, 12)))
        self.build_prs(11, 12)
        self.run_tick(gh)
        self.assertEqual([c for c in gh.calls if c[0] == "state"], [("state", 5, "closed")])
        merged = self.comments(gh)
        self.assertEqual(len(merged), 1)
        self.assertIn(f"https://github.com/{self.repo}/pull/11", merged[0])
        self.assertIn(f"https://github.com/{self.repo}/pull/12", merged[0])
        gh.calls.clear()
        self.run_tick(gh)                                                 # done: nothing is written again
        self.assertEqual(gh.calls, [])

    def test_it_waits_while_a_pr_is_still_open(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11,)))
        self.build_prs(11, 12)
        self.run_tick(gh)
        self.assertEqual(gh.calls, [])

    def test_a_pr_closed_without_a_merge_keeps_the_issue_open(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11,)))
        self.build_prs(11, 12)
        gh.get_pr = lambda repo, n: {"merged": n == 11, "state": "closed"}
        self.run_tick(gh)
        self.assertEqual(gh.calls, [])

    def test_a_ticket_that_built_nothing_is_never_closed(self):
        gh = self.imported()
        self.run_tick(gh)
        self.assertEqual(gh.calls, [])

    def test_a_running_job_holds_the_close_back(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11,)))
        self.build_prs(11)
        origin.tick(self.cfg, gh, self.db, lambda r, n: True, self.emit)
        self.assertEqual(gh.calls, [])

    def test_an_original_that_is_already_closed_gets_the_comment_but_no_second_close(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11,)))
        gh.issues[5]["state"] = "closed"                                   # a person closed it meanwhile
        self.build_prs(11)
        self.run_tick(gh)
        self.assertEqual(len(self.comments(gh)), 1)
        self.assertEqual([c for c in gh.calls if c[0] == "state"], [])

    def test_a_failed_close_is_retried_without_a_second_comment(self):
        gh = self.imported(GH({5: issue(5)}, merged=(11,)))
        self.build_prs(11)
        real = gh.update_issue

        def boom(*a, **k):
            raise OSError("boom")
        gh.update_issue = boom
        self.run_tick(gh)
        gh.update_issue = real
        self.run_tick(gh)
        self.assertEqual(len(self.comments(gh)), 1)
        self.assertEqual([c for c in gh.calls if c[0] == "state"], [("state", 5, "closed")])

    def test_a_ticket_made_in_the_ui_is_left_alone(self):
        gh = GH({}, merged=(11,))
        n = tracker.LocalTracker(self.db).create(self.repo, "t", "b", author="ui")
        dbm.track_pr(self.db, self.repo, 11, self.repo, n)
        self.run_tick(gh)
        self.assertEqual(gh.calls, [])
        cleanup.seed(self.db, self.repo, n)


if __name__ == "__main__":
    unittest.main()
