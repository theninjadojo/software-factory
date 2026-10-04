"""Terrain: what a person paints round the factory on a saved layout (the editor's Scenery, Water, Ground, Build and Light tools).

A layout's "terrain" holds, all in pixels of the floor unless said otherwise:
  items:   [kind, x, y, size, seed] for trees, pines, bushes, rocks, ponds, lamps, fog and shade (each rolls its own size and shape)
  rivers:  [[x, y], ...] each, the line a person dragged; the river widens and narrows along it
  tiles:   {kind: [[col, row, n], ...]} ground painted in tiles of TILE px, as runs of n tiles along a row (grass, dirt, sand,
           concrete, water); water tiles round off into one smooth body with a sandy shore
  fences, roads: [[x, y], ...] grid points (cells), straight runs across or down, like walls
  gates:   [x, y, "h" or "v"] a grid point in a fence gap
  hazards: [x, y, w, h] in cells: floor nobody should build on
Walls stay in the layout's "walls" (and old single trees in "trees"), as before.

The same pictures are drawn by the floor (here) and by the editor (static/terrain.js, a line-by-line port): both come from one
seeded generator, a fixed table of directions and the same rounding, so they produce the same SVG, and a browser test checks that.
Birds and ducks follow the terrain: ducks on each body of water (at most two), birds land beside trees and bushes.
Everything is markup with classes; no style attributes, so the page policy holds."""

TILE = 40                                   # a ground tile: two grid cells
KINDS = ("tree", "pine", "bush", "rock", "pond", "lamp", "fog", "shade")
SIZES = {"tree": (30, 74), "pine": (28, 68), "bush": (16, 28), "rock": (24, 50), "pond": (68, 108), "lamp": (110, 150),
         "fog": (90, 170), "shade": (60, 120)}
GROUNDS = ("grass", "dirt", "sand", "concrete", "water")
LIMITS = {"items": 600, "rivers": 12, "river_points": 160, "runs": 1500, "tiles": 6000, "fences": 80, "gates": 40, "roads": 80,
          "hazards": 40}
# 24 directions, 15 degrees apart, to four places: the same numbers in terrain.js, so both sides draw the same shapes
DIRS = ((1.0, 0.0), (0.9659, 0.2588), (0.866, 0.5), (0.7071, 0.7071), (0.5, 0.866), (0.2588, 0.9659), (0.0, 1.0), (-0.2588, 0.9659),
        (-0.5, 0.866), (-0.7071, 0.7071), (-0.866, 0.5), (-0.9659, 0.2588), (-1.0, 0.0), (-0.9659, -0.2588), (-0.866, -0.5),
        (-0.7071, -0.7071), (-0.5, -0.866), (-0.2588, -0.9659), (0.0, -1.0), (0.2588, -0.9659), (0.5, -0.866), (0.7071, -0.7071),
        (0.866, -0.5), (0.9659, -0.2588))
GREENS = (("#24452d", "#3a6b45", "#5e9a5c"), ("#21463f", "#336e5f", "#56a08a"), ("#33502a", "#4f7838", "#7da658"))


def f(v: float) -> str:
    """A number for SVG: rounded to one place (half up), without a trailing .0; the same as terrain.js's f."""
    import math
    r = math.floor(v * 10 + 0.5) / 10
    if r == 0:
        r = 0.0
    s = f"{r:.1f}"
    return s[:-2] if s.endswith(".0") else s


def rng(seed: int):
    """mulberry32: the same sequence as terrain.js's rng for the same seed."""
    a = [seed & 0xFFFFFFFF]
    m = 0xFFFFFFFF

    def nxt() -> float:
        a[0] = (a[0] + 0x6D2B79F5) & m
        x = a[0]
        t = ((x ^ (x >> 15)) * (1 | x)) & m
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & m)) & m) ^ t
        return ((t ^ (t >> 14)) & m) / 4294967296
    return nxt


