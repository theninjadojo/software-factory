"""A new project's first commit: render its templates and push them to the repositories a person created.

The files come only from this repository's templates/ (kinds and deploy overlays, see templates/README.md), never from an agent.
The values put into them are checked here: a slug, an owner name, a title reduced to plain words, and the plan's summary
(already validated text) in README.md only. That is why this one step may write .github/workflows: it is trusted code writing
trusted files. Agents still can never touch .github (runner deny_dirs).

The push is the only time the factory pushes to a default branch, so it is guarded: the repository must be fresh, meaning no
commits or a single commit holding only what GitHub's "new repository" page adds (a README, a licence, a .gitignore). It is a
plain push, never a force push, so anything that landed meanwhile makes it fail instead of being overwritten. A repository whose
newest commit carries the SCAFFOLD_TRAILER was already scaffolded by an earlier attempt and is skipped."""
import logging
import re
import shutil
import secrets
import subprocess
import time
from pathlib import Path

from . import newproject as N
from .runner import git, git_env

log = logging.getLogger("factory.scaffold")
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
FRESH_FILES = re.compile(r"(README(\.md)?|LICEN[CS]E(\.md|\.txt)?|\.gitignore)", re.I)
SKIP = {"node_modules", ".venv", "dist", "build", ".next", ".astro", ".expo", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "test-results", "playwright-report", "coverage", ".DS_Store"}
SCAFFOLD_TRAILER = "Scaffolded-by: Shikumi"
IDENT = ["-c", "user.name=shikumi", "-c", "user.email=software-factory@users.noreply.github.com"]
MAX_FILES, MAX_BYTES = 3000, 40_000_000


class ScaffoldError(Exception):
    """A failure with a message safe to show a person."""


def plain_title(title: str) -> str:
    """The project's title as it may appear in package.json, YAML and Markdown: letters, digits, spaces and a few marks."""
    t = " ".join(re.sub(r"[^\w .&'-]", " ", title, flags=re.UNICODE).split())[:60].strip(" .-'")
    return t or "New project"


