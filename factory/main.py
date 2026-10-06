import argparse
import calendar
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import why as W
from . import backup, captures, chat, ci, conflicts, designfiles, designlinks, jobs, mockups, pause, pm, reviewnotes, runner, scanner, schedules, screenboard, subtasks, tools, tracker, triage, usage, verify
from . import questions as Q
from . import db as dbm
from .classifier import RuleClassifier
from .config import Config, Route, chain, load, project_for, project_info, resource_warning
from .events import event_enabled
from .github import GitHub
from .jev import JevClassifier
from .pool import Pool, key as jobkey
from .roles import MARKER, STAGE_TO_ROLE
from .router import behind, decide, pick_stage
from .sanitize import sanitize_markdown
from .slack import Slack
from .telegram import Telegram

log = logging.getLogger("factory")
WORKING, DONE, FAILED = "factory:working", "factory:pr-open", "factory:failed"
tg: Telegram | None = None
sl: Slack | None = None                    # Slack works alongside Telegram: an alert goes to each channel whose verbosity allows it
ev = None                                  # sqlite connection for runs/events/status when ev_path is not set (tests)
ev_path: str | None = None                 # set in main(): each thread opens its own connection to this database
step_gh = None                             # GitHub client used to mirror pipeline steps as sub-issues; None = off
alert_filter = lambda event: True      # set from config in main(); read-only afterwards, so safe from every thread
slack_filter = lambda event: True      # the same for Slack
pool = Pool()                              # inline (one job at a time, in the caller's thread); main() starts worker threads
queued: list[dict] = []                    # tickets the last poll left waiting for a free slot (or, with a reason, blocked), for the UI
held: dict[tuple[str, int], tuple] = {}    # poll thread only: (repo, ticket) -> its open blockers, to report each change once
cycles_seen: set = set()                   # poll thread only: (repo, tickets) of each blocker cycle already reported


BOILERPLATE = ("Opened https://", "Factory run did not produce", "Rate limited", "The factory's ")
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$")
STAGE_HEAD = Q.STAGE_HEAD


def working_label(kind: str) -> str:
    """Implementation (and auto-allocated implementation) share one label; each role has its own."""
    return WORKING if kind in ("implement", "auto") else f"factory:working-{kind}"


def triggers(cfg: Config) -> list[tuple[str, str]]:
    """(kind, label), in the order an issue carrying several of them is handled: stages first, then auto, then build."""
    order = [(r.name, r.label) for r in cfg.roles] + [("auto", cfg.auto_label), ("implement", cfg.trigger_label)]
    return (order + ([("review", cfg.review.label)] if cfg.review.enabled else [])
            + ([("conflicts", cfg.conflicts.label)] if cfg.conflicts.enabled else []))


PRIORITY_RANK = {"priority: high": 0, "priority: low": 2}   # no label = 1 (normal)