def seed_of(*nums) -> int:
    """A seed from whole numbers (for what has none of its own: rivers, tiles, wildlife)."""
    h = 2166136261
    for n in nums:
        h = ((h ^ (int(n) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    return h


def size_for(kind: str, r: float) -> int:
    lo, hi = SIZES[kind]
    return int(lo + (hi - lo) * r)


def _blob(cx, cy, rad, rand, n=12, rough=0.15) -> str:
    """A smooth closed blob round (cx, cy): n points on a wobbly circle, joined by curves through their midpoints."""
    pts = []
    for i in range(n):
        dx, dy = DIRS[(i * 24 // n) % 24]
        k = rad * (1 - rough + rough * 2 * rand())
        pts.append((cx + dx * k, cy + dy * k))
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    m0 = mid(pts[-1], pts[0])
    d = f"M {f(m0[0])} {f(m0[1])}"
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        m = mid(p, q)
        d += f" Q {f(p[0])} {f(p[1])} {f(m[0])} {f(m[1])}"
    return d + " Z"


def tree(x, y, s, v) -> str:
    rand = rng(v)
    dark, mid, light = GREENS[int(rand() * 3) % 3]
    out = f'<g class="tr-tree"><ellipse class="tr-shadow" cx="{f(x + s * 0.08)}" cy="{f(y + s * 0.1)}" rx="{f(s * 0.46)}" ry="{f(s * 0.42)}"/>'
    n = 5 + int(rand() * 3)
    start = int(rand() * 24)
    for i in range(n):
        dx, dy = DIRS[(start + i * 24 // n) % 24]
        d = s * 0.2 * (0.75 + 0.3 * rand())
        r = s * 0.22 * (0.85 + 0.3 * rand())
        out += f'<circle cx="{f(x + dx * d)}" cy="{f(y + dy * d)}" r="{f(r)}" fill="{mid}" stroke="{dark}" stroke-width="1.5"/>'
    out += f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(s * 0.26)}" fill="{mid}"/>'
    out += f'<circle cx="{f(x - s * 0.07)}" cy="{f(y - s * 0.09)}" r="{f(s * 0.15)}" fill="{light}"/></g>'
    return out


def pine(x, y, s, v) -> str:
    rand = rng(v)
    dark, mid, light = GREENS[int(rand() * 3) % 3]
    out = f'<g class="tr-pine"><ellipse class="tr-shadow" cx="{f(x + s * 0.07)}" cy="{f(y + s * 0.09)}" rx="{f(s * 0.4)}" ry="{f(s * 0.36)}"/>'
    for layer, (scale, fill) in enumerate(((1.0, dark), (0.72, mid), (0.4, light))):
        start = int(rand() * 2)
        pts = []
        for i in range(24):
            dx, dy = DIRS[(i + start) % 24]
            k = (s / 2) * scale * ((0.82 + 0.18 * rand()) if i % 2 == 0 else (0.42 + 0.12 * rand()))
            pts.append(f"{f(x + dx * k)} {f(y + dy * k)}")
        out += f'<path d="M {" L ".join(pts)} Z" fill="{fill}"/>'
    return out + "</g>"


def bush(x, y, s, v) -> str:
    rand = rng(v)
    dark, mid, _ = GREENS[int(rand() * 3) % 3]
    out = f'<g class="tr-bush"><ellipse class="tr-shadow" cx="{f(x + s * 0.08)}" cy="{f(y + s * 0.1)}" rx="{f(s * 0.5)}" ry="{f(s * 0.44)}"/>'
    out += f'<path d="{_blob(x, y, s * 0.48, rand, 10, 0.22)}" fill="{mid}" stroke="{dark}" stroke-width="1.5"/>'
    for _ in range(3):
        dx, dy = DIRS[int(rand() * 24) % 24]
        k = s * 0.28 * rand()
        out += f'<circle class="tr-berry" cx="{f(x + dx * k)}" cy="{f(y + dy * k)}" r="{f(max(1.6, s * 0.08))}"/>'
    return out + "</g>"


def rock(x, y, s, v) -> str:
    rand = rng(v)
    pts = []
    for i in range(7):
        dx, dy = DIRS[(i * 24 // 7) % 24]
        k = s * 0.3 * (0.8 + 0.4 * rand())
        pts.append(f"{f(x + dx * k)} {f(y + dy * k)}")
    out = f'<g class="tr-rock"><ellipse class="tr-shadow" cx="{f(x + s * 0.06)}" cy="{f(y + s * 0.08)}" rx="{f(s * 0.34)}" ry="{f(s * 0.3)}"/>'
    out += f'<path class="rk-main" d="M {" L ".join(pts)} Z"/>'
    out += f'<path class="rk-lit" d="M {f(x - s * 0.18)} {f(y - s * 0.05)} L {f(x - s * 0.05)} {f(y - s * 0.2)} L {f(x + s * 0.1)} {f(y - s * 0.16)}"/>'
    for _ in range(1 + int(rand() * 2)):
        dx, dy = DIRS[int(rand() * 24) % 24]
        out += f'<circle class="rk-pebble" cx="{f(x + dx * s * 0.42)}" cy="{f(y + dy * s * 0.42)}" r="{f(s * (0.08 + 0.06 * rand()))}"/>'
    return out + "</g>"


def pond(x, y, s, v) -> str:
    rand = rng(v)
    shape = _blob(x, y, s / 2, rand, 12, 0.13)
    rand2 = rng(v)                                                   # the shore follows the same wobble, a little wider
    shore = _blob(x, y, s / 2 + 6, rand2, 12, 0.13)
    out = f'<g class="tr-pond"><path class="wt-sand" d="{shore}"/><path class="wt-water" d="{shape}"/>'
    for k in (-1, 1):
        out += f'<path class="wt-ripple" d="M {f(x + k * s * 0.12 - 6)} {f(y + k * s * 0.08)} q 6 -5 12 0"/>'
    return out + "</g>"


def lamp(x, y, s, v) -> str:
    return (f'<g class="lt-lamp"><circle class="lt-pool" cx="{f(x)}" cy="{f(y)}" r="{f(s / 2)}"/>'
            f'<circle class="lt-post" cx="{f(x)}" cy="{f(y)}" r="3"/></g>')


def fog(x, y, s, v) -> str:
    return f'<ellipse class="lt-fog" cx="{f(x)}" cy="{f(y)}" rx="{f(s / 2)}" ry="{f(s / 4)}"/>'


def shade(x, y, s, v) -> str:
    return f'<ellipse class="lt-shade" cx="{f(x)}" cy="{f(y)}" rx="{f(s / 2)}" ry="{f(s * 0.32)}"/>'


ART = {"tree": tree, "pine": pine, "bush": bush, "rock": rock, "pond": pond, "lamp": lamp, "fog": fog, "shade": shade}


def smooth(pts, rounds: int = 2) -> list:
    """Chaikin's corner cutting: the dragged line as a smooth curve (arithmetic only, so terrain.js gets the same points)."""
    for _ in range(rounds):
        if len(pts) < 3:
            return pts
        out = [pts[0]]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            out += [(ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25), (ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75)]
        pts = out + [pts[-1]]
    return pts


def river(pts, seed=None) -> str:
    """A river along the dragged points, smoothed into curves: a band that widens and narrows, a sandy bank and a shimmer down
    the middle."""
    import math
    if len(pts) < 2:
        return ""
    rand = rng(seed if seed is not None else seed_of(pts[0][0], pts[0][1], len(pts)))
    pts = smooth([tuple(p) for p in pts])
    widths = [11 + 6 * rand() for _ in pts]
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.sqrt(dx * dx + dy * dy) or 1
        nx, ny = -dy / n, dx / n
        left.append((x + nx * widths[i], y + ny * widths[i]))
        right.append((x - nx * widths[i], y - ny * widths[i]))
    water = "M " + " L ".join(f"{f(x)} {f(y)}" for x, y in left + list(reversed(right))) + " Z"
    line = "M " + " L ".join(f"{f(x)} {f(y)}" for x, y in pts)
    return (f'<g class="tr-river"><path class="wt-bank" d="{line}"/><path class="wt-water" d="{water}"/>'
            f'<path class="wt-shimmer" d="{line}"/></g>')


def runs(tiles: dict) -> dict:
    """{kind: {(col, row)}} -> {kind: [[col, row, n], ...]}: painted tiles as runs along each row."""
    out = {}
    for kind in GROUNDS:
        cells = sorted(tiles.get(kind, ()), key=lambda c: (c[1], c[0]))
        rs = []
        for c, r in cells:
            if rs and rs[-1][1] == r and rs[-1][0] + rs[-1][2] == c:
                rs[-1][2] += 1
            else:
                rs.append([c, r, 1])
        if rs:
            out[kind] = rs
    return out


def cells_of(runs_: dict) -> dict:
    return {kind: {(c + i, r) for c, r, n in rs for i in range(n)} for kind, rs in runs_.items()}


def ground(tiles: dict) -> str:
    """Painted ground, in its patterns; water tiles as one rounded body with a sandy shore."""
    out = ""
    for kind in ("grass", "dirt", "sand", "concrete"):
        for c, r, n in tiles.get(kind, ()):
            out += f'<rect class="gd-{kind}" x="{c * TILE}" y="{r * TILE}" width="{n * TILE}" height="{TILE}"/>'
    water = tiles.get("water") or []
    if water:
        out += "".join(f'<rect class="wt-sand" x="{c * TILE - 6}" y="{r * TILE - 6}" width="{n * TILE + 12}" height="{TILE + 12}" rx="14"/>'
                       for c, r, n in water)
        out += "".join(f'<rect class="wt-water" x="{c * TILE - 1}" y="{r * TILE - 1}" width="{n * TILE + 2}" height="{TILE + 2}" rx="12"/>'
                       for c, r, n in water)
        out += "".join(f'<path class="wt-ripple" d="M {c * TILE + 10 + 40 * i} {r * TILE + 20} q 5 -4 10 0 t 10 0"/>'
                       for c, r, n in water for i in range(n) if (c + i + r) % 3 == 0)
    return f'<g class="tr-ground">{out}</g>' if out else ""


def lines(kind: str, pts_list, G: int) -> str:
    """Fences (a rail with posts every cell) and roads (a dashed centre line), on grid points."""
    out = ""
    for pts in pts_list:
        d = "M " + " L ".join(f"{p[0] * G} {p[1] * G}" for p in pts)
        if kind == "road":
            out += f'<path class="rd-road" d="{d}"/><path class="rd-line" d="{d}"/>'
        else:
            out += f'<path class="fc-rail" d="{d}"/>'
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                n = abs(x2 - x1) + abs(y2 - y1)
                sx, sy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
                out += "".join(f'<circle class="fc-post" cx="{(x1 + sx * k) * G}" cy="{(y1 + sy * k) * G}" r="2.6"/>' for k in range(n + 1))
    return out


def gate(x, y, way, G) -> str:
    """A gate at grid point (x, y) in a fence gap, swinging open and shut on a loop."""
    px, py = x * G, y * G
    ex, ey = (px + G, py) if way == "h" else (px, py + G)
    return (f'<g class="fc-gate"><circle class="fc-post" cx="{px}" cy="{py}" r="3"/><circle class="fc-post" cx="{ex + (G if way == "h" else 0)}" '
            f'cy="{ey + (G if way == "v" else 0)}" r="3"/><line class="fc-leaf" x1="{px}" y1="{py}" x2="{ex}" y2="{ey}">'
            f'<animateTransform attributeName="transform" type="rotate" values="0 {px} {py};0 {px} {py};-70 {px} {py};-70 {px} {py};0 {px} {py}" '
            f'keyTimes="0;.3;.45;.8;1" dur="6s" repeatCount="indefinite"/></line></g>')


def hazards(rects, G) -> str:
    return "".join(f'<rect class="hz-zone" x="{x * G}" y="{y * G}" width="{w * G}" height="{h * G}"/>' for x, y, w, h in rects)


def defs() -> str:
    """Patterns and gradients the terrain classes use: once per page."""
    return ('<defs><pattern id="tl-grass" width="20" height="20" patternUnits="userSpaceOnUse"><rect width="20" height="20" fill="#1d3624"/>'
            '<path d="M5 8 l1 -3 l1 3 M13 15 l1 -3 l1 3" stroke="#3f6b45" stroke-width="1.2" fill="none"/></pattern>'
            '<pattern id="tl-dirt" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="#3a2a1b"/>'
            '<circle cx="4" cy="5" r="1" fill="#5a4330"/><circle cx="12" cy="11" r="1" fill="#2a1d12"/></pattern>'
            '<pattern id="tl-sand" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="#5b5132"/>'
            '<circle cx="5" cy="4" r=".9" fill="#7a6d44"/><circle cx="11" cy="12" r=".9" fill="#3e3722"/></pattern>'
            '<pattern id="tl-concrete" width="40" height="40" patternUnits="userSpaceOnUse"><rect width="40" height="40" fill="#30363b"/>'
            '<path d="M0 0 H40 V40" stroke="#22272b" stroke-width="2" fill="none"/></pattern>'
            '<pattern id="tl-hazard" width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            '<rect width="16" height="16" fill="#15181b"/><rect width="8" height="16" fill="#8a6b22"/></pattern>'
            '<radialGradient id="lg-lamp"><stop offset="0" stop-color="#f2c46b" stop-opacity=".42"/><stop offset="1" stop-color="#f2c46b" stop-opacity="0"/></radialGradient>'
            '<radialGradient id="lg-fog"><stop offset="0" stop-color="#c9d6dd" stop-opacity=".38"/><stop offset="1" stop-color="#c9d6dd" stop-opacity="0"/></radialGradient>'
            '<radialGradient id="lg-shade"><stop offset="0" stop-color="#000" stop-opacity=".55"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>'
            '</defs>')


# ---------------------------------------------------------------- wildlife
DUCK = ('<ellipse class="dk-body" cx="0" cy="0" rx="9" ry="5.5"/><circle class="dk-head" cx="8" cy="0" r="3.8"/>'
        '<path class="dk-bill" d="M 11 -1.5 L 15 0 L 11 1.5 Z"/>')
HEN = ('<ellipse class="dk-hen" cx="0" cy="0" rx="9" ry="5.5"/><circle class="dk-henhead" cx="8" cy="0" r="3.6"/>'
       '<path class="dk-bill" d="M 11 -1.5 L 15 0 L 11 1.5 Z"/>')
BIRD = ('<path class="bd-wing" d="M -2 0 L -5 -9 L 1 -9 L 3 0 L 1 9 L -5 9 Z"/><ellipse class="bd-body" cx="0" cy="0" rx="6" ry="3.4"/>'
        '<circle class="bd-head" cx="5" cy="0" r="2.6"/><path class="bd-bill" d="M 7 -1 L 10 0 L 7 1 Z"/><path class="bd-tail" d="M -6 0 L -10 -3 L -10 3 Z"/>')


def bodies(t: dict) -> list:
    """Each body of water as (kind, size, loop path for a duck): ponds, rivers, and each patch of joined water tiles."""
    out = []
    for kind, x, y, s, v in t.get("items") or []:
        if kind == "pond":
            r = s * 0.22
            out.append(("pond", s, f"M {f(x - r)} {f(y)} A {f(r)} {f(r * 0.7)} 0 1 1 {f(x + r)} {f(y)} A {f(r)} {f(r * 0.7)} 0 1 1 {f(x - r)} {f(y)}"))
    for pts in t.get("rivers") or []:
        if len(pts) >= 3:
            a, b = len(pts) // 4, max(len(pts) // 4 + 1, (3 * len(pts)) // 4)
            seg = pts[a:b + 1]
            d = "M " + " L ".join(f"{f(x)} {f(y)}" for x, y in seg + list(reversed(seg[:-1])))
            out.append(("river", len(pts) * 20, d))
    water = cells_of({"water": (t.get("tiles") or {}).get("water") or []}).get("water", set())
    seen = set()
    for c in sorted(water, key=lambda c: (c[1], c[0])):
        if c in seen:
            continue
        patch, todo = [], [c]
        seen.add(c)
        while todo:
            p = todo.pop()
            patch.append(p)
            for q in ((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)):
                if q in water and q not in seen:
                    seen.add(q)
                    todo.append(q)
        if len(patch) >= 3:
            patch.sort(key=lambda c: (c[1], c[0]))
            pts = [((cc + 0.5) * TILE, (rr + 0.5) * TILE) for cc, rr in patch[: max(2, min(len(patch), 8))]]
            d = "M " + " L ".join(f"{f(x)} {f(y)}" for x, y in pts + list(reversed(pts[:-1])))
            out.append(("tiles", len(patch) * TILE, d))
    return out


def ducks(t: dict) -> str:
    """Up to two ducks on each body of water, paddling round and resting, leaving a ripple where they rest."""
    out = ""
    for k, (kind, size, d) in enumerate(bodies(t)):
        n = 1 if size < (90 if kind == "pond" else 200) else 2
        for j in range(n):
            rand = rng(seed_of(k, j, size))
            dur = 16 + int(rand() * 10)
            begin = -int(rand() * dur) - j * 5
            art = DUCK if j == 0 else HEN
            out += (f'<g class="dk-duck">{art}<animateMotion path="{d}" dur="{dur}s" begin="{begin}s" rotate="auto" calcMode="linear" '
                    f'keyPoints="0;.45;.45;1" keyTimes="0;.5;.7;1" repeatCount="indefinite"/></g>'
                    f'<circle class="dk-ripple" r="4"><animateMotion path="{d}" dur="{dur}s" begin="{begin}s" calcMode="linear" '
                    f'keyPoints="0;.45;.45;1" keyTimes="0;.5;.7;1" repeatCount="indefinite"/>'
                    f'<animate attributeName="r" values="3;3;14;3" keyTimes="0;.5;.7;1" dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/>'
                    f'<animate attributeName="opacity" values="0;0;.6;0" keyTimes="0;.5;.6;.7" dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/></circle>')
    return out


def birds(t: dict, W: float, H: float) -> str:
    """A few birds (one per tree or bush, at most three) fly in from beyond the edge, land beside it, rest and take off again;
    their shadow closes up under them as they touch down."""
    perch = [(x, y, s) for kind, x, y, s, v in t.get("items") or [] if kind in ("tree", "bush")][:3]
    out = ""
    for k, (x, y, s) in enumerate(perch):
        rand = rng(seed_of(x, y, k))
        side = int(rand() * 4)
        start = ((-40, y - 120), (W + 40, y - 80), (x - 140, -40), (x + 160, H + 40))[side]
        end = ((W + 40, y - 160), (-40, y + 60), (x + 180, H + 40), (x - 120, -40))[side]
        lx, ly = x + s * 0.55 + 6, y + s * 0.15
        dur = 18 + int(rand() * 8)
        begin = -int(rand() * dur)
        path = f"M {f(start[0])} {f(start[1])} Q {f((start[0] + lx) / 2)} {f(start[1] - 60)} {f(lx)} {f(ly)} Q {f((lx + end[0]) / 2)} {f(ly - 80)} {f(end[0])} {f(end[1])}"
        motion = (f'<animateMotion path="{path}" dur="{dur}s" begin="{begin}s" rotate="auto" calcMode="linear" '
                  f'keyPoints="0;.5;.5;1;1" keyTimes="0;.3;.62;.9;1" repeatCount="indefinite"/>')
        out += (f'<g class="bd-shadow-wrap"><g class="bd-shadow"><ellipse cx="0" cy="0" rx="6" ry="3"/>'
                f'<animateTransform attributeName="transform" type="translate" values="16 22;2 3;2 3;16 22;16 22" keyTimes="0;.3;.62;.9;1" '
                f'dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/></g>{motion}</g>'
                f'<g class="bd-bird">{BIRD}{motion}<animate attributeName="opacity" values="1;1;0" keyTimes="0;.9;1" calcMode="discrete" '
                f'dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/></g>')
    return out


# ---------------------------------------------------------------- the whole terrain
def svg(t: dict, G: int, W: float, H: float, walls=(), trees=(), layer: str = "all") -> str:
    """The terrain in layers: "under" (ground, water, roads, hazard zones, shade), "over" (walls, fences, gates, trees, bushes,
    rocks, the ducks), "top" (lamp light, fog and the birds), or "all"."""
    t = t or {}
    items = t.get("items") or []
    under = ground(t.get("tiles") or {})
    under += "".join(river(p) for p in t.get("rivers") or [])
    under += "".join(pond(x, y, s, v) for kind, x, y, s, v in items if kind == "pond")
    under += lines("road", t.get("roads") or [], G) + hazards(t.get("hazards") or [], G)
    under += "".join(shade(x, y, s, v) for kind, x, y, s, v in items if kind == "shade")
    over = "".join(f'<path class="fp-wall" d="M {" L ".join(f"{p[0] * G} {p[1] * G}" for p in w)}"/>' for w in walls)
    over += lines("fence", t.get("fences") or [], G) + "".join(gate(x, y, way, G) for x, y, way in t.get("gates") or [])
    over += "".join(tree(x * G, y * G, 36, seed_of(x, y)) for x, y in trees)                      # the editor's older single trees
    over += "".join(ART[kind](x, y, s, v) for kind, x, y, s, v in items if kind in ("rock", "bush", "tree", "pine"))
    over += ducks(t)
    top = "".join(ART[kind](x, y, s, v) for kind, x, y, s, v in items if kind in ("lamp", "fog")) + birds(t, W, H)
    parts = {"under": under, "over": over, "top": top}
    if layer != "all":
        return parts[layer]
    return under + over + top
