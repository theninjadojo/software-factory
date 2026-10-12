// Runs inside the sandboxed /diagram-frame (opaque origin). The parent posts {t:"mm-render", id, src}; this draws it with Mermaid
// (strict, dark) and answers {t:"mm-done", id, h, w} or {t:"mm-error", id, msg}. The SVG never leaves this frame.
(function () {
  var MAX = 20000, MIN = 0.6;
  function post(m) { parent.postMessage(m, "*"); }
  var cur = 0;
  window.addEventListener("unhandledrejection", function (e) { e.preventDefault(); post({ t: "mm-error", id: cur, msg: "Could not draw this diagram." }); });
  if (!window.mermaid) { post({ t: "mm-unavailable" }); return; }
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark", maxTextSize: MAX, flowchart: { htmlLabels: false } });
  window.addEventListener("message", function (e) {
    var m = e.data;
    if (e.source !== parent || !m || m.t !== "mm-render" || typeof m.src !== "string" || m.src.length > MAX || typeof m.id !== "number" || (m.width !== undefined && typeof m.width !== "number")) return;
    cur = m.id;
    mermaid.render("mm" + m.id, m.src).then(function (r) {
      var d = document.getElementById("d");
      d.innerHTML = r.svg;                                   // our own frame: the library's strict-mode output, in an opaque origin
      var sv = d.querySelector("svg"), vb = sv ? (sv.getAttribute("viewBox") || "").split(/\s+/).map(Number) : [];
      if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {      // Mermaid writes width="100%" plus a max-width, which shrinks the drawing to nothing in this box
        // Draw it at its own size, scaled down to the card's width but never below MIN (smaller text cannot be read; the card scrolls instead).
        var k = m.width > 0 ? Math.max(MIN, Math.min(1, Math.min(m.width, 4000) / vb[2])) : 1;
        sv.setAttribute("width", Math.ceil(vb[2] * k)); sv.setAttribute("height", Math.ceil(vb[3] * k)); sv.style.maxWidth = "none";
      }
      var b = d.getBoundingClientRect();
      post({ t: "mm-done", id: m.id, h: Math.ceil(b.height), w: Math.ceil(b.width) });
    }).catch(function (err) {
      var s = String((err && err.message) || err).split("\n")[0].slice(0, 200);
      post({ t: "mm-error", id: m.id, msg: s });
    });
  });
  post({ t: "mm-ready" });
})();
