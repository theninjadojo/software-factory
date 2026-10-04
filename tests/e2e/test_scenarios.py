"""User journeys through the admin UI, each run on a desktop and on a phone."""
import re
import tomllib

import pytest
from playwright.sync_api import expect

from conftest import PASSWORD, REPO
from factory import db as dbm

LABELS = "/labels"          # the label table: start, build or skip a ticket by hand


def go(page, viewport, name):
    """Use the navigation like a person would: the top bar, or the header menu on a phone."""
    if viewport == "phone":
        page.locator(".ph-menu summary").click()
        page.locator(".ph-menu-list").get_by_role("link", name=re.compile("^" + re.escape(name))).click()
    else:
        page.get_by_role("navigation", name="Main").get_by_role("link", name=re.compile("^" + re.escape(name))).click()


def sign_out(page, viewport):
    if viewport == "phone":
        page.locator(".ph-menu summary").click()
        page.locator(".ph-menu-list").get_by_role("button", name="Sign out").click()
    else:
        page.get_by_role("button", name="Sign out").click()


def test_login_rejects_a_wrong_password_then_accepts_the_right_one(browser, server):
    pg = browser.new_context().new_page()
    pg.goto(server.url + "/runs")
    expect(pg).to_have_url(re.compile(r"/login$"))
    pg.fill("input[name=password]", "wrong password")
    pg.click("button")
    expect(pg.get_by_text("Wrong password.")).to_be_visible()
    pg.fill("input[name=password]", PASSWORD)
    pg.click("button")
    expect(pg.get_by_role("heading", name="Factory", exact=True)).to_be_visible()


def test_sign_out_ends_the_session(page, server, viewport):
    sign_out(page, viewport)
    expect(page).to_have_url(re.compile(r"/login$"))
    page.goto(server.url + "/tickets")
    expect(page).to_have_url(re.compile(r"/login$"))


def test_navigating_every_primary_and_secondary_destination(page, server, viewport):
    go(page, viewport, "Tickets")
    expect(page.get_by_role("heading", name="Tickets", exact=True)).to_be_visible()
    for path, heading in (("/needs", "Needs you"), ("/runs", "Runs")):       # part of Tickets now, at their old addresses
        page.goto(server.url + path)
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
    page.goto(server.url + "/prs")
    expect(page).to_have_url(re.compile(r"/tickets\?stage=prs$"))
    for name in ("Events", "Settings"):
        go(page, viewport, name)
        expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    # Harnesses, Credentials, Telegram and Labels live inside Settings, behind its side list
    side = page.get_by_role("navigation", name="Settings")
    for name in ("Harnesses", "Credentials", "Telegram", "Labels"):
        side.get_by_role("link", name=name, exact=True).click()
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(side.locator("a.active", has_text=name)).to_be_visible()
    go(page, viewport, "Factory")
    expect(page.get_by_role("heading", name="Factory", exact=True)).to_be_visible()


def test_the_floor_shows_what_is_running_and_pause_resume_works(page, server, viewport):
    running = page.get_by_role("region", name="Running now")
    expect(running.get_by_text("Add dark mode toggle")).to_be_visible()
    lit = ".sd-mach.p6.run" if viewport == "phone" else 'svg.fm a.fm-m.run[href*="at=build"]'      # the phone's column, or the map
    expect(page.locator(lit)).to_be_visible()                                # the Build station is lit
    paused = server.root / "state" / "PAUSED"
    page.get_by_role("button", name="Pause").click()
    expect(page.get_by_role("button", name="Resume")).to_be_visible()
    assert paused.exists()
    expect(page.get_by_text(re.compile("Paused: "))).to_be_visible()
    page.get_by_role("button", name="Resume").click()
    expect(page.get_by_role("button", name="Pause")).to_be_visible()
    assert not paused.exists()


