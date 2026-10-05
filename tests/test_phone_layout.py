import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "factory" / "ui" / "static" / "style.css").read_text()


class PhoneLayout(unittest.TestCase):
    def test_dialog_close_button_keeps_its_width(self):
        m = re.search(r"\.nd-dhead > button \{([^}]*)\}", CSS)
        self.assertIsNotNone(m)
        self.assertIn("flex:none", m.group(1))
        self.assertIn("white-space:nowrap", m.group(1))

    def test_phone_station_name_is_not_squeezed_by_refs(self):
        m = re.search(r"\.sd-mach \{ display:grid; grid-template-columns:([^;]*);", CSS)
        self.assertIsNotNone(m)
        self.assertIn("minmax(max-content,1fr)", m.group(1))
        self.assertNotIn("auto", m.group(1))


if __name__ == "__main__":
    unittest.main()
