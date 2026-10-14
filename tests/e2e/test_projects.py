"""Settings, Projects in a browser: the New project form on top, saved projects as rows that open on their form and build order,
adding repository rows, the checks made as a form is filled in, saving and removing."""
from pathlib import Path

from factory.config import load


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def new_project(page):
    return page.locator("section.pj-new form")


def projects(server) -> list:
    return list(load(str(Path(server.root) / "config.toml")).projects)


def test_add_repositories_and_save(page, server):
    page.goto(server.url + "/settings?section=projects")
    box, save = new_project(page), page.get_by_role("button", name="Add project")
    assert box.locator("tbody tr").count() == 2
    box.get_by_label("Name").fill("Yuki")
    for n in range(2):
        box.get_by_role("button", name="Add repository").click()
    assert box.locator("tbody tr").count() == 4
    assert box.get_by_label("Repository 4", exact=True).evaluate("el => el === document.activeElement")      # the new row is ready to type in
    repos = ["https://github.com/theninjadojo/ninja-packages", "theninjadojo/yuki-api", "theninjadojo/yuki-web", "git@github.com:theninjadojo/yuki-docs.git"]
    for n, repo in enumerate(repos, 1):
        box.get_by_label(f"Repository {n}", exact=True).fill(repo)
        box.get_by_label(f"Role of repository {n}").fill(f"part {n}")
    assert box.get_by_label("Repository 1", exact=True).input_value() == "theninjadojo/ninja-packages"         # the pasted address became owner/name
    assert page.locator(".f-err").count() == 0 and save.is_enabled()
    assert overflow(page) <= 0
    save.click()
    assert page.get_by_text("Saved").first.is_visible()
    yuki = next(p for p in projects(server) if p.name == "Yuki")
    assert [r.repo for r in yuki.repos] == ["theninjadojo/ninja-packages", "theninjadojo/yuki-api", "theninjadojo/yuki-web", "theninjadojo/yuki-docs"]
    row = page.locator("details.pj-item[open]")                       # back on the page with the new project's row open
    assert row.locator(".pj-title").inner_text() == "Yuki" and row.locator(".pj-meta").inner_text() == "4 repositories"
    assert new_project(page).get_by_label("Name").input_value() == ""
    page.once("dialog", lambda d: d.accept())
    row.get_by_role("button", name="Remove project").click()
    page.wait_for_url("**/settings?section=projects&ok=saved")
    assert [p.name for p in projects(server)] == ["shop"] and page.locator("details.pj-item").count() == 1
    assert not page.errors


def test_a_wrong_field_is_marked_and_blocks_saving(page, server):
    before = (Path(server.root) / "config.toml").read_text()
    page.goto(server.url + "/settings?section=projects")
    box, save = new_project(page), page.get_by_role("button", name="Add project")
    repo = box.get_by_label("Repository 1", exact=True)
    repo.fill("not a repo")
    assert page.locator(".f-err").count() == 0                      # not while it is still being typed
    box.get_by_label("Role of repository 1").fill("the api")
    assert box.get_by_text("Write the repository as owner/name, like your-org/shop-web.").is_visible()
    assert repo.get_attribute("aria-invalid") == "true"
    assert save.is_disabled()
    assert page.get_by_text("Correct the field marked above to save.").is_visible()
    assert overflow(page) <= 0
    repo.fill("your-org/yuki-api")                                    # corrected: the mark goes as it is typed
    assert page.locator(".f-err").count() == 0 and save.is_enabled()
    save.click()                                                      # the name is still missing: nothing is sent, nothing typed is lost
    assert box.get_by_text("Give the project a name.").is_visible()
    assert box.get_by_label("Name").evaluate("el => el === document.activeElement")
    assert repo.input_value() == "your-org/yuki-api" and box.get_by_label("Role of repository 1").input_value() == "the api"
    box.get_by_label("Name").fill("Shop")                             # the saved project's name, in other letters
    assert box.get_by_text("Another project already has this name.").is_visible()
    repo.fill("your-org/shop-web")
    box.get_by_label("Role of repository 1").fill("the web")
    assert box.get_by_text("your-org/shop-web is in two projects: shop and Shop.").is_visible()
    assert (Path(server.root) / "config.toml").read_text() == before
    assert not page.errors


def test_saved_projects_are_rows_that_open_on_their_form_and_build_order(page, server):
    page.goto(server.url + "/settings?section=projects")
    row = page.locator("details.pj-item").first
    assert page.locator("details.pj-item").count() == 1 and not row.evaluate("el => el.open")
    assert page.get_by_role("heading", name="New project").bounding_box()["y"] < row.bounding_box()["y"]
    assert row.locator(".pj-title").inner_text() == "shop"
    assert row.locator(".pj-meta").inner_text() == "3 repositories · build order set"
    assert not row.get_by_role("heading", name="Build order").is_visible()
    row.locator("summary").click()
    assert row.get_by_role("heading", name="Build order").is_visible()
    assert row.get_by_text("publishes with a GitHub release").is_visible()
    assert row.get_by_label("Name").input_value() == "shop" and row.get_by_label("Repository 3", exact=True).input_value() == "your-org/shop-packages"
    assert overflow(page) <= 0
    row.get_by_label("Description").fill("The shop, edited")
    row.get_by_role("button", name="Save project").click()
    assert page.get_by_text("Saved").first.is_visible()
    assert projects(server)[0].description == "The shop, edited"
    assert projects(server)[0].repo("your-org/shop-packages").publish is not None                  # the build order is kept
    row = page.locator("details.pj-item").first
    assert row.evaluate("el => el.open")                              # it stays open after saving
    page.once("dialog", lambda d: d.dismiss())
    row.get_by_role("button", name="Remove project").click()
    assert [p.name for p in projects(server)] == ["shop"]             # not without a yes
    page.once("dialog", lambda d: d.accept())
    row.get_by_role("button", name="Remove project").click()          # still needed by the screen captures: refused, and said why
    assert page.get_by_text("your-org/shop-web must be a configured repo").is_visible()
    assert [p.name for p in projects(server)] == ["shop"] and page.locator("details.pj-item[open]").count() == 1
    assert not [e for e in page.errors if "status of 422" not in e]   # the refusal itself is logged by the browser
