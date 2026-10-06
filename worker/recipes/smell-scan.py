#!/usr/bin/env python3
"""Code-smell scan recipe (docs/scanner.md). Standard library only. Runs in the checkout (the worker sets the working directory).

It only READS files: it never imports, builds or runs anything from the repository, and skips symlinks. The rules come from the
orchestrator through the file named by FACTORY_JOB_PARAMS (outside the checkout); findings are written as a JSON list to the file
named by FACTORY_FINDINGS_FILE: [{"smell", "path", "line", "snippet"}]. The orchestrator validates them again.

    command = ["/path/to/smell-scan.py"]        in worker.toml
"""
import contextlib
import json
import os
import re
import signal
import sys

MAX_FILE = 1_000_000          # bytes; larger files are skipped
MAX_FINDINGS = 1000           # the orchestrator refuses more than this in one result
MAX_LINE = 2000               # characters of a line that are searched
FILE_SECONDS = 2.0            # one smell may spend this long matching one file; then the file is skipped for that smell
MAX_SLOW = 3                  # after this many slow files the smell is dropped for the rest of the run
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv", "target"}
SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".swift", ".kt", ".java", ".rb", ".rs", ".c", ".cc", ".cpp", ".cs", ".php", ".sh"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
IGNORED_STEMS = {"__init__", "__main__", "conftest", "setup"}


class Slow(Exception):
    pass


@contextlib.contextmanager
def time_limit(seconds: float):
    """Raise Slow in the block after `seconds` (SIGALRM; CPython checks for signals inside a regex match). No limit off the main thread or
    where there is no setitimer; the worker's recipe timeout is then the only bound."""
    def fire(signum, frame):
        raise Slow()
    try:
        old = signal.signal(signal.SIGALRM, fire)
    except (ValueError, AttributeError):
        yield
        return
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def glob_re(pattern: str) -> re.Pattern:
    """`**/` is any number of folders, `**` anything, `*` and `?` stay inside one path part."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def walk(root: str, exclude: list[re.Pattern]):
    """Relative paths of regular, text, not-too-large files, in a stable order."""
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(base, d)))
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if os.path.islink(full) or any(x.fullmatch(rel) for x in exclude):
                continue
            try:
                if os.path.getsize(full) > MAX_FILE:
                    continue
                with open(full, "rb") as f:
                    head = f.read(8192)
            except OSError:
                continue
            if b"\0" not in head:
                yield rel, full


def lines_of(full: str) -> list[str]:
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def is_test(rel: str) -> bool:
    parts = rel.lower().split("/")
    name = parts[-1]
    return any(p in TEST_DIRS for p in parts[:-1]) or "test" in name or "spec" in name


def scan(root: str, params: dict) -> list[dict]:
    exclude = [glob_re(g) for g in params.get("exclude", [])]
    files = list(walk(root, exclude))
    out: list[dict] = []

    def add(smell, path, line, snippet):
        if len(out) < MAX_FINDINGS:
            out.append({"smell": smell, "path": path, "line": line, "snippet": snippet.strip()[:300]})

    rules = params.get("smells", [])
    regexes = {r["id"]: (re.compile(r["pattern"]), [glob_re(g) for g in r.get("globs", ["**/*"])]) for r in rules if r["type"] == "regex"}
    slow: dict[str, int] = {}
    test_names = {os.path.basename(rel).lower() for rel, _ in files if is_test(rel)}
    for rel, full in files:
        ext = os.path.splitext(rel)[1].lower()
        text = None
        for r in rules:
            sid, kind = r["id"], r["type"]
            if kind == "regex":
                pattern, globs = regexes[sid]
                if slow.get(sid, 0) < MAX_SLOW and any(g.fullmatch(rel) for g in globs):
                    text = lines_of(full) if text is None else text
                    try:
                        with time_limit(FILE_SECONDS):
                            for n, line in enumerate(text, 1):
                                if pattern.search(line[:MAX_LINE]):
                                    add(sid, rel, n, line)
                    except Slow:
                        slow[sid] = slow.get(sid, 0) + 1
                        print(f"[smell-scan] smell {sid}: {rel} skipped, the pattern took more than {FILE_SECONDS:g}s", file=sys.stderr)
                        if slow[sid] >= MAX_SLOW:
                            print(f"[smell-scan] smell {sid} dropped: too slow", file=sys.stderr)
            elif kind == "long-file" and ext in SOURCE_EXT:
                text = lines_of(full) if text is None else text
                if len(text) > int(r.get("max_lines", 800)):
                    add(sid, rel, len(text), f"more than {int(r.get('max_lines', 800))} lines")      # no count: the finding must stay the same as it grows
            elif kind == "missing-tests" and ext in SOURCE_EXT and not is_test(rel):
                stem = os.path.splitext(os.path.basename(rel))[0].lower()
                if stem not in IGNORED_STEMS and not any(stem in t for t in test_names):
                    add(sid, rel, 0, f"no test file mentions {stem}")
    return out


def main() -> int:
    params_file, out_file = os.environ.get("FACTORY_JOB_PARAMS"), os.environ.get("FACTORY_FINDINGS_FILE")
    if not params_file or not out_file:
        print("[smell-scan] this recipe must be run by the factory worker for a scan job", file=sys.stderr)
        return 2
    with open(params_file) as f:
        params = json.load(f)
    findings = scan(".", params)
    with open(out_file, "w") as f:
        json.dump(findings, f)
    print(f"[smell-scan] {len(findings)} finding(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
