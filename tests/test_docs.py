import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def md_files():
    return [ROOT / n for n in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")] + sorted((ROOT / "docs").rglob("*.md"))


def broken_links(path):
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    bad = []
    for target in LINK.findall(text):
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        if not (path.parent / target.split("#")[0]).exists():
            bad.append(target)
    return bad


# The runtime image ships neither the docs nor the top-level markdown files.
@unittest.skipUnless((ROOT / "README.md").exists() and (ROOT / "docs").is_dir(), "docs are not in this tree")
class DocsTest(unittest.TestCase):
    def test_relative_links_resolve(self):
        for f in md_files():
            self.assertEqual(broken_links(f), [], str(f.relative_to(ROOT)))

    def test_docs_index_lists_every_doc(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for f in (ROOT / "docs").glob("*.md"):
            if f.name != "README.md":
                self.assertIn(f"({f.name})", index, f.name)

    def test_no_temp_files_at_root(self):
        self.assertEqual(list(ROOT.glob("TEMP-*")), [])

    def test_broken_link_is_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.md"
            p.write_text("[a](missing.md) [b](https://x.y) [c](#h)")
            self.assertEqual(broken_links(p), ["missing.md"])


if __name__ == "__main__":
    unittest.main()
