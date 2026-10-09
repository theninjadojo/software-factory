"""A stage document's own 'Recommended next stage' line steers `factory:auto` when the classifier names no stage (the labels-only
classifier never does). Read-only stages follow it; a build never does."""
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from factory import db as dbm
from factory import main as m
from factory import recommend
from factory.classifier import Classification
from factory.runner import RunResult
from test_questions import block, q
from test_roles import CFG, FakeClf, FakeGH, SeqClf, issue

NONE = dict(stage=None, stage_confidence=None, source="labels", confidence=0.4)      # what the labels-only classifier answers
SPEC = recommend.SOURCE


def clf_result(**kw) -> Classification:
    base = dict(kind="bug", complexity="medium", needs_human=False, confidence=0.4, source="labels")
    base.update(kw)
    return Classification(**base)


def analyst_comment(line: str) -> dict:
    return {"user": {"login": "bot"}, "body": "<!-- factory:stage=analyst -->\n### Analyst\n\nSummary.\n\n" + line}


class Parse(unittest.TestCase):
    def test_the_stage_is_read_from_the_line_in_any_markdown_dress(self):
        for line, want in (("Recommended next stage: architect", "architect"),
                           ("**Recommended next stage:** implement. The cause is one field.", "implement"),
                           ("## Recommended next stage\nDesign", None),                       # the word must be on the line itself
                           ("> _Recommended next stage_ - `design`", "design"),
                           ("Recommended next stage: designer (users see something new)", "design"),
                           ("**Recommended next stage:** needs-human. Open questions.", "needs-human"),
                           ("Recommended next stage: Needs Human", "needs-human"),
                           ("recommended next stage: IMPLEMENT", "implement")):
            self.assertEqual(recommend.parse("# Doc\n\n" + line + "\n"), want, line)

    def test_nothing_else_is_read(self):
        for doc in (None, "", "# Doc\nno recommendation here", "I would not say Recommended next stage: implement mid-sentence",
                    "Recommended next stage: ship it", "Recommended next stage: implementation-ish rm -rf /"):
            self.assertIsNone(recommend.parse(doc), doc)

    def test_the_last_line_wins_so_a_quoted_earlier_one_cannot_override_it(self):
        self.assertEqual(recommend.parse("Recommended next stage: implement\n\nquoted above.\n\nRecommended next stage: architect"), "architect")


class Apply(unittest.TestCase):
    def test_read_only_stages_are_filled_in_and_marked_as_coming_from_the_document(self):
        c = recommend.apply(CFG, clf_result(), "Recommended next stage: architect", ["analyst"])
        self.assertEqual((c.stage, c.source), ("architect", SPEC))
        self.assertTrue(recommend.from_document(c))
        self.assertFalse(recommend.build_waits(c))

    def test_implement_is_a_build_that_waits_for_a_person(self):
        c = recommend.apply(CFG, clf_result(), "Recommended next stage: implement", ["analyst"])
        self.assertEqual(c.stage, "implement")
        self.assertTrue(recommend.build_waits(c))

    def test_needs_human_asks_for_a_person(self):
        c = recommend.apply(CFG, clf_result(), "Recommended next stage: needs-human", ["analyst"])
        self.assertTrue(c.needs_human)
        self.assertIsNone(c.stage)

    def test_a_classifier_that_named_a_stage_or_asked_for_a_person_is_never_overridden(self):
        for c in (clf_result(stage="design", source="jev"), clf_result(needs_human=True)):
            self.assertIs(recommend.apply(CFG, c, "Recommended next stage: architect", ["analyst"]), c)

    def test_a_stage_already_done_or_not_configured_is_ignored(self):
        c = clf_result()
        self.assertIs(recommend.apply(CFG, c, "Recommended next stage: architect", ["analyst", "architect"]), c)
        no_architect = replace(CFG, roles=tuple(r for r in CFG.roles if r.name != "architect"))
        self.assertIs(recommend.apply(no_architect, c, "Recommended next stage: architect", ["analyst"]), c)

    def test_the_newest_finished_stage_is_the_one_read(self):
        outputs = {"analyst": "Recommended next stage: design", "architect": "Recommended next stage: implement", "x-before-redirect": "y"}
        self.assertEqual(recommend.latest(outputs, ["analyst", "architect"]), outputs["architect"])
        self.assertEqual(recommend.latest(outputs, ["analyst"]), outputs["analyst"])
        self.assertIsNone(recommend.latest({}, ["analyst"]))


