import tempfile
import time
import unittest
from pathlib import Path

from factory import pause
from factory.runner import RATE_LIMIT


class T(unittest.TestCase):
    def test_manual_and_backoff(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pause.paused(d))
            (Path(d) / "PAUSED").write_text("")
            self.assertEqual(pause.paused(d), "manual pause")
            (Path(d) / "PAUSED").unlink()
            pause.set_backoff(d, 60)
            self.assertIn("backoff", pause.paused(d))
            (Path(d) / "pause_until").write_text(str(time.time() - 1))
            self.assertIsNone(pause.paused(d))

    def test_corrupt_pause_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pause_until").write_text("garbage")
            self.assertIn("corrupt", pause.paused(d))

    def test_rate_limit_detection(self):
        for t in ("Claude AI usage limit reached|1700000000", "API Error: 429 Too Many Requests", "rate limit exceeded"):
            self.assertTrue(RATE_LIMIT.search(t), t)
        self.assertFalse(RATE_LIMIT.search("Updated the README successfully"))


if __name__ == "__main__":
    unittest.main()
