"""Playwright runs for the Screens board: the Build screens button, the worker job it becomes, and the screens it brings back."""
import base64
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from factory import captures as CP, jobs
from factory import screenboard as SB
from factory import db as dbm
from factory.config import ScreenCapture, ScreensCfg, WorkersCfg, load

from test_review_notes import png
from test_ui import UiCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                       # noqa: E402

SHOP, SOLO = "your-org/shop-web", "your-org/standalone-service"
SHA = "b" * 40
PARAMS = json.dumps({"purpose": "screens"})


class FakeGh:
    token = "t"

    def default_branch(self, repo):
        return "main"

    def branch_sha(self, repo, branch):
        return SHA


def make_cfg(workers=True):
    cfg = load("config.example.toml")
    return replace(cfg, workers=WorkersCfg(enabled=workers),
                   screens=ScreensCfg(captures=(ScreenCapture(SHOP), ScreenCapture(SOLO, "playwright-screens", "linux"))))


def finish(db, jid, n, status="passed"):
    """Run a screens job to the end the way the worker API would, with n screenshots."""
    jobs.claim(db, "w1", "linux", ["playwright-screens"], 100.0, 120, 900, 2)
    body = {"status": status, "exit_code": 0 if status == "passed" else 1, "log": "ok",
            "artifacts": [{"name": f"shot-{i}", "png_b64": base64.b64encode(png(1280, 800)).decode()} for i in range(n)]}
    return jobs.complete(db, jid, "w1", body, 200.0)


