import html
import json
import unittest
import unittest.mock
from dataclasses import replace

from factory import db as dbm
from factory import main as m
from factory import plan, pm
from factory.config import Project, ProjectRepo
from factory.ui import roadmap
from test_pm import GH, PM, fake_runner, tk
from test_roles import CFG

PAY = "Parents can pay all their invoices in one checkout"
WEEK = "Coaches can see their whole week's classes in one place"
ENROL = "Guardians can enrol a child without the account owner"
SHOP = replace(PM, repos=["o/a", "o/b", "o/solo"], projects=(Project("Shop", (ProjectRepo("o/a"), ProjectRepo("o/b"))),))


def block(milestones, tickets):
    return "# Plan\n```factory-priorities\n" + json.dumps({"milestones": milestones, "tickets": tickets}) + "\n```\n"


class Names(unittest.TestCase):
    def test_plain_capabilities_pass_and_engineer_words_are_dropped(self):
        for good in (PAY, WEEK, ENROL, "Managers can export the month's attendance"):
            self.assertEqual(pm.milestone_name(good), good)
        for bad in ("Billing", "Phase 2", "Sprint 3: parents pay", "Milestone 1 for coaches", "Implement multi-invoice API",
                    "Fix #12 for coaches", "Parents see L-55 in checkout", "Move factory/pm.py to the board", "Release v1.2 to parents",
                    "Parents can use `pay()` in one go", "12", "Tighten RLS on invoices for parents", "No milestone"):
            self.assertEqual(pm.milestone_name(bad), "", bad)
        self.assertEqual(pm.milestone_name("Parents   can pay\nall invoices"), "Parents can pay all invoices")
        self.assertEqual(len(pm.milestone_name("Parents can " + "really " * 30 + "pay")), 0)    # too many words for one line

    def test_the_line_fits_in_the_longer_limit(self):
        self.assertEqual(plan.MAX_NAME, 120)
        self.assertEqual(len(plan.clean_name("x" * 300)), 120)

    def test_parse_drops_bad_names_and_their_tickets(self):
        got = pm.parse_milestones(block([PAY, "Phase 2", WEEK, PAY], [
            {"issue": 5, "priority": "high", "milestone": PAY}, {"issue": 6, "priority": "low", "milestone": "Phase 2"},
            {"issue": 7, "priority": "low", "milestone": ""}, {"issue": 99, "priority": "low", "milestone": PAY}]), {5, 6, 7})
        self.assertEqual(got, ([PAY, WEEK], {("", 5): PAY, ("", 7): ""}))
        self.assertIsNone(pm.parse_milestones(block(None, []).replace('"milestones": null, ', ""), {5}))

    def test_several_repositories_need_the_repo_on_each_entry(self):
        keys = {("o/a", 5), ("o/b", 5)}
        got = pm.parse_milestones(block([PAY], [{"issue": 5, "repo": "o/b", "priority": "high", "milestone": PAY},
                                                {"issue": 5, "priority": "high", "milestone": PAY}]), keys)
        self.assertEqual(got[1], {("o/b", 5): PAY})
        found = pm.parse(block([], [{"issue": 5, "repo": "o/a", "priority": "high"}, {"issue": 5, "priority": "low"}]), keys)
        self.assertEqual([(a.repo, a.priority) for a in found], [("o/a", "high")])


