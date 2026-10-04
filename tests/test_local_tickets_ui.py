"""Local tickets are run from the admin UI alone: turned on in Settings, created with New ticket, listed and opened on the
Tickets page, commented on, edited, closed and reopened there."""
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
    setUp = LocalTickets.setUp
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
