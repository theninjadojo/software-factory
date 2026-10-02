"""Structured open questions from the analyst, designer and architect, and a person's answers to them.

A role agent ends its document with a `factory-questions` block. That block is untrusted agent output, so it is validated like
the classifier's answers: if anything in it is malformed the whole block is ignored, and the ticket behaves as before (the
classifier reads the document). Each question has a class. The agent proposes it, and the fixed rules here can only make it
stricter (safe-default -> needs-person), never the reverse. Only safe defaults are ever auto-accepted.

Answers: a person clicks an option on the UI's Tickets page or a Telegram button, and the factory posts the choice as an
answers comment from its own account. The factory ignores its own comments when it reads the discussion, so answers are read
here instead. One counts only if the factory's account wrote it, it starts with the marker, it was posted after the stage
document it answers, and it names a question in that document and one of the question's option ids. It is a person's choice
among options the agent offered. It is data, never an instruction.
"""
import json
import re
from dataclasses import dataclass, field

from .sanitize import sanitize_markdown

SAFE, PERSON = "safe-default", "needs-person"
BLOCK = re.compile(r"^```factory-questions[ \t]*\n(.*?)^```[ \t]*$\n?", re.M | re.S)
STAGE_HEAD = re.compile(r"<!-- factory:stage=([a-z]+) -->\n")
DATA_HEAD = "<!-- factory:questions "
ANSWERS = "<!-- factory:answers -->"
QID = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")
OID = re.compile(r"[a-z0-9][a-z0-9-]{0,7}")
MAX_QUESTIONS, MAX_TEXT, MAX_LABEL, MAX_REASON, MAX_OTHER = 10, 500, 200, 300, 1000

# The rules may only downgrade a class. A false positive costs a click, so they are deliberately broad.
RULES = (
    (re.compile(r"\.github|\.git\b|\.claude|\.githooks|\.agents|\.husky|\.mcp\.json|codeowners|\bgit config", re.I), "mentions a protected path"),
    (re.compile(r"credential|secret|token|password|passphrase|api[ _-]?key|private key|ssh key|\.env\b", re.I), "mentions credentials"),
    (re.compile(r"delet|\bdrop\b|truncat|destroy|destruct|wipe|purge|\berase|rm -rf|force[- ]?push|irreversib|migrat|backfill", re.I),
     "involves a destructive step or a migration"),
    (re.compile(r"permission|privilege|\bsudo\b|\broot\b|\badmin|access control|\bauth(?:n|z|entication|ori[sz]ation)?\b|allow-?list|"
                r"trust|\brls\b|row[- ]level", re.I), "involves permissions or a trust boundary"),
    (re.compile(r"secur|vulnerab|sandbox|inject|encrypt|\bcsrf\b|\bxss\b|\bcors\b|privacy|personal data|\bpii\b|gdpr", re.I), "involves security or privacy"),
    (re.compile(r"\bcosts?\b|billing|pricing|\bpaid\b|quota|\bspend|budget|subscription|invoice", re.I), "involves cost"),
)


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    options: tuple                  # ((option id, label), ...)
    recommended: str
    reason: str
    cls: str
    why: str = ""                   # why the rules made it needs-person ("" if the agent chose that, or it is safe)

    @property
    def safe(self) -> bool:
        return self.cls == SAFE

    def label(self, oid: str) -> str | None:
        return dict(self.options).get(oid)


@dataclass
class StageQuestions:
    stage: str
    questions: list
    answers: dict = field(default_factory=dict)       # question id -> ("option", option id) | ("other", text), set by a person

    def pending(self) -> list:
        """Questions that need a person and have no answer yet: the only ones that stop a ticket."""
        return [q for q in self.questions if not q.safe and q.id not in self.answers]


