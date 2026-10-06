"""A worker that lacks a tool a recipe needs does not take that recipe's jobs, says why, and can install a Node of its own."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "worker"))
import worker as W                                                    # noqa: E402
from factory import db as dbm, jobs                                   # noqa: E402

RECIPES = ROOT / "worker" / "recipes"


def script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def cfg(tmp: Path, **recipes) -> W.Config:
    (tmp / "token").write_text("x" * 32)
    return W.Config({"server": "http://127.0.0.1:1", "token_file": str(tmp / "token"), "platform": "linux", "work_dir": str(tmp / "work"),
                     "install_tools": False, "recipes": {n: {"command": [c]} for n, c in recipes.items()}})


class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_exit_3_with_lines_means_not_ready_and_anything_else_means_ready(self):
        c = cfg(self.tmp,
                lacks=script(self.tmp / "a.sh", '[ "$1" = --preflight ] && { echo "node is missing"; echo "and so is npm"; exit 3; }\nexit 0\n'),
                fine=script(self.tmp / "b.sh", "exit 0\n"),
                oldstyle=script(self.tmp / "c.sh", 'echo "unknown argument" >&2; exit 2\n'))       # a recipe with no --preflight flag
        ready, unready = W.readiness(c, {})
        self.assertEqual(ready, ["fine", "oldstyle"])
        self.assertEqual(unready, {"lacks": "node is missing"})

    def test_it_is_asked_again_after_a_minute_so_a_new_tool_is_noticed(self):
        flag = self.tmp / "installed"
        c = cfg(self.tmp, js=script(self.tmp / "a.sh", f'[ -e {flag} ] || {{ echo "no node"; exit 3; }}\n'))
        state: dict = {}
        self.assertEqual(W.readiness(c, state, 1000.0)[0], [])
        flag.write_text("")
        self.assertEqual(W.readiness(c, state, 1030.0)[0], [])                       # not yet: asked 30 s ago
        self.assertEqual(W.readiness(c, state, 1061.0)[0], ["js"])

    def test_check_warns_and_does_not_fail(self):
        c = cfg(self.tmp, js=script(self.tmp / "a.sh", 'echo "no node"; exit 3\n'))
        out = W.check(c, W.Api(c))
        self.assertIn(("warn", "recipe js cannot run yet, so the factory will not send it jobs: no node"), out)
        self.assertNotIn("FAIL", [lvl for lvl, msg in out if "recipe js" in msg])      # an update's check must not roll back over a missing tool

    def test_a_js_recipe_without_node_gets_one_installed_once(self):
        c = cfg(self.tmp, **{"web": script(self.tmp / "web-test.sh", '[ -e "$0.ok" ] || { echo "no node"; exit 3; }\n')})
        c.install_tools = True
        calls = []
        real = W.install_node
        W.install_node = lambda root=W.ROOT: (calls.append(1), (self.tmp / "web-test.sh.ok").write_text(""), "")[2]
        self.addCleanup(setattr, W, "install_node", real)
        state: dict = {}
        self.assertEqual(W.readiness(c, state, 0.0)[0], ["web"])
        W.readiness(c, state, 100.0)
        self.assertEqual(calls, [1])

    def test_a_failed_install_is_not_retried_every_minute(self):
        c = cfg(self.tmp, **{"web": script(self.tmp / "web-test.sh", 'echo "no node"; exit 3\n')})
        c.install_tools = True
        calls = []
        real = W.install_node
        W.install_node = lambda root=W.ROOT: (calls.append(1), "offline")[1]
        self.addCleanup(setattr, W, "install_node", real)
        state: dict = {}
        for t in (0.0, 70.0, 140.0):
            self.assertEqual(W.readiness(c, state, t)[0], [])
        self.assertEqual(calls, [1])


class Recipes(unittest.TestCase):
    def run_recipe(self, name: str, path: str, *extra: str) -> subprocess.CompletedProcess:
        env = {"PATH": path, "HOME": tempfile.gettempdir()}
        return subprocess.run(["sh", str(RECIPES / name), *extra], capture_output=True, text=True, env=env, timeout=30)

    def test_the_javascript_recipes_say_what_a_bare_machine_lacks(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        for tool in ("sh", "dirname"):                       # a machine with a shell and nothing else
            os.symlink(shutil.which(tool), os.path.join(empty, tool))
        for name in ("web-test.sh", "playwright-screens.sh"):
            r = self.run_recipe(name, empty, "--preflight")
            self.assertEqual(r.returncode, 3, (name, r.stdout, r.stderr))
            self.assertIn("Node.js 18 or newer is not installed", r.stdout)
            self.assertIn("install-node.sh", r.stdout)

    def test_corepack_is_not_required_when_pnpm_is_there(self):
        node = shutil.which("node")
        if not (node and shutil.which("npm")):
            self.skipTest("no node on this machine")
        bin_ = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bin_, True)
        for tool in ("sh", "node", "npm", "dirname", "env"):
            if shutil.which(tool):
                os.symlink(shutil.which(tool), bin_ / tool)
        script(bin_ / "pnpm", "exit 0\n")
        r = self.run_recipe("web-test.sh", str(bin_), "--preflight")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def test_the_installer_refuses_a_download_that_does_not_match_its_checksum(self):
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dest, True)
        shutil.copytree(ROOT / "worker", dest / "worker")
        dist = dest / "dist"
        dist.mkdir()
        sysname = {"Linux": "linux", "Darwin": "darwin"}.get(os.uname().sysname)
        arch = {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(os.uname().machine)
        if not (sysname and arch):
            self.skipTest("no pinned Node for this machine")
        (dist / f"node-v22.23.3-{sysname}-{arch}.tar.gz").write_bytes(b"not node")
        r = subprocess.run(["bash", str(dest / "worker" / "install-node.sh")], capture_output=True, text=True,
                           env={**os.environ, "NODE_DIST_BASE": dist.as_uri()}, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match the checksum", r.stderr)
        self.assertFalse((dest / "tools" / "node").exists())


class Server(unittest.TestCase):
    def test_a_worker_with_an_unready_recipe_shows_why_and_gets_none_of_its_jobs(self):
        db = dbm.connect(":memory:")
        jobs.init(db) if hasattr(jobs, "init") else None
        jobs.touch_worker(db, "arch", "linux", ["web-test"], 1, 1.0, "0.24.0", {"playwright-screens": "Node.js 18 or newer is not installed"})
        row = jobs.online_workers(db, 2.0, 60)[0]
        self.assertEqual(row["recipes"], "web-test")
        self.assertEqual(json.loads(row["unready"]), {"playwright-screens": "Node.js 18 or newer is not installed"})
        jobs.touch_worker(db, "arch", "linux", ["web-test", "playwright-screens"], 1, 3.0)          # fixed: nothing left over
        self.assertEqual(jobs.online_workers(db, 4.0, 60)[0]["unready"], "")


if __name__ == "__main__":
    unittest.main()
