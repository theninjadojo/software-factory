"""Second opinions on open questions: the judges see the question differently, only agreement with the recommendation answers it,
always-ask categories never reach them, every failure leaves the question for a person, and a person can change the answer."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import judges as J
from factory import main as m
from factory import questions as Q
from factory.config import QuestionsCfg, _questions
from factory.runner import RunResult, build_prompt
from factory.config import Project, ProjectRepo
from test_questions import FIRST, NEXT, doc, q
from test_roles import CFG, FakeGH, SeqClf, issue

ON = replace(CFG, questions=QuestionsCfg(auto_answer=True))


def qs(*raw):
    return Q.extract(doc(*raw))[1]


STORE = q("q2", "Which store should the drafts use?", Q.PERSON, "b", "The app already uses it.", (("a", "Files"), ("b", "SQLite")))
LAYOUT = q("q3", "Should the list be grouped by day?", Q.PERSON, "a", "Easier to scan.", (("a", "Grouped"), ("b", "Flat")))


def agrees(judge="jev", conf=0.9):
    return lambda questions: {x.id: J.Verdict(judge, x.recommended, conf) for x in questions}


class Decide(unittest.TestCase):
    def test_both_judges_agreeing_answers_it(self):
        found = J.decide(ON, qs(STORE), 0, agrees(), agrees("opus"))
        self.assertEqual(list(found), ["q2"])
        self.assertIn("jev 0.90", found["q2"])
        self.assertIn("opus 0.90", found["q2"])

    def test_disagreement_low_confidence_or_a_failed_judge_leaves_it_for_a_person(self):
        other = lambda questions: {x.id: J.Verdict("opus", "a", 0.95) for x in questions}
        unsure = agrees("opus", 0.6)
        def boom(questions):
            raise RuntimeError("down")
        for agent in (other, unsure, boom, lambda questions: {}):
            self.assertEqual(J.decide(ON, qs(STORE), 0, agrees(), agent), {})

    def test_the_agent_is_only_asked_about_what_jev_agreed_on(self):
        seen = []
        def agent(questions):
            seen.extend(x.id for x in questions)
            return agrees("opus")(questions)
        jev = lambda questions: {"q2": J.Verdict("jev", "b", 0.9), "q3": J.Verdict("jev", "b", 0.9)}
        self.assertEqual(list(J.decide(ON, qs(STORE, LAYOUT), 0, jev, agent)), ["q2"])
        self.assertEqual(seen, ["q2"])
        seen.clear()
        self.assertEqual(J.decide(ON, qs(STORE), 0, lambda questions: {}, agent), {})
        self.assertEqual(seen, [])                       # nothing left that could pass: no run

    def test_any_needs_one_agreeing_and_none_confidently_disagreeing(self):
        cfg = replace(ON, questions=replace(ON.questions, require="any"))
        self.assertEqual(list(J.decide(cfg, qs(STORE), 0, lambda questions: {}, agrees("opus"))), ["q2"])
        against = lambda questions: {x.id: J.Verdict("jev", "a", 0.85) for x in questions}
        self.assertEqual(J.decide(cfg, qs(STORE), 0, against, agrees("opus")), {})

    def test_always_ask_categories_never_reach_the_judges(self):
        token = q("q4", "Where should the API token live?", Q.PERSON, "a", "Simplest.", (("a", "Env"), ("b", "Vault")))
        asked = []
        judge = lambda questions: asked.extend(x.id for x in questions) or agrees()(questions)
        self.assertEqual(J.decide(ON, qs(token), 0, judge, judge), {})
        self.assertEqual(asked, [])
        loose = replace(ON, questions=replace(ON.questions, always_ask=("cost",)))
        self.assertEqual(list(J.decide(loose, qs(token), 0, agrees(), agrees("opus"))), ["q4"])

    def test_the_cap_per_ticket_sends_them_all_to_a_person(self):
        self.assertEqual(J.decide(ON, qs(STORE, LAYOUT), 2, agrees(), agrees("opus")), {})
        self.assertEqual(len(J.decide(ON, qs(STORE, LAYOUT), 1, agrees(), agrees("opus"))), 2)

    def test_no_judge_answers_nothing(self):
        self.assertEqual(J.decide(ON, qs(STORE), 0, None, None), {})


class Jev(unittest.TestCase):
    def test_jev_is_blind_to_the_recommendation(self):
        sent = {}
        def post(body):
            sent.update(body)
            return {"answers": {"q_q2": {"type": "choice", "choice": "b", "confidence": 0.93}}}
        got = J.jev_verdicts("k", "typesafe/jev-1.13", "Drafts", "Keep drafts", "analyst", "# Doc", qs(STORE),
                             [{"question": "Q?", "chosen": "X", "recommended": "Y"}], post=post)
        self.assertEqual(got["q2"], J.Verdict("jev", "b", 0.93))
        text = json.dumps(sent)
        self.assertNotIn("already uses it", text)
        self.assertNotIn("recommended\": \"b", text)
        self.assertEqual(sent["questions"]["q_q2"]["criteria"], {"a": "Files", "b": "SQLite"})
        self.assertEqual(sent["state"]["owner_past_answers"][0]["chosen"], "X")

    def test_a_bad_reply_or_an_error_gives_nothing(self):
        for reply in ({"answers": {"q_q2": {"type": "choice", "choice": "z", "confidence": 0.9}}}, {"nope": 1}):
            self.assertEqual(J.jev_verdicts("k", "m", "t", "b", "analyst", "d", qs(STORE), [], post=lambda b, r=reply: r), {})


class Agent(unittest.TestCase):
    def test_the_block_is_validated(self):
        body = {"answers": [{"id": "q2", "option": "b", "confidence": 0.85, "evidence": "app/db.py", "reason": "Uses SQLite."},
                            {"id": "q9", "option": "a", "confidence": 1}, {"id": "q3", "option": "z", "confidence": 1},
                            {"id": "q3", "option": "a", "confidence": 1.5}]}
        got = J.parse_agent("doc\n```factory-second-opinion\n" + json.dumps(body) + "\n```\n", qs(STORE, LAYOUT), "opus")
        self.assertEqual(got, {"q2": J.Verdict("opus", "b", 0.85, "Uses SQLite.", "app/db.py")})
        self.assertEqual(J.parse_agent("no block", qs(STORE), "opus"), {})
        self.assertEqual(J.parse_agent("```factory-second-opinion\n{bad\n```\n", qs(STORE), "opus"), {})

    def test_the_prompt_carries_the_questions_with_the_recommendation_and_past_answers(self):
        p = build_prompt("t", "b", Project("p", (ProjectRepo("o/r"),)), "o/r", "second-opinion", judge=J.agent_brief(qs(STORE)),
                         history=[{"question": "Old?", "chosen": "Yes", "recommended": "No"}])
        self.assertIn("make the strongest case against the recommended option", p)
        self.assertIn("recommended: b (The app already uses it.)", p)
        self.assertIn("<owner_past_answers>", p)
        self.assertIn("factory-second-opinion", p)


class Answers(unittest.TestCase):
    def comments(self, *extra):
        body = f"<!-- factory:stage=analyst -->\n" + Q.stored(qs(STORE)) + "### Analyst\n\nbody"
        auto = Q.answers_comment("analyst", qs(STORE), {"q2": ("option", "b")}, "second opinion", {"q2": "jev 0.90; opus 0.88"})
        return [{"user": {"login": "bot"}, "body": b} for b in (body, auto, *extra)]

    def test_an_automatic_answer_settles_the_question_and_says_so(self):
        st = Q.from_comments(self.comments(), "bot")[0]
        self.assertEqual(st.pending(), [])
        self.assertEqual(st.auto, {"q2": "jev 0.90; opus 0.88"})
        self.assertIn("answered automatically", Q.summary([st]))

    def test_a_person_changing_it_is_counted_and_confirming_it_is_not(self):
        change = Q.answers_comment("analyst", qs(STORE), {"q2": ("option", "a")}, "factory UI")
        st = Q.from_comments(self.comments(change), "bot")[0]
        self.assertEqual((st.auto, st.overturned, st.answers["q2"]), ({}, {"q2"}, ("option", "a")))
        same = Q.answers_comment("analyst", qs(STORE), {"q2": ("option", "b")}, "factory UI")
        self.assertEqual(Q.from_comments(self.comments(same), "bot")[0].overturned, set())

    def test_a_forged_automatic_answer_is_ignored(self):
        cs = self.comments()
        cs[1]["user"]["login"] = "mallory"
        self.assertEqual(len(Q.from_comments(cs, "bot")[0].pending()), 1)

    def test_recording_the_same_answer_again_changes_nothing(self):
        gh = FakeGH(comments=self.comments())
        self.assertEqual(Q.record(gh, "o/r", 5, "analyst", {"q2": ("option", "b")}, "factory UI"), ("analyst", False))
        self.assertEqual([c for c in gh.calls if c[0] == "comment"], [])
        self.assertEqual(Q.record(gh, "o/r", 5, "analyst", {"q2": ("option", "a")}, "factory UI"), ("analyst", True))

    def test_answers_are_kept_and_tallied(self):
        conn = dbm.connect(":memory:")
        m.save_settled(conn, "o/r", 5, Q.from_comments(self.comments(), "bot"))
        self.assertEqual((dbm.auto_tally(conn), dbm.past_answers(conn, "o/r")), ((1, 0), []))
        change = Q.answers_comment("analyst", qs(STORE), {"q2": ("option", "a")}, "factory UI")
        m.save_settled(conn, "o/r", 5, Q.from_comments(self.comments(change), "bot"))
        self.assertEqual(dbm.auto_tally(conn), (1, 1))
        self.assertEqual(dbm.past_answers(conn, "o/r")[0]["chosen"], "Files")


class Config(unittest.TestCase):
    def test_defaults_are_off_and_bad_values_are_refused(self):
        self.assertFalse(_questions({}).auto_answer)
        self.assertEqual(_questions({"always_ask": ["cost"]}).always_ask, ("cost",))
        for bad in ({"min_confidence": 0.2}, {"require": "most"}, {"max_auto_per_ticket": 0}, {"always_ask": ["anything"]},
                    {"auto_answer": "yes"}, {"effort": "max"}):
            with self.assertRaises(ValueError, msg=bad):
                _questions(bad)


def run_stage(output, judge_output, cfg, jev_reply=None):
    """One analyst run through the poll, with the agent judge's reply and (when given) Jev's."""
    gh = FakeGH({"factory:auto": [issue(labels=["factory:auto"])]})
    def task(*a, **kw):
        return RunResult("stage", "ok", output=judge_output if kw.get("role") == "second-opinion" else output)
    with mock.patch.object(m.runner, "run_task", side_effect=task) as rt, tempfile.TemporaryDirectory() as d, \
            mock.patch.object(J.jev, "post", return_value=jev_reply or {}):
        key = Path(d) / "key"
        key.write_text("k")
        conn = dbm.connect(":memory:")
        m.poll_once(replace(cfg, db_path=d + "/f.db", openrouter_key_file=str(key)), gh, conn, SeqClf(FIRST, NEXT))
    return gh, rt, conn


AGENT_YES = "Looked.\n```factory-second-opinion\n" + json.dumps({"answers": [{"id": "q2", "option": "b", "confidence": 0.9,
                                                                              "evidence": "app/db.py", "reason": "SQLite is there."}]}) + "\n```\n"
JEV_YES = {"answers": {"q_q2": {"type": "choice", "choice": "b", "confidence": 0.88}}}


class Flow(unittest.TestCase):
    def test_agreement_answers_the_question_and_the_chain_goes_on(self):
        gh, rt, conn = run_stage(doc(q(), STORE), AGENT_YES, ON, JEV_YES)
        bodies = [c[1] for c in gh.calls if c[0] == "comment"]
        self.assertTrue(bodies[0].startswith("<!-- factory:stage=analyst -->"))
        self.assertTrue(bodies[1].startswith(Q.ANSWERS))
        self.assertIn("answered automatically", bodies[1])
        self.assertIn("evidence: app/db.py", bodies[1])
        self.assertIn(("add", ("factory:auto",)), gh.calls)
        self.assertNotIn(("add", (Q.NEEDS_ANSWERS,)), gh.calls)
        self.assertEqual(conn.execute("select count(*) from open_questions").fetchone()[0], 0)
        self.assertEqual(dbm.auto_tally(conn), (1, 0))
        judge = [c for c in rt.call_args_list if c.kwargs.get("role") == "second-opinion"]
        self.assertEqual(len(judge), 1)
        self.assertIn("recommended: b", judge[0].kwargs["judge"])

    def test_a_disagreeing_judge_leaves_it_for_a_person(self):
        no = AGENT_YES.replace('"option": "b"', '"option": "a"')
        gh, _, conn = run_stage(doc(q(), STORE), no, ON, JEV_YES)
        self.assertIn(("add", (Q.NEEDS_ANSWERS,)), gh.calls)
        self.assertNotIn(("add", ("factory:auto",)), gh.calls)
        self.assertFalse(any(c[1].startswith(Q.ANSWERS) for c in gh.calls if c[0] == "comment"))
        self.assertEqual(conn.execute("select pending from open_questions").fetchone()[0], 1)

    def test_off_by_default_nothing_is_asked(self):
        gh, rt, _ = run_stage(doc(q(), STORE), AGENT_YES, CFG, JEV_YES)
        self.assertEqual([c for c in rt.call_args_list if c.kwargs.get("role") == "second-opinion"], [])
        self.assertIn(("add", (Q.NEEDS_ANSWERS,)), gh.calls)


if __name__ == "__main__":
    unittest.main()
