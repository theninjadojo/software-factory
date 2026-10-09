"""The canvas of screens that the ticket's Design output card, the ticket's full design page and the Screens board's canvas share: the
screens laid out in rows at their own size, which app.js lets a person pan (drag, or scroll on a full page) and zoom (the buttons,
Ctrl + scroll or a pinch). Open notes are drawn on their screens as numbered pins. The Note tool drops a pin where a person clicks and
asks what should change there; the form posts to /review/add like Review the screens, and comes back to the canvas.

Without the page script the canvas scrolls and the screens keep a readable size. The page's CSP forbids inline styles, so the server
writes none: the pan and zoom are set by app.js through the element's style object."""
import re

from .. import reviewnotes as RN
from .views import csrf_field, esc

BACK = ("ticket", "design", "canvas")         # where a note left on a canvas returns to: the ticket, its design page, a Screens canvas
VIEWPORT = re.compile(r"[\s_-]+(desktop|tablet|phone|mobile)$", re.I)


def _pct(v: float) -> str:
    return f"{v / 10:.1f}".rstrip("0").rstrip(".")


def marks(notes: list[tuple[int, dict]]) -> str:
    """The notes on one image as SVG drawn over it in percentages (no inline styles: the page's CSP forbids them)."""
    out = ""
    for k, n in notes:
        x, y, w, h = (_pct(n[c]) for c in ("x", "y", "w", "h"))
        if n["w"]:
            out += f'<rect class="rv-box" data-note="{int(n["id"])}" x="{x}%" y="{y}%" width="{w}%" height="{h}%"></rect>'
        out += (f'<g class="rv-pin" data-note="{int(n["id"])}"><circle cx="{x}%" cy="{y}%" r="14"></circle>'
                f'<text x="{x}%" y="{y}%" text-anchor="middle" dominant-baseline="central">{k}</text></g>')
    return f'<svg class="rv-marks" aria-hidden="true">{out}</svg>'


def numbered(notes: list[dict]) -> list[tuple[int, dict]]:
    """The open notes with the numbers a person (and the designer) knows them by: their order, oldest first."""
    return list(enumerate((x for x in notes if x["sent"] is None), 1))


def group(name: str) -> str:
    """A screen's row on the canvas: its name without a trailing viewport, so "Done summary mobile" sits beside "Done summary"."""
    return VIEWPORT.sub("", name).strip() or name


def shot(key: str, src: str, alt: str, caption: str, href: str, narrow: bool = False) -> dict:
    return {"key": key, "src": src, "alt": alt, "caption": caption, "href": href, "narrow": narrow}


def canvas(cid: str, label: str, rows: list[tuple[str, list[dict]]], notes: list[tuple[int, dict]], hidden: str, back: str,
           full: bool = False, extra: str = "") -> str:
    """The canvas: a toolbar (the Note tool, zoom out, the zoom level, zoom in, Fit, 100%), the screens in `rows` [(title, [shot])] with
    their pins, and the hidden note form. `hidden` holds the form's scope fields (review._hidden); `back` is one of BACK. A full canvas
    (a page of its own) pans on scroll; the ticket's card leaves scrolling to the page."""
    if back not in BACK:
        raise ValueError(back)
    on: dict = {}
    for k, x in notes:
        on.setdefault(x["image"], []).append((k, x))
    body = ""
    for title, shots in rows:
        figs = ""
        for s in shots:
            here = on.get(s["key"], [])
            badge = f' <span class="rv-count" title="Open notes">{len(here)}</span>' if here else ""
            figs += (f'<figure class="pz-shot{" pz-narrow" if s["narrow"] else ""}" data-key="{esc(s["key"])}"><div class="pz-frame">'
                     f'<a href="{esc(s["href"])}" aria-label="Open {esc(s["caption"])} large"><img src="{esc(s["src"])}" alt="{esc(s["alt"])}" '
                     f'loading="lazy" draggable="false"></a>{marks(here)}</div><figcaption>{esc(s["caption"])}{badge}</figcaption></figure>')
        head = f'<h4 class="pz-title">{esc(title)}</h4>' if title else ""
        body += f'<section class="pz-row">{head}<div class="pz-shots">{figs}</div></section>'
    btn = '<button type="button" class="pz-b"'
    bar = (f'<div class="pz-bar" role="toolbar" aria-label="Canvas">'
           f'{btn} data-pz-tool="note" aria-pressed="false" title="Drop a note on a screen (N)">{NOTE_ICON}<span>Note</span></button>'
           f'<span class="pz-zoom">{btn} data-pz-zoom="out" aria-label="Zoom out" title="Zoom out (−)">−</button>'
           '<output class="pz-pct mono" data-pz-pct aria-live="polite">Fit</output>'
           f'{btn} data-pz-zoom="in" aria-label="Zoom in" title="Zoom in (+)">+</button>'
           f'{btn} data-pz-zoom="fit" title="Fit every screen (1)">Fit</button>{btn} data-pz-zoom="1" title="Actual size (0)">100%</button></span>'
           f'{extra}</div>')
    form = (f'<form method="post" action="/review/add" class="pz-note" data-pz-note hidden>{hidden}<input type="hidden" name="back" value="{esc(back)}">'
            '<input type="hidden" name="img" value=""><input type="hidden" name="x" value=""><input type="hidden" name="y" value="">'
            '<input type="hidden" name="w" value="0"><input type="hidden" name="h" value="0">'
            f'<label for="{esc(cid)}-text">What should change here?</label>'
            f'<textarea id="{esc(cid)}-text" name="text" rows="3" maxlength="{RN.MAX_TEXT}" required></textarea>'
            '<div class="pz-acts"><button>Add note</button><button type="button" class="pz-cancel" data-pz-cancel>Cancel</button></div></form>')
    return (f'<div class="pz{" pz-full" if full else ""}" id="{esc(cid)}" data-pz{" data-pz-wheel" if full else ""}>{bar}'
            f'<div class="pz-view" tabindex="0" role="group" aria-label="{esc(label)}. Drag to move around; Ctrl and scroll, or pinch, to zoom.">'
            f'<div class="pz-layer" data-pz-layer>{body}</div></div>{form}</div>')


def notes_list(notes: list[tuple[int, dict]], label: dict, open_url, cid: str) -> str:
    """The open notes beside a full canvas. Each shows its pin on the canvas (app.js); its screen's link opens Review the screens."""
    if not notes:
        return '<p class="rv-empty">No notes yet. Pick <strong>Note</strong> and click a screen, or open a screen to mark an area.</p>'
    items = "".join(f'<li><span class="rv-num" aria-hidden="true">{k}</span><div class="rv-body">'
                    f'<button type="button" class="rv-show" data-pz-show="{int(x["id"])}" data-pz-for="{esc(cid)}">{esc(x["text"])}</button>'
                    f'<span class="rv-meta"><a href="{esc(open_url(x["image"]))}">{esc(label.get(x["image"], "an image that is gone"))}</a></span></div></li>'
                    for k, x in notes)
    return f'<ol class="rv-list">{items}</ol>'


def scope_hidden(repo: str, n: int, csrf: str, board: bool = False) -> str:
    if board:
        return f'{csrf_field(csrf)}<input type="hidden" name="board" value="1">'
    return f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'


NOTE_ICON = ('<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" '
             'stroke-linejoin="round"><path d="M3 4h14v10H8l-4 3v-3H3z"></path></svg>')
OPEN_ICON = ('<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2">'
             '<path d="M9 2h5v5M14 2 8 8M7 3H2v11h11V9"></path></svg>')
