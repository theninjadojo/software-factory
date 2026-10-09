"""Settings › Design libraries: the presets and the person's own libraries, one library's values and history, duplicating and editing,
uploading a zip, and which library each project uses. Everything is set here; nothing needs editing on disk.

Libraries are stored through factory/designlib.py, which checks every value before it is kept. Colours are drawn as SVG images built
here from checked hex values (the admin pages allow no inline styles), so nothing a person typed reaches the page unescaped."""
import sqlite3
import time
from urllib.parse import quote, urlencode

from .. import designlib as D
from . import forms, views
from . import labels as L
from . import settings as S
from .views import ago, badge, csrf_field, esc

HOME = "/design/libraries"
CHECK_LABEL = {"off": "Don't check", "warn": "Say so on the ticket", "reject": "Reject the mockup"}
SOURCE_LABEL = {"edit": "Made here", "upload": "Uploaded", "agent": "Proposed by an agent", "preset": "Preset"}
FONTS = ("IBM Plex Sans", "IBM Plex Mono", "Inter Tight", "Manrope", "DM Sans", "Nunito", "Space Grotesk", "JetBrains Mono", "Roboto Flex",
         "Roboto Mono", "Source Sans 3", "Work Sans", "Lexend", "Outfit", "Fraunces", "Literata", "system-ui", "ui-monospace")


def _db(h) -> sqlite3.Connection:
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    D.ensure_tables(db)
    return db


def _page(h, title: str, crumbs: list, body: str, csrf: str, status: int = 200, flash=None, kind: str = "ok") -> None:
    """A page in the Settings layout, with a breadcrumb from Settings: crumbs are (name, href or '') pairs."""
    shown = None if flash else L.flash_pop(csrf)
    msg, k = (flash, kind) if flash else (shown if shown else (None, "ok"))
    cached = L.needs_cached()
    try:
        cfg = h.app.cfg()
    except Exception:  # noqa: BLE001 - the menu then shows no state markers
        cfg = None
    trail = "".join(' <span aria-hidden="true">›</span> ' + (f'<a href="{href}">{esc(n)}</a>' if href else esc(n)) for n, href in crumbs)
    crumb = f'<nav class="crumb" aria-label="Breadcrumb"><a href="/settings">Settings</a>{trail}</nav>'
    h._send(status, views.page(title, crumb + body, "/settings", csrf, nav=views.NAV, flash=msg, flash_kind=k,
                               badges={"/tickets": len(cached)} if cached else None, side=forms.side_list("design_libraries", cfg), bare=True))


def _back(h, csrf: str, where: str, msg: str, kind: str = "ok") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect(where)


def lib_url(lid: str, version: int = 0) -> str:
    return "/design/library?" + urlencode({"id": lid, **({"v": version} if version else {})})


# ---------------------------------------------------------------- pictures (SVG, from checked values only)

def _svg(w: int, h: int, inner: str, label: str) -> str:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{inner}</svg>'
    return f'<img src="data:image/svg+xml,{quote(svg)}" width="{w}" height="{h}" alt="{esc(label)}" class="dl-img">'


def strip(tokens: dict, w: int = 320, h: int = 48, label: str = "") -> str:
    cols = list(tokens["colors"].values())[:10]
    step = w / max(1, len(cols))
    inner = "".join(f'<rect x="{i * step:.1f}" y="0" width="{step + 0.5:.1f}" height="{h}" fill="{c}"/>' for i, c in enumerate(cols))
    return _svg(w, h, inner, label or "Colours: " + ", ".join(tokens["colors"]))


def card_art(tokens: dict, label: str) -> str:
    """A small scene in the library's look: its page, a heading, a button and a surface."""
    c, r = tokens["colors"], min(D.radius(tokens), 22)
    gen = "monospace" if tokens["fonts"]["heading"].lower() in ("ui-monospace", "monospace") else "sans-serif"
    inner = (f'<rect width="320" height="120" fill="{c["page"]}"/>'
             f'<text x="28" y="74" font-family="{gen}" font-size="34" font-weight="700" fill="{c["ink"]}">Aa</text>'
             f'<rect x="100" y="44" width="104" height="40" rx="{r}" fill="{c["primary"]}"/>'
             f'<text x="152" y="69" text-anchor="middle" font-family="{gen}" font-size="14" font-weight="600" fill="{c["on-primary"]}">Button</text>'
             f'<rect x="222" y="40" width="70" height="48" rx="{r}" fill="{c["surface"]}" stroke="{c["line"]}"/>')
    return _svg(320, 120, inner, label)


def swatch(hexv: str, label: str) -> str:
    return _svg(120, 56, f'<rect width="120" height="56" fill="{hexv}"/>', label)


def type_art(size: int, weight: int, label: str) -> str:
    s = min(size, 64)
    return _svg(560, s + 16, f'<text x="0" y="{s}" font-family="sans-serif" font-size="{s}" font-weight="{weight}" fill="#e8edf0">'
                             f'Order your groceries in minutes</text>', label)


def spacing_art(steps: list) -> str:
    x, inner = 0, ""
    for v in steps[:10]:
        d = max(2, min(v, 64))
        inner += f'<rect x="{x}" y="{64 - d}" width="{d}" height="{d}" fill="#8fa8ff"/>'
        x += d + 8
    return _svg(max(x, 16), 64, inner, "Spacing scale: " + ", ".join(map(str, steps)) + " px")


# ---------------------------------------------------------------- the list

def _used(cfg, lid: str) -> list[str]:
    out = list(D.usage(cfg).get(lid, []))
    loose = [r for r in cfg.repos if not any(p.repo(r) for p in cfg.projects)]
    if lid == cfg.design.library and loose:
        out.append("repositories without a project")
    return out


