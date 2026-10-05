"""The default floor's scenery: parks, ponds, a lake, forests, roads, a fence and a hazard zone laid round the buildings.

default_plan (floorplan.py) puts the stations in a few islands with open ground between them; this fills that ground. Nothing is
random in the way that matters: a fixed seed and a fixed order of places, so the same buildings always get the same scenery, and it
is laid only where nothing else is, so the layout's terrain rules hold (water never touches a belt or track, parks and hazard zones
stay clear of buildings, a road crosses only track (over a bridge) and never a belt). It all stays inside the floor's present
bounds, so the floor does not grow."""
import math
import random

from . import terrain as T

G = 20
SEED = 20261005


class Ground:
    """What is taken, as grid cells: buildings, belts, track, districts, the roads laid so far and everything placed on the ground."""

    def __init__(self, boxes: dict, belts: dict, tracks: dict, districts: dict, cells, area):
        self.nodes = boxes
        self.taken: set = set()
        self.belt: set = set()
        self.rail: set = set()
        for nid, (x, y, w, h) in boxes.items():
            m = 3 if nid.split(":")[0] in ("yard", "depot", "worker", "junction") else 1
            self.block((x - m, y - m, w + 2 * m, h + 2 * m))
        for d in (districts or {}).values():
            self.block((d["x"], d["y"], d["w"], d["h"]))
        for pts in belts.values():
            self.belt |= {(cx + dx, cy + dy) for cx, cy in cells(pts) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        for pts in tracks.values():
            self.rail |= {(cx + dx, cy + dy) for cx, cy in cells(pts) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)}
        self.x0, self.y0, self.x1, self.y1 = area
        self.px: list = []                                       # footprints of what was placed, in pixels

    def block(self, r) -> None:
        x, y, w, h = r
        self.taken |= {(i, j) for i in range(int(x), int(x + w) + 1) for j in range(int(y), int(y + h) + 1)}

    def free(self, x, y, w, h, m=0, rail_ok=False) -> bool:
        """Is the cell rectangle (x, y, w, h), grown by m cells, clear of everything and inside the floor?"""
        if x - m < self.x0 or y - m < self.y0 or x + w + m > self.x1 or y + h + m > self.y1:
            return False
        cs = {(i, j) for i in range(x - m, x + w + m + 1) for j in range(y - m, y + h + m + 1)}
        return not (cs & self.taken or cs & self.belt or (not rail_ok and cs & self.rail))

    def clear_px(self, fp, m=6) -> bool:
        """Is the pixel hitbox fp clear of what is already placed (and of the cells taken)?"""
        a = (fp[0] - m, fp[1] - m, fp[2] + 2 * m, fp[3] + 2 * m)
        if any(T.hits(a, o) for o in self.px):
            return False
        return self.free(int(a[0] // G), int(a[1] // G), int(a[2] // G) + 1, int(a[3] // G) + 1)

    def put(self, fp) -> None:
        self.px.append(fp)

    def find(self, w, h, near, step=2, m=1, avoid=(), apart=0, rail_ok=False):
        """The free cell rectangle w by h nearest to the cell `near`, even-aligned (a ground tile is two cells), or None."""
        best = None
        for y in range(self.y0 + 2, self.y1 - h, step):
            for x in range(self.x0 + 2, self.x1 - w, step):
                if best and (x - near[0]) ** 2 + (y - near[1]) ** 2 >= best[0]:
                    continue
                if any(abs(x - a[0]) < apart and abs(y - a[1]) < apart for a in avoid):
                    continue
                if self.free(x, y, w, h, m, rail_ok):
                    best = ((x - near[0]) ** 2 + (y - near[1]) ** 2, x, y)
        return (best[1], best[2]) if best else None


def _tiles(rects) -> dict:
    """Ground runs ([column, row, count] per row of tiles) for cell rectangles that start on even cells."""
    cs = set()
    for x, y, w, h in rects:
        cs |= {(i, j) for i in range(x // 2, (x + w) // 2) for j in range(y // 2, (y + h) // 2)}
    return T.runs({"grass": cs})


def build(boxes: dict, belts: dict, tracks: dict, districts: dict, cells) -> dict:
    """The default terrain for these buildings, belts and track, in the layout's terrain shape (terrain.py)."""
    rnd = random.Random(SEED)
    inland = [b for k, b in boxes.items() if k not in ("mainland", "sea")]
    gx = min(b[0] for b in inland) - 1
    area = (max(gx, 29), 0, max(b[0] + b[2] for b in boxes.values() if b is not boxes.get("sea")) - 2, max(b[1] + b[3] for b in inland) - 1)
    g = Ground(boxes, belts, tracks, districts, cells, area)
    items, tiles, fences, gates, roads, hazards = [], [], [], [], [], []

    def thing(kind, x, y, size=None, seed=None):
        s = size or rnd.randint(*T.SIZES[kind])
        it = [kind, int(x), int(y), int(s), seed if seed is not None else rnd.randrange(1, 0xFFFFFFFF)]
        fp = T.footprint(it)
        if fp:
            g.put(fp)
        items.append(it)

    # roads first: straight runs that cross only track (a bridge there) and never a belt or a building
    def road_ok(pts):
        cs = cells(pts)
        return all((c[0], c[1]) not in g.belt and not any((c[0] + dx, c[1] + dy) in g.taken for dx in (-1, 0, 1) for dy in (-1, 0, 1)) for c in cs)

    for vertical in (True, False):
        best = None
        span = range(area[0] + 6, area[2] - 6) if vertical else range(area[1] + 4, area[3] - 3)
        for c in span:
            run = None
            for a in range(area[1] + 1 if vertical else area[0] + 1, (area[3] if vertical else area[2]) - 1):
                pt = (c, a) if vertical else (a, c)
                if road_ok([pt, pt]):
                    run = run or [a, a]
                    run[1] = a
                    if best is None or run[1] - run[0] > best[0]:
                        best = (run[1] - run[0], c, run[0], run[1])
                else:
                    run = None
        if best and best[0] >= 14:
            _, c, a0, a1 = best
            pts = [[c, a0], [c, a1]] if vertical else [[a0, c], [a1, c]]
            roads.append(pts)
            for p in cells(pts):
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        g.taken.add((p[0] + dx, p[1] + dy))

    # the parks, each on its own grass inside a fence with a gate; the dog walker and the old man's pond are near
    park_a = g.find(18, 14, (84, 38), m=1)
    if park_a:
        ax, ay = park_a
        o = (ax * G, ay * G)
        g.block((ax - 1, ay - 1, 20, 16))
        tiles.append((ax, ay, 18, 14))
        thing("playground", o[0] + 100, o[1] + 95, 180, 5)
        thing("picnic", o[0] + 100, o[1] + 225, 110, 2)
        thing("bench", o[0] + 280, o[1] + 130, 70, 7)
        thing("dogwalk", o[0] + 255, o[1] + 235, 180, 4)
        fences.append([[ax - 1, ay - 1], [ax + 19, ay - 1], [ax + 19, ay + 15], [ax - 1, ay + 15], [ax - 1, ay - 1]])
        gates.append([ax + 6, ay + 15, "h"])
    park_b = g.find(20, 10, (50, 62), m=1, avoid=[park_a] if park_a else [], apart=14)
    if park_b:
        bx, by = park_b
        o = (bx * G, by * G)
        g.block((bx - 1, by - 1, 22, 12))
        tiles.append((bx, by, 20, 10))
        thing("fetch", o[0] + 105, o[1] + 70, 200, 3)
        thing("bench", o[0] + 65, o[1] + 170, 70, 6)
        thing("picnic", o[0] + 310, o[1] + 150, 110, 8)

    # a lake of painted water and a few ponds, kept two cells clear of every belt and track
    lake = g.find(10, 6, (66, 8), m=2)
    water = {}
    if lake:
        lx, ly = lake
        g.block((lx - 2, ly - 2, 14, 10))
        water = T.runs({"water": {(i, j) for i in range(lx // 2, (lx + 10) // 2) for j in range(ly // 2, (ly + 6) // 2)}})["water"]
    for near in ((98, 34), (118, 14), (36, 56), (104, 70)):
        spot = g.find(6, 6, near, m=1, rail_ok=False)
        if spot:
            x, y = (spot[0] + 3) * G, (spot[1] + 3) * G
            thing("pond", x, y, rnd.randint(80, 108))
            g.block((spot[0] - 1, spot[1] - 1, 8, 8))
            thing("fog", x, y - 10, 150)

    # a hazard zone next to the airfield (nothing is built there)
    hz = g.find(6, 3, (64, 38), m=1)
    if hz:
        hazards.append([hz[0], hz[1], 6, 3])
        g.block((hz[0] - 1, hz[1] - 1, 8, 5))

    # forests: clumps of trees, pines, bushes and rocks, each started on open ground and grown outwards, so the gaps between the
    # islands fill up without ever touching a belt, a track or a building
    kinds = (("tree", 8), ("pine", 5), ("bush", 4), ("rock", 1))
    pool = [k for k, n in kinds for _ in range(n)]
    seeds = [(x, y) for y in range(area[1] + 2, area[3] - 1, 3) for x in range(area[0] + 1, area[2] - 1, 3)
             if g.free(x, y, 2, 2, 1)]
    rnd.shuffle(seeds)
    made = 0
    for cx, cy in seeds:
        if made >= 190:
            break
        if not g.free(cx, cy, 2, 2, 1):
            continue
        r, n = rnd.uniform(3, 7), rnd.randint(6, 16)
        placed = 0
        for _ in range(n * 12):
            if placed >= n:
                break
            kind = rnd.choice(pool)
            size = rnd.randint(*T.SIZES[kind])
            ang, d = rnd.uniform(0, 6.2832), r * rnd.random() ** 0.6
            x, y = (cx + 1 + d * math.cos(ang)) * G, (cy + 1 + d * 0.75 * math.sin(ang)) * G
            if g.clear_px(T.footprint([kind, x, y, size, 1]), 6):
                thing(kind, x, y, size)
                placed += 1
        made += placed

    # lamps along the roads and at the parks
    for pts in roads:
        a, b = pts
        vert = a[0] == b[0]
        for k in range((a[1] if vert else a[0]) + 4, (b[1] if vert else b[0]), 12):
            thing("lamp", ((a[0] + 2) if vert else k) * G, (k if vert else (a[1] + 2)) * G, 120)
    for spot, w, h in ((park_a, 18, 14), (park_b, 20, 10)):
        if spot:
            thing("lamp", (spot[0] + 2) * G, (spot[1] + 2) * G, 130)
            thing("lamp", (spot[0] + w - 2) * G, (spot[1] + h - 2) * G, 130)

    t = {"items": items}
    ground = _tiles(tiles)
    if water:
        t["tiles"] = {"grass": ground["grass"], "water": water} if ground.get("grass") else {"water": water}
    elif ground.get("grass"):
        t["tiles"] = {"grass": ground["grass"]}
    for key, val in (("fences", fences), ("gates", gates), ("roads", roads), ("hazards", hazards)):
        if val:
            t[key] = val
    return t
