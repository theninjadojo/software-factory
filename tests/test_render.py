import struct
import subprocess
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import designfiles, render
from factory.config import RunnerCfg
from test_designfiles import VALID
from test_designer_files import DesignerFlow, TICKET, ROUTE
from factory.runner import run_task


def png(w=20, h=20, extra=b""):
    raw = b"".join(b"\x00" + b"\xff\x00\xff" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"") + extra


class PngChecks(unittest.TestCase):
    def test_only_real_pngs_of_sane_size_are_accepted(self):
        self.assertTrue(render.png_ok(png(1184, 520)))
        for bad in (b"", b"GIF89a" + b"\0" * 60, b"<html>" * 20, png()[:20], png(10, 20), png(20, 10), png(render.MAX_W + 1, 20), png(20, render.MAX_H + 1),
                    png(20, 20, b"\0" * render.MAX_PNG_BYTES), "not bytes", None):
            self.assertFalse(render.png_ok(bad), repr(bad)[:40])


class Command(unittest.TestCase):
    def test_the_container_is_sealed(self):
        for engine in ("podman", "docker"):
            cmd = render.render_cmd(RunnerCfg(engine=engine), "factory-render-ab12", Path("/w/x"))
            joined = " ".join(cmd)
            for flag in ("--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges", "/w/x/in:/in:ro", "/w/x/out:/out:rw"):
                self.assertIn(flag, joined)
            for never in ("--env-file", "proxy.sock", "-e ", "--privileged", "--network=host", "claude.env"):
                self.assertNotIn(never, joined)
            self.assertEqual(cmd[cmd.index("--name") + 1], "factory-render-ab12")             # counted like a run by the deploy guard
            self.assertEqual(cmd[-1], "localhost/factory-render:latest")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.t, True)
        self.rn = RunnerCfg(work_dir=self.t)

    def engine(self, outputs, code=0):
        """A stand-in for subprocess.run: chmods succeed, the 'container' writes `outputs` (name -> bytes) into the mounted out dir."""
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "chmod":
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[1] == "run":
                out = Path(next(a for a in cmd if a.endswith(":/out:rw")).split(":")[0])
                for name, data in outputs.items():
                    (out / name).write_bytes(data)
                return subprocess.CompletedProcess(cmd, code, "", "boom")
            return subprocess.CompletedProcess(cmd, 0)
        run.calls = calls
        return run

    def test_good_previews_come_back_and_bad_ones_are_dropped(self):
        run = self.engine({"a.png": png(100, 50), "b.png": b"<html>pwned</html>", "c.png": png(5, 5)})
        got = render.render(self.rn, {"a": VALID, "b": VALID, "c": VALID, "d": VALID}, run)
        self.assertEqual(list(got), ["a"])                                  # b is not a PNG, c is too small, d was never written
        engine_call = next(c for c in run.calls if c[1] == "run")
        self.assertIn("--network=none", engine_call)
        self.assertEqual(list(Path(self.t).iterdir()), [])                 # the working folder is removed

    def test_a_symlink_in_the_output_is_never_followed(self):
        def run(cmd, **kw):
            if cmd[0] != "chmod" and cmd[1] == "run":
                out = Path(next(a for a in cmd if a.endswith(":/out:rw")).split(":")[0])
                (out / "a.png").symlink_to("/etc/hostname")
            return subprocess.CompletedProcess(cmd, 0)
        self.assertEqual(render.render(self.rn, {"a": VALID}, run), {})

    def test_the_input_is_what_was_given_and_only_that(self):
        seen = {}

        def run(cmd, **kw):
            if cmd[1:2] == ["run"]:
                src = Path(next(a for a in cmd if a.endswith(":/in:ro")).split(":")[0])
                seen.update({p.name: p.read_text() for p in src.iterdir()})
            return subprocess.CompletedProcess(cmd, 0)
        render.render(self.rn, {"factory-3-x": VALID}, run)
        self.assertEqual(seen, {"factory-3-x.html": VALID})

    def test_a_timeout_removes_the_container_and_returns_nothing(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[1:2] == ["run"]:
                raise subprocess.TimeoutExpired(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0)
        self.assertEqual(render.render(self.rn, {"a": VALID}, run), {})
        self.assertTrue(any(c[1:3] == ["rm", "-f"] for c in calls))

    def test_a_missing_engine_or_image_never_stops_publishing(self):
        def boom(cmd, **kw):
            if cmd[0] == "chmod":
                return subprocess.CompletedProcess(cmd, 0)
            raise FileNotFoundError("podman")
        self.assertEqual(render.render(self.rn, {"a": VALID}, boom), {})
        run = self.engine({}, code=125)                                     # image not found: exit 125, no output
        self.assertEqual(render.render(self.rn, {"a": VALID}, run), {})

    def test_off_means_nothing_runs_and_bad_names_are_refused(self):
        run = self.engine({"a.png": png()})
        self.assertEqual(render.render(replace(self.rn, render_previews=False), {"a": VALID}, run), {})
        self.assertEqual(run.calls, [])
        for bad in ("../x", "A", "a b", "a/b", "", "-x"):
            with self.assertRaises(ValueError):
                render.render(self.rn, {bad: VALID}, run)


class Links(unittest.TestCase):
    def test_preview_paths_and_links(self):
        self.assertEqual(designfiles.preview_path("docs/design/factory-3-x.dc.html"), "docs/design/previews/factory-3-x.png")
        with self.assertRaises(designfiles.DesignFileRejected):
            designfiles.preview_path("docs/design/x.png")
        sha = "a" * 40
        ok = {"repo": "o/web", "path": "docs/design/previews/factory-3-x.png", "url": f"https://github.com/o/web/blob/{sha}/docs/design/previews/factory-3-x.png", "pr": ""}
        self.assertTrue(designfiles.link_ok(ok))
        for bad in ({**ok, "url": ok["url"].replace("o/web", "o/other")}, {**ok, "path": "docs/design/previews/factory-3-x.jpg", "url": ok["url"].replace(".png", ".jpg")},
                    {**ok, "path": "docs/design/previews/../x.png", "url": ok["url"].replace("previews/factory-3-x", "previews/../x")},
                    {**ok, "url": "https://evil.example/o/web/blob/" + sha + "/docs/design/previews/factory-3-x.png"}):
            self.assertFalse(designfiles.link_ok(bad), bad["url"])


class Published(DesignerFlow):
    """The designer flow with a stand-in renderer: previews are committed to the draft PR next to the canvas, and linked."""

    def go_with(self, previews, edits):
        t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, edits)
        cfg = replace(cfg, runner=replace(cfg.runner, render_previews=True))
        with p1, p2, mock.patch("factory.runner.preview_render", return_value=previews) as rendered:
            res = run_task(cfg, gh, "o/web", TICKET, ROUTE, role="designer")
        return res, gh, bare, g, t, rendered

    def test_the_preview_lands_on_the_draft_pr_and_is_linked(self):
        res, gh, bare, g, t, rendered = self.go_with({"factory-3-x": png(300, 200)}, lambda w: self.write(w, "factory-3-x.dc.html", VALID))
        self.assertEqual(rendered.call_args.args[1], {"factory-3-x": VALID})            # exactly the validated text, nothing else
        branch = gh.prs[0][1]
        shown = g("--git-dir", str(bare["o/web"]), "ls-tree", "-r", "--name-only", branch, cwd=t).stdout.split()
        self.assertEqual(sorted(shown), ["a.txt", "docs/design/factory-3-x.dc.html", "docs/design/previews/factory-3-x.png"])
        self.assertEqual([f["path"] for f in res.files], ["docs/design/factory-3-x.dc.html", "docs/design/previews/factory-3-x.png"])
        self.assertTrue(res.files[1]["preview"] and all(designfiles.link_ok(f) for f in res.files))
        self.assertIn("preview: `docs/design/previews/factory-3-x.png`", gh.prs[0][2])

    def test_no_render_still_publishes_the_canvas(self):
        res, gh, bare, g, t, _ = self.go_with({}, lambda w: self.write(w, "factory-3-x.dc.html", VALID))
        self.assertEqual([f["path"] for f in res.files], ["docs/design/factory-3-x.dc.html"])
        self.assertEqual(len(gh.prs), 1)

    def test_a_renderer_crash_never_loses_the_design(self):
        t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, lambda w: self.write(w, "factory-3-x.dc.html", VALID))
        cfg = replace(cfg, runner=replace(cfg.runner, render_previews=True))
        with p1, p2, mock.patch("factory.runner.preview_render", side_effect=RuntimeError("chromium exploded")):
            res = run_task(cfg, gh, "o/web", TICKET, ROUTE, role="designer")
        self.assertEqual((res.status, len(gh.prs), len(res.files)), ("stage", 1, 1))

    def test_a_canvas_that_fails_validation_is_never_rendered(self):
        bad = VALID.replace("<h1", "<script>alert(1)</script><h1", 1)
        res, gh, bare, g, t, rendered = self.go_with({"factory-3-x": png()}, lambda w: self.write(w, "factory-3-x.dc.html", bad))
        rendered.assert_not_called()
        self.assertEqual(res.files, [])


