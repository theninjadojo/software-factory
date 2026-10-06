import base64
import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

from factory import db as dbm
from factory import jobs, workerapi
from factory.config import Config, WorkerCheck, WorkersCfg, load
from test_render import png

TOKEN = "t" * 32


def cfg_for(tmp: Path, **kw) -> Config:
    (tmp / "tokens").write_text(f"# comment\nmac {TOKEN}\nshort tooshort\nBad_Name {'x' * 30}\n")
    toml = tmp / "config.toml"
    toml.write_text(f'[general]\ndb_path = "{tmp}/f.db"\npoll_seconds = 60\ndry_run = true\nconfidence_threshold = 0.6\n'
                    f'[github]\ntrigger_label = "x"\nrepos = ["o/r"]\ntrusted_permissions = ["write"]\n'
                    f'[routing.low]\nharness="claude-code"\nmodel="haiku"\neffort="low"\n[routing.medium]\nharness="claude-code"\nmodel="sonnet"\neffort="medium"\n'
                    f'[routing.high]\nharness="claude-code"\nmodel="opus"\neffort="high"\n'
                    f'[runner]\nengine="podman"\nimage="i"\nwork_dir="{tmp}/w"\nproxy_socket="{tmp}/p"\nallow_hosts=["api.anthropic.com"]\ntimeout_seconds=60\nmax_turns=5\nclaude_env_file="{tmp}/c"\n'
                    f'[workers]\nenabled = true\ntokens_file = "{tmp}/tokens"\n[[workers.checks]]\nrepo = "o/r"\nrecipe = "ios-test"\nplatform = "macos"\n')
    return replace(load(str(toml)), **kw) if kw else load(str(toml))


