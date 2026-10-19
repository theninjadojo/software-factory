import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import wave
import io
from dataclasses import replace
from pathlib import Path

from factory import db as dbm
from factory import videos, voices
from factory.config import ReviewCfg, RunnerCfg, VideosCfg, parse
from test_render import png
from test_roles import CFG


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(seconds=10.0, codecs=(b"avc1", b"mp4a"), moov_first=True) -> bytes:
    """A minimal MP4 that mp4_ok reads like a real one: ftyp, moov (mvhd + one trak per codec), mdat."""
    mvhd = box(b"mvhd", b"\0" * 12 + struct.pack(">II", 1000, int(seconds * 1000)) + b"\0" * 80)
    traks = b"".join(box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", box(b"stsd", b"\0" * 4 + struct.pack(">I", 1)
                                                                                  + struct.pack(">I", 16) + c + b"\0" * 8))))) for c in codecs)
    moov, mdat = box(b"moov", mvhd + traks), box(b"mdat", b"\0" * 64)
    return box(b"ftyp", b"isom" + b"\0\0\0\0" + b"isom") + (moov + mdat if moov_first else mdat + moov)


PATCH = "@@ -1,2 +1,3 @@\n line one\n-old\n+new\n+added\n@@ -40,1 +41,2 @@\n ctx\n+second hunk"
FILES = [{"filename": "factory/videos.py", "status": "modified", "additions": 3, "deletions": 1, "patch": PATCH},
         {"filename": "docs/videos.md", "status": "added", "additions": 50, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+# Review videos"}]
BLOCK = """Looks fine.

```factory-video
{"videos": [{"pr": "o/r#7", "scenes": [
  {"kind": "title", "title": "Remotion **videos**", "say": "This adds <b>videos</b>. See https://evil.example/x for more."},
  {"kind": "code", "file": "factory/videos.py", "hunk": 1, "say": "The second hunk adds the maker."},
  {"kind": "code", "file": "/etc/passwd", "say": "Not a file of the PR."},
  {"kind": "shot", "screen": "home-desktop", "say": "The home screen."},
  {"kind": "shot", "screen": "never-taken", "say": "A screen the build did not take."},
  {"kind": "points", "title": "What to check", "points": ["The voice", 5, ""], "say": "Check the voice."},
  {"kind": "script", "say": "not a kind"}]},
 {"pr": "other/repo#1", "scenes": [{"kind": "title", "title": "Not reviewed here", "say": "x"}]}]}
```
"""


class FakeGH:
    def __init__(self, state="open", files=FILES):
        self.state, self.files, self.comments, self.sha = state, files, [], "abc123"

    def get_pr(self, repo, n):
        return {"state": self.state, "title": "Add **review** videos", "head": {"sha": self.sha}}

    def pr_files(self, repo, n):
        return self.files

    def comment(self, repo, n, body):
        self.comments.append((repo, n, body))
        return f"https://github.com/{repo}/issues/{n}#c"


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, True)
        (self.t / "secrets").mkdir()
        self.cfg = replace(CFG, db_path=str(self.t / "state" / "factory.db"), dry_run=False, runner=replace(CFG.runner, work_dir=str(self.t / "work")),
                           videos=VideosCfg(enabled=True, elevenlabs_key_file=str(self.t / "secrets" / "elevenlabs_key"), elevenlabs_voice_id="voiceABCDEF12"),
                           review=ReviewCfg(enabled=True, auto=True))
        (self.t / "state").mkdir()
        self.db = dbm.connect(self.cfg.db_path)
        self.addCleanup(self.db.close)
        dbm.track_pr(self.db, "o/r", 7, "o/r", 3)

    def container(self, data=None, timings=None, code=0):
        """A stand-in for subprocess.run: the 'video container' records what it was given and writes review.mp4 and timings.json."""
        seen = {"calls": []}

        def run(cmd, **kw):
            seen["calls"].append(cmd)
            if cmd[0] == "chmod":
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[1] == "run":
                inp = Path(next(a for a in cmd if a.endswith(":/in:ro")).split(":")[0])
                out = Path(next(a for a in cmd if a.endswith(":/out:rw")).split(":")[0])
                seen["script"] = json.loads((inp / "script.json").read_text())
                seen["img"] = sorted(p.name for p in (inp / "img").iterdir())
                seen["audio"] = sorted(p.name for p in (inp / "audio").iterdir())
                if code == 0:
                    n = len(seen["script"]["scenes"])
                    (out / "review.mp4").write_bytes(mp4() if data is None else data)
                    (out / "timings.json").write_text(json.dumps({"scenes": timings or [2.0] * n}))
                return subprocess.CompletedProcess(cmd, code, "", "boom")
            return subprocess.CompletedProcess(cmd, 0)
        run.seen = seen
        return run

    def row(self):
        vid = videos.queue(self.db, "o/r", 3, "o/r", 7, 1000.0)
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        return videos.get(self.db, vid)


class Config(unittest.TestCase):
    def raw(self, **v):
        import tomllib
        raw = tomllib.loads((Path(__file__).resolve().parent.parent / "config.example.toml").read_text())
        raw["github"]["token_file"] = "/srv/factory/secrets/github_token"
        return {**raw, "videos": v}

    def test_defaults_and_the_key_next_to_the_github_token(self):
        vc = parse(self.raw()).videos
        self.assertEqual((vc.enabled, vc.voice, vc.elevenlabs_model), (False, "piper", "eleven_multilingual_v2"))
        self.assertEqual(vc.elevenlabs_key_file, "/srv/factory/secrets/elevenlabs_key")

    def test_bad_voices_are_refused(self):
        for bad in ({"voice": "espeak"}, {"voice": "elevenlabs"}, {"elevenlabs_voice_id": "x/../y"}, {"elevenlabs_model": "Bad Model"}):
            with self.assertRaises(ValueError, msg=bad):
                parse(self.raw(**bad))
        self.assertEqual(parse(self.raw(voice="elevenlabs", elevenlabs_voice_id="21m00Tcm4TlvDq8ikWAM")).videos.voice, "elevenlabs")


class ElevenLabs(unittest.TestCase):
    def test_speech_comes_back_as_a_wav(self):
        sent = []

        def post(url, key, body, timeout):
            sent.append((url, key, body))
            return b"\x01\x00" * 22050
        out = voices.elevenlabs("sk_key", "voiceABCDEF12", "eleven_multilingual_v2", "Hello there.", post=post)
        with wave.open(io.BytesIO(out)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 22050, 22050))
        self.assertEqual(sent, [("https://api.elevenlabs.io/v1/text-to-speech/voiceABCDEF12?output_format=pcm_22050", "sk_key",
                                 {"text": "Hello there.", "model_id": "eleven_multilingual_v2"})])

    def test_failures_are_fixed_messages(self):
        def refused(code):
            def post(*a):
                raise urllib.error.HTTPError("u", code, "m", {}, None)
            return post
        for code, words in ((401, "refused the key"), (404, "no such voice"), (429, "quota"), (500, "answered 500")):
            with self.assertRaisesRegex(voices.VoiceError, words):
                voices.elevenlabs("k" * 10, "voiceABCDEF12", "eleven_v3", "hi", post=refused(code))
        ok = lambda *a: b"\0\0"
        for args in (("", "voiceABCDEF12", "m_1", "hi"), ("k", "bad id!", "m_1", "hi"), ("k", "voiceABCDEF12", "m_1", "")):
            with self.assertRaises(voices.VoiceError):
                voices.elevenlabs(*args, post=ok)
        for reply in (b"\0", b"", "text", b"\0" * (voices.MAX_PCM + 2)):
            with self.assertRaisesRegex(voices.VoiceError, "not speech"):
                voices.elevenlabs("k", "voiceABCDEF12", "m_1", "hi", post=lambda *a, r=reply: r)


