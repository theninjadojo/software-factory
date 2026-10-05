"""The floor's layout as data, for people who arrange the Factory floor themselves (the edit page, /floor/edit).

A layout is a small JSON document, never markup. It holds where each building stands on a grid of 20px cells, one belt per hop of
the route (an orthogonal line of grid points, from beside the station a ticket leaves to beside the one it goes to), and the pieces
placed on the belts (splitters, mergers, side-loads, underground pairs). The server draws the floor from it (plant.planned_map), so
text is still escaped and the page policy holds. Each hop being its own belt is what keeps every station reachable: a layout is
only saved when every hop of the route has a belt that starts beside one station and ends beside the next.

Buildings and areas (all but the Verify yard, whose size follows its trains) can be resized: a node may carry "w" and "h", never
below its minimum (MIN); the floor draws its picture scaled evenly and centred in that box (fit_art). The editor keeps a building's
proportions as it is resized; an area (the mainland, the sea, the airfield) may take any shape and fills the rest with its ground. Walls (orthogonal lines of grid points) and trees (grid
points) are scenery: they block nothing.

Districts are boxes of their own (optional: one left out is drawn round its stations). Moving a district moves its stations with it,
it can be resized, and it must always hold its stations: the editor grows it when one of them is moved out.

Workers are train stops outside the factory. With the workers on, the layout also holds the Train station (where trains offload),
optional junctions, and track: one line of grid points per stretch, from beside one rail building to beside the next (the yard to a
junction or a worker, a junction to a worker, a junction, the Train station or the yard, a worker to the Train station or a junction,
the Train station to the yard or a junction). Every worker needs its loop: track from the yard to it, from it to the Train station
and from the Train station back to the yard (through junctions as needed); each rail building has a turnaround loop, so a train only
ever drives forwards round the circuit, and a junction's signals let one train onto the shared track at a time.

Without a saved layout the Factory page draws the default layout (default_plan: islands of stations with scenery between them, scenery.py), as the editor opens it, so the two always agree;
a saved layout that cannot be read falls back to that. One that is stale (the config gained or lost a station, an agent or the workers) is merged: what is gone is dropped, and what is new
gets a default spot and an auto-routed belt."""
import hashlib
import heapq
import json
import logging
import math
import os
from pathlib import Path

from . import scenery

log = logging.getLogger("factory.ui")

VERSION, G = 1, 20
W, H = 200, 120                            # the floor's bounds, in cells
MAX_BYTES = 64 * 1024                      # the document (terrain included); url-encoded it stays under the layout save's body limit
MAX_POINTS, MAX_CELLS, MAX_PIECES, MAX_UNDER = 64, 6000, 100, 5
MAX_WALLS, MAX_WALL_CELLS, MAX_TREES = 60, 4000, 400
FILE = "floor-layout.json"
INTAKE, LATER = ("poll", "classify", "route"), ("build", "review", "ci", "pr")
SIZE = {"station": (7, 5), "power": (10, 5), "sources": (7, 6), "receiving": (7, 5), "queue": (3, 3), "airfield": (34, 11),
        "mainland": (12, 66), "sea": (15, 66), "harbor": (6, 4), "notify": (6, 4), "yard": (10, 5), "depot": (8, 4), "worker": (9, 4),
        "junction": (2, 2), "outside": (60, 12)}
MIN = {"station": (5, 4), "power": (7, 4), "sources": (5, 5), "receiving": (5, 4), "queue": (2, 2), "airfield": (17, 6),
       "mainland": (6, 20), "sea": (6, 20), "harbor": (5, 3), "notify": (5, 3), "depot": (6, 3), "worker": (7, 3), "junction": (2, 2),
       "outside": (10, 4)}                     # the smallest a resized building may be, in cells
JUNCTIONS = ("a", "b", "c", "d")             # optional: where tracks join or split
YARD_DX = -14                                # the yard's box starts 14 cells left of its anchor, the top of Build's feeder (on its right)
SINGLE = ("mainland", "sea", "sources", "receiving", "harbor", "queue", "airfield")
NOTIFIERS = ("telegram", "slack")          # the channels that tell a person what the factory needs: wireless, so they take no belt
NOTIFY_NAMES = {"telegram": "Telegram", "slack": "Slack"}
DIRS = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}
KINDS = ("splitter", "merger", "sideload", "underground")
NAMES = {"mainland": "The mainland", "sea": "The sea", "sources": "Sources", "receiving": "Receiving", "queue": "The queue",
         "airfield": "The airfield", "yard": "The Verify yard", "harbor": "The harbor", "depot": "The Train station",
         "outside": "Outside the factory"}
FREE = ("outside", "sea")                    # areas other buildings may stand in (the harbor in the sea), and belts and track may cross
DISTRICTS = ("INTAKE", "PLANNING", "PRODUCTION", "QUALITY", "SHIPPING")
YARD_TOP = 590                             # the yard's feeder top in yard.py's coordinates (yard.BOT_Y + yard.MH + 30)


# ---------------------------------------------------------------- what the config puts on the floor
class Ctx:
    """What the factory has now: the stations in route order, the enabled agent harnesses, whether the yard is shown and with how many
    trains (its size follows them), and the notifiers (every channel the factory can talk on, set up or not)."""

    def __init__(self, ids, harnesses=(), yard_on: bool = False, trains: int = 0, notifiers=NOTIFIERS, workers=None):
        self.ids, self.harnesses, self.yard_on, self.trains = tuple(ids), tuple(harnesses), bool(yard_on), max(0, min(5, int(trains)))
        self.notifiers = tuple(n for n in notifiers if n in NOTIFY_NAMES)
        names = list(workers) if workers is not None else [f"worker {k + 1}" for k in range(self.trains)]
        self.workers = tuple(dict.fromkeys(str(n)[:40] for n in names))[:5] if self.yard_on else ()
        self.key = (self.ids, self.harnesses, self.yard_on, self.trains, self.notifiers, self.workers)
        self.roles = [s for s in self.ids if s not in INTAKE + LATER]
        self.districts: dict[str, list[str]] = {}
        for s in self.ids:
            self.districts.setdefault(district_of(s), []).append(f"station:{s}")

    def nodes(self) -> list[str]:
        return (list(SINGLE) + [f"station:{s}" for s in self.ids] + [f"power:{h}" for h in self.harnesses]
                + [f"notify:{n}" for n in self.notifiers] + (["yard", "depot"] + [f"worker:{w}" for w in self.workers] if self.yard_on else []))

    def optional(self) -> list[str]:
        """Buildings a layout may leave out: the junctions and the outside area (with the workers on)."""
        return ["outside"] + [f"junction:{j}" for j in JUNCTIONS] if self.yard_on else []

    def rail(self) -> list[str]:
        return (["yard", "depot"] + [f"worker:{w}" for w in self.workers] + [f"junction:{j}" for j in JUNCTIONS]) if self.yard_on else []

    def tracks(self) -> list[tuple[str, str]]:
        """Every stretch of track a layout may lay, as (from, to) node ids."""
        return [(a, b) for a in self.rail() for b in self.rail() if rail_ok(a, b)]

    def hops(self) -> list[tuple[str, str]]:
        """Every hop a ticket can ride, as (from, to) node ids."""
        s = lambda x: f"station:{x}"
        has = set(self.ids)
        out = [("airfield", "receiving"), ("harbor", "receiving")]                # the 747's crates; the ships' tickets
        if "poll" in has:
            out.append(("receiving", s("poll")))
        intake = [x for x in INTAKE if x in has]
        out += [(s(a), s(b)) for a, b in zip(intake, intake[1:])]
        if {"classify", "route"} <= has:
            out += [(s("classify"), "queue"), ("queue", s("route"))]
        main = [x for x in self.ids if x not in INTAKE + ("review", "ci", "pr")]
        if "route" in has:
            main = ["route"] + main
        out += [(s(a), s(b)) for a, b in zip(main, main[1:])]
        if self.roles and {"route", "build"} <= has:
            out.append((s("route"), s("build")))                     # the bypass
        late = [x for x in ("build", "review", "ci", "pr") if x in has]
        out += [(s(a), s(b)) for a, b in zip(late, late[1:])]
        if {"ci", "build"} <= has:
            out.append((s("ci"), s("build")))                        # CI's fix rounds
        if self.yard_on and "build" in has:
            out.append((s("build"), "yard"))
        if self.yard_on:
            off = next((x for x in ("ci", "review", "pr", "build") if x in has), None)
            if off:
                out.append(("depot", s(off)))                        # the Train station hands the checked builds on
        return out


