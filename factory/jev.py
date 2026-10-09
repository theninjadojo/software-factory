"""Jev (TypeSafe) issue classifier via OpenRouter's Decisions API (alpha).
Jev reads the whole ticket (title, description, discussion) and returns typed answers: what kind of change it is,
which agent tier should handle it, how much reasoning it needs, and whether a person is needed first.
It never returns text, so ticket content can only select among allowed values. Any failure falls back to labels."""
import dataclasses
import json
import logging
import urllib.request

from .classifier import COMPLEXITY, KINDS, Classification, RuleClassifier, kind_from_labels

log = logging.getLogger("factory.jev")
URL = "https://openrouter.ai/api/alpha/decisions"
TIER_TO_LEVEL = {"light": "low", "standard": "medium", "deep": "high"}   # level keys the [routing.*] config table
ORDER = {"low": 0, "medium": 1, "high": 2}
STAGES = ("analyze", "design", "architect", "implement")

QUESTIONS = {
    "kind": {
        "type": "choice",
        "instructions": "What kind of change does this GitHub issue request?",
        "criteria": {
            "bug": "Something is broken or behaves incorrectly.",
            "feature": "A new capability or behavior is requested.",
            "docs": "Documentation, README or comments need to change.",
            "chore": "Maintenance: dependencies, config, refactoring, tooling, typos.",
            "question": "A question or discussion that is not a request to change code.",
        },
    },
    "tier": {
        "type": "choice",
        "instructions": ("Which tier of AI coding agent should handle this ticket? Judge from the whole ticket and the project it belongs to: the "
                         "description, the discussion, and which repositories are involved. Choose the lightest tier that would still get it right first time."),
        "criteria": {
            "light": "Small, well-specified, low-risk edit in one or two files (typo, copy or docs change, simple config, "
                     "one-line fix). The correct change is obvious from the ticket.",
            "standard": "Ordinary bug fix or feature touching a few files within one area; needs reading surrounding code "
                        "and making modest design choices.",
            "deep": "Cross-cutting, architectural, security- or data-sensitive, or subtle change spanning many files or "
                    "packages or repositories that must change together, or where correct behavior needs careful reasoning about edge cases.",
        },
    },
    "stage": {
        "type": "choice",
        "instructions": ("Given the ticket, the project, and which stages are already complete (stages_done), which single stage "
                         "should happen next? Never choose a stage that is already in stages_done. "
                         "Skipping stages is normal: choose the furthest-along stage the ticket genuinely needs, so a small, clear "
                         "ticket goes straight to implement even when stages_done is empty."),
        "criteria": {
            "analyze": "Requirements are unclear, incomplete or have no acceptance criteria; the problem needs analysing first.",
            "design": "Requirements are clear and the work changes what users see or do (UI, UX, flows, copy) and has no design yet.",
            "architect": "Requirements are clear and the work needs a technical plan across components or repositories "
                         "(schema, APIs, migrations, shared packages, release order) before coding.",
            "implement": "The change is clear enough to build now (a small or well-specified ticket needs no earlier stage), "
                         "or the needed earlier stages are done: build it.",
        },
    },
    "effort": {
        "type": "score",
        "instructions": "How much careful step-by-step reasoning will resolving this ticket require?",
        "criteria": [
            "Little: the change is mechanical",
            "Moderate: some analysis or investigation is needed",
            "Extensive: many interacting parts or subtle edge cases",
        ],
    },
    "needs_human": {
        "type": "noul",
        "instructions": ("Does this issue need a person before an engineer could act on it? Judge the ticket as it stands now: the "
                         "discussion can hold stage documents the factory wrote and the answers people gave to their questions, and "
                         "a question a person has already answered no longer needs a person."),
        "criteria": {
            "true": "Still ambiguous or underspecified after the documents and answers in the discussion, or needs a product or "
                    "design decision nobody has made yet, credentials, or access outside the repo.",
            "false": "Clear and actionable from the repository, the stage documents and the answers already given.",
        },
    },
}


