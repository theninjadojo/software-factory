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

The park is items too: fetch with a dog, a playground, a picnic, a bench with an old man who walks to the nearest water to feed the
ducks, and a dog walker. They are drawn side-on, standing on the floor like the buildings, centred on (x, y) at a fixed size; an odd
seed mirrors one. Every road gets traffic both ways, and where track crosses a road it goes over a bridge (cars pass under).
Buildings, the park and the solid scenery have hitboxes (footprint); what moves (cars, birds, ducks, drones, trains, ships) has none.

The same pictures are drawn by the floor (here) and by the editor (static/terrain.js, a line-by-line port): both come from one
seeded generator, a fixed table of directions and the same rounding, so they produce the same SVG, and a browser test checks that.
Birds and ducks follow the terrain: ducks on each body of water (at most two), birds land beside trees and bushes.
Everything is markup with classes; no style attributes, so the page policy holds."""

TILE = 40                                   # a ground tile: two grid cells
# the park: each piece's box, width by height; its size is its width
PARK = {"fetch": (200, 120), "playground": (180, 130), "picnic": (110, 90), "bench": (70, 40), "dogwalk": (180, 90)}
PARK_NAMES = {"fetch": "fetch with a dog", "playground": "playground", "picnic": "picnic", "bench": "bench", "dogwalk": "dog walker"}
KINDS = ("tree", "pine", "bush", "rock", "pond", "lamp", "fog", "shade") + tuple(PARK)
SIZES = {"tree": (30, 74), "pine": (28, 68), "bush": (16, 28), "rock": (24, 50), "pond": (68, 108), "lamp": (110, 150),
         "fog": (90, 170), "shade": (60, 120), **{k: (w, w) for k, (w, _) in PARK.items()}}
SOLID = ("tree", "pine", "bush", "rock", "pond") + tuple(PARK)    # what has a hitbox; light, fog and shade lie over or under anything
CAR_COLOURS = ("#d9675b", "#8fa8ff", "#e8edf0", "#f2a93b", "#52c7a1", "#59636b", "#c9a46a", "#b69cff")
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


# ---------------------------------------------------------------- the park, traffic and bridges (static/terrain.js holds the same strings)
PK_FETCH = "<path d=\"M 20 96 Q 110 100 196 94\" fill=\"none\" stroke=\"#25331f\" stroke-width=\"5\" stroke-linecap=\"round\" opacity=\".7\"/><g><ellipse cx=\"34\" cy=\"94\" rx=\"2.6\" ry=\".9\" fill=\"#000\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;70 0;138 0;147 0;147 0;121 0;1 0;0 0;0 0\" keyTimes=\"0;.06;.19;.33;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\".2;.2;.12;.6;.6;.6;.5;.5;.2;.2\" keyTimes=\"0;.06;.19;.33;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/></ellipse></g><g transform=\"translate(26 94)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#2f3a52\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#3f8fd0\" stroke=\"#285c87\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"30 .6 -16;110 .6 -16;-40 .6 -16;30 .6 -16;30 .6 -16;55 .6 -16;30 .6 -16;30 .6 -16\" keyTimes=\"0;.05;.08;.14;.84;.88;.92;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 7 -14\" fill=\"none\" stroke=\"#3272a6\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(50 94)\"><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;120 0;120 0;0 0;0 0\" keyTimes=\"0;.08;.4;.46;.8;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1;1 1\" keyTimes=\"0;.42;.84\" calcMode=\"discrete\" dur=\"5.6s\" repeatCount=\"indefinite\"/><g transform=\"translate(0 0)\"><ellipse cx=\"0\" cy=\"0\" rx=\"9\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -5 -6;26 -5 -6;-26 -5 -6\" dur=\"0.36s\" begin=\"-0.18s\" repeatCount=\"indefinite\"/><path d=\"M -5 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 4.5 -6;26 4.5 -6;-26 4.5 -6\" dur=\"0.36s\" begin=\"-0.18s\" repeatCount=\"indefinite\"/><path d=\"M 4.5 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-18 -7 -8;22 -7 -8;-18 -7 -8\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M -7 -8 q -4 -2 -5.5 -6.5\" fill=\"none\" stroke=\"#b07a45\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><ellipse cx=\"0\" cy=\"-7.2\" rx=\"7.6\" ry=\"3.6\" fill=\"#b07a45\" stroke=\"#6d4b2a\" stroke-width=\".6\"/><circle cx=\"7.4\" cy=\"-10.6\" r=\"3.3\" fill=\"#b07a45\" stroke=\"#6d4b2a\" stroke-width=\".6\"/><ellipse cx=\"10.4\" cy=\"-9.6\" rx=\"2.4\" ry=\"1.6\" fill=\"#b07a45\"/><circle cx=\"12.5\" cy=\"-9.9\" r=\".8\" fill=\"#1a1f24\"/><path d=\"M 6 -13.4 q -2 2.6 0 5\" fill=\"#6d4b2a\"/><circle cx=\"8.4\" cy=\"-11.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -3.6 -6;26 -3.6 -6;-26 -3.6 -6\" dur=\"0.36s\" repeatCount=\"indefinite\"/><path d=\"M -3.6 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 6 -6;26 6 -6;-26 6 -6\" dur=\"0.36s\" repeatCount=\"indefinite\"/><path d=\"M 6 -6 V -.6\" stroke=\"#6d4b2a\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g></g></g></g></g><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 0;14 -26;40 -46;70 -52;100 -42;124 -18;138 12;143 2;147 12;147 4;121 4;1 4;0 0;0 0\" keyTimes=\"0;.06;.09;.14;.19;.24;.29;.33;.36;.39;.42;.43;.8;.86;1\" dur=\"5.6s\" repeatCount=\"indefinite\"/><circle cx=\"34\" cy=\"78\" r=\"2.4\" fill=\"#d7e05a\" stroke=\"#6b7020\" stroke-width=\".8\"/></g>"
PK_PLAYGROUND = "<path d=\"M 4 126 L 176 126 L 166 98 L 14 98 Z\" fill=\"#8a7a52\" stroke=\"#5c4a30\" stroke-width=\"2\" stroke-linejoin=\"round\"/><path d=\"M 30 112 h2 M 92 118 h2 M 140 108 h2 M 60 104 h2 M 120 120 h2\" stroke=\"#6e6040\" stroke-width=\"2\" stroke-linecap=\"round\"/><path d=\"M 8 106 L 20 34 L 32 106 M 80 106 L 92 34 L 104 106\" fill=\"none\" stroke=\"#a8473d\" stroke-width=\"3\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 20 34 H 92\" stroke=\"#d9675b\" stroke-width=\"4\" stroke-linecap=\"round\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-30 42 34;30 42 34;-30 42 34\" calcMode=\"spline\" keyTimes=\"0;.5;1\" keySplines=\".45 0 .55 1;.45 0 .55 1\" dur=\"2.2s\" repeatCount=\"indefinite\"/><path d=\"M 37 34 V 78 M 47 34 V 78\" stroke=\"#9aa7b0\" stroke-width=\"1\" stroke-dasharray=\"2 1.5\"/><rect x=\"35\" y=\"78\" width=\"14\" height=\"3\" rx=\"1\" fill=\"#3b464e\"/><g transform=\"translate(40 83.5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#52c7a1\" stroke=\"#358168\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#419f80\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-30 70 34;30 70 34;-30 70 34\" calcMode=\"spline\" keyTimes=\"0;.5;1\" keySplines=\".45 0 .55 1;.45 0 .55 1\" dur=\"2.2s\" begin=\"-1.1s\" repeatCount=\"indefinite\"/><path d=\"M 65 34 V 78 M 75 34 V 78\" stroke=\"#9aa7b0\" stroke-width=\"1\" stroke-dasharray=\"2 1.5\"/><rect x=\"63\" y=\"78\" width=\"14\" height=\"3\" rx=\"1\" fill=\"#3b464e\"/><g transform=\"translate(68 83.5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#f2a93b\" stroke=\"#9d6d26\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#3a2a18\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#c1872f\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><path d=\"M 118 106 V 50 M 128 106 V 50\" stroke=\"#7d8890\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M 118 58 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 67 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 76 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 85 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 94 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><path d=\"M 118 103 H 128\" stroke=\"#7d8890\" stroke-width=\"1.6\"/><rect x=\"114\" y=\"46\" width=\"20\" height=\"4\" rx=\"1\" fill=\"#59636b\"/><path d=\"M 115 46 V 38 H 133 V 46\" fill=\"none\" stroke=\"#9aa7b0\" stroke-width=\"1.5\"/><path d=\"M 133 50 C 146 52 152 70 160 88 S 170 104 178 104\" fill=\"none\" stroke=\"#5f78c9\" stroke-width=\"8\" stroke-linecap=\"round\"/><path d=\"M 133 49 C 146 51 152 69 160 87 S 170 103 178 103\" fill=\"none\" stroke=\"#a6baff\" stroke-width=\"2.5\" stroke-linecap=\"round\" opacity=\".8\"/><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 -56;0 -56\" keyTimes=\"0;.46;1\" dur=\"4s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;.47\" calcMode=\"discrete\" dur=\"4s\" repeatCount=\"indefinite\"/><g transform=\"translate(123 106) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g><g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0\" keyTimes=\"0;.48;.85\" calcMode=\"discrete\" dur=\"4s\" repeatCount=\"indefinite\"/><g><animateMotion path=\"M 133 48 C 146 50 152 68 160 86 S 170 102 176 102\" keyPoints=\"0;0;1;1\" keyTimes=\"0;.5;.8;1\" calcMode=\"linear\" dur=\"4s\" repeatCount=\"indefinite\"/><g transform=\"translate(-2 5) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#5c3d22\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g><path d=\"M 72 120 L 82 120 L 81 112 L 73 112 Z\" fill=\"#f2a93b\" stroke=\"#9c6420\" stroke-width=\".8\"/><path d=\"M 73 112 Q 77 106 81 112\" fill=\"none\" stroke=\"#9c6420\" stroke-width=\".8\"/><g transform=\"translate(58 121) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#8fa8ff\" stroke=\"#5c6da5\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"0 0.6 -10;28 0.6 -10;0 0.6 -10\" dur=\"1.2s\" repeatCount=\"indefinite\"/><path d=\"M .6 -10 L 6 -6\" fill=\"none\" stroke=\"#7286cc\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(100 124)\"><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;56 0;56 0;0 0;0 0\" keyTimes=\"0;.46;.54;.96;1\" dur=\"7s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1\" keyTimes=\"0;.5\" calcMode=\"discrete\" dur=\"7s\" repeatCount=\"indefinite\"/><g transform=\"translate(0 0) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#d9675b\" stroke=\"#8d423b\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"20 0.6 -16;-20 0.6 -16;20 0.6 -16\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#ad5248\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g></g>"
PK_PICNIC = "<path d=\"M 6 84 L 90 84 L 104 58 L 20 58 Z\" fill=\"url(#pk-check)\" stroke=\"#9c4a40\" stroke-width=\"1.5\" stroke-linejoin=\"round\"/><g transform=\"translate(46 66) scale(0.72 0.72)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#62c497\" stroke=\"#3f7f62\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#9c7d62\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#3a2a18\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-20 0.6 -10.5;20 0.6 -10.5;-20 0.6 -10.5\" dur=\"1.4s\" repeatCount=\"indefinite\"/><path d=\"M .6 -10.5 L 4 -16\" fill=\"none\" stroke=\"#4e9c78\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><ellipse cx=\"34\" cy=\"76\" rx=\"6\" ry=\"2\" fill=\"#f4f1ea\" stroke=\"#9aa7b0\" stroke-width=\".6\"/><circle cx=\"34\" cy=\"75.4\" r=\"1.6\" fill=\"#d9675b\"/><ellipse cx=\"74\" cy=\"78\" rx=\"6\" ry=\"2\" fill=\"#f4f1ea\" stroke=\"#9aa7b0\" stroke-width=\".6\"/><ellipse cx=\"74\" cy=\"77.2\" rx=\"2.4\" ry=\"1\" fill=\"#62c497\"/><path d=\"M 50 74 L 66 74 L 64 64 L 52 64 Z\" fill=\"#8a6a40\" stroke=\"#3a2a18\" stroke-width=\".8\"/><path d=\"M 51 68 H 65 M 51.5 71 H 64.5\" stroke=\"#6b5233\" stroke-width=\".8\"/><path d=\"M 53 64 Q 58 55 63 64\" fill=\"none\" stroke=\"#5c4630\" stroke-width=\"1.6\"/><rect x=\"80\" y=\"64\" width=\"3.4\" height=\"11\" rx=\"1\" fill=\"#2f7a54\"/><rect x=\"80.6\" y=\"61.5\" width=\"2.2\" height=\"3\" fill=\"#c9d1d7\"/><ellipse cx=\"58\" cy=\"80\" rx=\"5\" ry=\"1.8\" fill=\"#e2b56b\"/><g transform=\"translate(16 84)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#e3788a\" stroke=\"#934e59\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#c68d5e\" stroke=\"#8a6241\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"0 .6 -14.5;0 .6 -14.5;28 .6 -14.5;28 .6 -14.5;0 .6 -14.5\" keyTimes=\"0;.35;.5;.62;.8\" dur=\"3.4s\" repeatCount=\"indefinite\"/><path d=\"M .6 -14.5 L 6.5 -10\" fill=\"none\" stroke=\"#b5606e\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g><g><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;0 -1.4;0 0\" keyTimes=\"0;.5;1\" dur=\"1.8s\" repeatCount=\"indefinite\"/><g transform=\"translate(99 83) scale(-1 1)\"><ellipse cx=\".5\" cy=\"0\" rx=\"7\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -.5 -7 H 5.2 V -.8\" fill=\"none\" stroke=\"#5c4630\" stroke-width=\"2.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><path d=\"M 5 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><rect x=\"-3.3\" y=\"-16.5\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#f2a93b\" stroke=\"#9d6d26\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-19.7\" r=\"3.4\" fill=\"#f1c9a5\" stroke=\"#a88c73\" stroke-width=\".5\"/><path d=\"M -3.1 -19.7 A 3.4 3.4 0 0 1 3.8 -20.3 Q 1.2 -21.9 -1 -20.1 L -1.4 -17.5 L -3.1 -18.3 Z\" fill=\"#c9a46a\"/><circle cx=\"2.6\" cy=\"-19.9\" r=\".5\" fill=\"#1a1f24\"/><g><path d=\"M .6 -14.5 L 1.4 -9\" fill=\"none\" stroke=\"#c1872f\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g>"
PK_BENCH = "<ellipse cx=\"35\" cy=\"38.5\" rx=\"31\" ry=\"2\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M 10 12 V 38 M 60 12 V 38\" stroke=\"#3b464e\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><rect x=\"7\" y=\"12\" width=\"56\" height=\"3.2\" rx=\"1\" fill=\"#9a7448\" stroke=\"#3a2a18\" stroke-width=\".6\"/><rect x=\"7\" y=\"17\" width=\"56\" height=\"3.2\" rx=\"1\" fill=\"#9a7448\" stroke=\"#3a2a18\" stroke-width=\".6\"/><rect x=\"5\" y=\"26\" width=\"60\" height=\"4\" rx=\"1\" fill=\"#b08655\" stroke=\"#3a2a18\" stroke-width=\".6\"/><path d=\"M 14 30 V 38 M 56 30 V 38\" stroke=\"#3b464e\" stroke-width=\"2.2\" stroke-linecap=\"round\"/>"
PK_DOGWALK = "<path d=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" fill=\"none\" stroke=\"#3a4631\" stroke-width=\"10\"/><path d=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" fill=\"none\" stroke=\"#4a5a3c\" stroke-width=\"1\" stroke-dasharray=\"3 6\"/><g><animateMotion path=\"M 20 52 A 70 30 0 1 1 160 52 A 70 30 0 1 1 20 52\" dur=\"26s\" repeatCount=\"indefinite\"/><g><animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1.12 1.12\" keyTimes=\"0;.5\" calcMode=\"discrete\" dur=\"26s\" repeatCount=\"indefinite\"/><path d=\"M 1.6 -9.6 Q 10 -4 21 -10\" fill=\"none\" stroke=\"#c9d1d7\" stroke-width=\".8\"/><g transform=\"translate(19 0) scale(0.82)\"><ellipse cx=\"0\" cy=\"0\" rx=\"9\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -5 -6;26 -5 -6;-26 -5 -6\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M -5 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 4.5 -6;26 4.5 -6;-26 4.5 -6\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 4.5 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-18 -7 -8;22 -7 -8;-18 -7 -8\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M -7 -8 q -4 -2 -5.5 -6.5\" fill=\"none\" stroke=\"#e8edf0\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><ellipse cx=\"0\" cy=\"-7.2\" rx=\"7.6\" ry=\"3.6\" fill=\"#e8edf0\" stroke=\"#8f9294\" stroke-width=\".6\"/><circle cx=\"7.4\" cy=\"-10.6\" r=\"3.3\" fill=\"#e8edf0\" stroke=\"#8f9294\" stroke-width=\".6\"/><ellipse cx=\"10.4\" cy=\"-9.6\" rx=\"2.4\" ry=\"1.6\" fill=\"#e8edf0\"/><circle cx=\"12.5\" cy=\"-9.9\" r=\".8\" fill=\"#1a1f24\"/><path d=\"M 6 -13.4 q -2 2.6 0 5\" fill=\"#8f9294\"/><circle cx=\"8.4\" cy=\"-11.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 -3.6 -6;26 -3.6 -6;-26 -3.6 -6\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M -3.6 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-26 6 -6;26 6 -6;-26 6 -6\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 6 -6 V -.6\" stroke=\"#8f9294\" stroke-width=\"1.9\" stroke-linecap=\"round\"/></g></g><g transform=\"translate(0 0)\"><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" begin=\"-0.25s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b4a6b\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><rect x=\"-3.3\" y=\"-18\" width=\"6.6\" height=\"9.6\" rx=\"2.8\" fill=\"#c99a32\" stroke=\"#826420\" stroke-width=\".6\"/><circle cx=\".4\" cy=\"-21.2\" r=\"3.4\" fill=\"#8d5a3b\" stroke=\"#623e29\" stroke-width=\".5\"/><path d=\"M -3.1 -21.2 A 3.4 3.4 0 0 1 3.8 -21.8 Q 1.2 -23.4 -1 -21.6 L -1.4 -19 L -3.1 -19.8 Z\" fill=\"#1a1f24\"/><circle cx=\"2.6\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"20 0.6 -16;-20 0.6 -16;20 0.6 -16\" dur=\"0.5s\" repeatCount=\"indefinite\"/><path d=\"M .6 -16 L 1.4 -10.5\" fill=\"none\" stroke=\"#a07b28\" stroke-width=\"2\" stroke-linecap=\"round\"/></g></g></g></g>"
OM_FRONT = "<ellipse cx=\"0\" cy=\"0\" rx=\"6\" ry=\"1.4\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M -2.2 -7 V -.8 M 2.2 -7 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.5\" stroke-linecap=\"round\"/><ellipse cx=\"-2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><ellipse cx=\"2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><rect x=\"-4.6\" y=\"-9.6\" width=\"9.2\" height=\"3.4\" rx=\"1.4\" fill=\"#3b3a36\"/><rect x=\"-4.8\" y=\"-18.4\" width=\"9.6\" height=\"10\" rx=\"3\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><path d=\"M -4.4 -16 L -3.6 -9.4 M 4.4 -16 L 3.6 -9.4\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"6.2\" y1=\"-12\" x2=\"7.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><path d=\"M 4.4 -12.6 Q 6 -14.2 6.6 -12\" fill=\"none\" stroke=\"#8a6a40\" stroke-width=\"1.4\"/><circle cx=\"0\" cy=\"-21.6\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -3.4 -21 q -.6 2 .6 2.6 M 3.4 -21 q .6 2 -.6 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -3.7 -22.6 Q 0 -27.4 3.7 -22.6 Z\" fill=\"#4a535b\"/><ellipse cx=\"0\" cy=\"-22.6\" rx=\"4.6\" ry=\"1\" fill=\"#3b464e\"/><path d=\"M -1.4 -20 h 2.8\" stroke=\"#e8edf0\" stroke-width=\"1\" stroke-linecap=\"round\"/>"
OM_SIT = "<g><animate attributeName=\"opacity\" values=\"1;0;1\" keyTimes=\"0;.26;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\"0\" cy=\"0\" rx=\"6\" ry=\"1.4\" fill=\"rgba(0,0,0,.3)\"/><path d=\"M -2.2 -7 V -.8 M 2.2 -7 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.5\" stroke-linecap=\"round\"/><ellipse cx=\"-2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><ellipse cx=\"2.4\" cy=\"-.5\" rx=\"1.9\" ry=\"1\" fill=\"#2a2f35\"/><rect x=\"-4.6\" y=\"-9.6\" width=\"9.2\" height=\"3.4\" rx=\"1.4\" fill=\"#3b3a36\"/><rect x=\"-4.8\" y=\"-18.4\" width=\"9.6\" height=\"10\" rx=\"3\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><path d=\"M -4.4 -16 L -3.6 -9.4 M 4.4 -16 L 3.6 -9.4\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"6.2\" y1=\"-12\" x2=\"7.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><path d=\"M 4.4 -12.6 Q 6 -14.2 6.6 -12\" fill=\"none\" stroke=\"#8a6a40\" stroke-width=\"1.4\"/><circle cx=\"0\" cy=\"-21.6\" r=\"3.4\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -3.4 -21 q -.6 2 .6 2.6 M 3.4 -21 q .6 2 -.6 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -3.7 -22.6 Q 0 -27.4 3.7 -22.6 Z\" fill=\"#4a535b\"/><ellipse cx=\"0\" cy=\"-22.6\" rx=\"4.6\" ry=\"1\" fill=\"#3b464e\"/><path d=\"M -1.4 -20 h 2.8\" stroke=\"#e8edf0\" stroke-width=\"1\" stroke-linecap=\"round\"/></g>"
OM_GO = "<g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0;1;0\" keyTimes=\"0;.26;.46;.745;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.6s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"-24 0 -9.5;24 0 -9.5;-24 0 -9.5\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M 0 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.6\" stroke-linecap=\"round\"/><path d=\"M -.2 -.6 h 2.4\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></g><g transform=\"rotate(9 0 -9)\"><rect x=\"-3.5\" y=\"-18\" width=\"7\" height=\"9.8\" rx=\"2.8\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><circle cx=\".8\" cy=\"-21.4\" r=\"3.3\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -2.6 -21 q -.6 2 .8 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -2.6 -22.4 Q .6 -26.6 4 -22.6 Z\" fill=\"#4a535b\"/><path d=\"M 0 -22.6 H 5.6\" stroke=\"#3b464e\" stroke-width=\"1.2\" stroke-linecap=\"round\"/><circle cx=\"3\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/></g><g><animateTransform attributeName=\"transform\" type=\"rotate\" values=\"16 1.6 -15.2;-16 1.6 -15.2;16 1.6 -15.2\" dur=\"0.6s\" repeatCount=\"indefinite\"/><path d=\"M 1.6 -15.2 L 4.4 -10\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/></g><line x1=\"4.6\" y1=\"-10.4\" x2=\"7.6\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/></g>"
OM_FEED = "<g opacity=\"0\"><animate attributeName=\"opacity\" values=\"0;1;0\" keyTimes=\"0;.46;.745\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/><ellipse cx=\".5\" cy=\"0\" rx=\"6\" ry=\"1.6\" fill=\"rgba(0,0,0,.34)\"/><path d=\"M -1 -9.5 V -.8 M 1.2 -9.5 V -.8\" stroke=\"#3b3a36\" stroke-width=\"2.4\" stroke-linecap=\"round\"/><path d=\"M -1.2 -.6 h 2.2 M 1 -.6 h 2.2\" stroke=\"#2a2f35\" stroke-width=\"1.8\" stroke-linecap=\"round\"/><g transform=\"rotate(9 0 -9)\"><rect x=\"-3.5\" y=\"-18\" width=\"7\" height=\"9.8\" rx=\"2.8\" fill=\"#6b5a48\" stroke=\"#3d3228\" stroke-width=\".6\"/><circle cx=\".8\" cy=\"-21.4\" r=\"3.3\" fill=\"#e0b48c\" stroke=\"#a87e58\" stroke-width=\".5\"/><path d=\"M -2.6 -21 q -.6 2 .8 2.6\" stroke=\"#d6d1c8\" stroke-width=\"1.4\" fill=\"none\"/><path d=\"M -2.6 -22.4 Q .6 -26.6 4 -22.6 Z\" fill=\"#4a535b\"/><path d=\"M 0 -22.6 H 5.6\" stroke=\"#3b464e\" stroke-width=\"1.2\" stroke-linecap=\"round\"/><circle cx=\"3\" cy=\"-21.4\" r=\".5\" fill=\"#1a1f24\"/></g><path d=\"M 1.6 -15.2 L 7.2 -14\" stroke=\"#5c4c3c\" stroke-width=\"2\" stroke-linecap=\"round\"/><line x1=\"-1.6\" y1=\"-10\" x2=\"-3.4\" y2=\"-.4\" stroke=\"#8a6a40\" stroke-width=\"1.4\" stroke-linecap=\"round\"/><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" repeatCount=\"indefinite\"/></circle><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.3s\" repeatCount=\"indefinite\"/></circle><circle cx=\"7.6\" cy=\"-14\" r=\".9\" fill=\"#e2c98f\"><animateTransform attributeName=\"transform\" type=\"translate\" values=\"0 0;8 14\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.6s\" repeatCount=\"indefinite\"/><animate attributeName=\"opacity\" values=\"1;0\" keyTimes=\"0;1\" dur=\"0.9s\" begin=\"-0.6s\" repeatCount=\"indefinite\"/></circle></g>"
OM_BACK = "<animateTransform attributeName=\"transform\" type=\"scale\" values=\"1 1;-1 1;1 1\" keyTimes=\"0;.745;.945\" calcMode=\"discrete\" dur=\"28s\" repeatCount=\"indefinite\"/>"
OM_KEYS = "keyPoints=\"0;0;1;1;0;0\" keyTimes=\"0;.26;.46;.74;.94;1\" calcMode=\"linear\" dur=\"28s\" repeatCount=\"indefinite\""
CAR_A = "<rect x=\"-10.5\" y=\"-4\" width=\"22\" height=\"10\" rx=\"3\" fill=\"rgba(0,0,0,.35)\"/><rect x=\"-11.5\" y=\"-5.5\" width=\"23\" height=\"11\" rx=\"3.4\" fill=\""
CAR_B = "\" stroke=\"#1a1f24\" stroke-width=\".8\"/><rect x=\"-5\" y=\"-4.2\" width=\"9\" height=\"8.4\" rx=\"1.8\" fill=\"#fff\" opacity=\".16\"/><path d=\"M 4 -4 L 7 -3.4 L 7 3.4 L 4 4 Z\" fill=\"#20262b\"/><path d=\"M -5.5 -3.8 L -8 -3.2 L -8 3.2 L -5.5 3.8 Z\" fill=\"#20262b\" opacity=\".8\"/><rect x=\"10\" y=\"-4.4\" width=\"1.6\" height=\"2\" rx=\".6\" fill=\"#ffe2a8\"/><rect x=\"10\" y=\"2.4\" width=\"1.6\" height=\"2\" rx=\".6\" fill=\"#ffe2a8\"/><rect x=\"-11.6\" y=\"-4.4\" width=\"1.2\" height=\"2\" fill=\"#ff6b5e\"/><rect x=\"-11.6\" y=\"2.4\" width=\"1.2\" height=\"2\" fill=\"#ff6b5e\"/>"
TRUCK_A = "<rect x=\"-17\" y=\"-4.5\" width=\"36\" height=\"11\" rx=\"2\" fill=\"rgba(0,0,0,.35)\"/><rect x=\"-18.5\" y=\"-6\" width=\"26\" height=\"12\" rx=\"1.4\" fill=\""
TRUCK_B = "\" stroke=\"#1a1f24\" stroke-width=\".8\"/><path d=\"M -16 -2 H 5 M -16 2 H 5\" stroke=\"#fff\" stroke-width=\".8\" opacity=\".25\"/><rect x=\"8.5\" y=\"-5.4\" width=\"10\" height=\"10.8\" rx=\"2.4\" fill=\"#3b464e\" stroke=\"#1a1f24\" stroke-width=\".8\"/><path d=\"M 14.5 -4.4 L 17 -3.8 L 17 3.8 L 14.5 4.4 Z\" fill=\"#20262b\"/><rect x=\"17.6\" y=\"-4.6\" width=\"1.4\" height=\"2\" fill=\"#ffe2a8\"/><rect x=\"17.6\" y=\"2.6\" width=\"1.4\" height=\"2\" fill=\"#ffe2a8\"/>"
BRIDGE = "<rect x=\"-30\" y=\"-19\" width=\"60\" height=\"38\" fill=\"rgba(0,0,0,.38)\" transform=\"translate(3 7)\"/><rect x=\"-35\" y=\"-21\" width=\"9\" height=\"42\" rx=\"1\" fill=\"#3b464e\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><rect x=\"26\" y=\"-21\" width=\"9\" height=\"42\" rx=\"1\" fill=\"#3b464e\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><rect x=\"-28\" y=\"-19\" width=\"56\" height=\"38\" rx=\"1.5\" fill=\"#4a535b\" stroke=\"#1d2328\" stroke-width=\"1.5\"/><path d=\"M -28 -16.5 H 28 M -28 16.5 H 28\" stroke=\"#9aa7b0\" stroke-width=\"2.4\"/><path d=\"M -24 -16.5 v -2.4 M -12 -16.5 v -2.4 M 0 -16.5 v -2.4 M 12 -16.5 v -2.4 M 24 -16.5 v -2.4 M -24 16.5 v 2.4 M -12 16.5 v 2.4 M 0 16.5 v 2.4 M 12 16.5 v 2.4 M 24 16.5 v 2.4\" stroke=\"#c9d1d7\" stroke-width=\"1.6\"/>"
PK_CHECK = "<pattern id=\"pk-check\" width=\"10\" height=\"6\" patternUnits=\"userSpaceOnUse\" patternTransform=\"skewX(-28)\"><rect width=\"10\" height=\"6\" fill=\"#efe9df\"/><rect width=\"5\" height=\"3\" fill=\"#d9675b\"/><rect x=\"5\" y=\"3\" width=\"5\" height=\"3\" fill=\"#d9675b\"/></pattern>"


def footprint(it) -> tuple | None:
    """An item's hitbox (x, y, w, h) in pixels, or None for what lies over or under anything (lamps, fog, shade)."""
    kind, x, y, s, _ = it
    if kind in PARK:
        w, h = PARK[kind]
        return (x - w / 2, y - h / 2, w, h)
    if kind not in SOLID:
        return None
    r = s / 2 if kind == "pond" else s * 0.4
    return (x - r, y - r, 2 * r, 2 * r)


