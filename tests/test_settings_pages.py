"""The settings pages: every setting has a control, a section saves what it shows, and the menu and the home find them."""
import tomllib
import unittest

from factory.config import deep_merge, load, overrides_path
from factory.ui import features as FT
from factory.ui import forms
from factory.ui import scans as SN
from factory.ui import schedules as SC
from factory.ui import settings as S
from test_ui_admin import AdminCase

NEW = ("comments", "ticket_review", "design_links", "scanner", "chat", "health", "update_check")


class SettingsPages(AdminCase):
    def cfg(self):
        return load(str(self.root / "config.toml"))

    def overrides(self):
        p = overrides_path(str(self.root / "config.toml"))
        return tomllib.loads(p.read_text()) if p.exists() else {}

    def section_form(self, section, **over):
        """The section as a browser posts it: every field with the value it shows, then the changes in `over`."""
        path = str(self.root / "config.toml")
        eff = deep_merge(S.base_raw(path), S.overrides_raw(path))
        pairs = [("section", section)]
        for f in S.fields_for(section, eff):
            v = over.pop(f.key) if f.key in over else forms.effective_value(f, eff)
            if f.kind == "bool":
                if v not in (False, None, "", "0"):
                    pairs.append((f.key, "1"))
            elif f.kind == "checks":
                pairs += [(f.key, c) for c in v]
            else:
                pairs.append((f.key, str(v)))
        return pairs + list(over.items())

    def test_every_section_renders_with_a_breadcrumb_and_its_own_title(self):
        cookie, _ = self.session()
        for section, (title, _) in S.SECTIONS.items():
            if section == "update_check":                # its page is /updates (see test_merge_release)
                continue
            s, _, html = self.req("GET", f"/settings?section={section}", cookie=cookie)
            self.assertEqual(s, 200, section)
            self.assertIn('<nav class="crumb" aria-label="Breadcrumb"><a href="/settings">Settings</a>', html, section)
            self.assertIn(f"<h1>{title.replace('&', '&amp;')}</h1>", html, section)

    def test_an_unchanged_save_of_a_new_section_writes_nothing(self):
        cookie, csrf = self.session()
        for section in NEW:
            s, h, _ = self.post(cookie, csrf, "/settings/save", self.section_form(section))
            self.assertEqual(s, 303, section)
        self.assertEqual(self.overrides(), {})

    def test_settings_that_were_config_only_save_from_the_ui(self):
        cookie, csrf = self.session()
        saves = [("health", {"health.disk_warn_percent": "20", "health.heartbeat_url": "https://hc.example.com/ping/abc"}),
                 ("chat", {"chat.enabled": "1", "chat.max_per_hour": "5"}),
                 ("comments", {"comments.max_per_day": "7"}),
                 ("ticket_review", {"ticket_review.max_tickets": "25"}),
                 ("design_links", {"design_links.enabled": "1", "design_links.hosts": "claude.ai", "confirm__design_links.hosts": "1"}),
                 ("scanner", {"scanner.max_tickets": "9", "scanner.labels": "factory:analyze, priority: low"}),
                 ("update_check", {"updates.check": ""}),
                 ("ci", {"ci.queued_warn_minutes": "30"}),
                 ("runner", {"runner.thinking_tokens.high": "30000", "runner.render_previews": ""}),
                 ("general", {"subtasks.enabled": "1"})]
        for section, over in saves:
            s, h, html = self.post(cookie, csrf, "/settings/save", self.section_form(section, **over))
            self.assertEqual(s, 303, (section, html[-600:] if s != 303 else ""))
        c = self.cfg()
        self.assertEqual((c.health.disk_warn_percent, c.health.heartbeat_url), (20, "https://hc.example.com/ping/abc"))
        self.assertEqual((c.chat.enabled, c.chat.max_per_hour, c.comments.max_per_day, c.ticket_review.max_tickets), (True, 5, 7, 25))
        self.assertEqual((c.design_links.enabled, c.design_links.hosts), (True, ("claude.ai",)))
        self.assertEqual((c.scanner.max_tickets, tuple(c.scanner.labels)), (9, ("factory:analyze", "priority: low")))
        self.assertFalse(c.updates.check)
        self.assertEqual(c.ci.queued_warn_minutes, 30)
        self.assertEqual(c.runner.thinking_tokens, {"low": 2000, "medium": 8000, "high": 30000})
        self.assertFalse(c.runner.render_previews)
        self.assertTrue(c.subtasks.enabled)
        self.post(cookie, csrf, "/settings/save", self.section_form("scanner", **{"scanner.labels": ""}))
        self.assertIsNone(self.cfg().scanner.labels)                                       # empty: back to the default

    def test_bad_values_in_new_kinds_are_refused(self):
        cookie, csrf = self.session()
        for section, over in (("health", {"health.heartbeat_url": "http://plain.example.com"}), ("health", {"health.extra_disk_paths": "relative/path"}),
                              ("design_links", {"design_links.hosts": "not a host", "confirm__design_links.hosts": "1"}),
                              ("scanner", {"scanner.labels": ",".join(f"l{i}" for i in range(11))})):
            self.assertEqual(self.post(cookie, csrf, "/settings/save", self.section_form(section, **over))[0], 422, over)
        self.assertEqual(self.overrides(), {})

    def test_the_changed_marker_and_default_are_shown(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/settings/save", self.section_form("chat", **{"chat.max_per_hour": "5"}))
        html = self.req("GET", "/settings?section=chat", cookie=cookie)[2]
        row = html.split('id="s-chat.max_per_hour"')[1].split('class="ctl"')[0]
        self.assertIn("Changed", row)
        self.assertIn("Default: <code>20</code>", row)
        self.assertIn('data-for="chat.max_per_hour" data-value="20"', row)

    def test_the_menu_is_grouped_and_marks_what_is_off(self):
        cookie, _ = self.session()
        html = self.req("GET", "/settings?section=review", cookie=cookie)[2]
        side = html.split('<nav class="side"')[1].split("</nav>")[0]
        for group, _ in forms.SIDE_GROUPS:
            self.assertIn(f'<span class="side-h">{group}</span>', side)
        self.assertIn('<div class="side-g cur"><span class="side-h">Before a pull request is done</span>', side)
        self.assertIn('<span class="sdot off" aria-hidden="true"></span>Code review<span class="sstate off">Off</span>', side)
        for section in S.SECTIONS:                       # every section is reachable from the menu (Workers through its own page)
            want = 'href="/workers"' if section == "workers" else 'href="/updates"' if section == "update_check" else f'href="/settings?section={section}"'
            self.assertIn(want, side, section)

    def test_the_home_finds_single_settings_and_says_what_needs_you(self):
        cookie, _ = self.session()
        html = self.req("GET", "/settings?q=heartbeat", cookie=cookie)[2]
        self.assertIn("Heartbeat address", html)
        self.assertIn('href="/settings?section=health#s-health.heartbeat_url"', html)
        home = self.req("GET", "/settings", cookie=cookie)[2]
        self.assertIn('aria-label="Mode"', home)
        if any(f.kind == "setup" for f in FT.features(self.cfg())):
            self.assertIn("Needs your attention", home)

    def test_stage_agents_show_one_card_per_stage_and_post_every_field(self):
        cookie, csrf = self.session()
        html = self.req("GET", "/settings?section=roles", cookie=cookie)[2]
        for name in ("analyst", "designer", "architect"):
            self.assertIn(f'id="mx-{name}"', html)
        for f in S.fields_for("roles", {}):
            self.assertIn(f'name="{f.key}"', html, f.key)
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.section_form("roles"))[0], 303)
        self.assertEqual(self.overrides(), {})

    def test_telegram_keeps_the_address_of_this_ui(self):
        cookie, csrf = self.session()
        s, _, _ = self.post(cookie, csrf, "/telegram/save", {"telegram.chat_id": "0", "telegram.verbosity": "normal", "telegram.mode": "level",
                                                              "telegram.ui_url": "https://factory.example.internal/"})
        self.assertEqual(s, 303)
        self.assertEqual(self.cfg().telegram_ui_url, "https://factory.example.internal")
        self.assertIn('value="https://factory.example.internal"', self.req("GET", "/telegram", cookie=cookie)[2])


