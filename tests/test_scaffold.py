import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import tomllib

from factory import main
from factory import newproject as N
from factory import scaffold
from factory.config import overrides_path
from test_newproject import PLAN
from test_ui import UiCase

SPA = {**PLAN, "pattern": "spa-api", "deploy": "cloudflare-fly", "name": "rota"}


def sh(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Templates(unittest.TestCase):
    def test_every_kind_and_overlay_the_catalogue_names_exists(self):
        for p in N.CATALOGUE.values():
            for _, _, kind in p.repos:
                self.assertTrue((scaffold.TEMPLATES / "kinds" / kind).is_dir(), kind)
                self.assertTrue((scaffold.TEMPLATES / "verify" / f"{kind}.sh").is_file(), kind)
                for deploy in p.deploys:
                    top = scaffold.TEMPLATES / "deploy" / N.overlay(deploy, kind) / kind
                    self.assertTrue(top.is_dir(), (p.id, deploy, kind))

    def test_every_template_renders_with_ci_a_deploy_or_release_workflow_and_no_leftover_placeholders(self):
        for p in N.CATALOGUE.values():
            for deploy in p.deploys:
                plan = {**PLAN, "pattern": p.id, "deploy": deploy, "name": "team-rota"}
                repos = N.repo_plan(plan, "acme")
                for i, r in enumerate(repos):
                    files = scaffold.files_for(plan, "Team Rota", repos, i)
                    where = (p.id, deploy, r["kind"])
                    self.assertIn(".github/workflows/ci.yml", files, where)
                    self.assertTrue({".github/workflows/deploy.yml", ".github/workflows/release.yml"} & set(files), where)
                    for name in ("CLAUDE.md", "README.md", "DEPLOY.md", ".github/dependabot.yml", ".gitignore"):
                        self.assertIn(name, files, where)
                    self.assertIn("## This project: Team Rota", files["CLAUDE.md"][0].decode(), where)
                    self.assertIn(plan["summary"], files["README.md"][0].decode(), where)
                    for path, (data, _) in files.items():
                        self.assertNotIn("shikumi", path.lower(), where)
                        text = data.decode("utf-8", "ignore").lower()
                        self.assertNotIn("shikumi", text, (where, path))          # every placeholder replaced, and nothing else named after it
                        self.assertFalse(set(Path(path).parts) & scaffold.SKIP, (where, path))


class Rendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name) / "templates"
        (t / "kinds" / "k" / "src" / "shikumi_app").mkdir(parents=True)
        (t / "kinds" / "k" / "src" / "shikumi_app" / "__init__.py").write_text('NAME = "shikumi-app"\nTITLE = "Shikumi App"\n')
        (t / "kinds" / "k" / "README.md").write_text("# Shikumi App\n\n<!-- shikumi:summary -->\n")
        (t / "kinds" / "k" / "CLAUDE.md").write_text("# Conventions\n")
        (t / "kinds" / "k" / "run.sh").write_text("#!/bin/sh\n")
        (t / "kinds" / "k" / "run.sh").chmod(0o755)
        (t / "kinds" / "k" / "icon.png").write_bytes(b"\x89PNG\xff\xfe shikumi-app")
        (t / "kinds" / "k" / "node_modules").mkdir()
        (t / "kinds" / "k" / "node_modules" / "x.js").write_text("junk")
        (t / "deploy" / "o" / "k").mkdir(parents=True)
        (t / "deploy" / "o" / "k" / "deploy.txt").write_text("image ghcr.io/shikumi-org/shikumi-app\n")
        p = mock.patch.object(scaffold, "TEMPLATES", t)
        p.start()
        self.addCleanup(p.stop)

    def test_placeholders_paths_binaries_and_skipped_folders(self):
        out = scaffold.render("k", "o", {"name": "team-rota", "title": "Team Rota", "owner": "acme", "summary": "A rota."})
        self.assertEqual(out["src/team_rota/__init__.py"][0].decode(), 'NAME = "team-rota"\nTITLE = "Team Rota"\n')
        self.assertEqual(out["README.md"][0].decode(), "# Team Rota\n\nA rota.\n")
        self.assertEqual(out["deploy.txt"][0].decode(), "image ghcr.io/acme/team-rota\n")
        self.assertEqual(out["icon.png"][0], b"\x89PNG\xff\xfe shikumi-app")              # binary files are copied as they are
        self.assertTrue(out["run.sh"][1])
        self.assertNotIn("node_modules/x.js", out)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.render("k", "nope", {"name": "a", "title": "A", "owner": "o"})

    def test_titles_are_reduced_to_plain_words(self):
        self.assertEqual(scaffold.plain_title('Team "rota" <script>{x}'), "Team rota script x")
        self.assertEqual(scaffold.plain_title("$$$"), "New project")
        self.assertEqual(scaffold.plain_title("Café & Co's rota"), "Café & Co's rota")


