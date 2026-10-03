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

from . import ci, conflicts, designfiles, mockups, pause, pm, runner, subtasks
from . import questions as Q
from . import db as dbm
from .classifier import RuleClassifier
from .config import Config, Route, chain, load, project_for, project_info, resource_warning
from .events import event_enabled
from .github import GitHub
from .jev import JevClassifier
from .pool import Pool, key as jobkey
from .roles import MARKER, STAGE_TO_ROLE
from .router import decide, pick_stage
from .sanitize import sanitize_markdown
from .telegram import Telegram

log = logging.getLogger("factory")
WORKING, DONE, FAILED = "factory:working", "factory:pr-open", "factory:failed"
tg: Telegram | None = None
ev = None                                  # sqlite connection for runs/events/status when ev_path is not set (tests)
ev_path: str | None = None                 # set in main(): each thread opens its own connection to this database
step_gh = None                             # GitHub client used to mirror pipeline steps as sub-issues; None = off
alert_filter = lambda event: True      # set from config in main(); read-only afterwards, so safe from every thread
pool = Pool()                              # inline (one job at a time, in the caller's thread); main() starts worker threads
queued: list[dict] = []                    # tickets the last poll left waiting for a free slot (or, with a reason, blocked), for the UI
held: dict[tuple[str, int], tuple] = {}    # poll thread only: (repo, ticket) -> its open blockers, to report each change once
cycles_seen: set = set()                   # poll thread only: (repo, tickets) of each blocker cycle already reported


BOILERPLATE = ("Opened https://", "Factory run did not produce", "Rate limited")
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
    carrying the marker must not be able to steer a later stage or the implementer."""
    try:
        me = gh.login()
        out: dict[str, str] = {}
        for c in gh.issue_comments(repo, num):          # oldest first, so a re-run replaces the earlier document
            m = STAGE_HEAD.match(c["body"])
            if m and c["user"]["login"] == me:
                out[m.group(1)] = Q.read_stored(c["body"][m.end():])[1]       # without the questions data line
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


def start(cfg: Config, conn, repo: str, num: int, kind: str, job) -> None:
    """Hand job(db) for ticket repo#num to the pool. The caller has checked pool.can_take. A job running inline uses conn;
    one on a worker thread uses that thread's own connection to the database."""
    pool.submit(jobkey(repo, num), kind, lambda: job(conn if pool.inline else dbm.local(cfg.db_path)))


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


def run_chain(cfg: Config, gh: GitHub, kind: str, repo: str, issue: dict, route, c=None, stage: str | None = None, **kw):
    """Run the task with the route's model, then with each admin-configured fallback while a model is unavailable (rate
    limit or API error). Every attempt is its own run: fresh workspace, container, branch and `runs` row. The last result is
    returned unchanged when the chain ends, so requeue/backoff/failure handling is as without fallbacks."""
    models = chain(route)
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
    verdict, why = mockups.gate(cfg.mockups, [lb["name"] for lb in issue.get("labels", [])], shown,
                                NO_MOCKUPS in prior.get("designer", "") or MOCKUPS_OFF in prior.get("designer", ""),
                                cfg.mockups.require_approval and design_pr_merged(gh, shown))
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
    kw = dict(prior=prior, mockups=shown, comments=human_comments(gh, repo, num), answers=Q.summary(question_state(gh, repo, num)))
    res, route = run_chain(cfg, gh, "build", repo, issue, route, c, **kw)
    if res.status == "failed" and getattr(res, "screen_failure", None):      # one fix round with the diffs, then it stays failed
        emit("screens:retry", "the screens did not match the baselines: one fix round with the diffs", repo, num)
        res, route = run_chain(cfg, gh, "build", repo, issue, route, c, screen_retry=res.screen_failure, **kw)
    gh.remove_label(repo, num, WORKING)
    if res.status == "rate-limited":
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, "implement")
    elif res.status == "pr":
        gh.add_labels(repo, num, [DONE])
        draft = cfg.review.enabled and cfg.review.auto
        gh.comment(repo, num, f"Opened {res.pr_url} for review." + (" Draft until the automated review is done." if draft else ""))
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
    res, route = run_chain(cfg, gh, "stage", repo, issue, route, c, role.name, role=role.name, prior=stage_outputs(gh, repo, num),
                           comments=comments, answers=Q.summary(before))
    gh.remove_label(repo, num, working_label(role.name))
    if res.status == "rate-limited":
        requeue_rate_limited(cfg, gh, repo, num, trigger_label, role.name)
    elif res.status == "stage":
        if conn is not None:                            # a designer's draft PR is checked for merge conflicts too
            for pm in map(PR_URL.match, (res.pr_url or "").split()):
                if pm:
                    dbm.track_pr(conn, pm.group(1), int(pm.group(2)), repo, num)
        labels = [lb["name"] for lb in gh.get_issue(repo, num).get("labels", [])] + [role.done_label]
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
            nxt, _ = pick_stage(cfg, nxt, stages_done(cfg, labels), len(project_for(cfg, repo).repos) > 1)
            hint = next_hint(cfg, nxt)
            # Keep going only when nothing needs a person and the classifier named a real next step. A needs-a-person question
            # always stops the chain; with only safe defaults the classifier still has to agree (a second opinion that can only
            # stop it). The re-applied label goes through the normal auto gate, which still asks before a build it is unsure
            # about. Stages already done are never chosen again, so a chain always ends.
            go_on = bool(chain and cfg.auto_chain and not pending and not earlier and not nxt.needs_human
                         and (nxt.stage in STAGE_TO_ROLE or nxt.stage == "implement"))
        except Exception:
            log.exception("next-stage suggestion failed")
        if pending or earlier:
            hint = "a person's answer to the open questions " + ", ".join(q.id for q in pending + earlier)
        url = gh.comment(repo, num, stage_comment(role, route, doc, hint, res.files, res.notes, qs, role.design_files))
        gh.add_labels(repo, num, [role.done_label])
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
    else:
        gh.add_labels(repo, num, [FAILED])
        gh.comment(repo, num, f"Factory {role.name} run did not produce a document ({res.status}). A person should take a look.")
        alert(f"{role.name.title()} failed: {repo}#{num} ({res.status})\n{res.detail[:300]}", event="failure")
    return res


