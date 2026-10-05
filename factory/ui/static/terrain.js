// Terrain for the floor layout editor: a line-by-line port of factory/ui/terrain.py, so the editor draws exactly what the floor
// draws (the same seeded shapes, the same rounding, the same markup; a browser test compares the two). It only builds SVG markup
// strings with classes, never style attributes. window.FT.svg(terrain, G, W, H, walls, trees, layer, tracks) is the whole terrain.
(function () {
  var TILE = 40;
  var SIZES = {tree: [30, 74], pine: [28, 68], bush: [16, 28], rock: [24, 50], pond: [68, 108], lamp: [110, 150], fog: [90, 170], shade: [60, 120]};
  // the park: each piece's box, width by height; its size is its width (terrain.py's PARK)
  var PARK = {fetch: [200, 120], playground: [180, 130], picnic: [110, 90], bench: [70, 40], dogwalk: [180, 90]};
  var PARK_NAMES = {fetch: "fetch with a dog", playground: "playground", picnic: "picnic", bench: "bench", dogwalk: "dog walker"};
  var TOWN = window.TOWN_ART || {};         // static/town.js, generated: the town's buildings, vehicles and scenery stand like park pieces
  Object.keys(TOWN).forEach(function (k) { PARK[k] = [TOWN[k].w, TOWN[k].h]; PARK_NAMES[k] = TOWN[k].name.toLowerCase(); });
  Object.keys(PARK).forEach(function (k) { SIZES[k] = [PARK[k][0], PARK[k][0]]; });
  var SOLID = ["tree", "pine", "bush", "rock", "pond"].concat(Object.keys(PARK));
  var CAR_COLOURS = ["#d9675b", "#8fa8ff", "#e8edf0", "#f2a93b", "#52c7a1", "#59636b", "#c9a46a", "#b69cff"];
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
  // ---- the park, traffic and bridges (terrain.py holds the same strings)
  var PK_FETCH = "<path d=\"M 20 96 Q 110 100 196 94\" fill=\"none\" stroke=\"#25331f\" stroke-width=\"5\" stroke-linecap=\"round\" opacity=\".7\"/><g><ellipse cx=\"34\" cy=\"94\" rx=\"2.6\" ry=\".9\" fill=\"#000\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;70 0;138 0;147 0;147 0;121 0;1 0;0 0;0 0\" keyTimes=\"0;.06;.19;.33;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\".2;.2;.12;.6;.6;.6;.5;.5;.2;.2\" keyTimes=\"0;.06;.19;.33;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/></ellipse></g><g transform=\"translate(26 94)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#2f3a52\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#3f8fd0\" stroke=\"#285c87\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"30 .6 -16;110 .6 -16;-40 .6 -16;30 .6 -16;30 .6 -16;55 .6 -16;30 .6 -16;30 .6 -16\" keyTimes=\"0;.05;.08;.14;.84;.88;.92;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 7 -14\" fill=\"none\" stroke=\"#3272a6\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(50 94)\"><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;120 0;120 0;0 0;0 0\" keyTimes=\"0;.08;.4;.46;.8;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1;1 1\" keyTimes=\"0;.42;.84\" calcMode=\"discrete\" dur=\"5.6s\" repeatCount=\"indefinite\"/><g transform=\"translate(0 0)\"><ellipse cx=\"0\" cy=\"0\" rx=\"9\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -5 -6;26 -5 -6;-26 -5 -6\" dur=\"0.36s\" begin=\"-0.18s\" repeatCount=\"indefinite\"/><path d=\"M -5 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 4.5 -6;26 4.5 -6;-26 4.5 -6\" dur=\"0.36s\" begin=\"-0.18s\" repeatCount=\"indefinite\"/><path d=\"M 4.5 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-18 -7 -8;22 -7 -8;-18 -7 -8\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M -7 -8 q -4 -2 -5.5 -6.5\" fill=\"none\" stroke=\"#b07a45\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><ellipse cx=\"0\" cy=\"-7.2\" rx=\"7.6\" ry=\"3.6\" fill=\"#b07a45\" stroke=\"#6d4b2a\" stroke-width=\".6\"/><circle cx=\"7.4\" cy=\"-10.6\" r=\"3.3\" fill=\"#b07a45\" stroke=\"#6d4b2a\" stroke-width=\".6\"/><ellipse cx=\"10.4\" cy=\"-9.6\" rx=\"2.4\" ry=\"1.6\" fill=\"#b07a45\"/><circle cx=\"12.5\" cy=\"-9.9\" r=\".8\" fill=\"#1a1f24\"/><path d=\"M 6 -13.4 q -2 2.6 0 5\" fill=\"#6d4b2a\"/><circle cx=\"8.4\" cy=\"-11.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -3.6 -6;26 -3.6 -6;-26 -3.6 -6\" dur=\"0.36s\" repeatCount=\"indefinite\"/><path d=\"M -3.6 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 6 -6;26 6 -6;-26 6 -6\" dur=\"0.36s\" repeatCount=\"indefinite\"/><path d=\"M 6 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g></g></g></g></g><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;14 -26;40 -46;70 -52;100 -42;124 -18;138 12;143 2;147 12;147 4;121 4;1 4;0 0;0 0\" keyTimes=\"0;.06;.09;.14;.19;.24;.29;.33;.36;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><circle cx=\"34\" cy=\"78\" r=\"2.4\" fill=\"#d7e05a\" stroke=\"#6b7020\" stroke-width=\".8\"/></g>";
  var PK_PLAYGROUND = "<path d=\"M 4 126 L 176 126 L 166 98 L 14 98 Z\" fill=\"#8a7a52\" stroke=\"#5c4a30\" stroke-width=\"2\" stroke-linejoin=\"round\"/><path d=\"M 30 112 h2 M 92 118 h2 M 140 108 h2 M 60 104 h2 M 120 120 h2\" stroke=\"#6e6040\" stroke-width=\"2\" stroke-linecap=\"round\"/><path d=\"M 8 106 L 20 34 L 32 106 M 80 106 L 92 34 L 104 106\" fill=\"none\" stroke=\"#a8473d\" stroke-width=\"3\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 20 34 H 92\" stroke=\"#d9675b\" stroke-width=\"4\" stroke-linecap=\"round\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-30 42 34;30 42 34;-30 42 34\" calcMode=\"spline\" keyTimes=\"0;.5;1\" keySplines=\".45 0 .55 1;.45 0 .55 1\" dur=\"2.2s\" repeatCount=\"indefinite\"/><path d=\"M 37 34 V 78 M 47 34 V 78\" stroke=\"#9aa7b0\" stroke-width=\"1\" stroke-dasharray=\"2 1.5\"/><rect x=\"35\" y=\"78\" width=\"14\" height=\"3\" rx=\"1\" fill=\"#3b464e\"/><g transform=\"translate(40 83.5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#52c7a1\" stroke=\"#358168\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#419f80\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-30 70 34;30 70 34;-30 70 34\" calcMode=\"spline\" keyTimes=\"0;.5;1\" keySplines=\".45 0 .55 1;.45 0 .55 1\" dur=\"2.2s\" begin=\"-1.1s\" repeatCount=\"indefinite\"/><path d=\"M 65 34 V 78 M 75 34 V 78\" stroke=\"#9aa7b0\" stroke-width=\"1\" stroke-dasharray=\"2 1.5\"/><rect x=\"63\" y=\"78\" width=\"14\" height=\"3\" rx=\"1\" fill=\"#3b464e\"/><g transform=\"translate(68 83.5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#f2a93b\" stroke=\"#9d6d26\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#3a2a18\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#c1872f\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><path d=\"M 118 106 V 50 M 128 106 V 50\" stroke=\"#7d8890\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M 118 58 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 67 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 76 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 85 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 94 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 103 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><rect x=\"114\" y=\"46\" width=\"20\" height=\"4\" rx=\"1\" fill=\"#59636b\"/><path d=\"M 115 46 V 38 H 133 V 46\" fill=\"none\" stroke=\"#9aa7b0\" stroke-width=\"1.5\"/><path d=\"M 133 50 C 146 52 152 70 160 88 S 170 104 178 104\" fill=\"none\" stroke=\"#5f78c9\" stroke-width=\"8\" stroke-linecap=\"round\"/><path d=\"M 133 49 C 146 51 152 69 160 87 S 170 103 178 103\" fill=\"none\" stroke=\"#a6baff\" stroke-width=\"2.5\" stroke-linecap=\"round\" opacity=\".8\"/><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 -56;0 -56\" keyTimes=\"0;.46;1\" dur=\"4s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;.47\" calcMode=\"discrete\" dur=\"4s\" repeatCount=\"indefinite\"/><g transform=\"translate(123 106) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0\" keyTimes=\"0;.48;.85\" calcMode=\"discrete\" dur=\"4s\" repeatCount=\"indefinite\"/><g><animateMotion path=\"M 133 48 C 146 50 152 68 160 86 S 170 102 176 102\" keyPoints=\"0;0;1;1\" keyTimes=\"0;.5;.8;1\" calcMode=\"linear\" dur=\"4s\" repeatCount=\"indefinite\"/><g transform=\"translate(-2 5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g><path d=\"M 72 120 L 82 120 L 81 112 L 73 112 Z\" fill=\"#f2a93b\" stroke=\"#9c6420\" stroke-width=\".8\"/><path d=\"M 73 112 Q 77 106 81 112\" fill=\"none\" stroke=\"#9c6420\" stroke-width=\".8\"/><g transform=\"translate(58 121) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#8fa8ff\" stroke=\"#5c6da5\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"0 0.6 -10;28 0.6 -10;0 0.6 -10\" dur=\"1.2s\" repeatCount=\"indefinite\"/><path d=\"M .6 -10 L 6 -6\" fill=\"none\" stroke=\"#7286cc\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(100 124)\"><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;56 0;56 0;0 0;0 0\" keyTimes=\"0;.46;.54;.96;1\" dur=\"7s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1\" keyTimes=\"0;.5\" calcMode=\"discrete\" dur=\"7s\" repeatCount=\"indefinite\"/><g transform=\"translate(0 0) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#d9675b\" stroke=\"#8d423b\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"20 0.6 -16;-20 0.6 -16;20 0.6 -16\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#ad5248\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g></g>";
  var PK_PICNIC = "<path d=\"M 6 84 L 90 84 L 104 58 L 20 58 Z\" fill=\"url(#pk-check)\" stroke=\"#9c4a40\" stroke-width=\"1.5\" stroke-linejoin=\"round\"/><g transform=\"translate(46 66) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#62c497\" stroke=\"#3f7f62\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#3a2a18\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-20 0.6 -10.5;20 0.6 -10.5;-20 0.6 -10.5\" dur=\"1.4s\" repeatCount=\"indefinite\"/><path d=\"M .6 -10.5 L 4 -16\" fill=\"none\" stroke=\"#4e9c78\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><ellipse cx=\"34\" cy=\"76\" rx=\"6\" ry=\"2\" fill=\"#f4f1ea\" stroke=\"#9aa7b0\" stroke-width=\".6\"/><circle cx=\"34\" cy=\"75.4\" r=\"1.6\" fill=\"#d9675b\"/><ellipse cx=\"74\" cy=\"78\" rx=\"6\" ry=\"2\" fill=\"#f4f1ea\" stroke=\"#9aa7b0\" stroke-width=\".6\"/><ellipse cx=\"74\" cy=\"77.2\" rx=\"2.4\" ry=\"1\" fill=\"#62c497\"/><path d=\"M 50 74 L 66 74 L 64 64 L 52 64 Z\" fill=\"#8a6a40\" stroke=\"#3a2a18\" stroke-width=\".8\"/><path d=\"M 51 68 H 65 M 51.5 71 H 64.5\" stroke=\"#6b5233\" stroke-width=\".8\"/><path d=\"M 53 64 Q 58 55 63 64\" fill=\"none\" stroke=\"#5c4630\" stroke-width=\"1.6\"/><rect x=\"80\" y=\"64\" width=\"3.4\" height=\"11\" rx=\"1\" fill=\"#2f7a54\"/><rect x=\"80.6\" y=\"61.5\" width=\"2.2\" height=\"3\" fill=\"#c9d1d7\"/><ellipse cx=\"58\" cy=\"80\" rx=\"5\" ry=\"1.8\" fill=\"#e2b56b\"/><g transform=\"translate(16 84)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#c68d5e\" stroke=\"#8a6241\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"0 .6 -14.5;0 .6 -14.5;28 .6 -14.5;28 .6 -14.5;0 .6 -14.5\" keyTimes=\"0;.35;.5;.62;.8\" dur=\"3.4s\" repeatCount=\"indefinite\"/><path d=\"M .6 -14.5 L 6.5 -10\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 -1.4;0 0\" keyTimes=\"0;.5;1\" dur=\"1.8s\" repeatCount=\"indefinite\"/><g transform=\"translate(99 83) scale(-1 1)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#5c4630\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#f2a93b\" stroke=\"#9d6d26\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#f1c9a5\" stroke=\"#a88c73\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#c1872f\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g>";
  var PK_BENCH = "<ellipse cx=\"35\" cy=\"38.5\" rx=\"31\" ry=\"2\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M 10 12 V 38 M 60 12 V 38\" stroke=\"#3b464e\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><rect x=\"7\" y=\"12\" width=\"56\" height=\"3.2\" rx=\"1\" fill=\"#9a7448\" stroke=\"#3a2a18\" stroke-width=\".6\"/><rect x=\"7\" y=\"17\" width=\"56\" height=\"3.2\" rx=\"1\" fill=\"#9a7448\" stroke=\"#3a2a18\" stroke-width=\".6\"/><rect x=\"5\" y=\"26\" width=\"60\" height=\"4\" rx=\"1\" fill=\"#b08655\" stroke=\"#3a2a18\" stroke-width=\".6\"/><path d=\"M 14 30 V 38 M 56 30 V 38\" stroke=\"#3b464e\" stroke-width=\"2.2\" stroke-linecap=\"round\"/>";
  var PK_DOGWALK = "<path d=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" fill=\"none\" stroke=\"#3a4631\" stroke-width=\"10\"/><path d=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" fill=\"none\" stroke=\"#4a5a3c\" stroke-width=\"1\" stroke-dasharray=\"3 6\"/><g><animateMotion path=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" dur=\"26s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1.12 1.12\" keyTimes=\"0;.5\" calcMode=\"discrete\" dur=\"26s\" repeatCount=\"indefinite\"/><path d=\"M 1.6 -9.6 Q 10 -4 21 -10\" fill=\"none\" stroke=\"#c9d1d7\" stroke-width=\".8\"/><g transform=\"translate(19 0) scale(0.82)\"><ellipse cx=\"0\" cy=\"0\" rx=\"9\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -5 -6;26 -5 -6;-26 -5 -6\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M -5 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 4.5 -6;26 4.5 -6;-26 4.5 -6\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 4.5 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-18 -7 -8;22 -7 -8;-18 -7 -8\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M -7 -8 q -4 -2 -5.5 -6.5\" fill=\"none\" stroke=\"#e8edf0\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><ellipse cx=\"0\" cy=\"-7.2\" rx=\"7.6\" ry=\"3.6\" fill=\"#e8edf0\" stroke=\"#8f9294\" stroke-width=\".6\"/><circle cx=\"7.4\" cy=\"-10.6\" r=\"3.3\" fill=\"#e8edf0\" stroke=\"#8f9294\" stroke-width=\".6\"/><ellipse cx=\"10.4\" cy=\"-9.6\" rx=\"2.4\" ry=\"1.6\" fill=\"#e8edf0\"/><circle cx=\"12.5\" cy=\"-9.9\" r=\".8\" fill=\"#1a1f24\"/><path d=\"M 6 -13.4 q -2 2.6 0 5\" fill=\"#8f9294\"/><circle cx=\"8.4\" cy=\"-11.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -3.6 -6;26 -3.6 -6;-26 -3.6 -6\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M -3.6 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 6 -6;26 6 -6;-26 6 -6\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 6 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(0 0)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#c99a32\" stroke=\"#826420\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#8d5a3b\" stroke=\"#623e29\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"20 0.6 -16;-20 0.6 -16;20 0.6 -16\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#a07b28\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g>";
  var OM_FRONT = "<ellipse cx=\"0\" cy=\"0\" rx=\"6\" ry=\"1.4\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M -2.2 -7 V -.8 M 2.2 -7 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.5\" stroke-linecap=\"round\"/><ellipse cx=\"-2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><ellipse cx=\"2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><rect x=\"-4.6\" y=\"-9.6\" width=\"9.2\" height=\"3.4\" rx=\"1.4\" fill=\"#3b3a36\"/><rect x=\"-4.8\" y=\"-18.4\" width=\"9.6\" height=\"10\" rx=\"3\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><path d=\"M -4.4 -16 L -3.6 -9.4 M 4.4 -16 L 3.6 -9.4\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"6.2\" y1=\"-12\" x2=\"7.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><path d=\"M 4.4 -12.6 Q 6 -14.2 6.6 -12\" fill=\"none\" stroke=\"#8a6a40\" stroke-width=\"1.4\"/><circle cx=\"0\" cy=\"-21.6\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -3.4 -21 q -.6 2 .6 2.6 M 3.4 -21 q .6 2 -.6 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -3.7 -22.6 Q 0 -27.4 3.7 -22.6 Z\" fill=\"#4a535b\"/><ellipse cx=\"0\" cy=\"-22.6\" rx=\"4.6\" ry=\"1\" fill=\"#3b464e\"/><path d=\"M -1.4 -20 h 2.8\" stroke=\"#e8edf0\" stroke-width=\"1\" stroke-linecap=\"round\"/>";
  var OM_SIT = "<g><animate attributeName=\"opacity\" values=\"1;0;1\" keyTimes=\"0;.26;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\"0\" cy=\"0\" rx=\"6\" ry=\"1.4\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M -2.2 -7 V -.8 M 2.2 -7 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.5\" stroke-linecap=\"round\"/><ellipse cx=\"-2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><ellipse cx=\"2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><rect x=\"-4.6\" y=\"-9.6\" width=\"9.2\" height=\"3.4\" rx=\"1.4\" fill=\"#3b3a36\"/><rect x=\"-4.8\" y=\"-18.4\" width=\"9.6\" height=\"10\" rx=\"3\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><path d=\"M -4.4 -16 L -3.6 -9.4 M 4.4 -16 L 3.6 -9.4\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"6.2\" y1=\"-12\" x2=\"7.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><path d=\"M 4.4 -12.6 Q 6 -14.2 6.6 -12\" fill=\"none\" stroke=\"#8a6a40\" stroke-width=\"1.4\"/><circle cx=\"0\" cy=\"-21.6\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -3.4 -21 q -.6 2 .6 2.6 M 3.4 -21 q .6 2 -.6 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -3.7 -22.6 Q 0 -27.4 3.7 -22.6 Z\" fill=\"#4a535b\"/><ellipse cx=\"0\" cy=\"-22.6\" rx=\"4.6\" ry=\"1\" fill=\"#3b464e\"/><path d=\"M -1.4 -20 h 2.8\" stroke=\"#e8edf0\" stroke-width=\"1\" stroke-linecap=\"round\"/></g>";
  var OM_GO = "<g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0;1;0\" keyTimes=\"0;.26;.46;.745;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.6s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g transform=\"rotate(9 0 -9)\"><rect x=\"-3.5\" y=\"-18\" width=\"7\" height=\"9.8\" rx=\"2.8\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><circle cx=\".8\" cy=\"-21.4\" r=\"3.3\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -2.6 -21 q -.6 2 .8 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -2.6 -22.4 Q .6 -26.6 4 -22.6 Z\" fill=\"#4a535b\"/><path d=\"M 0 -22.6 H 5.6\" stroke=\"#3b464e\" stroke-width=\"1.2\" stroke-linecap=\"round\"/><circle cx=\"3\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"16 1.6 -15.2;-16 1.6 -15.2;16 1.6 -15.2\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M 1.6 -15.2 L 4.4 -10\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/></g><line x1=\"4.6\" y1=\"-10.4\" x2=\"7.6\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/></g>";
  var OM_FEED = "<g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0\" keyTimes=\"0;.46;.745\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><g transform=\"rotate(9 0 -9)\"><rect x=\"-3.5\" y=\"-18\" width=\"7\" height=\"9.8\" rx=\"2.8\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><circle cx=\".8\" cy=\"-21.4\" r=\"3.3\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -2.6 -21 q -.6 2 .8 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -2.6 -22.4 Q .6 -26.6 4 -22.6 Z\" fill=\"#4a535b\"/><path d=\"M 0 -22.6 H 5.6\" stroke=\"#3b464e\" stroke-width=\"1.2\" stroke-linecap=\"round\"/><circle cx=\"3\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/></g><path d=\"M 1.6 -15.2 L 7.2 -14\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"-1.6\" y1=\"-10\" x2=\"-3.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" repeatCount=\"indefinite\"/></circle><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/></circle><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.6s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.6s\" repeatCount=\"indefinite\"/></circle></g>";
  var OM_BACK = "<animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1;1 1\" keyTimes=\"0;.745;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/>";
  var OM_KEYS = "keyPoints=\"0;0;1;1;0;0\" keyTimes=\"0;.26;.46;.74;.94;1\" calcMode=\"linear\" dur=\"28s\" repeatCount=\"indefinite\"";
  var CAR_A = "<rect x=\"-10.5\" y=\"-4\" width=\"22\" height=\"10\" rx=\"3\" fill=\"rgba(0,0,0,.35)\"/><rect x=\"-11.5\" y=\"-5.5\" width=\"23\" height=\"11\" rx=\"3.4\" fill=\"";
  var CAR_B = "\" stroke=\"#1a1f24\" stroke-width=\".8\"/><rect x=\"-5\" y=\"-4.2\" width=\"9\" height=\"8.4\" rx=\"1.8\" fill=\"#fff\" opacity=\".16\"/><path d=\"M 4 -4 L 7 -3.4 L 7 3.4 L 4 4 Z\" fill=\"#20262b\"/><path d=\"M -5.5 -3.8 L -8 -3.2 L -8 3.2 L -5.5 3.8 Z\" fill=\"#20262b\" opacity=\".8\"/><rect x=\"10\" y=\"-4.4\" width=\"1.6\" height=\"2\" rx=\".6\" fill=\"#ffe2a8\"/><rect x=\"10\" y=\"2.4\" width=\"1.6\" height=\"2\" rx=\".6\" fill=\"#ffe2a8\"/><rect x=\"-11.6\" y=\"-4.4\" width=\"1.2\" height=\"2\" fill=\"#ff6b5e\"/><rect x=\"-11.6\" y=\"2.4\" width=\"1.2\" height=\"2\" fill=\"#ff6b5e\"/>";
  var TRUCK_A = "<rect x=\"-17\" y=\"-4.5\" width=\"36\" height=\"11\" rx=\"2\" fill=\"rgba(0,0,0,.35)\"/><rect x=\"-18.5\" y=\"-6\" width=\"26\" height=\"12\" rx=\"1.4\" fill=\"";
  var TRUCK_B = "\" stroke=\"#1a1f24\" stroke-width=\".8\"/><path d=\"M -16 -2 H 5 M -16 2 H 5\" stroke=\"#fff\" stroke-width=\".8\" opacity=\".25\"/><rect x=\"8.5\" y=\"-5.4\" width=\"10\" height=\"10.8\" rx=\"2.4\" fill=\"#3b464e\" stroke=\"#1a1f24\" stroke-width=\".8\"/><path d=\"M 14.5 -4.4 L 17 -3.8 L 17 3.8 L 14.5 4.4 Z\" fill=\"#20262b\"/><rect x=\"17.6\" y=\"-4.6\" width=\"1.4\" height=\"2\" fill=\"#ffe2a8\"/><rect x=\"17.6\" y=\"2.6\" width=\"1.4\" height=\"2\" fill=\"#ffe2a8\"/>";
  var BRIDGE = "<rect x=\"-30\" y=\"-19\" width=\"60\" height=\"38\" fill=\"rgba(0,0,0,.38)\" transform=\"translate(3 7)\"/><rect x=\"-35\" y=\"-21\" width=\"9\" height=\"42\" rx=\"1\" fill=\"#3b464e\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><rect x=\"26\" y=\"-21\" width=\"9\" height=\"42\" rx=\"1\" fill=\"#3b464e\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><rect x=\"-28\" y=\"-19\" width=\"56\" height=\"38\" rx=\"1.5\" fill=\"#4a535b\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><path d=\"M -28 -16.5 H 28 M -28 16.5 H 28\" stroke=\"#9aa7b0\" stroke-width=\"2.4\"/><path d=\"M -24 -16.5 v -2.4 M -12 -16.5 v -2.4 M 0 -16.5 v -2.4 M 12 -16.5 v -2.4 M 24 -16.5 v -2.4 M -24 16.5 v 2.4 M -12 16.5 v 2.4 M 0 16.5 v 2.4 M 12 16.5 v 2.4 M 24 16.5 v 2.4\" stroke=\"#c9d1d7\" stroke-width=\"1.6\"/>";
  var PK_CHECK = "<pattern id=\"pk-check\" width=\"10\" height=\"6\" patternUnits=\"userSpaceOnUse\" patternTransform=\"skewX(-28)\"><rect width=\"10\" height=\"6\" fill=\"#efe9df\"/><rect width=\"5\" height=\"3\" fill=\"#d9675b\"/><rect x=\"5\" y=\"3\" width=\"5\" height=\"3\" fill=\"#d9675b\"/></pattern>";

  function footprint(it) {
    var kind = it[0], x = it[1], y = it[2], s = it[3];
    if (PARK[kind]) { var w = PARK[kind][0], h = PARK[kind][1]; return [x - w / 2, y - h / 2, w, h]; }
    if (SOLID.indexOf(kind) < 0) return null;
    var r = kind === "pond" ? s / 2 : s * 0.4;
    return [x - r, y - r, 2 * r, 2 * r];
  }
  function hits(a, b) { return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3]; }
  function waterSpots(t) {
    var out = [];
    (t.items || []).forEach(function (it) { if (it[0] === "pond") out.push([it[1], it[2], it[3] / 2]); });
    (t.rivers || []).forEach(function (r) { r.forEach(function (p) { out.push([p[0], p[1], 13]); }); });
    ((t.tiles || {}).water || []).forEach(function (r) { for (var i = 0; i < r[2]; i++) out.push([(r[0] + i + 0.5) * TILE, (r[1] + 0.5) * TILE, TILE / 2]); });
    return out;
  }
  function oldMan(it, t, mirrored) {
    var x = it[1], y = it[2], w = PARK.bench[0], h = PARK.bench[1];
    var sx = mirrored ? x + w / 2 - 35 : x - w / 2 + 35, sy = y - h / 2 + 37, best = null;
    waterSpots(t).forEach(function (q) {
      var d = Math.sqrt((sx - q[0]) * (sx - q[0]) + (sy - q[1]) * (sy - q[1]));
      if (best === null || d - q[2] < best[0]) best = [d - q[2], q[0], q[1], q[2], d];
    });
    if (best === null || best[0] > 600 || best[0] < 12) return '<g transform="translate(35 37)">' + OM_FRONT + "</g>";
    var k = (best[3] + 8) / best[4], tx = best[1] + (sx - best[1]) * k, ty = best[2] + (sy - best[2]) * k;
    var lx = mirrored ? x + w / 2 - tx : tx - (x - w / 2), ly = ty - (y - h / 2), way = lx < 35 ? "scale(-1 1)" : "scale(1 1)";
    return '<g class="pk-man"><animateMotion path="M 35 37 L ' + f(lx) + " " + f(ly) + '" ' + OM_KEYS + "/>" + OM_SIT + "<g>" + OM_BACK + '<g transform="' + way + '">' + OM_GO + OM_FEED + "</g></g></g>";
  }
  function park(it, t) {
    var kind = it[0], x = it[1], y = it[2], v = it[4], w = PARK[kind][0], h = PARK[kind][1], mirrored = v % 2 === 1;
    var tf = mirrored ? "translate(" + f(x + w / 2) + " " + f(y - h / 2) + ") scale(-1 1)" : "translate(" + f(x - w / 2) + " " + f(y - h / 2) + ")";
    var body = {fetch: PK_FETCH, playground: PK_PLAYGROUND, picnic: PK_PICNIC, dogwalk: PK_DOGWALK}[kind];
    if (TOWN[kind]) body = '<g class="tw-art">' + TOWN[kind].svg + "</g>";
    if (kind === "bench") body = PK_BENCH + oldMan(it, t, mirrored);
    return '<g class="pk-' + kind + '" transform="' + tf + '">' + body + "</g>";
  }
  function lane(pts, o) {
    var segs = [], out = [];
    for (var i = 1; i < pts.length; i++) {
      var ax = pts[i - 1][0], ay = pts[i - 1][1], bx = pts[i][0], by = pts[i][1];
      var L = Math.sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay)) || 1;
      segs.push([-(by - ay) / L, (bx - ax) / L]);
    }
    pts.forEach(function (p, i) {
      var a = i > 0 ? segs[i - 1] : null, b = i < segs.length ? segs[i] : null, nx, ny;
      if (a && b) { var dot = a[0] * b[0] + a[1] * b[1]; nx = (a[0] + b[0]) / (1 + dot); ny = (a[1] + b[1]) / (1 + dot); }
      else { var n = a || b; nx = n[0]; ny = n[1]; }
      out.push([p[0] + nx * o, p[1] + ny * o]);
    });
    return out;
  }
  function traffic(roads, G) {
    var out = "";
    roads.forEach(function (pts, k) {
      var P = pts.map(function (p) { return [p[0] * G, p[1] * G]; }), L = 0;
      for (var i = 1; i < P.length; i++) L += Math.abs(P[i][0] - P[i - 1][0]) + Math.abs(P[i][1] - P[i - 1][1]);
      if (L < 80) return;
      var rand = rng(seedOf(k, pts[0][0], pts[0][1], pts.length));
      [P, P.slice().reverse()].forEach(function (way) {
        var d = "M " + lane(way, 4.5).map(function (q) { return f(q[0]) + " " + f(q[1]); }).join(" L ");
        var n = Math.max(1, Math.floor(L / 360 + rand() * 1.5));
        for (var j = 0; j < n; j++) {
          var dur = Math.max(2, Math.floor(L / (60 + rand() * 70))), begin = -Math.floor(rand() * dur), truck = rand() < 0.2;
          var col = CAR_COLOURS[Math.floor(rand() * CAR_COLOURS.length) % CAR_COLOURS.length];
          var art = truck ? TRUCK_A + col + TRUCK_B : CAR_A + col + CAR_B;
          out += '<g class="tf-car"><g transform="scale(.75)">' + art + '</g><animateMotion path="' + d + '" dur="' + dur + 's" begin="' + begin + 's" rotate="auto" repeatCount="indefinite"/>' +
            '<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.03;.97;1" dur="' + dur + 's" begin="' + begin + 's" repeatCount="indefinite"/></g>';
        }
      });
    });
    return out;
  }
  function bridges(roads, tracks, G) {
    var out = "", seen = {};
    (tracks || []).forEach(function (pts) {
      for (var i = 1; i < pts.length; i++) {
        var ax = pts[i - 1][0], ay = pts[i - 1][1], bx = pts[i][0], by = pts[i][1], vert = ax === bx, flat = ay === by;
        roads.forEach(function (road) {
          for (var j = 1; j < road.length; j++) {
            var rx0 = road[j - 1][0] * G, ry0 = road[j - 1][1] * G, rx1 = road[j][0] * G, ry1 = road[j][1] * G, x, y, ok;
            if (vert && ry0 === ry1) { x = ax; y = ry0; ok = Math.min(ay, by) + 14 < y && y < Math.max(ay, by) - 14 && Math.min(rx0, rx1) < x && x < Math.max(rx0, rx1); }
            else if (flat && rx0 === rx1) { x = rx0; y = ay; ok = Math.min(ax, bx) + 14 < x && x < Math.max(ax, bx) - 14 && Math.min(ry0, ry1) < y && y < Math.max(ry0, ry1); }
            else continue;
            if (ok && !seen[x + "," + y]) {
              seen[x + "," + y] = 1;
              out += '<g class="tf-bridge" transform="translate(' + f(x) + " " + f(y) + ") rotate(" + (vert ? 90 : 0) + ')">' + BRIDGE + "</g>";
            }
          }
        });
      }
    });
    return out;
  }
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
  function svg(t, G, W, H, walls, trees, layer, tracks) {
    t = t || {};
    var items = t.items || [], kinds = function (ks) { return items.filter(function (it) { return ks.indexOf(it[0]) >= 0; }); };
    var under = ground(t.tiles || {});
    under += (t.rivers || []).map(function (p) { return river(p); }).join("");
    under += kinds(["pond"]).map(function (it) { return pond(it[1], it[2], it[3], it[4]); }).join("");
    under += lines("road", t.roads || [], G) + hazards(t.hazards || [], G);
    under += kinds(["shade"]).map(function (it) { return shade(it[1], it[2], it[3], it[4]); }).join("");
    under += traffic(t.roads || [], G);
    var bridge = bridges(t.roads || [], tracks, G);
    var over = (walls || []).map(function (w) { return '<path class="fp-wall" d="M ' + w.map(function (p) { return p[0] * G + " " + p[1] * G; }).join(" L ") + '"/>'; }).join("");
    over += lines("fence", t.fences || [], G) + (t.gates || []).map(function (g) { return gate(g[0], g[1], g[2], G); }).join("");
    over += items.filter(function (it) { return PARK[it[0]]; }).map(function (it) { return park(it, t); }).join("");
    over += (trees || []).map(function (p) { return tree(p[0] * G, p[1] * G, 36, seedOf(p[0], p[1])); }).join("");
    over += kinds(["rock", "bush", "tree", "pine"]).map(function (it) { return ART[it[0]](it[1], it[2], it[3], it[4]); }).join("");
    over += ducks(t);
    var top = kinds(["lamp", "fog"]).map(function (it) { return ART[it[0]](it[1], it[2], it[3], it[4]); }).join("") + birds(t, W, H);
    var parts = {under: under, bridge: bridge, over: over, top: top};
    return layer && layer !== "all" ? parts[layer] : under + bridge + over + top;
  }
  window.FT = {svg: svg, f: f, rng: rng, seedOf: seedOf, sizeFor: sizeFor, runs: runs, cellsOf: cellsOf, bodies: bodies, footprint: footprint, hits: hits,
    TILE: TILE, SIZES: SIZES, GROUNDS: GROUNDS, PARK: PARK, PARK_NAMES: PARK_NAMES, SOLID: SOLID};
})();
