import time
import unittest
from unittest import mock

from factory import db as dbm
from factory.ui import floor, views
from test_ui import UiCase

REPO = "your-org/shop-web"
NEED = {"repo": REPO, "issue": 13, "title": "Per-repo budget limits", "reason": "low confidence", "at": 1,
        "acts": [("stage:analyst", "Run analyst", "factory:analyze"), ("build", "Build anyway", "factory:ready"), ("skip", "Skip", None)]}
ASK = {"repo": REPO, "issue": 19, "title": "Retry failed CI fixes", "reason": "questions waiting for you", "at": 2, "acts": [], "questions": True}


def run(i, kind, stage, issue, title="T", status="running", started=None, finished=None, model="opus"):
    return dict(id=i, kind=kind, stage=stage, repo=REPO, issue=issue, title=title, harness="claude-code", model=model, effort="high",
                started=started or time.time() - 100, finished=finished, status=status, detail="", pr_urls="")


def data(**over):
    d = {"cfg": {"poll_seconds": 60, "live": True, "classifier": "rules", "telegram": "normal", "ci": "on", "max_parallel": 2, "review": False, "conflicts": True},
         "paused": "", "status": {"last_poll_ok": {"value": str(time.time() - 5), "updated": 0}}, "running": [], "queued": [], "runs": [], "events": [],
         "prs": [], "recent": [], "decided": {}, "conflicting": 0}
    d.update(over)
    return d


class Stations(unittest.TestCase):
    def test_runs_map_to_stations(self):
        for kind, stage, want in (("stage", "architect", "architect"), ("stage", "analyst", "analyst"), ("stage", "reviewer", "review"), ("implement", None, "build"),
                                  ("fix", None, "ci"), ("review", "reviewer", "review"), ("conflicts", None, "conflicts"), ("stage", "odd", None), ("other", None, None)):
            self.assertEqual(floor.station_of(kind, stage), want, (kind, stage))

    def test_running_and_queued_work_shows_on_its_station(self):
        d = data(running=[run(1, "stage", "architect", 18)], queued=[{"repo": REPO, "issue": 14, "kind": "implement", "title": "Q"}, {"repo": REPO, "issue": 15, "kind": "analyst", "title": "Q2"}])
        live = floor.gather(d, [NEED], time.time())
        self.assertEqual((live["architect"]["state"], live["architect"]["count"]), ("run", 1))
        self.assertEqual((live["build"]["count"], live["build"]["ns"]), (1, "1 queued"))
        self.assertEqual(live["analyst"]["count"], 1)
        self.assertEqual(live["needs"]["count"], 1)
        self.assertEqual(floor.default_station(live), "architect")
        self.assertEqual(floor.default_station(floor.gather(data(), [], time.time())), "classify")

    def test_disabled_stations_are_shown_off(self):
        live = floor.gather(data(cfg={**data()["cfg"], "ci": "off", "conflicts": False}), None, time.time())
        for sid in ("review", "ci", "conflicts"):
            self.assertEqual(live[sid]["state"], "off", sid)
        self.assertEqual(live["needs"]["ns"], "unknown")

    def test_progress_never_reaches_100_and_handles_a_fresh_run(self):
        self.assertLess(floor.progress({"started": time.time() - 99999}, 60), 100)
        self.assertEqual(floor.progress({"started": time.time()}, None), 0)


class Render(unittest.TestCase):
    def test_no_inline_styles_anywhere_because_the_csp_forbids_them(self):
        d = data(running=[run(1, "stage", "architect", 18, "A <b>title</b>")], queued=[{"repo": REPO, "issue": 14, "kind": "implement", "title": "Q"}])
        html = floor.render(d, "tok", "architect", [NEED, ASK], lambda n, c, b="/": "<form></form>")
        self.assertNotIn("style=", html)
        self.assertNotIn("<b>title</b>", html)
        self.assertIn("&lt;b&gt;title&lt;/b&gt;", html)

    def test_hostile_text_is_escaped_in_every_part(self):
        evil = '<img src=x onerror=alert(1)>'
        d = data(running=[run(1, "stage", "architect", 18, evil)], queued=[{"repo": REPO, "issue": 14, "kind": "implement", "title": evil}])
        need = {**NEED, "title": evil, "reason": evil}
        html = floor.render(d, "tok", "build", [need], lambda n, c, b="/": "")
        html += floor.render(d, "tok", "architect", [need], lambda n, c, b="/": "")
        self.assertNotIn("<img", html)

    def test_unknown_station_falls_back_and_selection_is_marked(self):
        d = data(running=[run(1, "stage", "architect", 18)])
        self.assertIn('aria-current=true', floor.render(d, "t", "analyst", [], None))
        self.assertIn("<h2>Analyst</h2>", floor.render(d, "t", "analyst", [], None))
        self.assertIn("<h2>Architect</h2>", floor.render(d, "t", "<script>", [], None))

    def test_the_tray_has_every_state(self):
        forms = lambda n, c, b="/": f"[{n['issue']}]"
        self.assertIn("Save a GitHub token", floor.tray(None, "t", forms))
        self.assertIn("Nothing needs you", floor.tray([], "t", forms))
        html = floor.tray([NEED, ASK], "t", forms)
        self.assertIn("[13]", html) and self.assertIn("[19]", html)
        self.assertIn("%2313", html)
        many = floor.tray([dict(NEED, issue=i) for i in range(9)], "t", forms)
        self.assertIn("3 more in Tickets", many)


