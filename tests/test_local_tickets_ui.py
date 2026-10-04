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
