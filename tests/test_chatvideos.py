"""Videos asked for in the factory chat: the proposal, confirming it, the writer's scenes, and the Videos page."""
import json
import sqlite3
import time
import unittest
from urllib.parse import urlencode

from factory import factorychat as F
from factory import videos
from factory.config import overrides_path
from test_factorychat import memdb
from test_ui import UiCase
from test_videos import FILES, Base, FakeGH, png

REFS = {"o/r#3": ("o/r", 3, "Board drag"), "o/r#4": ("o/r", 4, "Login fix")}


def block(**obj) -> str:
    base = {"action": "video", "title": "Board tour", "brief": "Show the board changes.", "tickets": ["o/r#3"]}
    return "Here is a plan.\n```factory-proposal\n" + json.dumps({**base, **obj}) + "\n```\n"


class Proposal(unittest.TestCase):
    def test_a_valid_proposal_is_kept_and_the_block_is_not_shown(self):
        shown, p = F.parse_proposal(block(tickets=["o/r#3", "o/r#4"]), REFS)
        self.assertNotIn("factory-proposal", shown)
        self.assertEqual(p, {"action": "video", "title": "Board tour", "brief": "Show the board changes.",
                             "tickets": [["o/r", 3, "Board drag"], ["o/r", 4, "Login fix"]]})

    def test_extra_keys_unknown_tickets_and_videos_off_are_dropped(self):
        for text in (block(voice="x"), block(tickets=["o/r#99"]), block(tickets=[]), block(tickets=["o/r#3"] * 9),
                     block(action="redirect"), block(title=""), "```factory-proposal\nnot json\n```"):
            shown, p = F.parse_proposal(text, REFS)
            self.assertIsNone(p, text)
            self.assertNotIn("factory-proposal", shown)
        self.assertIsNone(F.parse_proposal(block(), None)[1])
        self.assertIsNone(F.parse_proposal(block(), {})[1])

    def test_markup_in_the_texts_is_flattened(self):
        p = F.parse_proposal(block(title="<b>Tour</b> " + "x" * 500), REFS)[1]
        self.assertNotIn("<", p["title"])
        self.assertLessEqual(len(p["title"]), F.MAX_VIDEO_TITLE)

    def test_the_reply_stores_an_open_proposal_and_the_prompt_says_when_videos_are_off(self):
        db = memdb()
        F.add_reply(db, {"id": F.add_person(db, "make a video")}, block(), None, refs=REFS)
        t = F.turns(db)[-1]
        self.assertEqual((t["proposal_state"], t["proposal"]["title"]), ("open", "Board tour"))
        self.assertTrue(F.set_proposal_state(db, t["id"], "open", "confirmed"))
        self.assertFalse(F.set_proposal_state(db, t["id"], "open", "confirmed"))
        self.assertIn("switched off", F.build_prompt("snap", [], videos=False))
        self.assertIn("factory-proposal", F.build_prompt("snap", [], videos=True))
        self.assertNotIn("VIDEOS.", F.build_prompt("snap", []))


WRITER = """```factory-video
{"scenes": [
  {"kind": "title", "title": "Board tour", "say": "What changed on the board."},
  {"kind": "code", "pr": "o/r#7", "file": "factory/videos.py", "hunk": 1, "say": "The second hunk."},
  {"kind": "code", "pr": "o/r#7", "file": "/etc/passwd", "say": "Not in the material."},
  {"kind": "code", "pr": "x/y#1", "file": "factory/videos.py", "say": "Not a PR of the material."},
  {"kind": "shot", "screen": "o/r#3:home-desktop", "say": "The home screen."},
  {"kind": "shot", "screen": "o/r#3:never-taken", "say": "Not taken."},
  {"kind": "shot", "screen": "x/y#9:home-desktop", "say": "Another ticket."}]}
```"""


