"""scripts/update.sh in a source checkout: it asks, then runs git pull with the GitHub token and the docker build. git and docker are
fakes on PATH that record what they were asked to do (and what the environment held), so nothing is pulled or built."""
import base64
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update.sh"
TOKEN = "ghp" + "_" + "SECRETVALUE1234567890abcdef"       # built from pieces so no token-shaped literal sits in the source

FAKE = """#!/bin/sh
# $0 = this fake's name; the log gets one line per call
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$FAKE_LOG"
env | grep '^GIT_CONFIG' >> "$FAKE_LOG" || true
case "$(basename "$0")" in
  git) [ "$1" = symbolic-ref ] && { [ -n "$FAKE_DETACHED" ] && exit 1; echo "${FAKE_BRANCH:-main}"; }; [ "$1" = pull ] && [ -n "$FAKE_PULL_FAILS" ] && exit 1 ;;
esac
exit 0
"""


class SourceCheckout(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / "scripts").mkdir()
        shutil.copy(SCRIPT, self.d / "scripts" / "update.sh")
        (self.d / ".git").mkdir()                                  # what makes it a source checkout, with the Dockerfile
        (self.d / "Dockerfile").write_text("FROM scratch\n")
        (self.d / "VERSION").write_text("1.0.0\n")
        self.bin = self.d / "bin"
        self.bin.mkdir()
        for name in ("git", "docker"):
            f = self.bin / name
            f.write_text(FAKE)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.log = self.d / "calls.log"
        self.home = self.d / "factory"
        (self.home / "secrets").mkdir(parents=True)
        (self.d / ".env").write_text(f"FACTORY_HOME={self.home}\n")

    def run_update(self, answer: str | None = "y\n", token_file: str | None = TOKEN + "\n", env=None, *args):
        if token_file is not None:
            (self.home / "secrets" / "github_token").write_text(token_file)
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_LOG": str(self.log), "HOME": str(self.d)}
        e.update(env or {})
        r = subprocess.run(["bash", str(self.d / "scripts" / "update.sh"), *args], input=answer or "", capture_output=True, text=True, env=e, timeout=30)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    def test_yes_pulls_with_the_token_then_builds(self):
        r, calls = self.run_update("y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("source checkout", r.stdout)
        self.assertIn("git pull --ff-only", r.stdout)                  # it says what it is about to do before asking
        pulls = [c for c in calls if c.startswith("git pull")]
        self.assertEqual(pulls, ["git pull --ff-only https://github.com/theninjadojo/software-factory.git +refs/heads/main:refs/remotes/origin/main"])
        self.assertIn("docker compose --profile build build", calls)
        self.assertLess(calls.index(pulls[0]), calls.index("docker compose --profile build build"))      # pull first, then build
        self.assertIn("docker compose up -d", r.stdout)                # told how to restart, not done for the person

    def test_the_token_goes_in_the_environment_only_for_github_and_never_on_a_command_line(self):
        r, calls = self.run_update("yes\n")
        text = "\n".join(calls)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        self.assertIn(f"GIT_CONFIG_VALUE_0=Authorization: Basic {basic}", calls)
        self.assertIn("GIT_CONFIG_KEY_0=http.https://github.com/.extraheader", calls)    # scoped to github.com
        build_at = calls.index("docker compose --profile build build")
        self.assertFalse([c for c in calls[build_at:] if c.startswith("GIT_CONFIG")])   # gone again before docker runs
        self.assertNotIn(TOKEN, (self.d / ".git").as_posix())                           # nothing written to the checkout

    def test_the_github_token_variable_wins_over_the_file(self):
        other = "ghp" + "_" + "FROMTHEENVIRONMENT0123456789"
        _, calls = self.run_update("y\n", env={"GITHUB_TOKEN": other})
        basic = base64.b64encode(f"x-access-token:{other}".encode()).decode()
        self.assertIn(f"GIT_CONFIG_VALUE_0=Authorization: Basic {basic}", calls)

    def test_no_or_empty_or_other_answer_changes_nothing(self):
        for answer in ("n\n", "\n", "", "maybe\n", "no\n"):
            with self.subTest(answer=answer):
                self.log.unlink(missing_ok=True)
                r, calls = self.run_update(answer)
                self.assertEqual(r.returncode, 1)
                self.assertEqual([c for c in calls if c.startswith(("git pull", "docker"))], [])
                self.assertIn("git pull && docker compose --profile build build", r.stderr)    # the old instructions, for doing it by hand

    def test_no_token_uses_gits_own_credentials_and_says_so(self):
        r, calls = self.run_update("y\n", token_file=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No GitHub token found", r.stdout)
        self.assertIn("git pull --ff-only", calls)
        self.assertFalse([c for c in calls if c.startswith("GIT_CONFIG")])
        self.assertIn("docker compose --profile build build", calls)

    def test_a_failed_pull_stops_before_building(self):
        r, calls = self.run_update("y\n", env={"FAKE_PULL_FAILS": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("docker compose --profile build build", calls)

    def test_a_detached_head_is_refused_before_pulling(self):
        r, calls = self.run_update("y\n", env={"FAKE_DETACHED": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("detached", r.stderr)
        self.assertEqual([c for c in calls if c.startswith(("git pull", "docker"))], [])

    def test_a_worktree_counts_as_a_source_checkout(self):
        (self.d / ".git").rmdir()
        (self.d / ".git").write_text("gitdir: /elsewhere\n")           # in a worktree .git is a file
        r, calls = self.run_update("y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docker compose --profile build build", calls)

    def test_dry_run_prints_the_commands_without_running_them(self):
        r, calls = self.run_update("y\n", env={"DRY_RUN": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+ docker compose --profile build build", r.stdout)
        self.assertFalse([c for c in calls if c.startswith(("git pull", "docker"))])
        self.assertNotIn(TOKEN, r.stdout)

    def test_a_release_install_is_not_treated_as_a_source_checkout(self):
        shutil.rmtree(self.d / ".git")
        (self.d / "Dockerfile").unlink()
        r, _ = self.run_update("y\n", None, {"DRY_RUN": "1", "SHIKUMI_ASSET_BASE": "file:///nonexistent"}, "v9.9.9")
        self.assertNotIn("source checkout", r.stdout)
        self.assertIn("could not download", r.stderr)                  # it went down the release path (which fails here: no files)


if __name__ == "__main__":
    unittest.main()
