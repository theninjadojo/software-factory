// The floor's layout editor (/floor/edit). The server renders the page, checks every save and draws the floor; this script only shows
// the layout from the form's JSON as a plan on a grid with each building's picture, lets a person start from an empty floor and bring
// buildings in from the parts tray, move and resize them, draw belts (point by point, or by dragging from one building onto the next)
// and slide them, draw walls and trees and place pieces (mouse, pen or touch), pan and zoom, and writes the JSON back into the form. It sets SVG attributes only, never style attributes, so the page policy holds.
(function () {
  var root = document.querySelector(".fe");
  var area = document.querySelector(".fe-save textarea[name=plan]");
  if (!root || !area) return;
  var meta = JSON.parse(root.getAttribute("data-meta"));
  var svg = root.querySelector(".fe-svg"), list = root.querySelector(".fe-problems"), canvas = root.querySelector(".fe-canvas");
  var tray = root.querySelector(".fe-tray-list"), checklist = root.querySelector(".fe-check");
  var FREE = {};
  (meta.free || []).forEach(function (k) { FREE[k] = 1; });
  var RAIL = {};
  (meta.tracks || []).forEach(function (t) { RAIL[t[0] + ">" + t[1]] = 1; });
  var artSrc = root.querySelector(".fe-art"), pics = {}, zoomOut = root.querySelector(".fe-zoomval");
  var hopSel = root.querySelector(".fe-hop"), dirSel = root.querySelector(".fe-dir");
  var G = meta.grid, NS = "http://www.w3.org/2000/svg";
  var doc = parse(area.value) || clone(meta["default"]);
  if (!doc.tracks) doc.tracks = {};
  if (!doc.districts) doc.districts = {};
  var past = [], future = [], tool = "move", drawing = null, drag = null, pan = null, ugFrom = null, focused = null, focusedDist = null;
  var link = null, slide = null, carryIn = null, carried = false, note = "", selTrack = null, selBelt = null;
  var msg = root.querySelector(".fe-msg"), removeBtn = root.querySelector(".fe-remove");
  var zoom = 1, AREAS = {mainland: 1, sea: 1, airfield: 1, outside: 1};
  var GROUND = {mainland: "ap-land", sea: "sh-sea", airfield: "ap-field"};    // what fills a resized area round its picture
  var details = document.querySelector(".fe-data");
  if (details) details.removeAttribute("open");

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function parse(t) {
    try { var d = JSON.parse(t); return d && d.nodes && d.belts && d.pieces ? d : null; } catch (e) { return null; }
  }
  function el(name, attrs, parent, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, String(attrs[k]));
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function label(id) { return meta.nodes[id] ? meta.nodes[id].label : id; }

  // ---- history: every change is one step back
  function remember() { past.push(JSON.stringify(doc)); if (past.length > 200) past.shift(); future = []; }
  function undo() { if (!past.length) return; future.push(JSON.stringify(doc)); doc = JSON.parse(past.pop()); drawing = null; draw(); }
  function redo() { if (!future.length) return; past.push(JSON.stringify(doc)); doc = JSON.parse(future.pop()); drawing = null; draw(); }

  // ---- the same checks the server makes (it has the last word)
  function box(id) {
    var n = doc.nodes[id], m = meta.nodes[id];
    return n && m ? [n.x + m.dx, n.y + m.dy, n.w || m.w, n.h || m.h] : null;
  }
  // a district: its saved box, or the smallest one round its stations (as the server draws it)
  function fit(name) {
    var bs = meta.districts[name].map(box).filter(Boolean);
    if (!bs.length) return null;
    var x0 = Math.min.apply(null, bs.map(function (b) { return b[0]; })) - 1, y0 = Math.min.apply(null, bs.map(function (b) { return b[1]; })) - 2;
    var x1 = Math.max.apply(null, bs.map(function (b) { return b[0] + b[2]; })) + 1, y1 = Math.max.apply(null, bs.map(function (b) { return b[1] + b[3]; })) + 2;
    return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
  }
  function dbox(name) { return doc.districts[name] || fit(name); }
  function grow() {
    // a district always holds its stations: moving one out stretches the box round it
    Object.keys(doc.districts).forEach(function (name) {
      var d = doc.districts[name], f = fit(name);
      if (!f || !meta.districts[name]) { delete doc.districts[name]; return; }
      var x0 = Math.min(d.x, f.x), y0 = Math.min(d.y, f.y), x1 = Math.max(d.x + d.w, f.x + f.w), y1 = Math.max(d.y + d.h, f.y + f.h);
      x0 = Math.max(0, x0); y0 = Math.max(0, y0); x1 = Math.min(meta.w, x1); y1 = Math.min(meta.h, y1);
      doc.districts[name] = {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
    });
  }
  function inside(p, b) { return b[0] <= p[0] && p[0] <= b[0] + b[2] && b[1] <= p[1] && p[1] <= b[1] + b[3]; }
  function beside(p, id) {
    if (id === "yard") { var a = doc.nodes.yard; return !!a && p[0] === a.x && p[1] === a.y - 1; }
    var b = box(id);
    if (!b) return false;
    return (b[0] <= p[0] && p[0] <= b[0] + b[2] && (p[1] === b[1] - 1 || p[1] === b[1] + b[3] + 1)) ||
           (b[1] <= p[1] && p[1] <= b[1] + b[3] && (p[0] === b[0] - 1 || p[0] === b[0] + b[2] + 1));
  }
  function cells(pts) {
    var out = [pts[0]];
    for (var i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i], dx = Math.sign(b[0] - a[0]), dy = Math.sign(b[1] - a[1]);
      var n = Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]);
      for (var k = 1; k <= n; k++) out.push([a[0] + dx * k, a[1] + dy * k]);
    }
    return out;
  }
  // a piece sits on a belt (as the server checks): a splitter, merger or side-load on any belt's line, an underground pair on one run
  function hasCell(pts, c) { return cells(pts).some(function (q) { return q[0] === c[0] && q[1] === c[1]; }); }
  function sameRun(pts, u, v) {
    for (var i = 1; i < pts.length; i++) {
      var line = cells([pts[i - 1], pts[i]]), at = function (c) { for (var k = 0; k < line.length; k++) if (line[k][0] === c[0] && line[k][1] === c[1]) return k; return -1; };
      var a = at(u), b = at(v);
      if (a >= 0 && b > a) return true;
    }
    return false;
  }
  function pieceOk(p, belts) {
    var ids = meta.hops.map(function (h) { return h[0] + ">" + h[1]; }).filter(function (k) { return belts[k] && belts[k].length >= 2; });
    if (p.kind !== "underground") return ids.some(function (k) { return hasCell(belts[k], p.at); });
    var n = Math.abs(p.from[0] - p.to[0]) + Math.abs(p.from[1] - p.to[1]);
    return n >= 2 && n <= meta.under + 1 && ids.some(function (k) { return sameRun(belts[k], p.from, p.to); });
  }
  function carry(from, shifted, dx, dy, ends) {
    // pieces ride with a belt that moved whole, and one at a moved end of a belt (a splitter where belts leave a station) goes with
    // that end; one left off its belt by a move or a resize is taken away
    var mv = function (q) { return [q[0] + dx, q[1] + dy]; }, same = function (a, b) { return a[0] === b[0] && a[1] === b[1]; };
    doc.pieces = from.pieces.map(function (p) {
      if (p.kind !== "underground") {
        for (var i = 0; i < (ends || []).length; i++) {
          var e = ends[i], old = from.belts[e[0]], now = doc.belts[e[0]];
          if (same(p.at, e[1] ? old[old.length - 1] : old[0])) return {kind: p.kind, at: (e[1] ? now[now.length - 1] : now[0]).slice(), dir: p.dir};
        }
      }
      var on = shifted.some(function (k) { return p.kind === "underground" ? sameRun(from.belts[k], p.from, p.to) : hasCell(from.belts[k], p.at); });
      if (!on) return p;
      return p.kind === "underground" ? {kind: p.kind, from: mv(p.from), to: mv(p.to)} : {kind: p.kind, at: mv(p.at), dir: p.dir};
    }).filter(function (p) { return pieceOk(p, doc.belts); });
  }
  function check() {
    var out = [], bad = {}, ids = Object.keys(meta.nodes), i, j;
    for (i = 0; i < ids.length; i++) {
      var a = box(ids[i]);
      if (!a) { if (!meta.nodes[ids[i]].optional) out.push("Missing: " + label(ids[i]) + "."); continue; }
      if (a[0] < 0 || a[1] < 0 || a[0] + a[2] > meta.w || a[1] + a[3] > meta.h) out.push(label(ids[i]) + " is outside the floor.");
      if (FREE[ids[i]]) continue;
      for (j = i + 1; j < ids.length; j++) {
        if (FREE[ids[j]]) continue;
        var b = box(ids[j]);
        if (b && a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3]) out.push(label(ids[i]) + " overlaps " + label(ids[j]) + ".");
      }
    }
    meta.hops.forEach(function (h) {
      var id = h[0] + ">" + h[1], pts = doc.belts[id], name = "The belt from " + label(h[0]) + " to " + label(h[1]);
      if (!pts || pts.length < 2) { out.push("No belt from " + label(h[0]) + " to " + label(h[1]) + ": " + label(h[1]) + " cannot be reached."); bad[id] = 1; return; }
      var msg = !beside(pts[0], h[0]) ? name + " must start beside " + label(h[0]) + "."
              : !beside(pts[pts.length - 1], h[1]) ? name + " must end beside " + label(h[1]) + "." : "";
      if (!msg) {
        var cs = cells(pts);
        for (var k = 0; k < cs.length && !msg; k++)
          for (var n = 0; n < ids.length && !msg; n++) { var bx = !FREE[ids[n]] && box(ids[n]); if (bx && inside(cs[k], bx)) msg = name + " runs through " + label(ids[n]) + "."; }
      }
      if (msg) { out.push(msg); bad[id] = 1; }
    });
    Object.keys(doc.tracks).forEach(function (id) {
      var ab = id.split(">"), pts = doc.tracks[id], name = "The track from " + label(ab[0]) + " to " + label(ab[1]), ba = box(ab[0]), bb = box(ab[1]), msg = "";
      if (!RAIL[id]) msg = "Track cannot run from " + label(ab[0]) + " to " + label(ab[1]) + ".";
      else if (!ba || !bb) msg = name + " needs both on the floor.";
      else if (!near(pts[0], ba)) msg = name + " must start beside " + label(ab[0]) + ".";
      else if (!near(pts[pts.length - 1], bb)) msg = name + " must end beside " + label(ab[1]) + ".";
      else {
        var cs = cells(pts);
        for (var k = 0; k < cs.length && !msg; k++)
          for (var n = 0; n < ids.length && !msg; n++) { var bx = !FREE[ids[n]] && box(ids[n]); if (bx && inside(cs[k], bx)) msg = name + " runs through " + label(ids[n]) + "."; }
      }
      if (msg) { out.push(msg); bad["t:" + id] = 1; }
    });
    var tp = terrainProblems(ids);
    out = out.concat(tp.problems);
    Object.keys(tp.bad).forEach(function (k) { bad[k] = 1; });
    var lp = loops();
    (meta.workers || []).forEach(function (w) {
      if (!lp[w]) out.push("No track loop for " + label(w) + ": lay track from the yard to it, from it to the Train station, and from the Train station back to the yard.");
    });
    doc.pieces.forEach(function (p) {
      if (pieceOk(p, doc.belts)) return;
      out.push(p.kind === "underground" ? "An underground belt at " + p.from[0] + ", " + p.from[1] + " is not on one straight run of a belt, its ends 2 to " +
               (meta.under + 1) + " cells apart." : "The " + p.kind + " at " + p.at[0] + ", " + p.at[1] + " is not on a belt.");
    });
    return {problems: out, bad: bad, loops: lp};
  }
  function near(p, b) {
    return (b[0] <= p[0] && p[0] <= b[0] + b[2] && (p[1] === b[1] - 1 || p[1] === b[1] + b[3] + 1)) ||
           (b[1] <= p[1] && p[1] <= b[1] + b[3] && (p[0] === b[0] - 1 || p[0] === b[0] + b[2] + 1));
  }
  function reach(a, b) {
    // through junctions only, along the track laid (as the server checks a worker's loop)
    var seen = {}, todo = [a];
    seen[a] = 1;
    while (todo.length) {
      var n = todo.pop(), ks = Object.keys(doc.tracks);
      for (var i = 0; i < ks.length; i++) {
        var ab = ks[i].split(">");
        if (ab[0] !== n || !box(ab[1]) || !box(ab[0])) continue;
        if (ab[1] === b) return true;
        if (ab[1].indexOf("junction:") === 0 && !seen[ab[1]]) { seen[ab[1]] = 1; todo.push(ab[1]); }
      }
    }
    return false;
  }
  function loops() {
    var out = {}, home = reach("depot", "yard");
    (meta.workers || []).forEach(function (w) { out[w] = home && reach("yard", w) && reach(w, "depot"); });
    return out;
  }

  // ---- drawing the plan
  function poly(pts) { return pts.map(function (p) { return p[0] * G + "," + p[1] * G; }).join(" "); }
  // a building's picture, cloned once from the page's art and stretched to its box
  function pic(id) {
    if (!(id in pics)) {
      var src = artSrc && artSrc.querySelector('[data-art="' + id + '"]');
      pics[id] = src && src.firstChild ? src.cloneNode(true) : null;
      if (pics[id]) {
        pics[id].removeAttribute("data-art");
        var links = pics[id].querySelectorAll("[href]");
        for (var i = 0; i < links.length; i++) links[i].removeAttribute("href");
      }
    }
    return pics[id];
  }
  function drawNode(id) {
    var b = box(id), m = meta.nodes[id];
    if (!b) return;
    var g = el("g", {"class": "fe-node" + (id === focused ? " sel" : "") + (AREAS[id] ? " area" : ""), "data-node": id, tabindex: 0, role: "button",
                     "aria-label": label(id) + ", at " + doc.nodes[id].x + ", " + doc.nodes[id].y + ", " + b[2] + " by " + b[3]}, svg);
    var art = pic(id);
    if (art) {
      // one scale, centred in the box, as the floor draws it (floorplan.fit_art): the picture keeps its proportions
      var s = Math.min(b[2] / m.w, b[3] / m.h), ox = (b[2] - m.w * s) * G / 2, oy = (b[3] - m.h * s) * G / 2;
      if (GROUND[id] && (b[2] !== m.w || b[3] !== m.h)) el("rect", {"class": GROUND[id] + " fe-pic", x: b[0] * G, y: b[1] * G, width: b[2] * G, height: b[3] * G}, g);
      var w = el("g", {"class": "fe-pic", transform: "translate(" + (b[0] * G + ox) + " " + (b[1] * G + oy) + ") scale(" + s + ")"}, g);
      w.appendChild(art);
    }
    el("rect", {"class": art ? "fe-hit" : "", x: b[0] * G, y: b[1] * G, width: b[2] * G, height: b[3] * G, rx: 3}, g);
    if (!art) el("text", {x: b[0] * G + 8, y: b[1] * G + 18}, g, label(id));
    if (id === "yard") el("circle", {"class": "fe-port", cx: doc.nodes.yard.x * G, cy: doc.nodes.yard.y * G, r: 5}, g);
    if (m.min) el("rect", {"class": "fe-grip", "data-grip": id, x: (b[0] + b[2]) * G - 12, y: (b[1] + b[3]) * G - 12, width: 12, height: 12}, g);
  }
  function setZoom(z, cx, cy) {
    z = Math.max(0.15, Math.min(3, Math.round(z * 100) / 100));
    var r = canvas.getBoundingClientRect();
    if (cx == null) { cx = r.left + canvas.clientWidth / 2; cy = r.top + canvas.clientHeight / 2; }
    var qx = (canvas.scrollLeft + cx - r.left) / zoom, qy = (canvas.scrollTop + cy - r.top) / zoom;
    zoom = z;
    svg.setAttribute("width", meta.w * G * zoom);
    svg.setAttribute("height", meta.h * G * zoom);
    canvas.scrollLeft = qx * zoom - (cx - r.left);
    canvas.scrollTop = qy * zoom - (cy - r.top);
    if (zoomOut) zoomOut.textContent = Math.round(zoom * 100) + "%";
  }
  function fitView() {
    // zoom so that everything placed fits the canvas, and scroll to it
    var xs = [], ys = [];
    Object.keys(meta.nodes).forEach(function (id) { var b = box(id); if (b) { xs.push(b[0], b[0] + b[2]); ys.push(b[1], b[1] + b[3]); } });
    Object.keys(doc.belts).forEach(function (k) { (doc.belts[k] || []).forEach(function (p) { xs.push(p[0]); ys.push(p[1]); }); });
    Object.keys(doc.tracks || {}).forEach(function (k) { (doc.tracks[k] || []).forEach(function (p) { xs.push(p[0]); ys.push(p[1]); }); });
    if (!xs.length) return setZoom(1);
    var x0 = Math.max(0, Math.min.apply(null, xs) - 1), y0 = Math.max(0, Math.min.apply(null, ys) - 1);
    var x1 = Math.max.apply(null, xs) + 1, y1 = Math.max.apply(null, ys) + 1;
    setZoom(Math.min(canvas.clientWidth / ((x1 - x0) * G), canvas.clientHeight / ((y1 - y0) * G), 1.5));
    canvas.scrollLeft = x0 * G * zoom;
    canvas.scrollTop = y0 * G * zoom;
  }
  function draw() {
    if (!doc.districts) doc.districts = {};
    if (!doc.walls) doc.walls = [];
    if (!doc.trees) doc.trees = [];
    if (!doc.tracks) doc.tracks = {};
    area.value = JSON.stringify(doc);
    var res = check();
    while (list.firstChild) list.removeChild(list.firstChild);
    if (note) { var nl = document.createElement("li"); nl.className = "note"; nl.textContent = note; list.appendChild(nl); }
    res.problems.slice(0, 12).forEach(function (p) { var li = document.createElement("li"); li.textContent = p; list.appendChild(li); });
    drawTray();
    drawChecklist(res);
    drawStatus();
    if (!res.problems.length) { var ok = document.createElement("li"); ok.className = "ok"; ok.textContent = "Every station is reachable on its route."; list.appendChild(ok); }
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var W = meta.w * G, H = meta.h * G;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("width", W * zoom);
    svg.setAttribute("height", H * zoom);
    var pat = el("pattern", {id: "fe-grid", width: G, height: G, patternUnits: "userSpaceOnUse"}, el("defs", {}, svg));
    el("path", {d: "M " + G + " 0 L 0 0 0 " + G, "class": "fe-gridline"}, pat);
    el("rect", {width: W, height: H, fill: "url(#fe-grid)", "class": "fe-ground"}, svg);
    Object.keys(meta.nodes).forEach(function (id) { if (AREAS[id]) drawNode(id); });
    var tn = doc.terrain || {};
    el("g", {"class": "fe-terrain under"}, svg).innerHTML = FT.svg(tn, G, W, H, doc.walls, doc.trees, "under");
    Object.keys(meta.districts).forEach(function (name) {
      var d = dbox(name);
      if (!d) return;
      var g = el("g", {"class": "fe-dist d-" + name.toLowerCase() + (name === focusedDist ? " sel" : ""), "data-district": name, tabindex: 0, role: "button",
                       "aria-label": "District " + name + ", at " + d.x + ", " + d.y + ", " + d.w + " by " + d.h}, svg);
      el("rect", {x: d.x * G, y: d.y * G, width: d.w * G, height: d.h * G, rx: 4}, g);
      el("text", {x: d.x * G + 6, y: d.y * G + 14}, g, name);
      el("rect", {"class": "fe-resize", "data-resize": name, x: (d.x + d.w) * G - 10, y: (d.y + d.h) * G - 10, width: 10, height: 10}, g);
    });
    el("g", {"class": "fe-terrain over"}, svg).innerHTML = FT.svg(tn, G, W, H, doc.walls, doc.trees, "over");
    var trackPx = Object.keys(doc.tracks).map(function (id) { return (doc.tracks[id] || []).map(function (q) { return [q[0] * G, q[1] * G]; }); });
    el("g", {"class": "fe-terrain bridge"}, svg).innerHTML = FT.svg(tn, G, W, H, doc.walls, doc.trees, "bridge", trackPx);
    // the railway: a turnaround loop round each rail building, the track, and stubs into the junctions
    Object.keys(meta.nodes).forEach(function (id) {
      var b = box(id);
      if (!b || !meta.nodes[id].rail || id.indexOf("junction:") === 0) return;
      el("rect", {"class": "fe-ring", x: (b[0] - 1) * G, y: (b[1] - 1) * G, width: (b[2] + 2) * G, height: (b[3] + 2) * G, rx: G}, svg);
    });
    Object.keys(doc.tracks).forEach(function (id) {
      var pts = doc.tracks[id];
      if (!pts || pts.length < 2) return;
      var g = el("g", {"class": "fe-track" + (res.bad["t:" + id] ? " bad" : "") + (id === selTrack ? " sel" : ""), "data-track": id}, svg);
      var ab = id.split(">"), all = pts.slice();
      [[ab[0], 0], [ab[1], 1]].forEach(function (e) {
        var b = e[0].indexOf("junction:") === 0 && box(e[0]);
        if (b) { var c = [b[0] + b[2] / 2, b[1] + b[3] / 2]; if (e[1]) all.push(c); else all.unshift(c); }
      });
      var rd = roundD(all.map(function (q) { return [q[0] * G, q[1] * G]; }), CURVE);
      el("path", {d: rd, "class": "fe-bed"}, g);
      el("path", {d: rd, "class": "fe-ties"}, g);
    });
    Object.keys(meta.nodes).forEach(function (id) { if (!AREAS[id]) drawNode(id); });
    if (!Object.keys(doc.nodes).length) {
      var cx = (canvas.scrollLeft + canvas.clientWidth / 2) / zoom, cy = (canvas.scrollTop + canvas.clientHeight / 2) / zoom;
      el("text", {"class": "fe-empty-h", x: cx, y: cy - 10}, svg, "An empty floor: start with Receiving");
      el("text", {"class": "fe-empty", x: cx, y: cy + 16}, svg, "Drag it in from the parts tray, or use Start from the default.");
    }
    Object.keys(doc.belts).forEach(function (id) {
      var pts = doc.belts[id];
      if (!pts || pts.length < 2) return;
      var src = id.split(">")[0];
      var cls = "fe-belt" + (src === "airfield" || src === "harbor" ? " in" : "") + (res.bad[id] ? " bad" : "") + (id === selBelt ? " sel" : "");
      el("polyline", {points: poly(pts), "class": cls, "data-hop": id}, svg);
      el("circle", {"class": "fe-end", cx: pts[0][0] * G, cy: pts[0][1] * G, r: 4}, svg);
      var z = pts[pts.length - 1], y = pts[pts.length - 2], dx = Math.sign(z[0] - y[0]), dy = Math.sign(z[1] - y[1]);
      el("path", {"class": "fe-arrow", d: "M " + (z[0] * G - dx * 8 - dy * 6) + " " + (z[1] * G - dy * 8 - dx * 6) + " L " + z[0] * G + " " + z[1] * G +
                  " L " + (z[0] * G - dx * 8 + dy * 6) + " " + (z[1] * G - dy * 8 + dx * 6)}, svg);
    });
    doc.pieces.forEach(function (p, i) {
      var g = el("g", {"class": "fe-piece " + p.kind, "data-piece": i}, svg);
      if (p.kind === "underground") {
        el("line", {x1: p.from[0] * G, y1: p.from[1] * G, x2: p.to[0] * G, y2: p.to[1] * G}, g);
        [p.from, p.to].forEach(function (q) { el("rect", {x: q[0] * G - 7, y: q[1] * G - 7, width: 14, height: 14, rx: 2}, g); });
      } else {
        var across = p.dir === "e" || p.dir === "w";
        el("rect", {x: p.at[0] * G - (across ? 8 : 18), y: p.at[1] * G - (across ? 18 : 8), width: across ? 16 : 36, height: across ? 36 : 16, rx: 3}, g);
        el("text", {x: p.at[0] * G, y: p.at[1] * G + 4}, g, {splitter: "S", merger: "M", sideload: "L"}[p.kind]);
      }
    });
    // the selected belt or track: a diamond on its longest run, where it can be dragged sideways
    var selPts = selBelt ? doc.belts[selBelt] : selTrack ? doc.tracks[selTrack] : null;
    if (selPts && selPts.length >= 2) {
      var best = 1, bl = -1;
      for (var si = 1; si < selPts.length; si++) { var ln = Math.abs(selPts[si][0] - selPts[si - 1][0]) + Math.abs(selPts[si][1] - selPts[si - 1][1]); if (ln > bl) { bl = ln; best = si; } }
      var mx = (selPts[best][0] + selPts[best - 1][0]) / 2 * G, my = (selPts[best][1] + selPts[best - 1][1]) / 2 * G;
      el("rect", {"class": "fe-handle", x: mx - 6, y: my - 6, width: 12, height: 12, transform: "rotate(45 " + mx + " " + my + ")"}, svg);
    }
    railAnim(res);
    el("g", {"class": "fe-terrain top"}, svg).innerHTML = FT.svg(tn, G, W, H, doc.walls, doc.trees, "top");
    if (showHit) {
      // every hitbox: the buildings and the solid terrain items (what moves has none)
      var hg = el("g", {"class": "fe-hitboxes"}, svg);
      Object.keys(doc.nodes).forEach(function (id) {
        var b = !GROUND_AREAS[id] && box(id);
        if (b) el("rect", {"class": "fe-hitbox", x: b[0] * G, y: b[1] * G, width: b[2] * G, height: b[3] * G}, hg);
      });
      (tn.items || []).forEach(function (it) {
        var fp = FT.footprint(it);
        if (fp) el("rect", {"class": "fe-hitbox" + (FT.PARK[it[0]] ? " park" : ""), x: fp[0], y: fp[1], width: fp[2], height: fp[3]}, hg);
      });
    }
    drawTerrainPreview();
    if (drawing) el("polyline", {points: poly(drawing), "class": "fe-belt drawing"}, svg);
    if (link && link.pts) el("polyline", {points: poly(link.pts), "class": (link.rail ? "fe-track-draw" : "fe-belt drawing") + (link.to && !link.hop ? " bad" : "")}, svg);
    if (carryIn && carryIn.at) {
      var cm = meta.nodes[carryIn.id], cb = spot(carryIn.id, carryIn.at);
      el("rect", {"class": "fe-ghost", x: (cb[0] + cm.dx) * G, y: (cb[1] + cm.dy) * G, width: cm.w * G, height: cm.h * G, rx: 3}, svg);
    }
    if (ugFrom) el("circle", {"class": "fe-end drawing", cx: ugFrom[0] * G, cy: ugFrom[1] * G, r: 6}, svg);
    if (focusedDist && !focused) { var fd = svg.querySelector('[data-district="' + focusedDist + '"]'); if (fd && document.activeElement !== fd && svg.contains(document.activeElement || null)) fd.focus(); }
    if (focused) { var f = svg.querySelector('[data-node="' + focused + '"]'); if (f && document.activeElement !== f && svg.contains(document.activeElement || null)) f.focus(); }
  }

  // ---- terrain (terrain.py; window.FT draws it): scenery, water, ground, fences, gates, roads, hazard zones, light
  var TERRAIN = {tree: "item", pine: "item", bush: "item", rock: "item", pond: "item", lamp: "item", fog: "item", shade: "item", river: "river",
                 "g-grass": "tile", "g-dirt": "tile", "g-sand": "tile", "g-concrete": "tile", "g-water": "tile", "g-snow": "tile", "g-dusting": "tile", "g-half": "tile", "g-drifts": "tile", "g-prints": "tile", "g-packed": "tile", "g-ice": "tile",
                 wall: "line", fence: "line", road: "line", gate: "gate", hazard: "rect",
                 fetch: "item", playground: "item", picnic: "item", bench: "item", dogwalk: "item"};
  Object.keys(FT.PARK).forEach(function (k) { TERRAIN[k] = "item"; });
  var TOWN_SAYS = {};
  Object.keys(window.TOWN_ART || {}).forEach(function (k) {
    TOWN_SAYS[k] = window.TOWN_ART[k].name + (FT.SOLID.indexOf(k) < 0 ? ": click to place one; it wanders to and fro, and has no hitbox." : ": click to place one; it stands on the floor like a building and has a hitbox.");
  });
  Object.keys(FT.SCENES).forEach(function (k) {
    var a = FT.SCENES[k].actor;
    TOWN_SAYS[k] = FT.SCENES[k].name + ": click to place your scene" + (a ? "; it walks to the nearest " + a.target + " and back." : ".");
  });
  var showHit = false;
  var GROUND_AREAS = {mainland: 1, sea: 1, airfield: 1, outside: 1};      // floorplan.GROUND_AREAS: the park may stand in these
  var paint = null;
  function terrain() {
    if (!doc.terrain) doc.terrain = {};
    var t = doc.terrain;
    ["items", "rivers", "fences", "gates", "roads", "hazards"].forEach(function (k) { if (!t[k]) t[k] = []; });
    if (!t.tiles) t.tiles = {};
    return t;
  }
  function tidyTerrain() {
    // empty parts are left out, so a layout without terrain stays as it was
    var t = doc.terrain;
    if (!t) return;
    Object.keys(t).forEach(function (k) { if ((Array.isArray(t[k]) && !t[k].length) || (k === "tiles" && !Object.keys(t[k]).length)) delete t[k]; });
    if (!Object.keys(t).length) delete doc.terrain;
  }
  function pointPx(e) {
    var m = svg.getScreenCTM();
    if (!m) return [0, 0];
    var pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    var q = pt.matrixTransform(m.inverse());
    return [Math.max(0, Math.min(meta.w * G, Math.round(q.x))), Math.max(0, Math.min(meta.h * G, Math.round(q.y)))];
  }
  function tileSets() {
    var t = terrain(), sets = {};
    FT.GROUNDS.forEach(function (k) { sets[k] = {}; FT.cellsOf(t.tiles[k]).forEach(function (c) { sets[k][c[0] + "," + c[1]] = c; }); });
    return sets;
  }
  function storeTiles(sets) {
    var lists = {};
    FT.GROUNDS.forEach(function (k) { lists[k] = Object.keys(sets[k]).map(function (key) { return sets[k][key]; }); });
    terrain().tiles = FT.runs(lists);
  }
  function tileCount(sets) { return FT.GROUNDS.reduce(function (n, k) { return n + Object.keys(sets[k]).length; }, 0); }
  function paintTile(sets, kind, c) {
    var key = c[0] + "," + c[1];
    if (c[0] < 0 || c[1] < 0 || (c[0] + 1) * FT.TILE > meta.w * G || (c[1] + 1) * FT.TILE > meta.h * G) return;
    FT.GROUNDS.forEach(function (k) { delete sets[k][key]; });
    if (kind && tileCount(sets) < 6000) sets[kind][key] = c;
  }
  function blockedBy(it) {
    // hitboxes: what an item would stand on (a building, a park piece, a tree, bush, rock or pond), or null when there is room
    var a = FT.footprint(it), ids = Object.keys(doc.nodes), i;
    if (!a) return null;
    for (i = 0; i < ids.length; i++) {
      var b = !GROUND_AREAS[ids[i]] && box(ids[i]);
      if (b && FT.hits(a, [b[0] * G, b[1] * G, b[2] * G, b[3] * G])) return label(ids[i]);
    }
    var items = terrain().items;
    for (i = 0; i < items.length; i++) {
      var o = FT.footprint(items[i]);
      if (o && FT.hits(a, o)) return FT.PARK[items[i][0]] ? "the " + FT.PARK_NAMES[items[i][0]] : "a " + items[i][0];
    }
    return null;
  }
  function placeItem(kind, p) {
    var t = terrain();
    if (t.items.length >= 600) return null;
    var it = [kind, p[0], p[1], FT.sizeFor(kind, Math.random()), Math.floor(Math.random() * 4294967295)];
    var by = blockedBy(it);
    if (by) { if (paint) paint.blocked = (FT.PARK[kind] ? "The " + FT.PARK_NAMES[kind] : "A " + kind) + " would stand on " + by; return null; }
    t.items.push(it);
    return it;
  }
  function segDist(p, a, b) {
    var dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy, u = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
    return Math.hypot(p[0] - (a[0] + u * dx), p[1] - (a[1] + u * dy));
  }
  function nearLine(p, pts, scale, r) {
    for (var i = 1; i < pts.length; i++) if (segDist(p, [pts[i - 1][0] * scale, pts[i - 1][1] * scale], [pts[i][0] * scale, pts[i][1] * scale]) <= r) return true;
    return false;
  }
  function eraseTerrainAt(p) {
    // whatever terrain is under the pointer, topmost first: an item, a gate, a fence, a wall, a road, a river, a hazard zone, an
    // older tree, then the ground tile
    var t = terrain(), i;
    for (i = t.items.length - 1; i >= 0; i--) {
      var it = t.items[i], r = it[0] === "lamp" ? 14 : (it[0] === "fog" || it[0] === "shade") ? it[3] / 4 : Math.max(10, it[3] / 2), fp = FT.PARK[it[0]] && FT.footprint(it);
      if (fp ? (p[0] >= fp[0] && p[0] <= fp[0] + fp[2] && p[1] >= fp[1] && p[1] <= fp[1] + fp[3]) : Math.hypot(p[0] - it[1], p[1] - it[2]) <= r) { t.items.splice(i, 1); return true; }
    }
    for (i = t.gates.length - 1; i >= 0; i--) if (Math.hypot(p[0] - t.gates[i][0] * G, p[1] - t.gates[i][1] * G) <= 14) { t.gates.splice(i, 1); return true; }
    for (i = t.fences.length - 1; i >= 0; i--) if (nearLine(p, t.fences[i], G, 8)) { t.fences.splice(i, 1); return true; }
    for (i = doc.walls.length - 1; i >= 0; i--) if (nearLine(p, doc.walls[i], G, 9)) { doc.walls.splice(i, 1); return true; }
    for (i = t.roads.length - 1; i >= 0; i--) if (nearLine(p, t.roads[i], G, 10)) { t.roads.splice(i, 1); return true; }
    for (i = t.rivers.length - 1; i >= 0; i--) if (nearLine(p, t.rivers[i], 1, 16)) { t.rivers.splice(i, 1); return true; }
    for (i = t.hazards.length - 1; i >= 0; i--) {
      var h = t.hazards[i];
      if (p[0] >= h[0] * G && p[0] <= (h[0] + h[2]) * G && p[1] >= h[1] * G && p[1] <= (h[1] + h[3]) * G) { t.hazards.splice(i, 1); return true; }
    }
    for (i = doc.trees.length - 1; i >= 0; i--) if (Math.hypot(p[0] - doc.trees[i][0] * G, p[1] - doc.trees[i][1] * G) <= 14) { doc.trees.splice(i, 1); return true; }
    var sets = tileSets(), c = [Math.floor(p[0] / FT.TILE), Math.floor(p[1] / FT.TILE)], key = c[0] + "," + c[1];
    if (FT.GROUNDS.some(function (k) { return sets[k][key]; })) { paintTile(sets, null, c); storeTiles(sets); return true; }
    return false;
  }
  function straight(a, b) {
    // a wall, fence or road locks to one straight run across or down the grid
    return Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1]) ? [b[0], a[1]] : [a[0], b[1]];
  }
  function startTerrain(kind, e) {
    var p = point(e), px = pointPx(e), type = TERRAIN[kind];
    paint = {kind: kind, type: type, id: e.pointerId, before: JSON.stringify(doc), start: p, end: p, last: px, pts: [px]};
    if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
    if (type === "item") {
      var it = placeItem(kind, px);
      paint.lastSize = it ? it[3] : 30;
      note = paint.blocked ? "No room there: " + paint.blocked + ". Buildings, the park and the scenery each have a hitbox; cars, birds and trains pass over them." : "";
    }
    else if (type === "tile") { paint.sets = tileSets(); paint.tile = [Math.floor(px[0] / FT.TILE), Math.floor(px[1] / FT.TILE)]; paintTile(paint.sets, kind.slice(2), paint.tile); storeTiles(paint.sets); }
    else if (type === "gate") {
      var t = terrain(), way = "h";
      t.fences.forEach(function (f) { for (var i = 1; i < f.length; i++) if (f[i][0] === f[i - 1][0] && f[i][0] === p[0] && Math.min(f[i][1], f[i - 1][1]) <= p[1] && p[1] <= Math.max(f[i][1], f[i - 1][1])) way = "v"; });
      if (t.gates.length < 40) t.gates.push([p[0], p[1], way]);
    }
    else if (kind === "erase-terrain") eraseTerrainAt(px);
    draw();
  }
  function moveTerrain(e) {
    var p = point(e), px = pointPx(e);
    if (paint.type === "item") {
      if (Math.hypot(px[0] - paint.last[0], px[1] - paint.last[1]) >= Math.max(24, paint.lastSize * 0.9)) { var it = placeItem(paint.kind, px); if (it) paint.lastSize = it[3]; paint.last = px; }
    } else if (paint.type === "river") {
      if (Math.hypot(px[0] - paint.last[0], px[1] - paint.last[1]) >= 14 && paint.pts.length < 160) { paint.pts.push(px); paint.last = px; }
    } else if (paint.type === "tile") {
      var c = [Math.floor(px[0] / FT.TILE), Math.floor(px[1] / FT.TILE)], a = paint.tile, n = Math.max(Math.abs(c[0] - a[0]), Math.abs(c[1] - a[1]));
      for (var k = 1; k <= n; k++) paintTile(paint.sets, paint.kind.slice(2), [Math.round(a[0] + (c[0] - a[0]) * k / n), Math.round(a[1] + (c[1] - a[1]) * k / n)]);
      paint.tile = c;
      storeTiles(paint.sets);
    } else if (paint.type === "line") paint.end = straight(paint.start, p);
    else if (paint.type === "rect") paint.end = p;
    else if (paint.kind === "erase-terrain") eraseTerrainAt(px);
    draw();
  }
  function endTerrain() {
    var t = terrain(), a = paint.start, b = paint.end;
    if (paint.type === "river" && paint.pts.length >= 2 && t.rivers.length < 12) t.rivers.push(paint.pts);
    if (paint.type === "line" && (a[0] !== b[0] || a[1] !== b[1])) (paint.kind === "wall" ? doc.walls : paint.kind === "fence" ? t.fences : t.roads).push([a.slice(), b.slice()]);
    if (paint.type === "rect" && t.hazards.length < 40) {
      var x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]);
      t.hazards.push([x, y, Math.max(1, Math.abs(b[0] - a[0])), Math.max(1, Math.abs(b[1] - a[1]))]);
    }
    tidyTerrain();
    if (JSON.stringify(doc) !== paint.before) { past.push(paint.before); future = []; }
    paint = null;
    draw();
  }
  function drawTerrainPreview() {
    if (!paint) return;
    var a = paint.start, b = paint.end;
    if (paint.type === "river" && paint.pts.length >= 2) el("g", {"class": "fe-preview"}, svg).innerHTML = FT.svg({rivers: [paint.pts]}, G, meta.w * G, meta.h * G, [], [], "under");
    if (paint.type === "line") {
      var d = "M " + a[0] * G + " " + a[1] * G + " L " + b[0] * G + " " + b[1] * G;
      el("path", {d: d, "class": (paint.kind === "wall" ? "fp-wall" : paint.kind === "fence" ? "fc-rail" : "rd-road") + " fe-preview"}, svg);
    }
    if (paint.type === "rect") {
      var x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]);
      el("rect", {"class": "hz-zone fe-preview", x: x * G, y: y * G, width: Math.max(1, Math.abs(b[0] - a[0])) * G, height: Math.max(1, Math.abs(b[1] - a[1])) * G}, svg);
    }
  }
  function terrainCounts() {
    var t = doc.terrain || {}, objects = (t.items || []).length + (t.rivers || []).length + (t.fences || []).length + (t.roads || []).length +
      (t.gates || []).length + (t.hazards || []).length + doc.walls.length + doc.trees.length, tiles = 0;
    Object.keys(t.tiles || {}).forEach(function (k) { t.tiles[k].forEach(function (r) { tiles += r[2]; }); });
    return objects + " objects, " + tiles + " tiles";
  }
  function terrainProblems(ids) {
    // the same rules the server checks (floorplan.terrain_rules)
    var t = doc.terrain || {}, out = [], bad = {};
    var ponds = (t.items || []).filter(function (it) { return it[0] === "pond"; }), rivers = t.rivers || [], water = {};
    FT.cellsOf((t.tiles || {}).water).forEach(function (c) { water[c[0] + "," + c[1]] = 1; });
    var wet = function (p) {
      return ponds.some(function (it) { return Math.hypot(p[0] - it[1], p[1] - it[2]) <= it[3] / 2; }) || rivers.some(function (r) { return nearLine(p, r, 1, 13); }) ||
        !!water[Math.floor(p[0] / FT.TILE) + "," + Math.floor(p[1] / FT.TILE)];
    };
    var walls = {}, fences = {};
    doc.walls.forEach(function (w) { cells(w).forEach(function (c) { walls[c[0] + "," + c[1]] = 1; }); });
    (t.fences || []).forEach(function (w) { cells(w).forEach(function (c) { fences[c[0] + "," + c[1]] = 1; }); });
    (t.gates || []).forEach(function (g) { delete fences[g[0] + "," + g[1]]; delete fences[(g[2] === "h" ? g[0] + 1 : g[0]) + "," + (g[2] === "h" ? g[1] : g[1] + 1)]; });
    var unders = doc.pieces.filter(function (q) { return q.kind === "underground"; });
    var lines = meta.hops.filter(function (h) { return doc.belts[h[0] + ">" + h[1]]; }).map(function (h) { return ["belt", h[0] + ">" + h[1], doc.belts[h[0] + ">" + h[1]]]; })
      .concat(Object.keys(doc.tracks).map(function (k) { return ["track", k, doc.tracks[k]]; }));
    lines.forEach(function (l) {
      var ab = l[1].split(">"), name = "The " + l[0] + " from " + label(ab[0]) + " to " + label(ab[1]), cs = cells(l[2]);
      if (cs.some(function (c) { return wet([c[0] * G, c[1] * G]); })) { out.push(name + " crosses water: route it round, there is no bridge yet."); bad[(l[0] === "track" ? "t:" : "") + l[1]] = 1; }
      var hit = cs.filter(function (c) { return walls[c[0] + "," + c[1]]; });
      if (hit.length && l[0] === "track") { out.push(name + " runs into a wall."); bad["t:" + l[1]] = 1; }
      else if (hit.length) {
        var hidden = {};
        unders.forEach(function (u) { if (sameRun(l[2], u.from, u.to)) cells([u.from, u.to]).slice(1, -1).forEach(function (c) { hidden[c[0] + "," + c[1]] = 1; }); });
        if (hit.some(function (c) { return !hidden[c[0] + "," + c[1]]; })) { out.push(name + " crosses a wall: take it under with an underground belt."); bad[l[1]] = 1; }
      }
      if (l[0] === "track" && cs.some(function (c) { return fences[c[0] + "," + c[1]]; })) { out.push(name + " runs into a fence: put a gate where it crosses."); bad["t:" + l[1]] = 1; }
    });
    // hitboxes (floorplan.park_rules): a park piece stands clear of the buildings, the other park pieces and the solid scenery
    var items = t.items || [];
    items.forEach(function (it, i) {
      if (!FT.PARK[it[0]]) return;
      var a = FT.footprint(it), nm = "The " + FT.PARK_NAMES[it[0]];
      Object.keys(doc.nodes).forEach(function (id) {
        var b = !GROUND_AREAS[id] && box(id);
        if (b && FT.hits(a, [b[0] * G, b[1] * G, b[2] * G, b[3] * G])) out.push(nm + " overlaps " + label(id) + ".");
      });
      items.forEach(function (o, j) {
        if (j === i || FT.SOLID.indexOf(o[0]) < 0 || (FT.PARK[o[0]] && j < i)) return;
        if (FT.hits(a, FT.footprint(o))) out.push(nm + " overlaps " + (FT.PARK[o[0]] ? "the " + FT.PARK_NAMES[o[0]] : "a " + o[0]) + ".");
      });
    });
    (t.hazards || []).forEach(function (h) {
      ids.forEach(function (id) {
        var b = !FREE[id] && box(id);
        if (b && b[0] < h[0] + h[2] && h[0] < b[0] + b[2] && b[1] < h[1] + h[3] && h[1] < b[1] + b[3]) out.push(label(id) + " stands in a hazard zone.");
      });
    });
    return {problems: out, bad: bad};
  }

  // ---- the status line over the canvas: what the tool does, what is selected, why something was refused
  var TOOL_SAYS = {
    move: "Move: drag a building to move it, or a belt or track to slide it. Select a building and drag a corner to resize it. Drag the ground to pan.",
    belt: "Draw belt: drag from the building a ticket leaves onto the one it goes to.",
    rail: "Draw track: the yard to a worker, the worker to the Train station, the Train station back to the yard. Run tracks into a junction to join them; its signals take turns.",
    erase: "Erase: click a building to put it back in the tray, or a belt, track, piece, wall or tree to remove it.",
    wall: "Wall: drag a straight run on the grid. Belts cross a wall only with an underground belt; track never does.",
    tree: "Tree: click or drag to plant round trees; each rolls its own size and green.", pine: "Pine: click or drag to plant pines.",
    bush: "Bush: click or drag to plant bushes.", rock: "Rock: click or drag to place rocks.",
    pond: "Pond: click to dig one; ducks move in. Belts and track cannot cross water (there is no bridge yet).",
    river: "River: drag where it runs; it widens and narrows as it goes. Belts and track cannot cross it.",
    "g-grass": "Grass: drag to paint ground tiles.", "g-dirt": "Dirt: drag to paint ground tiles (it suits roads).", "g-sand": "Sand: drag to paint ground tiles.",
    "g-snow": "Snow: drag to paint a permanent winter.", "g-dusting": "Dusting: grass with a few patches of snow.", "g-half": "Half covered: grass half under snow.",
    "g-drifts": "Deep drifts: snow with drifts across it.", "g-prints": "Footprints: snow with a trail of prints.", "g-packed": "Packed road: what a road becomes once cars have driven over snow.",
    "g-ice": "Ice: drag to lay ice, over water too.",
    "g-concrete": "Concrete: drag to paint ground tiles (it suits the factory pad).", "g-water": "Water: drag to paint water; it rounds into one body with a sandy shore, and ducks move in.",
    fence: "Fence: drag a straight run. Crates pass it; trains need a gate.", gate: "Gate: click a gap in a fence; it swings open and shut.",
    road: "Road: drag a straight run on the grid. Cars drive on it both ways; where track crosses it, a bridge carries the trains over.",
    fetch: "Fetch: click to place a person playing fetch with a dog.", playground: "Playground: click to place swings, a slide and a sandbox, with kids.",
    picnic: "Picnic: click to place a picnic on a blanket.", bench: "Bench: click to place a bench; its old man walks to the nearest water to feed the ducks.",
    dogwalk: "Dog walker: click to place someone walking a dog round a footpath.", hazard: "Hazard zone: drag a rectangle; nothing may be built in it.",
    lamp: "Lamp: click to place a warm light.", fog: "Fog: click to let a mist drift.", shade: "Shade: click to put a dark patch under trees or in a corner.",
    underground: "Underground: click where the belt goes under, then where it comes up.",
    splitter: "Splitter: click a belt to place one.", merger: "Merger: click a belt to place one.", sideload: "Side-load: click a belt to place one."};
  function drawStatus() {
    if (!msg) return;
    var text = note || ((TOOL_SAYS[tool] || TOWN_SAYS[tool] || "") + (TERRAIN[tool] || tool === "erase" ? " " + terrainCounts() + "." : "")), rm = "";
    if (!note && selBelt && doc.belts[selBelt]) {
      var ab = selBelt.split(">");
      text = "Belt " + label(ab[0]) + " → " + label(ab[1]) + ". Drag it sideways to slide it; it stays joined to both buildings.";
      rm = "Remove belt";
    } else if (!note && selTrack && doc.tracks[selTrack]) {
      var tb = selTrack.split(">");
      text = "Track " + label(tb[0]) + " → " + label(tb[1]) + ". Drag it sideways to slide it; it stays joined to both.";
      rm = "Remove track";
    } else if (!note && focused && doc.nodes[focused]) {
      if (focused.indexOf("notify:") === 0) text = label(focused) + " is wireless: it hears every station wherever it stands.";
      else {
        var ins = [], outs = [];
        meta.hops.forEach(function (h) { if (doc.belts[h[0] + ">" + h[1]]) { if (h[1] === focused) ins.push(label(h[0])); if (h[0] === focused) outs.push(label(h[1])); } });
        text = label(focused) + " · in from " + (ins.join(", ") || "nothing yet") + " · out to " + (outs.join(", ") || "nothing yet") + ".";
      }
      rm = "Back to the tray";
    }
    msg.textContent = text;
    msg.className = "fe-msg" + (note ? " note" : "");
    if (removeBtn) { removeBtn.hidden = !rm; removeBtn.textContent = rm || "Remove"; }
  }
  if (removeBtn) removeBtn.addEventListener("click", function () {
    if (selBelt && doc.belts[selBelt]) { remember(); delete doc.belts[selBelt]; doc.pieces = doc.pieces.filter(function (q) { return pieceOk(q, doc.belts); }); selBelt = null; draw(); }
    else if (selTrack && doc.tracks[selTrack]) { remember(); delete doc.tracks[selTrack]; selTrack = null; draw(); }
    else if (focused && doc.nodes[focused]) erase(focused);
  });

  // ---- the trains in the editor, on the same timetable as the floor (plant.timetable): one train at a time on shared track
  var SPEED = 150, DWELL = 3, HOME_DWELL = 2.5, GAP = 31, CURVE = 40, PLAT0 = 40, PLAT_GAP = 60, PARK = 50;
  function roundD(pts, r) {
    // a line through the points with wide rounded corners, as the floor draws track (plant.trace)
    var q = pts.filter(function (p, i) { return !i || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]; });
    if (q.length < 2) return "";
    var d = "M " + q[0][0] + " " + q[0][1];
    for (var i = 1; i < q.length - 1; i++) {
      var a = q[i - 1], b = q[i], c = q[i + 1], la = Math.hypot(b[0] - a[0], b[1] - a[1]), lc = Math.hypot(c[0] - b[0], c[1] - b[1]);
      var cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
      if (!la || !lc || Math.abs(cr) < 1e-9) { d += " L " + b[0] + " " + b[1]; continue; }
      var rr = Math.min(r, la / 2, lc / 2), p1 = [b[0] - (b[0] - a[0]) / la * rr, b[1] - (b[1] - a[1]) / la * rr], p2 = [b[0] + (c[0] - b[0]) / lc * rr, b[1] + (c[1] - b[1]) / lc * rr];
      d += " L " + p1[0] + " " + p1[1] + " A " + rr + " " + rr + " 0 0 " + (cr > 0 ? 1 : 0) + " " + p2[0] + " " + p2[1];
    }
    return d + " L " + q[q.length - 1][0] + " " + q[q.length - 1][1];
  }
  function legOf(a, b) {
    var prev = {}, todo = [a], ks = Object.keys(doc.tracks);
    prev[a] = "";
    while (todo.length) {
      var n = todo.shift();
      for (var i = 0; i < ks.length; i++) {
        var ab = ks[i].split(">");
        if (ab[0] !== n || ab[1] in prev || !box(ab[1])) continue;
        prev[ab[1]] = ks[i];
        if (ab[1] === b) { var out = [], c = b; while (prev[c]) { out.unshift(prev[c]); c = prev[c].split(">")[0]; } return out; }
        if (ab[1].indexOf("junction:") === 0) todo.push(ab[1]);
      }
    }
    return null;
  }
  function ringRoute(id, p, q) {
    var b = box(id), L = (b[0] - 1) * G, T = (b[1] - 1) * G, R = (b[0] + b[2] + 1) * G, B = (b[1] + b[3] + 1) * G, Wd = R - L, Ht = B - T, per = 2 * (Wd + Ht);
    var t = function (pt) {
      if (Math.abs(pt[1] - T) < 1) return pt[0] - L;
      if (Math.abs(pt[0] - R) < 1) return Wd + (pt[1] - T);
      if (Math.abs(pt[1] - B) < 1) return Wd + Ht + (R - pt[0]);
      return 2 * Wd + Ht + (B - pt[1]);
    };
    var ta = t(p), span = ((t(q) - ta) % per + per) % per || per, hits = [];
    [[Wd, [R, T]], [Wd + Ht, [R, B]], [2 * Wd + Ht, [L, B]], [per, [L, T]]].forEach(function (c) {
      var d = ((c[0] - ta) % per + per) % per;
      if (d > 0 && d < span) hits.push([d, c[1]]);
    });
    return hits.sort(function (x, y) { return x[0] - y[0]; }).map(function (h) { return h[1]; });
  }
  function circuitOf(w) {
    var legs = [legOf("yard", w), legOf(w, "depot"), legOf("depot", "yard")];
    if (!legs[0] || !legs[1] || !legs[2] || !legs[0].length || !legs[1].length || !legs[2].length) return null;
    var pts = [], run = 0, marks = {tracks: {}}, stops = {};
    var add = function (q) { if (pts.length) { var l = pts[pts.length - 1]; if (l[0] === q[0] && l[1] === q[1]) return; run += Math.hypot(q[0] - l[0], q[1] - l[1]); } pts.push(q); };
    var px = function (q) { return [q[0] * G, q[1] * G]; };
    // it starts and ends with the train's head at its platform in the yard (plant.circuit_marks): west onto the yard's loop and out
    var yb = box("yard"), py = yb[1] * G + PLAT0 + PLAT_GAP * meta.workers.indexOf(w), park = [yb[0] * G + PARK, py], edge = [(yb[0] - 1) * G, py];
    add(park); add(edge);
    ringRoute("yard", edge, px(doc.tracks[legs[0][0]][0])).forEach(add);
    var rings = [w, "depot", "yard"];
    legs.forEach(function (edges, k) {
      edges.forEach(function (tid, i) {
        if (i) { var jb = box(tid.split(">")[0]); add([(jb[0] + jb[2] / 2) * G, (jb[1] + jb[3] / 2) * G]); }
        var q = doc.tracks[tid];
        add(px(q[0]));
        var start = run;
        for (var j = 1; j < q.length; j++) add(px(q[j]));
        marks.tracks[tid] = [start, run];
      });
      var next = k < 2 ? px(doc.tracks[legs[k + 1][0]][0]) : edge, before = run;
      ringRoute(rings[k], pts[pts.length - 1], next).forEach(add);
      add(next);
      stops[rings[k]] = (before + run) / 2;
    });
    add(park);
    marks.stop = stops[w]; marks.off = stops.depot; marks.len = run;
    return {pts: pts, m: marks, legs: legs};
  }
  function railAnim(res) {
    var ws = (meta.workers || []).filter(function (w) { return res.loops[w]; });
    if (!ws.length) return;
    var cs = {}, uses = {};
    ws.forEach(function (w) { cs[w] = circuitOf(w); if (cs[w]) Object.keys(cs[w].m.tracks).forEach(function (t) { uses[t] = (uses[t] || 0) + 1; }); });
    ws = ws.filter(function (w) { return cs[w]; });
    if (!ws.length) return;
    var shared = {}, plan = {}, free = 0, t0 = 0, tail = 3 * GAP / SPEED + 0.6;
    Object.keys(uses).forEach(function (k) { if (uses[k] > 1) shared[k] = 1; });
    ws.forEach(function (w) {
      var c = cs[w], m = c.m, outEnd = 0, home = m.off, prev = null;
      c.legs[0].forEach(function (tid) { if (shared[tid]) outEnd = Math.max(outEnd, m.tracks[tid][1]); });
      var back = c.legs[1].concat(c.legs[2]);
      for (var i = 0; i < back.length; i++) {
        var tid = back[i];
        if (shared[tid] && m.tracks[tid][0] > m.stop) {
          if (prev && prev.split(">")[1].indexOf("junction:") === 0) { var se = m.tracks[prev]; home = se[1] - Math.min(26, (se[1] - se[0]) / 2); }
          else home = m.tracks[tid][0];
          break;
        }
        prev = tid;
      }
      var r = plan[w] = {homeAt: home};
      r.depart = Math.max(t0, free); r.arrive = r.depart + m.stop / SPEED; r.leave = r.arrive + DWELL;
      r.signal = r.leave + (home - m.stop) / SPEED;
      free = r.depart + outEnd / SPEED + tail; t0 = r.depart;
    });
    ws.slice().sort(function (a, b) { return plan[a].signal - plan[b].signal; }).forEach(function (w) {
      var r = plan[w], m = cs[w].m;
      r.go = Math.max(r.signal, free); r.atOff = r.go + (m.off - r.homeAt) / SPEED; r.offLeave = r.atOff + DWELL;
      r.home = r.offLeave + (m.len - m.off) / SPEED; free = r.home + tail;
    });
    var T = Math.round((Math.max.apply(null, ws.map(function (w) { return plan[w].home; })) + HOME_DWELL) * 10) / 10;
    var g = el("g", {"class": "fe-trains"}, svg);
    ws.forEach(function (w) {
      var r = plan[w], m = cs[w].m;
      var pts = [[0, 0], [r.depart, 0], [r.arrive, m.stop], [r.leave, m.stop], [r.signal, r.homeAt], [r.go, r.homeAt], [r.atOff, m.off], [r.offLeave, m.off], [r.home, m.len], [T, m.len]];
      r.pts = pts;
      var d = roundD(cs[w].pts, CURVE), probe = el("path", {d: d}, svg), len = probe.getTotalLength() || m.len, f = len / m.len;
      svg.removeChild(probe);
      var twice = d + " L" + d.slice(1), cars = [3, 4, 3, 2, 3][meta.workers.indexOf(w) % 5];
      for (var c = 0; c < cars; c++) {
        // each car runs the line on its own a fixed distance behind the one ahead, so the train bends round every curve (yard._train)
        var tr = el("g", {"class": "fe-train"}, g), ktc = [], kpc = [];
        pts.forEach(function (q) { var k = Math.round(q[0] / T * 10000) / 10000; if (ktc.length && k <= ktc[ktc.length - 1]) return; ktc.push(k); kpc.push(Math.round((len + q[1] * f - c * GAP) / (2 * len) * 100000) / 100000); });
        ktc[0] = 0; ktc[ktc.length - 1] = 1;
        if (c === 0) { el("rect", {"class": "fm-loco", x: -15, y: -9, width: 30, height: 18, rx: 4}, tr); el("circle", {"class": "fm-head", cx: 12, cy: 0, r: 2.4}, tr); }
        else { el("rect", {"class": "fm-wagon", x: -13, y: -9, width: 26, height: 18, rx: 2}, tr); el("rect", {"class": "fm-load", x: -9, y: -5, width: 18, height: 10}, tr); }
        el("animateMotion", {path: twice, dur: T + "s", rotate: "auto", calcMode: "linear", keyTimes: ktc.join(";"), keyPoints: kpc.join(";"), repeatCount: "indefinite"}, tr);
      }
    });
    // a signal on every track into a junction, green as it lets its train through
    Object.keys(doc.tracks).forEach(function (tid) {
      var dst = tid.split(">")[1];
      if (dst.indexOf("junction:") !== 0 || !box(dst)) return;
      var q = doc.tracks[tid], a = q[q.length - 2], z = q[q.length - 1], d = Math.hypot(z[0] - a[0], z[1] - a[1]) * G || 1;
      var ux = (z[0] - a[0]) * G / d, uy = (z[1] - a[1]) * G / d, back = Math.min(26, d / 2);
      var sx = z[0] * G - ux * back - uy * 14, sy = z[1] * G - uy * back + ux * 14, times = [];
      ws.forEach(function (w) {
        var m = cs[w].m, r = plan[w];
        if (!m.tracks[tid]) return;
        var at = m.tracks[tid][1] - back;
        for (var i = 1; i < r.pts.length; i++) { var p0 = r.pts[i - 1], p1 = r.pts[i]; if (p1[1] > p0[1] && p0[1] <= at && at <= p1[1]) { times.push(p0[0] + (p1[0] - p0[0]) * (at - p0[1]) / (p1[1] - p0[1])); break; } }
      });
      var s = el("g", {"class": "fm-sig", transform: "translate(" + sx + " " + sy + ")"}, g);
      el("rect", {x: -4.5, y: -8.5, width: 9, height: 17, rx: 2}, s);
      var red = el("circle", {"class": "r", cx: 0, cy: -4, r: 2.4}, s), green = el("circle", {"class": "g", cx: 0, cy: 4, r: 2.4, opacity: 0.15}, s);
      if (!times.length) return;
      var keys = [0], gv = [0.15];
      times.sort(function (x, y) { return x - y; }).forEach(function (tm) {
        var a1 = Math.max(0, tm - 0.5) / T, b1 = Math.min(T, tm + 0.9) / T;
        if (a1 <= keys[keys.length - 1]) gv[gv.length - 1] = 1; else { keys.push(a1); gv.push(1); }
        if (b1 < 1) { keys.push(b1); gv.push(0.15); }
      });
      var ks = keys.map(function (k) { return k.toFixed(4); }).join(";");
      el("animate", {attributeName: "opacity", values: gv.join(";"), keyTimes: ks, calcMode: "discrete", dur: T + "s", repeatCount: "indefinite"}, green);
      el("animate", {attributeName: "opacity", values: gv.map(function (v) { return v === 1 ? 0.15 : 1; }).join(";"), keyTimes: ks, calcMode: "discrete", dur: T + "s", repeatCount: "indefinite"}, red);
    });
  }

  // ---- the parts tray: every building of this factory, the ones not on the floor ready to be dragged in (or clicked in)
  var GROUPS = ["Arrivals", "Stations", "Notifiers", "Power", "Workers and rail", "Areas"];
  function drawChecklist(res) {
    // every hop of the route with its belt, and every worker's train loop: what is laid and what is still missing
    if (!checklist) return;
    while (checklist.firstChild) checklist.removeChild(checklist.firstChild);
    var items = meta.hops.map(function (h) {
      var id = h[0] + ">" + h[1];
      return {text: h[2], ok: !!(doc.belts[id] && doc.belts[id].length >= 2 && !res.bad[id] && box(h[0]) && box(h[1]))};
    }).concat((meta.workers || []).map(function (w) { return {text: "Train loop: the yard → " + label(w) + " → the Train station", ok: !!res.loops[w]}; }));
    var done = items.filter(function (i) { return i.ok; }).length;
    var h = document.createElement("h2");
    h.textContent = "Every hop needs a belt" + ((meta.workers || []).length ? " or track" : "") + " ";
    var n = document.createElement("span");
    n.className = done === items.length ? "fe-count ok" : "fe-count";
    n.textContent = done + " of " + items.length + " laid";
    h.appendChild(n);
    checklist.appendChild(h);
    var ul = document.createElement("ul");
    items.forEach(function (i) {
      var li = document.createElement("li");
      li.className = i.ok ? "ok" : "miss";
      li.textContent = (i.ok ? "✓ " : "✗ ") + i.text;
      ul.appendChild(li);
    });
    checklist.appendChild(ul);
  }
  function drawTray() {
    if (!tray) return;
    while (tray.firstChild) tray.removeChild(tray.firstChild);
    var left = 0;
    GROUPS.forEach(function (g) {
      var ids = Object.keys(meta.nodes).filter(function (id) { return meta.nodes[id].group === g; });
      if (!ids.length) return;
      var h = document.createElement("h3");
      h.textContent = g;
      tray.appendChild(h);
      ids.forEach(function (id) {
        var on = !!doc.nodes[id], b = document.createElement("button");
        b.type = "button";
        b.className = "fe-part" + (on ? " placed" : "") + (meta.nodes[id].group === "Notifiers" ? " radio" : "");
        b.setAttribute("data-part", id);
        b.setAttribute("aria-label", on ? label(id) + ", on the floor" : "Place " + label(id));
        if (on) b.setAttribute("aria-disabled", "true"); else if (!meta.nodes[id].optional) left++;
        var ic = el("svg", {viewBox: "0 0 24 24", "class": "fe-part-ico", "aria-hidden": "true"}, b);
        el("path", {d: meta.nodes[id].icon || ""}, ic);
        var nm = document.createElement("span");
        nm.textContent = label(id);
        b.appendChild(nm);
        if (on) { var t = document.createElement("small"); t.textContent = "On floor"; b.appendChild(t); }
        else if (meta.nodes[id].optional) { var o = document.createElement("small"); o.className = "opt"; o.textContent = "Optional"; b.appendChild(o); }
        tray.appendChild(b);
      });
      if (g === "Notifiers") {
        var later = document.createElement("p");
        later.className = "fe-part later";
        later.textContent = "More channels later";
        tray.appendChild(later);
      }
    });
    var head = root.querySelector(".fe-tray-left");
    if (head) head.textContent = left ? left + (left === 1 ? " part left" : " parts left") : "All placed";
    var cnt = root.querySelector('[data-cat="Buildings"] .fe-cat-n');
    if (cnt) cnt.textContent = "· " + root.querySelectorAll(".fe-tray-list [data-part]").length;
    applyFilter();
  }
  // ---- the build panel's categories (collapsible, remembered) and its filter
  var panel = root.querySelector(".fe-tray"), filter = root.querySelector(".fe-filter"), COLL = {};
  try { COLL = JSON.parse(localStorage.getItem("fe-cats-collapsed") || "{}") || {}; } catch (x) { COLL = {}; }
  function applyFilter() {
    if (!panel) return;
    var q = filter ? filter.value.trim().toLowerCase() : "", total = 0;
    var secs = panel.querySelectorAll(".fe-cat");
    for (var i = 0; i < secs.length; i++) {
      var tg = secs[i].querySelector(".fe-cat-toggle"), body = secs[i].querySelector(".fe-cat-body"), hits = 0, head = null, headHits = 0;
      var kids = body.querySelectorAll("[data-tool], [data-part], h3, .later");
      for (var j = 0; j <= kids.length; j++) {
        var k = kids[j];
        if (!k || k.tagName === "H3") {                                // a heading inside the parts list shows only if one of its parts does
          if (head) head.hidden = !!q && !headHits;
          head = k; headHits = 0;
          if (!k) break;
          continue;
        }
        var ok = !q || k.textContent.toLowerCase().indexOf(q) >= 0 && !k.classList.contains("later");
        k.hidden = !ok;
        if (ok) { hits++; headHits++; }
      }
      total += q ? hits : 0;
      var coll = secs[i].getAttribute("data-cat") in COLL ? COLL[secs[i].getAttribute("data-cat")] : secs[i].getAttribute("data-closed") === "1";
      tg.setAttribute("aria-expanded", coll ? "false" : "true");
      body.hidden = !q && coll;                                      // a filter opens every category that has a match
      secs[i].hidden = !!q && !hits;
    }
    var none = panel.querySelector(".fe-none"), found = panel.querySelector(".fe-found");
    if (none) { none.hidden = !(q && !total); var qq = none.querySelector(".fe-q"); if (qq) qq.textContent = "“" + filter.value.trim() + "”"; }
    if (found) found.textContent = q ? total + (total === 1 ? " match" : " matches") : "";
  }
  if (panel) {
    panel.addEventListener("click", function (e) {
      var t = e.target.closest && e.target.closest(".fe-cat-toggle");
      if (t) {
        var sec = t.closest(".fe-cat"), name = sec.getAttribute("data-cat");
        COLL[name] = t.getAttribute("aria-expanded") === "true";
        try { localStorage.setItem("fe-cats-collapsed", JSON.stringify(COLL)); } catch (x) { /* private mode: not remembered */ }
        return applyFilter();
      }
      if (e.target.closest && e.target.closest(".fe-clear")) { filter.value = ""; applyFilter(); filter.focus(); }
    });
    if (filter) {
      filter.addEventListener("input", applyFilter);
      filter.addEventListener("keydown", function (e) { if (e.key === "Escape" && filter.value) { e.stopPropagation(); filter.value = ""; applyFilter(); } });
    }
  }
  function spot(id, p) {
    // where a building dropped at grid point p stands: centred on it, inside the floor
    var m = meta.nodes[id], x = p[0] - Math.floor(m.w / 2) - m.dx, y = p[1] - Math.floor(m.h / 2) - m.dy;
    return [Math.max(-m.dx, Math.min(meta.w - m.w - m.dx, x)), Math.max(-m.dy, Math.min(meta.h - m.h - m.dy, y))];
  }
  function freeSpot(id) {
    // the nearest place to the middle of the view where the building touches nothing (a cell of room all round)
    var m = meta.nodes[id], c = [Math.round((canvas.scrollLeft + canvas.clientWidth / 2) / zoom / G), Math.round((canvas.scrollTop + canvas.clientHeight / 2) / zoom / G)];
    var taken = Object.keys(doc.nodes).map(box).filter(Boolean);
    for (var r = 0; r < 80; r++) {
      for (var dx = -r; dx <= r; dx++) {
        for (var dy = -r; dy <= r; dy++) {
          if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
          var at = spot(id, [c[0] + dx, c[1] + dy]), b = [at[0] + m.dx, at[1] + m.dy, m.w, m.h];
          if (taken.every(function (t) { return !(b[0] - 1 < t[0] + t[2] && t[0] < b[0] + b[2] + 1 && b[1] - 1 < t[1] + t[3] && t[1] < b[1] + b[3] + 1); })) return at;
        }
      }
    }
    return spot(id, c);
  }
  function place(id, at) {
    if (doc.nodes[id]) return;
    remember();
    doc.nodes[id] = {x: at[0], y: at[1]};
    focused = id; focusedDist = null; note = "";
    grow();
    draw();
  }
  function emptyFloor() {
    return {version: meta["default"].version, grid: G, nodes: {}, belts: {}, tracks: {}, pieces: [], districts: {}, walls: [], trees: []};
  }
  function erase(id) {
    // a building goes back to the tray, with the belts that reach it
    remember();
    delete doc.nodes[id];
    meta.hops.forEach(function (h) { if (h[0] === id || h[1] === id) delete doc.belts[h[0] + ">" + h[1]]; });
    Object.keys(doc.tracks).forEach(function (k) { if (k.split(">").indexOf(id) >= 0) delete doc.tracks[k]; });
    doc.pieces = doc.pieces.filter(function (p) { return pieceOk(p, doc.belts); });
    grow();
    if (focused === id) focused = null;
    draw();
  }
  function nodeAt(p) {
    // the building under a grid point (a building over an area)
    var hit = null;
    Object.keys(doc.nodes).forEach(function (id) { var b = box(id); if (b && inside(p, b) && (!hit || AREAS[hit])) hit = id; });
    return hit;
  }
  function ports(id) {
    // the grid points beside a building where a belt may start or end: the middle of each side (the yard: the top of its feeder)
    if (id === "yard") return [[doc.nodes.yard.x, doc.nodes.yard.y - 1]];
    var b = box(id), mx = b[0] + Math.floor(b[2] / 2), my = b[1] + Math.floor(b[3] / 2);
    return [[b[0] + b[2] + 1, my], [b[0] - 1, my], [mx, b[1] - 1], [mx, b[1] + b[3] + 1]];
  }
  function trackPorts(id) {
    // track leaves or reaches a rail building in the middle of a side (never the yard's top, where its feeder comes in; a worker
    // only at its left and right)
    var b = box(id), mx = b[0] + Math.floor(b[2] / 2), my = b[1] + Math.floor(b[3] / 2);
    var out = [[b[0] + b[2] + 1, my], [b[0] - 1, my]];
    if (id.indexOf("worker:") === 0) return out;                  // a worker's track comes in and goes out at its sides, clear of its neighbours
    out.push([mx, b[1] + b[3] + 1]);
    if (id !== "yard") out.push([mx, b[1] - 1]);
    return out;
  }
  function route(a, b, rail) {
    // a belt from beside one building to beside another: the shortest straight, L or Z shaped line that cuts through no building
    var best = null, any = null, len = function (w) { var n = 0; for (var k = 1; k < w.length; k++) n += Math.abs(w[k][0] - w[k - 1][0]) + Math.abs(w[k][1] - w[k - 1][1]); return n + 2 * (w.length - 2); };
    var P = rail ? trackPorts : ports;
    var to = Array.isArray(b) && typeof b[0] === "number" ? [b] : P(b);
    P(a).forEach(function (s) {
      to.forEach(function (e) {
        var mx = Math.round((s[0] + e[0]) / 2), my = Math.round((s[1] + e[1]) / 2);
        [[s, e], [s, [e[0], s[1]], e], [s, [s[0], e[1]], e], [s, [mx, s[1]], [mx, e[1]], e], [s, [s[0], my], [e[0], my], e]].forEach(function (w) {
          w = simplify(w);
          if (w.length < 2) return;
          for (var k = 1; k < w.length; k++) if (w[k][0] !== w[k - 1][0] && w[k][1] !== w[k - 1][1]) return;
          if (!any || len(w) < len(any)) any = w;
          if (!through(w) && (!best || len(w) < len(best))) best = w;
        });
      });
    });
    return best || any;
  }
  function nearestRun(pts, p) {
    // the run of a belt nearest a grid point
    var best = 0, bd = Infinity;
    for (var i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i];
      var x = Math.max(Math.min(a[0], b[0]), Math.min(Math.max(a[0], b[0]), p[0])), y = Math.max(Math.min(a[1], b[1]), Math.min(Math.max(a[1], b[1]), p[1]));
      var d = Math.abs(x - p[0]) + Math.abs(y - p[1]);
      if (d < bd) { bd = d; best = i - 1; }
    }
    return best;
  }
  function slid(pts, i, d) {
    // a belt with one run moved sideways by d cells; its ends stay beside their buildings and join the moved run with a bend
    var p = pts.map(function (q) { return q.slice(); }), across = p[i][1] === p[i + 1][1];
    if (i === 0) { p.unshift(p[0].slice()); i = 1; }
    if (i === p.length - 2) p.push(p[p.length - 1].slice());
    [i, i + 1].forEach(function (k) { if (across) p[k][1] += d; else p[k][0] += d; });
    p.forEach(function (q) { q[0] = Math.max(0, Math.min(meta.w, q[0])); q[1] = Math.max(0, Math.min(meta.h, q[1])); });
    return simplify(p);
  }

  // ---- editing
  function simplify(pts) {
    var out = [];
    pts.forEach(function (p) {
      var l = out[out.length - 1];
      if (l && l[0] === p[0] && l[1] === p[1]) return;
      var k = out[out.length - 2];
      if (k && ((k[0] === l[0] && l[0] === p[0]) || (k[1] === l[1] && l[1] === p[1]))) {
        var back = (l[0] - k[0]) * (p[0] - l[0]) + (l[1] - k[1]) * (p[1] - l[1]) < 0;
        if (!back) { out[out.length - 1] = p; return; }
      }
      out.push(p);
    });
    return out;
  }
  function through(pts) {
    var cs = cells(pts), ids = Object.keys(meta.nodes).filter(function (id) { return !FREE[id]; });
    return cs.some(function (c) { return ids.some(function (id) { var b = box(id); return b && inside(c, b); }); });
  }
  function follow(pts, atEnd, dx, dy) {
    // a building moved: its end of the belt moves with it and joins the rest with a bend
    var p = pts.map(function (q) { return q.slice(); });
    if (atEnd) p.reverse();
    var a = [p[0][0] + dx, p[0][1] + dy], ways = [];
    // join the moved end to each later corner with a bend, dropping the corners before it: after a long move the old corners
    // would make the belt double back. Take the shortest join that does not cut through a building (ties keep more of the belt).
    for (var i = 1; i < p.length; i++) {
      var b = p[i], rest = p.slice(i);
      if (a[0] === b[0] || a[1] === b[1]) ways.push([a].concat(rest));
      else ways.push([a, [a[0], b[1]]].concat(rest), [a, [b[0], a[1]]].concat(rest));
    }
    var len = function (w) { var n = 0; for (var k = 1; k < w.length; k++) n += Math.abs(w[k][0] - w[k - 1][0]) + Math.abs(w[k][1] - w[k - 1][1]); return n; };
    var ok = ways.filter(function (w) { return !through(w); }), out = ok[0] || ways[0];
    ok.forEach(function (w) { if (len(w) < len(out)) out = w; });
    out = simplify(out);
    if (atEnd) out.reverse();
    return out;
  }
  function moveNodes(ids, dx, dy, from, dists) {
    var nodes = from.nodes, belts = from.belts, moved = {};
    dists = dists || [];
    doc.districts = clone(from.districts || {});
    dists.forEach(function (name) {
      if (!doc.districts[name]) doc.districts[name] = fitIn(from, name);
      var d = doc.districts[name];
      dx = Math.max(-d.x, Math.min(meta.w - d.w - d.x, dx));
      dy = Math.max(-d.y, Math.min(meta.h - d.h - d.y, dy));
    });
    // the group stops at the floor's edge as one, so its belts move by the same step as its buildings
    ids.forEach(function (id) {
      var n = nodes[id], m = meta.nodes[id], x = n.x + m.dx, y = n.y + m.dy;
      dx = Math.max(-x, Math.min(meta.w - (n.w || m.w) - x, dx));
      dy = Math.max(-y, Math.min(meta.h - (n.h || m.h) - y, dy));
    });
    ids.forEach(function (id) {
      var n = nodes[id], to = {x: n.x + dx, y: n.y + dy};
      if (n.w) { to.w = n.w; to.h = n.h; }
      moved[id] = 1; doc.nodes[id] = to;
    });
    dists.forEach(function (name) { var d = doc.districts[name]; doc.districts[name] = {x: d.x + dx, y: d.y + dy, w: d.w, h: d.h}; });
    var shifted = [], ends = [];
    meta.hops.forEach(function (h) {
      var id = h[0] + ">" + h[1], pts = belts[id];
      if (!pts || pts.length < 2) return;
      if (moved[h[0]] && moved[h[1]]) { doc.belts[id] = pts.map(function (q) { return [q[0] + dx, q[1] + dy]; }); shifted.push(id); }
      else if (moved[h[0]]) { doc.belts[id] = follow(pts, false, dx, dy); ends.push([id, false]); }
      else if (moved[h[1]]) { doc.belts[id] = follow(pts, true, dx, dy); ends.push([id, true]); }
      else doc.belts[id] = pts;
    });
    doc.tracks = {};
    Object.keys(from.tracks || {}).forEach(function (id) {
      var ab = id.split(">"), pts = from.tracks[id];
      if (moved[ab[0]] && moved[ab[1]]) doc.tracks[id] = pts.map(function (q) { return [q[0] + dx, q[1] + dy]; });
      else if (moved[ab[0]]) doc.tracks[id] = follow(pts, false, dx, dy);
      else if (moved[ab[1]]) doc.tracks[id] = follow(pts, true, dx, dy);
      else doc.tracks[id] = pts;
    });
    carry(from, shifted, dx, dy, ends);
    grow();
  }
  function resizeNode(id, dx, dy, from) {
    // the corner handle: never smaller than the building's minimum, never off the floor; belts that reach it keep reaching it
    var n = from.nodes[id], m = meta.nodes[id], w0 = n.w || m.w, h0 = n.h || m.h;
    var w = Math.max(m.min[0], Math.min(meta.w - n.x, w0 + dx)), h = Math.max(m.min[1], Math.min(meta.h - n.y, h0 + dy));
    if (!AREAS[id]) {
      // a building keeps its proportions: the side dragged further sets the scale, the other follows
      var cur = w0 / m.w, sx = (w0 + dx) / m.w, sy = (h0 + dy) / m.h, sc = Math.abs(sx - cur) >= Math.abs(sy - cur) ? sx : sy;
      sc = Math.max(sc, m.min[0] / m.w, m.min[1] / m.h);
      sc = Math.min(sc, (meta.w - n.x) / m.w, (meta.h - n.y) / m.h);
      w = Math.max(m.min[0], Math.round(m.w * sc)); h = Math.max(m.min[1], Math.round(m.h * sc));
      if (dx && !dy && w === w0) w = w0 + Math.sign(dx);                    // Shift+arrow: always one step, the other side rounds after it
      if (dy && !dx && h === h0) h = h0 + Math.sign(dy);
      w = Math.max(m.min[0], Math.min(meta.w - n.x, w)); h = Math.max(m.min[1], Math.min(meta.h - n.y, h));
    }
    doc.nodes = clone(from.nodes); doc.belts = clone(from.belts); doc.districts = clone(from.districts || {});
    doc.nodes[id] = w === m.w && h === m.h ? {x: n.x, y: n.y} : {x: n.x, y: n.y, w: w, h: h};
    var x0 = n.x, y0 = n.y, cl = function (v, a, b) { return Math.max(a, Math.min(b, v)); }, ends = [];
    meta.hops.forEach(function (hp) {
      var hid = hp[0] + ">" + hp[1], pts = from.belts[hid];
      if (!pts || pts.length < 2) return;
      [[hp[0], false], [hp[1], true]].forEach(function (end) {
        if (end[0] !== id) return;
        var cur = doc.belts[hid], p = end[1] ? cur[cur.length - 1] : cur[0], q;
        if (p[0] === x0 + w0 + 1) q = [x0 + w + 1, cl(p[1], y0, y0 + h)];
        else if (p[1] === y0 + h0 + 1) q = [cl(p[0], x0, x0 + w), y0 + h + 1];
        else if (p[0] === x0 - 1) q = [p[0], cl(p[1], y0, y0 + h)];
        else q = [cl(p[0], x0, x0 + w), p[1]];
        if (q[0] !== p[0] || q[1] !== p[1]) { doc.belts[hid] = follow(cur, end[1], q[0] - p[0], q[1] - p[1]); ends.push([hid, end[1]]); }
      });
    });
    carry(from, [], 0, 0, ends);
    grow();
  }
  function fitIn(state, name) { var keep = doc; doc = state; var f = fit(name); doc = keep; return f; }
  function resize(name, dx, dy, from) {
    // the corner handle: never smaller than the stations it holds, never off the floor
    var d = (from.districts || {})[name] || fitIn(from, name), f = fit(name);
    doc.districts = clone(from.districts || {});
    var x1 = Math.min(meta.w, Math.max(f.x + f.w, d.x + d.w + dx)), y1 = Math.min(meta.h, Math.max(f.y + f.h, d.y + d.h + dy));
    doc.districts[name] = {x: d.x, y: d.y, w: Math.max(1, x1 - d.x), h: Math.max(1, y1 - d.y)};
  }
  function point(e) {
    var m = svg.getScreenCTM();
    if (!m) return [0, 0];
    var pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    var q = pt.matrixTransform(m.inverse());
    return [Math.max(0, Math.min(meta.w, Math.round(q.x / G))), Math.max(0, Math.min(meta.h, Math.round(q.y / G)))];
  }
  function finish() {
    if (drawing && drawing.length >= 2 && hopSel.value) { remember(); doc.belts[hopSel.value] = simplify(drawing); }
    drawing = null;
    draw();
  }
  function setTool(t) {
    tool = t; drawing = null; ugFrom = null; selBelt = selTrack = null; note = "";
    var bs = root.querySelectorAll("[data-tool]");
    for (var i = 0; i < bs.length; i++) bs[i].setAttribute("aria-pressed", bs[i].getAttribute("data-tool") === t ? "true" : "false");
    svg.setAttribute("data-tool", t);
    draw();
  }

  function startPan(e) {
    pan = {x: e.clientX, y: e.clientY, l: canvas.scrollLeft, t: canvas.scrollTop, id: e.pointerId};
    if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
    svg.setAttribute("data-panning", "true");
    e.preventDefault();
  }
  svg.addEventListener("pointerdown", function (e) {
    if (e.button === 1) return startPan(e);                               // the middle button pans with any tool
    if (e.button > 0) return;
    var p = point(e), t = e.target;
    var node = t.closest && t.closest("[data-node]"), dist = t.closest && t.closest("[data-district]");
    var piece = t.closest && t.closest("[data-piece]"), hop = t.closest && t.closest("[data-hop]"), trk = t.closest && t.closest("[data-track]");
    note = "";
    if (tool === "move") {
      if (trk && !node) {
        var tid = trk.getAttribute("data-track");
        selTrack = tid; selBelt = null; focused = null;
        slide = {track: tid, run: nearestRun(doc.tracks[tid], p), start: p, from: doc.tracks[tid], before: JSON.stringify(doc), id: e.pointerId};
        if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
        e.preventDefault();
        draw();
        return;
      }
      if (hop && !node) {
        // a belt: picked to draw again, and dragged sideways it slides
        var hid = hop.getAttribute("data-hop");
        hopSel.value = hid; selBelt = hid; selTrack = null; focused = null;
        slide = {hop: hid, run: nearestRun(doc.belts[hid], p), start: p, from: doc.belts[hid], before: JSON.stringify(doc), id: e.pointerId};
        if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
        e.preventDefault();
        draw();
        return;
      }
      var grip = t.closest && t.closest("[data-resize]"), ngrip = t.closest && t.closest("[data-grip]");
      var name = !node && dist ? dist.getAttribute("data-district") : null;
      var ids = node ? [node.getAttribute("data-node")] : name ? meta.districts[name].slice() : null;
      if (!ids) { selBelt = selTrack = focused = null; draw(); return startPan(e); }   // the bare ground: drag to pan
      selBelt = selTrack = null;
      focused = node ? ids[0] : null;
      focusedDist = name;
      drag = {ids: ids, dists: name ? [name] : [], resize: grip ? grip.getAttribute("data-resize") : null,
              grow: ngrip ? ngrip.getAttribute("data-grip") : null, start: p, from: clone(doc),
              before: JSON.stringify(doc), id: e.pointerId};
      if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    e.preventDefault();
    if ((tool === "belt" || tool === "rail") && !drawing && node && !AREAS[node.getAttribute("data-node")]) {
      // drag from one building onto the next: the belt for that hop (or, with Draw track, the track) is laid round the buildings
      link = {from: node.getAttribute("data-node"), id: e.pointerId, pts: null, to: null, hop: null, rail: tool === "rail"};
      if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
      return;
    }
    if (TERRAIN[tool]) return startTerrain(tool, e);
    if (tool === "belt") {
      if (!hopSel.value) return;
      if (!drawing) { drawing = [p]; }
      else {
        var l = drawing[drawing.length - 1];
        if (l[0] === p[0] && l[1] === p[1]) return finish();
        if (l[0] !== p[0] && l[1] !== p[1]) drawing.push([p[0], l[1]]);
        drawing.push(p);
      }
      return draw();
    }
    if (tool === "erase") {
      if (piece) { remember(); doc.pieces.splice(Number(piece.getAttribute("data-piece")), 1); return draw(); }
      if (trk) { remember(); delete doc.tracks[trk.getAttribute("data-track")]; return draw(); }
      if (hop) { remember(); delete doc.belts[hop.getAttribute("data-hop")]; doc.pieces = doc.pieces.filter(function (q) { return pieceOk(q, doc.belts); }); return draw(); }
      if (node && !AREAS[node.getAttribute("data-node")]) return erase(node.getAttribute("data-node"));
      return startTerrain("erase-terrain", e);                          // terrain under the pointer; drag to erase more
    }
    if (tool === "underground") {
      if (!ugFrom) { ugFrom = p; return draw(); }
      if (ugFrom[0] !== p[0] || ugFrom[1] !== p[1]) { remember(); doc.pieces.push({kind: "underground", from: ugFrom, to: p}); }
      ugFrom = null;
      return draw();
    }
    remember();
    doc.pieces.push({kind: tool, at: p, dir: dirSel.value});
    draw();
  });
  svg.addEventListener("pointermove", function (e) {
    if (pan && e.pointerId === pan.id) {
      canvas.scrollLeft = pan.l - (e.clientX - pan.x);
      canvas.scrollTop = pan.t - (e.clientY - pan.y);
      return;
    }
    if (paint && e.pointerId === paint.id) return moveTerrain(e);
    if (slide && e.pointerId === slide.id) {
      var q = point(e), r = slide.from[slide.run], s2 = slide.from[slide.run + 1], d = r[1] === s2[1] ? q[1] - slide.start[1] : q[0] - slide.start[0];
      var lines = slide.track ? doc.tracks : doc.belts;
      lines[slide.track || slide.hop] = d ? slid(slide.from, slide.run, d) : slide.from;
      return draw();
    }
    if (link && e.pointerId === link.id) {
      var lp = point(e), over = nodeAt(lp);
      link.to = over && over !== link.from && !AREAS[over] ? over : null;
      link.hop = link.to && (link.rail ? RAIL[link.from + ">" + link.to] : meta.hops.some(function (h) { return h[0] === link.from && h[1] === link.to; })) ? link.from + ">" + link.to : null;
      link.pts = route(link.from, link.to || lp, link.rail);
      return draw();
    }
    if (!drag || e.pointerId !== drag.id) return;
    var p = point(e);
    if (drag.grow) resizeNode(drag.grow, p[0] - drag.start[0], p[1] - drag.start[1], drag.from);
    else if (drag.resize) resize(drag.resize, p[0] - drag.start[0], p[1] - drag.start[1], drag.from);
    else moveNodes(drag.ids, p[0] - drag.start[0], p[1] - drag.start[1], drag.from, drag.dists);
    draw();
  });
  function drop(e) {
    if (pan && e.pointerId === pan.id) { pan = null; svg.removeAttribute("data-panning"); return; }
    if (paint && e.pointerId === paint.id) return endTerrain();
    if (slide && e.pointerId === slide.id) {
      if (JSON.stringify(doc) !== slide.before) { past.push(slide.before); future = []; }
      slide = null;
      return draw();
    }
    if (link && e.pointerId === link.id) {
      var done = link;
      link = null;
      if (done.hop && done.pts && done.rail) { remember(); doc.tracks[done.hop] = done.pts; selTrack = done.hop; selBelt = null; }
      else if (done.hop && done.pts) { remember(); doc.belts[done.hop] = done.pts; hopSel.value = done.hop; selBelt = done.hop; selTrack = null; doc.pieces = doc.pieces.filter(function (q) { return pieceOk(q, doc.belts); }); }
      else if (done.to && done.rail) note = "Track cannot run from " + label(done.from) + " to " + label(done.to) + ": trains go from the yard to the workers, on to the Train station and back to the yard, through junctions.";
      else if (done.to && RAIL[done.from + ">" + done.to]) note = "Between " + label(done.from) + " and " + label(done.to) + " trains run on track: use Draw track.";
      else if (done.to) note = "No hop from " + label(done.from) + " to " + label(done.to) + ": a belt carries tickets along the route, from the station a ticket leaves to the one it goes to.";
      return draw();
    }
    if (!drag || e.pointerId !== drag.id) return;
    if (JSON.stringify(doc) !== drag.before) { past.push(drag.before); future = []; }
    drag = null;
    draw();
  }
  svg.addEventListener("pointerup", drop);
  svg.addEventListener("pointercancel", drop);
  svg.addEventListener("focusin", function (e) {
    var n = e.target.closest && e.target.closest("[data-node]"), dn = e.target.closest && e.target.closest("[data-district]");
    if (n) { focused = n.getAttribute("data-node"); focusedDist = null; }
    else if (dn) { focusedDist = dn.getAttribute("data-district"); focused = null; }
  });
  svg.addEventListener("keydown", function (e) {
    var n = e.target.closest && e.target.closest("[data-node]"), dn = e.target.closest && e.target.closest("[data-district]");
    var step = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[e.key];
    if (!step || !(n || dn)) return;
    e.preventDefault();
    remember();
    if (n) {
      focused = n.getAttribute("data-node"); focusedDist = null;
      if (e.shiftKey && meta.nodes[focused].min) resizeNode(focused, step[0], step[1], clone(doc));     // Shift+arrows resize
      else moveNodes([focused], step[0], step[1], clone(doc));
    }
    else {
      focusedDist = dn.getAttribute("data-district"); focused = null;
      if (e.shiftKey) resize(focusedDist, step[0], step[1], clone(doc));            // Shift+arrows resize
      else moveNodes(meta.districts[focusedDist].slice(), step[0], step[1], clone(doc), [focusedDist]);
    }
    draw();
  });
  document.addEventListener("keydown", function (e) {
    if (e.target === area || e.target === filter) return;
    var k = (e.key || "").toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
    if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); return redo(); }
    if (k === "escape") { drawing = null; ugFrom = null; link = null; carryIn = null; draw(); }
    if (k === "enter" && drawing) { e.preventDefault(); finish(); }
  });
  root.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-tool")) return setTool(b.getAttribute("data-tool"));
    var act = b.getAttribute("data-act");
    if (act === "undo") undo();
    else if (act === "redo") redo();
    else if (act === "finish") finish();
    else if (act === "default") { remember(); doc = clone(meta["default"]); drawing = null; note = ""; draw(); }
    else if (act === "scratch") { remember(); doc = emptyFloor(); drawing = null; focused = null; focusedDist = null; note = ""; draw(); }
    else if (act === "hitboxes") { showHit = !showHit; b.setAttribute("aria-pressed", showHit ? "true" : "false"); draw(); }
    else if (act === "zoomin") setZoom(zoom * 1.25);
    else if (act === "zoomout") setZoom(zoom / 1.25);
    else if (act === "fit") fitView();
    else if (act === "full") {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (root.requestFullscreen) root.requestFullscreen();
    }
  });
  document.addEventListener("fullscreenchange", function () {
    var b = root.querySelector('[data-act="full"]');
    if (b) b.textContent = document.fullscreenElement === root ? "Exit full screen" : "Full screen";
  });
  if (tray) {
    // a part: clicked, it goes to a free spot in view; dragged onto the floor, it lands where it is let go
    tray.addEventListener("pointerdown", function (e) {
      var b = e.target.closest && e.target.closest("[data-part]");
      if (!b || e.button > 0 || doc.nodes[b.getAttribute("data-part")]) return;
      carryIn = {id: b.getAttribute("data-part"), x: e.clientX, y: e.clientY, moved: false, at: null};
    });
    tray.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-part]");
      if (!b || carried || doc.nodes[b.getAttribute("data-part")]) { carried = false; return; }
      place(b.getAttribute("data-part"), freeSpot(b.getAttribute("data-part")));
    });
    document.addEventListener("pointermove", function (e) {
      if (!carryIn) return;
      if (Math.abs(e.clientX - carryIn.x) + Math.abs(e.clientY - carryIn.y) > 4) carryIn.moved = true;
      var r = canvas.getBoundingClientRect(), over = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      var at = carryIn.moved && over ? point(e) : null;
      if (String(at) !== String(carryIn.at)) { carryIn.at = at; draw(); }
    });
    document.addEventListener("pointerup", function () {
      if (!carryIn) return;
      var c = carryIn;
      carryIn = null;
      carried = c.moved;
      if (c.moved && c.at) place(c.id, spot(c.id, c.at));
      else if (c.moved) draw();
    });
  }
  canvas.addEventListener("wheel", function (e) {
    if (!e.ctrlKey && !e.metaKey) return;                                  // a plain wheel scrolls; with Ctrl it zooms round the pointer
    e.preventDefault();
    setZoom(zoom * Math.pow(1.0015, -e.deltaY), e.clientX, e.clientY);
  }, {passive: false});
  hopSel.addEventListener("change", function () { drawing = null; draw(); });
  area.addEventListener("change", function () {
    var d = parse(area.value);
    if (d) { remember(); doc = d; draw(); }
  });

  meta.hops.forEach(function (h) {
    var o = document.createElement("option");
    o.value = h[0] + ">" + h[1];
    o.textContent = h[2];
    hopSel.appendChild(o);
  });
  setTool("move");
  fitView();
})();