def priority_rank(issue: dict) -> int:
    """Pickup order from a `priority: high` / `priority: low` label (lower first). It only orders issues that are
    already eligible; it never makes one eligible. With both labels the higher priority wins."""
    ranks = [PRIORITY_RANK[n] for lb in issue.get("labels", []) if (n := lb.get("name", "").strip().lower()) in PRIORITY_RANK]
    return min(ranks, default=1)


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
    carrying the marker must not be able to steer a later stage or the implementer. A document written before the ticket was
    sent back to its stage (or an earlier one) is kept, as `<stage>-before-redirect`, until the stage writes a new one."""
    try:
        me = gh.login()
        out: dict[str, str] = {}
        for c in gh.issue_comments(repo, num):          # oldest first, so a re-run replaces the earlier document
            if c["user"]["login"] != me:
                continue
            if (m := STAGE_HEAD.match(c["body"])):
                out.pop(f"{m.group(1)}-before-redirect", None)
                out[m.group(1)] = Q.read_stored(c["body"][m.end():])[1]       # without the questions data line
            elif (m := Q.REDIRECT_HEAD.match(c["body"])):
                for name in m.group(1).split(","):
                    if name in out:
                        out[f"{name}-before-redirect"] = SUPERSEDED_NOTE.format(stage=m.group(1).split(",")[0]) + out.pop(name)
        return out
    except Exception:
        return {}


def question_state(gh: GitHub, repo: str, num: int) -> list:
    """Each stage's structured open questions with the answers a person gave (only our own account's comments count)."""
    try:
        return Q.from_comments(gh.issue_comments(repo, num), gh.login())
    except Exception:
        log.exception("could not read the open questions of %s#%s", repo, num)
        return []


def stages_done(cfg: Config, labels: list[str]) -> list[str]:
    return [r.name for r in cfg.roles if r.done_label in labels]


SUPERSEDED_NOTE = "(Written before the ticket was sent back to the {stage} stage, so it may be outdated.)\n\n"
REDIRECT_COMMENT = ("Sent back to the {stage} stage {source}. The steps from there on run again. The earlier documents "
                    "and answers are kept, and the next agents see them as possibly outdated.")
SUPERSEDED_PR = ("This pull request is superseded: its ticket was sent back to the {stage} stage, and a new pull request will follow. "
                 "The factory no longer watches it. A person can close it, or keep it if it is still useful.")


def redirect(cfg: Config, gh: GitHub, conn, repo: str, num: int, role, source: str = "from the factory admin UI") -> None:
    """Send a ticket back to a stage (a person's choice in the admin UI, or a comment triage a person confirmed). The done labels of that stage and every later one come off,
    with the build's, so the stages run again and the auto chain can go forward from there (router.behind no longer stops it).
    Nothing is deleted: documents, answers, runs and design files stay, and a marker comment makes stage_outputs pass the earlier
    documents on as possibly outdated. The ticket's open factory PRs for the steps being redone stay open for a person to close;
    the factory stops watching them. Trigger labels are cleared by the caller (process_approvals)."""
    names = [r.name for r in cfg.roles]
    redo = names[names.index(role.name):]
    held = {lb["name"] for lb in gh.get_issue(repo, num).get("labels", [])}
    drop = [r.done_label for r in cfg.roles if r.name in redo] + [DONE, FAILED, Q.NEEDS_ANSWERS, cfg.review.done_label]
    if "designer" in redo:                              # new screens need a new approval
        drop.append(cfg.mockups.approve_label)
    removed = [lb for lb in dict.fromkeys(drop) if lb in held]
    for lb in removed:
        gh.remove_label(repo, num, lb)
    keep = set() if "designer" in redo else dbm.design_prs(conn, repo, num)
    superseded = dbm.supersede_prs(conn, repo, num, keep)
    dbm.set_questions(conn, repo, num, role.name, 0)
    for r, n in superseded:
        try:
            gh.comment(r, n, SUPERSEDED_PR.format(stage=role.name))
        except Exception:
            log.warning("could not comment on the superseded PR %s#%s", r, n)
    gh.comment(repo, num, Q.REDIRECT.format(stages=",".join(redo)) + REDIRECT_COMMENT.format(stage=role.name, source=source))
    emit("decision", f"sent back to the {role.name} {source}; removed {', '.join(removed) or 'no labels'}; "
                     f"{len(superseded)} PR(s) superseded", repo, num)


def picked(route, c) -> str:
    """Human-readable record of what was chosen and by whom, for the chat message."""
    how = (f"classified {c.kind}/{c.complexity}, confidence {c.confidence:.2f}, by {c.source}" if c
           else "approved from chat (default medium route)")
    return f"Agent: {route.model}, effort {route.effort} ({route.harness})\n{how}"


def human_buttons(cfg: Config, repo: str, num: int, c, done=()) -> list:
    """What a person can do with a ticket the classifier is unsure about. If it recommended a stage (analyst, designer or
    architect) that is the first button: 'Run' used to mean only 'build', which skipped the recommendation."""
    role = next((r for r in cfg.roles if r.name == STAGE_TO_ROLE.get(c.stage or "") and r.name not in done), None)
    out = ([(f"Run {role.name}", f"stage:{role.name}|{repo}|{num}")] if role else [])
    out += [("Build anyway (medium)" if role else "Run (medium)", f"run|{repo}|{num}"), ("Skip", f"skip|{repo}|{num}")]
    return [b for b in out if len(b[1].encode()) <= 64]          # Telegram rejects callback data over 64 bytes (Slack allows more, but one message serves both)


def suggestion(c) -> str:
    return f"\nJev suggests: {c.stage} first" if c and c.stage else ""


def alert(text: str, buttons=None, event: str = "info") -> None:
    """Send a chat message (Telegram and/or Slack) to each channel whose configured verbosity enables this event category."""
    sent = False
    for chat, allowed in ((tg, alert_filter), (sl, slack_filter)):
        if chat and allowed(event):
            chat.send(text, buttons)
            sent = True
    emit(f"alert:{event}", text + ("" if sent else "  [not sent]"))


def _ev():
    """The observability connection for the calling thread (a sqlite connection must not cross threads)."""
    return dbm.local(ev_path) if ev_path else ev


def emit(kind: str, message: str, repo: str | None = None, issue: int | None = None, run_id: int | None = None) -> None:
    """Append to the timeline the UI shows. Observability must never break the orchestrator."""
    try:
        db = _ev()
        if db is not None:
            dbm.add_event(db, kind, message, repo, issue, run_id)
    except Exception:
        log.exception("could not record event")


def begin_run(kind: str, repo: str, issue: dict, route, c=None, stage: str | None = None):
    try:
        db = _ev()
        if db is None:
            return None
        cls = json.dumps({"kind": c.kind, "complexity": c.complexity, "stage": c.stage, "effort": c.effort,
                          "needs_human": c.needs_human, "confidence": round(c.confidence, 3), "source": c.source}) if c else ""
        run_id = dbm.start_run(db, kind, repo, issue["number"], issue.get("title", ""), route.harness, route.model, route.effort, cls, stage)
        emit("run:start", f"{kind}{' ' + stage if stage else ''} started with {route.model} ({route.effort})", repo, issue["number"], run_id)
        if step_gh is not None:
            subtasks.sync(step_gh, db, repo, issue["number"], dbm.step_of(kind, stage))
        return run_id
    except Exception:
        log.exception("could not record run start")
        return None


def end_run(run_id, res, sink: dict) -> None:
    if run_id is None:
        return
    try:
        db = _ev()
        dbm.finish_run(db, run_id, res.status, res.detail, res.pr_url or "", res.output, sink.get("log", ""), sink.get("usage"))
        dbm.add_design_files(db, run_id, getattr(res, "files", None) or [])
        dbm.add_run_images(db, run_id, getattr(res, "images", None) or [])
        emit(f"run:{res.status}", res.detail[:300], None, None, run_id)
        if step_gh is not None:
            r = dbm.get_run(db, run_id)
            subtasks.sync(step_gh, db, r["repo"], r["issue"], dbm.step_of(r["kind"], r["stage"]))
    except Exception:
        log.exception("could not record run end")


def start(cfg: Config, conn, repo: str, num: int, kind: str, job, gh: GitHub | None = None) -> None:
    """Hand job(db) for ticket repo#num to the pool. The caller has checked pool.can_take. A job running inline uses conn;
    one on a worker thread uses that thread's own connection to the database. With gh, a job that crashes leaves the ticket
    marked failed (stranded) instead of stopping with nothing to show."""
    def run():
        db = conn if pool.inline else dbm.local(cfg.db_path)
        try:
            job(db)
        except Exception as e:
            if gh is None:
                raise
            log.exception("job for %s#%s failed", repo, num)
            stranded(gh, repo, num, kind, e)
    pool.submit(jobkey(repo, num), kind, run)


STRANDED = ("The factory's {kind} run stopped with an error before it could finish ({error}). Nothing more will happen on its own: "
            "a person should take a look, then apply the label again to retry.")


def stranded(gh: GitHub, repo: str, num: int, kind: str, error: Exception) -> None:
    """A job crashed (often GitHub timing out). If the run had claimed the ticket (its working label is still on), mark it failed
    with a fixed comment and an alert. If GitHub cannot be reached even for that, the working label stays and the next start's
    recovery requeues the ticket. A crash before the claim leaves the trigger label on, so the next poll simply tries again."""
    working = WORKING if kind == "build" else working_label(kind)
    try:
        if working not in [lb["name"] for lb in gh.get_issue(repo, num).get("labels", [])]:
            return
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, STRANDED.format(kind=kind, error=type(error).__name__))
        gh.remove_label(repo, num, working)
    except Exception:
        log.exception("could not mark %s#%s failed after its job crashed", repo, num)
        return
    emit("run:crashed", f"{kind} job crashed ({type(error).__name__}): marked failed", repo, num)
    alert(f"Run crashed: {repo}#{num} ({kind}, {type(error).__name__}). Marked failed.", event="failure")


def maybe_restart(state_dir: Path) -> None:
    """Settings saved in the UI take effect by re-executing at an idle moment, never in the middle of a task: with jobs
    running, the pool stops taking new ones and the restart waits until the running ones have finished."""
    marker = state_dir / "RESTART"
    if not marker.exists():
        pool.drain(False)                   # the marker was withdrawn while waiting: take jobs again
        return
    if not pool.idle():
        if not pool.draining:
            pool.drain()
            emit("restart", f"settings changed: restarting once {len(pool.running())} running job(s) finish")
        return
    marker.unlink(missing_ok=True)
    emit("restart", "settings changed: restarting to apply them")
    log.warning("RESTART marker found: re-executing to apply new settings")
    os.execv(sys.executable, [sys.executable, "-m", "factory.main", *sys.argv[1:]])


def trusted(cfg: Config, gh: GitHub, repo: str, num: int, label: str | None = None) -> tuple[bool, str]:
    """Fail closed: act only if a user with write access applied the trigger label."""
    label = label or cfg.trigger_label
    if tracker.is_local(num):                      # a local ticket: only the admin UI or the factory itself may have applied it
        actor = gh.label_actor(repo, num, label)
        if actor in tracker.TRUSTED_ACTORS:
            return True, f"label applied by {actor} (local ticket)"
        return False, f"local label applied by {actor}" if actor else "no label event found"
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
    for stale in (DONE, FAILED, Q.NEEDS_ANSWERS):                    # clear state left by an earlier attempt
        gh.remove_label(repo, num, stale)
    gh.add_labels(repo, num, [working_label(kind)])


def requeue_rate_limited(cfg: Config, gh: GitHub, repo: str, num: int, trigger_label: str, kind: str) -> None:
    gh.add_labels(repo, num, [trigger_label])       # no comment, no failure label
    # The pause stops new jobs for the whole pool; runs already in flight finish (or hit the limit and requeue themselves).
    started = pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds)
    log.warning("rate limited: requeued %s#%d, backing off %ds", repo, num, cfg.runner.rate_limit_backoff_seconds)
    if started:                                     # parallel runs share one subscription: one alert per pause, not per run
        alert(f"Rate limited on {repo}#{num}. Requeued; pausing {cfg.runner.rate_limit_backoff_seconds // 60} min.", event="rate_limit")
    else:
        emit("rate-limit", "rate limited too: requeued while already paused", repo, num)


def ticket_mockups(repo: str, num: int) -> list[dict]:
    """The rendered design mockups recorded for a ticket ([] when there are none or the database is unavailable)."""
    try:
        db = _ev()
        return dbm.mockup_previews(db, repo, num) if db is not None else []
    except Exception:
        log.exception("could not read the mockups of %s#%s", repo, num)
        return []


def design_pr_merged(gh: GitHub, previews: list[dict]) -> bool:
    """True when the design draft PR(s) the mockups live on are merged (read from GitHub, only for recorded PR urls)."""
    prs = [m for f in previews if (m := PR_URL.match(f.get("pr") or ""))]
    try:
        return bool(prs) and all(gh.get_pr(m.group(1), int(m.group(2))).get("merged") for m in prs)
    except Exception:
        log.exception("could not read the design PR")
        return False


def ticket_design_imports(cfg: Config, repo: str, issue: dict) -> list[dict]:
    """The design exports linked in the issue body, fetched or reused ([] on any failure: a missing design never stops a run)."""
    try:
        db = _ev()
        if db is None:
            return []
        found = designlinks.refresh(db, cfg, cfg.runner, repo, issue["number"], issue.get("body") or "")
        if found:
            emit("design-link:fetched", f"{len(found)} linked design export(s) handed to the agent", repo, issue["number"])
        return found
    except Exception:
        log.exception("could not read the design links of %s#%s", repo, issue.get("number"))
        return []


def chat_conversation(cfg: Config, repo: str, num: int) -> list:
    """The ticket chat's newest turns, as untrusted context for a stage run or a review ([] when there are none)."""
    try:
        return chat.prompt_turns(_ev(), repo, num, cfg.chat.context_turns)
    except Exception:
        log.exception("could not read the conversation of %s#%s", repo, num)
        return []


def chat_answer(cfg: Config, gh: GitHub, db, turn: dict) -> None:
    """Answer one claimed message with a read-only run (no clone, no GitHub token). Nothing here touches a label, a decision or an
    approval: the reply, and a validated proposal a person may confirm in the UI, are only stored."""
    repo, num = turn["repo"], turn["issue"]
    if cfg.dry_run or (not tracker.is_local(num) and not cfg.github_issues_enabled):
        return chat.settle(db, turn["id"], "refused", "The factory is in dry-run, so no reply was written." if cfg.dry_run
                           else "GitHub issues are switched off.")
    try:
        issue = gh.get_issue(repo, num)
    except Exception:
        log.exception("chat: could not read %s#%s", repo, num)
        return chat.settle(db, turn["id"], "failed", "The ticket could not be read.")
    if issue.get("state") != "open" or "pull_request" in issue:
        return chat.settle(db, turn["id"], "failed", "The ticket is closed.")
    route = Route(cfg.chat.harness, cfg.chat.model, cfg.chat.effort)
    run_id, sink = begin_run("chat", repo, issue, route), {}
    res = runner.run_chat(cfg, repo, issue, route, human_comments(gh, repo, num), stage_outputs(gh, repo, num),
                          Q.summary(question_state(gh, repo, num)),
                          chat.prompt_turns(db, repo, num, cfg.chat.context_turns, upto=turn["id"]), sink)
    end_run(run_id, res, sink)
    if res.status == "stage":
        chat.add_reply(db, turn, res.output, [r.name for r in cfg.roles], run_id)
        emit("chat", "replied in the ticket chat", repo, num, run_id)
    elif res.status == "rate-limited":
        chat.settle(db, turn["id"], "failed", "The model is rate limited. Try again later.")
        if pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds):
            alert(f"Rate limited during a ticket chat reply ({repo}#{num}); pausing.", event="rate_limit")
    else:
        log.warning("chat: %s#%s: %s %s", repo, num, res.status, res.detail[:300])
        chat.settle(db, turn["id"], "failed", "The reply could not be written. Try again.")


def chat_lane(cfg: Config, gh: GitHub) -> None:
    """The chat's own lane, next to the job pool: every chat.check_seconds it takes the oldest waiting message, up to
    chat.max_parallel at once. A reply never waits for a stage run, and it changes nothing a stage run reads or writes."""
    slots = threading.Semaphore(cfg.chat.max_parallel)

    def work(turn: dict) -> None:
        try:
            chat_answer(cfg, gh, dbm.local(cfg.db_path), turn)
        except Exception:
            log.exception("chat reply failed")
            try:
                chat.settle(dbm.local(cfg.db_path), turn["id"], "failed", "The reply could not be written. Try again.")
            except Exception:
                pass
        finally:
            slots.release()

    db = dbm.local(cfg.db_path)
    while True:
        try:
            if cfg.chat.enabled and not pause.paused(Path(cfg.db_path).parent) and slots.acquire(blocking=False):
                turn = chat.claim(db)
                if turn is None:
                    slots.release()
                else:
                    threading.Thread(target=work, args=(turn,), daemon=True, name="chat-reply").start()
        except Exception:
            log.exception("chat lane failed")
        time.sleep(cfg.chat.check_seconds)


def run_chain(cfg: Config, gh: GitHub, kind: str, repo: str, issue: dict, route, c=None, stage: str | None = None, **kw):
    """Run the task with the route's model, then with each admin-configured fallback while a model is unavailable (rate
    limit or API error). Every attempt is its own run: fresh workspace, container, branch and `runs` row. The last result is
    returned unchanged when the chain ends, so requeue/backoff/failure handling is as without fallbacks."""
    models = chain(route)
    if cfg.design_links.enabled and "design_imports" not in kw:
        kw["design_imports"] = ticket_design_imports(cfg, repo, issue)
    for i, r in enumerate(models):
        run_id, sink = begin_run(kind, repo, issue, r, c, stage), {}
        res = runner.run_task(cfg, gh, repo, issue, r, sink=sink, **kw)
        if i + 1 < len(models) and runner.wants_fallback(res):
            res.detail = f"{res.detail} (falling back to {models[i + 1].model})"
        end_run(run_id, res, sink)
        if i + 1 == len(models) or not runner.wants_fallback(res):
            return res, r
        msg = f"{repo}#{issue['number']}: {r.model} unavailable ({res.status}); retrying with {models[i + 1].model}"
        alert(msg, event="fallback")


