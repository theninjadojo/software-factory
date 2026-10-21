"""The JSON API for the command-line client (/api/v1/): bearer tokens only, JSON only, and the same checks and messages as the pages."""
import http.client
import json
import re
from unittest import mock
from urllib.parse import quote

from factory import db as dbm
from factory import tracker
from factory.config import load
from factory.ui import integrations as I
from factory.ui import labels as L
from factory.ui import views

from test_ui_admin import TOKEN, AdminCase

REPO = "your-org/standalone-service"
LOCAL = tracker.LOCAL_BASE + 1


class ApiCase(AdminCase):
    def setUp(self):
        super().setUp()
        self.token = self.app.api_tokens.add("cli")
        L._recent.clear()
        L.needs_forget(REPO, 7)

    def api(self, method, path, obj=None, token=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        tok = self.token if token is None else token
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        body = raw if raw is not None else (json.dumps(obj) if obj is not None else None)
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        text = r.read().decode()
        c.close()
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = text
        return r.status, dict(r.getheaders()), data

    def local_on(self):
        p = self.root / "config.toml"
        p.write_text(p.read_text() + "\n[local]\nenabled = true\n")


class Auth(ApiCase):
    def test_only_a_bearer_token_opens_the_api(self):
        s, h, body = self.api("GET", "/api/v1/version", token="")
        self.assertEqual((s, body["code"]), (401, "unauthorized"))
        self.assertEqual(h["WWW-Authenticate"], 'Bearer realm="shikumi"')
        self.assertEqual(self.api("GET", "/api/v1/version", token="shk_nope_nope")[0], 401)
        cookie, _ = self.session()                                   # a signed-in browser's cookie is not enough
        self.assertEqual(self.api("GET", "/api/v1/version", token="", headers={"Cookie": cookie})[0], 401)
        s, h, body = self.api("GET", "/api/v1/version")
        self.assertEqual(s, 200)
        self.assertEqual((body["api"], body["token"]), (1, {"name": "cli", "scope": "write"}))
        self.assertEqual(h["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", h)
        self.assertNotIn("Set-Cookie", h)

    def test_a_revoked_token_stops_at_once_and_a_read_token_cannot_write(self):
        self.app.api_tokens.revoke("cli")
        self.assertEqual(self.api("GET", "/api/v1/version")[0], 401)
        ro = self.app.api_tokens.add("ro", "read")
        self.assertEqual(self.api("GET", "/api/v1/status", token=ro)[0], 200)
        s, _, body = self.api("POST", "/api/v1/pause", {}, token=ro)
        self.assertEqual((s, body["code"]), (403, "read_only"))
        self.assertFalse((self.state / "PAUSED").exists())

    def test_the_host_check_still_applies(self):
        self.assertEqual(self.api("GET", "/api/v1/version", headers={"Host": "evil.example"})[0], 421)

    def test_wrong_tokens_are_throttled(self):
        for _ in range(20):
            self.api("GET", "/api/v1/version", token="shk_x_wrong")
        s, h, body = self.api("GET", "/api/v1/version")              # even the right token waits now
        self.assertEqual((s, body["code"], h["Retry-After"]), (429, "throttled", "300"))

    def test_a_write_must_be_json_of_a_known_size(self):
        self.assertEqual(self.api("POST", "/api/v1/pause", raw="x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.assertEqual(self.api("POST", "/api/v1/pause", raw="", headers={"Content-Type": "application/json"})[0], 411)
        self.assertEqual(self.api("POST", "/api/v1/pause", raw="{" + " " * 70000 + "}")[0], 413)
        self.assertEqual(self.api("POST", "/api/v1/pause", raw="{nope")[2]["code"], "bad_json")
        self.assertEqual(self.api("POST", "/api/v1/pause", [1, 2])[0], 400)
        self.assertFalse((self.state / "PAUSED").exists())

    def test_unknown_paths_and_methods(self):
        self.assertEqual(self.api("GET", "/api/v1/nothing")[0], 404)
        self.assertEqual(self.api("GET", "/api/v2/version")[0], 404)
        self.assertEqual(self.api("GET", "/api")[0], 404)
        self.assertEqual(self.api("GET", "/api/v1/pause")[0], 405)
        self.assertEqual(self.api("POST", "/api/v1/status", {})[0], 405)
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/7/frobnicate", {})[0], 404)


class TokenCard(ApiCase):
    def test_the_credentials_page_makes_and_revokes_tokens_with_the_password(self):
        from test_ui import PASSWORD
        cookie, csrf = self.session()
        html = self.req("GET", "/credentials", cookie=cookie)[2]
        self.assertIn("Command-line access", html)
        self.assertIn(">cli</td>", html)
        self.assertNotIn(self.token, html)
        s, _, html = self.post(cookie, csrf, "/credentials/api-token/add", {"name": "laptop", "password": "wrong password"})
        self.assertEqual(s, 403)
        self.assertEqual([t["name"] for t in self.app.api_tokens.list()], ["cli"])
        s, _, html = self.post(cookie, csrf, "/credentials/api-token/add", {"name": "laptop", "read_only": "1", "password": PASSWORD})
        self.assertEqual(s, 200)
        token = re.search(r'<pre class="ak-token">(shk_[^<]+)</pre>', html).group(1)
        self.assertIn("shikumi instance add factory --url http://127.0.0.1:", html)
        self.assertEqual(self.api("GET", "/api/v1/version", token=token)[2]["token"], {"name": "laptop", "scope": "read"})
        self.assertNotIn(token, self.req("GET", "/credentials", cookie=cookie)[2])                    # shown once
        self.assertEqual(self.post(cookie, csrf, "/credentials/api-token/add", {"name": "Bad Name", "password": PASSWORD})[0], 422)
        s, h, _ = self.post(cookie, csrf, "/credentials/api-token/revoke", {"name": "laptop"})
        self.assertEqual((s, h["Location"]), (303, "/credentials?ok=token_revoked#api-tokens"))
        self.assertEqual(self.api("GET", "/api/v1/version", token=token)[0], 401)
        self.assertEqual(self.post(cookie, csrf, "/credentials/api-token/revoke", {"name": "laptop"})[0], 404)
        self.assertEqual(self.req("POST", "/credentials/api-token/add", "name=x&password=" + PASSWORD, cookie=cookie)[0], 403)   # no CSRF field


class Reads(ApiCase):
    def test_version_meta_and_status(self):
        s, _, meta = self.api("GET", "/api/v1/meta")
        cfg = load(str(self.root / "config.toml"))
        self.assertEqual(meta["repos"], list(cfg.repos))
        self.assertEqual(meta["roles"], [r.name for r in cfg.roles])
        self.assertIn({"value": "needs", "label": "Needs you"}, meta["filters"])
        self.assertEqual(meta["priorities"], ["high", "normal", "low", "pm"])
        s, _, st = self.api("GET", "/api/v1/status")
        self.assertEqual(s, 200)
        self.assertEqual((st["live"], st["paused"], st["healthy"]), (not cfg.dry_run, None, False))
        self.assertEqual(set(st["tiles"]), {"needs", "asking", "deciding", "running", "working_at", "open_prs", "checks_running",
                                            "checks_failing", "passed_today", "runs_today", "failed"})

    def test_status_asks_github_when_the_last_read_is_old_instead_of_counting_a_stale_record(self):
        dbm.record(self.db, REPO, 13, "t", "human", "needs a person: low confidence")      # the database still says a person was called
        dbm.add_event(self.db, "decision", "needs a person: low confidence", REPO, 13)
        with mock.patch.object(L, "needs_cached", return_value=None), mock.patch.object(L, "needs_you", return_value=[]) as read:
            st = self.api("GET", "/api/v1/status")[2]
        read.assert_called_once()
        self.assertEqual((st["tiles"]["needs"], st["tiles"]["asking"], st["tiles"]["deciding"], st["needs_known"]), (0, 0, 0, True))
        with mock.patch.object(L, "needs_cached", return_value=None), mock.patch.object(L, "needs_you", return_value=None):
            st = self.api("GET", "/api/v1/status")[2]              # GitHub could not be read: the record is all there is, and it says so
        self.assertEqual((st["tiles"]["needs"], st["needs_known"]), (1, False))

    def test_status_uses_the_recent_read_without_calling_github(self):
        with mock.patch.object(L, "needs_cached", return_value=[]), mock.patch.object(L, "needs_you", side_effect=AssertionError("read GitHub")):
            self.assertTrue(self.api("GET", "/api/v1/status")[2]["needs_known"])

    def test_status_gives_the_usage_reason_but_not_the_token_fingerprint(self):
        dbm.set_status(self.db, "claude_usage", json.dumps({"fetched": 1.0, "error": "HTTP 403", "reason": "cannot read it", "token": "ab12cd34ef56"}))
        u = self.api("GET", "/api/v1/status")[2]["usage"]
        self.assertEqual((u["error"], u["reason"]), ("HTTP 403", "cannot read it"))
        self.assertNotIn("token", u)

    def test_pause_and_resume(self):
        s, _, body = self.api("POST", "/api/v1/pause", {})
        self.assertEqual((s, body["ok"]), (200, True))
        self.assertTrue((self.state / "PAUSED").exists())
        self.assertEqual(self.api("GET", "/api/v1/status")[2]["paused"], "manual pause")
        self.api("POST", "/api/v1/resume", {})
        self.assertFalse((self.state / "PAUSED").exists())

    def test_runs_run_and_events(self):
        rid = dbm.start_run(self.db, "stage", REPO, 7, "Add <the> thing", "claude-code", "sonnet", "medium", "", "designer")
        dbm.finish_run(self.db, rid, "stage", "designer document ready", "", "# The design\n\x1b[31mbody", "agent log tail",
                       {"tokens_in": 10, "tokens_out": 5})
        dbm.add_event(self.db, "alert:pr_ready", "PR <ready>", REPO, 7, rid)
        s, _, runs = self.api("GET", "/api/v1/runs")
        self.assertEqual((s, runs["page"], runs["more"]), (200, 1, False))
        (r,) = runs["runs"]
        self.assertEqual((r["id"], r["title"], r["status"], r["tokens"]), (rid, "Add <the> thing", "stage", {"in": 10, "out": 5}))
        self.assertNotIn("output", r)
        s, _, run = self.api("GET", f"/api/v1/runs/{rid}")
        self.assertEqual(run["output"], "# The design\n\x1b[31mbody")            # as stored: the client cleans it for the terminal
        self.assertEqual(run["log_tail"], "agent log tail")
        self.assertEqual(self.api("GET", "/api/v1/runs/999999")[0], 404)
        self.assertEqual(self.api("GET", "/api/v1/runs?status=bogus")[0], 422)
        self.assertEqual(self.api("GET", "/api/v1/runs?repo=evil/repo")[0], 422)
        s, _, ev = self.api("GET", "/api/v1/events?kind=all")
        self.assertEqual(ev["events"][0]["message"], "PR <ready>")
        self.assertEqual(self.api("GET", "/api/v1/events?kind=a%20b")[0], 422)

    def test_stage_documents(self):
        rid = dbm.start_run(self.db, "stage", REPO, 7, "T", "claude-code", "sonnet", "medium", "", "analyst")
        dbm.finish_run(self.db, rid, "stage", "done", "", "# Analysis\nThe <b>plan</b>.")
        s, _, doc = self.api("GET", f"/api/v1/tickets/{REPO}/7/docs/analyst")
        self.assertEqual((s, doc["stage"], doc["run_id"], doc["cut_short"]), (200, "analyst", rid, False))
        self.assertIn("The <b>plan</b>.", doc["text"])
        self.assertEqual(self.api("GET", f"/api/v1/tickets/{REPO}/7/docs/designer")[0], 404)
        self.assertEqual(self.api("GET", f"/api/v1/tickets/{REPO}/7/docs/bogus")[0], 404)
        self.assertEqual(self.api("GET", "/api/v1/tickets/evil/repo/7/docs/analyst")[0], 404)

    def test_needs_without_a_github_token_says_so(self):
        s, _, body = self.api("GET", "/api/v1/needs")
        self.assertEqual((s, body["available"], body["reason"]), (200, False, L.NO_TOKEN))

    def test_a_missing_database_is_reported(self):
        self.db.close()
        (self.state / "factory.db").unlink()
        s, _, body = self.api("GET", "/api/v1/runs")
        self.assertEqual((s, body["code"]), (503, "no_database"))


class LocalTickets(ApiCase):
    def setUp(self):
        super().setUp()
        self.local_on()

    def test_create_list_show_comment_edit_priority_close_reopen(self):
        s, _, made = self.api("POST", "/api/v1/tickets", {"repo": REPO, "title": "Fix the  <footer>", "body": "It **wraps**.", "where": "local"})
        self.assertEqual((s, made["issue"], made["ref"]), (201, LOCAL, "L-1"))
        self.assertIn("Created", made["message"])
        s, _, again = self.api("POST", "/api/v1/tickets", {"repo": REPO, "title": "Fix the <footer>", "where": "local"})
        self.assertEqual((s, again["error"]), (409, "That ticket was just created."))            # the double-click guard
        s, _, lst = self.api("GET", "/api/v1/tickets?stage=new")
        (row,) = lst["tickets"]
        self.assertEqual((row["ref"], row["title"], row["state"], row["local"]), ("L-1", "Fix the <footer>", "new", True))
        s, _, t = self.api("GET", f"/api/v1/tickets/{REPO}/{LOCAL}?cached=1")
        self.assertEqual(s, 200)
        self.assertEqual((t["local"]["body"], t["local"]["state"]), ("It **wraps**.", "open"))
        self.assertIn("auto", [o["action"] for o in t["offers"]["start"]])
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/comment", {"body": "More <detail>"})[2]["message"], "Comment added.")
        s, _, bad = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/comment", {"body": "  "})
        self.assertEqual((s, bad["code"], bad["error"]), (422, "invalid", "Write a comment first."))
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/edit", {"title": "Fix the footer"})[0], 200)
        t = self.api("GET", f"/api/v1/tickets/{REPO}/{LOCAL}?cached=1")[2]
        self.assertEqual((t["title"], t["local"]["body"]), ("Fix the footer", "It **wraps**."))          # a body not sent is kept
        self.assertEqual(t["local"]["comments"][-1]["body"], "More <detail>")
        s, _, p = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/priority", {"priority": "high"})
        self.assertEqual((s, p["message"]), (200, "Pinned at High. The project manager will not change it."))
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/priority", {"priority": "urgent"})[0], 422)
        s, _, st = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/start", {"action": "auto"})
        self.assertEqual((s, st["message"]), (200, L.FLASH["started"]))
        s, _, again = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/start", {"action": "auto"})
        self.assertEqual((s, again["code"]), (409, "refused"))
        self.assertTrue(again["error"].startswith("That cannot be started now"))
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/close", {})[2]["message"], "Closed your-org/standalone-service L-1.")
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/close", {})[2]["error"], "This ticket is already closed.")
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/reopen", {})[2]["message"], "Reopened L-1.")

    def test_create_checks_its_fields(self):
        for body, field in (({"repo": "evil/repo", "title": "x"}, None), ({"repo": REPO, "title": ""}, None),
                            ({"repo": REPO, "title": "x", "start": "rm -rf"}, None), ({"repo": REPO, "title": 5}, "title"),
                            ({"repo": REPO, "title": "x", "where": "mars"}, "where")):
            s, _, out = self.api("POST", "/api/v1/tickets", body)
            self.assertIn(s, (404, 422), body)
            if field:
                self.assertIn(field, out["fields"])
        self.assertEqual(self.api("GET", "/api/v1/tickets")[2]["tickets"], [])

    def test_unknown_tickets_are_not_found(self):
        self.assertEqual(self.api("GET", f"/api/v1/tickets/{REPO}/{LOCAL + 5}")[0], 404)
        self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL + 5}/comment", {"body": "hi"})[0], 404)
        self.assertEqual(self.api("GET", "/api/v1/tickets/evil/repo/7")[0], 404)
        self.assertEqual(self.api("GET", "/api/v1/tickets?stage=bogus")[0], 422)
        self.assertEqual(self.api("GET", "/api/v1/tickets?project=nope")[0], 422)

    def test_answering_needs_the_github_token(self):
        self.api("POST", "/api/v1/tickets", {"repo": REPO, "title": "T", "where": "local"})
        s, _, out = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/answer", {"accept_all": True})
        self.assertEqual((s, out["code"], out["error"]), (409, "no_github_token", L.NO_TOKEN))

    def test_the_same_refusal_as_the_page(self):
        self.api("POST", "/api/v1/tickets", {"repo": REPO, "title": "T", "where": "local", "start": "auto"})
        cookie, csrf = self.session()
        self.post(cookie, csrf, "/tickets/start", {"repo": REPO, "n": str(LOCAL), "action": "auto", "back": "/tickets"})
        page = self.req("GET", "/tickets", cookie=cookie)[2]
        api = self.api("POST", f"/api/v1/tickets/{REPO}/{LOCAL}/start", {"action": "auto"})[2]["error"]
        self.assertTrue(api.startswith("That cannot be started now"))
        self.assertIn(views.esc(api), page)


class GitHubTickets(ApiCase):
    def setUp(self):
        super().setUp()
        I.save_secret(load(str(self.root / "config.toml")), "github", TOKEN, "subscription")
        self.gh = mock.MagicMock()
        self.gh.repo_labels.return_value = [{"name": "factory:ready"}, {"name": "factory:auto"}]
        self.gh.get_issue.return_value = {"number": 7, "title": "T", "state": "open", "labels": []}
        self.gh.issues.return_value = ([self.gh.get_issue.return_value], False)
        p = mock.patch("factory.ui.labels.GitHub", return_value=self.gh)
        p.start()
        self.addCleanup(p.stop)

    def test_start_queues_a_build_and_skip_clears_the_triggers(self):
        s, _, out = self.api("POST", f"/api/v1/tickets/{REPO}/7/start", {"action": "build"})
        self.assertEqual((s, out["message"]), (200, L.FLASH["started"]))
        self.assertEqual([(r, n, a) for r, n, a in dbm.approvals(self.db)], [(REPO, 7, "run")])
        s, _, out = self.api("POST", f"/api/v1/tickets/{REPO}/7/start", {"action": "nonsense"})
        self.assertEqual(s, 409)

    def test_answers_are_checked_then_recorded_as_from_the_api(self):
        with mock.patch("factory.ui.labels._record", return_value=True) as rec:
            s, _, out = self.api("POST", f"/api/v1/tickets/{REPO}/7/answer",
                                 {"stage": "analyst", "answers": {"q1": {"option": "a"}, "q2": {"other": " my words "}}})
        self.assertEqual((s, out["done"], out["message"]), (200, True, L.FLASH["continued"]))
        args = rec.call_args.args
        self.assertEqual((args[5], args[6], args[7], args[8]), ("analyst", {"q1": ("option", "a"), "q2": ("other", "my words")}, False, "factory API"))
        for bad in ({"answers": {"q1": "a"}}, {"answers": {"q1": {"option": "a", "other": "b"}}}, {"answers": {"q1": {"pick": "a"}}},
                    {"answers": []}, {"stage": "analyst"}, {"stage": "Not A Stage", "answers": {"q1": {"option": "a"}}}):
            self.assertEqual(self.api("POST", f"/api/v1/tickets/{REPO}/7/answer", bad)[0], 422, bad)

    def test_answer_all(self):
        with mock.patch("factory.ui.labels._record", side_effect=[True, L.Refused("closed")]):
            s, _, out = self.api("POST", "/api/v1/answer-all", {"tickets": [f"{REPO}#7", f"{REPO}#8"]})
        self.assertEqual((s, out["done"], out["failed"]), (200, [f"{REPO}#7"], [f"{REPO}#8"]))
        self.assertEqual(self.api("POST", "/api/v1/answer-all", {"tickets": "x"})[0], 422)
        self.assertEqual(self.api("POST", "/api/v1/answer-all", {"tickets": ["not a ref"]})[0], 422)

    def test_merge_only_a_passed_pr(self):
        self.db.execute("INSERT INTO prs (repo, number, issue_repo, issue_num, status, rounds, watch_started, updated) VALUES (?,?,?,?,?,?,?,?)",
                        (REPO, 12, REPO, 7, "watching", 0, 1.0, 1.0))
        self.db.commit()
        s, _, out = self.api("POST", f"/api/v1/prs/{REPO}/12/merge", {})
        self.assertEqual((s, out["error"]), (409, "Only a pull request whose checks passed can be merged here."))
        self.db.execute("UPDATE prs SET status='passed'")
        self.db.commit()
        self.gh.get_pr.return_value = {"state": "open", "merged": False, "draft": False, "head": {"sha": "abc"}}
        s, _, out = self.api("POST", f"/api/v1/prs/{REPO}/12/merge", {})
        self.assertEqual((s, out["message"]), (200, f"Merged {REPO}#12."))
        self.gh.merge_pr.assert_called_once_with(REPO, 12, "abc")
        self.assertEqual(self.api("POST", "/api/v1/prs/evil/repo/12/merge", {})[0], 409)              # not one the factory watches

    def test_github_failures_are_502_with_a_fixed_message(self):
        import urllib.error
        self.gh.get_issue.side_effect = urllib.error.HTTPError("https://api.github.com/x?token=secret", 500, "boom", {}, None)
        s, _, out = self.api("POST", f"/api/v1/tickets/{REPO}/7/close", {})
        self.assertEqual((s, out["code"], out["error"]), (502, "github", "GitHub rejected the request (500)."))
        self.assertNotIn("secret", json.dumps(out))

    def test_ticket_detail_reads_its_questions(self):
        s, _, t = self.api("GET", f"/api/v1/tickets/{REPO}/7")
        self.assertEqual((s, t["repo"], t["issue"], t["ref"]), (200, REPO, 7, "#7"))
        self.assertEqual(set(t), {"repo", "issue", "ref", "local", "title", "state", "closed", "at", "at_label", "when", "why", "decision",
                                  "lined_up", "priority", "priority_source", "prs", "need", "journey", "docs", "offers",
                                  "queued", "outcome", "events"} | {"local"})
        self.assertFalse(re.search(r"&lt;|&gt;|&amp;", json.dumps(t)))


if __name__ == "__main__":
    import unittest
    unittest.main()
