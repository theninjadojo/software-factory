"""Demo videos: a chat video names Playwright tests, a worker runs them with a screenshot after each step, and the video shows those steps."""
import base64
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from factory import captures as CP, jobs, videos
from factory import factorychat as F
from factory import screenboard as SB
from factory.config import ScreenCapture, ScreensCfg, WorkersCfg, overrides_path

from test_captures import FakeGh
from test_chatvideos import REFS, block
from test_ui import UiCase
from test_videos import Base, FakeGH, png

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                       # noqa: E402

RECIPES = Path(__file__).resolve().parent.parent / "worker" / "recipes"
REPO = "o/r"
TEST = "web/e2e/auth.spec.ts › signing in"


def step_body(status="passed", outcome="pass", steps=(("Navigate /login", 1), ("Fill getByPlaceholder('Email')", 2),
                                                       ("Click getByRole('button', { name: 'Sign in' })", 3)), motion=None):
    """A worker's result for a demo run: one test, a PNG and a step label per step (and `motion` {step number: motion})."""
    return {"status": status, "exit_code": 0 if status == "passed" else 1, "log": "ok",
            "tests": [{"id": TEST, "outcome": outcome, "message": ""}],
            "artifacts": [{"name": f"auth-signing-in-s{n:02d}-abc123", "png_b64": base64.b64encode(png()).decode(), "test": TEST,
                           "step": {"n": n, "label": label, **({"motion": motion[n]} if n in (motion or {}) else {})}}
                          for label, n in steps]}


def run_job(db, jid, body):
    jobs.claim(db, "w1", "linux", ["playwright-screens"], 100.0, 120, 900, 2)
    return jobs.complete(db, jid, "w1", body, 200.0)


