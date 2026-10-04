"""Settings the UI can edit. Every save is validated by loading the merged configuration before anything is written,
and only the overrides file is touched (the hand-written config.toml and its comments are never rewritten)."""
import copy
import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from ..classifier import KIND_ALIASES, KINDS
from ..config import (DEFAULT_ROLES, MAX_FALLBACKS, MODEL_RE, PROMPT_MAX, CiCfg, ConflictsCfg, PmCfg, PromptsCfg, ReviewCfg, RunnerCfg, ScreensCfg, WorkersCfg, deep_merge,
                      default_harnesses, load_raw, overrides_path, parse, prompt_problem)
from ..events import ALL_EVENTS
from ..schedules import parse_every
from ..tomlw import dumps

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
HOST_RE = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
EFFORTS = ("low", "medium", "high")
CHECK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


class SettingsError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Field:
    key: str                 # dotted path in the config
    label: str
    kind: str                # int float bool text select checks repos hosts kv models prompt wchecks every viewports
    help: str = ""
    choices: tuple = ()
    lo: float | None = None
    hi: float | None = None
    danger: str = ""         # non-empty: changing it requires an explicit confirmation

FALLBACK_HELP = ("Tried in order, on the same harness and effort, when the model hits a usage limit or an API error. "
                 "One model id per line, up to 3; empty for none.")


def _role_fields(harnesses: tuple = ("claude-code",)) -> list[Field]:
    out = []
    for r in DEFAULT_ROLES:
        out += [Field(f"roles.{r.name}.label", f"{r.name.title()}: trigger label", "text"),
                Field(f"roles.{r.name}.done_label", f"{r.name.title()}: done label", "text"),
                Field(f"roles.{r.name}.model", f"{r.name.title()}: model", "text", "sonnet, opus, haiku (or a full model id)"),
                Field(f"roles.{r.name}.effort", f"{r.name.title()}: effort", "select", choices=EFFORTS),
                Field(f"roles.{r.name}.harness", f"{r.name.title()}: agent harness", "select", choices=harnesses),
                Field(f"roles.{r.name}.fallback_models", f"{r.name.title()}: fallback models", "models", FALLBACK_HELP)]
        if r.name == "designer":
            out.append(Field("roles.designer.design_files", "Designer: write design mockup files", "bool",
                             "In a repo that has Claude Design canvases (*.dc.html), add static mockups on a draft PR and link them from the ticket."))
            out.append(Field("roles.designer.design_dir", "Designer: mockup folder", "text",
                             "Relative folder in the repo, for example docs/design. Avoid design/ if your repo git-ignores it."))
    return out + [Field("auto.label", "Auto-allocate label", "text", "A person applies it and the classifier picks the next stage."),
                  Field("auto.chain", "Auto continues to the next stage", "bool",
                        "On (default): after a stage, if nothing needs a person, the next stage starts by itself. Off: one stage per label."),
                  Field("auto.confirm_stages", "Ask before auto runs a stage", "bool",
                        "Off (default): analysis, design and architecture run straight away, since they only write a document. A person is "
                        "always asked before a build the classifier is unsure about. On: ask for stages too.")]


def _routing_fields(harnesses: tuple = ("claude-code",)) -> list[Field]:
    out = []
    for tier in ("low", "medium", "high"):
        out += [Field(f"routing.{tier}.harness", f"{tier.title()} tier: agent harness", "select", choices=harnesses),
                Field(f"routing.{tier}.model", f"{tier.title()} tier: model", "text"),
                Field(f"routing.{tier}.effort", f"{tier.title()} tier: effort", "select", choices=EFFORTS),
                Field(f"routing.{tier}.fallback_models", f"{tier.title()} tier: fallback models", "models", FALLBACK_HELP)]
    return out


PROMPT_DANGER = "Agents follow this text on every run, with write access to their sandbox workspace."
PROMPT_LABELS = {"all": "All agents", "analyst": "Analyst", "designer": "Designer", "architect": "Architect", "reviewer": "Code reviewer",
                 "implementer": "Implementer (builds)", "ci_fix": "CI fix runs", "conflicts": "Merge-conflict runs"}


def _prompt_fields() -> list[Field]:
    return [Field(f"prompts.{k}", label, "prompt",
                  "Added to every agent, before its own text below." if k == "all" else
                  "Put before this agent's built-in instructions, which always apply and win if they conflict.", danger=PROMPT_DANGER)
            for k, label in PROMPT_LABELS.items()]


