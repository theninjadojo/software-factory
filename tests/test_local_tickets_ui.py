"""Local tickets are run from the admin UI alone: turned on in Settings, created with New ticket, listed and opened on the
Tickets page, commented on, edited, closed and reopened there."""
from dataclasses import replace
from urllib.parse import quote

from factory import tracker
from factory.config import load
from factory.ui import board
from factory.ui import labels as L

from test_ui_admin import AdminCase

REPO = "your-org/standalone-service"


class LocalTickets(AdminCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + "\n[local]\nenabled = true\n")
        self.cookie, self.csrf = self.session()
        L._recent.clear()                           # the same title twice within seconds counts as a double click

    def cfg(self):
        return load(str(self.root / "config.toml"))

    def create(self, **over):
        return self.post(self.cookie, self.csrf, "/tickets/create", {"repo": REPO, "title": "Fix the  footer", "body": "It **wraps**.",
                                                                     "start": "", "where": "local", **over})

    def page(self, n):
        return self.req("GET", f"/ticket?repo={quote(REPO, safe='')}&n={n}", cookie=self.cookie)

    def store(self):
        return tracker.LocalTracker(self.db)

    def test_new_ticket_offers_local_and_creates_one_without_github(self):
        html = self.req("GET", "/tickets", cookie=self.cookie)[2]
        self.assertIn('name="where"', html)
        s, _, _ = self.create()
        self.assertEqual(s, 303)
        t = self.store().issue(REPO, tracker.LOCAL_BASE + 1)
        self.assertEqual((t["title"], t["body"], t["state"], t["labels"]), ("Fix the footer", "It **wraps**.", "open", []))

    def test_a_start_action_is_a_trusted_label(self):
        self.create(start="auto")
        n = tracker.LOCAL_BASE + 1
        self.assertEqual([x["name"] for x in self.store().issue(REPO, n)["labels"]], [self.cfg().auto_label])
        self.assertIn(self.store().label_actor(REPO, n, self.cfg().auto_label), tracker.TRUSTED_ACTORS)

    def test_an_unstarted_local_ticket_is_listed_as_not_started_and_opens_here(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        (row,) = [r for r in board.ticket_rows(self.db) if r["issue"] == n]
        self.assertEqual((row["state"], row["title"]), ("new", "Fix the footer"))
        html = self.req("GET", "/tickets?stage=new", cookie=self.cookie)[2]
        self.assertIn("standalone-service L-1", html)
        self.assertNotIn(f"#{n}", html)
        s, _, html = self.page(n)
        self.assertEqual(s, 200)
        self.assertIn("Local ticket", html)
        self.assertIn("<strong>wraps</strong>", html)
        self.assertNotIn("Open on GitHub", html)
        self.assertNotIn(f"github.com/{REPO}/issues/{n}", html)

    def test_an_open_ticket_the_factory_stopped_on_needs_a_person_and_is_not_shipped(self):
        # L-53, L-54, L-55 and L-64 on the VM: the chain stopped after the analyst and the board called them Shipped
        from factory import db as dbm
        self.create()
        n = tracker.LOCAL_BASE + 1
        dbm.finish_run(self.db, dbm.start_run(self.db, "stage", REPO, n, "Fix the footer", "claude-code", "sonnet", "medium", stage="analyst"),
                       "stage", "analyst document ready")
        (row,) = [r for r in board.ticket_rows(self.db) if r["issue"] == n]
        self.assertEqual(row["state"], "needs")
        self.assertEqual(row["why"], "Analyst finished; choose the next step")
        self.store().set_state(REPO, n, "closed")
        (row,) = [r for r in board.ticket_rows(self.db) if r["issue"] == n]
        self.assertEqual(row["state"], "done")                                    # closed is done

    def test_a_ticket_queued_for_a_free_agent_is_working_not_waiting_for_a_person(self):
        # L-11 and L-13 on the VM: answers put factory:architect back on, every agent was busy, and the ticket said
        # "Architect finished; choose the next step" with no buttons (its start label was already on)
        import json
        from factory import db as dbm
        self.create()
        n = tracker.LOCAL_BASE + 1
        dbm.finish_run(self.db, dbm.start_run(self.db, "stage", REPO, n, "Fix the footer", "claude-code", "opus", "high", stage="architect"),
                       "stage", "architect document ready")
        dbm.set_status(self.db, "pool", json.dumps({"max": 2, "draining": False,
                                                    "queued": [{"repo": REPO, "issue": n, "kind": "architect", "title": "Fix the footer"}]}))
        (row,) = [r for r in board.ticket_rows(self.db) if r["issue"] == n]
        self.assertEqual((row["state"], row["why"]), ("working", "Architect queued: waiting for a free agent"))
        self.assertEqual(board.row_for(self.db, REPO, n)["state"], "working")
        self.assertTrue(board.journey_line(row).startswith("Architect queued: waiting for a free agent"))
        self.assertIn("Architect queued: waiting for a free agent", self.page(n)[2])     # the page's own "queued" (a Move) is another key
        dbm.set_status(self.db, "pool", json.dumps({"max": 2, "draining": False, "queued": []}))
        (row,) = [r for r in board.ticket_rows(self.db) if r["issue"] == n]
        self.assertEqual(row["state"], "needs")                                   # the next poll started nothing: a person picks

    def test_comment_edit_close_and_reopen(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        back = f"/ticket?repo={quote(REPO, safe='')}&n={n}"
        f = {"repo": REPO, "n": str(n), "back": back}
        s, h, _ = self.post(self.cookie, self.csrf, "/tickets/local/comment", {**f, "body": "<script>x</script> more detail"})
        self.assertEqual((s, h["Location"]), (303, back))
        self.assertEqual(self.store().comments(REPO, n)[0]["user"]["login"], tracker.UI_ACTOR)
        html = self.page(n)[2]
        self.assertIn("You (admin UI)", html)
        self.assertNotIn("<script>x</script>", html)
        self.post(self.cookie, self.csrf, "/tickets/local/edit", {**f, "title": "Fix the footer on phones", "body": "Only below 400px."})
        self.assertEqual(self.store().issue(REPO, n)["title"], "Fix the footer on phones")
        self.assertEqual(self.post(self.cookie, self.csrf, "/tickets/local/edit", {**f, "title": " "})[0], 303)       # refused, flashed
        self.assertEqual(self.store().issue(REPO, n)["title"], "Fix the footer on phones")
        with self.no_github():
            self.post(self.cookie, self.csrf, "/tickets/close", f)
        self.assertEqual(self.store().issue(REPO, n)["state"], "closed")
        self.assertIn("Reopen", self.page(n)[2])
        self.post(self.cookie, self.csrf, "/tickets/local/state", {**f, "state": "open"})
        self.assertEqual(self.store().issue(REPO, n)["state"], "open")

    def no_github(self):
        from unittest import mock
        from factory.ui import integrations as I
        return mock.patch.object(I, "read_secret", return_value="ghp_" + "a" * 36)

    def test_actions_refuse_github_numbers_and_unknown_tickets(self):
        for n in ("12", str(tracker.LOCAL_BASE + 9)):
            self.post(self.cookie, self.csrf, "/tickets/local/comment", {"repo": REPO, "n": n, "body": "hi"})
        self.assertFalse(self.has_tables() and self.db.execute("SELECT COUNT(*) FROM local_comments").fetchone()[0])

    def has_tables(self):
        return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='local_comments'").fetchone())

    def test_local_tickets_off_means_github_only(self):
        p = self.root / "config.toml"
        p.write_text(p.read_text().replace("[local]\nenabled = true\n", ""))
        self.assertNotIn('name="where"', self.req("GET", "/tickets", cookie=self.cookie)[2])
        self.create()
        self.assertFalse(self.has_tables() and self.db.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0])


class Settings(AdminCase):
    def test_local_tracker_and_github_interval_are_in_general_settings(self):
        cookie, csrf = self.session()
        html = self.req("GET", "/settings?section=general", cookie=cookie)[2]
        self.assertIn('name="local.enabled"', html)
        self.assertIn('name="github.poll_seconds"', html)
        s, _, _ = self.post(cookie, csrf, "/settings/save", self.general_form(**{"local.enabled": "1", "github.poll_seconds": "300"}))
        self.assertEqual(s, 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.local_enabled, cfg.github_poll_seconds), (True, 300))
        self.post(cookie, csrf, "/settings/save", self.general_form(**{"local.enabled": "1", "github.poll_seconds": str(cfg.poll_seconds)}))
        self.assertNotIn("poll_seconds", self.overrides().get("github", {}))         # back to following the poll interval


