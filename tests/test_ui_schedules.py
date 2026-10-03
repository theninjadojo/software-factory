import re
import time
from unittest import mock

from factory import schedules as sc
from factory.config import load
from test_ui_admin import AdminCase

REPO = "your-org/standalone-service"
FORM = {"name": "umami-weekly", "orig": "", "repo": REPO, "when": "every", "every": "7d", "cron": "0 9 * * 1", "stype": "umami",
        "base_url": "https://api.umami.is/v1", "auth": "api-key", "website_id": "site-1", "days": "7", "url": "", "header": "Authorization",
        "prefix": "Bearer ", "title": "Weekly ${date}", "instructions": "", "labels": "auto", "skip_if_open": "1", "enabled": "1", "key": ""}


def fields(**over):
    f = {**FORM, **over}
    return list(f.items()) + [("metric", m) for m in ("path", "referrer")]


class Schedules(AdminCase):
    def setUp(self):
        super().setUp()
        self.cookie, self.csrf = self.session()

    def save(self, **over):
        return self.post(self.cookie, self.csrf, "/schedules/save", fields(**over))

    def test_empty_list_and_form_render(self):
        s, _, html = self.req("GET", "/schedules", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertIn("No schedules yet", html)
        s, _, html = self.req("GET", "/schedules/edit", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertIn('name="name"', html)
        self.assertIn("Test fetch", html)

    def test_save_writes_overrides_credential_and_restart(self):
        s, h, _ = self.save(key="umami-secret-key-123")
        self.assertEqual((s, h["Location"]), (303, "/schedules/view?name=umami-weekly&ok=sched_saved"))
        (entry,) = self.overrides()["schedules"]
        self.assertEqual((entry["name"], entry["every"], entry["source"]["type"], entry["source"]["metrics"]), ("umami-weekly", "7d", "umami", ["path", "referrer"]))
        self.assertNotIn("labels", entry)
        path = self.root / "secrets" / "schedule-umami-weekly"
        self.assertEqual(entry["source"]["token_file"], str(path))
        self.assertEqual(path.read_text(), "umami-secret-key-123")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertTrue((self.state / "RESTART").exists())
        self.assertEqual(load(str(self.root / "config.toml")).schedules[0].name, "umami-weekly")
        _, _, html = self.req("GET", "/schedules/view?name=umami-weekly", cookie=self.cookie)
        self.assertNotIn("umami-secret-key-123", html)
        self.assertIn("saved", html)

    def test_validation_errors_keep_the_form(self):
        for over, text in (({"every": "soon"}, "Interval"), ({"name": "Bad Name"}, "Name"), ({"repo": "o/other"}, "Repository"),
                           ({"base_url": "http://x"}, "https"), ({"key": "short"}, "API key"), ({"when": "cron", "cron": "99 * * * *"}, "Cron")):
            s, _, html = self.save(**over)
            self.assertEqual(s, 422, over)
            self.assertIn(text, html)
            self.assertIn('name="name"', html)
        self.assertEqual(self.overrides().get("schedules"), None)

    def test_edit_keeps_unowned_keys_labels_and_delete(self):
        self.save()
        s, _, _ = self.save(orig="umami-weekly", name="ignored", labels="analyze", **{"every": "2d"})
        self.assertEqual(s, 303)
        (entry,) = self.overrides()["schedules"]
        self.assertEqual((entry["name"], entry["every"], entry["labels"]), ("umami-weekly", "2d", ["factory:analyze"]))
        self.assertEqual(self.post(self.cookie, self.csrf, "/schedules/delete", {"name": "umami-weekly"})[0], 400)
        s, h, _ = self.post(self.cookie, self.csrf, "/schedules/delete", {"name": "umami-weekly", "confirm": "1"})
        self.assertEqual((s, h["Location"]), (303, "/schedules?ok=sched_deleted"))
        self.assertIsNone(self.overrides().get("schedules"))

    def test_duplicate_name_refused(self):
        self.save()
        self.assertEqual(self.save()[0], 422)

    def test_run_now_queues_a_request_for_the_orchestrator(self):
        self.save()
        self.assertEqual(self.post(self.cookie, self.csrf, "/schedules/run", {"name": "nope"})[0], 404)
        s, h, _ = self.post(self.cookie, self.csrf, "/schedules/run", {"name": "umami-weekly"})
        self.assertEqual((s, h["Location"]), (303, "/schedules?ok=run"))
        self.assertEqual(sc.requested(self.db), {"umami-weekly"})
        _, _, html = self.req("GET", "/schedules", cookie=self.cookie)
        self.assertIn("queued", html)

    def test_detail_shows_history_and_escapes_untrusted_text(self):
        self.save()
        sc.record(self.db, "umami-weekly", time.time() - 100, "ok", "opened #7 <script>alert(1)</script>", 7, "x.json")
        snap = self.state / "schedules" / "umami-weekly"
        snap.mkdir(parents=True)
        (snap / "20260101T000000Z.json").write_text('{"referrer": "<img src=x onerror=alert(1)>"}')
        _, _, html = self.req("GET", "/schedules/view?name=umami-weekly", cookie=self.cookie)
        self.assertIn("History", html)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)
        self.assertEqual(self.req("GET", "/schedules/view?name=nope", cookie=self.cookie)[0], 404)

    def test_test_fetch_shows_data_or_error_and_saves_nothing(self):
        with mock.patch("factory.schedules.SOURCES", {"umami": lambda src, now: {"stats": {"pageviews": 5}}, "http": None}):
            s, _, html = self.post(self.cookie, self.csrf, "/schedules/test", fields())
        self.assertEqual(s, 200)
        self.assertIn("pageviews", html)
        with mock.patch("factory.schedules.SOURCES", {"umami": mock.Mock(side_effect=OSError("down")), "http": None}):
            _, _, html = self.post(self.cookie, self.csrf, "/schedules/test", fields())
        self.assertIn("OSError: down", html)
        self.assertEqual(self.overrides().get("schedules"), None)

    def test_requires_login_and_csrf(self):
        self.assertEqual(self.req("GET", "/schedules")[0], 303)
        self.assertEqual(self.req("POST", "/schedules/run", "name=x", cookie=self.cookie)[0], 403)
