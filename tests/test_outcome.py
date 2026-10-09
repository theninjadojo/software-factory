import unittest

from factory.ui import board, outcome


def row(**kw):
    return {"prs": [], "closed": False, "decision": "", "detail": "", **kw}


class OutcomeTests(unittest.TestCase):
    def test_merged_beats_closed(self):
        o = outcome.derive(row(prs=[{"number": 7, "status": "closed", "summary": "merged"}], closed=True))
        self.assertEqual((o["tone"], o["headline"], o["reason"]), ("good", "Done · merged", "PR #7 merged."))

    def test_closed_without_merge(self):
        o = outcome.derive(row(prs=[{"number": 7, "status": "closed", "summary": "closed"}]))
        self.assertEqual(o["headline"], "Done · closed without merging")

    def test_closed_ignored_document_and_fallback(self):
        self.assertEqual(outcome.derive(row(closed=True))["headline"], "Done · closed by a person")
        self.assertEqual(outcome.derive(row(decision="ignored"), why_ignored="Not a bug")["reason"], "Not a bug")
        self.assertIn("analyst", outcome.derive(row(), "s", "analyst")["headline"])
        o = outcome.derive(row(), "stray sentence")
        self.assertEqual((o["tone"], o["sentence"]), ("warn", ""))

    def test_preview_cuts_on_a_word(self):
        out = outcome.preview("word " * 40, 120)
        self.assertTrue(out.endswith("…") and len(out) <= 121)
        self.assertEqual(outcome.preview("short"), "short")

    def test_card_escapes_untrusted_text(self):
        o = outcome.derive(row(), "<script>alert(1)</script>", "analyst")
        html = board.outcome_card(o, "a/b", 5)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_card_links_only_a_valid_repo(self):
        o = outcome.derive(row(prs=[{"number": 7, "status": "closed", "summary": "merged"}]))
        self.assertIn("/pull/7", board.outcome_card(o, "a/b", 5))
        self.assertNotIn("/pull/", board.outcome_card(o, 'a"/b', 5))


if __name__ == "__main__":
    unittest.main()
