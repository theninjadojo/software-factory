"""Review the screens: a person marks areas on a ticket's mockups and screenshots and writes what should change there; the notes
go to the designer on its next run (factory/reviewnotes.py). Every form works without the page script, which only adds drawing
the area with the pointer. The page only ever names an image the ticket has, and every value is re-checked here."""
import logging
import sqlite3
import time
import urllib.error
from urllib.parse import urlencode

from .. import db as dbm
from .. import reviewnotes as RN
from .. import scenarios as SC
from .. import screenboard as SB
from . import canvas as CV
from . import labels as L
from . import views
from .canvas import marks
from .scenarios import BADGE
from .views import esc

log = logging.getLogger("factory.ui")
DESIGNER = "designer"


def url(repo: str, n: int, img: str = "") -> str:
    if not repo:
        return "/screens/review" + ("?" + urlencode({"img": img}) if img else "")
    return "/ticket/review?" + urlencode({"repo": repo, "n": int(n), **({"img": img} if img else {})})


def _ticket(h, repo: str, n: str) -> tuple[str, int]:
    return L._repo(h.app.cfg(), repo), L._number(n)


def _scope(h, form) -> tuple[str, int]:
    """(repo, issue) the form is about: a configured ticket, or the Screens board, whose notes are kept as ("", 0) until a person
    turns them into a ticket. Raises L.Refused."""
    if form.get("board") == "1":
        return RN.BOARD
    return _ticket(h, form.get("repo", ""), form.get("n", ""))


def _hidden(repo: str, n: int, csrf: str) -> str:
    return CV.scope_hidden(repo, n, csrf, (repo, n) == RN.BOARD)


def _images(h, repo: str, n: int) -> list[dict]:
    db = h.app.ro_db()
    if db is None:
        return []
    try:
        if (repo, n) == RN.BOARD:
            cfg = h.app.cfg()
            return SB.board_images(db, cfg.screens, [x["image"] for x in RN.notes(db, *RN.BOARD, open_only=True)])
        return RN.images(db, repo, n)
    finally:
        db.close()


def _write(h, fn):
    """A short write to the factory's database, like a queued approval."""
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        RN.ensure_tables(db)
        return fn(db)
    finally:
        db.close()


def _pct(v: float) -> str:
    return f"{v / 10:.1f}".rstrip("0").rstrip(".")


def where(note: dict) -> str:
    """The area in words, as a share of the screen (the pixels depend on the image; the designer is told those)."""
    if note["w"] == 0:
        return f"pin at {_pct(note['x'])}%, {_pct(note['y'])}%"
    return f"{_pct(note['w'])}% × {_pct(note['h'])}% at {_pct(note['x'])}%, {_pct(note['y'])}%"


def page_body(repo: str, n: int, imgs: list[dict], sel: dict | None, notes: list[dict], queued: bool, can_send: bool, csrf: str) -> str:
    """A ticket's review page."""
    head = (f'<p>{views.ticket_link(repo, n)} · <a href="/ticket?repo={esc(repo)}&amp;n={int(n)}">Pipeline</a> · '
            f'<a href="{views.doc_url(repo, n, DESIGNER)}">Design document</a></p>')
    if not imgs:
        return head + ('<p class="muted">This ticket has no screens to review yet. They appear here once the design stage renders its '
                       'mockups, or a build keeps screenshots.</p>')
    return head + review_ui(repo, n, imgs, imgs, sel, notes, send_form(repo, n, notes, queued, can_send, csrf), "Notes for the designer", csrf)


def send_form(repo: str, n: int, notes: list[dict], queued: bool, can_send: bool, csrf: str) -> str:
    """Send the open notes to the designer (a design run), or why that is not possible now."""
    hidden = _hidden(repo, n, csrf)
    k = sum(1 for x in notes if x["sent"] is None)
    if queued:
        send = ('<p class="flash ok">A design run is queued. The open notes go with it, along with any you add before it starts.</p>')
    elif not can_send:
        send = '<p class="muted">The design stage is not configured, so these notes wait for a person to pass them on.</p>'
    else:
        send = (f'<form method="post" action="/review/send" class="rv-send">{hidden}'
                f'<button{" disabled" if not k else ""}>Send {k} note{"" if k == 1 else "s"} to the designer</button>'
                '<p class="muted">The design stage runs again with the notes and the screens they are on. Notes you leave here go with '
                'the next design run anyway.</p></form>')
    return send


