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

  // --- The canvas of screens (canvas.py): drag (or scroll, on a page of its own) to pan; the buttons, Ctrl + scroll, a pinch or the
  // keys + - 0 1 to zoom. The Note tool drops a pin where a person clicks a screen and opens the note form there. The state lives on
  // the element, and pzSync puts it back after the live refresh has redrawn the ticket (which drops the style and hidden changes).
  var PZ_MIN = 0.05, PZ_MAX = 4;
  function pzState(root) {
    if (!root._pz) root._pz = { s: 1, x: 0, y: 0, touched: false, mode: "move", draft: null };
    return root._pz;
  }
  function pzParts(root) {
    return { view: root.querySelector(".pz-view"), layer: root.querySelector("[data-pz-layer]"), pct: root.querySelector("[data-pz-pct]"),
             form: root.querySelector("[data-pz-note]") };
  }
  function pzApply(root) {
    var st = pzState(root), p = pzParts(root);
    if (!p.layer) return;
    p.layer.style.transform = "translate(" + st.x + "px," + st.y + "px) scale(" + st.s + ")";
    p.layer.style.setProperty("--pz-inv", String(1 / st.s));
    p.view.style.backgroundPosition = st.x + "px " + st.y + "px";
    if (p.pct) p.pct.textContent = Math.round(st.s * 100) + "%";
  }
  // The first view fits the widest row and starts at the top (screens are often whole, tall pages); Fit shows everything.
  function pzFit(root, all) {
    var st = pzState(root), p = pzParts(root);
    if (!p.layer || !p.view.clientWidth) return;
    var w = p.layer.offsetWidth, h = p.layer.offsetHeight, vw = p.view.clientWidth, vh = p.view.clientHeight - 56;   // 56: the toolbar
    st.s = Math.max(PZ_MIN, Math.min(1, vw / w, all ? vh / h : Infinity));
    st.x = (vw - w * st.s) / 2; st.y = 56 + (all ? Math.max(0, (vh - h * st.s) / 2) : 0);
    pzApply(root);
  }
  function pzZoom(root, k, cx, cy) {
    var st = pzState(root), p = pzParts(root), r = p.view.getBoundingClientRect();
    if (cx === undefined) { cx = r.width / 2; cy = r.height / 2; }
    var s = Math.max(PZ_MIN, Math.min(PZ_MAX, st.s * k));
    st.x = cx - (cx - st.x) * s / st.s; st.y = cy - (cy - st.y) * s / st.s; st.s = s; st.touched = true;
    pzApply(root);
  }
  function pzMode(root, mode) {
    var st = pzState(root);
    st.mode = mode;
    root.setAttribute("data-mode", mode);
    var b = root.querySelector("[data-pz-tool=note]");
    if (b) b.setAttribute("aria-pressed", mode === "note" ? "true" : "false");
    if (mode !== "note") pzClose(root);
  }
  function pzClose(root) {
    var st = pzState(root), p = pzParts(root);
    st.draft = null;
    if (p.form) p.form.hidden = true;
    var d = root.querySelectorAll(".pz-draft");
    for (var i = 0; i < d.length; i++) d[i].parentNode.removeChild(d[i]);
  }
  function pzDraft(root) {                     // the open note: its pin on the screen, the form beside it
    var st = pzState(root), p = pzParts(root), d = st.draft;
    var old = root.querySelectorAll(".pz-draft");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
    if (!d || !p.form) return;
    var fig = null, figs = root.querySelectorAll(".pz-shot");
    for (var j = 0; j < figs.length; j++) if (figs[j].getAttribute("data-key") === d.key) fig = figs[j];
    if (!fig) { pzClose(root); return; }
    var frame = fig.querySelector(".pz-frame"), pin = document.createElement("span");
    pin.className = "pz-draft"; pin.style.left = d.x + "%"; pin.style.top = d.y + "%";
    frame.appendChild(pin);
    p.form.hidden = false;
    var pr = pin.getBoundingClientRect(), rr = root.getBoundingClientRect(), fw = p.form.offsetWidth, fh = p.form.offsetHeight;
    var left = pr.right - rr.left + 12, top = pr.top - rr.top - 12;
    if (left + fw > rr.width - 8) left = Math.max(8, pr.left - rr.left - fw - 12);
    top = Math.max(8, Math.min(top, rr.height - fh - 8));
    p.form.style.left = left + "px"; p.form.style.top = top + "px";
  }
  function pzSync() {
    var all = document.querySelectorAll("[data-pz]");
    for (var i = 0; i < all.length; i++) {
      var root = all[i], st = pzState(root);
      if (!root._pzReady) pzSetup(root);
      root.setAttribute("data-mode", st.mode);
      var b = root.querySelector("[data-pz-tool=note]");
      if (b) b.setAttribute("aria-pressed", st.mode === "note" ? "true" : "false");
      if (st.touched) pzApply(root); else pzFit(root);
      if (st.draft) pzDraft(root); else pzClose(root);
    }
  }
  function pzSetup(root) {
    root._pzReady = true;
    var st = pzState(root), p = pzParts(root), view = p.view, pts = {}, drag = null, pinch = null, moved = false;
    if (!view) return;
    root.addEventListener("load", function () { if (!st.touched) pzFit(root); }, true);       // an image arrived: the layout grew
    root.addEventListener("click", function (e) {
      var t = e.target, z = t.closest("[data-pz-zoom]"), tool = t.closest("[data-pz-tool]");
      if (z) {
        var v = z.getAttribute("data-pz-zoom");
        if (v === "in") pzZoom(root, 1.25); else if (v === "out") pzZoom(root, 0.8);
        else if (v === "fit") { st.touched = true; pzFit(root, true); } else pzZoom(root, 1 / st.s);
        return;
      }
      if (tool) { pzMode(root, st.mode === "note" ? "move" : "note"); return; }
      if (t.closest("[data-pz-cancel]")) { pzClose(root); return; }
      if (!view.contains(t)) return;
      if (moved) { e.preventDefault(); moved = false; return; }           // the end of a drag is not a click on a screen
      if (st.mode !== "note") return;
      e.preventDefault();
      var fig = t.closest(".pz-shot"), img = fig && fig.querySelector("img");
      if (!img || !img.contains(t) && !t.closest(".pz-frame")) { pzClose(root); return; }
      var r = img.getBoundingClientRect();
      st.draft = { key: fig.getAttribute("data-key"), x: Math.round(Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100)) * 10) / 10,
                   y: Math.round(Math.max(0, Math.min(100, (e.clientY - r.top) / r.height * 100)) * 10) / 10 };
      pzDraft(root);
      var ta = p.form.querySelector("textarea"); if (ta) ta.focus({ preventScroll: true });
    });
    if (p.form) p.form.addEventListener("submit", function () {          // the live refresh may have reset the hidden fields: set them now
      var d = st.draft;
      if (!d) return;
      p.form.querySelector("[name=img]").value = d.key;
      p.form.querySelector("[name=x]").value = d.x; p.form.querySelector("[name=y]").value = d.y;
      p.form.querySelector("[name=w]").value = 0; p.form.querySelector("[name=h]").value = 0;
    });
    view.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(pts);
      if (ids.length === 2) {
        var a = pts[ids[0]], b = pts[ids[1]];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), s: st.s }; drag = null;
      } else if (ids.length === 1) {
        drag = { x: e.clientX, y: e.clientY, ox: st.x, oy: st.y, id: e.pointerId, live: false }; moved = false;
      }
    });
    view.addEventListener("pointermove", function (e) {
      if (!pts[e.pointerId]) return;
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(pts);
      if (pinch && ids.length === 2) {
        var a = pts[ids[0]], b = pts[ids[1]], r = view.getBoundingClientRect(), d = Math.hypot(a.x - b.x, a.y - b.y);
        pzZoom(root, pinch.s * d / pinch.d / st.s, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
        moved = true;
        return;
      }
      if (!drag) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.live && Math.abs(dx) + Math.abs(dy) < 5) return;
      if (!drag.live) { drag.live = true; try { view.setPointerCapture(drag.id); } catch (err) {} view.classList.add("pz-drag"); }
      moved = true; st.touched = true;
      st.x = drag.ox + dx; st.y = drag.oy + dy;
      pzApply(root);
    });
    function up(e) {
      delete pts[e.pointerId];
      if (Object.keys(pts).length < 2) pinch = null;
      if (drag && drag.id === e.pointerId) { drag = null; view.classList.remove("pz-drag"); }
      if (e.type === "pointercancel") moved = false;
    }
    view.addEventListener("pointerup", up);
    view.addEventListener("pointercancel", up);
    view.addEventListener("dragstart", function (e) { e.preventDefault(); });
    view.addEventListener("wheel", function (e) {
      var r = view.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); pzZoom(root, Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top); return; }
      if (!root.hasAttribute("data-pz-wheel")) return;                 // on the ticket, scrolling scrolls the page
      e.preventDefault();
      st.x -= e.deltaX; st.y -= e.deltaY; st.touched = true;
      pzApply(root);
    }, { passive: false });
    view.addEventListener("keydown", function (e) {
      var k = e.key, step = 60;
      if (k === "+" || k === "=") pzZoom(root, 1.25);
      else if (k === "-") pzZoom(root, 0.8);
      else if (k === "0") pzZoom(root, 1 / st.s);
      else if (k === "1") { st.touched = true; pzFit(root, true); }
      else if (k === "n" || k === "N") pzMode(root, st.mode === "note" ? "move" : "note");
      else if (k === "ArrowLeft" || k === "ArrowRight" || k === "ArrowUp" || k === "ArrowDown") {
        st.x += k === "ArrowLeft" ? step : k === "ArrowRight" ? -step : 0; st.y += k === "ArrowUp" ? step : k === "ArrowDown" ? -step : 0;
        st.touched = true; pzApply(root);
      } else return;
      e.preventDefault();
    });
    root.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (st.draft) { pzClose(root); view.focus(); } else if (st.mode === "note") pzMode(root, "move");
    });
  }
  // A note in the list beside a canvas: show its pin, centred.
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-pz-show]"), root = b && document.getElementById(b.getAttribute("data-pz-for"));
    if (!root) return;
    var id = b.getAttribute("data-pz-show"), li = b.closest("li"), was = li.classList.contains("on");
    var on = document.querySelectorAll(".pz-side .on, .pz .on");
    for (var i = 0; i < on.length; i++) on[i].classList.remove("on");
    if (was) return;
    li.classList.add("on");
    var hit = root.querySelectorAll('.rv-marks [data-note="' + id + '"]');
    for (var j = 0; j < hit.length; j++) hit[j].classList.add("on");
    var pin = root.querySelector('.rv-pin[data-note="' + id + '"] circle');
    if (!pin) return;
    var fig = pin.closest(".pz-shot"); if (fig) fig.classList.add("on");
    var st = pzState(root), view = root.querySelector(".pz-view"), vr = view.getBoundingClientRect(), pr = pin.getBoundingClientRect();
    if (st.s < 0.5) pzZoom(root, 0.5 / st.s, pr.left + pr.width / 2 - vr.left, pr.top + pr.height / 2 - vr.top);
    pr = pin.getBoundingClientRect();
    st.x += vr.width / 2 - (pr.left + pr.width / 2 - vr.left); st.y += vr.height / 2 - (pr.top + pr.height / 2 - vr.top); st.touched = true;
    pzApply(root);
  });
  window.addEventListener("resize", function () {
    var all = document.querySelectorAll("[data-pz]");
    for (var i = 0; i < all.length; i++) if (!pzState(all[i]).touched) pzFit(all[i]);
  });
  pzSync();

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

  // --- Tickets keep their place across an action. A form that posts from Tickets or a ticket's page comes back to this exact
  // address (filters, search, project and the open ticket), and the scroll position and the open parts are put back. The
  // server still checks where it may send you back to. The message about what was done stays in sight for a few seconds.
  var KEEP = "sf-keep", here = location.pathname + location.search;
  function keepable() { return location.pathname === "/tickets" || location.pathname === "/ticket"; }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!keepable() || !f || (f.method || "").toLowerCase() !== "post") return;
    var back = f.querySelector('input[name="back"]');
    if (back && back.value.charAt(0) === "/") back.value = here;       // only an address; some forms name a place by a word
    var open = Array.prototype.map.call(document.querySelectorAll("details.sd-fold[open]"), function (d) { return d.getAttribute("data-fold"); });
    var list = document.querySelector(".sd-list");
    try { sessionStorage.setItem(KEEP, JSON.stringify({ url: here, y: window.scrollY, open: open, list: list ? list.scrollTop : 0, at: Date.now() })); } catch (err) {}
  }, true);
  (function () {
    var k = null;
    try { k = JSON.parse(sessionStorage.getItem(KEEP) || "null"); sessionStorage.removeItem(KEEP); } catch (err) {}
    if (!k || k.url !== here || Date.now() - k.at > 120000) return;
    Array.prototype.forEach.call(document.querySelectorAll("details.sd-fold"), function (d) {
      if (k.open.indexOf(d.getAttribute("data-fold")) >= 0) d.open = true;
    });
    var list = document.querySelector(".sd-list");
    if (list) list.scrollTop = k.list || 0;
    window.scrollTo(0, k.y || 0);
  })();
  Array.prototype.forEach.call(document.querySelectorAll("[data-flash]"), function (f) {
    setTimeout(function () { f.classList.add("settled"); }, 8000);
  });

  document.addEventListener("toggle", function (e) {      // a canvas measured while its part was folded: fit it the first time it opens
    var d = e.target;
    if (!d.classList || !d.classList.contains("sd-fold") || !d.open || d.hasAttribute("data-fitted")) return;
    var fit = d.querySelector('[data-pz-zoom="fit"]');
    if (!fit) return;
    d.setAttribute("data-fitted", "");
    var imgs = Array.prototype.filter.call(d.querySelectorAll(".pz img"), function (i) { return !i.complete; }), left = imgs.length;
    function go() { requestAnimationFrame(function () { fit.click(); }); }
    if (!left) return go();                                  // the screens load once they show: fit when the last one has its size
    imgs.forEach(function (i) { function one() { if (--left === 0) go(); } i.addEventListener("load", one, { once: true }); i.addEventListener("error", one, { once: true }); });
  }, true);
  document.addEventListener("toggle", function (e) {      // one station's confirmation at a time
    var d = e.target;
    if (!d.classList || !d.classList.contains("sd-sta") || !d.open) return;
    Array.prototype.forEach.call(document.querySelectorAll(".sd-sta[open]"), function (o) { if (o !== d) o.open = false; });
    var b = d.querySelector(".sd-staconf button"); if (b) b.focus({ preventScroll: true });
  }, true);

  // --- A ticket's folded parts: Expand all / Collapse all. The small menus (More, Filters, the page's settings, Move) close on a
  // click elsewhere or Escape.
  var MENUS = ".sd-menu[open], .tk-line[open], .kb-menu-btn[open]";
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-fold-all]");
    if (b) {
      var open = b.getAttribute("data-fold-all") === "1", art = b.closest("article") || document;
      Array.prototype.forEach.call(art.querySelectorAll("details.sd-fold"), function (d) { d.open = open; });
      return;
    }
    Array.prototype.forEach.call(document.querySelectorAll(MENUS), function (m) { if (!m.contains(e.target)) m.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    Array.prototype.forEach.call(document.querySelectorAll(".sd-menu[open], .tk-line[open]"), function (m) {
      m.open = false; var s = m.querySelector("summary"); if (s) s.focus();
    });
  });

  // --- Board: dropping a card on a column opens the card's menu at that column: Working shows its start choices, Done its close
  // confirmation. The button in the menu is the confirmation (the ordinary form post), so a drop never changes anything by itself,
  // and a column the factory fills by itself says so. One menu is open at a time; closing it also folds its choice back up.
  (function () {
    var drag = null;
    function alertBox() { return document.querySelector("[data-kb-alert]"); }
    function say(text) { var box = alertBox(); if (box) { box.hidden = !text; box.textContent = text; } }
    function shut(except) {
      Array.prototype.forEach.call(document.querySelectorAll(".kb-menu[open]"), function (m) { if (m !== except) m.open = false; });
    }
    document.addEventListener("toggle", function (e) {
      var d = e.target;
      if (!d.classList) return;
      if (d.classList.contains("kb-menu")) {
        if (d.open) shut(d);
        else Array.prototype.forEach.call(d.querySelectorAll(".kb-opt[open]"), function (o) { o.open = false; });
      } else if (d.classList.contains("kb-opt") && d.open) {
        var b = d.querySelector(".kb-conf button"); if (b) b.focus();
      }
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var m = document.querySelector(".kb-menu[open]");
      if (!m) return;
      var o = m.querySelector(".kb-opt[open]");
      if (o) { o.open = false; o.querySelector("summary").focus(); } else { m.open = false; m.querySelector("summary").focus(); }
    });
    document.addEventListener("dragstart", function (e) {
      var c = e.target.closest && e.target.closest("[data-card]");
      if (!c) return;
      drag = c; shut(null); say("");
      var ghost = null;
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", "");
        // The whole card follows the pointer (not just the link inside it), grabbed where it was picked up.
        // The picture is of a copy set apart from the board: taken in place, the browser also takes in
        // whatever lies behind and around the card (the column, its neighbours).
        if (e.dataTransfer.setDragImage) {
          var r = c.getBoundingClientRect();
          ghost = c.cloneNode(true); ghost.removeAttribute("id"); ghost.classList.add("kb-ghost"); ghost.style.width = r.width + "px";
          document.body.appendChild(ghost);
          e.dataTransfer.setDragImage(ghost, Math.max(0, e.clientX - r.left), Math.max(0, e.clientY - r.top));
        }
      }
      // Dim the card left behind only after the browser has taken its picture, so the dragged copy stays fully visible.
      setTimeout(function () { if (ghost) ghost.remove(); if (drag === c) c.classList.add("dragging"); }, 0);
    });
    document.addEventListener("dragend", function () {
      if (drag) drag.classList.remove("dragging");
      drag = null;
      Array.prototype.forEach.call(document.querySelectorAll(".kb-card.dragging"), function (x) { x.classList.remove("dragging"); });
      Array.prototype.forEach.call(document.querySelectorAll(".kb-col.over"), function (x) { x.classList.remove("over"); });
    });
    document.addEventListener("dragover", function (e) {
      var col = drag && e.target.closest && e.target.closest(".kb-col");
      if (!col || col.getAttribute("data-col") === drag.getAttribute("data-col")) return;
      e.preventDefault();
      Array.prototype.forEach.call(document.querySelectorAll(".kb-col.over"), function (x) { if (x !== col) x.classList.remove("over"); });
      col.classList.add("over");
    });
    document.addEventListener("drop", function (e) {
      var col = drag && e.target.closest && e.target.closest(".kb-col");
      if (!col) return;
      e.preventDefault();
      var card = drag, to = col.getAttribute("data-col"), word = (col.getAttribute("aria-label") || to).split(",")[0];
      drag = null; col.classList.remove("over"); card.classList.remove("dragging");
      if (to === card.getAttribute("data-col")) return;
      var menu = card.querySelector(".kb-menu"), opt = card.querySelector('details.kb-opt[data-to="' + to + '"]');
      if (!menu) return;
      if (!opt) {
        var note = to === "working" ? card.querySelector(".kb-note") : null;
        say(note ? note.textContent : "The factory moves tickets to " + word + " by itself. Nothing was changed.");
        return;
      }
      say("");
      menu.open = true;
      if (to === "working") opt.querySelector("summary").focus();     // several ways to start: the person picks one
      else opt.open = true;                                           // one way to get there: straight to its confirmation
    });
  })();

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
      slot.outerHTML = html; countAll(); calm(); tod(); pzSync();
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
    else {
      // list scrolled down: bring the detail's top back into view
      var shown = document.querySelector(".sd-detail"), gap = shown ? shown.getBoundingClientRect().top : 0;
      if (gap < 0) window.scrollBy(0, gap - 16);
    }
    calm();
    get("/fragment/detail" + url.search).then(function (html) {
      var spot = document.querySelector(".sd-detail");
      if (html === null || !spot) { location.href = href; return; }
      spot.outerHTML = html; countAll(); calm(); tod(); pzSync();
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

  // --- The floor's time of day and weather: Auto runs a 40 s day, Day, Dusk and Night hold it. The page keeps the choice (data-tod, the Time buttons
  // inside the live part are redrawn every few seconds) and Auto's phase follows the clock, so a refresh does not restart the day.
  var TOD = "auto";
  try { TOD = localStorage.getItem("floor-time") || "auto"; } catch (e) {}
  var WX = "clear";
  try { WX = localStorage.getItem("floor-weather") || "clear"; } catch (e) {}
  var SNOWING = false, MELT = 0;
  function settle() {         // snow settles on painted grass in stages while it falls (style.css gd-settle), and melts back in 12 s when it clears
    var root = document.documentElement;
    if (WX === "snow" && !SNOWING) { SNOWING = true; clearTimeout(MELT); root.setAttribute("data-snow", "falling"); }
    else if (WX !== "snow" && SNOWING) {
      SNOWING = false; root.setAttribute("data-snow", "melting");
      MELT = setTimeout(function () { root.removeAttribute("data-snow"); }, 12000);
    }
  }
  function tod() {
    document.documentElement.setAttribute("data-tod", TOD);
    var phase = -((Date.now() % 40000) / 1000) + "s", sky = document.querySelectorAll(".fm-tod, .fm-nglow"), b = document.querySelectorAll(".fm-time [data-tod]"), i;
    for (i = 0; i < sky.length; i++) sky[i].style.animationDelay = phase;
    for (i = 0; i < b.length; i++) b[i].setAttribute("aria-pressed", b[i].getAttribute("data-tod") === TOD ? "true" : "false");
    document.documentElement.setAttribute("data-wx", WX);
    settle();
    var flash = -((Date.now() % 6000) / 1000) + "s", fx = document.querySelectorAll(".fm-bolt, .fm-flash");     // the lightning keeps its own 6 s beat
    for (i = 0; i < fx.length; i++) fx[i].style.animationDelay = flash;
    var w = document.querySelectorAll(".fm-time [data-wx]");
    for (i = 0; i < w.length; i++) w[i].setAttribute("aria-pressed", w[i].getAttribute("data-wx") === WX ? "true" : "false");
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".fm-time [data-tod], .fm-time [data-wx]");
    if (!b) return;
    if (b.hasAttribute("data-tod")) { TOD = b.getAttribute("data-tod"); try { localStorage.setItem("floor-time", TOD); } catch (er) {} }
    else { WX = b.getAttribute("data-wx"); try { localStorage.setItem("floor-weather", WX); } catch (er) {} }
    tod();
  });
  tod();

  // --- Live refresh of the Floor, the Needs-you page and a running ticket (whichever #live is on the page now).
  // The fragment is patched into the page in place, so running animations and open popups survive a refresh.
  // A popup or form that must survive keeps a stable id or position inside #live. The user owns `open` on
  // <details>/<dialog>, typed values and checked state; SMIL `begin` is ignored (the server sets it from the clock).
  var ANIM = "animate,animateMotion,animateTransform,set";
  function animSig(svg) {
    var out = [], a = svg.querySelectorAll(ANIM);
    for (var i = 0; i < a.length; i++) {
      var s = a[i].nodeName;
      for (var j = 0; j < a[i].attributes.length; j++) if (a[i].attributes[j].name !== "begin") s += " " + a[i].attributes[j].name + "=" + a[i].attributes[j].value;
      out.push(s);
    }
    return out.join("|");
  }
  function same(a, b) {
    if (a.nodeType !== b.nodeType) return false;
    return a.nodeType !== 1 || (a.nodeName === b.nodeName && a.id === b.id && a.getAttribute("data-row") === b.getAttribute("data-row"));
  }
  function skipAttr(tag, name, form, el) {
    if (name === "open") return tag === "details" || tag === "dialog";
    if (name === "begin") return /^(animate|animatemotion|animatetransform|set)$/i.test(tag);
    return form && /^(value|checked|selected)$/.test(name);
  }
  function patchAttrs(c, n) {
    var tag = c.nodeName.toLowerCase(), form = /^(input|textarea|select|option)$/.test(tag) && c.getAttribute("type") !== "hidden";
    var i, name;
    for (i = c.attributes.length - 1; i >= 0; i--) {
      name = c.attributes[i].name;
      if (n.hasAttribute(name) || skipAttr(tag, name, form, c)) continue;
      c.removeAttribute(name);
    }
    for (i = 0; i < n.attributes.length; i++) {
      name = n.attributes[i].name;
      if (skipAttr(tag, name, form, c) || c.getAttribute(name) === n.attributes[i].value) continue;
      c.setAttribute(name, n.attributes[i].value);
    }
    if (tag === "input" && !form) c.value = n.getAttribute("value") || "";
  }
  function patchNode(c, n) {
    if (c.nodeType !== 1) { if (c.nodeValue !== n.nodeValue) c.nodeValue = n.nodeValue; return c; }
    if (c.nodeName.toLowerCase() === "svg" && !c.ownerSVGElement && animSig(c) !== animSig(n)) {
      var r = document.importNode(n, true);
      c.parentNode.replaceChild(r, c);
      return r;
    }
    patchAttrs(c, n);
    if (c.nodeName !== "TEXTAREA") patchKids(c, n);
    return c;
  }
  function patchKids(cur, nxt) {
    var c = cur.firstChild, n = nxt.firstChild, nn;
    while (n) {
      nn = n.nextSibling;
      if (c && same(c, n)) c = patchNode(c, n).nextSibling;
      else cur.insertBefore(document.importNode(n, true), c);
      n = nn;
    }
    while (c) { nn = c.nextSibling; cur.removeChild(c); c = nn; }
  }
  function morph(live, html) {
    var t = document.createElement("template");
    if (!t.content || !document.importNode) { live.innerHTML = String(html); return; }
    t.innerHTML = html;
    patchKids(live, t.content);
  }
  var lastLive = null, lastHtml = null;
  function busy(live) {
    var a = document.activeElement;
    if (a && live.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    var f = live.querySelectorAll("input[type=text], input:not([type]), textarea");
    for (var i = 0; i < f.length; i++) if (f[i].value) return true;
    return live.querySelector("input[type=radio]:checked") !== null;
  }
  function tick() {
    var live = document.getElementById("live");
    if (!live || document.hidden || busy(live)) return;
    get((live.getAttribute("data-src") || "/fragment/overview") + location.search)
      .then(function (html) { if (html !== null && document.getElementById("live") === live) {
        if (live === lastLive && html === lastHtml) return;
        lastLive = live; lastHtml = html;
        morph(live, html); countAll(); calm(); tod(); pzSync();
      } })
      .catch(function () {});
  }
  setInterval(tick, 5000);
  function chatTick() {          // the ticket chat, the factory chat or a new-project interview while a reply is awaited: the panel replaces itself (its form is hidden meanwhile)
    var box = document.querySelector(".sd-chat[data-pending=\"1\"], .np-draft[data-pending=\"1\"], .fc-chat[data-pending=\"1\"]");
    if (!box || document.hidden) return;
    get(box.getAttribute("data-src")).then(function (html) { if (html !== null && box.isConnected) box.outerHTML = html; }).catch(function () {});
  }
  setInterval(chatTick, 3000);

  // --- Create forms: paste or drop images next to the file picker. One list feeds the form's file input (DataTransfer), so the server gets
  // the same multipart "file" parts as with the picker and vets each one. Limits here only save a failed submit.
  var TYPES = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif" }, EXT = /\.(png|jpe?g|gif|pdf|txt|md|log|json|csv)$/i;
  function size(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function setupAttach(box) {
    var input = box.querySelector("input[type=file]");
    if (!input || typeof DataTransfer === "undefined" || box.hasAttribute("data-ready")) return;
    box.setAttribute("data-ready", "");
    var maxFiles = +box.getAttribute("data-max-files") || 0, maxMb = +box.getAttribute("data-max-mb") || 0;
    var files = [], pasted = 0, mac = /Mac|iPhone|iPad/.test(navigator.platform || "");
    var label = input.parentNode, zone = document.createElement("div"), hint = document.createElement("span");
    zone.className = "at-zone"; hint.className = "at-hint muted";
    hint.textContent = "or paste a screenshot (" + (mac ? "Cmd+V" : "Ctrl+V") + ") or drop images here";
    label.parentNode.insertBefore(zone, label.nextSibling); zone.appendChild(label); zone.appendChild(hint);
    var list = document.createElement("ul"), status = document.createElement("p");
    list.className = "at-list"; list.setAttribute("aria-label", "Attached files");
    status.className = "at-status"; status.setAttribute("role", "status");
    var help = box.querySelector("p.muted");
    box.insertBefore(list, help); box.insertBefore(status, help);
    function say(msg, bad) { status.textContent = msg; status.className = "at-status " + (bad ? "bad-text" : "at-ok"); status.setAttribute("role", bad ? "alert" : "status"); }
    function count() { return files.length + " of " + (maxFiles || "\u221e") + " files attached."; }
    function sync() {
      var dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
      list.textContent = "";
      files.forEach(function (f) {
        var li = document.createElement("li"), th = document.createElement("span"), txt = document.createElement("span"),
            nm = document.createElement("span"), meta = document.createElement("span"), rm = document.createElement("button");
        li.className = "at-row"; th.className = "at-thumb"; txt.className = "at-txt"; nm.className = "at-name"; meta.className = "muted";
        nm.textContent = f.name;
        meta.textContent = (f.name.split(".").pop() || "").toUpperCase() + " \u00b7 " + size(f.size);
        if (/^image\/(png|jpeg|gif)$/.test(f.type)) {
          var r = new FileReader(), im = document.createElement("img");
          im.alt = ""; r.onload = function () { im.src = r.result; th.appendChild(im); }; r.readAsDataURL(f);
        }
        rm.type = "button"; rm.className = "secondary"; rm.textContent = "Remove"; rm.setAttribute("aria-label", "Remove " + f.name);
        rm.addEventListener("click", function () {
          files.splice(files.indexOf(f), 1); sync(); say("Removed " + f.name + ". " + count());
          var next = list.querySelectorAll("button")[0]; (next || input).focus();
        });
        txt.appendChild(nm); txt.appendChild(meta); li.appendChild(th); li.appendChild(txt); li.appendChild(rm); list.appendChild(li);
      });
    }
    function add(incoming) {
      var added = [];
      incoming.forEach(function (f) {
        var ext = TYPES[f.type], name = f.name;
        if (f.type === "image/webp") return say("That image is WebP, which is not allowed. Take a screenshot instead, or save it as png or jpg.", true);
        if (ext && !EXT.test(name)) { pasted++; name = "pasted-image-" + pasted + "." + ext; f = new File([f], name, { type: f.type }); }
        else if (ext && /^(image\.png|image\.jpe?g|image\.gif|blob)$/i.test(name)) { pasted++; name = "pasted-image-" + pasted + "." + ext; f = new File([f], name, { type: f.type }); }
        if (!EXT.test(name)) return say(name + " is a " + ((name.split(".").pop() || "").toUpperCase() || "this type of") + " file, which is not allowed. Use png, jpg or gif for images.", true);
        if (maxFiles && files.length >= maxFiles) return say("You can attach up to " + maxFiles + " files. " + name + " was not added.", true);
        if (maxMb && f.size > maxMb * 1048576) return say(name + " is " + (f.size / 1048576).toFixed(1) + " MB. The limit is " + maxMb + " MB per file.", true);
        files.push(f); added.push(name);
      });
      if (added.length) { sync(); say("Added " + added.join(", ") + ". " + count()); }
    }
    input.addEventListener("change", function () { var picked = Array.prototype.slice.call(input.files); input.value = ""; add(picked); });
    var form = box.closest("form");
    form.addEventListener("paste", function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [], imgs = [];
      for (var i = 0; i < items.length; i++) if (items[i].kind === "file" && /^image\//.test(items[i].type)) imgs.push(items[i].getAsFile());
      if (!imgs.length) return;
      e.preventDefault(); add(imgs.filter(Boolean));
    });
    ["dragenter", "dragover"].forEach(function (t) {
      form.addEventListener(t, function (e) {
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
        e.preventDefault(); zone.classList.add("drag"); hint.textContent = "Drop images to attach them";
      });
    });
    function undrag() { zone.classList.remove("drag"); hint.textContent = "or paste a screenshot (" + (mac ? "Cmd+V" : "Ctrl+V") + ") or drop images here"; }
    form.addEventListener("dragleave", function (e) { if (!form.contains(e.relatedTarget)) undrag(); });
    form.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault(); undrag(); add(Array.prototype.slice.call(e.dataTransfer.files));
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-attach]"), setupAttach);

  // --- Settings: say how many changes are not saved yet, and let Reset put a setting back to its default.
  function changed(el) {
    if (el.type === "checkbox" || el.type === "radio") return el.checked !== el.defaultChecked;
    if (el.tagName === "SELECT") { for (var i = 0; i < el.options.length; i++) if (el.options[i].selected !== el.options[i].defaultSelected) return true; return false; }
    return el.value !== el.defaultValue;
  }
  function dirty(form) {
    var n = 0, els = form.elements, note = form.querySelector(".s-dirty");
    for (var i = 0; i < els.length; i++) if (els[i].name && els[i].type !== "hidden" && els[i].name.indexOf("confirm__") !== 0 && changed(els[i])) n++;
    if (!note) return;
    if (!note.dataset.idle) note.dataset.idle = note.textContent;
    note.textContent = n ? (n === 1 ? "1 change not saved" : n + " changes not saved") : note.dataset.idle;
    note.classList.toggle("on", n > 0);
    var save = form.querySelector(".s-save button");
    if (save && !save.dataset.saving) save.disabled = n === 0 || !!form.querySelector(".sr-sec .f-err");      // a marked repository: nothing to save yet
  }
  function onEdit(e) { var f = e.target.closest("form[data-dirty]"); if (f) dirty(f); }
  Array.prototype.forEach.call(document.querySelectorAll("form[data-dirty]"), dirty);
  document.addEventListener("submit", function (e) {
    var b = !e.defaultPrevented && e.target.matches && e.target.matches("form[data-dirty]") && e.target.querySelector(".s-save button");
    if (b) { b.dataset.saving = "1"; setTimeout(function () { b.disabled = true; b.textContent = "Saving…"; }, 0); }
  });
  document.addEventListener("input", onEdit);
  document.addEventListener("change", onEdit);
  Array.prototype.forEach.call(document.querySelectorAll(".s-reset"), function (b) { b.hidden = false; });
  // A link to one setting (from the search on the Settings home) opens the Advanced or More section it is in.
  if (location.hash.indexOf("#s-") === 0) {
    var t = document.getElementById(decodeURIComponent(location.hash.slice(1))), d = t && t.closest("details");
    if (d) { d.open = true; t.scrollIntoView(); }
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".s-reset");
    if (!b) return;
    var el = document.getElementById(b.dataset.for);
    if (!el) return;
    if (el.type === "checkbox") el.checked = b.dataset.value === "1"; else el.value = b.dataset.value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.focus();
  });

  // --- Mermaid diagrams in stage documents: the server marks a fence (pre.mm-src) and keeps its escaped text. Each is drawn by Mermaid
  // inside a sandboxed frame (/diagram-frame, opaque origin); the SVG never enters this page. Any failure leaves the source showing.
  var MM_MAX = 20000, MM_WAIT = 5000, MM_NOLOAD = "Diagram not drawn: the diagram viewer did not load. The source is below.", mmFrames = {}, mmN = 0;
  function mmEl(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; }
  function mmCard(pre) {
    var id = ++mmN, src = pre.textContent, card = mmEl("div", "mm-diagram"), head = mmEl("div", "mm-head"), btns = mmEl("div", "mm-btns");
    var note = mmEl("p", "mm-note muted"), view = mmEl("div", "mm-view"), toggle = mmEl("button", "", "Show source"), copy = mmEl("button", "", "Copy source");
    var drawn = false, showing = false;
    toggle.type = copy.type = "button"; toggle.hidden = true; note.setAttribute("role", "status");
    head.appendChild(mmEl("span", "mm-label", "Diagram · Mermaid")); btns.appendChild(toggle); btns.appendChild(copy); head.appendChild(btns);
    pre.parentNode.insertBefore(card, pre);
    card.appendChild(head); card.appendChild(note); card.appendChild(view); card.appendChild(pre);
    function sync() {
      toggle.hidden = !drawn; view.hidden = showing; pre.hidden = drawn && !showing;
      toggle.textContent = showing ? "Show diagram" : "Show source";
    }
    function say(msg, bad) { note.textContent = msg; note.className = "mm-note" + (bad ? " mm-bad" : " muted"); }
    function fail(msg, bad) { drawn = false; delete mmFrames[id]; view.textContent = ""; say(msg, bad); sync(); }
    toggle.addEventListener("click", function () { showing = !showing; sync(); });
    copy.addEventListener("click", function () {
      if (navigator.clipboard) navigator.clipboard.writeText(src).then(function () { say("Copied"); });
    });
    if (pre.getAttribute("data-diagram") !== "mermaid" || src.length > MM_MAX) return fail("This diagram is too large to draw here (over 20,000 characters). The source is below.");
    say("Drawing diagram…");
    var f = mmEl("iframe", "mm-frame"), scroll = mmEl("div", "mm-scroll"), timer = setTimeout(function () { fail(MM_NOLOAD); }, MM_WAIT);
    f.setAttribute("sandbox", "allow-scripts"); f.title = "Diagram";
    f.setAttribute("aria-label", "Diagram: " + src.split("\n")[0].slice(0, 80) + ". The full source is below the diagram.");
    scroll.tabIndex = 0; scroll.appendChild(f); view.appendChild(scroll);
    mmFrames[id] = {
      win: null,
      msg: function (m) {
        if (m.t === "mm-ready") return f.contentWindow.postMessage({ t: "mm-render", id: id, src: src, width: scroll.clientWidth }, "*");
        clearTimeout(timer);
        if (m.t === "mm-error") fail("Could not draw this diagram. " + String(m.msg || "").slice(0, 200) + " Showing the source instead.", true);
        else if (m.t === "mm-done") {
          f.style.height = Math.max(40, Math.min(+m.h || 0, 480)) + 16 + "px"; f.style.width = Math.max(40, Math.min(+m.w || 0, 4000)) + 16 + "px";
          drawn = true; view.classList.add("mm-on"); say(""); sync();
        } else fail(MM_NOLOAD);
      }
    };
    f.src = "/diagram-frame"; mmFrames[id].win = f.contentWindow;
  }
  window.addEventListener("message", function (e) {
    var m = e.data;
    if (!m || typeof m.t !== "string" || e.origin !== "null") return;
    for (var k in mmFrames) if (mmFrames[k].win === e.source && (m.id === undefined || m.id === +k)) return mmFrames[k].msg(m);
  });
  var mmPres = document.querySelectorAll(".docview pre.mm-src");
  for (var mi = 0; mi < mmPres.length; mi++) mmCard(mmPres[mi]);
  // --- Repository fields (Settings: Projects, and the standalone repositories of General): shared by the two parts below.
  var REPO = /^[\w.-]+\/[\w.-]+$/, GH_ADDRESS = /^(?:(?:https?:\/\/)?(?:www\.)?github\.com\/|git@github\.com:)([^\/\s]+)\/([^\/\s#?]+)/i;
  function slug(v) {
    v = v.trim();
    var m = GH_ADDRESS.exec(v);
    if (m) v = m[1] + "/" + m[2];
    return /\.git$/.test(v) ? v.slice(0, -4) : v;
  }
  // What is wrong with a field, written under it (forms.py _checked writes the same markup); an empty reason takes it away.
  function mark(el, why) {
    var id = el.name + "-err", p = document.getElementById(id);
    if (!why) {
      if (p) p.parentNode.removeChild(p);
      el.removeAttribute("aria-invalid"); el.removeAttribute("aria-describedby");
      return;
    }
    if (!p) { p = document.createElement("p"); p.className = "f-err"; p.id = id; el.parentNode.insertBefore(p, el.nextSibling); }
    if (p.textContent !== why) p.textContent = why;
    el.setAttribute("aria-invalid", "true"); el.setAttribute("aria-describedby", id);
  }

  // --- Settings, Projects: one form per project (New project on top, then a row per saved project). Add repository rows, check each
  // field once it has been filled in, and keep Save off while a field is marked. A name or repository is compared with the saved
  // projects (the other rows' saved values), as the server does. The server makes the same checks (settings.py check_projects) and
  // has the last word; these save the round trip. Keep the two alike.
  (function () {
    var forms = document.querySelectorAll("form.pj-form");
    if (!forms.length) return;
    var NAME;
    try { NAME = new RegExp("^[\\p{L}\\p{M}\\p{N}_ .-]{1,60}$", "u"); } catch (e) { NAME = /^[\w .-]{1,60}$/; }
    function saved(self) {                      // the other saved projects: names and repositories as the page was drawn
      var names = {}, owner = {};
      for (var f = 0; f < forms.length; f++) {
        var was = forms[f].elements.was.value;
        if (forms[f] === self || !was) continue;
        names[was.toLowerCase()] = true;
        var ins = forms[f].querySelectorAll("input[name$=_repo]");
        for (var r = 0; r < ins.length; r++) if (ins[r].defaultValue.trim()) owner[ins[r].defaultValue.trim().toLowerCase()] = was;
      }
      return { names: names, owner: owner };
    }
    function wire(form) {
      var i = form.getAttribute("data-p"), save = form.querySelector(".pj-save"), status = form.querySelector(".pj-status"), seen = {}, all = false;
      function val(name) { var el = form.elements[name]; return el ? el.value.trim() : ""; }
      function check() {                        // field name -> what is wrong with it
        var bad = {}, taken = saved(form), mine = {}, name = val("p" + i + "_name"), rows = form.querySelectorAll("tbody tr"), repos = 0;
        for (var j = 0; j < rows.length; j++) {
          var key = "p" + i + "_r" + j + "_repo", repo = slug(val(key)), role = val("p" + i + "_r" + j + "_role");
          if (!repo) continue;                  // a row without a repository is not saved
          repos++;
          var low = repo.toLowerCase();
          if (!REPO.test(repo)) bad[key] = "Write the repository as owner/name, like your-org/shop-web.";
          else if (mine[low]) bad[key] = "This repository is listed twice.";
          else if (taken.owner[low]) bad[key] = repo + " is in two projects: " + taken.owner[low] + " and " + (name || "this one") + ".";
          mine[low] = true;
          if (role.length > 300) bad["p" + i + "_r" + j + "_role"] = "Keep the role under 300 characters.";
        }
        if (!name) bad["p" + i + "_name"] = "Give the project a name.";
        else if (!NAME.test(name)) bad["p" + i + "_name"] = "Use a short name: letters, digits, spaces, dot or dash, 60 characters at most.";
        else if (taken.names[name.toLowerCase()]) bad["p" + i + "_name"] = "Another project already has this name.";
        if (val("p" + i + "_desc").length > 500) bad["p" + i + "_desc"] = "Keep the description under 500 characters.";
        if (!repos) bad["p" + i + "_r0_repo"] = "Add at least one repository, as owner/name.";
        return bad;
      }
      function show() {
        var bad = check(), els = form.querySelectorAll("input[name^=p]"), shown = 0, firstBad = null;
        for (var x = 0; x < els.length; x++) {
          var why = all || seen[els[x].name] ? bad[els[x].name] : "";
          mark(els[x], why);
          if (why) { shown++; if (!firstBad) firstBad = els[x]; }
        }
        save.disabled = shown > 0;
        status.textContent = shown ? "Correct the " + (shown === 1 ? "field" : shown + " fields") + " marked above to save." : "";
        return firstBad;
      }
      var marked = form.querySelectorAll("[aria-invalid=true]");          // the server refused a save: its marks follow the typing from here on
      for (var k = 0; k < marked.length; k++) seen[marked[k].name] = true;
      if (marked.length) all = true;
      var add = form.querySelector(".pj-add");
      add.hidden = false;
      add.addEventListener("click", function () {
        var body = form.querySelector("tbody"), last = body.lastElementChild, j = body.children.length;
        if (!last || j >= +add.getAttribute("data-max")) return;
        var tr = last.cloneNode(true), ins = tr.querySelectorAll("input"), old = tr.querySelectorAll(".f-err");
        for (var o = 0; o < old.length; o++) old[o].parentNode.removeChild(old[o]);
        for (var x = 0; x < ins.length; x++) {
          var kind = /_role$/.test(ins[x].name) ? "role" : "repo";
          ins[x].name = ins[x].id = "p" + i + "_r" + j + "_" + kind;
          ins[x].value = ins[x].defaultValue = "";
          ins[x].removeAttribute("aria-invalid"); ins[x].removeAttribute("aria-describedby");
          ins[x].setAttribute("aria-label", kind === "role" ? "Role of repository " + (j + 1) : "Repository " + (j + 1));
        }
        body.appendChild(tr);
        if (j + 1 >= +add.getAttribute("data-max")) add.hidden = true;
        ins[0].focus();
      });
      form.addEventListener("change", function (e) {
        var el = e.target;
        if (!el.name || !/^p\d/.test(el.name)) return;
        if (/_repo$/.test(el.name)) el.value = slug(el.value);          // a pasted GitHub address becomes owner/name
        seen[el.name] = true;
        show();
      });
      form.addEventListener("input", function () { show(); });
      form.addEventListener("submit", function (e) {
        var rm = e.submitter && e.submitter.classList.contains("pj-remove");
        if (rm) {                               // removing needs no valid fields, only a yes
          if (!window.confirm("Remove the project “" + e.submitter.getAttribute("data-name") + "”? The factory stops working on its " +
                              "repositories unless they are also listed on their own (General), and its build order is forgotten.")) e.preventDefault();
          return;
        }
        all = true;
        var firstBad = show();
        if (firstBad) { e.preventDefault(); firstBad.focus(); }
      });
      show();
    }
    for (var f = 0; f < forms.length; f++) wire(forms[f]);
    var here = document.querySelector(".pj-item[data-here]");           // the project just saved, or whose build order was looked for
    if (here && !location.hash) here.scrollIntoView({ block: "start" });
  })();
  // --- Workers, Add a worker: the Recipes drop-down shows the chosen recipes while closed, and the form needs at least one.
  (function () {
    var pick = document.querySelector("details.wk-pick");
    if (!pick) return;
    var form = pick.closest("form"), shown = pick.querySelector(".wk-chosen"), boxes = pick.querySelectorAll("input[type=checkbox]");
    function chosen() {
      var t = [];
      for (var i = 0; i < boxes.length; i++) if (boxes[i].checked) t.push(boxes[i].getAttribute("data-title"));
      shown.textContent = t.length ? t.join(", ") : "None chosen";
      pick.querySelector("summary").setAttribute("aria-label", "Recipes: " + shown.textContent);
      boxes[0].setCustomValidity(t.length ? "" : "Choose at least one recipe.");
    }
    pick.addEventListener("change", chosen);
    form.addEventListener("invalid", function (e) { if (e.target.name === "recipes") pick.open = true; }, true);
    document.addEventListener("click", function (e) { if (pick.open && !pick.contains(e.target)) pick.open = false; });
    chosen();
  })();
  // --- Settings, General: the standalone repositories. Add rows, check each one once it has been filled in, and keep Save off while
  // one is marked. The server makes the same checks (settings.py check_repos) and asks GitHub about a new repository when you save;
  // what it refused stays marked until that field is changed. The part remembers in this browser whether you folded it.
  (function () {
    var sec = document.querySelector("details.sr-sec"), form = sec && sec.closest("form");
    if (!form) return;
    var seen = {}, held = {}, FOLD = "sf-repos-folded";
    function rows() { return sec.querySelectorAll("tbody tr"); }
    function check() {
      var bad = {}, had = {}, trs = rows();
      for (var j = 0; j < trs.length; j++) {
        var ins = trs[j].querySelectorAll("input"), repo = slug(ins[0].value), low = repo.toLowerCase();
        if (!repo) continue;                      // a row without a repository is not saved
        if (!REPO.test(repo)) bad[ins[0].name] = "Write the repository as owner/name, like your-org/shop-web.";
        else if (had[low]) bad[ins[0].name] = "This repository is listed twice.";
        had[low] = true;
        if (ins[1].value.trim().length > 300) bad[ins[1].name] = "Keep the description under 300 characters.";
      }
      return bad;
    }
    function show() {
      var bad = check(), els = sec.querySelectorAll("tbody input"), n = 0, first = null;
      for (var i = 0; i < els.length; i++) {
        var el = els[i], h = held[el.name], why = seen[el.name] ? bad[el.name] : "";
        if (h && h.value === el.value) why = h.why;
        mark(el, why);
        if (why) { n++; if (!first) first = el; }
      }
      var listed = 0, trs = rows();
      for (var j = 0; j < trs.length; j++) if (slug(trs[j].querySelector("input").value)) listed++;
      sec.querySelector(".sr-count").textContent = (listed ? listed + (listed === 1 ? " repository" : " repositories") : "none yet") + (n ? " · " + n + " to correct" : "");
      if (n) sec.open = true;
      return first;
    }
    var marked = sec.querySelectorAll("[aria-invalid=true]");
    for (var k = 0; k < marked.length; k++) {
      var p = document.getElementById(marked[k].name + "-err");
      held[marked[k].name] = { value: marked[k].value, why: p ? p.textContent : "" };
      seen[marked[k].name] = true;
    }
    try { if (!marked.length && localStorage.getItem(FOLD) === "1") sec.open = false; } catch (e) { /* private window: stays open */ }
    sec.addEventListener("click", function (e) {          // saved at the click, not in the toggle event, which the browser sends a moment later
      if (e.target.closest("summary") && e.target.closest("summary").parentNode === sec) try { localStorage.setItem(FOLD, sec.open ? "1" : "0"); } catch (x) { /* not remembered */ }
    });                                                   // (the click comes before the part opens or closes, so an open part is about to be folded)
    var add = sec.querySelector(".sr-add");
    add.hidden = false;
    add.addEventListener("click", function () {
      var body = sec.querySelector("tbody"), j = body.children.length;
      if (j >= +add.getAttribute("data-max")) return;
      var tr = body.lastElementChild.cloneNode(true), ins = tr.querySelectorAll("input"), old = tr.querySelectorAll(".f-err");
      for (var o = 0; o < old.length; o++) old[o].parentNode.removeChild(old[o]);
      for (var x = 0; x < ins.length; x++) {
        ins[x].name = ins[x].id = "repo" + j + (x ? "_role" : "");
        ins[x].value = ins[x].defaultValue = "";
        ins[x].removeAttribute("aria-invalid"); ins[x].removeAttribute("aria-describedby");
        ins[x].setAttribute("aria-label", x ? "What repository " + (j + 1) + " is for" : "Repository " + (j + 1));
      }
      body.appendChild(tr);
      if (j + 1 >= +add.getAttribute("data-max")) add.hidden = true;
      ins[0].focus();
    });
    sec.addEventListener("change", function (e) {
      var el = e.target;
      if (!el.name || !/^repo\d/.test(el.name)) return;
      if (/^repo\d+$/.test(el.name)) el.value = slug(el.value);          // a pasted GitHub address becomes owner/name
      seen[el.name] = true;
      show();
    });
    sec.addEventListener("input", function () { show(); });
    form.addEventListener("submit", function (e) {
      var els = sec.querySelectorAll("tbody input");
      for (var i = 0; i < els.length; i++) seen[els[i].name] = true;
      var first = show();
      if (first) { e.preventDefault(); first.focus(); dirty(form); }
    });
    show();
    dirty(form);
  })();
})();