def dispatch(cfg: Config, gh: GitHub, repo: str, issue: dict, route, c=None,
             trigger_label: str | None = None, conn=None) -> runner.RunResult:
    """Build it: edit the repos in the sandbox and open PRs."""
    num, trigger_label = issue["number"], trigger_label or cfg.trigger_label
    prior = stage_outputs(gh, repo, num)
    shown = ticket_mockups(repo, num)
    names = [lb["name"] for lb in issue.get("labels", [])]
    verdict, why = mockups.gate(cfg.mockups, names, shown,
                                NO_MOCKUPS in prior.get("designer", "") or MOCKUPS_OFF in prior.get("designer", ""),
                                (cfg.mockups.require_approval or cfg.mockups.request_label in names) and design_pr_merged(gh, shown))
    if verdict == "block":
        gh.remove_label(repo, num, trigger_label)       # no retry loop: a person fixes it and triggers again
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, why)
        emit("mockups:blocked", "build not started: the design stage left no rendered mockup", repo, num)
        return runner.RunResult("failed", "blocked: no rendered design mockup")
    if verdict == "warn":
        gh.comment(repo, num, why)
    alert(f"Starting {repo}#{num}: {issue['title'][:80]}\n{picked(route, c)}", event="started")
    claim(gh, repo, num, trigger_label, "implement")
    kw = dict(prior=prior, mockups=shown, comments=human_comments(gh, repo, num), answers=Q.summary(question_state(gh, repo, num)),
              conversation=chat_conversation(cfg, repo, num))
    res, route = run_chain(cfg, gh, "build", repo, issue, route, c, **kw)
    if res.status == "failed" and getattr(res, "screen_failure", None):      # one fix round with the diffs, then it stays failed
        emit("screens:retry", "the screens did not match the baselines: one fix round with the diffs", repo, num)
        res, route = run_chain(cfg, gh, "build", repo, issue, route, c, screen_retry=res.screen_failure, **kw)
    while res.status == "failed" and getattr(res, "verify_failure", None):      # fix rounds with the failing check's log, then it stays failed
        vf = res.verify_failure
        emit("verify:retry", f"a worker check failed: fix round {vf['round']} of {cfg.workers.fix_rounds}", repo, num)
        res, route = run_chain(cfg, gh, "build", repo, issue, route, c, verify_retry=vf, **kw)
    if res.status == "rate-limited":                 # the working label comes off once the outcome is on the ticket (see dispatch_stage)
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, "implement")
        gh.remove_label(repo, num, WORKING)
    elif res.status == "pr":
        gh.add_labels(repo, num, [DONE])
        draft = cfg.review.enabled and cfg.review.auto
        gh.comment(repo, num, f"Opened {res.pr_url} for review." + (" Draft until the automated review is done." if draft else ""))
        gh.remove_label(repo, num, WORKING)
        if conn is not None:
            for u in res.pr_url.split():                  # watch each PR's CI; skip anything that is not a PR URL
                m = PR_URL.match(u)
                if m:
                    dbm.track_pr(conn, m.group(1), int(m.group(2)), repo, num)    # merge conflicts are checked whether or not CI is
                    if cfg.ci.enabled:
                        dbm.watch_pr(conn, m.group(1), int(m.group(2)), repo, num)
        alert(f"PR ready: {repo}#{num}\n{res.pr_url}\n{picked(route, c)}", event="pr_ready")
        if cfg.review.enabled and cfg.review.auto:
            prs = [(m.group(1), int(m.group(2))) for m in map(PR_URL.match, res.pr_url.split()) if m]
            try:
                review_changes(cfg, gh, repo, issue, prs)
            except Exception:
                log.exception("automatic review failed")           # a review problem must never undo a finished build
    elif res.status == "cancelled":                  # the ticket was closed: no failure label, no comment
        gh.remove_label(repo, num, WORKING)
    else:
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, f"Factory run did not produce a PR ({res.status}). A person should take a look.")
        gh.remove_label(repo, num, WORKING)
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


NO_MOCKUPS = ("No design mockups were produced (the repository has no Claude Design canvases to follow, or the ticket has no "
              "user-facing screens).")
MOCKUPS_OFF = "Design mockups are turned off for this project, so only the document above was produced."


def design_section(files: list, notes: str, designer: bool = False, enabled: bool = True) -> str:
    """Links to the design files, built here from validated values (never from agent text)."""
    out = ""
    if files:
        prs = sorted({f["pr"] for f in files if PR_URL.match(f["pr"])})
        out += "\n\n### Design files\n" + (f"Draft PR: {prs[0]}\n\n" if prs else "")
        out += "\n".join(f"- [`{f['path']}`]({f['url']})" + (" (rendered preview)" if f.get("preview") else "")
                         for f in files if designfiles.link_ok(f))
        out += ("\n\nStatic Claude Design canvases. Import a file into Claude Design to work on it, or merge the draft PR to keep them "
                "with the repository." + ("\n\nThe preview images are screenshots of the canvases, rendered in a sealed container "
                                         "with no network." if any(f.get("preview") for f in files) else ""))
    if notes:
        out += f"\n\n_Note: {sanitize_markdown(notes)}_"
    elif designer and not files:                        # fixed text, never agent text
        out += "\n\n_" + (NO_MOCKUPS if enabled else MOCKUPS_OFF) + "_"
    return out


def stage_comment(role, route, text: str, hint: str | None, files: list | None = None, notes: str = "", questions: list | None = None, enabled: bool = True) -> str:
    """The validated questions go on the line right after the marker, where agent text can never be, so the UI and later
    stages read them back from there (questions.read_stored), and as a readable section built from validated values."""
    data = Q.stored(questions) if questions is not None else ""
    section = "\n\n" + sanitize_markdown(Q.section(questions)) if questions else ""
    body = (MARKER.format(name=role.name) + "\n" + data
            + f"### {role.name.title()} (harness: {route.harness}, model: {route.model}, effort: {route.effort})\n\n"
            + sanitize_markdown(text, 60000 - len(data) - len(section)) + section     # GitHub's comment limit is 65536
            + design_section(files or [], notes, role.name == "designer", enabled)
            + "\n\n---\n_Generated by Shikumi from this ticket and the code. A draft: verify before relying on it._")
    if hint:
        body += f"\n_Suggested next: **{hint}**_"
    return body


def dispatch_stage(cfg: Config, gh: GitHub, classifier, repo: str, issue: dict, role, c=None,
                   trigger_label: str | None = None, chain: bool = False, conn=None) -> runner.RunResult:
    """Run an analyst, designer or architect and put its document on the ticket."""
    num, trigger_label = issue["number"], trigger_label or role.label
    route = Route(role.harness, role.model, role.effort, role.fallback_models)
    alert(f"Starting {role.name}: {repo}#{num}: {issue['title'][:80]}\nAgent: {route.model}, effort {route.effort}"
          + (f"\nchosen by {c.source} (stage {c.stage}, confidence {c.stage_confidence:.2f})" if c and c.stage_confidence else ""), event="started")
    claim(gh, repo, num, trigger_label, role.name)
    comments = human_comments(gh, repo, num)
    before = question_state(gh, repo, num)
    review = None
    if role.name == "designer" and conn is not None:      # a person's notes on the screens go to the designer
        try:
            review = reviewnotes.for_designer(conn, repo, num)
        except Exception:
            log.exception("could not read the review notes of %s#%s", repo, num)
    res, route = run_chain(cfg, gh, "stage", repo, issue, route, c, role.name, role=role.name, prior=stage_outputs(gh, repo, num),
                           comments=comments, answers=Q.summary(before), conversation=chat_conversation(cfg, repo, num),
                           **({"review": review} if review else {}))
    if review and res.status == "stage":
        reviewnotes.mark_sent(conn, review["ids"])
    # The working label comes off only once the outcome is on the ticket: a crash or a restart before that leaves it on, so the
    # job's crash handler or the next start's recovery finds the run instead of a ticket with nothing to show.
    working = working_label(role.name)
    if res.status == "rate-limited":
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, role.name)
        gh.remove_label(repo, num, working)
    elif res.status == "stage":
        if conn is not None:                            # a designer's draft PR is checked for merge conflicts too
            for pm in map(PR_URL.match, (res.pr_url or "").split()):
                if pm:
                    dbm.track_pr(conn, pm.group(1), int(pm.group(2)), repo, num)
        labels = [lb["name"] for lb in gh.get_issue(repo, num).get("labels", []) if lb["name"] != working] + [role.done_label]
        doc, qs = Q.extract(res.output)                 # qs None: no block or a malformed one, so the classifier alone decides
        pending = [q for q in qs or [] if not q.safe]
        earlier = [q for st in before if st.stage != role.name for q in st.pending()]    # still unanswered from another stage
        settled = (f"(every open question was a safe default; the recommendations were auto-accepted)\n"
                   + Q.summary([Q.StageQuestions(role.name, qs)]) + "\n\n" if qs and not pending else "")
        hint, go_on = None, False
        try:                                            # ask Jev what should happen next, now that this stage is done
            nxt = classifier.classify(issue["title"], issue.get("body") or "", labels,
                                      comments + ["(just completed) " + (settled + doc)[:3000]],
                                      project_info(project_for(cfg, repo), repo), stages_done(cfg, labels))
            nxt, _ = pick_stage(cfg, nxt, stages_done(cfg, labels), len(project_for(cfg, repo).repos) > 1,
                                cfg.mockups.request_label in labels)
            hint = next_hint(cfg, nxt)
            # Keep going only when nothing needs a person and the classifier named a real next step. A needs-a-person question
            # always stops the chain; with only safe defaults the classifier still has to agree (a second opinion that can only
            # stop it). The re-applied label goes through the normal auto gate, which still asks before a build it is unsure
            # about. Stages already done are never chosen again, so a chain always ends.
            go_on = bool(chain and cfg.auto_chain and not pending and not earlier and not nxt.needs_human
                         and (nxt.stage in STAGE_TO_ROLE or nxt.stage == "implement")
                         and not behind(cfg, STAGE_TO_ROLE.get(nxt.stage or ""), stages_done(cfg, labels)))
        except Exception:
            log.exception("next-stage suggestion failed")
        if pending or earlier:
            hint = "a person's answer to the open questions " + ", ".join(q.id for q in pending + earlier)
        url = gh.comment(repo, num, stage_comment(role, route, doc, hint, res.files, res.notes, qs, role.design_files))
        gh.add_labels(repo, num, [role.done_label])
        gh.remove_label(repo, num, working)
        if pending or earlier:                          # a visible "waiting for you" mark on the issue itself
            try:
                gh.create_label(repo, Q.NEEDS_ANSWERS, "fbca04", "Open questions are waiting for a person's answers")
            except Exception:
                log.warning("could not create the label %s in %s", Q.NEEDS_ANSWERS, repo)      # adding it still works
            gh.add_labels(repo, num, [Q.NEEDS_ANSWERS])
        if conn is not None:
            dbm.set_questions(conn, repo, num, role.name, len(pending) + len(earlier))
        if go_on:
            gh.add_labels(repo, num, [cfg.auto_label])             # picked up on the next poll, like any auto request
        done = f"{role.name.title()} done: {repo}#{num}\n{url}" + (f"\n{len(res.files)} design file(s): {res.pr_url}" if res.files else "")
        if pending:                                     # one message per ticket, asking only about what needs a person
            text, buttons = question_message(cfg, repo, num, pending)
            alert(done + "\n\n" + text, buttons, event="needs_human")
        else:
            assumed = "".join(f"\n{q.id}. {Q.describe(q, None)}" for q in qs or [])
            alert(done + ("\nContinuing automatically: " + (hint or "next stage") + assumed if go_on else (f"\nWaiting for you: {hint}" if hint else "")),
                  event="stage_done")
    elif res.status == "cancelled":
        gh.remove_label(repo, num, working)
    else:
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, f"Factory {role.name} run did not produce a document ({res.status}). A person should take a look.")
        gh.remove_label(repo, num, working)
        alert(f"{role.name.title()} failed: {repo}#{num} ({res.status})\n{res.detail[:300]}", event="failure")
    return res


