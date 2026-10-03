"""The worker install path, with the real scripts: install-worker.sh -> setup-worker.sh -> worker.py --check, update-worker.sh (with
rollback), the factory-side setup.sh worker section, ctl doctor, and the ping endpoint. A real worker API server stands in for the factory."""
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "worker"))
import worker as W                                                       # noqa: E402
from factory import ctl, workerapi                                       # noqa: E402
from factory import db as dbm                                            # noqa: E402
from test_workerapi import TOKEN, cfg_for                                # noqa: E402


def sh(cmd, cwd=None, env=None, timeout=120):
    e = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


class Server(unittest.TestCase):
    """A live worker API (the 'factory') with TOKEN registered."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "factory").mkdir()
        self.cfg = cfg_for(self.tmp / "factory")
        dbm.connect(self.cfg.db_path).close()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), workerapi.make_handler(workerapi.Api(lambda: self.cfg)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


class Ping(Server):
    def test_ping_proves_the_token_and_claims_nothing(self):
        api = W.Api(W.Config({"server": self.url, "token_file": self.write_token(TOKEN), "platform": "linux", "recipes": {}}))
        self.assertEqual(api.call("/v1/ping", {}), (200, {"ok": True, "protocol": 1, "worker": "mac"}))
        bad = W.Api(W.Config({"server": self.url, "token_file": self.write_token("x" * 32), "platform": "linux", "recipes": {}}))
        self.assertEqual(bad.call("/v1/ping", {})[0], 401)

    def write_token(self, t):
        p = self.tmp / "tok"
        p.write_text(t)
        return str(p)

    def check(self, token=TOKEN, url=None, recipes=None):
        recipes = recipes if recipes is not None else {"t": {"command": ["/bin/sh"]}}
        cfg = W.Config({"server": url or self.url, "token_file": self.write_token(token), "platform": "linux", "recipes": recipes})
        return W.check(cfg, W.Api(cfg))

    def test_check_passes_when_everything_is_right(self):
        out = self.check()
        self.assertFalse([m for lvl, m in out if lvl == "FAIL"], out)
        self.assertTrue(any("accepted the token" in m for _, m in out))

    def test_check_names_each_problem(self):
        self.assertTrue(any(l == "FAIL" and "refused the token" in m for l, m in self.check(token="y" * 32)))
        self.assertTrue(any(l == "FAIL" and "cannot reach" in m for l, m in self.check(url="http://127.0.0.1:9")))
        self.assertTrue(any(l == "FAIL" and "not an executable" in m for l, m in self.check(recipes={"t": {"command": ["/nope/x.sh"]}})))
        self.assertTrue(any(l == "FAIL" and "none configured" in m for l, m in self.check(recipes={})))

    def test_check_flag_exit_codes(self):
        (self.tmp / "tok").write_text(TOKEN)
        toml = lambda url: f'server = "{url}"\ntoken_file = "{self.tmp}/tok"\nplatform = "linux"\n[recipes.t]\ncommand = ["/bin/sh"]\n'
        (self.tmp / "ok.toml").write_text(toml(self.url))
        (self.tmp / "bad.toml").write_text(toml("http://127.0.0.1:9"))
        self.assertEqual(sh([sys.executable, str(ROOT / "worker/worker.py"), "--config", str(self.tmp / "ok.toml"), "--check"])[0], 0)
        self.assertEqual(sh([sys.executable, str(ROOT / "worker/worker.py"), "--config", str(self.tmp / "bad.toml"), "--check"])[0], 1)


class Doctor(unittest.TestCase):
    def test_worker_lines(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = cfg_for(tmp)
        db = dbm.connect(cfg.db_path)
        self.addCleanup(db.close)
        ok = lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", "")
        lines = lambda: [(l, m) for l, m in ctl.doctor(cfg, None, run=ok) if "worker" in m.lower()]
        out = lines()
        self.assertIn(("ok", f"1 worker token(s) in {cfg.workers.tokens_file}"), out)
        self.assertTrue(any(l == "warn" and "no worker has connected" in m or l == "warn" and "no worker has polled" in m for l, m in out), out)
        db.execute("INSERT OR REPLACE INTO workers VALUES ('mac','macos','ios-test',?,1)", (time.time(),))
        db.commit()
        self.assertTrue(any(l == "ok" and "polled" in m for l, m in lines()), lines())
        (tmp / "tokens").write_text("")
        self.assertTrue(any(l == "FAIL" and "no worker token" in m for l, m in lines()))


class Release:
    """Builds a fake release folder (what install-worker.sh and update-worker.sh download) with file:// URLs."""
    def __init__(self, tmp: Path):
        self.tmp = tmp

    def make(self, tag: str, mutate=None) -> str:
        d = self.tmp / f"release-{tag}"
        src = self.tmp / f"src-{tag}"
        (src / "sandbox").mkdir(parents=True)
        shutil.copytree(ROOT / "worker", src / "worker", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "sandbox" / "android", src / "sandbox" / "android")
        if mutate:
            mutate(src)
        d.mkdir()
        with tarfile.open(d / "shikumi-worker.tar.gz", "w:gz") as t:
            t.add(src / "worker", "worker")
            t.add(src / "sandbox" / "android", "sandbox/android")
        (d / "VERSION").write_text(tag.lstrip("v") + "\n")
        for s in ("install-worker.sh", "setup-worker.sh", "update-worker.sh"):
            shutil.copy(ROOT / "scripts" / s, d / s)
        return d.as_uri()


