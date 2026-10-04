// Terrain for the floor layout editor: a line-by-line port of factory/ui/terrain.py, so the editor draws exactly what the floor
// draws (the same seeded shapes, the same rounding, the same markup; a browser test compares the two). It only builds SVG markup
// strings with classes, never style attributes. window.FT.svg(terrain, G, W, H, walls, trees, layer) is the whole terrain.
(function () {
  var TILE = 40;
  var SIZES = {tree: [30, 74], pine: [28, 68], bush: [16, 28], rock: [24, 50], pond: [68, 108], lamp: [110, 150], fog: [90, 170], shade: [60, 120]};
  var GROUNDS = ["grass", "dirt", "sand", "concrete", "water"];
  var DIRS = [[1.0, 0.0], [0.9659, 0.2588], [0.866, 0.5], [0.7071, 0.7071], [0.5, 0.866], [0.2588, 0.9659], [0.0, 1.0], [-0.2588, 0.9659],
              [-0.5, 0.866], [-0.7071, 0.7071], [-0.866, 0.5], [-0.9659, 0.2588], [-1.0, 0.0], [-0.9659, -0.2588], [-0.866, -0.5],
              [-0.7071, -0.7071], [-0.5, -0.866], [-0.2588, -0.9659], [0.0, -1.0], [0.2588, -0.9659], [0.5, -0.866], [0.7071, -0.7071],
              [0.866, -0.5], [0.9659, -0.2588]];
  var GREENS = [["#24452d", "#3a6b45", "#5e9a5c"], ["#21463f", "#336e5f", "#56a08a"], ["#33502a", "#4f7838", "#7da658"]];

  function f(v) {
    var r = Math.floor(v * 10 + 0.5) / 10;
    if (r === 0) r = 0;
    var s = r.toFixed(1);
    return s.slice(-2) === ".0" ? s.slice(0, -2) : s;
  }
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var x = a;
      var t = Math.imul(x ^ (x >>> 15), 1 | x) >>> 0;
      t = (((t + (Math.imul(t ^ (t >>> 7), 61 | t) >>> 0)) >>> 0) ^ t) >>> 0;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function seedOf() {
    var h = 2166136261;
    for (var i = 0; i < arguments.length; i++) h = Math.imul((h ^ (Math.trunc(arguments[i]) >>> 0)) >>> 0, 16777619) >>> 0;
    return h;
  }
  function sizeFor(kind, r) { var lh = SIZES[kind]; return Math.floor(lh[0] + (lh[1] - lh[0]) * r); }

  function blob(cx, cy, rad, rand, n, rough) {
    var pts = [];
    for (var i = 0; i < n; i++) {
      var d = DIRS[Math.floor(i * 24 / n) % 24], k = rad * (1 - rough + rough * 2 * rand());
      pts.push([cx + d[0] * k, cy + d[1] * k]);
    }
    var mid = function (a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; };
    var m0 = mid(pts[n - 1], pts[0]), out = "M " + f(m0[0]) + " " + f(m0[1]);
    for (i = 0; i < n; i++) {
      var p = pts[i], q = pts[(i + 1) % n], m = mid(p, q);
      out += " Q " + f(p[0]) + " " + f(p[1]) + " " + f(m[0]) + " " + f(m[1]);
    }
    return out + " Z";
  }
  function tree(x, y, s, v) {
    var rand = rng(v), g = GREENS[Math.floor(rand() * 3) % 3], dark = g[0], mid = g[1], light = g[2];
    var out = '<g class="tr-tree"><ellipse class="tr-shadow" cx="' + f(x + s * 0.08) + '" cy="' + f(y + s * 0.1) + '" rx="' + f(s * 0.46) + '" ry="' + f(s * 0.42) + '"/>';
    var n = 5 + Math.floor(rand() * 3), start = Math.floor(rand() * 24);
    for (var i = 0; i < n; i++) {
      var d = DIRS[(start + Math.floor(i * 24 / n)) % 24], dd = s * 0.2 * (0.75 + 0.3 * rand()), r = s * 0.22 * (0.85 + 0.3 * rand());
      out += '<circle cx="' + f(x + d[0] * dd) + '" cy="' + f(y + d[1] * dd) + '" r="' + f(r) + '" fill="' + mid + '" stroke="' + dark + '" stroke-width="1.5"/>';
    }
    out += '<circle cx="' + f(x) + '" cy="' + f(y) + '" r="' + f(s * 0.26) + '" fill="' + mid + '"/>';
    out += '<circle cx="' + f(x - s * 0.07) + '" cy="' + f(y - s * 0.09) + '" r="' + f(s * 0.15) + '" fill="' + light + '"/></g>';
    return out;
  }
  function pine(x, y, s, v) {
    var rand = rng(v), g = GREENS[Math.floor(rand() * 3) % 3];
    var out = '<g class="tr-pine"><ellipse class="tr-shadow" cx="' + f(x + s * 0.07) + '" cy="' + f(y + s * 0.09) + '" rx="' + f(s * 0.4) + '" ry="' + f(s * 0.36) + '"/>';
    [[1.0, g[0]], [0.72, g[1]], [0.4, g[2]]].forEach(function (lf) {
      var start = Math.floor(rand() * 2), pts = [];
      for (var i = 0; i < 24; i++) {
        var d = DIRS[(i + start) % 24], k = (s / 2) * lf[0] * (i % 2 === 0 ? (0.82 + 0.18 * rand()) : (0.42 + 0.12 * rand()));
        pts.push(f(x + d[0] * k) + " " + f(y + d[1] * k));
      }
      out += '<path d="M ' + pts.join(" L ") + ' Z" fill="' + lf[1] + '"/>';
    });
    return out + "</g>";
  }
  function bush(x, y, s, v) {
    var rand = rng(v), g = GREENS[Math.floor(rand() * 3) % 3];
    var out = '<g class="tr-bush"><ellipse class="tr-shadow" cx="' + f(x + s * 0.08) + '" cy="' + f(y + s * 0.1) + '" rx="' + f(s * 0.5) + '" ry="' + f(s * 0.44) + '"/>';
    out += '<path d="' + blob(x, y, s * 0.48, rand, 10, 0.22) + '" fill="' + g[1] + '" stroke="' + g[0] + '" stroke-width="1.5"/>';
    for (var i = 0; i < 3; i++) {
      var d = DIRS[Math.floor(rand() * 24) % 24], k = s * 0.28 * rand();
      out += '<circle class="tr-berry" cx="' + f(x + d[0] * k) + '" cy="' + f(y + d[1] * k) + '" r="' + f(Math.max(1.6, s * 0.08)) + '"/>';
    }
    return out + "</g>";
  }
  function rock(x, y, s, v) {
    var rand = rng(v), pts = [];
    for (var i = 0; i < 7; i++) {
      var d = DIRS[Math.floor(i * 24 / 7) % 24], k = s * 0.3 * (0.8 + 0.4 * rand());
      pts.push(f(x + d[0] * k) + " " + f(y + d[1] * k));
    }
    var out = '<g class="tr-rock"><ellipse class="tr-shadow" cx="' + f(x + s * 0.06) + '" cy="' + f(y + s * 0.08) + '" rx="' + f(s * 0.34) + '" ry="' + f(s * 0.3) + '"/>';
    out += '<path class="rk-main" d="M ' + pts.join(" L ") + ' Z"/>';
    out += '<path class="rk-lit" d="M ' + f(x - s * 0.18) + " " + f(y - s * 0.05) + " L " + f(x - s * 0.05) + " " + f(y - s * 0.2) + " L " + f(x + s * 0.1) + " " + f(y - s * 0.16) + '"/>';
    var n = 1 + Math.floor(rand() * 2);
    for (i = 0; i < n; i++) {
      var dd = DIRS[Math.floor(rand() * 24) % 24];
      out += '<circle class="rk-pebble" cx="' + f(x + dd[0] * s * 0.42) + '" cy="' + f(y + dd[1] * s * 0.42) + '" r="' + f(s * (0.08 + 0.06 * rand())) + '"/>';
    }
    return out + "</g>";
  }
  function pond(x, y, s, v) {
    var shape = blob(x, y, s / 2, rng(v), 12, 0.13), shore = blob(x, y, s / 2 + 6, rng(v), 12, 0.13);
    var out = '<g class="tr-pond"><path class="wt-sand" d="' + shore + '"/><path class="wt-water" d="' + shape + '"/>';
    [-1, 1].forEach(function (k) { out += '<path class="wt-ripple" d="M ' + f(x + k * s * 0.12 - 6) + " " + f(y + k * s * 0.08) + ' q 6 -5 12 0"/>'; });
    return out + "</g>";
  }
  function lamp(x, y, s) { return '<g class="lt-lamp"><circle class="lt-pool" cx="' + f(x) + '" cy="' + f(y) + '" r="' + f(s / 2) + '"/><circle class="lt-post" cx="' + f(x) + '" cy="' + f(y) + '" r="3"/></g>'; }
  function fog(x, y, s) { return '<ellipse class="lt-fog" cx="' + f(x) + '" cy="' + f(y) + '" rx="' + f(s / 2) + '" ry="' + f(s / 4) + '"/>'; }
  function shade(x, y, s) { return '<ellipse class="lt-shade" cx="' + f(x) + '" cy="' + f(y) + '" rx="' + f(s / 2) + '" ry="' + f(s * 0.32) + '"/>'; }
  var ART = {tree: tree, pine: pine, bush: bush, rock: rock, pond: pond, lamp: lamp, fog: fog, shade: shade};

  function smooth(pts, rounds) {
    // Chaikin's corner cutting, as terrain.py's smooth
    for (var k = 0; k < (rounds == null ? 2 : rounds); k++) {
      if (pts.length < 3) return pts;
      var out = [pts[0]];
      for (var i = 1; i < pts.length; i++) {
        var a = pts[i - 1], b = pts[i];
        out.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25], [a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
      }
      out.push(pts[pts.length - 1]);
      pts = out;
    }
    return pts;
  }
  function river(pts, seed) {
    if (pts.length < 2) return "";
    var rand = rng(seed != null ? seed : seedOf(pts[0][0], pts[0][1], pts.length));
    pts = smooth(pts);
    var widths = pts.map(function () { return 11 + 6 * rand(); });
    var left = [], right = [];
    pts.forEach(function (p, i) {
      var a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1];
      var n = Math.sqrt(dx * dx + dy * dy) || 1, nx = -dy / n, ny = dx / n;
      left.push([p[0] + nx * widths[i], p[1] + ny * widths[i]]);
      right.push([p[0] - nx * widths[i], p[1] - ny * widths[i]]);
    });
    var pt = function (q) { return f(q[0]) + " " + f(q[1]); };
    var water = "M " + left.concat(right.slice().reverse()).map(pt).join(" L ") + " Z", line = "M " + pts.map(pt).join(" L ");
    return '<g class="tr-river"><path class="wt-bank" d="' + line + '"/><path class="wt-water" d="' + water + '"/><path class="wt-shimmer" d="' + line + '"/></g>';
  }
  function runs(tiles) {
    // {kind: [[col, row], ...] or a Set of "col,row"} -> {kind: [[col, row, n], ...]}
    var out = {};
    GROUNDS.forEach(function (kind) {
      var cells = (tiles[kind] || []).slice().sort(function (a, b) { return a[1] - b[1] || a[0] - b[0]; }), rs = [];
      cells.forEach(function (c) {
        var l = rs[rs.length - 1];
        if (l && l[1] === c[1] && l[0] + l[2] === c[0]) l[2] += 1; else rs.push([c[0], c[1], 1]);
      });
      if (rs.length) out[kind] = rs;
    });
    return out;
  }
  function cellsOf(rs) { var out = []; (rs || []).forEach(function (r) { for (var i = 0; i < r[2]; i++) out.push([r[0] + i, r[1]]); }); return out; }
  function ground(tiles) {
    var out = "";
    ["grass", "dirt", "sand", "concrete"].forEach(function (kind) {
      (tiles[kind] || []).forEach(function (r) { out += '<rect class="gd-' + kind + '" x="' + r[0] * TILE + '" y="' + r[1] * TILE + '" width="' + r[2] * TILE + '" height="' + TILE + '"/>'; });
    });
    var water = tiles.water || [];
    if (water.length) {
      water.forEach(function (r) { out += '<rect class="wt-sand" x="' + (r[0] * TILE - 6) + '" y="' + (r[1] * TILE - 6) + '" width="' + (r[2] * TILE + 12) + '" height="' + (TILE + 12) + '" rx="14"/>'; });
      water.forEach(function (r) { out += '<rect class="wt-water" x="' + (r[0] * TILE - 1) + '" y="' + (r[1] * TILE - 1) + '" width="' + (r[2] * TILE + 2) + '" height="' + (TILE + 2) + '" rx="12"/>'; });
      water.forEach(function (r) {
        for (var i = 0; i < r[2]; i++) if ((r[0] + i + r[1]) % 3 === 0) out += '<path class="wt-ripple" d="M ' + (r[0] * TILE + 10 + 40 * i) + " " + (r[1] * TILE + 20) + ' q 5 -4 10 0 t 10 0"/>';
      });
    }
    return out ? '<g class="tr-ground">' + out + "</g>" : "";
  }
  function lines(kind, list, G) {
    var out = "";
    list.forEach(function (pts) {
      var d = "M " + pts.map(function (p) { return p[0] * G + " " + p[1] * G; }).join(" L ");
      if (kind === "road") { out += '<path class="rd-road" d="' + d + '"/><path class="rd-line" d="' + d + '"/>'; return; }
      out += '<path class="fc-rail" d="' + d + '"/>';
      for (var i = 1; i < pts.length; i++) {
        var a = pts[i - 1], b = pts[i], n = Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]), sx = Math.sign(b[0] - a[0]), sy = Math.sign(b[1] - a[1]);
        for (var k = 0; k <= n; k++) out += '<circle class="fc-post" cx="' + (a[0] + sx * k) * G + '" cy="' + (a[1] + sy * k) * G + '" r="2.6"/>';
      }
    });
    return out;
  }
  function gate(x, y, way, G) {
    var px = x * G, py = y * G, ex = way === "h" ? px + G : px, ey = way === "h" ? py : py + G;
    return '<g class="fc-gate"><circle class="fc-post" cx="' + px + '" cy="' + py + '" r="3"/><circle class="fc-post" cx="' + (ex + (way === "h" ? G : 0)) +
      '" cy="' + (ey + (way === "v" ? G : 0)) + '" r="3"/><line class="fc-leaf" x1="' + px + '" y1="' + py + '" x2="' + ex + '" y2="' + ey + '">' +
      '<animateTransform attributeName="transform" type="rotate" values="0 ' + px + " " + py + ";0 " + px + " " + py + ";-70 " + px + " " + py + ";-70 " + px + " " + py + ";0 " + px + " " + py +
      '" keyTimes="0;.3;.45;.8;1" dur="6s" repeatCount="indefinite"/></line></g>';
  }
  function hazards(rects, G) { return rects.map(function (r) { return '<rect class="hz-zone" x="' + r[0] * G + '" y="' + r[1] * G + '" width="' + r[2] * G + '" height="' + r[3] * G + '"/>'; }).join(""); }

  var DUCK = '<ellipse class="dk-body" cx="0" cy="0" rx="9" ry="5.5"/><circle class="dk-head" cx="8" cy="0" r="3.8"/><path class="dk-bill" d="M 11 -1.5 L 15 0 L 11 1.5 Z"/>';
  var HEN = '<ellipse class="dk-hen" cx="0" cy="0" rx="9" ry="5.5"/><circle class="dk-henhead" cx="8" cy="0" r="3.6"/><path class="dk-bill" d="M 11 -1.5 L 15 0 L 11 1.5 Z"/>';
  var BIRD = '<path class="bd-wing" d="M -2 0 L -5 -9 L 1 -9 L 3 0 L 1 9 L -5 9 Z"/><ellipse class="bd-body" cx="0" cy="0" rx="6" ry="3.4"/>' +
             '<circle class="bd-head" cx="5" cy="0" r="2.6"/><path class="bd-bill" d="M 7 -1 L 10 0 L 7 1 Z"/><path class="bd-tail" d="M -6 0 L -10 -3 L -10 3 Z"/>';
  function bodies(t) {
    var out = [];
    (t.items || []).forEach(function (it) {
      if (it[0] !== "pond") return;
      var x = it[1], y = it[2], s = it[3], r = s * 0.22;
      out.push(["pond", s, "M " + f(x - r) + " " + f(y) + " A " + f(r) + " " + f(r * 0.7) + " 0 1 1 " + f(x + r) + " " + f(y) + " A " + f(r) + " " + f(r * 0.7) + " 0 1 1 " + f(x - r) + " " + f(y)]);
    });
    (t.rivers || []).forEach(function (pts) {
      if (pts.length < 3) return;
      var a = Math.floor(pts.length / 4), b = Math.max(a + 1, Math.floor(3 * pts.length / 4)), seg = pts.slice(a, b + 1);
      out.push(["river", pts.length * 20, "M " + seg.concat(seg.slice(0, -1).reverse()).map(function (q) { return f(q[0]) + " " + f(q[1]); }).join(" L ")]);
    });
    var water = {}, list = cellsOf((t.tiles || {}).water);
    list.forEach(function (c) { water[c[0] + "," + c[1]] = c; });
    var seen = {};
    list.slice().sort(function (a, b) { return a[1] - b[1] || a[0] - b[0]; }).forEach(function (c) {
      var key = c[0] + "," + c[1];
      if (seen[key]) return;
      var patch = [], todo = [c];
      seen[key] = 1;
      while (todo.length) {
        var p = todo.pop();
        patch.push(p);
        [[p[0] + 1, p[1]], [p[0] - 1, p[1]], [p[0], p[1] + 1], [p[0], p[1] - 1]].forEach(function (q) {
          var k = q[0] + "," + q[1];
          if (water[k] && !seen[k]) { seen[k] = 1; todo.push(q); }
        });
      }
      if (patch.length >= 3) {
        patch.sort(function (a, b) { return a[1] - b[1] || a[0] - b[0]; });
        var pts = patch.slice(0, Math.max(2, Math.min(patch.length, 8))).map(function (q) { return [(q[0] + 0.5) * TILE, (q[1] + 0.5) * TILE]; });
        out.push(["tiles", patch.length * TILE, "M " + pts.concat(pts.slice(0, -1).reverse()).map(function (q) { return f(q[0]) + " " + f(q[1]); }).join(" L ")]);
      }
    });
    return out;
  }
  function ducks(t) {
    var out = "";
    bodies(t).forEach(function (b, k) {
      var n = b[1] < (b[0] === "pond" ? 90 : 200) ? 1 : 2;
      for (var j = 0; j < n; j++) {
        var rand = rng(seedOf(k, j, b[1])), dur = 16 + Math.floor(rand() * 10), begin = -Math.floor(rand() * dur) - j * 5, d = b[2];
        out += '<g class="dk-duck">' + (j === 0 ? DUCK : HEN) + '<animateMotion path="' + d + '" dur="' + dur + 's" begin="' + begin + 's" rotate="auto" calcMode="linear" ' +
          'keyPoints="0;.45;.45;1" keyTimes="0;.5;.7;1" repeatCount="indefinite"/></g>' +
          '<circle class="dk-ripple" r="4"><animateMotion path="' + d + '" dur="' + dur + 's" begin="' + begin + 's" calcMode="linear" ' +
          'keyPoints="0;.45;.45;1" keyTimes="0;.5;.7;1" repeatCount="indefinite"/>' +
          '<animate attributeName="r" values="3;3;14;3" keyTimes="0;.5;.7;1" dur="' + dur + 's" begin="' + begin + 's" repeatCount="indefinite"/>' +
          '<animate attributeName="opacity" values="0;0;.6;0" keyTimes="0;.5;.6;.7" dur="' + dur + 's" begin="' + begin + 's" repeatCount="indefinite"/></circle>';
      }
    });
    return out;
  }
  function birds(t, W, H) {
    var perch = (t.items || []).filter(function (it) { return it[0] === "tree" || it[0] === "bush"; }).slice(0, 3), out = "";
    perch.forEach(function (it, k) {
      var x = it[1], y = it[2], s = it[3], rand = rng(seedOf(x, y, k)), side = Math.floor(rand() * 4);
      var start = [[-40, y - 120], [W + 40, y - 80], [x - 140, -40], [x + 160, H + 40]][side], end = [[W + 40, y - 160], [-40, y + 60], [x + 180, H + 40], [x - 120, -40]][side];
      var lx = x + s * 0.55 + 6, ly = y + s * 0.15, dur = 18 + Math.floor(rand() * 8), begin = -Math.floor(rand() * dur);
      var path = "M " + f(start[0]) + " " + f(start[1]) + " Q " + f((start[0] + lx) / 2) + " " + f(start[1] - 60) + " " + f(lx) + " " + f(ly) + " Q " + f((lx + end[0]) / 2) + " " + f(ly - 80) + " " + f(end[0]) + " " + f(end[1]);
      var motion = '<animateMotion path="' + path + '" dur="' + dur + 's" begin="' + begin + 's" rotate="auto" calcMode="linear" keyPoints="0;.5;.5;1;1" keyTimes="0;.3;.62;.9;1" repeatCount="indefinite"/>';
      out += '<g class="bd-shadow-wrap"><g class="bd-shadow"><ellipse cx="0" cy="0" rx="6" ry="3"/>' +
        '<animateTransform attributeName="transform" type="translate" values="16 22;2 3;2 3;16 22;16 22" keyTimes="0;.3;.62;.9;1" dur="' + dur + 's" begin="' + begin + 's" repeatCount="indefinite"/></g>' + motion + "</g>" +
        '<g class="bd-bird">' + BIRD + motion + '<animate attributeName="opacity" values="1;1;0" keyTimes="0;.9;1" calcMode="discrete" dur="' + dur + 's" begin="' + begin + 's" repeatCount="indefinite"/></g>';
    });
    return out;
  }
  function svg(t, G, W, H, walls, trees, layer) {
    t = t || {};
    var items = t.items || [], kinds = function (ks) { return items.filter(function (it) { return ks.indexOf(it[0]) >= 0; }); };
    var under = ground(t.tiles || {});
    under += (t.rivers || []).map(function (p) { return river(p); }).join("");
    under += kinds(["pond"]).map(function (it) { return pond(it[1], it[2], it[3], it[4]); }).join("");
    under += lines("road", t.roads || [], G) + hazards(t.hazards || [], G);
    under += kinds(["shade"]).map(function (it) { return shade(it[1], it[2], it[3], it[4]); }).join("");
    var over = (walls || []).map(function (w) { return '<path class="fp-wall" d="M ' + w.map(function (p) { return p[0] * G + " " + p[1] * G; }).join(" L ") + '"/>'; }).join("");
    over += lines("fence", t.fences || [], G) + (t.gates || []).map(function (g) { return gate(g[0], g[1], g[2], G); }).join("");
    over += (trees || []).map(function (p) { return tree(p[0] * G, p[1] * G, 36, seedOf(p[0], p[1])); }).join("");
    over += kinds(["rock", "bush", "tree", "pine"]).map(function (it) { return ART[it[0]](it[1], it[2], it[3], it[4]); }).join("");
    over += ducks(t);
    var top = kinds(["lamp", "fog"]).map(function (it) { return ART[it[0]](it[1], it[2], it[3], it[4]); }).join("") + birds(t, W, H);
    var parts = {under: under, over: over, top: top};
    return layer && layer !== "all" ? parts[layer] : under + over + top;
  }
  window.FT = {svg: svg, f: f, rng: rng, seedOf: seedOf, sizeFor: sizeFor, runs: runs, cellsOf: cellsOf, bodies: bodies, TILE: TILE, SIZES: SIZES, GROUNDS: GROUNDS};
})();
