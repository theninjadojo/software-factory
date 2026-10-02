import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import main as m
from factory.config import (CODEX_COMMAND, Config, HarnessCfg, Role, Route, RunnerCfg, allowed_hosts, default_harnesses,
                            harness_for, load, overrides_path)
from factory.proxy import dynamic_allowlist
from factory.runner import RunResult, run_task, sandbox_cmd
from factory.tomlw import dumps
from test_roles import CFG as ROLE_CFG, FakeClf, FakeGH, issue
from test_runner import RunTaskEndToEnd

EXAMPLE = Path("config.example.toml")


def load_with(extra: str, base: str | None = None):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write((base if base is not None else EXAMPLE.read_text()) + extra)
    return load(f.name)


def low_tier_on(harness: str) -> str:
    """The example config with the low routing tier pointed at a harness (edits the existing table; TOML forbids redeclaring it)."""
    return EXAMPLE.read_text().replace('harness = "claude-code"', f'harness = "{harness}"', 1)


class HarnessConfig(unittest.TestCase):
    def test_defaults(self):
        h = default_harnesses(RunnerCfg())
        self.assertEqual([n for n, x in h.items() if x.enabled], ["claude-code"])
        self.assertTrue(h["codex"].experimental and h["gemini"].experimental)
        self.assertEqual(h["codex"].env_var, "CODEX_API_KEY")
        self.assertTrue(h["codex"].env_file.endswith("/codex.env"))
        self.assertIn("$(cat /task/prompt.txt)", h["claude-code"].command)

    def test_enabling_and_overriding(self):
        cfg = load_with('\n[harnesses.codex]\nenabled = true\nallow_hosts = ["api.openai.com", "chatgpt.com"]\n')
        self.assertTrue(cfg.harnesses["codex"].enabled)
        self.assertEqual(cfg.harnesses["codex"].allow_hosts, ("api.openai.com", "chatgpt.com"))
        self.assertEqual(cfg.harnesses["codex"].command, CODEX_COMMAND)               # untouched fields keep their defaults

    def test_routes_and_roles_may_only_use_enabled_harnesses(self):
        with self.assertRaises(ValueError):
            load_with("", low_tier_on("codex"))                                              # codex is not enabled
        with self.assertRaises(ValueError):
            load_with('\n[roles.analyst]\nharness = "nope"\n')
        cfg = load_with('\n[harnesses.codex]\nenabled = true\n', low_tier_on("codex"))
        self.assertEqual(cfg.routes["low"].harness, "codex")

    def test_custom_harness_needs_all_fields_and_valid_values(self):
        ok = '\n[harnesses.mine]\nimage = "x/y:1"\nenv_file = "/s/mine.env"\ncommand = "agent < /task/prompt.txt"\nallow_hosts = ["api.example.com"]\n'
        self.assertEqual(load_with(ok).harnesses["mine"].image, "x/y:1")
        for bad in ('\n[harnesses.mine]\nimage = "x"\n',
                    ok.replace('"api.example.com"', '"not a host"'),
                    ok.replace('"agent < /task/prompt.txt"', '"   "')):
            with self.assertRaises(ValueError):
                load_with(bad)

    def test_allowlist_is_runner_hosts_plus_enabled_harnesses_only(self):
        self.assertEqual(allowed_hosts(load_with("")), ("api.anthropic.com",))
        both = allowed_hosts(load_with('\n[harnesses.codex]\nenabled = true\n[harnesses.gemini]\nenabled = false\n'))
        self.assertEqual(both, ("api.anthropic.com", "api.openai.com"))

    def test_opencode_harnesses_start_off_and_open_only_their_own_host(self):
        h = default_harnesses(RunnerCfg())
        self.assertFalse(h["opencode-openrouter"].enabled or h["opencode-zen"].enabled)
        self.assertEqual(allowed_hosts(load_with("")), ("api.anthropic.com",))
        self.assertEqual(allowed_hosts(load_with('\n[harnesses.opencode-openrouter]\nenabled = true\n')), ("api.anthropic.com", "openrouter.ai"))
        self.assertEqual(allowed_hosts(load_with('\n[harnesses.opencode-zen]\nenabled = true\n')), ("api.anthropic.com", "opencode.ai"))
        self.assertTrue(h["opencode-openrouter"].data_notice and h["opencode-zen"].model_hint)

    def test_model_ids_are_validated_at_load(self):
        cfg = load_with('\n[harnesses.opencode-openrouter]\nenabled = true\n',
                        low_tier_on("opencode-openrouter").replace('model = "haiku"', 'model = "openrouter/qwen/qwen3-coder"', 1))
        self.assertEqual(cfg.routes["low"].harness, "opencode-openrouter")
        for bad in ('x; rm -rf /', 'a b', '$(id)', ''):
            with self.assertRaises(ValueError):
                load_with("", EXAMPLE.read_text().replace('model = "haiku"', f'model = "{bad}"', 1))

    def test_harness_for_always_has_claude(self):
        self.assertEqual(harness_for(ROLE_CFG, "claude-code").name, "claude-code")        # a Config built without harnesses
        self.assertIsNone(harness_for(ROLE_CFG, "codex"))


