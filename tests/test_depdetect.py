import json
import unittest
from unittest import mock

from factory import db as dbm
from factory import depdetect
from factory import main as m
from factory.config import Project, ProjectRepo, PublishCfg, load
from factory.ui import forms
from test_roles import CFG
from test_ui_admin import AdminCase

MANUAL = "name: publish\non:\n  workflow_dispatch:\n    inputs: {}\npermissions:\n  packages: write\njobs:\n  p:\n    steps:\n      - run: pnpm publish --no-git-checks\n"
CI = "on:\n  push:\n    branches: [main]\njobs:\n  t:\n    steps:\n      - run: pnpm test\n"
REPOS = {
    "o/pkg": {"package.json": {"name": "root", "private": True, "devDependencies": {"typescript": "5"}},
              "packages/ui/package.json": {"name": "@o/ui", "dependencies": {"@o/utils": "1"}},
              "packages/utils/package.json": {"name": "@o/utils"},
              "packages/ui/node_modules/x/package.json": {"name": "@o/hidden"},
              ".github/workflows/ci.yml": CI, ".github/workflows/publish.yml": MANUAL},
    "o/web": {"package.json": {"name": "web-root", "private": True}, "web/package.json": {"name": "web", "private": True, "dependencies": {"@o/ui": "^1"}}},
    "o/mobile": {"package.json": {"name": "app", "private": True, "dependencies": {"react": "19"}}},
}
PROJ = Project("ln", (ProjectRepo("o/pkg"), ProjectRepo("o/web"), ProjectRepo("o/mobile")))


class FakeGH:
    def __init__(self, repos=REPOS, releases=(), tags=()):
        self.repos, self.rel, self.tg = repos, list(releases), list(tags)

    def default_branch(self, repo): return "main"
    def tree(self, repo, ref): return list(self.repos[repo])
    def raw_file(self, repo, path, ref, max_bytes=0):
        v = self.repos[repo][path]
        return (v if isinstance(v, str) else json.dumps(v)).encode()
    def releases(self, repo, n=10): return self.rel
    def tags(self, repo, n=10): return self.tg


class Detect(unittest.TestCase):
    def test_finds_the_publisher_its_workflow_and_who_uses_it(self):
        sug, problems = depdetect.detect(FakeGH(), PROJ)
        self.assertEqual(problems, [])
        self.assertEqual(len(sug), 1)
        s = sug[0]
        self.assertEqual((s.publisher, s.detect, s.workflow, s.manual), ("o/pkg", "workflow", "publish.yml", True))
        self.assertEqual(s.consumers, {"o/web": ["@o/ui"]})                 # mobile uses nothing of it; node_modules is ignored
        self.assertEqual(s.packages, ["@o/ui"])
        self.assertIn("someone runs it by hand", s.note)

    def test_without_a_publishing_workflow_it_falls_back_to_releases_then_tags(self):
        repos = {**REPOS, "o/pkg": {k: v for k, v in REPOS["o/pkg"].items() if "publish" not in k}}
        self.assertEqual(depdetect.detect(FakeGH(repos, releases=[{"tag_name": "v1"}]), PROJ)[0][0].detect, "release")
        self.assertEqual(depdetect.detect(FakeGH(repos, tags=[{"name": "v1"}]), PROJ)[0][0].detect, "tag")
        none = depdetect.detect(FakeGH(repos), PROJ)[0][0]
        self.assertEqual(none.detect, "release")
        self.assertIn("check how it publishes", none.note)

    def test_an_unreadable_repo_is_a_problem_not_a_crash(self):
        class Broken(FakeGH):
            def tree(self, repo, ref):
                if repo == "o/web":
                    raise OSError("boom")
                return super().tree(repo, ref)
        sug, problems = depdetect.detect(Broken(), PROJ)
        self.assertEqual(sug, [])
        self.assertEqual(problems, ["could not read o/web (OSError)"])

    def test_workflow_triggers(self):
        self.assertEqual(depdetect.triggers(MANUAL), {"workflow_dispatch"})
        self.assertEqual(depdetect.triggers(CI), {"push"})
        self.assertEqual(depdetect.triggers("on: [push, workflow_dispatch]\n"), {"push", "workflow_dispatch"})
        self.assertEqual(depdetect.triggers("'on':\n  # x\n  release:\n    types: [published]\n  workflow_dispatch:\njobs: {}\n"),
                         {"release", "workflow_dispatch"})

    def test_is_applied_compares_with_the_config(self):
        s = depdetect.detect(FakeGH(), PROJ)[0][0]
        self.assertFalse(depdetect.is_applied(PROJ, s))
        done = Project("ln", (ProjectRepo("o/pkg", publish=PublishCfg("workflow", "publish.yml", ("@o/ui",))),
                              ProjectRepo("o/web", depends_on=("o/pkg",)), ProjectRepo("o/mobile")))
        self.assertTrue(depdetect.is_applied(done, s))


