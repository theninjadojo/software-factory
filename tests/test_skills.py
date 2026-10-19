"""Skills (factory/skills.py): the operator's [[skills]], the factory's own design-mockup skill, how a run gets them, and the
prompt split into the trusted rules (/task/system.txt) and the ticket (/task/user.txt)."""
import dataclasses
import tempfile
import tomllib
import unittest
from pathlib import Path

from factory import skills as sk
from factory.config import CLAUDE_COMMAND, load, OLD_CLAUDE_COMMAND, Project, ProjectRepo, SkillCfg, parse
from factory.runner import build_prompt, build_prompt_parts, write_prompt
from factory.tomlw import dumps
import test_runner
from test_prompts import raw
from test_ui_admin import AdminCase

PR = Project("proj", (ProjectRepo("o/web", "web"),))
GOOD = {"name": "deploy-steps", "description": "How the api is deployed: use when a ticket touches deployment.", "body": "# Deploy\n\nRun make deploy."}


class Config_(unittest.TestCase):
    def test_absent_means_none_and_entries_parse(self):
        self.assertEqual(parse(raw()).skills, ())
        r = raw()
        r["skills"] = [GOOD, {**GOOD, "name": "mig", "body": "a\r\nb", "agents": ["implementer", "implementer"], "projects": ["proj"]}]
        a, b = parse(r).skills
        self.assertEqual(a, SkillCfg("deploy-steps", GOOD["description"], GOOD["body"]))
        self.assertEqual((b.body, b.agents, b.projects), ("a\nb", ("implementer",), ("proj",)))

    def test_bad_entries_are_rejected(self):
        for bad in ({**GOOD, "name": "Deploy"}, {**GOOD, "name": "factory-x"}, {**GOOD, "name": "-x"}, {**GOOD, "name": "x" * 64},
                    {**GOOD, "description": ""}, {**GOOD, "description": "two\nlines"}, {**GOOD, "description": "x" * 501},
                    {**GOOD, "body": " "}, {**GOOD, "body": "x" * 20001}, {**GOOD, "body": "bell\x07"},
                    {**GOOD, "agents": ["pm"]}, {**GOOD, "agents": "implementer"}, {**GOOD, "projects": [""]}, {**GOOD, "extra": 1}):
            r = raw()
            r["skills"] = [bad]
            with self.assertRaises(ValueError, msg=bad):
                parse(r)
        r = raw()
        r["skills"] = [GOOD, GOOD]
        with self.assertRaisesRegex(ValueError, "twice"):
            parse(r)
        r["skills"] = [{**GOOD, "name": f"s{i}"} for i in range(31)]
        with self.assertRaisesRegex(ValueError, "at most 30"):
            parse(r)

    def test_a_saved_old_claude_command_becomes_the_split_one(self):
        r = raw()
        r["harnesses"] = {"claude-code": {"command": OLD_CLAUDE_COMMAND}}
        self.assertEqual(parse(r).harnesses["claude-code"].command, CLAUDE_COMMAND)
        r["harnesses"] = {"claude-code": {"command": "claude -p \"$(cat /task/prompt.txt)\" --model x"}}
        self.assertEqual(parse(r).harnesses["claude-code"].command, "claude -p \"$(cat /task/prompt.txt)\" --model x")


class Selection(unittest.TestCase):
    def cfg(self, *skills):
        r = raw()
        r["skills"] = list(skills)
        return parse(r)

    def test_agents_and_projects_narrow_where_a_skill_is_offered(self):
        cfg = self.cfg(GOOD, {**GOOD, "name": "impl-only", "agents": ["implementer"]}, {**GOOD, "name": "other", "projects": ["other"]})
        self.assertEqual([s.name for s in sk.for_run(cfg, "implementer", "proj")], ["deploy-steps", "impl-only"])
        self.assertEqual([s.name for s in sk.for_run(cfg, "analyst", "proj")], ["deploy-steps"])
        self.assertEqual([s.name for s in sk.for_run(cfg, "analyst", "other")], ["deploy-steps", "other"])
        self.assertEqual([s.name for s in sk.for_run(cfg, "designer", "proj", design_files=True)], ["factory-design-mockups", "deploy-steps"])

    def test_the_file_has_front_matter_that_survives_awkward_descriptions(self):
        s = sk.Skill("x", 'Use it: when "quoted" — and # not a comment', "Body")
        with tempfile.TemporaryDirectory() as t:
            sk.write(Path(t), [s])
            text = (Path(t) / "skills" / "x" / "SKILL.md").read_text()
        front = text.split("---\n")[1]
        self.assertEqual(front, 'name: x\ndescription: "Use it: when \\"quoted\\" \\u2014 and # not a comment"\n')
        self.assertTrue(text.endswith("---\n\nBody\n"))


