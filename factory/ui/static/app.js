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
    var nets = (root || document).querySelectorAll("svg.ld-net");
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
