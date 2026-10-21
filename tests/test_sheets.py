import base64
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from factory import sheets as sh

HAS_OPENSSL = shutil.which("openssl") is not None
SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
FAKE_PEM = sh.PEM_HEAD + "\nMIIfake\n" + sh.PEM_TAIL + "\n"


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOpener:
    """Records requests; answers the token endpoint with a token and everything else from `answers` (bytes or an HTTP code)."""

    def __init__(self, *answers):
        self.requests, self.answers = [], list(answers)

    def open(self, req, timeout=None):
        self.requests.append(req)
        if req.full_url == sh.TOKEN_URL:
            return Resp(json.dumps({"access_token": "ya29.tok", "expires_in": 3600}).encode())
        a = self.answers.pop(0) if self.answers else b"{}"
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "x", {}, None)
        return Resp(a)


class FakeRun:
    def __init__(self, returncode=0, stdout=b"sig", stderr=b""):
        self.calls, self.result = [], (returncode, stdout, stderr)

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        return subprocess.CompletedProcess(args, *self.result)


class KeyDir:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def write(self, name="key.json", **over) -> str:
        data = {"type": "service_account", "client_email": "factory@proj.iam.gserviceaccount.com", "private_key": FAKE_PEM, **over}
        p = self.dir / name
        p.write_text(json.dumps(data))
        return str(p)


class Base(unittest.TestCase):
    def setUp(self):
        sh._tokens.clear()
        self.keys = KeyDir()
        self.addCleanup(self.keys.tmp.cleanup)

    def client(self, *answers, run=None, key=None):
        self.opener = FakeOpener(*answers)
        self.run = run or FakeRun()
        return sh.Client(key or self.keys.write(), opener=self.opener, run=self.run, which=lambda _: "/usr/bin/openssl",
                         clock=lambda: 1_800_000_000)


class LinkTest(unittest.TestCase):
    def test_link_and_bare_id(self):
        self.assertEqual(sh.parse_link(f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"), SID)
        self.assertEqual(sh.parse_link(f"https://docs.google.com/spreadsheets/d/{SID}"), SID)
        self.assertEqual(sh.parse_link(SID), SID)

    def test_other_links_refused(self):
        for bad in ("", "short", f"http://docs.google.com/spreadsheets/d/{SID}", f"https://evil.example/spreadsheets/d/{SID}",
                    f"https://docs.google.com.evil.example/spreadsheets/d/{SID}", "../../etc/passwd" + "a" * 20, None):
            self.assertIsNone(sh.parse_link(bad), bad)


class KeyFileTest(Base):
    def test_good_key_keeps_only_two_fields(self):
        self.assertEqual(set(sh.load_key(self.keys.write(token_uri="https://evil.example/t", project_id="p"))),
                         {"client_email", "private_key"})

    def test_bad_key_files_give_fixed_messages_without_contents(self):
        cases = {
            "missing": str(self.keys.dir / "nope.json"),
            "not json": self.keys.dir / "a.json",
            "wrong type": self.keys.write("b.json", type="authorized_user"),
            "bad email": self.keys.write("c.json", client_email="not an email"),
            "bad pem": self.keys.write("d.json", private_key="SECRET-NOT-A-PEM"),
            "big pem": self.keys.write("e.json", private_key=sh.PEM_HEAD + "A" * 9000 + sh.PEM_TAIL),
        }
        (self.keys.dir / "a.json").write_text("SECRET{")
        big = self.keys.dir / "f.json"
        big.write_text("x" * (sh.KEY_MAX + 1))
        cases["too large"] = big
        for name, path in cases.items():
            with self.subTest(name), self.assertRaises(sh.SheetsError) as e:
                sh.load_key(str(path))
            self.assertNotIn("SECRET", str(e.exception))


class SigningTest(Base):
    def test_claims(self):
        head, claims = sh.signing_input("a@b.iam.gserviceaccount.com", 1000.7).decode().split(".")
        self.assertEqual(json.loads(_unb64(head)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(_unb64(claims)), {"iss": "a@b.iam.gserviceaccount.com", "scope": sh.SCOPE,
                                                      "aud": sh.TOKEN_URL, "iat": 1000, "exp": 4600})

    def test_key_goes_through_a_pipe_never_argv(self):
        run = FakeRun()
        sh.sign(FAKE_PEM, b"data", run=run, which=lambda _: "/usr/bin/openssl")
        args, kw = run.calls[0]
        self.assertEqual(args[:4], ["openssl", "dgst", "-sha256", "-sign"])
        fd = int(args[4].removeprefix("/dev/fd/"))
        self.assertEqual(kw["pass_fds"], (fd,))
        self.assertEqual(kw["input"], b"data")
        self.assertFalse(any("MIIfake" in a or "PRIVATE" in a for a in args))

    def test_missing_openssl(self):
        run = FakeRun()
        with self.assertRaises(sh.SheetsError) as e:
            sh.sign(FAKE_PEM, b"data", run=run, which=lambda _: None)
        self.assertEqual(str(e.exception), sh.NO_OPENSSL)
        self.assertEqual(run.calls, [])

    def test_openssl_failure_hides_its_output(self):
        with self.assertRaises(sh.SheetsError) as e:
            sh.sign(FAKE_PEM, b"data", run=FakeRun(1, b"", b"unable to load key MIIfake"), which=lambda _: "/usr/bin/openssl")
        self.assertEqual(str(e.exception), "openssl could not sign (exit 1).")

    @unittest.skipUnless(HAS_OPENSSL, "openssl is not installed")
    def test_real_signature_verifies(self):
        d = self.keys.dir
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(d / "k.pem")],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(d / "k.pem"), "-pubout", "-out", str(d / "pub.pem")], check=True, capture_output=True)
        key = sh.load_key(self.keys.write(private_key=(d / "k.pem").read_text()))
        jwt = sh.assertion(key, 1_800_000_000)
        data, sig = jwt.rsplit(".", 1)
        (d / "data").write_bytes(data.encode())
        (d / "sig").write_bytes(_unb64(sig))
        ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(d / "pub.pem"), "-signature", str(d / "sig"), str(d / "data")],
                            capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)


