"""Integration test (needs Docker; opt in with FACTORY_DOCKER_TESTS=1):

    FACTORY_DOCKER_TESTS=1 python3 -m unittest tests/integration/test_docker_harness.py

Runs a stand-in agent through the real path with the Docker engine: a custom harness image and credential file, the generic
sandbox entrypoint, the proxy socket, patch collection, validation, and a PR against local git remotes."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, ".")
sys.path.insert(0, "tests")
from factory import runner  # noqa: E402
from factory.config import Config, HarnessCfg, Project, ProjectRepo, Route, RunnerCfg  # noqa: E402
from test_designfiles import VALID  # noqa: E402

HERE = Path(__file__).parent
ENABLED = os.environ.get("FACTORY_DOCKER_TESTS") == "1" and shutil.which("docker")


@unittest.skipUnless(ENABLED, "set FACTORY_DOCKER_TESTS=1 and have Docker available")
class DockerHarness(unittest.TestCase):
    def test_a_custom_harness_runs_end_to_end_under_the_docker_engine(self):
        t = Path(tempfile.mkdtemp(prefix="sf-int-"))
        self.addCleanup(shutil.rmtree, t, True)
        ctx = t / "ctx"
        shutil.copytree(HERE / "fake-agent", ctx)
        shutil.copy(HERE.parent.parent / "sandbox" / "entrypoint.sh", ctx / "entrypoint.sh")
        (ctx / "mockup.html").write_text(VALID)
        subprocess.run(["docker", "build", "-q", "-t", "factory-fake-agent:test", str(ctx)], check=True, capture_output=True)

        env = {"PATH": os.environ["PATH"], "HOME": str(t), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        g = lambda *a, cwd: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env)
        seed = t / "seed" / "web"
        seed.mkdir(parents=True)
        g("init", "-q", "-b", "main", cwd=seed)
        (seed / "a.txt").write_text("one\n")
        g("add", ".", cwd=seed)
        g("commit", "-qm", "i", cwd=seed)
        bare = t / "remotes" / "web.git"
        bare.parent.mkdir()
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, env=env)

        (t / "work").mkdir()
        (t / "run").mkdir()
        os.chmod(t / "work", 0o777)
        cred = t / "fake.env"
        cred.write_text("FAKE_AGENT_KEY=not-a-real-key\n")
        proxy_cfg = t / "config.toml"
        proxy_cfg.write_text(Path("config.example.toml").read_text().replace("/srv/factory", str(t)))
        proxy = subprocess.Popen([sys.executable, "-m", "factory.proxy", str(proxy_cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proxy.terminate)
        sock = t / "run" / "proxy.sock"
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.1)

        harness = HarnessCfg("fake", "factory-fake-agent:test", str(cred), "fake-agent", ("api.example.com",))
        route = Route("fake", "model-x", "low")
        cfg = Config("x", 1, False, 0.6, None, "factory:ready", ["o/web"], frozenset(), {"low": route, "medium": route, "high": route},
                     runner=RunnerCfg(engine="docker", work_dir=str(t / "work"), proxy_socket=str(sock), claude_env_file=str(t / "unused.env")),
                     projects=(Project("p", (ProjectRepo("o/web", "web"),)),), harnesses={"fake": harness})

        class GH:
            token = "tok"
            prs = []
            def default_branch(self, repo): return "main"
            def create_pr(self, repo, head, base, title, body):
                self.prs.append((repo, head, body)); return f"https://github.com/{repo}/pull/1"
            def comment(self, *a): pass

        gh = GH()
        real_git = runner.git
        mapped = lambda args, cwd, e, timeout=180: real_git([str(bare) if "https://github.com/" in a else a for a in args], cwd, e, timeout)
        with mock.patch.object(runner, "git", mapped):
            res = runner.run_task(cfg, gh, "o/web", {"number": 1, "title": "t", "body": ""}, route)

        self.assertEqual(res.status, "pr", res.detail)
        branch = gh.prs[0][1]
        report = g("--git-dir", str(bare), "show", f"{branch}:agent-report.txt", cwd=t).stdout
        self.assertIn("uid: 1000", report)                       # not root
        self.assertIn("network: blocked", report)                # no direct network
        self.assertIn("model: model-x", report)                  # $MODEL reached the harness command
        self.assertIn("credential-present: yes", report)         # the harness's own credential file was used
        self.assertIn("prompt-bytes:", report)                   # the task was delivered at /task/prompt.txt
        self.assertIn("fake agent finished", gh.prs[0][2])       # the agent's output is in the PR body
        self.assertEqual(g("--git-dir", str(bare), "rev-list", "--count", "main", cwd=t).stdout.strip(), "1")   # main untouched
        self.assertEqual(list((t / "work").iterdir()), [])       # workspace cleaned up


    def test_design_files_in_a_git_ignored_folder_reach_a_draft_pr_through_the_real_entrypoint(self):
        """The bug a live run exposed: the repo git-ignores design/ (a pattern that also matches docs/design), so the agent's file was
        invisible to `git diff` and nothing was published. Runs the REAL sandbox entrypoint in a REAL container."""
        t = Path(tempfile.mkdtemp(prefix="sf-int-"))
        self.addCleanup(shutil.rmtree, t, True)
        ctx = t / "ctx"
        shutil.copytree(HERE / "fake-agent", ctx)
        shutil.copy(HERE.parent.parent / "sandbox" / "entrypoint.sh", ctx / "entrypoint.sh")
        (ctx / "mockup.html").write_text(VALID)
        subprocess.run(["docker", "build", "-q", "-t", "factory-fake-agent:test", str(ctx)], check=True, capture_output=True)
        env = {"PATH": os.environ["PATH"], "HOME": str(t), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        g = lambda *a, cwd: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env)
        seed = t / "seed" / "web"
        seed.mkdir(parents=True)
        g("init", "-q", "-b", "main", cwd=seed)
        (seed / "a.txt").write_text("one\n")
        (seed / ".gitignore").write_text("# local pulls\ndesign/\n")
        g("add", ".", cwd=seed)
        g("commit", "-qm", "i", cwd=seed)
        bare = t / "remotes" / "web.git"
        bare.parent.mkdir()
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, env=env)
        for d in ("work", "run"):
            (t / d).mkdir()
        os.chmod(t / "work", 0o777)
        cred = t / "fake.env"
        cred.write_text("FAKE_AGENT_KEY=not-a-real-key\n")
        proxy_cfg = t / "config.toml"
        proxy_cfg.write_text(Path("config.example.toml").read_text().replace("/srv/factory", str(t)))
        proxy = subprocess.Popen([sys.executable, "-m", "factory.proxy", str(proxy_cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proxy.terminate)
        sock = t / "run" / "proxy.sock"
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.1)
        route = Route("fake", "model-x", "low")
        cfg = Config("x", 1, False, 0.6, None, "factory:ready", ["o/web"], frozenset(), {"low": route, "medium": route, "high": route},
                     runner=RunnerCfg(engine="docker", work_dir=str(t / "work"), proxy_socket=str(sock), claude_env_file=str(t / "unused.env")),
                     projects=(Project("p", (ProjectRepo("o/web", "web"),)),),
                     harnesses={"fake": HarnessCfg("fake", "factory-fake-agent:test", str(cred), "fake-agent", ("api.example.com",))})

        class GH:
            token = "tok"
            prs, drafts = [], []
            def default_branch(self, repo): return "main"
            def create_pr(self, repo, head, base, title, body, draft=False):
                self.prs.append((repo, head)); self.drafts.append(draft); return f"https://github.com/{repo}/pull/1"
            def comment(self, *a): pass

        gh = GH()
        real_git = runner.git
        mapped = lambda args, cwd, e, timeout=180: real_git([str(bare) if "https://github.com/" in a else a for a in args], cwd, e, timeout)
        with mock.patch.object(runner, "git", mapped):
            res = runner.run_task(cfg, gh, "o/web", {"number": 1, "title": "Reorder button", "body": ""}, route, role="designer")
        self.assertEqual(res.status, "stage", res.detail)
        self.assertEqual([f["path"] for f in res.files], ["docs/design/factory-1-demo.dc.html"], res.notes)
        self.assertEqual(gh.drafts, [True])
        branch = gh.prs[0][1]
        shown = g("--git-dir", str(bare), "ls-tree", "-r", "--name-only", branch, cwd=t).stdout.split()
        self.assertIn("docs/design/factory-1-demo.dc.html", shown)       # in a folder the repo's .gitignore matches
        self.assertNotIn("agent-report.txt", shown)                      # nothing else sneaked in
        self.assertEqual(g("--git-dir", str(bare), "rev-list", "--count", "main", cwd=t).stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
