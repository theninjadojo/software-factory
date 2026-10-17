#!/usr/bin/env python3
"""Release workflow guard: a release made from a merge to main must be a higher version than every existing v<major>.<minor>.<patch> tag.
A beta is v<major>.<minor>.<patch>-beta.<n> and sorts below the release it leads to (v0.40.0-beta.2 < v0.40.0).
Usage: release_guard.py <tag> <ref_type>. A hand-pushed tag (ref_type 'tag') is the maintainer's call and is not compared."""
import re
import subprocess
import sys

PAT = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")


def parse(tag: str):
    m = PAT.match(tag)
    if not m:
        return None
    a, b, c, n = m.groups()
    return (int(a), int(b), int(c), 0, int(n)) if n is not None else (int(a), int(b), int(c), 1, 0)


def name(v) -> str:
    return f"v{v[0]}.{v[1]}.{v[2]}" + (f"-beta.{v[4]}" if v[3] == 0 else "")


def check(tag: str, existing: list[str]) -> str | None:
    """None when `tag` may be released, else the reason it may not."""
    v = parse(tag)
    if v is None:
        return f"{tag} is not a version tag (v<major>.<minor>.<patch>, or v<major>.<minor>.<patch>-beta.<n>)"
    top = max((parse(t) for t in existing if parse(t)), default=None)
    if top is not None and v <= top:
        return f"{tag} is not higher than the latest tag {name(top)}: bump VERSION to a newer number"
    return None


if __name__ == "__main__":
    tag, ref_type = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "branch"
    if ref_type == "tag":
        sys.exit(0 if parse(tag) else f"{tag} is not a version tag")
    tags = subprocess.run(["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True).stdout.split()
    problem = check(tag, tags)
    if problem:
        sys.exit(problem)