def question_message(cfg: Config, repo: str, num: int, pending: list) -> tuple[str, list]:
    """The Telegram message for questions that need a person: Accept recommendations, Open in UI (when its address is
    configured), and one button per option only when there are one or two questions. A button's data is parsed strictly when
    it comes back (telegram.parse_callback) and then checked against the questions on the ticket (questions.record)."""
    text = "Questions for you:\n" + "\n".join(
        f"{q.id}. {q.text[:300]}\n  recommended: {q.label(q.recommended)[:120]} ({q.reason[:150]})" for q in pending)
    rows = [[("Accept recommendations", f"accept|{repo}|{num}")]]
    if cfg.telegram_ui_url:
        from urllib.parse import urlencode
        rows[0].append(("Open in UI", "url:" + cfg.telegram_ui_url + "/tickets?" + urlencode({"repo": repo, "q": f"#{num}"})))
    if len(pending) <= 2:
        rows += [[(f"{q.id}: {lab[:40]}", f"q:{q.id}:{oid}|{repo}|{num}") for oid, lab in q.options] for q in pending]
    return text, [[b for b in row if b[1].startswith("url:") or len(b[1].encode()) <= 64] for row in rows]


def answer_from_telegram(cfg: Config, gh: GitHub, conn, repo: str, num: int, action: str) -> None:
    """Accept recommendations, or one option of one question, chosen by the allowlisted Telegram user. Recorded like an
    answer from the UI; when nothing needing a person is left, the auto label continues the ticket through the normal gates."""
    try:
        if action == "accept":
            stage, done = Q.record(gh, repo, num, None, None, "Telegram", accept_all=True)
        else:
            _, qid, oid = action.split(":")
            stage, done = Q.record(gh, repo, num, None, {qid: ("option", oid)}, "Telegram")
    except Q.Refused as e:
        alert(f"Not recorded for {repo}#{num}: {e}", event="needs_human")
        return
    if done:
        dbm.set_questions(conn, repo, num, stage, 0)
        gh.add_labels(repo, num, [cfg.auto_label])
    emit("answers", f"answers to the {stage}'s questions recorded from Telegram", repo, num)
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
                           comments=human_comments(gh, repo, num), fix_branch=branch, mockups=ticket_mockups(repo, num))
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
        pushed, links = res.status == "pr", "\n".join(u for _, _, _, u, _ in prs)
        # Our own validation messages are safe to show; an agent's log tail (in failed / no-change) only goes to Telegram and the UI.
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
    """Run/Skip decisions made by the allowlisted Telegram chat."""
    if pause.paused(Path(cfg.db_path).parent):
        return
    for repo, num, action in dbm.approvals(conn):
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
            if action == "accept" or action.startswith("q:"):
                answer_from_telegram(cfg, gh, conn, repo, num, action)
                continue
            role = next((r for r in cfg.roles if action == f"stage:{r.name}"), None)
            if action != "run" and role is None:
                continue                            # an unknown action is ignored, never guessed at
            for _, label in triggers(cfg):         # the approval covers the whole ticket: clear EVERY trigger label (factory:auto
                gh.remove_label(repo, num, label)  # included), or finishing the run re-triages it and asks a person again
            def approved(db, repo=repo, num=num, issue=issue, role=role):
                if role:
                    res = dispatch_stage(cfg, gh, classifier, repo, issue, role, conn=db)
                else:
                    res = dispatch(cfg, gh, repo, issue, cfg.routes["medium"], conn=db)
                dbm.record(db, repo, num, issue["updated_at"] + "+approved", f"run:{res.status}", f"approved via telegram; {res.detail}; {res.pr_url or ''}")
            start(cfg, conn, repo, num, role.name if role else "build", approved)
        except Exception:
            log.exception("approval failed for %s#%s", repo, num)


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
        start(cfg, conn, repo, num, "review", review_job)
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
        start(cfg, conn, repo, num, "conflicts", conflicts_job)
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
        c, adjusted = pick_stage(cfg, c, done, len(project_for(cfg, repo).repos) > 1)
        if adjusted:
            summary = f"{summary}; stage {before} -> {c.stage} ({adjusted})"
            emit("decision", f"auto: {c.stage} instead of {before} ({adjusted})", repo, num)
        st = STAGE_TO_ROLE.get(c.stage or "")
        sure = min(c.confidence, c.stage_confidence if c.stage_confidence is not None else c.confidence) >= cfg.confidence_threshold
        # A read-only stage (analyst, designer, architect) only writes a document on the ticket, and low confidence or "needs a person"
        # is exactly why an analyst is the right next step, so it runs without asking. A person is asked before a BUILD, or when
        # the classifier named no stage, or a stage that is already done.
        read_only_pick = bool(st) and st not in done and not cfg.auto_confirm_stages
        if (not read_only_pick and (c.needs_human or not sure) and not cfg.auto_confirm_stages and "analyst" not in done
                and any(r.name == "analyst" for r in cfg.roles)):
            st, read_only_pick = "analyst", True       # unsure what to do: the cheap read-only analyst is the right first step, not a question
            emit("decision", f"unsure ({summary}): running the analyst first", repo, num)
        # A question that needs a person is never auto-resolved: until it is answered, auto does not build (whatever the classifier says).
        if not read_only_pick and (c.needs_human or open_questions or not sure or (st and st in done)):
            reason = ("needs a person" if c.needs_human else "open questions need a person" if open_questions
                      else "low confidence" if not sure else "chose a stage already done")
            dbm.record(conn, repo, num, updated, "human", f"{why}; {summary}; {reason}")
            emit("decision", f"needs a person: {reason} ({summary})", repo, num)
            log.info("%s#%d auto -> human (%s)", repo, num, reason)
            if not cfg.dry_run:
                alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {reason}\n"
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
        start(cfg, conn, repo, num, role.name, stage_job)
        return None

    d = decide(cfg, c)
    detail = f"{why}; {d.reason}; {summary}; route={d.route}"
    if d.action == "dispatch" and not cfg.dry_run:
        def build_job(db):
            res = dispatch(cfg, gh, repo, issue, d.route, c, label, db)
            dbm.record(db, repo, num, updated, f"run:{res.status}", f"{detail}; {res.detail}; {res.pr_url or ''}")
            log.info("%s#%d run %s: %s %s", repo, num, res.status, res.detail, res.pr_url or "")
        start(cfg, conn, repo, num, "build", build_job)
    else:
        dbm.record(conn, repo, num, updated, d.action, detail)
        emit("decision", f"{d.action}: {d.reason} ({summary}){' [dry-run]' if cfg.dry_run else ''}", repo, num)
        log.info("%s#%d -> %s (%s)%s", repo, num, d.action, detail, " [dry-run]" if cfg.dry_run else "")
        if d.action == "human" and not cfg.dry_run:
            alert(f"Needs a person: {repo}#{num}\n{issue['title'][:120]}\nReason: {d.reason}\n"
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


def poll_once(cfg: Config, gh: GitHub, conn, classifier) -> None:
    queued.clear()
    if not cfg.dry_run:
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
    for repo in cfg.repos:                                  # after every repository's tickets had their chance at a slot
        try:
            maybe_pm_sweep(cfg, gh, conn, repo)
        except Exception:
            log.exception("project manager sweep of %s failed", repo)        # never stops the poll
    if not cfg.dry_run:
        ci.watch_ci(cfg, gh, conn, lambda text, event="ci_result": alert(text, event=event),
                    lambda *a: run_fix(cfg, gh, *a), ci_submit(cfg, conn))
        conflicts.watch(cfg, gh, conn, lambda text, event="conflict": alert(text, event=event))


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
    global ev_path, pool, step_gh
    ev_path = cfg.db_path
    conn, gh = dbm.local(cfg.db_path), GitHub(token)     # the poll thread's connection; every worker opens its own
    if not args.once:               # --once runs its jobs inline, one after another, and returns when they are done
        pool = Pool(cfg.runner.max_parallel, threaded=True)
    log.info("running up to %d job(s) at once", pool.max)
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
    if (warning := resource_warning(cfg.runner)) and not args.once:
        log.warning("%s", warning)
        alert(warning, event="startup")
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