class Card(unittest.TestCase):
    def eff(self, **pkg):
        return {"projects": [{"name": "ln", "repos": [{"repo": "o/pkg", **pkg}, {"repo": "o/web", **({"depends_on": ["pkg"]} if pkg else {})},
                                                      {"repo": "o/mobile"}]}]}

    def test_shows_the_suggestion_with_apply_and_the_lockfile_choice(self):
        html = forms.build_order(self.eff(), "tok", {"ln": depdetect.detect(FakeGH(), PROJ)})
        self.assertIn("No build order set", html)
        self.assertIn("<strong>Suggested:</strong> pkg publishes packages (web uses 1 of its packages)", html)
        self.assertIn('name="workflow" value="publish.yml"', html)
        self.assertIn('name="lockfiles" value="1" checked', html)
        self.assertIn("someone runs publish.yml", html)
        self.assertIn("Look again", html)

    def test_shows_what_is_set_and_marks_a_finding_already_set(self):
        pub = {"publish": {"detect": "workflow", "workflow": "publish.yml", "packages": ["@o/ui"]}}
        html = forms.build_order(self.eff(**pub), "tok", {"ln": depdetect.detect(FakeGH(), PROJ)}, "no worker seen. On a worker run: X")
        self.assertIn("<strong>pkg</strong> publishes by the publish.yml workflow, then <strong>web</strong> is built against it.", html)
        self.assertIn("already set", html)
        self.assertNotIn("Suggested:", html)
        self.assertIn("Lockfile refresh:</strong> no worker seen", html)

    def test_no_projects_no_card(self):
        self.assertEqual(forms.build_order({}, "tok"), "")


