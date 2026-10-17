import dataclasses
from dataclasses import dataclass

from .classifier import Classification
from .config import EFFORT_LEVELS, Config, Route


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
    return Decision("dispatch", f"{c.kind}/{c.complexity}", build_route(cfg, c))


def floor(cfg: Config, step: str | None, route: Route) -> Route:
    """The route with its effort raised to the step's minimum ([effort_floor], set in the admin UI). Never lowered; an effort the
    factory does not rank is left as it is."""
    least = cfg.effort_floor.get(step or "")
    if least in EFFORT_LEVELS and route.effort in EFFORT_LEVELS and EFFORT_LEVELS.index(route.effort) < EFFORT_LEVELS.index(least):
        return dataclasses.replace(route, effort=least)
    return route


def build_route(cfg: Config, c: Classification | None) -> Route:
    """What a build runs with: the routing row of the ticket's size (the medium row when it was never classified, as when a person
    approves a build from chat), the classifier's effort if it chose one, then the implement step's minimum effort."""
    route = cfg.routes[c.complexity if c else "medium"]
    if c and c.effort:
        route = dataclasses.replace(route, effort=c.effort)
    return floor(cfg, "implement", route)


def behind(cfg: Config, role: str | None, done) -> bool:
    """Is this stage before one that is already done (the analyst after the architect)? auto never goes back."""
    names = [r.name for r in cfg.roles]
    later = [names.index(d) for d in done if d in names]
    return bool(role in names and later and names.index(role) < max(later))


def pick_stage(cfg: Config, c: Classification, done, multi_repo: bool = False,
               screens: bool = False) -> tuple[Classification, str | None]:
    """What `factory:auto` should treat as the classifier's stage. Three adjustments, all only for auto (a person's explicit
    label is always obeyed): no stage from the model (not the labels fallback) means the safe default, the analyst; a ticket a
    person asked screens for (screens: the mockups request label), or one the classifier says changes what users see (c.ui), gets
    the designer before the architect or a build; and a build of a high-complexity ticket, or a medium one spanning several
    repositories, gets the architect's plan first.
    Returns the classification (stage possibly changed) and why it was changed, if it was."""
    have = {r.name for r in cfg.roles}
    if c.stage is None and c.source != "labels" and "analyst" in have and not done:     # only before any stage has run
        return dataclasses.replace(c, stage="analyze"), "no stage from the classifier: analyst first"
    if (screens or c.ui) and c.stage in ("implement", "architect") and "designer" in have and "designer" not in done:
        why = "screens were requested" if screens else "it changes what users see"
        return dataclasses.replace(c, stage="design"), f"{why}: designer first"
    if (c.stage == "implement" and "architect" in have and "architect" not in done
            and (c.complexity == "high" or (c.complexity == "medium" and multi_repo))):
        return dataclasses.replace(c, stage="architect"), f"{c.complexity} complexity" + (" across repositories" if c.complexity == "medium" else "") + ": architect before build"
    return c, None
