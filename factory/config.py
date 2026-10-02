import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .classifier import KIND_ALIASES, KINDS


@dataclass(frozen=True)
class Route:
    harness: str
    model: str
    effort: str


CLAUDE_COMMAND = 'claude -p "$(cat /task/prompt.txt)" --model "$MODEL" --max-turns "$MAX_TURNS" --dangerously-skip-permissions'
CODEX_COMMAND = 'codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -m "$MODEL" "$(cat /task/prompt.txt)"'
GEMINI_COMMAND = 'gemini --yolo --model "$MODEL" --prompt "$(cat /task/prompt.txt)"'


@dataclass(frozen=True)
class HarnessCfg:
    """An agent CLI the sandbox can run. The command is configuration (admin-set), never derived from ticket text; it runs
    inside the locked-down sandbox with $MODEL, $MAX_TURNS and the prompt at /task/prompt.txt."""
    name: str
    image: str
    env_file: str                        # KEY=value lines handed to the sandbox (the agent's credential)
    command: str
    allow_hosts: tuple[str, ...]         # added to the egress allowlist while this harness is enabled
    enabled: bool = True
    experimental: bool = False
    env_var: str = ""                    # the variable the UI writes into env_file
    notes: str = ""


@dataclass(frozen=True)
class Role:
    """A read-only stage agent (analyst, designer, architect) triggered by a label; its output is posted on the ticket."""
    name: str
    label: str
    done_label: str
    model: str
    effort: str
    harness: str = "claude-code"


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
class ReviewCfg:
    """An independent code-review agent. It reads the PR branches and posts a comment; it never approves, requests
    changes, merges or edits code. Off by default (it costs a run per PR); pick a different harness for a second opinion."""
    enabled: bool = False
    auto: bool = True                    # review every PR the factory opens
    label: str = "factory:review"        # a person applies it to a ticket to (re-)review its PRs
    done_label: str = "stage:reviewed"
    model: str = "sonnet"
    effort: str = "high"
    harness: str = "claude-code"


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
    review: ReviewCfg = field(default_factory=ReviewCfg)
    harnesses: dict = field(default_factory=dict)      # name -> HarnessCfg (claude-code is always available)
    telegram_verbosity: str = "normal"          # quiet | normal | verbose
    telegram_events: tuple[str, ...] | None = None   # explicit allow-list; overrides verbosity
    kind_aliases: dict = field(default_factory=lambda: dict(KIND_ALIASES))   # label -> kind (bug/feature/docs/chore/question)


def default_harnesses(rn: "RunnerCfg") -> dict:
    secrets = Path(rn.claude_env_file).parent
    return {
        "claude-code": HarnessCfg("claude-code", rn.image, rn.claude_env_file, CLAUDE_COMMAND, ("api.anthropic.com",), True, False,
                                  "", "Claude Code. The credential is managed on the Credentials page."),
        "codex": HarnessCfg("codex", "localhost/factory-agent-codex:latest", str(secrets / "codex.env"), CODEX_COMMAND, ("api.openai.com",),
                            False, True, "CODEX_API_KEY", "OpenAI Codex CLI (codex exec). Build the image from sandbox/codex/. Not yet verified end to end."),
        "gemini": HarnessCfg("gemini", "localhost/factory-agent-gemini:latest", str(secrets / "gemini.env"), GEMINI_COMMAND,
                             ("generativelanguage.googleapis.com",), False, True, "GEMINI_API_KEY",
                             "Google Gemini CLI. Build the image from sandbox/gemini/. Needs an API key. Not yet verified end to end."),
    }


def harness_for(cfg: "Config", name: str):
    """The harness a route or role asked for, or None. claude-code always exists, built from the [runner] settings."""
    return cfg.harnesses.get(name) or (default_harnesses(cfg.runner).get(name) if name == "claude-code" else None)


def allowed_hosts(cfg: "Config") -> tuple[str, ...]:
    """Everything the egress proxy may tunnel to: the runner's own list plus the hosts of every enabled harness."""
    hosts = list(cfg.runner.allow_hosts)
    for h in (cfg.harnesses or default_harnesses(cfg.runner)).values():
        if h.enabled:
            hosts += [x for x in h.allow_hosts if x not in hosts]
    return tuple(hosts)


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


