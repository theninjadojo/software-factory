import json
import os
import tempfile
import unittest

from factory import db as dbm, split, telegram, tracker
from factory.config import Project, ProjectRepo
from factory.pm import is_open
from factory.roles import builtin_prompt
from factory.runner import build_prompt

L = tracker.LOCAL_BASE
REPO, OTHER = "o/api", "o/app"
ALLOWED = {REPO, OTHER}
PR = Project("proj", (ProjectRepo("o/api", "api"),))


def item(title="Do a part", repo="api", after=(), body="Build it."):
    return dict(title=title, body=body, repo=repo, after=list(after))


def block(items, reason="Too big", doc="# Requirements\n"):
    return doc + "\n```factory-split\n" + json.dumps({"reason": reason, "items": items}) + "\n```\n\n```factory-questions\n{\"questions\": []}\n```\n"


class Parse(unittest.TestCase):
    def test_valid(self):
        p = split.parse(block([item(), item("Second", "app", [0])]), ALLOWED)
        self.assertEqual([(i.title, i.repo, i.after) for i in p.items], [("Do a part", REPO, ()), ("Second", OTHER, (0,))])
        self.assertEqual(p.reason, "Too big")

    def test_invalid_blocks_make_no_proposal(self):
        bad = [[item()], [item()] * 11, [item(), item(repo="evil/x")], [item(after=[0]), item()], [item(), item(after=[1])],
               [item(), item(after=[True])], [item(), item(after=["0"])], [item(), item(title="  ")], [item(), item(body="")],
               [item(), "close everything"], [item(), item(repo=None)]]
        for items in bad:
            self.assertIsNone(split.parse(block(items), ALLOWED), items)
        self.assertEqual(len(split.parse(block([item()] * 10), ALLOWED).items), 10)

    def test_no_block_or_malformed(self):
        for text in ("nothing", "```factory-split\n{oops\n```", "```factory-split\n[1]\n```", '```factory-split\n{"items": 3}\n```'):
            self.assertIsNone(split.parse(text, ALLOWED))

    def test_titles_are_one_clean_line_and_text_is_sanitized(self):
        p = split.parse(block([item("a\n\nb " + "x" * 300, body="hi @admin ![x](http://e/x.png)"), item()]), ALLOWED)
        self.assertNotIn("\n", p.items[0].title)
        self.assertLessEqual(len(p.items[0].title), split.MAX_TITLE + 20)
        self.assertNotIn("@admin", p.items[0].body)
        self.assertNotIn("![x]", p.items[0].body)

    def test_block_is_removed_from_the_document(self):
        text, p = split.extract(block([item(), item()]), ALLOWED)
        self.assertNotIn("factory-split", text)
        self.assertIn("factory-questions", text)
        self.assertIsNotNone(p)
        text, p = split.extract("```factory-split\n{oops\n```\nrest", ALLOWED)
        self.assertNotIn("factory-split", text)
        self.assertIsNone(p)

    def test_directory_name_must_name_one_repository(self):
        self.assertIsNone(split.parse(block([item(), item(repo="api")]), {"a/api", "b/api"}))


class Prompts(unittest.TestCase):
    def test_only_the_analyst_may_split(self):
        self.assertIn("factory-split", build_prompt("t", "b", PR, "o/api", "analyst"))
        self.assertIn("factory-split", builtin_prompt("analyst"))
        for role in ("designer", "architect"):
            self.assertNotIn("factory-split", build_prompt("t", "b", PR, "o/api", role))

    def test_chat_buttons_are_matched_strictly(self):
        self.assertEqual(telegram.parse_callback("split:12|o/api|3"), ("split:12", "o/api", 3))
        self.assertEqual(telegram.parse_callback("split-no:12|o/api|3"), ("split-no:12", "o/api", 3))
        for bad in ("split:x|o/api|3", "split:|o/api|3", "splitx:1|o/api|3", "split:1234567890|o/api|3"):
            self.assertIsNone(telegram.parse_callback(bad))


class Hub(tracker.Hub):
    def create_label(self, *a, **k):
        pass