def hits(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def water_spots(t: dict) -> list:
    """Where the water is, as circles (x, y, r): each pond, each point a river was dragged through, each painted water tile."""
    out = [(x, y, s / 2) for kind, x, y, s, v in t.get("items") or [] if kind == "pond"]
    out += [(p[0], p[1], 13) for r in t.get("rivers") or [] for p in r]
    out += [((c + i + 0.5) * TILE, (r + 0.5) * TILE, TILE / 2) for c, r, n in (t.get("tiles") or {}).get("water") or [] for i in range(n)]
    return out


def old_man(it, t: dict, mirrored: bool) -> str:
    """He sits on the bench (its seat at (35, 37) in the bench's box), walks to the nearest water, scatters crumbs for the ducks and
    walks back; with no water near he stays on his bench."""
    import math
    _, x, y, s, v = it
    w, h = PARK["bench"]
    sx, sy = (x + w / 2 - 35 if mirrored else x - w / 2 + 35), y - h / 2 + 37
    best = None
    for cx, cy, r in water_spots(t):
        d = math.sqrt((sx - cx) * (sx - cx) + (sy - cy) * (sy - cy))
        if best is None or d - r < best[0]:
            best = (d - r, cx, cy, r, d)
    if best is None or best[0] > 600 or best[0] < 12:
        return f'<g transform="translate(35 37)">{OM_FRONT}</g>'
    _, cx, cy, r, d = best
    k = (r + 8) / d
    tx, ty = cx + (sx - cx) * k, cy + (sy - cy) * k
    lx, ly = ((x + w / 2 - tx) if mirrored else (tx - (x - w / 2))), ty - (y - h / 2)
    way = "scale(-1 1)" if lx < 35 else "scale(1 1)"
    return (f'<g class="pk-man"><animateMotion path="M 35 37 L {f(lx)} {f(ly)}" {OM_KEYS}/>{OM_SIT}<g>{OM_BACK}<g transform="{way}">{OM_GO}{OM_FEED}</g></g></g>')


def park(it, t: dict) -> str:
    """A park piece in its box, centred on (x, y); an odd seed mirrors it."""
    kind, x, y, s, v = it
    w, h = PARK[kind]
    mirrored = v % 2 == 1
    tf = f"translate({f(x + w / 2)} {f(y - h / 2)}) scale(-1 1)" if mirrored else f"translate({f(x - w / 2)} {f(y - h / 2)})"
    body = {"fetch": PK_FETCH, "playground": PK_PLAYGROUND, "picnic": PK_PICNIC, "dogwalk": PK_DOGWALK}.get(kind)
    if kind == "bench":
        body = PK_BENCH + old_man(it, t, mirrored)
    return f'<g class="pk-{kind}" transform="{tf}">{body}</g>'


def lane(pts, o: float) -> list:
    """A road's centre line moved o pixels to one side (its runs are straight across or down, so the corners are simple)."""
    import math
    segs = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        L = math.sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay)) or 1
        segs.append((-(by - ay) / L, (bx - ax) / L))
    out = []
    for i, (x, y) in enumerate(pts):
        a, b = segs[i - 1] if i > 0 else None, segs[i] if i < len(segs) else None
        if a and b:
            dot = a[0] * b[0] + a[1] * b[1]
            nx, ny = (a[0] + b[0]) / (1 + dot), (a[1] + b[1]) / (1 + dot)
        else:
            nx, ny = a or b
        out.append((x + nx * o, y + ny * o))
    return out


