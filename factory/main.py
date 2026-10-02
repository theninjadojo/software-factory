import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import ci, pause, runner, subtasks
from . import db as dbm
from .classifier import RuleClassifier
from .config import Config, Route, load, project_for, project_info
from .events import event_enabled
from .github import GitHub
from .jev import JevClassifier
from .roles import MARKER, STAGE_TO_ROLE
from .router import decide
from .sanitize import sanitize_markdown
from .telegram import Telegram

log = logging.getLogger("factory")
WORKING, DONE, FAILED = "factory:working", "factory:pr-open", "factory:failed"
tg: Telegram | None = None
ev = None                                  # sqlite connection for runs/events/status; set in main()
step_gh = None                             # GitHub client used to mirror pipeline steps as sub-issues; None = off
alert_filter = lambda event: True      # set from config in main()


BOILERPLATE = ("Opened https://", "Factory run did not produce", "Rate limited")
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$")
STAGE_HEAD = re.compile(r"<!-- factory:stage=([a-z]+) -->\n")


def working_label(kind: str) -> str:
    """Implementation (and auto-allocated implementation) share one label; each role has its own."""
    return WORKING if kind in ("implement", "auto") else f"factory:working-{kind}"


def triggers(cfg: Config) -> list[tuple[str, str]]:
    """(kind, label), in the order an issue carrying several of them is handled: stages first, then auto, then build."""
    order = [(r.name, r.label) for r in cfg.roles] + [("auto", cfg.auto_label), ("implement", cfg.trigger_label)]
    return order + ([("review", cfg.review.label)] if cfg.review.enabled else [])


def human_comments(gh: GitHub, repo: str, num: int) -> list[str]:
    """Discussion by people (untrusted data). Our own comments are excluded: status lines and stage documents."""
    try:
        me = gh.login()
        return [c["body"] for c in gh.issue_comments(repo, num)
                if c["user"]["login"] != me and not c["body"].startswith(BOILERPLATE)]
    except Exception:
        return []


def stage_outputs(gh: GitHub, repo: str, num: int) -> dict[str, str]:
    """Earlier stage documents on the ticket. Only comments authored by OUR account count: a forged comment
    carrying the marker must not be able to steer a later stage or the implementer."""
    try:
        me = gh.login()
        out: dict[str, str] = {}
        for c in gh.issue_comments(repo, num):          # oldest first, so a re-run replaces the earlier document
            m = STAGE_HEAD.match(c["body"])
            if m and c["user"]["login"] == me:
                out[m.group(1)] = c["body"][m.end():]
        return out
    except Exception:
        return {}


def stages_done(cfg: Config, labels: list[str]) -> list[str]:
    return [r.name for r in cfg.roles if r.done_label in labels]


def picked(route, c) -> str:
    """Human-readable record of what was chosen and by whom, for Telegram."""
    how = (f"classified {c.kind}/{c.complexity}, confidence {c.confidence:.2f}, by {c.source}" if c
           else "approved via Telegram (default medium route)")
    return f"Agent: {route.model}, effort {route.effort} ({route.harness})\n{how}"


def human_buttons(cfg: Config, repo: str, num: int, c, done=()) -> list:
    """What a person can do with a ticket the classifier is unsure about. If it recommended a stage (analyst, designer or
    architect) that is the first button: 'Run' used to mean only 'build', which skipped the recommendation."""
    role = next((r for r in cfg.roles if r.name == STAGE_TO_ROLE.get(c.stage or "") and r.name not in done), None)
    out = ([(f"Run {role.name}", f"stage:{role.name}|{repo}|{num}")] if role else [])
    out += [("Build anyway (medium)" if role else "Run (medium)", f"run|{repo}|{num}"), ("Skip", f"skip|{repo}|{num}")]
    return [b for b in out if len(b[1].encode()) <= 64]          # Telegram rejects callback data over 64 bytes


def suggestion(c) -> str:
    return f"\nJev suggests: {c.stage} first" if c and c.stage else ""


def alert(text: str, buttons=None, event: str = "info") -> None:
    """Send a Telegram message if this event category is enabled by the configured verbosity."""
    sent = bool(tg and alert_filter(event))
    if sent:
        tg.send(text, buttons)
    emit(f"alert:{event}", text + ("" if sent else "  [not sent to Telegram]"))