if __name__ == "__main__":
    unittest.main()


class CropOnWhite(unittest.TestCase):
    def test_unpainted_areas_become_white_never_the_old_pink(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is only in the render image")
        import importlib.util
        spec = importlib.util.spec_from_file_location("render_container", Path(__file__).resolve().parent.parent / "sandbox" / "render" / "render.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        for x in range(10, 30):
            for y in range(10, 30):
                img.putpixel((x, y), (18, 52, 86, 255))
        img.putpixel((10, 10), (0, 0, 0, 0))                    # a rounded corner
        out = mod.crop_on_white(img)
        self.assertEqual(out.size, (20, 20))
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(out.getpixel((5, 5)), (18, 52, 86))
        self.assertNotIn((255, 0, 255), set(out.getdata()))
        with self.assertRaises(ValueError):
            mod.crop_on_white(Image.new("RGBA", (40, 40), (0, 0, 0, 0)))


class CropOnWhite(unittest.TestCase):
    def test_unpainted_areas_become_white_never_the_old_pink(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is only in the render image")
        import importlib.util
        spec = importlib.util.spec_from_file_location("render_container", Path(__file__).resolve().parent.parent / "sandbox" / "render" / "render.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        for x in range(10, 30):
            for y in range(10, 30):
                img.putpixel((x, y), (18, 52, 86, 255))
        img.putpixel((10, 10), (0, 0, 0, 0))                    # a rounded corner
        out = mod.crop_on_white(img)
        self.assertEqual(out.size, (20, 20))
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(out.getpixel((5, 5)), (18, 52, 86))
        self.assertNotIn((255, 0, 255), set(out.getdata()))
        with self.assertRaises(ValueError):
            mod.crop_on_white(Image.new("RGBA", (40, 40), (0, 0, 0, 0)))
