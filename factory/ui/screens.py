"""The Screens board: every configured screen, grouped by journey, as designed (its canvas) and as built on the default branch.

The orchestrator takes the shots (factory/screenboard.py); this page only shows the stored ones and can ask for a refresh."""
import logging
import sqlite3
from urllib.parse import urlencode

from .. import screenboard as SB
from . import labels as L
from . import views
from .views import csrf_field, esc

log = logging.getLogger("factory.ui")


def url(journey: str = "", view: str = "") -> str:
    q = {k: v for k, v in (("journey", journey), ("view", view)) if v}
    return "/screens" + ("?" + urlencode(q) if q else "")


def title(journey: str) -> str:
    return journey.replace("-", " ").capitalize() if journey else "Other screens"


def open_url(k: str) -> str:
    """Where a screen opens when clicked."""
    return SB.src(k)


def figure(label: str, shot: dict | None, alt: str, empty: str) -> str:
    if not shot:
        return f'<figure class="sb-shot sb-none"><figcaption>{esc(label)}</figcaption><p class="muted">{esc(empty)}</p></figure>'
    return (f'<figure class="sb-shot"><figcaption>{esc(label)}</figcaption><a href="{esc(open_url(shot["key"]))}">'
            f'<img src="{esc(SB.src(shot["key"]))}" alt="{esc(alt)}" loading="lazy"></a></figure>')


def board_body(cfg, shots: dict, st: dict, pending: bool, running: bool, journey: str, view: str, csrf: str) -> str:
    sc = cfg.screens
    if not sc.pages:
        return ('<p class="muted">No screens are configured. List them under <code>[[screens.pages]]</code> in the config, with a '
                '<code>journey</code> and <code>step</code> to group them, and a <code>design</code> canvas to show beside each one.</p>')
    groups = SB.journeys(sc.pages)
    names = [v.name for v in sc.viewports]
    view = view if view in names else names[0]
    journey = journey if journey in {j for j, _ in groups} or journey == "" else ""
    multi = len({p.repo for p in sc.pages}) > 1

    repos = st.get("repos") or {}
    when = f'Last shot {esc(views.ago(st["at"]))}' if st.get("at") else "Not shot yet"
    shas = " · ".join(f'{esc(r)} @ <code>{esc(x["sha"][:7])}</code>' for r, x in sorted(repos.items()) if x.get("sha"))
    probs = "".join(f'<p class="bad-text">{esc(r)}: {esc(x["problem"])}</p>' for r, x in sorted(repos.items()) if x.get("problem"))
    busy = running or pending
    refresh = (f'<form method="post" action="/screens/refresh" class="sb-refresh">{csrf_field(csrf)}'
               f'<button{" disabled" if busy else ""}>{"Refreshing…" if busy else "Refresh now"}</button></form>')
    head = (f'<div class="sb-head"><p class="muted">{when}{" · " + shas if shas else ""}. Built screens come from each repository\'s '
            f'default branch; designed ones from the canvas set for the screen.</p>{refresh}</div>{probs}')

    tabs = ('<nav class="tabs" aria-label="Journeys">'
            + f'<a href="{esc(url("", view))}"{" class=active" if not journey else ""}>All</a>'
            + "".join(f'<a href="{esc(url(j or "-", view))}"{" class=active" if journey == (j or "-") else ""}>{esc(title(j))}</a>'
                      for j, _ in groups) + "</nav>")
    vtabs = ('<nav class="tabs" aria-label="Viewport">'
             + "".join(f'<a href="{esc(url(journey, v))}"{" class=active" if v == view else ""}>{esc(v.capitalize())}</a>' for v in names)
             + "</nav>")

    out = ""
    for j, pages in groups:
        if journey and journey != (j or "-"):
            continue
        cards = ""
        for k, p in enumerate(pages, 1):
            views_of = p.viewports or tuple(names)
            built = shots.get((p.repo, p.name, view)) if view in views_of else None
            design = shots.get((p.repo, p.name, SB.DESIGN)) if p.design else None
            figs = (figure("Designed", design, f"{p.name}, designed", "Not rendered yet." if p.design else "No canvas set.")
                    + figure("Built", built, f"{p.name}, built, {view}",
                             "Not shot yet." if view in views_of else f"Not checked at {view}."))
            repo = f'<span class="muted">{esc(p.repo)}</span>' if multi else ""
            cards += (f'<li class="sb-step"><h3><span class="sb-num">{k}</span>{esc(p.name)}</h3>{repo}'
                      f'<div class="sb-pair">{figs}</div></li>')
        out += f'<section class="sb-journey"><h2>{esc(title(j))}</h2><ol class="sb-steps">{cards}</ol></section>'
    return head + tabs + vtabs + out


def board_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    db = h.app.ro_db()
    shots, st, pending = {}, {}, False
    if db is not None:
        try:
            shots, st, pending = SB.latest(db), SB.status(db), SB.requested(db)
        finally:
            db.close()
    body = board_body(cfg, shots, st, pending, SB.running(), q.get("journey", ""), q.get("view", ""), csrf)
    shown = L.flash_pop(csrf)
    h._send(200, views.page("Screens", body, "/screens", csrf, wide=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def refresh(h, form, csrf: str) -> None:
    """Ask the orchestrator for a refresh at its next poll."""
    if not h.app.cfg().screens.pages:
        L.flash_set(csrf, "No screens are configured.", "bad")
        return h._redirect("/screens")
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        SB.request(db)
    finally:
        db.close()
    log.info("screens: refresh requested")
    L.flash_set(csrf, "Asked for new shots. They are taken at the factory's next poll and take a few minutes.")
    h._redirect("/screens")