def rail_ok(a: str, b: str) -> bool:
    """May track run from a to b? Trains leave the yard for the workers, go on to the Train station and come back to the yard."""
    kind = lambda n: n.split(":")[0]
    ka, kb = kind(a), kind(b)
    if a == b:
        return False
    if ka == "yard":
        return kb in ("worker", "junction")
    if ka == "junction":
        return kb in ("worker", "junction", "depot", "yard")
    if ka == "worker":
        return kb in ("depot", "junction")
    if ka == "depot":
        return kb in ("yard", "junction")
    return False


def district_of(sid: str) -> str:
    return ("INTAKE" if sid in INTAKE else "PRODUCTION" if sid == "build" else "QUALITY" if sid in ("review", "ci")
            else "SHIPPING" if sid == "pr" else "PLANNING")


def fit(members: list, B: dict) -> dict:
    """The smallest district round its stations' boxes, with room for its name: a cell aside and below, two above."""
    bs = [B[m] for m in members if m in B]
    x0, y0 = min(b[0] for b in bs) - 1, min(b[1] for b in bs) - 2
    x1, y1 = max(b[0] + b[2] for b in bs) + 1, max(b[1] + b[3] for b in bs) + 2
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def holds(d: dict, b) -> bool:
    return d["x"] <= b[0] and d["y"] <= b[1] and b[0] + b[2] <= d["x"] + d["w"] and b[1] + b[3] <= d["y"] + d["h"]


def district_boxes(doc: dict, ctx: "Ctx", B: dict) -> dict:
    """Each district of the config: the saved box, or the one round its stations."""
    saved = doc.get("districts") or {}
    return {n: dict(saved[n]) if n in saved else fit(m, B) for n, m in ctx.districts.items()}


def hop_id(a: str, b: str) -> str:
    return f"{a}>{b}"


def label(nid: str) -> str:
    from .board import LABEL
    from .plant import NAMES as POWER
    kind, _, name = nid.partition(":")
    if kind == "station":
        return LABEL.get(name, name.replace("_", " ").title())
    if kind == "power":
        return "Power: " + POWER.get(name, name)
    if kind == "notify":
        return NOTIFY_NAMES.get(name, name)
    if kind == "worker":
        return name
    if kind == "junction":
        return "Junction " + name.upper()
    return NAMES.get(nid, nid)


ICONS = {"sources": "M4 3h6v18H4zM14 3h6v18h-6zM7 7v.01M7 11v.01M17 7v.01M17 11v.01",
         "receiving": "M2 10l10-6 10 6M4 10v10h16V10M8 14h8M8 17h8",
         "harbor": "M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM12 7v14M5 14a7 7 0 0 0 14 0M8 10h8",
         "airfield": "M2 13l9-2 3-7 2 1-1 6 6 1v2l-6 1 1 6-2 1-3-7-9-2z", "queue": "M3 8h18v12H3zM3 12h18M11 10h2v4h-2z",
         "notify": "M5 11a10 10 0 0 1 14 0M8 14a6 6 0 0 1 8 0M12 18v.01", "power": "M13 2L4 14h7l-1 8 9-12h-7z",
         "yard": "M5 17h14M7 17V7h10v10M9 21l-2-4M15 21l2-4", "depot": "M4 6h12l4 4v7H4zM4 11h16M8 20h.01M16 20h.01",
         "worker": "M4 3h16v7H4zM4 14h16v7H4zM8 6.5h.01M8 17.5h.01", "junction": "M12 3v7M12 10l-6 11M12 10l6 11",
         "mainland": "M3 20h18M6 20V10l6-6 6 6v10", "sea": "M2 8c3-2 5 2 8 0s5 2 8 0 3 0 4 0M2 14c3-2 5 2 8 0s5 2 8 0 3 0 4 0",
         "outside": "M3 12h18M12 3v18"}


def icon(nid: str) -> str:
    """The tray's small picture of a building: a station's own icon, else one per kind."""
    from .board import ICON
    kind, _, name = nid.partition(":")
    return ICON.get(name, ICONS["queue"]) if kind == "station" else ICONS.get(kind, ICONS["queue"])


def group(nid: str) -> str:
    """Where the editor's parts tray lists a building."""
    kind = nid.split(":")[0]
    return {"station": "Stations", "queue": "Stations", "power": "Power", "yard": "Workers and rail", "depot": "Workers and rail",
            "worker": "Workers and rail", "junction": "Workers and rail", "notify": "Notifiers", "mainland": "Areas", "sea": "Areas",
            "outside": "Areas"}.get(kind, "Arrivals")


def yard_box(trains: int = 0) -> tuple[int, int, int, int]:
    """The yard's box relative to its anchor (the top of its feeder), in cells: (dx, dy, w, h): a platform per train, three cells
    apart, where each waits to be loaded from Build's feeder. The workers are buildings of their own, joined to it by track."""
    return (YARD_DX, 0, 16, 2 + 3 * max(1, trains))


def box(nid: str, at, ctx: Ctx, wh=None) -> tuple[int, int, int, int]:
    """The node's box in cells; wh: its size when it was resized."""
    x, y = at
    if nid == "yard":
        dx, dy, w, h = yard_box(ctx.trains)
        return (x + dx, y + dy, w, h)
    return (x, y, *(wh or SIZE[nid.split(":")[0]]))


def size_of(p: dict):
    return (p["w"], p["h"]) if "w" in p else None


def boxes(nodes: dict, ctx: Ctx) -> dict:
    return {nid: box(nid, (p["x"], p["y"]), ctx, size_of(p)) for nid, p in nodes.items()}


def solid(B: dict) -> dict:
    """The boxes belts and track may not run through (every building but the areas others stand in)."""
    return {k: v for k, v in B.items() if k not in FREE}


def _inside(p, b) -> bool:
    return b[0] <= p[0] <= b[0] + b[2] and b[1] <= p[1] <= b[1] + b[3]


def beside(p, nid: str, B: dict, nodes: dict) -> bool:
    """Is grid point p one cell outside the node's box (where its inserter reaches)? The yard takes only at its feeder."""
    if nid == "yard":
        a = nodes["yard"]
        return tuple(p) == (a["x"], a["y"] - 1)
    x, y, w, h = B[nid]
    px, py = p
    return ((x <= px <= x + w and py in (y - 1, y + h + 1)) or (y <= py <= y + h and px in (x - 1, x + w + 1)))


def near(p, b) -> bool:
    """Is grid point p one cell outside box b?"""
    x, y, w, h = b
    return (x <= p[0] <= x + w and p[1] in (y - 1, y + h + 1)) or (y <= p[1] <= y + h and p[0] in (x - 1, x + w + 1))