class Music(unittest.TestCase):
    MP3 = b"ID3" + b"\0" * 2000

    def test_a_track_is_asked_for_with_the_moods_fixed_prompt_and_checked(self):
        sent = []

        def post(url, key, body, timeout):
            sent.append((url, key, body))
            return self.MP3
        self.assertEqual(voices.music("sk_key", "calm", 42.4, post=post), self.MP3)
        self.assertEqual(sent, [("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128", "sk_key",
                                 {"prompt": voices.MOODS["calm"], "music_length_ms": 42400, "force_instrumental": True})])
        voices.music("k", "bright", 9999, post=post)
        self.assertEqual(sent[-1][2]["music_length_ms"], 300000)                    # at most five minutes
        for args, reply in ((("", "calm", 30), self.MP3), (("k", "jazz", 30), self.MP3), (("k", "calm", 30), b"<html>" * 400),
                            (("k", "calm", 30), b"ID3")):
            with self.assertRaises(voices.VoiceError):
                voices.music(*args, post=lambda *a, r=reply: r)

        def refused(*a):
            raise urllib.error.HTTPError("u", 401, "no", {}, None)
        with self.assertRaisesRegex(voices.VoiceError, "refused the key"):
            voices.music("k", "calm", 30, post=refused)

    def test_a_key_without_music_access_says_so(self):
        def restricted(*a):
            body = io.BytesIO(json.dumps({"detail": {"status": "missing_permissions", "message": "missing music_generation"}}).encode())
            raise urllib.error.HTTPError("u", 401, "no", {}, body)
        with self.assertRaisesRegex(voices.VoiceError, "may not make music; turn on Music for this key"):
            voices.music("k", "calm", 30, post=restricted)

    def test_the_setting_is_off_or_a_mood(self):
        self.assertEqual(parse(Config.raw(Config(), music="focused")).videos.music, "focused")
        self.assertEqual(parse(Config.raw(Config())).videos.music, "off")
        with self.assertRaises(ValueError):
            parse(Config.raw(Config(), music="loud"))


