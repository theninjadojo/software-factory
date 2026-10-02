"""Alert categories and verbosity levels (shared by config, the orchestrator and Telegram)."""
CRITICAL = frozenset({"needs_human", "failure", "rate_limit"})
NORMAL = CRITICAL | {"pr_ready", "stage_done", "ci_result", "ci_fix", "recovery", "review_done", "conflict"}
ALL_EVENTS = NORMAL | {"started", "startup", "skipped", "info"}
LEVELS = {"quiet": CRITICAL, "normal": NORMAL, "verbose": ALL_EVENTS}


def event_enabled(verbosity: str, event: str, overrides=None) -> bool:
    """An explicit `events` list wins; otherwise the verbosity level decides."""
    if overrides is not None:
        return event in overrides
    return event in LEVELS.get(verbosity, NORMAL)
