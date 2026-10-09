import http.client
import io
import json
import sqlite3
import threading
import time
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from factory import backup as B
from factory import db as dbm
from test_ui import UiCase

BOUNDARY = "----sfboundary"


def multipart(fields: dict, file: bytes | None = None) -> bytes:
    out = b""
    for k, v in fields.items():
        out += f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    if file is not None:
        out += f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="b.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode() + file + b"\r\n"
    return out + f"--{BOUNDARY}--\r\n".encode()


class BackupCase(UiCase):
    def raw(self, method, path, body=None, cookie=None, ctype="application/x-www-form-urlencoded"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": ctype}
        if cookie:
            h["Cookie"] = cookie
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def seed(self):
        d = self.db
        d.execute("INSERT INTO runs (kind, repo, issue, started, output, log_tail) VALUES ('build','o/r',1,1.0,'SECRETOUTPUT','SECRETLOG')")
        d.execute("INSERT INTO approvals VALUES ('o/r',1,'build',1.0)")
        d.execute("INSERT INTO verify_jobs (repo, issue, base_sha, patch, recipe, created, status, log) VALUES ('o/r',1,'abc','SECRETPATCH','web',1.0,'passed','SECRETJOB')")
        d.execute("INSERT INTO verify_jobs (repo, issue, base_sha, patch, recipe, created, status) VALUES ('o/r',2,'abc','QUEUEDPATCH','web',1.0,'queued')")
        d.execute("INSERT INTO mockup_images VALUES ('o/r','design/x.png',?,1.0)", (b"\x89PNG-bytes",))
        d.execute("INSERT INTO events (ts, kind, message) VALUES (1.0,'startup','hello')")
        d.commit()

    def download(self):
        cookie, csrf = self.session()
        s, h, data = self.raw("POST", "/backup/download", urlencode({"csrf": csrf}), cookie)
        self.assertEqual(s, 200)
        return cookie, csrf, data

    def zip_db(self, data: bytes, to: Path) -> sqlite3.Connection:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            to.write_bytes(z.read("factory.db"))
        return sqlite3.connect(to)


class Download(BackupCase):
    def test_download_needs_a_session_and_a_csrf_token(self):
        self.seed()
        s, h, _ = self.raw("POST", "/backup/download", "x=1")
        self.assertEqual((s, h["Location"]), (303, "/login"))
        cookie, csrf = self.session()
        self.assertEqual(self.raw("POST", "/backup/download", "csrf=wrong", cookie)[0], 403)
        self.assertEqual(self.raw("POST", "/backup/download", "", cookie)[0], 403)
        s, _, body = self.raw("GET", "/backup/download", cookie=cookie)       # a GET never exports anything
        self.assertEqual(s, 404)
        self.assertNotIn(b"PK", body[:2])
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)    # a Host outside the allowlist
        c.request("POST", "/backup/download", body=urlencode({"csrf": csrf}), headers={"Host": "evil.example", "Cookie": cookie})
        self.assertEqual(c.getresponse().status, 421)

    def test_the_backup_is_a_consistent_sanitised_copy(self):
        self.seed()
        _, _, data = self.download()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            manifest = json.loads(z.read("manifest.json"))
        self.assertEqual(names, {"manifest.json", "factory.db", "config.toml"})
        self.assertEqual(manifest["format"], B.FORMAT)
        db = self.zip_db(data, self.root / "copy.db")
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(db.execute("SELECT output, log_tail FROM runs").fetchone(), ("", ""))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT patch, log, status FROM verify_jobs").fetchall(), [("", "", "passed")])     # the queued job is gone
        self.assertEqual(db.execute("SELECT COUNT(*) FROM mockup_images").fetchone()[0], 1)                            # images are kept
        self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        for secret in (b"SECRETOUTPUT", b"SECRETLOG", b"SECRETPATCH", b"SECRETJOB", b"QUEUEDPATCH"):
            self.assertNotIn(secret, (self.root / "copy.db").read_bytes())                                              # not even in free pages
        self.assertFalse((self.root / "copy.db-wal").exists())
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(sqlite3.connect(self.db_path).execute("SELECT output FROM runs").fetchone()[0], "SECRETOUTPUT")      # the live data is untouched

    def test_no_secret_file_or_password_is_included_and_temp_files_are_removed(self):
        secrets = self.root / "secrets"
        secrets.mkdir()
        (secrets / "github_token").write_text("ghp_NOTINTHEBACKUP")
        _, _, data = self.download()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(set(z.namelist()), {"manifest.json", "factory.db", "config.toml"})      # no secrets/, no ui-auth.json
            self.assertNotIn(b"ghp_NOTINTHEBACKUP", b"".join(z.read(n) for n in z.namelist()))
        tmp = self.state / B.TMP_BACKUP
        for _ in range(200):                # the handler removes its work dir and records the backup after the response is sent
            if not any(tmp.iterdir()) and B.last_backup(self.state):
                break
            time.sleep(0.01)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertIsNotNone(B.last_backup(self.state))

    def test_a_backup_taken_while_the_orchestrator_writes_is_consistent(self):
        stop = threading.Event()

        def writer():
            c = dbm.connect(self.db_path)
            i = 0
            while not stop.is_set():
                c.execute("INSERT INTO events (ts, kind, message) VALUES (?, 'x', ?)", (float(i), "m" * 200))
                c.commit()
                i += 1

        t = threading.Thread(target=writer)
        t.start()
        try:
            for n in range(3):
                out = B.snapshot(self.db_path, self.root / f"snap{n}.db")
                db = sqlite3.connect(out)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                db.close()
        finally:
            stop.set()
            t.join()

    def test_the_backup_page_shows_the_size(self):
        cookie, _ = self.session()
        s, _, body = self.req("GET", "/backup", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("The database is", body)
        self.assertIn("Download backup", body)


class Restore(BackupCase):
    def upload(self, cookie, csrf, data, confirm="1", token=None):
        fields = {"csrf": csrf if token is None else token}
        if confirm:
            fields["confirm"] = confirm
        return self.raw("POST", "/backup/restore", multipart(fields, data), cookie, f"multipart/form-data; boundary={BOUNDARY}")

    def test_restore_stages_the_database_and_config_and_pauses(self):
        self.seed()
        cookie, csrf, data = self.download()
        self.db.execute("DELETE FROM events")
        self.db.commit()
        s, h, _ = self.upload(cookie, csrf, data)
        self.assertEqual((s, h["Location"]), (303, "/backup?ok=restore"))
        for name in ("PAUSED", "RESTART", B.MARKER, "restore/factory.db"):
            self.assertTrue((self.state / name).exists(), name)
        self.assertTrue((self.root / "config.toml.bak").exists())
        tmp = self.state / B.TMP_RESTORE
        for _ in range(200):                # the handler removes its upload dir after the response is sent
            if not any(tmp.iterdir()):
                break
            time.sleep(0.01)
        self.assertEqual(list(tmp.iterdir()), [])
        self.assertEqual(sqlite3.connect(self.db_path).execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)      # the live database is not touched yet
        info = B.apply_pending(self.state, self.db_path)                                                              # the orchestrator's next start
        self.assertTrue(info["previous"].startswith("factory.db.pre-restore-"))
        self.assertTrue((self.state / info["previous"]).exists())
        self.assertFalse((self.state / B.MARKER).exists())
        new = dbm.connect(self.db_path)
        self.assertEqual(new.execute("SELECT message FROM events").fetchone()[0], "hello")
        self.assertEqual(new.execute("SELECT output FROM runs").fetchone()[0], "")
        self.assertIsNone(B.apply_pending(self.state, self.db_path))

    def test_restore_checks_the_session_the_csrf_token_and_the_confirmation(self):
        cookie, csrf, data = self.download()
        s, h, _ = self.raw("POST", "/backup/restore", multipart({"csrf": csrf, "confirm": "1"}, data), None, f"multipart/form-data; boundary={BOUNDARY}")
        self.assertEqual((s, h["Location"]), (303, "/login"))
        self.assertEqual(self.upload(cookie, csrf, data, token="wrong")[0], 403)
        self.assertEqual(self.upload(cookie, csrf, data, token="")[0], 403)
        self.assertEqual(self.upload(cookie, csrf, data, confirm="")[0], 400)
        self.assertEqual(self.raw("POST", "/backup/restore", "csrf=" + csrf, cookie)[0], 400)      # not multipart
        self.assertFalse((self.state / "PAUSED").exists())
        self.assertFalse((self.state / "restore").exists())

    def test_restore_rejects_bad_bundles_without_changing_anything(self):
        cookie, csrf, data = self.download()
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            for n in ("manifest.json", "factory.db"):
                z.writestr(n, zipfile.ZipFile(io.BytesIO(data)).read(n))
            z.writestr("../evil", "x")
        tampered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(tampered, "w") as z:
            for n in src.namelist():
                z.writestr(n, src.read(n) + (b"# changed\n" if n == "config.toml" else b""))
        for blob in (b"not a zip", bad.getvalue(), tampered.getvalue()):
            s, _, body = self.upload(cookie, csrf, blob)
            self.assertEqual(s, 422, blob[:20])
            self.assertIn(b"Not restored", body)
        self.assertEqual([p.name for p in self.state.iterdir() if p.name in ("PAUSED", "RESTART", "restore", B.MARKER)], [])
        self.assertFalse((self.root / "config.toml.bak").exists())

    def test_rebuild_copies_rows_only(self):
        evil = self.root / "evil.db"
        c = sqlite3.connect(evil)
        c.executescript("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, repo TEXT, issue INTEGER, run_id INTEGER, message TEXT, extra TEXT);
            CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT, repo TEXT, issue INTEGER, started REAL, output TEXT, log_tail TEXT);
            CREATE TABLE junk (x);
            CREATE VIEW status AS SELECT 'k' AS key, 'v' AS value, 1.0 AS updated;
            INSERT INTO runs VALUES (1,'build','o/r',1,1.0,'SECRET','SECRET');""")
        c.execute("INSERT INTO events (ts, kind, message, extra) VALUES (1.0,'k','m','e')")
        c.execute("CREATE TRIGGER t AFTER INSERT ON events BEGIN DELETE FROM events; END")
        c.commit()
        c.close()
        counts = B.rebuild_db(evil, self.root / "out.db")
        out = sqlite3.connect(self.root / "out.db")
        self.assertEqual(counts["events"], 1)
        self.assertEqual(out.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 0)
        self.assertNotIn("junk", [r[0] for r in out.execute("SELECT name FROM sqlite_master")])
        self.assertEqual(out.execute("SELECT type FROM sqlite_master WHERE name='status'").fetchone()[0], "table")    # the view did not stand in
        self.assertEqual(out.execute("SELECT output, log_tail FROM runs").fetchone(), ("", ""))


if __name__ == "__main__":
    unittest.main()