def _walk(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not p.is_symlink() and not (set(rel.parts) & SKIP):
            yield rel, p


def render(kind: str, overlay: str, values: dict, extra: dict | None = None) -> dict[str, tuple[bytes, bool]]:
    """{path: (content, executable)} for one repository: the kind, then its overlay on top, with the placeholders replaced.
    values: name (slug), title, owner, summary. extra: generated files ({path: text}) added last."""
    base, top = TEMPLATES / "kinds" / kind, TEMPLATES / "deploy" / overlay / kind
    if not base.is_dir():
        raise ScaffoldError(f"There is no template for {kind}.")
    if not top.is_dir():
        raise ScaffoldError(f"There is no {overlay} deploy template for {kind}.")
    snake = values["name"].replace("-", "_")
    swaps = [("shikumi-app", values["name"]), ("shikumi_app", snake), ("SHIKUMI_APP", snake.upper()), ("Shikumi App", values["title"]),
             ("shikumi-org", values["owner"])]
    out = {}
    for root in (base, top):
        for rel, p in _walk(root):
            path = rel.as_posix()
            for a, b in swaps:
                path = path.replace(a, b)
            data = p.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass                                         # binary (an icon): copied as it is
            else:
                for a, b in swaps:
                    text = text.replace(a, b)
                text = text.replace("<!-- shikumi:summary -->", values.get("summary", ""))
                data = text.encode("utf-8")
            out[path] = (data, bool(p.stat().st_mode & 0o111))
    for path, text in (extra or {}).items():
        out[path] = (text.encode("utf-8"), False)
    if len(out) > MAX_FILES or sum(len(d) for d, _ in out.values()) > MAX_BYTES:
        raise ScaffoldError("The template is too large to push.")
    return out


def project_notes(plan: dict, repos: list[dict], title: str, repo: str) -> str:
    """A section appended to each repository's CLAUDE.md: what the project is and how its repositories fit together."""
    p, d = N.CATALOGUE[plan["pattern"]], N.DEPLOYS[plan["deploy"]]
    others = [r for r in repos if r["repo"] != repo]
    lines = [f"\n\n## This project: {title}\n", plan["summary"], "",
             f"Pattern: {p.title}. Deploys to: {d.title}. {d.how}", ""]
    if others:
        lines += ["Other repositories of this project:"] + [f"- `{r['repo']}`: {r['role']}" for r in others] + [""]
    if plan.get("first_features"):
        lines += ["First features, in order:"] + [f"{i}. {f}" for i, f in enumerate(plan["first_features"], 1)] + [""]
    if plan.get("notes"):
        lines += ["Notes from planning: " + plan["notes"], ""]
    return "\n".join(lines)


def files_for(plan: dict, title: str, repos: list[dict], index: int) -> dict[str, tuple[bytes, bool]]:
    r = repos[index]
    owner, name = r["repo"].split("/")
    values = {"name": name, "title": plain_title(title), "owner": owner, "summary": plan["summary"]}
    out = render(r["kind"], r["overlay"], values)
    claude = out.get("CLAUDE.md", (b"", False))[0].decode("utf-8") + project_notes(plan, repos, values["title"], r["repo"])
    out["CLAUDE.md"] = (claude.encode("utf-8"), False)
    return out


def _fresh(base: Path, env: dict) -> tuple[bool, str]:
    """(fresh, why not). Fresh: no commits, or one commit holding only what GitHub's new-repository page adds."""
    try:
        count = int(git(["rev-list", "--count", "HEAD"], base, env).stdout.strip())
    except subprocess.CalledProcessError:
        return True, ""                                      # an empty repository: HEAD has no commit yet
    if git(["log", "-1", "--format=%B"], base, env).stdout.rstrip().endswith(SCAFFOLD_TRAILER):
        return False, "scaffolded"
    if count > 1:
        return False, f"it already has {count} commits"
    files = [f for f in git(["ls-files"], base, env).stdout.splitlines() if f]
    extra = [f for f in files if not FRESH_FILES.fullmatch(f)]
    return (False, f"it already has files ({', '.join(extra[:3])})") if extra else (True, "")


def _write(base: Path, files: dict[str, tuple[bytes, bool]]) -> None:
    root = base.resolve()
    for path, (data, exe) in files.items():
        dest = (base / path).resolve()
        if root not in dest.parents or path.startswith(".git/") or "/.git/" in path:
            raise ScaffoldError("The template has a file outside the repository.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(0o755 if exe else 0o644)


def push_error(stderr: str) -> str:
    """A fixed message for a refused push; git's own output may echo URLs and is not shown."""
    s = stderr.lower()
    if "workflow" in s and ("scope" in s or "permission" in s):
        return "GitHub refused the workflow files. Give the factory's GitHub token Workflows: read and write for this repository."
    if "protected branch" in s or "gh006" in s or "rule" in s and "violat" in s:
        return "The default branch is protected. Allow the factory's account to push to it once, or remove the rule until the first commit is in."
    if "non-fast-forward" in s or "fetch first" in s or "rejected" in s:
        return "Something was pushed to the repository meanwhile. Start again with an empty repository."
    if "403" in s or "permission" in s or "denied" in s:
        return "The factory's GitHub token cannot push to this repository. Add the repository to the token with Contents: read and write."
    if "not found" in s or "404" in s:
        return "The repository was not found, or the factory's GitHub token cannot see it."
    return "The push failed. Check the repository and the token, then try again."


def push(work_dir: str, token: str, repo: str, files: dict[str, tuple[bytes, bool]], message: str, url: str | None = None) -> str:
    """Clone `repo`, write the files over it, commit and push to its default branch. Returns the commit sha, or "" when an earlier
    attempt already scaffolded it. Raises ScaffoldError. url: where to clone from (tests); GitHub by default."""
    env = git_env(token)
    d = Path(work_dir) / f"scaffold-{repo.replace('/', '-')}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    try:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        try:
            git(["clone", "--quiet", url or f"https://github.com/{repo}.git", str(d)], None, env, 300)
        except subprocess.CalledProcessError as e:
            raise ScaffoldError(push_error(e.stderr or ""))
        fresh, why = _fresh(d, env)
        if why == "scaffolded":
            return ""
        if not fresh:
            raise ScaffoldError(f"{repo} is not a new repository: {why}. The first commit only goes into a new, empty one.")
        try:
            branch = git(["symbolic-ref", "--short", "HEAD"], d, env).stdout.strip() or "main"
        except subprocess.CalledProcessError:
            branch = "main"
        if not re.fullmatch(r"[\w.-]+(/[\w.-]+)*", branch):
            raise ScaffoldError("The repository's default branch has an unusual name.")
        _write(d, files)
        git(["add", "-A"], d, env)
        git([*IDENT, "commit", "-q", "-m", f"{message}\n\n{SCAFFOLD_TRAILER}"], d, env)
        try:
            git(["push", "origin", f"HEAD:refs/heads/{branch}"], d, env, 300)
        except subprocess.CalledProcessError as e:
            log.warning("scaffold: push to %s refused: %s", repo, (e.stderr or "")[-500:])
            raise ScaffoldError(push_error(e.stderr or ""))
        return git(["rev-parse", "HEAD"], d, env).stdout.strip()
    except subprocess.CalledProcessError:
        log.exception("scaffold: git failed for %s", repo)
        raise ScaffoldError("A git step failed while preparing the first commit.")
    finally:
        shutil.rmtree(d, ignore_errors=True)
