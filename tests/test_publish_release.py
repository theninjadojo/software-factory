import http.server
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("publish_release", Path(__file__).resolve().parent.parent / "scripts" / "publish-release.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


class Fake(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Fake.seen.append((self.path, self.headers.get("Authorization"), body))
        out = json.dumps({"id": 7, "html_url": "https://github.com/o/r/releases/tag/v1.2.3"}).encode()
        self.send_response(201); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a): pass


class Publish(unittest.TestCase):
    def test_creates_the_release_then_uploads_each_file(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        pr.API, pr.UPLOADS = base, base
        Fake.seen.clear()
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "VERSION").write_text("1.2.3\n"); (Path(d) / "update.sh").write_text("#!/bin/sh\n")
            url = pr.publish(Path(d), "o/r", "v1.2.3", "tok")
        self.assertEqual(url, "https://github.com/o/r/releases/tag/v1.2.3")
        self.assertEqual([p for p, _, _ in Fake.seen], ["/repos/o/r/releases", "/repos/o/r/releases/7/assets?name=VERSION", "/repos/o/r/releases/7/assets?name=update.sh"])
        self.assertTrue(all(a == "Bearer tok" for _, a, _ in Fake.seen))
        self.assertEqual(json.loads(Fake.seen[0][2])["tag_name"], "v1.2.3")


if __name__ == "__main__":
    unittest.main()