def question_message(cfg: Config, repo: str, num: int, pending: list) -> tuple[str, list]:
    """The chat message for questions that need a person: Accept recommendations, Open in UI (when its address is
    configured), and one button per option only when there are one or two questions. A button's data is parsed strictly when
    it comes back (telegram.parse_callback, shared by Slack) and then checked against the questions on the ticket (questions.record)."""
    text = "Questions for you:\n" + "\n".join(
        f"{q.id}. {q.text[:300]}\n  recommended: {q.label(q.recommended)[:120]} ({q.reason[:150]})" for q in pending)
    rows = [[("Accept recommendations", f"accept|{repo}|{num}")]]
    if (ui_url := cfg.telegram_ui_url or cfg.slack_ui_url):
        from urllib.parse import urlencode
        rows[0].append(("Open in UI", "url:" + ui_url + "/tickets?" + urlencode({"repo": repo, "q": f"#{num}"})))
    if len(pending) <= 2:
        rows += [[(f"{q.id}: {lab[:40]}", f"q:{q.id}:{oid}|{repo}|{num}") for oid, lab in q.options] for q in pending]
    return text, [[b for b in row if b[1].startswith("url:") or len(b[1].encode()) <= 64] for row in rows]


def answer_from_chat(cfg: Config, gh: GitHub, conn, repo: str, num: int, action: str) -> None:
    """Accept recommendations, or one option of one question, chosen by the allowlisted Telegram or Slack user. Recorded like an
    answer from the UI; when nothing needing a person is left, the auto label continues the ticket through the normal gates."""
    try:
        if action == "accept":
            stage, done = Q.record(gh, repo, num, None, None, "chat", accept_all=True)
        else:
            _, qid, oid = action.split(":")
            stage, done = Q.record(gh, repo, num, None, {qid: ("option", oid)}, "chat")
    except Q.Refused as e:
        alert(f"Not recorded for {repo}#{num}: {e}", event="needs_human")
        return
    if done:
        dbm.set_questions(conn, repo, num, stage, 0)
        gh.add_labels(repo, num, [cfg.auto_label])
    emit("answers", f"answers to the {stage}'s questions recorded from chat", repo, num)
    alert(f"Answers recorded for {repo}#{num}." + (" Continuing with the next stage." if done else " Other questions still need you."),
          event="needs_human")


def review_comment(route, text: str) -> str:
    return ("<!-- factory:review -->\n"
            f"### Automated code review (harness: {route.harness}, model: {route.model}, effort: {route.effort})\n\n" + sanitize_markdown(text)
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
    route = Route(cfg.review.harness, cfg.review.model, cfg.review.effort, cfg.review.fallback_models)
    alert(f"Reviewing {repo}#{num}: {len(prs)} PR(s)\nReviewer: {route.model}, effort {route.effort} ({route.harness})", event="started")
    res, route = run_chain(cfg, gh, "review", repo, issue, route, None, "reviewer", role="reviewer", prior=stage_outputs(gh, repo, num),
                           comments=human_comments(gh, repo, num), fix_branch=branch, mockups=ticket_mockups(repo, num),
                           conversation=chat_conversation(cfg, repo, num))
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
                continue                                      # not reviewed: it stays a draft
            try:
                gh.mark_ready(r, n)
            except Exception:
                log.exception("could not mark %s#%s ready for review", r, n)
                alert(f"Could not mark {r}#{n} ready for review; it is still a draft.", event="failure")
        gh.add_labels(repo, num, [cfg.review.done_label])
        verdict = next((v for v in ("Blocking issues", "Needs changes", "Looks good") if v.lower() in res.output[:600].lower()), "see the PR")
        alert(f"Review done: {repo}#{num}: {verdict}\n" + "\n".join(links), event="review_done")
    elif res.status == "cancelled":
        pass                                                  # the ticket was closed: nothing to post
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
    # Anchored: sandboxes are named factory-<issue>-<time>-<token> (every parallel run's). An unanchored match would also catch
    # containers like "software-factory-orchestrator-1" and remove the factory itself.
    subprocess.run(f"{eng} ps -aq --filter name=^factory- | xargs -r {eng} rm -f", shell=True,
                   capture_output=True, timeout=60)
    requeue = {WORKING: cfg.trigger_label, **{working_label(r.name): r.label for r in cfg.roles},
               **({working_label("review"): cfg.review.label} if cfg.review.enabled else {}),
               **({working_label("conflicts"): cfg.conflicts.label} if cfg.conflicts.enabled else {})}
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
    if issue.get("state") == "closed":                  # a closed ticket gets no more CI fix rounds
        return False
    res, _ = run_chain(cfg, gh, "fix", issue_repo, issue, cfg.routes["medium"], prior=stage_outputs(gh, issue_repo, issue_num),
                       comments=human_comments(gh, issue_repo, issue_num), fix_branch=branch, failures=failures)
    if res.status == "rate-limited":
        if pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds):
            alert(f"Rate limited during a CI fix for {repo}#{number}; pausing.", event="rate_limit")
        return None
    if res.status in ("failed", "rejected"):
        alert(f"CI fix round failed for {repo}#{number}: {res.status}\n{res.detail[:200]}", event="failure")
    return res.status == "pr"


def fix_conflicts(cfg: Config, gh: GitHub, conn, repo: str, issue: dict, label: str) -> str:
    """The conflicts label: merge the base branch into every conflicting factory PR of the ticket. Returns the outcome."""
    num = issue["number"]
    if issue.get("state") == "closed":              # a closed ticket gets no more conflict rounds
        gh.remove_label(repo, num, label)
        return "closed"
    gh.remove_label(repo, num, label)               # prevents re-dispatch; factory:pr-open stays, the PRs are still open
    gh.add_labels(repo, num, [working_label("conflicts")])
    try:
        return resolve_conflicts(cfg, gh, conn, repo, issue, label)
    finally:
        gh.remove_label(repo, num, working_label("conflicts"))


