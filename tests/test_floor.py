import time
import unittest
from unittest import mock

from factory import db as dbm
from factory.ui import floor, views
from test_ui import UiCase

REPO = "your-org/shop-web"
NEED = {"repo": REPO, "issue": 13, "title": "Per-repo budget limits", "reason": "low confidence", "at": 1,
        "acts": [("stage:analyst", "Run analyst", "factory:analyze"), ("build", "Build anyway", "factory:ready"), ("skip", "Skip", None)]}
from factory import questions as Q
_Q1 = Q.Question("q1", "How should a retry be applied?", (("a", "Another fix commit"), ("b", "Re-run the CI only")), "a", "keeps one history", Q.PERSON)
ASK = {"repo": REPO, "issue": 19, "title": "Retry failed CI fixes", "reason": "questions waiting for you", "at": 2, "acts": [], "questions": True,
       "st": Q.StageQuestions("architect", [_Q1])}


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
        d = data(running=[run(1, "stage", "architect", 18)], queued=[{"repo": REPO, "issue": 14, "kind": "implement", "title": "Q"}])
        need = {**NEED, "title": "A <b>title</b>"}
        html = floor.render(d, "tok", "architect", [need, ASK], lambda n, c, b="/": "<form></form>")
        self.assertNotIn("style=", html)
        self.assertNotIn("<b>title</b>", html)
        self.assertIn("A &lt;b&gt;title&lt;/b&gt;", html)

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

    def test_summary_bar_counts_and_links(self):
        d = data(running=[run(1, "stage", "architect", 18)], prs=[{"status": "passed", "repo": REPO, "number": 1}, {"status": "failed", "repo": REPO, "number": 2},
                 {"status": "watching", "repo": REPO, "number": 3}])
        html = floor.render(d, "t", None, [NEED, ASK], lambda n, c, b="/": "")
        for needle in ("Healthy, last poll 5s ago", "<b>1</b> working", "<b>2</b> need you", "<b>3</b> PRs open", 'href="/needs"', 'href="/prs"', "LIVE",
                       "Poll 60s · CI on · Telegram on · Edit", "Architect is working on #18. 2 tickets need you.", "3 PRs · 1 passing · 1 CI failing",
                       "Show as list", "See all 2"):
            self.assertIn(needle, html)
        self.assertIn("DRY RUN", floor.render(data(cfg={**data()["cfg"], "live": False}), "t", None, [], None))
        self.assertIn("Not reporting", floor.render(data(status={}), "t", None, [], None))

    def test_today_line_and_last_four_runs(self):
        now = time.time()
        recent = [run(i, "implement", None, i, status=s, started=now - 60) for i, s in enumerate(["pr", "pr", "failed", "stage"], 1)]
        html = floor.render(data(recent=recent, runs=recent + [run(9, "fix", None, 9, status="pr")]), "t", None, [], None)
        self.assertIn("4 runs · 3 passed · 1 failed", html)
        self.assertEqual(html.count('href="/runs/'), 4)

    def test_the_tables_and_bulk_panel_are_gone_from_home(self):
        d = data(cfg={**data()["cfg"], "live": False}, runs=[run(1, "implement", None, 5, "Unique run title", status="pr")], events=[{"id": 1, "ts": 1, "kind": "x", "message": "Unique event", "repo": REPO, "issue": 1, "run_id": 1}],
                 prs=[{"status": "watching", "repo": REPO, "number": 3, "issue_repo": REPO, "issue_num": 1, "rounds": 0, "summary": "Unique pr", "updated": 1}])
        html = floor.render(d, "t", None, [NEED, ASK, dict(ASK, issue=20)], lambda n, c, b="/": "")
        for gone in ("<table", "Timeline", "Unique run title", "Unique event", "Unique pr", "nd-bulk", "nd-seg"):
            self.assertNotIn(gone, html)
        self.assertEqual(html.count('class="badge warn">'), 4)

    def test_nodes_show_only_a_state_word_and_list_view_works(self):
        d = data(running=[run(1, "stage", "architect", 18, model="secret-model")], cfg={**data()["cfg"], "review": False})
        html = floor.render(d, "t", "architect", [], None)
        self.assertIn('<span class="fl-ns">Working</span>', html)
        self.assertIn('<span class="fl-ns">Off</span>', html)
        self.assertNotIn("secret-model", html.split('class="fl-insp"')[0])
        self.assertIn("secret-model", html.split('class="fl-insp"')[1])       # the model moved to the inspector
        self.assertNotIn("fl-listview", html)
        self.assertIn("fl-listview", floor.render(d, "t", "architect", [], None, view="list"))
        failing = data(recent=[run(2, "implement", None, 4, status="failed", started=time.time() - 10)])
        self.assertEqual(floor.gather(failing, [], time.time())["build"]["label"], "Failing")

    def test_the_tray_has_every_state(self):
        forms = lambda n, c, b="/": f"[{n['issue']}]"
        self.assertIn("Save a GitHub token", floor.tray(None, "t", forms))
        self.assertIn("Nothing needs you", floor.tray([], "t", forms))
        html = floor.tray([NEED, ASK], "t", forms)
        self.assertIn("shop-web#13", html)
        self.assertIn("shop-web#19", html)
        self.assertIn("https://github.com/your-org/shop-web/issues/13", html)         # the ticket on GitHub, not another page of this UI
        many = floor.tray([dict(NEED, issue=i) for i in range(15)], "t", forms)
        self.assertIn("3 more in Tickets", many)


