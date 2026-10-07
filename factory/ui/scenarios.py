"""The Tests page: the register of test scenarios, one scenario with its manual results, and the CSV and Excel round trip.

Every write goes through factory/scenarios.py, which cleans what a person typed. A ticket is made only when someone clicks, from stored
fields (never from the browser's text), with the start action looked up in labels.start_options."""
import logging
import secrets
import sqlite3
import threading
import urllib.error
from urllib.parse import urlencode

from .. import scenarios as SC
from . import labels as L
from . import views
from .views import ago, badge, csrf_field, cards_table, esc, trow

log = logging.getLogger("factory.ui")
HEADS = ["ID", "Feature", "Title", "Playwright test", "Last result", "Last tested", "Tickets"]
SORTS = {"id": "ID", "feature": "Feature", "title": "Title", "result": "Last result", "tested": "Last tested"}
BADGE = {"pass": "good", "fail": "bad", "blocked": "warn", "": ""}
_staged: dict = {}                      # token -> (csrf, repo, rows): an uploaded file waiting for its confirmation
_staged_lock = threading.Lock()
MAX_STAGED = 20


def url(repo: str = "", **q) -> str:
    q = {k: v for k, v in {"repo": repo, **q}.items() if v}
    return "/scenarios" + ("?" + urlencode(q) if q else "")


def view_url(repo: str, ref: str) -> str:
    return "/scenarios/view?" + urlencode({"repo": repo, "ref": ref})


def tabs(active: str) -> str:
    """The strip shared by Screens and Tests: they are one area."""
    link = lambda href, name: f'<a href="{href}"{" class=active aria-current=page" if name == active else ""}>{name}</a>'
    return f'<p class="tabs" aria-label="Screens and Tests">{link("/screens", "Screens")}{link("/scenarios", "Tests")}</p>'


def _db(h) -> sqlite3.Connection:
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    SC.ensure_tables(db)
    return db


def _repo(cfg, value: str) -> str:
    repo = value or (cfg.repos[0] if cfg.repos else "")
    return L._repo(cfg, repo)


def _page(h, title: str, body: str, csrf: str, status: int = 200, flash=None, kind: str = "ok") -> None:
    shown = None if flash else L.flash_pop(csrf)
    msg, k = (flash, kind) if flash else (shown if shown else (None, "ok"))
    h._send(status, views.page(title, tabs("Tests") + body, "/scenarios", csrf, wide=True, flash=msg, flash_kind=k))


def _back(h, csrf: str, where: str, msg: str, kind: str = "ok") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect(where)


def _repo_links(cfg, repo: str) -> str:
    if len(cfg.repos) < 2:
        return ""
    return views.chips([(r, r) for r in cfg.repos], repo, lambda r: url(r))


# ---------------------------------------------------------------- the register

