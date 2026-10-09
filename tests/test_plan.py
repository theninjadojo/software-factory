import unittest
from dataclasses import replace

from factory import db as dbm
from factory import plan, pm
from factory.config import PmCfg
from factory.ui import plancard, roadmap
from test_pm import GH, PM, tk
from test_roles import CFG


class Pins(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")

    def go(self, gh, *found):
        return pm.apply(PM, gh, self.conn, "o/r", {n: tk(n) for n in (5, 6)}, list(found), 1)

    def test_a_pin_on_the_label_the_pm_set_still_wins(self):
        self.go(GH(open_={5}), pm.Assessment(5, "high", (), ""))             # the PM puts priority: high on #5
        plan.set_pin(self.conn, "o/r", 5, "high")                           # a person picks High: the same label
        gh = GH(open_={5}, labels={5: ["priority: high"]})
        self.go(gh, pm.Assessment(5, "low", (), ""))
        self.assertEqual([c for c in gh.calls if c[0] in ("add", "rm")], [])
        self.assertEqual(plan.who_set(self.conn, "o/r", 5, ["priority: high"]), ("high", "you"))

    def test_normal_can_be_pinned_without_a_label(self):
        plan.set_pin(self.conn, "o/r", 6, "normal")
        gh = GH(open_={6})
        self.go(gh, pm.Assessment(6, "high", (), ""))
        self.assertEqual(gh.added(), [])
        self.assertEqual(plan.who_set(self.conn, "o/r", 6, []), ("normal", "you"))

    def test_letting_the_pm_decide_again_hands_the_label_back(self):
        self.go(GH(open_={5}, labels={5: ["Priority: Low"]}), pm.Assessment(5, "high", (), ""))   # a person's label: overridden
        plan.release(self.conn, "o/r", 5, ["Priority: Low"])
        gh = GH(open_={5}, labels={5: ["priority: low"]})
        self.go(gh, pm.Assessment(5, "high", (), ""))
        self.assertIn(("rm", 5, "priority: low"), gh.calls)
        self.assertEqual(gh.added(), [(5, ("priority: high",))])

    def test_a_label_edit_means_a_pin(self):
        self.assertEqual(plan.pin_from_label("Priority: High", True), "high")
        self.assertEqual(plan.pin_from_label("priority: low", False), "normal")
        self.assertIsNone(plan.pin_from_label("factory:ready", True))

    def test_the_pm_sees_what_a_person_pinned(self):
        items = pm.backlog_items([tk(5), tk(6)], 10, {5: "low"})
        self.assertEqual([i.get("pinned_priority") for i in items], ["low", None])

    def test_who_set_all_reads_local_labels_pins_and_assessments(self):
        self.conn.execute("INSERT INTO local_tickets VALUES ('o/r', 100000001, 't', '', 'open', 'ui', 0, 0)")
        self.conn.execute("INSERT INTO local_labels VALUES ('o/r', 100000001, 'priority: high')")
        dbm.set_pm_assessment(self.conn, "o/r", 7, "low", "priority: low", False, [], "", "", 1)
        plan.set_pin(self.conn, "o/r", 8, "normal")
        got = plan.who_set_all(self.conn)
        self.assertEqual(got[("o/r", 100000001)], ("high", "label"))
        self.assertEqual(got[("o/r", 7)], ("low", "pm"))
        self.assertEqual(got[("o/r", 8)], ("normal", "you"))


class Order(unittest.TestCase):
    def test_steps_follow_what_a_ticket_waits_for_and_survive_loops(self):
        self.assertEqual(plan.steps({1: [], 2: [1], 3: [2, 1], 4: []}), {1: 1, 2: 2, 3: 3, 4: 1})
        got = plan.steps({1: [2], 2: [1]})
        self.assertEqual(set(got), {1, 2})

    def test_chain_and_queue(self):
        waits = {1: [], 2: [1], 3: [2], 4: []}
        self.assertEqual(plan.chain(waits, 2), {1, 2, 3})
        tickets = [{"issue": 1, "priority": "low", "done": False, "waits": []},
                   {"issue": 2, "priority": "high", "done": False, "waits": [1]},
                   {"issue": 4, "priority": "high", "done": False, "waits": []}]
        self.assertEqual([t["issue"] for t in plan.queue(tickets, plan.steps(waits))], [4, 1, 2])


class Milestones(unittest.TestCase):
    def test_add_move_assign_and_delete(self):
        c = dbm.connect(":memory:")
        for n in ("Checkout", "Accounts", "Launch"):
            self.assertTrue(plan.add_milestone(c, "o/r", n))
        self.assertFalse(plan.add_milestone(c, "o/r", " Checkout "))
        plan.move_milestone(c, "o/r", "Launch", -1)
        self.assertEqual(plan.milestones(c, "o/r"), ["Checkout", "Launch", "Accounts"])
        self.assertTrue(plan.set_ticket_milestone(c, "o/r", "o/r", 5, "Launch"))
        self.assertFalse(plan.set_ticket_milestone(c, "o/r", "o/r", 5, "Nope"))
        plan.delete_milestone(c, "o/r", "Launch")
        self.assertEqual(plan.ticket_milestones(c, "o/r"), {})


def row(n, state="new", priority="normal", src=""):
    return {"repo": "o/r", "issue": n, "title": f"Ticket {n}", "state": state, "closed": False, "priority": priority, "prio_src": src}


class Pages(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")
        plan.add_milestone(self.conn, "o/r", "Checkout")
        plan.set_ticket_milestone(self.conn, "o/r", "o/r", 5, "Checkout")
        dbm.set_pm_assessment(self.conn, "o/r", 6, "normal", "", False, [5], "Needs <b>#5</b>", "", 1)

    def test_the_roadmap_orders_tickets_into_milestone_steps(self):
        m = roadmap.model(self.conn, [row(5, priority="high", src="you"), row(6), row(9, "done")], "o/r", ["o/r"])
        self.assertEqual({t["issue"]: t["step"] for t in m["tickets"]}, {5: 1, 6: 2})      # #9 is done and in no milestone
        out = roadmap.page(replace(CFG, repos=["o/r"]), m, ("o/r", 6), "tok")
        self.assertIn("rm-c2", out)
        self.assertIn("Checkout", out)
        self.assertIn("Tracing", out)
        self.assertNotIn("style=", out)

    def test_the_priority_card_says_who_set_it_and_escapes_the_reason(self):
        plan.set_pin(self.conn, "o/r", 6, "low")
        cfg = replace(CFG, pm=PmCfg(enabled=True))
        out = plancard.card(cfg, self.conn, "o/r", 6, None, "Blocked by #12", "tok", "/tickets")
        self.assertIn("Pinned by you · Low", out)
        self.assertIn("&lt;b&gt;#5&lt;/b&gt;", out)
        self.assertIn("Waits for #5, #12", out)
        self.assertIn('value="low" class="pc-opt on" aria-pressed="true"', out)


if __name__ == "__main__":
    unittest.main()
