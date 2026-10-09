"""Second opinions on open questions, so the factory asks a person less often.

A stage agent's needs-person questions are put to independent judges before anyone is asked. Each judge is shown the question in
a different way, so agreement means more than one model agreeing with itself:
- Jev (the classifier on OpenRouter) is blind: it sees the ticket, the document and the options, never the recommendation. It can
  only pick one of the options, so the ticket's text cannot make it say anything else.
- An agent in the sandbox reads the code. It is shown the recommendation and asked to argue against it and to find evidence in
  the repositories, and replies with a machine-read block that is validated like every other agent block.
A question is answered automatically only when the judges agree with the stage agent's recommendation, each at or above
questions.min_confidence ("all"), or at least one does and none disagrees ("any"). Any failure, malformed reply or disagreement
leaves the question for a person, as before. Questions whose text touches a category in questions.always_ask (credentials,
destructive steps, security ...) are never put to the judges. A person can change an automatic answer afterwards.
"""
import json
import logging
import re
from dataclasses import dataclass

from . import jev
from . import questions as Q

log = logging.getLogger("factory.judges")
BLOCK = re.compile(r"^```factory-second-opinion[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
MAX_REASON, MAX_EVIDENCE, MAX_HISTORY = 300, 200, 20


@dataclass(frozen=True)
class Verdict:
    judge: str             # "jev" or the agent's model
    option: str            # the option id it chose
    confidence: float      # 0 to 1
    reason: str = ""
    evidence: str = ""     # agent only: a path or commit it found

    def agrees(self, q, floor: float) -> bool:
        return self.option == q.recommended and self.confidence >= floor


def blocked(q, always_ask) -> set:
    """The always-ask categories the question touches (an empty set: the judges may answer it)."""
    return Q.categories(q) & set(always_ask)


def history_lines(rows: list) -> list:
    """A person's earlier answers in this repository, oldest first, as data the judges may learn the owner's taste from."""
    return [{"question": r["question"][:300], "chosen": r["chosen"][:200], "recommended": r["recommended"][:200]} for r in rows[-MAX_HISTORY:]]


def jev_verdicts(key: str, model: str, title: str, body: str, stage: str, doc: str, questions: list, history: list,
                 post=None) -> dict:
    """question id -> Verdict from Jev, blind to the recommendation. {} on any failure."""
    state = {"title": title[:300], "body": body[:8000], "stage": stage, "stage_document": doc[:12000]}
    if history:
        state["owner_past_answers"] = history
    asks = {f"q_{q.id}": {"type": "choice",
                          "instructions": (f"{q.text} Choose the option that is right for this project, judging from the ticket, the "
                                           f"{stage}'s document and, when given, how the owner answered similar questions before."),
                          "criteria": {o: label for o, label in q.options}} for q in questions}
    try:
        resp = (post or (lambda b: jev.post(key, b)))({"model": model, "state": state, "questions": asks})
        out = {}
        for q in questions:
            a = resp["answers"].get(f"q_{q.id}") or {}
            if a.get("type") == "choice" and q.label(a.get("choice")) is not None:
                out[q.id] = Verdict("jev", a["choice"], max(0.0, min(1.0, float(a["confidence"]))))
        return out
    except Exception as e:
        log.warning("jev second opinion failed (%s: %s)", type(e).__name__, str(e)[:120])
        return {}


def agent_brief(questions: list) -> str:
    """The questions for the agent judge, with the recommendation it is asked to challenge. Built from validated values only."""
    out = []
    for q in questions:
        opts = "\n".join(f"  - {o}: {label}" for o, label in q.options)
        out.append(f"{q.id}. {q.text}\n{opts}\n  recommended: {q.recommended} ({q.reason})")
    return "\n".join(out)


def parse_agent(text: str, questions: list, model: str) -> dict:
    """question id -> Verdict from the agent's factory-second-opinion block. Anything malformed is dropped: {} for a bad block."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return {}
    try:
        d = json.loads(blocks[-1])
        rows = d.get("answers") if isinstance(d, dict) else None
        if not isinstance(rows, list):
            return {}
    except (ValueError, RecursionError):
        return {}
    by_id, out = {q.id: q for q in questions}, {}
    for r in rows:
        if not isinstance(r, dict) or r.get("id") not in by_id or r["id"] in out:
            continue
        q, conf = by_id[r["id"]], r.get("confidence")
        if q.label(r.get("option")) is None or isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            continue
        reason, evidence = r.get("reason", ""), r.get("evidence", "")
        if not isinstance(reason, str) or not isinstance(evidence, str):
            continue
        out[q.id] = Verdict(model, r["option"], float(conf), " ".join(reason.split())[:MAX_REASON], " ".join(evidence.split())[:MAX_EVIDENCE])
    return out


def agreed(q, verdicts: list, judges: int, floor: float, require: str) -> bool:
    """judges: how many judges were asked about q. "all": every one answered and agrees. "any": one agrees and none confidently disagrees."""
    if not verdicts:
        return False
    if require == "all":
        return len(verdicts) == judges and all(v.agrees(q, floor) for v in verdicts)
    return (any(v.agrees(q, floor) for v in verdicts)
            and not any(v.option != q.recommended and v.confidence >= floor for v in verdicts))


def why(verdicts: list) -> str:
    """One line for people: who agreed, how sure, and the agent's evidence."""
    parts = []
    for v in verdicts:
        bit = f"{v.judge} {v.confidence:.2f}"
        if v.evidence:
            bit += f", evidence: {v.evidence}"
        parts.append(bit)
    reason = next((v.reason for v in verdicts if v.reason), "")
    return ("agreed with the recommendation (" + "; ".join(parts) + ")" + (f". {reason}" if reason else ""))[:Q.MAX_REASON]


def decide(cfg, questions: list, already: int, ask_jev, ask_agent) -> dict:
    """question id -> why, for the questions to answer automatically with their recommendation. ask_jev(questions) and
    ask_agent(questions) return question id -> Verdict (either may be None when that judge is off). already: automatic answers this
    ticket has had, counted against questions.max_auto_per_ticket. Every failure answers nothing."""
    qc = cfg.questions
    room = qc.max_auto_per_ticket - already
    candidates = [q for q in questions if not blocked(q, qc.always_ask)]
    if not candidates or room <= 0:
        return {}
    judges = [j for j in (ask_jev, ask_agent) if j is not None]
    if not judges:
        return {}
    got: dict = {q.id: [] for q in candidates}
    left = candidates
    for i, ask in enumerate(judges):
        try:
            found = ask(left) or {}
        except Exception:
            log.exception("a second opinion failed")
            found = {}
        for qid, v in found.items():
            got[qid].append(v)
        if qc.require == "all" and i + 1 < len(judges):        # a later (dearer) judge is only asked about what can still pass
            left = [q for q in left if q.id in found and found[q.id].agrees(q, qc.min_confidence)]
            if not left:
                return {}
    yes = {q.id: why(got[q.id]) for q in candidates if agreed(q, got[q.id], len(judges), qc.min_confidence, qc.require)}
    return yes if len(yes) <= room else {}             # more than the cap allows: a person looks at all of them