def _used_text(names: list[str]) -> str:
    if not names:
        return "Not used"
    return f"Used by {names[0]}" if len(names) == 1 else f"Used by {names[0]} and {names[1]}" if len(names) == 2 else f"Used by {len(names)} projects"


def _actions(*links) -> str:
    return '<div class="dl-actions">' + "".join(links) + "</div>"


def _btn(href: str, text: str, primary: bool = False) -> str:
    return f'<a class="btn{"" if primary else " secondary"}" href="{href}">{esc(text)}</a>'


def _post(action: str, csrf: str, text: str, fields: dict, kind: str = "") -> str:
    hidden = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in fields.items())
    return f'<form method="post" action="{action}" class="inline">{csrf_field(csrf)}{hidden}<button{f" class={kind}" if kind else ""}>{esc(text)}</button></form>'


def own_card(cfg, lib: dict, csrf: str) -> str:
    t = lib["tokens"]
    draft = lib["status"] == "draft"
    used = _used(cfg, lib["id"])
    tag = badge("Draft · needs approval", "warn") if draft else badge(_used_text(used), "good" if used else None)
    when = time.strftime("%-d %b %Y", time.localtime(lib["updated"]))
    meta = f'{SOURCE_LABEL.get(lib["source"], "")} · version {lib["version"]} · {when}'
    if draft and lib["origin"]:
        meta = f'Proposed by an agent from {lib["origin"]}'
    acts = (_actions(_post("/design/library/approve", csrf, "Approve", {"id": lib["id"]}), _btn(lib_url(lib["id"]), "Review"),
                     _post("/design/library/discard", csrf, "Discard", {"id": lib["id"]}, "link danger"))
            if draft else _actions(_btn(lib_url(lib["id"]), "Open"), _btn("/design/edit?" + urlencode({"from": lib["id"]}), "Edit")))
    return (f'<article class="card dl-card">{strip(t)}<div class="dl-head"><div><h3 class="dl-name">{esc(lib["name"])}</h3>'
            f'<div class="muted dl-meta">{esc(meta)}</div></div>{tag}</div>'
            f'<div class="dl-mono">{esc(t["fonts"]["body"])} · radius {D.radius(t)} · {len(t["colors"])} colours'
            f'{" · own components" if lib["custom_components"] else ""}</div>{acts}</article>')


def preset_card(cfg, lib: dict) -> str:
    t, used = lib["tokens"], _used(cfg, lib["id"])
    default = badge("Default", "warn") if lib["id"] == cfg.design.library else ""
    return (f'<article class="card dl-card">{card_art(t, lib["name"] + " preview")}<div class="dl-head"><h3 class="dl-name">{esc(lib["name"])}</h3>{default}</div>'
            f'<p class="muted dl-blurb">{esc(lib["blurb"])}</p><div class="dl-mono">{esc(t["fonts"]["body"])} · radius {D.radius(t)}</div>'
            + _actions(_btn(lib_url(lib["id"]), "Open"), _btn("/design/edit?" + urlencode({"from": lib["id"]}), "Duplicate"),
                       f'<span class="muted dl-used">{esc(_used_text(used))}</span>') + "</article>")


def _options(libs: list[dict], current: str) -> str:
    return "".join(f'<option value="{esc(x["id"])}"{" selected" if x["id"] == current else ""}>{esc(x["name"])}'
                   f'{" (preset)" if x["preset"] else ""}</option>' for x in libs)


def projects_card(cfg, libs: dict, csrf: str) -> str:
    """Which library each project uses, and the default for the rest."""
    ready = [x for x in libs.values() if x["status"] == "ready"]
    rows = ""
    for p in cfg.projects:
        lid, version, check = D.choice(cfg, p)
        name = libs[lid]["name"] if lid in libs else f"{lid} (missing: Neutral is used)"
        how = "default" if not p.design_library else f"version {version}" if version else "newest version"
        rows += (f'<tr><td data-l="Project">{esc(p.name)}</td><td data-l="Library">{esc(name)} <span class="muted">· {esc(how)}</span></td>'
                 f'<td data-l="Mockups">{esc(CHECK_LABEL[check])}</td>'
                 f'<td class="actions"><a class="btn secondary" href="/design/project?{urlencode({"name": p.name})}">Change</a></td></tr>')
    table = (f'<div class="scroll"><table class="stack"><thead><tr><th>Project</th><th>Library</th><th>Mockups outside it</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>' if rows else '<p class="muted">No projects yet. <a href="/settings?section=projects">Add one</a>.</p>')
    checks = "".join(f'<option value="{k}"{" selected" if k == cfg.design.check else ""}>{esc(v)}</option>' for k, v in CHECK_LABEL.items())
    default = (f'<form method="post" action="/design/default" class="card dl-default">{csrf_field(csrf)}'
               f'<div class="field"><label for="dl-def">Projects that have not picked one use</label><select id="dl-def" name="library">{_options(ready, cfg.design.library)}</select></div>'
               f'<div class="field"><label for="dl-chk">Mockups that use colours or fonts outside the library</label><select id="dl-chk" name="check">{checks}</select></div>'
               '<button>Save</button></form>')
    return f'<section aria-labelledby="dl-proj" class="dl-projects"><h2 id="dl-proj" class="dl-h">Projects</h2>{table}{default}</section>'


def from_code(cfg, csrf: str) -> str:
    """The 'Create from my code' control: pick a project, and an agent proposes a library from its code."""
    if not cfg.projects:
        return ""
    opts = "".join(f'<option>{esc(p.name)}</option>' for p in cfg.projects)
    return (f'<details class="dl-from"><summary class="btn secondary">Create from my code…</summary><form method="post" action="/design/from-code" class="card">'
            f'{csrf_field(csrf)}<p class="muted">An agent reads the project\'s code and proposes a library from the colours, fonts and spacing it already '
            'uses. It is a model run with the designer\'s model. The proposal waits here for you to approve it.</p>'
            f'<div class="field"><label for="dl-proj-pick">Project</label><select id="dl-proj-pick" name="project">{opts}</select></div>'
            '<button>Start</button></form></details>')


