"""The Factory floor drawn as a map (one SVG, so the page policy allows its motion; app.js holds it still for people who ask for
reduced motion).

Everything is laid out from what the factory has, not from a fixed picture:

- the stations (poll, classify, route, each stage role in the config, build, review and CI when they are on, the pull request) snake
  across the floor five to a row, joined by belts. A belt into a running station moves and carries crates with the ticket number;
  crates back up at a station waiting for a person and shake at one that failed;
- outside the factory, every verification worker the factory has seen has a stop and a train. An underground belt takes Build's
  output to the Verify stop, where each worker has a platform. A train only ever drives forwards: it leaves its platform, rounds the
  turning loop, runs down the one shared trunk, turns off onto its own branch, unloads at the worker's stop, goes round the worker's
  turning loop and comes back. Signals let one train onto the trunk at a time, so the others wait at red. Only a worker with a job
  drives; an idle one waits at its platform and an offline one has no train;
- GitHub, the ticket source, is a depot: drones carry new issues to Poll and pull requests back.

The timetable is computed here and every animation starts at the server clock's phase of its cycle, so the page's five-second
refresh does not restart the trains. All text is escaped; colours and the belts' flow are CSS classes."""
import math

from .views import esc

PER_ROW, MW, MH, PITCH, ROW_H, TOP = 5, 140, 100, 230, 244, 24
WIDTH = PITCH * (PER_ROW - 1) + MW + 40          # 1100: the floor and the yard share it
MAX_WORKERS = 5
SPEED, DWELL, HOME_DWELL, CLEAR = 150.0, 3.0, 2.5, 0.6       # px/s, seconds at a worker and at home, gap on the trunk
EASE = (0.42, 0.0, 0.58, 1.0)
SPLINE = "0.42 0 0.58 1"
GENERIC_ICON = "M4 4h16v16H4zM9 9h6v6H9z"


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------- paths with lengths
class Path:
    """An SVG path built from lines, quarter or half circles and cubic curves, keeping its length and named points."""

    def __init__(self, x: float, y: float):
        self.d, self.cur, self.len, self.marks = f"M {_f(x)} {_f(y)}", (x, y), 0.0, {}

    def L(self, x, y):
        self.len += math.dist(self.cur, (x, y))
        self.cur, self.d = (x, y), self.d + f" L {_f(x)} {_f(y)}"
        return self

    def A(self, r, sweep, x, y):
        ang = 2 * math.asin(min(1.0, math.dist(self.cur, (x, y)) / (2 * r)))
        self.len += r * ang
        self.cur, self.d = (x, y), self.d + f" A {_f(r)} {_f(r)} 0 0 {sweep} {_f(x)} {_f(y)}"
        return self

    def C(self, x1, y1, x2, y2, x, y):
        p0, n = self.cur, 48
        pts = [_bez(p0, (x1, y1), (x2, y2), (x, y), i / n) for i in range(n + 1)]
        self.len += sum(math.dist(pts[i], pts[i + 1]) for i in range(n))
        self.cur = (x, y)
        self.d += f" C {_f(x1)} {_f(y1)} {_f(x2)} {_f(y2)} {_f(x)} {_f(y)}"
        return self

    def mark(self, name):
        self.marks[name] = self.len
        return self


def _bez(p0, p1, p2, p3, t):
    u = 1 - t
    return tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d for a, b, c, d in zip(p0, p1, p2, p3))


def _ease_time(f: float) -> float:
    """When an ease-in-out move has covered fraction f of its distance (as a fraction of its time)."""
    x1, y1, x2, y2 = EASE
    for i in range(1001):
        t = i / 1000
        if 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3 >= f:
            return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
    return 1.0


# ---------------------------------------------------------------- the stations
def place(n: int) -> list[tuple[float, float]]:
    """Top-left corner of each station: five to a row, every other row right to left."""
    out = []
    for i in range(n):
        r, c = divmod(i, PER_ROW)
        col = c if r % 2 == 0 else PER_ROW - 1 - c
        out.append((20 + col * PITCH, TOP + r * ROW_H))
    return out


def _centre(p):
    return p[0] + MW / 2, p[1] + MH / 2


