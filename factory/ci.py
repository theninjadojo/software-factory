"""Watch the repos' own CI on factory PRs. Report the result on the PR, the ticket and Telegram, and (optionally) give
the agent a fix round with the failing logs. Needs the bot token to have Actions: read (Commit statuses: read is optional)."""
import logging
import re
import time
import urllib.error

from . import db as dbm
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.ci")
GOOD = {"success", "neutral", "skipped"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def normalize(checks: list[dict], statuses: list[dict]) -> list[dict]:
    out = [{"id": c["id"], "name": c["name"], "status": c["status"], "state": ("pending" if c["status"] != "completed" else
                                                       "success" if c.get("conclusion") in GOOD else "failure"),
            "conclusion": c.get("conclusion"), "url": c.get("html_url"), "actions": True,
            "summary": ((c.get("output") or {}).get("title") or "")} for c in checks]
    out += [{"id": None, "name": s["context"], "status": s["state"], "state": s["state"] if s["state"] in ("pending", "success") else "failure",
             "conclusion": s["state"], "url": s.get("target_url"), "actions": False, "summary": s.get("description") or ""}
            for s in statuses]
    return out


def evaluate(items: list[dict]) -> str:
    """none (nothing reported yet) | pending | passed | failed. Waits for everything to finish before judging."""
    if not items:
        return "none"
    if any(i["state"] == "pending" for i in items):
        return "pending"
    return "failed" if any(i["state"] == "failure" for i in items) else "passed"


def failing_logs(gh, repo: str, items: list[dict], chars: int) -> str:
    """Names, summaries and log tails of failing checks, for the fix agent and the report. Untrusted text."""
    parts = []
    for i in [x for x in items if x["state"] == "failure"][:4]:
        tail = ""
        if i["actions"] and i["id"]:
            try:
                tail = ANSI.sub("", gh.job_log_tail(repo, i["id"], chars) or "")
            except Exception:
                tail = ""
        parts.append(f"## {i['name']} ({i['conclusion']}) {i['summary']}\n{tail[-chars:]}")
    return "\n\n".join(parts)


def report(repo: str, number: int, url: str, state: str, items: list[dict], logs: str = "", note: str = "") -> str:
    icon = {"passed": "PASSED", "failed": "FAILED", "none": "NO CHECKS RAN", "timed-out": "TIMED OUT"}.get(state, state.upper())
    rows = "\n".join(f"| {i['name']} | {i['conclusion'] or i['state']} |" for i in items) or "| (none) | |"
    body = f"### CI {icon}: {repo}#{number}\n{url}\n\n| Check | Result |\n|---|---|\n{rows}\n"
    if note:
        body += f"\n{note}\n"
    if logs:
        body += "\n<details><summary>Failing logs (tail)</summary>\n\n```\n" + logs.replace("```", "'''")[-5000:] + "\n```\n</details>\n"
    return sanitize_markdown(body)


def record(db, state: str, repo: str, number: int, issue_repo: str, issue_num: int, what: str) -> None:
    """A ci:<state> event on the ticket, so its journey shows each CI round. Observability never stops the watcher."""
    try:
        dbm.add_event(db, f"ci:{state}", f"{repo}#{number}: {what}", issue_repo, issue_num)
    except Exception:
        log.exception("could not record the CI result")


def _inline(conn):
    def submit(issue_repo, issue_num, job) -> bool:
        job(conn)
        return True
    return submit


def recheck_failed(gh, conn, notify) -> None:
    """A PR marked failed is no longer watched for fixes, but it must not stay failed for good: a person may push a fix,
    re-run CI, merge or close it. Resolve it quietly to closed or passed; a still-failing one stays as it is."""
    for repo, number, issue_repo, issue_num in dbm.failed_prs(conn):
        try:
            pr = gh.get_pr(repo, number)
            if pr["state"] != "open":
                dbm.update_pr(conn, repo, number, status="closed", summary="merged" if pr.get("merged") else "closed")
                continue
            items = normalize(gh.check_runs(repo, pr["head"]["sha"]), gh.commit_statuses(repo, pr["head"]["sha"]))
            if evaluate(items) == "passed":
                dbm.update_pr(conn, repo, number, status="passed", summary=f"{len(items)} checks passed")
                record(conn, "passed", repo, number, issue_repo, issue_num, f"{len(items)} checks passed")
                notify(f"CI now passes: {repo}#{number} ({len(items)} checks)\n{pr['html_url']}", "ci_result")
        except Exception:
            log.exception("ci recheck failed for %s#%s", repo, number)


def sync_closed(gh, conn, statuses=("passed", "no-ci", "timed-out")) -> None:
    """A PR that finished CI is no longer watched, but a person may still merge or close it. Mark it closed so the ticket leaves PRs & CI."""
    for repo, number in dbm.unclosed_prs(conn, tuple(statuses)):
        try:
            pr = gh.get_pr(repo, number)
            if pr["state"] != "open":
                dbm.update_pr(conn, repo, number, status="closed", summary="merged" if pr.get("merged") else "closed")
        except Exception:
            log.exception("merge check failed for %s#%s", repo, number)


def watch_ci(cfg, gh, conn, notify, fix, submit=None) -> None:
    """One pass over every PR being watched. `fix(repo, number, issue_repo, issue_num, failures)` runs a fix round and
    returns True if it pushed a change (CI will restart), False otherwise. `submit(issue_repo, issue_num, job)` runs
    job(db) for the round (on a worker, with that thread's connection) and returns False when the ticket is busy or no
    slot is free: then nothing is posted and the next pass tries again. By default the round runs here, on conn."""
    if not cfg.ci.enabled:
        sync_closed(gh, conn, ("watching", "failed", "passed", "no-ci", "timed-out"))   # a light sync: merge state only, no CI
        return
    submit = submit or _inline(conn)
    recheck_failed(gh, conn, notify)
    sync_closed(gh, conn)
    for repo, number, issue_repo, issue_num, rounds, started, last in dbm.watching(conn):
        try:
            pr = gh.get_pr(repo, number)
            if pr["state"] != "open":
                dbm.update_pr(conn, repo, number, status="closed", summary="merged" if pr.get("merged") else "closed")
                continue
            sha, url, age = pr["head"]["sha"], pr["html_url"], (time.time() - started) / 60
            try:
                items = normalize(gh.check_runs(repo, sha), gh.commit_statuses(repo, sha))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    if last != "no-access":
                        dbm.update_pr(conn, repo, number, summary="no-access")
                        notify(f"CI status is not available for {repo}#{number}: the GitHub token needs read access to Actions "
                               "(and optionally Commit statuses). The pull request itself is fine. I'll keep trying quietly.", "ci_result")
                    continue
                raise
            state = evaluate(items)
            if state != "none" and last == "waiting-for-ci":      # CI that someone started by hand: start the clock now
                dbm.update_pr(conn, repo, number, watch_started=time.time(), summary="ci running")
                age, last = 0.0, "ci running"
            if state == "none":
                if age >= cfg.ci.manual_wait_hours * 60:
                    dbm.update_pr(conn, repo, number, status="no-ci", summary="no CI ever ran")
                elif age >= cfg.ci.wait_for_checks_minutes and last != "waiting-for-ci":
                    dbm.update_pr(conn, repo, number, summary="waiting-for-ci")
                    msg = report(repo, number, url, "none", items, note=(
                        "No automatic CI ran on this branch. If this repository runs CI manually, start the CI workflow on the "
                        "branch from the Actions tab: the factory keeps watching and will report the result here."))
                    gh.comment(issue_repo, issue_num, msg)
                    notify(f"No automatic CI ran for {repo}#{number}. If CI is manual, run it on the branch; I'll keep watching.\n{url}", "ci_result")
                continue
            if state == "pending":
                if all(i["status"] in ("queued", "waiting", "pending") for i in items) and age >= cfg.ci.queued_warn_minutes and last != "queued-warned":
                    dbm.update_pr(conn, repo, number, summary="queued-warned")
                    notify(f"CI is queued but has not started for {repo}#{number} after {int(age)} minutes. If the repo uses self-hosted "
                           f"runners, check that they are running.\n{url}", "ci_result")
                if age >= cfg.ci.timeout_minutes:
                    dbm.update_pr(conn, repo, number, status="timed-out", summary="checks still pending")
                    record(conn, "timed-out", repo, number, issue_repo, issue_num, "checks still pending")
                    gh.comment(issue_repo, issue_num, report(repo, number, url, "timed-out", items))
                    notify(f"CI timed out for {repo}#{number}\n{url}", "ci_result")
                continue
            if state == "passed":
                dbm.update_pr(conn, repo, number, status="passed", summary=f"{len(items)} checks passed")
                record(conn, "passed", repo, number, issue_repo, issue_num, f"{len(items)} checks passed")
                gh.comment(repo, number, report(repo, number, url, "passed", items))
                gh.comment(issue_repo, issue_num, f"CI passed on {url} ({len(items)} checks).")
                notify(f"CI passed: {repo}#{number} ({len(items)} checks)\n{url}", "ci_result")
                continue
            logs = failing_logs(gh, repo, items, cfg.ci.log_tail_chars)
            names = ", ".join(i["name"] for i in items if i["state"] == "failure")
            if rounds < cfg.ci.fix_rounds:
                def fix_round(db, repo=repo, number=number, issue_repo=issue_repo, issue_num=issue_num, rounds=rounds,
                              url=url, items=items, logs=logs, names=names):
                    gh.comment(repo, number, report(repo, number, url, "failed", items, logs,
                                                    f"Giving the agent a fix round ({rounds + 1} of {cfg.ci.fix_rounds})."))
                    record(db, "failed", repo, number, issue_repo, issue_num, f"failing: {names}")
                    notify(f"CI failed: {repo}#{number} ({names}). Starting fix round {rounds + 1} of {cfg.ci.fix_rounds}.\n{url}", "ci_fix")
                    pushed = fix(repo, number, issue_repo, issue_num, f"Failing checks: {names}\n\n{logs}")
                    if pushed is None:                  # rate limited: try again next time, round not used
                        return
                    dbm.update_pr(db, repo, number, rounds=rounds + 1, watch_started=time.time(),
                                  status="watching" if pushed else "failed", summary="fix pushed" if pushed else "fix produced no change")
                    if not pushed:
                        notify(f"The fix round produced no change for {repo}#{number}. A person should take a look.", "failure")
                submit(issue_repo, issue_num, fix_round)    # busy: left as it is, so the next pass offers the round again
                continue
            dbm.update_pr(conn, repo, number, status="failed", summary=f"failing: {names}")
            record(conn, "failed", repo, number, issue_repo, issue_num, f"failing: {names}")
            gh.comment(repo, number, report(repo, number, url, "failed", items, logs, "No automatic fix rounds left."))
            gh.comment(issue_repo, issue_num, f"CI failed on {url} ({names}). A person should take a look.")
            notify(f"CI failed: {repo}#{number} ({names}). Needs a person.\n{url}", "failure")
        except Exception:
            log.exception("ci watch failed for %s#%s", repo, number)