def extract_rows(rows: list[dict], csrf: str) -> str:
    out = ""
    for r in rows:
        if r["status"] == "failed":
            out += (f'<div class="flash bad dl-extract"><span>Could not propose a library from {esc(r["project"])}\'s code: {esc(r["detail"] or "it failed")}.</span>'
                    + _post("/design/from-code", csrf, "Try again", {"project": r["project"]}, "secondary") + "</div>")
        else:
            out += (f'<div class="flash dl-extract"><span>{"Reading" if r["status"] == "running" else "Waiting to read"} {esc(r["project"])}\'s code to propose '
                    f'a library (asked {esc(ago(r["created"]))}). The draft appears here when it is done; reload to check.</span></div>')
    return out


def list_get(h, q: dict, csrf: str) -> None:
    cfg, db = h.app.cfg(), _db(h)
    try:
        mine = D.own(db)
        extracts = D.recent_extracts(db, time.time())
    finally:
        db.close()
    libs = {x["id"]: x for x in D.presets() + mine}
    intro = ('<div class="dl-top"><div><h1>Design libraries</h1><p class="muted dl-intro">The colours, type, spacing and components the '
             'designer, builder and reviewer use. Each project uses one library; projects that have not picked one use '
             f'<strong>{esc(libs.get(cfg.design.library, D.preset(D.DEFAULT))["name"])}</strong>.</p></div>'
             + _actions(_btn("/design/upload", "Upload a library", True), from_code(cfg, csrf)) + "</div>")
    yours = ("".join(own_card(cfg, x, csrf) for x in mine) if mine else
             '<p class="muted">None yet. Duplicate a preset below to change its colours and fonts, or upload a zip.</p>')
    body = (intro + f'<section aria-labelledby="dl-yours"><div class="dl-sec"><h2 id="dl-yours" class="dl-h">Yours</h2>'
            f'<span class="muted">{len(mine)} {"library" if len(mine) == 1 else "libraries"}</span></div>{extract_rows(extracts, csrf)}<div class="cards dl-grid">{yours}</div></section>'
            '<section aria-labelledby="dl-pre"><div class="dl-sec"><h2 id="dl-pre" class="dl-h">Presets</h2>'
            '<span class="muted">Ship with the factory · read-only · duplicate one to make it yours</span></div>'
            f'<div class="cards dl-grid">{"".join(preset_card(cfg, x) for x in D.presets())}</div></section>'
            + projects_card(cfg, libs, csrf))
    _page(h, "Design libraries", [], body, csrf)


# ---------------------------------------------------------------- one library

def detail_get(h, q: dict, csrf: str) -> None:
    cfg, lid = h.app.cfg(), q.get("id", "")
    v = int(q["v"]) if q.get("v", "").isdigit() and len(q["v"]) < 7 else 0
    db = _db(h)
    try:
        lib = D.get(db, lid, v) if D.ID_RE.fullmatch(lid) else None
        hist = D.versions(db, lid) if lib else []
        png = D.preview(db, D.preview_key(lib["components"])) if lib else None
        if lib and png is None:
            D.want_preview(db, lib["components"])
    finally:
        db.close()
    if lib is None:
        return _page(h, "Design libraries", [("Design libraries", HOME)], '<h1>Not found</h1><p class="muted">There is no such library or version.</p>', csrf, 404)
    _page(h, lib["name"], [("Design libraries", HOME), (lib["name"], "")], library_body(cfg, lib, hist, png is not None, csrf), csrf)