class Pages(AdminCase):
    def test_detect_apply_and_remove_from_the_projects_page(self):
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/settings?section=projects", cookie=cookie)
        self.assertIn("Build order", html)
        self.assertIn("<strong>shop-packages</strong> publishes with a GitHub release", html)       # from config.example.toml
        shop = load(str(self.root / "config.toml")).projects[0]
        found = depdetect.Suggestion("your-org/shop-packages", "workflow", "publish.yml", ["@your-org/ui"],
                                     {"your-org/shop-web": ["@your-org/ui"], "your-org/shop-mobile": ["@your-org/ui"]}, True, "publish.yml publishes")
        with mock.patch.object(depdetect, "detect", return_value=([found], [])) as det, \
                mock.patch("factory.ui.integrations.read_secret", return_value="tok"):
            s, _, html = self.post(cookie, csrf, "/settings/projects/detect", {"project": "shop"})
        self.assertEqual(s, 200)
        self.assertEqual(det.call_args.args[1], shop)
        self.assertIn("Found 1 package link in shop.", html)
        self.assertIn("Suggested:", html)
        s, h, _ = self.post(cookie, csrf, "/settings/projects/deps", {"project": "shop", "action": "apply", "publisher": found.publisher,
                                                                       "detect": "workflow", "workflow": "publish.yml", "packages": "@your-org/ui",
                                                                       "consumers": "your-org/shop-web,your-org/shop-mobile", "lockfiles": "1"})
        self.assertEqual(s, 303)
        cfg = load(str(self.root / "config.toml"))
        p = cfg.projects[0]
        self.assertEqual(p.repo("your-org/shop-packages").publish, PublishCfg("workflow", "publish.yml", ("@your-org/ui",)))
        self.assertEqual(p.repo("your-org/shop-mobile").depends_on, ("your-org/shop-packages",))
        self.assertEqual(p.repo("your-org/shop-web").depends_on, ("your-org/shop-packages",))      # not listed twice
        self.assertEqual(sorted(x.repo for x in cfg.workers.lockfiles), ["your-org/shop-mobile", "your-org/shop-web"])
        _, _, html = self.req("GET", "/settings?section=projects", cookie=cookie)
        self.assertIn("verification workers are off", html)                                       # the lockfile hint
        s, _, _ = self.post(cookie, csrf, "/settings/projects/deps", {"project": "shop", "action": "remove", "publisher": "your-org/shop-packages"})
        self.assertEqual(s, 303)
        p = load(str(self.root / "config.toml")).projects[0]
        self.assertIsNone(p.repo("your-org/shop-packages").publish)
        self.assertEqual([r.depends_on for r in p.repos], [(), (), ()])

    def test_apply_checks_the_fields_again(self):
        cookie, csrf = self.session()
        for bad in ({"publisher": "evil/x"}, {"consumers": "your-org/shop-packages"}, {"detect": "npm"}, {"workflow": "../x.yml"},
                    {"packages": "not a name!"}):
            fields = {"project": "shop", "action": "apply", "publisher": "your-org/shop-packages", "detect": "workflow", "workflow": "p.yml",
                      "packages": "@your-org/ui", "consumers": "your-org/shop-web", **bad}
            s, _, _ = self.post(cookie, csrf, "/settings/projects/deps", fields)
            self.assertEqual(s, 422, bad)
        self.assertNotIn("projects", self.overrides())


class Cli(AdminCase):
    def test_deps_prints_and_applies(self):
        path = str(self.root / "config.toml")
        cfg = load(path)
        found = depdetect.Suggestion("your-org/shop-packages", "workflow", "publish.yml", ["@your-org/ui"],
                                     {"your-org/shop-mobile": ["@your-org/ui"]}, False, "publish.yml publishes")
        lines = []
        with mock.patch.object(depdetect, "detect", return_value=([found], [])):
            from factory import ctl
            self.assertEqual(ctl.deps_cmd(cfg, None, path, [], lines.append), 0)
            self.assertIn("Run with --apply", lines[-1])
            self.assertNotIn("projects", self.overrides())
            ctl.deps_cmd(cfg, None, path, ["--apply"], lines.append)
        self.assertIn("applied", lines[-1])
        self.assertEqual(load(path).projects[0].repo("your-org/shop-mobile").depends_on, ("your-org/shop-packages",))


class Alert(unittest.TestCase):
    def test_says_so_once_per_finding_and_at_most_daily(self):
        from dataclasses import replace
        cfg, conn, sent = replace(CFG, projects=(PROJ,)), dbm.connect(":memory:"), []
        with mock.patch.object(m, "alert", lambda text, buttons=None, event="info": sent.append(text)):
            m.suggest_build_order(cfg, FakeGH(), conn, now=1000.0)
            m.suggest_build_order(cfg, FakeGH(), conn, now=2000.0)                     # too soon: not even looked at
            m.suggest_build_order(cfg, FakeGH(), conn, now=1000.0 + m.DEPDETECT_EVERY)  # looked at again, already said
        self.assertEqual(len(sent), 1)
        self.assertIn("web installs packages that pkg publishes", sent[0])
        self.assertIn("Settings -> Projects -> Build order -> Apply", sent[0])


if __name__ == "__main__":
    unittest.main()
