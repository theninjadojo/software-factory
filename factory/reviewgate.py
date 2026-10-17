"""The automated review's verdict gates a factory pull request.

The reviewer starts its document with a verdict line (roles.py). That document is untrusted agent output, so the verdict is read
once, here, by a strict pattern over the first lines only: exactly one of VERDICTS, or "" when none is found (a missing or garbled
line never counts as Looks good). main.review_changes then decides:

* Looks good: the PRs are marked ready, as before.
* Needs changes / Blocking issues: the PRs stay drafts and the build runs a fix round on the same branch with the review as its
  input (the CI fix path, runner.run_task(fix_branch=..., findings=...)), then the review runs again. At most `review.fix_rounds`
  rounds per PR, counted here so a restart or a later manual review never grants more. Once they are used, or a round changes
  nothing, the PRs stay drafts, the ticket gets HELD_LABEL and a person is alerted.
* No readable verdict: the PRs stay drafts and a person is alerted; there is nothing reliable to fix.

With `review.gate` off none of this applies: the PRs are marked ready after any review (state 'ready', the verdict kept for the
UI only) and no fix round runs.

A person can still mark a PR ready on GitHub, or merge it anyway from the admin UI after a warning (recorded as an override).
Nothing here talks to GitHub: this module only parses and keeps state."""
import re
import sqlite3
import time

GOOD, CHANGES, BLOCKING = "Looks good", "Needs changes", "Blocking issues"
VERDICTS = (GOOD, CHANGES, BLOCKING)
STATES = ("ready", "fixing", "held", "override")
HELD_LABEL = "factory:review-held"         # on the ticket while its PRs wait for a person after the review
MAX_FINDINGS = 12000                       # characters of the review handed to a fix round
HEAD_LINES = 15                            # the verdict must be among the document's first lines
# "**Verdict**: Needs changes - why", "1. **Verdict:** Looks good", "## Verdict\n\nBlocking issues": the word Verdict at the start of a
# line (after list or emphasis marks), then separators (quotes and backticks too: "Verdict: `Looks good`"), then one of the three
# verdicts as whole words.
LINE = re.compile(r"^[ \t>*_#\d.)-]*verdict[\s*_:\-–—`'\"“‘]*(looks good|needs changes|blocking issues)\b", re.I | re.M)


def parse(text: str) -> str:
    """The review's verdict (one of VERDICTS), or "" when its first lines carry none."""
    head = "\n".join((text or "").splitlines()[:HEAD_LINES])[:4000]
    m = LINE.search(head)
    return next(v for v in VERDICTS if v.lower() == m.group(1).lower()) if m else ""


def findings(text: str) -> str:
    """What a fix round is given: the review document (already without its machine-read blocks), capped."""
    return (text or "").strip()[:MAX_FINDINGS]


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS pr_reviews (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            verdict TEXT NOT NULL DEFAULT '',          -- the last review's verdict, '' when it had none
            rounds INTEGER NOT NULL DEFAULT 0,         -- fix rounds the review has started on this PR
            state TEXT NOT NULL DEFAULT 'held',        -- ready | fixing | held | override
            updated REAL NOT NULL, PRIMARY KEY (repo, number))""")
    db.commit()


def record(db, prs: list, issue_repo: str, issue_num: int, verdict: str, state: str, rounds: int | None = None) -> None:
    """Keep the last verdict and state of each PR; rounds, when given, is the count of fix rounds used."""
    if db is None:
        return
    assert verdict in VERDICTS + ("",) and state in STATES
    ensure_tables(db)
    now = time.time()
    for r, n in prs:
        db.execute("INSERT INTO pr_reviews (repo, number, issue_repo, issue_num, verdict, rounds, state, updated) VALUES (?,?,?,?,?,?,?,?) "
                   "ON CONFLICT (repo, number) DO UPDATE SET verdict=excluded.verdict, state=excluded.state, updated=excluded.updated"
                   + (", rounds=excluded.rounds" if rounds is not None else ""),
                   (r, int(n), issue_repo, int(issue_num), verdict, rounds or 0, state, now))
    db.commit()


def rounds_used(db, prs: list) -> int:
    """The most fix rounds any of these PRs has had (siblings share a branch, so they share the rounds)."""
    if db is None or not prs:
        return 0
    ensure_tables(db)
    marks = " OR ".join("(repo=? AND number=?)" for _ in prs)
    row = db.execute(f"SELECT MAX(rounds) FROM pr_reviews WHERE {marks}", [x for r, n in prs for x in (r, int(n))]).fetchone()
    return int(row[0] or 0)


def last(db, repo: str, number: int) -> dict | None:
    """{verdict, rounds, state} of a PR's last review, or None when it was never reviewed."""
    try:
        row = db.execute("SELECT verdict, rounds, state FROM pr_reviews WHERE repo=? AND number=?", (repo, int(number))).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(zip(("verdict", "rounds", "state"), row)) if row else None


def set_override(db, repo: str, number: int) -> None:
    """A person merged (or marked ready) a PR whose last review was not Looks good."""
    ensure_tables(db)
    db.execute("UPDATE pr_reviews SET state='override', updated=? WHERE repo=? AND number=?", (time.time(), repo, int(number)))
    db.commit()


def needs_warning(verdict: str | None, state: str | None = None) -> bool:
    """Merging should come with a warning: the PR was reviewed (verdict is not None), the last review was not Looks good, and the
    gate holds it (state is not 'ready': with review.gate off a PR is marked ready after any review, and merges as before)."""
    return verdict is not None and verdict != GOOD and state != "ready"
