"""The Screens board: every configured screen, grouped by journey, as designed (its canvas) and as built on the default branch.

The orchestrator takes the shots (factory/screenboard.py); this page shows the stored ones, can ask for a refresh, and adds, edits and
removes screens. Those edits go to the overrides file through settings._commit, which validates the merged config first."""
import copy
import logging
import re
import sqlite3
from urllib.parse import urlencode

from .. import reviewnotes as RN
from .. import screenboard as SB
from ..config import deep_merge
from . import labels as L
from . import settings as S
from . import views
from .views import csrf_field, esc

log = logging.getLogger("factory.ui")
SCREEN_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


def url(journey: str = "", view: str = "") -> str:
    q = {k: v for k, v in (("journey", journey), ("view", view)) if v}
    return "/screens" + ("?" + urlencode(q) if q else "")


def title(journey: str) -> str:
    return journey.replace("-", " ").capitalize() if journey else "Other screens"


def open_url(k: str) -> str:
    """Where a screen opens when clicked: the review tool, to mark it up."""
    return "/screens/review?" + urlencode({"img": k})


def edit_url(repo: str, name: str) -> str:
    return "/screens/edit?" + urlencode({"repo": repo, "name": name})


def figure(label: str, shot: dict | None, alt: str, empty: str) -> str:
    if not shot:
        return f'<figure class="sb-shot sb-none"><figcaption>{esc(label)}</figcaption><p class="muted">{esc(empty)}</p></figure>'
    return (f'<figure class="sb-shot"><figcaption>{esc(label)}</figcaption><a href="{esc(open_url(shot["key"]))}">'
            f'<img src="{esc(SB.src(shot["key"]))}" alt="{esc(alt)}" loading="lazy"></a></figure>')


def board_body(cfg, shots: dict, st: dict, pending: bool, running: bool, journey: str, view: str, csrf: str,
               noted: dict | None = None) -> str:
    """noted: (repo, page) -> open notes on that screen."""
    sc = cfg.screens
    if not sc.pages:
        return ('<p>No screens yet. A screen is a page of one of your repositories: the board shows it as built on the default branch, '
                'beside its design canvas, and builds check it against its baseline images. '
                '<a href="/screens/edit">Add the first one</a>.</p>'
                '<p class="muted">Viewports, the refresh timer and the comparison tolerance are in '
                '<a href="/settings?section=screens">Settings → Screens</a>.</p>')
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
            f'default branch; designed ones from the canvas set for the screen. <a href="/screens/edit">Add a screen</a> · '
            f'<a href="/settings?section=screens">Board settings</a></p>{refresh}</div>{probs}')

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
            c = (noted or {}).get((p.repo, p.name), 0)
            badge = f' <span class="rv-count" title="Open notes">{c} note{"" if c == 1 else "s"}</span>' if c else ""
            edit = f'<a class="sb-edit" href="{esc(edit_url(p.repo, p.name))}" aria-label="Edit {esc(p.name)}">Edit</a>'
            cards += (f'<li class="sb-step"><div class="sb-top"><h3><span class="sb-num">{k}</span>{esc(p.name)}{badge}</h3>{edit}</div>{repo}'
                      f'<div class="sb-pair">{figs}</div></li>')
        out += f'<section class="sb-journey"><h2>{esc(title(j))}</h2><ol class="sb-steps">{cards}</ol></section>'
    return head + tabs + vtabs + out


def board_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    db = h.app.ro_db()
    shots, st, pending, noted = {}, {}, False, {}
    if db is not None:
        try:
            shots, st, pending = SB.latest(db), SB.status(db), SB.requested(db)
            for x in RN.notes(db, *RN.BOARD, open_only=True):
                if (m := SB.KEY.fullmatch(x["image"])):
                    noted[m.group(1, 2)] = noted.get(m.group(1, 2), 0) + 1
        finally:
            db.close()
    body = board_body(cfg, shots, st, pending, SB.running(), q.get("journey", ""), q.get("view", ""), csrf, noted)
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


# ---------------------------------------------------------------- adding, editing and removing screens

FIELDS = ("repo", "name", "path", "journey", "step", "design", "wait_for", "mask")


def raw_pages(cfg_path: str) -> list[dict]:
    """The screens as configured now (config.toml with the UI's overrides on top), as raw dicts."""
    return copy.deepcopy(deep_merge(S.base_raw(cfg_path), S.overrides_raw(cfg_path)).get("screens", {}).get("pages", []))


def find(cfg_path: str, repo: str, name: str) -> dict | None:
    return next((p for p in raw_pages(cfg_path) if p.get("repo") == repo and p.get("name") == name), None)


def flat(entry: dict | None, cfg) -> dict:
    e = entry or {}
    return {"repo": e.get("repo", cfg.repos[0] if cfg.repos else ""), "name": e.get("name", ""), "path": e.get("path", ""),
            "journey": e.get("journey", ""), "step": str(e.get("step", "") or ""), "design": e.get("design", ""),
            "wait_for": e.get("wait_for", ""), "mask": "\n".join(e.get("mask", [])), "viewports": list(e.get("viewports", []))}


