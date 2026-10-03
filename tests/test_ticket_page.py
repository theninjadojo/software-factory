"""The Factory and Tickets screens (board.py): the station model, belts and crates, the list's filters, a ticket's detail."""
import time
import unittest

from factory import questions as Q
from factory.ui import board, labels, views

REPO = "o/r"


def run(n, station, state, stage=None, kind="run", seconds=30, started=0.0, model="sonnet", run_id=None):
    return {"n": n, "kind": kind, "station": station, "state": state, "stage": stage, "run_kind": "stage" if stage else "build", "status": "stage",
            "started": started, "finished": started + seconds if state != "running" else None, "seconds": seconds, "run_id": run_id or n,
            "harness": "claude-code", "model": model, "effort": "medium", "tokens": {"in": 1000, "out": 200}, "message": ""}


def journey(steps, status="done"):
    return {"steps": steps, "status": status, "seconds": 60, "tokens": {"in": 2000, "out": 400}, "models": {"sonnet": 1},
            "waiting": steps[-1] if status == "waiting" else None}


def row(issue, state, st, title="T", why="w", at=None, need=None, prs=(), steps=()):
    return {"repo": REPO, "issue": issue, "title": title, "state": state, "stations": st, "at": at, "when": time.time() - 60, "why": why,
            "journey": journey(list(steps)), "prs": list(prs), "need": need}


WAITING = [run(1, "analyst", "done", "analyst"), run(2, "designer", "done", "designer"),
           {"n": 3, "kind": "person", "station": "needs", "state": "waiting", "stage": "designer", "pending": 2, "started": 99, "finished": None,
            "seconds": None, "message": "2 open question(s) from the designer"}]


class Stations(unittest.TestCase):
    def test_a_ticket_waiting_on_design_questions(self):
        st = board.stations(journey(WAITING, "waiting"))
        self.assertEqual([st[s] for s, _ in board.STATIONS], ["done", "done", "done", "done", "wait", "none", "none", "none", "none", "none"])
        self.assertEqual(board.current(st), "designer")
        self.assertEqual(board.ticket_state(st, False, []), "needs")

    def test_a_build_running_and_a_pull_request_in_ci(self):
        st = board.stations(journey([run(1, "build", "running")], "running"))
        self.assertEqual((st["build"], board.current(st), board.ticket_state(st, False, [])), ("run", "build", "working"))
        st = board.stations(journey([run(1, "build", "done")]), [{"status": "watching", "number": 5}])
        self.assertEqual((st["ci"], st["pr"], board.ticket_state(st, False, [{"status": "watching"}])), ("run", "run", "prs"))
        st = board.stations(journey([run(1, "build", "done")]), [{"status": "failed", "number": 5}])
        self.assertEqual(board.ticket_state(st, False, [{"status": "failed"}]), "failed")
        st = board.stations(journey([run(1, "build", "done")]), [{"status": "closed", "number": 5}])
        self.assertEqual((st["pr"], board.ticket_state(st, False, [{"status": "closed"}])), ("done", "done"))

    def test_the_router_handing_a_ticket_to_a_person(self):
        steps = [{"n": 1, "kind": "person", "station": "needs", "state": "waiting", "started": 1, "finished": None, "seconds": None, "message": "needs a person"}]
        st = board.stations(journey(steps, "waiting"))
        self.assertEqual((st["route"], board.current(st)), ("wait", "route"))

    def test_nothing_recorded(self):
        st = board.stations(journey([]))
        self.assertEqual(set(st.values()), {"none"})
        self.assertIsNone(board.current(st))


class Belts(unittest.TestCase):
    def test_crates_move_into_a_running_station_and_back_up_at_a_waiting_one(self):
        st = board.stations(journey(WAITING, "waiting"))
        html = board.strip(st)
        self.assertEqual(html.count('<span class="sd-belt '), 9)
        self.assertIn('<span class="sd-belt wait" aria-hidden="true"><i class="sd-crate"></i><i class="sd-crate two"></i></span>', html)
        self.assertEqual(html.count("sd-crate two"), 1)                   # finished and unreached belts carry no crates
        self.assertIn('aria-label="Designer: Needs you"', html)

    def test_the_floor_snake_places_ten_machines_and_nine_belts(self):
        rows = [row(1, "working", {**board.stations(journey([])), "build": "run"}, at="build"),
                row(2, "needs", {**board.stations(journey([])), "route": "wait"}, at="route"),
                row(3, "done", board.stations(journey([])), at="pr")]
        fl = board.floor_stations(rows)
        self.assertEqual((fl["build"]["count"], fl["build"]["state"], fl["route"]["state"], fl["pr"]["count"]), (1, "run", "wait", 0))  # done tickets are not on the floor
        html = board.snake(fl)
        self.assertEqual(sum(f' p{i} ' in html for i in range(10)), 10)
        self.assertEqual(sum(f' q{i}' in html for i in range(9)), 9)
        self.assertIn(" q4 down", html)
        self.assertIn(" q5 left", html)
        self.assertIn('href="/tickets?stage=all&amp;at=build"', html)