def emit(kind: str, message: str, repo: str | None = None, issue: int | None = None, run_id: int | None = None) -> None:
    """Append to the timeline the UI shows. Observability must never break the orchestrator."""
    if ev is None:
        return
    try:
        dbm.add_event(ev, kind, message, repo, issue, run_id)
    except Exception:
        log.exception("could not record event")


def begin_run(kind: str, repo: str, issue: dict, route, c=None, stage: str | None = None):
    if ev is None:
        return None
    try:
        cls = json.dumps({"kind": c.kind, "complexity": c.complexity, "stage": c.stage, "effort": c.effort,
                          "needs_human": c.needs_human, "confidence": round(c.confidence, 3), "source": c.source}) if c else ""
        run_id = dbm.start_run(ev, kind, repo, issue["number"], issue.get("title", ""), route.harness, route.model, route.effort, cls, stage)
        dbm.set_status(ev, "running", str(run_id))
        emit("run:start", f"{kind}{' ' + stage if stage else ''} started with {route.model} ({route.effort})", repo, issue["number"], run_id)
        if step_gh is not None:
            subtasks.sync(step_gh, ev, repo, issue["number"], dbm.step_of(kind, stage))
        return run_id
    except Exception:
        log.exception("could not record run start")
        return None


def end_run(run_id, res, sink: dict) -> None:
    if ev is None or run_id is None:
        return
    try:
        dbm.finish_run(ev, run_id, res.status, res.detail, res.pr_url or "", res.output, sink.get("log", ""))
        dbm.set_status(ev, "running", "")
        emit(f"run:{res.status}", res.detail[:300], None, None, run_id)
        if step_gh is not None:
            r = dbm.get_run(ev, run_id)
            subtasks.sync(step_gh, ev, r["repo"], r["issue"], dbm.step_of(r["kind"], r["stage"]))
    except Exception:
        log.exception("could not record run end")


def maybe_restart(state_dir: Path) -> None:
    """Settings saved in the UI take effect by re-executing at an idle moment, never in the middle of a task."""
    marker = state_dir / "RESTART"
    if marker.exists():
        marker.unlink(missing_ok=True)
        emit("restart", "settings changed: restarting to apply them")
        log.warning("RESTART marker found: re-executing to apply new settings")
        os.execv(sys.executable, [sys.executable, "-m", "factory.main", *sys.argv[1:]])


def trusted(cfg: Config, gh: GitHub, repo: str, num: int, label: str | None = None) -> tuple[bool, str]:
    """Fail closed: act only if a user with write access applied the trigger label."""
    label = label or cfg.trigger_label
    if not gh.token:
        return False, "no token: cannot verify who applied the label"
    actor = gh.label_actor(repo, num, label)
    if actor is None:
        return False, "no label event found"
    perm = gh.permission(repo, actor)
    if perm not in cfg.trusted_permissions:
        return False, f"label applied by {actor} with permission '{perm}'"
    return True, f"label applied by {actor} ({perm})"


def claim(gh: GitHub, repo: str, num: int, trigger_label: str, kind: str) -> None:
    gh.remove_label(repo, num, trigger_label)       # prevents re-dispatch
    for stale in (DONE, FAILED):                    # clear state left by an earlier attempt
        gh.remove_label(repo, num, stale)
    gh.add_labels(repo, num, [working_label(kind)])


def requeue_rate_limited(cfg: Config, gh: GitHub, repo: str, num: int, trigger_label: str, kind: str) -> None:
    gh.add_labels(repo, num, [trigger_label])       # no comment, no failure label
    pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds)
    log.warning("rate limited: requeued %s#%d, backing off %ds", repo, num, cfg.runner.rate_limit_backoff_seconds)
    alert(f"Rate limited on {repo}#{num}. Requeued; pausing {cfg.runner.rate_limit_backoff_seconds // 60} min.", event="rate_limit")