class ImportAll(LocalTickets):
    def queued(self):
        return sorted(r[0] for r in self.db.execute("SELECT gh_number FROM import_requests"))

    def submit(self, gh, **f):
        from unittest import mock
        with mock.patch.object(L, "_gh", return_value=gh):
            return self.post(self.cookie, self.csrf, "/tickets/import", {"repo": REPO, **f})

    def test_every_open_issue_is_queued_in_batches_and_a_close_needs_confirmation(self):
        from unittest import mock
        gh = mock.MagicMock()
        total = tracker.MAX_BULK + 5
        gh.issues.return_value = ([{"number": n, "labels": []} for n in range(1, total + 1)], False)
        self.submit(gh, all="1", close="1")
        self.assertEqual(self.queued(), [])                          # not confirmed: nothing queued
        self.submit(gh, all="1", close="1", confirm="1")
        self.assertEqual(self.queued(), list(range(1, tracker.MAX_BULK + 1)))
        self.assertEqual(self.db.execute("SELECT MIN(close) FROM import_requests").fetchone()[0], 1)
        self.submit(gh, all="1")                                     # the next batch skips the queued ones
        self.assertEqual(self.queued(), list(range(1, total + 1)))

    def test_all_cannot_be_mixed_with_a_number_or_label_and_needs_a_valid_session(self):
        from unittest import mock
        gh = mock.MagicMock()
        gh.issues.return_value = ([{"number": 1, "labels": []}], False)
        self.submit(gh, all="1", n="3")
        self.submit(gh, all="1", label="bug")
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.req("POST", "/tickets/import", "repo=" + quote(REPO, safe="") + "&all=1", cookie=self.cookie)[0], 403)
        self.assertEqual(self.queued(), [])


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


