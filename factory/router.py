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
