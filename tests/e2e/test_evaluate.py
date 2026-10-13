"""The Evaluate page in a browser: the summary, a ticket's evaluation and Evaluate now, at every viewport."""
from pathlib import Path
from urllib.parse import quote

from conftest import REPO, seed_evaluations


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def turn_on(server) -> None:
    cfg = Path(server.root) / "config.toml"
    text = cfg.read_text()
    assert "[evaluate]\nenabled = false" in text
    cfg.write_text(text.replace("dry_run = true", "dry_run = false").replace("[evaluate]\nenabled = false", "[evaluate]\nenabled = true"))


def test_off_until_turned_on(page, server):
    page.goto(server.url + "/evaluate")
    assert page.get_by_role("heading", name="Evaluate", exact=True).is_visible()
    assert page.get_by_text("Evaluation is off.").is_visible()
    assert page.get_by_role("button", name="Evaluate now").count() == 0
    page.get_by_role("link", name="Turn it on in Settings").click()
    assert page.get_by_role("heading", name="Evaluation").is_visible()
    assert not page.errors


def test_summary_and_ticket(page, server, viewport):
    turn_on(server)
    seed_evaluations(server.db)
    page.goto(server.url + "/evaluate")
    assert page.locator(".ev-tile").count() == 4
    assert page.get_by_text("1 of 6 reopened by a person").is_visible()
    assert page.get_by_role("heading", name="What to improve").is_visible()
    assert page.get_by_text("Route low-complexity tickets to sonnet.").first.is_visible()
    assert page.locator(".ev-steps tbody tr").count() == 4                 # analyst, architect, build, reviewer
    assert page.get_by_label("Repository").is_visible()                    # the filters stay on a phone
    assert overflow(page) <= 0
    page.get_by_role("link", name="standalone-service#41").click()
    assert page.get_by_text("Reopened by a person").is_visible()
    assert page.get_by_text("Capped at 2: a person reopened it.").is_visible()
    assert page.locator(".ev-run").count() == 4
    assert page.get_by_text("A lighter model would do").is_visible()
    assert overflow(page) <= 0
    assert not page.errors


def test_evaluate_now_queues_a_request(page, server):
    turn_on(server)
    seed_evaluations(server.db)
    page.goto(server.url + "/evaluate")
    page.get_by_role("button", name="Evaluate now").click()
    assert page.get_by_text("Queued every finished ticket").is_visible()
    assert {(r, n) for r, n in server.db.execute("SELECT repo, issue FROM evaluation_requests")} >= {(REPO, 0)}
    page.goto(server.url + f"/evaluate/ticket?repo={quote(REPO, safe='')}&issue=40")
    page.get_by_role("button", name="Evaluate again").click()
    assert page.get_by_text("Queued.").is_visible()
    assert (REPO, 40) in set(server.db.execute("SELECT repo, issue FROM evaluation_requests"))