def test_start_a_ticket_with_auto_applies_the_trigger_label(page, server, viewport):
    page.goto(server.url + LABELS)
    page.get_by_role("button", name="Auto #4").click()
    expect(page.get_by_text("Started.").first).to_be_visible()
    posts = [w for w in server.gh.writes if w[0] == "POST" and w[1].endswith("/issues/4/labels")]
    assert posts, server.gh.writes
    assert posts[0][2]["labels"] == ["factory:auto"]


def test_build_anyway_queues_an_approval_for_the_orchestrator(page, server, viewport):
    page.goto(server.url + LABELS)
    page.get_by_role("button", name="Build #4").click()
    expect(page.get_by_text("Started.").first).to_be_visible()
    db = dbm.connect(server.db_path)
    assert [(r, n, a) for r, n, a in dbm.approvals(db)] == [(REPO, 4, "run")]


def test_search_filters_the_ticket_list_by_number(page, server, viewport):
    page.goto(server.url + "/tickets?stage=all")
    page.fill("input[name=q]", "#9")
    page.press("input[name=q]", "Enter")
    expect(page).to_have_url(re.compile(r"q=%239"))
    expect(page.locator(".sd-pick")).to_have_count(1)
    expect(page.locator(".sd-pick")).to_contain_text("Per-repo budget limits")


def test_a_ticket_opens_from_the_list_and_a_phone_goes_back(page, server, viewport):
    page.goto(server.url + "/tickets?stage=all&q=9")
    page.locator(".sd-pick", has_text="Per-repo budget limits").click()
    expect(page).to_have_url(re.compile(r"/ticket\?repo=.*&n=9"))
    expect(page.get_by_role("heading", name="Per-repo budget limits", level=2)).to_be_visible()
    if viewport == "phone":
        expect(page.locator(".sd-list")).to_be_hidden()
        page.get_by_role("link", name="← All tickets").click()
        expect(page.locator(".sd-list")).to_be_visible()


def test_a_run_can_be_opened_from_the_runs_list(page, server, viewport):
    page.goto(server.url + "/runs?page=1")                 # the oldest runs: 60+ bulk runs fill the first page
    page.get_by_role("link", name="Run #1", exact=True).click()
    expect(page.get_by_role("heading", name="Run #1", exact=True)).to_be_visible()
    expect(page.get_by_text("Looks feasible")).to_be_visible()


def test_failed_chip_lists_only_failed_runs(page, server, viewport):
    page.goto(server.url + "/runs")
    page.get_by_role("link", name="Failed", exact=True).click()
    badges = page.locator("tbody .badge").all_inner_texts()
    assert badges and set(badges) == {"failed"}, badges
    expect(page.get_by_text("Ship the thing")).to_have_count(0)


def test_ticket_pipeline_links_to_the_stage_document(page, server, viewport):
    page.goto(server.url + f"/ticket?repo={REPO}&n=9")
    page.get_by_role("link", name="Read analysis").first.click()
    expect(page.get_by_text("Looks feasible")).to_be_visible()
    expect(page.get_by_role("link", name=re.compile("Pipeline"))).to_be_visible()


def test_hostile_event_text_is_shown_as_text_not_run(page, server, viewport):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(server.url + "/events")
    expect(page.get_by_text("<script>alert(1)</script>", exact=False)).to_be_visible()
    assert not dialogs


def test_saving_a_setting_writes_an_override_and_bad_input_is_refused(page, server, viewport):
    page.goto(server.url + "/settings?section=general")
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


def test_local_tickets_are_turned_on_from_new_ticket(page, server, viewport):
    page.goto(server.url + "/tickets")
    page.get_by_role("button", name="New ticket").click()
    page.get_by_role("link", name="Turn them on").click()
    strip = page.get_by_role("region", name="The settings that shape Tickets")
    expect(strip.get_by_role("button", name="Turn on")).to_be_visible()          # the confirmation, already open
    strip.get_by_role("button", name="Turn on").click()
    expect(page.locator(".flash")).to_contain_text("Local tickets turned on")
    assert tomllib.loads((server.root / "config.overrides.toml").read_text())["local"]["enabled"] is True
    expect(page.get_by_role("region", name="The settings that shape Tickets")).to_contain_text("GitHub and the factory")
    page.get_by_role("button", name="New ticket").click()
    expect(page.get_by_label("Keep it in")).to_be_visible()


