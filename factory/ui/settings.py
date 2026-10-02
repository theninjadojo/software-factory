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
from ..config import DEFAULT_ROLES, MAX_FALLBACKS, MODEL_RE, CiCfg, ConflictsCfg, PmCfg, ReviewCfg, RunnerCfg, deep_merge, default_harnesses, load_raw, overrides_path, parse
from ..events import ALL_EVENTS
from ..tomlw import dumps

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
HOST_RE = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
EFFORTS = ("low", "medium", "high")


class SettingsError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Field:
    key: str                 # dotted path in the config
    label: str
    kind: str                # int float bool text select checks repos hosts kv models
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
}


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
    if section == "pm":
        return getattr(PmCfg(), name, None)
    if section == "roles":
        role, _, field = name.partition(".")
        v = next((getattr(r, field) for r in DEFAULT_ROLES if r.name == role), None)
        return list(v) if isinstance(v, tuple) else v
    return {"auto.label": "factory:auto", "routing.low.fallback_models": [], "routing.medium.fallback_models": [], "routing.high.fallback_models": [],
            "classifier.backend": "rules", "classifier.model": "typesafe/jev-1.13",
            "classifier.kind_aliases": dict(KIND_ALIASES), "telegram.verbosity": "normal", "auto.confirm_stages": False, "auto.chain": True}.get(key)


def effective(raw: dict, key: str):
    v = get_in(raw, key)
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
        f.write("# Written by the software-factory UI. Safe to delete a key to fall back to config.toml.\n" + dumps(data))
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
            "models": f"up to {MAX_FALLBACKS} distinct model ids, one per line (may be empty)", "text": "a short single-line value"}.get(f.kind, "a valid value")
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
    current = get_in(base, key)
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
    _commit(cfg_path, state_dir, new_ov)
    return notes


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