class InstallBase(Server):
    ENV = {"SKIP_SERVICE": "1", "SKIP_IMAGE": "1", "SETTLE_SECONDS": "0"}

    def install(self, base: str, **env):
        e = {**self.ENV, "SHIKUMI_ASSET_BASE": base, "SHIKUMI_WORKER_DIR": str(self.tmp / "inst"), "FACTORY_URL": self.url, "WORKER_TOKEN": TOKEN, **env}
        return sh(["bash", str(ROOT / "scripts/install-worker.sh")], cwd=self.tmp, env=e)

    def setUp(self):
        super().setUp()
        self.rel = Release(self.tmp)
        self.inst = self.tmp / "inst"


class Install(InstallBase):
    def test_install_creates_a_working_worker(self):
        code, out = self.install(self.rel.make("v0.2.0"))
        self.assertEqual(code, 0, out)
        self.assertIn("accepted the token", out)
        self.assertEqual((self.inst / "VERSION").read_text().strip(), "0.2.0")
        self.assertTrue((self.inst / "worker" / "worker.py").is_file())
        for s in ("setup-worker.sh", "update-worker.sh"):
            self.assertTrue(os.access(self.inst / "scripts" / s, os.X_OK), s)
        self.assertTrue(os.access(self.inst / "worker" / "recipes" / "web-test.sh", os.X_OK))
        self.assertEqual(stat.S_IMODE((self.inst / "secrets" / "token").stat().st_mode), 0o600)
        self.assertEqual((self.inst / "secrets" / "token").read_text(), TOKEN)
        cfg = W.load_config(str(self.inst / "worker.toml"))
        self.assertEqual((cfg.server, cfg.platform, sorted(cfg.recipes)), (self.url, "macos" if sys.platform == "darwin" else "linux", ["web-test"]))
        self.assertEqual(cfg.recipes["web-test"].command, [str(self.inst / "worker/recipes/web-test.sh")])
        self.assertEqual(stat.S_IMODE((self.inst / "worker.toml").stat().st_mode), 0o600)

    def test_service_file_is_filled_in_and_holds_no_secret(self):
        self.install(self.rel.make("v0.2.0"))
        unit = next((self.inst / "service").iterdir())
        text = unit.read_text()
        self.assertIn(str(self.inst), text)
        self.assertIn("worker/worker.py", text)
        self.assertIn("worker.toml", text)
        self.assertNotIn("@DIR@", text)
        self.assertNotIn("@PYTHON@", text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(unit.name, "com.shikumi.worker.plist" if sys.platform == "darwin" else "shikumi-worker.service")

    def test_install_refuses_to_overwrite_and_setup_keeps_existing_files(self):
        base = self.rel.make("v0.2.0")
        self.assertEqual(self.install(base)[0], 0)
        code, out = self.install(base)
        self.assertEqual(code, 1)
        self.assertIn("update-worker.sh", out)
        (self.inst / "worker.toml").write_text((self.inst / "worker.toml").read_text() + "# my edit\n")
        code, out = sh(["bash", str(self.inst / "scripts/setup-worker.sh")], cwd=self.inst, env={**self.ENV, "FACTORY_URL": self.url})
        self.assertEqual(code, 0, out)
        self.assertIn("keeping it", out)
        self.assertIn("# my edit", (self.inst / "worker.toml").read_text())
        self.assertEqual((self.inst / "secrets" / "token").read_text(), TOKEN)

    def test_bad_input_is_refused_before_anything_is_written(self):
        base = self.rel.make("v0.2.0")
        for env in ({"FACTORY_URL": "ftp://x"}, {"FACTORY_URL": "not a url"}, {"WORKER_TOKEN": "short"}, {"WORKER_TOKEN": "has spaces " + "x" * 30},
                    {"WORKER_RECIPES": "ios"}, {"WORKER_RECIPES": "web; rm -rf /"}, {"WORKER_PLATFORM": "Mac OS"}):
            shutil.rmtree(self.inst, ignore_errors=True)
            code, out = self.install(base, **env)
            self.assertEqual(code, 1, env)
            self.assertFalse((self.inst / "worker.toml").exists(), env)

    def test_android_recipe_uses_the_existing_image_or_builds_one(self):
        log = self.tmp / "docker.log"
        bin = self.tmp / "bin"
        bin.mkdir()
        stub = bin / "docker"
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\ncase "$1 $2" in "image inspect") exit "${{HAVE_IMAGE:-1}}";; "pull "*) exit 1;; esac\nexit 0\n')
        stub.chmod(0o755)
        env = {"PATH": f"{bin}:{os.environ['PATH']}", "SKIP_IMAGE": ""}
        base = self.rel.make("v0.2.0")
        code, out = self.install(base, WORKER_RECIPES="web android", **env)
        calls = log.read_text()
        self.assertIn("pull ghcr.io/theninjadojo/shikumi-android:v0.2.0", calls)
        self.assertIn("build -t factory-android", calls)                          # no prebuilt image: it builds from sandbox/android
        self.assertEqual(sorted(W.load_config(str(self.inst / "worker.toml")).recipes), ["android-test", "web-test"])
        log.unlink()
        shutil.rmtree(self.inst, ignore_errors=True)
        code, out = self.install(base, WORKER_RECIPES="android", HAVE_IMAGE="0", **env)
        self.assertNotIn("build", log.read_text())                                # the image exists: left alone
        self.assertIn("keeping it", out)

    def test_update_replaces_code_keeps_config_and_verifies(self):
        self.install(self.rel.make("v0.2.0"))
        toml_before = (self.inst / "worker.toml").read_text()
        new = self.rel.make("v0.2.1", lambda src: (src / "worker" / "worker.py").write_text((src / "worker" / "worker.py").read_text() + "\n# v0.2.1\n"))
        code, out = sh(["bash", str(self.inst / "scripts/update-worker.sh"), "v0.2.1"], cwd=self.inst, env={**self.ENV, "SHIKUMI_ASSET_BASE": new})
        self.assertEqual(code, 0, out)
        self.assertIn("# v0.2.1", (self.inst / "worker" / "worker.py").read_text())
        self.assertEqual((self.inst / "VERSION").read_text().strip(), "0.2.1")
        self.assertEqual((self.inst / "worker.toml").read_text(), toml_before)
        self.assertEqual((self.inst / "secrets" / "token").read_text(), TOKEN)
        self.assertTrue((self.inst / ".shikumi-worker-backup" / "worker" / "worker.py").is_file())

    def test_a_broken_update_rolls_back(self):
        self.install(self.rel.make("v0.2.0"))
        good = (self.inst / "worker" / "worker.py").read_text()
        bad = self.rel.make("v0.2.1", lambda src: (src / "worker" / "worker.py").write_text("import sys\nsys.exit('this version is broken')\n"))
        code, out = sh(["bash", str(self.inst / "scripts/update-worker.sh"), "v0.2.1"], cwd=self.inst, env={**self.ENV, "SHIKUMI_ASSET_BASE": bad})
        self.assertEqual(code, 1, out)
        self.assertIn("rolling back to v0.2.0", out)
        self.assertEqual((self.inst / "worker" / "worker.py").read_text(), good)
        self.assertEqual((self.inst / "VERSION").read_text().strip(), "0.2.0")

    def test_update_refuses_while_a_job_runs_and_on_bad_input(self):
        self.install(self.rel.make("v0.2.0"))
        new = self.rel.make("v0.2.1")
        run = lambda *a, **env: sh(["bash", str(self.inst / "scripts/update-worker.sh"), *a], cwd=self.inst, env={**self.ENV, "SHIKUMI_ASSET_BASE": new, **env})
        (self.inst / "work" / "factory-job-abc").mkdir()
        code, out = run("v0.2.1")
        self.assertEqual(code, 1)
        self.assertIn("A job is running", out)
        self.assertEqual((self.inst / "VERSION").read_text().strip(), "0.2.0")
        self.assertEqual(run("v0.2.1", FORCE="1")[0], 0)
        self.assertIn("Already on", run("v0.2.1")[1])
        self.assertEqual(run("latest-ish")[0], 1)
        self.assertEqual(run("v9.9.9")[0], 1)                                        # no such release files

    def test_update_refuses_files_that_do_not_match_the_tag(self):
        self.install(self.rel.make("v0.2.0"))
        wrong = self.rel.make("v0.2.5")
        code, out = sh(["bash", str(self.inst / "scripts/update-worker.sh"), "v0.2.1"], cwd=self.inst, env={**self.ENV, "SHIKUMI_ASSET_BASE": wrong})
        self.assertEqual(code, 1)
        self.assertEqual((self.inst / "VERSION").read_text().strip(), "0.2.0")