def _aliases(v) -> dict:
    if v is None:
        return dict(KIND_ALIASES)
    bad = {k: x for k, x in v.items() if x not in KINDS}
    if bad:
        raise ValueError(f"classifier.kind_aliases values must be one of {sorted(KINDS)}, got {bad}")
    return dict(v)


def _harnesses(raw: dict, rn: "RunnerCfg") -> dict:
    out = default_harnesses(rn)
    for name, over in raw.get("harnesses", {}).items():
        over = {k: (tuple(v) if k == "allow_hosts" else v) for k, v in over.items()}
        if name in out:
            out[name] = dataclasses.replace(out[name], **over)
        else:
            missing = {"image", "env_file", "command", "allow_hosts"} - set(over)
            if missing:
                raise ValueError(f"harnesses.{name}: missing {sorted(missing)}")
            out[name] = HarnessCfg(name=name, **over)
    for h in out.values():
        if not h.command.strip() or "\x00" in h.command or len(h.command) > 4000:
            raise ValueError(f"harnesses.{h.name}.command is empty or invalid")
        if not all(re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", x) for x in h.allow_hosts):
            raise ValueError(f"harnesses.{h.name}.allow_hosts has an invalid host name")
    return out


def _check_harness_use(routes: dict, roles: tuple, harnesses: dict, review: "ReviewCfg | None" = None) -> None:
    uses = [(f"routing.{k}", r.harness) for k, r in routes.items()] + [(f"roles.{r.name}", r.harness) for r in roles]
    if review is not None and review.enabled:
        uses.append(("review", review.harness))
    for where, harness in uses:
        if harness not in harnesses or not harnesses[harness].enabled:
            raise ValueError(f"{where} uses the harness {harness!r}, which does not exist or is not enabled")


def _verbosity(v: str) -> str:
    from .events import LEVELS
    if v not in LEVELS:
        raise ValueError(f"telegram.verbosity must be one of {sorted(LEVELS)}, got {v!r}")
    return v


def _events(v):
    from .events import ALL_EVENTS
    if v is None or v == "level":
        return None
    unknown = set(v) - ALL_EVENTS
    if unknown:
        raise ValueError(f"unknown telegram events: {sorted(unknown)}")
    return tuple(v)


def overrides_path(path: str) -> Path:
    """The file the UI writes. Deep-merged over the hand-edited config, which is never rewritten."""
    p = Path(path)
    return p.with_name(p.stem + ".overrides.toml")


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_raw(path: str) -> dict:
    raw = tomllib.loads(Path(path).read_text())
    op = overrides_path(path)
    return deep_merge(raw, tomllib.loads(op.read_text())) if op.exists() else raw


def load(path: str) -> Config:
    return parse(load_raw(path))


def parse(raw: dict) -> Config:
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
    runner_cfg = _runner(rn)
    harnesses = _harnesses(raw, runner_cfg)
    roles = tuple(dataclasses.replace(r, **raw.get("roles", {}).get(r.name, {})) for r in DEFAULT_ROLES)
    review = ReviewCfg(**raw.get("review", {}))
    if review.effort not in ("low", "medium", "high"):
        raise ValueError("review.effort must be low, medium or high")
    _check_harness_use(routes, roles, harnesses, review)
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
        runner=runner_cfg,
        telegram_token_file=raw.get("telegram", {}).get("token_file"),
        telegram_chat_id=raw.get("telegram", {}).get("chat_id"),
        classifier_backend=raw.get("classifier", {}).get("backend", "rules"),
        openrouter_key_file=raw.get("classifier", {}).get("key_file"),
        jev_model=raw.get("classifier", {}).get("model", "typesafe/jev-1.13"),
        projects=projects,
        roles=roles,
        auto_label=raw.get("auto", {}).get("label", "factory:auto"),
        ci=CiCfg(**raw.get("ci", {})),
        review=review,
        harnesses=harnesses,
        telegram_verbosity=_verbosity(raw.get("telegram", {}).get("verbosity", "normal")),
        telegram_events=_events(raw.get("telegram", {}).get("events")),
        kind_aliases=_aliases(raw.get("classifier", {}).get("kind_aliases")),
    )