class Prompt(unittest.TestCase):
    def test_parts_join_into_the_whole_prompt_and_split_before_untrusted_text(self):
        for role in (None, "analyst", "designer", "reviewer"):
            system, user = build_prompt_parts("T", "B </issue>", PR, "o/web", role, {"analysis": "prior"}, ["c"], None, ("o/web", 3),
                                              operator="Be brief.")
            self.assertEqual(build_prompt("T", "B </issue>", PR, "o/web", role, {"analysis": "prior"}, ["c"], None, ("o/web", 3),
                                          operator="Be brief."), system + "\n\n" + user)
            self.assertTrue(system.startswith("<operator_instructions>"))
            for untrusted in ("<issue>", "<discussion>", "<prior_stage_outputs>", "B "):
                self.assertNotIn(untrusted, system)
            self.assertTrue(user.startswith("<prior_stage_outputs>"))
        system, user = build_prompt_parts("x", "", PR, "o/web", "pm", backlog=[{"number": 1, "title": "t", "labels": [], "body": "b"}])
        self.assertNotIn("<backlog>", system)
        self.assertIn("<backlog>", user)

    def test_no_skills_leave_the_prompt_unchanged_and_skills_are_listed_after_the_rules(self):
        before = build_prompt("t", "b", PR, "o/web")
        self.assertEqual(build_prompt("t", "b", PR, "o/web", skills=[]), before)
        self.assertNotIn("SKILLS.", before)
        system, _ = build_prompt_parts("t", "b", PR, "o/web", skills=[sk.Skill("deploy-steps", "How to deploy.", "x")])
        self.assertTrue(system.endswith("- deploy-steps (/task/skills/deploy-steps/SKILL.md): How to deploy."))
        self.assertLess(system.index("Do not commit."), system.index("SKILLS."))

    def test_the_designer_is_pointed_at_the_mockup_skill(self):
        on = build_prompt("t", "b", PR, "o/web", "designer", None, None, None, ("o/web", 3), True, skills=[sk.DESIGN_MOCKUPS])
        self.assertIn("/task/skills/factory-design-mockups/SKILL.md", on)
        self.assertIn("Design files: none", on)
        self.assertNotIn("support.js", on)                   # the format lives in the skill
        self.assertIn("support.js", sk.DESIGN_MOCKUPS.body)

    def test_write_prompt_writes_the_whole_and_both_halves(self):
        with tempfile.TemporaryDirectory() as t:
            write_prompt(Path(t), ("rules", "ticket"))
            self.assertEqual([(Path(t) / f).read_text() for f in ("prompt.txt", "system.txt", "user.txt")], ["rules\n\nticket", "rules", "ticket"])
            write_prompt(Path(t), "just text")
            self.assertEqual([(Path(t) / f).read_text() for f in ("prompt.txt", "system.txt", "user.txt")], ["just text", "", "just text"])


