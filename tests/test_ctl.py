import unittest

from factory import config, ctl


class Labels(unittest.TestCase):
    def cfg(self):
        return config.parse(__import__("tomllib").loads(open("config.example.toml").read()))

    def test_every_configured_label_is_listed_once(self):
        names = [n for n, _, _ in ctl.factory_labels(self.cfg())]
        for want in ("factory:ready", "factory:analyze", "factory:design", "factory:architect", "factory:auto", "stage:designed",
                     "factory:review", "factory:fix-conflicts", "factory:skip-mockup", "factory:design-approved", "factory:screens-changed",
                     "factory:failed", "priority: high"):
            self.assertIn(want, names)
        self.assertEqual(len(names), len(set(names)))

    def test_create_labels_continues_and_reports_a_failing_repo(self):
        made = []
        class GH:
            def create_label(self, repo, name, color, desc):
                if repo == "o/bad":
                    raise OSError("403")
                made.append((repo, name))
        self.assertEqual(ctl.create_labels(self.cfg(), GH(), ["o/a", "o/bad", "o/b"]), 1)
        self.assertEqual({r for r, _ in made}, {"o/a", "o/b"})


if __name__ == "__main__":
    unittest.main()


class Doctor(unittest.TestCase):
    import subprocess as _sp

    def cfg(self, **kw):
        import dataclasses, tempfile
        c = Labels().cfg()
        d = tempfile.mkdtemp()
        runner = dataclasses.replace(c.runner, work_dir=d, proxy_socket=d + "/none.sock", claude_env_file=d + "/claude.env")
        return dataclasses.replace(c, runner=runner, repos=["o/a"], **kw)

    def run_ok(self, cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def test_reports_problems_and_exits_clean_when_fixed(self):
        class GH:
            def login(self): return "bot"
            def repo_labels(self, repo): return [{"name": "factory:ready"}]
        c = self.cfg()
        res = ctl.doctor(c, GH(), run=self.run_ok)
        text = " | ".join(f"{l}:{m}" for l, m in res)
        self.assertIn("FAIL:model credentials", text)
        self.assertIn("warn:o/a:", text)                  # labels missing
        self.assertIn("warn:proxy socket", text)
        open(c.runner.claude_env_file, "w").write("ANTHROPIC_API_KEY=x\n")
        res = ctl.doctor(c, GH(), run=self.run_ok)
        self.assertFalse([m for l, m in res if l == "FAIL"])

    def test_missing_image_token_and_engine(self):
        import subprocess
        def no_image(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1 if "inspect" in cmd else 0, "", "")
        res = ctl.doctor(self.cfg(), None, run=no_image)
        self.assertTrue(any(l == "FAIL" and "image" in m and "missing" in m for l, m in res))
        self.assertTrue(any(l == "FAIL" and "GitHub token" in m for l, m in res))
        def no_engine(cmd, **kw): raise FileNotFoundError(cmd[0])
        self.assertTrue(any(l == "FAIL" and "cannot run" in m for l, m in ctl.doctor(self.cfg(), None, run=no_engine)))

    def test_a_refused_token_stops_early(self):
        class GH:
            def login(self): raise OSError("401")
        res = ctl.doctor(self.cfg(), GH(), run=self.run_ok)
        self.assertTrue(any("refused" in m for l, m in res))
