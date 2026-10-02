import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlencode

from factory import db as dbm
from factory.config import load, overrides_path
from factory.ui import integrations as I
from factory.ui import settings as S
from test_ui import PASSWORD, UiCase

# Built from pieces so no token-shaped literal exists in the source (secret scanners match on shape).
TOKEN = "ghp" + "_" + "SECRETVALUE1234567890abcdef"
APIKEY = "sk" + "-ant-" + "b" * 12
TG_TOKEN = "123456789" + ":" + "ABCdefGhiJKlmnoPQRstuVWXyz012345678"


class AdminCase(UiCase):
    def post(self, cookie, csrf, path, fields=None):
        pairs = [("csrf", csrf)] + (list(fields.items()) if isinstance(fields, dict) else list(fields or []))
        return self.req("POST", path, urlencode(pairs), cookie=cookie)

    def general_form(self, **over):
        cfg = load(str(self.root / "config.toml"))
        f = {"section": "general", "general.poll_seconds": str(cfg.poll_seconds), "general.confidence_threshold": str(cfg.confidence_threshold),
             "github.trigger_label": cfg.trigger_label, "github.repos": "\n".join(["your-org/standalone-service"])}
        f.update(over)
        pairs = list(f.items()) + [("github.trusted_permissions", p) for p in ("admin", "maintain", "write")]
        if f.pop("dry_run_on", True) is not False:
            pairs.append(("general.dry_run", "1"))
        return pairs

    def overrides(self):
        p = overrides_path(str(self.root / "config.toml"))
        return tomllib.loads(p.read_text()) if p.exists() else {}