class Pushing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        sh("init", "--bare", "-q", "-b", "main", str(self.remote))
        self.files = {"README.md": (b"# Rota\n", False), ".github/workflows/ci.yml": (b"name: CI\n", False), "bin/run": (b"#!/bin/sh\n", True)}

    def push(self):
        return scaffold.push(str(self.root / "work"), "t", "acme/rota", self.files, "Scaffold rota", url=str(self.remote))

    def seed(self, *names):
        w = self.root / "seed"
        sh("clone", "-q", str(self.remote), str(w))
        for n in names:
            (w / n).write_text("x")
        sh("add", "-A", cwd=w)
        sh("commit", "-q", "-m", "Initial commit", cwd=w)
        sh("push", "-q", "origin", "HEAD:refs/heads/main", cwd=w)

    def test_an_empty_repository_gets_the_first_commit_on_main(self):
        sha = self.push()
        self.assertEqual(sh("rev-parse", "main", cwd=self.remote).strip(), sha)
        self.assertEqual(sh("show", "main:README.md", cwd=self.remote), "# Rota\n")
        self.assertIn("100755 blob", sh("ls-tree", "main", "bin/run", cwd=self.remote))
        self.assertTrue(sh("log", "-1", "--format=%B", "main", cwd=self.remote).strip().endswith(scaffold.SCAFFOLD_TRAILER))
        self.assertEqual(self.push(), "")                                    # done before: skipped, not pushed again
        self.assertEqual(list((self.root / "work").iterdir()), [])          # the clone is cleaned up

    def test_a_readme_and_licence_from_github_are_built_on(self):
        self.seed("README.md", "LICENSE")
        self.push()
        self.assertEqual(sh("rev-list", "--count", "main", cwd=self.remote).strip(), "2")
        self.assertEqual(sh("show", "main:LICENSE", cwd=self.remote), "x")

    def test_a_repository_with_work_in_it_is_refused(self):
        self.seed("README.md", "app.py")
        with self.assertRaises(scaffold.ScaffoldError) as e:
            self.push()
        self.assertIn("not a new repository", str(e.exception))

    def test_files_outside_the_repository_are_refused(self):
        self.files = {"../evil": (b"x", False)}
        with self.assertRaises(scaffold.ScaffoldError):
            self.push()
        self.files = {".git/hooks/post-commit": (b"x", True)}
        with self.assertRaises(scaffold.ScaffoldError):
            self.push()

    def test_push_errors_become_fixed_messages(self):
        self.assertIn("Workflows", scaffold.push_error("refusing to allow a Personal Access Token to create or update workflow `.github/workflows/ci.yml` without `workflow` scope"))
        self.assertIn("protected", scaffold.push_error("remote: error: GH006: Protected branch update failed"))
        self.assertIn("cannot push", scaffold.push_error("remote: Permission to acme/rota.git denied to bot. 403"))
        self.assertIn("meanwhile", scaffold.push_error("! [rejected] HEAD -> main (fetch first)"))