def _crate(label: str, extra: str = "") -> str:
    return (f'<g class="fm-crate{extra}"><rect x="-19" y="-11" width="38" height="22" rx="2"/>'
            f'<text x="0" y="1">{esc(label[:6])}</text></g>')


def _belt(a, b, into: str, refs: list, phase: float) -> str:
    """The belt from one station's centre to the next (the machines, drawn later, cover its ends)."""
    (x1, y1), (x2, y2) = a, b
    kind = {"done": "ok", "none": "dim"}.get(into, into)
    d = f"M {_f(x1)} {_f(y1)} L {_f(x2)} {_f(y2)}"
    out = f'<path class="fm-belt" d="{d}"/><path class="fm-flow {kind}" d="{d}"/>'
    length = math.dist(a, b)
    ref = refs[0] if refs else "#"
    edge = MW / 2 if y1 == y2 else MH / 2
    if kind == "run":
        dur = length / 64
        for k in range(2):
            begin = -((phase + k * dur / 2) % dur)
            out += (f'<g class="fm-ride">{_crate(ref)}<animateMotion path="{d}" dur="{_f(dur)}s" begin="{begin:.2f}s" '
                    f'repeatCount="indefinite"/></g>')
    elif kind in ("wait", "fail"):
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        for k in range(2 if kind == "wait" else 1):
            back = edge + 24 + k * 42
            out += (f'<g transform="translate({_f(x2 - ux * back)} {_f(y2 - uy * back)})">'
                    + _crate(refs[k] if k < len(refs) else ref, " stuck" if kind == "fail" else "") + "</g>")
    return out


def _machine(sid: str, label: str, icon: str, o: dict, word: str, href: str, at, phase: float) -> str:
    x, y = at
    refs = " · ".join(o["refs"][:3])
    name = label if len(label) <= 15 else label[:14] + "…"
    prog = ""
    if o["state"] == "run":
        prog = (f'<rect class="fm-prog" x="{_f(x + 12)}" y="{_f(y + MH - 11)}" width="0" height="3">'
                f'<animate attributeName="width" from="0" to="{MW - 24}" dur="1.4s" begin="{-(phase % 1.4):.2f}s" repeatCount="indefinite"/></rect>')
    return (f'<a class="fm-m {o["state"]}" href="{esc(href)}" aria-label="{esc(label)}: {int(o["count"])} ticket(s), {esc(word)}">'
            f'<rect class="fm-box" x="{_f(x)}" y="{_f(y)}" width="{MW}" height="{MH}" rx="3"/>'
            f'<svg class="fm-i" x="{_f(x + 12)}" y="{_f(y + 10)}" width="22" height="22" viewBox="0 0 24 24"><path d="{icon}"/></svg>'
            f'<text class="fm-c" x="{_f(x + MW - 12)}" y="{_f(y + 27)}">{int(o["count"])}</text>'
            f'<text class="fm-t" x="{_f(x + 12)}" y="{_f(y + 54)}">{esc(name)}</text>'
            f'<text class="fm-s" x="{_f(x + 12)}" y="{_f(y + 71)}">{esc(word)}</text>'
            f'<text class="fm-r" x="{_f(x + 12)}" y="{_f(y + 86)}">{esc(refs)}</text>{prog}</a>')


# ---------------------------------------------------------------- the yard: platforms, trunk, branches, signals
def _yard_geometry(n: int, z: float) -> dict:
    """Where everything outside the factory goes for n workers, the zone starting at y=z."""
    plat = [z + 56 + 24 * k for k in range(n)]
    mid = (plat[0] + plat[-1]) / 2
    ret = plat[-1] + 40                                   # the return leg under the platforms, into the junction
    first = ret + 172
    lines = [first + 72 * i for i in range(n)]
    xs = [830 - 174 * i for i in range(n)]
    return {"plat": plat, "mid": mid, "ret": ret, "lines": lines, "xs": xs, "bottom": lines[-1] + 74}