class ReviewBlock(unittest.TestCase):
    def test_only_checked_scenes_of_reviewed_prs_are_kept(self):
        found = videos.parse_block(BLOCK, [("o/r", 7)])
        self.assertEqual(list(found), [("o/r", 7)])                          # other/repo#1 was not reviewed in this run
        scenes = found[("o/r", 7)]
        self.assertEqual([s["kind"] for s in scenes], ["title", "code", "code", "shot", "shot", "points"])   # the unknown kind is gone
        self.assertEqual(scenes[0]["title"], "Remotion videos")
        self.assertEqual(scenes[0]["say"], "This adds b videos /b . See for more.")   # no tags, Markdown or links survive
        self.assertEqual(scenes[-1]["points"], ["The voice"])
        self.assertNotIn("factory-video", videos.strip(BLOCK))
        self.assertEqual(videos.parse_block("no block", [("o/r", 7)]), {})
        self.assertEqual(videos.parse_block("```factory-video\nnot json\n```\n", [("o/r", 7)]), {})

    def test_scenes_only_show_the_prs_own_files_and_the_builds_own_screens(self):
        scenes, narration = videos.script(7, "t", FILES, ["home-desktop"], videos.parse_block(BLOCK, [("o/r", 7)])[("o/r", 7)])
        self.assertEqual(narration, "review")
        self.assertEqual([s["kind"] for s in scenes], ["title", "code", "shot", "points"])
        code = scenes[1]
        self.assertEqual(code["title"], "factory/videos.py")
        self.assertEqual(code["lines"], [{"t": "@", "text": "@@ -40,1 +41,2 @@"}, {"t": " ", "text": "ctx"}, {"t": "+", "text": "second hunk"}])
        self.assertEqual(code["say"], "The second hunk adds the maker.")

    def test_without_a_usable_block_the_wording_is_fixed(self):
        scenes, narration = videos.script(7, "Add **videos**", FILES, [], [{"kind": "code", "file": "nope.py", "hunk": 0, "say": "x"}])
        self.assertEqual(narration, "fixed")
        self.assertEqual(scenes[0]["say"], "Review of pull request 7: Add videos. It changes 2 files, with 53 lines added and 1 removed.")
        many = [{"filename": f"f{i:02d}.py", "additions": i, "deletions": 0, "patch": "+x"} for i in range(20)]
        scenes, _ = videos.script(7, "", many, ["home"])
        self.assertEqual(scenes[2]["title"], "f19.py")                          # the biggest change first, not the first name
        noisy = [{"filename": "package-lock.json", "additions": 5000, "deletions": 0, "patch": "+x"},
                 {"filename": "big.bin", "additions": 900, "deletions": 0}, {"filename": "app.py", "additions": 3, "deletions": 0, "patch": "+y"}]
        ordered, _ = videos.script(7, "", noisy, [])
        self.assertEqual([s["title"] for s in ordered[1:]], ["app.py", "package-lock.json", "big.bin"])  # lock files and binaries last
        self.assertEqual(videos.spec(VideosCfg(), ordered, "piper")["scenes"][3]["lines"], [])     # always sent, even when empty
        self.assertEqual(len(scenes), videos.MAX_SCENES)
        self.assertEqual(scenes[-1]["kind"], "points")
        self.assertIn("more files", scenes[-1]["say"])