class HarnessCommand(unittest.TestCase):
    def test_sandbox_uses_the_harness_image_credential_and_command(self):
        h = default_harnesses(RunnerCfg())["codex"]
        cmd = sandbox_cmd(RunnerCfg(), Route("codex", "gpt-x", "low"), "n", Path("/w"), h)
        self.assertEqual(cmd[-1], h.image)
        self.assertEqual(cmd[cmd.index("--env-file") + 1], h.env_file)
        self.assertIn(f"AGENT_COMMAND={h.command}", cmd)
        self.assertNotIn("claude.env", " ".join(cmd))                                      # another harness's credential never leaks in
        for flag in ("--network=none", "--read-only", "--cap-drop=all"):
            self.assertIn(flag, cmd)

    def test_default_is_claude(self):
        cmd = sandbox_cmd(RunnerCfg(), Route("claude-code", "haiku", "low"), "n", Path("/w"))
        self.assertIn("AGENT_COMMAND=" + default_harnesses(RunnerCfg())["claude-code"].command, cmd)


class HarnessRuns(unittest.TestCase):
    _setup = RunTaskEndToEnd._setup

    def test_unavailable_harness_or_missing_credential_fails_before_any_container(self):
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = self._setup(t, lambda w: None)
            with mock.patch("subprocess.run") as run:
                r = run_task(cfg, gh, "o/web", {"number": 1, "title": "t", "body": ""}, Route("codex", "x", "low"))
                self.assertEqual(r.status, "failed")
                self.assertIn("not available", r.detail)
                cfg2 = replace(cfg, harnesses={"codex": replace(default_harnesses(cfg.runner)["codex"], enabled=True)})
                r = run_task(cfg2, gh, "o/web", {"number": 1, "title": "t", "body": ""}, Route("codex", "x", "low"))
                self.assertIn("no credential file", r.detail)
                run.assert_not_called()

    def test_a_custom_harness_runs_end_to_end_with_its_own_image_and_credential(self):
        from factory import runner
        seen = {}
        with tempfile.TemporaryDirectory() as t:
            def edits(work):
                (work / "web" / "a.txt").write_text("by the other agent\n")
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            env = Path(t) / "other.env"
            env.write_text("OTHER_KEY=not-real\n")
            custom = HarnessCfg("other", "localhost/other-agent:1", str(env), "other-agent < /task/prompt.txt", ("api.example.com",))
            cfg = replace(cfg, harnesses={"other": custom})
            real_fake = p2.new

            def spy(cmd, *a, **k):
                if cmd[0] in ("podman", "docker"):
                    seen["cmd"] = cmd
                return real_fake(cmd, *a, **k)
            with p1, mock.patch("subprocess.run", spy):
                res = runner.run_task(cfg, gh, "o/web", {"number": 3, "title": "t", "body": ""}, Route("other", "m", "low"))
            self.assertEqual(res.status, "pr", res.detail)
            self.assertEqual(seen["cmd"][-1], "localhost/other-agent:1")
            self.assertEqual(seen["cmd"][seen["cmd"].index("--env-file") + 1], str(env))
            self.assertIn("AGENT_COMMAND=other-agent < /task/prompt.txt", seen["cmd"])

    def test_stage_runs_use_the_roles_harness(self):
        cfg = replace(ROLE_CFG, roles=(replace(ROLE_CFG.roles[0], harness="codex"),) + ROLE_CFG.roles[1:],
                      harnesses={"codex": replace(default_harnesses(RunnerCfg())["codex"], enabled=True)})
        gh = FakeGH({"factory:analyze": [issue(labels=["factory:analyze"])]})
        with mock.patch.object(m.runner, "run_task", return_value=RunResult("stage", "ok", output="# d")) as rt, tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(cfg, db_path=d + "/f.db"), gh, __import__("factory.db", fromlist=["x"]).connect(":memory:"), FakeClf())
        self.assertEqual(rt.call_args.args[4].harness, "codex")


class ProxyReload(unittest.TestCase):
    def test_allowlist_follows_config_changes_and_a_bad_reload_keeps_the_last_good_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(EXAMPLE.read_text())
            current = dynamic_allowlist(str(p), ("api.anthropic.com",))
            self.assertEqual(current(), ("api.anthropic.com",))
            overrides_path(str(p)).write_text(dumps({"harnesses": {"codex": {"enabled": True}}}))
            with mock.patch("factory.proxy.time.time", return_value=time.time() + 60):
                self.assertEqual(current(), ("api.anthropic.com", "api.openai.com"))
            overrides_path(str(p)).write_text("this is = = not toml")
            with mock.patch("factory.proxy.time.time", return_value=time.time() + 120):
                self.assertEqual(current(), ("api.anthropic.com", "api.openai.com"))      # kept, not reset and not widened


if __name__ == "__main__":
    unittest.main()