def _route(g: dict, k: int) -> Path:
    """Train k's whole loop, starting parked at its platform, heading west."""
    pk, mid, ret, y, xs = g["plat"][k], g["mid"], g["ret"], g["lines"][k], g["xs"][k]
    xu, xj = xs - 70, xs + 70
    p = Path(700, pk).L(650, pk)
    (p.L(580, mid) if pk == mid else p.C(620, pk, 610, mid, 580, mid))
    p.A((ret - mid) / 2, 0, 580, ret).L(880, ret).L(940, ret).A(60, 1, 1000, ret + 60).L(1000, y - 40).mark("peel")
    p.A(40, 1, 960, y).L(xs, y).mark("stop").L(xu, y).A(16, 0, xu, y + 32).L(xj - 50, y + 32)
    p.C(xj - 25, y + 32, xj - 25, y, xj, y).L(xj + 30, y).mark("signal").L(960, y).A(40, 0, 1000, y - 40)
    p.L(1000, ret + 60).A(60, 0, 940, ret).L(880, ret).mark("junction")
    p.C(850, ret, 830, pk, 800, pk).L(700, pk)
    return p


def _tracks(g: dict) -> list[str]:
    """Each piece of track once (shared pieces are not drawn twice, so their sleepers line up)."""
    n, out = len(g["plat"]), []
    mid, ret = g["mid"], g["ret"]
    for pk in g["plat"]:
        out.append(Path(626, pk).L(800, pk).d)
        out.append(Path(880, ret).C(850, ret, 830, pk, 800, pk).d)
        if pk != mid:
            out.append(Path(650, pk).C(620, pk, 610, mid, 580, mid).d)
    out.append(Path(650 if mid in g["plat"] else 580, mid).L(580, mid).A((ret - mid) / 2, 0, 580, ret).L(940, ret).A(60, 1, 1000, ret + 60).L(1000, g["lines"][-1] - 40).d)
    for k in range(n):
        y, xs = g["lines"][k], g["xs"][k]
        xu, xj = xs - 70, xs + 70
        out.append(Path(1000, y - 40).A(40, 1, 960, y).L(xu, y).A(16, 0, xu, y + 32).L(xj - 50, y + 32).C(xj - 25, y + 32, xj - 25, y, xj, y).d)
    return out


def timetable(routes: list[Path], running: list[bool]) -> tuple[float, list[dict]]:
    """One cycle for the trains that drive: each leaves when the trunk is free, unloads, and waits at its branch signal until the
    trunk is free again. Returns (cycle seconds, per-train times)."""
    plan = [dict() for _ in routes]
    t = 0.0
    for k, p in enumerate(routes):
        if not running[k]:
            continue
        r, stop, peel = plan[k], p.marks["stop"], p.marks["peel"]
        r["depart"], r["arrive"] = t, t + stop / SPEED
        r["leave"] = r["arrive"] + DWELL
        r["at_signal"] = r["leave"] + (p.marks["signal"] - stop) / SPEED
        t += stop / SPEED * _ease_time(peel / stop) + CLEAR
    free = t
    for k in sorted((k for k in range(len(routes)) if running[k]), key=lambda k: plan[k]["at_signal"]):
        p, r = routes[k], plan[k]
        home = p.len - p.marks["signal"]
        r["go"] = max(r["at_signal"], free)
        r["home"] = r["go"] + home / SPEED
        free = r["go"] + home / SPEED * _ease_time((p.marks["junction"] + 45 - p.marks["signal"]) / home) + CLEAR
    ends = [r["home"] for r in plan if r]
    return (round(max(ends) + HOME_DWELL, 1) if ends else 0.0), plan


def _keys(points: list[tuple[float, float]], T: float, length: float) -> tuple[str, str, str]:
    """keyTimes, keyPoints and keySplines for a motion through (time, distance) points over a cycle of T seconds."""
    times, dists = [], []
    for t, d in points:
        kt = round(t / T, 4)
        if times and kt <= times[-1]:
            continue
        times.append(kt)
        dists.append(round(min(1.0, d / length), 4))
    times[0], times[-1] = 0.0, 1.0
    return (";".join(f"{v:g}" for v in times), ";".join(f"{v:g}" for v in dists), ";".join([SPLINE] * (len(times) - 1)))