class TraceSteps(unittest.TestCase):
    """worker/recipes/trace-steps.mjs reads a Playwright trace: the test body's actions, each with the frame taken while it ran."""

    def trace(self) -> bytes:
        def ev(**d):
            return json.dumps(d)
        test = "\n".join([
            ev(type="before", callId="h", startTime=0, method="hook", title="Before Hooks"),
            ev(type="before", callId="f", parentId="h", startTime=1, method="pw:api", title="Create page"),
            ev(type="after", callId="f", endTime=2), ev(type="after", callId="h", endTime=3),
            ev(type="before", callId="a", startTime=10, method="pw:api", title="Navigate", subtitle="http://localhost:3000/login?next=/x"),
            ev(type="after", callId="a", endTime=20),
            ev(type="before", callId="g", startTime=21, method="test.step", title="sign in"),
            ev(type="before", callId="b", parentId="g", startTime=22, method="pw:api", title='Fill "hunter2"', subtitle="getByPlaceholder('Password')"),
            ev(type="after", callId="b", endTime=30),
            ev(type="before", callId="c", parentId="g", startTime=31, method="pw:api", title="Click", subtitle="getByRole('button')"),
            ev(type="after", callId="c", endTime=40), ev(type="after", callId="g", endTime=41),
            ev(type="before", callId="d", startTime=42, method="expect", title='Expect "toBeVisible"'),
            ev(type="after", callId="d", endTime=45),
            ev(type="before", callId="e", startTime=46, method="pw:api", title="Hover", subtitle="getByText('x')"),
            ev(type="after", callId="e", endTime=50)])
        frames = "\n".join(ev(type="screencast-frame", file=f"screencast/{t}.jpeg", timestamp=t, width=1280, height=720)
                           for t in (2, 15, 18, 25, 35, 48))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("test.trace", test)
            z.writestr("0-trace.trace", frames)
            for t in (2, 15, 18, 25, 35):
                z.writestr(f"screencast/{t}.jpeg", f"frame {t}")
            z.writestr("screencast/48.jpeg", "frame 35")                # the same picture as the step before: left out
        return buf.getvalue()

    def test_each_body_step_keeps_the_last_frame_it_took_and_never_a_typed_value(self):
        if not shutil.which("node"):
            self.skipTest("no node")
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "trace.zip").write_bytes(self.trace())
        js = ("import {readFileSync} from 'node:fs'; const m = await import(process.argv[2]);"
              "const s = m.steps(m.unzip(readFileSync(process.argv[3]), () => true));"
              "console.log(JSON.stringify(s.map((x) => [x.n, x.label, x.frame.toString()])));")
        out = subprocess.run(["node", "--input-type=module", "-e", js, "-", str(RECIPES / "trace-steps.mjs"), str(d / "trace.zip")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [[1, "Navigate /login", "frame 18"],
                                                  [2, "Fill getByPlaceholder('Password')", "frame 25"],
                                                  [3, "Click getByRole('button')", "frame 35"]])

    def test_newer_traces_name_frames_by_sha1_and_put_the_target_in_the_title(self):
        """Playwright 1.4x and later (1.62 here): frames are resources/<sha1>, a step's target is in its title, a URL in its params."""
        if not shutil.which("node"):
            self.skipTest("no node")
        ev = lambda **d: json.dumps(d)
        test = "\n".join([
            ev(type="before", callId="a", startTime=10, method="pw:api", title='Navigate to "/login"', params={"url": "http://localhost:3000/login?x=1"}),
            ev(type="after", callId="a", endTime=20),
            ev(type="before", callId="b", startTime=21, method="pw:api", title='Fill "hunter2" getByPlaceholder(\'Password\')'),
            ev(type="after", callId="b", endTime=30)])
        frames = "\n".join(ev(type="screencast-frame", sha1=f"page@1-{t}.jpeg", timestamp=t, width=1280, height=720) for t in (15, 25))
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        with zipfile.ZipFile(d / "trace.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("test.trace", test)
            z.writestr("0-trace.trace", frames)
            for t in (15, 25):
                z.writestr(f"resources/page@1-{t}.jpeg", f"frame {t}")
        js = ("import {readFileSync} from 'node:fs'; const m = await import(process.argv[2]);"
              "const s = m.steps(m.unzip(readFileSync(process.argv[3]), () => true));"
              "console.log(JSON.stringify(s.map((x) => [x.n, x.label, x.frame.toString()])));")
        out = subprocess.run(["node", "--input-type=module", "-e", js, "-", str(RECIPES / "trace-steps.mjs"), str(d / "trace.zip")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [[1, "Navigate /login", "frame 15"], [2, "Fill getByPlaceholder('Password')", "frame 25"]])

    def test_a_step_says_what_it_did_and_where_the_pointer_went_in_the_frame(self):
        """A click's point comes from the context trace's input event (by its stepId) and is scaled from the page to the frame."""
        if not shutil.which("node"):
            self.skipTest("no node")
        ev = lambda **d: json.dumps(d)
        test = "\n".join([
            ev(type="before", callId="pw:api@1", startTime=10, method="pw:api", title='Fill "x" getByPlaceholder(\'Email\')'),
            ev(type="after", callId="pw:api@1", endTime=20),
            ev(type="before", callId="pw:api@2", startTime=21, method="pw:api", title="Click getByRole('button')"),
            ev(type="after", callId="pw:api@2", endTime=30)])
        ctx = "\n".join([
            ev(type="context-options", options={"viewport": {"width": 1280, "height": 720}}),
            ev(type="before", callId="call@5", stepId="pw:api@2", method="click"),
            ev(type="input", callId="call@5", point={"x": 600.4, "y": 300}),
            *(ev(type="screencast-frame", sha1=f"p-{t}.jpeg", timestamp=t, width=640, height=360) for t in (15, 25))])
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        with zipfile.ZipFile(d / "trace.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("test.trace", test)
            z.writestr("0-trace.trace", ctx)
            for t in (15, 25):
                z.writestr(f"resources/p-{t}.jpeg", f"frame {t}")
        js = ("import {readFileSync} from 'node:fs'; const m = await import(process.argv[2]);"
              "const s = m.steps(m.unzip(readFileSync(process.argv[3]), () => true));"
              "console.log(JSON.stringify(s.map((x) => [x.act, x.point, x.before && x.before.toString()])));")
        out = subprocess.run(["node", "--input-type=module", "-e", js, "-", str(RECIPES / "trace-steps.mjs"), str(d / "trace.zip")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [["fill", None, None], ["click", [300, 150], "frame 15"]])

    def test_the_recipe_runs_it_only_for_a_demo_run(self):
        text = (RECIPES / "playwright-screens.sh").read_text()
        self.assertIn('if [ "${FACTORY_STEPS:-}" = 1 ]', text)
        self.assertIn('node "$HERE/trace-steps.mjs"', text)
        self.assertIn("video: { mode: 'on', size:", text)                # full-size frames, not 800 px
        self.assertIn("...(b.projects ? {", text)                     # a config without projects keeps its tests


class Worker(unittest.TestCase):
    def test_a_demo_job_asks_the_recipe_for_steps_and_sends_them(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        env, d = W.results_env({"params": {"purpose": "screens", "files": ["e2e/a.spec.ts"], "steps": True}}, tmp)
        self.assertEqual(env["FACTORY_STEPS"], "1")
        (tmp / "x").mkdir()
        self.assertNotIn("FACTORY_STEPS", W.results_env({"params": {"purpose": "screens"}}, tmp / "x")[0])
        (d / "steps.tsv").write_text("a-s01-x.png\t1\tClick\x1b[31m it\nbad\t0\tx\n../e.png\t2\ty\n")
        self.assertEqual(W.collect_steps(d), {"a-s01-x": {"n": 1, "label": "Click it"}})
        (d / "steps.tsv").write_text('a-s01-x.png\t1\tClick\t{"act": "click", "x": 3, "y": 4}\nb-s01-x.png\t1\tFill\tnot json\n')
        self.assertEqual(W.collect_steps(d), {"a-s01-x": {"n": 1, "label": "Click", "motion": {"act": "click", "x": 3, "y": 4}},
                                              "b-s01-x": {"n": 1, "label": "Fill"}})

    def test_the_factory_keeps_only_a_well_formed_motion(self):
        self.assertEqual(jobs.clean_motion({"act": "fill", "box": [1, 2, 30, 10], "x": 5, "extra": "<b>"}), {"act": "fill", "box": [1, 2, 30, 10]})
        self.assertEqual(jobs.clean_motion({"act": "click", "x": 5, "y": True}), {"act": "click"})
        self.assertEqual(jobs.clean_motion({"act": "click", "x": 5, "y": 6}), {"act": "click", "x": 5, "y": 6})
        self.assertEqual(jobs.clean_motion({"act": "fill", "box": [1, 2, 0, 10]}), {"act": "fill"})
        self.assertIsNone(jobs.clean_motion({"act": "rm -rf"}))
        self.assertIsNone(jobs.clean_motion("click"))
        self.assertEqual(jobs.clean_step({"n": 2, "label": "Click", "motion": {"act": "click", "x": 99999, "y": 1}}),
                         {"n": 2, "label": "Click", "motion": {"act": "click"}})

    def test_a_trace_attachment_links_its_step_shots_to_the_test(self):
        root = Path("/repo")
        report = {"config": {"rootDir": "/repo"}, "suites": [{"file": "e2e/a.spec.ts", "specs": [{"title": "t", "file": "e2e/a.spec.ts", "tests": [
            {"results": [{"attachments": [{"name": "trace", "contentType": "application/zip", "path": "/repo/test-results/t/trace.zip"}]}]}]}]}]}
        self.assertEqual(list(W.playwright_shots(report, root)), ["/repo/test-results/t/trace.zip"])


class Store(unittest.TestCase):
    def setUp(self):
        from factory import db as dbm
        self.db = dbm.connect(":memory:")

    def test_steps_are_validated_kept_in_order_and_kept_off_the_board_and_the_register(self):
        res, why = jobs.validate_result({**step_body(), "artifacts": step_body()["artifacts"] + [
            {"name": "odd", "png_b64": base64.b64encode(png()).decode(), "step": {"n": 0, "label": "x"}}]})
        self.assertEqual(why, "")
        self.assertEqual(sorted(res["artifact_steps"]), [f"auth-signing-in-s0{n}-abc123" for n in (1, 2, 3)])
        jid = jobs.enqueue(self.db, REPO, 0, "a" * 40, "", "playwright-screens", "any", 1.0,
                           json.dumps({"purpose": "screens", "files": ["web/e2e/auth.spec.ts"], "steps": True}))
        self.assertEqual(run_job(self.db, jid, step_body()), "")
        got = jobs.steps(self.db, jid)
        self.assertEqual([(s["n"], s["test"]) for s in got], [(1, TEST), (2, TEST), (3, TEST)])
        self.assertEqual(got[2]["label"], "Click getByRole('button', { name: 'Sign in' })")
        SB.ensure_tables(self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM screen_shots").fetchone()[0], 0)

    def test_a_demo_run_is_not_the_boards_run_and_old_ones_are_pruned(self):
        cfg = replace(__import__("test_captures").make_cfg(), screens=ScreensCfg(captures=(ScreenCapture(REPO),)))
        self.assertIsNone(CP.queue_demo(cfg, FakeGh(), self.db, "x/y", ["e2e/a.spec.ts"], 1.0))
        self.assertIsNone(CP.queue_demo(cfg, FakeGh(), self.db, REPO, ["../a.spec.ts"], 1.0))
        self.assertIsNone(CP.queue_demo(replace(cfg, workers=WorkersCfg(enabled=False)), FakeGh(), self.db, REPO, ["e2e/a.spec.ts"], 1.0))
        ids = [CP.queue_demo(cfg, FakeGh(), self.db, REPO, ["e2e/a.spec.ts"], float(i)) for i in range(CP.KEEP_STEP_RUNS + 1)]
        self.assertTrue(jobs.is_steps(jobs.get(self.db, ids[0])))
        self.assertIsNone(CP.active(self.db, REPO))                        # the board's button still works
        for jid in ids:
            run_job(self.db, jid, step_body())
        CP.tick(cfg, FakeGh(), self.db, 300.0)
        self.assertIsNone(jobs.get(self.db, ids[0]))
        self.assertEqual(jobs.steps(self.db, ids[0]), [])
        self.assertIsNotNone(jobs.get(self.db, ids[-1]))


WRITER = """```factory-video
{"scenes": [
  {"kind": "title", "title": "Signing in", "say": "How a person signs in."},
  {"kind": "shot", "screen": "o/r:auth-signing-in-s01-abc123", "say": "Open the sign in page."},
  {"kind": "shot", "screen": "o/r:auth-signing-in-s03-abc123", "say": "Press sign in."},
  {"kind": "shot", "screen": "o/r:auth-signing-in-s09-abc123", "say": "Not a step of the run."}]}
```"""


class Video(Base):
    def setUp(self):
        super().setUp()
        self.cfg = replace(self.cfg, workers=WorkersCfg(enabled=True), screens=ScreensCfg(captures=(ScreenCapture(REPO),)))

    def queued(self, now=1000.0):
        return videos.queue_brief(self.db, "Signing in", "Show signing in.", [], now, demo=[f"{REPO}:web/e2e/auth.spec.ts", "nope", "x/y:../a.ts"])

    def test_it_waits_for_its_demo_run_then_the_writer_shows_the_steps(self):
        vid = self.queued()
        row = videos.get(self.db, vid)
        self.assertEqual(videos.demo_of(row), {REPO: ["web/e2e/auth.spec.ts"]})
        self.assertTrue(videos.demo_pending(self.cfg, FakeGh(), self.db, row, 1001.0))
        jid = videos.demo_jobs(videos.get(self.db, vid))[REPO]
        self.assertEqual(json.loads(jobs.get(self.db, jid)["params"])["files"], ["web/e2e/auth.spec.ts"])
        self.assertTrue(videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1002.0))
        self.assertEqual(len(self.db.execute("SELECT id FROM verify_jobs").fetchall()), 1)            # queued once
        run_job(self.db, jid, step_body())
        row = videos.get(self.db, vid)
        self.assertFalse(videos.demo_pending(self.cfg, FakeGh(), self.db, row, 1003.0))

        seen, run = {}, self.container()

        def write(prompt):
            seen["prompt"] = prompt
            return WRITER
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        videos.work_one(self.cfg, FakeGH(), self.db, videos.get(self.db, vid), lambda: 2000.0, run, write=write)
        row = videos.get(self.db, vid)
        self.assertEqual((row["status"], row["narration"]), ("ready", "writer"))
        self.assertIn("DEMO RUNS.", seen["prompt"])
        self.assertIn(f"TEST {REPO} {TEST} (passed)", seen["prompt"])
        self.assertIn("- o/r:auth-signing-in-s03-abc123: step 3, Click Sign in", seen["prompt"])
        shots = [s for s in run.seen["script"]["scenes"] if s["kind"] == "shot"]
        self.assertEqual([s["title"] for s in shots], ["Step 1 · Open /login", "Step 3 · Click Sign in"])
        self.assertEqual(run.seen["img"], ["s02.png", "s03.png"])

    def test_a_step_that_says_what_it_did_is_animated_from_the_picture_before_it(self):
        vid = self.queued()
        videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        jid = videos.demo_jobs(videos.get(self.db, vid))[REPO]
        run_job(self.db, jid, step_body(motion={1: {"act": "other"}, 3: {"act": "click", "x": 10, "y": 12}}))
        self.assertEqual([s["motion"] for s in jobs.steps(self.db, jid)], [{"act": "other"}, None, {"act": "click", "x": 10, "y": 12}])
        run = self.container()
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        videos.work_one(self.cfg, FakeGH(), self.db, videos.get(self.db, vid), lambda: 2000.0, run, write=lambda p: WRITER)
        self.assertEqual(videos.get(self.db, vid)["status"], "ready")
        shots = [s for s in run.seen["script"]["scenes"] if s["kind"] == "shot"]
        self.assertNotIn("before", shots[0])                     # the first step has no picture before it
        w, h = videos.png_size(png())
        self.assertEqual({k: shots[1].get(k) for k in ("before", "size", "act", "x", "y")},
                         {"before": "s03-before.png", "size": [w, h], "act": "click", "x": 10, "y": 12})
        self.assertEqual(run.seen["img"], ["s02.png", "s03-before.png", "s03.png"])

    def make(self, vid):
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        videos.work_one(self.cfg, FakeGH(), self.db, videos.get(self.db, vid), lambda: 2000.0, self.container(), write=lambda p: WRITER)
        return videos.get(self.db, vid)

    def test_a_failed_demo_test_stops_the_video_and_says_so(self):
        vid = self.queued()
        videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        run_job(self.db, videos.demo_jobs(videos.get(self.db, vid))[REPO], step_body("failed", "fail"))
        self.assertEqual((self.make(vid)["status"], self.make(vid)["reason"]), ("skipped", "demo_failed"))

    def test_a_demo_that_did_not_run_in_full_stops_the_video(self):
        vid = self.queued()                                                    # queued, never run: the wait ran out
        videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        self.assertEqual(self.make(vid)["reason"], "demo_timeout")
        off = replace(self.cfg, workers=WorkersCfg(enabled=False))
        vid = self.queued()                                                    # no worker could take it
        videos.demo_pending(off, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        self.assertEqual(self.make(vid)["reason"], "demo_unqueued")

    def test_a_demo_that_took_no_steps_stops_the_video(self):
        vid = self.queued()
        videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        run_job(self.db, videos.demo_jobs(videos.get(self.db, vid))[REPO], step_body(steps=()))
        self.assertEqual(self.make(vid)["reason"], "demo_no_steps")

    def test_a_run_that_never_finishes_or_cannot_be_queued_does_not_hold_the_video(self):
        vid = self.queued()
        self.assertTrue(videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0))
        self.assertFalse(videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1000.0 + videos.DEMO_WAIT))
        off = replace(self.cfg, workers=WorkersCfg(enabled=False))
        other = self.queued()
        self.assertFalse(videos.demo_pending(off, FakeGh(), self.db, videos.get(self.db, other), 1001.0))
        self.assertEqual(videos.demo_jobs(videos.get(self.db, other)), {REPO: 0})

    def test_make_again_runs_the_tests_again(self):
        vid = self.queued()
        videos.demo_pending(self.cfg, FakeGh(), self.db, videos.get(self.db, vid), 1001.0)
        self.db.execute("UPDATE review_videos SET status='ready' WHERE id=?", (vid,))
        again = videos.get(self.db, videos.again_brief(self.db, vid, 3000.0))
        self.assertEqual((videos.demo_of(again), videos.demo_jobs(again)), ({REPO: ["web/e2e/auth.spec.ts"]}, None))

    def test_step_titles_read_like_a_person_would_say_them(self):
        for label, want in (("Navigate /login", "Open /login"), ("Fill getByPlaceholder('Email')", "Fill Email"),
                            ("Click getByRole('menuitem', { name: \"Sign out\" })", "Click Sign out"), ("Check getByLabel('Remember me')", "Check Remember me")):
            self.assertEqual(videos.step_title(label), want)


class Proposal(unittest.TestCase):
    def test_a_demo_is_kept_only_when_each_test_is_accepted(self):
        ok = lambda ref: ref == "o/r:web/e2e/auth.spec.ts"                  # noqa: E731
        p = F.parse_proposal(block(tickets=[], demo=["o/r:web/e2e/auth.spec.ts"]), REFS, None, ok)[1]
        self.assertEqual(p["demo"], ["o/r:web/e2e/auth.spec.ts"])
        for demo in (["o/r:web/e2e/other.spec.ts"], ["o/r:web/e2e/auth.spec.ts"] * 4, "o/r:web/e2e/auth.spec.ts"):
            self.assertIsNone(F.parse_proposal(block(tickets=[], demo=demo), REFS, None, ok)[1], demo)
        self.assertIsNone(F.parse_proposal(block(demo=["o/r:web/e2e/auth.spec.ts"]), REFS)[1])          # nothing accepts a demo
        self.assertNotIn("demo", F.parse_proposal(block(), REFS)[1])

    def test_the_prompt_offers_demos_only_with_videos_code_and_a_repo(self):
        self.assertIn("DEMO.", F.build_prompt("s", [], videos=True, code="o/r at /work/code/o/r", demo=["o/r"]))
        self.assertNotIn("DEMO.", F.build_prompt("s", [], videos=True, code="o/r at /work/code/o/r"))
        self.assertNotIn("DEMO.", F.build_prompt("s", [], videos=True, demo=["o/r"]))
        self.assertNotIn("DEMO.", F.build_prompt("s", [], videos=False, code="x", demo=["o/r"]))


class Page(UiCase):
    def test_the_proposal_shows_the_demo_and_making_it_queues_the_tests(self):
        overrides_path(str(self.root / "config.toml")).write_text(
            "[general]\ndry_run = false\n[chat]\nfactory_enabled = true\n[videos]\nenabled = true\n")
        cookie, csrf = self.session()
        db = sqlite3.connect(self.db_path)
        F.ensure_tables(db)
        thread = F.new_thread(db, "demo login")
        F.add_reply(db, {"id": F.add_person(db, "demo login", thread)}, block(tickets=[], demo=["o/r:web/e2e/auth.spec.ts"]), None,
                    refs=REFS, demo_ok=lambda ref: True)
        tid = F.turns(db, thread)[-1]["id"]
        html = self.req("GET", f"/chat?c={thread}", cookie=cookie)[2]
        self.assertIn("Showing these tests running in the app", html)
        self.assertIn("o/r:web/e2e/auth.spec.ts", html)
        self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid), ("voice", "")]), cookie=cookie)
        row = videos.briefs(db)[0]
        self.assertEqual(videos.demo_of(row), {"o/r": ["web/e2e/auth.spec.ts"]})
        html = self.req("GET", "/videos", cookie=cookie)[2]
        self.assertIn("o/r:web/e2e/auth.spec.ts (demo)", html)
        self.assertIn("Running the demo tests on a worker first.", html)


if __name__ == "__main__":
    unittest.main()