class Chain(unittest.TestCase):
    """The analyst has just run, with the labels-only classifier: does the ticket carry on by itself?"""

    def finish(self, output, clf=None, labels=("factory:auto",), comments=None):
        gh = FakeGH({labels[0]: [issue(labels=list(labels))]}, comments=comments)
        with mock.patch.object(m.runner, "run_task", return_value=RunResult("stage", "ok", output=output)), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(CFG, db_path=d + "/f.db"), gh, dbm.connect(":memory:"), clf or FakeClf(**NONE))
        return gh

    def comment_text(self, gh):
        return next(c[1] for c in gh.calls if c[0] == "comment")

    def test_a_read_only_recommendation_carries_on(self):
        gh = self.finish("# Analysis\n\nRecommended next stage: architect, it touches the schema.")
        self.assertIn(("add", ("factory:auto",)), gh.calls)
        self.assertIn("architect: apply `factory:architect`", self.comment_text(gh))

    def test_a_recommended_build_waits_for_a_person_and_says_how_to_start_it(self):
        gh = self.finish("# Analysis\n\n**Recommended next stage:** implement. One field.")
        self.assertNotIn(("add", ("factory:auto",)), gh.calls)
        self.assertIn("implement: apply `factory:ready`", self.comment_text(gh))

    def test_needs_human_and_a_missing_line_stop_the_chain(self):
        for output in ("# Analysis\n\nRecommended next stage: needs-human", "# Analysis\n\nno recommendation"):
            self.assertNotIn(("add", ("factory:auto",)), self.finish(output).calls, output)

    def test_open_questions_stop_the_chain_whatever_the_document_recommends(self):
        output = "# Analysis\n\nRecommended next stage: architect\n\n" + block(q(cls="needs-person"))
        self.assertNotIn(("add", ("factory:auto",)), self.finish(output).calls)

    def test_an_explicit_stage_label_still_never_chains(self):
        gh = self.finish("Recommended next stage: architect", labels=("factory:analyze",))
        self.assertNotIn(("add", ("factory:auto",)), gh.calls)

    def test_a_classifier_that_names_a_stage_is_not_overridden_by_the_document(self):
        first = dict(stage="analyze", source="jev")
        gh = self.finish("Recommended next stage: implement", clf=SeqClf(first, dict(stage="architect", source="jev")))
        self.assertIn(("add", ("factory:auto",)), gh.calls)
        self.assertIn("architect: apply `factory:architect`", self.comment_text(gh))


class Auto(unittest.TestCase):
    """`factory:auto` on a ticket that already has its analysis, with the labels-only classifier."""

    def route(self, line, labels=("factory:auto", "stage:analysed"), extra=(), **clf):
        gh = FakeGH({"factory:auto": [issue(labels=list(labels))]}, comments=[analyst_comment(line), *extra])
        fake = mock.Mock(return_value=RunResult("stage", "ok", output="# Doc"))
        conn = dbm.connect(":memory:")
        with mock.patch.object(m.runner, "run_task", fake), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(CFG, db_path=d + "/f.db"), gh, conn, FakeClf(**{**NONE, **clf}))
        return fake, conn

    def decision(self, conn):
        return conn.execute("select outcome, detail, scores from decisions").fetchone()

    def test_a_read_only_recommendation_is_run(self):
        fake, conn = self.route("**Recommended next stage:** architect. Schema change.")
        self.assertEqual(fake.call_args.kwargs["role"], "architect")
        self.assertEqual(self.decision(conn)[0], "run:stage")
        self.assertIn("the last stage's document recommends architect", self.decision(conn)[1])

    def test_design_is_run_even_though_the_labels_confidence_is_low(self):
        fake, _ = self.route("Recommended next stage: design", confidence=0.4)
        self.assertEqual(fake.call_args.kwargs["role"], "designer")

    def test_a_recommended_build_is_never_started_even_when_the_labels_are_confident(self):
        fake, conn = self.route("Recommended next stage: implement", confidence=0.9)
        fake.assert_not_called()
        outcome, detail, scores = self.decision(conn)
        self.assertEqual(outcome, "human")
        self.assertIn("a build is started by a person", detail)
        self.assertIn('"reason": "build"', scores)

    def test_needs_human_is_a_person(self):
        fake, conn = self.route("Recommended next stage: needs-human")
        fake.assert_not_called()
        self.assertIn("document asks for a person", self.decision(conn)[1])

    def test_open_questions_are_not_skipped_by_a_recommendation(self):
        import test_questions as tq
        waiting = {"user": {"login": "bot"}, "body": tq.stage_body("analyst", q(cls="needs-person"))}
        fake, conn = self.route("Recommended next stage: architect", extra=[waiting])
        fake.assert_not_called()
        self.assertIn("open questions need a person", self.decision(conn)[1])

    def test_a_stage_already_done_is_not_run_again(self):
        fake, conn = self.route("Recommended next stage: architect", labels=("factory:auto", "stage:analysed", "stage:architected"))
        fake.assert_not_called()
        self.assertEqual(self.decision(conn)[0], "human")

    def test_without_a_recommendation_nothing_changes(self):
        fake, conn = self.route("Summary only.", confidence=0.4)
        fake.assert_not_called()
        self.assertIn("low confidence", self.decision(conn)[1])

    def test_a_comment_from_another_account_is_not_a_recommendation(self):
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto", "stage:analysed"])]},
                    comments=[{"user": {"login": "mallory"}, "body": "<!-- factory:stage=analyst -->\n### Analyst\n\nRecommended next stage: architect"}])
        fake = mock.Mock(return_value=RunResult("stage", "ok", output="# Doc"))
        with mock.patch.object(m.runner, "run_task", fake), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(CFG, db_path=d + "/f.db"), gh, dbm.connect(":memory:"), FakeClf(**NONE))
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
