import http.client
import http.server
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

spec = importlib.util.spec_from_file_location("publish_release", Path(__file__).resolve().parent.parent / "scripts" / "publish-release.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


class Github:
    """The part of GitHub's release API the script uses: the releases, the calls it received, and two ways to make it misbehave."""
    def __init__(self):
        self.releases, self.calls, self.drop_upload_of, self.refuse = [], [], None, None


class Fake(http.server.BaseHTTPRequestHandler):
    gh = Github()

    def reply(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def release(self, rid):
        return next(r for r in self.gh.releases if r["id"] == rid)

    def do_GET(self):
        self.gh.calls.append(("GET", self.path, None, self.headers.get("Authorization")))
        self.reply(200, self.gh.releases)

    def do_POST(self):
        raw, path = self.body(), urllib.parse.urlsplit(self.path)
        self.gh.calls.append(("POST", self.path, raw, self.headers.get("Authorization")))
        if self.gh.refuse:
            return self.reply(self.gh.refuse, {"message": "refused"})
        if path.path.endswith("/releases"):
            data = json.loads(raw)
            rel = {"id": 7, "tag_name": data["tag_name"], "draft": data.get("draft", False), "assets": [],
                   "html_url": "https://github.com/o/r/releases/tag/" + data["tag_name"], "created": data}
            self.gh.releases.insert(0, rel)
            return self.reply(201, rel)
        name = urllib.parse.parse_qs(path.query)["name"][0]
        if name == self.gh.drop_upload_of:                  # the connection dies after the request, with no answer (what GitHub did)
            self.gh.drop_upload_of = None
            self.close_connection = True
            return self.connection.close()
        rel = self.release(int(path.path.split("/")[5]))
        rel["assets"].append({"id": len(rel["assets"]) + 100, "name": name, "state": "uploaded"})
        self.reply(201, {"name": name})

    def do_PATCH(self):
        raw = self.body()
        self.gh.calls.append(("PATCH", self.path, raw, self.headers.get("Authorization")))
        rel = self.release(int(self.path.split("/")[-1]))
        rel.update(json.loads(raw))
        self.reply(200, rel)

    def do_DELETE(self):
        self.gh.calls.append(("DELETE", self.path, None, self.headers.get("Authorization")))
        aid = int(self.path.split("/")[-1])
        for r in self.gh.releases:
            r["assets"] = [a for a in r["assets"] if a["id"] != aid]
        self.reply(200, {})

    def log_message(self, *a): pass


class Publish(unittest.TestCase):
    def setUp(self):
        Fake.gh = self.gh = Github()
        srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        old = (pr.API, pr.UPLOADS, pr.PAUSE)
        pr.API, pr.UPLOADS, pr.PAUSE = base, base, 0
        self.addCleanup(lambda: (setattr(pr, "API", old[0]), setattr(pr, "UPLOADS", old[1]), setattr(pr, "PAUSE", old[2])))
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        (self.dir / "VERSION").write_text("1.2.3\n"); (self.dir / "update.sh").write_text("#!/bin/sh\n")

    def uploaded(self):
        return [p.split("name=")[1] for m, p, *_ in self.gh.calls if m == "POST" and "name=" in p]

    def test_creates_a_draft_uploads_each_file_then_publishes_it(self):
        url = pr.publish(self.dir, "o/r", "v1.2.3", "tok")
        self.assertEqual(url, "https://github.com/o/r/releases/tag/v1.2.3")
        self.assertEqual([(m, p) for m, p, *_ in self.gh.calls], [
            ("GET", "/repos/o/r/releases?per_page=100"), ("POST", "/repos/o/r/releases"),
            ("POST", "/repos/o/r/releases/7/assets?name=VERSION"), ("POST", "/repos/o/r/releases/7/assets?name=update.sh"),
            ("PATCH", "/repos/o/r/releases/7")])
        self.assertTrue(all(c[3] == "Bearer tok" for c in self.gh.calls))
        made = self.gh.releases[0]["created"]
        self.assertEqual((made["tag_name"], made["draft"]), ("v1.2.3", True))        # nothing is public until every file is up
        self.assertNotIn("target_commitish", made)
        self.assertFalse(self.gh.releases[0]["draft"])

    def test_tags_the_given_commit(self):
        pr.publish(self.dir, "o/r", "v1.2.3", "tok", "abc123")
        self.assertEqual(self.gh.releases[0]["created"]["target_commitish"], "abc123")

    def test_resumes_a_release_that_stopped_halfway(self):
        self.gh.releases.append({"id": 7, "tag_name": "v1.2.3", "draft": False, "html_url": "https://x/y", "assets": [
            {"id": 1, "name": "VERSION", "state": "uploaded"}, {"id": 2, "name": "update.sh", "state": "starter"}]})
        pr.publish(self.dir, "o/r", "v1.2.3", "tok")
        self.assertEqual(self.uploaded(), ["update.sh"])                     # the file that arrived is not sent again
        steps = [(m, p) for m, p, *_ in self.gh.calls]
        self.assertIn(("DELETE", "/repos/o/r/releases/assets/2"), steps)     # the half-sent one is cleared first
        self.assertNotIn(("POST", "/repos/o/r/releases"), steps)             # and no second release is made
        self.assertEqual(sorted(a["name"] for a in self.gh.releases[0]["assets"]), ["VERSION", "update.sh"])
        self.assertNotIn("PATCH", [m for m, _ in steps])                     # already public: nothing to publish

    def test_a_dropped_connection_is_retried_from_where_it_stopped(self):
        self.gh.drop_upload_of = "update.sh"
        pr.publish_with_retries(self.dir, "o/r", "v1.2.3", "tok")
        self.assertEqual(self.uploaded(), ["VERSION", "update.sh", "update.sh"])        # VERSION once, the dropped one twice
        self.assertEqual(len(self.gh.releases), 1)
        self.assertEqual(sorted(a["name"] for a in self.gh.releases[0]["assets"]), ["VERSION", "update.sh"])
        self.assertFalse(self.gh.releases[0]["draft"])

    def test_a_refusal_is_not_retried(self):
        self.gh.refuse = 403
        with self.assertRaises(urllib.error.HTTPError) as cm:
            pr.publish_with_retries(self.dir, "o/r", "v1.2.3", "tok")
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(len([1 for m, *_ in self.gh.calls if m == "POST"]), 1)

    def test_only_connection_problems_and_server_errors_count_as_transient(self):
        def http_error(code):
            return urllib.error.HTTPError("u", code, "m", {}, None)
        self.assertTrue(pr.transient(http.client.RemoteDisconnected("x")))
        self.assertTrue(pr.transient(TimeoutError()))
        self.assertTrue(pr.transient(http_error(502)))
        self.assertFalse(pr.transient(http_error(422)))
        self.assertFalse(pr.transient(http_error(403)))
        self.assertFalse(pr.transient(KeyError("x")))


if __name__ == "__main__":
    unittest.main()