def register_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo = _repo(cfg, q.get("repo", ""))
    except L.Refused as e:
        return _page(h, "Tests", f'<p class="muted">{esc(e)}</p>', csrf, 400)
    if not repo:
        return _page(h, "Tests", '<p class="muted">No repository is configured yet.</p>', csrf)
    db = _db(h)
    try:
        rows = SC.listing(db, repo)
    finally:
        db.close()
    feature, result, status, text = q.get("feature", ""), q.get("result", ""), q.get("status", ""), q.get("q", "")[:100]
    sort = q.get("sort", "id") if q.get("sort", "id") in SORTS else "id"
    if result not in ("", "none", *SC.RESULTS):
        result = ""
    if status not in ("", *SC.STATUSES):
        status = ""
    shown = SC.filtered(rows, feature, result, status, text, sort)
    keep = lambda **o: url(repo, **{"feature": feature, "result": result, "status": status, "q": text, "sort": sort, **o})
    feats = SC.features(rows)
    options = [("", f"All {len(rows)}")] + [(f, f"{'No feature' if f == '-' else f} {n}") for f, n in feats]
    chips = views.chips(options, feature, lambda v: keep(feature=v))
    sel = lambda name, cur, opts: (f'<label>{name}<select name="{name.lower()}">'
                                   + "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(t)}</option>' for v, t in opts) + "</select></label>")
    filters = (f'<form method="get" action="/scenarios" class="filters"><input type="hidden" name="repo" value="{esc(repo)}">'
               f'<input type="hidden" name="feature" value="{esc(feature)}"><input type="hidden" name="sort" value="{esc(sort)}">'
               f'<label>Search<input type="search" name="q" value="{esc(text)}" maxlength="100" placeholder="Search title or steps"></label>'
               + sel("Result", result, [("", "Any")] + [("none", "Not run")] + [(r, SC.RESULT_LABEL[r]) for r in SC.RESULTS])
               + sel("Status", status, [("", "Any")] + [(s, s.capitalize()) for s in SC.STATUSES])
               + '<button>Filter</button>' + (f' <a href="{esc(url(repo))}">Clear filters</a>' if feature or result or status or text else "") + '</form>')
    actions = (f'<p class="actions"><a class="btn" href="/scenarios/import?{esc(urlencode({"repo": repo}))}">Import CSV or Excel</a> '
               f'<a class="btn" href="/scenarios/export?{esc(urlencode({"repo": repo}))}">Export CSV</a> '
               f'<a class="btn" href="/scenarios/export?{esc(urlencode({"repo": repo, "format": "xlsx"}))}">Export Excel</a> '
               f'<a class="btn" href="/scenarios/edit?{esc(urlencode({"repo": repo}))}">Add scenario</a></p>'
               f'<form method="post" action="/scenarios/discover" class="actions">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
               '<button>Add from the repository\'s tests</button> <span class="muted">Reads the test files on GitHub; you see the list before anything is saved.</span></form>')
    if not rows:
        table = ('<div class="card"><h3>Empty register</h3><p>No scenarios yet for this repository.</p>'
                 '<p class="muted">Add one, import a CSV or Excel file from your spreadsheet, or add the tests the repository already has.</p></div>')
    elif not shown:
        table = f'<p class="muted">No scenarios match these filters. <a href="{esc(url(repo))}">Clear filters</a></p>'
    else:
        head = lambda key, name: f'<a href="{esc(keep(sort=key))}">{esc(name)}</a>' if key in SORTS else esc(name)
        keys = {"Feature": "feature", "Title": "title", "Last result": "result", "Last tested": "tested", "ID": "id"}
        cells = []
        for r in shown:
            cells.append(trow(HEADS, [
                f'<a href="{esc(view_url(repo, r["ref"]))}">{esc(r["ref"])}</a>', esc(r["feature"] or "—"),
                esc(r["title"]), f'<code>{esc(r["pw_test"])}</code>' if r["pw_test"] else '<span class="muted">Not linked</span>',
                badge(SC.RESULT_LABEL[r["last_result"]], BADGE[r["last_result"]]),
                esc(ago(r["last_tested"])) if r["last_tested"] else "never",
                " ".join(views.ticket_link(repo, n) for n in r["tickets"]) or "—"]))
        table = cards_table(HEADS, "".join(cells))
        for name, key in keys.items():                         # sortable column headers are links
            table = table.replace(f"<th>{esc(name)}</th>", f'<th aria-sort="{"ascending" if sort == key else "none"}">{head(key, name)}</th>')
    body = (f'<h1>Tests</h1>{_repo_links(cfg, repo)}{actions}'
            f'<p class="muted" id="feat">Feature</p>{chips}{filters}{table}')
    _page(h, "Tests", body, csrf)


# ---------------------------------------------------------------- one scenario

def _find(h, q: dict):
    cfg = h.app.cfg()
    repo = _repo(cfg, q.get("repo", ""))
    db = _db(h)
    s = SC.get(db, repo, q.get("ref", ""))
    return cfg, repo, db, s


def view_get(h, q: dict, csrf: str) -> None:
    try:
        cfg, repo, db, s = _find(h, q)
    except L.Refused as e:
        return _page(h, "Tests", f'<p class="muted">{esc(e)}</p>', csrf, 400)
    try:
        hist = SC.history(db, s["id"]) if s else []
        links = SC.ticket_links(db, s["id"]) if s else []
    finally:
        db.close()
    if not s:
        return _page(h, "Tests", f'<p class="muted">No such scenario. <a href="{esc(url(repo))}">Back to Tests</a></p>', csrf, 404)
    ref = s["ref"]
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="ref" value="{esc(ref)}">'
    meta = (f'<p><strong>Feature</strong> {esc(s["feature"] or "—")} <strong>Status</strong> {esc(s["status"].capitalize())}'
            + (f' <strong>Test</strong> <code>{esc(s["pw_test"])}</code>' if s["pw_test"] else "") + '</p>')
    card = (f'<div class="card"><h3>Scenario</h3>{meta}<p><strong>Steps</strong></p><p class="pre-wrap">{esc(s["steps"] or "—")}</p>'
            f'<p><strong>Expected</strong></p><p class="pre-wrap">{esc(s["expected"] or "—")}</p>'
            f'<p><a class="btn" href="/scenarios/edit?{esc(urlencode({"repo": repo, "ref": ref}))}">Edit</a></p></div>')
    names = {"pass": "Pass", "fail": "Fail", "blocked": "Blocked"}
    radios = "".join(f'<label class="check"><input type="radio" name="result" value="{r}" required> {names[r]}</label> ' for r in SC.RESULTS)
    record = (f'<form method="post" action="/scenarios/result" class="card">{hidden}<h3>Record a manual result</h3>'
              f'<fieldset><legend>Result</legend>{radios}</fieldset>'
              f'<label>Comment (what you saw)<textarea name="comment" rows="4" maxlength="{SC.MAX_COMMENT}"></textarea></label>'
              '<button>Save result</button></form>')
    if hist:
        rows = "".join(
            f'<tr><td><input type="checkbox" name="c" value="{c["id"]}" aria-label="Include comment from {esc(c["author"] or "a person")}, {esc(ago(c["ts"]))}"></td>'
            f'<td>{badge(SC.RESULT_LABEL[c["result"]], BADGE[c["result"]])} <span class="muted">{esc(c["source"])}</span></td>'
            f'<td class="wrap">{esc(c["comment"] or "—")}</td><td class="nowrap muted">{esc(c["author"])} {esc(ago(c["ts"]))}</td></tr>' for c in hist)
        listing = f'<div class="scroll"><table><tbody>{rows}</tbody></table></div>'
    else:
        listing = '<p class="muted">Not run yet.</p>'
    starts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t, _ in L.start_options(cfg))
    open_links = "".join(f'<li>{esc("Correct" if k == "fix" else "Add")}: {views.ticket_link(repo, n)}</li>' for k, n in links)
    again = ('<label class="check"><input type="checkbox" name="again" value="1"> Create another even if a ticket of that kind exists</label>'
             if links else "")
    tick = (f'<form method="post" action="/scenarios/ticket">{hidden}<h3>Tickets</h3>{listing}'
            f'<label>Then<select name="start">{starts}</select></label>{again}'
            '<p><button name="kind" value="fix">Create ticket: correct this scenario</button> '
            '<button name="kind" value="add">Create ticket: add a scenario</button></p>'
            '<p class="muted">Ticks choose which comments go in. Nothing starts until you pick a start label.</p></form>'
            + (f'<ul>{open_links}</ul>' if open_links else ""))
    body = (f'<p><a href="{esc(url(repo))}">Tests</a> / {esc(ref)}</p><h1>{esc(s["title"])}</h1>'
            f'<div class="grid2">{card}{record}</div><h2>History and comments</h2>{tick}')
    _page(h, f"{ref} · Tests", body, csrf)