def from_form(form) -> dict:
    v = {k: str(form.get(k, "")) for k in FIELDS}
    v["viewports"] = form.getall("viewport")
    return v


def build(v: dict, cfg, existing: dict | None, new: bool, taken: set) -> dict:
    """The raw page dict for a submitted form, keeping any key of `existing` the form does not own. Raises SettingsError.
    The full checks run when the merged configuration is validated before it is written (settings._commit)."""
    errs: list[str] = []
    repo, name = v["repo"], v["name"].strip()
    if repo not in cfg.repos:
        errs.append("Repository: choose one the factory handles.")
    if not SCREEN_NAME.fullmatch(name):
        errs.append("Name: lowercase letters, digits and dashes, starting with a letter or digit.")
    elif new and (repo, name) in taken:
        errs.append(f"Name: {repo} already has a screen called {name}.")
    path = v["path"].strip()
    if not path:
        errs.append("Page: the file to open, relative to the repository root, for example site/index.html.")
    journey = v["journey"].strip().lower()
    if journey and not SCREEN_NAME.fullmatch(journey):
        errs.append("Journey: lowercase letters, digits and dashes, or empty.")
    step = v["step"].strip()
    if step and not (step.isdigit() and int(step) <= 999):
        errs.append("Step: a whole number from 0 to 999, or empty.")
    names = [x.name for x in cfg.screens.viewports]
    vps = [x for x in names if x in v["viewports"]]
    mask = [x.strip() for x in v["mask"].splitlines() if x.strip()]
    if errs:
        raise S.SettingsError(errs)
    e = {k: x for k, x in (existing or {}).items() if k not in FIELDS + ("viewports",)}
    e.update({"repo": repo, "name": name, "path": path})
    for k, x in (("journey", journey), ("step", int(step) if step else 0), ("design", v["design"].strip()),
                 ("wait_for", v["wait_for"].strip()), ("mask", mask), ("viewports", vps if len(vps) < len(names) else [])):
        if x:
            e[k] = x
    return e


def store(cfg_path: str, state_dir, at: tuple[str, str] | None, entry: dict | None) -> None:
    """Replace the page at (repo, name), add it (at=None), or remove it (entry=None). The whole list is kept in the overrides file,
    because a list there replaces config.toml's: the first save copies the hand-written pages across."""
    out, hit = [], False
    for x in raw_pages(cfg_path):
        if at and (x.get("repo"), x.get("name")) == at:
            hit = True
            if entry is not None:
                out.append(entry)
        else:
            out.append(x)
    if not hit and entry is not None:
        out.append(entry)
    base, ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    sc = ov.setdefault("screens", {})
    if out == base.get("screens", {}).get("pages", []):
        sc.pop("pages", None)
    else:
        sc["pages"] = out
    if not sc:
        ov.pop("screens")
    S._commit(cfg_path, state_dir, ov)


def _input(name: str, v: dict, label: str, hint: str = "", extra: str = "") -> str:
    return (f'<div class="field"><label for="f_{name}">{esc(label)}</label><input id="f_{name}" name="{name}" value="{esc(v.get(name, ""))}" '
            f'{extra} autocomplete="off">{f"<div class=muted>{esc(hint)}</div>" if hint else ""}</div>')


