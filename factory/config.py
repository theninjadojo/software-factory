import dataclasses
import os
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
    fallback_models: tuple[str, ...] = ()   # admin-set; tried in order on the same harness and effort when a model is unavailable


CLAUDE_COMMAND = ('claude -p "$(cat /task/prompt.txt)" --model "$MODEL" --max-turns "$MAX_TURNS" --dangerously-skip-permissions '
                  '--output-format json')
CODEX_COMMAND = 'codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -m "$MODEL" "$(cat /task/prompt.txt)"'
OPENCODE_COMMAND = 'opencode run --model "$MODEL" "$(cat /task/prompt.txt)"'
OPENCODE_IMAGE = "localhost/factory-agent-opencode:latest"
OPENCODE_NOTICE = ("Ticket text, code and repository contents are sent to {0} and the upstream model provider. "
                   "Free or logging tiers may retain or train on them.")
MODEL_RE = re.compile(r"[A-Za-z0-9._:/@+\[\]-]{1,200}")
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
    data_notice: str = ""                # shown on the Harnesses page: where ticket text and code go when this harness runs
    model_hint: str = ""                 # the model-id format this harness expects
    usage_format: str = ""               # how to read token counts from its output ("" none, "claude-json"); see runner.parse_usage


@dataclass(frozen=True)
class Role:
    """A read-only stage agent (analyst, designer, architect) triggered by a label; its output is posted on the ticket."""
    name: str
    label: str
    done_label: str
    model: str
    effort: str
    harness: str = "claude-code"
    design_files: bool = False           # designer only: also write static design mockups into the repo
    design_dir: str = "docs/design"      # where they go (design/ is often git-ignored: it holds local pulls of the design project)
    fallback_models: tuple[str, ...] = ()


