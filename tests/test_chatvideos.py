"""Videos asked for in the factory chat: the proposal, confirming it, the writer's scenes, and the Videos page."""
import json
from dataclasses import replace
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
                             "tickets": [["o/r", 3, "Board drag"], ["o/r", 4, "Login fix"]], "files": []})

    def test_extra_keys_unknown_tickets_and_videos_off_are_dropped(self):
        self.assertTrue(F.parse_proposal(block(show_code=True), REFS)[1]["show_code"])
        self.assertNotIn("show_code", F.parse_proposal(block(show_code=False), REFS)[1])
        for text in (block(voice="x"), block(show_code="yes"), block(tickets=["o/r#99"]), block(tickets=[]), block(tickets=["o/r#3"] * 9),
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
        c = F.new_thread(db, "make a video")
        F.add_reply(db, {"id": F.add_person(db, "make a video", c)}, block(), None, refs=REFS)
        t = F.turns(db, c)[-1]
        self.assertEqual((t["proposal_state"], t["proposal"]["title"]), ("open", "Board tour"))
        self.assertTrue(F.set_proposal_state(db, t["id"], "open", "confirmed"))
        self.assertFalse(F.set_proposal_state(db, t["id"], "open", "confirmed"))
        self.assertIn("switched off", F.build_prompt("snap", [], videos=False))
        self.assertIn("factory-proposal", F.build_prompt("snap", [], videos=True))
        self.assertNotIn("VIDEOS.", F.build_prompt("snap", []))
        on = F.build_prompt("snap", [], videos=True, screens=["o/r: login"])
        self.assertIn("do not propose it and do not offer a lesser video", on)
        self.assertIn("- o/r: login", on)
        self.assertIn("(none)", F.build_prompt("snap", [], videos=True))
        self.assertNotIn("MISSING.", F.build_prompt("snap", [], videos=False))


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
        F.ensure_tables(self.db)
        self.thread = F.new_thread(self.db, "make a video")
        turn = F.add_reply(self.db, {"id": F.add_person(self.db, "make a video", self.thread)}, block(), None, refs=REFS)
        F.link_video(self.db, turn, vid)                                       # as confirming the proposal does
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
        self.assertEqual(run.seen["img"], ["s02.png"])                         # the code scene was dropped: no code was asked for
        notes = [t for t in F.turns(self.db, self.thread) if t["video_id"] == row["id"] and not t["proposal"]]
        self.assertIn("is ready", notes[0]["body"])

    def test_without_a_usable_writer_it_is_skipped_and_said(self):
        title_only = '```factory-video\n{"scenes": [{"kind": "title", "title": "T", "say": "S."}, {"kind": "points", "title": "P", "points": ["a"], "say": "S."}]}\n```'
        for out in ("no block", title_only, None):
            row = self.queued()
            videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, self.container(),
                            write=None if out is None else (lambda p, out=out: out))
            row = videos.get(self.db, row["id"])
            self.assertEqual((row["status"], row["reason"]), ("skipped", "writer_failed"), out)
            self.assertIn("did not write a usable video", F.turns(self.db, self.thread)[-1]["body"])

    def test_a_failed_container_is_skipped_with_a_note(self):
        row = self.queued()
        videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, self.container(code=1), write=None)
        self.assertEqual(videos.get(self.db, row["id"])["status"], "skipped")
        self.assertIn("was not made", F.turns(self.db, self.thread)[-1]["body"])

    def test_mark_stale_never_reads_a_brief_video(self):
        row = self.queued()
        self.db.execute("UPDATE review_videos SET status='ready', head_sha='old' WHERE id=?", (row["id"],))
        self.db.commit()

        class Boom(FakeGH):
            def get_pr(self, repo, n):
                raise AssertionError("read a PR")
        self.assertEqual(videos.mark_stale(Boom(), self.db, 99999.0), 0)
        self.assertEqual(videos.get(self.db, row["id"])["status"], "ready")

    def test_code_is_shown_only_when_asked_for(self):
        mat = videos.material(FakeGH(), self.db, [("o/r", 3, "Board drag")])
        self.assertEqual([s["kind"] for s in videos.brief_scenes(WRITER, {**mat, "code": False})], ["title", "shot"])
        self.assertEqual([s["kind"] for s in videos.brief_scenes(WRITER, {**mat, "code": True})], ["title", "code", "shot"])
        row = {"title": "Tour", "brief": "Show it.", "files": "[]"}
        self.assertIn("SCREENS, NOT CODE", videos.writer_prompt(self.cfg.videos, row, mat))
        self.assertNotIn('"kind": "code"', videos.writer_prompt(self.cfg.videos, row, mat))
        self.assertIn('"kind": "code"', videos.writer_prompt(self.cfg.videos, {**row, "show_code": 1}, mat))
        vid = videos.queue_brief(self.db, "Tour", "Show it.", [("o/r", 3, "Board drag")], 1000.0, show_code=True)
        self.db.execute("UPDATE review_videos SET status='ready' WHERE id=?", (vid,))
        self.assertEqual(videos.get(self.db, videos.again_brief(self.db, vid, 2000.0))["show_code"], 1)

    def test_the_screens_board_shows_the_app_as_it_is_now(self):
        from factory import screenboard
        from factory.config import ScreenPage, ScreensCfg
        screenboard.ensure_tables(self.db)
        sha = "a" * 40
        for repo, page, view in (("o/r", "login", "design"), ("o/r", "login", "desktop"), ("o/r", "login", "mobile"),
                                 ("x/y", "home", "desktop")):
            screenboard.store(self.db, repo, page, view, sha, png())
        sc = ScreensCfg(pages=(ScreenPage("o/r", "login", "site/login.html"), ScreenPage("x/y", "home", "site/index.html")))
        board = videos.board_material(self.db, sc, ["o/r"])
        self.assertEqual(list(board["shots"]), ["board:o/r:login"])           # one view per page, never the design, the video's repo
        self.assertEqual(board["titles"]["board:o/r:login"], "login screen")
        self.assertEqual(list(videos.board_material(self.db, sc, ["z/z"])["shots"]), ["board:x/y:home", "board:o/r:login"])   # the board's order
        self.assertEqual(videos.board_material(self.db, None, ["o/r"])["shots"], {})
        gh, run, seen = FakeGH(), self.container(), {}
        row = self.queued()
        cfg = replace(self.cfg, screens=sc)
        writer = WRITER.replace("x/y#9:home-desktop", "board:o/r:login")
        videos.work_one(cfg, gh, self.db, row, lambda: 2000.0, run, write=lambda p: seen.setdefault("prompt", p) and writer)
        self.assertIn("- board:o/r:login: login screen", seen["prompt"])
        self.assertEqual([s.get("title") for s in run.seen["script"]["scenes"] if s["kind"] == "shot"], ["home desktop screen", "login screen"])

    def test_with_nothing_to_show_it_is_skipped_and_said(self):
        self.db.execute("DELETE FROM run_images")
        row = self.queued()
        videos.work_one(self.cfg, FakeGH(), self.db, row, lambda: 2000.0, self.container(), write=lambda p: WRITER)
        self.assertEqual(videos.get(self.db, row["id"])["reason"], "no_screens")
        self.assertIn("no screens to show", F.turns(self.db, self.thread)[-1]["body"])

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
        self.thread = F.new_thread(db, "make a video")
        F.add_reply(db, {"id": F.add_person(db, "make a video", self.thread)}, block(), None, refs=REFS)
        return F.turns(db, self.thread)[-1]["id"]

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
        self.assertEqual(rows[0]["show_code"], 0)                              # the box was not ticked

    def test_the_show_code_box_follows_the_proposal_and_the_person(self):
        self.on()
        cookie, csrf = self.session()
        db = self.rw()
        thread = F.new_thread(db, "show me the login code")
        F.add_reply(db, {"id": F.add_person(db, "show me the login code", thread)}, block(show_code=True), None, refs=REFS)
        tid = F.turns(db, thread)[-1]["id"]
        self.assertIn('name="show_code" value="1" checked', self.req("GET", f"/chat?c={thread}", cookie=cookie)[2])
        self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid), ("c", thread), ("show_code", "1")]), cookie=cookie)
        self.assertEqual(videos.briefs(self.rw())[0]["show_code"], 1)

    def test_dismiss_and_videos_off(self):
        self.on(videos_on=False)
        cookie, csrf = self.session()
        tid = self.proposal()
        self.assertNotIn('action="/chat/video/make"', self.req("GET", "/chat", cookie=cookie)[2])
        self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid)]), cookie=cookie)
        self.assertEqual(videos.briefs(self.rw()), [])
        self.on()
        self.req("POST", "/chat/video/dismiss", urlencode([("csrf", csrf), ("id", tid)]), cookie=cookie)
        self.assertEqual(F.turns(self.rw(), self.thread)[-1]["proposal_state"], "dismissed")

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

    def test_the_menu_offers_videos_on_every_page_only_while_they_are_on(self):
        cookie, _ = self.session()
        self.on(videos_on=False)
        for path in ("/", "/tickets", "/chat", "/settings"):
            self.assertNotIn('href="/videos"', self.req("GET", path, cookie=cookie)[2], path)
        self.on()
        for path in ("/", "/tickets", "/chat", "/settings", "/events"):
            self.assertIn('href="/videos"', self.req("GET", path, cookie=cookie)[2], path)


if __name__ == "__main__":
    unittest.main()