def test_settings_opens_on_every_feature_and_switches_one(page, server, viewport):
    page.goto(server.url + "/")
    go(page, viewport, "Settings")
    card = page.locator("#f-review")
    expect(card).to_contain_text("Off")
    card.locator("summary", has_text="Turn on").click()
    card.get_by_role("button", name="Turn on").click()
    expect(page.locator(".flash")).to_contain_text("Code review turned on")
    expect(page.locator("#f-review .badge")).to_have_text("On")
    page.get_by_role("link", name=re.compile("^Off")).click()
    expect(page.locator("#f-local")).to_be_visible()
    expect(page.locator("#f-review")).to_have_count(0)


def test_slack_is_set_up_entirely_from_its_page(page, server, viewport, monkeypatch):
    from factory.ui import integrations as I
    page.goto(server.url + "/settings")
    page.get_by_role("navigation", name="Settings").get_by_role("link", name="Slack").click()
    expect(page.get_by_role("link", name="Create the app in Slack")).to_have_attribute("href", re.compile(r"^https://api\.slack\.com/apps\?new_app=1&manifest_json="))
    bot, app = "xoxb-" + "1234567890-abcdefABCDEF", "xapp-" + "1-A0123456789-abcdef0123456789"
    for label, value in (("Bot token", bot), ("App-level token", app)):
        card = page.locator("form.card", has=page.get_by_role("heading", name=label))
        card.get_by_label(label).fill(value)
        card.get_by_role("button", name="Save").click()
        expect(page.locator(".flash")).to_contain_text("Token saved")
    assert bot not in page.content() and app not in page.content()
    assert (server.root / "secrets" / "slack_app_token").read_text() == app
    monkeypatch.setattr(I, "_http", lambda *a, **k: (200, {"ok": True, "url": "wss://x"}))
    page.locator("form.card", has=page.get_by_role("heading", name="App-level token")).get_by_role("button", name="Test").click()
    expect(page.locator(".flash")).to_contain_text("Socket Mode is on")
    # the orchestrator, now connected, records whoever types /factory
    dbm.set_status(server.db, "slack_unknown_senders", '[{"id": "U0777ABCDEF", "name": "juan", "channel": "C0555ABCDEF", "ts": 1}]')
    page.reload()
    page.get_by_role("row", name=re.compile("juan")).get_by_role("button", name="Use this").click()
    expect(page.locator(".badge", has_text="Slack is on")).to_be_visible()
    slack = tomllib.loads((server.root / "config.overrides.toml").read_text())["slack"]
    assert (slack["user_id"], slack["channel"]) == ("U0777ABCDEF", "C0555ABCDEF")
    page.get_by_role("button", name="Send a test message").click()
    expect(page.locator(".flash")).to_contain_text("Sent.")


def test_settings_side_list_is_tappable_and_event_names_do_not_break(page, server, viewport):
    page.goto(server.url + "/telegram")
    for strong in page.locator(".check.evt strong").all():
        box = strong.bounding_box()
        assert box["height"] < 30, f"{strong.inner_text()} wrapped"          # one line: names are never broken mid-word
    if viewport == "phone":
        for link in page.get_by_role("navigation", name="Settings").get_by_role("link").all():
            assert link.bounding_box()["height"] >= 44, link.inner_text()


def test_ticket_actions_stay_on_screen_even_with_an_unbreakable_title(page, server, viewport):
    page.goto(server.url + LABELS)
    width = page.evaluate("document.documentElement.clientWidth")
    for name in ("Auto #4", "Build #4"):
        box = page.get_by_role("button", name=name).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (name, box, width)
