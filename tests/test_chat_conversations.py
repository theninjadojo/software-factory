import sqlite3
import time
import unittest
from urllib.parse import urlencode

from factory import factorychat as F
from factory import videos
from factory.config import overrides_path
from test_chatvideos import REFS, block
from test_ui import UiCase


def memdb():
    db = sqlite3.connect(":memory:")
    F.ensure_tables(db)
    return db


class Store(unittest.TestCase):
    def test_turns_from_before_conversations_become_one_conversation(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE factory_chat (id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL, body TEXT NOT NULL, "
                   "created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'done', detail TEXT NOT NULL DEFAULT '', reply_to INTEGER, "
                   "run_id INTEGER, snapshot_at REAL, snapshot_cut INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO factory_chat (author, body, created) VALUES ('person','old question',10)")
        db.execute("INSERT INTO factory_chat (author, body, created) VALUES ('agent','old answer',20)")
        F.ensure_tables(db)
        F.ensure_tables(db)                                                 # a second start changes nothing
        ts = F.threads(db)
        self.assertEqual([(t["title"], t["turns"]) for t in ts], [("Earlier conversation", 2)])
        self.assertEqual([t["body"] for t in F.turns(db, ts[0]["id"])], ["old question", "old answer"])

    def test_the_prompt_only_carries_its_own_conversation(self):
        db = memdb()
        a, b = F.new_thread(db, "about tickets"), F.new_thread(db, "about settings")
        F.add_reply(db, F.claim(db) or {"id": F.add_person(db, "Which ticket is stuck?", a)}, "Ticket 4.", None)
        F.claim(db)
        t = F.add_person(db, "What does dry run do?", b)
        said = " ".join(text for _, text in F.prompt_turns(db, 20, t))
        self.assertIn("dry run", said)
        self.assertNotIn("stuck", said)
        self.assertNotIn("Ticket 4", said)

    def test_one_conversation_waiting_does_not_block_another(self):
        db = memdb()
        a, b = F.new_thread(db, "a"), F.new_thread(db, "b")
        F.add_person(db, "a", a)
        self.assertTrue(F.busy(db, a))
        self.assertFalse(F.busy(db, b))

    def test_delete_refuses_a_waiting_conversation_and_others_keeps_the_current_one(self):
        db = memdb()
        a, b, c = F.new_thread(db, "a"), F.new_thread(db, "b"), F.new_thread(db, "c")
        F.add_reply(db, {"id": F.add_person(db, "a", a)}, "done", None)
        F.add_person(db, "b", b)                                            # waiting for its reply
        F.add_reply(db, {"id": F.add_person(db, "c", c)}, "done", None)
        self.assertFalse(F.delete_thread(db, b))
        self.assertEqual(F.delete_idle_threads(db, keep=c), 1)
        self.assertEqual(sorted(t["id"] for t in F.threads(db)), [b, c])
        self.assertTrue(F.delete_thread(db, c))
        self.assertEqual(F.turns(db, c), [])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM factory_chat WHERE thread_id=?", (c,)).fetchone()[0], 0)

    def test_a_reply_to_a_deleted_conversation_is_dropped(self):
        db = memdb()
        a = F.new_thread(db, "a")
        turn = F.claim(db) or {"id": F.add_person(db, "a", a)}
        db.execute("UPDATE factory_chat SET status='done' WHERE id=?", (turn["id"],))
        F.delete_thread(db, a)
        self.assertEqual(F.add_reply(db, turn, "late", None), 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM factory_chat").fetchone()[0], 0)

    def test_video_notes_go_to_the_conversation_that_asked(self):
        db = memdb()
        a, b = F.new_thread(db, "a"), F.new_thread(db, "b")
        F.add_reply(db, {"id": F.add_person(db, "other topic", b)}, "ok", None)
        turn = F.add_reply(db, {"id": F.add_person(db, "make a video", a)}, block(), None, refs=REFS)
        vid = videos.queue_brief(db, "Board tour", "Show it.", [("o/r", 3, "Board drag")], time.time())
        F.link_video(db, turn, vid)
        self.assertIsNotNone(F.add_note(db, "The video is ready.", vid))
        self.assertEqual(F.turns(db, a)[-1]["body"], "The video is ready.")
        self.assertNotIn("The video is ready.", [t["body"] for t in F.turns(db, b)])
        F.delete_thread(db, a)
        self.assertIsNone(F.add_note(db, "Again.", vid))                   # its conversation is gone: no note anywhere


class Page(UiCase):
    def on(self, videos_on=True):
        overrides_path(str(self.root / "config.toml")).write_text(
            f"[general]\ndry_run = false\n[chat]\nfactory_enabled = true\n[videos]\nenabled = {'true' if videos_on else 'false'}\n")

    def rw(self):
        db = sqlite3.connect(self.db_path)
        F.ensure_tables(db)
        return db

    def post(self, cookie, csrf, path, **fields):
        return self.req("POST", path, urlencode([("csrf", csrf)] + list(fields.items())), cookie=cookie)

    def test_conversations_are_listed_opened_and_started(self):
        self.on()
        cookie, csrf = self.session()
        self.assertIn("No conversations yet", self.req("GET", "/chat", cookie=cookie)[2])
        s, h, _ = self.post(cookie, csrf, "/chat/send", text="Why is #4 stuck?")
        db = self.rw()
        a = F.threads(db)[0]["id"]
        self.assertEqual(h["Location"], f"/chat?c={a}")
        F.add_reply(db, F.claim(db), "It waits for you.", None)
        html = self.req("GET", "/chat", cookie=cookie)[2]                   # opens the latest
        self.assertIn("Why is #4 stuck?", html)
        self.assertIn(f'href="/chat?c={a}" class=active', html)
        new = self.req("GET", "/chat?c=new", cookie=cookie)[2]
        self.assertIn("Start a new conversation", new)
        self.assertNotIn("It waits for you.", new.split('class="fc-main"')[1])
        self.post(cookie, csrf, "/chat/send", text="What does dry run do?")
        self.assertEqual(len(F.threads(db)), 2)
        self.assertIn(f'name="c" value="{a}"', self.req("GET", f"/chat?c={a}", cookie=cookie)[2])

    def test_deleting_needs_the_token_and_a_waiting_conversation_stays(self):
        self.on()
        cookie, csrf = self.session()
        db = self.rw()
        a, b = F.new_thread(db, "a"), F.new_thread(db, "b")
        F.add_reply(db, {"id": F.add_person(db, "a", a)}, "done", None)
        F.add_person(db, "b", b)
        self.assertEqual(self.req("POST", "/chat/delete", urlencode([("c", a)]), cookie=cookie)[0], 403)
        self.assertIn("Delete this conversation", self.req("GET", f"/chat?c={a}", cookie=cookie)[2])
        self.assertNotIn("Delete this conversation", self.req("GET", f"/chat?c={b}", cookie=cookie)[2])
        self.post(cookie, csrf, "/chat/delete", c=b)
        self.assertIn("waiting for a reply", self.req("GET", "/chat", cookie=cookie)[2])
        s, h, _ = self.post(cookie, csrf, "/chat/delete", c=a)
        self.assertEqual(h["Location"], "/chat")
        self.assertEqual([t["id"] for t in F.threads(db)], [b])
        c = F.new_thread(db, "c")
        self.post(cookie, csrf, "/chat/delete-others", c=c)
        self.assertEqual(sorted(t["id"] for t in F.threads(db)), [b, c])   # b still waits

    def test_the_videos_box_starts_a_conversation_asking_for_a_proposal(self):
        self.on()
        cookie, csrf = self.session()
        html = self.req("GET", "/videos", cookie=cookie)[2]
        self.assertIn('action="/videos/ask"', html)
        s, h, _ = self.post(cookie, csrf, "/videos/ask", text="A tour of the board changes, for the team.")
        db = self.rw()
        t = F.threads(db)[0]
        self.assertEqual(h["Location"], f"/videos?c={t['id']}")                  # the request stays on the Videos page
        self.assertEqual(F.turns(db, t["id"])[0]["body"], "Propose a video: A tour of the board changes, for the team.")
        html = self.req("GET", h["Location"], cookie=cookie)[2]
        self.assertIn("Your video request", html)
        self.assertIn("A tour of the board changes", html)
        self.assertIn(f'data-src="/fragment/factory-chat?c={t["id"]}&amp;in=videos"', html)
        self.post(cookie, csrf, "/videos/ask", text="")
        self.assertEqual(len(F.threads(db)), 1)

    def test_the_videos_box_is_refused_while_videos_are_off(self):
        self.on(videos_on=False)
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/videos/ask", text="A tour")
        self.assertEqual(F.threads(self.rw()), [])


if __name__ == "__main__":
    unittest.main()
