"""Classification. Output is validated against fixed enums: whatever the model
(or the issue text) says, the only thing it can do is select a value from these sets."""
from dataclasses import dataclass
from typing import Protocol

KINDS = {"bug", "feature", "docs", "chore", "question"}
COMPLEXITY = {"low", "medium", "high"}
KIND_ALIASES = {"enhancement": "feature", "documentation": "docs"}   # GitHub's default label names


def kind_from_labels(labels, aliases: dict | None = None) -> str | None:
    """A kind label (bug, feature, ...) or a configured alias for one (for example GitHub's `enhancement`)."""
    names, aliases = set(labels), (KIND_ALIASES if aliases is None else aliases)
    return next((k for k in sorted(KINDS) if k in names), None) or next(
        (aliases[a] for a in sorted(aliases) if a in names), None)


@dataclass(frozen=True)
class Classification:
    kind: str
    complexity: str
    needs_human: bool
    confidence: float
    source: str = "labels"   # which classifier produced this (model id, or "labels")
    effort: str | None = None   # low/medium/high when the classifier chose it; else the routing row decides
    stage: str | None = None             # analyze/design/architect/implement: the recommended next stage
    stage_confidence: float | None = None

    def __post_init__(self):
        if self.kind not in KINDS or self.complexity not in COMPLEXITY:
            raise ValueError(f"invalid classification: {self}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence out of range")


class Classifier(Protocol):
    def classify(self, title: str, body: str, labels: list[str], comments: list[str] | None = None, project: dict | None = None,
                 stages_done: list[str] | None = None) -> Classification: ...


class RuleClassifier:
    def __init__(self, kind_aliases: dict | None = None):
        self.kind_aliases = kind_aliases
    """Placeholder until the Jev (OpenRouter Decisions) classifier is wired in.
    Uses maintainer-applied labels only; ignores issue body entirely."""

    def classify(self, title: str, body: str, labels: list[str], comments: list[str] | None = None, project: dict | None = None,
                 stages_done: list[str] | None = None) -> Classification:
        names = set(labels)
        kind = kind_from_labels(names, self.kind_aliases)
        level = next((c for c in COMPLEXITY if f"complexity:{c}" in names), None)
        if kind is None or level is None:
            return Classification(kind or "chore", level or "medium", needs_human=False, confidence=0.4)
        return Classification(kind, level, needs_human=False, confidence=0.9)