class Pages(UiCase):
    def test_floor_is_the_default_page_and_the_nav_has_a_phone_menu(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[NEED, ASK]):
            s, h, html = self.req("GET", "/", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("Factory floor", 'class="fl-map"', "Needs you", "/tickets/start", "/tickets/answer", 'class="more"', "Floor</a>"):
            self.assertIn(needle, html)
        self.assertNotIn("style=", html)
        self.assertIn("Run analyst", html)

    def test_station_param_and_fragment_follow_the_selection(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[]):
            self.assertIn("<h2>Build</h2>", self.req("GET", "/fragment/overview?station=build", cookie=cookie)[2])
            html = self.req("GET", "/?station=%3Cscript%3E", cookie=cookie)[2]
        self.assertNotIn("<script>alert", html)
        self.assertEqual(self.req("GET", "/fragment/overview?station=build")[0], 401)

    def test_floor_works_without_a_github_token(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Save a GitHub token", html)

    def test_pages_stay_free_of_inline_styles(self):
        cookie, _ = self.session()
        for path in ("/runs", "/events", "/prs", "/tickets", "/settings"):
            self.assertNotIn("style=", self.req("GET", path, cookie=cookie)[2], path)


class Speed(unittest.TestCase):
    """The UI made a new GitHub client per request and called GitHub one step after another: a click took seconds."""

    def setUp(self):
        from factory.ui import labels as L
        self.L = L
        L._label_cache.clear()
        L._logins.clear()
        self.gh = mock.MagicMock()
        self.gh.token = "tok-1"
        self.gh.repo_labels.return_value = [{"name": "factory:ready"}, {"name": "bug"}]

    def test_label_names_are_cached_and_a_miss_is_rechecked_once(self):
        self.L._existing(self.gh, "o/r", "bug")
        self.L._existing(self.gh, "o/r", "factory:ready")
        self.assertEqual(self.gh.repo_labels.call_count, 1)
        self.gh.repo_labels.return_value.append({"name": "new-one"})       # created since: one fresh read finds it
        self.L._existing(self.gh, "o/r", "new-one")
        self.assertEqual(self.gh.repo_labels.call_count, 2)
        with self.assertRaises(self.L.Refused):                             # still unknown: refused after the fresh read
            self.L._existing(self.gh, "o/r", "nope")
        with self.assertRaises(self.L.Refused):
            self.L._existing(self.gh, "o/r", "x" * 51)

    def test_the_cache_expires(self):
        self.L.label_names(self.gh, "o/r")
        self.L._label_cache["o/r"] = (time.time() - self.L.LABEL_TTL - 1, ["stale"])
        self.assertEqual(self.L.label_names(self.gh, "o/r"), ["bug", "factory:ready"])

    def test_the_account_login_is_looked_up_once_per_token(self):
        self.gh.login.return_value = "bot"
        self.assertEqual(self.L._login(self.gh), "bot")
        self.assertEqual(self.L._login(self.gh), "bot")
        self.assertEqual(self.gh.login.call_count, 1)

    def test_parallel_reads_keep_their_order_and_raise_the_first_error(self):
        self.assertEqual(self.L._par(lambda: 1, lambda: 2, lambda: 3), [1, 2, 3])
        self.assertEqual(self.L._par(lambda: 7), [7])

        def boom():
            raise OSError("down")
        with self.assertRaises(OSError):
            self.L._par(lambda: 1, boom)


if __name__ == "__main__":
    unittest.main()