class Apply(unittest.TestCase):
    def setUp(self):
        self.c = dbm.connect(":memory:")
        plan.ensure_tables(self.c)

    def go(self, proposed, assign, backlog=((("o/r"), 5), ("o/r", 6), ("o/r", 7), ("o/r", 8))):
        return pm.apply_milestones(self.c, "o/r", set(backlog), proposed, {("o/r", n): v for n, v in assign.items()})

    def placed(self):
        return {n: v for (_, n), v in plan.ticket_milestones(self.c, "o/r").items()}

    def test_it_makes_milestones_with_two_tickets_or_the_first_one(self):
        changes = self.go([PAY, WEEK, ENROL], {5: PAY, 6: WEEK, 7: WEEK, 8: ENROL})
        self.assertEqual(plan.milestones(self.c, "o/r"), [PAY, WEEK])          # ENROL has one ticket and is not first
        self.assertEqual(self.placed(), {5: PAY, 6: WEEK, 7: WEEK})
        self.assertEqual(plan.milestone_owners(self.c, "o/r"), {PAY: "pm", WEEK: "pm"})
        self.assertIn(f"new milestone {PAY}", changes)

    def test_a_persons_milestones_and_tickets_are_never_touched(self):
        plan.add_milestone(self.c, "o/r", ENROL)                                  # made by a person (no mark)
        plan.set_ticket_milestone(self.c, "o/r", "o/r", 5, ENROL)
        plan.mark_ticket(self.c, "o/r", 5, "person")
        plan.mark_milestone(self.c, "o/r", PAY, "person")                       # a person deleted PAY once
        self.go([PAY, WEEK], {5: WEEK, 6: WEEK, 7: WEEK, 8: PAY})
        self.assertEqual(plan.milestones(self.c, "o/r"), [ENROL, WEEK])
        self.assertEqual(self.placed(), {5: ENROL, 6: WEEK, 7: WEEK})
        self.go([WEEK, ENROL], {6: ENROL, 7: ENROL})                            # the PM may fill a person's milestone
        self.assertEqual(plan.milestones(self.c, "o/r")[0], ENROL)              # but never moves it

    def test_a_settled_milestone_does_not_churn(self):
        self.go([PAY, WEEK], {5: PAY, 6: PAY, 7: WEEK, 8: WEEK})
        changes = self.go([WEEK, PAY, ENROL], {5: ENROL, 6: ENROL, 7: WEEK, 8: WEEK})   # a new idea for the same tickets
        self.assertEqual(changes, [])
        self.assertEqual(plan.milestones(self.c, "o/r"), [PAY, WEEK])
        self.assertEqual(self.placed(), {5: PAY, 6: PAY, 7: WEEK, 8: WEEK})

    def test_a_milestone_whose_tickets_closed_may_change(self):
        self.go([PAY, WEEK], {5: PAY, 6: PAY, 7: WEEK, 8: WEEK})
        # 5 and 6 are done now: PAY is no longer settled, so its order may change; WEEK still is
        changes = self.go([WEEK, PAY], {7: WEEK, 8: WEEK}, backlog=(("o/r", 7), ("o/r", 8)))
        self.assertEqual(plan.milestones(self.c, "o/r"), [PAY, WEEK])           # WEEK keeps its place: only PAY's slot moves
        self.assertEqual(changes, [])
        changes = self.go([ENROL], {7: ENROL, 8: ENROL}, backlog=(("o/r", 7), ("o/r", 8), ("o/r", 9)))
        self.assertEqual(self.placed()[7], WEEK)                                # settled: its tickets stay
        self.assertNotIn(ENROL, plan.milestones(self.c, "o/r"))

    def test_an_empty_unsettled_milestone_it_dropped_is_removed(self):
        self.go([PAY], {5: PAY, 6: PAY})
        plan.set_ticket_milestone(self.c, "o/r", "o/r", 5, "")
        plan.set_ticket_milestone(self.c, "o/r", "o/r", 6, "")
        changes = self.go([WEEK], {7: WEEK, 8: WEEK})
        self.assertEqual(plan.milestones(self.c, "o/r"), [WEEK])
        self.assertIn(f"removed empty milestone {PAY}", changes)