def traffic(roads, G: int) -> str:
    """Cars and the odd truck both ways along every road, each at its own speed, from a seed of the road's place."""
    out = ""
    for k, pts in enumerate(roads):
        P = [(p[0] * G, p[1] * G) for p in pts]
        L = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(P, P[1:]))
        if L < 80:
            continue
        rand = rng(seed_of(k, pts[0][0], pts[0][1], len(pts)))
        for way in (P, list(reversed(P))):
            d = "M " + " L ".join(f"{f(x)} {f(y)}" for x, y in lane(way, 4.5))
            for _ in range(max(1, int(L / 360 + rand() * 1.5))):
                dur = max(2, int(L / (60 + rand() * 70)))
                begin = -int(rand() * dur)
                truck = rand() < 0.2
                col = CAR_COLOURS[int(rand() * len(CAR_COLOURS)) % len(CAR_COLOURS)]
                art = (TRUCK_A + col + TRUCK_B) if truck else (CAR_A + col + CAR_B)
                out += (f'<g class="tf-car"><g transform="scale(.75)">{art}</g><animateMotion path="{d}" dur="{dur}s" begin="{begin}s" rotate="auto" repeatCount="indefinite"/>'
                        f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.03;.97;1" dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/></g>')
    return out


def bridges(roads, tracks, G: int) -> str:
    """A bridge wherever track crosses a road on a straight run: a deck on two piers under the track; cars pass beneath it."""
    out, seen = "", set()
    for pts in tracks or ():
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            vert, flat = ax == bx, ay == by
            for road in roads:
                for p, q in zip(road, road[1:]):
                    rx0, ry0, rx1, ry1 = p[0] * G, p[1] * G, q[0] * G, q[1] * G
                    if vert and ry0 == ry1:
                        x, y = ax, ry0
                        ok = min(ay, by) + 14 < y < max(ay, by) - 14 and min(rx0, rx1) < x < max(rx0, rx1)
                    elif flat and rx0 == rx1:
                        x, y = rx0, ay
                        ok = min(ax, bx) + 14 < x < max(ax, bx) - 14 and min(ry0, ry1) < y < max(ry0, ry1)
                    else:
                        continue
                    if ok and (x, y) not in seen:
                        seen.add((x, y))
                        out += f'<g class="tf-bridge" transform="translate({f(x)} {f(y)}) rotate({90 if vert else 0})">{BRIDGE}</g>'
    return out


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
            + PK_CHECK +
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
def svg(t: dict, G: int, W: float, H: float, walls=(), trees=(), layer: str = "all", tracks=()) -> str:
    """The terrain in layers: "under" (ground, water, roads and their traffic, hazard zones, shade), "bridge" (a bridge wherever one of
    the tracks, lists of pixel points, crosses a road), "over" (walls, fences, gates, the park, trees, bushes, rocks, the ducks), "top"
    (lamp light, fog and the birds), or "all"."""
    t = t or {}
    items = t.get("items") or []
    under = ground(t.get("tiles") or {})
    under += "".join(river(p) for p in t.get("rivers") or [])
    under += "".join(pond(x, y, s, v) for kind, x, y, s, v in items if kind == "pond")
    under += lines("road", t.get("roads") or [], G) + hazards(t.get("hazards") or [], G)
    under += "".join(shade(x, y, s, v) for kind, x, y, s, v in items if kind == "shade")
    under += traffic(t.get("roads") or [], G)
    bridge = bridges(t.get("roads") or [], tracks, G)
    over = "".join(f'<path class="fp-wall" d="M {" L ".join(f"{p[0] * G} {p[1] * G}" for p in w)}"/>' for w in walls)
    over += lines("fence", t.get("fences") or [], G) + "".join(gate(x, y, way, G) for x, y, way in t.get("gates") or [])
    over += "".join(park(it, t) for it in items if it[0] in PARK)
    over += "".join(tree(x * G, y * G, 36, seed_of(x, y)) for x, y in trees)                      # the editor's older single trees
    over += "".join(ART[kind](x, y, s, v) for kind, x, y, s, v in items if kind in ("rock", "bush", "tree", "pine"))
    over += ducks(t)
    top = "".join(ART[kind](x, y, s, v) for kind, x, y, s, v in items if kind in ("lamp", "fog")) + birds(t, W, H)
    parts = {"under": under, "bridge": bridge, "over": over, "top": top}
    if layer != "all":
        return parts[layer]
    return under + bridge + over + top
