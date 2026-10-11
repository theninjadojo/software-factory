// Runs inside the sandboxed /diagram-frame (opaque origin). The parent posts {t:"mm-render", id, src}; this draws it with Mermaid
// (strict, dark) and answers {t:"mm-done", id, h, w} or {t:"mm-error", id, msg}. The SVG never leaves this frame.
(function () {
  var MAX = 20000;
  function post(m) { parent.postMessage(m, "*"); }
  var cur = 0;
  window.addEventListener("unhandledrejection", function (e) { e.preventDefault(); post({ t: "mm-error", id: cur, msg: "Could not draw this diagram." }); });
  if (!window.mermaid) { post({ t: "mm-unavailable" }); return; }
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark", maxTextSize: MAX, flowchart: { htmlLabels: false } });
  window.addEventListener("message", function (e) {
    var m = e.data;
    if (e.source !== parent || !m || m.t !== "mm-render" || typeof m.src !== "string" || m.src.length > MAX || typeof m.id !== "number") return;
    cur = m.id;
    mermaid.render("mm" + m.id, m.src).then(function (r) {
      var d = document.getElementById("d");
      d.innerHTML = r.svg;                                   // our own frame: the library's strict-mode output, in an opaque origin
      var b = d.getBoundingClientRect();
      post({ t: "mm-done", id: m.id, h: Math.ceil(b.height), w: Math.ceil(b.width) });
    }).catch(function (err) {
      var s = String((err && err.message) || err).split("\n")[0].slice(0, 200);
      post({ t: "mm-error", id: m.id, msg: s });
    });
  });
  post({ t: "mm-ready" });
})();
