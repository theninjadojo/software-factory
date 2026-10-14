"""API tokens for the command-line client: made once, kept hashed, checked in constant time, revoked by name or id."""
import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from factory import ctl
from factory.ui.apitokens import Store


class Tokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "api-tokens.json"
        self.store = Store(self.path)

    def test_a_token_is_shown_once_and_only_its_hash_is_kept(self):
        token = self.store.add("laptop")
        self.assertTrue(token.startswith("shk_"))
        self.assertGreater(len(token), 45)
        text = self.path.read_text()
        self.assertNotIn(token, text)
        self.assertNotIn(token.split("_", 2)[2], text)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        (row,) = self.store.list()
        self.assertEqual((row["name"], row["scope"], row["last_used"]), ("laptop", "write", None))
        self.assertNotIn("hash", row)

    def test_authenticate_takes_only_a_stored_bearer_token(self):
        good = self.store.add("laptop")
        ro = self.store.add("dash", "read")
        self.assertEqual(self.store.authenticate(f"Bearer {good}")["name"], "laptop")
        self.assertEqual(self.store.authenticate(f"Bearer {ro}")["scope"], "read")
        for bad in (None, "", good, f"Basic {good}", "Bearer ", f"Bearer {good}x", "Bearer shk_" + "a" * 300, f"Bearer {good[:-1]}"):
            self.assertIsNone(self.store.authenticate(bad), bad)

    def test_names_are_checked_and_never_reused(self):
        self.store.add("laptop")
        for name in ("laptop", "", "Laptop", "a b", "-x", "x" * 42):
            with self.assertRaises(ValueError):
                self.store.add(name)
        with self.assertRaises(ValueError):
            self.store.add("other", "admin")

    def test_revoke_by_name_or_id_takes_effect_for_a_store_already_reading(self):
        other = Store(self.path)                                       # the UI's store, while ctl changes the file
        token = self.store.add("laptop")
        self.assertIsNotNone(other.authenticate(f"Bearer {token}"))
        tid = self.store.list()[0]["id"]
        self.assertTrue(self.store.revoke(tid))
        self.assertIsNone(other.authenticate(f"Bearer {token}"))
        self.assertFalse(self.store.revoke("laptop"))
        t2 = self.store.add("laptop")
        self.assertTrue(other.revoke("laptop"))
        self.assertIsNone(self.store.authenticate(f"Bearer {t2}"))

    def test_last_used_is_written_at_most_hourly(self):
        token = self.store.add("laptop")
        rec = self.store.authenticate(f"Bearer {token}")
        self.store.touch(rec, now=1000.0)
        rec = self.store.authenticate(f"Bearer {token}")
        self.assertEqual(rec["last_used"], 1000)
        before = self.path.stat().st_mtime_ns
        self.store.touch(rec, now=1500.0)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.store.touch(rec, now=5000.0)
        self.assertEqual(self.store.list()[0]["last_used"], 5000)

    def test_tokens_made_at_once_are_all_kept(self):
        made = []

        def add(i):
            made.append(Store(self.path).add(f"t{i}"))
        threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.store.list()), 8)
        self.assertTrue(all(self.store.authenticate(f"Bearer {t}") for t in made))

    def test_ctl_makes_lists_and_revokes(self):
        state = Path(self.tmp.name)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ctl.tokens_cmd(state, ["add", "laptop", "--read-only"]), 0)
        token = out.getvalue().strip().splitlines()[-1]
        self.assertEqual(self.store.authenticate(f"Bearer {token}")["scope"], "read")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ctl.tokens_cmd(state, ["list"]), 0)
            self.assertEqual(ctl.tokens_cmd(state, ["add", "laptop"]), 1)          # taken
            self.assertEqual(ctl.tokens_cmd(state, ["revoke", "laptop"]), 0)
            self.assertEqual(ctl.tokens_cmd(state, ["revoke", "laptop"]), 1)
            self.assertEqual(ctl.tokens_cmd(state, ["frobnicate"]), 2)
        self.assertIn("laptop", out.getvalue())
        self.assertNotIn(token, out.getvalue())
        self.assertEqual(self.store.list(), [])
        self.assertFalse(any(name.startswith("api-tokens.tmp") for name in os.listdir(state)))


if __name__ == "__main__":
    unittest.main()
