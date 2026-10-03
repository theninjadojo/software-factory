"""The iOS recipe, with a stub `tart` that records every call and can fail at any step. No Mac, no Xcode, no VM: this proves the logic, the
argument checks, the exit codes and that the VM is always cleaned up, not that Xcode builds anything."""
import io
import os
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "worker"))
import worker as W                                                       # noqa: E402

RECIPE = ROOT / "worker" / "recipes" / "ios-test.sh"

TART = r'''#!/bin/sh
echo "tart $*" >> "$CALLS"
sub="$1"; shift
case "$sub" in
  list)
    echo "Source Name Disk Size State"
    [ -n "${NO_IMAGE:-}" ] || echo "local shikumi-ios 50 50 stopped"
    echo "local some-other-vm 20 20 stopped"
    [ -z "${LEFTOVER:-}" ] || echo "local shikumi-job-1-1 50 50 stopped"
    ;;
  clone) exit "${CLONE_CODE:-0}" ;;
  run) [ -z "${RUN_EXIT:-}" ] || exit "$RUN_EXIT"; exec sleep 30 ;;
  ip) [ "${IP_CODE:-0}" = 0 ] && echo 192.168.64.5; exit "${IP_CODE:-0}" ;;
  exec)
    [ "$1" = "-i" ] && shift
    vm="$1"; shift
    cmd="$*"
    case "$cmd" in
      true) exit "${AGENT_CODE:-0}" ;;
      *"tar -xf -"*) cat > "$TARFILE"; exit "${COPY_CODE:-0}" ;;
      *xcodebuild*) echo "XCODE: $cmd" >> "$CALLS"; [ -z "${XCODE_SLEEP:-}" ] || sleep "$XCODE_SLEEP"; exit "${XCODE_CODE:-0}" ;;
      *simctl*) [ -n "${NO_SHOT:-}" ] || printf PNGDATA; exit 0 ;;
      *) exit "${PREP_CODE:-0}" ;;
    esac ;;
  stop|delete) exit 0 ;;
esac
'''