def effort_from_score(score: float) -> str:
    """Biased toward more reasoning: under-thinking a hard task costs more than over-thinking an easy one."""
    return "low" if score < 0.6 else "medium" if score < 1.4 else "high"


def parse_answers(answers: dict, labels: set[str], aliases: dict | None = None) -> Classification:
    """Strict parse. Maintainer kind label overrides the model; a complexity label is a floor, never a ceiling."""
    confs = []
    scores = {"kind": None}                      # None: a maintainer label set the kind, so the model's answer was not counted
    kind = kind_from_labels(labels, aliases)
    if kind is None:
        a = answers["kind"]
        if a["type"] != "choice" or a["choice"] not in KINDS:
            raise ValueError("bad kind answer")
        kind = a["choice"]
        confs.append(float(a["confidence"]))
        scores["kind"] = [kind, confs[-1]]
    a = answers["tier"]
    if a["type"] != "choice" or a["choice"] not in TIER_TO_LEVEL:
        raise ValueError("bad tier answer")
    level = TIER_TO_LEVEL[a["choice"]]
    confs.append(float(a["confidence"]))
    scores["size"] = [a["choice"], confs[-1]]
    floor = next((c for c in COMPLEXITY if f"complexity:{c}" in labels), None)
    if floor and ORDER[floor] > ORDER[level]:
        level = floor
    e = answers["effort"]
    if e["type"] != "score":
        raise ValueError("bad effort answer")
    n = answers["needs_human"]
    if n["type"] != "noul":
        raise ValueError("bad needs_human answer")
    p = float(n["noul"])
    confs.append(max(p, 1 - p))
    scores["human"] = ["yes" if p > 0.5 else "no", confs[-1]]
    stage = stage_conf = None
    st = answers.get("stage")
    if st and st.get("type") == "choice" and st.get("choice") in STAGES:      # tolerant: routing never depends on it
        stage, stage_conf = st["choice"], float(st["confidence"])
        scores["stage"] = [stage, stage_conf]
    return Classification(kind, level, needs_human=p > 0.5, confidence=min(confs), effort=effort_from_score(float(e["score"])),
                          stage=stage, stage_confidence=stage_conf, scores=scores)


def build_state(title: str, body: str, labels: list[str], comments: list[str] | None, project: dict | None = None,
                stages_done: list[str] | None = None) -> dict:
    state = {"title": title[:300], "body": body[:15000], "labels": labels}
    if project:
        state["project"] = project
    state["stages_done"] = list(stages_done or [])
    if comments:
        state["discussion"] = [c[:1500] for c in comments[:10]]
    return state


class JevClassifier:
    def __init__(self, api_key: str, model: str = "typesafe/jev-1.13", timeout: int = 20, post=None, kind_aliases: dict | None = None):
        self.key, self.model, self.timeout, self.aliases = api_key, model, timeout, kind_aliases
        self._post = post or self._http_post
        self.fallback = RuleClassifier(kind_aliases)

    def _http_post(self, body: dict) -> dict:
        req = urllib.request.Request(
            URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def classify(self, title: str, body: str, labels: list[str], comments: list[str] | None = None,
                 project: dict | None = None, stages_done: list[str] | None = None) -> Classification:
        try:
            resp = self._post({"model": self.model, "state": build_state(title, body, labels, comments, project, stages_done),
                               "questions": QUESTIONS})
            c = dataclasses.replace(parse_answers(resp["answers"], set(labels), self.aliases), source=self.model)
            log.info("jev: %s tier->%s effort=%s human=%s conf=%.2f cost=$%s", c.kind, c.complexity, c.effort,
                     c.needs_human, c.confidence, resp.get("usage", {}).get("cost"))
            return c
        except Exception as e:
            log.warning("jev failed (%s: %s); falling back to labels", type(e).__name__, str(e)[:120])
            return self.fallback.classify(title, body, labels, comments, project, stages_done)
