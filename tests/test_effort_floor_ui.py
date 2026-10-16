"""#482: the minimum effort per step is shown and set on the Models by ticket size page."""
import unittest

from factory.config import FLOOR_STEPS, load
from test_ui_admin import AdminCase


class EffortFloorPage(AdminCase):
    def form(self, **floors):
        f = {"section": "routing"}
        for tier, model, eff in (("low", "haiku", "low"), ("medium", "sonnet", "medium"), ("high", "opus", "high")):
            f.update({f"routing.{tier}.harness": "claude-code", f"routing.{tier}.model": model, f"routing.{tier}.effort": eff})
        f.update({f"effort_floor.{s}": "high" if s == "implement" else "none" for s in FLOOR_STEPS})
        f.update({f"effort_floor.{k.replace('_', '-')}": v for k, v in floors.items()})
        return f

    def cfg(self):
        return load(str(self.root / "config.toml"))

    def test_the_page_shows_the_build_minimum_with_its_default(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/settings?section=routing", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Minimum effort per step", html)
        self.assertRegex(html, r'<select id="effort_floor.implement" name="effort_floor.implement">.*<option selected>high</option>')
        for step in FLOOR_STEPS:
            self.assertIn(f'name="effort_floor.{step}"', html)
        self.assertEqual(self.cfg().effort_floor, {"implement": "high"})

    def test_saving_sets_and_clears_minimums(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.form(implement="none", ci_fix="medium"))[0], 303)
        self.assertEqual(self.cfg().effort_floor, {"ci-fix": "medium"})
        self.assertIn('effort_floor', (self.root / "config.overrides.toml").read_text())
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.form())[0], 303)       # back to the defaults
        self.assertEqual(self.cfg().effort_floor, {"implement": "high"})

    def test_an_unknown_effort_is_refused(self):
        cookie, csrf = self.session()
        self.assertEqual(self.post(cookie, csrf, "/settings/save", self.form(implement="max"))[0], 422)
        self.assertEqual(self.cfg().effort_floor, {"implement": "high"})


if __name__ == "__main__":
    unittest.main()