def dispatch(cfg: Config, gh: GitHub, repo: str, issue: dict, route, c=None,
             trigger_label: str | None = None, conn=None) -> runner.RunResult:
    """Build it: edit the repos in the sandbox and open PRs."""
    num, trigger_label = issue["number"], trigger_label or cfg.trigger_label
    alert(f"Starting {repo}#{num}: {issue['title'][:80]}\n{picked(route, c)}", event="started")
    claim(gh, repo, num, trigger_label, "implement")
    run_id, sink = begin_run("build", repo, issue, route, c), {}
    res = runner.run_task(cfg, gh, repo, issue, route, prior=stage_outputs(gh, repo, num),
                          comments=human_comments(gh, repo, num), sink=sink)
    end_run(run_id, res, sink)
    gh.remove_label(repo, num, WORKING)
    if res.status == "rate-limited":
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, "implement")
    elif res.status == "pr":
        gh.add_labels(repo, num, [DONE])
        gh.comment(repo, num, f"Opened {res.pr_url} for review.")
        if conn is not None and cfg.ci.enabled:
            for u in res.pr_url.split():                  # watch each PR's CI; skip anything that is not a PR URL
                m = PR_URL.match(u)
                if m:
                    dbm.watch_pr(conn, m.group(1), int(m.group(2)), repo, num)
        alert(f"PR ready: {repo}#{num}\n{res.pr_url}\n{picked(route, c)}", event="pr_ready")
        if cfg.review.enabled and cfg.review.auto:
            prs = [(m.group(1), int(m.group(2))) for m in map(PR_URL.match, res.pr_url.split()) if m]
            try:
                review_changes(cfg, gh, repo, issue, prs)
            except Exception:
                log.exception("automatic review failed")           # a review problem must never undo a finished build
    else:
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, f"Factory run did not produce a PR ({res.status}). A person should take a look.")
        alert(f"Run did not produce a PR: {repo}#{num} ({res.status})\n{res.detail[:300]}", event="failure")
    return res


def next_hint(cfg: Config, c) -> str | None:
    if c is None:
        return None
    if c.needs_human:
        return "a person's answers to the open questions"
    role = next((r for r in cfg.roles if r.name == STAGE_TO_ROLE.get(c.stage or "")), None)
    if role:
        return f"{role.name}: apply `{role.label}`"
    if c.stage == "implement":
        return f"implement: apply `{cfg.trigger_label}`"
    return None


def design_section(files: list, notes: str) -> str:
    """Links to the design files, built here from validated values (never from agent text)."""
    out = ""
    if files:
        prs = sorted({f["pr"] for f in files if PR_URL.match(f["pr"])})
        out += "\n\n### Design files\n" + (f"Draft PR: {prs[0]}\n\n" if prs else "")
        out += "\n".join(f"- [`{f['path']}`]({f['url']})" for f in files if f["url"].startswith("https://github.com/"))
        out += ("\n\nStatic Claude Design canvases. Import a file into Claude Design to work on it, or merge the draft PR to keep them "
                "with the repository.")
    if notes:
        out += f"\n\n_Note: {sanitize_markdown(notes)}_"
    return out


def stage_comment(role, route, text: str, hint: str | None, files: list | None = None, notes: str = "") -> str:
    body = (MARKER.format(name=role.name) + "\n"
            f"### {role.name.title()} (model: {route.model}, effort: {route.effort})\n\n"
            + sanitize_markdown(text) + design_section(files or [], notes)
            + "\n\n---\n_Generated by the software factory from this ticket and the code. A draft: verify before relying on it._")
    if hint:
        body += f"\n_Suggested next: **{hint}**_"
    return body