class TokenTest(Base):
    def test_token_request_goes_to_google_whatever_the_key_says(self):
        c = self.client(key=self.keys.write(token_uri="https://evil.example/token"))
        self.assertEqual(c.token(), "ya29.tok")
        req = self.opener.requests[0]
        self.assertEqual(req.full_url, sh.TOKEN_URL)
        form = urllib.parse.parse_qs(req.data.decode())
        self.assertEqual(form["grant_type"], ["urn:ietf:params:oauth:grant-type:jwt-bearer"])
        claims = json.loads(_unb64(form["assertion"][0].split(".")[1]))
        self.assertEqual(claims["aud"], sh.TOKEN_URL)
        self.assertNotIn("Authorization", req.headers)

    def test_token_is_cached_until_shortly_before_expiry(self):
        now = [1_800_000_000]
        c = self.client()
        c.clock = lambda: now[0]
        c.token(), c.token()
        self.assertEqual(len(self.opener.requests), 1)
        now[0] += 3600 - 59
        c.token()
        self.assertEqual(len(self.opener.requests), 2)

    def test_refused_key(self):
        c = self.client()
        c.opener.open = lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 400, "x", {}, None))
        with self.assertRaisesRegex(sh.SheetsError, "refused the service-account key"):
            c.token()


class CallTest(Base):
    def test_only_google_hosts_over_https(self):
        c = self.client()
        for url in ("https://evil.example/x", "http://sheets.googleapis.com/v4/x", "https://sheets.googleapis.com.evil.example/x",
                    "https://sheets.googleapis.com:8443/x", "https://u:p@sheets.googleapis.com/x", "file:///etc/passwd"):
            with self.subTest(url), self.assertRaisesRegex(sh.SheetsError, "host other than Google"):
                c._call("GET", url, auth=False)
        self.assertEqual(self.opener.requests, [])

    def test_redirects_are_not_followed(self):
        opener = sh.Client("k").opener
        self.assertTrue(any(isinstance(h, sh._NoRedirect) for h in opener.handlers))
        self.assertIsNone(sh._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))
        with self.assertRaisesRegex(sh.SheetsError, "redirect"):
            self.client(302).metadata(SID)

    def test_size_cap(self):
        with self.assertRaisesRegex(sh.SheetsError, "too large"):
            self.client(b" " * (sh.MAX_BYTES + 1)).metadata(SID)

    def test_errors_are_fixed_messages(self):
        for code, text in ((403, "Share the sheet"), (404, "not found"), (429, "rate limit"), (500, "HTTP 500")):
            with self.subTest(code), self.assertRaisesRegex(sh.SheetsError, text):
                self.client(code).metadata(SID)

    def test_bad_sheet_id_never_reaches_the_url(self):
        c = self.client()
        with self.assertRaises(sh.SheetsError):
            c.batch_get("../../other?x=", ["A1"])
        self.assertEqual(self.opener.requests, [])