class Phone(unittest.TestCase):
    """The phone layout: markup that CSS shows below 760px (it needs no JavaScript) and the stacked-card tables."""

    def test_the_factory_screen_has_its_parts(self):
        d = data(running=[run(1, "stage", "architect", 18, "Build it")])
        html = floor.render(d, "t", None, [NEED, ASK], None)
        for needle in ('class="ph-home"', "ph-health", "ph-tiles", "working</span>", "need you</span>", "PRs open</span>", "Running now", 'class="ph-pipe"',
                       "Intake", "Analyst", "Designer", "Architect", "Build", "PRs and CI", "Running</span>", "Idle</span>", "Review →", 'href="/needs"'):
            self.assertIn(needle, html)
        self.assertEqual(html.count("ph-step "), 6)
        self.assertIn("<progress", html.split('class="ph-home"')[1])
        self.assertIn("shop-web#13", html.split("ph-need")[1])
        self.assertNotIn("style=", html)

    def test_phone_markup_escapes_untrusted_text(self):
        evil = "<img src=x onerror=alert(1)>"
        d = data(running=[run(1, "stage", "architect", 18, evil)], paused=evil)
        html = floor.phone_home(d, floor.gather(d, [], time.time()), [{**NEED, "title": evil, "reason": evil}], time.time())
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_phone_states_without_work_or_a_token(self):
        d = data()
        live = floor.gather(d, None, time.time())
        self.assertIn("Save a GitHub token", floor.phone_home(d, live, None, time.time()))
        html = floor.phone_home(d, live, [], time.time())
        self.assertIn("Nothing is running", html)
        self.assertIn("Nothing needs you", html)
        self.assertNotIn("Review →", html)

    def test_tables_carry_column_names_for_stacked_cards_and_stay_escaped(self):
        evil = "<script>x</script>"
        html = views.runs_table([run(1, "stage", "architect", 18, evil)]) + views.events_table(
            [dict(ts=1, kind="error", repo=REPO, issue=1, run_id=None, message=evil)])
        self.assertIn('class="stack"', html)
        for col in ('data-l="Status"', 'data-l="Ticket"', 'data-l="Message"'):
            self.assertIn(col, html)
        self.assertNotIn("<script>", html)

    def test_the_tab_bar_keeps_the_four_destinations(self):
        html = views.page("T", "", "/", "c", badges={"/needs": 3})
        nav = html.split('<nav aria-label="Main">')[1].split("</nav>")[0]
        self.assertIn('<span class="navbadge">3</span>', nav)
        self.assertEqual([p for p, _ in views.NAV[:views.PRIMARY]], ["/", "/needs", "/tickets"])
        for label in ("Factory", "Needs you", "Tickets", "More", "Runs", "PRs &amp; CI", "Events", "Settings"):
            self.assertIn(label, nav)


