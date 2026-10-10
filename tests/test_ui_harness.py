import re
import unittest

from factory.config import CODEX_COMMAND, allowed_hosts, default_harnesses, RunnerCfg, load
from test_ui_admin import AdminCase

CODEX = default_harnesses(RunnerCfg())["codex"]


class HarnessPage(AdminCase):
    def form(self, name="codex", enabled=True, confirm=True, **over):
        h = default_harnesses(RunnerCfg())[name]
        f = {"name": name, "image": h.image, "command": h.command, "allow_hosts": "\n".join(h.allow_hosts)}
        if enabled:
            f["enabled"] = "1"
        if confirm:
            f["confirm"] = "1"
        f.update(over)
        return f

    def cfg(self):
        return load(str(self.root / "config.toml"))

    def test_page_lists_harnesses_and_marks_experimental_ones(self):
        self.assertEqual(self.req("GET", "/harnesses")[0], 303)
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/harnesses", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("claude-code", "codex", "gemini", "experimental", "CODEX_API_KEY", "managed on the"):
            self.assertIn(needle, html)
        self.assertEqual(self.req("POST", "/harnesses/save", "name=codex", cookie=cookie)[0], 403)

    def test_enabling_a_harness_needs_confirmation_and_widens_the_proxy_allowlist(self):
        cookie, csrf = self.session()
        s, _, html = self.post(cookie, csrf, "/harnesses/save", self.form(confirm=False))
        self.assertEqual(s, 422)
        self.assertIn("confirmation box", html)
        self.assertFalse(self.cfg().harnesses["codex"].enabled)
        self.assertEqual(self.post(cookie, csrf, "/harnesses/save", self.form())[0], 303)
        self.assertTrue(self.cfg().harnesses["codex"].enabled)
        self.assertIn("api.openai.com", allowed_hosts(self.cfg()))
        self.assertTrue((self.state / "RESTART").exists())

    def test_command_image_and_hosts_are_validated(self):
        cookie, csrf = self.session()
        bad = [{"command": "codex exec"},                                    # does not read the task file
               {"command": ""}, {"command": "x" * 5000 + " /task/prompt.txt"},
               {"image": "bad image name"}, {"image": ""},
               {"allow_hosts": "not a host"}, {"allow_hosts": ""}, {"allow_hosts": "127.0.0.1"}]
        for over in bad:
            self.assertEqual(self.post(cookie, csrf, "/harnesses/save", self.form(**over))[0], 422, over)
        self.assertEqual(self.post(cookie, csrf, "/harnesses/save", {**self.form(), "name": "nope"})[0], 422)
        self.assertEqual(self.post(cookie, csrf, "/harnesses/save", self.form(command=CODEX_COMMAND.replace("exec ", "exec --json ")))[0], 303)
        self.assertIn("--json", self.cfg().harnesses["codex"].command)

    def test_claude_code_cannot_be_disabled(self):
        cookie, csrf = self.session()
        s, _, html = self.post(cookie, csrf, "/harnesses/save", self.form("claude-code", enabled=False))
        self.assertEqual(s, 422)
        self.assertIn("stays enabled", html)

    def test_routes_choose_among_enabled_harnesses_and_cannot_be_orphaned(self):
        cookie, csrf = self.session()
        _, _, before = self.req("GET", "/settings?section=routing", cookie=cookie)
        self.assertNotIn("<option>codex</option>", before)
        self.post(cookie, csrf, "/harnesses/save", self.form())
        _, _, after = self.req("GET", "/settings?section=routing", cookie=cookie)
        self.assertIn("<option>codex</option>", after)
        routing = {"section": "routing"}
        for tier, model in (("low", "haiku"), ("medium", "sonnet"), ("high", "opus")):
            routing.update({f"routing.{tier}.harness": "codex" if tier == "low" else "claude-code",
                            f"routing.{tier}.model": "gpt-x" if tier == "low" else model, f"routing.{tier}.effort": "low"})
        self.assertEqual(self.post(cookie, csrf, "/settings/save", routing)[0], 303)
        self.assertEqual(self.cfg().routes["low"].harness, "codex")
        s, _, html = self.post(cookie, csrf, "/harnesses/save", self.form(enabled=False))      # codex is still in use
        self.assertEqual(s, 422)
        self.assertIn("uses the harness", html)
        self.assertTrue(self.cfg().harnesses["codex"].enabled)
        self.assertIn("routing.low", self.req("GET", "/harnesses", cookie=cookie)[2])           # the page shows what uses it

    def test_roles_can_pick_a_harness(self):
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/harnesses/save", self.form())
        form = {"section": "roles", "auto.label": "factory:auto", "split.notes_label": "factory:notes"}
        for name, label, done, model, eff in (("analyst", "factory:analyze", "stage:analysed", "sonnet", "medium"),
                                              ("designer", "factory:design", "stage:designed", "sonnet", "medium"),
                                              ("architect", "factory:architect", "stage:architected", "opus", "high")):
            form.update({f"roles.{name}.label": label, f"roles.{name}.done_label": done, f"roles.{name}.model": model,
                         f"roles.{name}.effort": eff, f"roles.{name}.harness": "codex" if name == "architect" else "claude-code",
                         "roles.designer.design_dir": "docs/design"})
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)
        self.assertEqual({r.name: r.harness for r in self.cfg().roles}["architect"], "codex")

    def test_the_designer_design_files_switch_is_a_setting(self):
        cookie, csrf = self.session()
        self.assertIn("Write design mockup files", self.req("GET", "/settings?section=roles", cookie=cookie)[2])
        self.assertTrue(next(r for r in self.cfg().roles if r.name == "designer").design_files)          # on by default
        form = {"section": "roles", "auto.label": "factory:auto", "split.notes_label": "factory:notes"}
        for name, label, done, model, eff in (("analyst", "factory:analyze", "stage:analysed", "sonnet", "medium"),
                                              ("designer", "factory:design", "stage:designed", "sonnet", "medium"),
                                              ("architect", "factory:architect", "stage:architected", "opus", "high")):
            form.update({f"roles.{name}.label": label, f"roles.{name}.done_label": done, f"roles.{name}.model": model,
                         f"roles.{name}.effort": eff, f"roles.{name}.harness": "claude-code", "roles.designer.design_dir": "docs/design"})
        self.assertEqual(self.post(cookie, csrf, "/settings/save", form)[0], 303)                           # checkbox not ticked
        self.assertFalse(next(r for r in self.cfg().roles if r.name == "designer").design_files)
        self.assertEqual(self.post(cookie, csrf, "/settings/save", {**form, "roles.designer.design_files": "1"})[0], 303)
        self.assertTrue(next(r for r in self.cfg().roles if r.name == "designer").design_files)

    def test_harness_credentials_are_write_only_and_stored_0600(self):
        cookie, csrf = self.session()
        key = "sk-test-VERYSECRET1234567890"
        s, h, _ = self.post(cookie, csrf, "/harnesses/credential", {"name": "codex", "value": key})
        self.assertEqual((s, h["Location"]), (303, "/harnesses?ok=secret"))
        path = self.root / "secrets" / "codex.env"
        self.assertEqual((path.read_text(), oct(path.stat().st_mode & 0o777)), (f"CODEX_API_KEY={key}\n", "0o600"))
        _, _, html = self.req("GET", "/harnesses", cookie=cookie)
        self.assertIn("CODEX_API_KEY", html)
        self.assertNotIn("VERYSECRET", html)
        for bad in ({"name": "claude-code", "value": key}, {"name": "codex", "value": "short"}, {"name": "../x", "value": key},
                    {"name": "codex", "value": "has a space 123456"}):
            self.assertEqual(self.post(cookie, csrf, "/harnesses/credential", bad)[0], 400, bad)

    def test_hostile_command_text_is_escaped(self):
        cookie, csrf = self.session()
        evil = '</textarea><script>alert(1)</script> /task/prompt.txt'
        self.assertEqual(self.post(cookie, csrf, "/harnesses/save", self.form(command=evil))[0], 303)
        _, _, html = self.req("GET", "/harnesses", cookie=cookie)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
