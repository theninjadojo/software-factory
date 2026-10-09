import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_guard", ROOT / "scripts" / "release_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Guard(unittest.TestCase):
    def test_a_release_must_be_higher_than_every_tag(self):
        tags = ["v0.1.0", "v0.2.1", "v0.2.2", "latest", "v1.0.0-rc1"]
        self.assertIsNone(guard.check("v0.2.3", tags))
        self.assertIsNone(guard.check("v0.10.0", tags))                       # numeric, not alphabetical
        self.assertIn("not higher", guard.check("v0.2.2", tags))
        self.assertIn("not higher", guard.check("v0.2.0", tags))
        self.assertIn("not a version tag", guard.check("nightly", tags))
        self.assertIsNone(guard.check("v0.1.0", []))

    def test_cli_compares_only_branch_releases(self):
        run = lambda *a: subprocess.run(["python3", str(ROOT / "scripts" / "release_guard.py"), *a], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run("v99.0.0", "tag").returncode, 0)                 # a hand-pushed tag is the maintainer's call
        self.assertNotEqual(run("nightly", "tag").returncode, 0)
        self.assertEqual(run("v0.0.1", "branch").returncode, 1)               # lower than the tags in this repo


class Workflow(unittest.TestCase):
    def test_release_runs_on_main_and_tags_and_skips_existing_tags(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text()          # (text checks: PyYAML is not on the runners' python)
        self.assertIn("branches: [main]", text)
        self.assertIn('tags: ["v*"]', text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("already released: nothing to do", text)
        # the tests run (test.yml, called) before anything is published, and only when there is something to release
        self.assertIn("uses: ./.github/workflows/test.yml", text)
        self.assertIn("needs: [decide, test]", text)
        self.assertEqual(text.count("if: needs.decide.outputs.go == 'true'"), 2)       # the test and the release jobs
        self.assertIn("workflow_call:", (ROOT / ".github" / "workflows" / "test.yml").read_text())
        write = text.index("contents: write")
        self.assertGreater(write, text.index("  release:\n"))                           # the write token belongs to the release job only

    def test_no_workflow_runs_on_a_self_hosted_runner(self):
        # The repository is public: a fork's pull request can change any workflow, so none may run on a self-hosted runner.
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            runs_on = [l.strip() for l in path.read_text().splitlines() if l.strip().startswith("runs-on:")]
            self.assertTrue(runs_on, path.name)
            self.assertTrue(all(l.startswith("runs-on: ubuntu-") for l in runs_on), (path.name, runs_on))
        test = (ROOT / ".github" / "workflows" / "test.yml").read_text()
        self.assertIn("pull_request:", test)
        self.assertIn("permissions:\n  contents: read\n", test)


class AutoUpdate(unittest.TestCase):
    """update-native.sh --auto: only a newer patch, and never over a run in flight."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        (self.tmp / "app").mkdir(); (self.tmp / "state").mkdir(); (self.tmp / "bin").mkdir()
        (self.tmp / "app" / "VERSION").write_text("0.2.1\n")
        stub = self.tmp / "bin" / "podman"
        # the stub notes whether the factory was paused when the update looked for runs in flight
        stub.write_text('#!/bin/sh\nif [ "$1" = ps ]; then [ -e "$SHIKUMI_ROOT/state/PAUSED" ] && touch "$SHIKUMI_ROOT/paused-at-check"; echo abc123; fi\nexit 0\n')
        stub.chmod(0o755)

    def run_auto(self, tag, busy=False):
        env = {**os.environ, "SHIKUMI_ROOT": str(self.tmp), "PATH": (f"{self.tmp / 'bin'}:" if busy else "") + os.environ["PATH"], "XDG_RUNTIME_DIR": str(self.tmp), "SHIKUMI_SETTLE": "0"}
        return subprocess.run(["bash", str(ROOT / "deploy" / "update-native.sh"), "--auto", tag], capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)

    def test_minor_and_major_releases_are_left_for_a_person(self):
        for tag in ("v0.3.0", "v1.0.0"):
            r = self.run_auto(tag)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("leaving it for a person", r.stdout)
        self.assertEqual((self.tmp / "app" / "VERSION").read_text().strip(), "0.2.1")

    def test_every_release_mode_takes_a_minor_or_major_but_never_an_older_one(self):
        (self.tmp / "state" / "AUTO_UPDATE").write_text("all\n")
        for tag in ("v0.3.0", "v1.0.0", "v0.2.2"):
            r = self.run_auto(tag, busy=True)                  # busy: it got past the version check and stopped at the run in flight
            self.assertIn("will try again at the next timer", r.stdout, tag)
        self.assertIn("leaving it for a person", self.run_auto("v0.2.0").stdout)
        (self.tmp / "state" / "AUTO_UPDATE").write_text("patch\n")
        self.assertIn("leaving it for a person", self.run_auto("v0.3.0").stdout)

    def test_an_older_or_same_patch_is_ignored(self):
        self.assertIn("leaving it for a person", self.run_auto("v0.2.0").stdout)

    def test_a_newer_patch_waits_while_a_run_is_in_flight(self):
        r = self.run_auto("v0.2.2", busy=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("will try again at the next timer", r.stdout)
        self.assertEqual((self.tmp / "app" / "VERSION").read_text().strip(), "0.2.1")
        self.assertTrue((self.tmp / "paused-at-check").exists())                 # no run could start between the check and a restart
        self.assertFalse((self.tmp / "state" / "PAUSED").exists())               # and the pause is lifted again

    def test_a_pause_a_person_set_is_left_in_place(self):
        (self.tmp / "state" / "PAUSED").write_text("")
        self.run_auto("v0.2.2", busy=True)
        self.assertTrue((self.tmp / "state" / "PAUSED").exists())


class Units(unittest.TestCase):
    def test_timer_is_opt_in_and_runs_the_auto_mode(self):
        svc = (ROOT / "deploy" / "systemd" / "shikumi-update.service").read_text()
        self.assertIn("update-native.sh --auto", svc)
        self.assertIn("timers.target", (ROOT / "deploy" / "systemd" / "shikumi-update.timer").read_text())


if __name__ == "__main__":
    unittest.main()