class Making(Base):
    def test_a_review_narrated_video_with_piper(self):
        videos.store_notes(self.db, videos.parse_block(BLOCK, [("o/r", 7)]), 1000.0)
        run = self.container()
        gh = FakeGH()
        videos.work_one(self.cfg, gh, self.db, self.row(), lambda: 2000.0, run)
        v = videos.newest_for_pr(self.db, "o/r", 7)
        self.assertEqual((v["status"], v["narration"], v["voice"], v["mode"]), ("ready", "review", "piper", "diff"))
        self.assertEqual(v["seconds"], 10.0)
        spec = run.seen["script"]
        self.assertEqual((spec["voice"], spec["max_seconds"]), ("piper", 120))
        self.assertEqual([s["kind"] for s in spec["scenes"]], ["title", "code", "points"])       # no screenshots were taken
        self.assertEqual(run.seen["audio"], [])
        self.assertEqual([t for t, _ in videos.transcript(v)], [0.0, 2.0, 4.0])
        self.assertTrue(videos.video_path(self.cfg, v).is_file())
        self.assertEqual(oct(videos.video_path(self.cfg, v).stat().st_mode & 0o777), "0o600")
        cmd = next(c for c in run.seen["calls"] if c[1] == "run")
        for flag in ("--network=none", "--read-only", "--cap-drop=all", "--security-opt=no-new-privileges"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[-1], "localhost/factory-video:latest")
        self.assertEqual(len(gh.comments), 2)                                   # the PR and the ticket get the link

    def test_screenshots_the_build_took_go_in_as_files(self):
        rid = dbm.start_run(self.db, "build", "o/r", 3, "t", "claude-code", "m", "high")
        dbm.add_run_images(self.db, rid, [{"kind": "built", "name": "home-desktop", "png": png(40, 30)}])
        run = self.container()
        videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 2000.0, run)
        self.assertEqual(run.seen["img"], ["s02.png"])
        self.assertEqual(run.seen["script"]["scenes"][1], {"kind": "shot", "say": "The home desktop screen, as built.", "title": "home desktop screen",
                                                           "image": "s02.png"})
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["mode"], "ui")

    def test_an_elevenlabs_voice_is_made_here_and_handed_in(self):
        (self.t / "secrets" / "elevenlabs_key").write_text("sk_test_key\n")
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 1000.0, "elevenlabs", "otherVoice123")
        asked = []

        def post(url, key, body, timeout):
            asked.append((url.split("/")[-1].split("?")[0], key, body["text"]))
            return b"\0\0" * 100
        run = self.container()
        row = videos.get(self.db, self.db.execute("SELECT id FROM review_videos").fetchone()[0])
        videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, run, post)
        spec = run.seen["script"]
        self.assertEqual(spec["voice"], "files")
        self.assertEqual(run.seen["audio"], [s["audio"] for s in spec["scenes"]])
        self.assertTrue(all(k == "sk_test_key" and v == "otherVoice123" for v, k, _ in asked))   # the ticket's voice over the config's
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["voice"], "elevenlabs")

    def test_background_music_goes_in_with_the_video_and_its_failure_does_not_stop_it(self):
        (self.t / "secrets" / "elevenlabs_key").write_text("sk_test_key\n")
        self.cfg = replace(self.cfg, videos=replace(self.cfg.videos, music="calm"))
        asked = []

        def music(key, mood, seconds, **kw):
            asked.append((key, mood, seconds))
            return Music.MP3
        videos.store_notes(self.db, videos.parse_block(BLOCK, [("o/r", 7)]), 1000.0)
        run = self.container()
        with mock.patch.object(videos.voices, "music", music):
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 2000.0, run)
        self.assertEqual(run.seen["script"]["music"], "music.mp3")
        self.assertEqual(run.seen["audio"], ["music.mp3"])                             # Piper speaks in the container
        self.assertEqual(asked[0][:2], ("sk_test_key", "calm"))
        self.assertTrue(5 < asked[0][2] <= 120)

        def broken(*a, **kw):
            raise voices.VoiceError("ElevenLabs rate limit or quota reached")
        self.db.execute("DELETE FROM review_videos")
        run = self.container()
        with mock.patch.object(videos.voices, "music", broken):
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 4000.0, run)
        self.assertNotIn("music", run.seen["script"])
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["status"], "ready")

    def test_a_tickets_music_wins_over_the_config_and_a_failure_is_kept_on_the_video(self):
        (self.t / "secrets" / "elevenlabs_key").write_text("sk_test_key\n")
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 900.0, music="bright")      # the config's is off
        self.db.execute("DELETE FROM review_videos")
        videos.store_notes(self.db, videos.parse_block(BLOCK, [("o/r", 7)]), 1000.0)
        asked = []
        with mock.patch.object(videos.voices, "music", lambda key, mood, seconds, **kw: asked.append(mood) or Music.MP3):
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 2000.0, self.container())
        self.assertEqual(asked, ["bright"])
        made = videos.newest_for_pr(self.db, "o/r", 7)
        self.assertEqual((made["music"], made["music_note"]), ("bright", ""))

        def refused(*a, **kw):
            raise voices.VoiceError("the ElevenLabs key may not make music")
        self.db.execute("DELETE FROM review_videos")
        with mock.patch.object(videos.voices, "music", refused):
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 3000.0, self.container())
        made = videos.newest_for_pr(self.db, "o/r", 7)
        self.assertEqual((made["status"], made["music"], made["music_note"]), ("ready", "", "the ElevenLabs key may not make music"))

        videos.set_enabled(self.db, "o/r", 3, True, "admin", 900.0, music="off")         # off asks for nothing, even with the config on
        self.cfg = replace(self.cfg, videos=replace(self.cfg.videos, music="calm"))
        self.db.execute("DELETE FROM review_videos")
        with mock.patch.object(videos.voices, "music", refused):
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 4000.0, self.container())
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["music_note"], "")

    def test_a_voice_that_fails_skips_the_video_with_a_note(self):
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 1000.0, "elevenlabs")      # no key saved
        gh = FakeGH()
        run = self.container()
        row = videos.get(self.db, self.db.execute("SELECT id FROM review_videos").fetchone()[0])
        videos.work_one(self.cfg, gh, self.db, row, lambda: 2000.0, run)
        v = videos.newest_for_pr(self.db, "o/r", 7)
        self.assertEqual((v["status"], v["reason"]), ("skipped", "voice"))
        self.assertNotIn("script", run.seen)                                    # the container never ran
        self.assertIn("voice could not be made", gh.comments[0][2])

    def test_what_comes_back_is_checked(self):
        cases = [(dict(code=3), "too_long"), (dict(code=1), "tool_failed"), (dict(data=b"<html>not a video</html>" * 4), "tool_failed"),
                 (dict(data=mp4(codecs=(b"avc1", b"avc1"))), "tool_failed"), (dict(data=mp4(moov_first=False)), "tool_failed"),
                 (dict(data=mp4(seconds=500)), "too_long"), (dict(timings=[1.0]), "tool_failed")]
        for kw, reason in cases:
            self.db.execute("DELETE FROM review_videos")
            videos.work_one(self.cfg, FakeGH(), self.db, self.row(), lambda: 2000.0, self.container(**kw))
            self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["reason"], reason, kw)

    def test_a_closed_pr_gets_no_video(self):
        videos.work_one(self.cfg, FakeGH(state="closed"), self.db, self.row(), lambda: 2000.0, self.container())
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["reason"], "closed")


