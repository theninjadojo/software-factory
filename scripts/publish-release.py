#!/usr/bin/env python3
"""Create a GitHub Release for a tag and upload the files in a folder, with no `gh` CLI (standard library only).
Safe to run again after a failure: it reuses the release for the tag, uploads only the files it does not have yet, and retries a dropped
connection. The release is a draft until every file is up, so a half-finished release is never the public "latest".
A beta tag (vX.Y.Z-beta.N) is published as a prerelease: GitHub never makes it "latest", so stable installs do not see it.
Env: GH_TOKEN, GITHUB_REPOSITORY, RELEASE_TAG (else GITHUB_REF_NAME), GITHUB_SHA (the commit to tag when the tag does not exist yet); GITHUB_API / GITHUB_UPLOADS override the endpoints (tests).
Usage: publish-release.py <folder-of-assets>"""
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("GITHUB_API", "https://api.github.com")
UPLOADS = os.environ.get("GITHUB_UPLOADS", "https://uploads.github.com")
TRIES, PAUSE = 4, 15            # whole attempts, and seconds before the second (then doubled)


def call(method: str, url: str, token: str, body: bytes | None = None, ctype: str = "application/json"):
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": ctype, "User-Agent": "shikumi-release"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def transient(e: Exception) -> bool:
    """A dropped or timed-out connection or a GitHub 5xx is worth another try; any other answer (401, 403, 422 ...) is not."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    return isinstance(e, (OSError, http.client.HTTPException))


def publish(folder: Path, repo: str, tag: str, token: str, sha: str = "") -> str:
    rels = call("GET", f"{API}/repos/{repo}/releases?per_page=100", token)       # drafts included, newest first
    rel = next((r for r in rels if r.get("tag_name") == tag), None)
    if rel is None:
        body = {"tag_name": tag, "name": f"Shikumi {tag}", "generate_release_notes": True, "draft": True, "prerelease": "-beta." in tag}
        if sha:
            body["target_commitish"] = sha          # creates the tag at this commit when it does not exist yet
        rel = call("POST", f"{API}/repos/{repo}/releases", token, json.dumps(body).encode())
    have = set()
    for a in rel.get("assets", []):
        if a.get("state") == "uploaded":
            have.add(a["name"])
        else:                                       # a half-sent file from an earlier attempt blocks the same name
            call("DELETE", f"{API}/repos/{repo}/releases/assets/{a['id']}", token)
    for f in sorted(p for p in folder.iterdir() if p.is_file()):
        if f.name in have:
            print("already there", f.name)
            continue
        name = urllib.parse.quote(f.name)
        call("POST", f"{UPLOADS}/repos/{repo}/releases/{rel['id']}/assets?name={name}", token, f.read_bytes(), "application/octet-stream")
        print("uploaded", f.name)
    if rel.get("draft"):
        rel = call("PATCH", f"{API}/repos/{repo}/releases/{rel['id']}", token, json.dumps({"draft": False}).encode())
    return rel.get("html_url", "")


def publish_with_retries(*args) -> str:
    """Each attempt starts from what GitHub already has, so a failure halfway costs only the files that did not arrive."""
    for attempt in range(1, TRIES + 1):
        try:
            return publish(*args)
        except Exception as e:
            if attempt == TRIES or not transient(e):
                raise
            wait = PAUSE * 2 ** (attempt - 1)
            print(f"attempt {attempt} failed ({type(e).__name__}: {e}); trying again in {wait}s", flush=True)
            time.sleep(wait)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        sys.exit("usage: publish-release.py <folder>")
    print(publish_with_retries(Path(sys.argv[1]), os.environ["GITHUB_REPOSITORY"], os.environ.get("RELEASE_TAG") or os.environ["GITHUB_REF_NAME"],
                  os.environ["GH_TOKEN"], os.environ.get("GITHUB_SHA", "")))
