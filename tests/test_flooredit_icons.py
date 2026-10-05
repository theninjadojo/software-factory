import unittest

from factory.ui import flooredit


class BuildPanelIcons(unittest.TestCase):
    def test_every_tool_has_an_icon(self):
        for t, _, _ in flooredit.TOOLS:
            self.assertIn(t, flooredit.TOOL_ICONS, t)
        for _, items in flooredit.TERRAIN_TOOLS:
            for t, _ in items:
                if not t.startswith("g-"):         # ground tools keep their colour swatch
                    self.assertTrue(flooredit.TOOL_ICONS.get(t) or flooredit.GENERIC_ICON)

    def test_icons_are_decorative_and_headings_have_one(self):
        html = flooredit._category("Belts", [("belt", "Draw belt")], True)
        self.assertEqual(html.count('<svg class="fe-part-ico'), 2)         # the heading and the tool
        self.assertEqual(html.count('aria-hidden="true"'), 2)
        self.assertIn("<span>Draw belt</span>", html)                      # the word stays

    def test_unknown_tool_falls_back(self):
        self.assertIn(flooredit.GENERIC_ICON, flooredit._category("Mystery", [("zzz", "Zzz")], False))


if __name__ == "__main__":
    unittest.main()
