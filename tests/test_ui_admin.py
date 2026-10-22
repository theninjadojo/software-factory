import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlencode

from factory import db as dbm
from factory.config import load, overrides_path
from factory.ui import integrations as I
from factory.ui import labels as L
from factory.ui import settings as S
from test_ui import PASSWORD, UiCase

# Built from pieces so no token-shaped literal exists in the source (secret scanners match on shape).
TOKEN = "ghp" + "_" + "SECRETVALUE1234567890abcdef"
APIKEY = "sk" + "-ant-" + "b" * 12
TG_TOKEN = "123456789" + ":" + "ABCdefGhiJKlmnoPQRstuVWXyz012345678"
SLACK_BOT = "xox" + "b-" + "1234567890-abcdefABCDEF"
SLACK_APP = "xap" + "p-" + "1-A0123456789-abcdef0123456789"


class AdminCase(UiCase):
    def post(self, cookie, csrf, path, fields=None):
        pairs = [("csrf", csrf)] + (list(fields.items()) if isinstance(fields, dict) else list(fields or []))
        return self.req("POST", path, urlencode(pairs), cookie=cookie)

    def general_form(self, **over):
        cfg = load(str(self.root / "config.toml"))
        f = {"section": "general", "general.poll_seconds": str(cfg.poll_seconds), "general.confidence_threshold": str(cfg.confidence_threshold),
             "github.trigger_label": cfg.trigger_label, "repo0": "your-org/standalone-service",
             "github.poll_seconds": str(cfg.github_poll_seconds), "local.max_attachment_mb": str(cfg.attach_max_mb),
             "local.max_attachments": str(cfg.attach_max_files), "local.max_attachments_total_mb": str(cfg.attach_max_total_mb),
             "tickets.archive_days": str(cfg.tickets_archive_days), "local.auto_import_seconds": str(cfg.local_auto_import_seconds)}
        if cfg.github_issues_enabled:
            f["github.issues_enabled"] = "1"                                              # a checked box is sent; an unchecked one is not
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
            self.assertIn("Check for work every (seconds): must be a whole number", html)
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
        form = lambda hosts, **extra: [("section", "runner"), ("runner.max_parallel", "1"), ("runner.timeout_seconds", "1800"), ("runner.max_turns", "40"),
                                       ("runner.rate_limit_backoff_seconds", "3600"), ("runner.memory", "3g"), ("runner.cpus", "2"),
                                       ("runner.allow_hosts", hosts), ("runner.render_previews", "1"), ("runner.thinking_tokens.low", "2000"),
                                       ("runner.thinking_tokens.medium", "8000"), ("runner.thinking_tokens.high", "24000"), ("runner.max_patch_bytes", "2000000"),
                                       ("runner.max_files", "100"), ("runner.render_timeout_seconds", "120"), ("runner.engine", "podman"),
                                       ("runner.image", "localhost/factory-agent:latest"), ("runner.render_image", "localhost/factory-render:latest")] + list(extra.items())
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form("api.anthropic.com\nregistry.npmjs.org"))[0], 422)   # unconfirmed
        for bad in ("evil", "a b.com", "127.0.0.1", "https://x.com", ""):
            self.assertEqual(self.post(cookie, csrf, "/settings/save", form(bad, **{"confirm__runner.allow_hosts": "1"}))[0], 422, bad)
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form("api.anthropic.com\nregistry.npmjs.org", **{"confirm__runner.allow_hosts": "1"}))[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).runner.allow_hosts, ("api.anthropic.com", "registry.npmjs.org"))
        self.assertEqual(self.post(cookie, csrf, "/settings/save", [("section", "runner"), ("runner.memory", "3 gigs")])[0], 422)

    def test_projects_add_edit_remove_one_at_a_time(self):
        cookie, csrf = self.session()
        new = {"pj": "1", "was": "", "p1_name": "blog", "p1_desc": "A blog", "p1_r0_repo": "your-org/blog-web", "p1_r0_role": "web",
               "p1_r1_repo": "your-org/blog-api", "p1_r1_role": "api"}
        s, h, _ = self.post(cookie, csrf, "/settings/projects", new)
        self.assertEqual((s, h["Location"]), (303, "/settings?section=projects&ok=saved&open=blog"))           # back with its row open
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual([p.name for p in cfg.projects], ["shop", "blog"])
        self.assertEqual([r.repo for r in cfg.projects[1].repos], ["your-org/blog-web", "your-org/blog-api"])
        self.assertIn("your-org/blog-api", cfg.repos)
        self.assertEqual(cfg.projects[0].repo("your-org/shop-packages").publish.detect, "release")            # the other project is untouched
        s, h, _ = self.post(cookie, csrf, "/settings/projects", {"pj": "1", "was": "blog", "p1_name": "journal", "p1_r0_repo": "your-org/blog-web"})
        self.assertEqual((s, h["Location"]), (303, "/settings?section=projects&ok=saved&open=journal"))
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual(([p.name for p in cfg.projects], [r.repo for r in cfg.projects[1].repos]), (["shop", "journal"], ["your-org/blog-web"]))
        # a clash with another project is marked on the project being saved, wherever it is in the list
        s, _, html = self.post(cookie, csrf, "/settings/projects", {"pj": "0", "was": "shop", "p0_name": "shop", "p0_r0_repo": "your-org/shop-web",
                                                                    "p0_r1_repo": "your-org/blog-web"})
        self.assertEqual(s, 422)
        self.assertIn('id="p0_r1_repo-err">your-org/blog-web is in two projects: journal and shop.', html)
        self.assertIn('<details class="pj-item" id="pj-0" open data-here>', html)                              # its row is open on what was typed
        s, _, html = self.post(cookie, csrf, "/settings/projects", {"pj": "2", "was": "", "p2_name": "Shop", "p2_r0_repo": "your-org/new"})
        self.assertEqual(s, 422)
        self.assertIn('id="p2_name-err">Another project already has this name.', html)
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", {"pj": "1", "was": "journal", "p1_r0_repo": "not a repo"})[0], 422)
        s, _, html = self.post(cookie, csrf, "/settings/projects", {"pj": "1", "was": "gone", "p1_name": "gone", "p1_r0_repo": "o/r"})
        self.assertEqual(s, 422)
        self.assertIn("Reload the page", html)
        self.assertEqual([p.name for p in load(str(self.root / "config.toml")).projects], ["shop", "journal"])  # none of these saved anything
        s, h, _ = self.post(cookie, csrf, "/settings/projects", {"pj": "1", "was": "journal", "action": "remove", "p1_name": "journal", "p1_r0_repo": "x"})
        self.assertEqual((s, h["Location"]), (303, "/settings?section=projects&ok=saved"))
        self.assertEqual([p.name for p in load(str(self.root / "config.toml")).projects], ["shop"])
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", {"pj": "0", "was": "shop"})[0], 303)       # emptied = removed
        self.assertEqual(load(str(self.root / "config.toml")).projects, ())
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", {"pj": "0", "was": "", "action": "remove"})[0], 422)

    def test_the_projects_page_puts_the_new_form_first_and_each_project_in_a_row(self):
        cookie, csrf = self.session()
        _, _, html = self.req("GET", "/settings?section=projects", cookie=cookie)
        self.assertLess(html.index("<h2>New project</h2>"), html.index('class="pj-item"'))
        self.assertIn('name="pj" value="1"><input type="hidden" name="was" value="">', html)                   # the new one comes after shop
        self.assertIn('<details class="pj-item" id="pj-0"><summary><span class="pj-title">shop</span>'
                      '<span class="pj-meta muted">3 repositories · build order set</span></summary>', html)
        row = html[html.index('id="pj-0"'):html.index("</details>", html.index("Find package dependencies"))]
        self.assertIn("<h3>Build order</h3>", row)                                                            # the build order is inside it
        self.assertIn('class="secondary pj-remove" data-name="shop">Remove project</button>', row)
        _, _, html = self.req("GET", "/settings?section=projects&open=shop", cookie=cookie)
        self.assertIn('<details class="pj-item" id="pj-0" open data-here>', html)

    def test_standalone_repositories_are_rows_with_a_description_and_new_ones_are_checked_with_the_token(self):
        from factory.config import project_for
        from factory.ui import integrations as I
        cookie, csrf = self.session()
        _, _, html = self.req("GET", "/settings?section=general", cookie=cookie)
        self.assertIn('<details class="sr-sec" id="s-github.repos" open>', html)
        self.assertIn('name="repo0" id="repo0" placeholder="owner/name" autocomplete="off" aria-label="Repository 1"  value="your-org/standalone-service"', html)
        self.assertEqual(html.count('placeholder="owner/name"'), 3)                     # the one listed and two spare rows
        self.assertNotIn("<textarea id=\"github.repos\"", html)
        asked = []

        def http(url, token, *a, **k):
            asked.append(url)
            return {"ok-org/new-api": (200, {"permissions": {"push": True}}), "ok-org/docs": (200, {"permissions": {"push": False}}),
                    "ok-org/down": (0, {"error": "timed out"})}.get(url.split("/repos/")[1], (404, {"message": "Not Found"}))
        with mock.patch.object(I, "read_secret", return_value="ghp_x"), mock.patch.object(I, "_http", side_effect=http):
            # a pasted address, a repository the token cannot see, and something that is not a repository: nothing is saved, all is kept
            form = self.general_form(repo0_role="Billing API", repo1="https://github.com/ok-org/new-api", repo1_role="The new API",
                                     repo4="other-org/secret", repo5="not a repo")
            s, _, html = self.post(cookie, csrf, "/settings/save", form)
            self.assertEqual(s, 422)
            self.assertIn("Standalone repositories: correct the field marked below.", html)
            self.assertIn('id="repo3-err">Write the repository as owner/name', html)     # empty rows close up: the fourth typed is repo3
            self.assertEqual(asked, [])                                                    # GitHub is asked only once the list is well formed
            for kept in ('value="Billing API"', 'value="ok-org/new-api"', 'value="The new API"', 'value="other-org/secret"', 'value="not a repo"'):
                self.assertIn(kept, html)
            s, _, html = self.post(cookie, csrf, "/settings/save", self.general_form(repo0_role="Billing API", repo1="ok-org/new-api", repo2="other-org/secret"))
            self.assertEqual(s, 422)
            self.assertIn('id="repo2-err">The factory&#x27;s GitHub token cannot see this repository.', html)
            self.assertEqual([u.split("/repos/")[1] for u in asked], ["ok-org/new-api", "other-org/secret"])   # not the one already listed
            self.assertEqual(load(str(self.root / "config.toml")).repo_roles, {})
            # read-only and unreachable ones are saved, and the page says so
            ok = self.general_form(repo0_role="Billing API", repo1="ok-org/new-api", repo1_role="The new API", repo2="ok-org/docs", repo3="ok-org/down")
            s, hdrs, _ = self.post(cookie, csrf, "/settings/save", ok)
            self.assertEqual(s, 303)
            _, _, html = self.req("GET", "/settings?section=general&ok=saved", cookie=cookie)
            self.assertIn("The GitHub token can read ok-org/docs but not write to it", html)
            self.assertIn("Access to ok-org/down could not be checked.", html)
            cfg = load(str(self.root / "config.toml"))
            self.assertEqual(cfg.repos[:4], ["your-org/standalone-service", "ok-org/new-api", "ok-org/docs", "ok-org/down"])
            self.assertEqual(cfg.repo_roles, {"your-org/standalone-service": "Billing API", "ok-org/new-api": "The new API"})
            self.assertEqual(project_for(cfg, "ok-org/new-api").repos[0].role, "The new API")                 # what the agents are told
            self.assertIn('value="The new API"', html)
            asked.clear()
            # clearing a description and removing a repository; nothing already listed is asked about again
            self.assertEqual(self.post(cookie, csrf, "/settings/save", self.general_form(repo0_role="", repo1="ok-org/new-api", repo1_role="The new API"))[0], 303)
            cfg = load(str(self.root / "config.toml"))
            self.assertEqual((cfg.repos[:2], cfg.repo_roles), (["your-org/standalone-service", "ok-org/new-api"], {"ok-org/new-api": "The new API"}))
            self.assertEqual(asked, [])

    def test_a_description_set_in_config_toml_can_be_cleared_from_the_ui(self):
        cookie, csrf = self.session()
        path = self.root / "config.toml"
        path.write_text(path.read_text().replace("[github]\n", '[github]\nrepo_roles = { "your-org/standalone-service" = "From the file" }\n', 1))
        self.assertEqual(load(str(path)).repo_roles, {"your-org/standalone-service": "From the file"})
        self.assertIn('value="From the file"', self.req("GET", "/settings?section=general", cookie=cookie)[2])
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.general_form(repo0_role=""))[0], 303)
        self.assertEqual(load(str(path)).repo_roles, {})
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.general_form(repo0_role="From the file"))[0], 303)
        self.assertEqual(load(str(path)).repo_roles, {"your-org/standalone-service": "From the file"})
        self.assertNotIn("repo_roles", self.overrides().get("github", {}))               # back to what the file says: no override kept

    def test_a_refused_projects_save_keeps_what_was_typed_and_marks_each_field(self):
        cookie, csrf = self.session()
        typed = {"pj": "1", "was": "", "p1_name": "bad/name", "p1_desc": "A blog", "p1_r0_repo": "your-org/blog-web", "p1_r0_role": "the storefront",
                 "p1_r2_repo": "not a repo", "p1_r2_role": "api", "p1_r4_repo": "your-org/shop-web"}
        s, _, html = self.post(cookie, csrf, "/settings/projects", typed)
        self.assertEqual(s, 422)
        self.assertIn("Nothing was saved. Correct the 3 fields marked below.", html)
        for kept in ('value="A blog"', 'value="the storefront"', 'value="not a repo"', 'value="bad/name"'):
            self.assertIn(kept, html)
        # empty rows close up, so the second repository typed is r1 and the third r2
        self.assertIn('name="p1_r1_repo" id="p1_r1_repo" placeholder="owner/name" aria-label="Repository 2"  value="not a repo" aria-invalid="true" aria-describedby="p1_r1_repo-err">'
                      '<p class="f-err" id="p1_r1_repo-err">Write the repository as owner/name, like your-org/shop-web.</p>', html)
        self.assertIn('id="p1_name-err">Use a short name', html)
        self.assertIn('id="p1_r2_repo-err">your-org/shop-web is in two projects: shop and bad/name.', html)
        self.assertEqual(html.count('class="f-err"'), 3)
        self.assertEqual([p.name for p in load(str(self.root / "config.toml")).projects], ["shop"])           # the example's project, untouched

    def test_projects_take_a_pasted_github_address_and_more_than_two_new_repos(self):
        cookie, csrf = self.session()
        form = {"pj": "1", "was": "", "p1_name": "Yuki", "p1_r0_repo": "https://github.com/theninjadojo/ninja-packages", "p1_r1_repo": "git@github.com:theninjadojo/yuki-api.git",
                "p1_r2_repo": "github.com/theninjadojo/yuki-web/tree/main", "p1_r3_repo": "theninjadojo/yuki-docs", "p1_r7_repo": " theninjadojo/yuki-infra "}
        self.assertEqual(self.post(cookie, csrf, "/settings/projects", form)[0], 303)
        self.assertEqual([r.repo for r in load(str(self.root / "config.toml")).projects[1].repos],
                         ["theninjadojo/ninja-packages", "theninjadojo/yuki-api", "theninjadojo/yuki-web", "theninjadojo/yuki-docs", "theninjadojo/yuki-infra"])
        _, _, html = self.req("GET", "/settings?section=projects", cookie=cookie)
        self.assertEqual(html.count('name="p1_r'), 14)                       # five repos and two spare rows, each a repository and a role
        self.assertIn('class="secondary pj-add" data-max="30" hidden>Add repository</button>', html)

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
                ("review.done_label", "stage:reviewed"), ("review.fix_rounds", "3"), ("review.model", "opus"), ("review.effort", "high"),
                ("review.harness", "claude-code")]
        page = self.req("GET", "/settings?section=review", cookie=cookie)[2]
        self.assertIn('name="review.fix_rounds"', page)                                    # the fix-round cap is set here, not in a file
        self.assertIn('name="review.gate"', page)                                          # and the gate's off switch
        self.assertIn("Keep a pull request a draft until the review says Looks good", page)
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)          # the gate box unticked: off
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.review.enabled, cfg.review.model, cfg.review.auto, cfg.review.fix_rounds, cfg.review.gate), (True, "opus", True, 3, False))
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form + [("review.gate", "1")])[0], 303)
        self.assertTrue(load(str(self.root / "config.toml")).review.gate)
        bad = [x for x in form if x[0] != "review.effort"] + [("review.effort", "extreme")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", bad)[0], 422)
        too_many = [x for x in form if x[0] != "review.fix_rounds"] + [("review.fix_rounds", "9")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", too_many)[0], 422)
        off = [x for x in form if x[0] not in ("review.enabled", "review.auto")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", off)[0], 303)
        self.assertFalse(load(str(self.root / "config.toml")).review.enabled)


class ClassifierHint(AdminCase):
    KEY = "sk" + "-or-" + "c" * 12
    TEXT = "classifier is still set to"

    def test_warns_while_a_key_is_saved_and_the_classifier_is_on_labels(self):
        cookie, csrf = self.session()
        self.assertNotIn(self.TEXT, self.req("GET", "/credentials", cookie=cookie)[2])                    # no key yet: nothing to say
        self.post(cookie, csrf, "/credentials/save", {"name": "openrouter", "value": self.KEY})
        html = self.req("GET", "/credentials", cookie=cookie)[2]
        self.assertIn(self.TEXT, html)
        self.assertIn('href="/settings?section=classifier"', html)
        self.assertNotIn(self.KEY, html)
        form = [("section", "classifier"), ("classifier.backend", "jev"), ("classifier.model", "typesafe/jev-1.13"), ("classifier.kind_aliases", "story=feature")]
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)
        self.assertNotIn(self.TEXT, self.req("GET", "/credentials", cookie=cookie)[2])                    # switched: the warning goes


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


class Slack(AdminCase):
    def test_page_renders_and_the_nav_links_to_it(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/slack", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn('href="/slack"', html)
        self.assertIn("not set", html)

    def test_slack_token_files_default_next_to_the_github_token(self):
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual(Path(cfg.slack_bot_token_file).name, "slack_bot_token")
        self.assertEqual(Path(cfg.slack_bot_token_file).parent, Path(cfg.token_file).parent)

    def test_tokens_must_have_the_right_prefix_and_are_stored_privately(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "slack_bot", "value": SLACK_APP})[0], 400)    # the wrong token
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "slack_app", "value": SLACK_BOT})[0], 400)
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "slack_bot", "value": SLACK_BOT})[0], 303)
        self.assertEqual(self.post(cookie, csrf, "/credentials/save", {"name": "slack_app", "value": SLACK_APP})[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual(Path(cfg.slack_bot_token_file).read_text(), SLACK_BOT)
        self.assertEqual(Path(cfg.slack_app_token_file).stat().st_mode & 0o777, 0o600)
        self.assertNotIn(SLACK_BOT, self.req("GET", "/credentials", cookie=cookie)[2])

    def test_save_level_or_events_and_validate_ids(self):
        cookie, csrf = self.session()
        base = [("slack.channel", "C0123ABCDEF"), ("slack.user_id", "U0123ABCDEF")]
        self.assertEqual(self.post(cookie, csrf, "/slack/save", base + [("slack.verbosity", "quiet"), ("slack.mode", "level")])[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.slack_channel, cfg.slack_user_id, cfg.slack_verbosity, cfg.slack_events), ("C0123ABCDEF", "U0123ABCDEF", "quiet", None))
        ev = base + [("slack.verbosity", "quiet"), ("slack.mode", "events"), ("slack.events", "failure"), ("slack.events", "pr_ready")]
        self.assertEqual(self.post(cookie, csrf, "/slack/save", ev)[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).slack_events, ("failure", "pr_ready"))
        self.assertEqual(self.post(cookie, csrf, "/slack/save", [("slack.channel", ""), ("slack.user_id", ""), ("slack.verbosity", "normal"), ("slack.mode", "level")])[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.slack_channel, cfg.slack_user_id, cfg.slack_events), (None, None, None))           # empty turns Slack off
        for bad in ([("slack.channel", "general"), ("slack.verbosity", "quiet")], [("slack.user_id", "alice"), ("slack.verbosity", "quiet")],
                    [("slack.user_id", "C0123ABCDEF"), ("slack.verbosity", "quiet")], [("slack.verbosity", "loud")],
                    [("slack.verbosity", "quiet"), ("slack.mode", "events")]):
            self.assertEqual(self.post(cookie, csrf, "/slack/save", bad)[0], 422, bad)

    def test_people_recorded_by_the_orchestrator_can_be_chosen_as_the_user(self):
        cookie, csrf = self.session()
        dbm.set_status(self.db, "slack_unknown_senders", '[{"id": "U0777ABCDEF", "name": "Eve", "ts": 1}]')
        _, _, html = self.req("GET", "/slack", cookie=cookie)
        self.assertIn("Eve", html)
        self.assertEqual(self.post(cookie, csrf, "/slack/use", {"user_id": "U0777ABCDEF"})[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).slack_user_id, "U0777ABCDEF")
        self.assertEqual(self.post(cookie, csrf, "/slack/use", {"user_id": "nobody"})[0], 400)

    def test_test_message_needs_token_and_channel_and_scrubs_errors(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/slack/test")[0], 400)
        self.post(cookie, csrf, "/credentials/save", {"name": "slack_bot", "value": SLACK_BOT})
        self.assertEqual(self.post(cookie, csrf, "/slack/test")[0], 400)                                       # still no channel
        self.post(cookie, csrf, "/slack/save", [("slack.channel", "C0123ABCDEF"), ("slack.verbosity", "normal"), ("slack.mode", "level")])
        sent = {}

        def fake(url, token=None, data=None, timeout=15, bearer=True):
            sent.update(data or {})
            return 200, {"ok": True}
        with mock.patch.object(I, "_http", fake):
            s, _, html = self.post(cookie, csrf, "/slack/test")
        self.assertEqual((s, sent["channel"]), (200, "C0123ABCDEF"))
        self.assertIn("Sent.", html)
        with mock.patch.object(I, "_http", lambda *a, **k: (200, {"ok": False, "error": "not_in_channel"})):
            _, _, html = self.post(cookie, csrf, "/slack/test")
        self.assertIn("/invite", html)

    def test_the_page_walks_through_the_whole_set_up(self):
        cookie, _ = self.session()
        _, _, html = self.req("GET", "/slack", cookie=cookie)
        for step in ("Create the app in Slack", "manifest_json=", "connections:write", 'action="/slack/token"', "/factory", 'action="/slack/save"', 'action="/slack/test"'):
            self.assertIn(step, html)
        self.assertIn("Slack is off", html)

    def test_tokens_are_saved_and_tested_from_the_slack_page(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/slack/token", {"name": "slack_bot", "value": SLACK_APP})[0], 400)       # the wrong token
        self.assertEqual(self.post(cookie, csrf, "/slack/token", {"name": "github", "value": TOKEN})[0], 400)            # only Slack's tokens here
        self.assertEqual(self.post(cookie, csrf, "/slack/check", {"name": "slack_app"})[0], 400)                         # nothing saved yet
        s, headers, _ = self.post(cookie, csrf, "/slack/token", {"name": "slack_bot", "value": SLACK_BOT})
        self.assertEqual((s, headers["Location"]), (303, "/slack?ok=slack_token"))
        self.assertEqual(self.post(cookie, csrf, "/slack/token", {"name": "slack_app", "value": SLACK_APP})[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((Path(cfg.slack_bot_token_file).read_text(), Path(cfg.slack_app_token_file).read_text()), (SLACK_BOT, SLACK_APP))
        self.assertTrue(any(self.root.rglob("RESTART")))                                                      # the factory reconnects with them
        _, _, html = self.req("GET", "/slack", cookie=cookie)
        self.assertNotIn(SLACK_BOT, html)
        self.assertIn("not connected yet", html)
        with mock.patch.object(I, "_http", lambda *a, **k: (200, {"ok": True, "url": "wss://x"})):
            s, _, html = self.post(cookie, csrf, "/slack/check", {"name": "slack_app"})
        self.assertEqual(s, 200)
        self.assertIn("Socket Mode is on", html)
        with mock.patch.object(I, "_http", lambda *a, **k: (200, {"ok": False, "error": "invalid_auth"})):
            _, _, html = self.post(cookie, csrf, "/slack/check", {"name": "slack_bot"})
        self.assertIn("invalid_auth", html)
        self.assertNotIn(SLACK_APP, html)

    def test_use_this_sets_the_member_and_the_channel_they_typed_in(self):
        cookie, csrf = self.session()
        dbm.set_status(self.db, "slack_unknown_senders", '[{"id": "U0777ABCDEF", "name": "Me", "channel": "C0555ABCDEF", "ts": 1}]')
        dbm.set_status(self.db, "slack_connection", '{"ok": true, "error": ""}')
        _, _, html = self.req("GET", "/slack", cookie=cookie)
        self.assertIn("C0555ABCDEF", html)
        self.assertIn("connected", html)
        self.assertEqual(self.post(cookie, csrf, "/slack/use", {"user_id": "U0777ABCDEF", "channel": "C0555ABCDEF"})[0], 303)
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual((cfg.slack_user_id, cfg.slack_channel), ("U0777ABCDEF", "C0555ABCDEF"))
        self.assertEqual(self.post(cookie, csrf, "/slack/use", {"user_id": "U0777ABCDEF", "channel": "#general"})[0], 400)
        self.assertIn("Slack is on", self.req("GET", "/slack", cookie=cookie)[2])

    def test_the_ui_address_is_saved_and_checked(self):
        cookie, csrf = self.session()
        base = [("slack.verbosity", "normal"), ("slack.mode", "level")]
        self.assertEqual(self.post(cookie, csrf, "/slack/save", base + [("slack.ui_url", "https://f.example/")])[0], 303)
        self.assertEqual(load(str(self.root / "config.toml")).slack_ui_url, "https://f.example")
        self.assertEqual(self.post(cookie, csrf, "/slack/save", base + [("slack.ui_url", "javascript:alert(1)")])[0], 422)
        self.assertEqual(self.post(cookie, csrf, "/slack/save", base + [("slack.ui_url", "")])[0], 303)
        self.assertIsNone(load(str(self.root / "config.toml")).slack_ui_url)

    def test_credential_test_checks_the_bot_token(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/credentials/save", {"name": "slack_bot", "value": SLACK_BOT})
        with mock.patch.object(I, "_http", lambda *a, **k: (200, {"ok": True, "team": "Acme", "user": "shikumi"})):
            s, _, html = self.post(cookie, csrf, "/credentials/test", {"name": "slack_bot"})
        self.assertEqual(s, 200)
        self.assertIn("Acme", html)
        with mock.patch.object(I, "_http", lambda *a, **k: (200, {"ok": False, "error": "invalid_auth"})):
            _, _, html = self.post(cookie, csrf, "/credentials/test", {"name": "slack_bot"})
        self.assertIn("invalid_auth", html)
        self.assertNotIn(SLACK_BOT, html)


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
        self.gh.create_ticket.assert_called_once_with(self.REPO, "A <b>", "d", [])
        self.post(cookie, csrf, "/tickets/create", {"repo": self.REPO, "title": "A <b>"})
        self.gh.create_ticket.assert_called_once()
        self.gh.add_labels.assert_not_called()

    def test_create_ticket_start_choice_is_allow_listed(self):
        self.gh.create_ticket.return_value = {"number": 43}
        cookie, csrf = self.session()
        for bad in ("factory:ready", "build", "auto,analyst", "evil"):
            self.post(cookie, csrf, "/tickets/create", {"repo": self.REPO, "title": "t" + bad, "start": bad})
        self.gh.create_ticket.assert_not_called()
        self.assertEqual(self.post(cookie, csrf, "/tickets/create", {"repo": self.REPO, "title": "Go", "start": "auto"})[0], 303)
        self.gh.create_ticket.assert_called_once_with(self.REPO, "Go", "", ["factory:auto"])

    def test_close_a_busy_ticket_clears_its_triggers_and_refuses_a_closed_one(self):
        cookie, csrf = self.session()
        busy = {"number": 7, "title": "T", "state": "open", "labels": [{"name": "factory:working"}, {"name": "factory:auto"}]}
        self.gh.get_issue.return_value = busy
        self.assertEqual(self.post(cookie, csrf, "/tickets/close", self.fields())[0], 303)
        self.gh.comment.assert_called_once_with(self.REPO, 7, L.CLOSE_BUSY_COMMENT)
        self.gh.remove_label.assert_called_once_with(self.REPO, 7, "factory:auto")      # the working label is the orchestrator's
        self.gh.update_issue.assert_called_once_with(self.REPO, 7, state="closed")
        for m in (self.gh.comment, self.gh.remove_label, self.gh.update_issue):
            m.reset_mock()
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "closed", "labels": []}
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
        self.assertNotIn('value="auto"', html.split("<tbody>")[1])                 # the row's buttons (New ticket's Start list has Auto)
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
        self.gh.remove_label.assert_called_once_with(self.REPO, 7, "stage:architected")   # the stage that asked runs again
        self.gh.add_labels.assert_called_once_with(self.REPO, 7, ["factory:architect"])
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
        row = html.split("<tbody>")[1]                                             # not New ticket's Start list, which has every stage
        self.assertNotIn('value="analyst"', row)
        self.assertIn('value="designer"', row)

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
        self.assertEqual([n for _, n in views.NAV], ["Factory", "Tickets", "Events", "Chat", "Roadmap", "Screens", "Tests", "Evaluate", "Settings"])
        cookie, _ = self.session()
        for path, side in (("/harnesses", "Harnesses"), ("/credentials", "Credentials"), ("/telegram", "Telegram"), ("/settings?section=labels", "Labels")):
            s, _, html = self.req("GET", path, cookie=cookie)
            self.assertEqual(s, 200, path)
            nav = html.split('<nav aria-label="Main">')[1].split("</nav>")[0]
            self.assertIn('<a href="/settings" class="active ">Settings</a>', nav, path)
            self.assertNotIn('href="/harnesses"', nav)
            self.assertRegex(html, rf'<a href="{re.escape(path)}" class=active aria-current=page>(<span class="sdot[^"]*" aria-hidden="true"></span>)?{side}<', path)
            for item in ("General", "Models by ticket size", "Stage agents", "Projects", "Harnesses", "Credentials", "Telegram", "Labels", "Health alerts"):
                self.assertIn(f">{item}<", html)

    def test_general_is_rows_with_one_save_button(self):
        cookie, _ = self.session()
        _, _, html = self.req("GET", "/settings?section=general", cookie=cookie)
        self.assertIn("Save changes", html)
        self.assertIn("Changes apply the next time the factory is idle", html)
        self.assertEqual(html.count('class="srow'), 15)                       # the standalone repositories are their own part (sr-sec), not a row