class Flow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "f.db")
        self.conn = dbm.connect(self.path)
        self.gh = Hub(None, self.path)
        self.lt = tracker.LocalTracker(self.conn)
        self.parent = self.lt.create(REPO, "Big", "Do everything")
        self.events = []
        self.cfg = type("C", (), {"github_issues_enabled": False})()

    def tearDown(self):
        self.dir.cleanup()

    def propose(self, items=None):
        p = split.parse(block(items or [item("One"), item("Two", "app", [0]), item("Three", "api", [0, 1])]), ALLOWED)
        return split.store(self.conn, REPO, self.gh.get_issue(REPO, self.parent), p)

    def emit(self, *a, **k):
        self.events.append(a)

    def run_process(self, starts=("factory:analyze",)):
        split.process(self.cfg, self.gh, self.conn, self.emit, starts, ["factory:build"])

    def test_nothing_is_created_without_approval(self):
        pid = self.propose()
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "proposed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 1)

    def test_decide_checks_ticket_status_and_start_action(self):
        pid = self.propose()
        self.assertFalse(split.decide(self.conn, pid, REPO, self.parent + 1, True))
        self.assertFalse(split.decide(self.conn, pid, OTHER, self.parent, True))
        self.assertFalse(split.decide(self.conn, pid, REPO, self.parent, True, "factory:evil", ("factory:analyze",)))
        self.assertTrue(split.decide(self.conn, pid, REPO, self.parent, True, "factory:analyze", ("factory:analyze",)))
        self.assertFalse(split.decide(self.conn, pid, REPO, self.parent, False))      # already decided

    def test_dismissed_creates_nothing(self):
        pid = self.propose()
        self.assertTrue(split.decide(self.conn, pid, REPO, self.parent, False))
        self.run_process()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 1)

    def approve(self, pid, start=""):
        self.assertTrue(split.decide(self.conn, pid, REPO, self.parent, True, start, ("factory:analyze",)))

    def test_children_are_created_linked_and_the_parent_marked(self):
        pid = self.propose()
        self.approve(pid)
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "done")
        kids = split.items(self.conn, pid)
        self.assertEqual([k["status"] for k in kids], ["created"] * 3)
        two = self.gh.get_issue(OTHER, kids[1]["child_issue"])
        self.assertIn("Part of", two["body"])
        self.assertIn("Starts after", two["body"])
        self.assertEqual(two["labels"], [])                                  # no start action chosen: nothing starts
        parent = self.gh.get_issue(REPO, self.parent)
        self.assertIn(split.UMBRELLA_LABEL, [lb["name"] for lb in parent["labels"]])
        self.assertEqual(split.parent_of(self.conn, OTHER, kids[1]["child_issue"])[0]["issue"], self.parent)

    def test_start_action_is_applied_to_every_child(self):
        pid = self.propose()
        self.approve(pid, "factory:analyze")
        self.run_process()
        for k in split.items(self.conn, pid):
            labels = [lb["name"] for lb in self.gh.get_issue(k["child_repo"], k["child_issue"])["labels"]]
            self.assertEqual(labels, ["factory:analyze"])

    def test_a_retry_does_not_duplicate_children(self):
        pid = self.propose()
        self.approve(pid)
        calls = []

        class Boom(tracker.LocalTracker):
            def create(s, *a, **k):
                calls.append(1)
                if len(calls) == 2:
                    raise RuntimeError("crash")
                return super().create(*a, **k)
        orig = tracker.LocalTracker
        tracker.LocalTracker = Boom
        try:
            self.run_process()
        finally:
            tracker.LocalTracker = orig
        self.assertEqual(split.get(self.conn, pid)["status"], "approved")    # will be retried
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 4)

    def test_edited_or_closed_parent_is_stale(self):
        pid = self.propose()
        self.approve(pid)
        self.lt.edit(REPO, self.parent, "Big", "Something else now")
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "stale")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 1)

    def test_a_later_child_waits_for_the_items_it_comes_after(self):
        pid = self.propose()
        self.approve(pid)
        self.run_process()
        k = split.items(self.conn, pid)
        one, two, three = (x["child_issue"] for x in k)
        self.assertEqual(split.blockers(self.conn, self.gh, REPO, one, is_open, {}), [])
        self.assertEqual(len(split.blockers(self.conn, self.gh, OTHER, two, is_open, {})), 1)
        self.assertEqual(len(split.blockers(self.conn, self.gh, REPO, three, is_open, {})), 2)
        self.lt.set_state(REPO, one, "closed")
        self.assertEqual(len(split.blockers(self.conn, self.gh, REPO, three, is_open, {})), 1)
        self.assertEqual(split.blockers(self.conn, self.gh, REPO, 5, is_open, {}), [])

    def test_umbrella_closes_once_when_every_child_is_closed(self):
        pid = self.propose()
        self.approve(pid)
        self.run_process()
        kids = split.items(self.conn, pid)
        for k in kids[:-1]:
            self.lt.set_state(k["child_repo"], k["child_issue"], "closed")
        split.sweep(self.gh, self.conn, self.emit)
        self.assertEqual(self.gh.get_issue(REPO, self.parent)["state"], "open")
        self.lt.set_state(kids[-1]["child_repo"], kids[-1]["child_issue"], "closed")
        split.sweep(self.gh, self.conn, self.emit)
        self.assertEqual(self.gh.get_issue(REPO, self.parent)["state"], "closed")
        self.lt.set_state(REPO, self.parent, "open")                         # a person reopens it: left alone
        split.sweep(self.gh, self.conn, self.emit)
        self.assertEqual(self.gh.get_issue(REPO, self.parent)["state"], "open")


if __name__ == "__main__":
    unittest.main()