class Queue(Base):
    def test_a_new_prs_video_waits_for_its_review(self):
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 900.0)
        self.db.execute("DELETE FROM review_videos")
        videos.pr_opened(self.db, "o/r", 3, "o/r", 7, 1000.0)
        run = self.container()
        self.assertFalse(videos.tick(self.cfg, FakeGH(), self.db, 1100.0, lambda: dbm.connect(self.cfg.db_path), lambda: 1100.0, False, run))
        self.assertTrue(videos.wanted(self.db, [("o/r", 7)]))                   # so the reviewer is asked for the block
        videos.reviewed(self.cfg, FakeGH(), self.db, [("o/r", 7)], BLOCK, 1200.0)
        self.assertTrue(videos.tick(self.cfg, FakeGH(), self.db, 1300.0, lambda: dbm.connect(self.cfg.db_path), lambda: 1300.0, False, run))
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["narration"], "review")

    def test_it_stops_waiting_after_an_hour_and_never_waits_without_auto_review(self):
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 900.0)
        self.db.execute("DELETE FROM review_videos")
        videos.pr_opened(self.db, "o/r", 3, "o/r", 7, 1000.0)
        row = videos._dicts(self.db.execute("SELECT * FROM review_videos"))[0]
        self.assertTrue(videos.waiting_for_review(self.cfg, self.db, row, 1000.0 + videos.REVIEW_WAIT - 1))
        self.assertFalse(videos.waiting_for_review(self.cfg, self.db, row, 1000.0 + videos.REVIEW_WAIT))
        self.assertFalse(videos.waiting_for_review(replace(self.cfg, review=ReviewCfg(enabled=True, auto=False)), self.db, row, 1001.0))

    def test_turning_it_on_for_open_prs_does_not_wait(self):
        self.assertEqual(videos.set_enabled(self.db, "o/r", 3, True, "admin", 1000.0), 1)
        row = videos._dicts(self.db.execute("SELECT * FROM review_videos"))[0]
        self.assertFalse(videos.waiting_for_review(self.cfg, self.db, row, 1001.0))

    def test_a_fixed_wording_video_is_made_again_once_the_review_arrives(self):
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 1000.0)
        videos.work_one(self.cfg, FakeGH(), self.db, self.row_making(), lambda: 1100.0, self.container())
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["narration"], "fixed")
        out = videos.reviewed(self.cfg, FakeGH(), self.db, [("o/r", 7)], BLOCK, 1200.0)
        self.assertEqual(out, "Looks fine.")
        self.assertEqual(videos.newest_for_pr(self.db, "o/r", 7)["status"], "queued")

    def row_making(self):
        vid = self.db.execute("SELECT id FROM review_videos").fetchone()[0]
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        return videos.get(self.db, vid)

    def test_voices_are_checked_when_saved(self):
        for voice, vid in (("espeak", ""), ("elevenlabs", "../../x"), ("", "has space")):
            with self.assertRaises(ValueError):
                videos.set_enabled(self.db, "o/r", 3, True, "admin", 1.0, voice, vid)
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 1.0, "piper")
        self.assertEqual(videos.ticket_voice(self.db, "o/r", 3), ("piper", ""))
        self.assertEqual(videos.voice_for(self.cfg.videos, ("", "")), ("piper", ""))
        self.assertEqual(videos.voice_for(self.cfg.videos, ("elevenlabs", "")), ("elevenlabs", "voiceABCDEF12"))

    def test_music_is_checked_when_saved(self):
        with self.assertRaises(ValueError):
            videos.set_enabled(self.db, "o/r", 3, True, "admin", 1.0, music="loud")
        with self.assertRaises(ValueError):
            videos.queue_brief(self.db, "T", "B", [], 1.0, music="loud")
        videos.set_enabled(self.db, "o/r", 3, True, "admin", 1.0, music="calm")
        self.assertEqual(videos.ticket_music(self.db, "o/r", 3), "calm")
        self.assertEqual(videos.music_for(self.cfg.videos, ""), "off")
        self.assertEqual(videos.music_for(self.cfg.videos, "focused"), "focused")
        vid = videos.queue_brief(self.db, "T", "B", [], 1.0, music="bright")
        self.db.execute("UPDATE review_videos SET status='ready' WHERE id=?", (vid,))
        self.assertEqual(videos.get(self.db, videos.again_brief(self.db, vid, 2.0))["ask_music"], "bright")