def library_body(cfg, lib: dict, hist: list, rendered: bool, csrf: str) -> str:
    t, lid, own = lib["tokens"], lib["id"], not lib["preset"]
    newest = hist[0]["version"] if hist else lib["version"]
    used = _used(cfg, lid)
    if len(hist) > 1:
        opts = "".join(f'<option value="{x["version"]}"{" selected" if x["version"] == lib["version"] else ""}>{x["version"]}'
                       f'{" · newest" if x["version"] == newest else ""} · {esc(time.strftime("%-d %b %Y", time.localtime(x["created"])))}</option>' for x in hist)
        ver = (f'<form method="get" action="/design/library" class="dl-ver" data-autosubmit><input type="hidden" name="id" value="{esc(lid)}">'
               f'<label for="dl-v">Version</label> <select id="dl-v" name="v">{opts}</select> <button class="secondary sc-apply">Show</button></form>')
    else:
        ver = f'<span class="muted">Version {lib["version"]}</span>'
    who = "Ships with the factory" if lib["preset"] else (f'Proposed by an agent from {lib["origin"]}' if lib["source"] == "agent" else SOURCE_LABEL.get(lib["source"], ""))
    acts = []
    if lib["status"] == "draft":
        acts += [_post("/design/library/approve", csrf, "Approve", {"id": lid}), _post("/design/library/discard", csrf, "Discard", {"id": lid}, "secondary")]
    elif own:
        acts.append(_btn("/design/edit?" + urlencode({"from": lid}), "Edit", True))
    acts += [_btn("/design/edit?" + urlencode({"from": lid, "v": lib["version"], "copy": 1}), "Duplicate"),
             _btn("/design/library/download?" + urlencode({"id": lid, "v": lib["version"]}), "Download .zip")]
    head = (f'<div class="dl-top"><div><h1>{esc(lib["name"])}</h1><div class="dl-sub">{ver}<span class="muted">{esc(who)}</span></div></div>'
            + _actions(*acts) + "</div>")
    if lib["status"] == "draft":
        head += ('<p class="flash">An agent proposed this library from the project\'s code. Check the values below; nothing uses it until you '
                 'approve it, and then you pick it for a project.</p>')
    elif lib["version"] != newest:
        head += f'<p class="flash">This is an older version. <a href="{lib_url(lid)}">Show the newest (version {newest})</a>.</p>'
    warn = D.contrast_notes(t)
    colours = "".join(f'<figure class="dl-sw">{swatch(v, k + " " + v)}<figcaption><strong>{esc(k)}</strong><code>{v}</code>'
                      + (f'<span class="dl-warn">{esc(warn[k])}</span>' if k in warn else "") + "</figcaption></figure>" for k, v in t["colors"].items())
    types = "".join(f'<div class="dl-type"><code>{esc(k)} {v["size"]}px · {v["weight"]}</code>{type_art(v["size"], v["weight"], f"{k} sample")}</div>'
                    for k, v in t["type"].items())
    fonts = " · ".join(f"{esc(k)}: {esc(v)}" for k, v in t["fonts"].items())
    comps = (f'<img class="dl-comp" src="/design/preview?key={D.preview_key(lib["components"])}" alt="The library\'s reference components">' if rendered else
             '<p class="muted">The picture of the components is being drawn; reload in a minute. It needs '
             '<a href="/settings?section=runner">Render mockup previews</a> on. The agents get the canvas either way.</p>')
    rules = ("<ul class=\"dl-rules\">" + "".join(f"<li>{esc(x.lstrip('-*• ').strip())}</li>" for x in lib["rules"].splitlines() if x.strip()) + "</ul>"
             if lib["rules"] else '<p class="muted">No written rules.</p>')
    users = "".join(f"<li>{esc(n)}</li>" for n in used) or '<li class="muted">No project uses it.</li>'
    history = "".join(f'<li><a href="{lib_url(lid, x["version"])}">Version {x["version"]}</a>'
                      + (f' · {esc(time.strftime("%-d %b %Y", time.localtime(x["created"])))}' if x["created"] else "")
                      + (f'<br><span class="muted">{esc(x["note"])}</span>' if x["note"] else "") + "</li>" for x in hist)
    delete = ""
    if own and lib["status"] == "ready":
        delete = (_post("/design/library/delete", csrf, "Delete this library", {"id": lid}, "link danger") if not used and lid != cfg.design.library
                  else '<p class="muted">A library a project uses cannot be deleted; pick another for those projects first.</p>')
    main = (f'<section class="card" aria-labelledby="dl-c"><h2 id="dl-c" class="dl-h">Colours</h2><div class="dl-swatches">{colours}</div></section>'
            f'<section class="card" aria-labelledby="dl-t"><h2 id="dl-t" class="dl-h">Type</h2><p class="dl-mono">{fonts}</p>{types}</section>'
            f'<section class="card" aria-labelledby="dl-k"><h2 id="dl-k" class="dl-h">Components</h2>'
            f'<p class="muted">{"Uploaded with the library" if lib["custom_components"] else "Drawn from the values above"}: Button, TextField, Card, Badge, ListRow, Alert.</p>{comps}</section>'
            f'<section class="card" aria-labelledby="dl-r"><h2 id="dl-r" class="dl-h">Rules for the agents</h2>{rules}</section>')
    side = (f'<section class="card" aria-labelledby="dl-u"><h2 id="dl-u" class="dl-h">Used by</h2><ul class="dl-list">{users}</ul>'
            f'<p><a href="{HOME}#dl-proj">Choose per project</a></p></section>'
            f'<section class="card" aria-labelledby="dl-s"><h2 id="dl-s" class="dl-h">Spacing and corners</h2>{spacing_art(t["spacing"])}'
            f'<p class="dl-mono">{" ".join(map(str, t["spacing"]))}</p><p class="dl-mono">radius {" · ".join(f"{k} {v}" for k, v in t["radius"].items())}</p></section>'
            f'<section class="card" aria-labelledby="dl-hi"><h2 id="dl-hi" class="dl-h">History</h2><ol class="dl-list">{history}</ol>{delete}</section>')
    return head + f'<div class="dl-cols"><div class="dl-main">{main}</div><aside class="dl-side">{side}</aside></div>'


def download_get(h, q: dict, csrf: str) -> None:
    lid = q.get("id", "")
    v = int(q["v"]) if q.get("v", "").isdigit() and len(q["v"]) < 7 else 0
    db = _db(h)
    try:
        lib = D.get(db, lid, v) if D.ID_RE.fullmatch(lid) else None
    finally:
        db.close()
    if lib is None:
        return h._send(404, "no such library", "text/plain")
    h._send(200, D.to_zip(lib), "application/zip", {"Content-Disposition": f'attachment; filename="{lid}-v{lib["version"]}.zip"'})


def preview_get(h, q: dict, csrf: str) -> None:
    key = q.get("key", "")
    db = h.app.ro_db()
    png = None
    if db is not None and len(key) == 32 and all(c in "0123456789abcdef" for c in key):
        try:
            png = D.preview(db, key)
        finally:
            db.close()
    return h._send(200, png, "image/png") if png else h._send(404, "no such image", "text/plain")


# ---------------------------------------------------------------- duplicate and edit

def _source(db, q: dict) -> dict | None:
    lid = q.get("from", "")
    v = int(q["v"]) if str(q.get("v", "")).isdigit() and len(str(q["v"])) < 7 else 0
    return D.get(db, lid, v) if D.ID_RE.fullmatch(lid) else None


