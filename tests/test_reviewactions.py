import sqlite3
import unittest
from dataclasses import replace

from factory import db as dbm
from factory import reviewactions as RA
from factory.config import ReviewCfg
from test_roles import CFG

LABELS, STAGES = ("review:needs-changes", "review:blocking"), ["analyst", "architect"]
CFG_ON = replace(CFG, review=ReviewCfg(enabled=True, follow_actions=True))


def block(body: str) -> str:
    return f"**Verdict**: Needs changes\n\n```factory-review-actions\n{body}\n```\n"


class FakeGH:
    def __init__(self, state="open"):
        self.state, self.comments, self.labels = state, [], []

    def get_issue(self, repo, n):
        return {"number": n, "state": self.state, "labels": []}

    def comment(self, repo, n, body):
        self.comments.append(body)

    def add_labels(self, repo, n, labels):
        self.labels += labels


class Parse(unittest.TestCase):
    def test_keeps_only_allowed_entries(self):
        text = block('{"actions": [{"kind": "label", "value": "review:blocking", "reason": "Bad\\n @x"},'
                     '{"kind": "label", "value": "factory:implement", "reason": "start work"},'
                     '{"kind": "send-back", "value": "ghost"}, {"kind": "send-back", "value": "architect"},'
                     '{"kind": "close", "value": "x"}, "junk", {"kind": "label", "value": ["a"]}]}')
        got = RA.parse(text, LABELS, STAGES)
        self.assertEqual([(k, v) for k, v, _ in got], [("label", "review:blocking"), ("send-back", "architect")])

    def test_missing_or_malformed_block_gives_nothing(self):
        for t in ("no block", block("{not json"), block('{"actions": 3}'), block("[]")):
            self.assertEqual(RA.parse(t, LABELS, STAGES), [])

    def test_last_block_wins_and_capped(self):
        many = ",".join('{"kind": "send-back", "value": "analyst"}' for _ in range(9))
        self.assertEqual(len(RA.parse(block('{"actions": []}') + block('{"actions": [' + many + "]}"), LABELS, STAGES)), 1)

    def test_strip_removes_the_block(self):
        self.assertNotIn("factory-review-actions", RA.strip(block('{"actions": []}')))

    def test_trigger_labels_are_not_follow_labels(self):
        self.assertFalse(RA.valid_label("factory:implement"))
        self.assertFalse(RA.valid_label("stage:reviewed"))
        self.assertFalse(RA.valid_label("go", {"GO"}))
        self.assertTrue(RA.valid_label("review:blocking"))

    def test_prompt_only_when_on(self):
        self.assertEqual(RA.prompt(CFG), "")
        self.assertIn("review:blocking", RA.prompt(CFG_ON))


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS approvals (repo TEXT, issue INTEGER, action TEXT, created REAL, PRIMARY KEY (repo, issue))")
        self.db.execute("CREATE TABLE IF NOT EXISTS prs (repo TEXT, number INTEGER, issue_repo TEXT, issue_num INTEGER, status TEXT)")
        self.db.execute("INSERT INTO prs VALUES ('o/r', 9, 'o/r', 5, 'open')")
        RA.ensure_tables(self.db)
        self.prs = [("o/r", 9)]

    def store(self, found):
        return RA.store(self.db, "o/r", 5, self.prs, found, 1.0)

    def ids(self):
        return [r["id"] for r in RA.pending(self.db, "o/r", 5)]

    def test_nothing_applies_until_picked(self):
        self.store([("label", "review:blocking", "why")])
        gh = FakeGH()
        RA.process(CFG_ON, gh, self.db, lambda *a: None)
        self.assertEqual((gh.labels, gh.comments), ([], []))

    def test_picked_label_is_applied_once(self):
        self.store([("label", "review:blocking", "why")])
        self.assertEqual(RA.decide(self.db, "o/r", 5, self.ids(), True, 2.0), 1)
        gh = FakeGH()
        RA.process(CFG_ON, gh, self.db, lambda *a: None)
        RA.process(CFG_ON, gh, self.db, lambda *a: None)
        self.assertEqual(gh.labels, ["review:blocking"])
        self.assertEqual(len(gh.comments), 1)

    def test_ids_of_another_ticket_are_ignored(self):
        self.store([("label", "review:blocking", "")])
        self.assertEqual(RA.decide(self.db, "o/r", 6, self.ids(), True, 2.0), 0)
        self.assertEqual(RA.decide(self.db, "x/y", 5, self.ids(), True, 2.0), 0)

    def test_stale_when_closed_or_prs_changed(self):
        self.store([("label", "review:blocking", "")])
        RA.decide(self.db, "o/r", 5, self.ids(), True, 2.0)
        gh = FakeGH(state="closed")
        RA.process(CFG_ON, gh, self.db, lambda *a: None)
        self.assertEqual(gh.labels, [])
        self.store([("label", "review:blocking", "")])
        RA.decide(self.db, "o/r", 5, self.ids(), True, 2.0)
        self.db.execute("INSERT INTO prs VALUES ('o/r', 10, 'o/r', 5, 'open')")
        gh = FakeGH()
        RA.process(CFG_ON, gh, self.db, lambda *a: None)
        self.assertEqual(gh.labels, [])
        self.assertEqual(self.db.execute("SELECT status FROM review_actions ORDER BY id").fetchall(), [("stale",), ("stale",)])

    def test_send_back_queues_the_existing_approval(self):
        cfg = replace(CFG_ON)
        stage = cfg.roles[0].name
        self.store([("send-back", stage, "redo")])
        RA.decide(self.db, "o/r", 5, self.ids(), True, 2.0)
        RA.process(cfg, FakeGH(), self.db, lambda *a: None)
        self.assertEqual(dbm.approvals(self.db), [("o/r", 5, f"redirect:{stage}")])

    def test_send_back_waits_while_another_decision_is_queued(self):
        stage = CFG_ON.roles[0].name
        self.db.execute("INSERT INTO approvals VALUES ('o/r', 5, 'run', 1.0)")
        self.store([("send-back", stage, "")])
        RA.decide(self.db, "o/r", 5, self.ids(), True, 2.0)
        RA.process(CFG_ON, FakeGH(), self.db, lambda *a: None)
        self.assertEqual(self.db.execute("SELECT status FROM review_actions").fetchone(), ("queued",))
        self.assertEqual(dbm.approvals(self.db), [("o/r", 5, "run")])

    def test_rejected_is_not_proposed_again_and_new_review_supersedes(self):
        self.store([("label", "review:blocking", ""), ("label", "review:needs-changes", "")])
        RA.decide(self.db, "o/r", 5, self.ids()[:1], False, 2.0)
        self.store([("label", "review:blocking", ""), ("label", "review:needs-changes", "")])
        shown = RA.pending(self.db, "o/r", 5)
        self.assertEqual([r["value"] for r in shown], ["review:needs-changes"])


if __name__ == "__main__":
    unittest.main()
