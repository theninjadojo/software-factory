"""A short GitHub rate limit (#485): rate-limited calls that are safe to repeat wait and try again, a plain 403 still fails at
once, and a build whose branch is pushed opens its pull request once the limit clears instead of failing."""
import email.message
import io
import time
import unittest
import urllib.error
from unittest import mock

from factory import github as ghm
from factory import runner


def http_error(code, headers=None, body=b""):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", h, io.BytesIO(body))


def secondary():
    return http_error(403, body=b'{"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}')


class Detect(unittest.TestCase):
    def test_signs_of_a_rate_limit(self):
        self.assertTrue(ghm.rate_limited(http_error(429)))
        self.assertTrue(ghm.rate_limited(http_error(403, {"Retry-After": "30"})))
        self.assertTrue(ghm.rate_limited(http_error(403, {"X-RateLimit-Remaining": "0"})))
        self.assertTrue(ghm.rate_limited(secondary()))

    def test_a_plain_refusal_is_not_one(self):
        self.assertFalse(ghm.rate_limited(http_error(403, {"X-RateLimit-Remaining": "4999"}, b'{"message": "Resource not accessible by integration"}')))
        self.assertFalse(ghm.rate_limited(http_error(404, {"Retry-After": "30"})))
        self.assertFalse(ghm.rate_limited(http_error(500)))
        self.assertFalse(ghm.rate_limited(TimeoutError()))
        self.assertFalse(ghm.rate_limited(urllib.error.HTTPError("u", 403, "m", {}, None)))

    def test_the_body_can_still_be_read_after_the_check(self):
        e = secondary()
        self.assertTrue(ghm.rate_limited(e))
        self.assertIn(b"secondary rate limit", e.read())

    def test_waits_as_told_but_not_for_ever(self):
        self.assertEqual(ghm.rate_limit_wait(http_error(429, {"Retry-After": "7"})), 7)
        self.assertEqual(ghm.rate_limit_wait(http_error(429, {"Retry-After": "9999"})), ghm.MAX_RATE_LIMIT_WAIT)
        self.assertEqual(ghm.rate_limit_wait(secondary()), ghm.RATE_LIMIT_WAIT)
        reset = http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 40)})
        self.assertTrue(30 <= ghm.rate_limit_wait(reset) <= 42)
        self.assertEqual(ghm.rate_limit_wait(http_error(429, {"Retry-After": "soon"})), ghm.RATE_LIMIT_WAIT)


def waits(sleep):
    """The waits asked for, leaving out the short polls of any other thread (time.sleep is patched for the whole process)."""
    return [c.args[0] for c in sleep.call_args_list if c.args and c.args[0] >= 1]


class Retries(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ghm.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_a_403_with_rate_limit_headers_is_retried(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=[http_error(403, {"Retry-After": "12"}), {"default_branch": "main"}]) as send:
            self.assertEqual(gh.default_branch("o/r"), "main")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(waits(self.sleep), [12])

    def test_a_429_and_a_secondary_limit_are_retried(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=[http_error(429), secondary(), {"number": 1}]) as send:
            self.assertEqual(gh.get_issue("o/r", 1), {"number": 1})
        self.assertEqual(send.call_count, 3)

    def test_a_plain_403_fails_at_once(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=http_error(403, body=b'{"message": "Must have admin rights"}')) as send:
            with self.assertRaises(urllib.error.HTTPError):
                gh.default_branch("o/r")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(waits(self.sleep), [])

    def test_a_comment_is_not_sent_twice_even_when_rate_limited(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=http_error(429)) as send:
            with self.assertRaises(urllib.error.HTTPError):
                gh.comment("o/r", 1, "hi")
        self.assertEqual(send.call_count, 1)

    def test_it_gives_up_when_the_limit_does_not_clear(self):
        gh = ghm.GitHub("t")
        with mock.patch.object(gh, "_send", side_effect=lambda *a: (_ for _ in ()).throw(http_error(429, {"Retry-After": "5"}))) as send:
            with self.assertRaises(urllib.error.HTTPError):
                gh.get_issue("o/r", 1)
        self.assertEqual(send.call_count, ghm.RATE_LIMIT_RETRIES + 1)


class FakeGH:
    def __init__(self, base=(), create=(), pulls=()):
        self.base, self.create, self.pulls, self.calls = list(base), list(create), list(pulls), []

    def default_branch(self, repo):
        x = self.base.pop(0) if self.base else "main"
        if isinstance(x, Exception):
            raise x
        return x

    def create_pr(self, repo, head, base, title, body, draft=False):
        self.calls.append((repo, head, base, draft))
        x = self.create.pop(0) if self.create else f"https://github.com/{repo}/pull/9"
        if isinstance(x, Exception):
            raise x
        return x

    def open_pulls(self, repo, prefix=""):
        return [p for p in self.pulls if p["head"]["ref"].startswith(prefix)]


class OpenPR(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(runner.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_opening_the_pr_after_the_push_recovers_from_a_rate_limit(self):
        gh = FakeGH(base=[secondary()], create=[http_error(429, {"Retry-After": "20"})])
        self.assertEqual(runner.open_pr(gh, "o/r", "factory/issue-1-x", "t", "b", draft=True), "https://github.com/o/r/pull/9")
        self.assertEqual(gh.calls[-1], ("o/r", "factory/issue-1-x", "main", True))
        self.assertEqual(waits(self.sleep), [ghm.RATE_LIMIT_WAIT, 20])

    def test_a_missing_permission_fails_at_once_and_says_so(self):
        gh = FakeGH(create=[http_error(403, body=b'{"message": "Resource not accessible by integration"}')])
        with self.assertRaises(runner.PROpenFailed) as cm:
            runner.open_pr(gh, "o/r", "factory/issue-1-x", "t", "b")
        self.assertIn("Pull requests: write", str(cm.exception))
        self.assertIn("compare/factory/issue-1-x", str(cm.exception))
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(waits(self.sleep), [])

    def test_a_limit_that_does_not_clear_names_the_rate_limit(self):
        gh = FakeGH(create=[http_error(429)] * runner.PR_STEP_TRIES)
        with self.assertRaises(runner.PROpenFailed) as cm:
            runner.open_pr(gh, "o/r", "factory/issue-1-x", "t", "b")
        self.assertIn("rate limited", str(cm.exception))
        self.assertEqual(len(gh.calls), runner.PR_STEP_TRIES)

    def test_a_pr_opened_by_a_try_whose_answer_was_lost_is_found(self):
        gh = FakeGH(create=[TimeoutError(), http_error(422, body=b'{"message": "A pull request already exists"}')],
                    pulls=[{"head": {"ref": "factory/issue-1-x"}, "html_url": "https://github.com/o/r/pull/4"}])
        self.assertEqual(runner.open_pr(gh, "o/r", "factory/issue-1-x", "t", "b"), "https://github.com/o/r/pull/4")


if __name__ == "__main__":
    unittest.main()