def _line(value, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("not a string")
    value = " ".join(value.split())
    if not value or len(value) > limit:
        raise ValueError("empty or too long")
    return value


def enforce(q: Question) -> Question:
    """Fixed rules over everything the agent wrote for the question. They can only turn safe into needs-person."""
    if not q.safe:
        return q
    text = " ".join([q.text, q.reason, *(label for _, label in q.options)])
    why = next((reason for rx, reason in RULES if rx.search(text)), None)
    return Question(q.id, q.text, q.options, q.recommended, q.reason, PERSON, why) if why else q


def _question(d) -> Question:
    if not isinstance(d, dict):
        raise ValueError("not an object")
    qid = d.get("id")
    if not isinstance(qid, str) or not QID.fullmatch(qid):
        raise ValueError("bad id")
    opts = d.get("options")
    if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
        raise ValueError("2 to 4 options")
    options = []
    for o in opts:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not OID.fullmatch(o["id"]):
            raise ValueError("bad option")
        options.append((o["id"], _line(o.get("label"), MAX_LABEL)))
    if len({oid for oid, _ in options}) != len(options):
        raise ValueError("duplicate option id")
    rec = d.get("recommended")
    if rec not in {oid for oid, _ in options}:
        raise ValueError("recommended is not an option")
    cls = d.get("class")
    if cls not in (SAFE, PERSON):
        raise ValueError("bad class")
    why = d.get("why", "")
    if not isinstance(why, str) or len(why) > 100:
        raise ValueError("bad why")
    return enforce(Question(qid, _line(d.get("question"), MAX_TEXT), tuple(options), rec, _line(d.get("reason"), MAX_REASON),
                            cls, " ".join(why.split()) if cls == PERSON else ""))


def parse(raw: str) -> list | None:
    """The questions in a block's JSON, validated and with the rules applied; None if anything is malformed."""
    try:
        d = json.loads(raw)
        qs = d.get("questions") if isinstance(d, dict) else None
        if not isinstance(qs, list) or len(qs) > MAX_QUESTIONS:
            return None
        out = [_question(q) for q in qs]
    except (ValueError, TypeError, RecursionError):
        return None
    return out if len({q.id for q in out}) == len(out) else None


def extract(doc: str) -> tuple[str, list | None]:
    """(the document without its questions block, the questions or None). With no block or a malformed one: None."""
    blocks = BLOCK.findall(doc)
    if not blocks:
        return doc, None
    return BLOCK.sub("", doc).rstrip(), parse(blocks[-1])


def _escaped_json(obj) -> str:
    """JSON that cannot end an HTML comment, ping anyone or close a code span."""
    s = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("@", "\\u0040").replace("`", "\\u0060")


def stored(questions: list) -> str:
    """The data line the factory puts right after the stage marker of its own comment (agent text can never be there)."""
    data = [{"id": q.id, "question": q.text, "options": [{"id": o, "label": lab} for o, lab in q.options],
             "recommended": q.recommended, "reason": q.reason, "class": q.cls, "why": q.why} for q in questions]
    return DATA_HEAD + _escaped_json({"questions": data}) + " -->\n"


def read_stored(text: str) -> tuple[list | None, str]:
    """(questions, rest) from a stage document's text after its marker. Only its FIRST line is read, and it is
    validated again (the rules are applied again too, so a stored class can never be safer than the rules allow)."""
    if not text.startswith(DATA_HEAD):
        return None, text
    line, _, rest = text.partition("\n")
    if not line.endswith(" -->"):
        return None, text
    return parse(line[len(DATA_HEAD):-4]), rest


def answer_ok(q: Question, ans) -> bool:
    if not isinstance(ans, (tuple, list)) or len(ans) != 2:
        return False
    kind, value = ans
    if kind == "option":
        return isinstance(value, str) and q.label(value) is not None
    if kind == "other":
        return isinstance(value, str) and 0 < len(" ".join(value.split())) <= MAX_OTHER
    return False


def valid_answers(questions: list, picks: dict) -> dict:
    """Only answers to questions that exist, with one of their option ids (or a non-empty free-text 'other')."""
    by_id = {q.id: q for q in questions}
    out = {}
    for qid, ans in (picks or {}).items():
        q = by_id.get(qid)
        if q and answer_ok(q, ans):
            kind, value = ans
            out[qid] = (kind, " ".join(value.split()) if kind == "other" else value)
    return out


def answers_comment(stage: str, questions: list, picks: dict, source: str) -> str:
    """The answers comment. The first two lines are data the factory reads; the rest is for people."""
    picks = valid_answers(questions, picks)
    data = {"stage": stage, "answers": {k: {"option": v} if kind == "option" else {"other": v} for k, (kind, v) in picks.items()}}
    by_id = {q.id: q for q in questions}
    lines = [f"- **{k}.** {by_id[k].text} → " + (by_id[k].label(v) if kind == "option" else f"Other: {v}") for k, (kind, v) in picks.items()]
    return (ANSWERS + "\n<!-- " + _escaped_json(data) + " -->\n"
            + sanitize_markdown(f"**Answers to the {stage}'s open questions**, recorded by a signed-in person ({source}):\n\n" + "\n".join(lines), 8000))


def read_answers(body: str) -> tuple[str, dict] | None:
    """(stage, raw answers) from an answers comment, or None. The caller checks the author and validates the answers."""
    if not body.startswith(ANSWERS + "\n<!-- "):
        return None
    line = body[len(ANSWERS) + 1:].partition("\n")[0]
    if not line.endswith(" -->"):
        return None
    try:
        d = json.loads(line[5:-4])
        stage, raw = d["stage"], d["answers"]
        if not isinstance(stage, str) or not isinstance(raw, dict):
            return None
        picks = {}
        for k, v in raw.items():
            if isinstance(v, dict) and len(v) == 1:
                kind, value = next(iter(v.items()))
                picks[k] = (kind, value)
        return stage, picks
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def from_comments(comments: list, me: str) -> list:
    """Each stage's latest questions and the answers given since that document, oldest stage first. Only comments
    authored by our own account count: anyone else's comment with either marker is ignored."""
    stages: dict[str, StageQuestions] = {}
    for c in comments:                                   # oldest first
        if (c.get("user") or {}).get("login") != me:
            continue
        body = c.get("body") or ""
        if (m := STAGE_HEAD.match(body)):
            stages.pop(m.group(1), None)                 # a newer document replaces the stage's questions and their answers
            qs, _ = read_stored(body[m.end():])
            if qs is not None:
                stages[m.group(1)] = StageQuestions(m.group(1), qs)
        elif (got := read_answers(body)) and got[0] in stages:
            st = stages[got[0]]
            st.answers.update(valid_answers(st.questions, got[1]))
    return list(stages.values())


def latest(stages: list) -> StageQuestions | None:
    """The stage a person is asked about: the most recent one with a question still waiting, else the most recent one."""
    return next((s for s in reversed(stages) if s.pending()), stages[-1] if stages else None)


class Refused(ValueError):
    """An answer that does not fit the ticket's current questions. The message is safe to show."""


def record(gh, repo: str, num: int, stage: str | None, picks: dict | None, source: str, accept_all: bool = False) -> tuple[str, bool]:
    """Post a person's answers as the factory's account, after checking them against the questions the factory itself
    posted on the ticket (re-read from GitHub, never taken from the request). accept_all answers every question without an
    answer with its recommendation. Returns (stage, True when no question needing a person is left)."""
    stages = from_comments(gh.issue_comments(repo, num), gh.login())
    cur = next((s for s in stages if s.stage == stage), None) if stage else latest(stages)
    if cur is None or not cur.questions:
        raise Refused("This ticket has no open questions to answer.")
    if accept_all:
        chosen = {q.id: ("option", q.recommended) for q in cur.questions if q.id not in cur.answers}
    else:
        chosen = valid_answers(cur.questions, picks)
        if not chosen or len(chosen) != len(picks or {}):
            raise Refused("That answer does not match the ticket's questions. Reload the page.")
    if chosen:
        gh.comment(repo, num, answers_comment(cur.stage, cur.questions, chosen, source))
    return cur.stage, not StageQuestions(cur.stage, cur.questions, {**cur.answers, **chosen}).pending()


def describe(q: Question, ans) -> str:
    if ans:
        kind, value = ans
        return f"{q.label(value) if kind == 'option' else 'Other: ' + value} (answered by a person)"
    if q.safe:
        return f"Assumed: {q.label(q.recommended)} (recommended, auto-accepted)"
    return f"not answered yet (needs a person; recommended: {q.label(q.recommended)})"


def summary(stages: list) -> str:
    """What the next stage's agent is told about the earlier open questions."""
    return "\n".join(f"- {s.stage} {q.id}. {q.text} -> {describe(q, s.answers.get(q.id))}" for s in stages for q in s.questions)


def section(questions: list) -> str:
    """The open questions as people read them on the ticket (sanitized by the caller)."""
    if not questions:
        return ""
    out = ["### Open questions"]
    for q in questions:
        cls = "safe default" if q.safe else "needs a person" + (f" ({q.why})" if q.why else "")
        opts = "\n".join(f"- `{o}` {lab}" + (f" **(recommended: {q.reason})**" if o == q.recommended else "") for o, lab in q.options)
        out.append(f"**{q.id}. {q.text}** · _{cls}_\n{opts}" + (f"\n\n{describe(q, None)}" if q.safe else ""))
    if any(q.safe for q in questions):
        out.append("_A person can overturn an assumption with a comment, or on the factory UI's Tickets page, before the build._")
    return "\n\n".join(out)
