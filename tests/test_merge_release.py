import urllib.error
from unittest import mock

from factory import db as dbm
from factory.config import load
from factory.ui import integrations as I
from factory.ui import release as R
from test_ui_admin import TOKEN, AdminCase

REPO = "your-org/standalone-service"


class MergeFromPrView(AdminCase):
    def setUp(self):
        super().setUp()
        I.save_secret(load(str(self.root / "config.toml")), "github", TOKEN, "subscription")
        self.gh = mock.MagicMock()
        self.gh.get_pr.return_value = {"state": "open", "draft": False, "merged": False, "head": {"sha": "abc"}}
        p = mock.patch("factory.ui.labels.GitHub", return_value=self.gh)
        p.start()
        self.addCleanup(p.stop)

    def watch(self, status, n=5):
        dbm.watch_pr(self.db, REPO, n, REPO, 7)
        dbm.update_pr(self.db, REPO, n, status=status)

    def test_only_a_passing_pr_has_a_merge_button(self):
        self.watch("passed", 5)
        self.watch("failed", 6)
        cookie, _ = self.session()
        _, _, html = self.req("GET", f"/fragment/ticket?repo={REPO}&n=7", cookie=cookie)
        self.assertIn('aria-label="Merge pull request #5"', html)
        self.assertNotIn('aria-label="Merge pull request #6"', html)

    def test_merge_pins_the_head_sha_and_reports_it(self):
        self.watch("passed")
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        self.assertEqual(s, 303)
        self.gh.merge_pr.assert_called_once_with(REPO, 5, "abc")
        self.assertIn("/ticket?repo=", h["Location"])
        _, _, html = self.req("GET", h["Location"], cookie=cookie)
        self.assertIn(f"Merged {REPO}#5", html)

    def test_refused_when_not_passed_untracked_draft_or_no_csrf(self):
        self.watch("failed")
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "99"})
        self.assertEqual(self.post(cookie, csrf, "/prs/merge", {"repo": "evil", "n": "5"})[0], 400)
        self.watch("passed", 8)
        self.gh.get_pr.return_value["draft"] = True
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "8"})
        self.assertEqual(self.req("POST", "/prs/merge", "repo=x&n=5", cookie=cookie)[0], 403)
        self.gh.merge_pr.assert_not_called()

    def test_github_refusal_is_not_shown_as_success(self):
        self.watch("passed")
        self.gh.merge_pr.side_effect = urllib.error.HTTPError("u", 405, "x", {}, None)
        cookie, csrf = self.session()
        _, h, _ = self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        _, _, html = self.req("GET", h["Location"], cookie=cookie)
        self.assertIn("would not merge", html)
        self.assertNotIn("Merged your-org", html)


class Releases(AdminCase):
    def setUp(self):
        super().setUp()
        self.gh = mock.MagicMock()
        self.gh.token = "t"
        self.gh.default_branch.return_value = "main"
        self.gh.raw_file.return_value = b"0.20.0\n"
        self.gh.open_pulls.return_value = []
        self.gh.bump_file.return_value = "https://github.com/o/r/pull/9"
        for p in (mock.patch.object(R, "_gh", return_value=self.gh), mock.patch("factory.updates.fetch_latest", return_value={"tag": "v0.20.1", "url": "https://github.com/x"})):
            p.start()
            self.addCleanup(p.stop)

    def test_bump_arithmetic(self):
        self.assertEqual([R.bump("0.20.1", k) for k in ("patch", "minor", "major")], ["0.20.2", "0.21.0", "1.0.0"])
        self.assertIsNone(R.bump("dev", "patch"))
        self.assertIsNone(R.bump("1.2.3", "huge"))

    def test_page_offers_versions_above_the_highest_of_main_and_latest_release(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/release", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("0.20.2", html)
        self.assertIn('action="/release/bump"', html)

    def test_bump_opens_a_release_branch_pr_not_a_push_to_main(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/release/bump", {"kind": "minor"})[0], 303)
        args = self.gh.bump_file.call_args.args
        self.assertEqual((args[1], args[2], args[3], args[4]), ("VERSION", "0.21.0\n", "release/v0.21.0", "main"))
        self.assertEqual(self.post(cookie, csrf, "/release/bump", {"kind": "huge"})[0], 303)
        self.assertEqual(self.gh.bump_file.call_count, 1)

    def test_merge_only_accepts_an_open_release_pr(self):
        cookie, csrf = self.session()
        self.gh.get_pr.return_value = {"state": "open", "head": {"ref": "factory/issue-3", "sha": "s"}}
        self.post(cookie, csrf, "/release/merge", {"n": "9"})
        self.gh.merge_pr.assert_not_called()
        self.gh.get_pr.return_value = {"state": "open", "head": {"ref": "release/v0.21.0", "sha": "s"}}
        self.post(cookie, csrf, "/release/merge", {"n": "9"})
        self.gh.merge_pr.assert_called_once()