class Ordering(unittest.TestCase):
    """Tickets: what needs a person first, then what is ready, then work in progress; newest first within each group."""

    def setUp(self):
        from factory.config import load
        from factory.ui import labels as L
        self.L, self.cfg = L, load("config.example.toml")

    def issue(self, n, labels=(), state="open", updated="2026-10-02T10:00:00Z"):
        return {"number": n, "title": f"T{n}", "state": state, "updated_at": updated, "labels": [{"name": x} for x in labels]}

    def test_order(self):
        iss = [self.issue(1, updated="2026-10-01T10:00:00Z"),                                          # ready, older
               self.issue(2, updated="2026-10-02T09:00:00Z"),                                          # ready, newer
               self.issue(3, ["factory:working"], updated="2026-10-02T11:00:00Z"),                    # running
               self.issue(4, ["factory:pr-open"], updated="2026-10-02T12:00:00Z"),                    # PR open
               self.issue(5, ["factory:auto"], updated="2026-09-30T10:00:00Z"),                       # waiting for a person (oldest)
               self.issue(6, ["factory:failed"], updated="2026-09-29T10:00:00Z"),                     # failed
               self.issue(7, state="closed", updated="2026-10-02T13:00:00Z"),
               self.issue(8, ["factory:auto"], updated="2026-10-02T08:00:00Z")]                       # queued: auto label, no "human" decision
        dec = {("o/r", 5): {"outcome": "human", "detail": "x; needs a person", "decided_at": 1.0}}
        got = [i["number"] for i in self.L.order(self.cfg, "o/r", iss, dec, {}, set())]
        self.assertEqual(got, [5, 6, 2, 1, 3, 8, 4, 7])

    def test_questions_waiting_and_a_queued_decision(self):
        iss = [self.issue(1), self.issue(2, updated="2026-09-01T10:00:00Z"), self.issue(3, updated="2026-09-02T10:00:00Z")]
        got = [i["number"] for i in self.L.order(self.cfg, "o/r", iss, {}, {2: object()}, {(("o/r"), 3)})]
        self.assertEqual(got, [2, 1, 3])             # questions first; the ticket with a queued approval moves to in-progress

    def test_a_recent_factory_decision_counts_as_activity(self):
        iss = [self.issue(1, updated="2026-10-01T10:00:00Z"), self.issue(2, updated="2026-10-01T11:00:00Z")]
        dec = {("o/r", 1): {"outcome": "run:pr", "detail": "", "decided_at": time.time()}}
        self.assertEqual([i["number"] for i in self.L.order(self.cfg, "o/r", iss, dec, {}, set())], [1, 2])


class Inline(UiCase):
    """The Floor's tray lets a person finish an interaction without leaving the page."""

    def test_questions_are_answerable_on_the_floor(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[ASK, NEED]):
            html = self.req("GET", "/?station=build", cookie=cookie)[2]
        for needle in ('name="accept" value="1"', 'name="back" value="/?station=build"', "Run analyst", "See all 2"):
            self.assertIn(needle, html)
        self.assertNotIn("nd-tog", html)                      # the question forms are on /needs now

    def test_needs_you_attaches_the_questions_and_drops_stale_rows(self):
        from factory.ui import labels as L
        L._needs_cache.update(at=0.0, rows=None)
        L._logins.clear()
        gh = mock.MagicMock()
        gh.token = "t"
        gh.login.return_value = "bot"
        gh.issues.return_value = ([{"number": 19, "title": "A", "state": "open", "labels": [], "updated_at": "2026-10-02T10:00:00Z"},
                                   {"number": 20, "title": "B", "state": "open", "labels": [], "updated_at": "2026-10-02T10:00:00Z"}], False)
        answered = Q.StageQuestions("architect", [_Q1], {"q1": ("option", "a")})
        cur = {19: Q.StageQuestions("architect", [_Q1]), 20: answered}
        h = mock.MagicMock()
        h.app.cfg.return_value = mock.MagicMock(repos=[REPO])
        h.app.ro_db.return_value = self.app.ro_db()
        self.db.execute("INSERT INTO status VALUES ('x','y',0)") if False else None
        with mock.patch("factory.ui.labels._gh", return_value=gh), \
             mock.patch("factory.ui.labels.dbm.questions_waiting", return_value={19, 20}), \
             mock.patch("factory.ui.labels.actions_for", return_value=("", [])), \
             mock.patch("factory.ui.labels.Q.from_comments", side_effect=lambda c, me: c), \
             mock.patch("factory.ui.labels.Q.latest", side_effect=lambda c: cur[c]), \
             mock.patch.object(gh, "issue_comments", side_effect=lambda repo, n: n):
            rows = L.needs_you(h)
        self.assertEqual([r["issue"] for r in rows], [19])                         # #20 is already answered: no longer waiting
        self.assertEqual(rows[0]["st"].stage, "architect")
        L._needs_cache.update(at=0.0, rows=None)


