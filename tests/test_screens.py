import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from factory import config, screens
from factory.config import RunnerCfg, ScreenPage, ScreensCfg
from test_render import png

SC = ScreensCfg(pages=(ScreenPage("o/r", "home", "site/index.html", ("desktop", "mobile")),))
IDS = ["home-desktop", "home-mobile"]


def mount(cmd, target):
    return Path(next(v.split(":")[0] for v in cmd if v.endswith(f":{target}:rw") or v.endswith(f":{target}:ro")))


class Fake:
    """Stands in for the two containers. shot: id -> PNG the renderer 'produced'; diffs: id -> differing pixels it 'reports'."""
    def __init__(self, shot, diffs=None, verdict=None, code=0):
        self.shot, self.diffs, self.verdict, self.code, self.cmds = shot, diffs or {}, verdict, code, []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[0] == "chmod":
            return subprocess.CompletedProcess(cmd, 0)
        if "/app/shoot.js" in cmd:
            for i, data in self.shot.items():
                (mount(cmd, "/out") / f"{i}.png").write_bytes(data)
        elif "/app/compare.js" in cmd:
            out = mount(cmd, "/out")
            v = self.verdict if self.verdict is not None else {"results": [{"id": i, "diff_pixels": self.diffs.get(i, 0)} for i in IDS]}
            (out / "verdict.json").write_text(v if isinstance(v, str) else json.dumps(v))
            for i, n in self.diffs.items():
                (out / f"{i}-diff.png").write_bytes(png(20, 20))
        return subprocess.CompletedProcess(cmd, self.code, "", "boom")


