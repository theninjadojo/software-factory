"""Read-only copies of the configured repositories' default branches, for the factory chat and the video writer.

The orchestrator keeps one shallow clone per repository under `state/code/<owner>__<name>` and refreshes it at most every
REFRESH_SECONDS, before a chat or writer run that wants it. The clone is made with the token passed in the environment (runner.git_env),
so nothing in the copy holds a credential. The runs get the copies mounted read-only (mounts()), still with no network and no token:
the agent can read and search the code, never change it or reach GitHub.

Code a video shows is read here by the factory (excerpt()), never taken from model output: the path must name a regular file inside a
copy (no symlinks, no `..`), be text, and the lines are cut to fixed limits."""
import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("factory.codecopy")
REFRESH_SECONDS = 900
MAX_MB = 500                          # a larger repository is not kept (the runs would be slow to search it)
MAX_FILE_BYTES = 400_000
MAX_LINES = 40
LINE_CHARS = 110
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}")
REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}):([^\0\n]{1,300})")
_lock = threading.Lock()


def root(cfg) -> Path:
    return Path(cfg.db_path).parent / "code"


def dirname(repo: str) -> str:
    return repo.replace("/", "__")


def path_of(cfg, repo: str) -> Path | None:
    """The copy of `repo` when it is a configured repository and has been made, else None."""
    if repo not in cfg.repos or not REPO.fullmatch(repo):
        return None
    p = root(cfg) / dirname(repo)
    return p if (p / ".git").is_dir() else None


def _size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


def refresh(cfg, token: str | None, now=time.time, run_git=None) -> dict[str, str]:
    """Make or update the copy of every configured repository whose copy is older than REFRESH_SECONDS. Returns {repo: problem} for
    the ones that could not be kept. Never raises: a run then sees the copies it has."""
    from .runner import git, git_env
    if not token:
        return {r: "no GitHub token" for r in cfg.repos}
    run_git = run_git or git
    env, problems = git_env(token), {}
    with _lock:
        root(cfg).mkdir(mode=0o700, parents=True, exist_ok=True)
        for repo in cfg.repos:
            if not REPO.fullmatch(repo):
                continue
            dest = root(cfg) / dirname(repo)
            stamp = dest / ".git" / "factory-refreshed"
            try:
                if stamp.is_file() and now() - stamp.stat().st_mtime < REFRESH_SECONDS:
                    continue
                if (dest / ".git").is_dir():
                    run_git(["fetch", "--quiet", "--depth", "1", "origin", "HEAD"], dest, env, 300)
                    run_git(["reset", "--quiet", "--hard", "FETCH_HEAD"], dest, env, 120)
                    run_git(["clean", "--quiet", "-fdx"], dest, env, 120)
                else:
                    part = dest.with_name(dest.name + ".part")
                    shutil.rmtree(part, ignore_errors=True)
                    run_git(["clone", "--quiet", "--depth", "1", "--single-branch", f"https://github.com/{repo}.git", str(part)], None, env, 600)
                    if _size_mb(part) > MAX_MB:
                        shutil.rmtree(part, ignore_errors=True)
                        problems[repo] = f"larger than {MAX_MB} MB"
                        continue
                    part.rename(dest)
                stamp.touch()
                subprocess.run(["chmod", "-R", "a+rX", str(dest)], check=False, capture_output=True)
            except (subprocess.SubprocessError, OSError) as e:
                log.warning("code copy of %s: %s", repo, type(e).__name__)
                problems[repo] = "could not be read from GitHub"
    return problems


def mounts(cfg) -> list[tuple[str, str]]:
    """[(host path, container path)] of every copy there is, for a read-only mount under /work/code."""
    out = []
    for repo in cfg.repos:
        p = path_of(cfg, repo)
        if p is not None:
            out.append((str(p), f"/work/code/{dirname(repo)}"))
    return out


def listing(cfg) -> str:
    """The prompt's note of what is mounted where, or '' when no copy is."""
    lines = [f"- {repo}: /work/code/{dirname(repo)}" for repo in cfg.repos if path_of(cfg, repo) is not None]
    return "".join(x + "\n" for x in lines)


def resolve(cfg, ref: str) -> tuple[str, str, Path] | None:
    """('owner/name', 'path/in/repo', file) for a reference 'owner/name:path' to a regular text-sized file inside a copy, else None."""
    m = REF.fullmatch(str(ref or "").strip())
    if not m:
        return None
    repo, rel = m[1], m[2].strip().lstrip("/")
    base = path_of(cfg, repo)
    if base is None or not rel or any(part in ("", "..", ".git") for part in rel.split("/")):
        return None
    f = base / rel
    try:
        if f.is_symlink() or not f.is_file() or f.stat().st_size > MAX_FILE_BYTES:
            return None
        if not f.resolve().is_relative_to(base.resolve()):
            return None
    except OSError:
        return None
    return repo, rel, f


def excerpt(cfg, ref: str, first: int, last: int) -> tuple[str, list[dict], int, int] | None:
    """(repo:path, lines, first, last) of up to MAX_LINES lines of a file in a copy, 1-based and inclusive, for a code scene; None when
    the reference or the file is not usable (not text, or the range is outside it)."""
    got = resolve(cfg, ref)
    if got is None or not all(isinstance(x, int) and not isinstance(x, bool) for x in (first, last)):
        return None
    repo, rel, f = got
    data = f.read_bytes()
    if b"\0" in data[:8000]:
        return None
    text = data.decode("utf-8", errors="replace").splitlines()
    first = max(1, first)
    last = min(len(text), last, first + MAX_LINES - 1)
    if not text or first > last:
        return None
    lines = [{"t": " ", "text": "".join(c if c.isprintable() else " " for c in text[i - 1].expandtabs(4))[:LINE_CHARS]}
             for i in range(first, last + 1)]
    return f"{repo}:{rel}", lines, first, last