def dispatch_stage(cfg: Config, gh: GitHub, classifier, repo: str, issue: dict, role, c=None,
                   trigger_label: str | None = None, chain: bool = False) -> runner.RunResult:
    """Run an analyst, designer or architect and put its document on the ticket."""
    num, trigger_label = issue["number"], trigger_label or role.label
    route = Route(role.harness, role.model, role.effort)
    alert(f"Starting {role.name}: {repo}#{num}: {issue['title'][:80]}\nAgent: {route.model}, effort {route.effort}"
          + (f"\nchosen by {c.source} (stage {c.stage}, confidence {c.stage_confidence:.2f})" if c and c.stage_confidence else ""), event="started")
    claim(gh, repo, num, trigger_label, role.name)
    comments = human_comments(gh, repo, num)
    run_id, sink = begin_run("stage", repo, issue, route, c, role.name), {}
    res = runner.run_task(cfg, gh, repo, issue, route, role=role.name, prior=stage_outputs(gh, repo, num), comments=comments, sink=sink)
    end_run(run_id, res, sink)
    gh.remove_label(repo, num, working_label(role.name))
    if res.status == "rate-limited":
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, role.name)
    elif res.status == "stage":
        labels = [lb["name"] for lb in gh.get_issue(repo, num).get("labels", [])] + [role.done_label]
        hint, go_on = None, False
        try:                                            # ask Jev what should happen next, now that this stage is done
            nxt = classifier.classify(issue["title"], issue.get("body") or "", labels,
                                      comments + ["(just completed) " + res.output[:3000]],
                                      project_info(project_for(cfg, repo), repo), stages_done(cfg, labels))
            hint = next_hint(cfg, nxt)
            # Keep going only when nothing needs a person (open questions in the document make the classifier say so) and it
            # named a real next step. The re-applied label goes through the normal auto gate, which still asks before a build it
            # is unsure about. Stages already done are never chosen again, so a chain always ends.
            go_on = bool(chain and cfg.auto_chain and not nxt.needs_human and (nxt.stage in STAGE_TO_ROLE or nxt.stage == "implement"))
        except Exception:
            log.exception("next-stage suggestion failed")
        url = gh.comment(repo, num, stage_comment(role, route, res.output, hint, res.files, res.notes))
        gh.add_labels(repo, num, [role.done_label])
        if go_on:
            gh.add_labels(repo, num, [cfg.auto_label])             # picked up on the next poll, like any auto request
        alert(f"{role.name.title()} done: {repo}#{num}\n{url}" + (f"\n{len(res.files)} design file(s): {res.pr_url}" if res.files else "")
              + ("\nContinuing automatically: " + (hint or "next stage") if go_on else (f"\nWaiting for you: {hint}" if hint else "")),
              event="stage_done")
    else:
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, f"Factory {role.name} run did not produce a document ({res.status}). A person should take a look.")
        alert(f"{role.name.title()} failed: {repo}#{num} ({res.status})\n{res.detail[:300]}", event="failure")
    return res


def review_comment(route, text: str) -> str:
    return ("<!-- factory:review -->\n"
            f"### Automated code review (model: {route.model}, effort: {route.effort})\n\n" + sanitize_markdown(text)
            + "\n\n---\n_A draft review by an agent. It never approves or requests changes: a person decides. Verify before relying on it._")


def review_changes(cfg: Config, gh: GitHub, repo: str, issue: dict, prs: list, trigger_label: str | None = None) -> runner.RunResult | None:
    """Independent review of the factory's PR branches for a ticket. Posts a comment on each PR; changes nothing else."""
    if not prs:
        return None
    num = issue["number"]
    try:
        first_repo, first_num = prs[0]
        branch = gh.get_pr(first_repo, first_num)["head"]["ref"]
    except Exception:
        log.exception("review: could not read the PR branch")
        return None
    if not branch.startswith("factory/"):                     # never review (or check out) a branch the factory did not create
        return None
    route = Route(cfg.review.harness, cfg.review.model, cfg.review.effort)
    alert(f"Reviewing {repo}#{num}: {len(prs)} PR(s)\nReviewer: {route.model}, effort {route.effort} ({route.harness})", event="started")
    run_id, sink = begin_run("review", repo, issue, route, None, "reviewer"), {}
    res = runner.run_task(cfg, gh, repo, issue, route, role="reviewer", prior=stage_outputs(gh, repo, num),
                          comments=human_comments(gh, repo, num), fix_branch=branch, sink=sink)
    end_run(run_id, res, sink)
    if res.status == "rate-limited":
        if trigger_label:
            requeue_rate_limited(cfg, gh, repo, num, trigger_label, "review")
        else:
            alert(f"Review of {repo}#{num} hit a rate limit. Apply `{cfg.review.label}` to the ticket to retry.", event="rate_limit")
    elif res.status == "stage":
        body = review_comment(route, res.output)
        links = []
        for r, n in prs:
            try:
                gh.comment(r, n, body)
                links.append(f"https://github.com/{r}/pull/{n}")
            except Exception:
                log.exception("could not post the review on %s#%s", r, n)
        gh.add_labels(repo, num, [cfg.review.done_label])
        verdict = next((v for v in ("Blocking issues", "Needs changes", "Looks good") if v.lower() in res.output[:600].lower()), "see the PR")
        alert(f"Review done: {repo}#{num}: {verdict}\n" + "\n".join(links), event="review_done")
    else:
        alert(f"Review failed: {repo}#{num} ({res.status})\n{res.detail[:300]}", event="failure")
    return res


