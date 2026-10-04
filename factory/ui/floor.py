"""The Factory page's pieces that are not the dashboard itself (board.py draws that): the health check, the pause and mode
controls, and the Needs-you rows and tray (/needs), whose buttons post to the same /tickets actions as everywhere else
(CSRF, re-validated server-side). Everything is server-rendered and escaped; the page policy forbids inline styles."""
import time
from urllib.parse import quote

from ..tracker import is_local
from . import views
from .views import ago, badge, csrf_field, esc



def _stale(d: dict, now: float) -> bool:
    cfg, st = d["cfg"], d["status"]
    last_ok = float(st["last_poll_ok"]["value"]) if "last_poll_ok" in st else None
    return last_ok is None or now - last_ok > max(120, 3 * cfg["poll_seconds"])


def pause_form(paused, csrf: str) -> str:
    return (f'<form method="post" action="/action/{"resume" if paused else "pause"}" class="inline">{csrf_field(csrf)}'
            f'<button class="secondary">{"Resume" if paused else "Pause"}</button></form>')


def mode_bar(d: dict, csrf: str, ask: bool = False) -> str:
    """Dry run versus live, on the home screen. Going live needs the confirmation box ticked; going back to dry run is one click."""
    if d["cfg"]["live"]:
        return (f'<div class="fl-mode live card"><span>{badge("LIVE", "warn")} Shikumi is live: it starts agents and opens PRs for labeled issues.</span><span class="fl-grow"></span>'
                f'<form method="post" action="/mode/set" class="inline">{csrf_field(csrf)}<input type="hidden" name="dry_run" value="1">'
                '<button class="secondary">Switch to dry run</button></form></div>')
    note = ' <strong class="bad-text">Tick the box to confirm.</strong>' if ask else ""
    return (f'<div class="fl-mode dry card" role="status"><div><strong>Dry run.</strong> Shikumi is only logging what it would do. Nothing is built, and nothing is written to GitHub. '
            f'When the decisions in the log look right, go live.{note}</div>'
            f'<form method="post" action="/mode/set" class="fl-mode-form">{csrf_field(csrf)}<input type="hidden" name="dry_run" value="0">'
            '<label class="check"><input type="checkbox" name="confirm" value="1"> I understand it will start agents and open PRs</label>'
            '<button>Go live</button></form></div>')


def _meter(conf: float) -> str:
    return f'<meter class="nd-meter" min="0" max="1" value="{conf:.2f}" aria-label="Confidence {conf:.2f}"></meter>'


def _row(k: int, n: dict, csrf: str, back: str) -> str:
    """One ticket waiting for a person: who and why on the left, the recommendation and the buttons on the right. A ticket with
    questions opens its question form below (inline for one or two questions, in a popup for more)."""
    from . import labels as L
    ref = esc(views.ref(n["repo"], n["issue"], short=True))
    if is_local(int(n["issue"])):
        gh_link = f'<a href="/ticket?repo={quote(str(n["repo"]), safe="")}&amp;n={int(n["issue"])}">{esc(n["title"])}</a>'
    else:
        gh_link = views.gh_link(f'https://github.com/{n["repo"]}/issues/{int(n["issue"])}', n["title"]) if views.REPO.match(str(n["repo"])) else esc(n["title"])
    st = n.get("st")
    title = f'<span class="nd-t">{gh_link}</span>'
    age = esc(ago(n.get("at")))
    head = lambda meta, why: (f'<div class="nd-top"><div class="nd-main">{title}<p class="muted">{meta}</p></div><div class="nd-tag">{why}</div></div>')
    if st:
        pend = st.pending()
        total = len(st.questions)
        meta = f'{ref} · the {esc(st.stage)} asked {total} question{"s" if total != 1 else ""} · {age}'
        why = (f'<span class="badge bad">{len(pend)} need{"" if len(pend) != 1 else "s"} a person</span>'
               + (f' <span class="badge good">{total - len(pend)} safe default{"s" if total - len(pend) != 1 else ""}</span>' if total > len(pend) else ""))
        hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(n["repo"])}"><input type="hidden" name="n" value="{int(n["issue"])}">'
                  f'<input type="hidden" name="back" value="{esc(back)}">')
        accept = (f'<form method="post" action="/tickets/answer" class="inline">{hidden}<button name="accept" value="1" '
                  f'aria-label="Accept recommendations for {ref}">Accept recommendations</button></form>')
        form = L.question_form(n["repo"], n["issue"], st, csrf, back)
        if total <= 2:
            return f'<article class="card nd-row questions">{head(meta, why)}<div class="nd-body">{form}</div></article>'
        dialog = (f'<dialog id="nd-d{k}" class="nd-dialog" aria-label="Questions for {ref}"><div class="nd-dhead"><div>{title}<p class="muted">{meta}</p></div>'
                  f'<button type="button" class="secondary" data-close aria-label="Close">Close</button></div>{form}</dialog>')
        opener = f'<button type="button" class="secondary" data-dialog="nd-d{k}">Answer…</button><noscript><a class="btn secondary" href="/tickets?repo={esc(n["repo"])}&amp;q=%23{int(n["issue"])}">Answer</a></noscript>'
        return f'<article class="card nd-row questions">{head(meta, why)}<div class="nd-low"><div class="nd-acts">{accept}{opener}</div></div>{dialog}</article>'
    bits = [x for x in (n.get("kind"), (n.get("complexity") or "") + " complexity" if n.get("complexity") else "") if x]
    meta = " · ".join([ref, *bits, age])
    why = f'<span class="badge bad">{esc(n["reason"])}</span>'
    conf = f'<span class="nd-conf">{_meter(n["conf"])}<span class="muted fl-mono">confidence {n["conf"]:.2f}</span></span>' if "conf" in n else ""
    acts = n.get("acts") or []
    rec = f'<p class="nd-rec">Recommended: <b>{esc(acts[0][1].lower())}</b></p>' if acts else ""
    return (f'<article class="card nd-row">{head(meta, why)}<div class="nd-low">{conf}<div class="nd-acts2">{rec}'
            f'<div class="nd-acts">{L.action_forms(n["repo"], n["issue"], acts, csrf, back)}</div></div></div></article>')


