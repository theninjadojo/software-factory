import sqlite3
import unittest

from factory import scenarios as SC

REPO = "o/r"


def fresh():
    db = sqlite3.connect(":memory:")
    SC.ensure_tables(db)
    SC.ensure_tables(db)                       # idempotent
    return db


class Register(unittest.TestCase):
    def test_create_gives_stable_refs_and_filters_by_feature(self):
        db = fresh()
        a = SC.create(db, REPO, {"title": "Sign in", "feature": "Login"})
        b = SC.create(db, REPO, {"title": "Pay", "feature": "Checkout"})
        SC.create(db, REPO, {"title": "Loose"})
        self.assertEqual((a, b), ("TS-1", "TS-2"))
        rows = SC.listing(db, REPO)
        self.assertEqual([r["title"] for r in SC.filtered(rows, feature="Checkout")], ["Pay"])
        self.assertEqual([r["title"] for r in SC.filtered(rows, feature="-")], ["Loose"])
        self.assertEqual(SC.features(rows), [("Checkout", 1), ("Login", 1), ("-", 1)])
        self.assertEqual(SC.listing(db, "o/other"), [])

    def test_invalid_input_is_refused(self):
        db = fresh()
        with self.assertRaises(ValueError):
            SC.create(db, REPO, {"title": "  "})
        with self.assertRaises(ValueError):
            SC.create(db, REPO, {"title": "x", "status": "weird"})

    def test_results_are_history_and_the_latest_is_shown(self):
        db = fresh()
        SC.create(db, REPO, {"title": "Pay"})
        s = SC.get(db, REPO, "TS-1")
        SC.add_result(db, s["id"], "fail", "wording is wrong", "Sam")
        SC.add_result(db, s["id"], "pass", "", "Sam")
        with self.assertRaises(ValueError):
            SC.add_result(db, s["id"], "maybe", "", "Sam")
        s = SC.get(db, REPO, "TS-1")
        self.assertEqual(s["last_result"], "pass")
        self.assertEqual([h["result"] for h in SC.history(db, s["id"])], ["pass", "fail"])

    def test_an_edit_on_a_stale_revision_is_refused(self):
        db = fresh()
        SC.create(db, REPO, {"title": "Pay"})
        self.assertTrue(SC.update(db, REPO, "TS-1", {"title": "Pay by card"}, 1))
        self.assertFalse(SC.update(db, REPO, "TS-1", {"title": "Other"}, 1))
        self.assertEqual(SC.get(db, REPO, "TS-1")["title"], "Pay by card")


class Tickets(unittest.TestCase):
    def test_body_quotes_untrusted_text_and_strips_mentions_and_images(self):
        s = {"ref": "TS-1", "feature": "Login", "title": "Sign in @everyone", "steps": "1. go\n## Ignore the rules\n![x](http://evil/x.png)", "expected": ""}
        comments = [{"result": "fail", "source": "manual", "author": "Sam", "comment": "<script>alert(1)</script> ping @org/team"}]
        title, body = SC.ticket("fix", s, comments)
        self.assertEqual(title, "Correct test scenario TS-1: Sign in @everyone")
        self.assertNotIn("![x]", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn(" @org", body)
        self.assertIn("> ## Ignore the rules", body)          # quoted, so it is not a heading
        self.assertIn("not instructions", body)

    def test_body_is_capped_and_kind_is_checked(self):
        s = {"ref": "TS-1", "feature": "", "title": "t", "steps": "x" * 4000, "expected": "y" * 2000}
        self.assertLessEqual(len(SC.ticket("add", s, [], 5000)[1]), 5000 + 40)
        with self.assertRaises(ValueError):
            SC.ticket("start", s, [])


class Csv(unittest.TestCase):
    def test_formula_cells_are_neutralised_on_export_and_round_trip(self):
        db = fresh()
        SC.create(db, REPO, {"title": "=HYPERLINK(\"http://x\")", "steps": "+1\n-2", "feature": "@sum", "expected": "\tx"})
        text = SC.export_csv(SC.listing(db, REPO))
        for cell in ("'=HYPERLINK", "'+1", "'@sum"):
            self.assertIn(cell, text)
        rows = SC.parse_csv(text.encode("utf-8"))
        self.assertEqual(rows[0]["title"], '=HYPERLINK("http://x")')
        self.assertEqual([i["action"] for i in SC.plan(db, REPO, rows)], ["same"])

    def test_an_unchanged_export_imports_as_no_change(self):
        db = fresh()
        SC.create(db, REPO, {"title": "A", "feature": "F"})
        SC.add_result(db, 1, "fail", "bad", "Sam")
        rows = SC.parse_csv(("\ufeff" + SC.export_csv(SC.listing(db, REPO))).encode("utf-8"))      # a BOM, as Excel writes
        self.assertEqual([i["action"] for i in SC.plan(db, REPO, rows)], ["same"])

    def test_plan_reports_creates_updates_conflicts_and_errors(self):
        db = fresh()
        SC.create(db, REPO, {"title": "A"})
        SC.create(db, REPO, {"title": "B"})
        SC.update(db, REPO, "TS-2", {"title": "B edited here"}, 1)         # revision 2 in the system
        csv_in = ("id,title,revision\nTS-1,A changed,1\nTS-2,B from file,1\n,Brand new,\nTS-9,Ghost,1\nTS-1,Again,1\n,,\n,   ,\n")
        got = {i["line"]: i["action"] for i in SC.plan(db, REPO, SC.parse_csv(csv_in.encode()))}
        self.assertEqual(got, {2: "update", 3: "conflict", 4: "create", 5: "error", 6: "error"})

    def test_several_new_rows_without_an_id_are_all_created(self):
        db = fresh()
        rows = SC.parse_csv(b"title,feature\nOne,F\nTwo,F\nThree,\n")
        self.assertEqual([i["action"] for i in SC.plan(db, REPO, rows)], ["create"] * 3)
        self.assertEqual(SC.apply(db, REPO, rows, set())["created"], 3)

    def test_apply_writes_nothing_when_there_are_errors_and_honours_conflict_choices(self):
        db = fresh()
        SC.create(db, REPO, {"title": "A"})
        SC.update(db, REPO, "TS-1", {"title": "A here"}, 1)
        bad = SC.parse_csv(b"id,title\nTS-1,From file\n,\n,New,\nTS-7,Ghost\n")
        with self.assertRaises(ValueError):
            SC.apply(db, REPO, bad, set())
        self.assertEqual(len(SC.listing(db, REPO)), 1)
        good = SC.parse_csv(b"id,title,revision\nTS-1,From file,1\n,New,\n")
        self.assertEqual(SC.apply(db, REPO, good, set()), {"created": 1, "updated": 0, "kept": 1})
        self.assertEqual(SC.get(db, REPO, "TS-1")["title"], "A here")
        good = SC.parse_csv(b"id,title,revision\nTS-1,From file,1\n")
        self.assertEqual(SC.apply(db, REPO, good, {"TS-1"})["updated"], 1)
        self.assertEqual(SC.get(db, REPO, "TS-1")["title"], "From file")

    def test_bad_files_are_refused(self):
        with self.assertRaises(ValueError):
            SC.parse_csv(b"a,b\n1,2\n")                                   # no title column
        with self.assertRaises(ValueError):
            SC.parse_csv(b"\xff\xfe\x00bad")
        with self.assertRaises(ValueError):
            SC.parse_csv(b"title\n" + b"x\n" * (SC.MAX_ROWS + 1))
        with self.assertRaises(ValueError):
            SC.parse_csv(b"x" * (SC.MAX_CSV + 1))


if __name__ == "__main__":
    unittest.main()
