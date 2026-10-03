"""User journeys through the admin UI, each run on a desktop and on a phone."""
import re
import tomllib

import pytest
from playwright.sync_api import expect

from conftest import PASSWORD, REPO
from factory import db as dbm

TICKETS = "/tickets"


def tab_bar(page, viewport):
    """The primary navigation: the top bar on a desktop, the bottom tab bar on a phone."""
    return page.get_by_role("navigation", name="Main")


def go(page, viewport, name, more=False):
    """Use the navigation like a person would, including the phone's More menu."""
    nav = tab_bar(page, viewport)
    if viewport == "phone" and more:
        nav.get_by_text("More", exact=True).click()
    nav.get_by_role("link", name=name, exact=True).click()


def test_login_rejects_a_wrong_password_then_accepts_the_right_one(browser, server, viewport):
    pg = browser.new_context().new_page()
    pg.goto(server.url + "/runs")
    expect(pg).to_have_url(re.compile(r"/login$"))
    pg.fill("input[name=password]", "wrong password")
    pg.click("button")
    expect(pg.get_by_text("Wrong password.")).to_be_visible()
    pg.fill("input[name=password]", PASSWORD)
    pg.click("button")
    expect(pg.get_by_role("heading", name="Factory floor", exact=True)).to_be_visible()


def test_sign_out_ends_the_session(page, server):
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile(r"/login$"))
    page.goto(server.url + "/tickets")
    expect(page).to_have_url(re.compile(r"/login$"))


def test_navigating_every_primary_and_secondary_destination(page, server, viewport):
    go(page, viewport, "Tickets")
    expect(page.get_by_role("heading", name="Tickets", exact=True)).to_be_visible()
    go(page, viewport, "Needs you")
    expect(page.get_by_role("heading", name="Needs you", exact=True)).to_be_visible()
    for name in ("Runs", "PRs & CI", "Events", "Settings"):
        go(page, viewport, name, more=True)
        expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    # Harnesses, Credentials, Telegram and Labels live inside Settings, behind its side list
    side = page.get_by_role("navigation", name="Settings")
    for name in ("Harnesses", "Credentials", "Telegram", "Labels"):
        side.get_by_role("link", name=name, exact=True).click()
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(side.locator("a.active", has_text=name)).to_be_visible()
    go(page, viewport, "Factory")
    expect(page.get_by_role("heading", name="Factory floor", exact=True)).to_be_visible()


def test_the_floor_shows_what_is_running_and_pause_resume_works(page, server, viewport):
    if viewport == "phone":
        expect(page.locator(".ph-now").get_by_text("Add dark mode toggle")).to_be_visible()
    else:
        expect(page.get_by_text("Build is working on #4")).to_be_visible()
    paused = server.root / "state" / "PAUSED"
    page.get_by_role("button", name="Pause").click()
    expect(page.get_by_role("button", name="Resume")).to_be_visible()
    assert paused.exists()
    if viewport == "phone":
        expect(page.get_by_text("The queue is paused")).to_be_visible()
    page.get_by_role("button", name="Resume").click()
    expect(page.get_by_role("button", name="Pause")).to_be_visible()
    assert not paused.exists()


def test_start_a_ticket_with_auto_applies_the_trigger_label(page, server, viewport):
    page.goto(server.url + TICKETS)
    page.get_by_role("button", name="Auto #4").click()
    expect(page.get_by_text("Started.").first).to_be_visible()
    posts = [w for w in server.gh.writes if w[0] == "POST" and w[1].endswith("/issues/4/labels")]
    assert posts, server.gh.writes
    assert posts[0][2]["labels"] == ["factory:auto"]


def test_build_anyway_queues_an_approval_for_the_orchestrator(page, server, viewport):
    page.goto(server.url + TICKETS)
    page.get_by_role("button", name="Build #4").click()
    expect(page.get_by_text("Started.").first).to_be_visible()
    db = dbm.connect(server.db_path)
    assert [(r, n, a) for r, n, a in dbm.approvals(db)] == [(REPO, 4, "run")]


def test_search_filters_the_ticket_list_by_number(page, server, viewport):
    page.goto(server.url + TICKETS)
    page.fill("input[name=q]", "#9")
    page.get_by_role("button", name="Filter").click()
    expect(page).to_have_url(re.compile(r"q=%239|q=#9"))


def test_a_run_can_be_opened_from_the_runs_list(page, server, viewport):
    page.goto(server.url + "/runs")
    page.get_by_role("link", name="Run #1", exact=True).click()
    expect(page.get_by_role("heading", name="Run #1", exact=True)).to_be_visible()
    expect(page.get_by_text("Looks feasible")).to_be_visible()


def test_failed_chip_lists_only_failed_runs(page, server, viewport):
    page.goto(server.url + "/runs")
    page.get_by_role("link", name="Failed", exact=True).click()
    expect(page.locator(".badge", has_text="failed")).to_have_count(1)
    expect(page.get_by_text("Ship the thing")).to_have_count(0)


def test_ticket_pipeline_links_to_the_stage_document(page, server, viewport):
    page.goto(server.url + f"/ticket?repo={REPO}&n=9")
    page.get_by_role("link", name="Read document").first.click()
    expect(page.get_by_text("Looks feasible")).to_be_visible()
    expect(page.get_by_role("link", name=re.compile("Pipeline"))).to_be_visible()


def test_hostile_event_text_is_shown_as_text_not_run(page, server, viewport):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(server.url + "/events")
    expect(page.get_by_text("<script>alert(1)</script>", exact=False)).to_be_visible()
    assert not dialogs


def test_saving_a_setting_writes_an_override_and_bad_input_is_refused(page, server, viewport):
    page.goto(server.url + "/settings")
    field = page.locator("input[name=poll_seconds], input[name='general.poll_seconds']").first
    field.fill("90")
    page.get_by_role("button", name="Save changes").click()
    expect(page.locator(".flash")).to_be_visible()
    overrides = tomllib.loads((server.root / "config.overrides.toml").read_text())
    assert overrides["general"]["poll_seconds"] == 90
    field = page.locator("input[name=poll_seconds], input[name='general.poll_seconds']").first
    field.fill("-5")
    page.get_by_role("button", name="Save changes").click()
    expect(page.locator(".flash.bad")).to_be_visible()
    assert tomllib.loads((server.root / "config.overrides.toml").read_text())["general"]["poll_seconds"] == 90


def test_a_saved_credential_is_never_shown_back(page, server, viewport):
    page.goto(server.url + "/credentials")
    secret = "ghp_SECRETSECRETSECRET1234"
    card = page.locator("form", has=page.locator("input[value=github]")).first
    card.locator("input[type=password], input[type=text]:not([name=name])").first.fill(secret)
    card.get_by_role("button", name="Save").click()
    assert secret not in page.content()
    assert (server.root / "secrets" / "github_token").read_text().strip() == secret


def test_settings_side_list_is_tappable_and_event_names_do_not_break(page, server, viewport):
    page.goto(server.url + "/telegram")
    for strong in page.locator(".check.evt strong").all():
        box = strong.bounding_box()
        assert box["height"] < 30, f"{strong.inner_text()} wrapped"          # one line: names are never broken mid-word
    if viewport == "phone":
        for link in page.get_by_role("navigation", name="Settings").get_by_role("link").all():
            assert link.bounding_box()["height"] >= 44, link.inner_text()
