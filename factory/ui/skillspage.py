"""The Agent skills pages: the operator's skills ([[skills]], factory/skills.py), listed and edited here.

A skill is trusted configuration like the Agent instructions: an agent reads it when its description fits the work. The only
write is the overrides file, through settings._commit, which validates the merged config exactly as the orchestrator will load it."""
import copy
from pathlib import Path

from .. import skills as sk
from ..config import SKILL_AGENTS, SKILL_MAX, SKILLS_MAX, deep_merge, skill_problems
from . import settings as S
from .views import badge, csrf_field, esc

AGENT_LABELS = {k: v for k, v in S.PROMPT_LABELS.items() if k in SKILL_AGENTS}
DANGER = "agents follow this text when it fits their work, with write access to their sandbox workspace."


def raw_list(cfg_path: str) -> list[dict]:
    """The skills as configured now (hand-written file with the UI's overrides on top), as raw dicts."""
    return copy.deepcopy(deep_merge(S.base_raw(cfg_path), S.overrides_raw(cfg_path)).get("skills", []))


def _where(names: tuple, every: str, labels: dict | None = None) -> str:
    return esc(", ".join((labels or {}).get(n, n) for n in names)) if names else f'<span class="muted">{every}</span>'


def list_page(cfg, csrf: str) -> str:
    head = ('<p class="muted">A skill is a set of instructions an agent reads only when its description fits the work in front of it, '
            'for example how your app is deployed or how your design system names things. Unlike Agent instructions, which every run '
            'carries, a skill costs nothing until it is needed. Rules that must always hold belong in '
            '<a href="/settings?section=prompts">Agent instructions</a>. <a href="/skills/edit">New skill</a></p>')
    rows = "".join(
        f'<tr><td data-l="Skill" class="nowrap"><a href="/skills/edit?name={esc(s.name)}"><code>{esc(s.name)}</code></a></td>'
        f'<td data-l="When to use it">{esc(s.description)}</td>'
        f'<td data-l="Agents">{_where(s.agents, "all agents", AGENT_LABELS)}</td>'
        f'<td data-l="Projects">{_where(s.projects, "all projects")}</td></tr>' for s in cfg.skills)
    table = ('<div class="scroll"><table class=stack><thead><tr><th>Skill</th><th>When to use it</th><th>Agents</th><th>Projects</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>' if cfg.skills else
             '<p>No skills yet. <a href="/skills/edit">Add the first one</a>.</p>')
    builtin = sk.DESIGN_MOCKUPS
    own = (f'<h2>Built in</h2><div class="card"><p><code>{esc(builtin.name)}</code> {badge("factory", "")}</p><p class="muted">{esc(builtin.description)}</p>'
           '<p class="muted">Given to the designer when design files are on (Stage agents). It cannot be changed here.</p>'
           f'<details><summary>Show the instructions</summary><pre>{esc(builtin.body)}</pre></details></div>')
    return (head + "<h2>Your skills</h2>" + table + own +
            '<p class="muted">Changes are saved to the overrides file and apply from the next run. Each run lists its skills to the agent, '
            'and the files are in <code>/task/skills</code> inside the sandbox.</p>')


def flat(entry: dict | None) -> dict:
    e = entry or {}
    return {"name": e.get("name", ""), "description": e.get("description", ""), "body": e.get("body", ""),
            "agents": list(e.get("agents", [])), "projects": list(e.get("projects", []))}


def from_form(form) -> dict:
    return {"name": (form.get("name") or "").strip(), "description": " ".join((form.get("description") or "").split()),
            "body": (form.get("body") or "").replace("\r\n", "\n").strip(),
            "agents": [a for a in form.getall("agent") if a in SKILL_AGENTS] if hasattr(form, "getall") else [],
            "projects": [p for p in form.getall("project") if p] if hasattr(form, "getall") else []}