class Lane(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        N.ensure_tables(self.db)
        d = N.create(self.db, "Team rota", "brief")
        N.add_reply(self.db, N.claim(self.db), "```factory-plan\n" + json.dumps(SPA) + "\n```\n", None)
        N.accept(self.db, d, N.draft(self.db, d)["plan"])
        self.d = d
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.token = Path(tmp.name) / "token"
        self.token.write_text("t")

    def cfg(self, **over):
        from factory.config import load
        c = load("config.example.toml")
        return c.__class__(**{**c.__dict__, "dry_run": False, "token_file": str(self.token), **over})

    def test_each_repository_is_pushed_and_a_failure_is_retried_where_it_stopped(self):
        self.assertTrue(N.queue_scaffold(self.db, self.d, "acme", N.repo_plan(N.draft(self.db, self.d)["chosen"], "acme")))
        pushed = []

        def fake_push(work_dir, token, repo, files, message):
            pushed.append(repo)
            if repo == "acme/rota-api" and len(pushed) == 2:
                raise scaffold.ScaffoldError("The default branch is protected.")
            self.assertIn("CLAUDE.md", files)
            return "abc123"
        with mock.patch.object(scaffold, "push", fake_push), mock.patch.object(main, "emit"), \
                mock.patch.object(scaffold, "files_for", lambda plan, title, repos, i: {"CLAUDE.md": (repos[i]["kind"].encode(), False)}):
            main.scaffold_project(self.cfg(), self.db, N.claim_scaffold(self.db))
            d = N.draft(self.db, self.d)
            self.assertEqual((d["status"], [r["state"] for r in d["repos"]]), ("failed", ["done", "failed"]))
            self.assertIn("protected", d["detail"])
            self.assertTrue(N.queue_scaffold(self.db, self.d, "acme", N.repo_plan(d["chosen"], "acme")))
            main.scaffold_project(self.cfg(), self.db, N.claim_scaffold(self.db))
        d = N.draft(self.db, self.d)
        self.assertEqual(pushed, ["acme/rota-web", "acme/rota-api", "acme/rota-api"])
        self.assertEqual((d["status"], [r["sha"] for r in d["repos"]]), ("scaffolded", ["abc123", "abc123"]))
        self.assertEqual([r["overlay"] for r in d["repos"]], ["cloudflare", "fly"])

    def test_no_token_or_dry_run_pushes_nothing(self):
        for cfg in (self.cfg(token_file=""), self.cfg(dry_run=True)):
            N.queue_scaffold(self.db, self.d, "acme", N.repo_plan(N.draft(self.db, self.d)["chosen"], "acme"))
            with mock.patch.object(scaffold, "push") as push:
                main.scaffold_project(cfg, self.db, N.claim_scaffold(self.db))
            push.assert_not_called()
            self.assertEqual(N.draft(self.db, self.d)["status"], "failed")

    def test_a_restart_queues_an_interrupted_push_again(self):
        N.queue_scaffold(self.db, self.d, "acme", N.repo_plan(N.draft(self.db, self.d)["chosen"], "acme"))
        N.claim_scaffold(self.db)
        N.mark_interrupted(self.db)
        self.assertEqual(N.draft(self.db, self.d)["status"], "queued")


class Pages(UiCase):
    def setUp(self):
        super().setUp()
        overrides_path(str(self.root / "config.toml")).write_text("[general]\ndry_run = false\n[local]\nenabled = true\n")
        db = sqlite3.connect(self.db_path)
        N.ensure_tables(db)
        d = N.create(db, "Team rota", "brief")
        N.add_reply(db, N.claim(db), "```factory-plan\n" + json.dumps(SPA) + "\n```\n", None)
        N.accept(db, d, N.draft(db, d)["plan"])
        self.d, self.rw = d, db

    def post(self, cookie, csrf, path, fields):
        return self.req("POST", path, urlencode([("csrf", csrf)] + list(fields.items())), cookie=cookie)

    def test_create_check_push_and_start(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/projects/draft/owner", {"id": str(self.d), "owner": "acme"})
        html = self.req("GET", f"/projects/draft?id={self.d}", cookie=cookie)[2]
        self.assertIn("https://github.com/new?owner=acme&amp;name=rota-web", html)
        self.assertIn("Workflows", html)
        states = {"acme/rota-web": {"exists": True, "push": True, "commits": 1, "files": ["README.md"]},
                  "acme/rota-api": {"exists": False, "push": False, "commits": 0, "files": []}}
        gh = mock.Mock()
        gh.new_repo_state.side_effect = lambda r: states[r]
        with mock.patch("factory.ui.labels._gh", return_value=gh):
            self.post(cookie, csrf, "/projects/draft/scaffold", {"id": str(self.d), "owner": "acme"})
            self.assertEqual(N.draft(self.rw, self.d)["status"], "accepted")
            self.assertIn("Not found. Create it", self.req("GET", f"/projects/draft?id={self.d}", cookie=cookie)[2])
            states["acme/rota-api"] = {"exists": True, "push": True, "commits": 0, "files": []}
            self.post(cookie, csrf, "/projects/draft/scaffold", {"id": str(self.d), "owner": "acme"})
        d = N.draft(self.rw, self.d)
        self.assertEqual((d["status"], d["owner"], [r["repo"] for r in d["repos"]]), ("queued", "acme", ["acme/rota-web", "acme/rota-api"]))
        self.assertIn('data-pending="1"', self.req("GET", f"/fragment/interview?id={self.d}", cookie=cookie)[2])

        N.claim_scaffold(self.rw)
        N.save_repos(self.rw, self.d, [{**r, "state": "done", "sha": "abc1234"} for r in d["repos"]])
        N.scaffold_done(self.rw, self.d, True)
        html = self.req("GET", f"/projects/draft?id={self.d}", cookie=cookie)[2]
        self.assertIn("https://github.com/acme/rota-web/commit/abc1234", html)
        self.post(cookie, csrf, "/projects/draft/finish", {"id": str(self.d)})
        ov = tomllib.loads(overrides_path(str(self.root / "config.toml")).read_text())
        project = ov["projects"][-1]
        self.assertEqual((project["name"], [r["repo"] for r in project["repos"]]), ("Team rota", ["acme/rota-web", "acme/rota-api"]))
        self.assertTrue((self.state / "RESTART").exists())
        d = N.draft(self.rw, self.d)
        self.assertEqual(d["status"], "done")
        repo, _, n = d["ticket"].rpartition("#")
        title, labels = self.rw.execute("SELECT title FROM local_tickets WHERE repo=? AND number=?", (repo, int(n))).fetchone()[0], \
            [r[0] for r in self.rw.execute("SELECT label FROM local_labels WHERE repo=? AND number=?", (repo, int(n)))]
        self.assertEqual((repo, title), ("acme/rota-web", "Build the first features of Team rota"))
        self.assertIn("factory:auto", labels)
        self.assertIn("DEPLOY.md", self.req("GET", f"/projects/draft?id={self.d}", cookie=cookie)[2])

    def test_without_local_tickets_the_first_ticket_is_a_github_issue(self):
        overrides_path(str(self.root / "config.toml")).write_text("[general]\ndry_run = false\n")
        N.queue_scaffold(self.rw, self.d, "acme", N.repo_plan(N.draft(self.rw, self.d)["chosen"], "acme"))
        N.claim_scaffold(self.rw)
        N.scaffold_done(self.rw, self.d, True)
        cookie, csrf = self.session()
        gh = mock.Mock()
        gh.create_ticket.return_value = {"number": 1}
        with mock.patch("factory.ui.labels._gh", return_value=gh):
            self.post(cookie, csrf, "/projects/draft/finish", {"id": str(self.d)})
        repo, title, body, labels = gh.create_ticket.call_args[0]
        self.assertEqual((repo, labels), ("acme/rota-web", ["factory:auto"]))
        self.assertIn("## First features", body)
        self.assertEqual(N.draft(self.rw, self.d)["ticket"], "acme/rota-web#1")

    def test_bad_owner_and_wrong_states_are_refused(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/projects/draft/owner", {"id": str(self.d), "owner": "-bad-"})
        self.assertIsNone(N.draft(self.rw, self.d)["owner"])
        self.post(cookie, csrf, "/projects/draft/finish", {"id": str(self.d)})
        self.assertEqual(N.draft(self.rw, self.d)["status"], "accepted")
        overrides_path(str(self.root / "config.toml")).write_text("[general]\ndry_run = true\n")
        self.post(cookie, csrf, "/projects/draft/scaffold", {"id": str(self.d), "owner": "acme"})
        self.assertEqual(N.draft(self.rw, self.d)["status"], "accepted")
