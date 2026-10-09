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

  // --- A canvas of screens (a Playwright run's, a ticket's design): the zoom buttons set how big the screens are (CSS does the rest).
  // The live refresh leaves data-z and the buttons' aria-pressed alone (skipAttr), so the chosen zoom stays.
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-zoom]"), g = b && b.closest("[data-for]"), cv = g && document.getElementById(g.getAttribute("data-for"));
    if (!cv) return;
    cv.setAttribute("data-z", b.getAttribute("data-zoom"));
    var all = g.querySelectorAll("[data-zoom]");
    for (var i = 0; i < all.length; i++) all[i].setAttribute("aria-pressed", all[i] === b ? "true" : "false");
  });

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
      slot.outerHTML = html; countAll(); calm(); tod();
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
      spot.outerHTML = html; countAll(); calm(); tod();
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
    if (name === "data-z" || (name === "aria-pressed" && el.hasAttribute("data-zoom"))) return true;
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
        morph(live, html); countAll(); calm(); tod();
      } })
      .catch(function () {});
  }
  setInterval(tick, 5000);
  function chatTick() {          // the ticket chat or a new-project interview while a reply is awaited: the panel replaces itself (its form is hidden meanwhile)
    var box = document.querySelector(".sd-chat[data-pending=\"1\"], .np-draft[data-pending=\"1\"]");
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
    if (save && !save.dataset.saving) save.disabled = n === 0;
  }
  function onEdit(e) { var f = e.target.closest("form[data-dirty]"); if (f) dirty(f); }
  Array.prototype.forEach.call(document.querySelectorAll("form[data-dirty]"), dirty);
  document.addEventListener("submit", function (e) {
    var b = e.target.matches && e.target.matches("form[data-dirty]") && e.target.querySelector(".s-save button");
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
})();