def edit_form(lib: dict, editing: bool, csrf: str, values: dict | None = None, problems=()) -> str:
    """The form for a new library from `lib`, or (editing) a new version of it. values: what the person sent, shown again after a problem."""
    t = lib["tokens"]
    val = lambda k, d: esc(values.get(k, d)) if values else esc(d)
    colours = list(t["colors"].items()) + [("", "")] * 3
    rows = ""
    for i, (k, v) in enumerate(colours):
        fixed = k in D.REQUIRED_COLORS
        name_in = (f'<input type="hidden" name="c{i}_name" value="{esc(k)}"><span class="dl-cname">{esc(k)}</span>' if fixed else
                   f'<input name="c{i}_name" value="{val(f"c{i}_name", k)}" placeholder="new colour name" aria-label="Colour name">')
        hexv = values.get(f"c{i}_hex", v) if values else v
        pick = D.hex6(hexv) or "#000000"
        rows += (f'<div class="dl-crow">{name_in}<span class="dl-cin"><input type="color" value="{pick}" data-dl-pick="c{i}_hex" '
                 f'aria-label="{esc(k or "New colour")}: picker" tabindex="-1">'
                 f'<input name="c{i}_hex" value="{esc(hexv)}" placeholder="#rrggbb" class="dl-hex" aria-label="{esc(k or "New colour")}: hex value" data-dl-token="{esc(k)}"></span></div>')
    fonts_dl = "".join(f'<option value="{esc(f)}">' for f in FONTS)
    font_rows = "".join(f'<div class="field"><label for="dl-f-{esc(k)}">{esc(k.title())} font</label><input id="dl-f-{esc(k)}" name="f_{esc(k)}" '
                        f'value="{val("f_" + k, v)}" list="dl-fonts" data-dl-font="{esc(k)}"></div>'
                        for k, v in list(t["fonts"].items()) + ([("mono", "")] if "mono" not in t["fonts"] else []))
    type_rows = "".join(f'<tr><td data-l="Style"><input name="t{i}_name" value="{val(f"t{i}_name", k)}" aria-label="Text style name"{" readonly" if k == "body" else ""}></td>'
                        f'<td data-l="Size (px)"><input name="t{i}_size" value="{val(f"t{i}_size", s["size"])}" inputmode="numeric" aria-label="Size of {esc(k or "new style")} in px"></td>'
                        f'<td data-l="Weight"><input name="t{i}_weight" value="{val(f"t{i}_weight", s["weight"])}" inputmode="numeric" aria-label="Weight of {esc(k or "new style")}"></td></tr>'
                        for i, (k, s) in enumerate(list(t["type"].items()) + [("", {"size": "", "weight": ""})]))
    radius_rows = "".join(f'<div class="field dl-num"><label for="dl-r{i}">Radius {esc(k)} (px)</label><input type="hidden" name="r{i}_name" value="{esc(k)}">'
                          f'<input id="dl-r{i}" name="r{i}_val" value="{val(f"r{i}_val", v)}" inputmode="numeric" data-dl-radius="{esc(k)}"></div>'
                          for i, (k, v) in enumerate(t["radius"].items()))
    keep = ""
    if editing and lib["custom_components"]:
        keep = ('<label class="check"><input type="checkbox" name="keep_components" value="1" checked> Keep the uploaded components.dc.html '
                '(untick to draw the components from these values instead)</label>')
    note = (f'<div class="field"><label for="dl-note">What changed</label><input id="dl-note" name="note" value="{val("note", "")}" class="wide" '
            'placeholder="For example: brand blue updated"></div>') if editing else ""
    warn = D.contrast_notes(t)
    warns = "".join(f"<li>{esc(k)}: {esc(m)}</li>" for k, m in warn.items())
    bad = "".join(f"<li>{esc(p)}</li>" for p in problems)
    hidden = f'<input type="hidden" name="from" value="{esc(lib["id"])}"><input type="hidden" name="v" value="{lib["version"]}">' + (
        '<input type="hidden" name="mode" value="version">' if editing else '<input type="hidden" name="mode" value="new">')
    name = lib["name"] if editing else (lib["name"] if lib["preset"] else lib["name"] + " copy")
    return (f'<form method="post" action="/design/edit/save" class="dl-edit" id="dl-edit">{csrf_field(csrf)}{hidden}'
            + (f'<div class="flash bad" role="alert"><strong>Not saved:</strong><ul>{bad}</ul></div>' if bad else "")
            + '<div class="dl-cols"><div class="dl-main">'
            f'<fieldset><legend>Name</legend><div class="field"><label for="dl-name">Library name</label><input id="dl-name" name="name" value="{val("name", name)}" maxlength="{D.MAX_NAME}" required></div></fieldset>'
            f'<fieldset><legend>Colours</legend><p class="muted">The first eight are needed by every library. Add your own below them; clear a name to remove one.</p><div class="dl-colours">{rows}</div>'
            + (f'<ul class="dl-warns">{warns}</ul>' if warns else "") + '</fieldset>'
            f'<fieldset><legend>Fonts</legend><p class="muted">Font family names from Google Fonts, or system-ui. Mockups load them from Google Fonts.</p>'
            f'<datalist id="dl-fonts">{fonts_dl}</datalist><div class="dl-fields">{font_rows}</div></fieldset>'
            f'<fieldset><legend>Text styles</legend><table class="stack dl-types"><thead><tr><th>Style</th><th>Size (px)</th><th>Weight</th></tr></thead><tbody>{type_rows}</tbody></table>'
            '<p class="muted">Clear a name to remove a style; body is needed.</p></fieldset>'
            f'<fieldset><legend>Spacing and corners</legend><div class="field"><label for="dl-sp">Spacing scale (px, smallest first)</label>'
            f'<input id="dl-sp" name="spacing" value="{val("spacing", " ".join(map(str, t["spacing"])))}" class="wide"></div><div class="dl-fields">{radius_rows}</div></fieldset>'
            f'<fieldset><legend>Rules for the agents</legend><div class="field"><label for="dl-rules">One rule per line</label>'
            f'<textarea id="dl-rules" name="rules" rows="6">{val("rules", lib["rules"])}</textarea></div></fieldset>'
            f'{note}{keep}<div class="dl-actions"><button>{"Save as version " + str(lib["version"] + 1) if editing else "Save library"}</button>'
            f'<a class="btn secondary" href="{lib_url(lib["id"]) if editing else HOME}">Cancel</a></div></div>'
            f'<aside class="dl-side"><section class="card dl-preview"><h2 class="dl-h">Preview</h2><div id="dl-art">{card_art(t, "Preview of these values")}</div>'
            '<p class="muted">Changes as you type. The full components are drawn after you save.</p></section></aside></div></form>')


