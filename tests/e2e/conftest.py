"""Browser (Playwright) tests of the admin UI (a fresh server and database per test). Not part of `unittest discover`: run them with

    python3 -m venv .venv && .venv/bin/pip install -r tests/e2e/requirements.txt && .venv/bin/playwright install chromium
    .venv/bin/pytest tests/e2e            # E2E_BROWSER=/usr/bin/chromium to use a system Chromium instead

The real UI server runs in-process against a temp config and a seeded database. GitHub is replaced by an in-memory fake,
so nothing leaves the machine."""
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from factory import db as dbm  # noqa: E402
from factory import github as ghm  # noqa: E402
from factory.config import load  # noqa: E402
from factory.ui import integrations as I  # noqa: E402
from factory.ui.server import App, serve  # noqa: E402

PASSWORD = "correct horse battery"
REPO = "your-org/standalone-service"
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


class FakeGitHub:
    """Just enough of api.github.com for the pages under test. Writes are recorded in `writes`."""

    def __init__(self):
        now = "2026-10-01T10:00:00Z"
        mk = lambda n, title, labels, body="": {"number": n, "title": title, "state": "open", "body": body, "updated_at": now, "created_at": now,
                                                 "html_url": f"https://github.com/{REPO}/issues/{n}", "user": {"login": "juan"},
                                                 "labels": [{"name": x, "color": "ededed"} for x in labels], "comments": 0}
        self.issues = [mk(4, "Add dark mode toggle", []), mk(7, "Retry failed CI fixes once more, with a deliberately long title that must wrap on a phone", ["factory:ready"]),
                       mk(9, "Per-repo budget limits", ["factory:analyze"])]
        self.labels = ["factory:ready", "factory:auto", "factory:analyze", "factory:design", "factory:architect", "bug"]
        self.writes: list[tuple] = []

    def req(self, _self, method, path, data=None):
        p = path.split("?")[0]
        if method == "GET":
            if p == "/user":
                return {"login": "factory-bot"}
            if p.endswith("/labels"):
                return [{"name": n, "color": "ededed"} for n in self.labels]
            if p.startswith("/search/issues"):
                return {"items": self.issues, "total_count": len(self.issues)}
            if p.endswith("/comments"):
                return []
            m = re.search(r"/issues/(\d+)$", p)
            if m:
                for i in self.issues:
                    if i["number"] == int(m[1]):
                        return i
                raise urllib.error.HTTPError(path, 404, "nf", {}, None)
            if p.endswith("/issues"):
                return self.issues
            return []
        self.writes.append((method, p, data))
        m = re.search(r"/issues/(\d+)/labels", p)
        if m and method == "POST":
            for i in self.issues:
                if i["number"] == int(m[1]):
                    i["labels"] += [{"name": n, "color": "ededed"} for n in data["labels"]]
        if m and method == "DELETE":
            for i in self.issues:
                if i["number"] == int(m[1]):
                    i["labels"] = [l for l in i["labels"] if l["name"] != p.rsplit("/", 1)[1].replace("%3A", ":")]
        return {}


def seed(db) -> None:
    now = time.time()
    dbm.set_status(db, "last_poll_ok", str(now - 4))
    done = dbm.start_run(db, "stage", REPO, 9, "Per-repo budget limits", "claude-code", "opus", "high", stage="analyst")
    dbm.finish_run(db, done, "stage", "analysed", output="# Analysis\n\nLooks feasible. **Two** open questions.", log_tail="step 1\nstep 2")
    bad = dbm.start_run(db, "implement", REPO, 7, "Retry failed CI fixes once more, with a deliberately long title that must wrap on a phone",
                        "claude-code", "sonnet", "medium")
    dbm.finish_run(db, bad, "failed", "sandbox exited 1: " + "x" * 200)
    dbm.start_run(db, "implement", REPO, 4, "Add dark mode toggle", "claude-code", "sonnet", "medium")
    pr = dbm.start_run(db, "implement", REPO, 12, "Ship the thing", "claude-code", "opus", "high")
    dbm.finish_run(db, pr, "pr", "opened", pr_urls=f"https://github.com/{REPO}/pull/31")
    dbm.record(db, REPO, 7, "2026-10-01T10:00:00Z", "run", "confident")
    dbm.record(db, REPO, 4, "2026-10-01T10:00:00Z", "ignored", "label applied by someone without write access")
    dbm.watch_pr(db, REPO, 31, REPO, 12)
    dbm.add_event(db, "run.start", "implement started", REPO, 4)
    dbm.add_event(db, "alert.error", "something went wrong <script>alert(1)</script>", REPO, 7)


class Server:
    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / "state").mkdir()
        cfg = (ROOT / "config.example.toml").read_text().replace("/srv/factory", str(tmp))
        (tmp / "config.toml").write_text(cfg)
        self.db_path = str(tmp / "state" / "factory.db")
        self.db = dbm.connect(self.db_path)
        seed(self.db)
        self.gh = FakeGitHub()
        self.app = App(str(tmp / "config.toml"), {"localhost", "127.0.0.1"})
        self.app.auth.set_password(PASSWORD)
        I.save_secret(load(str(tmp / "config.toml")), "github", "ghp_faketoken", "subscription")
        self.srv = serve(self.app, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    with tempfile.TemporaryDirectory() as t:
        s = Server(Path(t))
        orig = ghm.GitHub._req
        ghm.GitHub._req = lambda self, method, path, data=None: s.gh.req(self, method, path, data)
        try:
            yield s
        finally:
            ghm.GitHub._req = orig
            s.srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = os.environ.get("E2E_BROWSER")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(params=["desktop", "phone"])
def viewport(request):
    return request.param


@pytest.fixture
def page(browser, server, viewport):
    """A signed-in page, once at desktop size and once as a phone (touch, mobile user agent)."""
    ctx = browser.new_context(viewport=DESKTOP if viewport == "desktop" else PHONE, is_mobile=viewport == "phone", has_touch=viewport == "phone",
                              device_scale_factor=2 if viewport == "phone" else 1)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(f"{m.text} {m.location.get('url', '')}") if m.type == "error" else None)
    pg.goto(server.url + "/login")
    pg.fill("input[name=password]", PASSWORD)
    pg.click("button[type=submit], button")
    pg.wait_for_url(server.url + "/")
    yield pg
    ctx.close()
