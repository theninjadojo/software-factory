"""Every screen renders without errors and fits its viewport, on desktop and on a phone."""
import os
import re
from pathlib import Path

import pytest

# body hides sideways overflow, so scrollWidth proves nothing: look for visible elements that stick out of the viewport instead,
# unless a scroll container (a table wrapper, a code block, the tab strip) is meant to scroll them.
STICKS_OUT = """() => {
  const w = document.documentElement.clientWidth, out = [];
  for (const el of document.querySelectorAll('main *, header *')) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (!r.width || st.visibility === 'hidden' || st.position === 'fixed') continue;
    if (r.right <= w + 1 && r.left >= -1) continue;
    let p = el.parentElement, scrolls = false;
    while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolls = true; break; } p = p.parentElement; }
    if (!scrolls && st.position !== 'absolute') out.push(el.tagName.toLowerCase() + '.' + el.className + ' right=' + Math.round(r.right));
  }
  return out.slice(0, 8);
}"""

PAGES = ["/", "/needs", "/tickets", "/runs", "/runs/1", "/prs", "/events", "/ticket?repo=your-org/standalone-service&n=9",
         "/screens", "/screens/canvas?repo=your-org/standalone-service",
         "/screens/review?img=screen:your-org/standalone-service:home:desktop:" + "c" * 40, "/settings", "/settings?section=general", "/settings?section=runner", "/harnesses", "/credentials", "/telegram", "/slack", "/labels/issue?repo=your-org/standalone-service&n=7"]


def keep_for_board(page, path, viewport):
    """When the worker's playwright-screens recipe runs this suite it sets E2E_SCREENS_DIR: keep one PNG per page and viewport there
    (named like the page, at most 5900px tall: the Screens board refuses taller images). Otherwise nothing is written."""
    out = os.environ.get("E2E_SCREENS_DIR")
    if not out:
        return
    name = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "factory"
    height = page.evaluate("document.documentElement.scrollHeight")
    width = page.evaluate("document.documentElement.clientWidth")
    clip = {"x": 0, "y": 0, "width": width, "height": min(height, 5900)}
    Path(out).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(Path(out) / f"{name[:46]}-{viewport}.png"), full_page=True, clip=clip, scale="css")


@pytest.mark.parametrize("path", PAGES)
def test_screen_renders_and_does_not_overflow(page, server, viewport, path, tmp_path):
    r = page.goto(server.url + path)
    assert r.status == 200, path
    page.wait_for_load_state("networkidle")
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    page.screenshot(path=str(tmp_path / "s.png"), full_page=True)
    keep_for_board(page, path, viewport)
    assert over <= 0, f"{path} scrolls sideways by {over}px on {viewport}"
    assert not page.evaluate(STICKS_OUT), f"{path} on {viewport}: {page.evaluate(STICKS_OUT)}"
    assert not page.errors, page.errors
    assert page.locator("main").is_visible()


def test_unsigned_visitor_is_sent_to_login(browser, server):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(server.url + "/runs")
    assert pg.url.endswith("/login")
    ctx.close()