def edit_get(h, q: dict, csrf: str) -> None:
    db = _db(h)
    try:
        lib = _source(db, q)
    finally:
        db.close()
    if lib is None:
        return _page(h, "Design libraries", [("Design libraries", HOME)], '<h1>Not found</h1><p class="muted">There is no such library.</p>', csrf, 404)
    editing = not lib["preset"] and lib["status"] == "ready" and not q.get("copy")
    title = f'Edit {lib["name"]}' if editing else f'New library from {lib["name"]}'
    intro = ('<p class="muted dl-intro">Saving makes a new version. Projects on the newest version get it on their next run; a project that '
             'stays on a version keeps it.</p>' if editing else
             f'<p class="muted dl-intro">A copy of {esc(lib["name"])}. Change what you need; the rest stays as it is. Saving makes version 1.</p>')
    _page(h, title, [("Design libraries", HOME), (lib["name"], lib_url(lib["id"])), ("Edit" if editing else "Duplicate", "")],
          f"<h1>{esc(title)}</h1>{intro}" + edit_form(lib, editing, csrf), csrf)


def _num(v: str):
    v = (v or "").strip()
    return int(v) if v.isdigit() and len(v) < 6 else v


def tokens_from_form(form, base: dict) -> dict:
    """The tokens a person sent (checked by designlib.clean_tokens afterwards); shadows are kept from the library they started from."""
    colors = {}
    for i in range(D.MAX_COLORS + 3):
        k, v = (form.get(f"c{i}_name") or "").strip(), (form.get(f"c{i}_hex") or "").strip()
        if k:
            colors[k] = v
    fonts = {k: (form.get(f"f_{k}") or "").strip() for k in ("heading", "body", "mono") if (form.get(f"f_{k}") or "").strip()}
    types = {}
    for i in range(D.MAX_ENTRIES + 1):
        k = (form.get(f"t{i}_name") or "").strip()
        if k:
            types[k] = {"size": _num(form.get(f"t{i}_size")), "weight": _num(form.get(f"t{i}_weight"))}
    spacing = [_num(x) for x in (form.get("spacing") or "").replace(",", " ").split()]
    radius = {}
    for i in range(8):
        k = (form.get(f"r{i}_name") or "").strip()
        if k:
            radius[k] = _num(form.get(f"r{i}_val"))
    return {"colors": colors, "fonts": fonts, "type": types, "spacing": spacing, "radius": radius,
            **({"shadow": base["shadow"]} if base.get("shadow") else {})}


def edit_save(h, form, csrf: str) -> None:
    db = _db(h)
    try:
        lib = _source(db, form)
        if lib is None:
            return _page(h, "Design libraries", [("Design libraries", HOME)], '<h1>Not found</h1><p class="muted">There is no such library.</p>', csrf, 404)
        editing = form.get("mode") == "version" and not lib["preset"] and lib["status"] == "ready"
        try:
            name = D.clean_name(form.get("name"))
            tokens = D.clean_tokens(tokens_from_form(form, lib["tokens"]))
            rules = D.clean_rules(form.get("rules") or "")
        except D.LibraryRejected as e:
            title = f'Edit {lib["name"]}' if editing else f'New library from {lib["name"]}'
            return _page(h, title, [("Design libraries", HOME), (lib["name"], lib_url(lib["id"])), ("Edit", "")],
                         f"<h1>{esc(title)}</h1>" + edit_form(lib, editing, csrf, dict(form), e.problems), csrf, 422)
        if editing:
            keep = lib["components"] if lib["custom_components"] and form.get("keep_components") == "1" else ""
            note = D.CONTROL.sub("", (form.get("note") or "").strip())[:200] or "Edited here"
            v = D.add_version(db, lib["id"], tokens, rules, keep, note, name)
            return _back(h, csrf, lib_url(lib["id"]), f"Saved {name}, version {v}.")
        lid = D.create(db, name, tokens, rules, "", "edit", lib["id"], note=f"Copied from {lib['name']}")
        _back(h, csrf, lib_url(lid), f"Added {name}. Pick it for a project under Projects on the Design libraries page.")
    finally:
        db.close()


# ---------------------------------------------------------------- upload