class TicketList(unittest.TestCase):
    ROWS = [row(1, "needs", {}, "Alpha <b>", at="designer"), row(2, "working", {}, "Beta", at="build"), row(3, "done", {}, "Gamma", at="pr")]

    def test_filters_counts_search_and_station(self):
        self.assertEqual(board.counts(self.ROWS), {"needs": 1, "working": 1, "prs": 0, "failed": 0, "done": 1, "all": 3})
        self.assertEqual([r["issue"] for r in board.pick(self.ROWS, "all")], [1, 2, 3])
        self.assertEqual([r["issue"] for r in board.pick(self.ROWS, "working")], [2])
        self.assertEqual([r["issue"] for r in board.pick(self.ROWS, "all", "pr")], [3])
        self.assertEqual([r["issue"] for r in board.pick(self.ROWS, "all", q="#2")], [2])
        self.assertEqual([r["issue"] for r in board.pick(self.ROWS, "all", q="gam")], [3])
        many = [dict(self.ROWS[0], issue=n) for n in (9, 19, 109, 90)]
        self.assertEqual([r["issue"] for r in board.pick(many, "all", q="#9")], [9])     # a number is exact

    def test_the_page_is_escaped_and_free_of_inline_styles(self):
        st = board.stations(journey([]))
        rows = [dict(r, stations=st) for r in self.ROWS]
        html = board.tickets_page(rows, rows[0], True, "all", "", "", "latest", "", "", time.time(), "tok")
        self.assertNotIn("<b>", html)
        self.assertIn("Alpha &lt;b&gt;", html)
        self.assertNotIn("style=", html)
        self.assertIn('class="sd-page has-sel"', html)                  # a phone shows the ticket, not the list
        self.assertIn('href="/ticket?repo=o%2Fr&amp;n=1&amp;stage=all"', html)
        searched = board.tickets_page(rows, None, False, "all", "<q>", "", "latest", "", "", time.time(), "tok")
        self.assertIn('value="&lt;q&gt;"', searched)
        self.assertIn("No tickets match", searched)
        for word in ("Needs you", "Working", "PRs &amp; CI", "Failed", "Done", "All"):
            self.assertIn(word, html)

    def test_the_answer_forms_may_return_to_a_ticket_page(self):
        for ok in ("/ticket?repo=o%2Fr&n=980", "/tickets?stage=prs", "/tickets?stage=needs&repo=o%2Fr"):
            self.assertTrue(labels.BACK.fullmatch(ok), ok)
        for bad in ("/ticket?repo=o%2Fr&n=x", "//evil.example", "/ticket?repo=o%2Fr&n=1&x=<script>"):
            self.assertFalse(labels.BACK.fullmatch(bad), bad)


class Detail(unittest.TestCase):
    def test_the_questions_post_the_same_fields_as_the_answer_form(self):
        q1 = Q.Question("q1", "Contrast <ok>?", (("a", "Darker"), ("b", "Keep")), "a", "4.3:1 is low", Q.PERSON)
        q2 = Q.Question("q2", "Rows?", (("a", "Headings"), ("b", "Both")), "a", "small", Q.SAFE)
        need = {"repo": REPO, "issue": 5, "title": "T", "st": Q.StageQuestions("designer", [q1, q2]), "acts": []}
        html = board.needs_card(need, "tok", "/ticket?repo=o%2Fr&n=5")
        for needle in ('action="/tickets/answer"', 'name="stage" value="designer"', 'name="a_q1" value="a" checked', 'name="x_q1"',
                       'name="accept" value="1"', 'name="send" value="1"', 'name="back" value="/ticket?repo=o%2Fr&amp;n=5"', "1 answered with safe defaults",
                       "Contrast &lt;ok&gt;?", "recommended"):
            self.assertIn(needle, html)
        self.assertNotIn('name="a_q2"', html)                            # a safe default is folded away, not asked again
        self.assertEqual(board.needs_card(None, "tok", "/"), "")

    def test_the_detail_has_every_part_in_the_design_order(self):
        r = row(5, "needs", board.stations(journey(WAITING, "waiting")), "Checkout", at="designer", steps=WAITING,
                prs=[{"repo": REPO, "number": 9, "status": "failed", "rounds": 1, "title": "Fix"}])
        r["journey"] = journey(WAITING, "waiting")
        files = [{"repo": REPO, "path": "docs/design/previews/factory-5-x.png", "url": f"https://github.com/{REPO}/blob/{'a' * 40}/docs/design/previews/factory-5-x.png",
                  "pr": f"https://github.com/{REPO}/pull/77"}]
        html = board.detail_html(r, "<section>NEEDS</section>", files, ["analyst", "designer"], [{"ts": time.time() - 30, "message": "Design <done>"}], 2, time.time(), True)
        order = [html.index(x) for x in ('class="sd-tiles"', "NEEDS", 'id="j-h"', 'id="d-h"', "Steps in order", 'id="pr-h"', 'id="ev-h"')]
        self.assertEqual(order, sorted(order))
        for needle in ("Draft PR #77", "PR #9", "Checks failing", "Read design", 'id="live" data-src="/fragment/ticket"', "Design &lt;done&gt;", "/runs/1"):
            self.assertIn(needle, html)
        self.assertNotIn("style=", html)

    def test_the_phone_journey_lists_every_station_with_the_mockups_in_design(self):
        r = row(5, "needs", board.stations(journey(WAITING, "waiting")), steps=WAITING)
        files = [{"repo": REPO, "path": "docs/design/previews/factory-5-x.png", "url": f"https://github.com/{REPO}/blob/{'a' * 40}/docs/design/previews/factory-5-x.png", "pr": ""}]
        html = board.phone_journey(r, files, ["designer"])
        self.assertEqual(html.count('<li class="sd-pj'), 10)
        self.assertIn("img", html.split("Designer")[1].split("Architect")[0])
        self.assertIn("Not started", html)


