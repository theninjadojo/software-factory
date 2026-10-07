"""Find the tests a repository already has, so the Tests register can start from them instead of from a blank sheet.

Reads the default branch through the GitHub API and never runs anything: Python test files are parsed with `ast`, JavaScript and
TypeScript spec files are scanned for `test('...')` / `it('...')` calls with a literal name. Each test becomes a row in the shape
scenarios.parse_csv gives, so it goes through the same plan, preview and confirm as a CSV import. The file contents are untrusted;
only names and the first docstring line are taken from them, and scenarios._fields cleans and caps those."""
import ast
import re

MAX_FILES = 300
MAX_FILE_BYTES = 500_000
SKIP_DIRS = ("/node_modules/", "/.venv/", "/venv/", "/site-packages/", "/dist/", "/build/", "/.git/")
PY_FILE = re.compile(r"(?:^|/)(test_[\w-]+|[\w-]+_test)\.py$")
JS_FILE = re.compile(r"(?:^|/)([\w.-]+)\.(?:spec|test)\.[cm]?[jt]sx?$")
JS_TEST = re.compile(r"""(?<![\w.$])(?:test|it)(?:\.(?:only|skip|fixme|fail|slow))?\s*\(\s*(['"`])((?:\\.|(?!\1).){1,300})\1""")
JS_DESCRIBE = re.compile(r"""(?<![\w.$])(?:test\.)?describe(?:\.(?:only|skip|serial|parallel))?\s*\(\s*(['"`])((?:\\.|(?!\1).){1,200})\1""")


def test_files(paths: list[str]) -> list[str]:
    """The test files among a repository's paths, Python first, capped at MAX_FILES."""
    keep = [p for p in paths if not any(d in "/" + p for d in SKIP_DIRS) and (PY_FILE.search(p) or JS_FILE.search(p))]
    return sorted(keep, key=lambda p: (not p.endswith(".py"), p))[:MAX_FILES]


def feature_of(path: str) -> str:
    """The feature a test file is about, from its name: tests/test_screenboard.py -> screenboard, e2e/checkout.spec.ts -> checkout."""
    name = path.rsplit("/", 1)[-1]
    if (m := PY_FILE.search(path)):
        name = m.group(1)
        name = name[5:] if name.startswith("test_") else name[:-5]
    elif (m := JS_FILE.search(path)):
        name = m.group(1)
    return name.replace("_", " ").replace("-", " ").strip()


def humanize(name: str) -> str:
    """test_a_failing_run_opens_a_ticket -> A failing run opens a ticket."""
    words = re.sub(r"^test_?", "", name).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().split("\n", 1)[0].strip()


def python_tests(path: str, text: str) -> list[dict]:
    """Every test pytest or unittest would collect by name: top-level test functions, and test methods of Test* classes or
    unittest.TestCase subclasses. The id is pytest's node id (path::Class::name)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out = []
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)

    def add(node, cls: str = "") -> None:
        title = _first_line(ast.get_docstring(node)) or humanize(node.name)
        out.append({"title": title, "id": "::".join(x for x in (path, cls, node.name) if x)})

    for node in tree.body:
        if isinstance(node, funcs) and node.name.startswith("test"):
            add(node)
        elif isinstance(node, ast.ClassDef):
            bases = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases}
            if node.name.startswith("Test") or any(b.endswith("TestCase") for b in bases):
                for m in node.body:
                    if isinstance(m, funcs) and m.name.startswith("test"):
                        add(m, node.name)
    return out


def _without_comments(text: str) -> str:
    """The text with // and /* */ comments turned into spaces (newlines kept), so commented-out tests are not found and positions
    stay the same. Strings are left alone, so a URL's // is not a comment."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i) or text.startswith("/*", i):
            close = "\n" if text[i + 1] == "/" else "*/"
            end = text.find(close, i + 2)
            end = n if end < 0 else end + (2 if close == "*/" else 0)       # a line comment keeps its newline
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _call_end(text: str, i: int) -> int:
    """The index just past the ) that closes the ( at text[i], skipping strings (comments are already blanked); the end of the text if it never closes."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def js_tests(path: str, text: str) -> list[dict]:
    """Tests named with a literal string: test('...'), it('...'), with the innermost describe('...') around it as a prefix of the id
    (Playwright's `path › describe › title`). Template strings with ${...} are skipped: their name is only known at run time."""
    text = _without_comments(text)
    out, seen = [], set()
    groups = []                                             # (start, end, name) of each describe(...) call
    for m in JS_DESCRIBE.finditer(text):
        if "${" not in m.group(2):
            groups.append((m.start(), _call_end(text, text.rindex("(", m.start(), m.start(1))), re.sub(r"\\(.)", r"\1", m.group(2))))
    for m in JS_TEST.finditer(text):
        name = m.group(2)
        if "${" in name:
            continue
        name = re.sub(r"\\(.)", r"\1", name)
        group = max(((s, g) for s, e, g in groups if s < m.start() < e), default=(0, ""))[1]
        tid = " › ".join(x for x in (path, group, name) if x)
        if tid not in seen:
            seen.add(tid)
            out.append({"title": name[:1].upper() + name[1:], "id": tid})
    return out


def tests_in(path: str, text: str) -> list[dict]:
    return python_tests(path, text) if path.endswith(".py") else js_tests(path, text)


def discover(gh, repo: str) -> tuple[list[dict], dict]:
    """(rows, info): one import row per test on the default branch, and what was read (ref, files, skipped, truncated).
    Raises on a failure to read the repository itself; a file that cannot be read is counted in info['skipped']."""
    ref = gh.default_branch(repo)
    paths = gh.tree(repo, ref)
    files = test_files(paths)
    info = {"ref": ref, "files": len(files), "skipped": 0, "capped": len([p for p in paths if PY_FILE.search(p) or JS_FILE.search(p)]) > MAX_FILES}
    rows = []
    for p in files:
        try:
            text = gh.raw_file(repo, p, ref, MAX_FILE_BYTES).decode("utf-8", errors="replace")
        except Exception:
            info["skipped"] += 1
            continue
        feature = feature_of(p)
        for t in tests_in(p, text):
            rows.append({"feature": feature, "title": t["title"], "steps": "", "expected": "", "status": "active", "playwright_test": t["id"]})
    return rows, info


def new_only(rows: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    """The rows whose test is not linked to a scenario yet, and how many were already linked."""
    have = {r["pw_test"] for r in existing if r["pw_test"]}
    fresh = [r for r in rows if r["playwright_test"][:300] not in have]
    return fresh, len(rows) - len(fresh)