class Scopes(unittest.TestCase):
    def test_a_projects_repositories_share_one_scope(self):
        self.assertEqual([plan.scope_of(SHOP, r) for r in SHOP.repos], ["Shop", "Shop", "o/solo"])
        self.assertEqual(plan.scope_repos(SHOP, "Shop"), ["o/a", "o/b"])
        self.assertEqual(plan.scopes(SHOP), [("Shop", "Shop"), ("o/solo", "o/solo")])

    def test_per_repository_milestones_move_into_the_project(self):
        c = dbm.connect(":memory:")
        plan.ensure_tables(c)
        for r, names in (("o/a", [PAY, WEEK]), ("o/b", [ENROL, PAY]), ("o/solo", ["Solo users can sign in"])):
            for n in names:
                plan.add_milestone(c, r, n)
        plan.set_ticket_milestone(c, "o/a", "o/a", 1, WEEK)
        plan.set_ticket_milestone(c, "o/b", "o/b", 1, PAY)
        plan.set_ticket_milestone(c, "o/b", "o/b", 2, ENROL)
        plan.mark_milestone(c, "o/a", PAY, "pm")
        plan.mark_milestone(c, "o/b", PAY, "pm")
        plan.mark_milestone(c, "o/b", ENROL, "pm")
        plan.mark_milestone(c, "o/a", WEEK, "person")
        moved = plan.migrate(c, lambda r: plan.scope_of(SHOP, r))
        self.assertTrue(moved)
        self.assertEqual(plan.milestones(c, "Shop"), [PAY, WEEK, ENROL])          # o/a's order, then what only o/b had
        self.assertEqual(plan.milestones(c, "o/a") + plan.milestones(c, "o/b"), [])
        self.assertEqual(plan.milestones(c, "o/solo"), ["Solo users can sign in"])
        self.assertEqual(plan.ticket_milestones(c, "Shop"), {("o/a", 1): WEEK, ("o/b", 1): PAY, ("o/b", 2): ENROL})
        self.assertEqual(plan.milestone_owners(c, "Shop"), {PAY: "pm", WEEK: "person", ENROL: "pm"})
        self.assertEqual(plan.migrate(c, lambda r: plan.scope_of(SHOP, r)), 0)    # safe to run again

    def test_merging_with_a_persons_milestone_keeps_it_theirs(self):
        c = dbm.connect(":memory:")
        plan.add_milestone(c, "o/a", PAY)                                        # no mark: a person's
        plan.add_milestone(c, "o/b", PAY)
        plan.mark_milestone(c, "o/b", PAY, "pm")
        plan.migrate(c, lambda r: plan.scope_of(SHOP, r))
        self.assertEqual(plan.milestone_owners(c, "Shop"), {PAY: "person"})

    def test_the_roadmap_shows_the_projects_tickets_with_their_repository(self):
        c = dbm.connect(":memory:")
        plan.add_milestone(c, "Shop", PAY)
        plan.mark_milestone(c, "Shop", PAY, "pm")
        plan.set_ticket_milestone(c, "Shop", "o/b", 5, PAY)
        rows = [{"repo": r, "issue": 5, "title": f"T {r}", "state": "new", "closed": False, "priority": "normal", "prio_src": ""}
                for r in ("o/a", "o/b", "o/solo")]
        mod = roadmap.model(c, rows, "Shop", ["o/a", "o/b"])
        self.assertEqual(sorted(t["key"] for t in mod["tickets"]), [("o/a", 5), ("o/b", 5)])
        out = roadmap.page(SHOP, mod, ("o/b", 5), "tok")                        # a traced ticket: the step chart
        self.assertIn("b#5", out)
        self.assertIn("by the project manager", out)
        out = roadmap.page(SHOP, mod, None, "tok")
        self.assertIn('<option value="Shop" selected>', out)
        self.assertIn(f'maxlength="{plan.MAX_NAME}"', out)
        self.assertNotIn("style=", out)


def trow(repo, n, title, state="new"):
    return {"repo": repo, "issue": n, "title": title, "state": state, "closed": False, "priority": "normal", "prio_src": ""}