class Words(unittest.TestCase):
    def test_the_classifiers_tags_become_words(self):
        self.assertEqual(board.plain("human: classifier flagged needs_human (cls=feature/medium/human=True/conf=0.68/stage=design)"),
                         "The classifier asked for a person")
        self.assertEqual(board.plain("needs a person: low confidence (cls=feature/medium/human=False/conf=0.84/stage=implement)"), "Low confidence")
        self.assertEqual(board.plain("run: cls=feature/deep; analyst first"), "Run: analyst first")
        self.assertEqual(board.plain(""), "")


class Nav(unittest.TestCase):
    def test_ticket_pages_light_up_tickets_and_a_phone_gets_the_menu(self):
        for path in ("/needs", "/runs", "/runs/7", "/prs", "/ticket", "/ticket/doc"):
            nav = views.page("T", "", path, "c").split('<nav aria-label="Main">')[1].split("</nav>")[0]
            self.assertIn('<a href="/tickets" class="active ">Tickets</a>', nav, path)
        html = views.page("T", "", "/", "c", badges={"/tickets": 3})
        self.assertIn('<details class="ph-menu">', html)
        self.assertEqual(html.count('<span class="navbadge">3</span>'), 2)      # the tab and the phone menu
        self.assertNotIn("<h1>", views.page("T", "x", "/", "c", bare=True))


class Styled(unittest.TestCase):
    """Every class the two screens put on the page has a rule in style.css. Class names built in code (p0..p9, a state word)
    do not appear literally in the source, so a clean-up that looks for unused CSS cannot see them: this test can."""

    def test_every_emitted_class_has_a_rule(self):
        import re
        from pathlib import Path
        css = (Path(board.__file__).parent / "static" / "style.css").read_text()
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"\{[^}]*\}", "", css)))
        q1 = Q.Question("q1", "Contrast?", (("a", "Darker"), ("b", "Keep")), "a", "low", Q.PERSON)
        need = {"repo": REPO, "issue": 5, "title": "T", "st": Q.StageQuestions("designer", [q1]), "acts": []}
        states = ("done", "run", "wait", "fail", "none")
        rows = [row(i, s, {sid: states[(i + k) % 5] for k, (sid, _) in enumerate(board.STATIONS)}, at=board.STATIONS[i][0]) for i, s in enumerate(board.ORDER)]
        fl = {sid: {"state": states[k % 5], "refs": ["#1"], "count": 1} for k, (sid, _) in enumerate(board.STATIONS)}
        r = row(5, "needs", board.stations(journey(WAITING, "waiting")), steps=WAITING, prs=[{"repo": REPO, "number": 9, "status": "failed", "rounds": 1}])
        r["journey"] = journey(WAITING, "waiting")
        html = (board.snake(fl) + board.tickets_page(rows, rows[0], True, "all", "", "", "latest", "", "", time.time(), "tok")
                + board.detail_html(r, board.needs_card(need, "tok", "/"), [], ["designer"], [], 2, time.time(), True) + board.phone_journey(r, [], []))
        used = {c for attr in re.findall(r'class="([^"]+)"', html) for c in attr.split()}
        generic = {"muted", "mono", "lab", "btn", "secondary", "blue", "wide", "link", "num", "live", "dim", "two", "big", "hot", "on", "down", "left",
                   "nd-form", "inline", "scroll", "stack", "mockups", "sd-floor", "sd-journey", "sd-cl", "sd-pjl", "sd-said", "sd-agents", "sd-q", *states, "ok"}
        missing = sorted(c for c in used - generic if c not in defined)
        self.assertEqual(missing, [])
        for i in range(10):                                              # the snake's places
            self.assertIn(f".p{i} {{ grid-area:", css)
        for i in range(9):
            self.assertIn(f".q{i} {{ grid-area:", css)
