"""The Settings home and the settings shown where they matter (factory/ui/features.py)."""
import unittest

from factory import db as dbm
from factory.config import load
from factory.ui import features as FT
from test_ui_admin import AdminCase

REPO = "your-org/standalone-service"


class Home(AdminCase):
    def cfg(self):
        return load(str(self.root / "config.toml"))

    def test_every_feature_is_listed_with_its_state_and_where_it_is_set(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/settings", cookie=cookie)
        self.assertEqual(s, 200)
        for f in FT.features(self.cfg()):
            self.assertIn(f'id="f-{f.key}"', html, f.key)
            self.assertIn(f'href="{f.href.replace("&", "&amp;")}"', html, f.key)
        self.assertIn('<a href="/settings" class=active aria-current=page>Overview</a>', html)
        self.assertIn('href="/settings?section=mockups"', html)                # a section the side list never linked to
        self.assertIn('name="key" value="local.enabled"', html)              # off, with its switch right there

    def test_filters_and_search_are_plain_links_and_forms(self):
        cookie, _ = self.session()
        html = self.req("GET", "/settings?show=off", cookie=cookie)[2]
        self.assertIn('id="f-local"', html)
        self.assertNotIn('id="f-github"', html)
        html = self.req("GET", "/settings?q=slack", cookie=cookie)[2]
        self.assertIn('id="f-slack"', html)
        self.assertNotIn('id="f-local"', html)
        html = self.req("GET", "/settings?q=%3Cb%3E", cookie=cookie)[2]
        self.assertIn("No setting matches", html)
        self.assertIn('value="&lt;b&gt;"', html)                              # escaped back into the box

    def test_a_switch_turns_a_feature_on_and_off_and_nothing_else(self):
        cookie, csrf = self.session()
        s, _, html = self.post(cookie, csrf, "/settings/feature", {"key": "local.enabled", "on": "1", "back": "/settings"})
        self.assertEqual(s, 200)
        self.assertIn("Local tickets turned on", html)
        self.assertTrue(self.cfg().local_enabled)
        self.assertTrue((self.state / "RESTART").exists())
        self.post(cookie, csrf, "/settings/feature", {"key": "review.enabled", "on": "1", "back": "/settings"})
        self.assertTrue(self.cfg().review.enabled)
        self.post(cookie, csrf, "/settings/feature", {"key": "local.enabled", "on": "0", "back": "/settings"})
        self.assertFalse(self.cfg().local_enabled)
        for key in ("general.dry_run", "runner.allow_hosts", "workers.enabled", "nope"):
            s, _, _ = self.post(cookie, csrf, "/settings/feature", {"key": key, "on": "0", "back": "/settings"})
            self.assertEqual(s, 400, key)
        self.assertTrue(self.cfg().dry_run)                                  # going live is never a quiet switch
        self.assertEqual(self.req("POST", "/settings/feature", "key=local.enabled&on=1", cookie=cookie)[0], 403)

    def test_a_switch_goes_back_only_to_a_page_of_ours(self):
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/settings/feature", {"key": "local.enabled", "on": "1", "back": "/tickets"})
        self.assertEqual((s, h["Location"]), (303, "/tickets"))
        self.assertIn("Local tickets turned on", self.req("GET", "/tickets", cookie=cookie)[2])
        s, _, _ = self.post(cookie, csrf, "/settings/feature", {"key": "local.enabled", "on": "0", "back": "https://evil.example/"})
        self.assertEqual(s, 200)                                             # unknown: rendered on the Settings home instead


class Strips(AdminCase):
    def test_tickets_says_where_tickets_live_and_offers_local_tickets(self):
        cookie, csrf = self.session()
        html = self.req("GET", "/tickets", cookie=cookie)[2]
        self.assertIn('id="tk-settings"', html)
        self.assertIn("GitHub · ", html)
        self.assertIn('href="/tickets?ask=local#tk-settings"', html)            # New ticket says local tickets are off
        self.assertNotIn('<details class="ft-switch" open', html)
        self.assertIn('<details class="ft-switch" open', self.req("GET", "/tickets?ask=local", cookie=cookie)[2])
        self.post(cookie, csrf, "/settings/feature", {"key": "local.enabled", "on": "1", "back": "/tickets"})
        html = self.req("GET", "/tickets", cookie=cookie)[2]
        self.assertIn("GitHub and the factory", html)
        self.assertIn('name="where"', html)
        self.assertNotIn("ask=local", html)

    def test_the_factory_shows_its_switches_and_changes_agents_at_once(self):
        cookie, csrf = self.session()
        html = self.req("GET", "/", cookie=cookie)[2]
        self.assertIn("The settings that shape the factory right now", html)
        self.assertIn('action="/settings/parallel"', html)
        self.assertIn('href="/settings#f-local"', html)                        # an off feature, linked to its card
        s, h, _ = self.post(cookie, csrf, "/settings/parallel", {"value": "3"})
        self.assertEqual((s, h["Location"]), (303, "/"))
        self.assertEqual(load(str(self.root / "config.toml")).runner.max_parallel, 3)
        for bad in ("9", "0", "x"):
            self.post(cookie, csrf, "/settings/parallel", {"value": bad})
            self.assertEqual(load(str(self.root / "config.toml")).runner.max_parallel, 3, bad)
        self.assertIn('name="runner.max_parallel"', self.req("GET", "/settings?section=runner", cookie=cookie)[2])

    def test_a_ticket_says_how_it_is_handled(self):
        cookie, _ = self.session()
        dbm.record(self.db, REPO, 7, "t1", "stage", "Agent: sonnet, effort medium (claude-code)\nclassified feature/high, confidence 0.90, by rules")
        html = self.req("GET", f"/ticket?repo={REPO}&n=7", cookie=cookie)[2]
        self.assertIn("How #7 is handled", html)
        route = load(str(self.root / "config.toml")).routes["high"]
        self.assertIn(f'<span class="mono">{route.model}</span>', html)
        self.assertIn("high <span class=\"muted\">(classifier)</span>", html)


class Unit(unittest.TestCase):
    def test_every_switch_names_a_boolean_setting_with_a_default(self):
        from factory.ui import settings as S
        for key in FT.SWITCHES:
            self.assertIsInstance(S.default_for(key), bool, key)


if __name__ == "__main__":
    unittest.main()
