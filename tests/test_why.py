import json
import unittest

from factory import db as dbm, why
from factory.classifier import RuleClassifier
from factory.jev import parse_answers
from factory.ui import board
from test_jev import answers


def card(scores, reason="low confidence"):
    row = {"repo": "o/r", "issue": 1, "acts": [], "reason": reason, "scores": why.parse(scores) if isinstance(scores, str) else scores}
    return board.needs_card(row, "tok", "/ticket")


class T(unittest.TestCase):
    def low(self):
        c = parse_answers(answers(kind="feature", kconf=0.91, tier="standard", tconf=0.62, noul=0.06), set())
        return c, why.build(0.75, c, "low")

    def test_parse_answers_keeps_each_score(self):
        c, _ = self.low()
        self.assertEqual(c.scores["kind"], ["feature", 0.91])
        self.assertEqual(c.scores["size"], ["standard", 0.62])
        self.assertEqual(c.scores["human"][0], "no")
        self.assertIsNone(parse_answers(answers(), {"bug"}).scores["kind"])      # a label set it: no score

    def test_card_shows_score_threshold_and_weakest(self):
        _, s = self.low()
        html = card(s)
        for text in ("Confidence 0.62, below the threshold of 0.75.", "Size (weakest)", "Below by 0.13", "Above", "Needs a person: low confidence"):
            self.assertIn(text, html)

    def test_alert_line(self):
        _, s = self.low()
        self.assertEqual(why.line(why.parse(s)), "low confidence 0.62 (threshold 0.75). Weakest: size, standard, 0.62.")

    def test_label_override_has_no_score(self):
        c = parse_answers(answers(tconf=0.5), {"bug"})
        self.assertIn("Set by label", card(why.build(0.75, c, "low")))

    def test_other_reasons_do_not_say_low_confidence(self):
        c, _ = self.low()
        html = card(why.build(0.75, c, "human"))
        self.assertIn("flagged this ticket as needing a person", html)
        self.assertNotIn("low confidence", html)
        self.assertNotIn("<table", html)

    def test_labels_only_names_the_missing_label(self):
        c = RuleClassifier().classify("t", "", ["bug"])
        html = card(why.build(0.75, c, "low"))
        self.assertIn("size label is missing", html)
        self.assertIn("0.40", html)

    def test_old_decision_renders_old_reason(self):
        html = card(None, reason="low confidence")
        self.assertIn("low confidence", html)
        self.assertIn("scores for this decision are not available", html)

    def test_bad_stored_scores_read_as_not_available(self):
        for bad in (None, "", "nope", "[]", json.dumps({"reason": "low", "threshold": "x", "confidence": 1}),
                    json.dumps({"reason": "<script>", "threshold": 1, "confidence": 1})):
            self.assertIsNone(why.parse(bad))

    def test_choice_text_is_escaped(self):
        s = json.dumps({"reason": "low", "threshold": 0.75, "confidence": 0.5, "source": "m",
                        "answers": {"size": ["<script>alert(1)</script>", 0.5]}})
        html = card(s)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_decision_row_round_trips_scores(self):
        db = dbm.connect(":memory:")
        _, s = self.low()
        dbm.record(db, "o/r", 1, "u", "human", "d", s)
        dbm.record(db, "o/r", 2, "u", "human", "d")
        got = {t["issue"]: t["scores"] for t in dbm.tickets(db)}
        self.assertEqual(why.parse(got[1])["confidence"], 0.62)
        self.assertIsNone(got[2])