class Queue(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")
        self.cfg = make_cfg()

    def test_a_request_becomes_one_job_for_the_workers_recipe_at_the_default_branch(self):
        CP.request(self.db, [SHOP, SOLO], 10.0)
        self.assertEqual(CP.tick(self.cfg, FakeGh(), self.db, 11.0), 2)
        rows = {j["repo"]: j for j in jobs.recent(self.db)}
        self.assertEqual((rows[SHOP]["recipe"], rows[SHOP]["platform"], rows[SHOP]["base_sha"]), ("playwright-screens", "any", SHA))
        self.assertEqual(rows[SOLO]["platform"], "linux")
        self.assertEqual(json.loads(jobs.get(self.db, rows[SHOP]["id"])["params"]), {"purpose": "screens"})
        self.assertEqual(jobs.get(self.db, rows[SHOP]["id"])["patch"], "")
        self.assertEqual(CP.requested(self.db), set())

    def test_pressing_twice_while_one_is_waiting_adds_nothing(self):
        CP.request(self.db, [SHOP], 10.0)
        CP.tick(self.cfg, FakeGh(), self.db, 11.0)
        CP.request(self.db, [SHOP], 12.0)
        self.assertEqual(CP.tick(self.cfg, FakeGh(), self.db, 13.0), 0)
        self.assertEqual(len(jobs.recent(self.db)), 1)

    def test_only_configured_repos_run_and_only_with_workers_on(self):
        CP.request(self.db, ["your-org/other"], 10.0)
        self.assertEqual(CP.tick(self.cfg, FakeGh(), self.db, 11.0), 0)
        CP.request(self.db, [SHOP], 10.0)
        self.assertEqual(CP.tick(make_cfg(workers=False), FakeGh(), self.db, 11.0), 0)
        self.assertEqual(jobs.recent(self.db), [])

    def test_a_github_failure_queues_nothing_and_does_not_raise(self):
        class Down(FakeGh):
            def branch_sha(self, repo, branch):
                raise OSError("down")
        CP.request(self.db, [SHOP], 10.0)
        self.assertEqual(CP.tick(self.cfg, Down(), self.db, 11.0), 0)

    def test_the_newest_finished_run_and_its_shots(self):
        CP.request(self.db, [SHOP], 10.0)
        CP.tick(self.cfg, FakeGh(), self.db, 11.0)
        self.assertEqual(CP.active(self.db, SHOP)["status"], "queued")
        self.assertIsNone(CP.latest(self.db, SHOP))
        self.assertEqual(finish(self.db, CP.active(self.db, SHOP)["id"], 12), "")        # more than the 8 an ordinary check may return
        run = CP.latest(self.db, SHOP)
        self.assertEqual((run["status"], len(run["shots"]), CP.active(self.db, SHOP)), ("passed", 12, None))

    def test_old_runs_are_dropped_with_their_images(self):
        ids = []
        for i in range(CP.KEEP_RUNS + 2):
            ids.append(jobs.enqueue(self.db, SHOP, 0, SHA, "", "playwright-screens", "any", float(i), PARAMS))
            finish(self.db, ids[-1], 1)
        CP.tick(self.cfg, FakeGh(), self.db, 500.0)
        left = [j["id"] for j in jobs.recent(self.db)]
        self.assertEqual(sorted(left), sorted(ids[-CP.KEEP_RUNS:]))
        self.assertEqual(jobs.artifacts(self.db, ids[0]), {})

    def test_an_ordinary_check_still_gets_only_eight_screenshots(self):
        jid = jobs.enqueue(self.db, SHOP, 5, SHA, "diff", "web-test", "any", 1.0)
        jobs.claim(self.db, "w1", "linux", ["web-test"], 100.0, 120, 900, 2)
        arts = [{"name": f"s-{i}", "png_b64": base64.b64encode(png(400, 400)).decode()} for i in range(9)]
        self.assertIn("at most 8", jobs.complete(self.db, jid, "w1", {"status": "passed", "exit_code": 0, "log": "", "artifacts": arts}, 2.0))

    def test_a_screens_job_is_told_apart_by_its_params(self):
        self.assertTrue(jobs.is_screens({"params": PARAMS}))
        self.assertFalse(jobs.is_screens({"params": ""}))
        self.assertFalse(jobs.is_screens({"params": "not json"}))
        self.assertFalse(jobs.is_screens({"params": json.dumps({"smells": []})}))


class TicketGh(FakeGh):
    """Records the tickets opened; `state` is what GitHub says of an existing one."""
    def __init__(self, state="open"):
        self.made, self.state = [], state

    def create_scheduled_issue(self, repo, title, body, labels):
        self.made.append((repo, title, body, labels))
        return {"number": 40 + len(self.made)}

    def get_issue(self, repo, issue):
        return {"state": self.state}


class FailTicket(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")
        self.cfg = make_cfg()

    def run_once(self, status="failed", log="2 failed, 5 passed"):
        jid = jobs.enqueue(self.db, SHOP, 0, SHA, "", "playwright-screens", "any", 1.0, PARAMS)
        jobs.claim(self.db, "w1", "linux", ["playwright-screens"], 100.0, 120, 900, 2)
        code = {"passed": 0, "failed": 1}[status]
        self.assertEqual(jobs.complete(self.db, jid, "w1", {"status": status, "exit_code": code, "log": log, "artifacts": []}, 200.0), "")
        return jid

    def test_failing_tests_open_one_unlabelled_ticket_with_the_log_fenced(self):
        self.run_once(log="FAILED tests/e2e/test_home.py::test_title ```@someone``` ")
        gh = TicketGh()
        CP.tick(self.cfg, gh, self.db, 300.0)
        CP.tick(self.cfg, gh, self.db, 301.0)                       # the same run never opens a second one
        self.assertEqual(len(gh.made), 1)
        repo, title, body, labels = gh.made[0]
        self.assertEqual((repo, labels), (SHOP, []))
        self.assertIn("Fix the failing Playwright tests", title)
        self.assertIn("untrusted", body)
        fence = body.split("```text\n", 1)[1]
        self.assertIn("test_home.py::test_title", fence)
        self.assertEqual(fence.count("```"), 1)                      # the log cannot close the fence

    def test_a_long_log_is_cut_to_its_end(self):
        self.run_once(log="x" * (CP.LOG_TAIL + 500) + "THE-END")
        gh = TicketGh()
        CP.tick(self.cfg, gh, self.db, 300.0)
        self.assertIn("THE-END", gh.made[0][2])
        self.assertIn("it is longer", gh.made[0][2])

    def test_a_passing_run_opens_nothing(self):
        self.run_once("passed")
        gh = TicketGh()
        CP.tick(self.cfg, gh, self.db, 300.0)
        self.assertEqual(gh.made, [])

    def test_while_the_ticket_is_open_a_new_failure_opens_no_other(self):
        self.run_once()
        gh = TicketGh("open")
        CP.tick(self.cfg, gh, self.db, 300.0)
        self.run_once()
        CP.tick(self.cfg, gh, self.db, 400.0)
        self.assertEqual(len(gh.made), 1)

    def test_once_the_ticket_is_closed_a_new_failure_opens_another(self):
        self.run_once()
        gh = TicketGh("closed")
        CP.tick(self.cfg, gh, self.db, 300.0)
        self.run_once()
        CP.tick(self.cfg, gh, self.db, 400.0)
        self.assertEqual(len(gh.made), 2)

    def test_a_github_failure_is_tried_again_at_the_next_poll(self):
        self.run_once()
        class Down(TicketGh):
            def create_scheduled_issue(self, *a):
                raise OSError("down")
        CP.tick(self.cfg, Down(), self.db, 300.0)
        gh = TicketGh()
        CP.tick(self.cfg, gh, self.db, 301.0)
        self.assertEqual(len(gh.made), 1)


class Config(unittest.TestCase):
    def parse(self, extra):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text(Path("config.example.toml").read_text() + extra)
            return load(str(p))

    def test_captures_are_read(self):
        c = self.parse(f'\n[screens]\n[[screens.captures]]\nrepo = "{SHOP}"\n[[screens.captures]]\nrepo = "{SOLO}"\nrecipe = "pw"\nplatform = "macos"\n')
        self.assertEqual(c.screens.captures, (ScreenCapture(SHOP), ScreenCapture(SOLO, "pw", "macos")))

    def test_bad_captures_are_refused(self):
        for bad in ('repo = "your-org/unknown"', f'repo = "{SHOP}"\nrecipe = "rm -rf"', f'repo = "{SHOP}"\nplatform = "A B"'):
            with self.assertRaises(ValueError, msg=bad):
                self.parse(f"\n[screens]\n[[screens.captures]]\n{bad}\n")
        with self.assertRaises(ValueError):
            self.parse(f'\n[screens]\n[[screens.captures]]\nrepo = "{SHOP}"\n[[screens.captures]]\nrepo = "{SHOP}"\n')


class Board(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{SHOP}"\n[[screens.captures]]\nrepo = "{SOLO}"\n')
        self.cookie, self.csrf = self.session()
        self.cfg = load(str(p))

    def post(self, **fields):
        return self.req("POST", "/screens/capture", urlencode({"csrf": self.csrf, **fields}), cookie=self.cookie)

    def board(self):
        return self.req("GET", "/screens", cookie=self.cookie)[2]

    def test_groups_by_project_with_a_button_each_and_build_all(self):
        html = self.board()
        self.assertIn("<h2>shop</h2>", html)
        self.assertIn("<h2>Other repositories</h2>", html)
        self.assertIn(">Build screens</button>", html)
        self.assertIn(">Build all</button>", html)
        self.assertIn('data-src="/screens/captures"', html)
        self.assertIn("Not built yet.", html)
        self.assertNotIn("style=", html.split("<main")[1])

    def test_the_button_asks_and_the_card_says_so(self):
        s, h, _ = self.post(project="shop")
        self.assertEqual((s, h["Location"]), (303, "/screens"))
        self.assertEqual(CP.requested(self.db), {SHOP})
        html = self.board()
        self.assertIn("Building screens.", html)
        self.assertIn("Starts at the factory", html)
        self.assertIn("Building…", html)

    def test_build_all_and_other_repositories(self):
        self.post(project="*")
        self.assertEqual(CP.requested(self.db), {SHOP, SOLO})
        self.db.execute("DELETE FROM capture_requests")
        self.db.commit()
        self.post(project="")
        self.assertEqual(CP.requested(self.db), {SOLO})

    def test_unknown_projects_and_workers_off_write_nothing(self):
        self.post(project="nope")
        self.assertEqual(CP.requested(self.db), set())
        p = self.root / "config.toml"
        p.write_text(p.read_text().replace("[workers]\nenabled = true", "[workers]\nenabled = false"))
        self.post(project="shop")
        self.assertEqual(CP.requested(self.db), set())
        self.assertIn("Turn on verification workers", self.board())

    def test_needs_a_session_and_a_csrf_token(self):
        self.assertEqual(self.req("POST", "/screens/capture", "project=*")[0], 303)
        self.assertEqual(self.req("POST", "/screens/capture", "project=*", cookie=self.cookie)[0], 403)
        self.assertEqual(CP.requested(self.db), set())

    def test_waiting_running_and_finished_runs(self):
        jid = jobs.enqueue(self.db, SHOP, 0, SHA, "", "playwright-screens", "any", 1.0, PARAMS)
        self.assertIn("Waiting for a worker", self.board())
        jobs.claim(self.db, "arch", "linux", ["playwright-screens"], 2.0, 120, 99999999, 2)
        self.assertIn("Running on arch", self.board())
        jobs.complete(self.db, jid, "arch", {"status": "failed", "exit_code": 1, "log": "<b>x</b>", "artifacts": [
            {"name": "login-chromium", "png_b64": base64.b64encode(png(1280, 800)).decode()}]}, 3.0)
        html = self.board()
        self.assertIn("some tests failed", html)
        self.assertIn("1 screen ·", html)
        self.assertIn(f"/workers/job?id={jid}", html)
        key = SB.key(SHOP, "login", "chromium", SHA)
        self.assertIn(("/screens/review?" + urlencode({"img": key})).replace("&", "&amp;"), html)        # a card's thumbnail opens the review tool
        s, h, _ = self.req("GET", SB.src(key), cookie=self.cookie)
        self.assertEqual((s, h["Content-Type"]), (200, "image/png"))
        self.assertIn("Open all 1 on the canvas", html)

    def test_a_repo_with_no_suite_says_so_instead_of_failing(self):
        jid = jobs.enqueue(self.db, SHOP, 0, SHA, "", "playwright-screens", "any", 1.0, PARAMS)
        jobs.claim(self.db, "arch", "linux", ["playwright-screens"], 2.0, 120, 99999999, 2)
        jobs.complete(self.db, jid, "arch", {"status": "failed", "exit_code": 2, "log": "[playwright-screens] no Playwright config found <b>x</b>", "artifacts": []}, 3.0)
        html = self.board()
        self.assertIn("nothing to run", html)
        self.assertIn("no Playwright config found &lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("some tests failed", html)

    def test_the_fragment_is_what_the_page_refreshes_with(self):
        s, _, html = self.req("GET", "/screens/captures", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertIn("<h2>shop</h2>", html)
        self.assertNotIn("<html", html)
        self.assertEqual(self.req("GET", "/screens/captures")[0], 303)


def run_with_shots(db, repo, names, sha=SHA, now=100.0):
    """A finished Playwright job that brought back `names`, imported to the board the way jobs.complete does."""
    jid = jobs.enqueue(db, repo, 0, sha, "", "playwright-screens", "any", now, PARAMS)
    jobs.claim(db, "w1", "linux", ["playwright-screens"], now, 120, 99999999, 2)
    arts = [{"name": n, "png_b64": base64.b64encode(png(1280, 800)).decode()} for n in names]
    assert jobs.complete(db, jid, "w1", {"status": "failed", "exit_code": 1, "log": "", "artifacts": arts}, now + 1) == ""
    return jid


class Split(unittest.TestCase):
    def test_names_become_a_page_and_a_viewport(self):
        cases = {"tickets-desktop": ("tickets", "desktop"), "labels-issue-repo-x-n-7-phone": ("labels-issue-repo-x-n-7", "phone"),
                 "home-spec-shows-home-mobile-1123493802": ("home-spec-shows-home", "mobile"), "login-spec-signs-in-chromium-99999": ("login-spec-signs-in", "chromium"),
                 "home-a-failing-one-test-failed-1-2374389124": ("home-a-failing-one-test-failed-1", "run"), "x": ("x", "run")}
        for name, want in cases.items():
            self.assertEqual(CP.split_name(name), want, name)

    def test_a_name_that_cannot_be_a_page_still_gets_one(self):
        self.assertEqual(CP.split_name("-desktop")[1], "run")
        self.assertEqual(CP.split_name("a" * 200 + "-phone"), ("a" * 60, "phone"))


class Canvas(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{SHOP}"\n')
        self.cookie, self.csrf = self.session()
        run_with_shots(self.db, SHOP, ["home-desktop", "home-tablet", "home-phone", "tickets-desktop", "tickets-phone", "login-spec-x-chromium-12345"])

    def get(self, path):
        return self.req("GET", path, cookie=self.cookie)

    def test_a_finished_run_lands_on_the_board_grouped_by_page_and_viewport(self):
        got = SB.latest(self.db)
        self.assertEqual({(p, v) for (r, p, v) in got}, {("home", "desktop"), ("home", "tablet"), ("home", "phone"), ("tickets", "desktop"),
                                                          ("tickets", "phone"), ("login-spec-x", "chromium")})
        self.assertTrue(all(x["sha"] == SHA for x in got.values()))

    def test_the_canvas_has_every_screen_in_one_place_with_the_viewports_in_order(self):
        s, _, html = self.get("/screens/canvas?" + urlencode({"repo": SHOP}))
        self.assertEqual(s, 200)
        self.assertIn("6 screens on 3 pages", html)
        for page in ("home", "tickets", "login-spec-x"):
            self.assertIn(f"<h3>{page}</h3>", html)
        self.assertLess(html.index("cv-desktop"), html.index("cv-tablet"))
        self.assertLess(html.index("cv-tablet"), html.index("cv-phone"))
        key = SB.key(SHOP, "home", "tablet", SHA)
        self.assertIn(("/screens/review?" + urlencode({"img": key})).replace("&", "&amp;"), html)
        self.assertIn('data-zoom="3"', html)
        self.assertNotIn("style=", html.split("<main")[1])

    def test_only_a_repo_with_a_playwright_run_has_a_canvas_and_it_needs_a_session(self):
        self.assertEqual(self.get("/screens/canvas?repo=your-org/other")[0], 404)
        self.assertEqual(self.get("/screens/canvas")[0], 404)
        self.assertEqual(self.req("GET", "/screens/canvas?" + urlencode({"repo": SHOP}))[0], 303)

    def test_a_screen_opens_in_the_review_tool_with_its_viewports_as_tabs_and_a_way_back(self):
        key = SB.key(SHOP, "home", "desktop", SHA)
        s, _, html = self.get("/screens/review?" + urlencode({"img": key}))
        self.assertEqual(s, 200)
        self.assertIn("home · desktop", html)
        self.assertIn("home · tablet", html)
        self.assertNotIn("tickets · desktop", html)
        self.assertIn("/screens/canvas?repo=", html)

    def test_a_note_on_a_screen_counts_on_the_canvas_and_can_become_a_ticket_note(self):
        from factory import reviewnotes as RN
        key = SB.key(SHOP, "tickets", "phone", SHA)
        body = urlencode({"csrf": self.csrf, "board": "1", "img": key, "text": "chips wrap", "x": "10", "y": "10", "w": "20", "h": "10"})
        s, h, _ = self.req("POST", "/review/add", body, cookie=self.cookie)
        self.assertEqual(s, 303)
        self.assertEqual([x["image"] for x in RN.notes(self.db, *RN.BOARD, open_only=True)], [key])
        html = self.get("/screens/canvas?" + urlencode({"repo": SHOP}))[2]
        self.assertIn('class="rv-count" title="Open notes">1<', html)
        self.assertIn("1 open notes", html)

    def test_a_newer_run_replaces_the_canvas_but_a_note_keeps_its_older_screen(self):
        from factory import reviewnotes as RN
        old = SB.key(SHOP, "tickets", "phone", SHA)
        RN.ensure_tables(self.db)
        RN.add(self.db, *RN.BOARD, old, (1, 1, 10, 10), "fix")
        self.db.commit()
        run_with_shots(self.db, SHOP, ["home-desktop", "home-tablet"], sha="d" * 40, now=500.0)
        html = self.get("/screens/canvas?" + urlencode({"repo": SHOP}))[2]
        self.assertIn("2 screens on 1 pages", html)
        self.assertNotIn("<h3>tickets</h3>", html)
        self.assertIsNotNone(SB.image(self.db, old))                                   # kept: an open note is on it
        self.assertIsNone(SB.image(self.db, SB.key(SHOP, "home", "tablet", SHA)))        # no note, and a newer run has its own home: replaced

    def test_a_run_that_kept_nothing_leaves_the_previous_canvas(self):
        jid = jobs.enqueue(self.db, SHOP, 0, "e" * 40, "", "playwright-screens", "any", 900.0, PARAMS)
        jobs.claim(self.db, "w1", "linux", ["playwright-screens"], 900.0, 120, 99999999, 2)
        jobs.complete(self.db, jid, "w1", {"status": "failed", "exit_code": 2, "log": "no suite", "artifacts": []}, 901.0)
        self.assertIn("6 screens on 3 pages", self.get("/screens/canvas?" + urlencode({"repo": SHOP}))[2])


class SetUp(UiCase):
    """Everything is set up from the Screens page: nothing needs a config file."""
    def setUp(self):
        super().setUp()
        self.cookie, self.csrf = self.session()
        self.path = self.root / "config.toml"

    def save(self, repos, **extra):
        fields = [("csrf", self.csrf)] + [("repo", r) for r in repos] + list(extra.items())
        return self.req("POST", "/screens/captures/save", urlencode(fields), cookie=self.cookie)

    def overrides(self):
        import tomllib
        from factory import config
        p = config.overrides_path(str(self.path))
        return tomllib.loads(p.read_text()) if p.exists() else {}

    def page(self):
        return self.req("GET", "/screens", cookie=self.cookie)[2]

    def test_the_first_visit_explains_what_is_missing_and_offers_the_repos(self):
        html = self.page()
        self.assertIn("Set up Playwright runs", html)
        self.assertIn("<details class=\"cp-setup\" open>", html)
        self.assertIn("Settings → Workers", html)
        self.assertIn("No online worker has the Playwright recipe", html)
        self.assertIn('name="repo" value="your-org/shop-web"', html)
        self.assertNotIn(">Build screens</button>", html)

    def test_choosing_repos_saves_to_the_overrides_and_shows_the_buttons(self):
        before = self.path.read_text()
        s, h, _ = self.save([SHOP, SOLO], recipe_0="", platform_0="")
        self.assertEqual((s, h["Location"]), (303, "/screens"))
        caps = self.overrides()["screens"]["captures"]
        self.assertEqual({c["repo"] for c in caps}, {SHOP, SOLO})
        self.assertTrue(all(set(c) == {"repo"} for c in caps))                       # defaults are not written
        self.assertEqual(self.path.read_text(), before)                              # config.toml is untouched
        self.assertTrue((self.state / "RESTART").exists())
        html = self.page()
        self.assertIn(">Build screens</button>", html)
        self.assertIn("Saved.", html)

    def test_recipe_and_platform_per_repo_and_switching_everything_off(self):
        repos = list(dict.fromkeys(list(load("config.example.toml").repos) + ["your-org/shop-web", "your-org/shop-mobile", "your-org/shop-packages"]))
        i = repos.index(SHOP)
        self.save([SHOP], **{f"recipe_{i}": "pw-web", f"platform_{i}": "linux"})
        self.assertEqual(self.overrides()["screens"]["captures"], [{"repo": SHOP, "recipe": "pw-web", "platform": "linux"}])
        self.save([])
        self.assertEqual(self.overrides().get("screens", {}).get("captures", []), [])
        self.assertIn("Playwright runs switched off.", self.page())

    def test_unknown_repos_and_bad_names_write_nothing(self):
        self.save(["your-org/unknown"])
        self.assertEqual(self.overrides(), {})
        self.assertIn("Choose repositories the factory handles.", self.page())
        repos = list(dict.fromkeys(list(load("config.example.toml").repos) + ["your-org/shop-web", "your-org/shop-mobile", "your-org/shop-packages"]))
        self.save([SHOP], **{f"recipe_{repos.index(SHOP)}": "rm -rf"})
        self.assertEqual(self.overrides(), {})
        self.assertIn("The configuration would be invalid", self.page())

    def test_the_checklist_ticks_off_as_things_are_ready(self):
        p = self.path
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{SHOP}"\n')
        jobs.touch_worker(self.db, "arch-laptop", "linux", ["playwright-screens", "web-test"], 1, __import__("time").time())
        self.db.commit()
        html = self.page()
        self.assertIn("A worker with the Playwright recipe is online (arch-laptop).", html)
        self.assertIn("1 repository chosen.", html)
        self.assertNotIn("<details class=\"cp-setup\" open>", html)                   # all ready: folded away
        jobs.touch_worker(self.db, "arch-laptop", "linux", ["web-test"], 1, __import__("time").time())     # same worker, no such recipe
        self.db.commit()
        self.assertIn("No online worker has the Playwright recipe", self.page())

    def test_needs_a_session_and_a_csrf_token(self):
        self.assertEqual(self.req("POST", "/screens/captures/save", "repo=" + SHOP)[0], 303)
        self.assertEqual(self.req("POST", "/screens/captures/save", "repo=" + SHOP, cookie=self.cookie)[0], 403)
        self.assertEqual(self.overrides(), {})


class Collect(unittest.TestCase):
    def test_a_screens_run_may_return_many_but_within_a_total_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "screens-out").mkdir()
            for i in range(12):
                (root / "screens-out" / f"s-{i}.png").write_bytes(png(800, 600))
            r = W.Recipe("playwright-screens", {"command": ["x"], "artifacts": ["screens-out/*.png"]})
            self.assertEqual(len(W.collect_artifacts(r, root)), 8)
            self.assertEqual(len(W.collect_artifacts(r, root, W.MAX_SCREEN_ARTIFACTS)), 12)
            one = len(png(800, 600))
            self.assertEqual(len(W.collect_artifacts(r, root, 300, one * 5 + 1)), 5)

    def test_only_the_orchestrators_params_make_a_screens_job(self):
        self.assertTrue(W.is_screens({"params": {"purpose": "screens"}}))
        self.assertFalse(W.is_screens({"params": {"smells": []}}))
        self.assertFalse(W.is_screens({}))


RECIPE = Path(__file__).resolve().parent.parent / "worker" / "recipes" / "playwright-screens.sh"


class Recipe(unittest.TestCase):
    """The real script with stub npm/npx: `npx playwright test` drops screenshots where Playwright would."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        self.bin, self.repo, self.log = self.tmp / "bin", self.tmp / "repo", self.tmp / "calls.log"
        self.bin.mkdir()
        self.repo.mkdir()
        shot = "\\211PNG\\r\\n\\032\\n"
        npx = ('#!/bin/sh\necho "npx $*" >> "$CALLS"\n'
               'if [ "$3" = test ]; then mkdir -p test-results-screens/login-spec-user-signs-in-chromium test-results-screens/home-spec-shows-home-mobile\n'
               f'printf "{shot}" > test-results-screens/login-spec-user-signs-in-chromium/test-finished-1.png\n'
               f'printf "{shot}" > test-results-screens/home-spec-shows-home-mobile/test-failed-1.png\n'
               'cat playwright.screens.config.* >> "$CALLS"\n[ -n "$TESTS_FAIL" ] && exit 1\nfi\nexit 0\n')
        npm = '#!/bin/sh\necho "npm $*" >> "$CALLS"\nexit 0\n'
        for name, body in (("npx", npx), ("npm", npm)):
            f = self.bin / name
            f.write_text(body)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def project(self, sub="", config="playwright.config.ts", lock="package-lock.json", module=False):
        d = self.repo / sub if sub else self.repo
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text(json.dumps({"type": "module"} if module else {}))
        (d / lock).write_text("")
        (d / config).write_text("export default {}")

    def run_recipe(self, *args, tests_fail=False):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.log), "HOME": str(self.tmp), "TESTS_FAIL": "1" if tests_fail else ""}
        r = subprocess.run([str(RECIPE), *args], cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, self.log.read_text() if self.log.exists() else ""

    def kept(self):
        return sorted(p.name for p in (self.repo / "screens-out").glob("*.png"))

    def test_installs_runs_with_screenshots_forced_on_and_keeps_every_one_under_a_unique_name(self):
        self.project()
        code, out, calls = self.run_recipe()
        self.assertEqual(code, 0, out)
        self.assertIn("npm ci --no-audit --no-fund", calls)
        self.assertIn("npx --no-install playwright install chromium", calls)
        self.assertIn("playwright test --config playwright.screens.config.ts", calls)
        self.assertIn("mode: 'on', fullPage: true", calls)                       # forced on, whatever the project's config says
        names = self.kept()
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("home-spec-shows-home-mobile-test-failed-1-"), names)
        self.assertTrue(names[1].startswith("login-spec-user-signs-in-chromium-"), names)
        self.assertTrue(all(len(n[:-4]) <= 60 for n in names), names)

    def test_failing_tests_still_return_the_screenshots(self):
        self.project()
        code, out, _ = self.run_recipe(tests_fail=True)
        self.assertEqual((code, len(self.kept())), (1, 2), out)

    def test_a_monorepo_is_found_and_prefixed(self):
        self.project("web")
        self.project("apps/demo", config="playwright.config.js")
        code, out, calls = self.run_recipe()
        self.assertEqual(code, 0, out)
        self.assertTrue(any(n.startswith("web-") for n in self.kept()), self.kept())
        self.assertTrue(any(n.startswith("apps-demo-") for n in self.kept()), self.kept())
        self.assertIn("playwright.screens.config.cjs" if "module.exports" in calls else "playwright.screens.config", calls)

    def test_dir_limits_it_to_one_folder(self):
        self.project("web")
        self.project("apps/demo")
        self.assertEqual(self.run_recipe("--dir", "web")[0], 0)
        self.assertTrue(all(n.startswith("web-") for n in self.kept()), self.kept())

    def python_suite(self):
        """tests/e2e/requirements.txt, and a stub python3 whose `-m venv` makes a venv of stubs; its pytest drops a PNG into E2E_SCREENS_DIR."""
        (self.repo / "tests" / "e2e").mkdir(parents=True)
        (self.repo / "tests" / "e2e" / "requirements.txt").write_text("pytest\nplaywright\n")
        shot = "\\211PNG\\r\\n\\032\\n"
        py = ('#!/bin/sh\nif [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; for t in pip playwright python; do\n'
              '  printf \'#!/bin/sh\\necho "venv-%s $*; browser=[$E2E_BROWSER] dir=[$E2E_SCREENS_DIR]" >> "$CALLS"\\n\' "$t" > "$3/bin/$t"; chmod +x "$3/bin/$t"; done\n'
              f'  printf \'printf "{shot}" > "$E2E_SCREENS_DIR/factory-desktop.png"\\n[ -n "$TESTS_FAIL" ] && exit 1\\nexit 0\\n\' >> "$3/bin/python"; fi\nexit 0\n')
        f = self.bin / "python3"
        f.write_text(py)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def test_a_python_suite_runs_as_pytest_and_hands_its_screens_over(self):
        self.python_suite()
        code, out, calls = self.run_recipe()
        self.assertEqual(code, 0, out)
        self.assertIn("venv-pip install -q -r tests/e2e/requirements.txt", calls)
        self.assertIn("venv-playwright install chromium", calls)
        self.assertIn(f"venv-python -m pytest tests/e2e -q -p no:cacheprovider; browser=[] dir=[{self.repo}/screens-out]", calls)
        self.assertEqual(self.kept(), ["factory-desktop.png"])

    def test_a_given_browser_replaces_the_download_and_failing_tests_still_return_screens(self):
        self.python_suite()
        code, out, calls = self.run_recipe("--browser", "/usr/bin/chromium", tests_fail=True)
        self.assertEqual((code, self.kept()), (1, ["factory-desktop.png"]), out)
        self.assertNotIn("playwright install", calls)
        self.assertIn("browser=[/usr/bin/chromium]", calls)

    def test_a_javascript_suite_wins_over_a_python_one_and_dir_skips_the_python_path(self):
        self.python_suite()
        self.project("web")
        self.run_recipe()
        self.assertNotIn("venv-", self.log.read_text())
        self.assertEqual(self.run_recipe("--dir", "nothing-here")[0], 2)

    def test_nothing_to_run_is_a_recipe_problem_not_a_test_failure(self):
        (self.repo / "package.json").write_text("{}")
        self.assertEqual(self.run_recipe()[0], 2)
        self.assertEqual(self.run_recipe("--dir", "../x")[0], 2)
        self.assertEqual(self.run_recipe("--nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
