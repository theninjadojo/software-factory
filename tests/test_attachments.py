"""Attachments of local tickets: what is accepted, the limits, the all-or-nothing rules, and that downloads and the upload form
cannot be turned against the admin origin."""
import http.client
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from factory import backup, tracker
from factory.config import load
from factory.ui import labels as L

from test_local_tickets_ui import REPO
from test_ui_admin import AdminCase

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
LIM = (1024, 3, 2048)


def store():
    return tracker.LocalTracker(sqlite3.connect(":memory:"))


class Rules(unittest.TestCase):
    def test_names_are_made_safe(self):
        self.assertEqual(tracker.safe_name("../../etc/pa ss<wd>.png"), "pa_ss_wd_.png")
        self.assertEqual(tracker.safe_name("C:\\x\\..\\shot.PNG"), "shot.PNG")
        self.assertEqual(tracker.safe_name(".htaccess"), "htaccess")
        self.assertLessEqual(len(tracker.safe_name("a" * 500 + ".png")), tracker.MAX_NAME)
        self.assertTrue(tracker.safe_name("a" * 500 + ".png").endswith(".png"))

    def test_type_comes_from_the_extension_and_the_content_must_match(self):
        self.assertEqual(tracker.check_attachment("a.png", PNG, 1000), ("a.png", "image/png"))
        self.assertEqual(tracker.check_attachment("n.md", b"# hi", 1000)[1], tracker.TEXT_MIME)
        for name, data in (("a.svg", b"<svg onload=x>"), ("a.html", b"<b>"), ("a.png", b"<html>"), ("a.txt", b"\xff\xfe"),
                           ("a.txt", b"a\0b"), ("a.pdf", b"nope"), ("noext", b"x"), ("a.png", b"")):
            with self.assertRaises(tracker.AttachmentError, msg=name):
                tracker.check_attachment(name, data, 1000)
        with self.assertRaises(tracker.AttachmentError):
            tracker.check_attachment("a.txt", b"x" * 1001, 1000)

    def test_limits_count_what_the_ticket_already_has(self):
        t = store()
        n = t.create("o/r", "T")
        t.add_attachments("o/r", n, [("a.txt", b"x" * 900), ("b.txt", b"y" * 900)], limits=LIM)
        with self.assertRaises(tracker.AttachmentError):                                 # 2700 > 2048 in all
            t.add_attachments("o/r", n, [("c.txt", b"z" * 900)], limits=LIM)
        with self.assertRaises(tracker.AttachmentError):                                 # four files
            t.add_attachments("o/r", n, [("c.txt", b"1"), ("d.txt", b"1")], limits=LIM)
        self.assertEqual([a["name"] for a in t.attachments("o/r", n)], ["a.txt", "b.txt"])

    def test_a_ticket_is_created_with_all_its_files_or_not_at_all(self):
        t = store()
        with self.assertRaises(tracker.AttachmentError):
            t.create("o/r", "T", files=[("a.txt", b"ok"), ("b.exe", b"MZ")])
        self.assertIsNone(t.db.execute("SELECT 1 FROM local_tickets").fetchone())
        n = t.create("o/r", "T", files=[("a.txt", b"ok")])
        meta, data = t.attachment("o/r", n, t.attachments("o/r", n)[0]["id"])
        self.assertEqual((meta["name"], meta["mime"], data), ("a.txt", tracker.TEXT_MIME, b"ok"))

    def test_attachments_belong_to_their_ticket_and_can_be_removed(self):
        t = store()
        a, b = t.create("o/r", "A", files=[("a.txt", b"1")]), t.create("o/r", "B")
        att = t.attachments("o/r", a)[0]["id"]
        with self.assertRaises(LookupError):
            t.attachment("o/r", b, att)
        self.assertFalse(t.delete_attachment("o/r", b, att))
        self.assertTrue(t.delete_attachment("o/r", a, att))
        self.assertEqual(t.attachments("o/r", a), [])


class Parts(unittest.TestCase):
    def body(self, parts):
        b = "XB"
        out = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{n}"{f}\r\n\r\n'.encode() + d + b"\r\n" for n, f, d in parts)
        return out + f"--{b}--\r\n".encode(), "multipart/form-data; boundary=XB"

    def read(self, parts, **kw):
        raw, ct = self.body(parts)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b"
            p.write_bytes(raw)
            fields, files = backup.read_parts(p, ct, **kw)
            return fields, [(n, raw[a:b]) for n, (a, b) in files]

    def test_fields_and_several_files(self):
        fields, files = self.read([("title", "", b"T"), ("file", '; filename="a.txt"', b"one"), ("file", '; filename="b.txt"', b"two"),
                                   ("file", '; filename=""', b""), ("other", '; filename="c.txt"', b"ignored")], max_files=2)
        self.assertEqual((fields, files), ({"title": "T"}, [("a.txt", b"one"), ("b.txt", b"two")]))

    def test_too_many_files(self):
        with self.assertRaises(backup.BackupError):
            self.read([("file", '; filename="a"', b"1"), ("file", '; filename="b"', b"2")], max_files=1)