def recover(cfg: Config, gh: GitHub) -> None:
    """Run once at startup. The single-instance lock means anything left over is from a run that died
    (crash, restart): clear its leftovers and requeue the issue so work is never silently stranded."""
    work = Path(cfg.runner.work_dir)
    if work.exists():
        for d in work.iterdir():
            shutil.rmtree(d, ignore_errors=True)
    eng = cfg.runner.engine                      # validated to be podman or docker when the config is loaded
    # Anchored: sandboxes are named factory-<issue>-<time>. An unanchored match would also catch containers like
    # "software-factory-orchestrator-1" and remove the factory itself.
    subprocess.run(f"{eng} ps -aq --filter name=^factory- | xargs -r {eng} rm -f", shell=True,
                   capture_output=True, timeout=60)
    requeue = {WORKING: cfg.trigger_label, **{working_label(r.name): r.label for r in cfg.roles},
               **({working_label("review"): cfg.review.label} if cfg.review.enabled else {})}
    for repo in cfg.repos:
        try:
            for working, trigger in requeue.items():
                for issue in gh.labeled_issues(repo, working):
                    num = issue["number"]
                    gh.remove_label(repo, num, working)
                    gh.add_labels(repo, num, [trigger])
                    log.warning("recovered interrupted run: %s#%d requeued", repo, num)
                    alert(f"Recovered an interrupted run: {repo}#{num} requeued.", event="recovery")
        except Exception:
            log.exception("recovery failed for %s", repo)


def run_fix(cfg: Config, gh: GitHub, repo: str, number: int, issue_repo: str, issue_num: int, failures: str):
    """One agent attempt to fix failing CI on a factory PR branch. True = pushed a change, False = did not, None = retry later."""
    if pause.paused(Path(cfg.db_path).parent):
        return None
    pr = gh.get_pr(repo, number)
    branch = pr["head"]["ref"]
    if not branch.startswith("factory/") or pr["head"]["repo"]["full_name"] != repo:
        return False                                     # never touch a branch the factory did not create
    issue = gh.get_issue(issue_repo, issue_num)
    run_id, sink = begin_run("fix", issue_repo, issue, cfg.routes["medium"]), {}
    res = runner.run_task(cfg, gh, issue_repo, issue, cfg.routes["medium"], prior=stage_outputs(gh, issue_repo, issue_num),
                          comments=human_comments(gh, issue_repo, issue_num), fix_branch=branch, failures=failures, sink=sink)
    end_run(run_id, res, sink)
    if res.status == "rate-limited":
        pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds)
        alert(f"Rate limited during a CI fix for {repo}#{number}; pausing.", event="rate_limit")
        return None
    if res.status in ("failed", "rejected"):
        alert(f"CI fix round failed for {repo}#{number}: {res.status}\n{res.detail[:200]}", event="failure")
    return res.status == "pr"


def process_approvals(cfg: Config, gh: GitHub, conn, classifier=None) -> None:
    """Run/Skip decisions made by the allowlisted Telegram chat."""
    if pause.paused(Path(cfg.db_path).parent):
        return
    for repo, num, action in dbm.pop_approvals(conn):
        try:
            if action == "skip":
                for _, label in triggers(cfg):
                    gh.remove_label(repo, num, label)
                alert(f"Skipped {repo}#{num}", event="skipped")
                continue
            issue = gh.get_issue(repo, num)
            if issue["state"] != "open" or "pull_request" in issue:
                continue
            role = next((r for r in cfg.roles if action == f"stage:{r.name}"), None)
            if action != "run" and role is None:
                continue                            # an unknown action is ignored, never guessed at
            for _, label in triggers(cfg):         # the approval covers the whole ticket: clear EVERY trigger label (factory:auto
                gh.remove_label(repo, num, label)  # included), or finishing the run re-triages it and asks a person again
            if role:
                res = dispatch_stage(cfg, gh, classifier, repo, issue, role)
            else:
                res = dispatch(cfg, gh, repo, issue, cfg.routes["medium"], conn=conn)
            dbm.record(conn, repo, num, issue["updated_at"] + "+approved", f"run:{res.status}", f"approved via telegram; {res.detail}; {res.pr_url or ''}")
        except Exception:
            log.exception("approval failed for %s#%s", repo, num)


