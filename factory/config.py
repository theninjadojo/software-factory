import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Route:
    harness: str
    model: str
    effort: str


@dataclass(frozen=True)
class Role:
    """A read-only stage agent (analyst, designer, architect) triggered by a label; its output is posted on the ticket."""
    name: str
    label: str
    done_label: str
    model: str
    effort: str


DEFAULT_ROLES = (
    Role("analyst", "factory:analyze", "stage:analysed", "sonnet", "medium"),
    Role("designer", "factory:design", "stage:designed", "sonnet", "medium"),
    Role("architect", "factory:architect", "stage:architected", "opus", "high"),
)


@dataclass(frozen=True)
class RunnerCfg:
    engine: str = "podman"           # container engine that runs sandboxes: "podman" or "docker"
    image: str = "localhost/factory-agent:latest"
    work_dir: str = "/srv/factory/work"
    proxy_socket: str = "/srv/factory/run/proxy.sock"
    allow_hosts: tuple[str, ...] = ("api.anthropic.com",)
    claude_env_file: str = "/srv/factory/secrets/claude.env"   # CLAUDE_CODE_OAUTH_TOKEN=...
    timeout_seconds: int = 1800
    memory: str = "3g"
    cpus: str = "2"
    max_turns: int = 40
    rate_limit_backoff_seconds: int = 3600
    max_patch_bytes: int = 2_000_000
    max_files: int = 100
    # Agent/tooling config and git plumbing: a PR editing these can run code on a developer's machine. Matched at any depth.
    deny_dirs: tuple[str, ...] = (".git", ".github", ".claude", ".githooks", ".agents", ".husky")
    deny_files: tuple[str, ...] = (".gitmodules", ".gitattributes", "codeowners", ".mcp.json", "opencode.json")
    thinking_tokens: dict = field(default_factory=lambda: {"low": 2000, "medium": 8000, "high": 24000})


@dataclass(frozen=True)
class CiCfg:
    """Watch the repos' own CI on factory PRs and report it; optionally let the agent fix failures."""
    enabled: bool = True
    fix_rounds: int = 1               # extra agent attempts after a failed CI run (0 = report only)
    wait_for_checks_minutes: int = 10  # how long to wait for any check to appear before reporting "no CI ran"
    timeout_minutes: int = 90
    log_tail_chars: int = 6000


@dataclass(frozen=True)
class ProjectRepo:
    repo: str            # owner/name
    role: str = ""       # one line telling the agent and the classifier what this repo is for


@dataclass(frozen=True)
class Project:
    """Repos that work together. Agents get all of them checked out side by side and may change several in one task."""
    name: str
    repos: tuple[ProjectRepo, ...]
    description: str = ""


@dataclass(frozen=True)
class Config:
    db_path: str
    poll_seconds: int
    dry_run: bool
    confidence_threshold: float
    token_file: str | None
    trigger_label: str
    repos: list[str]                     # every repo polled: [github].repos plus all project members
    trusted_permissions: frozenset[str]
    routes: dict[str, Route]
    runner: RunnerCfg = field(default_factory=RunnerCfg)
    telegram_token_file: str | None = None
    telegram_chat_id: int | None = None
    classifier_backend: str = "rules"
    openrouter_key_file: str | None = None
    jev_model: str = "typesafe/jev-1.13"
    projects: tuple[Project, ...] = ()
    roles: tuple[Role, ...] = DEFAULT_ROLES
    auto_label: str = "factory:auto"
    ci: CiCfg = field(default_factory=CiCfg)
    telegram_verbosity: str = "normal"          # quiet | normal | verbose
    telegram_events: tuple[str, ...] | None = None   # explicit allow-list; overrides verbosity


def project_for(cfg: Config, repo: str) -> Project:
    for p in cfg.projects:
        if any(r.repo == repo for r in p.repos):
            return p
    return Project(name=repo.split("/")[1], repos=(ProjectRepo(repo),))


def project_info(p: Project, issue_repo: str) -> dict:
    """What the classifier is told about the project (data only)."""
    return {"name": p.name, "description": p.description, "issue_filed_in": issue_repo.split("/")[1],
            "repositories": {r.repo.split("/")[1]: r.role for r in p.repos}}


def _runner(rn: dict) -> "RunnerCfg":
    r = RunnerCfg(**rn)
    if r.engine not in ("podman", "docker"):
        raise ValueError(f"runner.engine must be 'podman' or 'docker', got {r.engine!r}")
    return r


def _verbosity(v: str) -> str:
    from .events import LEVELS
    if v not in LEVELS:
        raise ValueError(f"telegram.verbosity must be one of {sorted(LEVELS)}, got {v!r}")
    return v


def _events(v):
    from .events import ALL_EVENTS
    if v is None:
        return None
    unknown = set(v) - ALL_EVENTS
    if unknown:
        raise ValueError(f"unknown telegram events: {sorted(unknown)}")
    return tuple(v)


def load(path: str) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    g, gh = raw["general"], raw["github"]
    routes = {k: Route(**v) for k, v in raw["routing"].items()}
    for level in ("low", "medium", "high"):
        if level not in routes:
            raise ValueError(f"routing.{level} missing")
    rn = {k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.get("runner", {}).items()}
    projects = tuple(
        Project(name=p["name"], description=p.get("description", ""),
                repos=tuple(ProjectRepo(**r) if isinstance(r, dict) else ProjectRepo(r) for r in p["repos"]))
        for p in raw.get("projects", []))
    repos = list(gh["repos"])
    seen: dict[str, str] = {}
    for p in projects:
        for r in p.repos:
            if r.repo in seen:
                raise ValueError(f"{r.repo} is in two projects: {seen[r.repo]} and {p.name}")
            seen[r.repo] = p.name
            if r.repo not in repos:
                repos.append(r.repo)
    return Config(
        db_path=g["db_path"],
        poll_seconds=int(g["poll_seconds"]),
        dry_run=bool(g["dry_run"]),
        confidence_threshold=float(g["confidence_threshold"]),
        token_file=gh.get("token_file"),
        trigger_label=gh["trigger_label"],
        repos=repos,
        trusted_permissions=frozenset(gh["trusted_permissions"]),
        routes=routes,
        runner=_runner(rn),
        telegram_token_file=raw.get("telegram", {}).get("token_file"),
        telegram_chat_id=raw.get("telegram", {}).get("chat_id"),
        classifier_backend=raw.get("classifier", {}).get("backend", "rules"),
        openrouter_key_file=raw.get("classifier", {}).get("key_file"),
        jev_model=raw.get("classifier", {}).get("model", "typesafe/jev-1.13"),
        projects=projects,
        roles=tuple(dataclasses.replace(r, **raw.get("roles", {}).get(r.name, {})) for r in DEFAULT_ROLES),
        auto_label=raw.get("auto", {}).get("label", "factory:auto"),
        ci=CiCfg(**raw.get("ci", {})),
        telegram_verbosity=_verbosity(raw.get("telegram", {}).get("verbosity", "normal")),
        telegram_events=_events(raw.get("telegram", {}).get("events")),
    )
