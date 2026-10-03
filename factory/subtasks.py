"""Mirror each pipeline step of a ticket (analyze, design, architect, implement, review, ci-fix) as a GitHub sub-issue.

The local database is the source of truth (see db.steps_for_ticket); this only reflects it on GitHub. It is best effort:
a GitHub error is logged and never fails or undoes a run. A sub-issue is created when its step first starts, and a person
who closes or reopens it by hand is respected: the factory stops touching that sub-issue."""
import logging
import re

from . import db as dbm
from .github import STEP_TITLE_PREFIX

log = logging.getLogger("factory.subtasks")
PR_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")
SAFE = re.compile(r"[^\w.:/ -]")
LABELS = {"analyze": "Analyze", "design": "Design", "architect": "Architect", "implement": "Implement", "review": "Review", "ci-fix": "CI fix"}


def title(step: str, parent: int) -> str:
    return f"{STEP_TITLE_PREFIX}{LABELS[step]} · #{int(parent)}"


def body(step: str, parent: int, s: dict) -> str:
    """Built only from fields the factory controls. Ticket text and agent output are deliberately left out."""
    clean = lambda v: SAFE.sub("", str(v or "")) or "?"
    lines = [f"<!-- factory:step={step} parent={int(parent)} -->",
             f"**Status:** {s['status']}", f"**Agent:** {clean(s['role'])} · {clean(s['harness'])} / {clean(s['model'])} (effort {clean(s['effort'])})",
             f"**Attempts:** {int(s['attempts'])}", f"**Latest run:** #{int(s['run_id'])}"]
    prs = [u for u in (s["pr_urls"] or "").split() if PR_URL.match(u)]
    if prs:
        lines.append("**Pull requests:** " + " ".join(prs))
    return "\n".join(lines) + "\n\n_Updated by Shikumi. Do not edit._"


def sync(gh, db, repo: str, issue: int, step: str | None) -> None:
    """Bring the step's sub-issue in line with the latest run. Never raises."""
    if not step:
        return
    try:
        s = next((x for x in dbm.steps_for_ticket(db, repo, issue) if x["step"] == step), None)
        if s is None:
            return
        state = "closed" if s["status"] == "done" else "open"
        row = dbm.get_step_issue(db, repo, issue, step)
        if row and row["hands_off"]:
            return
        if row is None or row["sub_number"] is None:
            made = gh.create_issue(repo, title(step, issue), body(step, issue, s))
            dbm.upsert_step_issue(db, repo, issue, step, sub_number=made["number"], sub_id=made["id"], last_state="open", sync_error="unlinked")
            gh.add_sub_issue(repo, issue, made["id"])
            dbm.upsert_step_issue(db, repo, issue, step, sync_error="")
            if state == "open":
                return
            row = dbm.get_step_issue(db, repo, issue, step)
        else:
            if row["sync_error"] == "unlinked":
                gh.add_sub_issue(repo, issue, row["sub_id"])
                dbm.upsert_step_issue(db, repo, issue, step, sync_error="")
            if gh.get_issue(repo, row["sub_number"]).get("state") != row["last_state"]:
                dbm.upsert_step_issue(db, repo, issue, step, hands_off=1)     # a person changed it: leave it alone
                return
        gh.update_issue(repo, row["sub_number"], body=body(step, issue, s), state=state)
        dbm.upsert_step_issue(db, repo, issue, step, last_state=state, sync_error="")
    except Exception as e:
        log.exception("could not sync the %s sub-issue of %s#%s", step, repo, issue)
        try:
            row = dbm.get_step_issue(db, repo, issue, step)
            if not row or row["sync_error"] != "unlinked":       # keep the marker so the next sync retries linking
                dbm.upsert_step_issue(db, repo, issue, step, sync_error=f"{type(e).__name__}: {e}"[:300])
        except Exception:
            pass
