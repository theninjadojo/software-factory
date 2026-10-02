// Refreshes the overview fragment. The server renders (and escapes) all HTML; this only swaps it in.
(function () {
  var live = document.getElementById("live");
  if (!live) return;
  function tick() {
    if (document.hidden) return;
    fetch("/fragment/overview", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { location.href = "/login"; return null; }
        return r.ok ? r.text() : null;
      })
      .then(function (html) { if (html !== null) live.innerHTML = html; })
      .catch(function () {});
  }
  setInterval(tick, 5000);
})();