def form_page(cfg, v: dict, csrf: str, orig: tuple[str, str] | None = None) -> str:
    new = orig is None
    sc = cfg.screens
    repos = "".join(f'<option value="{esc(r)}"{" selected" if r == v["repo"] else ""}>{esc(r)}</option>' for r in cfg.repos)
    journeys = sorted({p.journey for p in sc.pages if p.journey})
    names = [x.name for x in sc.viewports]
    every = not v["viewports"] or set(names) <= set(v["viewports"])
    vps = "".join(f'<label class="check"><input type="checkbox" name="viewport" value="{esc(x.name)}"'
                  f'{" checked" if every or x.name in v["viewports"] else ""}> {esc(x.name)} '
                  f'<span class="muted">{x.width}×{x.height}</span></label>' for x in sc.viewports)
    if new:
        who = (f'<div class="field"><label for="f_repo">Repository</label><select id="f_repo" name="repo">{repos}</select></div>'
               + _input("name", v, "Name", "Lowercase letters, digits and dashes. Names the baseline image: <name>-<viewport>.png.",
                        "required maxlength=61"))
    else:
        who = (f'<div class="field"><label>Screen</label><code>{esc(orig[0])}</code> · <code>{esc(orig[1])}</code>'
               f'<input type="hidden" name="repo" value="{esc(orig[0])}"><input type="hidden" name="name" value="{esc(orig[1])}"></div>')
    body = (f'<p><a href="/screens">← Screens</a></p>'
            f'<form method="post" action="/screens/save" class="settings">{csrf_field(csrf)}'
            f'<input type="hidden" name="orig_repo" value="{esc(orig[0] if orig else "")}">'
            f'<input type="hidden" name="orig_name" value="{esc(orig[1] if orig else "")}">'
            f'<fieldset><legend>The screen</legend>{who}'
            + _input("path", v, "Page", "The file to open, relative to the repository root, for example site/checkout.html. "
                     "It is served from the default branch inside a sealed container.", "required maxlength=200")
            + _input("design", v, "Design canvas", "Optional: the Claude Design canvas (*.dc.html) of this screen, shown beside it.", "maxlength=200")
            + '</fieldset><fieldset><legend>Where it sits on the board</legend>'
            + _input("journey", v, "Journey", "Groups screens on the board, for example checkout. Empty: Other screens.",
                     'list="sb-journeys" maxlength=61')
            + f'<datalist id="sb-journeys">{"".join(f"<option value={chr(34)}{esc(j)}{chr(34)}>" for j in journeys)}</datalist>'
            + _input("step", v, "Step", "Its order within the journey, 0 to 999.", 'inputmode="numeric" maxlength=3')
            + '</fieldset><fieldset><legend>How it is shot</legend>'
            + f'<div class="field"><label>Viewports</label>{vps}<div class="muted">Viewports are set in '
              '<a href="/settings?section=screens">Settings → Screens</a>.</div></div>'
            + _input("wait_for", v, "Wait for", "Optional CSS selector to wait for before the shot.", "maxlength=200")
            + f'<div class="field"><label for="f_mask">Masked regions</label><textarea id="f_mask" name="mask" rows="3">{esc(v["mask"])}</textarea>'
              '<div class="muted">CSS selectors of parts that change on their own (clocks, avatars), one per line. They are painted over before comparing.</div></div>'
            + f'</fieldset><p><button type="submit">{"Add screen" if new else "Save screen"}</button></p></form>')
    if not new:
        body += ('<h2>Remove</h2><form method="post" action="/screens/delete" class="settings">' + csrf_field(csrf)
                 + f'<input type="hidden" name="repo" value="{esc(orig[0])}"><input type="hidden" name="name" value="{esc(orig[1])}">'
                 f'<label class="check danger"><input type="checkbox" name="confirm" value="1"> Remove {esc(orig[1])}. Builds stop checking it; '
                 'baseline images in the repository are left alone.</label><button type="submit">Remove screen</button></form>')
    return body


def _form(h, csrf: str, v: dict, orig, status: int = 200, flash=None) -> None:
    h._send(status, views.page("Edit screen" if orig else "Add a screen", form_page(h.app.cfg(), v, csrf, orig), "/screens", csrf,
                               flash=flash, flash_kind="bad"))


def edit_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        L.flash_set(csrf, "Add a repository in Settings first.", "bad")
        return h._redirect("/screens")
    if not q.get("name"):
        return _form(h, csrf, flat(None, cfg), None)
    entry = find(h.app.config_path, q.get("repo", ""), q["name"])
    if entry is None:
        return h._send(404, views.page("Screens", '<p class="muted">No such screen.</p>', "/screens", csrf))
    _form(h, csrf, flat(entry, cfg), (entry["repo"], entry["name"]))


def save(h, form, csrf: str) -> None:
    cfg, path = h.app.cfg(), h.app.config_path
    orig = (form.get("orig_repo", ""), form.get("orig_name", "")) if form.get("orig_name") else None
    v = from_form(form)
    if orig:
        v["repo"], v["name"] = orig
    existing = find(path, *orig) if orig else None
    if orig and existing is None:
        return h._send(404, views.page("Screens", '<p class="muted">No such screen.</p>', "/screens", csrf))
    try:
        entry = build(v, cfg, existing, orig is None, {(p.repo, p.name) for p in cfg.screens.pages})
        store(path, h.app.state_dir(), orig, entry)
    except S.SettingsError as e:
        return _form(h, csrf, v, orig, 422, " · ".join(e.messages))
    log.info("screens: %s %s/%s", "saved" if orig else "added", entry["repo"], entry["name"])
    L.flash_set(csrf, f"Screen {entry['name']} {'saved' if orig else 'added'}. The factory applies it at its next idle moment; "
                      "press Refresh now to shoot it.")
    h._redirect(url(entry.get("journey", "") or "-"))


def delete(h, form, csrf: str) -> None:
    at = (form.get("repo", ""), form.get("name", ""))
    if find(h.app.config_path, *at) is None:
        return h._send(404, views.page("Screens", '<p class="muted">No such screen.</p>', "/screens", csrf))
    if form.get("confirm") != "1":
        return _form(h, csrf, flat(find(h.app.config_path, *at), h.app.cfg()), at, 400, "Tick the box to confirm.")
    try:
        store(h.app.config_path, h.app.state_dir(), at, None)
    except S.SettingsError as e:
        return _form(h, csrf, flat(find(h.app.config_path, *at), h.app.cfg()), at, 422, " · ".join(e.messages))
    log.info("screens: removed %s/%s", *at)
    L.flash_set(csrf, f"Screen {at[1]} removed. The factory applies it at its next idle moment.")
    h._redirect("/screens")