class Settings(AdminCase):
    def test_every_section_renders_with_current_values(self):
        cookie, _ = self.session()
        for section in ("general", "routing", "roles", "projects", "classifier", "runner", "ci"):
            s, _, html = self.req("GET", f"/settings?section={section}", cookie=cookie)
            self.assertEqual(s, 200, section)
        _, _, general = self.req("GET", "/settings?section=general", cookie=cookie)
        self.assertIn('value="60"', general)
        self.assertEqual(self.req("GET", "/settings?section=nope", cookie=cookie)[0], 404)

    def test_saving_writes_overrides_only_and_requests_a_restart(self):
        cookie, csrf = self.session()
        base_before = (self.root / "config.toml").read_text()
        s, h, _ = self.post(cookie, csrf, "/settings/save", self.general_form(**{"general.poll_seconds": "30"}))
        self.assertEqual((s, h["Location"]), (303, "/settings?section=general&ok=saved"))
        self.assertEqual(self.overrides()["general"]["poll_seconds"], 30)
        self.assertEqual((self.root / "config.toml").read_text(), base_before)            # hand-written file untouched
        self.assertTrue((self.state / "RESTART").exists())
        self.assertEqual(load(str(self.root / "config.toml")).poll_seconds, 30)
        self.assertIn("applies it at its next idle", self.req("GET", h["Location"], cookie=cookie)[2])

    def test_unchanged_values_leave_no_override_and_a_second_save_keeps_a_backup(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/settings/save", self.general_form())
        self.assertEqual(self.overrides(), {})                                            # equal to the file: nothing stored
        self.post(cookie, csrf, "/settings/save", self.general_form(**{"general.poll_seconds": "45"}))
        self.post(cookie, csrf, "/settings/save", self.general_form(**{"general.poll_seconds": "46"}))
        self.assertTrue(overrides_path(str(self.root / "config.toml")).with_suffix(".toml.bak").exists())

    def test_invalid_values_are_rejected_with_the_form_kept(self):
        cookie, csrf = self.session()
        for bad in ("abc", "1", "99999", ""):
            s, _, html = self.post(cookie, csrf, "/settings/save", self.general_form(**{"general.poll_seconds": bad}))
            self.assertEqual(s, 422, bad)
            self.assertIn("Poll interval (seconds): must be a whole number", html)
        self.assertEqual(self.overrides(), {})
        self.assertFalse((self.state / "RESTART").exists())
        _, _, html = self.post(cookie, csrf, "/settings/save", self.general_form(**{"general.poll_seconds": "7x"}))
        self.assertIn('value="7x"', html)                                                 # what you typed is still there

    def test_going_live_needs_an_explicit_confirmation(self):
        cookie, csrf = self.session()
        live = self.general_form(dry_run_on=False)
        s, _, html = self.post(cookie, csrf, "/settings/save", live)
        self.assertEqual(s, 422)
        self.assertIn("tick the confirmation box", html)
        self.assertNotIn("dry_run", self.overrides().get("general", {}))
        s, _, _ = self.post(cookie, csrf, "/settings/save", live + [("confirm__general.dry_run", "1")])
        self.assertEqual(s, 303)
        self.assertFalse(load(str(self.root / "config.toml")).dry_run is True)
        self.post(cookie, csrf, "/settings/save", self.general_form())                    # back to dry-run needs no confirmation
        self.assertTrue(load(str(self.root / "config.toml")).dry_run)

    def test_widening_the_sandbox_allowlist_needs_confirmation_and_valid_hosts(self):
        cookie, csrf = self.session()
        form = lambda hosts, **extra: [("section", "runner"), ("runner.timeout_seconds", "1800"), ("runner.max_turns", "40"),
                                       ("runner.rate_limit_backoff_seconds", "3600"), ("runner.memory", "3g"), ("runner.cpus", "2"),
                                       ("runner.allow_hosts", hosts)] + list(extra.items())
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form("api.anthropic.com\nregistry.npmjs.org"))[0], 422)   # unconfirmed
        for bad in ("evil", "a b.com", "127.0.0.1", "https://x.com", ""):
            self.assertEqual(self.post(cookie, csrf, "/settings/save", form(bad, **{"confirm__runner.allow_hosts": "1"}))[0], 422, bad)
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form("api.anthropic.com\nregistry.npmjs.org", **{"confirm__runner.allow_hosts": "1"}))[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).runner.allow_hosts, ("api.anthropic.com", "registry.npmjs.org"))
        self.assertEqual(self.post(cookie, csrf, "/settings/save", [("section", "runner"), ("runner.memory", "3 gigs")])[0], 422)

    def test_projects_edit_add_remove_and_validation(self):
        cookie, csrf = self.session()
        proj = {"p0_name": "shop", "p0_desc": "A shop", "p0_r0_repo": "your-org/shop-web", "p0_r0_role": "web", "p0_r1_repo": "your-org/shop-api", "p0_r1_role": "api"}
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", proj)[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual([r.repo for r in cfg.projects[0].repos], ["your-org/shop-web", "your-org/shop-api"])
        self.assertIn("your-org/shop-api", cfg.repos)
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", {**proj, "p0_r1_repo": "not a repo"})[0], 422)
        two = {**proj, "p1_name": "other", "p1_r0_repo": "your-org/shop-web", "p1_r0_role": "dup"}
        s, _, html = self.post(cookie, csrf, "/settings/projects", two)
        self.assertEqual(s, 422)
        self.assertIn("is in two projects", html)
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", {"p0_name": "", "p0_r0_repo": ""})[0], 303)       # empty = remove all
        self.assertEqual(load(str(self.root / "config.toml")).projects, ())

    def test_alias_rules_and_classifier_tester(self):
        cookie, csrf = self.session()
        form = [("section", "classifier"), ("classifier.backend", "rules"), ("classifier.model", "typesafe/jev-1.13"),
                ("classifier.kind_aliases", "story=feature\n<b>x</b>=bug")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).kind_aliases["story"], "feature")
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form[:-1] + [("classifier.kind_aliases", "story=rm -rf")])[0], 422)
        s, _, html = self.post(cookie, csrf, "/classify/test", {"title": "Add a thing", "body": "please", "labels": "story, complexity:high"})
        self.assertEqual(s, 200)
        for needle in ("labels only", "feature", "high → opus"):
            self.assertIn(needle, html)
        self.assertNotIn("<b>x</b>", self.req("GET", "/settings?section=classifier", cookie=cookie)[2])         # escaped on re-render

    def test_all_new_routes_need_a_session_and_csrf(self):
        for path in ("/settings", "/credentials", "/telegram"):
            self.assertEqual(self.req("GET", path)[0], 303, path)
        cookie, csrf = self.session()
        for path in ("/settings/save", "/settings/projects", "/classify/test", "/credentials/save", "/credentials/test",
                     "/telegram/save", "/telegram/detect", "/telegram/use", "/telegram/test"):
            self.assertEqual(self.req("POST", path, "x=1", cookie=cookie)[0], 403, path)


