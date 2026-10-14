"""What the JSON API (api.py) returns: plain data built from the same rows the pages draw. Each function copies the fields it names,
never a whole row, so a field added for the pages does not appear in the API by accident. Text is returned as stored: it is not
HTML, and ticket text is untrusted (the command-line client strips control characters before printing it)."""
from .. import db as dbm
from .. import tracker
from . import board, kanban, views

ROLES = ("analyst", "designer", "architect")


def _ref(issue: int) -> str:
    return tracker.display(int(issue))


def pr_json(p: dict) -> dict:
    return {"repo": p["repo"], "number": int(p["number"]), "issue_repo": p.get("issue_repo"), "issue": p.get("issue_num"),
            "status": p.get("status"), "rounds": p.get("rounds"), "summary": p.get("summary") or "", "updated": p.get("updated"),
            "open": views.live_pr(p), "mergeable": p.get("status") == "passed"}


def need_brief(need: dict | None) -> dict | None:
    if not need:
        return None
    return {"kind": "questions" if need.get("questions") else "decision", "reason": need.get("reason") or ""}


def row_json(r: dict) -> dict:
    """One ticket in a list."""
    return {"repo": r["repo"], "issue": int(r["issue"]), "ref": _ref(r["issue"]), "local": tracker.is_local(int(r["issue"])),
            "title": r.get("title") or "", "state": r.get("state"), "closed": bool(r.get("closed")), "at": r.get("at") or "",
            "at_label": board.LABEL.get(r.get("at") or "", ""), "when": r.get("when"), "why": r.get("why") or "",
            "decision": r.get("decision") or "", "lined_up": bool(r.get("lined_up")), "priority": r.get("priority") or "normal",
            "priority_source": r.get("prio_src") or "", "prs": [pr_json(p) for p in r.get("prs") or []], "need": need_brief(r.get("need"))}


def step_json(s: dict) -> dict:
    keys = ("n", "kind", "station", "state", "run_id", "run_kind", "stage", "status", "harness", "model", "effort", "started",
            "finished", "seconds", "tokens", "message", "repeat", "pending")
    return {k: s[k] for k in keys if k in s}


def journey_json(j: dict) -> dict:
    return {"status": j.get("status"), "started": j.get("started"), "finished": j.get("finished"), "seconds": j.get("seconds"),
            "tokens": j.get("tokens"), "models": j.get("models") or {}, "steps": [step_json(s) for s in j.get("steps") or []]}


def questions_json(st) -> dict:
    """A stage's questions (questions.StageQuestions): each with its options, the recommendation, and the answer given so far."""
    pending = {q.id for q in st.pending()}
    qs = []
    for q in st.questions:
        kind, value = st.answers.get(q.id, (None, None))
        qs.append({"id": q.id, "text": q.text, "options": [{"id": o, "label": lab} for o, lab in q.options], "recommended": q.recommended,
                   "reason": q.reason, "needs_person": not q.safe, "why": q.why, "pending": q.id in pending,
                   "answer": {kind: value} if kind else None, "auto": st.auto.get(q.id) or None})
    return {"stage": st.stage, "questions": qs, "pending": [q.id for q in st.pending()]}


def need_json(need: dict | None) -> dict | None:
    """What a ticket waits for a person to do: answer questions, or decide how it goes on."""
    if not need:
        return None
    asks = bool(need.get("questions"))
    out = {"kind": "questions" if asks else "decision", "reason": need.get("reason") or "", "at": need.get("at")}
    if need.get("st") is not None:
        out["questions"] = questions_json(need["st"])
    if not asks:                     # the factory asks how to go on: the choices, and what the classifier made of the ticket
        out["actions"] = [{"action": a, "text": text} for a, text, _ in need.get("acts") or []]
        out.update(ticket_kind=need.get("kind"), complexity=need.get("complexity"), confidence=need.get("conf"))
    return out


def needs_row_json(n: dict) -> dict:
    """One row of Needs you."""
    return {"repo": n["repo"], "issue": int(n["issue"]), "ref": _ref(n["issue"]), "title": n.get("title") or "", **need_json(n)}


def offers_json(row: dict, cfg, has_token: bool) -> dict:
    """What can be done to the ticket from here (kanban.starts_for and moves_for: the same choices the page offers). The API checks
    each request again, so this is a guide, not a permission."""
    note, starts = kanban.starts_for(row, cfg, has_token)
    moves = kanban.moves_for(row, has_token)
    close = next(((k, why) for col, (k, why) in moves.items() if k == "close"), None)
    return {"note": note,
            "start": [{"action": a, "text": word, "does": does} for a, word, does, off in starts if not off],
            "close": close is not None, "reopen": any(k == "reopen" for k, _ in moves.values())}


def local_json(issue: dict | None) -> dict | None:
    """A local ticket's own text, labels and comments (localtickets.ticket)."""
    if issue is None:
        return None
    from . import localtickets as LT
    return {"state": issue.get("state"), "body": issue.get("body") or "", "labels": [x.get("name") for x in issue.get("labels") or []],
            "comments": [{"author": LT.AUTHOR.get(c["user"]["login"], c["user"]["login"]), "at": c.get("created_at"),
                          "body": LT.factory_data(c.get("body") or "")[1]} for c in issue.get("comment_list") or []],
            "attachments": [{"id": a["id"], "name": a["name"], "type": a["mime"], "size": a["size"]} for a in issue.get("attachment_list") or []]}


def detail_json(sel: dict, docs, events, cfg, has_token: bool, local=None) -> dict:
    """One ticket in full."""
    out = row_json(sel)
    out.update({"journey": journey_json(sel["journey"]), "need": need_json(sel.get("need")), "docs": [d for d in docs if d in ROLES],
                "offers": offers_json(sel, cfg, has_token), "queued": bool(sel.get("queued")),
                "outcome": _outcome(sel.get("outcome_full")), "local": local_json(local),
                "events": [event_json(e) for e in events or []]})
    return out


def _outcome(o: dict | None) -> dict | None:
    if not o:
        return None
    return {k: o.get(k) for k in ("tone", "headline", "reason", "sentence", "stage", "prs")}


def run_json(r: dict, full: bool = False) -> dict:
    keys = ("id", "kind", "stage", "repo", "issue", "title", "harness", "model", "effort", "started", "finished", "status", "detail")
    out = {k: r.get(k) for k in keys}
    out["ref"] = _ref(r["issue"]) if r.get("issue") is not None else ""
    out["pr_urls"] = [u for u in (r.get("pr_urls") or "").split() if u]
    out["tokens"] = dbm.run_tokens(r)
    if full:
        out.update(output=r.get("output") or "", log_tail=r.get("log_tail") or "", classification=r.get("classification") or "")
    return out


def event_json(e: dict) -> dict:
    return {k: e.get(k) for k in ("id", "ts", "kind", "repo", "issue", "run_id", "message")}


def doc_json(repo: str, issue: int, stage: str, doc, text: str, source: str, cut: bool) -> dict:
    return {"repo": repo, "issue": int(issue), "ref": _ref(issue), "stage": stage, "text": text, "source": source, "cut_short": cut,
            "run_id": doc["id"] if doc else None}
