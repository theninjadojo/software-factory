"""The Evaluate page: how well the factory did on its finished tickets, step by step, and what to change.

Everything shown comes from factory/evaluate.py: scores an agent gave from a clean context (validated there) and numbers the
factory computed itself. The page only reads. Its one action, Evaluate now, writes a request the orchestrator picks up at its
next poll; the repository and ticket it names are checked against the configuration and the database."""
import time
from urllib.parse import quote

from .. import evaluate as E
from .. import tracker
from . import labels as L, views
from .views import csrf_field, esc

PERIODS = ((7, "Last 7 days"), (30, "Last 30 days"), (90, "Last 90 days"), (0, "All time"))
OUTCOME = {"merged": ("Merged", "good"), "closed": ("Closed without merging", "warn"), "failed": ("Failed", "bad")}
VERDICT = {"model": {"lighter": "A lighter model would do", "right": "Right model", "heavier": "Needed a stronger model"},
           "effort": {"lighter": "Less effort would do", "right": "Right effort", "heavier": "Needed more effort"}}
LEAN = {"model": ("lighter model would do", "needed a stronger model"), "effort": ("less effort would do", "needed more effort")}
FACTS = (("runs", "Runs"), ("failed_runs", "Failed runs"), ("fallbacks", "Model fallbacks"), ("ci_fix_rounds", "CI fix rounds"),
         ("conflict_rounds", "Merge conflict rounds"), ("questions", "Open questions raised"),
         ("questions_overturned", "Automatic answers a person changed"), ("person_stops", "Stops for a person"),
         ("runs_after_merge", "Runs after the merge"))
RESULT = {"stage": "finished", "pr": "opened a pull request", "failed": "failed", "rate-limited": "hit a rate limit",
          "cancelled": "cancelled", "interrupted": "interrupted"}


def tone(score) -> str:
    return "" if score is None else "good" if score >= 4 else "warn" if score >= 3 else "bad"


def score(v, of: str = "") -> str:
    """A score (or an average of scores) out of 5, coloured by how good it is; a dash where there is none."""
    if v is None:
        return '<span class="ev-score none" title="Not scored">–</span>'
    shown = f"{v:.1f}" if isinstance(v, float) else str(int(v))
    title = f' title="{esc(of)}"' if of else ""
    return f'<span class="ev-score {tone(v)}"{title}><b>{esc(shown)}</b><i>/5</i></span>'


def _href(q: dict, **kw) -> str:
    keep = {k: v for k, v in {"repo": q.get("repo", ""), "days": q.get("days", ""), **kw}.items() if v not in ("", None)}
    return "/evaluate" + ("?" + "&amp;".join(f"{k}={esc(quote(str(v), safe=''))}" for k, v in keep.items()) if keep else "")


def _ticket_href(repo: str, issue: int) -> str:
    return f'/evaluate/ticket?repo={esc(quote(repo, safe=""))}&amp;issue={int(issue)}'


def _args(cfg, q: dict) -> tuple[list[str], str, int]:
    """The repositories shown (one, or every configured one), the chosen one ("" for all) and the period in days (0: all time)."""
    repo = q.get("repo") if q.get("repo") in cfg.repos else ""
    days = int(q["days"]) if q.get("days") in {str(d) for d, _ in PERIODS} else 30
    return ([repo] if repo else list(cfg.repos)), repo, days