def _lamp(at: float | None, T: float, begin: str) -> str:
    """A two-lamp signal's lights: green for a moment as its train is let through, red otherwise (always red without a train)."""
    if at is None or not T:
        return '<circle class="r" cx="0" cy="-4" r="2.4"/><circle class="g off" cx="0" cy="4" r="2.4"/>'
    a, b = max(0.0, at - 0.5) / T, min(T, at + 0.9) / T
    keys = f"0;{a:.4f};{b:.4f}"
    return (f'<circle class="r" cx="0" cy="-4" r="2.4"><animate attributeName="opacity" values="1;.15;1" keyTimes="{keys}" calcMode="discrete" '
            f'dur="{T}s" begin="{begin}" repeatCount="indefinite"/></circle>'
            f'<circle class="g" cx="0" cy="4" r="2.4"><animate attributeName="opacity" values=".15;1;.15" keyTimes="{keys}" calcMode="discrete" '
            f'dur="{T}s" begin="{begin}" repeatCount="indefinite"/></circle>')


def _signal(x, y, label, lamps) -> str:
    return f'<g class="fm-sig" transform="translate({_f(x)} {_f(y)})" role="img" aria-label="{esc(label)}"><rect x="-4.5" y="-8.5" width="9" height="17" rx="2"/>{lamps}</g>'


def _stop(x, y, name: str) -> str:
    return (f'<g class="fm-stop" transform="translate({_f(x)} {_f(y)})"><rect x="0" y="0" width="108" height="24" rx="2"/>'
            f'<path class="fm-flag" d="M10 18V6h9l-2 3 2 3h-9"/><text x="25" y="16">{esc(name[:11])}</text></g>')


TRAIN = ('<rect class="fm-wagon" x="-36" y="-9" width="34" height="18" rx="2"/><rect class="fm-load" x="-30" y="-5" width="22" height="10"/>'
         '<rect class="fm-loco" x="1" y="-9" width="32" height="18" rx="4"/><circle class="fm-head" cx="29" cy="0" r="2.4"/>')


def _worker(w: dict, at, label_job: str) -> str:
    x, y = at
    on = w["online"]
    state = ("Online · " + (f'job {w["job"]["recipe"]}' if w.get("job") else "idle")) if on else "Offline"
    return (f'<a class="fm-w{"" if on else " off"}" href="/workers" aria-label="Worker {esc(w["name"])}: {esc(state)}">'
            f'<rect class="fm-box" x="{_f(x)}" y="{_f(y)}" width="160" height="76" rx="3"/>'
            f'<circle class="fm-on" cx="{_f(x + 144)}" cy="{_f(y + 18)}" r="4"/>'
            f'<text class="fm-t" x="{_f(x + 12)}" y="{_f(y + 24)}">{esc(w["name"][:16])}</text>'
            f'<text class="fm-s" x="{_f(x + 12)}" y="{_f(y + 44)}">{esc(state[:24])}</text>'
            f'<text class="fm-r" x="{_f(x + 12)}" y="{_f(y + 62)}">{esc(label_job[:24])}</text></a>')


def _loader(x, y_from, y_to) -> str:
    """A loader: a short belt (flowing from y_from to y_to) under a striped hood at the y_to end."""
    d = f"M {_f(x)} {_f(y_from)} L {_f(x)} {_f(y_to)}"
    hood = min(y_from, y_to) if y_to < y_from else y_to - 12
    return (f'<path class="fm-belt thin" d="{d}"/><path class="fm-flow run" d="{d}"/>'
            f'<rect class="fm-hood" x="{_f(x - 12)}" y="{_f(hood)}" width="24" height="12" rx="2"/>')


DRONE = ('<g class="fm-rotor"><circle cx="-8" cy="-8" r="4.5"/><circle cx="8" cy="-8" r="4.5"/><circle cx="-8" cy="8" r="4.5"/>'
         '<circle cx="8" cy="8" r="4.5"/></g><rect class="fm-dbody" x="-5" y="-5" width="10" height="10" rx="2"/>')


