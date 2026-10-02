// Refreshes the overview fragment. The server renders (and escapes) all HTML; this only swaps it in.
(function () {
  var live = document.getElementById("live");
  if (!live) return;
  // Never replace the page while someone is answering on it: a focused control, or text typed into a field, would be lost.
  function busy() {
    var a = document.activeElement;
    if (a && live.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    var f = live.querySelectorAll("input[type=text], input:not([type]), textarea");
    for (var i = 0; i < f.length; i++) if (f[i].value) return true;
    return false;
  }
  function tick() {
    if (document.hidden || busy()) return;
    fetch("/fragment/overview" + location.search, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { location.href = "/login"; return null; }
        return r.ok ? r.text() : null;
      })
      .then(function (html) { if (html !== null) live.innerHTML = html; })
      .catch(function () {});
  }
  setInterval(tick, 5000);
})();
