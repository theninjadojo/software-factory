#!/usr/bin/env python3
"""Release workflow guard: a release made from a merge to main must be a higher version than every existing v<major>.<minor>.<patch> tag.
Usage: release_guard.py <tag> <ref_type>. A hand-pushed tag (ref_type 'tag') is the maintainer's call and is not compared."""
import re
import subprocess
import sys

PAT = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse(tag: str):
    m = PAT.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None


def check(tag: str, existing: list[str]) -> str | None:
    """None when `tag` may be released, else the reason it may not."""
    v = parse(tag)
    if v is None:
        return f"{tag} is not a version tag (v<major>.<minor>.<patch>)"
    top = max((parse(t) for t in existing if parse(t)), default=None)
    if top is not None and v <= top:
        return f"{tag} is not higher than the latest tag v{'.'.join(map(str, top))}: bump VERSION to a newer number"
    return None


if __name__ == "__main__":
    tag, ref_type = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "branch"
    if ref_type == "tag":
        sys.exit(0 if parse(tag) else f"{tag} is not a version tag")
    tags = subprocess.run(["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True).stdout.split()
    problem = check(tag, tags)
    if problem:
        sys.exit(problem)