class Views(unittest.TestCase):
    def setUp(self):
        c = self.c = dbm.connect(":memory:")
        for name in (PAY, WEEK):
            plan.add_milestone(c, "Shop", name)
        for r, n, name in (("o/a", 1, PAY), ("o/b", 2, PAY), ("o/a", 3, PAY), ("o/b", 4, WEEK)):
            plan.set_ticket_milestone(c, "Shop", r, n, name)
        plan.set_feature(c, "o/a", 1, "short", "Pay several invoices at once", "pm")
        plan.set_feature(c, "o/a", 1, "category", "Payments", "pm")
        plan.set_feature(c, "o/b", 2, "category", "Payments", "pm")
        dbm.set_pm_assessment(c, "o/b", 2, "normal", "", False, [], "", "", 1)
        dbm.set_pm_assessment(c, "o/a", 3, "normal", "", False, [1], "", "", 1)
        rows = [trow("o/a", 1, "Pay several invoices in one Stripe session " + "x" * 80, "working"), trow("o/b", 2, "Invoice <b>list</b>", "needs"),
                trow("o/a", 3, "Receipt email", "done"), trow("o/b", 4, "Weekly calendar"), trow("o/a", 9, "Loose end")]
        self.m = roadmap.model(c, rows, "Shop", ["o/a", "o/b"])

    def test_the_overview_lists_milestones_in_order_with_progress(self):
        out = roadmap.page(SHOP, self.m, None, "tok")
        self.assertLess(out.index(PAY), out.index(html.escape(WEEK)))
        self.assertIn("1 of 3 done", out)
        self.assertIn("1 needs you", out)
        self.assertIn("1 being built", out)
        self.assertIn("Shop · 2 milestones, worked on top to bottom · 4 open features", out)
        self.assertIn("Not planned yet", out)
        self.assertIn("Loose end", out)
        self.assertIn("view=chart", out)
        self.assertIn("rm-w35", out)                                             # 1 of 3, as a width class: no style attributes
        self.assertNotIn("style=", out)

    def test_a_milestone_opens_to_its_features_by_category(self):
        out = roadmap.page(SHOP, self.m, None, "tok", {"milestone": PAY})
        self.assertIn("Milestone 1 of 2", out)
        self.assertLess(out.index("<h2>Payments"), out.index("<h2>Other"))
        self.assertIn('<span class="rp-short">Pay several invoices at once</span>', out)
        self.assertIn("Pay several invoices in one Stripe session " + "x" * 80, out)   # the full title, never cut
        self.assertIn("Invoice &lt;b&gt;list&lt;/b&gt;", out)                          # no short name: the title, escaped
        self.assertIn("Open ticket", out)
        self.assertIn('action="/roadmap/feature"', out)
        self.assertIn('action="/tickets/milestone"', out)
        self.assertNotIn("style=", out)

    def test_the_categories_view_shows_a_tile_per_category(self):
        out = roadmap.page(SHOP, self.m, None, "tok", {"view": "categories"})
        self.assertIn('<span class="rp-cat">Payments</span>', out)
        self.assertIn('<span class="rp-cat">Other</span>', out)
        self.assertIn(f"Part of: {PAY}", out)
        self.assertIn("rp-seg built rm-w50", out)                                # Payments: 1 of 2 being built
        self.assertIn("Shop · 2 categories", out)
        self.assertNotIn("style=", out)
        out = roadmap.page(SHOP, self.m, None, "tok", {"category": "Payments"})
        self.assertIn(f"<h2>{PAY}", out)

    def test_the_unplanned_and_chart_views(self):
        self.assertIn("Loose end", roadmap.page(SHOP, self.m, None, "tok", {"unplanned": "1"}))
        out = roadmap.page(SHOP, self.m, None, "tok", {"view": "chart"})
        self.assertIn("Build order", out)
        self.assertIn("rm-chart", out)


class Features(unittest.TestCase):
    def test_short_names_and_categories_are_validated(self):
        got = pm.parse_features(block([], [
            {"issue": 5, "priority": "high", "short": "Pay several invoices at once", "category": "Payments"},
            {"issue": 6, "priority": "low", "short": "Multi-invoice API for #12", "category": "x" * 30},
            {"issue": 7, "priority": "low", "short": "y" * 41, "category": "payments"}]), {5, 6, 7})
        self.assertEqual(got, {("", 5): {"short": "Pay several invoices at once", "category": "Payments"}, ("", 7): {"category": "Payments"}})
        many = [{"issue": i, "priority": "low", "category": f"Area {chr(65 + i)}"} for i in range(1, 11)]
        cats = {v["category"] for v in pm.parse_features(block([], many), set(range(1, 11))).values()}
        self.assertEqual(len(cats), pm.MAX_CATEGORIES)

    def test_a_persons_wording_wins_and_the_pms_does_not_churn(self):
        c = dbm.connect(":memory:")
        pm.apply_features(c, {("o/r", 5): {"short": "Pay at once", "category": "Payments"}, ("o/r", 6): {"short": "See the week", "category": "Coaching"}})
        plan.set_feature(c, "o/r", 6, "category", "Coach tools", "person")
        changes = pm.apply_features(c, {("o/r", 5): {"short": "Pay quickly", "category": "Payments"}, ("o/r", 6): {"short": "x y", "category": "Coaching"}})
        f = plan.features(c, ["o/r"])
        self.assertEqual((f[("o/r", 5)]["short"], f[("o/r", 6)]["category"]), ("Pay at once", "Coach tools"))
        self.assertEqual(changes, [])
        pm.apply_features(c, {("o/r", 5): {"category": "Billing"}, ("o/r", 7): {"category": "Billing"}})   # Payments is gone: it may follow
        self.assertEqual(plan.features(c, ["o/r"])[("o/r", 5)]["category"], "Billing")


