"""Tickets that stopped with nothing to show (#187): auto went back to the analyst after the architect, a GitHub timeout after a
finished run crashed the job silently, and a restart in that moment left no working label for recovery to find."""
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from unittest import mock

from factory import db as dbm
from factory import github as ghm
from factory import main as m
from factory.router import behind
from factory.runner import RunResult
from test_roles import CFG, FakeClf, FakeGH, issue


class LabelGH(FakeGH):
    """FakeGH that keeps the ticket's labels, and whose comment can fail like GitHub timing out."""

    def __init__(self, issues=None, labels=(), fail_comments=0, down_after_fail=False):
        super().__init__(issues)
        self.labels, self.fail_comments, self.down_after_fail, self.down = list(labels), fail_comments, down_after_fail, False

    def get_issue(self, repo, num):
        if self.down:
            raise TimeoutError("The read operation timed out")
        return super().get_issue(repo, num)

    def add_labels(self, repo, num, labels):
        super().add_labels(repo, num, labels)
        self.labels += [l for l in labels if l not in self.labels]

    def remove_label(self, repo, num, label):
        super().remove_label(repo, num, label)
        self.labels = [l for l in self.labels if l != label]

    def comment(self, repo, num, body):
        if self.fail_comments:
            self.fail_comments -= 1
            self.down = self.down_after_fail
            raise TimeoutError("The read operation timed out")
        return super().comment(repo, num, body)


class Steps(FakeClf):
    """A classifier that answers each call with the next stage in a list (the first pick, then the suggestion after the stage)."""

    def __init__(self, stages, **kw):
        super().__init__(**kw)
        self.stages = list(stages)

    def classify(self, *a, **kw):
        self.kw["stage"] = self.stages.pop(0) if len(self.stages) > 1 else self.stages[0]
        return super().classify(*a, **kw)


def poll(gh, clf, result=None, cfg=CFG):
    conn = dbm.connect(":memory:")
    fake = mock.Mock(return_value=result or RunResult("stage", "ok", output="# Analysis\nDone."))
    with mock.patch.object(m.runner, "run_task", fake), tempfile.TemporaryDirectory() as d:
        m.poll_once(replace(cfg, db_path=d + "/f.db"), gh, conn, clf)
    return fake, conn


def outcome(conn):
    return conn.execute("select outcome, detail from decisions").fetchone()


class NeverBack(unittest.TestCase):
    def test_behind(self):
        self.assertTrue(behind(CFG, "analyst", ["architect"]))
        self.assertTrue(behind(CFG, "designer", ["analyst", "architect"]))
        self.assertFalse(behind(CFG, "architect", ["analyst"]))
        self.assertFalse(behind(CFG, "analyst", []))
        self.assertFalse(behind(CFG, None, ["architect"]))

    def test_an_unsure_classifier_after_the_architect_asks_a_person_instead_of_running_the_analyst(self):
        """#187: after the architect's questions were answered, auto ran the analyst ("unsure: analyst first")."""
        for kw in (dict(stage=None, needs_human=True, confidence=0.35), dict(stage="implement", confidence=0.3), dict(stage=None, confidence=0.2)):
            gh = FakeGH({"factory:auto": [issue(labels=["factory:auto", "stage:architected"])]})
            fake, conn = poll(gh, FakeClf(**kw))
            fake.assert_not_called()
            self.assertEqual(outcome(conn)[0], "human", kw)

    def test_an_earlier_stage_named_by_the_classifier_is_not_run(self):
        for stage in ("analyze", "design"):
            gh = FakeGH({"factory:auto": [issue(labels=["factory:auto", "stage:architected"])]})
            fake, conn = poll(gh, FakeClf(stage=stage))
            fake.assert_not_called()
            self.assertEqual(outcome(conn)[0], "human")
            self.assertIn("earlier stage", outcome(conn)[1])

    def test_a_confident_build_after_the_architect_still_builds(self):
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto", "stage:architected"])]})
        fake, _ = poll(gh, FakeClf(stage=None), RunResult("pr", "ok", "http://pr"))
        self.assertIsNone(fake.call_args.kwargs.get("role"))

    def test_the_analyst_still_runs_first_on_a_fresh_ticket(self):
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto"])]})
        fake, _ = poll(gh, FakeClf(stage=None, confidence=0.2))
        self.assertEqual(fake.call_args.kwargs["role"], "analyst")

    def test_the_chain_does_not_continue_into_an_earlier_stage(self):
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto", "stage:designed"])]})
        fake, _ = poll(gh, Steps(["architect", "analyze"]))
        self.assertEqual(fake.call_args.kwargs["role"], "architect")
        self.assertEqual(fake.call_count, 1)                           # no analyst after the architect: it asks a person instead
        self.assertIn("comes before a stage already done", next(c[1] for c in gh.calls if c[0] == "comment"))