class Ui(Base):
    def test_the_player_may_load_media_from_this_site(self):
        from factory.ui import reviewvideo, server
        directives = [d.strip() for d in reviewvideo.player_csp(server.HEADERS).split(";") if d.strip()]
        self.assertIn("media-src 'self'", directives)
        self.assertIn("default-src 'none'", directives)
        self.assertFalse(any("media-src" in d and not d.startswith("media-src") for d in directives))


    def test_elevenlabs_is_offered_only_with_a_saved_key(self):
        from factory.ui import reviewvideo
        html = reviewvideo.voice_fields(self.cfg, "", "")
        self.assertNotIn('value="elevenlabs"', html)
        self.assertIn("save an ElevenLabs key", html)
        (self.t / "secrets" / "elevenlabs_key").write_text("sk_test_key")
        html = reviewvideo.voice_fields(self.cfg, "elevenlabs", "abcDEF12345")
        self.assertIn('<option value="elevenlabs" selected>', html)
        self.assertIn('value="abcDEF12345"', html)
        self.assertIn("ElevenLabs receives the narration text", html)
        self.assertNotIn("sk_test_key", html)

    def test_music_is_chosen_with_the_voice_and_its_outcome_shown(self):
        from factory.ui import reviewvideo
        self.assertNotIn('name="music"', reviewvideo.voice_fields(self.cfg, "", ""))         # ElevenLabs makes it: needs the key
        (self.t / "secrets" / "elevenlabs_key").write_text("sk_test_key")
        html = reviewvideo.voice_fields(self.cfg, "", "", "bright")
        self.assertIn('<option value="">Default (Off)</option>', html)
        self.assertIn('<option value="bright" selected>Bright</option>', html)
        self.assertEqual(reviewvideo._music({"music": "calm", "music_note": ""}), " · Calm music")
        self.assertEqual(reviewvideo._music({"music": "", "music_note": "no <key>"}), " · No music: no &lt;key&gt;")
        self.assertEqual(reviewvideo._music({"music": "", "music_note": ""}), "")


