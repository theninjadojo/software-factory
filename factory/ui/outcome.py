"""What a Done ticket delivered and why it counts as Done, from facts the factory already stores. Pure: no I/O, no HTML."""
from __future__ import annotations


def preview(text: str, n: int = 120) -> str:
    """At most n characters, cut at a word with an ellipsis."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(" ,·:;") + "…"


def derive(row: dict, sentence: str = "", stage: str = "", why_ignored: str = "") -> dict:
    """The Outcome of a Done row: tone (good, muted, warn), headline, reason, the sentence from the last stage document,
    that document's stage and the pull request numbers. The first rule that matches wins."""
    prs = row.get("prs") or []
    merged = [int(p["number"]) for p in prs if (p.get("summary") or "") == "merged"]
    shut = [int(p["number"]) for p in prs if (p.get("status") or "") == "closed"]
    nums = lambda ns: ", ".join(f"#{n}" for n in ns)      # noqa: E731
    if merged:
        tone, head, reason = "good", "Done · merged", f"PR {nums(merged)} merged."
    elif prs and len(shut) == len(prs):
        tone, head, reason = "muted", "Done · closed without merging", f"PR {nums(shut)} closed without merging."
    elif row.get("closed"):
        tone, head, reason = "muted", "Done · closed by a person", "A person closed the ticket."
    elif row.get("decision") == "ignored":
        tone, head, reason = "muted", "Done · closed as not needed", why_ignored or "The factory decided the ticket needed no work."
    elif stage:
        tone, head, reason = "muted", f"Done · {stage} document delivered", f"The {stage} stage finished and nothing else is queued."
    else:
        tone, head, reason = "warn", "Outcome not recorded", "The factory did not record a summary for this ticket."
    return {"tone": tone, "headline": head, "reason": reason, "sentence": sentence if tone != "warn" else "",
            "stage": stage, "prs": merged or shut}