class Brief(Base):
    def setUp(self):
        super().setUp()
        self.db.execute("INSERT INTO runs (kind, repo, issue, title, started, status) VALUES ('build','o/r',3,'t',1,'ok')")
        run = self.db.execute("SELECT MAX(id) FROM runs").fetchone()[0]
        self.db.execute("INSERT INTO run_images (run_id, kind, name, png, created) VALUES (?, 'built', 'home-desktop', ?, 1)", (run, png()))
        self.db.commit()

    def queued(self):
        vid = videos.queue_brief(self.db, "Board tour", "Show the board.", [("o/r", 3, "Board drag")], 1000.0)
        self.db.execute("UPDATE review_videos SET status='making' WHERE id=?", (vid,))
        return videos.get(self.db, vid)

    def test_only_scenes_pointing_at_the_material_survive(self):
        mat = videos.material(FakeGH(), self.db, [("o/r", 3, "Board drag")])
        self.assertEqual(list(mat["prs"]), ["o/r#7"])
        self.assertEqual(list(mat["shots"]), ["o/r#3:home-desktop"])
        scenes = videos.brief_scenes(WRITER, mat)
        self.assertEqual([s["kind"] for s in scenes], ["title", "code", "shot"])
        self.assertTrue(scenes[1]["subtitle"].startswith("o/r#7"))
        self.assertIn({"t": "+", "text": "second hunk"}, scenes[1]["lines"])

    def test_a_brief_video_is_made_and_noted_in_the_chat_without_github_comments(self):
        gh, run, seen = FakeGH(), self.container(), {}

        def write(prompt):
            seen["prompt"] = prompt
            return WRITER
        row = self.queued()
        self.assertEqual(row["title"], "Board tour")
        videos.work_one(self.cfg, gh, self.db, row, lambda: 2000.0, run, write=write)
        row = videos.get(self.db, row["id"])
        self.assertEqual((row["status"], row["narration"], row["mode"]), ("ready", "writer", "ui"))
        self.assertIn("o/r#3:home-desktop", seen["prompt"])
        self.assertIn("<brief>", seen["prompt"])
        self.assertEqual(gh.comments, [])
        self.assertEqual(run.seen["img"], ["s03.png"])
        notes = [t for t in F.turns(self.db) if t["video_id"] == row["id"]]
        self.assertIn("is ready", notes[0]["body"])

    def test_without_a_usable_writer_the_wording_is_fixed(self):
        row = self.queued()
        videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, self.container(), write=lambda p: "no block")
        row = videos.get(self.db, row["id"])
        self.assertEqual((row["status"], row["narration"]), ("ready", "fixed"))

    def test_a_failed_container_is_skipped_with_a_note(self):
        row = self.queued()
        videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, self.container(code=1), write=None)
        self.assertEqual(videos.get(self.db, row["id"])["status"], "skipped")
        self.assertIn("was not made", F.turns(self.db)[-1]["body"])

    def test_mark_stale_never_reads_a_brief_video(self):
        row = self.queued()
        self.db.execute("UPDATE review_videos SET status='ready', head_sha='old' WHERE id=?", (row["id"],))
        self.db.commit()

        class Boom(FakeGH):
            def get_pr(self, repo, n):
                raise AssertionError("read a PR")
        self.assertEqual(videos.mark_stale(Boom(), self.db, 99999.0), 0)
        self.assertEqual(videos.get(self.db, row["id"])["status"], "ready")

    def test_make_again_queues_once(self):
        row = self.queued()
        self.db.execute("UPDATE review_videos SET status='ready' WHERE id=?", (row["id"],))
        self.db.commit()
        self.assertIsNotNone(videos.again_brief(self.db, row["id"], 3000.0))
        self.assertIsNone(videos.again_brief(self.db, row["id"], 3001.0))


class Page(UiCase):
    def on(self, videos_on=True):
        overrides_path(str(self.root / "config.toml")).write_text(
            f"[general]\ndry_run = false\n[chat]\nfactory_enabled = true\n[videos]\nenabled = {'true' if videos_on else 'false'}\n")

    def rw(self):
        db = sqlite3.connect(self.db_path)
        F.ensure_tables(db)
        return db

    def proposal(self):
        db = self.rw()
        F.add_reply(db, {"id": F.add_person(db, "make a video")}, block(), None, refs=REFS)
        return F.turns(db)[-1]["id"]

    def test_confirm_queues_exactly_once(self):
        self.on()
        cookie, csrf = self.session()
        tid = self.proposal()
        html = self.req("GET", "/chat", cookie=cookie)[2]
        self.assertIn("Proposed video: Board tour", html)
        self.assertIn('action="/chat/video/make"', html)
        for _ in range(2):
            s, h, _ = self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid), ("voice", "")]), cookie=cookie)
            self.assertEqual((s, h["Location"]), (303, "/chat"))
        rows = videos.briefs(self.rw())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["title"], json.loads(rows[0]["sources"])), ("Board tour", [["o/r", 3, "Board drag"]]))
        self.assertIn("Video queued", self.req("GET", "/chat", cookie=cookie)[2])

    def test_dismiss_and_videos_off(self):
        self.on(videos_on=False)
        cookie, csrf = self.session()
        tid = self.proposal()
        self.assertNotIn('action="/chat/video/make"', self.req("GET", "/chat", cookie=cookie)[2])
        self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid)]), cookie=cookie)
        self.assertEqual(videos.briefs(self.rw()), [])
        self.on()
        self.req("POST", "/chat/video/dismiss", urlencode([("csrf", csrf), ("id", tid)]), cookie=cookie)
        self.assertEqual(F.turns(self.rw())[-1]["proposal_state"], "dismissed")

    def test_the_videos_page_lists_chat_videos(self):
        self.on()
        cookie, csrf = self.session()
        self.assertIn("No videos yet", self.req("GET", "/videos", cookie=cookie)[2])
        db = self.rw()
        vid = videos.queue_brief(db, "Board tour", "Show it.", [("o/r", 3, "Board drag")], time.time())
        db.execute("UPDATE review_videos SET status='skipped', reason='daily_cap' WHERE id=?", (vid,))
        db.commit()
        s, _, html = self.req("GET", "/videos", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Board tour", html)
        self.assertIn("o/r#3", html)
        self.assertIn("Daily limit", html)
        self.assertIn('action="/videos/again"', html)
        self.assertEqual(self.req("GET", "/videos")[0], 303)                  # behind the login


if __name__ == "__main__":
    unittest.main()