class Verify(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.work = Path(self._t.name) / "work"
        self.src = Path(self._t.name) / "repo"
        (self.src / SC.baseline_dir).mkdir(parents=True)
        (self.src / ".git").mkdir()
        (self.src / ".git" / "config").write_text("secret")
        for i in IDS:
            (self.src / SC.baseline_dir / f"{i}.png").write_bytes(png(20, 20))
        self.rn = RunnerCfg(work_dir=str(self.work))

    def check(self, fake, sc=SC):
        return screens.verify(self.rn, sc, "o/r", self.src, fake)

    def test_matching_screens_pass(self):
        rep = self.check(Fake({i: png(20, 20) for i in IDS}))
        self.assertTrue(rep.ok, rep.text())
        self.assertEqual(len(rep.lines), 2)

    def test_a_repo_without_screens_is_untouched(self):
        fake = Fake({})
        self.assertTrue(screens.verify(self.rn, SC, "o/other", self.src, fake).ok)
        self.assertEqual(fake.cmds, [])

    def test_a_difference_over_the_limit_fails_and_names_the_screen(self):
        rep = self.check(Fake({i: png(20, 20) for i in IDS}, {"home-mobile": 5}))      # 5 of 400 px = 1.25 % > 0.1 %
        self.assertFalse(rep.ok)
        self.assertIn("home-mobile", rep.text())
        self.assertNotIn("home-desktop: 1", rep.text())
        self.assertIn("home-mobile", rep.diffs)

    def test_a_difference_within_the_limit_passes(self):
        sc = replace(SC, max_diff_ratio=0.05)
        self.assertTrue(self.check(Fake({i: png(20, 20) for i in IDS}, {"home-mobile": 5}), sc).ok)

    def test_the_verdict_is_judged_here_not_taken_from_the_container(self):
        fake = Fake({i: png(20, 20) for i in IDS}, verdict={"pass": True, "results": [{"id": i, "diff_pixels": 400} for i in IDS]})
        self.assertFalse(self.check(fake).ok)

    def test_a_bad_verdict_fails(self):
        shot = {i: png(20, 20) for i in IDS}
        for v in ("not json", {"results": []}, {"results": [{"id": "home-desktop", "diff_pixels": -1}, {"id": "home-mobile", "diff_pixels": 0}]},
                  {"results": [{"id": "home-desktop", "diff_pixels": 0}, {"id": "other", "diff_pixels": 0}]},
                  {"results": [{"id": i, "diff_pixels": True} for i in IDS]},
                  {"results": [{"id": i, "diff_pixels": 10 ** 9} for i in IDS]}):
            self.assertFalse(self.check(Fake(shot, verdict=v)).ok, v)

    def test_an_unusable_screenshot_fails(self):
        for bad in (b"<html>" * 20, png(20, 20, b"\0" * 3_000_000)):
            rep = self.check(Fake({"home-desktop": bad, "home-mobile": png(20, 20)}))
            self.assertFalse(rep.ok)
            self.assertIn("home-desktop", rep.text())

    def test_a_missing_screenshot_fails(self):
        self.assertFalse(self.check(Fake({"home-desktop": png(20, 20)})).ok)

    def test_a_missing_or_symlinked_baseline_fails(self):
        (self.src / SC.baseline_dir / "home-mobile.png").unlink()
        rep = self.check(Fake({i: png(20, 20) for i in IDS}))
        self.assertFalse(rep.ok)
        self.assertIn("no baseline", rep.text())
        (self.src / SC.baseline_dir / "home-mobile.png").symlink_to(self.src / SC.baseline_dir / "home-desktop.png")
        self.assertFalse(self.check(Fake({i: png(20, 20) for i in IDS})).ok)

    def test_a_size_change_fails_without_comparing(self):
        fake = Fake({"home-desktop": png(20, 30), "home-mobile": png(20, 20)})
        rep = self.check(fake)
        self.assertFalse(rep.ok)
        self.assertIn("size changed", rep.text())

    def test_an_unavailable_renderer_fails_closed(self):
        class Raises(Fake):
            def __init__(self, exc):
                super().__init__({})
                self.exc = exc

            def __call__(self, cmd, **kw):
                if cmd[0] == "chmod":
                    return super().__call__(cmd, **kw)
                raise self.exc
        for fake in (Fake({}, code=125), Raises(OSError("no podman")), Raises(subprocess.TimeoutExpired("x", 1))):
            rep = self.check(fake)
            self.assertFalse(rep.ok)
            self.assertIn("unavailable", rep.text())

    def test_both_containers_are_sealed_and_the_page_never_meets_the_comparison(self):
        fake = Fake({i: png(20, 20) for i in IDS})
        self.check(fake)
        runs = [c for c in fake.cmds if c[0] == "podman"]
        self.assertEqual(len(runs), 2)
        for cmd in runs:
            joined = " ".join(cmd)
            for flag in ("--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges"):
                self.assertIn(flag, joined)
            for never in ("--env-file", "proxy.sock", "-e ", "--privileged", "--network=host", "claude.env"):
                self.assertNotIn(never, joined)
            self.assertTrue(cmd[cmd.index("--name") + 1].startswith("factory-screens-"))
        shoot, compare = runs
        self.assertIn(":/in:ro", " ".join(shoot))
        self.assertNotIn(":/base:", " ".join(shoot))
        self.assertNotIn(":/in:", " ".join(compare))      # the repo (page code) is not mounted in the comparison

    def test_the_repo_copy_has_no_git_dir(self):
        seen = []

        class Peek(Fake):
            def __call__(self, cmd, **kw):
                if "/app/shoot.js" in cmd:
                    seen.append((mount(cmd, "/in") / ".git").exists())
                return super().__call__(cmd, **kw)
        self.check(Peek({i: png(20, 20) for i in IDS}))
        self.assertEqual(seen, [False])

    def test_the_workspace_is_removed(self):
        self.check(Fake({i: png(20, 20) for i in IDS}))
        self.assertEqual(list(self.work.iterdir()), [])


class Touched(unittest.TestCase):
    def test_baseline_changes_are_detected(self):
        self.assertTrue(screens.baselines_touched(["a.py", "screens/baselines/home-desktop.png"], SC))
        self.assertFalse(screens.baselines_touched(["a.py", "screens/baselines-old/x.png", "x/screens/baselines/y.png"], SC))


class Parse(unittest.TestCase):
    def cfg(self, **screens_raw):
        raw = {"general": {"db_path": "x", "poll_seconds": 1, "dry_run": True, "confidence_threshold": 0.6},
               "github": {"repos": ["o/r"], "trigger_label": "t", "trusted_permissions": ["write"]},
               "routing": {k: {"harness": "claude-code", "model": "sonnet", "effort": "medium"} for k in ("low", "medium", "high")},
               "screens": screens_raw}
        return config.parse(raw)

    def page(self, **kw):
        return {"repo": "o/r", "name": "home", "path": "site/index.html", **kw}

    def test_defaults_and_a_valid_page(self):
        self.assertEqual(self.cfg().screens.pages, ())
        c = self.cfg(pages=[self.page(viewports=["mobile"], mask=[".clock"])]).screens
        self.assertEqual([(v.name, v.width, v.height) for v in c.viewports], [("desktop", 1440, 900), ("mobile", 390, 844)])
        self.assertEqual(c.pages[0].viewports, ("mobile",))
        self.assertEqual((c.threshold, c.max_diff_ratio), (0.1, 0.001))

    def test_bad_values_are_refused(self):
        bad = [dict(pages=[self.page(repo="x/y")]), dict(pages=[self.page(name="Home!")]), dict(pages=[self.page(), self.page()]),
               dict(pages=[self.page(path="../x.html")]), dict(pages=[self.page(path="/etc/passwd")]),
               dict(pages=[self.page(path=".git/config")]), dict(pages=[self.page(viewports=["tablet"])]),
               dict(pages=[self.page(mask=["x" * 300])]), dict(baseline_dir="../x"), dict(threshold=2), dict(max_diff_ratio=-1),
               dict(timeout_seconds=1), dict(viewports={"d": {"width": 99999, "height": 900}})]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                self.cfg(**b)


if __name__ == "__main__":
    unittest.main()