def result_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo = _repo(cfg, form.get("repo", ""))
    except L.Refused as e:
        return _back(h, csrf, "/scenarios", str(e), "bad")
    ref = form.get("ref", "")
    db = _db(h)
    try:
        s = SC.get(db, repo, ref)
        if not s:
            return _back(h, csrf, url(repo), "No such scenario.", "bad")
        try:
            SC.add_result(db, s["id"], form.get("result", ""), form.get("comment", ""), "UI")
        except ValueError as e:
            return _back(h, csrf, view_url(repo, ref), f"Not saved: {e}.", "bad")
    finally:
        db.close()
    _back(h, csrf, view_url(repo, ref), f"Saved the result for {ref}.")


def _create(h, cfg, repo: str, title: str, body: str, labels: list[str]) -> int:
    """A ticket on the local tracker when it is on (as the Tickets page does), else on GitHub. Raises L.Refused."""
    if cfg.local_enabled:
        from . import localtickets as LT
        try:
            return LT.create(cfg, repo, title, body, labels)
        except ValueError as e:
            raise L.Refused(str(e)) from None
    gh = L._gh(h)
    if gh is None:
        raise L.Refused(L.NO_TOKEN)
    try:
        return int(gh.create_ticket(repo, title, body, labels)["number"])
    except (urllib.error.URLError, OSError, KeyError, TypeError, ValueError) as e:
        log.warning("scenarios: creating a ticket on %s failed", repo)
        raise L.Refused(L._github_error(e) if isinstance(e, urllib.error.HTTPError)
                        else "GitHub did not accept the ticket. Nothing was created.") from None


