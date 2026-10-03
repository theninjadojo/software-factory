"""The whole path with nothing faked but git remotes: gate -> queue -> real worker API server -> real worker -> result -> verdict."""
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                    # noqa: E402
from factory import ctl, verify, workerapi                            # noqa: E402
from factory import db as dbm                                         # noqa: E402
from test_workerapi import TOKEN, cfg_for                             # noqa: E402


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = cfg_for(self.tmp)
        dbm.connect(self.cfg.db_path).close()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), workerapi.make_handler(workerapi.Api(lambda: self.cfg)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        src = self.tmp / "remote" / "o" / "r"
        src.mkdir(parents=True)
        run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=src, check=True, capture_output=True, text=True)
        run("init", "-q")
        (src / "a.txt").write_text("one\n")
        run("add", ".")
        run("commit", "-qm", "i")
        self.sha = run("rev-parse", "HEAD").stdout.strip()
        self.patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"

    def worker(self, script: str) -> W.Config:
        (self.tmp / "token").write_text(TOKEN)
        recipe = self.tmp / "recipe.sh"
        recipe.write_text("#!/bin/sh\n" + script)
        recipe.chmod(recipe.stat().st_mode | stat.S_IEXEC)
        return W.Config({"server": f"http://127.0.0.1:{self.srv.server_port}", "token_file": str(self.tmp / "token"), "platform": "macos",
                         "work_dir": str(self.tmp / "work"), "git_url": str(self.tmp / "remote" / "{repo}"),
                         "recipes": {"ios-test": {"command": [str(recipe)], "timeout_seconds": 60}}})

    def verify(self, script: str) -> verify.Report:
        cfg_w = self.worker(script)
        out = []
        t = threading.Thread(target=lambda: out.append(verify.gate(self.cfg, "o/r", 5, self.sha, self.patch)))
        t.start()
        api, ran = W.Api(cfg_w), False
        for _ in range(50):                                            # the worker's poll loop, bounded
            if W.poll_once(cfg_w, api):
                ran = True
                break
            threading.Event().wait(0.2)
        t.join(30)
        self.assertTrue(ran and out)
        return out[0]

    def test_patch_that_passes_the_recipe(self):
        rep = self.verify("grep -q two a.txt\n")
        self.assertTrue(rep.ok, rep.text())
        self.assertIn("passed", rep.text())

    def test_patch_that_fails_the_recipe_returns_its_output(self):
        rep = self.verify("echo 'FAILURE in module X'; exit 1\n")
        self.assertFalse(rep.ok)
        self.assertIn("FAILURE in module X", rep.text())


class Tokens(unittest.TestCase):
    def test_ctl_creates_a_private_token_once(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = cfg_for(tmp)
        path = Path(cfg.workers.tokens_file)
        path.unlink()
        self.assertEqual(ctl.workers_add(cfg, "my-mac"), 0)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn("my-mac", workerapi.read_tokens(str(path)))
        self.assertEqual(ctl.workers_add(cfg, "my-mac"), 1)               # never overwrites
        self.assertEqual(ctl.workers_add(cfg, "Bad Name"), 1)
        self.assertEqual(len(path.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