@unittest.skipUnless(os.environ.get("FACTORY_VIDEO_IMAGE") and shutil.which("docker"), "set FACTORY_VIDEO_IMAGE to render with the real image")
class RealContainer(Base):
    def test_the_image_makes_a_video_the_factory_accepts(self):
        cfg = replace(self.cfg, runner=replace(self.cfg.runner, engine="docker"), videos=replace(self.cfg.videos, image=os.environ["FACTORY_VIDEO_IMAGE"]))
        videos.work_one(cfg, FakeGH(), self.db, self.row(), lambda: 2000.0)
        v = videos.newest_for_pr(self.db, "o/r", 7)
        self.assertEqual(v["status"], "ready", v["reason"])
        self.assertGreater(v["seconds"], 5)

    def test_the_image_refuses_a_script_it_was_not_given_the_files_for(self):
        cfg = replace(self.cfg, runner=replace(self.cfg.runner, engine="docker"), videos=replace(self.cfg.videos, image=os.environ["FACTORY_VIDEO_IMAGE"]))
        bad = {"voice": "none", "scenes": [{"kind": "shot", "image": "../../etc/passwd", "say": "x"}]}
        with self.assertRaises(videos.Skip) as cm:
            videos._compose(cfg, bad, {}, {}, subprocess.run)
        self.assertEqual(cm.exception.args[0], "tool_failed")


if __name__ == "__main__":
    unittest.main()
