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


def block(items, reason="Too big", doc="# Requirements\n", size=None):
    d = {"reason": reason, "items": items, **({"size": size} if size is not None else {})}
    return doc + "\n```factory-split\n" + json.dumps(d) + "\n```\n\n```factory-questions\n{\"questions\": []}\n```\n"


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


class ArchitectSize(unittest.TestCase):
    def test_a_large_plan_keeps_its_proposal(self):
        text = block([item(), item("Second", "app", [0])], doc="# Plan\n", size={"areas": 4, "items": 3})
        out, p, got = split.sized(text, ALLOWED, 3, 8)
        self.assertEqual(got, (4, 3))
        self.assertEqual(len(p.items), 2)
        self.assertNotIn("factory-split", out)
        self.assertIsNotNone(split.sized(block([item(), item()], size={"areas": 1, "items": 8}), ALLOWED, 3, 8)[1])   # either count

    def test_a_small_plan_or_a_missing_or_bad_size_makes_no_proposal(self):
        for size in ({"areas": 2, "items": 7}, None, {"areas": "9", "items": 9}, {"areas": True, "items": 9}, {"areas": 5},
                     {"areas": -1, "items": 99}, {"areas": 5000, "items": 1}, [4, 9]):
            out, p, _ = split.sized(block([item(), item()], size=size), ALLOWED, 3, 8)
            self.assertIsNone(p, size)
            self.assertNotIn("factory-split", out)                           # the block never reaches the ticket either way

    def test_the_comment_says_how_large_the_plan_is(self):
        p = split.parse(block([item(), item()]), ALLOWED)
        self.assertIn("4 areas and has 11 plan items", split.section(p, (4, 11)))
        self.assertNotIn("plan items", split.section(p))