def handle_issue(cfg: Config, gh: GitHub, conn, classifier, repo: str, issue: dict, kind: str, label: str) -> str | None:
    """One labeled issue. Returns "paused" if the orchestrator is paused and should stop polling."""
    num, updated = issue["number"], issue["updated_at"]
    if dbm.seen(conn, repo, num, updated):
        return None
    ok, why = trusted(cfg, gh, repo, num, label)
    if not ok:
        dbm.record(conn, repo, num, updated, "ignored", why)
        emit("decision", f"ignored: {why}", repo, num)
        log.info("%s#%d ignored: %s", repo, num, why)
        return None
    if not cfg.dry_run and (why_paused := pause.paused(Path(cfg.db_path).parent)):
        log.info("paused (%s); leaving %s#%d queued", why_paused, repo, num)
        return "paused"
    if kind == "review":
        prs = dbm.prs_for_issue(conn, repo, num)
        if cfg.dry_run:
            dbm.record(conn, repo, num, updated, "review", f"{why}; would review {len(prs)} PR(s) [dry-run]")
            emit("decision", f"dry-run: would review {len(prs)} PR(s)", repo, num)
            return None
        claim(gh, repo, num, label, "review")
        if prs:
            res = review_changes(cfg, gh, repo, issue, prs, label)
            outcome = res.status if res else "skipped"
        else:
            gh.comment(repo, num, "No open factory pull requests were found for this ticket, so there is nothing to review.")
            outcome = "no-prs"
        gh.remove_label(repo, num, working_label("review"))
        dbm.record(conn, repo, num, updated, f"run:{outcome}", f"{why}; review of {len(prs)} PR(s)")
        return None
    labels = [lb["name"] for lb in issue.get("labels", [])]
    done = stages_done(cfg, labels)
    seen_by_classifier = human_comments(gh, repo, num)
    if kind == "auto":                                  # earlier stage documents (and the open questions in them) inform the next call
        seen_by_classifier += [f"({k} document written by the factory) {v[:2500]}" for k, v in stage_outputs(gh, repo, num).items()]
    c = classifier.classify(issue["title"], issue.get("body") or "", labels, seen_by_classifier,
                            project_info(project_for(cfg, repo), repo), done)
    summary = f"cls={c.kind}/{c.complexity}/human={c.needs_human}/conf={c.confidence:.2f}/stage={c.stage}"
    role = next((r for r in cfg.roles if r.name == kind), None)       # explicit stage label: the human chose it

    if kind == "auto":
        st = STAGE_TO_ROLE.get(c.stage or "")
        sure = min(c.confidence, c.stage_confidence if c.stage_confidence is not None else c.confidence) >= cfg.confidence_threshold
        # A read-only stage (analyst, designer, architect) only writes a document on the ticket, and low confidence or "needs a person"
        # is exactly why an analyst is the right next step, so it runs without asking. A person is asked before a BUILD, or when
        # the classifier named no stage, or a stage that is already done.
        read_only_pick = bool(st) and st not in done and not cfg.auto_confirm_stages
        if not read_only_pick and (c.needs_human or not sure or (st and st in done)):
            reason = "needs a person" if c.needs_human else "low confidence" if not sure else "chose a stage already done"
            dbm.record(conn, repo, num, updated, "human", f"{why}; {summary}; {reason}")
            emit("decision", f"needs a person: {reason} ({summary})", repo, num)
            log.info("%s#%d auto -> human (%s)", repo, num, reason)
            if not cfg.dry_run:
                alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {reason}\n"
                      f"Classified {c.kind}/{c.complexity}, stage {c.stage}, confidence {c.confidence:.2f}, by {c.source}" + suggestion(c),
                      human_buttons(cfg, repo, num, c, done), event="needs_human")
            return None
        role = next((r for r in cfg.roles if r.name == st), None)

    if role:
        detail = f"{why}; {summary}; stage={role.name}"
        if cfg.dry_run:
            dbm.record(conn, repo, num, updated, "stage", detail)
            emit("decision", f"dry-run: would run {role.name} ({summary})", repo, num)
            log.info("%s#%d -> stage %s [dry-run]", repo, num, role.name)
            return None
        res = dispatch_stage(cfg, gh, classifier, repo, issue, role, c if kind == "auto" else None, label, chain=(kind == "auto"))
        dbm.record(conn, repo, num, updated, f"run:{res.status}", f"{detail}; {res.detail}")
        log.info("%s#%d stage %s: %s", repo, num, role.name, res.status)
        return None

    d = decide(cfg, c)
    detail = f"{why}; {d.reason}; {summary}; route={d.route}"
    if d.action == "dispatch" and not cfg.dry_run:
        res = dispatch(cfg, gh, repo, issue, d.route, c, label, conn)
        dbm.record(conn, repo, num, updated, f"run:{res.status}", f"{detail}; {res.detail}; {res.pr_url or ''}")
        log.info("%s#%d run %s: %s %s", repo, num, res.status, res.detail, res.pr_url or "")
    else:
        dbm.record(conn, repo, num, updated, d.action, detail)
        emit("decision", f"{d.action}: {d.reason} ({summary}){' [dry-run]' if cfg.dry_run else ''}", repo, num)
        log.info("%s#%d -> %s (%s)%s", repo, num, d.action, detail, " [dry-run]" if cfg.dry_run else "")
        if d.action == "human" and not cfg.dry_run:
            alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {d.reason}\n"
                  f"Classified {c.kind}/{c.complexity}, human={c.needs_human}, confidence {c.confidence:.2f}, by {c.source}" + suggestion(c),
                  human_buttons(cfg, repo, num, c, done), event="needs_human")
    return None


