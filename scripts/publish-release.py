#!/usr/bin/env python3
"""Create a GitHub Release for a tag and upload the files in a folder, with no `gh` CLI (the self-hosted runners do not have it).
Env: GH_TOKEN, GITHUB_REPOSITORY, GITHUB_REF_NAME (the tag); GITHUB_API / GITHUB_UPLOADS override the endpoints (tests).
Usage: publish-release.py <folder-of-assets>"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("GITHUB_API", "https://api.github.com")
UPLOADS = os.environ.get("GITHUB_UPLOADS", "https://uploads.github.com")


def call(method: str, url: str, token: str, body: bytes | None = None, ctype: str = "application/json") -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": ctype, "User-Agent": "shikumi-release"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def publish(folder: Path, repo: str, tag: str, token: str) -> str:
    rel = call("POST", f"{API}/repos/{repo}/releases", token, json.dumps({
        "tag_name": tag, "name": f"Shikumi {tag}", "generate_release_notes": True}).encode())
    for f in sorted(p for p in folder.iterdir() if p.is_file()):
        name = urllib.parse.quote(f.name)
        call("POST", f"{UPLOADS}/repos/{repo}/releases/{rel['id']}/assets?name={name}", token, f.read_bytes(), "application/octet-stream")
        print("uploaded", f.name)
    return rel.get("html_url", "")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        sys.exit("usage: publish-release.py <folder>")
    print(publish(Path(sys.argv[1]), os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF_NAME"], os.environ["GH_TOKEN"]))