class IosRecipe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin, self.proj = self.tmp / "bin", self.tmp / "proj"
        self.calls, self.tarfile = self.tmp / "calls", self.tmp / "copied.tar"
        self.bin.mkdir()
        self.proj.mkdir()
        t = self.bin / "tart"
        t.write_text(TART)
        t.chmod(t.stat().st_mode | stat.S_IEXEC)
        (self.proj / "App.xcodeproj").mkdir()
        (self.proj / "App.xcodeproj" / "project.pbxproj").write_text("x")
        (self.proj / ".git").mkdir()
        (self.proj / ".git" / "config").write_text("secret remote")

    def env(self, **extra):
        return {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "CALLS": str(self.calls), "TARFILE": str(self.tarfile), "BOOT_POLL": "0", **extra}

    def run_recipe(self, *args, env=None, cwd=None, bare=False):
        args = args or (() if bare else ("--scheme", "App", "--project", "App.xcodeproj"))
        r = subprocess.run([str(RECIPE), *args], cwd=cwd or self.proj, env=env or self.env(), capture_output=True, text=True, timeout=60)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return r.returncode, r.stdout + r.stderr, calls

    def verbs(self, calls):
        return [c.split()[1] for c in calls if c.startswith("tart ")]

    def test_a_passing_run_clones_boots_copies_tests_screenshots_and_deletes(self):
        code, out, calls = self.run_recipe()
        self.assertEqual(code, 0, out)
        v = self.verbs(calls)
        # `tart run` is started in the background, so its log line can land after `tart ip`: only the real dependencies are ordered
        order = [v.index(x) for x in ("clone", "ip", "exec")]
        self.assertEqual(order, sorted(order))
        self.assertGreater(v.index("run"), v.index("clone"))
        self.assertEqual(v[-2:], ["stop", "delete"])
        self.assertIn("tart clone shikumi-ios shikumi-job-", calls[next(i for i, c in enumerate(calls) if " clone " in c)])
        xc = next(c for c in calls if c.startswith("XCODE:"))
        for piece in ("xcodebuild test -project App.xcodeproj", "-scheme App", "-destination 'platform=iOS Simulator,name=iPhone 15'",
                      "CODE_SIGNING_ALLOWED=NO", "-derivedDataPath /tmp/dd"):
            self.assertIn(piece, xc)
        self.assertEqual((self.proj / "build" / "screens" / "final.png").read_bytes(), b"PNGDATA")
        self.assertIn("tart run --no-graphics shikumi-job-", "\n".join(calls))

    def test_the_checkout_is_copied_without_git_history_or_credentials(self):
        self.run_recipe()
        names = tarfile.open(self.tarfile).getnames()
        self.assertIn("./App.xcodeproj/project.pbxproj", names)
        self.assertFalse([n for n in names if ".git" in n.split("/")], names)

    def test_workspace_and_destination_options(self):
        code, out, calls = self.run_recipe("--scheme", "My-App_1", "--workspace", "App.xcworkspace", "--destination", "platform=iOS Simulator,name=iPhone 15 Pro,OS=17.5")
        self.assertEqual(code, 0, out)
        xc = next(c for c in calls if c.startswith("XCODE:"))
        self.assertIn("-workspace App.xcworkspace -scheme My-App_1", xc)
        self.assertIn("-destination 'platform=iOS Simulator,name=iPhone 15 Pro,OS=17.5'", xc)

    def test_subfolder(self):
        (self.proj / "ios").mkdir()
        (self.proj / "ios" / "x.txt").write_text("1")
        code, out, _ = self.run_recipe("--scheme", "App", "--project", "App.xcodeproj", "--dir", "ios")
        self.assertEqual(code, 0, out)
        self.assertIn("./x.txt", tarfile.open(self.tarfile).getnames())
        self.assertTrue((self.proj / "ios" / "build" / "screens" / "final.png").exists())

    def test_exit_codes(self):
        for xcode, want in ((0, 0), (65, 1), (1, 1), (137, 1), (64, 2), (66, 2), (70, 2), (73, 2)):
            self.calls.unlink(missing_ok=True)
            self.assertEqual(self.run_recipe(env=self.env(XCODE_CODE=str(xcode)))[0], want, f"xcodebuild exit {xcode}")

    def test_failure_keeps_the_screenshot_and_still_deletes_the_vm(self):
        code, out, calls = self.run_recipe(env=self.env(XCODE_CODE="65"))
        self.assertEqual(code, 1)
        self.assertIn("FAILED: xcodebuild test", out)
        self.assertTrue((self.proj / "build" / "screens" / "final.png").exists())
        self.assertEqual(self.verbs(calls)[-2:], ["stop", "delete"])

    def test_no_screenshot_means_no_artifact_file(self):
        self.run_recipe(env=self.env(NO_SHOT="1"))
        self.assertFalse((self.proj / "build" / "screens" / "final.png").exists())

    def test_every_environment_failure_is_exit_2_and_cleans_up(self):
        for what, env in (("clone fails", {"CLONE_CODE": "1"}), ("no address", {"IP_CODE": "1"}), ("vm stops at boot", {"RUN_EXIT": "1", "AGENT_CODE": "1"}),
                          ("agent never answers", {"AGENT_CODE": "1"}), ("copy fails", {"COPY_CODE": "1"}), ("prepare fails", {"PREP_CODE": "1"})):
            self.calls.unlink(missing_ok=True)
            code, out, calls = self.run_recipe("--scheme", "App", "--project", "App.xcodeproj", "--boot-timeout", "5", env=self.env(**env))
            self.assertEqual(code, 2, f"{what}: {out}")
            self.assertEqual(self.verbs(calls)[-2:], ["stop", "delete"], what)
            self.assertFalse(any(c.startswith("XCODE:") for c in calls), what)           # never tested a half-prepared VM

    def test_leftover_job_vms_are_deleted_but_nothing_else(self):
        code, out, calls = self.run_recipe(env=self.env(LEFTOVER="1"))
        self.assertEqual(code, 0, out)
        deleted = [c.split()[-1] for c in calls if c.startswith("tart delete")]
        self.assertIn("shikumi-job-1-1", deleted)
        self.assertNotIn("shikumi-ios", deleted)
        self.assertNotIn("some-other-vm", deleted)

    def test_missing_tart_or_image_is_exit_2(self):
        (self.bin / "tart").unlink()
        code, out, _ = self.run_recipe(env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp)})
        self.assertEqual(code, 2)
        self.assertIn("tart is not installed", out)
        (self.bin / "tart").write_text(TART)
        (self.bin / "tart").chmod(0o755)
        code, out, calls = self.run_recipe(env=self.env(NO_IMAGE="1"))
        self.assertEqual((code, "no VM image named shikumi-ios" in out), (2, True))
        self.assertNotIn("clone", self.verbs(calls))
        self.assertEqual(self.run_recipe("--scheme", "App", "--project", "App.xcodeproj", "--image", "other")[0], 2)    # not in the list either

    def test_bad_arguments_never_reach_tart(self):
        bad = [[], ["--scheme", "App"], ["--project", "A.xcodeproj"], ["--scheme", "", "--project", "A"], ["--scheme", "A b", "--project", "A"],
               ["--scheme", "A;rm", "--project", "A"], ["--scheme", "$(id)", "--project", "A"], ["--scheme", "A", "--project", "../A"],
               ["--scheme", "A", "--project", "/etc/A"], ["--scheme", "A", "--project", "A", "--workspace", "B"],
               ["--scheme", "A", "--project", "A'B"], ["--scheme", "A", "--project", "A", "--destination", "x'; rm -rf / #"],
               ["--scheme", "A", "--project", "A", "--destination", "$(id)"], ["--scheme", "A", "--project", "A", "--destination", "a`id`"],
               ["--scheme", "A", "--project", "A", "--destination", "ok\nrm -rf /"], ["--scheme", "A", "--project", "A", "--destination", ""],
               ["--scheme", "A", "--project", "A", "--image", "a b"], ["--scheme", "A", "--project", "A", "--dir", "../x"],
               ["--scheme", "A", "--project", "A", "--dir", "/etc"], ["--scheme", "A", "--project", "A", "--boot-timeout", "soon"],
               ["--scheme", "A", "--project", "A", "--surprise"]]
        for args in bad:
            self.calls.unlink(missing_ok=True)
            self.assertEqual(self.run_recipe(*args, bare=True)[0], 2, args)
            self.assertFalse(self.calls.exists(), args)

    def test_sigterm_from_the_worker_deletes_the_vm(self):
        """The worker stops a timed-out recipe with SIGTERM (then SIGKILL after a grace): the clone must not outlive the job."""
        proc = subprocess.Popen([str(RECIPE), "--scheme", "App", "--project", "App.xcodeproj"], cwd=self.proj, env=self.env(XCODE_SLEEP="30"),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):                                                    # wait until it is inside xcodebuild
            if self.calls.exists() and any(c.startswith("XCODE:") for c in self.calls.read_text().splitlines()):
                break
            time.sleep(0.1)
        W.stop_group(proc, grace=10)
        verbs = self.verbs(self.calls.read_text().splitlines())
        self.assertEqual(verbs[-2:], ["stop", "delete"])