def resolve_conflicts(cfg: Config, gh: GitHub, conn, repo: str, issue: dict, label: str) -> str:
    num, limit = issue["number"], cfg.conflicts.max_attempts
    groups: dict[str, list] = {}                    # PR branch -> [(repo, number, base branch, url, attempts)]; siblings share a branch
    notes, limited = [], []
    for row in dbm.conflicts_for_issue(conn, repo, num):
        r, n = row["repo"], row["number"]
        url = f"https://github.com/{r}/pull/{n}"
        try:
            pr = gh.get_pr(r, n)
        except Exception:
            log.exception("could not read %s#%s", r, n)
            notes.append(f"{url}: could not be read from GitHub; apply `{label}` again later")
            continue
        state = conflicts.state_of(pr)
        if state == "closed":
            dbm.update_conflict(conn, r, n, state="closed")
        elif state == "unknown":
            notes.append(f"{url}: GitHub has not finished checking it for conflicts; apply `{label}` again in a few minutes")
        elif state == "conflicting":
            branch = pr["head"]["ref"]
            if not branch.startswith("factory/") or pr["head"]["repo"]["full_name"] != r:
                notes.append(f"{url}: not a factory branch in this repository, left alone")     # never touch a branch the factory did not create
            elif row["attempts"] >= limit:
                dbm.update_conflict(conn, r, n, state="needs-person", detail="attempt limit reached")
                limited.append(url)
                notes.append(f"{url}: all {limit} resolution attempts are used; a person should resolve it")
            else:
                groups.setdefault(branch, []).append((r, n, pr["base"]["ref"], url, row["attempts"]))
    extra = "".join(f"\n- {x}" for x in notes)
    if not groups:
        gh.comment(repo, num, sanitize_markdown("No factory pull request of this ticket needs its conflicts resolved, so nothing was merged." + extra))
        if limited:
            alert(f"Merge conflicts need a person: {repo}#{num} (attempt limit reached)\n" + "\n".join(limited), event="failure")
        return "no-conflicts"
    route, outcomes = cfg.routes["medium"], []
    for branch, prs in groups.items():
        alert(f"Resolving merge conflicts: {repo}#{num} on {branch}, {len(prs)} PR(s)\nAgent: {route.model}, effort {route.effort}", event="started")
        res, _ = run_chain(cfg, gh, "conflicts", repo, issue, route, prior=stage_outputs(gh, repo, num), comments=human_comments(gh, repo, num),
                           fix_branch=branch, merge_base={r: base for r, _, base, _, _ in prs})
        if res.status == "rate-limited":
            requeue_rate_limited(cfg, gh, repo, num, label, "conflicts")          # the attempt is not counted
            return "rate-limited"
        if res.status == "cancelled":                    # the ticket was closed: the attempt is not counted and nothing is posted
            return "cancelled"
        pushed, links = res.status == "pr", "\n".join(u for _, _, _, u, _ in prs)
        # Our own validation messages are safe to show; an agent's log tail (in failed / no-change) only goes to the chat and the UI.
        why = res.detail[:300] if res.status in ("pr", "needs-person", "rejected") else res.status
        for r, n, base, url, attempts in prs:
            if pushed:                                   # GitHub re-checks the new head; a conflict that remains is reported again
                dbm.update_conflict(conn, r, n, attempts=attempts + 1, state="unknown", notified_sha="", detail=why)
                if cfg.ci.enabled:
                    dbm.update_pr(conn, r, n, status="watching", watch_started=time.time(), summary="conflicts resolved")
                msg = f"Merged `{base}` into `{branch}` to resolve the merge conflicts: {why}."
            else:                                        # this head is not reported or labelled again; a person may re-apply the label
                dbm.update_conflict(conn, r, n, attempts=attempts + 1, state="needs-person", detail=f"{res.status}: {why}")
                msg = (f"Could not resolve the merge conflicts with `{base}` on `{branch}` ({why}). A person should take a look "
                       f"(attempt {attempts + 1} of {limit}).")
            gh.comment(r, n, sanitize_markdown(msg))
        gh.comment(repo, num, sanitize_markdown((f"Merge conflicts resolved on `{branch}`: {why}." if pushed else
                                                 f"Could not resolve the merge conflicts on `{branch}` ({why}). A person should take a look.")
                                                + "\n" + links + extra))
        if pushed:
            alert(f"Merge conflicts resolved: {repo}#{num}\n{links}", event="conflict")
        else:
            alert(f"Merge conflicts not resolved: {repo}#{num} ({res.status})\n{res.detail[:300]}\n{links}", event="failure")
        outcomes.append(res.status)
    return ",".join(outcomes)


def process_approvals(cfg: Config, gh: GitHub, conn, classifier=None) -> None:
    """Run/Skip decisions made by the allowlisted Telegram chat or Slack user."""
    if pause.paused(Path(cfg.db_path).parent):
        return
    for repo, num, action in dbm.approvals(conn):
        if not cfg.github_issues_enabled and not tracker.is_local(num):
            continue                                # GitHub issues are switched off; the approval waits
        try:
            if action != "skip" and not pool.can_take(jobkey(repo, num)):
                continue                            # waits for a free slot (or for this ticket's job to end); the approval stays
            dbm.drop_approval(conn, repo, num, action)
            if action == "skip":
                for _, label in triggers(cfg):
                    gh.remove_label(repo, num, label)
                alert(f"Skipped {repo}#{num}", event="skipped")
                continue
            issue = gh.get_issue(repo, num)
            if issue["state"] != "open" or "pull_request" in issue:
                continue
            if action.startswith("triage"):
                confirm_triage(cfg, gh, conn, classifier, repo, issue, action)
                continue
            if action == "accept" or action.startswith("q:"):
                answer_from_chat(cfg, gh, conn, repo, num, action)
                continue
            back = action.startswith("redirect:")
            role = next((r for r in cfg.roles if action == f"{'redirect' if back else 'stage'}:{r.name}"), None)
            if action != "run" and role is None:
                continue                            # an unknown action is ignored, never guessed at
            for _, label in triggers(cfg):         # the approval covers the whole ticket: clear EVERY trigger label (factory:auto
                gh.remove_label(repo, num, label)  # included), or finishing the run re-triages it and asks a person again
            if back:
                redirect(cfg, gh, conn, repo, num, role)
            def approved(db, repo=repo, num=num, issue=issue, role=role, back=back):
                if role:                            # sent back: the stages after it follow through the normal auto chain
                    res = dispatch_stage(cfg, gh, classifier, repo, issue, role, chain=back, conn=db)
                else:
                    res = dispatch(cfg, gh, repo, issue, cfg.routes["medium"], conn=db)
                dbm.record(db, repo, num, issue["updated_at"] + "+approved", f"run:{res.status}", f"approved from chat; {res.detail}; {res.pr_url or ''}")
            start(cfg, conn, repo, num, role.name if role else "build", approved, gh)
        except Exception:
            log.exception("approval failed for %s#%s", repo, num)


def _epoch(iso: str) -> float:
    try:
        return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return time.time()


ISSUE_NUMBER = re.compile(r"/issues/(\d+)$")


def triage_scan(cfg: Config, gh: GitHub, conn, repo: str, github_due: bool) -> None:
    """Record each new comment of a repository once, for triage_process. A comment counts when the admin UI wrote it (local tickets) or
    a GitHub user with a trusted permission did (the same check as trusted()); other people's comments are recorded as ignored, and
    our own comments, imported ones and pull request comments are never triaged. The first scan after the feature is turned on
    only sets the cursors, so older comments are never triaged, and an edited comment is not looked at again."""
    status = dbm.get_status(conn)
    gh_key, local_key = f"triage:cursor:{repo}", f"triage:local:{repo}"
    if local_key not in status or gh_key not in status:
        top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM local_comments WHERE repo=?", (repo,)).fetchone()[0]
        dbm.set_status(conn, local_key, str(top))
        dbm.set_status(conn, gh_key, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return
    if cfg.local_enabled:
        rows = conn.execute("SELECT id, number, body, created FROM local_comments WHERE repo=? AND id>? AND author=? ORDER BY id LIMIT 200",
                            (repo, int(status[local_key]["value"]), tracker.UI_ACTOR)).fetchall()
        for cid, number, body, created in rows:
            triage.add(conn, repo, number, f"local:{cid}", tracker.UI_ACTOR, created, body)
        if rows:
            dbm.set_status(conn, local_key, str(rows[-1][0]))
    if not (github_due and cfg.github_issues_enabled and gh.token):
        return
    since, me, perms, newest = status[gh_key]["value"], gh.login(), {}, status[gh_key]["value"]
    for c in gh.comments_since(repo, since):
        newest = max(newest, c.get("updated_at") or "")
        found = ISSUE_NUMBER.search(c.get("issue_url") or "")
        author = (c.get("user") or {}).get("login") or ""
        if not found or not author or author == me or (c.get("created_at") or "") < since:
            continue
        if author not in perms:
            perms[author] = gh.permission(repo, author)
        ok = perms[author] in cfg.trusted_permissions
        triage.add(conn, repo, int(found.group(1)), f"gh:{c['id']}", author, _epoch(c.get("created_at")), c.get("body") or "",
                   "pending" if ok else "ignored", "" if ok else f"{author} has permission '{perms[author]}'")
    dbm.set_status(conn, gh_key, newest)


def triage_process(cfg: Config, gh: GitHub, conn, repo: str, github_due: bool = True) -> None:
    """Start a triage run for each ticket with recorded comments that can take one now. A comment waits (and is not lost) while
    the ticket has a job, the factory is paused or something waits for a slot, and once the ticket's daily cap of runs is used. It is
    dropped with its reason when the ticket is closed or never worked on, or marked seen when the next run reads it anyway."""
    if pause.paused(Path(cfg.db_path).parent) or any(not q.get("reason") for q in queued):
        return
    for row in triage.with_status(conn, repo, "running"):
        if not pool.busy(jobkey(repo, row["issue"])):                 # the process restarted or the job crashed: it is tried again
            triage.mark(conn, [row["id"]], "pending")
    tickets: dict[int, list] = {}
    for row in triage.with_status(conn, repo, "pending"):
        tickets.setdefault(row["issue"], []).append(row)
    done_labels = {r.done_label for r in cfg.roles} | {DONE, FAILED, Q.NEEDS_ANSWERS, cfg.review.done_label}
    for num, rows in tickets.items():
        if not pool.can_take(jobkey(repo, num)) or (not tracker.is_local(num) and not github_due):
            continue
        try:
            issue = gh.get_issue(repo, num)
        except Exception:
            log.warning("could not read %s#%s for its comment triage", repo, num)
            continue
        labels = {lb["name"] for lb in issue.get("labels", [])}
        ids = [r["id"] for r in rows]
        if issue.get("state") != "open" or "pull_request" in issue:
            triage.mark(conn, ids, "ignored", reason="the ticket is closed or is not a ticket")
            continue
        started = triage.last_work_started(conn, repo, num)
        if tracker.MOVED_LABEL in labels:
            triage.mark(conn, ids, "ignored", reason="the ticket moved to the local tracker")
            continue
        if any(lb.startswith("factory:working") for lb in labels):
            continue                                               # a job holds the ticket; the comment waits for it to end
        if labels & {label for _, label in triggers(cfg)}:
            triage.mark(conn, ids, "seen-by-run", reason="a run is queued: it reads the comment")
            continue
        read = [r["id"] for r in rows if r["comment_at"] < started]
        rows = [r for r in rows if r["id"] not in read]
        triage.mark(conn, read, "seen-by-run", reason="a run started after the comment and read it")
        if not rows:
            continue
        if not (labels & done_labels or started):
            triage.mark(conn, [r["id"] for r in rows], "ignored", reason="the factory has not worked on this ticket")
            continue
        if triage.runs_today(conn, repo, num, time.time()) >= cfg.comments.max_per_day:
            continue                                               # waits for the next day's runs
        triage.mark(conn, [r["id"] for r in rows], "running")
        start(cfg, conn, repo, num, "triage", lambda db, issue=issue, rows=rows: triage_job(cfg, gh, db, repo, issue, rows), gh)


def triage_job(cfg: Config, gh: GitHub, conn, repo: str, issue: dict, rows: list) -> None:
    """The agent's run over the newest comments of one ticket (read-only in the sandbox), then triage_apply with what triage.parse
    validated. A run that did not finish, or whose block is invalid, ends as needs-person."""
    num, ids = issue["number"], [r["id"] for r in rows]
    route = Route(cfg.comments.harness, cfg.comments.model, cfg.comments.effort)
    run_id, sink = begin_run("triage", repo, issue, route), {}
    try:
        res = runner.run_task(cfg, gh, repo, issue, route, role="triage", sink=sink, prior=stage_outputs(gh, repo, num),
                              comments=[f"{r['author']}: {r['comment']}" for r in reversed(rows)],
                              answers=Q.summary(question_state(gh, repo, num)))
        end_run(run_id, res, sink)
        if res.status == "rate-limited":
            triage.mark(conn, ids, "pending")
            if pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds):
                alert(f"Rate limited during the comment triage of {repo}#{num}; pausing.", event="rate_limit")
            return
        if res.status == "cancelled":
            triage.mark(conn, ids, "ignored", reason="the ticket was closed")
            return
        found = triage.parse(res.output, [r.name for r in cfg.roles]) if res.status == "stage" else None
        why = "the triage reply had no valid factory-triage block" if res.status == "stage" else f"the triage run did not finish ({res.status})"
        triage_apply(cfg, gh, conn, repo, issue, rows, found or triage.Decision("needs-person", reason=why), run_id)
    except Exception:
        log.exception("comment triage of %s#%s failed", repo, num)
        triage.mark(conn, ids, "failed", reason="the triage crashed")