def form_page(cfg, v: dict, csrf: str, original: str = "") -> str:
    new = not original
    agents = "".join(f'<label class="check"><input type="checkbox" name="agent" value="{k}"{" checked" if k in v["agents"] else ""}> {esc(label)}</label>'
                     for k, label in AGENT_LABELS.items())
    names = list(dict.fromkeys([p.name for p in cfg.projects] + v["projects"]))
    projects = "".join(f'<label class="check"><input type="checkbox" name="project" value="{esc(n)}"{" checked" if n in v["projects"] else ""}> {esc(n)}</label>'
                       for n in names) or '<p class="muted">No projects are set up yet.</p>'
    body = (f'<p><a href="/skills">← all skills</a></p>'
            f'<form method="post" action="/skills/save" class="settings">{csrf_field(csrf)}<input type="hidden" name="orig" value="{esc(original)}">'
            '<fieldset><legend>The skill</legend>'
            + ('<div class="field"><label for="f_name">Name</label><input id="f_name" name="name" value="' + esc(v["name"]) + '" required maxlength="63" '
               'pattern="[a-z0-9][a-z0-9\\-]*" autocomplete="off"><div class="muted">Lower-case letters, digits and dashes, for example '
               '<code>deploy-steps</code>.</div></div>' if new else
               f'<div class="field"><label>Name</label><code>{esc(original)}</code><input type="hidden" name="name" value="{esc(original)}"></div>')
            + '<div class="field"><label for="f_desc">When to use it</label><input id="f_desc" name="description" value="' + esc(v["description"])
            + '" required maxlength="500" autocomplete="off" class="wide"><div class="muted">One line. The agent sees only this until it decides to read the '
            'skill, so say what work it is for: “How to add a database migration in the api repository.”</div></div>'
            f'<div class="field"><label for="f_body">Instructions</label><textarea id="f_body" name="body" rows="14" maxlength="{SKILL_MAX}" required>'
            f'\n{esc(v["body"])}</textarea><div class="muted">Markdown. At most {SKILL_MAX} characters.</div></div>'
            '</fieldset><fieldset><legend>Who gets it</legend>'
            f'<div class="field"><label>Agents</label>{agents}<div class="muted">None ticked: every agent.</div></div>'
            f'<div class="field"><label>Projects</label>{projects}<div class="muted">None ticked: every project.</div></div>'
            '</fieldset>'
            f'<label class="check danger"><input type="checkbox" name="confirm" value="1"> I understand: {esc(DANGER)}</label>'
            '<p><button type="submit">Save skill</button></p></form>')
    if not new:
        body += ('<h2>Delete</h2><form method="post" action="/skills/delete" class="settings">' + csrf_field(csrf) +
                 f'<input type="hidden" name="name" value="{esc(original)}"><label class="check danger"><input type="checkbox" name="confirm" value="1"> '
                 f'Delete {esc(original)}.</label><button type="submit">Delete skill</button></form>')
    return body


def build(v: dict, new: bool, taken: set[str], confirmed: bool) -> dict:
    """The [[skills]] entry for the form's values, or SettingsError with what to fix."""
    entry = {"name": v["name"], "description": v["description"], "body": v["body"]}
    if v["agents"]:
        entry["agents"] = list(dict.fromkeys(v["agents"]))
    if v["projects"]:
        entry["projects"] = list(dict.fromkeys(v["projects"]))
    errors = skill_problems(entry)
    if new and v["name"] in taken:
        errors.append(f"Name: there is already a skill called {v['name']}.")
    if new and len(taken) >= SKILLS_MAX:
        errors.append(f"There can be at most {SKILLS_MAX} skills.")
    if not confirmed:
        errors.append("Tick the confirmation box: " + DANGER)
    if errors:
        raise S.SettingsError(errors)
    return entry


def store(cfg_path: str, state_dir: Path, name: str, entry: dict | None) -> None:
    """Replace (or add, or with entry=None remove) one skill. The whole list is kept in the overrides file, because a list there
    replaces the config file's list: the first save copies the hand-written ones across."""
    out, hit = [], False
    for x in raw_list(cfg_path):
        if x.get("name") == name:
            hit = True
            if entry is not None:
                out.append(entry)
        else:
            out.append(x)
    if not hit and entry is not None:
        out.append(entry)
    base, new_ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    if out == base.get("skills", []):
        new_ov.pop("skills", None)
    else:
        new_ov["skills"] = out
    S._commit(cfg_path, state_dir, new_ov)