def ticket_post(h, form, csrf: str) -> None:
    """Make a ticket from a scenario and the ticked comments. The text is rebuilt here from stored rows; the start action is checked
    against the allow-list; nothing starts unless one was picked."""
    cfg = h.app.cfg()
    kind = form.get("kind", "")
    try:
        repo = _repo(cfg, form.get("repo", ""))
        if kind not in SC.KINDS:
            raise L.Refused("Pick which ticket to create.")
        chosen = next((o for o in L.start_options(cfg) if o[0] == (form.get("start") or "").strip()), None)
        if chosen is None:
            raise L.Refused("That start action is not available.")
        ids = {L._number(v) for v in form.getall("c")}
    except L.Refused as e:
        return _back(h, csrf, "/scenarios", str(e), "bad")
    ref = form.get("ref", "")
    back = view_url(repo, ref)
    db = _db(h)
    try:
        s = SC.get(db, repo, ref)
        if not s:
            return _back(h, csrf, url(repo), "No such scenario.", "bad")
        have = [n for k, n in SC.ticket_links(db, s["id"]) if k == kind]
        if have and form.get("again") != "1":
            return _back(h, csrf, back, f"{views.ref(repo, have[-1])} is already open for this. Tick “Create another” to make a second one.", "bad")
        picked = [c for c in SC.history(db, s["id"]) if c["id"] in ids]
        title, body = SC.ticket(kind, s, picked)
        try:
            n = _create(h, cfg, repo, title, body, [chosen[2]] if chosen[2] else [])
        except L.Refused as e:
            return _back(h, csrf, back, str(e), "bad")
        SC.link_ticket(db, s["id"], kind, n)
    finally:
        db.close()
    log.info("scenarios: created %s#%s from %s", repo, n, ref)
    _back(h, csrf, back, f"Created {views.ref(repo, n)}." + (f" {chosen[1]} starts at the factory's next poll." if chosen[2] else " Nothing started."))


# ---------------------------------------------------------------- add and edit

def edit_get(h, q: dict, csrf: str) -> None:
    try:
        cfg, repo, db, s = _find(h, q)
    except L.Refused as e:
        return _page(h, "Tests", f'<p class="muted">{esc(e)}</p>', csrf, 400)
    db.close()
    if q.get("ref") and not s:
        return _page(h, "Tests", '<p class="muted">No such scenario.</p>', csrf, 404)
    s = s or {"ref": "", "feature": q.get("feature", "")[:100], "title": "", "steps": "", "expected": "", "status": "active", "pw_test": "", "revision": 0}
    f = lambda name, label, key, area=0: (
        f'<label>{label}<textarea name="{key}" rows="{area}" maxlength="{SC.LIMITS[key]}">{esc(s[key])}</textarea></label>' if area else
        f'<label>{label}<input name="{key}" value="{esc(s[key])}" maxlength="{SC.LIMITS[key]}"></label>')
    opts = "".join(f'<option value="{x}"{" selected" if x == s["status"] else ""}>{x.capitalize()}</option>' for x in SC.STATUSES)
    body = (f'<h1>{esc("Edit " + s["ref"] if s["ref"] else "Add scenario")}</h1>'
            f'<form method="post" action="/scenarios/save" class="card">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
            f'<input type="hidden" name="ref" value="{esc(s["ref"])}"><input type="hidden" name="revision" value="{int(s["revision"])}">'
            + f("", "Feature", "feature") + f("", "Title", "title") + f("", "Steps", "steps", 6) + f("", "Expected result", "expected", 3)
            + f'<label>Status<select name="status">{opts}</select></label>' + f("", "Playwright test (optional)", "pw_test")
            + f'<button>Save</button> <a href="{esc(url(repo))}">Cancel</a></form>')
    _page(h, "Tests", body, csrf)


def save_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo = _repo(cfg, form.get("repo", ""))
    except L.Refused as e:
        return _back(h, csrf, "/scenarios", str(e), "bad")
    ref, db = form.get("ref", ""), _db(h)
    try:
        try:
            if ref:
                rev = form.get("revision", "")
                if not rev.isdigit() or not SC.update(db, repo, ref, form, int(rev)):
                    return _back(h, csrf, view_url(repo, ref), "Someone changed this scenario after you opened it. Reload to see their version.", "bad")
            else:
                ref = SC.create(db, repo, form)
        except ValueError as e:
            return _back(h, csrf, "/scenarios/edit?" + urlencode({"repo": repo, **({"ref": ref} if ref else {})}), f"Not saved: {e}.", "bad")
    finally:
        db.close()
    _back(h, csrf, view_url(repo, ref), f"Saved {ref}.")


