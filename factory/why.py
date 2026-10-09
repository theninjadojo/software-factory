"""Why a ticket was handed to a person: the classifier's per-answer scores, stored as JSON on the decision and shown on the ticket
card and in the chat alert. Everything read back is validated, since the UI renders it (a bad value reads as "not available")."""
import json

ROWS = (("kind", "Kind"), ("size", "Size"), ("human", "Needs a person"), ("stage", "Next stage"))
SENTENCES = {
    "human": "The classifier flagged this ticket as needing a person.",
    "questions": "Open questions need a person.",
    "done": "The classifier chose a stage that is already done.",
    "behind": "The classifier chose an earlier stage than one already done.",
    "question": "The classifier says this ticket is a question, which needs a person.",
    "build": "The last stage's document recommends a build, and a build is started by a person.",
    "advice": "The last stage's document asks for a person before anything else runs.",
}


def build(threshold: float, c, reason: str, overall: float | None = None) -> str:
    """reason: low, human, questions, done, behind, question, build or advice. overall: the score compared with the threshold, when it is not c.confidence."""
    return json.dumps({"reason": reason, "threshold": threshold, "confidence": c.confidence if overall is None else overall,
                       "source": c.source, "answers": c.scores or {}})


def parse(text) -> dict | None:
    """The stored scores as a dict with a known reason, numeric threshold and confidence, and only well-formed answers; else None."""
    try:
        d = json.loads(text) if text else None
        if (not isinstance(d, dict) or d.get("reason") not in ("low", *SENTENCES) or isinstance(d.get("threshold"), bool)
                or not isinstance(d.get("threshold"), (int, float)) or not isinstance(d.get("confidence"), (int, float))):
            return None
        answers = d.get("answers") if isinstance(d.get("answers"), dict) else {}
        good = {k: [str(v[0]), float(v[1])] for k, v in answers.items() if k in dict(ROWS) and isinstance(v, list) and len(v) == 2
                and isinstance(v[1], (int, float)) and not isinstance(v[1], bool)}
        if "kind" in answers and answers["kind"] is None:
            good["kind"] = None                                 # set by a maintainer's label
        missing = [m for m in answers.get("missing", []) if m in ("kind", "size")] if isinstance(answers.get("missing"), list) else []
        return {"reason": d["reason"], "threshold": float(d["threshold"]), "confidence": float(d["confidence"]),
                "source": str(d.get("source") or ""), "answers": good, "missing": missing}
    except (ValueError, TypeError, IndexError):
        return None


def weakest(d: dict) -> str | None:
    scored = {k: v[1] for k, v in d["answers"].items() if v}
    return min(scored, key=scored.get) if scored else None


def line(d: dict) -> str:
    """One line for the chat alert."""
    if d["reason"] != "low":
        return SENTENCES[d["reason"]]
    out = f"low confidence {d['confidence']:.2f} (threshold {d['threshold']:.2f})"
    if d["missing"]:
        return out + f". The {' and '.join(d['missing'])} label is missing (labels-only classifier)."
    w = weakest(d)
    return out + (f". Weakest: {w}, {d['answers'][w][0]}, {d['answers'][w][1]:.2f}." if w else "")
