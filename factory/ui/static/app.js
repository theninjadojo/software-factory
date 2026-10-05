// The server renders (and escapes) all HTML; this script only swaps it in and adds two conveniences. Every form works without it.
(function () {
  document.documentElement.classList.add("js");

  // --- Ticket filters: a station or sort choice applies at once (the Apply button is there for pages without this script).
  document.addEventListener("change", function (e) {
    var f = e.target.closest("form[data-autosubmit]");
    if (f && e.target.tagName === "SELECT") f.submit();
  });

  // --- Send answers: keep the button honest about how many questions have an answer.
  function count(form) {
    var qs = form.querySelectorAll("[data-q=open]"), done = 0;
    for (var i = 0; i < qs.length; i++) {
      var r = qs[i].querySelector("input[type=radio]:checked"), t = qs[i].querySelector("input[name^=x_]");
      if (r || (t && t.value.trim())) done++;
    }
    var b = form.querySelector(".nd-send");
    if (b) { b.disabled = done === 0; b.textContent = done ? "Send answers · " + done + " of " + qs.length : "Send answers"; }
  }
  function countAll() {
    var forms = document.querySelectorAll("form.nd-form");
    for (var i = 0; i < forms.length; i++) count(forms[i]);
  }
  document.addEventListener("change", function (e) { var f = e.target.closest("form.nd-form"); if (f) count(f); });
  document.addEventListener("input", function (e) { var f = e.target.closest("form.nd-form"); if (f) count(f); });

  // --- Popups for tickets with many questions.
  document.addEventListener("click", function (e) {
    var open = e.target.closest("[data-dialog]");
    if (open) { var d = document.getElementById(open.getAttribute("data-dialog")); if (d && d.showModal) d.showModal(); return; }
    var close = e.target.closest("[data-close]");
    if (close) { var dd = close.closest("dialog"); if (dd) dd.close(); }
  });
  // Backdrop click closes a ticket popup only while its fields are empty, so typed text is never lost.
  document.addEventListener("click", function (e) {
    var d = e.target; if (!(d.matches && d.matches("dialog[data-backdrop]"))) return;
    var r = d.getBoundingClientRect();
    if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) return;
    var used = false;
    Array.prototype.forEach.call(d.querySelectorAll("input[name=title],textarea"), function (x) { if (x.value) used = true; });
    if (!used) d.close();
  });
  countAll();


  // --- Tickets list: row buttons post in the background and swap the row, so the page keeps its scroll position.
  // The POST is the normal one (CSRF, checks and the 303 are unchanged); we read the list it redirects to and take the row and flash from it.
  function note(tr, text, kind) {
    var old = tr.nextElementSibling;
    if (old && old.className === "row-note") old.parentNode.removeChild(old);
    var n = document.createElement("tr"), td = document.createElement("td"), d = document.createElement("div");
    n.className = "row-note"; td.colSpan = tr.children.length;
    d.className = "flash " + kind; d.setAttribute("role", kind === "bad" ? "alert" : "status");
    d.textContent = (kind === "bad" ? "Error: " : "") + text;
    td.appendChild(d); n.appendChild(td); tr.parentNode.insertBefore(n, tr.nextSibling);
    if (kind === "ok") setTimeout(function () { if (n.parentNode) n.parentNode.removeChild(n); }, 6000);
  }
  function find(root, key) {
    var rows = root.querySelectorAll("tr[data-row]");
    for (var i = 0; i < rows.length; i++) if (rows[i].getAttribute("data-row") === key) return rows[i];
    return null;
  }
  document.addEventListener("submit", function (e) {
    var f = e.target, tr = f.closest && f.closest("tr[data-row]");
    if (!tr || !window.fetch || !window.DOMParser || e.defaultPrevented) return;
    if (!/\/tickets\/(start|close)$/.test(f.getAttribute("action") || "")) return;
    e.preventDefault();
    if (tr.getAttribute("aria-busy") === "true") return;
    var key = tr.getAttribute("data-row"), btns = tr.querySelectorAll("button"), data = new URLSearchParams(new FormData(f)), sub = e.submitter, label = sub ? sub.textContent : "";
    if (sub && sub.name) data.set(sub.name, sub.value);
    tr.setAttribute("aria-busy", "true");
    for (var i = 0; i < btns.length; i++) btns[i].disabled = true;
    if (sub) { sub.style.minWidth = sub.offsetWidth + "px"; sub.textContent = "Working…"; }
    function fail(text) {
      tr.removeAttribute("aria-busy");
      for (var j = 0; j < btns.length; j++) btns[j].disabled = false;
      if (sub) sub.textContent = label;
      note(tr, text, "bad");
    }
    fetch(f.getAttribute("action"), { method: "POST", body: data, credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401 || /\/login/.test(r.url)) { location.href = "/login"; return null; }
        return r.ok ? r.text() : Promise.reject();
      })
      .then(function (html) {
        if (html === null) return;
        var doc = new DOMParser().parseFromString(html, "text/html"), fl = doc.querySelector("main > .flash");
        var text = fl ? fl.textContent : "Done.";
        if (fl && fl.classList.contains("bad")) { fail(text); return; }
        var fresh = find(doc, key);
        if (fresh) { var row = document.importNode(fresh, true); tr.parentNode.replaceChild(row, tr); tr = row; }
        note(tr, text, "ok");
        if (fresh) { var b = tr.querySelector("button, a"); if (b) b.focus({ preventScroll: true }); }
        else setTimeout(function () { var n = tr.nextElementSibling; if (n && n.className === "row-note") n.parentNode.removeChild(n); if (tr.parentNode) tr.parentNode.removeChild(tr); }, 6000);
      })
      .catch(function () { fail("Couldn’t reach the factory. Nothing was changed that we know of. Try again."); });
  });

  // --- Parts that load by themselves (GitHub is slow): fetch, then put the answer where the loader was.
  function calm(root) {
    if (!window.matchMedia || !window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var nets = (root || document).querySelectorAll("svg.ld-net, svg.fm");
    for (var i = 0; i < nets.length; i++) if (nets[i].pauseAnimations) nets[i].pauseAnimations();
  }
  function get(url) {
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (r.status === 401) { location.href = "/login"; return null; }
      return r.ok ? r.text() : null;
    });
  }
  function fill(slot) {
    get(slot.getAttribute("data-load")).then(function (html) {
      if (html === null) { slot.querySelector(".ld p").textContent = "Could not load this. Reload the page to try again."; return; }
      slot.outerHTML = html; countAll(); calm();
    }).catch(function () { var p = slot.querySelector(".ld p"); if (p) p.textContent = "Could not reach the factory. Reload the page to try again."; });
  }
  var slots = document.querySelectorAll("[data-load]");
  for (var k = 0; k < slots.length; k++) fill(slots[k]);
  calm();

  // --- Tickets: picking a ticket loads only its detail, behind a loader where the detail goes; the list stays as it is.
  var tpl = document.getElementById("ld-detail");
  function open(href, push) {
    var page = document.querySelector(".sd-page"), detail = document.querySelector(".sd-detail");
    if (!page || !detail || !tpl) { location.href = href; return; }
    var url = new URL(href, location.href);
    detail.outerHTML = tpl.innerHTML;
    page.classList.add("has-sel");
    var picks = document.querySelectorAll(".sd-pick");
    for (var i = 0; i < picks.length; i++) {
      var on = new URL(picks[i].href, location.href).search === url.search;
      picks[i].classList.toggle("on", on);
      if (on) picks[i].setAttribute("aria-current", "page"); else picks[i].removeAttribute("aria-current");
    }
    if (push) history.pushState({ ticket: true }, "", href);
    if (window.innerWidth <= 760) window.scrollTo(0, 0);
    else { var spot0 = document.querySelector(".sd-detail"), top = spot0 ? spot0.getBoundingClientRect().top : 0; if (top < 0) window.scrollBy(0, top - 16); }   // list scrolled down: bring the detail's top back into view
    calm();
    get("/fragment/detail" + url.search).then(function (html) {
      var spot = document.querySelector(".sd-detail");
      if (html === null || !spot) { location.href = href; return; }
      spot.outerHTML = html; countAll(); calm();
    }).catch(function () { location.href = href; });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a.sd-pick");
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0 || !window.fetch || !window.history) return;
    e.preventDefault();
    open(a.href, true);
  });
  window.addEventListener("popstate", function () { location.reload(); });

  // --- Review the screens: drag over a screen to mark an area, click (or tap) for a pin; the area goes into the form's number fields.
  (function () {
    var frame = document.querySelector("[data-review]"), form = document.getElementById("rv-new");
    if (!frame || !form) return;
    var NS = "http://www.w3.org/2000/svg", img = frame.querySelector("img"), svg = frame.querySelector("svg");
    var where = form.querySelector("[data-where]"), text = form.querySelector("textarea"), idle = where.textContent;
    var box = document.createElementNS(NS, "rect"), pin = document.createElementNS(NS, "circle"), f = {}, start = null, touch = false;
    box.setAttribute("class", "rv-draft"); pin.setAttribute("class", "rv-draft-pin"); pin.setAttribute("r", "10");
    ["x", "y", "w", "h"].forEach(function (k) { f[k] = form.querySelector("[data-area=" + k + "]"); });
    function clamp(v) { return Math.max(0, Math.min(100, v)); }
    function r1(v) { return Math.round(v * 10) / 10; }
    function pt(e) {
      var r = img.getBoundingClientRect();
      return { x: clamp((e.clientX - r.left) / r.width * 100), y: clamp((e.clientY - r.top) / r.height * 100) };
    }
    function area(p, q) {
      var w = Math.abs(q.x - p.x), h = Math.abs(q.y - p.y);
      if (w < 1 && h < 1) return { x: r1(p.x), y: r1(p.y), w: 0, h: 0 };
      return { x: r1(Math.min(p.x, q.x)), y: r1(Math.min(p.y, q.y)), w: Math.max(r1(w), 0.1), h: Math.max(r1(h), 0.1) };
    }
    function show(a) {
      if (box.parentNode) svg.removeChild(box);
      if (pin.parentNode) svg.removeChild(pin);
      form.classList.toggle("drafting", !!a);
      if (!a) { where.textContent = idle; return; }
      if (a.w > 0) {
        box.setAttribute("x", a.x + "%"); box.setAttribute("y", a.y + "%"); box.setAttribute("width", a.w + "%"); box.setAttribute("height", a.h + "%");
        svg.appendChild(box);
        where.textContent = "Area " + a.w + "% × " + a.h + "% at " + a.x + "%, " + a.y + "%";
      } else {
        pin.setAttribute("cx", a.x + "%"); pin.setAttribute("cy", a.y + "%");
        svg.appendChild(pin);
        where.textContent = "Pin at " + a.x + "%, " + a.y + "%";
      }
    }
    function typed() {
      var v = {}, ok = true;
      ["x", "y", "w", "h"].forEach(function (k) {
        var s = f[k].value.trim();
        v[k] = s === "" && (k === "w" || k === "h") ? 0 : parseFloat(s);
        if (!(v[k] >= 0 && v[k] <= 100)) ok = false;
      });
      return ok ? v : null;
    }
    function fill(a) { f.x.value = a.x; f.y.value = a.y; f.w.value = a.w; f.h.value = a.h; show(a); }
    frame.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      touch = e.pointerType === "touch"; start = pt(e);
      if (touch) return;                                   // a finger may be scrolling the page: wait for it to lift
      e.preventDefault();
      try { frame.setPointerCapture(e.pointerId); } catch (err) {}
      show(area(start, start));
    });
    frame.addEventListener("pointermove", function (e) { if (start && !touch) show(area(start, pt(e))); });
    frame.addEventListener("pointerup", function (e) {
      if (!start) return;
      var a = touch ? { x: r1(start.x), y: r1(start.y), w: 0, h: 0 } : area(start, pt(e));
      start = null;
      fill(a);
      text.focus(touch ? undefined : { preventScroll: true });
    });
    frame.addEventListener("pointercancel", function () { start = null; show(typed()); });
    form.addEventListener("input", function (e) { if (e.target.hasAttribute("data-area")) show(typed()); });
    form.addEventListener("submit", function (e) {
      if (typed()) return;
      e.preventDefault();
      where.textContent = "Mark an area on the screen first, or set it by numbers.";
    });
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-show]");
      if (!b) return;
      var id = b.getAttribute("data-show"), li = b.closest("li"), was = li.classList.contains("on"), on = document.querySelectorAll(".rv .on");
      for (var i = 0; i < on.length; i++) on[i].classList.remove("on");
      if (was) return;
      var hit = document.querySelectorAll('.rv-marks [data-note="' + id + '"]');
      for (var j = 0; j < hit.length; j++) hit[j].classList.add("on");
      li.classList.add("on");
      if (hit.length && hit[0].scrollIntoView) hit[0].scrollIntoView({ block: "nearest" });
    });
  })();

  // --- Live refresh of the Floor, the Needs-you page and a running ticket (whichever #live is on the page now).
  function busy(live) {
    var a = document.activeElement;
    if (a && live.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    if (live.querySelector("dialog[open]")) return true;
    var f = live.querySelectorAll("input[type=text], input:not([type]), textarea");
    for (var i = 0; i < f.length; i++) if (f[i].value) return true;
    return live.querySelector("input[type=radio]:checked") !== null;
  }
  function tick() {
    var live = document.getElementById("live");
    if (!live || document.hidden || busy(live)) return;
    get((live.getAttribute("data-src") || "/fragment/overview") + location.search)
      .then(function (html) { if (html !== null && document.getElementById("live") === live) { live.innerHTML = html; countAll(); calm(); } })
      .catch(function () {});
  }
  setInterval(tick, 5000);
})();