SECTIONS: dict[str, tuple[str, list[Field]]] = {
    "general": ("General", [
        Field("general.poll_seconds", "Poll interval (seconds)", "int", "How often GitHub is checked.", lo=5, hi=3600),
        Field("general.dry_run", "Dry run", "bool", "On: decisions are only logged, nothing is run or written. Turn off to go live.",
              danger="Going live means the factory will start agents and open PRs for labeled issues."),
        Field("general.confidence_threshold", "Confidence threshold", "float", "Below this the classifier's call goes to a person.", lo=0, hi=1),
        Field("github.trigger_label", "Build label", "text", "Applying it makes the factory build the ticket."),
        Field("github.trusted_permissions", "Who may apply labels", "checks", "A label counts only if its author has one of these repository roles.",
              choices=("admin", "maintain", "write", "triage")),
        Field("github.repos", "Standalone repositories", "repos", "owner/name, one per line. Repos of a project (see Projects) are added automatically."),
        Field("github.issues_enabled", "Work from GitHub issues", "bool",
              "Off: GitHub issues are not read or acted on (no polling, approvals, schedules, sub-issues or Import). Running jobs finish. "
              "Pull requests and CI still use GitHub. Turn on local tickets or nothing has tickets to work on."),
        Field("local.enabled", "Keep tickets in the factory", "bool",
              "On: tickets can live in the factory's own database (L-1, L-2 ...) instead of GitHub issues. New ticket offers both, and Import "
              "moves GitHub issues across. Pull requests still go to GitHub."),
        Field("github.poll_seconds", "Read GitHub every (seconds)", "int",
              "Local tickets are handled at every poll; GitHub issues can be read less often to spare API calls. "
              "The same as the poll interval reads GitHub at every poll.", lo=5, hi=86400),
    ]),
    "routing": ("Routing", _routing_fields()),
    "roles": ("Role agents", _role_fields()),     # the harness choices are filled in by fields_for()
    "classifier": ("Classifier", [
        Field("classifier.backend", "Classifier", "select", "Jev reads the whole ticket; labels uses only your labels.", choices=("rules", "jev")),
        Field("classifier.model", "Jev model", "text", "typesafe/jev-1.13 pins a version; ~typesafe/jev-latest follows the newest."),
        Field("classifier.kind_aliases", "Label → kind aliases", "kv",
              "One per line, label=kind, where kind is bug, feature, docs, chore or question. Lets existing labels (like GitHub's enhancement) count."),
    ]),
    "runner": ("Agent runner", [
        Field("runner.max_parallel", "Agents at once", "int", "Sandboxes that may run at the same time. Each may use the memory and CPUs below.", lo=1, hi=8),
        Field("runner.timeout_seconds", "Task timeout (seconds)", "int", lo=60, hi=14400),
        Field("runner.max_turns", "Max agent turns", "int", lo=1, hi=200),
        Field("runner.rate_limit_backoff_seconds", "Pause after a rate limit (seconds)", "int", lo=60, hi=86400),
        Field("runner.memory", "Sandbox memory", "text", "For example 3g or 512m."),
        Field("runner.cpus", "Sandbox CPUs", "text", "For example 2 or 1.5."),
        Field("runner.allow_hosts", "Hosts the sandbox may reach", "hosts",
              "One per line. This is the ONLY way out of the sandbox. Keep it to the model API unless tasks truly need a registry.",
              danger="Widening the sandbox's reachable hosts weakens its isolation."),
    ]),
    "review": ("Code review", [
        Field("review.enabled", "Enable the code reviewer", "bool", "An independent agent reviews the factory's PRs and comments. It never approves or changes code."),
        Field("review.auto", "Review every PR the factory opens", "bool", "PRs open as drafts and are marked ready once the review is posted. Off: PRs open ready; only the review label starts a review."),
        Field("review.label", "Review label", "text", "Apply it to a ticket to (re-)review its open factory PRs."),
        Field("review.done_label", "Reviewed label", "text"),
        Field("review.model", "Reviewer model", "text", "Use a different harness and model family from the builder for a genuinely independent opinion."),
        Field("review.effort", "Reviewer effort", "select", choices=EFFORTS),
        Field("review.harness", "Reviewer agent harness", "select", choices=("claude-code",)),
        Field("review.fallback_models", "Reviewer: fallback models", "models", FALLBACK_HELP),
    ]),
    "pm": ("Project manager", [
        Field("pm.enabled", "Enable the project manager", "bool",
              "On a sweep an agent ranks each repository's factory tickets with priority labels and records blockers; while it is on, "
              "a build waits for its open blockers. A person's priority label always wins. It never starts or stops work."),
        Field("pm.interval_minutes", "Minutes between sweeps", "int", "Only when the tickets changed; each sweep is one run per repository.", lo=10, hi=10080),
        Field("pm.max_tickets", "Tickets per sweep", "int", lo=1, hi=200),
        Field("pm.body_chars", "Characters of each ticket body", "int", lo=200, hi=10000),
        Field("pm.unblock_label", "Unblock label", "text", "A person with write access applies it to make the factory ignore the project manager's blockers on a ticket."),
        Field("pm.model", "Project manager model", "text"),
        Field("pm.effort", "Project manager effort", "select", choices=EFFORTS),
        Field("pm.harness", "Project manager agent harness", "select", choices=("claude-code",)),
    ]),
    "mockups": ("Design mockups", [
        Field("mockups.mode", "When a design stage left no mockup", "select",
              "block: the build does not start. warn: build anyway and say so on the ticket. off: never check. Tickets whose design said "
              "there are no screens are never held.", choices=("block", "warn", "off")),
        Field("mockups.bypass_label", "Bypass label", "text", "Put it on one ticket to build without mockups."),
        Field("mockups.require_approval", "Mockups need a person's approval", "bool",
              "When on (and mode is block), a build waits until the approval label is on the ticket or the design PR is merged."),
        Field("mockups.approve_label", "Approval label", "text"),
    ]),
    "ci": ("CI feedback", [
        Field("ci.enabled", "Watch CI on factory PRs", "bool"),
        Field("ci.fix_rounds", "Agent fix rounds after a failure", "int", "0 = report only.", lo=0, hi=5),
        Field("ci.wait_for_checks_minutes", "Wait for checks to appear (minutes)", "int", lo=1, hi=120),
        Field("ci.timeout_minutes", "Give up on pending checks after (minutes)", "int", lo=5, hi=720),
        Field("ci.log_tail_chars", "Failing-log excerpt size", "int", lo=1000, hi=20000),
    ]),
    "conflicts": ("Merge conflicts", [
        Field("conflicts.enabled", "Resolve merge conflicts on factory PRs", "bool",
              "Checks every PR the factory opened. The base branch is merged into the PR branch (never rebased or force-pushed); an agent edits only the conflicted files."),
        Field("conflicts.auto", "Apply the label automatically", "bool", "Off: the factory only reports a conflict and a person applies the label."),
        Field("conflicts.label", "Conflicts label", "text", "Apply it to a ticket to resolve the conflicts in its open factory PRs."),
        Field("conflicts.max_attempts", "Resolution attempts per PR", "int", "Then a person is asked.", lo=0, hi=50),
    ]),
    "workers": ("Workers", [
        Field("workers.enabled", "Verify patches on workers", "bool",
              "On: for every repo with a check below, a patch must pass on a worker before anything is pushed. The worker API process and a worker must be running.",
              danger="Builds of repos with checks will wait for a worker, and fail without one."),
        Field("workers.mode", "When a required check fails or cannot run", "select",
              "block: the run fails and nothing is pushed. warn: the PR is opened anyway, with the failure noted on it.", choices=("block", "warn"),
              danger="Warn lets a change that failed its checks be pushed as a PR."),
        Field("workers.fix_rounds", "Agent fix rounds after a failed check", "int",
              "When a required check really FAILS (not when no worker could run it), the agent tries again with the log. 0 = report only.", lo=0, hi=3),
        Field("workers.claim_wait_seconds", "Give up if no worker claims a job (seconds)", "int", lo=10, hi=86400),
        Field("workers.max_wait_seconds", "Give up on a check after (seconds)", "int", lo=30, hi=86400),
        Field("workers.lease_seconds", "A worker must report in every (seconds)", "int", lo=10, hi=3600),
        Field("workers.checks", "Checks", "wchecks",
              "One per line: owner/name recipe platform, with an optional fourth word, advisory (a failure is noted on the PR but never blocks). "
              "The recipe is a name defined on the worker; platform is macos, linux ... or any.",
              danger="Removing or loosening a check lets changes through that would have been verified."),
    ]),
    "screens": ("Screens", [
        Field("screens.board_every", "Refresh the Screens board every", "every",
              "6h, 1d or 1w. Empty: only when someone presses Refresh now."),
        Field("screens.viewports", "Viewports", "viewports",
              "One per line: name widthxheight, for example desktop 1440x900. Every screen is shot at each one unless it picks some."),
        Field("screens.baseline_dir", "Baseline folder", "text", "Where each repository keeps its approved shots, as <name>-<viewport>.png."),
        Field("screens.threshold", "Pixel tolerance", "float", "Colour distance (0 to 1) above which a pixel counts as different.", lo=0, hi=1),
        Field("screens.max_diff_ratio", "Share of pixels allowed to differ", "float", "0.001 is 0.1 %.", lo=0, hi=1),
        Field("screens.timeout_seconds", "Shoot timeout (seconds)", "int", lo=10, hi=3600),
        Field("screens.label", "Screens-changed label", "text", "Put on a draft PR whose patch changes baseline images."),
    ]),
    "prompts": ("Agent prompts", _prompt_fields()),
}
VIEWPORT_LINE = re.compile(r"([a-z0-9][a-z0-9-]{0,60})\s+(\d{1,5})\s*[x×]\s*(\d{1,5})")