def many(n):
    return Q.StageQuestions("architect", [Q.Question(f"q{i}", f"Question {i}?", (("a", "A"), ("b", "B")), "a", "why", Q.PERSON) for i in range(1, n + 1)])


class Needs(UiCase):
    """The Needs-you redesign: rows, filters, popup, batched answers, bulk accept, its own page and nav count."""

    def test_rows_filters_popup_and_bulk(self):
        rows = [ASK, dict(ASK, issue=20, st=many(1)), dict(ASK, issue=21, st=many(3)), NEED]
        html = floor.tray(rows, "tok", None, "/?station=build")
        self.assertIn("nd-body", html)                                             # one or two questions: inline, no toggle
        self.assertIn('<dialog id="nd-d2"', html)                                  # three questions: a popup
        self.assertIn('data-dialog="nd-d2"', html)
        self.assertIn("<noscript>", html)                                          # and a link when scripts are off
        self.assertIn("Recommended: <b>run analyst</b>", html)
        self.assertIn('action="/tickets/answer-all"', html)                        # bulk accept, behind a "these are the answers" list
        self.assertIn('name="tickets" value="your-org/shop-web#19,your-org/shop-web#20,your-org/shop-web#21"', html)
        q = floor.tray(rows, "tok", None, "/?station=build", "questions")
        self.assertNotIn("Run analyst", q)
        self.assertIn('class="on" href="/?station=build&amp;need=questions"', q)
        d = floor.tray(rows, "tok", None, "/?station=build", "decisions")
        self.assertIn("Run analyst", d)
        self.assertNotIn("nd-body", d)
        self.assertNotIn("answer-all", d)                                          # fewer than two tickets with questions: no bulk button

    def test_the_confidence_meter_and_meta_come_from_the_decision(self):
        row = dict(NEED, kind="feature", complexity="high", conf=0.5)
        html = floor.tray([row], "t", None)
        self.assertIn('<meter class="nd-meter" min="0" max="1" value="0.50"', html)
        self.assertIn("feature · high complexity", html)
        self.assertIn("shop-web#13 · feature", html)                                # repo#n · kind · complexity · age
        self.assertNotIn("style=", html)

    def test_needs_page_nav_count_and_fragment(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[ASK, NEED]):
            s, _, html = self.req("GET", "/needs", cookie=cookie)
            frag = self.req("GET", "/fragment/needs?need=questions", cookie=cookie)[2]
        self.assertEqual(s, 200)
        self.assertIn('data-src="/fragment/needs"', html)
        self.assertIn('<span class="navbadge">2</span>', html)
        self.assertIn("nd-list", frag)
        self.assertNotIn("Run analyst", frag)
        self.assertEqual(self.req("GET", "/fragment/needs")[0], 401)
        self.assertNotIn("style=", html)

    def test_the_back_address_allows_needs_and_the_filter_only(self):
        from factory.ui import labels as L
        for ok in ("/needs", "/needs?need=questions", "/?need=decisions", "/?station=build&need=questions"):
            self.assertTrue(L.BACK.fullmatch(ok), ok)
        for bad in ("/needs?x=1", "/?need=<b>", "//evil", "/needs/../settings", "/?station=a&need=b&view=c&need=d"):
            self.assertFalse(L.BACK.fullmatch(bad), bad)


class Pages(UiCase):
    def test_floor_is_the_default_page_and_the_nav_has_a_phone_menu(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[NEED, ASK]):
            s, h, html = self.req("GET", "/", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("Factory floor", 'class="fl-map"', "Needs you", "/tickets/start", "/tickets/answer", 'class="more"', "Factory</a>"):
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


class TicketsPopup(unittest.TestCase):
    def test_tickets_list_uses_the_floor_popup_not_an_inline_row(self):
        from factory.ui import labels as L
        issue = {"number": 7, "title": "T"}
        html = L.question_popup(REPO, issue, many(1), "tok", "/tickets")
        self.assertIn('<dialog id="nd-t7" class="nd-dialog"', html)
        self.assertIn('data-dialog="nd-t7"', html)
        self.assertIn("q=%237", html)                                              # scripts off: narrow to the ticket
        self.assertNotIn("<details", html)
        self.assertIn("<noscript><details open>", L.question_popup(REPO, issue, many(1), "tok", "/tickets", alone=True))