def _drone(path: str, load: str, dur: float, begin: float) -> str:
    return (f'<g class="fm-drone">{DRONE}<rect class="fm-dload {load}" x="-3" y="-3" width="6" height="6"/>'
            f'<animateMotion path="{path}" dur="{_f(dur)}s" begin="{begin:.2f}s" rotate="auto" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.1;.88;1" dur="{_f(dur)}s" begin="{begin:.2f}s" repeatCount="indefinite"/></g>')


# ---------------------------------------------------------------- the whole floor
def floor_map(order: list[tuple[str, str, str]], fl: dict, workers: list[dict], workers_on: bool, now: float,
              word=None, href=None) -> str:
    """order: (station id, label, icon path) in route order. fl: {station: {"state", "refs", "count"}}. workers: [{"name", "online",
    "job": {"issue", "recipe"} | None}]."""
    word = word or (lambda sid, o: o["state"])
    href = href or (lambda sid: "#")
    n = len(order)
    spots = place(n)
    centres = [_centre(p) for p in spots]
    rows = (n + PER_ROW - 1) // PER_ROW
    z = TOP + (rows - 1) * ROW_H + MH + 70                    # where outside begins
    shown = workers[:MAX_WORKERS]
    nw = len(shown)
    g = _yard_geometry(nw, z) if nw else None
    height = (g["bottom"] if g else z + 190) + 10

    belts = ""
    for i in range(n - 1):
        a, b = order[i][0], order[i + 1][0]
        nxt = fl[b]["state"]
        into = nxt if nxt != "none" else ("done" if fl[a]["state"] != "none" else "none")
        belts += _belt(centres[i], centres[i + 1], into, fl[b]["refs"], now)
    machines = "".join(_machine(sid, label, icon, fl[sid], word(sid, fl[sid]), href(sid), spots[i], now)
                       for i, (sid, label, icon) in enumerate(order))

    # outside: the zone, GitHub and its drones
    poll = centres[0]
    pr = centres[[sid for sid, _, _ in order].index("pr")] if any(sid == "pr" for sid, _, _ in order) else centres[-1]
    depot = (40, z + 40)
    dc = (depot[0] + 90, depot[1] + 40)
    din = f"M {_f(dc[0])} {_f(dc[1])} C {_f(dc[0] + 140)} {_f(dc[1] - 260)} {_f(poll[0] + 260)} {_f(poll[1] - 60)} {_f(poll[0])} {_f(poll[1])}"
    dout = f"M {_f(pr[0])} {_f(pr[1])} C {_f(pr[0] + 160)} {_f(pr[1] + 120)} {_f(dc[0] + 220)} {_f(dc[1] - 120)} {_f(dc[0])} {_f(dc[1])}"
    outside = (f'<rect class="fm-zone" x="0" y="{_f(z - 26)}" width="{WIDTH}" height="{_f(height - z + 26)}"/>'
               f'<text class="fm-lab" x="16" y="{_f(z - 6)}">OUTSIDE THE FACTORY · GITHUB BY DRONE'
               + (" · WORKERS BY TRAIN" if workers_on or nw else "") + "</text>"
               f'<g class="fm-depot" role="img" aria-label="GitHub, where tickets come from and pull requests go">'
               f'<rect class="fm-box" x="{depot[0]}" y="{_f(depot[1])}" width="180" height="80" rx="3"/>'
               f'<svg class="fm-i" x="{depot[0] + 12}" y="{_f(depot[1] + 10)}" width="22" height="22" viewBox="0 0 24 24"><path d="M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5"/></svg>'
               f'<text class="fm-t" x="{depot[0] + 42}" y="{_f(depot[1] + 27)}">GitHub</text>'
               f'<text class="fm-s" x="{depot[0] + 12}" y="{_f(depot[1] + 50)}">Ticket source</text>'
               f'<text class="fm-r" x="{depot[0] + 12}" y="{_f(depot[1] + 66)}">issues in · PRs out</text></g>')
    drones = (_drone(din, "in", 7, -(now % 7)) + _drone(din, "in", 7, -((now + 3.5) % 7))
              + (_drone(dout, "out", 7, -((now + 1.8) % 7)) if fl.get("pr", {}).get("count") or fl.get("ci", {}).get("count") else ""))

    rail = ""
    if g:
        rail = _rail(g, shown, workers_on, order, spots, now)
    elif workers_on:
        rail = (f'<a class="fm-add" href="/workers"><rect x="260" y="{_f(z + 58)}" width="200" height="44" rx="2"/>'
                f'<text x="360" y="{_f(z + 85)}">+ Connect a worker</text></a>')
    more = len(workers) - nw
    if more > 0:
        rail += f'<text class="fm-r" x="40" y="{_f(z + 150)}">and {more} more worker{"s" if more != 1 else ""}</text>'
    return (f'<svg class="fm" viewBox="0 0 {WIDTH} {_f(height)}" width="{WIDTH}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'<rect class="fm-ground" width="{WIDTH}" height="{_f(height)}"/>'
            f'<g aria-hidden="true">{belts}</g>{machines}{outside}{rail}<g aria-hidden="true">{drones}</g></svg>')