def cells(pts):
    """Every grid point along an orthogonal line, in order (corners once)."""
    out = [tuple(pts[0])]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = abs(x2 - x1) + abs(y2 - y1)
        dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
        out += [(x1 + dx * k, y1 + dy * k) for k in range(1, n + 1)]
    return out


# ---------------------------------------------------------------- the default layout: today's arrangement on the grid
def _default_pos(ctx: Ctx) -> dict:
    """Islands with open ground between them (the scenery fills it, scenery.py): the arrivals at the sea, intake, planning across a
    pond, production below it, quality and shipping back along the airfield, the power station on the east edge, the yard and the
    workers at the south."""
    roles = ctx.roles
    P = {"mainland": (0, 0), "sea": (12, 0), "sources": (28, 2), "receiving": (28, 10), "harbor": (19, 13), "queue": (50, 12), "airfield": (28, 33)}
    for s, x in zip(INTAKE, (37, 45, 53)):
        P[f"station:{s}"] = (x, 2)
    x0 = 76
    for i, r in enumerate(roles):
        P[f"station:{r}"] = (x0 + 8 * i, 2)
    P["station:build"] = (x0 + 12, 18)
    for j, s in enumerate(x for x in ("review", "ci") if x in ctx.ids):
        P[f"station:{s}"] = (70 - 12 * j, 25)
    P["station:pr"] = (38, 25)
    px = max(x0 + 8 * len(roles) + 4, 96)
    for k, h in enumerate(ctx.harnesses):
        P[f"power:{h}"] = (px, 1 + 6 * k)
    for k, n in enumerate(ctx.notifiers):
        P[f"notify:{n}"] = (px + 7 * k, 2 + 6 * len(ctx.harnesses))
    P["yard"] = (79, 46)
    P["depot"] = (112, 47)
    n = len(ctx.workers)
    for k, w in enumerate(ctx.workers):                              # a column of stops, joined by a junction either side
        P[f"worker:{w}"] = (95, 56 + 7 * k)
    mid = 56 + (7 * (n - 1) + 4) // 2 - 1
    if n:
        P["junction:a"], P["junction:b"] = (89, mid), (108, mid)
    out = {k: {"x": P[k][0], "y": P[k][1]} for k in ctx.nodes() + ctx.optional() if k in P}
    if n:
        out["outside"] = {"x": 86, "y": 53, "w": 28, "h": 7 * n + 4}
    return out


def default_tracks(ctx: Ctx) -> list[tuple[str, str]]:
    """The default railway: out of the yard to junction A, a branch to each worker, all joining at junction B for the Train station,
    and back to the yard."""
    if not ctx.yard_on:
        return []
    out = [("yard", "junction:a")] if ctx.workers else []
    for w in ctx.workers:
        out += [("junction:a", f"worker:{w}"), (f"worker:{w}", "junction:b")]
    return out + ([("junction:b", "depot")] if ctx.workers else []) + [("depot", "yard")]