class Credentials(AdminCase):
    def test_secrets_are_stored_0600_and_never_shown_again(self):
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/credentials/save", {"name": "github", "value": TOKEN})
        self.assertEqual((s, h["Location"]), (303, "/credentials?ok=secret"))
        path = self.root / "secrets" / "github_token"
        self.assertEqual((path.read_text(), oct(path.stat().st_mode & 0o777)), (TOKEN, "0o600"))
        self.assertTrue((self.state / "RESTART").exists())
        for p in ("/credentials", "/", "/settings?section=general", "/telegram", "/events", "/runs", "/fragment/overview"):
            self.assertNotIn(TOKEN, self.req("GET", p, cookie=cookie)[2], p)
        creds = self.req("GET", "/credentials", cookie=cookie)[2]
        self.assertIn("set", creds)
        self.assertNotIn("SECRETVALUE", creds)

    def test_claude_credential_is_exactly_one_variable_and_replacing_switches_kind(self):
        cookie, csrf = self.session()
        env = self.root / "secrets" / "claude.env"
        self.post(cookie, csrf, "/credentials/save", {"name": "claude", "kind": "subscription", "value": "oat-aaaaaaaaaaaa"})
        self.assertEqual(env.read_text(), "CLAUDE_CODE_OAUTH_TOKEN=oat-aaaaaaaaaaaa\n")
        self.post(cookie, csrf, "/credentials/save", {"name": "claude", "kind": "apikey", "value": APIKEY})
        self.assertEqual(env.read_text(), f"ANTHROPIC_API_KEY={APIKEY}\n")
        self.assertIn("ANTHROPIC_API_KEY", self.req("GET", "/credentials", cookie=cookie)[2])       # shows the variable NAME only
        self.assertNotIn("bbbbbbbbbbbb", self.req("GET", "/credentials", cookie=cookie)[2])

    def test_bad_values_and_unknown_names_are_rejected(self):
        cookie, csrf = self.session()
        for bad in ("short", "has space in it 12345", "line\nbreak12345", ""):
            self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "github", "value": bad})[0], 400, repr(bad))
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "../../etc/passwd", "value": TOKEN})[0], 400)
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "claude", "kind": "nope", "value": TOKEN})[0], 400)
        self.assertFalse((self.root / "secrets" / "github_token").exists())

    def test_github_test_reports_access_without_leaking_the_token(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/credentials/save", {"name": "github", "value": TOKEN})
        def fake(url, token=None, data=None, timeout=15, bearer=True):
            self.assertEqual(token, TOKEN)
            if url.endswith("/user"):
                return 200, {"login": "factory-bot"}
            return (200, {"permissions": {"push": True}}) if "shop-web" in url else (404, {})
        with mock.patch.object(I, "_http", fake):
            s, _, html = self.post(cookie, csrf, "/credentials/test", {"name": "github"})
        self.assertEqual(s, 200)
        self.assertIn("Signed in as factory-bot", html)
        self.assertIn("your-org/shop-web", html)
        self.assertNotIn(TOKEN, html)
        self.assertEqual(self.post(cookie, csrf, "/credentials/test", {"name": "openrouter"})[0], 400)    # nothing stored yet

    def test_status_never_touches_real_paths(self):
        cfg = load(str(self.root / "config.toml"))
        for name in I.SECRETS:
            self.assertTrue(str(I.secret_path(cfg, name)).startswith(str(self.root)), name)


class Telegram(AdminCase):
    def test_save_level_or_events_and_switch_back(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/telegram/save", [("telegram.chat_id", "123"), ("telegram.verbosity", "quiet"), ("telegram.mode", "level")])[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.telegram_chat_id, cfg.telegram_verbosity, cfg.telegram_events), (123, "quiet", None))
        ev = [("telegram.chat_id", "123"), ("telegram.verbosity", "quiet"), ("telegram.mode", "events"), ("telegram.events", "failure"), ("telegram.events", "pr_ready")]
        self.assertEqual(self.post(cookie, csrf, "/telegram/save", ev)[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).telegram_events, ("failure", "pr_ready"))
        self.assertEqual(self.post(cookie, csrf, "/telegram/save", [("telegram.chat_id", "123"), ("telegram.verbosity", "verbose"), ("telegram.mode", "level")])[0], 303)
        self.assertIsNone(load(str(self.root / "config.toml")).telegram_events)
        for bad in ([("telegram.chat_id", "abc"), ("telegram.verbosity", "quiet")], [("telegram.chat_id", "1"), ("telegram.verbosity", "loud")],
                    [("telegram.chat_id", "1"), ("telegram.verbosity", "quiet"), ("telegram.mode", "events")]):
            self.assertEqual(self.post(cookie, csrf, "/telegram/save", bad)[0], 422, bad)

    def test_events_pinned_in_the_base_file_can_be_switched_back_to_a_level(self):
        cfg_path = self.root / "config.toml"
        cfg_path.write_text(cfg_path.read_text().replace("# events = [", "events = [").replace("pr_ready\"]   # optional", "pr_ready\"]   # optional"))
        self.assertEqual(load(str(cfg_path)).telegram_events, ("needs_human", "failure", "pr_ready"))
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/telegram/save", [("telegram.chat_id", "5"), ("telegram.verbosity", "normal"), ("telegram.mode", "level")])
        self.assertIsNone(load(str(cfg_path)).telegram_events)

    def test_detect_lists_senders_and_use_this_id_saves_it(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/telegram/detect")[0], 400)                          # no bot token yet
        self.post(cookie, csrf, "/credentials/save", {"name": "telegram", "value": TG_TOKEN})
        updates = {"ok": True, "result": [{"message": {"text": "SECRET TEXT", "chat": {"type": "private"}, "from": {"id": 4242, "first_name": "Juan"}}}]}
        with mock.patch.object(I, "_http", lambda url, *a, **k: (200, updates)):
            s, _, html = self.post(cookie, csrf, "/telegram/detect")
        self.assertEqual(s, 200)
        self.assertIn("Juan", html)
        self.assertIn("4242", html)
        self.assertNotIn("SECRET TEXT", html)                                                           # message text is never kept or shown
        self.assertEqual(self.post(cookie, csrf, "/telegram/use", {"chat_id": "4242"})[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).telegram_chat_id, 4242)
        self.assertEqual(self.post(cookie, csrf, "/telegram/use", {"chat_id": "-5"})[0], 400)

    def test_detect_also_offers_senders_recorded_by_the_orchestrator(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/credentials/save", {"name": "telegram", "value": TG_TOKEN})
        dbm.set_status(self.db, "telegram_unknown_senders", '[{"id": 777, "name": "Eve", "chat": "private", "ts": 1}]')
        with mock.patch.object(I, "_http", lambda url, *a, **k: (200, {"ok": True, "result": []})):
            _, _, html = self.post(cookie, csrf, "/telegram/detect")
        self.assertIn("Eve", html)
        self.assertIn("777", html)

    def test_test_message_needs_token_and_chat_id_and_scrubs_errors(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/telegram/test")[0], 400)
        tok = TG_TOKEN
        self.post(cookie, csrf, "/credentials/save", {"name": "telegram", "value": tok})
        self.post(cookie, csrf, "/telegram/use", {"chat_id": "42"})
        sent = {}
        def fake(url, token=None, data=None, timeout=15, bearer=True):
            sent.update(data or {}); return 200, {"ok": True}
        with mock.patch.object(I, "_http", fake):
            s, _, html = self.post(cookie, csrf, "/telegram/test")
        self.assertEqual((s, sent["chat_id"]), (200, 42))
        self.assertIn("Sent.", html)
        self.assertEqual(I._scrub(f"error calling bot{tok}", tok), "error calling bot[token]")


if __name__ == "__main__":
    unittest.main()