class Sweep(unittest.TestCase):
    def test_finished_work_is_left_out(self):
        c = dbm.connect(":memory:")
        self.assertTrue(pm.finished(c, "o/r", tk(5, ["factory:pr-open"]), m.DONE))
        self.assertFalse(pm.finished(c, "o/r", tk(6), m.DONE))
        dbm.upsert_pr(c, "o/r", 40, "o/r", 6, "closed") if hasattr(dbm, "upsert_pr") else c.execute(
            "INSERT INTO prs (repo, number, issue_repo, issue_num, status, watch_started, updated, summary) VALUES ('o/r',40,'o/r',6,'closed',0,0,'merged')")
        c.execute("UPDATE prs SET summary='merged' WHERE number=40")
        self.assertTrue(pm.finished(c, "o/r", tk(6), m.DONE))
        c.execute("INSERT INTO prs (repo, number, issue_repo, issue_num, status, watch_started, updated, summary) VALUES ('o/r',41,'o/r',6,'watching',0,0,'')")
        self.assertFalse(pm.finished(c, "o/r", tk(6), m.DONE))                    # one PR still open

    def test_a_project_is_swept_once_over_all_its_repositories(self):
        out = block([PAY], [{"issue": 5, "repo": "o/a", "priority": "high", "milestone": PAY},
                            {"issue": 5, "repo": "o/b", "priority": "high", "milestone": PAY}])
        gh = GH({"stage:analysed": [tk(5), tk(7, ["factory:pr-open"])]}, open_={5, 7})
        fake, calls = fake_runner(out)
        conn = dbm.connect(":memory:")
        cfg = replace(SHOP, repos=["o/a", "o/b"])
        with unittest.mock.patch.object(m.runner, "run_task", side_effect=fake), unittest.mock.patch.object(m, "alert"), \
                unittest.mock.patch.object(m, "start", side_effect=lambda cfg, conn_, repo, n, kind, job, gh=None: job(conn_)):
            for r in cfg.repos:
                m.maybe_pm_sweep(cfg, gh, conn, r)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted((t["repo"], t["number"]) for t in calls[0]["backlog"]), [("o/a", 5), ("o/b", 5)])   # #7 is done
        self.assertEqual(plan.ticket_milestones(conn, "Shop"), {("o/a", 5): PAY, ("o/b", 5): PAY})
        self.assertEqual(sorted(gh.added()), [(5, ("priority: high",)), (5, ("priority: high",))])

    def test_the_prompt_shows_pins_milestones_and_repositories(self):
        from factory.runner import build_prompt
        t = build_prompt("PM", "", Project("Shop", (ProjectRepo("o/a"), ProjectRepo("o/b"))), "o/a", "pm",
                         backlog=[{"number": 5, "repo": "o/b", "title": "x", "labels": [], "body": "b", "pinned_priority": "high",
                                   "milestone": PAY, "milestone_by": "person"}],
                         milestones=[{"name": PAY, "by": "person"}, {"name": WEEK, "by": "pm"}])
        self.assertIn('<ticket repo="o/b" number="5">', t)
        self.assertIn("<pinned_priority>high</pinned_priority>", t)
        self.assertIn(f'<milestone set_by="a person">{PAY}</milestone>', t)
        self.assertIn(f"2. {WEEK} (made by you)", t)
        self.assertIn("Implement multi-invoice API", t)                        # the bad example


if __name__ == "__main__":
    unittest.main()
