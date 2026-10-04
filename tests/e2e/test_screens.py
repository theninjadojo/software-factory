"""Every screen renders without errors and fits its viewport, on desktop and on a phone."""
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
         "/settings", "/settings?section=general", "/settings?section=runner", "/harnesses", "/credentials", "/telegram", "/slack", "/labels/issue?repo=your-org/standalone-service&n=7"]


@pytest.mark.parametrize("path", PAGES)
def test_screen_renders_and_does_not_overflow(page, server, viewport, path, tmp_path):
    r = page.goto(server.url + path)
    assert r.status == 200, path
    page.wait_for_load_state("networkidle")
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    page.screenshot(path=str(tmp_path / "s.png"), full_page=True)
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