class Upload(AdminCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + "\n[local]\nenabled = true\nattach_max_mb = 1\nattach_max_files = 2\nattach_max_total_mb = 2\n")
        self.cookie, self.csrf = self.session()
        L._recent.clear()
        self.ltr()                                  # make sure the tables exist

    def upload(self, path, fields, files, csrf=None):
        fields = {"csrf": self.csrf if csrf is None else csrf, **fields}
        b = "SFB"
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
        for name, data in files:
            body += f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: text/html\r\n\r\n'.encode() + data + b"\r\n"
        body += f"--{b}--\r\n".encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=body, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": self.cookie,
                                                    "Content-Type": f"multipart/form-data; boundary={b}"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def make(self, files, **over):
        return self.upload("/tickets/create", {"repo": REPO, "title": "With files", "body": "see", "start": "", "where": "local", **over}, files)

    def ltr(self):
        return tracker.LocalTracker(self.db)

    N = tracker.LOCAL_BASE + 1

    def test_create_with_attachments(self):
        self.assertEqual(self.make([("log.txt", b"boom"), ("shot.png", PNG)]), 303)
        self.assertEqual([(a["name"], a["size"]) for a in self.ltr().attachments(REPO, self.N)], [("log.txt", 4), ("shot.png", len(PNG))])

    def test_one_bad_file_creates_nothing(self):
        self.assertEqual(self.make([("log.txt", b"boom"), ("x.svg", b"<svg/>")]), 303)       # back to the page with a message
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_tickets").fetchone())
        self.assertEqual(self.make([("a.txt", b"1"), ("b.txt", b"2"), ("c.txt", b"3")]), 303)
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_tickets").fetchone())
        self.assertEqual(self.make([("big.txt", b"x" * (1024 * 1024 + 1))]), 303)
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_tickets").fetchone())

    def test_files_are_refused_for_a_github_ticket(self):
        self.assertEqual(self.make([("a.txt", b"1")], where="github"), 303)
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_tickets").fetchone())

    def test_the_csrf_token_is_checked_before_anything_is_stored(self):
        self.assertEqual(self.make([("a.txt", b"1")], csrf="bad"), 403)
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_tickets").fetchone())

    def test_attach_to_an_existing_ticket_and_remove(self):
        self.make([])
        self.assertEqual(self.upload("/tickets/local/attach", {"repo": REPO, "n": str(self.N)}, [("n.md", b"# notes")]), 303)
        (a,) = self.ltr().attachments(REPO, self.N)
        self.assertEqual(self.upload("/tickets/local/attach", {"repo": REPO, "n": str(self.N)}, [("1.txt", b"1"), ("2.txt", b"2")]), 303)
        self.assertEqual(len(self.ltr().attachments(REPO, self.N)), 1)                   # over the limit of two: none added
        self.post(self.cookie, self.csrf, "/tickets/local/attachment/delete", {"repo": REPO, "n": str(self.N), "id": str(a["id"])})
        self.assertEqual(self.ltr().attachments(REPO, self.N), [])

    def test_attaching_to_a_missing_ticket_stores_nothing(self):
        self.assertEqual(self.upload("/tickets/local/attach", {"repo": REPO, "n": str(self.N)}, [("n.md", b"x")]), 303)
        self.assertIsNone(self.db.execute("SELECT 1 FROM local_attachments").fetchone())

    def test_a_download_is_always_an_attachment_with_our_type(self):
        self.make([("page.txt", b"<script>alert(1)</script>")])
        att = self.ltr().attachments(REPO, self.N)[0]["id"]
        s, h, body = self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={self.N}&id={att}", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertEqual(h["Content-Type"], tracker.TEXT_MIME)
        self.assertEqual(h["Content-Disposition"], 'attachment; filename="page.txt"')
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={self.N}&id={att + 1}", cookie=self.cookie)[0], 404)
        self.assertEqual(self.req("GET", f"/tickets/local/attachment?repo={quote(REPO, safe='')}&n={self.N}&id={att}")[0], 303)   # no session

    def test_the_ticket_page_lists_files_escaped(self):
        self.make([("a<b>.txt", b"1")])
        html = self.req("GET", f"/ticket?repo={quote(REPO, safe='')}&n={self.N}", cookie=self.cookie)[2]
        self.assertIn("a_b_.txt", html)
        self.assertIn('action="/tickets/local/attach"', html)
        self.assertIsNotNone(re.search(r"/tickets/local/attachment\?repo=[^\"]+&amp;n=\d+&amp;id=\d+", html))

    def test_the_limits_are_settings(self):
        c = load(str(self.root / "config.toml"))
        self.assertEqual((c.attach_max_mb, c.attach_max_files, c.attach_max_total_mb), (1, 2, 2))


if __name__ == "__main__":
    unittest.main()
