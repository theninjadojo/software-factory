import sqlite3
import unittest

from factory import scenarios as SC
from factory import testfind as TF

PY = '''
import unittest

def test_top_level():
    pass

def helper():
    pass

class TestThing:
    def test_with_docstring(self):
        """Saves the thing.

        More words."""

    def not_a_test(self):
        pass

class Scanner(unittest.TestCase):
    async def test_reads_a_file(self):
        pass

class Plain:
    def test_ignored(self):
        pass
'''

JS = '''
import { test, expect } from '@playwright/test';
test('loads the home page', async ({ page }) => {});
test.describe('Checkout', () => {
  test("pays with a card", async () => { expect(')').toBe("}"); });  // a ) in a comment
  test.skip('it\\'s skipped but listed', async () => {});
  test(`total for ${n} items`, async () => {});
  /* test('commented out') ) */
  // test('also commented out')
  test('see http://x.test/a', async () => {});
  test.describe('Refunds', () => {
    test('refunds in full', async () => {});
  });
  test('after the nested group', async () => {});
});
it('also counts', () => {});
latest('not a test');
'''


class FakeGH:
    def __init__(self, files: dict):
        self.files = files

    def default_branch(self, repo):
        return "main"

    def tree(self, repo, ref):
        return list(self.files) + ["README.md", "node_modules/x/a.test.js"]

    def raw_file(self, repo, path, ref, max_bytes=0):
        if self.files[path] is None:
            raise OSError("gone")
        return self.files[path].encode()


class TestFind(unittest.TestCase):
    def test_picks_test_files_and_skips_vendored_ones(self):
        paths = ["tests/test_a.py", "pkg/b_test.py", "src/app.py", "e2e/checkout.spec.ts", "web/x.test.jsx", "node_modules/y/z.spec.js",
                 ".venv/lib/test_q.py", "conftest.py"]
        self.assertEqual(TF.test_files(paths), ["pkg/b_test.py", "tests/test_a.py", "e2e/checkout.spec.ts", "web/x.test.jsx"])

    def test_feature_comes_from_the_file_name(self):
        self.assertEqual(TF.feature_of("tests/test_screen_board.py"), "screen board")
        self.assertEqual(TF.feature_of("pkg/parser_test.py"), "parser")
        self.assertEqual(TF.feature_of("e2e/check-out.spec.ts"), "check out")

    def test_python_tests_are_found_by_name_without_running_anything(self):
        got = TF.python_tests("tests/test_x.py", PY)
        self.assertEqual([t["id"] for t in got], ["tests/test_x.py::test_top_level", "tests/test_x.py::TestThing::test_with_docstring",
                                                   "tests/test_x.py::Scanner::test_reads_a_file"])
        self.assertEqual([t["title"] for t in got], ["Top level", "Saves the thing.", "Reads a file"])
        self.assertEqual(TF.python_tests("x.py", "def (:"), [])

    def test_js_tests_take_their_describe_and_skip_runtime_names(self):
        got = TF.js_tests("e2e/shop.spec.ts", JS)
        self.assertEqual([t["id"] for t in got], ["e2e/shop.spec.ts › loads the home page", "e2e/shop.spec.ts › Checkout › pays with a card",
                                                   "e2e/shop.spec.ts › Checkout › it's skipped but listed",
                                                   "e2e/shop.spec.ts › Checkout › see http://x.test/a", "e2e/shop.spec.ts › Refunds › refunds in full",
                                                   "e2e/shop.spec.ts › Checkout › after the nested group", "e2e/shop.spec.ts › also counts"])
        self.assertEqual(got[0]["title"], "Loads the home page")

    def test_discover_reads_the_default_branch_and_counts_unreadable_files(self):
        gh = FakeGH({"tests/test_x.py": PY, "e2e/shop.spec.ts": JS, "tests/test_gone.py": None})
        rows, info = TF.discover(gh, "o/r")
        self.assertEqual((info["ref"], info["files"], info["skipped"], info["capped"]), ("main", 3, 1, False))
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], {"feature": "x", "title": "Top level", "steps": "", "expected": "", "status": "active",
                                   "playwright_test": "tests/test_x.py::test_top_level"})

    def test_found_tests_import_once_through_the_normal_plan(self):
        db = sqlite3.connect(":memory:")
        SC.ensure_tables(db)
        rows, _ = TF.discover(FakeGH({"tests/test_x.py": PY}), "o/r")
        fresh, linked = TF.new_only(rows, SC.listing(db, "o/r"))
        self.assertEqual((len(fresh), linked), (3, 0))
        self.assertEqual({i["action"] for i in SC.plan(db, "o/r", fresh)}, {"create"})
        self.assertEqual(SC.apply(db, "o/r", fresh, set())["created"], 3)
        again, linked = TF.new_only(rows, SC.listing(db, "o/r"))
        self.assertEqual((again, linked), ([], 3))
        self.assertEqual(SC.get(db, "o/r", "TS-2")["pw_test"], "tests/test_x.py::TestThing::test_with_docstring")


if __name__ == "__main__":
    unittest.main()