def poll_once(cfg: Config, gh: GitHub, conn, classifier) -> None:
    if not cfg.dry_run:
        process_approvals(cfg, gh, conn, classifier)
    for repo in cfg.repos:
        handled: set[int] = set()
        for kind, label in triggers(cfg):
            for issue in gh.labeled_issues(repo, label):
                if issue["number"] in handled:            # another trigger label on it is handled first; this one waits
                    continue
                handled.add(issue["number"])
                if handle_issue(cfg, gh, conn, classifier, repo, issue, kind, label) == "paused":
                    return
    if not cfg.dry_run:
        ci.watch_ci(cfg, gh, conn, lambda text, event="ci_result": alert(text, event=event),
                    lambda *a: run_fix(cfg, gh, *a))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/srv/factory/config.toml")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load(args.config)
    token = Path(cfg.token_file).read_text().strip() if cfg.token_file and Path(cfg.token_file).exists() else None
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    if not args.once:   # one orchestrator at a time
        import fcntl
        lock = open(Path(cfg.db_path).parent / "lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("another orchestrator is already running")
    clf = RuleClassifier(cfg.kind_aliases)
    if cfg.classifier_backend == "jev" and cfg.openrouter_key_file and Path(cfg.openrouter_key_file).exists():
        clf = JevClassifier(Path(cfg.openrouter_key_file).read_text().strip(), cfg.jev_model, kind_aliases=cfg.kind_aliases)
    log.info("classifier: %s", type(clf).__name__)
    global alert_filter
    alert_filter = lambda event: event_enabled(cfg.telegram_verbosity, event, cfg.telegram_events)
    conn, gh = dbm.connect(cfg.db_path), GitHub(token)
    global ev
    ev = conn
    global step_gh
    step_gh = gh if cfg.subtasks.enabled and not cfg.dry_run and token else None
    if not args.once:
        stranded = dbm.mark_interrupted(conn)
        emit("startup", f"orchestrator started ({'dry-run' if cfg.dry_run else 'LIVE'}), {len(cfg.repos)} repo(s)"
                        + (f"; {stranded} run(s) were interrupted by the restart" if stranded else ""))
    global tg
    tf = cfg.telegram_token_file
    if tf and cfg.telegram_chat_id and Path(tf).exists() and not args.once:
        tg = Telegram(Path(tf).read_text().strip(), cfg.telegram_chat_id, str(Path(cfg.db_path).parent),
                      cfg.db_path, cfg.repos)
        tg.start()
        alert(f"Factory started ({'dry-run' if cfg.dry_run else 'LIVE'}). {len(cfg.repos)} repo(s). /help", event="startup")
    if not args.once and not cfg.dry_run:
        recover(cfg, gh)
    while True:
        if not args.once:
            maybe_restart(Path(cfg.db_path).parent)
        try:
            dbm.set_status(conn, "mode", "dry-run" if cfg.dry_run else "LIVE")
            dbm.set_status(conn, "paused", pause.paused(Path(cfg.db_path).parent) or "")
            dbm.set_status(conn, "poll_started", str(time.time()))
            poll_once(cfg, gh, conn, clf)
            dbm.set_status(conn, "last_poll_ok", str(time.time()))
            dbm.set_status(conn, "last_error", "")
        except Exception as e:
            log.exception("poll failed")
            try:
                dbm.set_status(conn, "last_error", f"{type(e).__name__}: {str(e)[:300]}")
                emit("error", f"poll failed: {type(e).__name__}: {str(e)[:300]}")
            except Exception:
                pass
        if args.once:
            return
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