def filters(cfg, repo: str, days: int) -> str:
    opts = '<option value="">All repositories</option>' + "".join(
        f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    per = "".join(f'<option value="{d}"{" selected" if d == days else ""}>{esc(t)}</option>' for d, t in PERIODS)
    return (f'<form method="get" action="/evaluate" class="sd-form ev-filters" data-autosubmit>'
            f'<label class="ev-pick">Repository <select name="repo">{opts}</select></label>'
            f'<label class="ev-pick">Evaluated <select name="days">{per}</select></label>'
            f'<noscript><button class="btn secondary">Show</button></noscript></form>')


def queue(cfg, db, repo: str, csrf: str, now: float) -> str:
    """What the evaluator is doing: off, running, waiting (and for what), or up to date; with the Evaluate now button."""
    ev = cfg.evaluate
    if not ev.enabled:
        return ('<div class="tr-state warn" role="status"><p class="tr-state-head"><span class="badge warn">Off</span> '
                "<strong>Evaluation is off.</strong></p><p>When it is on, each finished ticket is evaluated once the factory has "
                "nothing else to do. Every evaluation is one agent run on your Claude plan. "
                '<a href="/settings?section=evaluate">Turn it on in Settings</a>.</p></div>')
    st, run = E.queue_status(db), E.running(db)
    waiting, why = int(st.get("waiting") or 0), str(st.get("why") or "")
    if cfg.dry_run:
        head, text, kind, word = "The factory is in dry-run, so nothing is evaluated.", "", "warn", "Held"
    elif run:
        r = run[0]
        head = f"Evaluating {views.ref(r['repo'], r['issue'], short=True)} for {views.dur(r['created'], now)}."
        text = f"{waiting} more waiting." if waiting else ""
        kind, word = "blue", "Running"
    elif waiting and why:
        head, text, kind, word = f"{waiting} finished ticket(s) waiting.", f"Holding off: {why}.", "warn", "Waiting"
    elif waiting:
        head, text, kind, word = f"{waiting} finished ticket(s) waiting.", "The next one starts at the factory's next poll.", "blue", "Waiting"
    else:
        head, kind, word = "Every finished ticket is evaluated.", "good", "Up to date"
        text = (f"A ticket is evaluated {ev.settle_minutes} minute(s) after its work ends. Tickets finished more than "
                f"{ev.backfill_days} day(s) ago are only evaluated when you ask.")
    scope = f'<input type="hidden" name="repo" value="{esc(repo)}">' if repo else ""
    btn = (f'<form method="post" action="/evaluate/run" class="ev-now">{csrf_field(csrf)}{scope}'
           f'<button class="btn secondary" title="Queue every finished ticket that has no evaluation yet, however old">Evaluate now</button></form>')
    return (f'<div class="tr-state {kind} ev-queue" role="status"><div><p class="tr-state-head"><span class="badge {kind}">{esc(word)}</span> '
            f'<strong>{esc(head)}</strong></p>' + (f"<p>{esc(text)}</p>" if text else "") + f"</div>{btn}</div>")


def tiles(t: dict) -> str:
    def tile(label: str, value: str, note: str) -> str:
        return f'<div class="ev-tile"><span class="ev-lab">{esc(label)}</span><span class="ev-val">{value}</span><span class="muted">{esc(note)}</span></div>'
    n = t["tickets"]
    reopened = f'{round(100 * t["reopened"] / n)}%' if n else "–"
    return ('<div class="ev-tiles">'
            + tile("Ticket quality", score(t["quality"]), "How well the tickets were written")
            + tile("Completion", score(t["completion"]), f'{t["merged"]} of {n} merged')
            + tile("Reopened", f'<b class="ev-num {"bad" if t["reopened"] else ""}">{esc(reopened)}</b>', f'{t["reopened"]} of {n} reopened by a person')
            + tile("Tokens per ticket", f'<b class="ev-num">{esc(views.tok(t["tokens"]))}</b>', "Average over every run of a ticket")
            + "</div>")


def _lean(dim: str, lighter: int, heavier: int) -> str:
    if dim not in LEAN or not (lighter or heavier):
        return ""
    bits = ([f"{lighter} × {LEAN[dim][0]}"] if lighter else []) + ([f"{heavier} × {LEAN[dim][1]}"] if heavier else [])
    return f'<span class="muted ev-lean">{esc(", ".join(bits))}</span>'


def steps_table(steps: list[dict]) -> str:
    if not steps:
        return ""
    head = "".join(f"<th>{esc(E.DIM_NAME[d])}</th>" for d in E.RUN_DIMS)
    rows = ""
    for s in steps:
        cells = ""
        for d in E.RUN_DIMS:
            c = s[d]
            cells += (f'<td data-label="{esc(E.DIM_NAME[d])}"><span class="nowrap">{score(c["avg"])}'
                      + (f' <span class="muted">of {c["n"]}</span>' if c["n"] else "") + "</span>" + _lean(d, c["lighter"], c["heavier"]) + "</td>")
        rows += f'<tr><th scope="row">{esc(s["name"])} <span class="muted">{int(s["runs"])} run(s)</span></th>{cells}</tr>'
    return (f'<h2>By step</h2><div class="scroll"><table class="ev-steps"><thead><tr><th>Step</th>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>")


def improve(items: list[dict], low: int) -> str:
    if not items:
        return ('<h2>What to improve</h2><p class="muted">Nothing scored ' + esc(low) + " or lower in this period.</p>")
    out = ""
    for i in items:
        tips = "".join(f"<li>{esc(t)}</li>" for t in i["suggestions"]) or '<li class="muted">No suggestion was given.</li>'
        out += (f'<li class="ev-fix"><p class="tr-head">{score(i["avg"])} <strong>{esc(i["name"])} · {esc(E.DIM_NAME[i["dimension"]])}</strong> '
                f'<span class="muted">low on {int(i["low"])} of {int(i["of"])}</span>{_lean(i["dimension"], i["lighter"], i["heavier"])}</p>'
                f'<ul class="ev-tips">{tips}</ul></li>')
    return f'<h2>What to improve</h2><p class="muted">Every step and measure with a score of {esc(low)} or lower, worst first, with the latest suggestions.</p><ul class="ev-fixes">{out}</ul>'


def tickets_table(evals: list[dict], now: float) -> str:
    if not evals:
        return ""
    rows = ""
    for e in evals:
        word, kind = OUTCOME.get(e["outcome"], (e["outcome"], ""))
        again = f' <span class="badge bad">Reopened{" × %d" % e["reopens"] if e["reopens"] > 1 else ""}</span>' if e["reopens"] else ""
        lows = [x["score"] for r in e["runs"] for d in E.RUN_DIMS if (x := r.get(d)) and x["score"] is not None]
        rows += (f'<tr><td data-label="Ticket"><a href="{_ticket_href(e["repo"], e["issue"])}">{esc(views.ref(e["repo"], e["issue"], short=True))}</a> '
                 f'<span class="ev-title">{esc(e["title"])}</span></td>'
                 f'<td data-label="Outcome"><span class="badge {kind}">{esc(word)}</span>{again}</td>'
                 f'<td data-label="Ticket quality">{score((e["ticket"].get("quality") or {}).get("score"))}</td>'
                 f'<td data-label="Completion">{score((e["ticket"].get("completion") or {}).get("score"))}</td>'
                 f'<td data-label="Lowest step score">{score(min(lows) if lows else None)}</td>'
                 f'<td data-label="Tokens" class="mono">{esc(views.tok(e["facts"].get("tokens")))}</td>'
                 f'<td data-label="Evaluated" class="muted nowrap" title="{esc(views.ts(e["finished"]))}">{esc(views.ago(e["finished"], now))}</td></tr>')
    return ('<h2>Tickets</h2><div class="scroll"><table class="ev-tickets"><thead><tr><th>Ticket</th><th>Outcome</th><th>Ticket quality</th>'
            f"<th>Completion</th><th>Lowest step score</th><th>Tokens</th><th>Evaluated</th></tr></thead><tbody>{rows}</tbody></table></div>")


def page(cfg, db, q: dict, csrf: str, now: float) -> str:
    repos, repo, days = _args(cfg, q)
    evals = E.latest(db, repos, now - days * 86400 if days else 0) if db is not None else []
    out = (f'<p class="ev-lede muted">How well the factory did on each finished ticket: scores from 1 to 5, judged by an agent with a '
           f"clean context from the ticket, the stage documents and the run numbers.</p>"
           + (queue(cfg, db, repo, csrf, now) if db is not None else "") + filters(cfg, repo, days))
    if not evals:
        return out + '<p class="muted ev-none">No ticket was evaluated in this period.</p>'
    a = E.aggregate(evals, cfg.evaluate.low_score)
    return out + tiles(a["tiles"]) + improve(a["improve"], cfg.evaluate.low_score) + steps_table(a["steps"]) + tickets_table(evals, now)


# ---------------------------------------------------------------- one ticket
def _entry(dim: str, x: dict | None) -> str:
    if x is None:
        return (f'<div class="ev-dim"><p class="ev-dimhead">{score(None)} <strong>{esc(E.DIM_NAME[dim])}</strong></p>'
                '<p class="muted">The evaluator gave no score for this.</p></div>')
    verdict = VERDICT.get(dim, {}).get(x.get("verdict") or "")
    vkind = "good" if x.get("verdict") == "right" else "warn"
    return (f'<div class="ev-dim"><p class="ev-dimhead">{score(x["score"])} <strong>{esc(E.DIM_NAME[dim])}</strong>'
            + (f' <span class="badge {vkind}">{esc(verdict)}</span>' if verdict else "") + "</p>"
            + (f'<p>{esc(x["reason"])}</p>' if x["reason"] else "")
            + (f'<p class="ev-tip"><span class="muted">Suggestion:</span> {esc(x["suggestion"])}</p>' if x["suggestion"] else "") + "</div>")


def _run_card(r: dict, fact: dict) -> str:
    name = E.STEP_NAME.get(r["step"], r["step"])
    attempt = f' <span class="muted">attempt {int(fact["attempt"])}</span>' if fact.get("attempt", 1) > 1 else ""
    meta = " · ".join(x for x in (fact.get("model") or "", (fact.get("effort") or "") and f'{fact["effort"]} effort',
                                  RESULT.get(fact.get("status") or "", fact.get("status") or ""), views.tok(fact.get("tokens")) + " tokens" if fact.get("tokens") is not None else "") if x)
    return (f'<li class="ev-run"><p class="tr-head"><strong>{esc(name)}</strong>{attempt} <span class="muted mono">{esc(meta)}</span> '
            f'<a href="/runs/{int(r["run"])}">Run #{int(r["run"])}</a></p>'
            f'<div class="ev-dims">{"".join(_entry(d, r.get(d)) for d in E.RUN_DIMS)}</div></li>')


def one(e: dict, now: float) -> str:
    """One evaluation: the ticket's two scores, the facts behind them, and a card per run."""
    f = e["facts"]
    facts = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(len(f[k]) if k == 'runs' else f[k])}</dd></div>"
                    for k, label in FACTS if f.get(k) is not None)
    facts += (f'<div><dt>First run to last</dt><dd>{esc(E._dur(f.get("seconds")))}</dd></div>'
              f'<div><dt>Tokens</dt><dd>{esc(views.tok(f.get("tokens")))}</dd></div>')
    by = {r["run"]: r for r in f.get("runs", [])}
    runs = "".join(_run_card(r, by.get(r["run"], {})) for r in e["runs"])
    judged = (f' <a href="/runs/{int(e["run_id"])}">How it was judged: run #{int(e["run_id"])}</a>' if e.get("run_id") else "")
    return (f'<div class="ev-dims ev-ticket">{"".join(_entry(d, e["ticket"].get(d)) for d in E.TICKET_DIMS)}</div>'
            f'<h2>What happened</h2><dl class="ev-facts">{facts}</dl>'
            f'<h2>Step by step</h2><ul class="ev-runs">{runs}</ul>'
            f'<p class="muted">Evaluated {esc(views.ago(e["finished"], now))}.{judged}</p>')