def review_ui(repo: str, n: int, imgs: list[dict], known: list[dict], sel: dict, notes: list[dict], send: str, notes_title: str,
              csrf: str) -> str:
    """The screen with its marks, the new-note form and the list of notes. `imgs` are the tabs over the screen; `known` is every
    image a note of this scope may be on (for its label and link)."""
    hidden = _hidden(repo, n, csrf)
    opened = [x for x in notes if x["sent"] is None]
    num = {x["id"]: k for k, x in enumerate(opened, 1)}           # the numbers the designer gets
    label = {i["key"]: i["label"] for i in known}
    count = {}
    for x in opened:
        count[x["image"]] = count.get(x["image"], 0) + 1
    tabs = "".join(f'<a href="{esc(url(repo, n, i["key"]))}"{" aria-current=\"page\"" if i is sel else ""}>{esc(i["label"])}'
                   + (f' <span class="rv-count">{count[i["key"]]}</span>' if count.get(i["key"]) else "") + "</a>" for i in imgs)
    here = [(num[x["id"]], x) for x in opened if x["image"] == sel["key"]]
    stage = (f'<div class="rv-stage"><div class="rv-frame" data-review>'
             f'<img src="{esc(sel["src"])}" alt="Screen {esc(sel["label"])}" draggable="false">{marks(here)}</div></div>')
    shot = (f'<section class="rv-main" aria-label="Screen"><nav class="rv-shots" aria-label="Screens">{tabs}</nav>'
            '<p class="rv-hint"><strong>Drag</strong> over an area to mark it, or <strong>click</strong> to drop a pin (on a phone, tap). '
            'Then write what should change.</p>' + stage + "</section>")
    nums = "".join(f'<label>{t}<input type="number" name="{f}" min="0" max="100" step="0.1" inputmode="decimal" data-area="{f}"></label>'
                   for f, t in (("x", "From left, %"), ("y", "From top, %"), ("w", "Width, %"), ("h", "Height, %")))
    new = (f'<form method="post" action="/review/add" class="rv-new" id="rv-new">{hidden}<input type="hidden" name="img" value="{esc(sel["key"])}">'
           '<h2>New note</h2><p class="muted" data-where aria-live="polite">No area marked yet. Mark one on the screen, or set it below.</p>'
           f'<label for="rv-text">What should change here?</label><textarea id="rv-text" name="text" rows="4" maxlength="{RN.MAX_TEXT}" required '
           'placeholder="For example: these chips wrap onto two rows. Keep them on one line."></textarea>'
           f'<details class="rv-nums"><summary>Set the area by numbers</summary><div class="rv-nums-grid">{nums}</div>'
           '<p class="muted">Leave width and height at 0 for a pin.</p></details><button>Add note</button></form>')

    def item(x: dict, sent: bool) -> str:
        on_img = (f'<a href="{esc(url(repo, n, x["image"]))}">{esc(label[x["image"]])}</a>' if x["image"] in label
                  else '<span>an image that is gone</span>')
        badge = "" if sent else f'<span class="rv-num" aria-hidden="true">{num[x["id"]]}</span>'
        text = (f'<button type="button" class="rv-show" data-show="{int(x["id"])}">{esc(x["text"])}</button>'
                if not sent and x["image"] == sel["key"] else f'<span class="rv-text">{esc(x["text"])}</span>')
        drop = ("" if sent else
                f'<form method="post" action="/review/delete">{hidden}<input type="hidden" name="id" value="{int(x["id"])}">'
                f'<input type="hidden" name="img" value="{esc(sel["key"])}">'
                f'<button class="rv-del" aria-label="Delete note {num[x["id"]]}" title="Delete">{TRASH}</button></form>')
        meta = f'{on_img} · {esc(where(x))}' + (f' · sent {esc(views.ago(x["sent"]))}' if sent else "")
        return f'<li id="note-{int(x["id"])}">{badge}<div class="rv-body">{text}<span class="rv-meta">{meta}</span></div>{drop}</li>'

    listed = ("<ol class=\"rv-list\">" + "".join(item(x, False) for x in opened) + "</ol>" if opened else
              '<p class="rv-empty">No notes yet. Mark an area of a screen to start.</p>')
    sent = [x for x in notes if x["sent"] is not None]
    earlier = (f'<details class="rv-sent"><summary>Sent earlier ({len(sent)})</summary><ol class="rv-list">'
               + "".join(item(x, True) for x in reversed(sent)) + "</ol></details>") if sent else ""
    side = (f'<aside class="rv-side" aria-label="{esc(notes_title)}">{new}<h2>{esc(notes_title)}</h2>{listed}{send}{earlier}</aside>')
    return f'<div class="rv">{shot}{side}</div>'