def _rail(g: dict, shown: list[dict], workers_on: bool, order, spots, now: float) -> str:
    n = len(shown)
    routes = [_route(g, k) for k in range(n)]
    running = [bool(w["online"] and w.get("job")) for w in shown]
    T, plan = timetable(routes, running)
    begin = f"{-(now % T):.2f}s" if T else "0s"
    layers = "".join(f'<g class="{c}">' + "".join(f'<path d="{d}"/>' for d in _tracks(g)) + "</g>"
                     for c in ("fm-ballast", "fm-ties", "fm-rail", "fm-rail-in", "fm-ties-in"))
    # Build's output reaches the Verify stop by an underground belt (it would cross the other belts above ground)
    sids = [sid for sid, _, _ in order]
    bx, by = _centre(spots[sids.index("build")]) if "build" in sids else (760, 0)
    ex, ey = bx + 40, by + MH / 2 + 22
    yx, yy = 760, g["plat"][0] - 34
    under = (f'<path class="fm-ugl" d="M {_f(ex)} {_f(ey + 8)} L {_f(yx)} {_f(yy - 8)}"/>'
             f'<rect class="fm-ug" x="{_f(ex - 10)}" y="{_f(ey - 8)}" width="20" height="16" rx="3"/>'
             f'<rect class="fm-ug" x="{_f(yx - 10)}" y="{_f(yy - 8)}" width="20" height="16" rx="3"/>'
             + _loader(yx, yy + 8, g["plat"][0] - 10))
    home = _stop(640, g["plat"][0] - 62, "Verify stop")
    sig, parts, trains = "", "", ""
    for k, w in enumerate(shown):
        p, y, xs, pk = routes[k], g["lines"][k], g["xs"][k], g["plat"][k]
        r = plan[k]
        sig += _signal(656, pk - 13, f'Signal at platform {k + 1}', _lamp(r.get("depart"), T, begin))
        sig += _signal(xs + 100, y - 21, f'Signal before the trunk, {w["name"]}', _lamp(r.get("go"), T, begin))
        job = w.get("job")
        parts += (_worker(w, (xs - 80, y - 136), f'#{int(job["issue"])}' if job else "") + _stop(xs - 54, y - 38, w["name"])
                  + _loader(xs, y - 38, y - 60))
        if r:
            a, b = (r["arrive"] + 0.3) / T, (r["leave"] - 0.4) / T
            parts += (f'<rect class="fm-unload" x="{_f(xs - 6)}" y="{_f(y - 50)}" width="12" height="10" opacity="0">'
                      f'<animate attributeName="opacity" values="0;1;1;0;0" keyTimes="0;{a:.4f};{(a + b) / 2:.4f};{b:.4f};1" dur="{T}s" begin="{begin}" repeatCount="indefinite"/>'
                      f'<animateTransform attributeName="transform" type="translate" values="0 0;0 0;0 -22;0 -22" keyTimes="0;{a:.4f};{b:.4f};1" '
                      f'dur="{T}s" begin="{begin}" repeatCount="indefinite"/></rect>')
            kt, kp, ks = _keys([(0, 0), (r["depart"], 0), (r["arrive"], p.marks["stop"]), (r["leave"], p.marks["stop"]),
                                (r["at_signal"], p.marks["signal"]), (r["go"], p.marks["signal"]), (r["home"], p.len), (T, p.len)], T, p.len)
            trains += (f'<g class="fm-train">{TRAIN}<animateMotion path="{p.d}" dur="{T}s" begin="{begin}" rotate="auto" calcMode="spline" '
                       f'keyTimes="{kt}" keyPoints="{kp}" keySplines="{ks}" repeatCount="indefinite"/></g>')
        elif w["online"]:
            trains += f'<g class="fm-train" transform="translate(700 {_f(pk)}) rotate(180)">{TRAIN}</g>'
    add = ""
    if workers_on and n < MAX_WORKERS:
        add = (f'<a class="fm-add" href="/workers"><rect x="40" y="{_f(g["plat"][0] + 80)}" width="180" height="44" rx="2"/>'
               f'<text x="130" y="{_f(g["plat"][0] + 107)}">+ Connect a worker</text></a>')
    return f'<g aria-hidden="true">{layers}{under}</g>{home}{sig}{parts}<g aria-hidden="true">{trains}</g>{add}'