def ticket_page(cfg, db, repo: str, issue: int, pick: str, csrf: str, now: float) -> str:
    rows = E.for_ticket(db, repo, issue) if db is not None else []
    back = f'<p><a href="/evaluate?repo={esc(quote(repo, safe=""))}">← Evaluate</a></p>'
    head = f'<h1 class="ev-h1">{views.ticket_link(repo, issue)} <span class="ev-title">{esc(rows[0]["title"] if rows else "")}</span></h1>'
    again = (f'<form method="post" action="/evaluate/run" class="ev-now">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
             f'<input type="hidden" name="issue" value="{int(issue)}"><button class="btn secondary">Evaluate again</button></form>'
             if cfg.evaluate.enabled else "")
    done = [e for e in rows if e["status"] == "done"]
    shown = next((e for e in done if str(e["id"]) == pick), done[0] if done else None)
    busy = next((e for e in rows if e["status"] == "running"), None)
    failed = rows[0] if rows and rows[0]["status"] == "failed" else None
    note = ""
    if busy:
        note = (f'<div class="tr-state blue" role="status"><p><span class="badge blue">Running</span> A new evaluation started '
                f'{esc(views.ago(busy["created"], now))}.' + (f' <a href="/runs/{int(busy["run_id"])}">Watch the run</a>' if busy.get("run_id") else "") + "</p></div>")
    elif failed:
        note = (f'<div class="tr-state bad" role="status"><p><span class="badge bad">Failed</span> The latest evaluation failed: {esc(failed["detail"])}.'
                + (f' <a href="/runs/{int(failed["run_id"])}">Open the run</a>' if failed.get("run_id") else "") + "</p></div>")
    if shown is None:
        return back + head + note + '<p class="muted">This ticket has no evaluation yet.</p>' + again
    word, kind = OUTCOME.get(shown["outcome"], (shown["outcome"], ""))
    reopened = f' <span class="badge bad">Reopened by a person{" × %d" % shown["reopens"] if shown["reopens"] > 1 else ""}</span>' if shown["reopens"] else ""
    hist = ""
    if len(done) > 1:
        items = "".join(
            f'<li{" class=on" if e["id"] == shown["id"] else ""}><a href="{_ticket_href(repo, issue)}&amp;evaluation={int(e["id"])}"'
            f'{" aria-current=true" if e["id"] == shown["id"] else ""}><b>Evaluation #{int(e["id"])}</b>'
            f'<span class="muted">{esc(views.ago(e["finished"], now))}</span>'
            f'<span class="badge {OUTCOME.get(e["outcome"], ("", ""))[1]}">{esc(OUTCOME.get(e["outcome"], (e["outcome"], ""))[0])}</span>'
            f'<span>{score((e["ticket"].get("completion") or {}).get("score"))} completion</span></a></li>' for e in done)
        hist = f'<h2>Earlier evaluations</h2><ul class="tr-hist">{items}</ul>'
    return (back + head + f'<p class="ev-outcome"><span class="badge {kind}">{esc(word)}</span>{reopened}</p>' + note
            + one(shown, now) + again + hist)