def upload_form(csrf: str, mine: list[dict], problems=(), notes=()) -> str:
    opts = '<option value="">A new library</option>' + "".join(
        f'<option value="{esc(x["id"])}">A new version of {esc(x["name"])}</option>' for x in mine if x["status"] == "ready")
    bad = "".join(f"<li>{esc(p)}</li>" for p in problems)
    return ('<h1>Upload a library</h1><p class="muted dl-intro">A .zip with <code>tokens.json</code> (required), and optionally '
            '<code>components.dc.html</code> and <code>README.md</code> (your rules, one per line). Nothing is saved until every file passes the checks. '
            f'<a href="/design/library/download?id={D.DEFAULT}">Download the Neutral zip</a> to see the format.</p>'
            + (f'<div class="flash bad" role="alert"><strong>Not added: {len(problems)} problem{"s" if len(problems) != 1 else ""}</strong><ul>{bad}</ul>'
               '<p>Fix the files and upload the zip again. Nothing was saved.</p></div>' if bad else "")
            + f'<form method="post" action="/design/upload" enctype="multipart/form-data" class="card dl-upload">{csrf_field(csrf)}'
            f'<div class="field"><label for="dl-file">Library zip (up to {D.MAX_ZIP // 1_000_000} MB)</label><input id="dl-file" type="file" name="file" accept=".zip,application/zip" required></div>'
            f'<div class="field"><label for="dl-as">Add as</label><select id="dl-as" name="as">{opts}</select></div>'
            '<button>Upload and check</button></form>'
            '<details class="card dl-format"><summary>What goes in tokens.json</summary><pre>'
            + esc('{\n  "name": "Acme brand",\n  "colors": {"page": "#ffffff", "surface": "#f3f5f8", "ink": "#0b1f3a", "muted": "#5b6b80",\n'
                  '             "line": "#c9d2de", "primary": "#1d6bf3", "on-primary": "#ffffff", "error": "#c8102e"},\n'
                  '  "fonts": {"heading": "Manrope", "body": "Manrope"},\n'
                  '  "type": {"title": {"size": 28, "weight": 800}, "body": {"size": 16, "weight": 400}},\n'
                  '  "spacing": [4, 8, 12, 16, 24, 32, 48],\n  "radius": {"sm": 4, "md": 8, "lg": 16}\n}')
            + '</pre><p class="muted">Colours are hex. Add any other colours you use (accent, success…). A shadow section is optional.</p></details>')


def upload_get(h, q: dict, csrf: str) -> None:
    db = _db(h)
    try:
        mine = D.own(db)
    finally:
        db.close()
    _page(h, "Upload a library", [("Design libraries", HOME), ("Upload", "")], upload_form(csrf, mine), csrf)


def upload_post(h, form, csrf: str, files=()) -> None:
    db = _db(h)
    try:
        mine = D.own(db)
        fail = lambda problems, status=422: _page(h, "Upload a library", [("Design libraries", HOME), ("Upload", "")],
                                                  upload_form(csrf, mine, problems), csrf, status)
        if not files or files[0][1] is None:
            return fail([f"Choose a .zip of up to {D.MAX_ZIP // 1_000_000} MB."], 400)
        fname, data = files[0]
        stem = (fname or "").rpartition("/")[2].rpartition(".")[0].replace("-", " ").replace("_", " ").strip()[: D.MAX_NAME]
        try:
            got, notes = D.read_zip(data, stem.capitalize())
        except D.LibraryRejected as e:
            return fail(e.problems)
        target = form.get("as", "")
        extra = (" " + " ".join(notes)) if notes else ""
        if target:
            lib = next((x for x in mine if x["id"] == target and x["status"] == "ready"), None)
            if lib is None:
                return fail(["Pick one of your libraries to add a version to, or A new library."], 400)
            v = D.add_version(db, target, got["tokens"], got["rules"], got["components"], f"Uploaded {fname[:80]}", got["name"])
            return _back(h, csrf, lib_url(target), f"Added {got['name']}, version {v}.{extra}")
        lid = D.create(db, got["name"], got["tokens"], got["rules"], got["components"], "upload", note=f"Uploaded {fname[:80]}")
        _back(h, csrf, lib_url(lid), f"Added {got['name']}. Pick it for a project under Projects on the Design libraries page.{extra}")
    finally:
        db.close()


# ---------------------------------------------------------------- drafts, deleting

def approve_post(h, form, csrf: str) -> None:
    db = _db(h)
    try:
        ok = D.approve(db, form.get("id", ""))
    finally:
        db.close()
    _back(h, csrf, lib_url(form.get("id", "")) if ok else HOME, "Approved. Pick it for a project under Projects below." if ok else "That draft is gone.",
          "ok" if ok else "bad")


def discard_post(h, form, csrf: str) -> None:
    db = _db(h)
    try:
        lib = D.get(db, form.get("id", "")) if D.ID_RE.fullmatch(form.get("id", "")) else None
        ok = bool(lib and lib["status"] == "draft" and D.remove(db, lib["id"]))
    finally:
        db.close()
    _back(h, csrf, HOME, "Discarded the draft." if ok else "That draft is gone.", "ok" if ok else "bad")


def delete_post(h, form, csrf: str) -> None:
    cfg, lid = h.app.cfg(), form.get("id", "")
    if _used(cfg, lid) or lid == cfg.design.library:
        return _back(h, csrf, lib_url(lid), "A project uses this library: pick another for it first.", "bad")
    db = _db(h)
    try:
        ok = D.remove(db, lid) if D.ID_RE.fullmatch(lid) else False
    finally:
        db.close()
    _back(h, csrf, HOME, "Deleted the library." if ok else "There is no such library.", "ok" if ok else "bad")


# ---------------------------------------------------------------- which library a project uses

