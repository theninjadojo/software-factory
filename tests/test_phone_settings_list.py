import unittest

from factory.ui import forms


class PhoneSettingsList(unittest.TestCase):
    def test_every_page_is_listed(self):
        html = forms.page_list(None)
        self.assertIn(">General<", html)
        for _, rows in forms.SIDE_GROUPS:
            for _, name, href in rows:
                self.assertIn(f'href="{href}"', html)
                self.assertIn(forms.esc(name), html)

    def test_search_and_empty_state(self):
        html = forms.page_list(None, q="telegram")
        self.assertIn("Telegram", html)
        self.assertNotIn("Slack", html)
        none = forms.page_list(None, q="<zzz>")
        self.assertIn("Nothing matches", none)
        self.assertIn("&lt;zzz&gt;", none)

    def test_related_lists_siblings_only(self):
        html = forms.related("runner")
        self.assertIn("Related pages", html)
        self.assertIn("Harnesses", html)
        self.assertNotIn(">Sandbox &amp; limits<", html)
        self.assertEqual(forms.related("general"), "")


if __name__ == "__main__":
    unittest.main()