# ---------------------------------------------------------------- one ticket's journey: the same stations, small, in a row
J_M, J_STEP, J_TOP = 56, 82, 8
J_WORD = {"done": "Done", "run": "Running", "wait": "Needs you", "fail": "Failed", "none": "Not yet"}
J_TRAIN = ('<rect class="fm-wagon" x="-24" y="-6" width="22" height="12" rx="2"/><rect class="fm-load" x="-20" y="-3" width="14" height="6"/>'
           '<rect class="fm-loco" x="1" y="-6" width="20" height="12" rx="3"/><circle class="fm-head" cx="18" cy="0" r="1.8"/>')
VERIFY_SHORT = {"queued": "Waiting", "claimed": "Checking", "passed": "Passed", "failed": "Failed", "error": "Could not run", "cancelled": "Cancelled"}
VERIFY_WORD = {"queued": "waiting for a worker", "claimed": "checking on {w}", "passed": "passed on {w}", "failed": "failed on {w}",
               "error": "could not run on {w}", "cancelled": "cancelled"}


def journey(stations: list[tuple[str, str, str, str]], verify: dict | None, now: float) -> str:
    """stations: (id, short label, icon path, state) in route order. verify: the ticket's latest verification job ({"status", "worker"})
    or None. A crate rides into the station that is working and crates wait at the one that needs a person; when the build is checked
    on a worker, a small railway runs from Build to it (the train only drives while the worker has the job)."""
    n = len(stations)
    width = 8 + (n - 1) * J_STEP + J_M
    cx = [4 + i * J_STEP + J_M / 2 for i in range(n)]
    cy = J_TOP + J_M / 2
    belts, machines = "", ""
    for i in range(n - 1):
        into = stations[i + 1][3] if stations[i + 1][3] != "none" else ("done" if stations[i][3] != "none" else "none")
        kind = {"done": "ok", "none": "dim"}.get(into, into)
        d = f"M {_f(cx[i])} {_f(cy)} L {_f(cx[i + 1])} {_f(cy)}"
        belts += f'<path class="fm-belt thin" d="{d}"/><path class="fm-flow {kind}" d="{d}"/>'
        if kind == "run":
            belts += (f'<rect class="fm-jc" x="-7" y="-5" width="14" height="10"><animateMotion path="{d}" dur="1.3s" '
                      f'begin="{-(now % 1.3):.2f}s" repeatCount="indefinite"/></rect>')
        elif kind == "wait":
            door = cx[i + 1] - J_M / 2
            belts += "".join(f'<rect class="fm-jc" x="{_f(door - 11 - k * 16)}" y="{_f(cy - 5)}" width="14" height="10"/>' for k in range(2))
    for i, (sid, label, icon, state) in enumerate(stations):
        x = cx[i] - J_M / 2
        prog = ""
        if state == "run":
            prog = (f'<rect class="fm-prog" x="{_f(x + 7)}" y="{_f(J_TOP + J_M - 9)}" width="0" height="3">'
                    f'<animate attributeName="width" from="0" to="{J_M - 14}" dur="1.4s" begin="{-(now % 1.4):.2f}s" repeatCount="indefinite"/></rect>')
        machines += (f'<g class="fm-m {state}" role="listitem" aria-label="{esc(label)}: {J_WORD[state]}">'
                     f'<rect class="fm-box" x="{_f(x)}" y="{J_TOP}" width="{J_M}" height="{J_M}" rx="3"/>'
                     f'<svg class="fm-i" x="{_f(x + 16)}" y="{J_TOP + 13}" width="24" height="24" viewBox="0 0 24 24"><path d="{icon}"/></svg>{prog}'
                     f'<text class="fm-jl" x="{_f(cx[i])}" y="{J_TOP + J_M + 17}">{esc(label[:11])}</text>'
                     f'<text class="fm-js" x="{_f(cx[i])}" y="{J_TOP + J_M + 31}">{J_WORD[state]}</text></g>')
    height, rail = J_TOP + J_M + 40, ""
    ids = [s[0] for s in stations]
    if verify and "build" in ids:
        rail, height = _spur(cx[ids.index("build")], min(width - 110, cx[ids.index("build")] + 190), J_TOP + J_M + 52, verify, now)
    return (f'<svg class="fm fm-journey" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="list" '
            f'aria-label="Stations">{rail}<g aria-hidden="true">{belts}</g>{machines}</svg>')