# ---------------------------------------------------------------- handlers
def _send(h, title: str, body: str, csrf: str, bare: bool = False) -> None:
    shown = L.flash_pop(csrf)
    cached = L.needs_cached()
    h._send(200, views.page(title, body, "/evaluate", csrf, wide=True, bare=bare, badges={"/tickets": len(cached)} if cached else None,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        return _send(h, "Evaluate", '<p class="muted">No repository is configured.</p>', csrf)
    db = h.app.ro_db()
    try:
        body = page(cfg, db, q, csrf, time.time())
    finally:
        if db is not None:
            db.close()
    _send(h, "Evaluate", body, csrf)


def ticket_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    repo, n = q.get("repo", ""), q.get("issue", "")
    if repo not in cfg.repos or not n.isdigit() or len(n) > 9 or int(n) < 1:
        return h._send(404, "not found", "text/plain")
    db = h.app.ro_db()
    try:
        body = ticket_page(cfg, db, repo, int(n), q.get("evaluation", ""), csrf, time.time())
    finally:
        if db is not None:
            db.close()
    _send(h, f"Evaluation of {views.ref(repo, int(n), short=True)}", body, csrf, bare=True)


def run_post(h, form, csrf: str) -> None:
    """Evaluate now: one ticket (repo and issue), a repository's finished tickets (repo), or every repository's."""
    cfg = h.app.cfg()
    repo, n = form.get("repo", ""), form.get("issue", "")
    if (repo and repo not in cfg.repos) or (n and (not repo or not n.isdigit() or len(n) > 9 or int(n) < 1)):
        return h._send(400, "bad request", "text/plain")
    where = f"/evaluate/ticket?repo={quote(repo, safe='')}&issue={int(n)}" if n else "/evaluate" + (f"?repo={quote(repo, safe='')}" if repo else "")
    if not cfg.evaluate.enabled:
        L.flash_set(csrf, "Evaluation is off. Turn it on in Settings first.", "bad")
        return h._redirect(where)
    for r in ([repo] if repo else cfg.repos):
        h.app.request_evaluation(r, int(n) if n else 0)
    L.flash_set(csrf, ("Queued. " if n else "Queued every finished ticket that has no evaluation yet. ")
                + "The factory evaluates one ticket at a time, when it has nothing else to do.")
    h._redirect(where)


GET = {"/evaluate": get, "/evaluate/ticket": ticket_get}
POST = {"/evaluate/run": run_post}
