"""The Android recipe (host side) and the script inside the image, with stub docker/adb/emulator/gradlew: no SDK, no emulator, no downloads."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "worker" / "recipes" / "android-test.sh"
INNER = ROOT / "sandbox" / "android" / "android-run.sh"
DOCKERFILE = ROOT / "sandbox" / "android" / "Dockerfile"
sys.path.insert(0, str(ROOT / "worker"))
import worker as W                                                       # noqa: E402


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

    def run_recipe(self, *args, path=None, cwd=None, **more):
        env = {"PATH": path if path is not None else f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), **more}
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

    def test_screens_records_automatically_with_a_bigger_home(self):
        code, out, call = self.run_recipe("--screens")
        self.assertEqual(code, 0, out)
        self.assertTrue(call.endswith("factory-android:latest unit auto screens"), call)
        self.assertIn("/home/builder:rw,size=2g", call)
        self.assertNotIn("--device", call)
        code, out, call = self.run_recipe("--screens", "--task", ":app:recordRoborazziDebug")
        self.assertTrue(call.splitlines()[-1].endswith("unit :app:recordRoborazziDebug screens"), call)

    def test_screens_on_the_emulator_runs_connected_tests(self):
        kvm = self.tmp / "kvm"
        kvm.write_text("")
        code, out, call = self.run_recipe("--screens", "--emulator", "--kvm-device", str(kvm))
        self.assertEqual(code, 0, out)
        self.assertTrue(call.endswith("emulator connectedDebugAndroidTest screens"), call)

    def test_screens_from_a_subfolder_end_up_at_the_checkout_top(self):
        (self.proj / "android").mkdir()
        (self.proj / "android" / "gradlew").write_text("")
        out_dir = self.proj / "android" / "screens-out"
        stub(self.bin / "docker", f'echo "$@" >> "{self.calls}"\nmkdir -p "{out_dir}" && printf PNG > "{out_dir}/home-1.png"\nexit 1\n')
        code, out, _ = self.run_recipe("--screens", "--dir", "android")
        self.assertEqual(code, 1)                                           # failing tests stay a failure, the screenshots are still kept
        self.assertEqual([f.name for f in (self.proj / "screens-out").iterdir()], ["home-1.png"])

    def demo(self, files, *args, docker_body=""):
        """A demo run (FACTORY_STEPS=1) of the Screens recipe limited to `files`; (exit code, output, docker call, results folder)."""
        kvm, results, listed = self.tmp / "kvm", self.tmp / "results", self.tmp / "test-files.txt"
        kvm.write_text("")
        results.mkdir(exist_ok=True)
        listed.write_text("".join(f + "\n" for f in files))
        if docker_body:
            stub(self.bin / "docker", f'echo "$@" >> "{self.calls}"\n{docker_body}\nexit 0\n')
        code, out, call = self.run_recipe("--screens", "--kvm-device", str(kvm), *args, FACTORY_STEPS="1",
                                          FACTORY_RESULTS_DIR=str(results), FACTORY_TEST_FILES=str(listed))
        return code, out, call, results

    def test_a_demo_run_records_steps_on_the_emulator_and_hands_over_what_they_show(self):
        steps = self.proj / ".factory-steps"
        body = (f'mkdir -p "{steps}" && printf "a.png\\t1\\tOpen\\t\\n" > "{steps}/steps.tsv" && printf "<testsuite/>" > "{steps}/junit.xml"'
                f' && ln -s "{self.tmp}/secret" "{steps}/e2e-shots.jsonl"')
        (self.tmp / "secret").write_text("SECRET")
        code, out, call, results = self.demo(["app/src/androidTest/java/a/LoginTest.kt", "web/e2e/x.spec.ts", "../x", "a b"],
                                             docker_body=body)
        self.assertEqual(code, 0, out)
        self.assertTrue(call.endswith("factory-android:latest emulator connectedDebugAndroidTest steps"), call)   # without --emulator
        self.assertIn(f"--device {self.tmp / 'kvm'}", call)
        self.assertIn("-e STEP_FILES=app/src/androidTest/java/a/LoginTest.kt web/e2e/x.spec.ts -e STEP_PREFIX= ", call)
        self.assertIn("--tmpfs /tmp:rw,exec,size=16g", call)                 # the AVD needs room for its data partition
        self.assertEqual(sorted(f.name for f in results.iterdir()), ["junit.xml", "steps.tsv"])     # never what a link points at
        self.assertEqual((results / "steps.tsv").read_text(), "a.png\t1\tOpen\t\n")
        self.assertFalse(steps.exists())                                    # nothing of it stays in the checkout

    def test_a_demo_run_in_a_subfolder_runs_only_its_files(self):
        (self.proj / "android").mkdir()
        (self.proj / "android" / "gradlew").write_text("")
        code, out, call, _ = self.demo(["android/app/src/androidTest/java/a/LoginTest.kt", "app/src/androidTest/java/b/Other.kt"], "--dir", "android")
        self.assertEqual(code, 0, out)
        self.assertIn("-e STEP_FILES=app/src/androidTest/java/a/LoginTest.kt -e STEP_PREFIX=android/ ", call)
        self.calls.unlink()
        code, out, call, _ = self.demo(["app/src/androidTest/java/b/Other.kt"], "--dir", "android")
        self.assertEqual((code, call), (2, ""), out)                          # none of them is there: nothing runs

    def test_a_demo_run_keeps_a_connected_task_it_was_given(self):
        code, out, call, _ = self.demo(["app/src/androidTest/java/a/T.kt"], "--task", ":app:connectedStagingAndroidTest")
        self.assertTrue(call.endswith("emulator :app:connectedStagingAndroidTest steps"), call)
        self.calls.unlink()
        code, out, call, _ = self.demo(["app/src/androidTest/java/a/T.kt"], "--task", ":app:recordRoborazziDebug")
        self.assertTrue(call.endswith("emulator connectedDebugAndroidTest steps"), call)

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

    def test_java_gets_a_home_folder_although_the_uid_has_no_passwd_entry(self):
        stub(self.proj / "gradlew", f'echo "$JAVA_TOOL_OPTIONS" >> "{self.log}"\n')       # Robolectric failed with "?/.robolectric-download-lock"
        code, out, log = self.run_inner("unit", "test", HOME="/home/builder")
        self.assertEqual((code, log), (0, ["-Duser.home=/home/builder"]), out)

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

    def test_an_emulator_that_dies_while_booting_is_exit_2_at_once(self):
        stub(self.bin / "emulator", f'echo "emulator $@" >> "{self.log}"\necho "FATAL | Not enough space"\nexit 1\n')
        code, out, log = self.run_inner("emulator", "connectedDebugAndroidTest", BOOTED="0", BOOT_TIMEOUT="300")
        self.assertEqual(code, 2)
        self.assertIn("the emulator exited", out)
        self.assertIn("Not enough space", out)

    def test_missing_avd_or_emulator_is_exit_2(self):
        self.assertEqual(self.run_inner("emulator", "t", AVD_CODE="1")[0], 2)
        (self.bin / "emulator").unlink()
        self.assertEqual(self.run_inner("emulator", "t")[0], 2)


    def write(self, rel: str, data: bytes = b"PNG") -> Path:
        f = self.proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return f

    def test_screens_auto_picks_roborazzi_and_keeps_one_png_per_test(self):
        self.write("gradle/libs.versions.toml", b'roborazzi = "1.40.0"\n')
        stub(self.proj / "gradlew", f'echo "gradlew $@" >> "{self.log}"\nmkdir -p app/build/outputs/roborazzi feature/home/build/outputs/roborazzi app/build/intermediates\n'
             'printf A > app/build/outputs/roborazzi/com.example.LoginTest.login.png\nprintf B > feature/home/build/outputs/roborazzi/com.example.HomeScreenshotTest.homeWithAVeryLongNameIndeed.png\n'
             'printf C > app/build/intermediates/icon.png\n')
        code, out, log = self.run_inner("unit", "auto", "screens")
        self.assertEqual(code, 0, out)
        self.assertEqual(log, ["gradlew --no-daemon --console=plain recordRoborazziDebug"])
        shots = sorted(f.name for f in (self.proj / "screens-out").iterdir())
        self.assertEqual(len(shots), 2, shots)                              # not the build's own drawables
        self.assertTrue(shots[0].startswith("app-com-example-logintest-login-"), shots)
        self.assertRegex(shots[1], r"^feature-home-com-examp-withaverylongnameindeed-\d+\.png$")
        self.assertTrue(all(len(n) <= 61 for n in shots), shots)

    def test_screens_auto_picks_paparazzi_and_drops_committed_images(self):
        self.write("app/build.gradle.kts", b'plugins { id("app.cash.paparazzi") }\n')
        self.write("app/src/test/snapshots/images/old_DeletedTest_gone.png")
        stub(self.proj / "gradlew", f'echo "gradlew $@" >> "{self.log}"\nprintf N > app/src/test/snapshots/images/com.example_HomeTest_home.png\nexit 1\n')
        code, out, log = self.run_inner("unit", "auto", "screens")
        self.assertEqual(code, 1)                                           # a failing run still returns what it recorded
        self.assertEqual(log, ["gradlew --no-daemon --console=plain recordPaparazziDebug"])
        shots = [f.name for f in (self.proj / "screens-out").iterdir()]
        self.assertEqual(len(shots), 1, shots)
        self.assertTrue(shots[0].startswith("app-com-example-hometest-home-"), shots)

    def test_screens_auto_without_a_screenshot_library_is_exit_2(self):
        self.write("app/build.gradle", b"apply plugin: 'com.android.application'\n")
        code, out, log = self.run_inner("unit", "auto", "screens")
        self.assertEqual(code, 2)
        self.assertIn("no Roborazzi or Paparazzi", out)
        self.assertEqual(log, [])

    def test_screens_on_the_emulator_keep_test_output_or_the_last_screen(self):
        stub(self.proj / "gradlew", f'echo "gradlew $@" >> "{self.log}"\nd=app/build/outputs/connected_android_test_additional_output/debugAndroidTest/connected/Pixel\n'
             'mkdir -p $d && printf S > $d/checkout.png\n')
        code, out, _ = self.run_inner("emulator", "connectedDebugAndroidTest", "screens")
        self.assertEqual(code, 0, out)
        shots = [f.name for f in (self.proj / "screens-out").iterdir()]
        self.assertEqual(len(shots), 1, shots)
        self.assertIn("checkout", shots[0])
        self.gradle(0)
        import shutil
        shutil.rmtree(self.proj / "screens-out"); shutil.rmtree(self.proj / "app")
        code, out, _ = self.run_inner("emulator", "connectedDebugAndroidTest", "screens")
        self.assertEqual([f.name for f in (self.proj / "screens-out").iterdir()], ["final.png"])

    def test_screens_argument_is_checked(self):
        self.assertEqual(self.run_inner("unit", "test", "rm")[0], 2)
        self.assertFalse((self.proj / "screens-out").exists())

    def test_screens_never_copies_symlinks(self):
        (self.proj / "secret").write_bytes(b"SECRET")
        self.write("app/build.gradle", b"roborazzi\n")
        stub(self.proj / "gradlew", 'mkdir -p app/build/outputs/roborazzi && ln -s ../../../../secret app/build/outputs/roborazzi/x.png\n')
        code, out, _ = self.run_inner("unit", "auto", "screens")
        self.assertEqual(list((self.proj / "screens-out").iterdir()), [])


class Steps(unittest.TestCase):
    """android-run.sh ... steps: the step recorder added to a stub project's run, and its output turned into what the worker reads."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin, self.proj, self.log, self.pulled = self.tmp / "bin", self.tmp / "work", self.tmp / "log", self.tmp / "device"
        for d in (self.bin, self.proj, self.pulled, self.tmp / "src"):
            d.mkdir()
        stub(self.proj / "gradlew", f'echo "gradlew $@" >> "{self.log}"\nexit ${{GRADLE_CODE:-0}}\n')
        stub(self.bin / "adb", f'echo "adb $@" >> "{self.log}"\ncase "$1" in\n shell) echo 1;;\n pull) cp -R "{self.pulled}/." "$3";;\n'
                               ' exec-out) printf PNG;;\nesac\nexit 0\n')
        stub(self.bin / "emulator", 'exec sleep 30\n')
        stub(self.bin / "avdmanager", f'echo "avdmanager $@" >> "{self.log}"\nexit 0\n')
        for rel, text in (("app/src/androidTest/java/com/example/demo/LoginTest.java", "package com.example.demo;\n\npublic class LoginTest {}\n"),
                          ("feature/home/src/androidTestDebug/kotlin/com/example/home/HomeTest.kt", "package com.example.home\nclass HomeTest\n"),
                          ("app/src/test/java/com/example/demo/UnitTest.java", "package com.example.demo;\n")):
            (self.proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.proj / rel).write_text(text)

    def run_steps(self, files="app/src/androidTest/java/com/example/demo/LoginTest.java", mode="emulator", **env):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "ANDROID_HOME": str(self.tmp / "sdk"), "TMPDIR": str(self.tmp),
             "ANDROID_USER_HOME": str(self.tmp / "ah"), "ANDROID_AVD_HOME": str(self.tmp / "ah" / "avd"), "BOOT_POLL": "0",
             "STEPS_SRC": str(self.tmp / "src"), "STEP_FILES": files, "STEP_PREFIX": "", **env}
        r = subprocess.run([str(INNER), mode, "connectedDebugAndroidTest", "steps"], cwd=self.proj, env=e, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, self.log.read_text().splitlines() if self.log.exists() else []

    def device(self, name, data=b"PNG"):
        (self.pulled / name).write_bytes(data)

    def test_the_chosen_classes_run_unchanged_with_the_recorder_as_a_listener(self):
        code, out, log = self.run_steps("app/src/androidTest/java/com/example/demo/LoginTest.java "
                                        "feature/home/src/androidTestDebug/kotlin/com/example/home/HomeTest.kt app/src/test/java/com/example/demo/UnitTest.java")
        self.assertEqual(code, 0, out)
        self.assertIn("-d pixel_6 --force", next(l for l in log if l.startswith("avdmanager")))    # a phone-sized screen for the video
        gradle = next(l for l in log if l.startswith("gradlew"))
        self.assertEqual(gradle, f"gradlew --no-daemon --console=plain --init-script {self.tmp}/shikumi-steps.gradle "
                                 ":app:connectedDebugAndroidTest :feature:home:connectedDebugAndroidTest")
        init = (self.tmp / "shikumi-steps.gradle").read_text()
        self.assertIn(f"android.sourceSets.androidTest.java.srcDir('{self.tmp}/src')", init)
        self.assertIn("a.get('listener') + ',shikumi.steps.StepListener'", init)       # after the project's own listener
        self.assertIn("a.put('class', 'com.example.demo.LoginTest,com.example.home.HomeTest')", init)
        self.assertIn("not an instrumented test", out)                                  # the unit test is left out, said so
        self.assertIn("adb shell rm -rf /data/local/tmp/shikumi-steps; mkdir -p /data/local/tmp/shikumi-steps", log)
        self.assertLess(log.index("adb shell rm -rf /data/local/tmp/shikumi-steps; mkdir -p /data/local/tmp/shikumi-steps"), log.index(gradle))
        self.assertTrue(any(l.startswith("adb pull /data/local/tmp/shikumi-steps/.") for l in log), log)
        steps = (Path(__file__).resolve().parent.parent / "sandbox/android/steps/src/shikumi/steps/StepListener.java").read_text()
        self.assertIn("package shikumi.steps;", steps)
        self.assertIn("public final class StepListener extends RunListener", steps)

    def test_the_whole_suite_runs_when_no_file_is_chosen_and_none_usable_is_exit_2(self):
        code, out, log = self.run_steps("")
        self.assertEqual(code, 0, out)
        self.assertIn(f"gradlew --no-daemon --console=plain --init-script {self.tmp}/shikumi-steps.gradle connectedDebugAndroidTest", log)
        self.assertIn("if ('')", (self.tmp / "shikumi-steps.gradle").read_text())
        self.log.unlink()
        code, out, log = self.run_steps("app/src/test/java/com/example/demo/UnitTest.java web/x.spec.ts")
        self.assertEqual((code, [l for l in log if not l.startswith("avdmanager")]), (2, []), out)
        self.assertEqual(self.run_steps(mode="unit")[0], 2)                             # steps need the emulator
        self.assertEqual(self.run_steps(STEPS_SRC=str(self.tmp / "missing"))[0], 2)      # an image without the recorder

    def test_what_the_recorder_wrote_becomes_what_the_worker_reads(self):
        tab = "\t"
        name = "logintest-signin-s02-0a1b2c"
        self.device(f"{name}.png")
        self.device(f"zz-{name}-f1.png")
        self.device("Bad Name.png")
        self.device("notes.txt", b"x")
        motion = '{"act":"fill","x":360,"y":420,"box":[110,400,180,40],"field":[96,390,540,92]}'
        self.device("steps.tsv", f"{name}.png{tab}2{tab}Fill Email{tab}{motion}\n".encode())
        self.device("shots.tsv", f"{name}.png{tab}com.example.demo.LoginTest{tab}signIn\nx.png{tab}com.example.Gone{tab}t\n".encode())
        self.device("results.tsv", f"com.example.demo.LoginTest{tab}signIn{tab}pass{tab}\ncom.example.home.HomeTest{tab}opens{tab}fail{tab}"
                                   f"expected <a & \"b\">\ncom.example.Gone{tab}t{tab}pass{tab}\n".encode())
        code, out, _ = self.run_steps("", STEP_PREFIX="android/", GRADLE_CODE="1")
        self.assertEqual(code, 1, out)                                     # a failing test fails the run, its steps are still kept
        self.assertEqual(sorted(f.name for f in (self.proj / "screens-out").iterdir()), [f"{name}.png", f"zz-{name}-f1.png"])
        self.assertIn("1 step screenshot(s) and 1 in-between frame(s) kept", out)
        got = self.proj / ".factory-steps"
        self.assertEqual(W.collect_steps(got), {name: {"n": 2, "label": "Fill Email", "motion": json.loads(motion)}})
        login = "android/app/src/androidTest/java/com/example/demo/LoginTest.java::signIn"
        home = "android/feature/home/src/androidTestDebug/kotlin/com/example/home/HomeTest.kt::opens"
        repo = self.tmp / "repo"
        (repo / "android").mkdir(parents=True)
        shutil.copytree(self.proj / "app", repo / "android" / "app")
        shutil.copytree(self.proj / "feature", repo / "android" / "feature")
        self.assertEqual(W.junit_tests((got / "junit.xml").read_text(), repo),
                         [(login, "pass", ""), (home, "fail", 'expected <a & "b">')])
        self.assertEqual([W.suite_shot_test(l, repo) for l in (got / "e2e-shots.jsonl").read_text().splitlines()], [(f"{name}.png", login)])
        self.assertEqual(W.collect_shot_tests(got, repo, {login, home}), {name: login})
        tests, _ = W.collect_results(got, repo)
        self.assertEqual({t["id"]: t["outcome"] for t in tests}, {login: "pass", home: "fail"})

    def test_a_link_on_the_device_side_is_never_followed(self):
        (self.tmp / "secret.png").write_bytes(b"SECRET")
        (self.pulled / "x-s01-abc.png").symlink_to(self.tmp / "secret.png")
        code, out, _ = self.run_steps()
        self.assertEqual(list((self.proj / "screens-out").iterdir()), [])


class ImageDefinition(unittest.TestCase):
    def test_dockerfile_pins_verifies_and_stays_small_in_privilege(self):
        d = DOCKERFILE.read_text()
        self.assertRegex(d, r"FROM \S+:\d+\.\d+")                              # a pinned base image, not :latest
        self.assertIn("CMDLINE_TOOLS_SHA256", d)
        self.assertIn("sha256sum -c", d)                                         # the download is verified
        self.assertIn('test -n "$CMDLINE_TOOLS_SHA256"', d)                      # and the build fails without a checksum
        self.assertNotIn("curl | sh", d.replace("curl -fsSL -o", ""))
        self.assertIn("ENTRYPOINT", d)
        self.assertIn("COPY steps/src /opt/shikumi-steps/src", d)              # the step recorder of a demo run, as source
        self.assertTrue(os.access(INNER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
