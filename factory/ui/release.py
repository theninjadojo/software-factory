"""The release half of Settings > Updates and releases: bump VERSION of the factory's own repository, and merge that bump to kick off the release build.
Nothing is pushed to main: a bump is a pull request on a release/ branch, and merging it is what the release workflow reacts to."""
import urllib.error

from .. import updates, version
from . import integrations as I
from .labels import NO_TOKEN, _github_error, flash_set
from .views import csrf_field, esc

BRANCH = "release/v"
KINDS = {"patch": "Patch: a fix", "minor": "Minor: a feature that needs no action", "major": "Major: an install must change its config or data"}


def bump(current: str, kind: str) -> str | None:
    v = updates.parse(current)
    if not v or kind not in KINDS:
        return None
    a, b, c = v
    return {"patch": f"{a}.{b}.{c + 1}", "minor": f"{a}.{b + 1}.0", "major": f"{a + 1}.0.0"}[kind]


def _gh(h):
    token = I.read_secret(h.app.cfg(), "github")
    from ..github import GitHub
    return GitHub(token) if token else None


def _latest(h, repo: str, gh) -> tuple[str, str]:
    """(highest of VERSION on the default branch and the latest release tag, default branch)."""
    base = gh.default_branch(repo)
    on_main = gh.raw_file(repo, "VERSION", base).decode().strip()
    tag = ((updates.fetch_latest(repo, token=gh.token or "") or {}).get("tag") or "").lstrip("v")
    top = max((x for x in (on_main, tag) if updates.parse(x)), key=updates.parse, default="")
    return top, base


def release_body(h, csrf: str) -> str:
    """The "Publish a release" part of the Updates page. A GitHub failure is shown here and nowhere else: the page still renders."""
    cfg = h.app.cfg()
    repo = cfg.updates.repo
    gh = _gh(h)
    head = (f'<p>Releases come from <code>{esc(repo)}</code>: merging a change to its <code>VERSION</code> file '
            'runs the release workflow, which tests, builds and publishes the images and creates the GitHub Release.</p>')
    if gh is None:
        return head + f'<p class="muted">{esc(NO_TOKEN)}</p>'
    try:
        top, base = _latest(h, repo, gh)
        pulls = gh.open_pulls(repo, BRANCH)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:   # TypeError: a reply of another shape
        return head + f'<p class="bad-text" role="alert"><span aria-hidden="true">✕</span> Release information could not be loaded: {esc(_github_error(e))} Updates above still work.</p>'
    rows = ""
    for p in pulls:
        n = int(p["number"])
        rows += (f'<tr><td><a href="{esc(p["html_url"])}" rel="noopener noreferrer" target="_blank">#{n}</a></td><td>{esc(p["title"])}</td><td>'
                 f'<form method="post" action="/release/merge" class="inline">{csrf_field(csrf)}<input type="hidden" name="n" value="{n}">'
                 f'<button aria-label="Merge and release #{n}">Merge and release</button></form></td></tr>')
    waiting = ('<h3>Waiting to be released</h3><table class="stack"><thead><tr><th>PR</th><th>Title</th><th></th></tr></thead><tbody>' + rows + "</tbody></table>"
              if rows else "")
    opts = "".join(f'<option value="{k}">{esc(t)}{" → " + esc(bump(top, k) or "") if top else ""}</option>' for k, t in KINDS.items())
    form = (f'<h3>Bump the version</h3><p class="muted">Latest on <code>{esc(base)}</code> or released: <strong>{esc(top or "unknown")}</strong>.</p>'
            f'<form method="post" action="/release/bump" class="field">{csrf_field(csrf)}<label>Kind of change<select name="kind">{opts}</select></label>'
            '<button>Open the version PR</button></form>' if top else '<p class="muted">Could not read VERSION from the repository.</p>')
    return head + waiting + form


def bump_post(h, form, csrf: str) -> None:
    cfg, repo = h.app.cfg(), h.app.cfg().updates.repo
    gh = _gh(h)
    if gh is None:
        return _back(h, csrf, NO_TOKEN, "bad")
    try:
        top, base = _latest(h, repo, gh)
        new = bump(top, form.get("kind", ""))
        if new is None:
            return _back(h, csrf, "Choose patch, minor or major.", "bad")
        if any(p["head"]["ref"] == f"{BRANCH}{new}" for p in gh.open_pulls(repo, BRANCH)):
            return _back(h, csrf, f"A pull request for v{new} is already open.", "bad")
        url = gh.bump_file(repo, "VERSION", new + "\n", f"{BRANCH}{new}", base, f"Release v{new}",
                           f"Bumps `VERSION` to {new}. Merging this runs the release workflow (tests, images, GitHub Release).")
    except urllib.error.HTTPError as e:
        msg = "A branch or pull request for that version already exists." if e.code == 422 else _github_error(e)
        return _back(h, csrf, msg, "bad")
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:   # TypeError: a reply of another shape
        return _back(h, csrf, _github_error(e), "bad")
    _back(h, csrf, f"Opened {url}. Merge it below to start the release build.")


def merge_post(h, form, csrf: str) -> None:
    repo, n = h.app.cfg().updates.repo, form.get("n", "")
    gh = _gh(h)
    if gh is None:
        return _back(h, csrf, NO_TOKEN, "bad")
    if not (n.isdigit() and len(n) <= 9):
        return _back(h, csrf, "That is not a pull request.", "bad")
    try:
        pr = gh.get_pr(repo, int(n))
        if pr.get("state") != "open" or not pr["head"]["ref"].startswith(BRANCH):
            return _back(h, csrf, "Only an open release/ pull request can be merged here.", "bad")
        gh.merge_pr(repo, int(n), pr["head"]["sha"])
    except urllib.error.HTTPError as e:
        msg = f"GitHub would not merge it ({e.code}): a required check or review is missing, or it changed just now." if e.code in (405, 409, 422) else _github_error(e)
        return _back(h, csrf, msg, "bad")
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:   # TypeError: a reply of another shape
        return _back(h, csrf, _github_error(e), "bad")
    _back(h, csrf, f"Merged #{n}. The release workflow is building it; the new version appears as a release in a few minutes.")


def _back(h, csrf: str, msg: str, kind: str = "ok") -> None:
    flash_set(csrf, msg, kind)
    h._redirect("/updates")