class Handle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = cfg_for(self.tmp)
        self.api = workerapi.Api(lambda: self.cfg, clock=lambda: 1000.0)
        self.db = dbm.connect(self.cfg.db_path)
        self.addCleanup(self.db.close)
        self.auth = f"Bearer {TOKEN}"

    def call(self, path, body=None, auth="default", method="POST"):
        return self.api.handle(method, path, self.auth if auth == "default" else auth, json.dumps(body if body is not None else {}).encode())

    def test_token_file_parsing(self):
        self.assertEqual(workerapi.read_tokens(str(self.tmp / "tokens")), {"mac": TOKEN})   # comment, short token and bad name skipped
        self.assertEqual(workerapi.read_tokens(str(self.tmp / "nope")), {})

    def test_auth_required(self):
        for a in (None, "", "Bearer wrong", f"Basic {TOKEN}", "Bearer " + TOKEN[:-1]):
            self.assertEqual(self.call("/v1/claim", {}, auth=a)[0], 401, a)

    def test_disabled_workers_are_not_served(self):
        self.api = workerapi.Api(lambda: replace(self.cfg, workers=WorkersCfg()))
        self.assertEqual(self.call("/v1/claim", {})[0], 404)

    def test_a_claim_stores_the_machines_report_cleaned(self):
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"], "stats": {"disk_free": 5, "disk_total": 10, "junk": "x"}})
        row = self.db.execute("SELECT stats FROM workers WHERE name='mac'").fetchone()
        self.assertEqual(json.loads(row[0]), {"disk_free": 5, "disk_total": 10})
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"]})                 # an older worker: the last report stays
        self.assertEqual(json.loads(self.db.execute("SELECT stats FROM workers WHERE name='mac'").fetchone()[0])["disk_free"], 5)

    def test_claim_heartbeat_result_round_trip(self):
        jid = jobs.enqueue(self.db, "o/r", 7, "a" * 40, "the patch", "ios-test", "macos", 1000)
        self.assertEqual(self.call("/v1/claim", {"platform": "linux", "recipes": ["ios-test"]})[0], 204)
        status, job = self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"]})
        self.assertEqual(status, 200)
        self.assertEqual({k: job[k] for k in ("id", "repo", "base_sha", "patch", "recipe")},
                         {"id": jid, "repo": "o/r", "base_sha": "a" * 40, "patch": "the patch", "recipe": "ios-test"})
        self.assertNotIn("issue", job)
        self.assertEqual(self.call(f"/v1/jobs/{jid}/heartbeat"), (200, {"cancel": False}))
        art = {"name": "home", "png_b64": base64.b64encode(png(20, 20)).decode()}
        self.assertEqual(self.call(f"/v1/jobs/{jid}/result", {"status": "passed", "exit_code": 0, "log": "ok", "artifacts": [art]}), (200, {"ok": True}))
        self.assertEqual(jobs.get(self.db, jid)["status"], "passed")
        self.assertEqual(list(jobs.artifacts(self.db, jid)), ["home"])

    def test_invalid_result_is_a_400_and_fails_the_job(self):
        jid = jobs.enqueue(self.db, "o/r", 7, "a" * 40, "p", "ios-test", "macos", 1000)
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"]})
        status, out = self.call(f"/v1/jobs/{jid}/result", {"status": "passed", "artifacts": [{"name": "a", "png_b64": "AAAA"}]})
        self.assertEqual(status, 400)
        self.assertEqual(jobs.get(self.db, jid)["status"], "error")

    def test_bad_requests(self):
        self.assertEqual(self.call("/v1/claim", {"platform": "mac os", "recipes": []})[0], 400)
        self.assertEqual(self.call("/v1/claim", {"platform": "macos", "recipes": ["Bad Name"]})[0], 400)
        self.assertEqual(self.call("/v1/claim", {"platform": "macos", "recipes": ["a"], "version": 2})[0], 400)
        self.assertEqual(self.api.handle("POST", "/v1/claim", self.auth, b"{nope")[0], 400)
        self.assertEqual(self.api.handle("POST", "/v1/claim", self.auth, b"[]")[0], 400)
        self.assertEqual(self.call("/v1/claim", method="GET")[0], 405)
        self.assertEqual(self.call("/v1/elsewhere")[0], 404)
        self.assertEqual(self.call("/v1/jobs/99/heartbeat")[0], 404)
        self.assertEqual(self.call("/v1/jobs/1/other")[0], 404)

    def test_a_worker_cannot_touch_another_workers_job(self):
        (self.tmp / "tokens").write_text(f"mac {TOKEN}\nother {'o' * 32}\n")
        jid = jobs.enqueue(self.db, "o/r", 7, "a" * 40, "p", "ios-test", "macos", 1000)
        self.call("/v1/claim", {"platform": "macos", "recipes": ["ios-test"]})
        theirs = "Bearer " + "o" * 32
        self.assertEqual(self.call(f"/v1/jobs/{jid}/heartbeat", auth=theirs)[0], 404)
        self.assertEqual(self.call(f"/v1/jobs/{jid}/result", {"status": "passed"}, auth=theirs)[0], 400)
        self.assertEqual(jobs.get(self.db, jid)["status"], "claimed")


class OverHttp(unittest.TestCase):
    def test_real_server(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = cfg_for(tmp)
        dbm.connect(cfg.db_path).close()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), workerapi.make_handler(workerapi.Api(lambda: cfg)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def req(path, body, auth=f"Bearer {TOKEN}", length=None):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            data = json.dumps(body).encode()
            c.putrequest("POST", path)
            c.putheader("Authorization", auth)
            c.putheader("Content-Length", str(len(data) if length is None else length))
            c.endheaders(data if length is None else b"")
            r = c.getresponse()
            out = r.read()
            c.close()
            return r.status, out

        self.assertEqual(req("/v1/claim", {"platform": "macos", "recipes": ["ios-test"]}), (204, b""))
        self.assertEqual(req("/v1/claim", {}, auth="Bearer nope")[0], 401)
        self.assertEqual(req("/v1/claim", {}, length=workerapi.MAX_BODY + 1)[0], 413)


if __name__ == "__main__":
    unittest.main()