def harness_choices(eff: dict) -> tuple:
    """Names of the enabled harnesses, for the selectors on the routing and roles pages."""
    try:
        return tuple(n for n, h in parse(eff).harnesses.items() if h.enabled) or ("claude-code",)
    except (ValueError, KeyError, TypeError):
        return ("claude-code",)


def fields_for(section: str, eff: dict) -> list[Field]:
    if section == "routing":
        return _routing_fields(harness_choices(eff))
    if section == "roles":
        return _role_fields(harness_choices(eff))
    if section == "review":
        return [replace(f, choices=harness_choices(eff)) if f.key == "review.harness" else f for f in SECTIONS["review"][1]]
    if section == "pm":
        return [replace(f, choices=harness_choices(eff)) if f.key == "pm.harness" else f for f in SECTIONS["pm"][1]]
    return SECTIONS[section][1]


# ---------------------------------------------------------------- dotted-path helpers
def get_in(d: dict, dotted: str, default=None):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_in(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def del_in(d: dict, dotted: str) -> None:
    parts = dotted.split(".")
    chain = [d]
    for part in parts[:-1]:
        if part not in chain[-1]:
            return
        chain.append(chain[-1][part])
    chain[-1].pop(parts[-1], None)
    for i in range(len(parts) - 1, 0, -1):               # prune tables left empty
        if not chain[i]:
            chain[i - 1].pop(parts[i - 1], None)


def default_for(key: str):
    section, _, name = key.partition(".")
    if section == "harnesses":
        hname, _, field = name.partition(".")
        h = default_harnesses(RunnerCfg()).get(hname)
        v = getattr(h, field, None) if h else None
        return list(v) if isinstance(v, tuple) else v
    if section == "runner":
        v = getattr(RunnerCfg(), name, None)
        return list(v) if isinstance(v, tuple) else v
    if section == "ci":
        return getattr(CiCfg(), name, None)
    if section == "review":
        v = getattr(ReviewCfg(), name, None)
        return list(v) if isinstance(v, tuple) else v
    if section == "conflicts":
        return getattr(ConflictsCfg(), name, None)
    if section == "workers":
        v = getattr(WorkersCfg(), name, None)
        return [] if name == "checks" else v
    if section == "prompts":
        return getattr(PromptsCfg(), name, None)
    if section == "pm":
        return getattr(PmCfg(), name, None)
    if section == "screens":
        v = getattr(ScreensCfg(), name, None)
        return [{"name": x.name, "width": x.width, "height": x.height} for x in v] if name == "viewports" else v
    if section == "roles":
        role, _, field = name.partition(".")
        v = next((getattr(r, field) for r in DEFAULT_ROLES if r.name == role), None)
        return list(v) if isinstance(v, tuple) else v
    return {"auto.label": "factory:auto", "routing.low.fallback_models": [], "routing.medium.fallback_models": [], "routing.high.fallback_models": [],
            "classifier.backend": "rules", "classifier.model": "typesafe/jev-1.13",
            "classifier.kind_aliases": dict(KIND_ALIASES), "telegram.verbosity": "normal", "slack.verbosity": "normal", "local.enabled": False, "github.issues_enabled": True, "auto.confirm_stages": False, "auto.chain": True}.get(key)


def canon(key: str, v):
    """Viewports are a table in a hand-written config.toml and a list in the overrides; compare and show them as the list."""
    if key == "screens.viewports" and isinstance(v, dict):
        return [{"name": k, "width": x.get("width"), "height": x.get("height")} for k, x in v.items()]
    return v


def fallback(raw: dict, key: str):
    """A setting whose default is another setting: GitHub is read at every poll unless told otherwise."""
    return get_in(raw, "general.poll_seconds") if key == "github.poll_seconds" else None


def effective(raw: dict, key: str):
    v = canon(key, get_in(raw, key))
    if v is None:
        v = fallback(raw, key)
    return default_for(key) if v is None else v


# ---------------------------------------------------------------- reading and writing the files
def base_raw(cfg_path: str) -> dict:
    return tomllib.loads(Path(cfg_path).read_text())


def overrides_raw(cfg_path: str) -> dict:
    p = overrides_path(cfg_path)
    return tomllib.loads(p.read_text()) if p.exists() else {}


def write_overrides(cfg_path: str, data: dict) -> None:
    p = overrides_path(cfg_path)
    if p.exists():
        p.with_suffix(".toml.bak").write_text(p.read_text())     # one-step undo
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Written by the Shikumi UI. Safe to delete a key to fall back to config.toml.\n" + dumps(data))
    os.replace(tmp, p)


def request_restart(state_dir: Path) -> None:
    """The orchestrator re-executes itself at its next idle moment, applying every setting and credential."""
    (Path(state_dir) / "RESTART").write_text("")


# ---------------------------------------------------------------- parsing form values
class Form(dict):
    """Form values: the first value of each field, with getall() for repeated ones (checkboxes)."""
    lists: dict

    def getall(self, key: str) -> list[str]:
        return self.lists.get(key, [])


def _lines(text: str) -> list[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def parse_value(f: Field, form: Form):
    raw = form.get(f.key, "")
    try:
        if f.kind == "bool":
            return raw == "1"
        if f.kind == "int":
            v = int(raw.strip())
            if (f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi):
                raise ValueError
            return v
        if f.kind == "float":
            v = float(raw.strip())
            if (f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi):
                raise ValueError
            return v
        if f.kind == "text":
            v = raw.strip()
            if not v or len(v) > 200 or "\n" in v or "\r" in v:
                raise ValueError
            if f.key == "runner.memory" and not re.fullmatch(r"\d+[kKmMgG]", v):
                raise ValueError
            if f.key == "runner.cpus" and not re.fullmatch(r"\d+(\.\d+)?", v):
                raise ValueError
            return v
        if f.kind == "prompt":
            v = raw.replace("\r\n", "\n")       # may be empty; kept as typed so an unchanged value from config.toml compares equal
            if prompt_problem(v):
                raise ValueError
            return v
        if f.kind == "select":
            if raw not in f.choices:
                raise ValueError
            return raw
        if f.kind == "checks":
            vals = [v for v in form.getall(f.key) if v in f.choices]
            if not vals:
                raise ValueError
            return vals
        if f.kind == "repos":
            vals = _lines(raw)
            if len(vals) > 100 or not all(REPO_RE.match(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "hosts":
            vals = [v.lower() for v in _lines(raw)]
            if not vals or len(vals) > 50 or not all(HOST_RE.match(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "models":
            vals = _lines(raw)
            if len(vals) > MAX_FALLBACKS or len(set(vals)) != len(vals) or not all(MODEL_RE.fullmatch(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "wchecks":
            out, seen = [], set()
            for line in _lines(raw):
                parts = line.split()
                if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "advisory"):
                    raise ValueError
                repo, recipe, platform = parts[:3]
                if not REPO_RE.match(repo) or not CHECK_NAME.fullmatch(recipe) or not CHECK_NAME.fullmatch(platform) or (repo, recipe) in seen:
                    raise ValueError
                seen.add((repo, recipe))
                out.append({"repo": repo, "recipe": recipe, "platform": platform, **({"required": False} if len(parts) == 4 else {})})
            if len(out) > 50:
                raise ValueError
            return out
        if f.kind == "every":
            v = raw.strip()
            if v and (len(v) > 10 or parse_every(v) is None):
                raise ValueError
            return v
        if f.kind == "viewports":
            out, seen = [], set()
            for line in _lines(raw):
                m = VIEWPORT_LINE.fullmatch(line)
                if not m or m.group(1) in seen or m.group(1) == "design":
                    raise ValueError
                seen.add(m.group(1))
                out.append({"name": m.group(1), "width": int(m.group(2)), "height": int(m.group(3))})
            if not out or len(out) > 10:
                raise ValueError
            return out
        if f.kind == "kv":
            out = {}
            for line in _lines(raw):
                k, sep, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if not sep or not k or len(k) > 100 or v not in KINDS:
                    raise ValueError
                out[k] = v
            return out
    except (ValueError, AttributeError):
        pass
    hint = {"int": f"a whole number between {f.lo} and {f.hi}", "float": f"a number between {f.lo} and {f.hi}",
            "select": "one of " + ", ".join(f.choices), "checks": "at least one choice", "repos": "owner/name per line",
            "hosts": "valid host names, one per line, at least one", "kv": "label=kind per line, kind one of " + ", ".join(sorted(KINDS)),
            "every": "like 6h, 1d or 1w, or empty",
            "viewports": "name widthxheight per line, 1 to 10 of them, distinct names of lowercase letters, digits and dashes (not design)",
            "wchecks": "owner/name recipe platform [advisory] per line, names of lowercase letters, digits and dashes, no repeated repo and recipe",
            "models": f"up to {MAX_FALLBACKS} distinct model ids, one per line (may be empty)",
            "text": "a short single-line value",
            "prompt": f"at most {PROMPT_MAX} characters, with no control characters other than line breaks and tabs"}.get(f.kind, "a valid value")
    raise ValueError(f"{f.label}: must be {hint}")


def _commit(cfg_path: str, state_dir: Path, new_overrides: dict) -> None:
    """Validate the merged result exactly as the orchestrator will load it, then write and ask for a restart."""
    try:
        parse(deep_merge(base_raw(cfg_path), new_overrides))
    except (ValueError, KeyError, TypeError) as e:
        raise SettingsError([f"The configuration would be invalid: {e}"])
    write_overrides(cfg_path, new_overrides)
    request_restart(state_dir)


def _store(new_ov: dict, base: dict, key: str, value) -> None:
    """Keep an override only when it differs from what the file (or the built-in default) already gives."""
    current = canon(key, get_in(base, key))
    if current is None:
        current = fallback(base, key)
    if current is None:
        current = default_for(key)
    if current == value or (isinstance(current, tuple) and list(current) == value):
        del_in(new_ov, key)
    else:
        set_in(new_ov, key, value)


def save_section(cfg_path: str, state_dir: Path, section: str, form: Form) -> list[str]:
    """Returns human-readable notes (for example 'switched to live'). Raises SettingsError."""
    if section not in SECTIONS:
        raise SettingsError(["Unknown settings section."])
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    eff = deep_merge(base, ov)
    fields = fields_for(section, eff)
    values, errors = {}, []
    for f in fields:
        try:
            values[f.key] = parse_value(f, form)
        except ValueError as e:
            errors.append(str(e))
    for f in fields:
        if f.danger and f.key in values and values[f.key] != effective(eff, f.key):
            # Dry run turning OFF is the dangerous direction; for other fields any change needs confirming.
            risky = (f.key != "general.dry_run") or values[f.key] is False
            if risky and form.get("confirm__" + f.key) != "1":
                errors.append(f"{f.label}: tick the confirmation box. {f.danger}")
    if errors:
        raise SettingsError(errors)
    new_ov, notes = copy.deepcopy(ov), []
    for key, value in values.items():
        if value != effective(eff, key):
            notes.append(key)
        _store(new_ov, base, key, value)
    if get_in(base, "github.poll_seconds") is None and "github.poll_seconds" in values and values["github.poll_seconds"] == values.get("general.poll_seconds"):
        del_in(new_ov, "github.poll_seconds")          # equal to the poll interval: keep following it
    _commit(cfg_path, state_dir, new_ov)
    return notes


def set_dry_run(cfg_path: str, state_dir: Path, dry: bool) -> bool:
    """Switch between dry run and live (the Floor page's switch). Returns True when the value changed. The factory applies it at its next idle moment."""
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    if bool(effective(deep_merge(base, ov), "general.dry_run")) == dry:
        return False
    new_ov = copy.deepcopy(ov)
    _store(new_ov, base, "general.dry_run", dry)
    _commit(cfg_path, state_dir, new_ov)
    return True


def set_switch(cfg_path: str, state_dir: Path, key: str, on: bool) -> bool:
    """Turn one on/off feature (features.SWITCHES) on or off from the screen it shapes. Returns True when the value changed."""
    from .features import SWITCHES
    if key not in SWITCHES:
        raise SettingsError(["That setting cannot be switched here."])
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    if bool(effective(deep_merge(base, ov), key)) == on:
        return False
    new_ov = copy.deepcopy(ov)
    _store(new_ov, base, key, on)
    _commit(cfg_path, state_dir, new_ov)
    return True


def set_parallel(cfg_path: str, state_dir: Path, value: int) -> None:
    """The Factory's agents-at-once stepper."""
    if not 1 <= value <= 8:
        raise SettingsError(["Agents at once must be from 1 to 8."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "runner.max_parallel", value)
    _commit(cfg_path, state_dir, new_ov)


def save_projects(cfg_path: str, state_dir: Path, form: Form) -> None:
    projects = []
    for i in range(20):
        name = (form.get(f"p{i}_name") or "").strip()
        repos = []
        for j in range(30):
            repo = (form.get(f"p{i}_r{j}_repo") or "").strip()
            role = (form.get(f"p{i}_r{j}_role") or "").strip()
            if repo:
                repos.append({"repo": repo, "role": role})
        if not name and not repos:
            continue
        if not name or len(name) > 60 or not re.fullmatch(r"[\w .-]+", name):
            raise SettingsError([f"Project {i + 1}: give it a short name (letters, digits, spaces, dot, dash)."])
        bad = [r["repo"] for r in repos if not REPO_RE.match(r["repo"])] + [r["repo"] for r in repos if len(r["role"]) > 300]
        if bad:
            raise SettingsError([f"Project {name}: invalid repository or role too long: {', '.join(bad[:3])}"])
        if not repos:
            raise SettingsError([f"Project {name}: add at least one repository."])
        projects.append({"name": name, "description": (form.get(f"p{i}_desc") or "").strip()[:500], "repos": repos})
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    if projects == base.get("projects", []):
        new_ov.pop("projects", None)
    else:
        new_ov["projects"] = projects
    _commit(cfg_path, state_dir, new_ov)


def save_telegram(cfg_path: str, state_dir: Path, form: Form) -> None:
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    try:
        chat = int((form.get("telegram.chat_id") or "0").strip() or 0)
        if chat < 0:
            raise ValueError
    except ValueError:
        raise SettingsError(["Chat id must be a whole number (0 turns Telegram off)."])
    verbosity = form.get("telegram.verbosity", "")
    if verbosity not in ("quiet", "normal", "verbose"):
        raise SettingsError(["Choose quiet, normal or verbose."])
    _store(new_ov, base, "telegram.chat_id", chat)
    _store(new_ov, base, "telegram.verbosity", verbosity)
    if form.get("telegram.mode") == "events":
        chosen = [e for e in form.getall("telegram.events") if e in ALL_EVENTS]
        if not chosen:
            raise SettingsError(["Pick at least one event, or choose the verbosity level instead."])
        _store(new_ov, base, "telegram.events", sorted(chosen))
    elif "events" in base.get("telegram", {}):
        set_in(new_ov, "telegram.events", "level")           # the base file pins a list; this switches back to the level
    else:
        del_in(new_ov, "telegram.events")
    _commit(cfg_path, state_dir, new_ov)


def set_chat_id(cfg_path: str, state_dir: Path, chat_id: int) -> None:
    if chat_id < 0:
        raise SettingsError(["Chat id must not be negative."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "telegram.chat_id", chat_id)
    _commit(cfg_path, state_dir, new_ov)


def save_slack(cfg_path: str, state_dir: Path, form: Form) -> None:
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    channel, user = (form.get("slack.channel") or "").strip(), (form.get("slack.user_id") or "").strip()
    if channel and not re.fullmatch(r"[CGD][A-Z0-9]{6,20}", channel):
        raise SettingsError(["The channel must be a Slack id such as C0123ABCDEF (empty turns Slack off)."])
    if user and not re.fullmatch(r"[UW][A-Z0-9]{6,20}", user):
        raise SettingsError(["Your user id must be a Slack member id such as U0123ABCDEF (empty turns Slack off)."])
    verbosity = form.get("slack.verbosity", "")
    if verbosity not in ("quiet", "normal", "verbose"):
        raise SettingsError(["Choose quiet, normal or verbose."])
    ui_url = (form.get("slack.ui_url") or "").strip()
    if ui_url and not re.fullmatch(r"https?://[^\s\"'<>|]{1,200}", ui_url):
        raise SettingsError(["The address of this UI must be an http(s) URL (or empty)."])
    _store(new_ov, base, "slack.channel", channel)
    _store(new_ov, base, "slack.user_id", user)
    if ui_url or "ui_url" in base.get("slack", {}):
        _store(new_ov, base, "slack.ui_url", ui_url.rstrip("/"))
    else:
        del_in(new_ov, "slack.ui_url")
    _store(new_ov, base, "slack.verbosity", verbosity)
    if form.get("slack.mode") == "events":
        chosen = [e for e in form.getall("slack.events") if e in ALL_EVENTS]
        if not chosen:
            raise SettingsError(["Pick at least one event, or choose the verbosity level instead."])
        _store(new_ov, base, "slack.events", sorted(chosen))
    elif "events" in base.get("slack", {}):
        set_in(new_ov, "slack.events", "level")
    else:
        del_in(new_ov, "slack.events")
    _commit(cfg_path, state_dir, new_ov)


def set_slack_user(cfg_path: str, state_dir: Path, user_id: str, channel: str = "") -> None:
    """Obey this member, and send to the channel they typed /factory in (when one was recorded)."""
    if not re.fullmatch(r"[UW][A-Z0-9]{6,20}", user_id):
        raise SettingsError(["That is not a Slack member id."])
    if channel and not re.fullmatch(r"[CGD][A-Z0-9]{6,20}", channel):
        raise SettingsError(["That is not a Slack channel id."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "slack.user_id", user_id)
    if channel:
        _store(new_ov, base, "slack.channel", channel)
    _commit(cfg_path, state_dir, new_ov)


IMAGE_RE = re.compile(r"^[\w./:@-]{1,200}$")


def save_harness(cfg_path: str, state_dir: Path, name: str, form: Form) -> None:
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    eff = deep_merge(base, ov)
    known = parse(eff).harnesses
    if name not in known:
        raise SettingsError(["Unknown harness."])
    cur = known[name]
    enabled = form.get("enabled") == "1"
    image = (form.get("image") or "").strip()
    command = (form.get("command") or "").strip()
    errors = []
    if not IMAGE_RE.match(image):
        errors.append("Image: a container image name such as localhost/factory-agent-codex:latest.")
    if not command or len(command) > 4000 or "\x00" in command or "/task/prompt.txt" not in command:
        errors.append("Command: required, and it must read the task from /task/prompt.txt.")
    try:
        hosts = parse_value(Field("hosts", "Allowed hosts", "hosts"), Form({"hosts": form.get("allow_hosts", "")}))
    except ValueError as e:
        hosts = None
        errors.append(str(e))
    if name == "claude-code" and not enabled:
        errors.append("claude-code is the default harness and stays enabled.")
    if not errors:
        widened = (enabled and not cur.enabled) or hosts != list(cur.allow_hosts) or command != cur.command or image != cur.image
        if widened and form.get("confirm") != "1":
            errors.append("Tick the confirmation box: enabling a harness, or changing its command, image or hosts, changes what runs "
                          "in the sandbox and what it can reach.")
    if errors:
        raise SettingsError(errors)
    new_ov = copy.deepcopy(ov)
    for field, value in (("enabled", enabled), ("image", image), ("command", command), ("allow_hosts", hosts)):
        _store(new_ov, base, f"harnesses.{name}.{field}", value)
    _commit(cfg_path, state_dir, new_ov)