class Outcomes(unittest.TestCase):
    def test_the_working_label_comes_off_only_after_the_outcome_is_on_the_ticket(self):
        gh = LabelGH({"factory:analyze": [issue(labels=["factory:analyze"])]})
        poll(gh, FakeClf())
        calls = gh.calls
        rm = calls.index(("rm", "factory:working-analyst"))
        self.assertLess(next(i for i, c in enumerate(calls) if c[0] == "comment"), rm)
        self.assertLess(calls.index(("add", ("stage:analysed",))), rm)
        gh = LabelGH({"factory:ready": [issue(labels=["factory:ready"])]})
        poll(gh, FakeClf(), RunResult("pr", "ok", "http://pr"))
        self.assertLess(gh.calls.index(("add", ("factory:pr-open",))), gh.calls.index(("rm", "factory:working")))

    def test_a_github_timeout_after_the_run_marks_the_ticket_failed(self):
        """#187: the analyst finished, GitHub timed out, and the job died with only a log line."""
        gh = LabelGH({"factory:analyze": [issue(labels=["factory:analyze"])]}, fail_comments=1)
        with mock.patch.object(m, "alert") as alert:
            poll(gh, FakeClf())
        self.assertIn("factory:failed", gh.labels)
        self.assertNotIn("factory:working-analyst", gh.labels)
        body = [c[1] for c in gh.calls if c[0] == "comment"][-1]
        self.assertTrue(body.startswith("The factory's analyst run stopped with an error"), body)
        self.assertIn("TimeoutError", body)
        self.assertTrue(any(c.kwargs.get("event") == "failure" for c in alert.call_args_list))

    def test_with_github_still_down_the_working_label_stays_for_recovery(self):
        gh = LabelGH({"factory:analyze": [issue(labels=["factory:analyze"])]}, fail_comments=1, down_after_fail=True)
        poll(gh, FakeClf())
        self.assertIn("factory:working-analyst", gh.labels)                  # the next start's recovery requeues it
        self.assertNotIn("factory:failed", gh.labels)

    def test_a_crash_before_the_claim_leaves_the_ticket_for_the_next_poll(self):
        gh = LabelGH(labels=["factory:analyze"])
        m.stranded(gh, "o/r", 5, "analyst", TimeoutError())
        self.assertEqual(gh.labels, ["factory:analyze"])
        self.assertFalse([c for c in gh.calls if c[0] == "comment"])

    def test_the_crash_comment_is_not_read_as_a_person(self):
        self.assertTrue(m.STRANDED.format(kind="build", error="X").startswith(m.BOILERPLATE))


class Retries(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ghm.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_reads_and_label_changes_are_tried_again_when_github_times_out(self):
        for method, path in (("GET", "/repos/o/r/issues/1"), ("DELETE", "/repos/o/r/issues/1/labels/x"), ("POST", "/repos/o/r/issues/1/labels")):
            gh = ghm.GitHub("t")
            with mock.patch.object(gh, "_send", side_effect=[TimeoutError(), urllib.error.HTTPError(path, 502, "bad", {}, None), {"ok": 1}]) as send:
                self.assertEqual(gh._req(method, path), {"ok": 1})
            self.assertEqual(send.call_count, 3, method)

    def test_a_comment_is_never_sent_twice_and_refusals_are_not_retried(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=TimeoutError()) as send:
            with self.assertRaises(TimeoutError):
                gh.comment("o/r", 1, "hi")
        self.assertEqual(send.call_count, 1)
        with mock.patch.object(gh, "_send", side_effect=urllib.error.HTTPError("x", 404, "nf", {}, None)) as send:
            with self.assertRaises(urllib.error.HTTPError):
                gh.get_issue("o/r", 1)
        self.assertEqual(send.call_count, 1)

    def test_it_gives_up_after_three_tries(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=TimeoutError()) as send:
            with self.assertRaises(TimeoutError):
                gh.get_issue("o/r", 1)
        self.assertEqual(send.call_count, 3)


if __name__ == "__main__":
    unittest.main()
