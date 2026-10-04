// The floor's layout editor (/floor/edit). The server renders the page, checks every save and draws the floor; this script only shows
// the layout from the form's JSON as a plan on a grid with each building's picture, lets a person move and resize buildings, draw belts,
// walls and trees and place pieces (mouse, pen or touch), pan and zoom, and writes the JSON back into the form. It sets SVG attributes only, never style attributes, so the page policy holds.
(function () {
  var root = document.querySelector(".fe");
  var area = document.querySelector(".fe-save textarea[name=plan]");
  if (!root || !area) return;
  var meta = JSON.parse(root.getAttribute("data-meta"));
  var svg = root.querySelector(".fe-svg"), list = root.querySelector(".fe-problems"), canvas = root.querySelector(".fe-canvas");
  var artSrc = root.querySelector(".fe-art"), pics = {}, zoomOut = root.querySelector(".fe-zoomval");
  var hopSel = root.querySelector(".fe-hop"), dirSel = root.querySelector(".fe-dir");
  var G = meta.grid, NS = "http://www.w3.org/2000/svg";
  var doc = parse(area.value) || clone(meta["default"]);
  if (!doc.districts) doc.districts = {};
  var past = [], future = [], tool = "move", drawing = null, drag = null, pan = null, ugFrom = null, focused = null, focusedDist = null;
  var zoom = 1, AREAS = {mainland: 1, sea: 1, airfield: 1, yard: 1};
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
  function check() {
    var out = [], bad = {}, ids = Object.keys(meta.nodes), i, j;
    for (i = 0; i < ids.length; i++) {
      var a = box(ids[i]);
      if (!a) { out.push("Missing: " + label(ids[i]) + "."); continue; }
      if (a[0] < 0 || a[1] < 0 || a[0] + a[2] > meta.w || a[1] + a[3] > meta.h) out.push(label(ids[i]) + " is outside the floor.");
      for (j = i + 1; j < ids.length; j++) {
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
          for (var n = 0; n < ids.length && !msg; n++) { var bx = box(ids[n]); if (bx && inside(cs[k], bx)) msg = name + " runs through " + label(ids[n]) + "."; }
      }
      if (msg) { out.push(msg); bad[id] = 1; }
    });
    return {problems: out, bad: bad};
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
      var w = el("g", {"class": "fe-pic", transform: "translate(" + b[0] * G + " " + b[1] * G + ") scale(" + b[2] / m.w + " " + b[3] / m.h + ")"}, g);
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
    area.value = JSON.stringify(doc);
    var res = check();
    while (list.firstChild) list.removeChild(list.firstChild);
    res.problems.slice(0, 12).forEach(function (p) { var li = document.createElement("li"); li.textContent = p; list.appendChild(li); });
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
    Object.keys(meta.districts).forEach(function (name) {
      var d = dbox(name);
      if (!d) return;
      var g = el("g", {"class": "fe-dist" + (name === focusedDist ? " sel" : ""), "data-district": name, tabindex: 0, role: "button",
                       "aria-label": "District " + name + ", at " + d.x + ", " + d.y + ", " + d.w + " by " + d.h}, svg);
      el("rect", {x: d.x * G, y: d.y * G, width: d.w * G, height: d.h * G, rx: 4}, g);
      el("text", {x: d.x * G + 6, y: d.y * G + 14}, g, name);
      el("rect", {"class": "fe-resize", "data-resize": name, x: (d.x + d.w) * G - 10, y: (d.y + d.h) * G - 10, width: 10, height: 10}, g);
    });
    doc.walls.forEach(function (w, i) { el("polyline", {points: poly(w), "class": "fp-wall fe-wall", "data-wall": i}, svg); });
    doc.trees.forEach(function (t, i) {
      var g = el("g", {"class": "fp-tree fe-tree", "data-tree": i}, svg);
      el("circle", {"class": "fp-crown", cx: t[0] * G, cy: t[1] * G, r: 9}, g);
      el("circle", {"class": "fp-leaf", cx: t[0] * G - 3, cy: t[1] * G - 3, r: 4}, g);
    });
    Object.keys(meta.nodes).forEach(function (id) { if (!AREAS[id]) drawNode(id); });
    Object.keys(doc.belts).forEach(function (id) {
      var pts = doc.belts[id];
      if (!pts || pts.length < 2) return;
      var cls = "fe-belt" + (res.bad[id] ? " bad" : "") + (id === hopSel.value ? " sel" : "");
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
    if (drawing) el("polyline", {points: poly(drawing), "class": (tool === "wall" ? "fp-wall" : "fe-belt") + " drawing"}, svg);
    if (ugFrom) el("circle", {"class": "fe-end drawing", cx: ugFrom[0] * G, cy: ugFrom[1] * G, r: 6}, svg);
    if (focusedDist && !focused) { var fd = svg.querySelector('[data-district="' + focusedDist + '"]'); if (fd && document.activeElement !== fd && svg.contains(document.activeElement || null)) fd.focus(); }
    if (focused) { var f = svg.querySelector('[data-node="' + focused + '"]'); if (f && document.activeElement !== f && svg.contains(document.activeElement || null)) f.focus(); }
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
    var cs = cells(pts), ids = Object.keys(meta.nodes);
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
    meta.hops.forEach(function (h) {
      var id = h[0] + ">" + h[1], pts = belts[id];
      if (!pts || pts.length < 2) return;
      if (moved[h[0]] && moved[h[1]]) doc.belts[id] = pts.map(function (q) { return [q[0] + dx, q[1] + dy]; });
      else if (moved[h[0]]) doc.belts[id] = follow(pts, false, dx, dy);
      else if (moved[h[1]]) doc.belts[id] = follow(pts, true, dx, dy);
      else doc.belts[id] = pts;
    });
    grow();
  }
  function resizeNode(id, dx, dy, from) {
    // the corner handle: never smaller than the building's minimum, never off the floor; belts that reach it keep reaching it
    var n = from.nodes[id], m = meta.nodes[id], w0 = n.w || m.w, h0 = n.h || m.h;
    var w = Math.max(m.min[0], Math.min(meta.w - n.x, w0 + dx)), h = Math.max(m.min[1], Math.min(meta.h - n.y, h0 + dy));
    doc.nodes = clone(from.nodes); doc.belts = clone(from.belts); doc.districts = clone(from.districts || {});
    doc.nodes[id] = w === m.w && h === m.h ? {x: n.x, y: n.y} : {x: n.x, y: n.y, w: w, h: h};
    var x0 = n.x, y0 = n.y, cl = function (v, a, b) { return Math.max(a, Math.min(b, v)); };
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
        if (q[0] !== p[0] || q[1] !== p[1]) doc.belts[hid] = follow(cur, end[1], q[0] - p[0], q[1] - p[1]);
      });
    });
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
    if (drawing && drawing.length >= 2 && tool === "wall") { remember(); doc.walls.push(simplify(drawing)); }
    else if (drawing && drawing.length >= 2 && hopSel.value) { remember(); doc.belts[hopSel.value] = simplify(drawing); }
    drawing = null;
    draw();
  }
  function setTool(t) {
    tool = t; drawing = null; ugFrom = null;
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
    var piece = t.closest && t.closest("[data-piece]"), hop = t.closest && t.closest("[data-hop]");
    if (tool === "move") {
      if (hop && !node) { hopSel.value = hop.getAttribute("data-hop"); draw(); return; }
      var grip = t.closest && t.closest("[data-resize]"), ngrip = t.closest && t.closest("[data-grip]");
      var name = !node && dist ? dist.getAttribute("data-district") : null;
      var ids = node ? [node.getAttribute("data-node")] : name ? meta.districts[name].slice() : null;
      if (!ids) return startPan(e);                                        // the bare ground: drag to pan
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
    if (tool === "belt" || tool === "wall") {
      if (tool === "belt" && !hopSel.value) return;
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
      var wall = t.closest && t.closest("[data-wall]"), tree = t.closest && t.closest("[data-tree]");
      if (!piece && !wall && !tree) return;
      remember();
      if (piece) doc.pieces.splice(Number(piece.getAttribute("data-piece")), 1);
      else if (wall) doc.walls.splice(Number(wall.getAttribute("data-wall")), 1);
      else doc.trees.splice(Number(tree.getAttribute("data-tree")), 1);
      return draw();
    }
    if (tool === "tree") {
      if (doc.trees.some(function (q) { return q[0] === p[0] && q[1] === p[1]; })) return;
      remember();
      doc.trees.push(p);
      return draw();
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
    if (!drag || e.pointerId !== drag.id) return;
    var p = point(e);
    if (drag.grow) resizeNode(drag.grow, p[0] - drag.start[0], p[1] - drag.start[1], drag.from);
    else if (drag.resize) resize(drag.resize, p[0] - drag.start[0], p[1] - drag.start[1], drag.from);
    else moveNodes(drag.ids, p[0] - drag.start[0], p[1] - drag.start[1], drag.from, drag.dists);
    draw();
  });
  function drop(e) {
    if (pan && e.pointerId === pan.id) { pan = null; svg.removeAttribute("data-panning"); return; }
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
    if (e.target === area) return;
    var k = (e.key || "").toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
    if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); return redo(); }
    if (k === "escape") { drawing = null; ugFrom = null; draw(); }
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
    else if (act === "default") { remember(); doc = clone(meta["default"]); drawing = null; draw(); }
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