class StopGroup(unittest.TestCase):
    """worker.stop_group: SIGTERM first so cleanup traps run, SIGKILL only for what ignores it."""
    def test_a_recipe_that_traps_sigterm_gets_to_clean_up(self):
        tmp = Path(tempfile.mkdtemp())
        script = tmp / "r.sh"
        script.write_text(f'#!/bin/sh\ntrap \'echo cleaned > "{tmp}/done"; exit 0\' TERM\nwhile true; do sleep 0.1; done\n')
        script.chmod(0o755)
        proc = subprocess.Popen([str(script)], start_new_session=True)
        time.sleep(0.5)
        W.stop_group(proc, grace=5)
        self.assertEqual((tmp / "done").read_text().strip(), "cleaned")

    def test_a_recipe_that_ignores_sigterm_is_killed_after_the_grace(self):
        tmp = Path(tempfile.mkdtemp())
        script = tmp / "r.sh"
        script.write_text('#!/bin/sh\ntrap "" TERM\nwhile true; do sleep 0.1; done\n')
        script.chmod(0o755)
        proc = subprocess.Popen([str(script)], start_new_session=True)
        time.sleep(0.5)
        started = time.monotonic()
        W.stop_group(proc, grace=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(proc.poll())

    def test_an_already_finished_process_is_fine(self):
        proc = subprocess.Popen(["true"], start_new_session=True)
        proc.wait()
        W.stop_group(proc, grace=1)


if __name__ == "__main__":
    unittest.main()