class ValuesTest(Base):
    def test_batch_get(self):
        c = self.client(json.dumps({"valueRanges": [{"values": [["a", "b"]]}, {}]}).encode())
        self.assertEqual(c.batch_get(SID, ["Tickets!A:J", "Milestones!A:A"]), [[["a", "b"]], []])
        req = self.opener.requests[-1]
        u = urllib.parse.urlsplit(req.full_url)
        self.assertEqual(u.path, f"/v4/spreadsheets/{SID}/values:batchGet")
        self.assertEqual(urllib.parse.parse_qs(u.query)["ranges"], ["Tickets!A:J", "Milestones!A:A"])
        self.assertEqual(req.headers["Authorization"], "Bearer ya29.tok")

    def test_writes_are_raw(self):
        c = self.client()
        c.batch_update(SID, [{"range": "Tickets!A2:C2", "values": [["=HYPERLINK(\"x\")", "+1", "ok"]], "extra": 1}])
        body = json.loads(self.opener.requests[-1].data)
        self.assertEqual(body["valueInputOption"], "RAW")
        self.assertEqual(body["data"], [{"range": "Tickets!A2:C2", "values": [["=HYPERLINK(\"x\")", "+1", "ok"]]}])

    def test_append_is_raw_and_inserts_rows(self):
        c = self.client()
        c.append(SID, "Tickets!A:J", [["o/r", "#3"]])
        req = self.opener.requests[-1]
        u = urllib.parse.urlsplit(req.full_url)
        self.assertEqual(urllib.parse.unquote(u.path), f"/v4/spreadsheets/{SID}/values/Tickets!A:J:append")
        self.assertEqual(urllib.parse.parse_qs(u.query), {"valueInputOption": ["RAW"], "insertDataOption": ["INSERT_ROWS"]})
        self.assertEqual(json.loads(req.data), {"values": [["o/r", "#3"]]})


class CheckAccessTest(Base):
    def test_linked(self):
        meta = {"properties": {"title": "Plan"}, "sheets": [{"properties": {"title": "Tickets"}}, {"properties": {"title": "Milestones"}}]}
        key = self.keys.write()
        out = sh.check_access(f"https://docs.google.com/spreadsheets/d/{SID}/edit", key, self.client(json.dumps(meta).encode(), key=key))
        self.assertEqual(out, {"id": SID, "title": "Plan", "tabs": ["Tickets", "Milestones"],
                               "email": "factory@proj.iam.gserviceaccount.com"})

    def test_bad_link_checks_nothing(self):
        c = self.client()
        with self.assertRaisesRegex(sh.SheetsError, "not a Google Sheets link"):
            sh.check_access("https://evil.example/", self.keys.write(), c)
        self.assertEqual(self.opener.requests, [])

    def test_not_shared(self):
        key = self.keys.write()
        with self.assertRaisesRegex(sh.SheetsError, "no access"):
            sh.check_access(SID, key, self.client(403, key=key))


if __name__ == "__main__":
    unittest.main()
