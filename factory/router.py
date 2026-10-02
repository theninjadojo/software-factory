import dataclasses
from dataclasses import dataclass

from .classifier import Classification
from .config import Config, Route


@dataclass(frozen=True)
class Decision:
    action: str            # "dispatch" | "human"
    reason: str
    route: Route | None = None


def decide(cfg: Config, c: Classification) -> Decision:
    if c.kind == "question":
        return Decision("human", "question needs a person")
    if c.needs_human:
        return Decision("human", "classifier flagged needs_human")
    if c.confidence < cfg.confidence_threshold:
        return Decision("human", f"low confidence {c.confidence:.2f}")
    route = cfg.routes[c.complexity]
    if c.effort:
        route = dataclasses.replace(route, effort=c.effort)
    return Decision("dispatch", f"{c.kind}/{c.complexity}", route)


def pick_stage(cfg: Config, c: Classification, done, multi_repo: bool = False) -> tuple[Classification, str | None]:
    """What `factory:auto` should treat as the classifier's stage. Two adjustments, both only for auto (a person's explicit
    label is always obeyed): no stage from the model (not the labels fallback) means the safe default, the analyst; and a build
    of a high-complexity ticket, or a medium one spanning several repositories, gets the architect's plan first.
    Returns the classification (stage possibly changed) and why it was changed, if it was."""
    have = {r.name for r in cfg.roles}
    if c.stage is None and c.source != "labels" and "analyst" in have and "analyst" not in done:
        return dataclasses.replace(c, stage="analyze"), "no stage from the classifier: analyst first"
    if (c.stage == "implement" and "architect" in have and "architect" not in done
            and (c.complexity == "high" or (c.complexity == "medium" and multi_repo))):
        return dataclasses.replace(c, stage="architect"), f"{c.complexity} complexity" + (" across repositories" if c.complexity == "medium" else "") + ": architect before build"
    return c, None
