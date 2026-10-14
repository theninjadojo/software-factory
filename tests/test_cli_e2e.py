"""The real `shikumi` binary against a real factory UI (python3 -m factory.ui), end to end: a token from `ctl tokens add`, the
client's own config file, local tickets made, listed, changed and closed through the API, and what the client refuses.
Skipped unless SHIKUMI_CLI_BIN names a built binary (the `cli` CI job builds it: cd cli && cargo build)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from factory import db as dbm

BIN = os.environ.get("SHIKUMI_CLI_BIN", "")
REPO = "your-org/standalone-service"
PASSWORD = "correct horse battery"
NASTY = "The plan\x1b[31m in red\x1b[0m, ‮reversed‬ and \u009b2J a C1 CSI. Done."


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(BIN and Path(BIN).is_file(), "set SHIKUMI_CLI_BIN to a built shikumi binary")
class CliEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "state").mkdir()
        cls.config = d / "config.toml"
        cfg = Path("config.example.toml").read_text().replace("/srv/factory", str(d))
        assert "/srv/factory" not in cfg
        cls.config.write_text(cfg + "\n[local]\nenabled = true\n")
        db = dbm.connect(str(d / "state" / "factory.db"))
        cls.run_id = dbm.start_run(db, "stage", REPO, 7, "Plan the thing", "claude-code", "sonnet", "medium", "", "analyst")
        dbm.finish_run(db, cls.run_id, "stage", "analyst document ready", "", NASTY, "log \x1b]0;pwned\x07 tail")
        dbm.add_event(db, "alert:test", "Something \x1b[2J happened", REPO, 7, cls.run_id)
        db.close()
        env = {**os.environ, "FACTORY_UI_PASSWORD": PASSWORD, "FACTORY_CONFIG": str(cls.config)}
        subprocess.run([sys.executable, "-m", "factory.ui", "--config", str(cls.config), "--set-password"], env=env, check=True, capture_output=True)
        out = subprocess.run([sys.executable, "-m", "factory.ctl", "tokens", "add", "e2e"], env=env, check=True, capture_output=True, text=True).stdout
        cls.token = out.strip().splitlines()[-1]
        assert cls.token.startswith("shk_"), out
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen([sys.executable, "-m", "factory.ui", "--config", str(cls.config), "--listen", f"127.0.0.1:{cls.port}"],
                                      env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.url + "/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            cls.server.kill()
            raise RuntimeError("the UI did not start")
        cls.home = d / "home"
        cls.home.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.tmp.cleanup()

    def cli(self, *args, stdin: str | None = None, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("SHIKUMI_")}
        e.update(HOME=str(self.home), NO_COLOR="1", **env)
        e.pop("XDG_CONFIG_HOME", None)
        r = subprocess.run([BIN, *args], input=stdin or "", capture_output=True, text=True, env=e, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def ok(self, *args, **kw) -> str:
        code, out, err = self.cli(*args, **kw)
        self.assertEqual(code, 0, f"{args}: {err}")
        return out

    def test_a_person_manages_local_tickets_from_the_terminal(self):
        out = self.ok("instance", "add", "e2e", "--url", self.url, "--token-stdin", "--repo", REPO, stdin=self.token + "\n")
        self.assertIn("Connected", out)
        cfg = self.home / ".config" / "shikumi" / "cli.toml"
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
        self.assertIn('default = "e2e"', cfg.read_text())
        self.assertIn("e2e", self.ok("instance", "list"))
        self.assertNotIn(self.token, self.ok("instance", "list"))
        self.assertIn("factory", self.ok("version"))
        self.assertIn("taking work", self.ok("status"))
        self.ok("pause")
        self.assertIn("paused", self.ok("status"))
        self.ok("resume")
        self.assertIn("Created", self.ok("ticket", "create", "--title", "Fix the footer", "--body", "It wraps.", "--where", "local"))
        out = self.ok("tickets", "--stage", "new")
        self.assertIn("L-1", out)
        self.assertIn("Fix the footer", out)
        shown = json.loads(self.ok("ticket", "show", "L-1", "--json"))
        self.assertEqual((shown["ref"], shown["local"]["body"]), ("L-1", "It wraps."))
        self.assertIn("Comment added.", self.ok("ticket", "comment", "L-1", "--body", "More detail."))
        self.assertIn("Saved.", self.ok("ticket", "edit", "L-1", "--title", "Fix the footer now"))
        self.assertIn("Pinned at High", self.ok("ticket", "priority", "L-1", "high"))
        out = self.ok("ticket", "show", "L-1")
        self.assertIn("Fix the footer now", out)
        self.assertIn("More detail.", out)
        self.assertIn("Started", self.ok("ticket", "start", "L-1", "auto"))
        code, _, err = self.cli("ticket", "start", "L-1", "auto")
        self.assertEqual(code, 5, err)
        self.assertIn("cannot be started now", err)
        code, _, err = self.cli("ticket", "close", "L-1")                    # no terminal and no --yes: it asks for --yes
        self.assertEqual(code, 2, err)
        self.assertIn("--yes", err)
        self.assertIn("Closed", self.ok("ticket", "close", "L-1", "--yes"))
        self.assertIn("Reopened L-1.", self.ok("ticket", "reopen", "L-1"))
        code, _, _ = self.cli("ticket", "show", "L-99")
        self.assertEqual(code, 4)

    def test_untrusted_text_reaches_the_terminal_clean(self):
        self.ok("instance", "add", "e2e", "--url", self.url, "--token-stdin", "--repo", REPO, stdin=self.token)
        for args in (("run", str(self.run_id), "--output", "--log"), ("runs",), ("events", "--kind", "all"),
                     ("ticket", "doc", "#7", "analyst")):
            out = self.ok(*args)
            for bad in ("\x1b", "‮", "\u009b", "\x07"):
                self.assertNotIn(bad, out, args)
        out = self.ok("run", str(self.run_id), "--output")
        self.assertIn("The plan[31m in red[0m, reversed and 2J a C1 CSI. Done.", out)
        raw = self.ok("run", str(self.run_id), "--json")                     # still valid JSON, the dangerous characters escaped
        self.assertNotIn("\u009b", raw)
        self.assertEqual(json.loads(raw)["output"], NASTY)

    def test_what_the_client_refuses(self):
        self.ok("instance", "add", "e2e", "--url", self.url, "--token-stdin", "--repo", REPO, stdin=self.token)
        self.assertIn("Not available", self.ok("needs"))                      # no GitHub token on this factory
        self.ok("ticket", "create", "--title", "Questions", "--where", "local")
        code, _, err = self.cli("answer", "L-1", "--accept-all")
        self.assertEqual(code, 5, err)
        self.assertIn("GitHub token", err)
        code, _, err = self.cli("status", SHIKUMI_TOKEN="shk_wrong_wrongwrongwrongwrongwrong")
        self.assertEqual(code, 3, err)
        code, _, err = self.cli("instance", "add", "lan", "--url", "http://192.0.2.1:8787", "--token-stdin", stdin=self.token)
        self.assertEqual(code, 1, err)
        self.assertIn("plain http", err)
        code, _, err = self.cli("instance", "add", "bad", "--url", self.url, "--token-stdin", stdin="shk_not_a_real_token_at_all")
        self.assertEqual(code, 3, err)
        self.assertNotIn("bad", self.ok("instance", "list"))
        code, _, err = self.cli("ticket", "show", "nonsense")
        self.assertEqual(code, 2, err)
        code, _, err = self.cli("tickets", "--stage", "bogus")
        self.assertEqual(code, 2, err)


if __name__ == "__main__":
    unittest.main()