def project_form(cfg, p, libs: dict, csrf: str, problems=()) -> str:
    lid, version, check = D.choice(cfg, p)
    picked = p.design_library
    cards = (f'<label class="dl-pick"><input type="radio" name="library" value=""{" checked" if not picked else ""}>'
             f'<span class="dl-pick-art">{strip(libs[cfg.design.library]["tokens"], 200, 40) if cfg.design.library in libs else ""}</span>'
             f'<span class="dl-pick-name">The default</span><span class="muted">{esc(libs.get(cfg.design.library, {}).get("name", ""))}</span></label>')
    for x in libs.values():
        if x["status"] != "ready":
            continue
        cards += (f'<label class="dl-pick"><input type="radio" name="library" value="{esc(x["id"])}"{" checked" if picked == x["id"] else ""}>'
                  f'<span class="dl-pick-art">{strip(x["tokens"], 200, 40)}</span><span class="dl-pick-name">{esc(x["name"])}</span>'
                  f'<span class="muted">{"Preset" if x["preset"] else "Yours · version " + str(x["version"])}</span></label>')
    current = libs.get(lid)
    stay = version or (current["version"] if current else 1)
    vers = (f'<label class="check dl-line"><input type="radio" name="pin" value="0"{"" if version else " checked"}> Always the newest version</label>'
            f'<label class="check dl-line"><input type="radio" name="pin" value="{stay}"{" checked" if version else ""}> Stay on version {stay} until I change it</label>'
            '<p class="muted">Staying applies to the library picked above when you save.</p>')
    own_check = p.design_check
    checks = (f'<label class="check dl-line"><input type="radio" name="check" value=""{" checked" if not own_check else ""}> The default ({esc(CHECK_LABEL[cfg.design.check].lower())})</label>'
              + "".join(f'<label class="check dl-line"><input type="radio" name="check" value="{k}"{" checked" if own_check == k else ""}> {esc(v)}</label>'
                        for k, v in CHECK_LABEL.items()))
    bad = "".join(f"<li>{esc(x)}</li>" for x in problems)
    return (f'<h1>{esc(p.name)}</h1>' + (f'<div class="flash bad" role="alert"><ul>{bad}</ul></div>' if bad else "")
            + f'<form method="post" action="/design/project/save" class="settings">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(p.name)}">'
            f'<fieldset><legend>Design library</legend><p class="muted">The designer, builder and reviewer for this project\'s tickets all get this library. '
            f'<a href="{HOME}">Manage libraries</a></p><div class="dl-picks" role="radiogroup" aria-label="Design library">{cards}</div></fieldset>'
            f'<div class="dl-two"><fieldset><legend>Version</legend>{vers}</fieldset>'
            f'<fieldset><legend>Mockups outside the library</legend>{checks}<p class="muted">Checks the colours and fonts in the designer\'s mockups against the library.</p></fieldset></div>'
            '<button>Save</button></form>')


def project_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    p = next((x for x in cfg.projects if x.name == q.get("name")), None)
    if p is None:
        return _page(h, "Design libraries", [("Design libraries", HOME)], '<h1>Not found</h1><p class="muted">There is no such project.</p>', csrf, 404)
    db = _db(h)
    try:
        libs = D.everything(db)
    finally:
        db.close()
    _page(h, p.name, [("Design libraries", HOME), (p.name, "")], project_form(cfg, p, libs, csrf), csrf)


def project_save(h, form, csrf: str) -> None:
    cfg, name = h.app.cfg(), form.get("name", "")
    p = next((x for x in cfg.projects if x.name == name), None)
    if p is None:
        return _back(h, csrf, HOME, "There is no such project.", "bad")
    lid, check, pin = form.get("library", ""), form.get("check", ""), form.get("pin", "0")
    stay = pin.isdigit() and len(pin) < 7 and int(pin) > 0
    db = _db(h)
    try:
        lib = D.get(db, lid) if lid and D.ID_RE.fullmatch(lid) else None
        problems = ([] if not lid or (lib and lib["status"] == "ready") else ["Pick one of the libraries shown."]) \
            + ([] if check in ("", *D.CHECKS) else ["Pick what happens to mockups outside the library."])
        version = 0
        if lib and stay:                               # the version shown on the form, when the library did not change; else its newest
            version = int(pin) if lid == D.choice(cfg, p)[0] and D.get(db, lid, int(pin)) else lib["version"]
        if not problems:
            try:
                S.set_project_design(h.app.config_path, h.app.state_dir(), name, lid, version, check)
            except S.SettingsError as e:
                problems = e.messages
        if problems:
            return _page(h, p.name, [("Design libraries", HOME), (p.name, "")], project_form(cfg, p, D.everything(db), csrf, problems), csrf, 422)
    finally:
        db.close()
    _back(h, csrf, HOME + "#dl-proj", f"Saved. {name}'s next runs use it (the factory applies settings at its next idle moment).")


def from_code_post(h, form, csrf: str) -> None:
    cfg, name = h.app.cfg(), form.get("project", "")
    if not any(p.name == name for p in cfg.projects):
        return _back(h, csrf, HOME, "There is no such project.", "bad")
    db = _db(h)
    try:
        rid = D.request_extract(db, name, time.time())
    finally:
        db.close()
    if rid is None:
        return _back(h, csrf, HOME, f"A proposal from {name}'s code is already waiting or running.", "bad")
    when = "Nothing runs while the factory is in dry run or paused; it starts when it is live." if cfg.dry_run else "The factory starts it within a poll (up to a minute)."
    _back(h, csrf, HOME, f"Asked an agent to propose a library from {name}'s code. {when}")


def default_save(h, form, csrf: str) -> None:
    lid, check = form.get("library", ""), form.get("check", "")
    db = _db(h)
    try:
        lib = D.get(db, lid) if D.ID_RE.fullmatch(lid) else None
    finally:
        db.close()
    if not lib or lib["status"] != "ready" or check not in D.CHECKS:
        return _back(h, csrf, HOME + "#dl-proj", "Pick a library and what happens to mockups outside it.", "bad")
    try:
        S.set_design_default(h.app.config_path, h.app.state_dir(), lid, check)
    except S.SettingsError as e:
        return _back(h, csrf, HOME + "#dl-proj", " · ".join(e.messages), "bad")
    _back(h, csrf, HOME + "#dl-proj", f"Saved. Projects that have not picked a library use {lib['name']}.")


GET = {HOME: list_get, "/design/library": detail_get, "/design/library/download": download_get, "/design/preview": preview_get,
       "/design/edit": edit_get, "/design/upload": upload_get, "/design/project": project_get}
POST = {"/design/edit/save": edit_save, "/design/library/approve": approve_post, "/design/library/discard": discard_post,
        "/design/library/delete": delete_post, "/design/from-code": from_code_post, "/design/project/save": project_save, "/design/default": default_save}
POST_UPLOAD = {"/design/upload": upload_post}
