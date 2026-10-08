"""A failed ticket's page says what went wrong first: each pull request's failing checks in full, how the fix rounds ended and what
to do next. The rest of the page drops noise: skipped stages read as skipped, repository names keep their case, factory labels fold away."""
import unittest

from factory.sanitize import md_render
from factory.ui import board

T = 1_800_000_000
J = {"steps": [
    {"kind": "run", "station": "build", "state": "done", "started": T, "finished": T + 60, "seconds": 60, "message": "",
     "tokens": {"in": 1000, "out": 10}, "model": "sonnet", "effort": "medium", "run_id": 7, "status": "pr"},
    {"kind": "ci", "station": "ci", "state": "failed", "started": T + 100, "finished": T + 100, "seconds": None,
     "message": "theninjadojo/little-ninjas#1089: failing: CI / lint · types · unit tests, CI / migrations · rls regression"},
    {"kind": "ci", "station": "ci", "state": "done", "started": T + 200, "finished": T + 200, "seconds": None,
     "message": "theninjadojo/ninja-packages#127: passed"}],
    "seconds": 300, "tokens": {"in": 1000, "out": 10}, "models": {"sonnet": 1}, "status": "failed"}
PRS = [{"repo": "theninjadojo/little-ninjas", "number": 1089, "status": "closed", "rounds": 1, "summary": "closed", "title": "t"},
       {"repo": "theninjadojo/ninja-packages", "number": 127, "status": "closed", "rounds": 1, "summary": "closed", "title": "t"}]


def row(**over):
    st = board.stations(J, PRS)
    return {"repo": "theninjadojo/little-ninjas", "issue": 100000005, "title": "Coach timetable", "state": "failed", "stations": st,
            "at": "ci", "journey": J, "prs": PRS, "need": None, "when": T + 200, **over}


class WhatWentWrong(unittest.TestCase):
    def test_it_lists_each_pull_requests_checks_in_full_and_says_the_prs_are_closed(self):
        out = board.failed_card(row(), 1, T + 3600, "<form>Build</form>", {"author": "Factory", "body": "CI failed on #1089.", "at": T + 100})
        self.assertIn("CI kept failing after 1 of 1 fix round.", out)
        self.assertIn("CI / migrations · rls regression", out)                   # never cut
        self.assertIn("ninja-packages #127", out)                                # another repository is named
        self.assertIn("Checks passed after the fix", out)
        self.assertIn("Both pull requests are closed without being merged", out)
        self.assertIn("Latest from the factory", out)
        self.assertIn("<form>Build</form>", out)

    def test_a_failed_run_without_a_pull_request_names_the_step(self):
        j = {**J, "steps": [{**J["steps"][0], "state": "failed", "message": "sandbox exited 1"}]}
        out = board.failed_card(row(journey=j, prs=[], at="build"), 1, T + 60)
        self.assertIn("Build failed.", out)
        self.assertIn("Sandbox exited 1", out)
        self.assertIn('href="/runs/7"', out)

    def test_the_list_line_names_the_checks_and_cuts_at_a_word(self):
        why = board._why("failed", J, PRS, None, {}, "ci", T)
        self.assertTrue(why.startswith("CI failing on #1089: lint · types · unit tests"), why)
        self.assertLessEqual(len(why), 140)


class Noise(unittest.TestCase):
    def test_stages_the_ticket_went_past_are_skipped(self):
        st = board.shown(board.stations(J, PRS))
        self.assertEqual([st[s] for s in ("analyst", "designer", "architect")], ["skip"] * 3)
        self.assertIn("Skipped", board.strip(board.stations(J, PRS)))

    def test_a_repository_name_keeps_its_case_and_italics_render(self):
        self.assertEqual(board.plain("theninjadojo/x#1: failing: a"), "theninjadojo/x#1: failing: a")
        self.assertEqual(board.plain("classifier said no"), "Classifier said no")
        html = md_render("_Source: notes (a_b.md)._ keeps snake_case")[0]
        self.assertIn("<em>Source: notes (a_b.md).</em>", html)
        self.assertIn("snake_case", html)


if __name__ == "__main__":
    unittest.main()
