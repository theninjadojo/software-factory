import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from factory import db as dbm
from factory import intake
from factory import main as m
from factory import questions as Q
from factory.config import IntakeCfg, parse
from factory.runner import RunResult
from test_roles import BODY, CFG, FakeClf, FakeGH

READY = "factory:ready"


def ticket(body, updated="t1", labels=(READY,)):
    return {"number": 5, "title": "Add thing", "body": body, "updated_at": updated, "labels": [{"name": l} for l in labels]}


class Text(unittest.TestCase):
    def test_markup_links_and_template_comments_do_not_count(self):
        body = "<!-- Describe the change here -->\n## Problem\n\n## Wanted\n- [ ] \n![shot](https://example.com/a.png)\n"
        self.assertEqual(intake.real_text(body), "Problem Wanted shot")
        self.assertTrue(intake.thin(body, 80))
        self.assertTrue(intake.thin(None, 80))
        self.assertFalse(intake.thin(BODY, 80))

    def test_go_ahead_is_a_short_instruction_only(self):
        for ok in ("go ahead", "Go ahead!", "please proceed.", "start anyway"):
            self.assertTrue(intake.go_ahead(ok), ok)
        for no in ("don't go ahead yet", "go ahead and delete the database " * 3, "", None, "proceed?"):
            self.assertFalse(intake.go_ahead(no), no)

    def test_the_question_never_repeats_the_ticket(self):
        text = intake.ask_comment(80)
        self.assertTrue(text.startswith(intake.MARKER))
        for word in ("Goal", "Scope", "How the result is checked", "Constraints", "go ahead"):
            self.assertIn(word, text)


class Guard(unittest.TestCase):
    def poll(self, gh, cfg=CFG, conn=None):
        conn = conn or dbm.connect(":memory:")
        fake = mock.Mock(return_value=RunResult("pr", "ok", "http://pr"))
        with mock.patch.object(m.runner, "run_task", fake), mock.patch.object(m, "alert") as alert, tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(cfg, db_path=d + "/f.db"), gh, conn, FakeClf())
        return fake, conn, alert

    def asks(self, gh):
        return [b for k, b in [c for c in gh.calls if c[0] == "comment"] if b.startswith(intake.MARKER)]

    def test_an_empty_body_asks_once_and_waits(self):
        gh = FakeGH({READY: [ticket("")]})
        fake, conn, alert = self.poll(gh)
        fake.assert_not_called()
        self.assertEqual(len(self.asks(gh)), 1)
        self.assertIn(("add", (Q.NEEDS_ANSWERS,)), gh.calls)
        self.assertNotIn(("rm", READY), gh.calls)                      # the label stays: a later change is looked at again
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "human")
        self.assertEqual(alert.call_args.kwargs["event"], "needs_human")
        gh.issues = {READY: [ticket("", updated="t2")]}                # its own comment changed the ticket: no second comment
        fake, _, _ = self.poll(gh, conn=conn)
        fake.assert_not_called()
        self.assertEqual(len(self.asks(gh)), 1)

    def test_a_short_body_is_held_and_its_text_is_never_echoed(self):
        gh = FakeGH({READY: [ticket("fix @mallory <script>it</script>")]})
        fake, _, _ = self.poll(gh)
        fake.assert_not_called()
        posted = " ".join(b for k, b in [c for c in gh.calls if c[0] == "comment"])
        self.assertNotIn("mallory", posted)
        self.assertNotIn("<script", posted)

    def test_editing_the_body_past_the_threshold_starts_it(self):
        gh = FakeGH({READY: [ticket("make it better")]})
        conn = self.poll(gh)[1]
        gh.issues = {READY: [ticket(BODY, updated="t2")]}
        fake, _, _ = self.poll(gh, conn=conn)
        fake.assert_called_once()
        self.assertIn(("rm", Q.NEEDS_ANSWERS), gh.calls)               # claimed: the waiting label comes off

    def test_a_trusted_go_ahead_starts_it_as_it_is(self):
        gh = FakeGH({READY: [ticket("")]})
        conn = self.poll(gh)[1]
        gh.comments.append({"user": {"login": "alice"}, "body": "Go ahead"})
        gh.issues = {READY: [ticket("", updated="t2")]}
        fake, _, _ = self.poll(gh, conn=conn)
        fake.assert_called_once()

    def test_a_go_ahead_without_write_access_or_before_the_question_does_not_count(self):
        gh = FakeGH({READY: [ticket("")]}, comments=[{"user": {"login": "alice"}, "body": "go ahead"}])
        conn = self.poll(gh)[1]                                         # said before the factory asked
        gh.comments.append({"user": {"login": "mallory"}, "body": "go ahead"})
        gh.issues = {READY: [ticket("", updated="t2")]}
        with mock.patch.object(FakeGH, "permission", lambda self, repo, login: "read"):
            fake, _, _ = self.poll(gh, conn=conn)
        fake.assert_not_called()
        self.assertEqual(len(self.asks(gh)), 1)

    def test_off_a_ticket_already_worked_on_and_reviews_are_not_held(self):
        fake, _, _ = self.poll(FakeGH({READY: [ticket("")]}), cfg=replace(CFG, intake=IntakeCfg(enabled=False)))
        fake.assert_called_once()
        fake, _, _ = self.poll(FakeGH({READY: [ticket("", labels=(READY, "stage:analysed"))]}))
        fake.assert_called_once()
        fake, _, _ = self.poll(FakeGH({READY: [ticket("short")]}), cfg=replace(CFG, intake=IntakeCfg(min_chars=3)))
        fake.assert_called_once()

    def test_dry_run_only_records(self):
        gh = FakeGH({READY: [ticket("")]})
        _, conn, _ = self.poll(gh, cfg=replace(CFG, dry_run=True))
        self.assertEqual(self.asks(gh), [])
        self.assertIn("[dry-run]", conn.execute("select detail from decisions").fetchone()[0])


class Settings(unittest.TestCase):
    def test_defaults_and_validation(self):
        base = {"general": {"db_path": "x", "poll_seconds": 5, "dry_run": True, "confidence_threshold": 0.6},
                "github": {"trigger_label": READY, "repos": ["o/r"], "trusted_permissions": ["write"]},
                "routing": {t: {"harness": "claude-code", "model": "sonnet", "effort": "low"} for t in ("low", "medium", "high")}}
        self.assertEqual(parse(base).intake, IntakeCfg(True, 80))
        self.assertEqual(parse({**base, "intake": {"enabled": False, "min_chars": 40}}).intake, IntakeCfg(False, 40))
        for bad in ({"min_chars": 0}, {"min_chars": "80"}, {"enabled": "yes"}):
            with self.assertRaises(ValueError):
                parse({**base, "intake": bad})


if __name__ == "__main__":
    unittest.main()