def triage_apply(cfg: Config, gh: GitHub, conn, repo: str, issue: dict, rows: list, d, run_id) -> None:
    """none and needs-person are a comment. redirect and followup are stored as a proposal and put to a person; they happen in
    confirm_triage, never here."""
    num, ids, primary = issue["number"], [r["id"] for r in rows], rows[-1]
    who = ", ".join(sorted({r["author"] for r in rows}))
    ref = f"{repo} {tracker.display(num)}" if tracker.is_local(num) else f"{repo}#{num}"
    if d.decision in ("none", "needs-person"):
        what = "nothing needs to change." if d.decision == "none" else "a person should take a look."
        gh.comment(repo, num, triage.NOTE.format(who=who, what=what, reason=d.reason))
        triage.mark(conn, ids, "done", decision=d.decision, reason=d.reason, run_id=run_id)
        emit("decision", f"comment triage: {d.decision}. {d.reason}", repo, num, run_id)
        if d.decision == "needs-person":
            alert(f"Comment triage: {ref} needs a person.\n{d.reason}", event="needs_human")
        return
    what = (f"send it back to the {d.stage} stage (the steps from there on run again, and its open factory pull requests are superseded)"
            if d.decision == "redirect" else f"open a follow-up ticket titled \"{d.title}\"")
    triage.mark(conn, [i for i in ids if i != primary["id"]], "done", decision=d.decision, reason=d.reason, run_id=run_id,
                result=f"covered by triage {primary['id']}")
    triage.mark(conn, [primary["id"]], "proposed", decision=d.decision, stage=d.stage, title=d.title, body=d.body, reason=d.reason, run_id=run_id)
    gh.comment(repo, num, triage.PROPOSAL.format(who=who, what=what, reason=d.reason))
    emit("decision", f"comment triage proposes: {d.decision} {d.stage or d.title}. {d.reason}", repo, num, run_id)
    buttons = [("Confirm", f"triage:{primary['id']}|{repo}|{num}"), ("Dismiss", f"triage-no:{primary['id']}|{repo}|{num}")]
    alert(f"Comment triage: {ref}\nSuggests to {what}.\n{d.reason}", [b for b in buttons if len(b[1].encode()) <= 64], event="needs_human")


def confirm_triage(cfg: Config, gh: GitHub, conn, classifier, repo: str, issue: dict, action: str) -> None:
    """A person's Confirm or Dismiss of a proposal. The row must be a proposal of this very ticket; the decision and its stage, title
    and body are read back from the row (validated when stored), never from the action."""
    num = issue["number"]
    found = re.fullmatch(r"(triage|triage-no):(\d{1,9})", action)
    row = triage.get(conn, int(found.group(2))) if found else None
    if row is None or row["repo"] != repo or row["issue"] != num or row["status"] != "proposed":
        return
    if found.group(1) == "triage-no":
        triage.mark(conn, [row["id"]], "dismissed")
        emit("decision", "comment triage: the suggestion was dismissed", repo, num, row["run_id"])
        return
    try:
        if row["decision"] == "redirect":
            role = next((r for r in cfg.roles if r.name == row["stage"]), None)
            if role is None:
                raise ValueError("not a configured stage")
            for _, label in triggers(cfg):               # as for any approval: the whole ticket, so finishing does not ask again
                gh.remove_label(repo, num, label)
            redirect(cfg, gh, conn, repo, num, role, "after a comment, as a person confirmed")
            triage.mark(conn, [row["id"]], "confirmed", result=f"sent back to {role.name}")

            def approved(db, role=role):
                res = dispatch_stage(cfg, gh, classifier, repo, issue, role, chain=True, conn=db)
                dbm.record(db, repo, num, issue["updated_at"] + "+triage", f"run:{res.status}", f"comment triage confirmed; {res.detail}")
            start(cfg, conn, repo, num, role.name, approved, gh)
        elif row["decision"] == "followup":
            body = triage.FOLLOWUP_HEAD.format(ref=tracker.ref(repo, num)) + row["body"]
            if tracker.is_local(num):
                shown = tracker.display(tracker.LocalTracker(conn).create(repo, row["title"], body, author=tracker.FACTORY_ACTOR))
            else:
                made = gh.create_followup(repo, row["title"], body)
                shown = f"#{made['number']}"
                try:
                    gh.add_sub_issue(repo, num, made["id"])
                except Exception:
                    log.warning("could not link the follow-up %s to %s#%s", shown, repo, num)
            triage.mark(conn, [row["id"]], "confirmed", result=shown)
            gh.comment(repo, num, triage.FOLLOWUP_DONE.format(shown=shown))
            emit("decision", f"comment triage: opened the follow-up ticket {shown}", repo, num, row["run_id"])
    except Exception:
        log.exception("could not apply the comment triage %s of %s#%s", row["id"], repo, num)
        triage.mark(conn, [row["id"]], "failed")


