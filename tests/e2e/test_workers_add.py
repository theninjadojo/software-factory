"""Workers, Add a worker in a browser: the Recipes drop-down, what each recipe is for, and the install command it leads to."""
from pathlib import Path


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def box(page, name):
    return page.get_by_role("checkbox", name=name, exact=True)


def test_choose_recipes_from_the_drop_down(page, server):
    cfg, tmp = Path(server.root) / "config.toml", Path(server.root) / "config.toml.new"       # replaced whole: the open page keeps polling
    tmp.write_text(cfg.read_text().replace("[workers]\nenabled = true", f'[workers]\nenabled = true\ntokens_file = "{server.root}/worker_tokens"'))
    tmp.replace(cfg)
    page.goto(server.url + "/workers")
    pick, shown = page.locator("details.wk-pick"), page.locator(".wk-chosen")
    assert shown.inner_text() == "Web tests" and not pick.evaluate("el => el.open")
    pick.locator("summary").click()
    assert page.get_by_text("Runs a project's Playwright suite and keeps a screenshot of every test").is_visible()
    assert page.get_by_text("Mac with Apple Silicon").is_visible()                       # where each one runs
    box(page, "Screenshots").check()
    box(page, "iOS").check()
    assert shown.inner_text() == "Web tests, Screenshots, iOS"
    assert box(page, "iOS").get_attribute("aria-describedby") == "wk-ios-d"            # read out with what it is for
    assert overflow(page) <= 0
    for name in ("Web tests", "Screenshots", "iOS"):
        box(page, name).uncheck()
    assert shown.inner_text() == "None chosen"
    page.locator("h2", has_text="Add a worker").click()                                  # a click elsewhere closes it
    assert not pick.evaluate("el => el.open")
    page.get_by_role("button", name="Create token and show install command").click()     # none chosen: not sent, the drop-down opens
    assert pick.evaluate("el => el.open") and "/workers/add" not in page.url
    box(page, "iOS").check()
    box(page, "Web tests").check()
    page.get_by_role("button", name="Create token and show install command").click()
    page.wait_for_url("**/workers/add")
    cmd = page.locator("pre", has_text="install-worker.sh").inner_text()
    assert 'WORKER_RECIPES="web ios" WORKER_IOS_SCHEME="<Xcode scheme>"' in cmd
    assert page.get_by_text("Before running it:").is_visible()
    assert page.locator(".wk-list li").count() == 2
    assert overflow(page) <= 0
    assert not page.errors
