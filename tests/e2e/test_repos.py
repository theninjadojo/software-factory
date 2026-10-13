"""Settings, General in a browser: the standalone repositories as rows, the checks made as they are typed, folding, and saving."""
from pathlib import Path

from factory.config import load


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def test_add_describe_and_save(page, server):
    page.goto(server.url + "/settings?section=general")
    sec, save = page.locator("details.sr-sec"), page.get_by_role("button", name="Save changes")
    assert sec.locator("tbody tr").count() == 3 and sec.get_by_text("1 repository", exact=True).is_visible()
    sec.get_by_label("What repository 1 is for").fill("The billing API")
    sec.get_by_label("Repository 2", exact=True).fill("https://github.com/theninjadojo/little-ninjas")
    sec.get_by_label("What repository 2 is for").fill("The web app")
    assert sec.get_by_label("Repository 2", exact=True).input_value() == "theninjadojo/little-ninjas"      # the pasted address became owner/name
    sec.get_by_label("Repository 3", exact=True).fill("theninjadojo/little-ninjas-mobile")
    sec.get_by_role("button", name="Add repository").click()
    assert sec.get_by_label("Repository 4", exact=True).evaluate("el => el === document.activeElement")
    sec.get_by_label("Repository 4", exact=True).fill("theninjadojo/ninja-packages")
    sec.get_by_label("What repository 4 is for").fill("Shared packages")
    assert sec.get_by_text("4 repositories", exact=True).is_visible()
    assert page.locator(".sr-sec .f-err").count() == 0 and save.is_enabled()
    assert overflow(page) <= 0
    save.click()
    assert page.get_by_text("Saved").first.is_visible()
    cfg = load(str(Path(server.root) / "config.toml"))
    assert cfg.repos[:4] == ["your-org/standalone-service", "theninjadojo/little-ninjas", "theninjadojo/little-ninjas-mobile", "theninjadojo/ninja-packages"]
    assert cfg.repo_roles == {"your-org/standalone-service": "The billing API", "theninjadojo/little-ninjas": "The web app",
                              "theninjadojo/ninja-packages": "Shared packages"}
    assert page.locator("details.sr-sec").get_by_label("What repository 4 is for").input_value() == "Shared packages"
    assert not page.errors


def test_a_wrong_or_unreachable_repository_is_marked_and_blocks_saving(page, server):
    server.refused = {"other-org/secret": "The factory's GitHub token cannot see this repository. Check the name, or give the token access to it."}
    before = (Path(server.root) / "config.toml").read_text()
    page.goto(server.url + "/settings?section=general")
    sec, save = page.locator("details.sr-sec"), page.get_by_role("button", name="Save changes")
    repo = sec.get_by_label("Repository 2", exact=True)
    repo.fill("not a repo")
    sec.get_by_label("What repository 2 is for").fill("something")
    assert sec.get_by_text("Write the repository as owner/name, like your-org/shop-web.").is_visible()
    assert repo.get_attribute("aria-invalid") == "true" and save.is_disabled()
    assert sec.get_by_text("1 to correct").is_visible()
    repo.fill("other-org/secret")
    assert page.locator(".sr-sec .f-err").count() == 0 and save.is_enabled()
    save.click()                                                      # well formed, but the token cannot see it: the server says so
    assert sec.get_by_text("The factory's GitHub token cannot see this repository.").is_visible()
    assert repo.input_value() == "other-org/secret" and sec.get_by_label("What repository 2 is for").input_value() == "something"
    assert save.is_disabled() and overflow(page) <= 0
    assert (Path(server.root) / "config.overrides.toml").exists() is False and (Path(server.root) / "config.toml").read_text() == before
    repo.fill("other-org/visible")                                    # changed: the server's mark goes
    assert page.locator(".sr-sec .f-err").count() == 0 and save.is_enabled()
    assert not [e for e in page.errors if "422" not in e]


def test_the_part_folds_and_stays_folded(page, server):
    page.goto(server.url + "/settings?section=general")
    sec = page.locator("details.sr-sec")
    assert sec.get_by_label("Repository 1", exact=True).is_visible()
    sec.locator("summary").click()
    assert not sec.get_by_label("Repository 1", exact=True).is_visible()
    assert sec.get_by_text("1 repository", exact=True).is_visible()   # folded, it still says how many
    page.reload()
    assert not page.locator("details.sr-sec").get_by_label("Repository 1", exact=True).is_visible()
    page.locator("details.sr-sec summary").click()
    assert page.locator("details.sr-sec").get_by_label("Repository 1", exact=True).is_visible()
    assert not page.errors