DEFAULT_ROLES = (
    Role("analyst", "factory:analyze", "stage:analysed", "sonnet", "medium"),
    Role("designer", "factory:design", "stage:designed", "sonnet", "medium", "claude-code", True),
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
    # Sandboxes running at once (1-8). Each may use `memory` and `cpus`: keep max_parallel x memory within the host's RAM
    # (a 4 vCPU / 8 GB host fits 2 runs of 3g; 3 is the edge). The orchestrator warns at startup when it does not fit.
    max_parallel: int = 1
    max_turns: int = 40
    # Rendered previews of the designer's mockups: a sealed container (no network, read-only, no capabilities) turns each validated
    # canvas into a PNG that is committed to the draft PR. Off the moment the image is missing: the mockup files are still published.
    render_previews: bool = True
    render_image: str = "localhost/factory-render:latest"
    render_timeout_seconds: int = 120
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
    fallback_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class MockupsCfg:
    """What a build needs from the design stage. The designer's rendered mockup PNGs are handed to the build and review agents
    to match. mode: "block" = a ticket whose design stage should have produced mockups (it ran, and did not say there are no
    screens) is not built without them; "warn" = built anyway, with a note on the ticket; "off" = never checked.
    A person can bypass the block on one ticket with bypass_label."""
    mode: str = "block"
    bypass_label: str = "factory:skip-mockup"
    require_approval: bool = False       # True: the mockups also need a person's approval (the approve_label, or the design PR merged)
    approve_label: str = "factory:design-approved"
    # A person asked for screens to review (the new-ticket form's "Make screens for me to review"): auto runs the designer
    # before a build, the designer must draw mockups, and the build waits for a person's approval whatever mode says.
    request_label: str = "factory:screens-requested"


@dataclass(frozen=True)
class UpdatesCfg:
    """Tell the person in the UI when a newer release exists (one cached read of GitHub's public releases API, every few hours)."""
    check: bool = True
    repo: str = "theninjadojo/software-factory"
    token_file: str = ""                 # a private repo needs a token that can read it; empty: the [github] token (public repos need none)


PROMPT_MAX = 4000


@dataclass(frozen=True)
class PromptsCfg:
    """Standing instructions from the operator, added before the built-in prompt of each agent. Trusted configuration
    (config.toml or the admin UI), never ticket or repository text; the built-in rules win if they conflict."""
    all: str = ""                        # every agent, before its own text
    analyst: str = ""
    designer: str = ""
    architect: str = ""
    reviewer: str = ""
    implementer: str = ""                # build runs
    ci_fix: str = ""                     # CI-fix runs (not the implementer text)
    conflicts: str = ""                  # merge-conflict runs (not the implementer text)


def prompt_problem(text) -> str | None:
    """Why an operator prompt is not acceptable, or None. Line breaks and tabs are the only control characters allowed."""
    if not isinstance(text, str):
        return "must be text"
    if len(text) > PROMPT_MAX:
        return f"must be at most {PROMPT_MAX} characters"
    if any((ord(c) < 32 and c not in "\n\t") or c == "\x7f" or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        return "must not contain control characters other than line breaks and tabs"
    return None


def _prompts(v) -> "PromptsCfg":
    if not isinstance(v, dict):
        raise ValueError("[prompts] must be a table")
    known = {f.name for f in dataclasses.fields(PromptsCfg)}
    if (unknown := sorted(set(v) - known)):
        raise ValueError(f"unknown prompts keys {unknown}; use {sorted(known)}")
    out = {}
    for k, x in v.items():
        x = x.replace("\r\n", "\n") if isinstance(x, str) else x
        if (why := prompt_problem(x)):
            raise ValueError(f"prompts.{k} {why}")
        out[k] = x.strip()
    return PromptsCfg(**out)


@dataclass(frozen=True)
class PmCfg:
    """The project manager: a read-only agent that, on a periodic sweep, ranks a repository's factory tickets and says which
    are blocked by others. It may only add or remove the `priority: high` / `priority: low` labels it applied itself (a person's
    priority label always wins) and comment when it changes something. While it is on, a build ticket with an open blocker
    waits. Off by default (a sweep costs a run per repository)."""
    enabled: bool = False
    interval_minutes: int = 360           # at most one sweep per repository this often, and only when its tickets changed
    max_tickets: int = 40                 # tickets sent to the agent per sweep
    body_chars: int = 1500                # of each ticket's body
    unblock_label: str = "factory:unblocked"   # applied by a person with write access: ignore the PM's blockers on that ticket
    model: str = "sonnet"
    effort: str = "medium"
    harness: str = "claude-code"


@dataclass(frozen=True)
class HealthCfg:
    """The health watchdog (factory.health, run by factory-health.timer): alerts on Telegram when something that would stop the
    factory goes wrong. Free space is checked as a percentage and in GB; the lower of the two thresholds trips first."""
    enabled: bool = True
    disk_warn_percent: int = 15
    disk_crit_percent: int = 5
    disk_warn_gb: int = 5
    disk_crit_gb: int = 2
    extra_disk_paths: tuple[str, ...] = ()   # more directories whose filesystems to watch (state, work and container storage are automatic)
    mem_warn_mb: int = 512
    mem_crit_mb: int = 256
    stale_poll_minutes: int = 10           # no completed poll for this long (and 3 x poll_seconds): the orchestrator is hung or down
    failed_runs_warn: int = 5              # this many finished runs in a row all failed
    github_rate_warn: int = 200            # API calls left this hour
    repeat_hours: int = 6                  # remind this often while a problem persists
    heartbeat_url: str = ""                # optional dead-man's switch (e.g. healthchecks.io): pinged each run while nothing is critical


@dataclass(frozen=True)
class SubtasksCfg:
    """Mirror each pipeline step of a ticket as a GitHub sub-issue. Off by default: it adds writes and notifications."""
    enabled: bool = False


@dataclass(frozen=True)
class CiCfg:
    """Watch the repos' own CI on factory PRs and report it; optionally let the agent fix failures."""
    enabled: bool = True
    fix_rounds: int = 1               # extra agent attempts after a failed CI run (0 = report only)
    wait_for_checks_minutes: int = 10  # how long to wait for any check to appear before reporting "no CI ran"
    timeout_minutes: int = 90
    log_tail_chars: int = 6000
    queued_warn_minutes: int = 15      # CI queued this long without starting: say so (self-hosted runners may be offline)
    manual_wait_hours: int = 24        # keep watching this long for CI that a person starts by hand


@dataclass(frozen=True)
class ConflictsCfg:
    """Notice factory PRs that conflict with their base branch and resolve them when the label is on the ticket. The
    orchestrator merges the base into the PR branch; an agent edits only the conflicted files. Nothing is rebased or
    force-pushed. Independent of [ci]. Off by default (a resolution costs a run and pushes a merge commit)."""
    enabled: bool = False
    auto: bool = True                     # apply the label itself when a conflict is found (a person can always apply it)
    label: str = "factory:fix-conflicts"
    max_attempts: int = 10                # resolution runs per PR; then a person is asked


@dataclass(frozen=True)
class ScheduleCfg:
    """A scheduled job: on `every` (6h, 2d, 1w) or `cron` (UTC, five fields), fetch `source`, save a snapshot and open a ticket
    on `repo`. `labels` default to the auto label, so the classifier picks the next stage and only builds what it is sure about;
    use [] for a ticket nobody acts on, or e.g. ["factory:analyze"] for analysis only. See factory/schedules.py."""
    name: str
    repo: str
    source: dict
    every: str | None = None
    cron: str | None = None
    title: str = "Scheduled review: ${name} ${date}"
    instructions: str = ""                 # what the agent should do with the data; empty = a general review (schedules.DEFAULT_INSTRUCTIONS)
    labels: tuple[str, ...] | None = None  # None = [auto].label
    enabled: bool = True
    skip_if_open: bool = True              # do not open another ticket while the previous one is still open
    keep: int = 30                         # snapshots kept on disk per schedule
    max_data_chars: int = 30000            # of the data put in the ticket


@dataclass(frozen=True)
class SmellCfg:
    """A custom smell: lines matching the regex `pattern` in files matching `globs` (relative, `**` allowed). See factory/scanner.py."""
    id: str
    name: str
    pattern: str
    globs: tuple[str, ...] = ("**/*",)
    description: str = ""


@dataclass(frozen=True)
class ScanCfg:
    """One repository scanned on `every` or `cron` for the listed smell ids (presets and custom)."""
    name: str
    repo: str
    smells: tuple[str, ...]
    every: str | None = None
    cron: str | None = None
    exclude: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class ScannerCfg:
    """Code-smell scans (factory/scanner.py, docs/scanner.md). Off by default. Needs [workers] and a worker with the recipe.
    `labels` default to the analyst role's label: scan tickets are analysed, not built, unless the admin says otherwise."""
    enabled: bool = False
    recipe: str = "smell-scan"
    platform: str = "any"
    max_tickets: int = 5                   # opened per scan run; the rest is considered again next run
    max_findings_per_ticket: int = 50
    max_lines: int = 800                   # the long-files preset
    labels: tuple[str, ...] | None = None
    smells: tuple[SmellCfg, ...] = ()
    scans: tuple[ScanCfg, ...] = ()


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
class Viewport:
    name: str
    width: int
    height: int


@dataclass(frozen=True)
class ScreenPage:
    """One screen of a project repo, checked at each named viewport against `<baseline_dir>/<name>-<viewport>.png` in that repo."""
    repo: str
    name: str
    path: str                            # relative file served over http://127.0.0.1 from the repo root, inside the sealed container
    viewports: tuple[str, ...] = ()      # empty: every viewport
    mask: tuple[str, ...] = ()           # CSS selectors of dynamic regions, painted over before comparing
    wait_for: str = ""                   # CSS selector to wait for before the screenshot
    journey: str = ""                    # the Screens board groups pages by journey ([a-z0-9-]); empty: "Other screens"
    step: int = 0                        # order within the journey
    design: str = ""                     # optional: the Claude Design canvas (*.dc.html) of this screen, rendered beside it on the board


@dataclass(frozen=True)
class ScreenCapture:
    """A repo whose Playwright run the Screens board can start with its Build screens button. The run happens on a verification worker
    (factory/captures.py); `recipe` names a recipe defined on the worker itself, never a command."""
    repo: str
    recipe: str = "playwright-screens"
    platform: str = "any"                # a worker claims the job only if it declares this platform


@dataclass(frozen=True)
class ScreensCfg:
    """Screen verification during a build (factory/screens.py). Screens are listed here, in trusted config, never in the repo,
    so an agent cannot drop one. Fails closed: with pages configured for a repo, a missing image or baseline fails the build."""
    image: str = "localhost/factory-screens:latest"
    timeout_seconds: int = 300
    baseline_dir: str = "screens/baselines"
    threshold: float = 0.1               # per-pixel colour distance (0..1) above which a pixel counts as different
    max_diff_ratio: float = 0.001        # fraction of pixels allowed to differ (0.001 = 0.1 %)
    viewports: tuple[Viewport, ...] = (Viewport("desktop", 1440, 900), Viewport("mobile", 390, 844))
    pages: tuple[ScreenPage, ...] = ()
    captures: tuple[ScreenCapture, ...] = ()   # repos whose Playwright run the board's Build screens button starts on a worker
    label: str = "factory:screens-changed"   # put on the draft PR when a patch changes baseline images
    board_every: str = ""                # the Screens board: shoot every page on the default branch this often ("6h", "1d"); "" = only on request


@dataclass(frozen=True)
class WorkerCheck:
    """One verification a repo's patch must pass on a worker before anything is pushed. Trusted config only."""
    repo: str
    recipe: str                          # a recipe name defined on the worker itself; never a command
    platform: str = "any"                # a worker claims the job only if it declares this platform
    required: bool = True                # False: advisory. A failure is noted on the PR but never blocks the push or starts a fix round


@dataclass(frozen=True)
class WorkersCfg:
    """Verification workers (factory/jobs.py, workerapi.py, verify.py; docs/workers.md). Off by default."""
    enabled: bool = False
    listen: str = "127.0.0.1:8788"       # the worker API; put TLS or a VPN / SSH tunnel in front of it
    tokens_file: str = "/srv/factory/secrets/worker_tokens"   # one "name token" per line, mode 0600
    mode: str = "block"                  # block: a failing or missing check fails the run; warn: push anyway and say so
    lease_seconds: int = 120             # a claimed job needs a heartbeat at least this often
    claim_wait_seconds: int = 900        # a job nobody claims within this long fails
    max_wait_seconds: int = 3600         # the orchestrator never waits longer than this for one check
    max_attempts: int = 2                # claims per job (a lost lease puts it back in the queue)
    fix_rounds: int = 1                  # extra agent attempts, with the failing log, when a required check FAILS (0 = report only)
    checks: tuple[WorkerCheck, ...] = ()


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
    auto_chain: bool = True              # after an auto stage, continue to the next one when nothing needs a person
    auto_confirm_stages: bool = False    # True: also ask a person before auto runs a read-only stage (analyst, designer, architect)
    ci: CiCfg = field(default_factory=CiCfg)
    conflicts: ConflictsCfg = field(default_factory=ConflictsCfg)
    review: ReviewCfg = field(default_factory=ReviewCfg)
    mockups: MockupsCfg = field(default_factory=MockupsCfg)
    updates: UpdatesCfg = field(default_factory=UpdatesCfg)
    pm: PmCfg = field(default_factory=PmCfg)
    screens: ScreensCfg = field(default_factory=ScreensCfg)
    workers: WorkersCfg = field(default_factory=WorkersCfg)
    subtasks: SubtasksCfg = field(default_factory=SubtasksCfg)
    health: HealthCfg = field(default_factory=HealthCfg)
    schedules: tuple[ScheduleCfg, ...] = ()
    scanner: ScannerCfg = field(default_factory=ScannerCfg)
    prompts: PromptsCfg = field(default_factory=PromptsCfg)
    harnesses: dict = field(default_factory=dict)      # name -> HarnessCfg (claude-code is always available)
    telegram_verbosity: str = "normal"          # quiet | normal | verbose
    telegram_events: tuple[str, ...] | None = None   # explicit allow-list; overrides verbosity
    telegram_ui_url: str | None = None          # the admin UI's address, for an "Open in UI" button on open-question messages
    slack_bot_token_file: str | None = None     # xoxb- token: posts messages
    slack_app_token_file: str | None = None     # xapp- token: opens the Socket Mode connection that carries button clicks and /factory
    slack_channel: str | None = None            # channel or DM id (C..., G..., D...) alerts go to and buttons are accepted from
    slack_user_id: str | None = None            # the only Slack user (U... / W...) the factory obeys
    slack_verbosity: str = "normal"
    slack_events: tuple[str, ...] | None = None
    slack_ui_url: str | None = None
    github_poll_seconds: int = 0                # how often GitHub is read (0: every poll); local tickets are handled every poll
    github_issues_enabled: bool = True          # GitHub issues are read and acted on (absent: on so old configs keep working; the example sets false)
    local_enabled: bool = False                # the local ticket tracker and the GitHub import
    attach_max_mb: int = 5                      # local ticket attachments: the largest file,
    attach_max_files: int = 5                   # the most files on one ticket,
    attach_max_total_mb: int = 20               # and the most megabytes on one ticket
    kind_aliases: dict = field(default_factory=lambda: dict(KIND_ALIASES))   # label -> kind (bug/feature/docs/chore/question)


def default_harnesses(rn: "RunnerCfg") -> dict:
    secrets = Path(rn.claude_env_file).parent
    return {
        "claude-code": HarnessCfg("claude-code", rn.image, rn.claude_env_file, CLAUDE_COMMAND, ("api.anthropic.com",), True, False,
                                  "", "Claude Code. The credential is managed on the Credentials page.", usage_format="claude-json"),
        "codex": HarnessCfg("codex", "localhost/factory-agent-codex:latest", str(secrets / "codex.env"), CODEX_COMMAND, ("api.openai.com",),
                            False, True, "CODEX_API_KEY", "OpenAI Codex CLI (codex exec). Build the image from sandbox/codex/. Not yet verified end to end."),
        "gemini": HarnessCfg("gemini", "localhost/factory-agent-gemini:latest", str(secrets / "gemini.env"), GEMINI_COMMAND,
                             ("generativelanguage.googleapis.com",), False, True, "GEMINI_API_KEY",
                             "Google Gemini CLI. Build the image from sandbox/gemini/. Needs an API key. Not yet verified end to end."),
        "opencode-openrouter": HarnessCfg(
            "opencode-openrouter", OPENCODE_IMAGE, str(secrets / "opencode-openrouter.env"), OPENCODE_COMMAND, ("openrouter.ai",),
            False, True, "OPENROUTER_API_KEY", "OpenCode CLI via OpenRouter (Qwen and others). Build the image from sandbox/opencode/. Not yet verified end to end.",
            OPENCODE_NOTICE.format("OpenRouter"), "openrouter/<vendor>/<model>, e.g. openrouter/qwen/qwen3-coder"),
        "opencode-zen": HarnessCfg(
            "opencode-zen", OPENCODE_IMAGE, str(secrets / "opencode-zen.env"), OPENCODE_COMMAND, ("opencode.ai",),
            False, True, "OPENCODE_API_KEY", "OpenCode CLI via OpenCode Zen (Big Pickle). Build the image from sandbox/opencode/. Needs an API key. Not yet verified end to end.",
            OPENCODE_NOTICE.format("OpenCode Zen"), "opencode/<model>, e.g. opencode/big-pickle"),
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
    if not isinstance(r.max_parallel, int) or isinstance(r.max_parallel, bool) or not 1 <= r.max_parallel <= 8:
        raise ValueError(f"runner.max_parallel must be a whole number from 1 to 8, got {r.max_parallel!r}")
    return r


def resource_warning(rn: "RunnerCfg") -> str | None:
    """A warning when max_parallel sandboxes at their limits need more memory or CPUs than this host reports, else None.
    Only what the host reports is compared: if it cannot be read, nothing is guessed and no warning is given."""
    out = []
    m = re.fullmatch(r"(\d+)([kKmMgG])", rn.memory.strip())
    try:
        host = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        host = None
    if m and host and host > 0:
        need = int(m.group(1)) * 1024 ** "kmg".index(m.group(2).lower()) * 1024 * rn.max_parallel
        if need > host:
            out.append(f"{rn.max_parallel} x {rn.memory} of sandbox memory is more than the host's {host / 1024 ** 3:.1f} GB")
    cpus = os.cpu_count()
    try:
        want = float(rn.cpus) * rn.max_parallel
    except ValueError:
        want = None
    if cpus and want and want > cpus:
        out.append(f"{rn.max_parallel} x {rn.cpus} sandbox CPUs is more than the host's {cpus}")
    return ("runner.max_parallel: " + "; ".join(out) + ". Lower max_parallel or the per-sandbox limits.") if out else None


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
        if h.usage_format not in ("", "claude-json"):
            raise ValueError(f"harnesses.{h.name}.usage_format must be '' or 'claude-json', got {h.usage_format!r}")
    return out


def _check_models(routes: dict, roles: tuple, review: "ReviewCfg | None" = None, pm: "PmCfg | None" = None) -> None:
    models = [(f"routing.{k}.model", r.model) for k, r in routes.items()] + [(f"roles.{r.name}.model", r.model) for r in roles]
    if review is not None:
        models.append(("review.model", review.model))
    if pm is not None:
        models.append(("pm.model", pm.model))
    for where, m in models:
        if not isinstance(m, str) or not MODEL_RE.fullmatch(m):
            raise ValueError(f"{where} is not a valid model id (letters, digits and . _ : / @ + [ ] - only, up to 200 characters)")


MAX_FALLBACKS = 3


def _tuples(d: dict) -> dict:
    return {k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items()}


def _check_fallbacks(routes: dict, roles: tuple, review: "ReviewCfg | None" = None) -> None:
    items = [(f"routing.{k}", r) for k, r in routes.items()] + [(f"roles.{r.name}", r) for r in roles]
    if review is not None:
        items.append(("review", review))
    for where, r in items:
        fb, key = r.fallback_models, f"{where}.fallback_models"
        if not isinstance(fb, tuple) or not all(isinstance(m, str) and MODEL_RE.fullmatch(m) for m in fb):
            raise ValueError(f"{key} must be a list of valid model ids")
        if len(fb) > MAX_FALLBACKS:
            raise ValueError(f"{key} may hold at most {MAX_FALLBACKS} models")
        if len(set(fb)) != len(fb) or r.model in fb:
            raise ValueError(f"{key} must not repeat a model or include the primary model")


def chain(route) -> list:
    """The primary route followed by its fallbacks (same harness and effort). Admin configuration only."""
    return [route] + [dataclasses.replace(route, model=m, fallback_models=()) for m in route.fallback_models]


def _check_harness_use(routes: dict, roles: tuple, harnesses: dict, review: "ReviewCfg | None" = None,
                       pm: "PmCfg | None" = None) -> None:
    uses = [(f"routing.{k}", r.harness) for k, r in routes.items()] + [(f"roles.{r.name}", r.harness) for r in roles]
    if review is not None and review.enabled:
        uses.append(("review", review.harness))
    if pm is not None and pm.enabled:
        uses.append(("pm", pm.harness))
    for where, harness in uses:
        if harness not in harnesses or not harnesses[harness].enabled:
            raise ValueError(f"{where} uses the harness {harness!r}, which does not exist or is not enabled")


def _verbosity(v: str, section: str = "telegram") -> str:
    from .events import LEVELS
    if v not in LEVELS:
        raise ValueError(f"{section}.verbosity must be one of {sorted(LEVELS)}, got {v!r}")
    return v


def _events(v, section: str = "telegram"):
    from .events import ALL_EVENTS
    if v is None or v == "level":
        return None
    unknown = set(v) - ALL_EVENTS
    if unknown:
        raise ValueError(f"unknown {section} events: {sorted(unknown)}")
    return tuple(v)


def _slack_secret(raw: dict, key: str, github_token_file, name: str):
    """Where a Slack token is kept: as configured, else next to the GitHub token, so an install made before Slack existed can
    still save the tokens from the UI (Slack stays off until the channel and user are set)."""
    v = raw.get("slack", {}).get(key)
    if v or not github_token_file:
        return v
    return str(Path(github_token_file).parent / name)


def _slack_id(v, kinds: str, what: str):
    """A Slack id: an upper-case letter from `kinds`, then letters and digits (U0123ABCD, C0123ABCD). None/'' means unset."""
    if not v:
        return None
    if not isinstance(v, str) or not re.fullmatch(rf"[{kinds}][A-Z0-9]{{6,20}}", v):
        raise ValueError(f"slack.{what} must be a Slack id such as {'U' if what == 'user_id' else 'C'}0123ABCDEF, got {v!r}")
    return v


def _github_poll(gh: dict, default: int) -> int:
    if "poll_seconds" not in gh:
        return default
    v = gh["poll_seconds"]
    if isinstance(v, bool) or not isinstance(v, int) or not 5 <= v <= 86400:
        raise ValueError("github.poll_seconds must be a whole number of seconds from 5 to 86400")
    return v


def _github_issues_enabled(gh: dict) -> bool:
    v = gh.get("issues_enabled", True)
    if not isinstance(v, bool):
        raise ValueError("github.issues_enabled must be true or false")
    return v


def _local_enabled(loc: dict) -> bool:
    v = loc.get("enabled", False)
    if not isinstance(v, bool):
        raise ValueError("local.enabled must be true or false")
    return v


def _attach_limit(loc: dict, key: str, default: int, hi: int) -> int:
    v = loc.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= hi:
        raise ValueError(f"local.{key} must be a whole number from 1 to {hi}")
    return v


def _ui_url(v, section: str = "telegram"):
    if not v:
        return None
    if not isinstance(v, str) or not re.fullmatch(r"https?://[^\s\"'<>|]{1,200}", v):
        raise ValueError(f"{section}.ui_url must be an http(s) URL")
    return v.rstrip("/")


_SCREEN_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
_SCREEN_PATH = re.compile(r"[A-Za-z0-9_./-]{1,200}")


def _screens(raw: dict, repos: list[str]) -> ScreensCfg:
    raw = dict(raw)
    pages_raw, vps_raw, caps_raw = raw.pop("pages", []), raw.pop("viewports", None), raw.pop("captures", [])
    if isinstance(vps_raw, list):         # the UI writes a list, so its overrides replace config.toml's viewports instead of merging into them
        vps_raw = {v["name"]: {"width": v["width"], "height": v["height"]} for v in vps_raw}
    vps = ScreensCfg().viewports if vps_raw is None else tuple(Viewport(str(k), v["width"], v["height"]) for k, v in vps_raw.items())
    pages = tuple(ScreenPage(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in p.items()}) for p in pages_raw)
    caps = tuple(ScreenCapture(**c) for c in caps_raw)
    c = ScreensCfg(**{**raw, "viewports": vps, "pages": pages, "captures": caps})
    from .designfiles import valid_dir
    from .render import MAX_H, MAX_W
    if not valid_dir(c.baseline_dir):
        raise ValueError("screens.baseline_dir must be a relative folder such as screens/baselines")
    if not isinstance(c.timeout_seconds, int) or isinstance(c.timeout_seconds, bool) or not 10 <= c.timeout_seconds <= 3600:
        raise ValueError("screens.timeout_seconds must be a whole number from 10 to 3600")
    for n in ("threshold", "max_diff_ratio"):
        v = getattr(c, n)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f"screens.{n} must be a number from 0 to 1")
    if not c.image.strip() or not c.label.strip():
        raise ValueError("screens.image and screens.label must not be empty")
    names = [v.name for v in c.viewports]
    if len(set(names)) != len(names):
        raise ValueError("screens.viewports names must be unique")
    if "design" in names:
        raise ValueError("screens.viewports: the name 'design' is reserved for the design canvases on the Screens board")
    from .schedules import parse_every
    if not isinstance(c.board_every, str) or (c.board_every and parse_every(c.board_every) is None):
        raise ValueError('screens.board_every must be like "6h", "1d" or "1w", or empty')
    for v in c.viewports:
        if not _SCREEN_NAME.fullmatch(v.name):
            raise ValueError("screens.viewports names must match [a-z0-9-]")
        for dim, top in ((v.width, MAX_W), (v.height, MAX_H)):
            if not isinstance(dim, int) or isinstance(dim, bool) or not 16 <= dim <= top:
                raise ValueError(f"screens.viewports.{v.name}: width and height must be whole numbers from 16 to {top}")
    taken = set()
    for k in c.captures:
        if k.repo not in repos or k.repo in taken:
            raise ValueError(f"screens.captures: {k.repo} must be a configured repo, listed once")
        taken.add(k.repo)
        if not isinstance(k.recipe, str) or not _SCREEN_NAME.fullmatch(k.recipe) or not isinstance(k.platform, str) or not _SCREEN_NAME.fullmatch(k.platform):
            raise ValueError(f"screens.captures.{k.repo}: recipe and platform must match [a-z0-9-]")
    seen = set()
    for p in c.pages:
        if p.repo not in repos:
            raise ValueError(f"screens.pages: {p.repo} is not a configured repo")
        if not _SCREEN_NAME.fullmatch(p.name) or (p.repo, p.name) in seen:
            raise ValueError(f"screens.pages: {p.repo} name {p.name!r} must be unique and match [a-z0-9-]")
        seen.add((p.repo, p.name))
        parts = p.path.split("/")
        if (not _SCREEN_PATH.fullmatch(p.path) or p.path.startswith("/") or ".." in parts or "" in parts
                or any(x.lower() == ".git" for x in parts)):
            raise ValueError(f"screens.pages.{p.name}: path must be a plain relative path inside the repo")
        if not set(p.viewports) <= set(names):
            raise ValueError(f"screens.pages.{p.name}: unknown viewport")
        if (len(p.mask) > 20 or any(not isinstance(m, str) or not 0 < len(m) <= 200 for m in p.mask)
                or not isinstance(p.wait_for, str) or len(p.wait_for) > 200):
            raise ValueError(f"screens.pages.{p.name}: mask and wait_for must be short CSS selectors")
        if not isinstance(p.journey, str) or (p.journey and not _SCREEN_NAME.fullmatch(p.journey)):
            raise ValueError(f"screens.pages.{p.name}: journey must match [a-z0-9-]")
        if not isinstance(p.step, int) or isinstance(p.step, bool) or not 0 <= p.step <= 999:
            raise ValueError(f"screens.pages.{p.name}: step must be a whole number from 0 to 999")
        dparts = p.design.split("/") if isinstance(p.design, str) else [""]
        if p.design and (not isinstance(p.design, str) or not _SCREEN_PATH.fullmatch(p.design) or not p.design.endswith(".dc.html")
                         or p.design.startswith("/") or ".." in dparts or "" in dparts or any(x.lower() == ".git" for x in dparts)):
            raise ValueError(f"screens.pages.{p.name}: design must be a relative path to a .dc.html file inside the repo")
    return c


WORKER_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


def _workers(raw: dict, repos: list[str]) -> WorkersCfg:
    raw = dict(raw)
    checks = tuple(WorkerCheck(**c) for c in raw.pop("checks", []))
    c = WorkersCfg(**{**raw, "checks": checks})
    if c.mode not in ("block", "warn"):
        raise ValueError("workers.mode must be block or warn")
    host, _, port = c.listen.rpartition(":")
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("workers.listen must look like 127.0.0.1:8788")
    for n, lo, hi in (("lease_seconds", 10, 3600), ("claim_wait_seconds", 10, 86400), ("max_wait_seconds", 30, 86400), ("max_attempts", 1, 5), ("fix_rounds", 0, 3)):
        v = getattr(c, n)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ValueError(f"workers.{n} must be a whole number from {lo} to {hi}")
    if not isinstance(c.tokens_file, str) or not c.tokens_file:
        raise ValueError("workers.tokens_file must not be empty")
    seen = set()
    for k in c.checks:
        if k.repo not in repos:
            raise ValueError(f"workers.checks: {k.repo} is not a configured repo")
        if not WORKER_NAME.fullmatch(str(k.recipe)) or not WORKER_NAME.fullmatch(str(k.platform)):
            raise ValueError("workers.checks: recipe and platform must match [a-z0-9-]")
        if not isinstance(k.required, bool):
            raise ValueError("workers.checks: required must be true or false")
        if (k.repo, k.recipe) in seen:
            raise ValueError(f"workers.checks: {k.repo} lists recipe {k.recipe!r} twice")
        seen.add((k.repo, k.recipe))
    return c


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


def _schedules(raw: list, repos: list[str]) -> tuple[ScheduleCfg, ...]:
    from . import schedules as sc
    out, seen = [], set()
    for r in raw:
        r = _tuples(dict(r))
        s = ScheduleCfg(**r)
        w = f"schedules.{s.name!r}"
        if not isinstance(s.name, str) or not sc.NAME.fullmatch(s.name) or s.name in seen:
            raise ValueError(f"{w}: name must be unique lowercase letters, digits and dashes")
        seen.add(s.name)
        if s.repo not in repos:
            raise ValueError(f"{w}: repo {s.repo} is not one of the configured repos")
        if (s.every is None) == (s.cron is None):
            raise ValueError(f"{w}: set exactly one of every and cron")
        if s.every is not None and (sc.parse_every(s.every) is None or sc.parse_every(s.every) < 300):
            raise ValueError(f"{w}: every must look like 30m, 6h, 2d or 1w and be at least 5m")
        if s.cron is not None and sc.parse_cron(s.cron) is None:
            raise ValueError(f"{w}: cron must be five fields (minute hour day month weekday), UTC")
        if (why := sc.source_problem(s.source)):
            raise ValueError(f"{w}: {why}")
        if s.labels is not None and not all(isinstance(l, str) and l.strip() for l in s.labels):
            raise ValueError(f"{w}: labels must be non-empty strings")
        for k in ("keep", "max_data_chars"):
            if not isinstance(getattr(s, k), int) or isinstance(getattr(s, k), bool) or getattr(s, k) < 0:
                raise ValueError(f"{w}: {k} must be a whole number")
        if not s.title.strip():
            raise ValueError(f"{w}: title must not be empty")
        out.append(s)
    return tuple(out)


def _scanner(raw: dict, repos: list[str], workers: "WorkersCfg") -> ScannerCfg:
    import re
    from . import jobs, scanner as sn, schedules as sc
    raw = dict(raw)
    smells = tuple(SmellCfg(**_tuples(dict(s))) for s in raw.pop("smells", []))
    scans = tuple(ScanCfg(**_tuples(dict(s))) for s in raw.pop("scans", []))
    s = ScannerCfg(**{**_tuples(raw), "smells": smells, "scans": scans})
    ids: set[str] = set(sn.PRESETS)
    for m in s.smells:
        w = f"scanner.smells.{m.id!r}"
        if not isinstance(m.id, str) or not sc.NAME.fullmatch(m.id) or m.id in ids:
            raise ValueError(f"{w}: id must be unique, not a preset id, and use lowercase letters, digits and dashes")
        ids.add(m.id)
        if not isinstance(m.name, str) or not m.name.strip() or len(m.name) > 80:
            raise ValueError(f"{w}: name is required (at most 80 characters)")
        if not isinstance(m.pattern, str) or not m.pattern or len(m.pattern) > 500:
            raise ValueError(f"{w}: pattern is required (a regular expression, at most 500 characters)")
        try:
            if re.compile(m.pattern).search(""):
                raise ValueError(f"{w}: pattern must not match an empty line")
        except re.error as e:
            raise ValueError(f"{w}: pattern is not a valid regular expression: {e}")
        if not m.globs or not all(isinstance(g, str) and g and not g.startswith("/") and ".." not in g.split("/") for g in m.globs):
            raise ValueError(f"{w}: globs must be relative patterns without ..")
    seen: set[str] = set()
    for c in s.scans:
        w = f"scanner.scans.{c.name!r}"
        if not isinstance(c.name, str) or not sc.NAME.fullmatch(c.name) or c.name in seen:
            raise ValueError(f"{w}: name must be unique lowercase letters, digits and dashes")
        seen.add(c.name)
        if c.repo not in repos:
            raise ValueError(f"{w}: repo {c.repo} is not one of the configured repos")
        if (c.every is None) == (c.cron is None):
            raise ValueError(f"{w}: set exactly one of every and cron")
        if c.every is not None and (sc.parse_every(c.every) is None or sc.parse_every(c.every) < 3600):
            raise ValueError(f"{w}: every must look like 6h, 2d or 1w and be at least 1h")
        if c.cron is not None and sc.parse_cron(c.cron) is None:
            raise ValueError(f"{w}: cron must be five fields (minute hour day month weekday), UTC")
        if not c.smells or (bad := [x for x in c.smells if x not in ids]):
            raise ValueError(f"{w}: smells must name at least one known smell" + (f" (unknown: {', '.join(map(str, bad))})" if c.smells else ""))
        if not all(isinstance(g, str) and g and not g.startswith("/") and ".." not in g.split("/") for g in c.exclude):
            raise ValueError(f"{w}: exclude must be relative patterns without ..")
    if s.labels is not None and not all(isinstance(l, str) and l.strip() for l in s.labels):
        raise ValueError("scanner.labels must be non-empty strings")
    if not jobs.NAME.fullmatch(str(s.recipe)) or not re.fullmatch(r"[a-z0-9-]{1,40}", str(s.platform)):
        raise ValueError("scanner.recipe and scanner.platform must match [a-z0-9-]")
    for k, lo in (("max_tickets", 1), ("max_findings_per_ticket", 1), ("max_lines", 50)):
        v = getattr(s, k)
        if not isinstance(v, int) or isinstance(v, bool) or v < lo:
            raise ValueError(f"scanner.{k} must be a whole number of at least {lo}")
    if s.enabled and not workers.enabled:
        raise ValueError("scanner.enabled needs [workers] enabled: scans read the code on a worker")
    return s


def parse(raw: dict) -> Config:
    g, gh = raw["general"], raw["github"]
    routes = {k: Route(**_tuples(v)) for k, v in raw["routing"].items()}
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
    roles = tuple(dataclasses.replace(r, **_tuples(raw.get("roles", {}).get(r.name, {}))) for r in DEFAULT_ROLES)
    from .designfiles import valid_dir
    for r in roles:
        if not valid_dir(r.design_dir):
            raise ValueError(f"roles.{r.name}.design_dir must be a relative folder such as docs/design")
    conflicts = ConflictsCfg(**raw.get("conflicts", {}))
    if conflicts.max_attempts < 0 or not conflicts.label.strip():
        raise ValueError("conflicts.max_attempts must be 0 or more and conflicts.label must not be empty")
    review = ReviewCfg(**_tuples(raw.get("review", {})))
    if review.effort not in ("low", "medium", "high"):
        raise ValueError("review.effort must be low, medium or high")
    mockups = MockupsCfg(**raw.get("mockups", {}))
    if (mockups.mode not in ("block", "warn", "off") or not mockups.bypass_label.strip() or not mockups.approve_label.strip()
            or not mockups.request_label.strip()):
        raise ValueError("mockups.mode must be block, warn or off, and mockups.bypass_label, mockups.approve_label and "
                         "mockups.request_label must not be empty")
    updates = UpdatesCfg(**raw.get("updates", {}))
    if not isinstance(updates.repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", updates.repo):
        raise ValueError("updates.repo must look like owner/name")
    health = HealthCfg(**_tuples(raw.get("health", {})))
    for name in ("disk_warn_percent", "disk_crit_percent", "disk_warn_gb", "disk_crit_gb", "mem_warn_mb", "mem_crit_mb",
                 "stale_poll_minutes", "failed_runs_warn", "github_rate_warn", "repeat_hours"):
        v = getattr(health, name)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 100000:
            raise ValueError(f"health.{name} must be a whole number from 1 to 100000")
    if health.disk_crit_percent > health.disk_warn_percent or health.disk_crit_gb > health.disk_warn_gb \
            or health.mem_crit_mb > health.mem_warn_mb:
        raise ValueError("health: each crit threshold must not exceed its warn threshold")
    if health.heartbeat_url and not health.heartbeat_url.startswith("https://"):
        raise ValueError("health.heartbeat_url must be an https:// address")
    pm = PmCfg(**raw.get("pm", {}))
    if pm.effort not in ("low", "medium", "high"):
        raise ValueError("pm.effort must be low, medium or high")
    for name in ("interval_minutes", "max_tickets", "body_chars"):
        v = getattr(pm, name)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 100000:
            raise ValueError(f"pm.{name} must be a whole number from 1 to 100000")
    if not isinstance(pm.unblock_label, str) or not pm.unblock_label.strip():
        raise ValueError("pm.unblock_label must not be empty")
    screens = _screens(raw.get("screens", {}), repos)
    workers = _workers(raw.get("workers", {}), repos)
    _check_models(routes, roles, review, pm)
    _check_fallbacks(routes, roles, review)
    _check_harness_use(routes, roles, harnesses, review, pm)
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
        auto_confirm_stages=bool(raw.get("auto", {}).get("confirm_stages", False)),
        auto_chain=bool(raw.get("auto", {}).get("chain", True)),
        ci=CiCfg(**raw.get("ci", {})),
        screens=screens,
        workers=workers,
        conflicts=conflicts,
        review=review,
        mockups=mockups,
        updates=updates,
        pm=pm,
        health=health,
        subtasks=SubtasksCfg(**raw.get("subtasks", {})),
        schedules=_schedules(raw.get("schedules", []), repos),
        scanner=_scanner(raw.get("scanner", {}), repos, workers),
        prompts=_prompts(raw.get("prompts", {})),
        harnesses=harnesses,
        telegram_verbosity=_verbosity(raw.get("telegram", {}).get("verbosity", "normal")),
        telegram_events=_events(raw.get("telegram", {}).get("events")),
        telegram_ui_url=_ui_url(raw.get("telegram", {}).get("ui_url")),
        slack_bot_token_file=_slack_secret(raw, "bot_token_file", gh.get("token_file"), "slack_bot_token"),
        slack_app_token_file=_slack_secret(raw, "app_token_file", gh.get("token_file"), "slack_app_token"),
        slack_channel=_slack_id(raw.get("slack", {}).get("channel"), "CGD", "channel"),
        slack_user_id=_slack_id(raw.get("slack", {}).get("user_id"), "UW", "user_id"),
        slack_verbosity=_verbosity(raw.get("slack", {}).get("verbosity", "normal"), "slack"),
        slack_events=_events(raw.get("slack", {}).get("events"), "slack"),
        slack_ui_url=_ui_url(raw.get("slack", {}).get("ui_url"), "slack"),
        github_poll_seconds=_github_poll(gh, int(g["poll_seconds"])),
        github_issues_enabled=_github_issues_enabled(gh),
        local_enabled=_local_enabled(raw.get("local", {})),
        attach_max_mb=_attach_limit(raw.get("local", {}), "max_attachment_mb", 5, 50),
        attach_max_files=_attach_limit(raw.get("local", {}), "max_attachments", 5, 20),
        attach_max_total_mb=_attach_limit(raw.get("local", {}), "max_attachments_total_mb", 20, 200),
        kind_aliases=_aliases(raw.get("classifier", {}).get("kind_aliases")),
    )