class ScheduleAndSmellForms(unittest.TestCase):
    def test_a_schedule_keeps_its_snapshot_and_data_limits_and_custom_labels(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(repos=["o/r"], roles=[SimpleNamespace(name="analyst", label="factory:analyze")],
                              runner=SimpleNamespace(claude_env_file="/tmp/x/claude.env"))
        v = SC.flat(None, cfg)
        v.update(name="weekly", url="https://example.com/data.json", stype="http", labels="custom", keep_labels="a, b", keep="5", max_data_chars="1000")
        e = SC.build(v, cfg, None, True, set())
        self.assertEqual((e["labels"], e["keep"], e["max_data_chars"]), (["a", "b"], 5, 1000))
        v.update(keep="-1")
        with self.assertRaises(S.SettingsError):
            SC.build(v, cfg, None, True, set())
        self.assertEqual(SC.flat({"labels": ["x", "y"], "keep": 3}, cfg)["labels"], "custom")

    def test_a_smell_has_a_title_and_description(self):
        v = {"id": "todo-left", "pattern": "TODO", "globs": "**/*.py", "advice": "", "sample": "", "preset": "", "title": "TODOs left behind",
             "description": "Unfinished work in shipped code."}
        e = SN.build_smell(v, None, set())
        self.assertEqual((e["name"], e["description"]), ("TODOs left behind", "Unfinished work in shipped code."))
        self.assertEqual(SN.build_smell({**v, "title": "", "description": ""}, None, set())["name"], "todo-left")


if __name__ == "__main__":
    unittest.main()
