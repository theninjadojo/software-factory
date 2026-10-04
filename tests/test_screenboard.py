"""The Screens board: shots of every configured screen on the default branch, and its design canvas, grouped by journey."""
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from factory import config, reviewnotes as RN, screenboard as SB
from factory import db as dbm
from factory.config import RunnerCfg, ScreenPage, ScreensCfg, load
from factory.ui import integrations as I

from test_review_notes import png
from test_screens import mount
from test_ui import UiCase

REPO = "your-org/standalone-service"
SHA = "a" * 40
CANVAS = "<!doctype html><html><head><title>x</title></head><body><x-dc><p>Pay</p></x-dc></body></html>"
PAGES = (ScreenPage(REPO, "basket", "site/basket.html", journey="checkout", step=1),
         ScreenPage(REPO, "pay", "site/pay.html", journey="checkout", step=2, design="docs/design/pay.dc.html"),
         ScreenPage(REPO, "home", "site/index.html"))


class Fake:
    """Stands in for git, the shoot container and the canvas renderer."""
    def __init__(self, shots=None, canvases=None):
        self.shots = shots if shots is not None else {"basket-desktop": png(1440, 900), "basket-mobile": png(390, 844),
                                                      "pay-desktop": png(1440, 900), "pay-mobile": png(390, 844)}
        self.canvases = canvases if canvases is not None else {"pay": png(1200, 800)}
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if "/app/shoot.js" in cmd:
            for i, data in self.shots.items():
                (mount(cmd, "/out") / f"{i}.png").write_bytes(data)
        elif any(str(c).startswith("localhost/factory-render") for c in cmd):
            out = Path(next(v.split(":")[0] for v in cmd if v.endswith(":/out:rw")))
            for stem, data in self.canvases.items():
                (out / f"{stem}.png").write_bytes(data)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def checkout(dest: Path, canvas=CANVAS):
    (dest / "site").mkdir(parents=True)
    (dest / "docs" / "design").mkdir(parents=True)
    if canvas is not None:
        (dest / "docs" / "design" / "pay.dc.html").write_text(canvas)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x"], check=True)


