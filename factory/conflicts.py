"""Notice factory PRs that conflict with their base branch. Each conflict is reported once per head commit on the PR, the
ticket and Telegram, and (with `auto`) the fix label is applied to the ticket, which main.handle_issue picks up through
the usual trust gate. The resolution itself is main.fix_conflicts."""
import logging

from . import db as dbm
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.conflicts")
COLOR, DESCRIPTION = "d93f0b", "Merge the base branch into this ticket's factory PRs and resolve the conflicts"
_created: set[str] = set()                 # repos where the label was created (or found) by this process


def state_of(pr: dict) -> str:
    """closed | unknown (GitHub is still computing mergeability) | conflicting | clean."""
    if pr.get("state") != "open":
        return "closed"
    if pr.get("mergeable") is None:
        return "unknown"
    return "conflicting" if pr["mergeable"] is False or pr.get("mergeable_state") == "dirty" else "clean"


def ensure_label(gh, repo: str, label: str) -> None:
    if repo in _created:
        return
    try:
        gh.create_label(repo, label, COLOR, DESCRIPTION)
        _created.add(repo)
    except Exception:
        log.warning("could not create the label %s in %s", label, repo)   # adding it to the issue still works


def watch(cfg, gh, conn, notify) -> None:
    """One pass over every tracked open factory PR. `notify(text, event)` sends an alert."""
    if not cfg.conflicts.enabled:
        return
    labelled: set[tuple[str, int]] = set()
    for row in dbm.tracked_prs(conn):
        repo, number, issue_repo, issue_num = row["repo"], row["number"], row["issue_repo"], row["issue_num"]
        try:
            pr = gh.get_pr(repo, number)
            state = state_of(pr)
            if state == "unknown":                    # never a conflict: GitHub computes it after this request; look again next pass
                continue
            if state != "conflicting":
                if state != row["state"]:
                    dbm.update_conflict(conn, repo, number, state=state, notified_sha="")
                continue
            sha, url = pr["head"]["sha"], pr["html_url"]
            if row["notified_sha"] == sha:            # already reported for this commit
                continue
            dbm.update_conflict(conn, repo, number, state="conflicting", notified_sha=sha)
            label, left = cfg.conflicts.label, cfg.conflicts.max_attempts - row["attempts"]
            auto = cfg.conflicts.auto and left > 0
            how = (f"The factory applied `{label}` to the ticket and will merge the base branch into the PR branch." if auto else
                   f"Apply `{label}` to the ticket to have the factory merge the base branch into the PR branch." if left > 0 else
                   f"The factory has used all {cfg.conflicts.max_attempts} resolution attempts for this PR: a person should resolve it.")
            msg = sanitize_markdown(f"{url} has merge conflicts with `{pr['base']['ref']}`. " + how)
            gh.comment(repo, number, msg)
            gh.comment(issue_repo, issue_num, msg)
            notify(f"Merge conflict: {repo}#{number} (ticket {issue_repo}#{issue_num})"
                   + ("; resolving automatically" if auto else "" if left > 0 else "; attempt limit reached") + f"\n{url}", "conflict")
            if auto and (issue_repo, issue_num) not in labelled:     # sibling PRs share one run: label the ticket once
                ensure_label(gh, issue_repo, label)
                gh.add_labels(issue_repo, issue_num, [label])
                labelled.add((issue_repo, issue_num))
        except Exception:
            log.exception("conflict check failed for %s#%s", repo, number)