class Attachments(AdminCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + "\n[local]\nenabled = true\n")
        self.cookie, self.csrf = self.session()
        L._recent.clear()

    cfg = LocalTickets.cfg
    create = LocalTickets.create
    page = LocalTickets.page
    store = LocalTickets.store

    def upload(self, path, fields, files, csrf=None, token="XBOUNDARYX"):
        body = b""
        for k, v in {"csrf": self.csrf if csrf is None else csrf, **fields}.items():
            body += f'--{token}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for name, data in files:
            body += f'--{token}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: x/y\r\n\r\n'.encode() + data + b"\r\n"
        body += f"--{token}--\r\n".encode()
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=body, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": self.cookie,
                                                    "Content-Type": f"multipart/form-data; boundary={token}"})
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def new(self, files, **over):
        return self.upload("/tickets/create", {"repo": REPO, "title": "With files", "body": "see", "start": "", "where": "local", **over}, files)

    def test_vetting_allows_listed_types_with_matching_content_only(self):
        self.assertEqual(tracker.vet_attachment("../../a b.PNG", PNG), ("a_b.PNG", "image/png"))
        self.assertEqual(tracker.vet_attachment("notes.md", "é".encode()), ("notes.md", "text/plain"))
        for name, data in (("x.svg", b"<svg/>"), ("x.html", b"<b>"), ("x.png", b"not a png"), ("x.txt", b"\xff\xfe"), ("x.pdf", b""),
                           ("noext", b"a"), ("x.png", None), ("x.txt", b"a" * (tracker.ATTACH_DEFAULTS[0] + 1))):
            with self.assertRaises(ValueError, msg=name):
                tracker.vet_attachment(name, data)
        safe, _ = tracker.vet_attachment("<script>\"x\".txt", b"a")
        self.assertRegex(safe, r"^[A-Za-z0-9._-]+$")
        self.assertLessEqual(len(tracker.vet_attachment("a" * 300 + ".txt", b"a")[0]), 80)

    def test_a_ticket_is_created_with_its_files_and_they_download_safely(self):
        s, _, _ = self.new([("shot.png", PNG), ("log.txt", b"boom")])
        self.assertEqual(s, 303)
        n = tracker.LOCAL_BASE + 1
        items = self.store().attachments(REPO, n)
        self.assertEqual([(a["name"], a["mime"]) for a in items], [("shot.png", "image/png"), ("log.txt", "text/plain")])
        s, h, body = self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={n}&id={items[1]['id']}", cookie=self.cookie)
        self.assertEqual((s, body, h["Content-Type"], h["X-Content-Type-Options"]), (200, "boom", "text/plain", "nosniff"))
        self.assertEqual(h["Content-Disposition"], 'attachment; filename="log.txt"')
        self.assertIn("shot.png", self.page(n)[2])
        self.assertEqual(self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={n}&id=999", cookie=self.cookie)[0], 404)
        self.assertEqual(self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={n}&id=1")[0], 303)    # no session

    def test_a_refused_file_creates_nothing(self):
        for files in ([("a.svg", b"<svg/>")], [("a.png", b"nope")], [(f"{i}.txt", b"x") for i in range(6)]):
            s, _, _ = self.new(files)
            self.assertEqual(s, 303)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 0)

    def test_files_need_a_local_ticket_and_a_csrf_token(self):
        self.new([("a.txt", b"x")], where="github")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM local_tickets").fetchone()[0], 0)
        self.assertEqual(self.upload("/tickets/create", {"repo": REPO, "title": "t"}, [], csrf="bad")[0], 403)

    def test_attach_and_remove_on_an_existing_ticket(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        self.assertEqual(self.upload("/tickets/local/attach", {"repo": REPO, "n": str(n)}, [("a.txt", b"x")])[0], 303)
        (a,) = self.store().attachments(REPO, n)
        self.assertEqual(self.post(self.cookie, self.csrf, "/tickets/local/attachment/delete", {"repo": REPO, "n": str(n), "id": str(a["id"])})[0], 303)
        self.assertEqual(self.store().attachments(REPO, n), [])


class GithubIssuesOff(LocalTickets):
    """With github.issues_enabled off, the UI lists only local tickets (the database rows stay, and come back when it is on)."""

    def test_ticket_rows_hide_github_tickets_only_when_asked(self):
        self.create()
        self.db.execute("INSERT INTO decisions (repo, issue, updated_at, outcome, detail, decided_at) VALUES (?, 5, 'x', 'skip', 'seen', 1)", (REPO,))
        self.db.commit()
        local = tracker.LOCAL_BASE + 1
        self.assertEqual({r["issue"] for r in board.ticket_rows(self.db)}, {5, local})
        self.assertEqual({r["issue"] for r in board.ticket_rows(self.db, github=False)}, {local})

    def test_needs_you_makes_no_github_call_when_off(self):
        class H:
            class app:
                @staticmethod
                def cfg():
                    return replace(load(str(self.root / "config.toml")), github_issues_enabled=False)
        self.assertIsNone(L.needs_you(H()))


class Board(LocalTickets):
    """The Board view: cards in state columns, moves offered from the existing routes, and local Start/Close without a token."""

    def board(self):
        return self.req("GET", "/tickets?view=board", cookie=self.cookie)

    def test_a_card_sits_in_its_state_column_with_a_move_menu(self):
        self.create()
        s, _, html = self.board()
        self.assertEqual(s, 200)
        self.assertIn('class="kb-col" data-col="new"', html)
        self.assertIn('action="/tickets/start"', html)
        self.assertIn('action="/tickets/close"', html)
        for action in ["auto"] + [r.name for r in self.cfg().roles]:                # Auto and every stage, each with its own confirmation
            self.assertIn(f'name="action" value="{action}"', html)
        self.assertIn("Start with Auto", html)

    def test_the_board_escapes_titles_and_unknown_views_fall_back_to_the_list(self):
        self.create(title="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", self.board()[2])
        self.assertNotIn("kb-board", self.req("GET", "/tickets?view=x", cookie=self.cookie)[2])

    def test_a_local_ticket_starts_and_closes_without_a_github_token(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        f = {"repo": REPO, "n": str(n), "back": "/tickets?view=board"}
        s, h, _ = self.post(self.cookie, self.csrf, "/tickets/start", {**f, "action": "auto"})
        self.assertEqual((s, h["Location"]), (303, "/tickets?view=board"))
        self.assertEqual([x["name"] for x in self.store().issue(REPO, n)["labels"]], [self.cfg().auto_label])
        self.assertIn(self.store().label_actor(REPO, n, self.cfg().auto_label), tracker.TRUSTED_ACTORS)
        self.post(self.cookie, self.csrf, "/tickets/close", f)
        self.assertEqual(self.store().issue(REPO, n)["state"], "closed")

    def test_a_ticket_goes_back_to_a_stage_that_ran_and_comes_back_to_the_same_place(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        role = self.cfg().roles[0]
        back = f"/ticket?repo={quote(REPO, safe='')}&n={n}&stage=prs&q=address&project=Shop"
        f = {"repo": REPO, "n": str(n), "back": back, "stage": role.name}
        s, h, _ = self.post(self.cookie, self.csrf, "/tickets/send-back", f)
        self.assertEqual((s, h["Location"]), (303, back))                       # the page's filters and search come back with it
        self.assertEqual(self.db.execute("SELECT action FROM approvals").fetchall(), [])     # it has not run there: nothing queued
        self.assertIn("has not run on this ticket yet", self.req("GET", back, cookie=self.cookie)[2])
        self.store().add_labels(REPO, n, [role.done_label])
        self.post(self.cookie, self.csrf, "/tickets/send-back", {**f, "stage": "nope"})
        self.assertEqual(self.db.execute("SELECT action FROM approvals").fetchall(), [])     # only a configured stage
        s, h, _ = self.post(self.cookie, self.csrf, "/tickets/send-back", f)
        self.assertEqual(self.db.execute("SELECT action FROM approvals").fetchall(), [(f"redirect:{role.name}",)])
        self.assertIn(f"goes back to the {role.name} stage", self.req("GET", back, cookie=self.cookie)[2])
        self.post(self.cookie, self.csrf, "/tickets/send-back", f)              # a second one waits for the first
        self.assertEqual(len(self.db.execute("SELECT action FROM approvals").fetchall()), 1)
        s, h, _ = self.post(self.cookie, self.csrf, "/tickets/send-back", {**f, "back": "https://evil.example/"})
        self.assertEqual(h["Location"], "/tickets")                              # never anywhere else

    def test_build_is_never_started_from_the_board_and_github_numbers_still_need_a_token(self):
        self.create()
        n = tracker.LOCAL_BASE + 1
        self.assertNotIn('name="action" value="build"', self.board()[2])
        self.post(self.cookie, self.csrf, "/tickets/start", {"repo": REPO, "n": str(n), "action": "bogus"})
        self.assertEqual(self.store().issue(REPO, n)["labels"], [])
        self.post(self.cookie, self.csrf, "/tickets/close", {"repo": REPO, "n": "12"})          # a GitHub number, no token: nothing happens
        self.assertEqual(self.store().issue(REPO, n)["state"], "open")

    def test_moves_are_decided_from_the_state(self):
        from factory.ui import kanban
        row = {"state": "new", "issue": tracker.LOCAL_BASE + 1, "closed": False, "prs": [], "journey": {"steps": []}}
        m = kanban.moves_for(row, False)
        self.assertEqual((m["working"][0], m["done"][0], m["failed"][0]), ("start", "close", ""))
        gh = {**row, "issue": 12, "state": "working"}
        self.assertEqual(kanban.moves_for(gh, False)["done"], ("", kanban.NEEDS_TOKEN))
        self.assertEqual(kanban.moves_for(gh, True)["done"][0], "close")
        ran = {**row, "state": "done", "closed": True, "journey": {"steps": [1]}}
        self.assertEqual((kanban.moves_for(ran, False)["new"][0], kanban.moves_for(ran, False)["done"][0]), ("", "reopen"))

    def test_starts_offer_auto_and_the_stages_not_done_and_rerun_a_failed_ticket(self):
        from factory.ui import kanban
        cfg = self.cfg()
        names = [r.name for r in cfg.roles]
        st = {sid: "none" for sid, _ in board.STATIONS}
        new = {"state": "new", "issue": tracker.LOCAL_BASE + 1, "stations": st}
        note, starts = kanban.starts_for(new, cfg, False)
        self.assertEqual((note, [a for a, _, _, why in starts if not why]), ("", ["auto", *names]))
        failed = {**new, "state": "failed", "stations": {**st, names[0]: "done", "review": "fail"}}
        offered = {a: why for a, _, _, why in kanban.starts_for(failed, cfg, False)[1]}
        self.assertEqual((offered[f"redirect:{names[0]}"], offered["build"]), ("", ""))   # a finished stage: send it back there
        self.assertNotIn(names[0], offered)                                               # ...not start it again from scratch
        ask, button = kanban._ask(f"redirect:{names[0]}", "", "L-1")
        self.assertIn("every stage after it run again", ask)
        self.assertEqual(button, f"Send back to {names[0].capitalize()}")
        form = kanban.start_form(f"redirect:{names[0]}", {"repo": "o/r", "issue": 5}, "tok", "/tickets", button)
        self.assertIn('action="/tickets/send-back"', form)
        self.assertIn(f'name="stage" value="{names[0]}"', form)
        picker = kanban.stage_picker({**failed, "repo": "o/r"}, cfg, "tok", "/tickets", False)
        self.assertIn(f'data-sta="{names[0]}"', picker)                       # the stage that ran: send it back there
        self.assertIn('data-sta="build"', picker)                              # build again
        self.assertIn('class="sd-sta off none" aria-disabled="true"><span class="mono sd-stn">01</span><strong>Poll</strong>', picker)
        self.assertEqual(picker.count('<details class="sd-sta'), len(names) + 2)   # each stage, Auto (at Route) and Build
        self.assertEqual(kanban.stage_picker({**failed, "repo": "o/r"}, cfg, "", "/tickets", False), "")   # no session, no picker
        running = {**new, "state": "working", "stations": {**st, "build": "run"}}
        self.assertEqual(kanban.starts_for(running, cfg, False), (kanban.RUNNING, []))
        self.assertIn(kanban.RUNNING, kanban.stage_picker({**running, "repo": "o/r"}, cfg, "tok", "/tickets", True))
        self.assertNotIn("<details", kanban.stage_picker({**running, "repo": "o/r"}, cfg, "tok", "/tickets", True))
        self.assertEqual(kanban.starts_for({**new, "issue": 12}, cfg, False), (kanban.NEEDS_TOKEN, []))
        self.assertIn(kanban.BLOCKED.strip(), kanban._ask("auto", "Auto", "L-1", pm=True)[0])          # the PM may hold a build
        self.assertNotIn(kanban.BLOCKED.strip(), kanban._ask("analyst", "Analyze", "L-1", pm=True)[0])

    def test_done_cards_leave_the_board_after_the_configured_days(self):
        from factory.ui import kanban
        now = 1_000_000_000.0
        rows = [{"when": now - 3600}, {"when": now - 8 * 86400}, {"when": 0}]
        keep, old = kanban.recent(rows, 7, now)
        self.assertEqual((keep, old), ([rows[0], rows[2]], 1))                       # no known activity time: it stays
        self.assertEqual(self.cfg().board_done_days, 7)
        n = tracker.LOCAL_BASE + 1
        self.create()
        self.post(self.cookie, self.csrf, "/tickets/close", {"repo": REPO, "n": str(n)})
        self.assertIn("Done · last 7 days", self.board()[2])

class PriorityAndRoadmap(LocalTickets):
    def test_a_pinned_priority_sticks_and_the_roadmap_shows_it(self):
        from factory import plan, pm
        from test_pm import PM
        self.create()
        n = tracker.LOCAL_BASE + 1
        f = {"repo": REPO, "n": str(n), "back": f"/ticket?repo={quote(REPO, safe='')}&n={n}"}
        self.assertEqual(self.post(self.cookie, self.csrf, "/tickets/priority", {**f, "priority": "high"})[0], 303)
        self.assertEqual([x["name"] for x in self.store().issue(REPO, n)["labels"]], ["priority: high"])
        html = self.page(n)[2]
        self.assertIn("Pinned by you · High", html)
        conn = self.db
        hub = tracker.Hub(None, self.cfg().db_path)
        pm.apply(PM, hub, conn, REPO, {n: {}}, [pm.Assessment(n, "low", (), "")], 1)
        self.assertEqual([x["name"] for x in self.store().issue(REPO, n)["labels"]], ["priority: high"])   # the PM left it alone
        self.post(self.cookie, self.csrf, "/tickets/priority", {**f, "priority": "pm"})
        self.assertIsNone(plan.pin(conn, REPO, n))
        self.post(self.cookie, self.csrf, "/roadmap/milestones", {"repo": REPO, "op": "add", "name": "Checkout"})
        self.post(self.cookie, self.csrf, "/tickets/milestone", {**f, "milestone": "Checkout"})
        s, _, html = self.req("GET", f"/roadmap?repo={quote(REPO, safe='')}", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertIn("Checkout", html)
        s, _, html = self.req("GET", f"/roadmap?repo={quote(REPO, safe='')}&milestone=Checkout", cookie=self.cookie)
        self.assertIn("Fix the footer", html)
        back = f"/roadmap?project={quote(REPO, safe='')}&milestone=Checkout"
        self.assertEqual(self.post(self.cookie, self.csrf, "/roadmap/feature", {"project": REPO, "repo": REPO, "n": str(n), "short": "Tidy footer",
                                                                                "category": "Website", "back": back})[0], 303)
        self.assertEqual(plan.features(conn, [REPO])[(REPO, n)], {"short": "Tidy footer", "category": "Website", "short_by": "person", "category_by": "person"})
        pm.apply_features(conn, {(REPO, n): {"short": "Footer fix", "category": "Site"}})          # a person's wording sticks
        self.assertEqual(plan.features(conn, [REPO])[(REPO, n)]["short"], "Tidy footer")
        s, _, html = self.req("GET", back, cookie=self.cookie)
        self.assertIn("Tidy footer", html)
        self.assertIn("Fix the footer", html)                                                    # the full title, when opened
        self.assertEqual(self.post(self.cookie, "bad", "/tickets/priority", {**f, "priority": "high"})[0], 403)
