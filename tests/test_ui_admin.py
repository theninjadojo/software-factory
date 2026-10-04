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


class ReviewSettings(AdminCase):
    def test_the_reviewer_can_be_enabled_and_configured_from_the_ui(self):
        cookie, csrf = self.session()
        self.assertIn("Code review", self.req("GET", "/settings?section=review", cookie=cookie)[2])
        form = [("section", "review"), ("review.enabled", "1"), ("review.auto", "1"), ("review.label", "factory:review"),
                ("review.done_label", "stage:reviewed"), ("review.model", "opus"), ("review.effort", "high"), ("review.harness", "claude-code")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.review.enabled, cfg.review.model, cfg.review.auto), (True, "opus", True))
        bad = [x for x in form if x[0] != "review.effort"] + [("review.effort", "extreme")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", bad)[0], 422)
        off = [x for x in form if x[0] not in ("review.enabled", "review.auto")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", off)[0], 303)
        self.assertFalse(load(str(self.root / "config.toml")).review.enabled)


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


class Labels(AdminCase):
    REPO = "your-org/standalone-service"

    def setUp(self):
        super().setUp()
        I.save_secret(load(str(self.root / "config.toml")), "github", TOKEN, "subscription")
        self.gh = mock.MagicMock()
        self.gh.repo_labels.return_value = [{"name": "bug"}, {"name": "<script>"}, {"name": "factory:ready"}]
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "bug"}]}
        self.gh.issues.return_value = ([self.gh.get_issue.return_value], False)
        p = mock.patch("factory.ui.labels.GitHub", return_value=self.gh)
        p.start()
        self.addCleanup(p.stop)

    def fields(self, **over):
        return {"repo": self.REPO, "n": "7", **over}

    def test_requires_a_session_and_csrf_and_makes_no_github_call(self):
        self.assertEqual(self.req("GET", "/labels")[0], 303)
        self.assertEqual(self.req("POST", "/labels/add", urlencode(self.fields(label="bug")))[0], 303)
        cookie, _ = self.session()
        s, _, _ = self.req("POST", "/labels/add", urlencode(self.fields(label="bug")), cookie=cookie)
        self.assertEqual(s, 403)
        self.gh.add_labels.assert_not_called()

    def test_add_sends_one_call_and_redirects(self):
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/labels/add", self.fields(label="factory:ready"))
        self.assertEqual(s, 303)
        self.gh.add_labels.assert_called_once_with(self.REPO, 7, ["factory:ready"])

    def test_unknown_label_repo_or_number_is_rejected_before_any_write(self):
        cookie, csrf = self.session()
        for path, f in (("/labels/add", self.fields(label="nope")), ("/labels/add", {"repo": "evil/repo", "n": "7", "label": "bug"}),
                        ("/labels/add", self.fields(n="x", label="bug")), ("/labels/replace", self.fields(old="bug", new="nope")),
                        ("/labels/remove", self.fields(n="7; DROP", label="bug"))):
            self.assertEqual(self.post(cookie, csrf, path, f)[0], 400, f)
        self.gh.add_labels.assert_not_called()
        self.gh.remove_label.assert_not_called()

    def test_replace_adds_then_removes_and_remove_works(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/labels/replace", self.fields(old="bug", new="factory:ready"))[0], 303)
        self.gh.add_labels.assert_called_once_with(self.REPO, 7, ["factory:ready"])
        self.gh.remove_label.assert_called_once_with(self.REPO, 7, "bug")
        self.gh.remove_label.reset_mock()
        self.assertEqual(self.post(cookie, csrf, "/labels/remove", self.fields(label="bug"))[0], 303)
        self.gh.remove_label.assert_called_once_with(self.REPO, 7, "bug")

    def test_github_error_is_reported_not_claimed_as_success(self):
        import urllib.error
        self.gh.add_labels.side_effect = urllib.error.HTTPError("u", 403, "x", {}, None)
        cookie, csrf = self.session()
        s, _, html = self.post(cookie, csrf, "/labels/add", self.fields(label="bug"))
        self.assertEqual(s, 502)
        self.assertIn("GitHub refused", html)
        self.assertNotIn("Label added", html)

    def test_tickets_page_merges_github_labels_with_the_factory_decision(self):
        dbm.record(self.db, self.REPO, 7, "T", "run:stage", "analyst first")
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/labels", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("bug", "run:stage", "/labels/issue?repo="):
            self.assertIn(needle, html)

    def test_tickets_page_shows_stage_progress_chips_and_footer(self):
        issue = {"number": 7, "title": "<script>x</script>", "state": "open", "updated_at": "2026-10-02T10:00:00Z",
                 "labels": [{"name": "factory:working-architect"}, {"name": "stage:analysed"}]}
        self.gh.issues.return_value = ([issue], True)
        self.gh.count_issues.return_value = 24
        cookie, _ = self.session()
        _, _, html = self.req("GET", "/labels", cookie=cookie)
        for needle in ("New ticket", 'type="search"', "In progress", "Needs you", "Done", "<th>Stage</th>", "<th>Updated</th>", "Architect working",
                       "1/4", "Showing 1 of 24", 'action="/tickets/create"', 'name="csrf"'):
            self.assertIn(needle, html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertIn('aria-current=page>All', html)

    def test_stage_chips_search_on_github_across_pages(self):
        issue = {"number": 7, "title": "T", "state": "closed", "labels": []}
        self.gh.search_page.return_value = ([issue], False, 24)
        cookie, _ = self.session()
        _, _, html = self.req("GET", "/labels?stage=done", cookie=cookie)
        self.assertEqual(self.gh.search_page.call_args.args[2], "closed")
        self.assertIn("Showing 1 of 24", html)
        self.assertIn("Done", html)
        self.req("GET", "/labels?stage=progress", cookie=cookie)
        self.assertIn("factory:working", self.gh.search_page.call_args.kwargs["labels"])
        self.req("GET", "/labels?stage=%3Cb%3E", cookie=cookie)                   # an unknown stage is ignored, never passed on
        self.assertEqual(self.gh.search_page.call_count, 2)

    def test_ticket_rows_are_addressable_for_in_place_updates(self):
        """app.js swaps a row by data-row after a background POST; the POST itself is the unchanged, CSRF-checked form."""
        cookie, _ = self.session()
        html = self.req("GET", "/labels", cookie=cookie)[2]
        self.assertIn(f'<tr data-row="{self.REPO}#7">', html)

    def refused(self, cookie, csrf, fields, back="/"):
        """A refusal sends the person back to the page they were on, with the reason shown there once (never a different page)."""
        s, h, _ = self.post(cookie, csrf, "/tickets/start", {**fields, "back": back})
        self.assertEqual((s, h["Location"]), (303, back))
        page = self.req("GET", back, cookie=cookie)[2]
        self.assertIn('class="flash bad"', page)
        self.assertNotIn('class="flash bad"', self.req("GET", back, cookie=cookie)[2])            # shown once

    def approvals(self):
        return self.db.execute("SELECT repo, issue, action FROM approvals ORDER BY issue").fetchall()

    def test_start_buttons_queue_an_approval_or_apply_a_label(self):
        """Build and the stages must not go back through the classifier (it would ask again: the click would seem to do nothing)."""
        self.gh.repo_labels.return_value = [{"name": n} for n in ("factory:auto", "factory:ready", "factory:analyze", "factory:design", "factory:architect")]
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/labels", cookie=cookie)
        for action in ("auto", "analyst", "designer", "architect", "build"):
            self.assertIn(f'name="action" value="{action}"', html)
        for action, want in (("build", "run"), ("analyst", "stage:analyst"), ("architect", "stage:architect")):
            self.db.execute("DELETE FROM approvals")
            self.db.commit()
            s, _, _ = self.post(cookie, csrf, "/tickets/start", self.fields(action=action))
            self.assertEqual(s, 303)
            self.assertEqual(self.approvals(), [(self.REPO, 7, want)], action)
        self.gh.add_labels.assert_not_called()
        self.gh.add_labels.reset_mock()
        self.db.execute("DELETE FROM approvals")
        self.db.commit()
        self.assertEqual(self.post(cookie, csrf, "/tickets/start", self.fields(action="auto"))[0], 303)       # Auto is the classifier's call: a label
        self.gh.add_labels.assert_called_once_with(self.REPO, 7, ["factory:auto"])
        self.assertEqual(self.approvals(), [])

    def test_a_queued_decision_hides_the_buttons_and_cannot_be_repeated(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/tickets/start", self.fields(action="build"))[0], 303)
        _, _, html = self.req("GET", "/labels", cookie=cookie)
        self.assertIn("starting", html)
        self.assertNotIn('name="action" value="build"', html)
        self.refused(cookie, csrf, self.fields(action="build"), "/tickets")
        self.assertEqual(len(self.approvals()), 1)

    def test_the_approval_written_by_the_ui_is_what_the_factory_runs(self):
        """The UI's row must be exactly what process_approvals consumes: a build that skips the classifier."""
        from dataclasses import replace
        from factory import main as m
        from factory.config import load
        from factory.runner import RunResult
        from test_roles import FakeGH, issue
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/tickets/start", self.fields(action="build"))
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto"])]})
        cfg = replace(load(str(self.root / "config.toml")), repos=[self.REPO], dry_run=False)
        with mock.patch.object(m.runner, "run_task", return_value=RunResult("pr", "ok", "http://pr")) as run, \
             mock.patch.object(m, "alert"):
            m.process_approvals(cfg, gh, self.db, mock.MagicMock())
        self.assertEqual(self.approvals(), [])

    def test_start_is_refused_when_not_available_or_invalid(self):
        self.gh.repo_labels.return_value = [{"name": "factory:auto"}, {"name": "factory:ready"}]
        cookie, csrf = self.session()
        busy = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:working"}]}
        queued = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:auto"}]}
        closed = {"number": 7, "title": "T", "state": "closed", "labels": []}
        for issue in (busy, queued, closed):
            self.gh.get_issue.return_value = issue
            self.refused(cookie, csrf, self.fields(action="build"))
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": []}
        for f in (self.fields(action="rm -rf"), self.fields(action="factory:ready")):
            self.refused(cookie, csrf, f, "/tickets")
        for f in ({"repo": "evil/repo", "n": "7", "action": "auto"}, self.fields(n="x", action="auto")):          # malformed: not a page the person was on
            self.assertEqual(self.post(cookie, csrf, "/tickets/start", f)[0], 400)
        self.gh.add_labels.assert_not_called()
        self.assertEqual(self.req("POST", "/tickets/start", urlencode(self.fields(action="auto")), cookie=cookie)[0], 403)
        self.gh.add_labels.assert_not_called()

    def test_create_ticket_validates_sends_no_labels_and_ignores_repeats(self):
        self.gh.create_ticket.return_value = {"number": 42}
        cookie, csrf = self.session()
        self.assertEqual(self.req("POST", "/tickets/create", urlencode(self.fields(title="x")), cookie=cookie)[0], 403)
        for f in ({"repo": "evil/repo", "title": "x"}, {"repo": self.REPO, "title": "  "}, {"repo": self.REPO, "title": "t" * 201},
                  {"repo": self.REPO, "title": "t", "body": "b" * 5001}):
            self.post(cookie, csrf, "/tickets/create", f)
        self.gh.create_ticket.assert_not_called()
        self.assertEqual(self.post(cookie, csrf, "/tickets/create", {"repo": self.REPO, "title": "A <b>", "body": "d"})[0], 303)
        self.gh.create_ticket.assert_called_once_with(self.REPO, "A <b>", "d")
        self.post(cookie, csrf, "/tickets/create", {"repo": self.REPO, "title": "A <b>"})
        self.gh.create_ticket.assert_called_once()
        self.gh.add_labels.assert_not_called()

    def test_close_comments_then_closes_and_refuses_busy_tickets(self):
        cookie, csrf = self.session()
        busy = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:working"}]}
        closed = {"number": 7, "title": "T", "state": "closed", "labels": []}
        for issue in (busy, closed):
            self.gh.get_issue.return_value = issue
            self.post(cookie, csrf, "/tickets/close", self.fields())
        self.gh.update_issue.assert_not_called()
        for f in ({"repo": "evil/repo", "n": "7"}, self.fields(n="x")):
            self.assertEqual(self.post(cookie, csrf, "/tickets/close", f)[0], 400)
        self.assertEqual(self.req("POST", "/tickets/close", urlencode(self.fields()), cookie=cookie)[0], 403)
        self.gh.update_issue.assert_not_called()
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": []}
        self.assertEqual(self.post(cookie, csrf, "/tickets/close", self.fields())[0], 303)
        self.gh.comment.assert_called_once()
        self.gh.update_issue.assert_called_once_with(self.REPO, 7, state="closed")

    def test_needs_a_person_offers_the_telegram_choices_and_clears_trigger_labels(self):
        dbm.record(self.db, self.REPO, 7, "T", "human", "x; cls=feature/high/human=True/conf=0.80/stage=architect; needs a person")
        issue = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:auto"}]}
        self.gh.get_issue.return_value = issue
        self.gh.issues.return_value = ([issue], False)
        self.gh.repo_labels.return_value = [{"name": n} for n in ("factory:auto", "factory:ready", "factory:architect")]
        cookie, csrf = self.session()
        _, _, html = self.req("GET", "/labels", cookie=cookie)
        self.assertIn("needs a person", html)
        self.assertLess(html.index('value="stage:architect"'), html.index('value="build"'))
        self.assertIn('value="skip"', html)
        self.assertNotIn('value="auto"', html)
        self.assertEqual(self.post(cookie, csrf, "/tickets/start", self.fields(action="stage:architect"))[0], 303)
        self.assertEqual(self.approvals(), [(self.REPO, 7, "stage:architect")])        # the factory clears the labels and runs it itself
        self.gh.add_labels.assert_not_called()
        self.db.execute("DELETE FROM approvals")
        self.db.commit()
        self.assertEqual(self.post(cookie, csrf, "/tickets/start", self.fields(action="skip"))[0], 303)
        self.gh.add_labels.assert_not_called()
        self.gh.remove_label.assert_called_once_with(self.REPO, 7, "factory:auto")

    def test_an_action_returns_to_the_page_it_came_from_and_only_to_ours(self):
        cookie, csrf = self.session()
        for back, want in (("/", "/"), ("/?station=build", "/?station=build"), ("/tickets?repo=a%2Fb&state=open&page=1", "/tickets?repo=a%2Fb&state=open&page=1"),
                           ("https://evil.example/", "/tickets"), ("//evil.example", "/tickets"), ("/settings", "/tickets"), ("/?station=<b>", "/tickets"), ("", "/tickets")):
            self.db.execute("DELETE FROM approvals")
            self.db.commit()
            s, h, _ = self.post(cookie, csrf, "/tickets/start", {**self.fields(action="build"), "back": back})
            self.assertEqual((s, h["Location"]), (303, want), back)

    def test_the_result_message_is_shown_on_the_page_you_came_back_to(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/tickets/start", {**self.fields(action="build"), "back": "/"})
        with mock.patch("factory.ui.server.L.needs_you", return_value=[]):
            page = self.req("GET", "/", cookie=cookie)[2]
            self.assertIn("Started.", page)
            self.assertNotIn("Started.", self.req("GET", "/", cookie=cookie)[2])
        self.post(cookie, csrf, "/tickets/start", {**self.fields(action="build"), "back": "/"})        # refused: already starting
        with mock.patch("factory.ui.server.L.needs_you", return_value=[]):
            fragment = self.req("GET", "/fragment/overview", cookie=cookie)[2]
            self.assertNotIn("cannot be started", fragment)                                      # the refresh must not eat the message
            self.assertIn("cannot be started", self.req("GET", "/", cookie=cookie)[2])

    def test_accept_recommendations_from_the_floor_needs_no_stage_and_returns_there(self):
        """Regression: the Floor's button sent no stage and was refused as 'does not match the ticket's questions'."""
        cookie, csrf = self.session()
        self.gh.repo_labels.return_value = [{"name": "factory:auto"}]
        with mock.patch("factory.ui.labels.Q.record", return_value=("architect", True)) as rec:
            s, h, _ = self.post(cookie, csrf, "/tickets/answer", {**self.fields(), "accept": "1", "back": "/?station=architect"})
        self.assertEqual((s, h["Location"]), (303, "/?station=architect"))
        self.assertIsNone(rec.call_args.args[3])                           # no stage given: the latest stage's questions
        self.gh.add_labels.assert_called_once_with(self.REPO, 7, ["factory:auto"])
        with mock.patch("factory.ui.labels.Q.record") as rec:               # a single answer still must name its stage
            s, h, _ = self.post(cookie, csrf, "/tickets/answer", {**self.fields(), "q": "q1", "o": "a", "back": "/"})
        self.assertEqual(s, 303)
        rec.assert_not_called()
        self.assertIn("does not match", self.req("GET", "/labels", cookie=cookie)[2])

    def test_several_answers_are_sent_together_and_validated(self):
        cookie, csrf = self.session()
        self.gh.repo_labels.return_value = [{"name": "factory:auto"}]
        with mock.patch("factory.ui.labels.Q.record", return_value=("architect", False)) as rec:
            s, h, _ = self.post(cookie, csrf, "/tickets/answer", {**self.fields(), "stage": "architect", "a_q1": "a", "a_q2": "b", "x_q2": "  my own  ",
                                                                    "send": "1", "back": "/needs"})
        self.assertEqual((s, h["Location"]), (303, "/needs"))
        self.assertEqual(rec.call_args.args[4], {"q1": ("option", "a"), "q2": ("other", "my own")})      # typed words win over the radio
        self.assertFalse(rec.call_args.args[6])
        with mock.patch("factory.ui.labels.Q.record") as rec:                                           # nothing chosen: refused, never recorded
            s, h, _ = self.post(cookie, csrf, "/tickets/answer", {**self.fields(), "stage": "architect", "send": "1", "back": "/"})
        rec.assert_not_called()
        self.assertEqual(s, 303)
        self.assertIn("Choose an answer first", self.req("GET", "/", cookie=cookie)[2])

    def test_accept_on_several_tickets_validates_each_one(self):
        cookie, csrf = self.session()
        self.gh.repo_labels.return_value = [{"name": "factory:auto"}]
        seen = []
        with mock.patch("factory.ui.labels.Q.record", side_effect=lambda gh, repo, n, *a: seen.append((repo, n, a[-1])) or ("architect", True)):
            s, h, _ = self.post(cookie, csrf, "/tickets/answer-all", {"tickets": f"{self.REPO}#7,{self.REPO}#8", "back": "/needs"})
        self.assertEqual((s, h["Location"]), (303, "/needs"))
        self.assertEqual(seen, [(self.REPO, 7, True), (self.REPO, 8, True)])                             # accept_all, every ticket re-read from GitHub
        for bad in ("", "evil/repo#7", f"{self.REPO}#x", f"{self.REPO}#7;rm", "a#1," * 3):
            with mock.patch("factory.ui.labels.Q.record") as rec:
                s, _, _ = self.post(cookie, csrf, "/tickets/answer-all", {"tickets": bad, "back": "/"})
            rec.assert_not_called()
            self.assertEqual(s, 303, bad)
        with mock.patch("factory.ui.labels.Q.record") as rec:                                              # a repository that is not configured
            self.post(cookie, csrf, "/tickets/answer-all", {"tickets": "evil/repo#7", "back": "/"})
        rec.assert_not_called()
        self.assertEqual(self.req("POST", "/tickets/answer-all", urlencode({"tickets": f"{self.REPO}#7"}), cookie=cookie)[0], 403)

    def test_skip_and_stage_actions_are_refused_when_nothing_is_waiting(self):
        cookie, csrf = self.session()
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:auto"}]}   # queued, no "human" decision
        for action in ("skip", "stage:architect", "build"):
            self.refused(cookie, csrf, self.fields(action=action))
        self.gh.remove_label.assert_not_called()
        self.gh.add_labels.assert_not_called()

    def test_done_stages_are_not_offered_again(self):
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "stage:analysed"}]}
        self.gh.issues.return_value = ([self.gh.get_issue.return_value], False)
        _, _, html = self.req("GET", "/labels", cookie=self.session()[0])
        self.assertNotIn('value="analyst"', html)
        self.assertIn('value="designer"', html)

    def test_label_names_are_escaped_and_token_never_rendered(self):
        self.gh.get_issue.return_value["labels"] = [{"name": "<script>"}]
        cookie, _ = self.session()
        for path in ("/labels", f"/labels/issue?repo={quote(self.REPO)}&n=7", "/tickets"):
            s, _, html = self.req("GET", path, cookie=cookie)
            self.assertEqual(s, 200)
            self.assertNotIn("<script>", html.replace('<script src="/static/app.js" defer></script>', ""))
            if path != "/tickets":                                     # the Tickets screen does not list labels
                self.assertIn("&lt;script&gt;", html)
            self.assertNotIn(TOKEN, html)


class SettingsLayout(UiCase):
    def test_nav_has_seven_items_and_moved_pages_render_in_settings(self):
        from factory.ui import views
        self.assertEqual([n for _, n in views.NAV], ["Factory", "Tickets", "Events", "Screens", "Settings"])
        cookie, _ = self.session()
        for path, side in (("/harnesses", "Harnesses"), ("/credentials", "Credentials"), ("/telegram", "Telegram"), ("/settings?section=labels", "Labels")):
            s, _, html = self.req("GET", path, cookie=cookie)
            self.assertEqual(s, 200, path)
            nav = html.split('<nav aria-label="Main">')[1].split("</nav>")[0]
            self.assertIn('<a href="/settings" class="active ">Settings</a>', nav, path)
            self.assertNotIn('href="/harnesses"', nav)
            self.assertIn(f'<a href="{path}" class=active aria-current=page>{side}</a>', html, path)
            for item in ("General", "Routing", "Role agents", "Projects", "Harnesses", "Credentials", "Telegram", "Labels"):
                self.assertIn(f">{item}</a>", html)

    def test_general_is_rows_with_one_save_button(self):
        cookie, _ = self.session()
        _, _, html = self.req("GET", "/settings", cookie=cookie)
        self.assertIn("Save changes", html)
        self.assertIn("Changes apply the next time the factory is idle.", html)
        self.assertEqual(html.count('class="srow"'), 6)
