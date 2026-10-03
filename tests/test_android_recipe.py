"""The Android recipe (host side) and the script inside the image, with stub docker/adb/emulator/gradlew: no SDK, no emulator, no downloads."""
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "worker" / "recipes" / "android-test.sh"
INNER = ROOT / "sandbox" / "android" / "android-run.sh"
DOCKERFILE = ROOT / "sandbox" / "android" / "Dockerfile"


def stub(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class HostRecipe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin, self.proj, self.cache, self.calls = self.tmp / "bin", self.tmp / "proj", self.tmp / "cache", self.tmp / "calls"
        self.bin.mkdir()
        self.proj.mkdir()
        (self.proj / "gradlew").write_text("#!/bin/sh\n")
        self.docker(0)

    def docker(self, code):
        stub(self.bin / "docker", f'echo "$@" >> "{self.calls}"\nexit {code}\n')

    def run_recipe(self, *args, path=None, cwd=None):
        env = {"PATH": path if path is not None else f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp)}
        r = subprocess.run([str(RECIPE), "--cache", str(self.cache), *args], cwd=cwd or self.proj, env=env, capture_output=True, text=True, timeout=30)
        called = self.calls.read_text().strip() if self.calls.exists() else ""
        return r.returncode, r.stdout + r.stderr, called

    def test_unit_run_is_confined_to_a_throwaway_container(self):
        code, out, call = self.run_recipe()
        self.assertEqual(code, 0, out)
        for flag in ("--rm", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "--pids-limit=2048", "--memory=6g", "--cpus=4",
                     "--pull=never", f"--user {os.getuid()}:{os.getgid()}"):
            self.assertIn(flag, call)
        self.assertTrue(call.endswith("factory-android:latest unit test"), call)
        mounts = re.findall(r"-v (\S+)", call)
        self.assertEqual(sorted(m.split(":")[1] for m in mounts), ["/gradle-cache", "/work"])           # nothing else from the host
        self.assertIn(f"-v {self.proj.resolve()}:/work:rw", call)
        self.assertNotIn("--device", call)
        self.assertNotIn("--privileged", call)
        self.assertNotIn("docker.sock", call)
        self.assertTrue(self.cache.is_dir())

    def test_emulator_mode_adds_only_the_kvm_device(self):
        kvm = self.tmp / "kvm"
        kvm.write_text("")
        code, out, call = self.run_recipe("--emulator", "--task", "connectedDebugAndroidTest", "--kvm-device", str(kvm))
        self.assertEqual(code, 0, out)
        self.assertIn(f"--device {kvm}", call)
        self.assertTrue(call.endswith("emulator connectedDebugAndroidTest"), call)
        self.assertNotIn("--privileged", call)

    def test_emulator_without_kvm_is_an_environment_problem(self):
        code, out, call = self.run_recipe("--emulator", "--kvm-device", str(self.tmp / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("KVM", out)
        self.assertEqual(call, "")                                          # never started a container

    def test_subfolder_is_what_gets_mounted(self):
        (self.proj / "app").mkdir()
        (self.proj / "app" / "gradlew").write_text("")
        code, out, call = self.run_recipe("--dir", "app", "--task", ":app:testDebugUnitTest")
        self.assertEqual(code, 0, out)
        self.assertIn(f"-v {(self.proj / 'app').resolve()}:/work:rw", call)
        self.assertTrue(call.endswith("unit :app:testDebugUnitTest"))

    def test_exit_codes_distinguish_failures_from_environment_problems(self):
        for container, expected in ((1, 1), (2, 2), (125, 2), (126, 2), (127, 2), (137, 1), (0, 0)):
            self.docker(container)
            self.assertEqual(self.run_recipe()[0], expected, f"container exit {container}")
        self.docker(125)
        self.assertIn("is the image", self.run_recipe()[1])

    def test_bad_arguments_never_reach_docker(self):
        for args in (["--dir", "../x"], ["--dir", "/etc"], ["--dir", ""], ["--task", "test; rm -rf /"], ["--task", "$(id)"], ["--task", ""],
                     ["--image", "evil image"], ["--image", "x;y"], ["--memory", "6g;x"], ["--cpus", "many"], ["--engine", "lxc"], ["--surprise"],
                     ["--dir", "missing"]):
            self.assertEqual(self.run_recipe(*args)[0], 2, args)
        self.assertEqual(self.calls.exists() and self.calls.read_text(), False)
        (self.proj / "gradlew").unlink()
        self.assertEqual(self.run_recipe()[0], 2)                           # no wrapper

    def test_relative_cache_is_refused(self):
        r = subprocess.run([str(RECIPE), "--cache", "rel/cache"], cwd=self.proj, env={"PATH": f"{self.bin}:/usr/bin:/bin"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_no_container_engine_is_an_environment_problem(self):
        code, out, _ = self.run_recipe(path="/usr/bin:/bin" if not Path("/usr/bin/docker").exists() and not Path("/usr/bin/podman").exists() else str(self.tmp))
        self.assertEqual(code, 2)

    def test_podman_is_used_when_docker_is_absent(self):
        (self.bin / "docker").unlink()
        stub(self.bin / "podman", f'echo "podman $@" >> "{self.calls}"\nexit 0\n')
        fake_path = f"{self.bin}:{self._tools()}"
        code, out, call = self.run_recipe(path=fake_path)
        self.assertEqual(code, 0, out)
        self.assertTrue(call.startswith("podman run"), call)

    def _tools(self) -> str:
        """A PATH folder with only the basic tools the script needs, so a real docker in /usr/bin cannot be picked up."""
        d = self.tmp / "tools"
        d.mkdir(exist_ok=True)
        for t in ("id", "mkdir", "cd", "pwd", "tail", "tr", "cat", "sh", "dirname"):
            src = next((Path(p) / t for p in ("/usr/bin", "/bin") if (Path(p) / t).exists()), None)
            if src and not (d / t).exists():
                (d / t).symlink_to(src)
        return str(d)


class InsideImage(unittest.TestCase):
    """android-run.sh with a stub gradlew and stub adb/emulator/avdmanager."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin, self.proj, self.log = self.tmp / "bin", self.tmp / "work", self.tmp / "log"
        self.bin.mkdir()
        self.proj.mkdir()
        self.gradle(0)
        log = self.log
        stub(self.bin / "adb", f'echo "adb $@" >> "{log}"\ncase "$1" in\n shell) echo "${{BOOTED:-1}}";;\n exec-out) printf "PNGDATA";;\nesac\nexit 0\n')
        stub(self.bin / "emulator", f'echo "emulator $@" >> "{log}"\nexec sleep 30\n')
        stub(self.bin / "avdmanager", f'echo "avdmanager $@" >> "{log}"\nexit ${{AVD_CODE:-0}}\n')

    def gradle(self, code):
        stub(self.proj / "gradlew", f'echo "gradlew $@" >> "{self.log}"\nexit {code}\n')

    def run_inner(self, *args, **env):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "ANDROID_HOME": str(self.tmp / "sdk"), "ANDROID_USER_HOME": str(self.tmp / "ah"),
             "ANDROID_AVD_HOME": str(self.tmp / "ah" / "avd"), "BOOT_POLL": "0", "BOOT_TIMEOUT": "10", **env}
        r = subprocess.run([str(INNER), *args], cwd=self.proj, env=e, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, self.log.read_text().splitlines() if self.log.exists() else []

    def test_unit_mode_runs_gradle_only(self):
        code, out, log = self.run_inner("unit", ":app:test")
        self.assertEqual((code, log), (0, ["gradlew --no-daemon --console=plain :app:test"]), out)

    def test_failing_gradle_is_exit_1(self):
        self.gradle(3)
        code, out, _ = self.run_inner("unit", "test")
        self.assertEqual(code, 1)
        self.assertIn("FAILED: gradlew test", out)

    def test_environment_problems_are_exit_2(self):
        self.assertEqual(self.run_inner("unit", "bad task")[0], 2)
        self.assertEqual(self.run_inner("unit", "x;y")[0], 2)
        self.assertEqual(self.run_inner("surprise", "test")[0], 2)
        self.assertEqual(self.run_inner("")[0], 2)
        (self.proj / "gradlew").unlink()
        self.assertEqual(self.run_inner("unit", "test")[0], 2)

    def test_emulator_mode_boots_tests_screenshots_and_cleans_up(self):
        code, out, log = self.run_inner("emulator", "connectedDebugAndroidTest")
        self.assertEqual(code, 0, out)
        order = [next(i for i, l in enumerate(log) if l.startswith(p)) for p in ("avdmanager create avd", "emulator -avd ci", "gradlew --no-daemon")]
        self.assertEqual(order, sorted(order))
        self.assertEqual((self.proj / "build" / "screens" / "final.png").read_bytes(), b"PNGDATA")
        self.assertIn("adb kill-server", log)

    def test_emulator_mode_still_screenshots_when_tests_fail(self):
        self.gradle(1)
        code, out, _ = self.run_inner("emulator", "connectedDebugAndroidTest")
        self.assertEqual(code, 1)
        self.assertTrue((self.proj / "build" / "screens" / "final.png").exists())      # the failing screen is the useful one

    def test_emulator_that_never_boots_is_exit_2_not_a_test_failure(self):
        code, out, log = self.run_inner("emulator", "connectedDebugAndroidTest", BOOTED="0")
        self.assertEqual(code, 2)
        self.assertIn("did not boot", out)
        self.assertFalse(any(l.startswith("gradlew") for l in log))

    def test_missing_avd_or_emulator_is_exit_2(self):
        self.assertEqual(self.run_inner("emulator", "t", AVD_CODE="1")[0], 2)
        (self.bin / "emulator").unlink()
        self.assertEqual(self.run_inner("emulator", "t")[0], 2)


class ImageDefinition(unittest.TestCase):
    def test_dockerfile_pins_verifies_and_stays_small_in_privilege(self):
        d = DOCKERFILE.read_text()
        self.assertRegex(d, r"FROM \S+:\d+\.\d+")                              # a pinned base image, not :latest
        self.assertIn("CMDLINE_TOOLS_SHA256", d)
        self.assertIn("sha256sum -c", d)                                         # the download is verified
        self.assertIn('test -n "$CMDLINE_TOOLS_SHA256"', d)                      # and the build fails without a checksum
        self.assertNotIn("curl | sh", d.replace("curl -fsSL -o", ""))
        self.assertIn("ENTRYPOINT", d)
        self.assertTrue(os.access(INNER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