def track_port(nid: str, other, B: dict, side: bool = False, way: str = ""):
    """Where track leaves or reaches a rail building: the middle of the side facing the other end (never the yard's top, where its
    feeder comes in). A worker's track comes in and goes out at its sides, so its neighbours' track never runs along its loop; a
    junction branches up and down to the stops above and below it. side: also return which side ("n", "e", "s", "w")."""
    x, y, w, h = B[nid]
    ox, oy = other
    cx, cy = x + w / 2, y + h / 2
    sides = {"e": (x + w + 1, y + h // 2), "w": (x - 1, y + h // 2), "s": (x + w // 2, y + h + 1), "n": (x + w // 2, y - 1)}
    if nid == "yard":                                                # trains leave low on a side and come back high, never on top
        del sides["n"]
        lo, hi = y + max(1, (3 * h) // 4), y + max(1, h // 4)
        sides["e"] = (x + w + 1, lo if way == "out" else hi)
        sides["w"] = (x - 1, lo if way == "out" else hi)
        sides["s"] = (x + (w // 4 if way == "out" else (3 * w) // 4), y + h + 1)
    if nid.startswith("worker:"):
        k = "e" if ox > cx else "w"
    elif nid.startswith("junction:") and abs(oy - cy) >= 2:
        k = "s" if oy > cy else "n"
    elif nid.startswith("junction:"):
        k = "e" if ox > cx else "w"
    else:
        k = sorted(sides, key=lambda k: -((ox - cx) * DIRS[k][0] + (oy - cy) * DIRS[k][1]) / (w if k in "ew" else h))[0]
    return (sides[k], k) if side else sides[k]


def _bend(a, b, upright: bool, blocked: set):
    """A track with one bend from a to b, leaving a straight up or down first (upright) or across first; None if it hits a building."""
    mid = (a[0], b[1]) if upright else (b[0], a[1])
    pts = [p for i, p in enumerate([a, mid, b]) if i == 0 or p != [a, mid, b][i - 1]]
    if len(pts) < 2 or any(c in blocked for c in cells(pts)):
        return None
    return pts


LOW = ("station:review", "station:ci", "station:pr")                 # the quality and shipping row, flowing west


def port(nid: str, way: str, other: str, nodes: dict, ctx: Ctx):
    """Where a default belt leaves (way "out") or reaches (way "in") a node, as a grid point beside it."""
    x, y, w, h = box(nid, (nodes[nid]["x"], nodes[nid]["y"]), ctx, size_of(nodes[nid]))
    if nid == "yard":
        return (nodes[nid]["x"], nodes[nid]["y"] - 1)
    if nid == "receiving":
        return (x + w + 1, y + 2) if way == "out" else (x - 1, y + 3) if other == "harbor" else (x + 2, y + h + 1)
    if nid == "harbor":
        return (x + w + 1, y + h // 2)
    if nid == "airfield":
        return (x + 2, y - 1)
    if nid == "queue":
        return (x + 1, y - 1) if way == "in" else (x + 1, y + h + 1)
    if nid == "station:build":
        if way == "in":
            return (x - 1, y + 2) if other == "station:ci" else (x + 1, y - 1)
        return (x + w + 1, y + 2) if other == "yard" else (x + 1, y + h + 1)
    if nid == "station:ci" and other == "station:build":
        return (x + 3, y + h + 1)
    if other == "depot":
        return (x + 3, y - 1)
    if nid == "depot":
        return (x + 1, y - 1)
    low = nid in LOW
    if way == "in":
        return (x + w - 1, y - 1) if low else (x + 1, y + h + 1)
    return (x + 1, y - 1) if low else (x + w - 1, y + h + 1)


def blocked_cells(B: dict) -> set:
    return {(x, y) for bx, by, bw, bh in B.values() for x in range(bx, bx + bw + 1) for y in range(by, by + bh + 1)}


def autoroute(a, b, blocked: set, used: set = frozenset()):
    """An orthogonal belt from a to b round the buildings (A* over cells, a turn costs more than a step, sharing another belt's cells
    costs a little). Returns its corner points, or None."""
    a, b = tuple(a), tuple(b)
    if a in blocked or b in blocked:
        return None
    for pad in (12, 40, None):
        lo_x, hi_x = (0, W) if pad is None else (max(0, min(a[0], b[0]) - pad), min(W, max(a[0], b[0]) + pad))
        lo_y, hi_y = (0, H) if pad is None else (max(0, min(a[1], b[1]) - pad), min(H, max(a[1], b[1]) + pad))
        h0 = abs(a[0] - b[0]) + abs(a[1] - b[1])
        best, prev, q, n = {(a, None): 0}, {}, [(h0, 0, a, None)], 0
        while q:
            _, c, p, d = heapq.heappop(q)
            if p == b:
                path, s = [p], (p, d)
                while s in prev:
                    s = prev[s]
                    path.append(s[0])
                path.reverse()
                return [path[0]] + [path[i] for i in range(1, len(path) - 1)
                                    if (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]) != (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])] + [path[-1]]
            if c > best.get((p, d), 1e18):
                continue
            n += 1
            if n > 200000:
                break
            for nd, (dx, dy) in DIRS.items():
                np_ = (p[0] + dx, p[1] + dy)
                if not (lo_x <= np_[0] <= hi_x and lo_y <= np_[1] <= hi_y) or np_ in blocked:
                    continue
                if d is not None and (DIRS[d][0] == -dx and DIRS[d][1] == -dy):
                    continue
                nc = c + 1 + (4 if d is not None and nd != d else 0) + (2 if np_ in used else 0)
                if nc < best.get((np_, nd), 1e18):
                    best[(np_, nd)] = nc
                    prev[(np_, nd)] = (p, d)
                    heapq.heappush(q, (nc + abs(np_[0] - b[0]) + abs(np_[1] - b[1]), nc, np_, nd))
    return None


def _pieces_for(belts: dict) -> list[dict]:
    """Splitters where belts leave one point, mergers where they meet, and an underground pair wherever a belt crosses another."""
    out, starts, ends = [], {}, {}
    for hop, pts in belts.items():
        starts.setdefault(tuple(pts[0]), []).append(pts)
        ends.setdefault(tuple(pts[-1]), []).append(pts)
    for at, ps in starts.items():
        if len(ps) > 1:
            out.append({"kind": "splitter", "at": list(at), "dir": _dir(ps[0][0], ps[0][1])})
    for at, ps in ends.items():
        if len(ps) > 1:
            out.append({"kind": "merger", "at": list(at), "dir": _dir(ps[0][-2], ps[0][-1])})
    seen = {}
    for hop, pts in belts.items():
        for c in cells(pts):
            seen.setdefault(c, set()).add(hop)
    taken = set()
    for hop, pts in belts.items():
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            line = cells([(x1, y1), (x2, y2)])
            for i in range(2, len(line) - 2):
                c, u, v = line[i], line[i - 1], line[i + 1]
                others = seen.get(c, set()) - {hop}
                if not others or c in taken or seen.get(u, set()) != {hop} or seen.get(v, set()) != {hop}:
                    continue
                crosses = any(_crosses(belts[o], c, (x1 == x2)) for o in others)
                if crosses and not _on_other_ug(out, c):
                    out.append({"kind": "underground", "from": list(u), "to": list(v)})
                    taken |= {u, c, v}
    return out


def _crosses(pts, c, vertical: bool) -> bool:
    """Does this belt run across point c the other way?"""
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if vertical and y1 == y2 == c[1] and min(x1, x2) < c[0] < max(x1, x2):
            return True
        if not vertical and x1 == x2 == c[0] and min(y1, y2) < c[1] < max(y1, y2):
            return True
    return False


def _on_other_ug(pieces, c) -> bool:
    return any(p["kind"] == "underground" and c in cells([p["from"], p["to"]]) for p in pieces)


def _dir(a, b) -> str:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return "e" if dx > 0 else "w" if dx < 0 else "s" if dy > 0 else "n"


_DEFAULTS: dict = {}


def default_plan(ctx: Ctx) -> dict:
    """Today's arrangement on the grid, its belts routed round the buildings."""
    if ctx.key in _DEFAULTS:
        return json.loads(json.dumps(_DEFAULTS[ctx.key]))
    nodes = _default_pos(ctx)
    belts = _route_all(nodes, {}, ctx)
    tracks = route_tracks(nodes, {}, default_tracks(ctx), ctx, belts or {})
    doc = {"version": VERSION, "grid": G, "nodes": nodes, "belts": belts or {}, "pieces": _pieces_for(belts or {}), "walls": [], "trees": []}
    if tracks:
        doc["tracks"] = tracks
    doc["districts"] = district_boxes(doc, ctx, boxes(nodes, ctx))
    doc["terrain"] = scenery.build(boxes(nodes, ctx), doc["belts"], doc.get("tracks") or {}, doc["districts"], cells)
    if len(_DEFAULTS) > 32:
        _DEFAULTS.clear()
    _DEFAULTS[ctx.key] = doc
    return json.loads(json.dumps(doc))


def _route_all(nodes: dict, keep: dict, ctx: Ctx) -> dict | None:
    """Belts for every hop: the kept ones as they are, the rest routed. None when a hop cannot be routed."""
    B = boxes(nodes, ctx)
    blocked = blocked_cells(solid(B))
    out, used = {}, set()
    for pts in keep.values():
        used |= set(cells(pts))
    for a, b in ctx.hops():
        hid = hop_id(a, b)
        if hid in keep:
            out[hid] = keep[hid]
            continue
        pts = autoroute(port(a, "out", b, nodes, ctx), port(b, "in", a, nodes, ctx), blocked, used)
        if pts is None or len(pts) < 2:
            return None
        out[hid] = [list(p) for p in pts]
        used |= set(cells(pts))
    return out


def route_tracks(nodes: dict, keep: dict, pairs, ctx: Ctx, belts: dict) -> dict:
    """Track for each (from, to) pair: the kept stretches as they are, the rest routed round the buildings (a stretch that cannot be
    routed is left out; the layout's check then names the loop it breaks)."""
    B = boxes(nodes, ctx)
    blocked = blocked_cells(solid(B))
    used = {c for pts in list(belts.values()) + list(keep.values()) for c in cells(pts)}
    out = dict(keep)
    for a, b in pairs:
        tid = hop_id(a, b)
        if tid in out or a not in B or b not in B:
            continue
        ca, cb = (B[a][0] + B[a][2] / 2, B[a][1] + B[a][3] / 2), (B[b][0] + B[b][2] / 2, B[b][1] + B[b][3] / 2)
        (pa, sa), pb = track_port(a, cb, B, True, "out"), track_port(b, ca, B, way="in")
        pts = _bend(pa, pb, sa in "ns", blocked) or autoroute(pa, pb, blocked, used)
        if pts and len(pts) >= 2:
            out[tid] = [list(p) for p in pts]
            used |= set(cells(pts))
    return out


def loops(tracks: dict, ctx: Ctx) -> dict:
    """{worker node: does a train have its whole loop?}: track from the yard to the worker, from it to the Train station and from the
    Train station back to the yard, through junctions only."""
    edges = {}
    for tid in tracks:
        a, _, b = tid.partition(">")
        edges.setdefault(a, set()).add(b)

    def reach(a, b):
        seen, todo = {a}, [a]
        while todo:
            n = todo.pop()
            for m in edges.get(n, ()):
                if m == b:
                    return True
                if m.startswith("junction:") and m not in seen:
                    seen.add(m)
                    todo.append(m)
        return False
    home = reach("depot", "yard")
    return {f"worker:{w}": home and reach("yard", f"worker:{w}") and reach(f"worker:{w}", "depot") for w in ctx.workers}


def leg(tracks: dict, a: str, b: str):
    """The stretches of track from a to b through junctions (the fewest), or None."""
    edges = {}
    for tid in tracks:
        x, _, y = tid.partition(">")
        edges.setdefault(x, []).append(y)
    prev, todo = {a: None}, [a]
    while todo:
        n = todo.pop(0)
        for m in edges.get(n, ()):
            if m in prev:
                continue
            prev[m] = n
            if m == b:
                out, c = [], b
                while prev[c] is not None:
                    out.append(hop_id(prev[c], c))
                    c = prev[c]
                return out[::-1]
            if m.startswith("junction:"):
                todo.append(m)
    return None


# ---------------------------------------------------------------- checking a layout
def _int(v) -> bool:
    return type(v) is int


def _pt(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(_int(c) for c in v) and 0 <= v[0] <= W and 0 <= v[1] <= H


def structure(doc) -> list[str]:
    """The document's shape: known keys, whole numbers inside the floor, bounded sizes. Nothing here knows about the config."""
    if not isinstance(doc, dict):
        return ["The layout must be a JSON object."]
    errs = []
    extra = set(doc) - {"version", "grid", "nodes", "belts", "pieces", "districts", "walls", "trees", "tracks", "terrain"}
    if extra:
        errs.append("Unknown keys: " + ", ".join(sorted(str(k)[:30] for k in extra)[:5]) + ".")
    if doc.get("version") != VERSION or doc.get("grid") != G:
        errs.append(f"The layout must have version {VERSION} and grid {G}.")
    nodes, belts, pieces = doc.get("nodes"), doc.get("belts"), doc.get("pieces")
    if not isinstance(nodes, dict) or not isinstance(belts, dict) or not isinstance(pieces, list):
        return errs + ["The layout needs nodes and belts (objects) and pieces (a list)."]
    dists = doc.get("districts", {})
    if not isinstance(dists, dict):
        errs.append("The districts are an object of boxes.")
    else:
        for k, v in dists.items():
            if not (isinstance(v, dict) and set(v) == {"x", "y", "w", "h"} and all(_int(v[c]) for c in v) and v["x"] >= 0 and v["y"] >= 0
                    and v["w"] >= 1 and v["h"] >= 1 and v["x"] + v["w"] <= W and v["y"] + v["h"] <= H):
                errs.append(f"District {str(k)[:40]}: a box is {{\"x\", \"y\", \"w\", \"h\"}} in whole cells inside the floor.")
    for k, v in nodes.items():
        if not (isinstance(v, dict) and set(v) in ({"x", "y"}, {"x", "y", "w", "h"}) and all(_int(v[c]) for c in v) and 0 <= v["x"] <= W
                and 0 <= v["y"] <= H and ("w" not in v or (1 <= v["w"] <= W and 1 <= v["h"] <= H))):
            errs.append(f"{str(k)[:40]}: a position is {{\"x\": whole number, \"y\": whole number}} inside the floor, with \"w\" and \"h\" "
                        "when it was resized.")
    walls, trees = doc.get("walls", []), doc.get("trees", [])
    if not isinstance(walls, list) or not isinstance(trees, list):
        errs.append("The walls and trees are lists.")
    else:
        if len(walls) > MAX_WALLS:
            errs.append(f"Too many walls ({len(walls)}; at most {MAX_WALLS}).")
        cells_ = 0
        for pts in walls[:MAX_WALLS]:
            if not (isinstance(pts, list) and 2 <= len(pts) <= MAX_POINTS and all(_pt(p) for p in pts)
                    and all((p[0] != q[0]) != (p[1] != q[1]) for p, q in zip(pts, pts[1:]))):
                errs.append(f"A wall is 2 to {MAX_POINTS} grid points inside the floor, each run straight across or down.")
                break
            cells_ += sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
        if cells_ > MAX_WALL_CELLS:
            errs.append(f"The walls are too long ({cells_} cells; at most {MAX_WALL_CELLS}).")
        if len(trees) > MAX_TREES:
            errs.append(f"Too many trees ({len(trees)}; at most {MAX_TREES}).")
        elif not all(_pt(t) for t in trees):
            errs.append("A tree is a grid point inside the floor.")
    errs += terrain_shape(doc.get("terrain", {}))
    tracks = doc.get("tracks", {})
    if not isinstance(tracks, dict):
        errs.append("The tracks are an object of lines.")
        tracks = {}
    total = 0
    for what, k, pts in [("Belt", k, v) for k, v in belts.items()] + [("Track", k, v) for k, v in tracks.items()]:
        if not (isinstance(pts, list) and 2 <= len(pts) <= MAX_POINTS and all(_pt(p) for p in pts)):
            errs.append(f"{what} {str(k)[:60]}: it needs 2 to {MAX_POINTS} grid points inside the floor.")
            continue
        for p, q in zip(pts, pts[1:]):
            if (p[0] != q[0]) == (p[1] != q[1]):
                errs.append(f"{what} {str(k)[:60]}: each run goes straight across or down the grid and is at least one cell long.")
                break
        total += sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
    if total > MAX_CELLS:
        errs.append(f"The belts are too long ({total} cells; at most {MAX_CELLS}).")
    if len(pieces) > MAX_PIECES:
        errs.append(f"Too many pieces ({len(pieces)}; at most {MAX_PIECES}).")
    for p in pieces[:MAX_PIECES]:
        ok = isinstance(p, dict) and p.get("kind") in KINDS
        if ok and p["kind"] == "underground":
            ok = set(p) == {"kind", "from", "to"} and _pt(p["from"]) and _pt(p["to"])
        elif ok:
            ok = set(p) == {"kind", "at", "dir"} and _pt(p["at"]) and p["dir"] in DIRS
        if not ok:
            errs.append("A piece is a splitter, merger or side-load {kind, at, dir} or an underground {kind, from, to}.")
            break
    return errs


def terrain_shape(t) -> list[str]:
    """The terrain's shape (terrain.py): known keys and kinds, whole numbers inside the floor, sizes in range, bounded counts."""
    from . import terrain as T
    if t in (None, {}):
        return []
    if not isinstance(t, dict):
        return ["The terrain is an object."]
    errs, L = [], T.LIMITS
    extra = set(t) - {"items", "rivers", "tiles", "fences", "gates", "roads", "hazards"}
    if extra:
        errs.append("Unknown terrain: " + ", ".join(sorted(str(k)[:20] for k in extra)[:5]) + ".")
    px = lambda v, hi: _int(v) and 0 <= v <= hi
    items = t.get("items", [])
    if not isinstance(items, list) or len(items) > L["items"]:
        errs.append(f"The terrain holds at most {L['items']} trees, bushes, rocks, ponds and lights.")
    else:
        for it in items:
            ok = isinstance(it, list) and len(it) == 5 and it[0] in T.KINDS and px(it[1], W * G) and px(it[2], H * G) and _int(it[3]) and _int(it[4])
            if not ok or not (T.SIZES[it[0]][0] <= it[3] <= T.SIZES[it[0]][1]) or not 0 <= it[4] <= 0xFFFFFFFF:
                errs.append("A terrain item is [kind, x, y, size, seed]: a tree, pine, bush, rock, pond, lamp, fog, shade or park piece (fetch, "
                            "playground, picnic, bench, dog walker) inside the floor, its size in range.")
                break
    rivers = t.get("rivers", [])
    if not isinstance(rivers, list) or len(rivers) > L["rivers"] or not all(
            isinstance(r, list) and 2 <= len(r) <= L["river_points"] and all(isinstance(p, list) and len(p) == 2 and px(p[0], W * G) and px(p[1], H * G) for p in r)
            for r in rivers):
        errs.append(f"A river is 2 to {L['river_points']} points inside the floor (at most {L['rivers']} rivers).")
    tiles = t.get("tiles", {})
    if not isinstance(tiles, dict) or set(tiles) - set(T.GROUNDS):
        errs.append("The ground is painted in grass, dirt, sand, concrete or water.")
    else:
        seen, nruns = set(), 0
        for kind, rs in tiles.items():
            if not isinstance(rs, list):
                errs.append("The ground is runs of tiles: [column, row, count].")
                break
            for r in rs:
                nruns += 1
                if not (isinstance(r, list) and len(r) == 3 and all(_int(v) for v in r) and r[0] >= 0 and r[1] >= 0 and r[2] >= 1
                        and (r[0] + r[2]) * T.TILE <= W * G and (r[1] + 1) * T.TILE <= H * G):
                    errs.append("The ground is runs of tiles inside the floor: [column, row, count].")
                    return errs
                cells = {(r[0] + i, r[1]) for i in range(r[2])}
                if cells & seen:
                    errs.append("A ground tile is painted only one kind.")
                    return errs
                seen |= cells
                if len(seen) > L["tiles"]:
                    errs.append(f"At most {L['tiles']} ground tiles.")
                    return errs
        if nruns > L["runs"]:
            errs.append(f"The ground is too broken up ({nruns} runs; at most {L['runs']}).")
    for key, what in (("fences", "fence"), ("roads", "road")):
        ls = t.get(key, [])
        if not isinstance(ls, list) or len(ls) > L[key] or not all(
                isinstance(pts, list) and 2 <= len(pts) <= MAX_POINTS and all(_pt(q) for q in pts)
                and all((q[0] != r[0]) != (q[1] != r[1]) for q, r in zip(pts, pts[1:])) for pts in ls):
            errs.append(f"A {what} is 2 to {MAX_POINTS} grid points inside the floor, each run straight across or down (at most {L[key]}).")
    gates = t.get("gates", [])
    if not isinstance(gates, list) or len(gates) > L["gates"] or not all(
            isinstance(g, list) and len(g) == 3 and _pt(g[:2]) and g[2] in ("h", "v") for g in gates):
        errs.append(f"A gate is [x, y, \"h\" or \"v\"] at a grid point (at most {L['gates']}).")
    hz = t.get("hazards", [])
    if not isinstance(hz, list) or len(hz) > L["hazards"] or not all(
            isinstance(r, list) and len(r) == 4 and all(_int(v) for v in r) and r[0] >= 0 and r[1] >= 0 and r[2] >= 1 and r[3] >= 1
            and r[0] + r[2] <= W and r[1] + r[3] <= H for r in hz):
        errs.append(f"A hazard zone is [x, y, w, h] in cells inside the floor (at most {L['hazards']}).")
    return errs


def water_at(t: dict):
    """A test for "is this pixel in water?": ponds, rivers and painted water tiles."""
    import math
    from . import terrain as T
    ponds = [(x, y, s / 2) for kind, x, y, s, v in (t.get("items") or []) if kind == "pond"]
    segs = [(a, b) for r in (t.get("rivers") or []) for a, b in zip(r, r[1:])]
    tiles = T.cells_of({"water": (t.get("tiles") or {}).get("water") or []}).get("water", set())

    def seg_dist(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        u = 0 if not L2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        return math.dist(p, (a[0] + u * dx, a[1] + u * dy))

    def at(p):
        return (any(math.dist(p, (x, y)) <= r for x, y, r in ponds) or any(seg_dist(p, a, b) <= 13 for a, b in segs)
                or (int(p[0] // T.TILE), int(p[1] // T.TILE)) in tiles)
    return at


def terrain_rules(doc: dict, B: dict, hops: list) -> list[str]:
    """What the terrain asks of the rest: belts and track do not cross water (a bridge is not drawn yet); a belt crosses a wall only
    with an underground belt; track never runs through a wall, nor through a fence except at a gate; nothing is built in a hazard
    zone."""
    t = doc.get("terrain") or {}
    errs = []
    wet = water_at(t)
    walls = {c for w in doc.get("walls") or [] for c in cells(w)}
    fences = {c for w in t.get("fences") or [] for c in cells(w)}
    for x, y, way in t.get("gates") or []:
        fences -= {(x, y), (x + 1, y)} if way == "h" else {(x, y), (x, y + 1)}
    under = [p for p in doc.get("pieces") or [] if p.get("kind") == "underground"]
    lines_ = [("belt", k, v) for k, v in (doc.get("belts") or {}).items() if k in hops] + [("track", k, v) for k, v in (doc.get("tracks") or {}).items()]
    for what, hid, pts in lines_:
        a, _, b = hid.partition(">")
        name = f"The {what} from {label(a)} to {label(b)}"
        cs = cells(pts)
        if any(wet((cx * G, cy * G)) for cx, cy in cs):
            errs.append(f"{name} crosses water: route it round, there is no bridge yet.")
        hit = [c for c in cs if c in walls]
        if hit and what == "track":
            errs.append(f"{name} runs into a wall.")
        elif hit:
            hidden = set()
            for u in under:
                if _same_run(pts, u["from"], u["to"]):
                    run = cells([u["from"], u["to"]])
                    hidden |= set(run[1:-1])
            if any(c not in hidden for c in hit):
                errs.append(f"{name} crosses a wall: take it under with an underground belt.")
        if what == "track" and any(c in fences for c in cs):
            errs.append(f"{name} runs into a fence: put a gate where it crosses.")
    for x, y, w, h in t.get("hazards") or []:
        for nid, (bx, by, bw, bh) in solid(B).items():
            if bx < x + w and x < bx + bw and by < y + h and y < by + bh:
                errs.append(f"{label(nid)} stands in a hazard zone.")
    errs += park_rules(t, B)
    return errs


GROUND_AREAS = ("mainland", "sea", "airfield", "outside")          # areas the park may stand in


def park_rules(t: dict, B: dict) -> list[str]:
    """Hitboxes: a park piece stands clear of the buildings, of the other park pieces and of the trees, bushes, rocks and ponds."""
    from . import terrain as T
    items, errs = t.get("items") or [], []
    for i, it in enumerate(items):
        if it[0] not in T.PARK:
            continue
        a, name = T.footprint(it), f"The {T.PARK_NAMES[it[0]]}"
        for nid, (bx, by, bw, bh) in B.items():
            if nid not in GROUND_AREAS and T.hits(a, (bx * G, by * G, bw * G, bh * G)):
                errs.append(f"{name} overlaps {label(nid)}.")
        for j, o in enumerate(items):
            if j == i or o[0] not in T.SOLID or (o[0] in T.PARK and j < i):
                continue
            if T.hits(a, T.footprint(o)):
                errs.append(f"{name} overlaps " + (f"the {T.PARK_NAMES[o[0]]}." if o[0] in T.PARK else f"a {o[0]}."))
    return errs


def _belt_problem(hid: str, pts, B: dict, nodes: dict) -> str:
    a, b = hid.split(">")
    if not beside(pts[0], a, B, nodes):
        return f"The belt from {label(a)} to {label(b)} must start beside {label(a)}."
    if not beside(pts[-1], b, B, nodes):
        return (f"The belt from {label(a)} to {label(b)} must end " + ("at the top of the yard's feeder." if b == "yard" else f"beside {label(b)}."))
    for c in cells(pts):
        for nid, bx in solid(B).items():
            if _inside(c, bx):
                return f"The belt from {label(a)} to {label(b)} runs through {label(nid)}."
    return ""


def _track_problem(tid: str, pts, B: dict) -> str:
    a, b = tid.split(">")
    if not near(pts[0], B[a]):
        return f"The track from {label(a)} to {label(b)} must start beside {label(a)}."
    if not near(pts[-1], B[b]):
        return f"The track from {label(a)} to {label(b)} must end beside {label(b)}."
    for c in cells(pts):
        for nid, bx in solid(B).items():
            if _inside(c, bx):
                return f"The track from {label(a)} to {label(b)} runs through {label(nid)}."
    return ""


def _on_line(pts, c) -> bool:
    return tuple(c) in set(cells(pts))


def _same_run(pts, u, v) -> bool:
    for p, q in zip(pts, pts[1:]):
        line = cells([p, q])
        if tuple(u) in line and tuple(v) in line and line.index(tuple(u)) < line.index(tuple(v)):
            return True
    return False


def placement(B: dict) -> list[str]:
    """Every building inside the floor and no two overlapping."""
    errs = [f"{label(nid)} is outside the floor." for nid, (x, y, w, h) in B.items() if x < 0 or y < 0 or x + w > W or y + h > H]
    ids = list(solid(B))
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (ax, ay, aw, ah), (bx, by, bw, bh) = B[a], B[b]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                errs.append(f"{label(a)} overlaps {label(b)}.")
    return errs


def validate(doc, ctx: Ctx) -> list[str]:
    """Everything a saved layout must satisfy: its shape, every building present once and inside the floor with no two overlapping,
    and every hop of the route on a belt that starts beside the station it leaves and ends beside the one it reaches."""
    errs = structure(doc)
    if errs:
        return errs
    want = ctx.nodes()
    nodes = doc["nodes"]
    unknown = [k for k in nodes if k not in want + ctx.optional()]
    if unknown:
        errs.append("Unknown buildings: " + ", ".join(str(k)[:40] for k in unknown[:5]) + ".")
    missing = [k for k in want if k not in nodes]
    if missing:
        errs.append("Missing buildings: " + ", ".join(label(k) for k in missing[:5]) + ".")
    if errs:
        return errs
    for k, v in nodes.items():
        if "w" in v:
            kind = k.split(":")[0]
            if k == "yard":
                errs.append("The Verify yard cannot be resized: its size follows its trains.")
            elif v["w"] < MIN[kind][0] or v["h"] < MIN[kind][1]:
                errs.append(f"{label(k)} is smaller than it may be (at least {MIN[kind][0]} by {MIN[kind][1]} cells).")
    if errs:
        return errs
    B = boxes(nodes, ctx)
    errs += placement(B)
    for name in doc.get("districts", {}):
        if name not in ctx.districts:
            errs.append(f"There is no district {str(name)[:40]} in this factory.")
    for name, d in district_boxes(doc, ctx, B).items():
        errs += [f"The {name.title()} district must hold {label(m)}." for m in ctx.districts[name] if not holds(d, B[m])]
    hops = [hop_id(a, b) for a, b in ctx.hops()]
    for k in doc["belts"]:
        if k not in hops:
            errs.append(f"There is no hop {str(k)[:60]} on the route.")
    for hid in hops:
        a, b = hid.split(">")
        pts = doc["belts"].get(hid)
        if pts is None:
            errs.append(f"No belt from {label(a)} to {label(b)}: {label(b)} cannot be reached on its route.")
            continue
        problem = _belt_problem(hid, pts, B, nodes)
        if problem:
            errs.append(problem)
    tracks = doc.get("tracks") or {}
    allowed = {hop_id(a, b) for a, b in ctx.tracks()}
    for tid, pts in tracks.items():
        a, _, b = tid.partition(">")
        if tid not in allowed:
            errs.append(f"Track cannot run from {label(a)[:40]} to {label(b)[:40]}: trains go from the yard to the workers, on to the Train station and back.")
        elif a not in B or b not in B:
            errs.append(f"The track from {label(a)} to {label(b)} needs both on the floor.")
        else:
            problem = _track_problem(tid, pts, B)
            if problem:
                errs.append(problem)
    for w, ok in loops(tracks, ctx).items():
        if not ok:
            errs.append(f"No track loop for {label(w)}: lay track from the yard to it, from it to the Train station, and from the Train station back to the yard.")
    errs += terrain_rules(doc, B, hops)
    belts = [p for k, p in doc["belts"].items() if k in hops]
    for p in doc["pieces"]:
        if p["kind"] == "underground":
            n = abs(p["from"][0] - p["to"][0]) + abs(p["from"][1] - p["to"][1])
            if not 2 <= n <= MAX_UNDER + 1 or not any(_same_run(bp, p["from"], p["to"]) for bp in belts):
                errs.append(f"An underground belt at {p['from']} must sit on one straight run of a belt, its ends 2 to {MAX_UNDER + 1} cells apart in the belt's direction.")
        elif not any(_on_line(bp, p["at"]) for bp in belts):
            errs.append(f"The {p['kind']} at {p['at']} is not on a belt.")
    return errs[:20]


# ---------------------------------------------------------------- stale layouts
def merge(doc: dict, ctx: Ctx) -> dict | None:
    """Fit a saved layout to what the factory has now: buildings that are gone are dropped, new ones get their default spot (or the
    nearest free one), belts that no longer fit are routed again. None when it cannot be made whole (the caller draws the default)."""
    want = ctx.nodes()
    nodes = {k: dict(v) for k, v in doc["nodes"].items() if k in want + ctx.optional()}
    if "yard" in nodes:
        nodes["yard"] = {"x": nodes["yard"]["x"], "y": nodes["yard"]["y"]}         # it is never resized
    default = _default_pos(ctx)
    for nid in want:
        if nid in nodes:
            continue
        spot = _free_spot(nid, default[nid], nodes, ctx)
        if spot is None:
            return None
        nodes[nid] = {"x": spot[0], "y": spot[1]}
    B = boxes(nodes, ctx)
    if placement(B):                                                 # the yard grew with the workers into a neighbour, or off the floor
        return None
    keep = {}
    for a, b in ctx.hops():
        hid = hop_id(a, b)
        pts = doc["belts"].get(hid)
        if pts and not _belt_problem(hid, pts, B, nodes):
            keep[hid] = pts
    belts = _route_all(nodes, keep, ctx)
    if belts is None:
        return None
    allowed = {hop_id(a, b) for a, b in ctx.tracks()}
    tracks = {k: v for k, v in (doc.get("tracks") or {}).items() if k in allowed and all(n in B for n in k.split(">"))
              and not _track_problem(k, v, B)}
    need = []
    for w, ok in loops(tracks, ctx).items():                         # a new worker (or a broken loop) gets track of its own
        if not ok:
            need += [(a, b) for a, b in (("yard", w), (w, "depot"), ("depot", "yard")) if not leg(tracks, a, b)]
    tracks = route_tracks(nodes, tracks, list(dict.fromkeys(need)), ctx, belts)
    on = list(belts.values())
    pieces = [p for p in doc["pieces"] if (p["kind"] == "underground" and any(_same_run(bp, p["from"], p["to"]) for bp in on))
              or (p["kind"] != "underground" and any(_on_line(bp, p["at"]) for bp in on))]
    dists = {}
    for name, d in (doc.get("districts") or {}).items():
        if name not in ctx.districts:
            continue
        f = fit(ctx.districts[name], B)                              # a new station of the district: the box grows round it
        x0, y0 = min(d["x"], f["x"]), min(d["y"], f["y"])
        x1, y1 = max(d["x"] + d["w"], f["x"] + f["w"]), max(d["y"] + d["h"], f["y"] + f["h"])
        if x0 >= 0 and y0 >= 0 and x1 <= W and y1 <= H:
            dists[name] = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
    out = {"version": VERSION, "grid": G, "nodes": nodes, "belts": belts, "pieces": pieces, "districts": dists,
           "walls": list(doc.get("walls") or []), "trees": list(doc.get("trees") or [])}
    if doc.get("terrain"):
        out["terrain"] = doc["terrain"]
    if tracks:
        out["tracks"] = tracks
    return out


def _free_spot(nid, at, nodes, ctx):
    taken = list(solid(boxes(nodes, ctx)).values())
    x0, y0 = at["x"], at["y"]
    for r in range(0, 60):
        for dx in range(-r, r + 1):
            for dy in (range(-r, r + 1) if abs(dx) == r else (-r, r)):
                x, y = x0 + dx, y0 + dy
                bx, by, bw, bh = box(nid, (x, y), ctx)
                if bx < 0 or by < 1 or bx + bw > W or by + bh > H:
                    continue
                if all(not (bx - 1 < tx + tw and tx < bx + bw + 1 and by - 1 < ty + th and ty < by + bh + 1) for tx, ty, tw, th in taken):
                    return (x, y)
    return None


# ---------------------------------------------------------------- the file
_READ: dict = {}
_MERGED: dict = {}
_TOLD: set = set()


def path(state_dir) -> Path:
    return Path(state_dir) / FILE


def rev(state_dir) -> str:
    """A short fingerprint of the saved file ("" when there is none), so a save from a stale editor tab is refused."""
    try:
        return hashlib.sha256(path(state_dir).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def read(state_dir) -> tuple[dict | None, str]:
    """(the saved document or None, why it is not used). Parsed once per change of the file."""
    p = path(state_dir)
    try:
        st = p.stat()
    except OSError:
        return None, ""
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _READ:
        doc, why = None, ""
        try:
            if st.st_size > MAX_BYTES:
                why = "the file is too large"
            else:
                doc = json.loads(p.read_text())
                errs = structure(doc)
                if errs:
                    doc, why = None, errs[0]
        except (OSError, ValueError, RecursionError) as e:
            why = f"it could not be read ({type(e).__name__})"
        _READ.clear()
        _READ[key] = (doc, why)
    return _READ[key]


def current(state_dir, ctx: Ctx) -> tuple[dict | None, str]:
    """The saved layout fitted to the config (None: draw the default) and, when the saved file is not used, why."""
    doc, why = read(state_dir)
    return usable(doc, why, ctx)


def usable(doc, why: str, ctx: Ctx) -> tuple[dict | None, str]:
    if doc is None:
        if why:
            _tell(why)
        return None, why
    key = (id(doc), ctx.key)                                         # read() hands back the same object until the file changes
    hit = _MERGED.get(key)
    if hit is None or hit[0] is not doc:
        _MERGED.clear()
        hit = _MERGED[key] = (doc, merge(doc, ctx))
    merged = hit[1]
    if merged is None:
        why = "it no longer fits the factory's stations"
        _tell(why)
        return None, why
    return merged, ""


def _tell(why: str) -> None:
    if why not in _TOLD:
        _TOLD.add(why)
        log.warning("saved floor layout not used: %s", why)


def canonical(doc: dict) -> dict:
    """Only what a layout holds, in a fixed order (the editor may send more than it needs)."""
    pos = lambda v: {"x": v["x"], "y": v["y"], **({"w": v["w"], "h": v["h"]} if "w" in v else {})}
    return {"version": VERSION, "grid": G, "nodes": {k: pos(v) for k, v in doc["nodes"].items()},
            "belts": {k: [list(p) for p in v] for k, v in doc["belts"].items()}, "pieces": doc.get("pieces", []),
            "districts": {k: {c: v[c] for c in ("x", "y", "w", "h")} for k, v in (doc.get("districts") or {}).items()},
            "walls": [[list(p) for p in w] for w in doc.get("walls") or []], "trees": [list(t) for t in doc.get("trees") or []],
            **({"tracks": {k: [list(p) for p in v] for k, v in doc["tracks"].items()}} if doc.get("tracks") else {}),
            **({"terrain": doc["terrain"]} if doc.get("terrain") else {})}


def save(state_dir, doc: dict) -> None:
    """Write the layout atomically, readable by the UI's user only (like the UI's other files)."""
    p = path(state_dir)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)                                             # a leftover .tmp keeps its old mode otherwise
    with os.fdopen(fd, "w") as f:
        json.dump(canonical(doc), f, separators=(",", ":"))
    os.replace(tmp, p)


def reset(state_dir) -> None:
    path(state_dir).unlink(missing_ok=True)


# ---------------------------------------------------------------- what the plant draws from
def compile_plan(doc: dict, ctx: Ctx) -> dict:
    """The merged layout in the plant's pixels: node boxes and origins, each hop's belt points with its underground stretches, and
    the pieces."""
    nodes = doc["nodes"]
    B = boxes(nodes, ctx)
    px = lambda p: (p[0] * G, p[1] * G)
    hops = {}
    for a, b in ctx.hops():
        hid = hop_id(a, b)
        pts = doc["belts"][hid]
        under = [(px(p["from"]), px(p["to"])) for p in doc["pieces"] if p["kind"] == "underground" and _same_run(pts, p["from"], p["to"])]
        hops[hid] = {"src": a, "dst": b, "pts": [px(p) for p in pts], "under": under}
    dist = {n: (d["x"] * G, d["y"] * G, d["w"] * G, d["h"] * G) for n, d in district_boxes(doc, ctx, B).items()}
    scale = {k: fit_art(k, B[k]) for k, v in nodes.items() if "w" in v}
    tracks = doc.get("tracks") or {}
    rails = {tid: {"src": tid.split(">")[0], "dst": tid.split(">")[1], "pts": [px(p) for p in pts]} for tid, pts in tracks.items()}
    circuits = {}
    for w, ok in loops(tracks, ctx).items():
        if ok:
            circuits[w] = [leg(tracks, "yard", w), leg(tracks, w, "depot"), leg(tracks, "depot", "yard")]
    return {"at": {k: px((v["x"], v["y"])) for k, v in nodes.items()}, "box": {k: tuple(c * G for c in v) for k, v in B.items()}, "districts": dist,
            "hops": hops, "pieces": [{**p, "at": px(p["at"])} for p in doc["pieces"] if p["kind"] != "underground"], "scale": scale,
            "walls": [[px(p) for p in w] for w in doc.get("walls") or []], "trees": [px(t) for t in doc.get("trees") or []],
            "tracks": rails, "circuits": circuits, "workers": [f"worker:{w}" for w in ctx.workers],
            "terrain": doc.get("terrain") or {}, "wall_cells": doc.get("walls") or [], "tree_cells": doc.get("trees") or []}


def fit_art(nid: str, b) -> tuple[float, float, float]:
    """How a resized node's picture sits in its box (cells), in pixels: (scale, x offset, y offset). The picture keeps its proportions
    (one scale, the smaller of the two stretches) and is centred, so its text is never squashed; an area fills the rest with its ground."""
    w0, h0 = SIZE[nid.split(":")[0]]
    s = min(b[2] / w0, b[3] / h0)
    return (s, (b[2] - w0 * s) * G / 2, (b[3] - h0 * s) * G / 2)


def meta(ctx: Ctx) -> dict:
    """What the editor needs besides the layout: each building's label and size, the hops, the districts, the default layout."""
    out = {}
    for nid in ctx.nodes() + ctx.optional():
        if nid == "yard":
            dx, dy, w, h = yard_box(ctx.trains)
        else:
            (dx, dy), (w, h) = (0, 0), SIZE[nid.split(":")[0]]
        kind = nid.split(":")[0]
        out[nid] = {"label": label(nid), "dx": dx, "dy": dy, "w": w, "h": h, "min": None if nid in ("yard",) or kind == "junction" else MIN[kind],
                    "group": group(nid), "icon": icon(nid), **({"optional": True} if nid in ctx.optional() else {}), **({"rail": True} if nid in ctx.rail() else {})}
    dist = ctx.districts
    return {"grid": G, "w": W, "h": H, "nodes": out, "hops": [[a, b, f"{label(a)} → {label(b)}"] for a, b in ctx.hops()],
            "tracks": [[a, b] for a, b in ctx.tracks()], "workers": [f"worker:{w}" for w in ctx.workers], "free": list(FREE),
            "districts": dist, "under": MAX_UNDER, "default": default_plan(ctx)}