# ---------------------------------------------------------------- CSV

def export_get(h, q: dict, csrf: str) -> None:
    try:
        repo = _repo(h.app.cfg(), q.get("repo", ""))
    except L.Refused:
        return h._send(400, "unknown repository", "text/plain")
    db = _db(h)
    try:
        rows = SC.listing(db, repo)
    finally:
        db.close()
    name = "".join(c if c.isalnum() else "-" for c in repo)
    if q.get("format") == "xlsx":
        return h._send(200, SC.export_xlsx(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       {"Content-Disposition": f'attachment; filename="scenarios-{name}.xlsx"'})
    h._send(200, SC.export_csv(rows).encode("utf-8"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="scenarios-{name}.csv"'})


def import_get(h, q: dict, csrf: str) -> None:
    try:
        repo = _repo(h.app.cfg(), q.get("repo", ""))
    except L.Refused as e:
        return _page(h, "Tests", f'<p class="muted">{esc(e)}</p>', csrf, 400)
    _page(h, "Import · Tests", _upload_form(repo, csrf), csrf)


def _upload_form(repo: str, csrf: str) -> str:
    return (f'<h1>Import CSV or Excel</h1><form method="post" action="/scenarios/import/preview" enctype="multipart/form-data" class="card">{csrf_field(csrf)}'
            f'<input type="hidden" name="repo" value="{esc(repo)}"><div class="field"><input type="file" name="file" accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></div>'
            '<p class="muted">Use a file exported from here, or a sheet with a title column (and optionally id, feature, steps, expected, status, playwright_test). '
            'For an Excel workbook (.xlsx, up to 1 MB) the first sheet is read. '
            'You see what would change before anything is written. Cells that start with = + - or @ get a leading apostrophe in a CSV export.</p>'
            '<button>Preview</button></form>')


def preview_post(h, form, csrf: str, files=()) -> None:
    """The uploaded file is parsed and planned; nothing is written until the confirm button."""
    cfg = h.app.cfg()
    try:
        repo = _repo(cfg, form.get("repo", ""))
    except L.Refused as e:
        return _page(h, "Tests", f'<p class="muted">{esc(e)}</p>', csrf, 400)
    if not files or files[0][1] is None:
        return _page(h, "Import · Tests", _upload_form(repo, csrf), csrf, 400, "Choose a CSV or Excel file of up to 1 MB (a larger one is refused).", "bad")
    try:
        rows = SC.parse_upload(files[0][1])
    except ValueError as e:
        return _page(h, "Import · Tests", _upload_form(repo, csrf), csrf, 422, str(e), "bad")
    _preview(h, csrf, repo, rows, "Import preview")


def _preview(h, csrf: str, repo: str, rows: list[dict], heading: str, note: str = "") -> None:
    """What importing `rows` would do, staged until the confirm button (which writes it through apply_post)."""
    db = _db(h)
    try:
        items = SC.plan(db, repo, rows)
    finally:
        db.close()
    token = secrets.token_urlsafe(16)
    with _staged_lock:
        while len(_staged) >= MAX_STAGED:
            _staged.pop(next(iter(_staged)))
        _staged[token] = (csrf, repo, rows)
    n = lambda a: sum(1 for i in items if i["action"] == a)
    errors = [i["error"] for i in items if i["action"] == "error"]
    conflicts = [i for i in items if i["action"] == "conflict"]
    summary = (f'<p>{len(items)} rows: {n("create")} new, {n("update")} changed, {n("conflict")} conflict{"" if n("conflict") == 1 else "s"}, '
               f'{n("same")} unchanged, {len(errors)} error{"" if len(errors) == 1 else "s"}.</p>')
    problems = "".join(f'<p class="bad-text">{esc(e)}</p>' for e in errors[:50])
    confl = "".join(
        f'<fieldset><legend>{esc(i["ref"])}: {esc(i["title"])} was edited here after the export</legend>'
        f'<label class="check"><input type="radio" name="use-{esc(i["ref"])}" value="system" checked> Keep system version</label> '
        f'<label class="check"><input type="radio" name="use-{esc(i["ref"])}" value="file"> Use file version</label></fieldset>' for i in conflicts)
    todo = n("create") + n("update") + n("conflict")
    confirm = ('<p class="muted">Nothing is saved until you confirm.</p>' if not errors else '<p class="muted">Fix the errors and upload the file again. Nothing was saved.</p>')
    if not errors:
        confirm += (f'<button>Confirm import of {todo} row{"" if todo == 1 else "s"}</button>' if todo else '<p>Nothing to import: the file matches the register.</p>')
    body = (f'<h1>{esc(heading)}</h1><form method="post" action="/scenarios/import/apply" class="card">{csrf_field(csrf)}'
            f'<input type="hidden" name="token" value="{esc(token)}">{note}{summary}{problems}{confl}{confirm}'
            f' <a href="{esc(url(repo))}">Cancel</a></form>')
    _page(h, f"{heading} · Tests", body, csrf)


def discover_post(h, form, csrf: str) -> None:
    """Read the repository's test files on GitHub (a few seconds) and preview a scenario for each test not linked to one yet."""
    from .. import testfind as TF
    from ..github import GitHub
    from . import integrations as I
    cfg = h.app.cfg()
    try:
        repo = _repo(cfg, form.get("repo", ""))
    except L.Refused as e:
        return _back(h, csrf, "/scenarios", str(e), "bad")
    token = I.read_secret(cfg, "github")
    if not token:
        return _back(h, csrf, url(repo), "Set the GitHub token first (Settings → Credentials): the tests are read from GitHub.", "bad")
    try:
        found, info = TF.discover(GitHub(token), repo)
    except Exception as e:
        log.warning("scenarios: reading the tests of %s failed: %s", repo, type(e).__name__)
        return _back(h, csrf, url(repo), f"Could not read {repo} on GitHub ({type(e).__name__}). Check the token can read it.", "bad")
    db = _db(h)
    try:
        rows, linked = TF.new_only(found, SC.listing(db, repo))
    finally:
        db.close()
    capped = len(rows) > SC.MAX_ROWS
    rows = rows[:SC.MAX_ROWS]
    n = lambda k, one, many: f"{k:,} {one if k == 1 else many}"
    note = (f'<p>Read {n(info["files"], "test file", "test files")} on <code>{esc(info["ref"])}</code> and found {n(len(found), "test", "tests")}'
            + (f', {n(linked, "already linked to a scenario", "already linked to scenarios")}' if linked else "") + '.</p>'
            + (f'<p class="muted">{n(info["skipped"], "file", "files")} could not be read.</p>' if info["skipped"] else "")
            + (f'<p class="muted">Only the first {TF.MAX_FILES} test files were read.</p>' if info["capped"] else "")
            + (f'<p class="muted">Only the first {SC.MAX_ROWS:,} new tests are shown; import them, then run this again for the rest.</p>' if capped else "")
            + '<p class="muted">Each new test becomes an active scenario: its feature is the file it is in, its title the test\'s name (or the first '
              'line of its docstring), and it is linked to the test. Steps and expected result are left for you to fill in.</p>')
    if not rows:
        return _back(h, csrf, url(repo), f"No new tests: {n(len(found), 'test', 'tests')} found in {n(info['files'], 'file', 'files')}, "
                     + ("all already linked to scenarios." if found else "nothing to add."))
    _preview(h, csrf, repo, rows, "Tests found", note)


def apply_post(h, form, csrf: str) -> None:
    with _staged_lock:
        staged = _staged.pop(form.get("token", ""), None)
    if not staged or staged[0] != csrf:
        return _back(h, csrf, "/scenarios", "That import expired. Upload the file again.", "bad")
    _, repo, rows = staged
    use_file = {k[4:] for k, v in form.items() if k.startswith("use-") and v == "file"}
    db = _db(h)
    try:
        done = SC.apply(db, repo, rows, use_file)
    except (ValueError, sqlite3.Error) as e:
        return _back(h, csrf, url(repo), f"Nothing was imported: {e}", "bad")
    finally:
        db.close()
    log.info("scenarios: imported into %s: %s", repo, done)
    _back(h, csrf, url(repo), f"Imported: {done['created']} new, {done['updated']} changed, {done['kept']} kept as they were.")


GET = {"/scenarios": register_get, "/scenarios/view": view_get, "/scenarios/edit": edit_get, "/scenarios/export": export_get, "/scenarios/import": import_get}
POST = {"/scenarios/save": save_post, "/scenarios/result": result_post, "/scenarios/ticket": ticket_post, "/scenarios/import/apply": apply_post, "/scenarios/discover": discover_post}
POST_UPLOAD = {"/scenarios/import/preview": preview_post}