def handle_issue(cfg: Config, gh: GitHub, conn, classifier, repo: str, issue: dict, kind: str, label: str) -> str | None:
    """One labeled issue. Returns "paused" if the orchestrator is paused and should stop polling."""
    num, updated = issue["number"], issue["updated_at"]
    if dbm.seen(conn, repo, num, updated):
        return None
    if not cfg.dry_run and not pool.can_take(jobkey(repo, num)):
        # No free slot, or this ticket already has a job (a ticket is never worked twice at once). Leave it untouched: the
        # trigger label stays and nothing is recorded, so a later poll picks it up through the same gates.
        if not pool.busy(jobkey(repo, num)):
            queued.append({"repo": repo, "issue": num, "kind": kind, "title": issue.get("title", "")[:120]})
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
    tracked = (dbm.conflicts_for_issue(conn, repo, num)
               if kind == "auto" and DONE in [lb["name"] for lb in issue.get("labels", [])] else [])
    if tracked or (kind == "auto" and DONE in [lb["name"] for lb in issue.get("labels", [])] and dbm.prs_for_issue(conn, repo, num)):
        # Open factory PRs already exist: auto must not build again (the Build label does that, on purpose).
        if cfg.conflicts.enabled and any(c["state"] == "conflicting" for c in tracked):
            kind = "conflicts"                      # resolve_conflicts re-reads each PR and keeps the attempt limit and branch checks
            emit("decision", "auto: open PR has merge conflicts; resolving them instead of rebuilding", repo, num)
        else:
            msg = (f"This ticket already has open factory pull requests (`{DONE}`), so `{label}` did not start a new build. "
                   f"Apply `{cfg.trigger_label}` to build again on purpose, or comment on what should change.")
            dbm.record(conn, repo, num, updated, "human", f"{why}; auto skipped: open PRs exist")
            emit("decision", "needs a person: auto skipped, the ticket already has open PRs", repo, num)
            if not cfg.dry_run:
                gh.comment(repo, num, sanitize_markdown(msg))
                gh.remove_label(repo, num, label)
            return None
    if kind == "review":
        prs = dbm.prs_for_issue(conn, repo, num)
        if cfg.dry_run:
            dbm.record(conn, repo, num, updated, "review", f"{why}; would review {len(prs)} PR(s) [dry-run]")
            emit("decision", f"dry-run: would review {len(prs)} PR(s)", repo, num)
            return None
        claim(gh, repo, num, label, "review")

        def review_job(db):
            if prs:
                res = review_changes(cfg, gh, repo, issue, prs, label)
                outcome = res.status if res else "skipped"
            else:
                gh.comment(repo, num, "No open factory pull requests were found for this ticket, so there is nothing to review.")
                outcome = "no-prs"
            gh.remove_label(repo, num, working_label("review"))
            dbm.record(db, repo, num, updated, f"run:{outcome}", f"{why}; review of {len(prs)} PR(s)")
        start(cfg, conn, repo, num, "review", review_job, gh)
        return None
    if kind == "conflicts":
        if cfg.dry_run:
            n = len(dbm.conflicts_for_issue(conn, repo, num))
            dbm.record(conn, repo, num, updated, "conflicts", f"{why}; would merge the base branch into conflicting PRs of {n} tracked [dry-run]")
            emit("decision", f"dry-run: would resolve merge conflicts ({n} tracked PR(s))", repo, num)
            return None

        def conflicts_job(db):
            outcome = fix_conflicts(cfg, gh, db, repo, issue, label)
            dbm.record(db, repo, num, updated, f"run:{outcome}", f"{why}; merge conflicts")
            log.info("%s#%d conflicts: %s", repo, num, outcome)
        start(cfg, conn, repo, num, "conflicts", conflicts_job, gh)
        return None
    labels = [lb["name"] for lb in issue.get("labels", [])]
    done = stages_done(cfg, labels)
    seen_by_classifier, open_questions = human_comments(gh, repo, num), False
    if kind == "auto":                                  # earlier stage documents (and the open questions in them) inform the next call
        seen_by_classifier += [f"({k} document written by the factory) {v[:2500]}" for k, v in stage_outputs(gh, repo, num).items()]
        asked = question_state(gh, repo, num)
        open_questions = any(st.pending() for st in asked)
        if (settled := Q.summary(asked)):
            seen_by_classifier.append("(the open questions and how each was settled) " + settled[:2500])
    c = classifier.classify(issue["title"], issue.get("body") or "", labels, seen_by_classifier,
                            project_info(project_for(cfg, repo), repo), done)
    summary = f"cls={c.kind}/{c.complexity}/human={c.needs_human}/conf={c.confidence:.2f}/stage={c.stage}"
    role = next((r for r in cfg.roles if r.name == kind), None)       # explicit stage label: the human chose it

    if kind == "auto":
        before = c.stage
        c, adjusted = pick_stage(cfg, c, done, len(project_for(cfg, repo).repos) > 1, cfg.mockups.request_label in labels)
        if adjusted:
            summary = f"{summary}; stage {before} -> {c.stage} ({adjusted})"
            emit("decision", f"auto: {c.stage} instead of {before} ({adjusted})", repo, num)
        st = STAGE_TO_ROLE.get(c.stage or "")
        sure = min(c.confidence, c.stage_confidence if c.stage_confidence is not None else c.confidence) >= cfg.confidence_threshold
        # A read-only stage (analyst, designer, architect) only writes a document on the ticket, and low confidence or "needs a person"
        # is exactly why an analyst is the right next step, so it runs without asking. A person is asked before a BUILD, or when
        # the classifier named no stage, or a stage that is already done.
        back = behind(cfg, st, done)                    # an earlier stage than one already done: auto never goes back
        read_only_pick = bool(st) and st not in done and not back and not cfg.auto_confirm_stages
        if (not read_only_pick and (c.needs_human or not sure) and not cfg.auto_confirm_stages and not done
                and any(r.name == "analyst" for r in cfg.roles)):
            st, read_only_pick = "analyst", True       # unsure what to do: the cheap read-only analyst is the right first step, not a question
            emit("decision", f"unsure ({summary}): running the analyst first", repo, num)
        # A question that needs a person is never auto-resolved: until it is answered, auto does not build (whatever the classifier says).
        if not read_only_pick and (c.needs_human or open_questions or not sure or (st and (st in done or back))):
            code = ("human" if c.needs_human else "questions" if open_questions else "low" if not sure else "behind" if back else "done")
            reason = {"human": "needs a person", "questions": "open questions need a person", "low": "low confidence",
                      "behind": "chose an earlier stage than one already done", "done": "chose a stage already done"}[code]
            overall = min(c.confidence, c.stage_confidence if c.stage_confidence is not None else c.confidence)
            scored = W.build(cfg.confidence_threshold, c, code, overall)
            dbm.record(conn, repo, num, updated, "human", f"{why}; {summary}; {reason}", scored)
            emit("decision", f"needs a person: {reason} ({summary})", repo, num)
            log.info("%s#%d auto -> human (%s)", repo, num, reason)
            if not cfg.dry_run:
                alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {W.line(W.parse(scored))}\n"
                      f"Classified {c.kind}/{c.complexity}, stage {c.stage}, confidence {c.confidence:.2f}, by {c.source}" + suggestion(c),
                      human_buttons(cfg, repo, num, c, done), event="needs_human")
            return None
        role = next((r for r in cfg.roles if r.name == st), None)
        names = [r.name for r in cfg.roles]
        skipped = [n for n in names if n not in done and (role is None or names.index(n) < names.index(role.name))]
        if skipped:
            summary += f"; skipped={','.join(skipped)}"
            emit("decision", f"auto: {role.name if role else 'build'} ({c.stage}, {c.confidence:.2f}, {c.source}); skipped {', '.join(skipped)}", repo, num)

    if role:
        detail = f"{why}; {summary}; stage={role.name}"
        if cfg.dry_run:
            dbm.record(conn, repo, num, updated, "stage", detail)
            emit("decision", f"dry-run: would run {role.name} ({summary})", repo, num)
            log.info("%s#%d -> stage %s [dry-run]", repo, num, role.name)
            return None
        def stage_job(db, role=role):
            res = dispatch_stage(cfg, gh, classifier, repo, issue, role, c if kind == "auto" else None, label, chain=(kind == "auto"), conn=db)
            dbm.record(db, repo, num, updated, f"run:{res.status}", f"{detail}; {res.detail}")
            log.info("%s#%d stage %s: %s", repo, num, role.name, res.status)
        start(cfg, conn, repo, num, role.name, stage_job, gh)
        return None

    d = decide(cfg, c)
    detail = f"{why}; {d.reason}; {summary}; route={d.route}"
    scored = (W.build(cfg.confidence_threshold, c, "question" if c.kind == "question" else "human" if c.needs_human else "low")
              if d.action == "human" else None)
    if d.action == "dispatch" and not cfg.dry_run:
        def build_job(db):
            res = dispatch(cfg, gh, repo, issue, d.route, c, label, db)
            dbm.record(db, repo, num, updated, f"run:{res.status}", f"{detail}; {res.detail}; {res.pr_url or ''}")
            log.info("%s#%d run %s: %s %s", repo, num, res.status, res.detail, res.pr_url or "")
        start(cfg, conn, repo, num, "build", build_job, gh)
    else:
        dbm.record(conn, repo, num, updated, d.action, detail, scored)
        emit("decision", f"{d.action}: {d.reason} ({summary}){' [dry-run]' if cfg.dry_run else ''}", repo, num)
        log.info("%s#%d -> %s (%s)%s", repo, num, d.action, detail, " [dry-run]" if cfg.dry_run else "")
        if d.action == "human" and not cfg.dry_run:
            alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {W.line(W.parse(scored))}\n"
                  f"Classified {c.kind}/{c.complexity}, human={c.needs_human}, confidence {c.confidence:.2f}, by {c.source}" + suggestion(c),
                  human_buttons(cfg, repo, num, c, done), event="needs_human")
    return None


def ci_submit(cfg: Config, conn):
    """How the CI watcher starts a fix round: only when not paused and the ticket can take a job, else it waits a pass."""
    def submit(issue_repo: str, issue_num: int, job) -> bool:
        if pause.paused(Path(cfg.db_path).parent) or not pool.can_take(jobkey(issue_repo, issue_num)):
            return False
        start(cfg, conn, issue_repo, issue_num, "fix", job)
        return True
    return submit


def pm_sweep(cfg: Config, gh: GitHub, conn, repo: str, issues: list) -> str:
    """The project manager's run over a repository's factory tickets. Read-only in the sandbox; afterwards the orchestrator applies
    only what pm.parse validated (see pm.py). Returns the outcome."""
    route = Route(cfg.pm.harness, cfg.pm.model, cfg.pm.effort)
    stand_in = {"number": 0, "title": f"Project manager: {len(issues)} ticket(s)", "body": "", "updated_at": ""}
    run_id, sink = begin_run("pm", repo, stand_in, route, None, "pm"), {}
    res = runner.run_task(cfg, gh, repo, stand_in, route, role="pm", sink=sink, backlog=pm.backlog_items(issues, cfg.pm.body_chars))
    end_run(run_id, res, sink)
    found = pm.parse(res.output, {i["number"] for i in issues}) if res.status == "stage" else None
    if found is None:
        pm.set_sweep_state(conn, repo, time.time(), "")             # try again after the interval, even if nothing changed
        if res.status == "rate-limited":
            if pause.set_backoff(Path(cfg.db_path).parent, cfg.runner.rate_limit_backoff_seconds):
                alert(f"Rate limited during the project manager's sweep of {repo}; pausing.", event="rate_limit")
        elif res.status == "stage":
            emit("pm", "the project manager's reply had no valid factory-priorities block: nothing changed", repo, None, run_id)
        else:
            alert(f"Project manager failed for {repo} ({res.status})\n{res.detail[:300]}", event="failure")
        return res.status
    changes = pm.apply(cfg, gh, conn, repo, {i["number"]: i for i in issues}, found, run_id)
    emit("pm", f"project manager assessed {len(found)} ticket(s), changed {len(changes)}" + "".join(f"; {c}" for c in changes)[:1500],
         repo, None, run_id)
    if changes:
        alert(f"Project manager: {repo}\n" + "\n".join(changes[:20]), event="info")
    return "stage"


def maybe_pm_sweep(cfg: Config, gh: GitHub, conn, repo: str) -> None:
    """Start a sweep when the PM is on, its interval has passed, the repository's tickets changed since the last one, and no
    ticket is waiting for a slot (real work comes first). It runs in the pool under ticket number 0, which no issue has."""
    if not cfg.pm.enabled or cfg.dry_run or any(not q.get("reason") for q in queued):
        return
    if not pool.can_take(jobkey(repo, 0)) or pause.paused(Path(cfg.db_path).parent):
        return
    last, now = pm.sweep_state(conn, repo), time.time()
    if now - float(last.get("ts") or 0) < cfg.pm.interval_minutes * 60:
        return
    issues = pm.backlog_issues(cfg, gh, repo, [label for _, label in triggers(cfg)] + [r.done_label for r in cfg.roles])
    dig = pm.digest(issues)
    if not issues or dig == last.get("digest"):
        pm.set_sweep_state(conn, repo, now, last.get("digest") or "")
        return
    pm.set_sweep_state(conn, repo, now, dig)
    start(cfg, conn, repo, 0, "pm", lambda db: pm_sweep(cfg, gh, db, repo, issues))


def report_blocked(cfg: Config, repo: str, now_held: dict) -> None:
    """One event when a ticket becomes blocked or its blockers change, and one alert per cycle of tickets waiting for each other."""
    for k in [k for k in held if k[0] == repo and k[1] not in now_held]:
        del held[k]
    for num, b in now_held.items():
        if held.get((repo, num)) != tuple(b):
            held[(repo, num)] = tuple(b)
            emit("decision", "waiting: blocked by " + ", ".join(f"#{n}" for n in b), repo, num)
    for cyc in pm.cycles(now_held):
        if (repo, cyc) not in cycles_seen:
            cycles_seen.add((repo, cyc))
            text = ", ".join(f"#{n}" for n in cyc)
            emit("pm:cycle", f"tickets block each other: {text}", repo, cyc[0])
            alert(f"Blocked in a cycle: {repo} {text} wait for each other, so none of them starts. Remove a 'Blocked by' line, "
                  f"or apply `{cfg.pm.unblock_label}` to one of them.", event="needs_human")


def pool_status() -> str:
    """What the UI shows next to the running runs (which it reads from the runs table)."""
    return json.dumps({"max": pool.max, "draining": pool.draining, "queued": queued})


