"""Settings, Projects in a browser: adding repository rows, the checks made as the form is filled in, and saving."""
from pathlib import Path

from factory.config import load


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def new_project(page):
    return page.locator("form.pj-form fieldset").last


def test_add_repositories_and_save(page, server):
    page.goto(server.url + "/settings?section=projects")
    box, save = new_project(page), page.get_by_role("button", name="Save projects")
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
    yuki = next(p for p in load(str(Path(server.root) / "config.toml")).projects if p.name == "Yuki")
    assert [r.repo for r in yuki.repos] == ["theninjadojo/ninja-packages", "theninjadojo/yuki-api", "theninjadojo/yuki-web", "theninjadojo/yuki-docs"]
    assert not page.errors


def test_a_wrong_field_is_marked_and_blocks_saving(page, server):
    before = (Path(server.root) / "config.toml").read_text()
    page.goto(server.url + "/settings?section=projects")
    box, save = new_project(page), page.get_by_role("button", name="Save projects")
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
    assert (Path(server.root) / "config.toml").read_text() == before
    assert not page.errors
