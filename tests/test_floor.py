import time
from pathlib import Path
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


class Render(unittest.TestCase):
    def test_the_dashboard_has_the_designs_parts(self):
        d = data(running=[run(1, "build", None, 18, "Build it")], prs=[{"status": "watching", "repo": REPO, "number": 3, "issue_repo": REPO, "issue_num": 18, "title": "PR"}],
                 status={"last_poll_ok": {"value": str(time.time() - 5)}, "claude_usage": {"value": '{"session": {"used": 41}, "week": {"used": 18}}'}})
        html = floor.render(d, "tok", None, [NEED, ASK], None)
        for needle in ("<h1>Factory</h1>", "Live · running · polling every 60 s · Healthy, last poll 5s ago", "Needs you", "Working now", "PRs &amp; CI",
                       "Done today", "Failed", "The floor", "Running now", "Build it", "CI · PR #3 · checks running", "Claude plan usage", 'value="41"',
                       'href="/tickets?stage=needs"', 'href="/tickets?stage=prs"', 'action="/action/pause"', "Mode: live"):
            self.assertIn(needle, html)
        self.assertEqual(sum(f"sd-mach p{i} " in html for i in range(10)), 9)       # review is off in this config: no Review station
        self.assertNotIn("Review: ", html)
        self.assertIn('<svg class="fm"', html)
        self.assertIn("Not reporting", floor.render(data(status={}), "t", None, [], None))
        self.assertIn("Dry run", floor.render(data(cfg={**data()["cfg"], "live": False}), "t", None, [], None))

    def test_the_tray_answers_and_decides_in_place(self):
        html = floor.render(data(), "tok", None, [ASK, NEED], None)
        self.assertIn('action="/tickets/answer-all"', html)                          # accept every recommendation at once
        self.assertIn("Answer 1 question", html)                                     # the questions themselves are on the ticket
        self.assertIn('href="/ticket?repo=your-org%2Fshop-web&amp;n=19"', html)
        self.assertIn("Run analyst", html)                                           # a decision is one click
        self.assertIn('name="back" value="/"', html)
        self.assertIn("Save a GitHub token", floor.render(data(), "t", None, None, None))

    def test_phones_hide_the_maps_legend_lines(self):
        css = (Path(__file__).resolve().parent.parent / "factory" / "ui" / "static" / "style.css").read_text()
        phone = css[css.index("@media (max-width:760px) { .fm-wrap"):]
        self.assertIn(".sd-legend .fm-key { display:none; }", phone.split("}")[0] + "}")   # beats .sd-legend li { display:inline-flex }

    def test_the_answers_dialog_cannot_scroll_sideways(self):
        css = (Path(__file__).resolve().parent.parent / "factory" / "ui" / "static" / "style.css").read_text()
        self.assertIn(".nd-dialog .nd-qs { grid-template-columns:minmax(0,1fr); }", css)        # a 1fr track grows to a long URL or path; minmax(0,1fr) wraps it
        self.assertIn(".nd-dialog { overflow-x:hidden; }", css)
        self.assertIn("overflow-wrap:anywhere", css.split(".nd-dialog { overflow-x:hidden; }")[1])

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

    def test_app_js_reads_the_form_action_as_an_attribute(self):
        # a <button name="action"> inside a form shadows form.action in the DOM, so fetch(f.action) posted to "[object HTMLButtonElement]"
        from pathlib import Path
        js = (Path(floor.__file__).parent / "static" / "app.js").read_text()
        self.assertNotIn("f.action", js)

    def test_app_js_patches_the_live_region_in_place(self):
        # innerHTML swaps restart every animation and close <details>; the refresh must morph and leave popups alone
        from pathlib import Path
        js = (Path(floor.__file__).parent / "static" / "app.js").read_text()
        self.assertNotIn("live.innerHTML = html;", js)
        self.assertNotIn("dialog[open]", js)
        for needle in ("morph(live, html)", '"open"', '"begin"', "hidden"):
            self.assertIn(needle, js)

    def test_tables_carry_column_names_for_stacked_cards_and_stay_escaped(self):
        evil = "<script>x</script>"
        html = views.runs_table([run(1, "stage", "architect", 18, evil)]) + views.events_table(
            [dict(ts=1, kind="error", repo=REPO, issue=1, run_id=None, message=evil)])
        self.assertIn('class="stack"', html)
        for col in ('data-l="Status"', 'data-l="Ticket"', 'data-l="Message"'):
            self.assertIn(col, html)
        self.assertNotIn("<script>", html)

    def test_the_tab_bar_keeps_the_four_destinations(self):
        html = views.page("T", "", "/", "c", badges={"/tickets": 3})
        nav = html.split('<nav aria-label="Main">')[1].split("</nav>")[0]
        self.assertIn('<span class="navbadge">3</span>', nav)
        self.assertEqual([p for p, _ in views.NAV[:views.PRIMARY]], ["/", "/tickets", "/events"])
        for label in ("Factory", "Tickets", "More", "Events", "Settings"):
            self.assertIn(label, nav)
        for gone in ("Needs you", "Runs", "PRs &amp; CI"):         # these live inside Tickets now
            self.assertNotIn(gone, nav)

    def test_ticket_pages_light_up_the_tickets_tab(self):
        for path in ("/needs", "/runs", "/runs/7", "/prs", "/ticket", "/ticket/doc"):
            nav = views.page("T", "", path, "c").split('<nav aria-label="Main">')[1].split("</nav>")[0]
            self.assertIn('<a href="/tickets" class="active ">Tickets</a>', nav, path)


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

    def test_github_off_still_reads_the_questions_of_local_tickets(self):
        from factory import tracker
        from factory.ui import labels as L
        L._needs_cache.update(at=0.0, rows=None)
        L._logins.clear()
        local = tracker.LOCAL_BASE + 1
        gh = mock.MagicMock()
        gh.login.return_value = "bot"
        gh.get_issue.side_effect = lambda repo, n: {"number": n, "title": "Mine", "state": "open", "labels": []}
        h = mock.MagicMock()
        h.app.cfg.return_value = mock.MagicMock(repos=[REPO], github_issues_enabled=False, local_enabled=True)
        h.app.ro_db.return_value = self.app.ro_db()
        with mock.patch("factory.ui.labels._gh", return_value=gh), \
             mock.patch("factory.ui.labels.dbm.questions_waiting", return_value={19, local}), \
             mock.patch("factory.ui.labels.actions_for", return_value=("", [])), \
             mock.patch("factory.ui.labels.Q.from_comments", side_effect=lambda c, me: c), \
             mock.patch("factory.ui.labels.Q.latest", side_effect=lambda c: Q.StageQuestions("architect", [_Q1])), \
             mock.patch.object(gh, "issue_comments", side_effect=lambda repo, n: n):
            rows = L.needs_you(h)
        self.assertEqual([r["issue"] for r in rows], [local])                      # the GitHub issue is not read; the local ticket's questions are
        gh.issues.assert_not_called()
        self.assertEqual([c.args[1] for c in gh.get_issue.call_args_list], [local])
        L._needs_cache.update(at=0.0, rows=None)

    def test_waiting_tickets_older_than_the_first_page_are_fetched_by_number(self):
        from factory.ui import labels as L
        L._needs_cache.update(at=0.0, rows=None)
        L._logins.clear()
        gh = mock.MagicMock()
        gh.login.return_value = "bot"
        gh.issues.return_value = ([{"number": 300, "title": "New", "state": "open", "labels": []}], True)       # page 1 is full of newer items
        old = {19: {"number": 19, "title": "Old one", "state": "open", "labels": []}, 7: {"number": 7, "title": "Closed", "state": "closed", "labels": []}}
        gh.get_issue.side_effect = lambda repo, n: old[n]
        h = mock.MagicMock()
        h.app.cfg.return_value = mock.MagicMock(repos=[REPO])
        h.app.ro_db.return_value = self.app.ro_db()
        with mock.patch("factory.ui.labels._gh", return_value=gh), \
             mock.patch("factory.ui.labels.dbm.questions_waiting", return_value={19, 7}), \
             mock.patch("factory.ui.labels.actions_for", return_value=("", [])), \
             mock.patch("factory.ui.labels.Q.from_comments", side_effect=lambda c, me: c), \
             mock.patch("factory.ui.labels.Q.latest", side_effect=lambda c: Q.StageQuestions("architect", [_Q1])), \
             mock.patch.object(gh, "issue_comments", side_effect=lambda repo, n: n):
            rows = L.needs_you(h)
        self.assertEqual([r["issue"] for r in rows], [19])                         # found by number; the closed one is dropped
        self.assertEqual(sorted(c.args[1] for c in gh.get_issue.call_args_list), [7, 19])
        self.assertEqual(L.titles_cached()[(REPO, 19)], "Old one")
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
    def test_the_dashboard_is_the_default_page_and_refreshes_itself(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_you", return_value=[NEED, ASK]), mock.patch("factory.ui.server.L.needs_cached", return_value=[NEED, ASK]):
            s, _, html = self.req("GET", "/", cookie=cookie)
            frag = self.req("GET", "/fragment/overview", cookie=cookie)[2]
        self.assertEqual(s, 200)
        for needle in ("<title>Factory · Shikumi</title>", '<div id="live">', "The floor", "/tickets/start", "Run analyst", 'class="ph-menu"'):
            self.assertIn(needle, html)
        self.assertNotIn("style=", html)
        self.assertIn("The floor", frag)
        self.assertNotIn("<html", frag)
        self.assertEqual(self.req("GET", "/fragment/overview")[0], 401)

    def test_the_page_never_waits_for_github_and_the_tray_loads_in_place(self):
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_cached", return_value=None), mock.patch("factory.ui.server.L.has_token", return_value=True), \
                mock.patch("factory.ui.server.L.needs_you", side_effect=AssertionError("the page waited for GitHub")):
            html = self.req("GET", "/", cookie=cookie)[2]
        self.assertIn('data-load="/fragment/needs-tray"', html)
        self.assertIn("asking GitHub", html)
        with mock.patch("factory.ui.server.L.needs_you", return_value=[NEED]):
            tray = self.req("GET", "/fragment/needs-tray", cookie=cookie)[2]
        self.assertIn("Run analyst", tray)
        self.assertNotIn("<html", tray)
        self.assertEqual(self.req("GET", "/fragment/needs-tray")[0], 401)

    def test_tickets_never_wait_for_github_and_the_ticket_loads_in_place(self):
        dbm.record(self.db, REPO, 13, "t", "human", "needs a person: low confidence")
        cookie, _ = self.session()
        with mock.patch("factory.ui.server.L.needs_cached", return_value=None), mock.patch("factory.ui.server.L.has_token", return_value=True), \
                mock.patch("factory.ui.server.L.needs_you", side_effect=AssertionError("the page waited for GitHub")):
            html = self.req("GET", "/tickets?stage=all", cookie=cookie)[2]
        self.assertIn('data-load="/fragment/detail?repo=your-org%2Fshop-web&amp;n=13"', html)
        self.assertIn('<template id="ld-detail">', html)
        with mock.patch("factory.ui.server.L.needs_you", return_value=[NEED]):
            s, _, detail = self.req("GET", f"/fragment/detail?repo={REPO}&n=13", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn('class="sd-detail"', detail)
        self.assertIn("Run analyst", detail)                        # the decision buttons came with it
        self.assertNotIn("<html", detail)
        self.assertEqual(self.req("GET", f"/fragment/detail?repo={REPO}&n=13")[0], 401)

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


class AddTicketPopup(unittest.TestCase):
    def test_add_a_ticket_is_a_popup_with_a_csrf_form_and_a_no_js_fallback(self):
        from factory.ui import board
        html = board.add_ticket(["o/r"], "tok", [])
        self.assertIn('data-dialog="fn-add-d"', html)
        self.assertIn('<dialog id="fn-add-d" class="nd-dialog"', html)
        self.assertIn('action="/tickets/create"', html.split("<dialog")[1])
        self.assertIn("csrf", html.split("<dialog")[1].lower())
        self.assertIn("<noscript><details", html)
        self.assertEqual(board.add_ticket([], "tok", []), "")
        self.assertNotIn('type="file"', html)
        self.assertNotIn("multipart", html)
        with_files = board.add_ticket(["o/r"], "tok", [], "png up to 3 files")
        self.assertIn('enctype="multipart/form-data"', with_files)
        self.assertIn('type="file" name="file"', with_files)
        self.assertIn("png up to 3 files", with_files)
        self.assertEqual(board.add_ticket(["o/r"], "", []), "")