def github_poll_due(cfg: Config, conn) -> bool:
    """GitHub is read at most every github.poll_seconds; the rest of the poll (local tickets, approvals, schedules) runs every time."""
    if cfg.github_poll_seconds <= cfg.poll_seconds:
        return True
    now = time.time()
    last = float(dbm.get_status(conn).get("last_github_poll", {}).get("value") or 0)
    if now - last < cfg.github_poll_seconds - 1:
        return False
    dbm.set_status(conn, "last_github_poll", str(now))
    return True


def stop_closed(cfg: Config, gh: GitHub, conn) -> None:
    """Stop the work on a ticket that was closed (from the UI or on GitHub) while a job runs for it. The issue state is the signal:
    the job kills its container and publishes nothing, and its queued worker checks are cancelled."""
    names = {r.lower(): r for r in cfg.repos}
    for job in pool.running():
        repo, num = names.get(job["repo"]), job["issue"]
        if repo is None or num <= 0:
            continue
        try:
            if gh.get_issue(repo, num).get("state") != "closed" or not pool.cancel(jobkey(repo, num)):
                continue
            jobs.cancel_for(conn, repo, num, time.time())
            dbm.set_questions(conn, repo, num, "", 0)
            emit("run:cancelling", "the ticket was closed: stopping its work", repo, num)
        except Exception:
            log.exception("could not check whether %s#%s was closed", repo, num)       # never stops the poll


warned_no_tickets: list[bool] = []


def poll_once(cfg: Config, gh: GitHub, conn, classifier) -> None:
    queued.clear()
    github_due = github_poll_due(cfg, conn)
    if hasattr(gh, "github_due"):
        gh.github_due = github_due and cfg.github_issues_enabled      # off: only local tickets are listed
    if not cfg.github_issues_enabled and not cfg.local_enabled and not warned_no_tickets:
        warned_no_tickets.append(True)
        log.warning("github.issues_enabled and local.enabled are both off: the factory has no tickets to work on")
    if cfg.local_enabled and not cfg.dry_run:                  # an import reads one GitHub issue by number, so it works with GitHub issues off
        try:
            tracker.process_imports(cfg, gh, conn, frozenset(label for _, label in triggers(cfg)), emit)
        except Exception:
            log.exception("import of GitHub issues failed")      # never stops the poll
    if not cfg.dry_run:
        stop_closed(cfg, gh, conn)
        process_approvals(cfg, gh, conn, classifier)
    for repo in cfg.repos:
        handled: set[int] = set()
        todo = []
        for kind, label in triggers(cfg):
            for issue in gh.labeled_issues(repo, label):
                if issue["number"] in handled:            # another trigger label on it is handled first; this one waits
                    continue
                handled.add(issue["number"])
                todo.append((kind, label, issue))
        todo.sort(key=lambda t: priority_rank(t[2]))       # stable: equal priority keeps trigger order
        cache, now_held = {}, {}
        for kind, label, issue in todo:
            num = issue["number"]
            # A build waits for its open blockers (only while the PM is on). Like a ticket waiting for a slot, it is left untouched,
            # so a later poll starts it once they are closed. Checked only before a start: a running job is never stopped.
            if (kind in pm.BUILD_KINDS and cfg.pm.enabled and not dbm.seen(conn, repo, num, issue["updated_at"])
                    and not pool.busy(jobkey(repo, num)) and (b := pm.blockers(cfg, gh, conn, repo, issue, cache, trusted))):
                now_held[num] = b
                queued.append({"repo": repo, "issue": num, "kind": kind, "title": issue.get("title", "")[:120],
                               "reason": "blocked by " + ", ".join(f"#{n}" for n in b)})
                continue
            if handle_issue(cfg, gh, conn, classifier, repo, issue, kind, label) == "paused":
                return
        report_blocked(cfg, repo, now_held)
    if cfg.comments.enabled and not cfg.dry_run:
        for repo in cfg.repos:                              # after every repository's tickets had their chance at a slot
            try:
                triage_scan(cfg, gh, conn, repo, github_due)
                triage_process(cfg, gh, conn, repo, github_due)
            except Exception:
                log.exception("comment triage of %s failed", repo)           # never stops the poll
    for repo in cfg.repos:                                  # after every repository's tickets had their chance at a slot
        try:
            maybe_pm_sweep(cfg, gh, conn, repo)
        except Exception:
            log.exception("project manager sweep of %s failed", repo)        # never stops the poll
    try:
        if cfg.github_issues_enabled:
            schedules.tick(cfg, gh, conn, time.time(), Path(cfg.db_path).parent, emit, alert)
    except Exception:
        log.exception("scheduled jobs failed")                  # never stops the poll
    try:
        scanner.tick(cfg, gh, conn, time.time(), Path(cfg.db_path).parent, emit, alert)
    except Exception:
        log.exception("code-smell scans failed")                # never stops the poll
    try:
        screenboard.tick(cfg, conn, time.time(), gh.token, lambda: dbm.connect(cfg.db_path), emit)
    except Exception:
        log.exception("screens board failed")                   # never stops the poll
    try:
        captures.tick(cfg, gh, conn, time.time())
    except Exception:
        log.exception("screens: Playwright runs failed")        # never stops the poll
    try:
        verify.watch(cfg, conn, lambda text, event="worker_offline": alert(text, event=event))
    except Exception:
        log.exception("worker watch failed")                    # never stops the poll
    if not cfg.dry_run and github_due:
        ci.watch_ci(cfg, gh, conn, lambda text, event="ci_result": alert(text, event=event),
                    lambda *a: run_fix(cfg, gh, *a), ci_submit(cfg, conn))
        conflicts.watch(cfg, gh, conn, lambda text, event="conflict": alert(text, event=event))


def check_usage(cfg: Config, conn) -> None:
    """Refresh the plan-usage numbers (throttled) and alert once when a window crosses usage.LOW_PERCENT."""
    if cfg.dry_run:
        return
    try:
        was = set(usage.over_limit(usage.load(conn)))
        cur = usage.refresh(conn, cfg.runner.claude_env_file)
        if new := [w for w in usage.over_limit(cur) if w not in was]:
            alert(f"Claude {' and '.join(new)} usage is at {usage.LOW_PERCENT}% or more.\n{usage.summary(cur)}", event="rate_limit")
    except Exception:
        log.exception("usage check failed")             # never stop the poll loop for a meter


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
    restored = None if args.once else backup.apply_pending(Path(cfg.db_path).parent, cfg.db_path)   # a restore staged in the UI, before the database is opened
    clf = RuleClassifier(cfg.kind_aliases)
    if cfg.classifier_backend == "jev" and cfg.openrouter_key_file and Path(cfg.openrouter_key_file).exists():
        clf = JevClassifier(Path(cfg.openrouter_key_file).read_text().strip(), cfg.jev_model, kind_aliases=cfg.kind_aliases)
    log.info("classifier: %s", type(clf).__name__)
    global alert_filter, slack_filter
    alert_filter = lambda event: event_enabled(cfg.telegram_verbosity, event, cfg.telegram_events)
    slack_filter = lambda event: event_enabled(cfg.slack_verbosity, event, cfg.slack_events)
    global ev_path, pool, step_gh
    ev_path = cfg.db_path
    conn, gh = dbm.local(cfg.db_path), tracker.Hub(token, cfg.db_path)     # the poll thread's connection; every worker opens its own
    if not args.once:               # --once runs its jobs inline, one after another, and returns when they are done
        pool = Pool(cfg.runner.max_parallel, threaded=True)
    log.info("running up to %d job(s) at once", pool.max)
    step_gh = gh if cfg.subtasks.enabled and cfg.github_issues_enabled and not cfg.dry_run and token else None
    if not args.once:
        stranded = dbm.mark_interrupted(conn)
        chat.mark_interrupted(conn)
        emit("startup", f"orchestrator started ({'dry-run' if cfg.dry_run else 'LIVE'}), {len(cfg.repos)} repo(s)"
                        + (f"; {stranded} run(s) were interrupted by the restart" if stranded else ""))
        if restored:
            emit("restore", f"restored from a backup made by factory {restored.get('factory_version') or '?'}; the factory is paused until you resume it "
                            f"(the previous database is state/{restored['previous']})")
    global tg, sl
    tf = cfg.telegram_token_file
    if tf and cfg.telegram_chat_id and Path(tf).exists() and not args.once:
        tg = Telegram(Path(tf).read_text().strip(), cfg.telegram_chat_id, str(Path(cfg.db_path).parent),
                      cfg.db_path, cfg.repos)
        tg.start()
    sb, sa = cfg.slack_bot_token_file, cfg.slack_app_token_file
    if sb and sa and Path(sb).exists() and Path(sa).exists() and not args.once:
        # Connected as soon as both tokens are saved, so /factory can be typed while setting up and the UI can offer 'use this';
        # nobody is obeyed and nothing is sent until the channel and the member id are set.
        listener = Slack(Path(sb).read_text().strip(), Path(sa).read_text().strip(), cfg.slack_channel, cfg.slack_user_id,
                         str(Path(cfg.db_path).parent), cfg.db_path, cfg.repos)
        listener.start()
        sl = listener if cfg.slack_channel and cfg.slack_user_id else None
    if (tg or sl) and not args.once:
        alert(f"Factory started ({'dry-run' if cfg.dry_run else 'LIVE'}). {len(cfg.repos)} repo(s).", event="startup")
    if (warning := resource_warning(cfg.runner)) and not args.once:
        log.warning("%s", warning)
        alert(warning, event="startup")
    if not args.once and cfg.chat.enabled:
        threading.Thread(target=chat_lane, args=(cfg, gh), daemon=True, name="chat-lane").start()
    if not args.once and not cfg.dry_run:
        recover(cfg, gh)
    while True:
        if not args.once:
            maybe_restart(Path(cfg.db_path).parent)
        try:
            dbm.set_status(conn, "mode", "dry-run" if cfg.dry_run else "LIVE")
            dbm.set_status(conn, "paused", pause.paused(Path(cfg.db_path).parent) or "")
            dbm.set_status(conn, "poll_started", str(time.time()))
            check_usage(cfg, conn)
            try:
                tools.tick(cfg, Path(cfg.db_path).parent)        # a requested CLI update, or the periodic version scan (background thread)
            except Exception:
                log.exception("tool update check failed")       # never stops the poll
            poll_once(cfg, gh, conn, clf)
            dbm.set_status(conn, "pool", pool_status())
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
