import unittest

from factory import version
from factory.ui import journey_view, labels, views


def step(n, station, state):
    return {"n": n, "station": station, "state": state, "kind": "run", "seconds": 30, "started": 0, "finished": 30, "status": state,
            "tokens": {"in": 1000, "out": 200}, "run_id": n, "harness": "claude-code", "model": "sonnet", "effort": "medium"}


class Crates(unittest.TestCase):
    def j(self, last):
        return {"steps": [step(1, "classify", "done"), step(2, "analyst", last)], "status": "running", "seconds": 60,
                "tokens": {"in": 2000, "out": 400}, "models": {"sonnet": 2}, "waiting": None}

    def test_crates_ride_the_belt_into_the_station_that_is_working(self):
        svg = journey_view.map_svg(self.j("running"))
        self.assertEqual(svg.count('class="jy-crate"'), 2)
        self.assertIn("<animateMotion", svg)

    def test_finished_belts_carry_no_crates(self):
        self.assertNotIn("jy-crate", journey_view.map_svg(self.j("done")))

    def test_the_trail_is_one_continuous_path(self):
        d = journey_view._trail(journey_view._route("classify", "build", 1))
        self.assertEqual(d.count("M"), 1)
        self.assertTrue(d.startswith("M") and " L" in d)


class TicketsAreOnePlace(unittest.TestCase):
    def test_the_answer_forms_may_return_to_a_ticket_page(self):
        for ok in ("/ticket?repo=o%2Fr&n=980", "/tickets?stage=prs", "/tickets?stage=needs&repo=o%2Fr"):
            self.assertTrue(labels.BACK.fullmatch(ok), ok)
        for bad in ("/ticket?repo=o%2Fr&n=x", "//evil.example", "/ticket?repo=o%2Fr&n=1&x=<script>"):
            self.assertFalse(labels.BACK.fullmatch(bad), bad)

    def test_the_stage_filters_include_prs_and_checks(self):
        self.assertIn("prs", labels.STAGE_FILTERS)
        html = labels.filters(type("C", (), {"repos": ["o/r"], "trigger_label": "t", "auto_label": "a", "roles": []})(), "o/r", "open", "", "", [], "", "prs")
        self.assertIn("PRs &amp; CI", html)
        self.assertIn("Needs you", html)

    def test_the_prs_card_on_a_ticket(self):
        self.assertIn("No pull request yet", views.ticket_prs([]))
        pr = {"repo": "o/r", "number": 5, "issue_repo": "o/r", "issue_num": 1, "status": "failed", "rounds": 1, "watch_started": 1, "updated": 2,
              "summary": "", "title": "Fix"}
        html = views.ticket_prs([pr], 2)
        self.assertIn("o/r#5", html)
        self.assertIn("1 failing", html)

    def test_the_ticket_page_has_needs_and_pull_requests(self):
        html = views.ticket_detail("o/r", 1, [], None, (), False, "", '<h2>Needs you</h2>X', "<p>PR</p>")
        self.assertLess(html.index("Needs you"), html.index("Pipeline"))
        self.assertIn("Pull requests and checks", html)


class Version(unittest.TestCase):
    def test_the_version_is_bumped_for_the_merged_pages(self):
        self.assertEqual(version.current().split(".")[:2], ["0", "4"])