@unittest.skipUnless(os.getuid() == 1000, "setup.sh chowns to uid 1000 (it uses sudo for any other user)")
class FactorySetup(unittest.TestCase):
    """scripts/setup.sh with a stub docker: the worker section writes config and .env, makes a token, and starts the stack."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app, self.home, self.bin, self.log = self.tmp / "app", self.tmp / "home", self.tmp / "bin", self.tmp / "docker.log"
        (self.app / "scripts").mkdir(parents=True)
        for f in ("config.example.toml", ".env.example", "VERSION"):
            shutil.copy(ROOT / f, self.app / f)
        shutil.copy(ROOT / "scripts" / "setup.sh", self.app / "scripts" / "setup.sh")
        self.bin.mkdir()
        (self.bin / "docker").write_text(f'#!/bin/sh\necho "$@" >> "{self.log}"\nexit 0\n')
        (self.bin / "docker").chmod(0o755)

    def run_setup(self, **env):
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FACTORY_HOME": str(self.home), "GITHUB_TOKEN": "t" * 30, "ANTHROPIC_API_KEY": "k" * 20,
             "FACTORY_REPOS": "o/web o/ios", "FACTORY_UI_PASSWORD": "pw", "SKIP_BUILD": "1", "SKIP_LABELS": "1", **env}
        return sh(["bash", str(self.app / "scripts" / "setup.sh")], cwd=self.app, env=e)

    def test_workers_are_off_unless_asked(self):
        code, out = self.run_setup()
        self.assertEqual(code, 0, out)
        self.assertNotIn("workers", tomllib.loads((self.app / "config" / "config.toml").read_text()))      # (the example's commented [workers] is not a table)
        self.assertNotIn("COMPOSE_PROFILES", (self.app / ".env").read_text())
        self.assertNotIn("workers add", self.log.read_text())

    def test_worker_section(self):
        code, out = self.run_setup(FACTORY_WORKERS="1", FACTORY_WORKER_NAME="my-mac", FACTORY_WORKER_CHECKS="o/ios:ios-test:macos o/web:web-test:any")
        self.assertEqual(code, 0, out)
        raw = tomllib.loads((self.app / "config" / "config.toml").read_text())
        self.assertEqual(raw["workers"]["enabled"], True)
        self.assertEqual(raw["workers"]["tokens_file"], f"{self.home}/secrets/worker_tokens")
        self.assertEqual([(c["repo"], c["recipe"], c["platform"]) for c in raw["workers"]["checks"]], [("o/ios", "ios-test", "macos"), ("o/web", "web-test", "any")])
        from factory.config import load
        cfg = load(str(self.app / "config" / "config.toml"))                         # the whole file still validates
        self.assertEqual((cfg.workers.enabled, len(cfg.workers.checks)), (True, 2))
        self.assertIn("COMPOSE_PROFILES=workers", (self.app / ".env").read_text())
        calls = self.log.read_text()
        self.assertIn("ctl workers add my-mac", calls)
        self.assertLess(calls.index("workers add"), calls.index("up -d"))           # the token exists before the stack starts
        self.assertIn("install-worker.sh", out)

    def test_rerun_keeps_what_is_there_and_does_not_duplicate_the_profile(self):
        self.run_setup(FACTORY_WORKERS="1")
        before = (self.app / "config" / "config.toml").read_text()
        code, out = self.run_setup(FACTORY_WORKERS="1", FACTORY_WORKER_CHECKS="o/ios:ios-test:macos")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.app / "config" / "config.toml").read_text(), before)
        self.assertEqual((self.app / ".env").read_text().count("COMPOSE_PROFILES"), 1)

    def test_an_existing_profile_list_is_extended(self):
        self.run_setup()
        env = self.app / ".env"
        env.write_text(env.read_text() + "COMPOSE_PROFILES=build\n")
        self.run_setup(FACTORY_WORKERS="1")
        self.assertIn("COMPOSE_PROFILES=build,workers", env.read_text())

    def test_bad_worker_input_stops_setup(self):
        for env in ({"FACTORY_WORKER_NAME": "Bad Name"}, {"FACTORY_WORKER_CHECKS": "o/ios:ios-test"}, {"FACTORY_WORKER_CHECKS": "o/ios:Bad:macos"},
                    {"FACTORY_WORKER_CHECKS": "o/ios:ios-test:macos;rm"}):
            shutil.rmtree(self.app / "config", ignore_errors=True)
            code, out = self.run_setup(FACTORY_WORKERS="1", **env)
            self.assertEqual(code, 1, env)
            cfg_file = self.app / "config" / "config.toml"
            self.assertNotIn("workers", tomllib.loads(cfg_file.read_text()) if cfg_file.exists() else {}, env)


class ReleaseWiring(unittest.TestCase):
    def test_workflow_publishes_the_worker_files_and_image(self):
        wf = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        for needle in ("shikumi-android sandbox/android/Dockerfile", "install-worker.sh", "setup-worker.sh", "update-worker.sh", "shikumi-worker.tar.gz"):
            self.assertIn(needle, wf)
        self.assertIn("worker sandbox/android", wf)                                 # the archive holds exactly what the scripts unpack

    def test_service_templates_have_every_placeholder_the_setup_script_fills(self):
        for f, needed in (("shikumi-worker.service", ("@DIR@", "@PYTHON@")), ("com.shikumi.worker.plist", ("@DIR@", "@PYTHON@", "@PATH@"))):
            text = (ROOT / "worker" / "service" / f).read_text()
            for n in needed:
                self.assertIn(n, text, (f, n))


if __name__ == "__main__":
    unittest.main()


class IosSetup(InstallBase):
    """setup-worker.sh with the ios recipe, using a stub tart."""
    def stub_tart(self, have_image=False):
        self.bin = self.tmp / "bin"
        self.bin.mkdir(exist_ok=True)
        self.log = self.tmp / "tart.log"
        t = self.bin / "tart"
        t.write_text(f'#!/bin/sh\necho "$@" >> "{self.log}"\n[ "$1" = list ] && {{ echo "Source Name Disk Size State"; {"echo local shikumi-ios 50 50 stopped;" if have_image else ""} }}\nexit 0\n')
        t.chmod(0o755)
        return {"PATH": f"{self.bin}:{os.environ['PATH']}", "SKIP_IMAGE": ""}

    IOS = {"WORKER_RECIPES": "ios", "WORKER_IOS_SCHEME": "App", "WORKER_IOS_PROJECT": "App.xcodeproj"}

    def test_ios_recipe_is_written_with_its_arguments(self):
        env = {**self.stub_tart(), **self.IOS}
        code, out = self.install(self.rel.make("v0.2.0"), **env)
        self.assertEqual(code, 0, out)
        cmd = W.load_config(str(self.inst / "worker.toml")).recipes["ios-test"].command
        self.assertEqual(cmd, [str(self.inst / "worker/recipes/ios-test.sh"), "--scheme", "App", "--project", "App.xcodeproj",
                               "--destination", "platform=iOS Simulator,name=iPhone 16"])

    def test_workspace_and_destination(self):
        env = {**self.stub_tart(), **self.IOS, "WORKER_IOS_PROJECT": "", "WORKER_IOS_WORKSPACE": "App.xcworkspace", "WORKER_IOS_DESTINATION": "platform=iOS Simulator,name=iPhone 15 Pro"}
        self.assertEqual(self.install(self.rel.make("v0.2.0"), **env)[0], 0)
        cmd = W.load_config(str(self.inst / "worker.toml")).recipes["ios-test"].command
        self.assertEqual(cmd[1:], ["--scheme", "App", "--workspace", "App.xcworkspace", "--destination", "platform=iOS Simulator,name=iPhone 15 Pro"])

    def test_the_golden_image_is_never_downloaded_unless_asked(self):
        base = self.rel.make("v0.2.0")
        code, out = self.install(base, **{**self.stub_tart(), **self.IOS})
        self.assertEqual(code, 0, out)
        self.assertNotIn("clone", self.log.read_text())
        self.assertIn("No VM named shikumi-ios yet", out)
        shutil.rmtree(self.inst)
        self.log.unlink()
        self.install(base, **{**self.stub_tart(), **self.IOS, "WORKER_IOS_PULL": "1"})
        self.assertIn("clone ghcr.io/cirruslabs/macos-sonoma-xcode:latest shikumi-ios", self.log.read_text())
        shutil.rmtree(self.inst)
        self.log.unlink()
        code, out = self.install(base, **{**self.stub_tart(have_image=True), **self.IOS, "WORKER_IOS_PULL": "1"})
        self.assertNotIn("clone", self.log.read_text())                       # already there: left alone
        self.assertIn("keeping it", out)

    def test_ios_input_is_checked_before_anything_is_written(self):
        base = self.rel.make("v0.2.0")
        for extra in ({"WORKER_IOS_SCHEME": ""}, {"WORKER_IOS_SCHEME": "A b"}, {"WORKER_IOS_SCHEME": "A;rm"}, {"WORKER_IOS_PROJECT": "", "WORKER_IOS_WORKSPACE": ""},
                      {"WORKER_IOS_WORKSPACE": "B.xcworkspace"}, {"WORKER_IOS_PROJECT": "../A.xcodeproj"}, {"WORKER_IOS_PROJECT": "/abs/A.xcodeproj"},
                      {"WORKER_IOS_DESTINATION": "x'; rm -rf /"}, {"WORKER_IOS_DESTINATION": "$(id)"}):
            shutil.rmtree(self.inst, ignore_errors=True)
            code, out = self.install(base, **{**self.stub_tart(), **self.IOS, **extra})
            self.assertEqual(code, 1, extra)
            self.assertFalse((self.inst / "worker.toml").exists(), extra)

    def test_ios_without_tart_is_refused(self):
        code, out = self.install(self.rel.make("v0.2.0"), **self.IOS)
        self.assertEqual(code, 1)
        self.assertIn("needs Tart", out)