class Board(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.db = dbm.connect(":memory:")
        cfg = load("config.example.toml")
        self.cfg = replace(cfg, screens=ScreensCfg(pages=PAGES), runner=replace(cfg.runner, work_dir=self._t.name + "/work"))

    def refresh(self, fake, canvas=CANVAS, keep=frozenset()):
        return SB.refresh(self.cfg, "t", self.db, set(keep), fake, clone=lambda repo, dest: checkout(dest, canvas))

    def test_every_page_and_viewport_is_kept_under_the_commit_with_the_canvas_beside_it(self):
        st = self.refresh(Fake())
        sha = st["repos"][REPO]["sha"]
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        got = SB.latest(self.db)
        self.assertEqual(sorted(got), sorted([(REPO, "basket", "desktop"), (REPO, "basket", "mobile"), (REPO, "pay", "desktop"),
                                              (REPO, "pay", "mobile"), (REPO, "pay", "design")]))
        self.assertEqual(got[(REPO, "pay", "design")]["key"], f"screen:{REPO}:pay:design:{sha}")
        self.assertIsNotNone(SB.image(self.db, got[(REPO, "pay", "design")]["key"]))
        self.assertIn("no usable screenshot for home-desktop, home-mobile", st["repos"][REPO]["problem"])   # a missing one is said
        self.assertEqual(SB.status(self.db)["repos"][REPO]["sha"], sha)

    def test_a_canvas_that_fails_the_designer_checks_is_never_rendered(self):
        fake = Fake()
        st = self.refresh(fake, canvas='<!doctype html><script>alert(1)</script>')
        self.assertNotIn((REPO, "pay", "design"), SB.latest(self.db))
        self.assertIn("design canvas missing or refused: docs/design/pay.dc.html", st["repos"][REPO]["problem"])
        self.assertFalse(any(str(c).startswith("localhost/factory-render") for cmd in fake.cmds for c in cmd))

    def test_the_renderer_output_is_logged_not_shown(self):
        class Fails(Fake):
            def __call__(self, cmd, **kw):
                res = super().__call__(cmd, **kw)
                return subprocess.CompletedProcess(cmd, 1, "", "page.goto: <b>evil</b> net::ERR") if "/app/shoot.js" in cmd else res
        with self.assertLogs("factory.screenboard", "WARNING") as logs:
            st = self.refresh(Fails(shots={}))
        self.assertIn("the screenshots could not be taken", st["repos"][REPO]["problem"])
        self.assertNotIn("evil", st["repos"][REPO]["problem"])
        self.assertIn("evil", "".join(logs.output))

    def test_unusable_pngs_are_dropped(self):
        self.refresh(Fake(shots={"basket-desktop": b"not a png"}, canvases={"pay": b"\x89PNG nope"}))
        self.assertEqual(SB.latest(self.db), {})

    def test_older_shots_go_unless_an_open_note_is_on_them(self):
        SB.store(self.db, REPO, "basket", "desktop", "1" * 40, png(100, 100), now=1)
        SB.store(self.db, REPO, "basket", "mobile", "2" * 40, png(100, 100), now=1)
        noted = SB.key(REPO, "basket", "mobile", "2" * 40)
        self.refresh(Fake(), keep={noted})
        self.assertIsNone(SB.image(self.db, SB.key(REPO, "basket", "desktop", "1" * 40)))
        self.assertIsNotNone(SB.image(self.db, noted))

    def test_keys_are_strict(self):
        for bad in ("screen:x:basket:desktop:" + SHA, f"screen:{REPO}:Basket:desktop:{SHA}", f"screen:{REPO}:basket:desktop:abc",
                    f"screen:{REPO}:../x:desktop:{SHA}", "mockup:x"):
            self.assertIsNone(SB.image(self.db, bad), bad)

    def test_a_refresh_is_due_on_request_or_on_the_timer(self):
        self.assertFalse(SB.due(self.cfg, self.db, 10_000))                       # no timer, no request
        SB.request(self.db)
        self.assertTrue(SB.due(self.cfg, self.db, 10_000))
        self.refresh(Fake())
        self.assertFalse(SB.due(self.cfg, self.db, 10_000))                       # the request was taken
        timed = replace(self.cfg, screens=replace(self.cfg.screens, board_every="1h"))
        at = SB.status(self.db)["at"]
        self.assertFalse(SB.due(timed, self.db, at + 60))
        self.assertTrue(SB.due(timed, self.db, at + 3600))
        self.assertFalse(SB.due(replace(self.cfg, screens=ScreensCfg()), self.db, at + 99_999))   # no pages: never

    def test_a_failed_checkout_is_reported_and_the_rest_goes_on(self):
        def boom(repo, dest):
            raise subprocess.CalledProcessError(128, "git")
        st = SB.refresh(self.cfg, "t", self.db, set(), Fake(), clone=boom)
        self.assertEqual(st["repos"][REPO], {"sha": "", "problem": "could not check out the default branch"})

    def test_tick_runs_one_refresh_at_a_time(self):
        SB.request(self.db)
        with SB._running:
            self.assertFalse(SB.tick(self.cfg, self.db, 1, "t", lambda: self.db, threaded=False))
        calls = []
        orig = SB.refresh
        SB.refresh = lambda *a, **k: calls.append(1) or {"repos": {}}
        try:
            self.assertTrue(SB.tick(self.cfg, self.db, 1, "t", lambda: dbm.connect(":memory:"), threaded=False))
        finally:
            SB.refresh = orig
        self.assertEqual(calls, [1])
        self.assertFalse(SB.running())

    def test_journeys_are_named_first_in_step_order(self):
        self.assertEqual([(j, [p.name for p in ps]) for j, ps in SB.journeys(PAGES)], [("checkout", ["basket", "pay"]), ("", ["home"])])


class ParseBoard(unittest.TestCase):
    def cfg(self, **screens_raw):
        raw = {"general": {"db_path": "x", "poll_seconds": 1, "dry_run": True, "confidence_threshold": 0.6},
               "github": {"repos": ["o/r"], "trigger_label": "t", "trusted_permissions": ["write"]},
               "routing": {k: {"harness": "claude-code", "model": "sonnet", "effort": "medium"} for k in ("low", "medium", "high")},
               "screens": screens_raw}
        return config.parse(raw)

    def page(self, **kw):
        return {"repo": "o/r", "name": "home", "path": "site/index.html", **kw}

    def test_journey_step_design_and_timer(self):
        c = self.cfg(board_every="6h", pages=[self.page(journey="checkout", step=3, design="docs/design/home.dc.html")]).screens
        self.assertEqual((c.board_every, c.pages[0].journey, c.pages[0].step, c.pages[0].design), ("6h", "checkout", 3, "docs/design/home.dc.html"))
        for b in (dict(board_every="soon"), dict(pages=[self.page(journey="Check out")]), dict(pages=[self.page(step=-1)]),
                  dict(pages=[self.page(step=True)]), dict(pages=[self.page(design="docs/x.html")]),
                  dict(pages=[self.page(design="../x.dc.html")]), dict(viewports={"design": {"width": 400, "height": 400}})):
            with self.assertRaises(ValueError, msg=str(b)):
                self.cfg(**b)


SCREENS_TOML = f'''
[screens]
[[screens.pages]]
repo = "{REPO}"
name = "basket"
path = "site/basket.html"
journey = "checkout"
step = 1
[[screens.pages]]
repo = "{REPO}"
name = "pay"
path = "site/pay.html"
journey = "checkout"
step = 2
design = "docs/design/pay.dc.html"
[[screens.pages]]
repo = "{REPO}"
name = "home"
path = "site/index.html"
'''


class BoardUi(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + SCREENS_TOML)


class Page(BoardUi):

    def test_needs_a_session(self):
        self.assertEqual(self.req("GET", "/screens")[0], 303)
        cookie, _ = self.session()
        self.assertEqual(self.req("POST", "/screens/refresh", "", cookie=cookie)[0], 403)      # no CSRF token
        self.assertFalse(SB.requested(self.db))

    def test_journeys_show_each_step_designed_and_built(self):
        SB.store(self.db, REPO, "pay", "desktop", SHA, png(1440, 900))
        SB.store(self.db, REPO, "pay", "design", SHA, png(1200, 800))
        self.db.commit()
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/screens", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn('href="/screens"', html)                                   # in the navigation
        self.assertLess(html.index("<h2>Checkout</h2>"), html.index("<h2>Other screens</h2>"))
        self.assertLess(html.index(">basket</h3>"), html.index(">pay</h3>"))
        self.assertIn(SB.src(SB.key(REPO, "pay", "desktop", SHA)).replace("&", "&amp;"), html)
        self.assertIn(SB.src(SB.key(REPO, "pay", "design", SHA)).replace("&", "&amp;"), html)
        self.assertIn("No canvas set.", html)                                      # basket has none
        mobile = self.req("GET", "/screens?view=mobile&journey=checkout", cookie=cookie)[2]
        self.assertNotIn(SB.src(SB.key(REPO, "pay", "desktop", SHA)).replace("&", "&amp;"), mobile)
        self.assertNotIn("Other screens</h2>", mobile)
        self.assertNotIn("style=", mobile.split("<main")[1])                     # the CSP forbids inline styles

    def test_the_image_route_serves_only_stored_shots(self):
        SB.store(self.db, REPO, "pay", "desktop", SHA, png(1440, 900))
        self.db.commit()
        cookie, _ = self.session()
        s, h, _ = self.req("GET", SB.src(SB.key(REPO, "pay", "desktop", SHA)), cookie=cookie)
        self.assertEqual((s, h["Content-Type"]), (200, "image/png"))
        self.assertEqual(self.req("GET", "/screenimg?" + urlencode({"key": SB.key(REPO, "pay", "mobile", SHA)}), cookie=cookie)[0], 404)
        self.assertEqual(self.req("GET", "/screenimg?key=../../etc", cookie=cookie)[0], 404)

    def test_refresh_now_asks_the_orchestrator(self):
        cookie, csrf = self.session()
        s, h, _ = self.req("POST", "/screens/refresh", urlencode({"csrf": csrf}), cookie=cookie)
        self.assertEqual((s, h["Location"]), (303, "/screens"))
        self.assertTrue(SB.requested(self.db))
        html = self.req("GET", "/screens", cookie=cookie)[2]
        self.assertIn("Asked for new shots", html)
        self.assertIn("Refreshing…", html)


class ReviewUi(BoardUi):
    def setUp(self):
        super().setUp()
        for page, view, w in (("pay", "desktop", 1440), ("pay", "design", 1200), ("basket", "desktop", 1440)):
            SB.store(self.db, REPO, page, view, SHA, png(w, 900))
        self.db.commit()
        self.pay = SB.key(REPO, "pay", "desktop", SHA)
        self.cookie, self.csrf = self.session()

    def post(self, path, **fields):
        return self.req("POST", path, urlencode({"csrf": self.csrf, **fields}), cookie=self.cookie)

    def page(self, img=""):
        s, _, html = self.req("GET", "/screens/review" + ("?" + urlencode({"img": img}) if img else ""), cookie=self.cookie)
        self.assertEqual(s, 200)
        return html


class Review(ReviewUi):
    """A board screen opens in the review tool; its notes are kept apart from every ticket until a person turns them into one."""
    def test_a_shot_on_the_board_opens_the_review_tool_with_that_screen_only(self):
        board = self.req("GET", "/screens", cookie=self.cookie)[2]
        self.assertIn("/screens/review?img=" + urlencode({"x": self.pay})[2:], board)
        html = self.page(self.pay)
        self.assertIn("data-review", html)
        self.assertIn(">pay · designed", html)
        self.assertIn(">pay · desktop", html)
        self.assertNotIn(">basket · desktop", html)                           # another screen is not a tab here
        self.assertIn(SB.src(self.pay).replace("&", "&amp;"), html)

    def test_a_board_note_is_stored_apart_from_tickets_and_shown_on_the_board(self):
        s, h, _ = self.post("/review/add", board="1", img=self.pay, x="10", y="20", w="30", h="5", text="Too tight")
        self.assertEqual((s, h["Location"]), (303, "/screens/review?" + urlencode({"img": self.pay})))
        n = RN.notes(self.db, *RN.BOARD)
        self.assertEqual([(x["image"], x["x"], x["y"], x["w"], x["h"], x["text"]) for x in n], [(self.pay, 100, 200, 300, 50, "Too tight")])
        self.assertIn('class="rv-box"', self.page(self.pay))
        self.assertIn("1 note</span>", self.req("GET", "/screens", cookie=self.cookie)[2])
        self.post("/review/delete", board="1", id=str(n[0]["id"]), img=self.pay)
        self.assertEqual(RN.notes(self.db, *RN.BOARD), [])

    def test_only_board_images_take_board_notes_and_a_ticket_cannot_take_them(self):
        for img in (SB.key(REPO, "pay", "mobile", SHA), "mockup:x/y:docs/design/previews/factory-5-a.png", "screen:../x"):
            self.post("/review/add", board="1", img=img, x="1", y="1", text="t")
        self.post("/review/add", repo=REPO, n="5", img=self.pay, x="1", y="1", text="t")
        self.assertEqual(RN.notes(self.db, *RN.BOARD), [])
        self.assertEqual(RN.notes(self.db, REPO, 5), [])

    def test_an_older_shot_with_a_note_stays_reviewable_after_a_new_one(self):
        RN.add(self.db, *RN.BOARD, self.pay, (0, 0, 10, 10), "old")
        SB.store(self.db, REPO, "pay", "desktop", "b" * 40, png(1440, 900), now=9e9)
        self.db.commit()
        html = self.page(self.pay)
        self.assertIn(f">pay · desktop · {SHA[:7]}", html)
        self.assertIn('class="rv-box"', html)

    def test_no_shots_yet_says_so(self):
        self.db.execute("DELETE FROM screen_shots")
        self.db.commit()
        self.assertIn("no screens to review yet", self.page())


class CreateTicket(ReviewUi):
    """Board notes become a new ticket: the notes move onto it, so its design run gets them and their screens."""
    def setUp(self):
        super().setUp()
        self.gh = mock.MagicMock()
        self.gh.create_ticket.return_value = {"number": 42}
        self.gh.repo_labels.return_value = [{"name": load(str(self.root / "config.toml")).auto_label}]
        I.save_secret(load(str(self.root / "config.toml")), "github", "ghp_" + "a" * 36, "subscription")
        p = mock.patch("factory.ui.labels.GitHub", return_value=self.gh)
        p.start()
        self.addCleanup(p.stop)
        self.a = RN.add(self.db, *RN.BOARD, self.pay, (100, 200, 300, 50), "Too tight\n@someone look")
        self.b = RN.add(self.db, *RN.BOARD, SB.key(REPO, "basket", "desktop", SHA), (500, 500, 0, 0), "Icon")

    def create(self, notes, **fields):
        body = urlencode({"csrf": self.csrf, "board": "1", "img": self.pay, "repo": REPO, "title": "Pay spacing", "start": "",
                          **fields}) + "".join(f"&note={n}" for n in notes)
        return self.req("POST", "/screens/issue", body, cookie=self.cookie)

    def test_the_form_ticks_the_notes_on_this_screen(self):
        html = self.page(self.pay)
        self.assertIn(f'name="note" value="{self.a}" checked', html)
        self.assertIn(f'name="note" value="{self.b}">', html)
        self.assertIn('action="/screens/issue"', html)

    def test_the_picked_notes_become_a_ticket_and_move_onto_it(self):
        s, h, _ = self.create([self.a])
        self.assertEqual((s, h["Location"]), (303, "/ticket/review?" + urlencode({"repo": REPO, "n": 42})))
        repo, title, body = self.gh.create_ticket.call_args[0]
        self.assertEqual((repo, title), (REPO, "Pay spacing"))
        self.assertIn("1. **pay · desktop**", body)
        self.assertIn(f"`{SHA[:7]}`", body)
        self.assertIn("30% × 5% at 10%, 20%", body)
        self.assertIn("> Too tight\n> @someone look", body)
        self.assertNotIn("Icon", body)
        self.gh.add_labels.assert_not_called()                                   # "Don't start it" adds no label
        self.assertEqual([x["id"] for x in RN.notes(self.db, *RN.BOARD)], [self.b])
        self.assertEqual([x["id"] for x in RN.notes(self.db, REPO, 42)], [self.a])
        html = self.req("GET", f"/ticket/review?repo={REPO}&n=42", cookie=self.cookie)[2]
        self.assertIn("Created your-org/standalone-service#42 with 1 note.", html)
        self.assertIn(SB.src(self.pay).replace("&", "&amp;"), html)              # the board shot is on the ticket's review page
        self.assertIn('class="rv-box"', html)

    def test_the_designer_gets_the_board_shot_with_the_note(self):
        self.create([self.a])
        pkg = RN.for_designer(self.db, REPO, 42)
        self.assertEqual([(n["file"], n["width"], n["x"], n["w"]) for n in pkg["notes"]], [("screen-1.png", 1440, 144, 432)])
        self.assertIn("screen-1.png", pkg["files"])

    def test_start_auto_labels_it_and_design_queues_the_designer(self):
        self.create([self.a], start="auto")
        self.gh.add_labels.assert_called_once()
        self.assertEqual(self.gh.add_labels.call_args[0][:2], (REPO, 42))
        self.gh.create_ticket.return_value = {"number": 43}
        self.create([self.b], start="design", title="Icon")
        self.assertEqual(self.db.execute("SELECT repo, issue, action FROM approvals").fetchall(), [(REPO, 43, "stage:designer")])

    def test_bad_requests_create_nothing(self):
        for notes, fields in (([], {}), ([self.a], {"title": ""}), ([self.a], {"title": "x" * 201}), ([self.a], {"repo": "evil/x"}),
                              ([self.a], {"start": "ready"}), ([999], {})):
            s, _, _ = self.create(notes, **fields)
            self.assertEqual(s, 303, (notes, fields))
            self.assertIn('class="flash bad"', self.page(self.pay), (notes, fields))
        self.gh.create_ticket.assert_not_called()
        self.assertEqual(len(RN.notes(self.db, *RN.BOARD)), 2)

    def test_a_github_failure_keeps_the_notes_on_the_board(self):
        import urllib.error
        self.gh.create_ticket.side_effect = urllib.error.URLError("down")
        self.create([self.a])
        self.assertEqual(len(RN.notes(self.db, *RN.BOARD)), 2)
        self.assertIn("Nothing was created", self.page(self.pay))


if __name__ == "__main__":
    unittest.main()
