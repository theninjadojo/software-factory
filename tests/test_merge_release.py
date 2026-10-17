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


class MergePastTheReview(MergeFromPrView):
    """A PR whose last automated review was not Looks good shows the verdict and merges only after a tick (reviewgate.py)."""

    def reviewed(self, verdict, n=5, state="held", rounds=2):
        from factory import reviewgate
        self.watch("passed", n)
        reviewgate.record(self.db, [(REPO, n)], REPO, 7, verdict, state, rounds)

    def test_the_verdict_and_a_merge_anyway_tick_show_on_the_ticket_and_the_prs_page(self):
        self.reviewed("Needs changes")
        self.reviewed("Looks good", n=6, state="ready", rounds=0)
        cookie, _ = self.session()
        self.assertEqual(self.req("GET", "/prs", cookie=cookie)[1]["Location"], "/tickets?stage=prs")
        self.assertIn("review: Needs changes", self.req("GET", "/tickets?stage=prs", cookie=cookie)[2])     # /prs is this filter of Tickets
        html = self.req("GET", f"/fragment/ticket?repo={REPO}&n=7", cookie=cookie)[2]
        self.assertIn("Needs changes", html)
        self.assertIn("2 review fix rounds", html)
        self.assertIn("Looks good", html)
        self.assertEqual(html.count('name="anyway"'), 1)                                  # only the PR the review did not pass
        self.assertIn("Merge anyway: the last review said", html)
        self.assertIn('aria-label="Merge pull request #5"', html)
        self.assertIn('aria-label="Merge pull request #6"', html)

    def test_merging_without_the_tick_is_refused(self):
        self.reviewed("Blocking issues")
        cookie, csrf = self.session()
        _, h, _ = self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        self.gh.merge_pr.assert_not_called()
        self.assertIn("said Blocking issues", self.req("GET", h["Location"], cookie=cookie)[2])

    def test_merge_anyway_takes_a_held_draft_out_of_draft_and_records_the_override(self):
        from factory import reviewgate
        self.reviewed("Needs changes")
        self.gh.get_pr.return_value["draft"] = True
        self.gh.mark_ready.return_value = True
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5", "anyway": "1"})
        self.gh.mark_ready.assert_called_once_with(REPO, 5)
        self.gh.merge_pr.assert_called_once_with(REPO, 5, "abc")
        self.assertEqual(reviewgate.last(self.db, REPO, 5)["state"], "override")

    def test_with_the_gate_off_a_reviewed_pr_merges_as_before(self):
        self.reviewed("Needs changes", state="ready", rounds=0)                          # review.gate off: marked ready after the review
        cookie, csrf = self.session()
        html = self.req("GET", f"/fragment/ticket?repo={REPO}&n=7", cookie=cookie)[2]
        self.assertNotIn('name="anyway"', html)
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        self.gh.merge_pr.assert_called_once()

    def test_a_looks_good_pr_merges_without_a_tick_and_other_drafts_stay_refused(self):
        self.reviewed("Looks good", state="ready", rounds=0)
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5"})
        self.gh.merge_pr.assert_called_once()
        self.gh.merge_pr.reset_mock()
        self.gh.get_pr.return_value["draft"] = True                                       # a draft the review did not hold
        self.post(cookie, csrf, "/prs/merge", {"repo": REPO, "n": "5", "anyway": "1"})
        self.gh.merge_pr.assert_not_called()
        self.gh.mark_ready.assert_not_called()


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
        s, _, html = self.req("GET", "/updates", cookie=cookie)
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

    def test_one_page_holds_updates_release_and_check_settings(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/updates", cookie=cookie)
        self.assertEqual(s, 200)
        for part in ("Running version and updates", "Publish a release", "Update checks"):
            self.assertIn(f"<h2>{part}</h2>", html)
        self.assertIn('data-src="/updates/status"', html)
        self.assertIn('action="/updates/check"', html)
        self.assertIn('name="updates.repo"', html)
        self.assertEqual(html.count('name="updates.repo"'), 1)
        self.assertIn("<h1>Updates and releases</h1>", html)
        self.assertIn('href="/updates"', html)                                            # the one menu entry
        for old in ('href="/release"', 'href="/settings?section=update_check"'):
            self.assertNotIn(old, html)

    def test_a_github_failure_leaves_the_update_controls_in_place(self):
        self.gh.open_pulls.side_effect = urllib.error.URLError("down")
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/updates", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Release information could not be loaded", html)
        self.assertIn('action="/updates/check"', html)
        self.assertIn('name="updates.check"', html)

    def test_a_github_reply_of_another_shape_still_renders_the_page(self):
        self.gh.default_branch.side_effect = TypeError("list indices must be integers or slices, not str")
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/updates", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Release information could not be loaded", html)

    def test_the_old_pages_redirect_to_the_fixed_path(self):
        cookie, _ = self.session()
        for path in ("/release", "/release?next=//evil.example", "/settings?section=update_check"):
            s, h, _ = self.req("GET", path, cookie=cookie)
            self.assertEqual((s, h["Location"]), (303, "/updates"), path)

    def test_actions_return_to_the_merged_page_and_still_need_csrf(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/release/bump", {"kind": "minor"})[1]["Location"], "/updates")
        self.assertEqual(self.post(cookie, csrf, "/release/merge", {"n": "x"})[1]["Location"], "/updates")
        for path in ("/release/bump", "/release/merge", "/updates/check", "/updates/apply", "/updates/auto"):
            self.assertEqual(self.req("POST", path, "kind=minor&n=9", cookie=cookie)[0], 403, path)

    def test_saving_the_check_settings_returns_to_the_merged_page(self):
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/settings/save", [("section", "update_check"), ("updates.repo", "o/r")])
        self.assertEqual((s, h["Location"]), (303, "/updates"))
        s, _, html = self.post(cookie, csrf, "/settings/save", [("section", "update_check"), ("updates.repo", "not a repo")])
        self.assertEqual(s, 422)
        self.assertIn("Publish a release", html)
