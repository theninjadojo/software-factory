"""Thin tickets: a trigger label on a ticket whose body says next to nothing does not start a run.

The factory asks the author once, with a fixed comment (nothing from the ticket is echoed in it), for the goal, the scope, how
the result is checked and any constraints, and puts the needs-answers label on the ticket. The trigger label stays, so the
ticket is looked at again whenever it changes: it starts once its body has enough real text, or once a person with write access
comments "go ahead" after the factory asked. The ticket's text is untrusted and is only measured here, never repeated.
"""
import re

MARKER = "<!-- factory:thin-ticket -->"
COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)
URL = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.I)
MARKUP = re.compile(r"[#*_`>~|\[\]()!=+\-\\]")
# A short comment that is only an instruction to start: "go ahead", "Proceed.", "please start anyway". Anything longer is not one.
GO_AHEAD = re.compile(r"(?:please\s+)?(?:go\s+ahead|proceed|start\s+anyway|run\s+anyway|build\s+anyway)(?:\s+please)?[\s.!]*", re.I)
MAX_GO = 60

ASK = (MARKER + "\n**The factory has not started on this ticket yet: its description is too short to work from** "
       "(fewer than {min_chars} characters of text). An agent would have to guess what you want. Please edit the description and add:\n\n"
       "- **Goal:** what should be different when this is done, and why.\n"
       "- **Scope:** which pages, screens, modules or repositories it touches, and what is out of scope.\n"
       "- **How the result is checked:** what a person will look at or test to say it is done.\n"
       "- **Constraints:** anything it must not change, deadlines, or decisions already made.\n\n"
       "The factory starts by itself once the description is long enough. To start it as it is, a person with write access "
       "can comment `go ahead`.")


def real_text(body) -> str:
    """The body's words without HTML comments (issue templates), links and markdown markup, with whitespace collapsed."""
    if not isinstance(body, str):
        return ""
    text = MARKUP.sub(" ", URL.sub(" ", COMMENT.sub(" ", body)))
    text = re.sub(r"\b[xX]\b", " ", text)          # an unticked or ticked checkbox's x
    return " ".join(text.split())


def thin(body, min_chars: int) -> bool:
    return len(real_text(body)) < min_chars


def ask_comment(min_chars: int) -> str:
    return ASK.format(min_chars=int(min_chars))


def go_ahead(body) -> bool:
    if not isinstance(body, str):
        return False
    text = body.strip()
    return len(text) <= MAX_GO and GO_AHEAD.fullmatch(text) is not None


def state(comments: list, me: str, may_override) -> tuple[bool, str | None]:
    """(the factory already asked, the login of the person who said go ahead after it asked, or None).
    Only the factory's own comment with the marker counts as asking; may_override(login) says whether a commenter may start it."""
    asked, by = False, None
    for c in comments:                                  # oldest first
        login = (c.get("user") or {}).get("login") or ""
        body = c.get("body") or ""
        if login == me:
            if body.startswith(MARKER):
                asked, by = True, None                  # asked again (the ticket was thin again): an earlier go ahead is spent
            continue
        if asked and by is None and go_ahead(body) and may_override(login):
            by = login
    return asked, by