def _spur(xb: float, xe: float, y: float, verify: dict, now: float) -> tuple[str, float]:
    """An oval of track under Build: out along the top, round, the worker's stop on the way back, round again to Build."""
    r, y2 = 14, y + 28
    loop = Path(xb, y).L(xe, y).A(r, 1, xe, y2).L(xb, y2).A(r, 1, xb, y)
    at_worker = (xe - xb) + math.pi * r + 24
    status, worker = verify.get("status") or "queued", verify.get("worker") or "a worker"
    tracks = "".join(f'<g class="{c}"><path d="{loop.d}"/></g>' for c in ("fm-ballast j", "fm-ties j", "fm-rail j", "fm-rail-in j", "fm-ties-in j"))
    feed = f'<path class="fm-belt thin" d="M {_f(xb + 22)} {J_TOP + J_M} L {_f(xb + 22)} {_f(y - 10)}"/>'
    wx = xe - 24                                             # the worker's stop sits on the lower track, just past the far turn
    tone = {"passed": "done", "failed": "fail", "error": "fail", "claimed": "run"}.get(status, "none")
    box = (f'<g class="fm-jw {tone}"><rect class="fm-box" x="{_f(wx - 50)}" y="{_f(y2 + 18)}" width="124" height="38" rx="3"/>'
           f'<text class="fm-jl" x="{_f(wx + 12)}" y="{_f(y2 + 34)}">{esc(worker[:16])}</text>'
           f'<text class="fm-js" x="{_f(wx + 12)}" y="{_f(y2 + 48)}">{esc(VERIFY_SHORT.get(status, status))}</text></g>')
    if status == "claimed":
        T, f = 8.0, at_worker / loop.len
        train = (f'<g class="fm-train">{J_TRAIN}<animateMotion path="{loop.d}" dur="{T}s" begin="{-(now % T):.2f}s" rotate="auto" '
                 f'calcMode="spline" keyTimes="0;.1;.4;.6;.95;1" keyPoints="0;0;{f:.4f};{f:.4f};1;1" '
                 f'keySplines="{SPLINE};{SPLINE};{SPLINE};{SPLINE};{SPLINE}" repeatCount="indefinite"/></g>')
    else:
        train = f'<g class="fm-train" transform="translate({_f(xb + 20)} {_f(y)})">{J_TRAIN}</g>'
    word = VERIFY_WORD.get(status, status).format(w=worker)
    label = f'<g role="listitem" aria-label="Verification: {esc(word)}"><text class="fm-jv {tone}" x="{_f(xb - 42)}" y="{_f((y + y2) / 2 + 4)}">Verify</text></g>'
    return f'<g aria-hidden="true">{tracks}{feed}{train}</g>{box}{label}', y2 + 66