class RunTaskGetsSkills(unittest.TestCase):
    def test_the_run_folder_holds_the_skills_and_the_split_prompt(self):
        from factory import runner
        seen = {}

        def edits(work):
            task = work.parent / "task"
            seen["files"] = sorted(str(p.relative_to(task)) for p in task.rglob("*") if p.is_file())
            seen["system"] = (task / "system.txt").read_text()
            seen["skill"] = (task / "skills" / "deploy-steps" / "SKILL.md").read_text()
            (work / "web" / "a.txt").write_text("two\n")
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, _, p1, p2, _ = test_runner.RunTaskEndToEnd._setup(self, t, edits)
            cfg = dataclasses.replace(cfg, skills=(SkillCfg(**GOOD), SkillCfg("review-only", "x", "y", ("reviewer",))))
            with p1, p2:
                self.assertEqual(runner.run_task(cfg, gh, "o/web", {"number": 5, "title": "x", "body": "y"}, cfg.routes["low"]).status, "pr")
        self.assertEqual(seen["files"], ["prompt.txt", "skills/deploy-steps/SKILL.md", "system.txt", "user.txt"])
        self.assertIn("deploy-steps (/task/skills/deploy-steps/SKILL.md)", seen["system"])
        self.assertNotIn("review-only", seen["system"])
        self.assertIn("Run make deploy.", seen["skill"])


class Entrypoint(unittest.TestCase):
    def test_the_sandbox_copies_skills_for_every_cli_and_splits_the_default_command(self):
        text = (Path(__file__).parent.parent / "sandbox" / "entrypoint.sh").read_text()
        self.assertIn('cp -R /task/skills "$HOME/$p/skills"', text)
        self.assertIn("for p in .claude .codex .gemini .agents .config/opencode", text)
        self.assertIn("--append-system-prompt-file /task/system.txt", text)


class Ui(AdminCase):
    def form(self, **over):
        f = {**GOOD, "orig": "", "confirm": "1", **over}
        return [(k, v) for k, v in f.items() if k not in ("agents", "projects")] + [("agent", a) for a in over.get("agents", [])]

    def test_adding_editing_and_deleting_a_skill(self):
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/skills", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("No skills yet", html)
        self.assertIn("factory-design-mockups", html)
        self.assertIn('href="/skills"', html)                      # in the settings menu
        s, _, html = self.post(cookie, csrf, "/skills/save", self.form(confirm=""))
        self.assertEqual(s, 422)
        self.assertIn("Tick the confirmation box", html)
        s, _, html = self.post(cookie, csrf, "/skills/save", self.form(name="Bad Name", body="<script>x</script>"))
        self.assertEqual(s, 422)
        self.assertIn("Name: lower-case letters", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)      # the typed text comes back, escaped
        s, _, _ = self.post(cookie, csrf, "/skills/save", self.form(agents=["implementer", "nope"]))
        self.assertEqual(s, 303)
        self.assertEqual(self.overrides()["skills"], [{**GOOD, "agents": ["implementer"]}])
        self.assertEqual(load(str(self.root / "config.toml")).skills[0].agents, ("implementer",))
        s, _, html = self.post(cookie, csrf, "/skills/save", self.form())
        self.assertEqual(s, 422)
        self.assertIn("already a skill called deploy-steps", html)
        s, _, html = self.req("GET", "/skills/edit?name=deploy-steps", cookie=cookie)
        self.assertIn("Run make deploy.", html)
        self.assertIn('value="implementer" checked', html)
        s, _, _ = self.post(cookie, csrf, "/skills/save", self.form(orig="deploy-steps", name="renamed", body="New body"))
        self.assertEqual(s, 303)
        self.assertEqual(self.overrides()["skills"], [{**GOOD, "body": "New body"}])   # the name stays; no agents ticked: every agent
        self.assertEqual(self.post(cookie, csrf, "/skills/delete", [("name", "deploy-steps")])[0], 400)
        self.assertEqual(self.post(cookie, csrf, "/skills/delete", [("name", "deploy-steps"), ("confirm", "1")])[0], 303)
        self.assertEqual(self.overrides().get("skills"), None)
        self.assertEqual(self.req("GET", "/skills/edit?name=deploy-steps", cookie=cookie)[0], 404)


class Toml(unittest.TestCase):
    def test_a_skill_round_trips_through_the_writer(self):
        d = {"skills": [{**GOOD, "body": "Line 1\n\t\"quoted\" back\\slash 😀", "agents": ["analyst"]}]}
        self.assertEqual(tomllib.loads(dumps(d)), d)