class Prompts(unittest.TestCase):
    def test_only_the_analyst_may_split_unless_the_architect_is_told_its_threshold(self):
        self.assertIn("factory-split", build_prompt("t", "b", PR, "o/api", "analyst"))
        self.assertIn("factory-split", builtin_prompt("analyst"))
        for role in ("designer", "architect"):
            self.assertNotIn("factory-split", build_prompt("t", "b", PR, "o/api", role))
        self.assertNotIn("factory-split", build_prompt("t", "b", PR, "o/api", "designer", split_at=(3, 8)))
        text = build_prompt("t", "b", PR, "o/api", "architect", split_at=(4, 12))
        self.assertIn("factory-split", text)
        self.assertIn("at least 4 areas or has at least 12 plan items", text)
        self.assertIn('"size": {"areas"', text)
        self.assertIn("Acceptance checks:", text)

    def test_chat_buttons_are_matched_strictly(self):
        self.assertEqual(telegram.parse_callback("split:12|o/api|3"), ("split:12", "o/api", 3))
        self.assertEqual(telegram.parse_callback("split-no:12|o/api|3"), ("split-no:12", "o/api", 3))
        for bad in ("split:x|o/api|3", "split:|o/api|3", "splitx:1|o/api|3", "split:1234567890|o/api|3"):
            self.assertIsNone(telegram.parse_callback(bad))
        self.assertEqual(telegram.parse_callback("split-one:12|o/api|3"), ("split-one:12", "o/api", 3))


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
        self.assertIn("Starts after", two["body"])                           # another repository: named, not a blocker line
        three = self.gh.get_issue(REPO, kids[2]["child_issue"])
        self.assertIn(f"Blocked by {tracker.display(kids[0]['child_issue'])}\n", three["body"])     # same repository: the PM reads it
        from factory.pm import explicit_blockers
        self.assertEqual(explicit_blockers(three["body"]), [kids[0]["child_issue"]])
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

    def test_merged_parts_become_one_ticket_after_everything_they_came_after(self):
        pid = self.propose([item("One"), item("Two", "api", [0]), item("Three", "api", [1]), item("Four", "api", [0])])
        self.assertEqual(split.merge(self.conn, pid, REPO, self.parent, [2, 3]), "")
        self.assertTrue(split.decide(self.conn, pid, REPO, self.parent, True, titles={2: "  Three and\nfour  "}))
        rows = split.items(self.conn, pid)
        kept = split.kept(rows)
        self.assertEqual([(k["title"], k["after"]) for k in kept], [("One", ""), ("Two", "0"), ("Three and four", "0,1")])
        self.assertIn("### Three", kept[2]["body"])
        self.assertIn("### Four", kept[2]["body"])
        self.assertEqual(sorted(r["title"] for r in rows if r["merged_into"] == 2), ["Four", "Three"])
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 4)

    def test_a_merge_that_would_wait_on_itself_or_leaves_one_ticket_is_refused(self):
        pid = self.propose([item("One"), item("Two", "api", [0]), item("Three", "api", [1]), item("Four", "app", [0])])
        self.assertIn("Part 2 has to happen between them", split.merge(self.conn, pid, REPO, self.parent, [0, 2]))
        self.assertIn("same repository", split.merge(self.conn, pid, REPO, self.parent, [2, 3]))
        self.assertEqual(split.merge(self.conn, pid, REPO, self.parent, [0, 1]), "")
        self.assertEqual(split.merge(self.conn, pid, REPO, self.parent, [0, 2]), "")            # 0 brings its merge along
        self.assertEqual(split.merges(split.get(self.conn, pid)), [[0, 1, 2]])
        self.assertTrue(split.unmerge(self.conn, pid, REPO, self.parent, 0))
        self.assertEqual(split.merges(split.get(self.conn, pid)), [])
        two = self.propose([item("One"), item("Two", "api", [0])])
        self.assertIn("every part", split.merge(self.conn, two, REPO, self.parent, [0, 1]))

    def test_keeping_it_whole_starts_the_ticket_and_is_not_proposed_again(self):
        pid = self.propose()
        self.gh.add_labels(REPO, self.parent, [split.PROPOSED_LABEL])
        self.assertFalse(split.decide(self.conn, pid, REPO, self.parent, False, "factory:evil", ("factory:analyze",), keep=True))
        self.assertTrue(split.decide(self.conn, pid, REPO, self.parent, False, "factory:analyze", ("factory:analyze",), keep=True))
        self.run_process()
        self.assertEqual(split.get(self.conn, pid)["status"], "kept")
        parent = self.gh.get_issue(REPO, self.parent)
        self.assertEqual([lb["name"] for lb in parent["labels"]], ["factory:analyze"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 1)
        self.assertTrue(split.declined(self.conn, REPO, parent))
        self.lt.edit(REPO, self.parent, "Big", "Something else now")
        self.assertFalse(split.declined(self.conn, REPO, self.gh.get_issue(REPO, self.parent)))

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


class Architect(unittest.TestCase):
    """The architect's run: a large plan's split is stored for a person, a small one or a switched-off one is not."""
    def run_stage(self, output, **split_cfg):
        from dataclasses import replace
        from unittest import mock
        from factory import main as m
        from factory.config import DEFAULT_ROLES, SplitCfg
        from factory.runner import RunResult
        from test_roles import CFG, FakeClf, FakeGH, issue
        conn = dbm.connect(":memory:")
        gh = FakeGH()
        gh.create_label = lambda *a, **k: None
        role = next(r for r in DEFAULT_ROLES if r.name == "architect")
        cfg = replace(CFG, split=SplitCfg(**split_cfg))
        with mock.patch.object(m.runner, "run_task", return_value=RunResult("stage", "ok", output=output)), tempfile.TemporaryDirectory() as d:
            m.dispatch_stage(replace(cfg, db_path=d + "/f.db"), gh, FakeClf(), "o/r", issue(), role, conn=conn)
        posted = "\n".join(c[1] for c in gh.calls if c[0] == "comment")
        return split.for_ticket(conn, "o/r", 5), posted, gh

    def plan(self, size):
        return block([item("Phase one", "r"), item("Phase two", "r", [0])], "Four areas", "Summary: big.\n# Plan\n", size)

    def test_a_large_plan_is_proposed_and_waits_for_a_person(self):
        p, posted, gh = self.run_stage(self.plan({"areas": 4, "items": 12}))
        self.assertEqual((p["status"], p["source"]), ("proposed", "architect"))
        self.assertIn("Proposed split", posted)
        self.assertIn("4 areas and has 12 plan items", posted)
        self.assertNotIn("factory-split", posted)
        self.assertIn(("add", (split.PROPOSED_LABEL,)), gh.calls)

    def test_a_small_plan_or_the_switch_off_proposes_nothing(self):
        for size, kw in (({"areas": 2, "items": 5}, {}), ({"areas": 9, "items": 30}, {"architect": False}),
                         ({"areas": 4, "items": 5}, {"architect_min_areas": 5, "architect_min_items": 6})):
            p, posted, _ = self.run_stage(self.plan(size), **kw)
            self.assertIsNone(p, (size, kw))
            self.assertNotIn("factory-split", posted)
            self.assertNotIn("Proposed split", posted)


class Card(unittest.TestCase):
    def test_the_ticket_page_says_the_architect_proposed_it_and_the_order(self):
        from factory.ui import splitcard
        from test_roles import CFG
        conn = dbm.connect(":memory:")
        lt = tracker.LocalTracker(conn)
        n = lt.create(REPO, "Big", "Do everything")
        p = split.parse(block([item("One"), item("Two", "app", [0]), item("Three", "api", [0, 1])]), ALLOWED)
        split.store(conn, REPO, {"number": n, "title": "Big", "body": "Do everything"}, p, source="architect")
        html = splitcard.card(CFG, conn, REPO, n, "tok", "/")
        self.assertIn("Proposed by the architect", html)
        self.assertIn("o/app, after 1<", html)
        self.assertIn("o/api, after 1, 2<", html)
        self.assertIn("blocked by the ones it comes after", html)
        folded = splitcard.fold(CFG, conn, REPO, n, "tok", "/")
        self.assertIn('data-fold="linked" open', folded)                     # waiting for a person: open on the ticket page
        self.assertIn("waiting for your decision", folded)
        split.decide(conn, split.for_ticket(conn, REPO, n)["id"], REPO, n, False)
        self.assertEqual(splitcard.fold(CFG, conn, REPO, n, "tok", "/"), "")  # dismissed: nothing linked


class Config(unittest.TestCase):
    def load(self, extra):
        from factory.config import load
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(open("config.example.toml").read() + extra)
        self.addCleanup(os.unlink, f.name)
        return load(f.name)

    def test_the_architect_split_settings_are_checked(self):
        cfg = self.load("")
        self.assertEqual((cfg.split.architect, cfg.split.architect_min_areas, cfg.split.architect_min_items), (True, 3, 8))
        self.assertEqual(self.load("\n[split]\narchitect_min_areas = 5\n").split.architect_min_areas, 5)
        for bad in ("architect_min_areas = 1", "architect_min_areas = 21", "architect_min_items = 51", 'architect = "yes"',
                    "architect_min_items = true"):
            with self.assertRaises(ValueError, msg=bad):
                self.load(f"\n[split]\n{bad}\n")


if __name__ == "__main__":
    unittest.main()


class Page(unittest.TestCase):
    """The split card's form, sent through the UI server as a browser sends it (POST /tickets/split)."""
    def setUp(self):
        from test_ui_admin import AdminCase
        self.case = type("C", (AdminCase,), {"runTest": lambda self: None})()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        p = self.case.root / "config.toml"
        p.write_text(p.read_text() + "\n[local]\nenabled = true\n")
        self.repo = "your-org/standalone-service"
        self.lt = tracker.LocalTracker(self.case.db)
        self.cookie, self.csrf = self.case.session()

    def propose(self):
        n = self.lt.create(self.repo, "Big", "Do everything")
        p = split.parse(block([item("One", "standalone-service"), item("Two", "standalone-service", [0]),
                               item("Three", "standalone-service", [1])]), {self.repo})
        return n, split.store(self.case.db, self.repo, Hub(None, self.case.db_path).get_issue(self.repo, n), p, source="architect")

    def post(self, n, pid, decision, drop=(), extra=()):
        fields = [("repo", self.repo), ("n", str(n)), ("id", str(pid)), ("back", f"/ticket?repo={self.repo}&n={n}"),
                  ("start", ""), ("decision", decision)] + [("drop", str(d)) for d in drop] + list(extra)
        return self.case.post(self.cookie, self.csrf, "/tickets/split", fields)

    def flash(self, n):
        return self.case.req("GET", f"/ticket?repo={self.repo.replace('/', '%2F')}&n={n}", cookie=self.cookie)[2]

    def test_approving_with_an_item_dropped_queues_the_split(self):
        n, pid = self.propose()
        self.assertIn('name="drop" value="1"', self.flash(n))                      # the card offers to drop each item
        s, _, _ = self.post(n, pid, "approve", drop=[1])
        self.assertEqual(s, 303)
        self.assertIn("Splitting the ticket.", self.flash(n))
        self.assertEqual(split.get(self.case.db, pid)["status"], "approved")
        rows = {r["pos"]: r for r in split.items(self.case.db, pid)}
        self.assertEqual([rows[p]["dropped"] for p in (0, 1, 2)], [0, 1, 0])
        self.assertEqual(rows[2]["after"], "0")                                     # Three came after Two, which is gone: now after One
        self.post(n, pid, "approve")                                                # decided already: nothing changes
        self.assertIn("Nothing changed: that proposal was already decided", self.flash(n))

    def test_dismissing_creates_nothing(self):
        n, pid = self.propose()
        self.assertEqual(self.post(n, pid, "dismiss")[0], 303)
        self.assertIn("Dismissed. Nothing was created.", self.flash(n))
        self.assertEqual(split.get(self.case.db, pid)["status"], "dismissed")
        self.assertEqual([r["dropped"] for r in split.items(self.case.db, pid)], [0, 0, 0])

    def test_two_tickets_must_remain(self):
        n, pid = self.propose()
        self.post(n, pid, "approve", drop=[0, 2])
        self.assertIn("At least two tickets must remain.", self.flash(n))
        self.assertEqual(split.get(self.case.db, pid)["status"], "proposed")

    def test_merging_then_approving_with_a_title(self):
        n, pid = self.propose()
        self.post(n, pid, "merge", extra=[("pick", "1"), ("pick", "2")])
        page = self.flash(n)
        self.assertIn("Merged into one ticket.", page)
        self.assertIn('name="title-1"', page)
        self.assertIn("Create 2 tickets", page)
        self.post(n, pid, "approve", extra=[("title-1", "Two and three, together")])
        self.assertEqual(split.get(self.case.db, pid)["status"], "approved")
        self.assertEqual([k["title"] for k in split.kept(split.items(self.case.db, pid))], ["One", "Two and three, together"])

    def test_split_it_again_and_refused_merges(self):
        n, pid = self.propose()
        self.post(n, pid, "merge", extra=[("pick", "0")])
        self.assertIn("Tick two or more parts", self.flash(n))
        self.post(n, pid, "merge", extra=[("pick", "0"), ("pick", "1")])
        self.case.post(self.cookie, self.csrf, "/tickets/split", [
            ("repo", self.repo), ("n", str(n)), ("id", str(pid)), ("back", f"/ticket?repo={self.repo}&n={n}"), ("unmerge", "0")])
        self.assertIn("Split again into its parts.", self.flash(n))
        self.assertEqual(split.merges(split.get(self.case.db, pid)), [])

    def test_working_on_it_as_one_ticket(self):
        n, pid = self.propose()
        self.post(n, pid, "keep")
        page = self.flash(n)
        self.assertIn("Working on it as one ticket.", page)
        self.assertEqual(split.get(self.case.db, pid)["status"], "keeping")
        self.assertIn("Kept as one ticket", page)
