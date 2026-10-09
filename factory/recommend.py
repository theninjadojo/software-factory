"""The next stage a stage document recommends, for a classifier that names none (the labels-only classifier never does).

Every stage document ends with a 'Recommended next stage' line. The document is one our own account posted (stage_outputs ignores
every other author), and only a fixed word is read from it, never free text: design, architect, implement or needs-human. The
result is a Classification with `source = SOURCE`, so the rest of auto treats it like a classifier's answer, with two limits:
  - design and architect (read-only stages) go ahead; a stage already done, or one the factory has no role for, is ignored;
  - implement never starts a build. A person starts builds (`factory:ready`, or a button on the alert).
A classifier that did name a stage (Jev) is never overridden."""
import dataclasses
import re

from .roles import STAGE_TO_ROLE

SOURCE = "stage recommendation"
LINE = re.compile(r"^[ \t>#*_-]*recommended next stage[ \t*_:\-–—]*[`'\"(\[ \t]*(needs[- _]?human|designer|design|architect|implement)\b",
                  re.IGNORECASE | re.MULTILINE)
WORDS = {"design": "design", "designer": "design", "architect": "architect", "implement": "implement"}


def parse(doc: str | None) -> str | None:
    """design, architect, implement or needs-human from the document's last 'Recommended next stage' line; None when there is none."""
    found = LINE.findall(doc or "")
    if not found:
        return None
    word = re.sub(r"[ _]", "-", found[-1].lower())
    return "needs-human" if word.startswith("needs") else WORDS[word]


def latest(outputs: dict, done) -> str | None:
    """The newest finished stage's document: the last of `done` (in role order) that has one."""
    return next((outputs[name] for name in reversed(list(done)) if outputs.get(name)), None)


def apply(cfg, c, doc: str | None, done):
    """`c` with the document's recommendation filled in, when the classifier named no stage and did not already ask for a person."""
    if c is None or c.stage is not None or c.needs_human:
        return c
    rec = parse(doc)
    if rec == "needs-human":
        return dataclasses.replace(c, needs_human=True, source=SOURCE)
    if rec == "implement":
        return dataclasses.replace(c, stage="implement", source=SOURCE)
    role = STAGE_TO_ROLE.get(rec or "")
    if role and role not in done and any(r.name == role for r in cfg.roles):
        return dataclasses.replace(c, stage=rec, stage_confidence=None, source=SOURCE)
    return c


def from_document(c) -> bool:
    return bool(c) and c.source == SOURCE


def build_waits(c) -> bool:
    """The document recommends a build: it is shown to a person, never started by auto."""
    return from_document(c) and c.stage == "implement"
