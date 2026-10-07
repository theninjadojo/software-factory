"""Work out which repos of a project publish packages that the others install, and how each one publishes, from what is on GitHub.

Read-only, from each repo's default branch: its package.json files (which packages it publishes; which it depends on) and its
workflow files (what publishes them). The result is only a suggestion: a person applies it on the Projects page (or in setup), which
writes `publish` and `depends_on` (see waves.py). JavaScript packages only.

A repo publishes the packages named in its non-private package.json files. Another repo of the same project depends on it when its
package.json files list one of those names. How the publish is seen: a workflow that publishes (npm/pnpm/yarn publish, changesets,
semantic-release) means `detect = "workflow"` with that file; otherwise a repo with releases means "release", one with tags "tag"."""
import json
import logging
import re
from dataclasses import dataclass, field

from .config import Project

log = logging.getLogger("factory.depdetect")
MAX_MANIFESTS, MAX_WORKFLOWS, MAX_DEPTH = 40, 20, 5
PUBLISHES = re.compile(r"\b(?:npm|pnpm|yarn(?: npm)?)\s+publish\b|changeset[s]?\s+publish|changesets/action|semantic-release|npm-publish@", re.I)
DEP_KEYS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


@dataclass
class RepoFacts:
    repo: str
    publishes: list[str] = field(default_factory=list)     # package names
    uses: set[str] = field(default_factory=set)            # dependency names
    workflow: str = ""                                      # the workflow file that publishes, if any
    manual: bool = False                                    # that workflow only runs when someone starts it
    releases: bool = False
    tags: bool = False
    error: str = ""


@dataclass
class Suggestion:
    publisher: str
    detect: str
    workflow: str
    packages: list[str]                                     # what it publishes that the others use
    consumers: dict[str, list[str]]                         # repo -> the packages it uses
    manual: bool = False
    note: str = ""

    def publish_table(self) -> dict:
        out = {"detect": self.detect, "packages": self.packages}
        if self.workflow:
            out["workflow"] = self.workflow
        return out


def _json(gh, repo: str, path: str, ref: str) -> dict:
    try:
        data = json.loads(gh.raw_file(repo, path, ref, 1_000_000))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def facts(gh, repo: str) -> RepoFacts:
    f = RepoFacts(repo)
    try:
        ref = gh.default_branch(repo)
        paths = gh.tree(repo, ref)
    except Exception as e:
        f.error = f"could not read {repo} ({type(e).__name__})"
        return f
    manifests = sorted((p for p in paths if p.rsplit("/", 1)[-1] == "package.json" and "node_modules/" not in p
                        and p.count("/") < MAX_DEPTH), key=lambda p: (p.count("/"), p))[:MAX_MANIFESTS]
    for p in manifests:
        m = _json(gh, repo, p, ref)
        name = m.get("name")
        if isinstance(name, str) and name and not m.get("private"):
            f.publishes.append(name)
        for k in DEP_KEYS:
            if isinstance(m.get(k), dict):
                f.uses |= {n for n in m[k] if isinstance(n, str)}
    f.uses -= set(f.publishes)                              # its own workspace packages are not a dependency on another repo
    for p in sorted(p for p in paths if re.fullmatch(r"\.github/workflows/[\w.-]+\.ya?ml", p))[:MAX_WORKFLOWS]:
        try:
            text = gh.raw_file(repo, p, ref, 500_000).decode(errors="replace")
        except Exception:
            continue
        if PUBLISHES.search(text):
            f.workflow = p.rsplit("/", 1)[-1]
            f.manual = _manual_only(text)
            break
    if not f.workflow:
        try:
            f.releases = bool(gh.releases(repo, 1))
            f.tags = not f.releases and bool(gh.tags(repo, 1))
        except Exception:
            pass
    return f


def triggers(text: str) -> set[str]:
    """The events of a workflow's top-level `on:` (inline `on: push`, `on: [push, release]`, or a block of `  event:` lines)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"""^["']?on["']?:\s*([^#]*)""", line)
        if not m:
            continue
        if m.group(1).strip():
            return set(re.findall(r"[a-z_]+", m.group(1)))
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.lstrip().startswith("#"):
                if not nxt.startswith((" ", "\t")):
                    break
                block.append(nxt)
        if not block:
            return set()
        indent = min(len(b) - len(b.lstrip()) for b in block)
        return {e.group(1) for b in block if len(b) - len(b.lstrip()) == indent and (e := re.match(r"^\s*-?\s*([a-z_]+)", b))}
    return set()


def _manual_only(text: str) -> bool:
    """A workflow whose only trigger is workflow_dispatch: someone has to start it after the merge."""
    return triggers(text) == {"workflow_dispatch"}


def suggest(project: Project, found: dict[str, RepoFacts]) -> list[Suggestion]:
    """One suggestion per repo that publishes something another repo of the project uses."""
    out = []
    for pub in project.repos:
        p = found.get(pub.repo)
        if not p or not p.publishes:
            continue
        consumers = {}
        for other in project.repos:
            o = found.get(other.repo)
            if o and other.repo != pub.repo and (used := sorted(set(p.publishes) & o.uses)):
                consumers[other.repo] = used
        if not consumers:
            continue
        used = sorted({n for ns in consumers.values() for n in ns})
        if p.workflow:
            s = Suggestion(pub.repo, "workflow", p.workflow, used, consumers, p.manual,
                           f"The {p.workflow} workflow publishes them" + (", and someone runs it by hand (it only has workflow_dispatch)" if p.manual else ""))
        elif p.releases or p.tags:
            kind = "release" if p.releases else "tag"
            s = Suggestion(pub.repo, kind, "", used, consumers, note=f"No publishing workflow was found; it has {kind}s, so a new {kind} counts as published")
        else:
            s = Suggestion(pub.repo, "release", "", used, consumers, note="No publishing workflow, release or tag was found: check how it publishes")
        out.append(s)
    return out


def detect(gh, project: Project) -> tuple[list[Suggestion], list[str]]:
    """(suggestions, problems) for a project. Never raises."""
    found = {r.repo: facts(gh, r.repo) for r in project.repos}
    return suggest(project, found), [f.error for f in found.values() if f.error]


def current(project: Project) -> dict:
    """What the project's config says now, in the shape `suggest` produces: {publisher: (detect, workflow, packages, consumers)}."""
    out = {}
    for r in project.repos:
        if r.publish:
            out[r.repo] = (r.publish.detect, r.publish.workflow, sorted(r.publish.packages),
                           sorted(x.repo for x in project.repos if r.repo in x.depends_on))
    return out


def is_applied(project: Project, s: Suggestion) -> bool:
    now = current(project).get(s.publisher)
    return bool(now) and now == (s.detect, s.workflow, sorted(s.packages), sorted(s.consumers))