TRASH = ('<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"></path></svg>')


def review_get(h, q: dict, csrf: str) -> None:
    try:
        repo, n = _ticket(h, q.get("repo", ""), q.get("n", ""))
    except L.Refused:
        return h._send(404, "no such ticket", "text/plain")
    db = h.app.ro_db()
    imgs, notes = [], []
    if db is not None:
        try:
            imgs, notes = RN.images(db, repo, n), RN.notes(db, repo, n)
        finally:
            db.close()
    sel = next((i for i in imgs if i["key"] == q.get("img")), imgs[0] if imgs else None)
    cfg = h.app.cfg()
    body = page_body(repo, n, imgs, sel, notes, (repo, n) in L._approved(h), any(r.name == DESIGNER for r in cfg.roles), csrf)
    shown = L.flash_pop(csrf)
    h._send(200, views.page(f"Review the screens · #{n}", body, "/ticket/review", csrf, wide=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def _back(h, csrf: str, repo: str, n: int, img: str, msg: str, kind: str = "ok", back: str = "") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect(back_url(repo, n, img, back) or url(repo, n, img))


def back_url(repo: str, n: int, img: str, back: str) -> str:
    """The canvas a note came from (canvas.BACK), so a person who drops a note there stays there; '' for Review the screens."""
    if back == "ticket" and repo:
        return "/ticket?" + urlencode({"repo": repo, "n": int(n)})
    if back == "design" and repo:
        return design_url(repo, n)
    if back == "canvas" and (repo, n) == RN.BOARD and (m := SB.KEY.fullmatch(img)):
        return "/screens/canvas?" + urlencode({"repo": m.group(1)})
    return ""


def design_url(repo: str, n: int) -> str:
    return "/ticket/design?" + urlencode({"repo": repo, "n": int(n)})


def _area(form) -> tuple[int, int, int, int]:
    """Percentages from the form (one decimal) as thousandths, a box clipped to the image. Raises ValueError."""
    vals = []
    for f in ("x", "y", "w", "h"):
        raw = (form.get(f, "") or "").strip()
        if raw == "" and f in ("w", "h"):
            raw = "0"
        v = float(raw)                               # ValueError for empty or junk; nan and inf fail the range check below
        if not 0 <= v <= 100:
            raise ValueError
        vals.append(round(v * 10))
    x, y, w, h = vals
    w, h = min(w, RN.SCALE - x), min(h, RN.SCALE - y)
    if (w == 0) != (h == 0):                         # a line is not an area: make it a pin
        w = h = 0
    return RN.area(x, y, w, h)


def add(h, form, csrf: str) -> None:
    try:
        repo, n = _scope(h, form)
    except L.Refused as e:
        return L._page(h, 400, "Review", "", csrf, str(e), "bad")
    img, back = form.get("img", ""), form.get("back", "")
    if img not in {i["key"] for i in _images(h, repo, n)}:
        return _back(h, csrf, repo, n, "", "That screen is no longer here. Nothing was saved.", "bad")
    try:
        box = _area(form)
    except ValueError:
        return _back(h, csrf, repo, n, img, "Mark an area on the screen first (or set it by numbers). Nothing was saved.", "bad", back)
    try:
        _write(h, lambda db: RN.add(db, repo, n, img, box, form.get("text", "")))
    except ValueError as e:
        return _back(h, csrf, repo, n, img, f"Not saved: {e}.", "bad", back)
    log.info("review: note added on %s", f"{repo}#{n}" if repo else "the screens board")
    _back(h, csrf, repo, n, img, "Note added.", back=back)


def delete(h, form, csrf: str) -> None:
    try:
        repo, n = _scope(h, form)
        nid = L._number(form.get("id", ""))
    except L.Refused as e:
        return L._page(h, 400, "Review", "", csrf, str(e), "bad")
    gone = _write(h, lambda db: RN.delete(db, repo, n, nid))
    img = form.get("img", "")
    _back(h, csrf, repo, n, img if img in {i["key"] for i in _images(h, repo, n)} else "",
          "Note deleted." if gone else "That note was already sent or deleted.", "ok" if gone else "bad")


def send(h, form, csrf: str) -> None:
    """Queue a design run for the ticket (the same approval a person's Run button writes); it picks up every open note."""
    cfg = h.app.cfg()
    try:
        repo, n = _ticket(h, form.get("repo", ""), form.get("n", ""))
    except L.Refused as e:
        return L._page(h, 400, "Review", "", csrf, str(e), "bad")
    if not any(r.name == DESIGNER for r in cfg.roles):
        return _back(h, csrf, repo, n, "", "The design stage is not configured.", "bad")
    db = h.app.ro_db()
    try:
        opened = RN.notes(db, repo, n, open_only=True) if db is not None else []
    finally:
        if db is not None:
            db.close()
    if not opened:
        return _back(h, csrf, repo, n, "", "There are no open notes to send.", "bad")
    if (repo, n) in L._approved(h):
        return _back(h, csrf, repo, n, "", "A run is already queued for this ticket. The open notes go with the next design run.")
    gh = L._gh(h)
    if gh is None:
        return _back(h, csrf, repo, n, "", L.NO_TOKEN, "bad")
    try:
        issue = gh.get_issue(repo, n)
    except (urllib.error.URLError, OSError) as e:
        log.warning("review: reading %s#%d failed", repo, n)
        return _back(h, csrf, repo, n, "", L._github_error(e), "bad")
    names = {lb.get("name", "") for lb in issue.get("labels", []) if isinstance(lb, dict)}
    if "pull_request" in issue or issue.get("state") != "open":
        return _back(h, csrf, repo, n, "", "The ticket is closed, so no design run was started.", "bad")
    if any(x == "factory:working" or x.startswith("factory:working-") for x in names):
        return _back(h, csrf, repo, n, "", "The factory is working on this ticket now. Send the notes when it is done; they stay here.", "bad")
    h.app.add_approval(repo, n, f"stage:{DESIGNER}")
    L.needs_forget(repo, n)
    log.info("review: design run queued for %s#%d with %d note(s)", repo, n, len(opened))
    _back(h, csrf, repo, n, "", f"Sent. The designer runs again with {len(opened)} note{'' if len(opened) == 1 else 's'} at the factory's next poll.")


def board_review_get(h, q: dict, csrf: str) -> None:
    """Review a screen of the Screens board. Its notes are kept apart from any ticket until a person turns them into one."""
    cfg = h.app.cfg()
    db = h.app.ro_db()
    known, notes, linked, sel = [], [], [], None
    if db is not None:
        try:
            notes = RN.notes(db, *RN.BOARD)
            known = SB.board_images(db, cfg.screens, [x["image"] for x in notes if x["sent"] is None])
            sel = next((i for i in known if i["key"] == q.get("img")), known[0] if known else None)
            linked = scenarios_for(db, sel["key"]) if sel else []
        finally:
            db.close()
    head = '<p><a href="/screens">← Screens</a></p>'
    if sel is None:
        body = head + '<p class="muted">There are no screens to review yet. Take the shots on the Screens page first.</p>'
    else:
        tabs = [i for i in known if i["screen"] == sel["screen"]]
        canvas = (f' · <a href="/screens/canvas?{urlencode({"repo": sel["screen"][0]})}">Canvas</a>'
                  if sel["screen"][0] in {c.repo for c in cfg.screens.captures} and (sel["screen"] not in {(p.repo, p.name) for p in cfg.screens.pages}) else "")
        head = (f'<p><a href="/screens">← Screens</a>{canvas} · {esc(sel["screen"][1])}'
                + (f' <span class="muted">({esc(sel["screen"][0])})</span>' if len({i["screen"][0] for i in known}) > 1 else "") + "</p>")
        body = (head + review_ui(*RN.BOARD, tabs, known, sel, notes, board_send(cfg, notes, known, sel, csrf), "Notes on the screens", csrf)
                + scenarios_card(linked, sel["screen"][0]))
    shown = L.flash_pop(csrf)
    h._send(200, views.page("Review a screen", body, "/screens", csrf, wide=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def scenarios_for(db, k: str) -> list[dict]:
    """The scenarios in the shot's repo whose Playwright test took it (by pw_test), with their latest result."""
    tests = set(SB.tests_for_shot(db, k))
    if not tests:
        return []
    try:
        return [s for s in SC.listing(db, SB.KEY.fullmatch(k).group(1)) if s["pw_test"] in tests]
    except sqlite3.OperationalError:
        return []


def scenarios_card(linked: list[dict], repo: str) -> str:
    """Which test scenarios this screenshot is evidence for. Links are built from the stored repo and ref only."""
    if not linked:
        return '<section class="card"><h2>Scenarios</h2><p class="muted">No linked scenarios.</p></section>'
    rows = "".join(
        f'<li><a href="/scenarios/view?{esc(urlencode({"repo": repo, "ref": s["ref"]}))}">{esc(s["ref"])}</a> {esc(s["title"])} '
        f'{views.badge(SC.RESULT_LABEL[s["last_result"]], BADGE[s["last_result"]])}</li>' for s in linked)
    return f'<section class="card"><h2>Scenarios</h2><p class="muted">The tests that took this screenshot.</p><ul>{rows}</ul></section>'


START = (("", "Don't start it"), ("auto", "Auto: the factory picks the next stage"), ("design", "Design: send the notes to the designer"))


def board_send(cfg, notes: list[dict], known: list[dict], sel: dict, csrf: str) -> str:
    """Turn open board notes into a new ticket. The notes on the screen being shown are ticked to start with."""
    opened = [x for x in notes if x["sent"] is None]
    if not opened:
        return '<p class="muted">Notes stay here until you turn them into a ticket.</p>'
    label = {i["key"]: i["label"] for i in known}
    picks = "".join(
        f'<label class="rv-pick"><input type="checkbox" name="note" value="{int(x["id"])}"{" checked" if x["image"] == sel["key"] else ""}>'
        f'<span><strong>{k}</strong> {esc(label.get(x["image"], "an image that is gone"))}: {esc(x["text"][:80])}'
        f'{"…" if len(x["text"]) > 80 else ""}</span></label>' for k, x in enumerate(opened, 1))
    screen_repo = sel["screen"][0]
    repos = "".join(f'<option value="{esc(r)}"{" selected" if r == screen_repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    designer = any(r.name == DESIGNER for r in cfg.roles)
    starts = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in START if v != "design" or designer)
    return (f'<form method="post" action="/screens/issue" class="rv-send rv-issue">{_hidden(*RN.BOARD, csrf)}'
            f'<input type="hidden" name="img" value="{esc(sel["key"])}"><h2>Turn into a ticket</h2>'
            f'<fieldset><legend>Notes to include</legend>{picks}</fieldset>'
            f'<label for="rv-title">Title</label><input id="rv-title" name="title" maxlength="{L.MAX_TITLE}" required '
            'placeholder="For example: Payment screen spacing">'
            f'<label for="rv-repo">Repository</label><select id="rv-repo" name="repo">{repos}</select>'
            f'<label for="rv-start">Then</label><select id="rv-start" name="start">{starts}</select>'
            '<button>Create ticket</button>'
            '<p class="muted">The ticket lists each note and where it is. The notes and their screens move to it, so its design run '
            'gets them like any ticket\'s notes.</p></form>')


def ticket_body(notes: list[dict], label: dict) -> str:
    out = "Notes a person left on the Screens board.\n\n"
    for k, x in enumerate(notes, 1):
        m = SB.KEY.fullmatch(x["image"])
        at = f" ({m.group(1)} @ `{m.group(4)[:7]}`)" if m else ""
        quoted = "\n".join("> " + line for line in x["text"].splitlines() or [""])
        out += f"{k}. **{label.get(x['image'], 'a screen')}**{at}, {where(x)}\n\n{quoted}\n\n"
    return out + ("The marked screens are on this ticket's review page in the factory, and go to the designer with the notes.")


def board_issue(h, form, csrf: str) -> None:
    """Create a ticket from the picked board notes and move them onto it; optionally start it."""
    cfg = h.app.cfg()
    img = form.get("img", "")
    title = " ".join((form.get("title") or "").split())
    start = form.get("start", "")
    back = lambda msg, kind="bad": _back(h, csrf, *RN.BOARD, img, msg, kind)
    try:
        repo = L._repo(cfg, form.get("repo", ""))
        ids = [L._number(v) for v in form.getall("note")]
    except L.Refused as e:
        return back(str(e))
    if not title or len(title) > L.MAX_TITLE:
        return back(f"Enter a title of up to {L.MAX_TITLE} characters. Nothing was created.")
    if start not in {v for v, _ in START} or (start == "design" and not any(r.name == DESIGNER for r in cfg.roles)):
        return back("Pick what happens next. Nothing was created.")
    db = h.app.ro_db()
    try:
        opened = RN.notes(db, *RN.BOARD, open_only=True) if db is not None else []
        known = SB.board_images(db, cfg.screens, [x["image"] for x in opened]) if db is not None else []
    finally:
        if db is not None:
            db.close()
    picked = [x for x in opened if x["id"] in set(ids)]
    if not picked:
        return back("Tick at least one note. Nothing was created.")
    if len(picked) > RN.MAX_OPEN:
        return back(f"A ticket can hold {RN.MAX_OPEN} notes. Pick fewer. Nothing was created.")
    gh = L._gh(h)
    if gh is None:
        return back(L.NO_TOKEN)
    body = ticket_body(picked, {i["key"]: i["label"] for i in known})
    if len(body) > L.MAX_BODY * 4:
        return back("Those notes are too long for one ticket. Pick fewer. Nothing was created.")
    try:
        n = int(gh.create_ticket(repo, title, body)["number"])
    except (urllib.error.URLError, OSError, KeyError, TypeError, ValueError) as e:
        log.warning("screens: creating a ticket on %s failed", repo)
        return back(L._github_error(e) if isinstance(e, urllib.error.HTTPError)
                    else "GitHub did not accept the ticket. Nothing was created.")
    moved = _write(h, lambda db: RN.move_to_ticket(db, [x["id"] for x in picked], repo, n))
    log.info("screens: created %s#%d with %d board note(s)", repo, n, moved)
    msg = f"Created {repo}#{n} with {moved} note{'' if moved == 1 else 's'}."
    try:
        if start == "auto":
            L._existing(gh, repo, cfg.auto_label)
            gh.add_labels(repo, n, [cfg.auto_label])
            msg += " The factory picks it up at its next poll."
        elif start == "design":
            h.app.add_approval(repo, n, f"stage:{DESIGNER}")
            msg += " The designer runs with the notes at the factory's next poll."
    except L.Refused as e:
        msg += f" It was not started: {e}"
    except (urllib.error.URLError, OSError) as e:
        log.warning("screens: starting %s#%d failed", repo, n)
        msg += f" It was not started: {L._github_error(e)}"
    L.needs_forget(repo, n)
    _back(h, csrf, repo, n, "", msg)


def design_rows(shots: list[dict], widths: dict, href) -> list[tuple[str, list[dict]]]:
    """A ticket's design screens [{key, src, label}] as canvas rows: the screens of one name (its desktop and phone versions) side by side."""
    rows: dict[str, list[dict]] = {}
    for i in shots:
        name = views.mockup_name(i["key"].rpartition(":")[2])
        narrow = 0 < widths.get(i["key"], 0) < views.NARROW
        rows.setdefault(CV.group(name), []).append(CV.shot(i["key"], i["src"], i["label"], name, href(i["key"]), narrow))
    return list(rows.items())


def design_body(repo: str, n: int, imgs: list[dict], notes: list[dict], widths: dict | None = None, send: str = "", csrf: str = "") -> str:
    """A ticket's design screens (the latest design run's previews) on the canvas, with the open notes beside it. Click a screen to open
    it large in Review the screens; pick Note to drop a note on the canvas itself."""
    shots = [i for i in imgs if i["key"].startswith("mockup:")]
    keys = {i["key"] for i in shots}
    opened = CV.numbered(notes)
    here = [(k, x) for k, x in opened if x["image"] in keys]
    head = (f'<p>{views.ticket_link(repo, n)} · <strong>Design output</strong> <span class="muted">· {len(shots)} screen{"s" if len(shots) != 1 else ""} · '
            f'{len(here)} open note{"" if len(here) == 1 else "s"}</span></p>')
    if not shots:
        return head + '<p class="muted">No design screens yet. They appear here once the designer has rendered its mockups.</p>'
    rows = design_rows(shots, widths or {}, lambda k: url(repo, n, k))
    board = CV.canvas("cv", f"Design screens of #{int(n)}", rows, here, _hidden(repo, n, csrf), "design", full=True)
    label = {i["key"]: views.mockup_name(i["key"].rpartition(":")[2]) for i in imgs}
    side = (f'<aside class="pz-side" aria-labelledby="pz-nh"><h2 id="pz-nh">Notes for the designer</h2>'
            f'{CV.notes_list(opened, label, lambda k: url(repo, n, k), "cv")}{send}'
            f'<p class="muted">Click a screen to open it large, drag over an area and write what should change. '
            f'<a href="{esc(url(repo, n))}">Review the screens</a></p></aside>')
    return head + f'<div class="pz-page">{board}{side}</div>'


def design_get(h, q: dict, csrf: str) -> None:
    try:
        repo, n = _ticket(h, q.get("repo", ""), q.get("n", ""))
    except L.Refused:
        return h._send(404, "no such ticket", "text/plain")
    db = h.app.ro_db()
    imgs, notes, widths = [], [], {}
    if db is not None:
        try:
            imgs, notes = RN.images(db, repo, n), RN.notes(db, repo, n)
            mocks = {i["key"]: (m.group(1), m.group(2)) for i in imgs if (m := RN.MOCKUP_KEY.fullmatch(i["key"]))}
            got = dbm.mockup_widths(db, [{"repo": r, "path": p} for r, p in mocks.values()])
            widths = {k: got.get(v, 0) for k, v in mocks.items()}
        finally:
            db.close()
    cfg = h.app.cfg()
    send = send_form(repo, n, notes, (repo, n) in L._approved(h), any(r.name == DESIGNER for r in cfg.roles), csrf)
    shown = L.flash_pop(csrf)
    h._send(200, views.page(f"Design output · #{n}", design_body(repo, n, imgs, notes, widths, send, csrf), "/ticket/design", csrf, wide=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))
