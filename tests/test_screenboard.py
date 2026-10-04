"""The Screens board: shots of every configured screen on the default branch, and its design canvas, grouped by journey."""
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from factory import config, screenboard as SB
from factory import db as dbm
from factory.config import RunnerCfg, ScreenPage, ScreensCfg, load

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


class Page(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + SCREENS_TOML)

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


if __name__ == "__main__":
    unittest.main()