def tray(needs, csrf: str, forms=None, back: str = "/", flt: str = "", heading: bool = True) -> str:
    head = '<h2 id="needs">Needs you</h2>' if heading else ""
    if needs is None:
        return f'<section class="fl-tray" aria-labelledby="needs">{head}<p class="muted">Save a GitHub token on the Credentials page to see the tickets waiting for you.</p></section>'
    if not needs:
        return (f'<section class="fl-tray" aria-labelledby="needs">{head}<div class="card nd-empty"><h3>Nothing needs you</h3>'
                '<p class="muted">The factory is working through its queue. It will ask here, and on Telegram, when it needs a decision.</p>'
                '<a class="btn secondary" href="/tickets">See the tickets</a></div></section>')
    qn = sum(1 for r in needs if r.get("questions"))
    base = back.split("&need=")[0].split("?need=")[0]
    sep = "&" if "?" in base else "?"
    chip = lambda label, key, count: (f'<a class="{"on" if flt == key else ""}" href="{esc(base + (sep + "need=" + key if key else ""))}"{" aria-current=page" if flt == key else ""}>{label} <b>{count}</b></a>')
    seg = f'<nav class="nd-seg" aria-label="Filter">{chip("All", "", len(needs))}{chip("Questions", "questions", qn)}{chip("Decisions", "decisions", len(needs) - qn)}</nav>'
    shown = [r for r in needs if (flt == "questions" and r.get("questions")) or (flt == "decisions" and not r.get("questions")) or flt not in ("questions", "decisions")]
    bulk = ""
    asking = [r for r in shown if r.get("st")]
    if len(asking) >= 2:
        items = "".join(f'<li><b>{esc(r["repo"].split("/")[-1])}#{int(r["issue"])}</b> {esc(r["title"])}<ul>'
                        + "".join(f'<li>{esc(q.id)}: {esc(q.label(q.recommended))}</li>' for q in r["st"].pending()) + '</ul></li>' for r in asking[:20])
        refs = ",".join(f'{r["repo"]}#{int(r["issue"])}' for r in asking[:20])
        bulk = (f'<details class="nd-bulk"><summary class="btn">Accept recommendations on {len(asking[:20])} tickets</summary><div class="card"><p class="muted">'
                f'These are the answers that will be recorded. Each is a recommendation you can see, and it is your decision to accept it.</p><ul>{items}</ul>'
                f'<form method="post" action="/tickets/answer-all" class="inline">{csrf_field(csrf)}<input type="hidden" name="tickets" value="{esc(refs)}">'
                f'<input type="hidden" name="back" value="{esc(back)}"><button>Confirm: accept all</button></form></div></details>')
    rows = "".join(_row(k, r, csrf, back) for k, r in enumerate(shown[:12]))
    more = f'<p class="muted"><a href="/tickets">{len(shown) - 12} more in Tickets →</a></p>' if len(shown) > 12 else ""
    top = (f'<div class="nd-head"><div>{head}<p class="muted">Tickets the factory will not decide on its own. Newest first. Finish each one right here.</p></div>{seg}</div>')
    return f'<section class="fl-tray" aria-labelledby="needs">{top}{bulk}<div class="nd-list">{rows or "<p class=muted>Nothing in this filter.</p>"}</div>{more}</section>'


def render(d: dict, csrf: str, selected: str | None = None, needs=None, forms=None, flt: str = "", view: str = "", ask: bool = False) -> str:
    """The Factory dashboard (board.dashboard): tiles, the floor of stations with the tickets at each, what needs you, what is running."""
    from . import board
    bar = "" if d["cfg"]["live"] and not ask else mode_bar(d, csrf, ask)
    return board.dashboard(d, d.get("ticket_rows") or [], needs, csrf, time.time(), bar)
