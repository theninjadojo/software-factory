"""The floor's parts that plant.py builds the plant from: paths with lengths, belts, splitters, underground belts and pipes,
inserters and the crates they take, the station buildings, and the Verify yard (platforms, the shared trunk, signals, and the
workers' trains of several cars that bend round the curves; a train only ever drives forwards and signals let one onto the trunk
at a time). Also a ticket's own journey (the Journey card).

The timetable is computed here and every animation starts at the server clock's phase of its cycle, so the page's five-second
refresh does not restart anything. All text is escaped; colours and the belts' flow are CSS classes."""
import math

from . import buildings
from .views import esc

MAX_WORKERS = 5
SPEED, DWELL, HOME_DWELL = 150.0, 3.0, 2.5                 # trains: px/s, seconds at a worker and at home
GAP = 31.0                                                 # a train's cars, centre to centre
CARS = (3, 4, 3, 2, 3)                                     # locomotive and wagons, per train
CLEAR = 0.6 + max(CARS) * GAP / SPEED                      # the trunk is free once the whole train has left it
EASE = (0.42, 0.0, 0.58, 1.0)
SPLINE = "0.42 0 0.58 1"
GENERIC_ICON = "M4 4h16v16H4zM9 9h6v6H9z"

# the factory: two rows of buildings around the loop
MW, MH, PITCH, X0 = 140, 100, 170, 200
TOP_Y, BOT_Y, T, B = 20, 460, 160, 420                     # machine rows; the loop's top and bottom runs
LEFT = 200                                                 # the loop's left run (the GitHub compound is left of it)
BELT_V, GRAB, SWING = 55.0, 0.35, 0.8                      # crates: px/s; wait at the pick point; one inserter swing


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




def _hood(x, y, facing) -> str:
    horiz = facing in ("e", "w")
    w, h = (22, 26) if horiz else (26, 22)
    mouth = f"M -7 0 L 7 0" if horiz else "M 0 -7 L 0 7"
    return (f'<g class="fn-ug" transform="translate({_f(x)} {_f(y)})"><rect x="{-w / 2:g}" y="{-h / 2:g}" width="{w}" height="{h}" rx="3"/>'
            f'<rect class="fn-ugm" x="{-w / 2 + 4:g}" y="{-h / 2 + 4:g}" width="{w - 8}" height="{h - 8}" rx="2"/>'
            f'<path class="fn-ugflow" d="{mouth}"/><path class="fn-arrow" d="{ARROW[facing]}"/></g>')


ARROW = {"e": "M -3 -4 L 3 0 L -3 4", "w": "M 3 -4 L -3 0 L 3 4", "s": "M -4 -3 L 0 3 L 4 -3", "n": "M -4 3 L 0 -3 L 4 3"}


def underground(a, b) -> str:
    """An underground belt: two hoods, mouths facing each other, chevrons running in, the belt shown faintly between."""
    (x1, y1), (x2, y2) = a, b
    if y1 == y2:
        f1, f2 = ("e", "w") if x2 > x1 else ("w", "e")
    else:
        f1, f2 = ("s", "n") if y2 > y1 else ("n", "s")
    return f'<path class="fn-ghost" d="M {_f(x1)} {_f(y1)} L {_f(x2)} {_f(y2)}"/>' + _hood(x1, y1, f1) + _hood(x2, y2, f2)


def belt(d: str, cls: str = "fn-belt") -> str:
    return f'<path class="{cls}" d="{d}"/><path class="fn-flow" d="{d}"/>'


def splitter(x, y1, y2, label) -> str:
    """A splitter (or a merger): one block across two belts, its cogs turning."""
    h = y2 - y1 + 24
    return (f'<g class="fn-split ma on" role="img" aria-label="{esc(label)}"><rect x="{_f(x - 10)}" y="{_f(y1 - 12)}" width="20" height="{_f(h)}" rx="3"/>'
            f'<path class="fn-split-s" d="M {_f(x - 10)} {_f(y1 - 12)} l 20 {_f(h)}"/>{buildings.cog(x, y1, 5.5, 6, "cog fn-cog")}{buildings.cog(x, y2, 5.5, 6, "cog ccw fn-cog")}</g>')


def pipe(d: str) -> str:
    return f'<path class="fn-pipe" d="{d}"/><path class="fn-pipe-h" d="{d}"/>'


def pipe_to_ground(x, y, facing) -> str:
    return f'<g class="fn-ptg" transform="translate({_f(x)} {_f(y)})"><rect x="-8" y="-8" width="16" height="16" rx="3"/><path d="{ARROW[facing]}"/></g>'


# ---------------------------------------------------------------- inserters and the crates they take
def _arm(x, y, src_down: bool, rot: str = "", carry: str = "") -> str:
    """An inserter: a base, an arm pointing at where it takes from, and the crate it carries."""
    a = 90 if src_down else -90
    hx, hy = x + 18 * math.cos(math.radians(a)), y + 18 * math.sin(math.radians(a))
    return (f'<g class="fn-ins"><rect class="fn-ins-base" x="{_f(x - 6)}" y="{_f(y - 6)}" width="12" height="12" rx="2"/>'
            f'<g>{rot}<line class="fn-ins-l" x1="{_f(x)}" y1="{_f(y)}" x2="{_f(hx)}" y2="{_f(hy)}"/>'
            f'<rect class="fn-ins-c" x="{_f(hx - 4)}" y="{_f(hy - 4)}" width="8" height="8" opacity="0">{carry}</rect>'
            f'{hand(hx, hy, a)}</g><circle class="fn-ins-p" cx="{_f(x)}" cy="{_f(y)}" r="3"/></g>')


def hand(hx, hy, ang) -> str:
    """An inserter's grab hand at the end of its arm: a wrist across the arm and two fingers reaching on towards what it takes."""
    ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    vx, vy = -uy * 4.5, ux * 4.5
    return (f'<path class="fn-ins-h" d="M {_f(hx + vx)} {_f(hy + vy)} L {_f(hx - vx)} {_f(hy - vy)} '
            f'M {_f(hx + vx)} {_f(hy + vy)} l {_f(ux * 5)} {_f(uy * 5)} M {_f(hx - vx)} {_f(hy - vy)} l {_f(ux * 5)} {_f(uy * 5)}"/>')


def _anim(attr: str, values: str, keys: str, P: float, begin: float, discrete=True) -> str:
    mode = ' calcMode="discrete"' if discrete else ""
    return f'<animate attributeName="{attr}" values="{values}" keyTimes="{keys}"{mode} dur="{P:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite"/>'


def _rot(x, y, angles, keys, P, begin) -> str:
    vals = ";".join(f"{a} {_f(x)} {_f(y)}" for a in angles)
    return (f'<animateTransform attributeName="transform" type="rotate" values="{vals}" keyTimes="{keys}" dur="{P:.2f}s" '
            f'begin="{begin:.2f}s" repeatCount="indefinite"/>')


def taking_arm(x, y, src_down, Tg, P, begin) -> str:
    """Waits pointing at the belt, closes on the crate at Tg, swings it in, swings back."""
    k = lambda t: f"{min(t / P, 1):.4f}"
    return _arm(x, y, src_down, _rot(x, y, (0, 0, 180, 0, 0), f"0;{k(Tg)};{k(Tg + SWING)};{k(Tg + 2 * SWING)};1", P, begin),
                _anim("opacity", "0;1;0", f"0;{k(Tg)};{k(Tg + SWING - .02)}", P, begin))


def giving_arm(x, y, src_down, P, begin) -> str:
    """Puts a crate on the belt as each cycle starts: swings back, waits, takes one from the building, swings out."""
    k = lambda t: f"{min(t / P, 1):.4f}"
    return _arm(x, y, src_down, _rot(x, y, (180, 0, 0, 180), f"0;{k(SWING)};{k(P - SWING)};1", P, begin),
                _anim("opacity", "0;1", f"0;{k(P - SWING)}", P, begin))


def crate_trip(path, hidden, label: str, P: float, begin: float, small: bool = False) -> tuple[str, float]:
    """A crate riding `path` from where it was put down to where it is taken; hidden: (from, to) distances underground.
    Returns the crate and when the arm takes it."""
    Tt = path.len / BELT_V
    Tg = Tt + GRAB
    k = lambda t: f"{min(t / P, 1):.4f}"
    pts = [(0, 1)]
    for a, b in hidden:
        pts += [(Tt * a / path.len, 0), (Tt * b / path.len, 1)]
    pts.append((Tg, 0))
    box = '<rect x="-6" y="-6" width="12" height="12" rx="2"/>' if small else f'<rect x="-12" y="-6" width="24" height="12" rx="2"/><text x="0" y="0.5">{esc(label[:6])}</text>'
    return (f'<g class="fn-crate">{box}<animateMotion path="{path.d}" keyPoints="0;1;1" keyTimes="0;{k(Tt)};1" calcMode="linear" '
            f'dur="{P:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite"/>'
            + _anim("opacity", ";".join(str(v) for _, v in pts), ";".join(k(t) for t, _ in pts), P, begin) + "</g>"), Tg


def _still_crate(x, y, label, stuck=False) -> str:
    inner = f'<rect x="-12" y="-6" width="24" height="12" rx="2"/><text x="0" y="0.5">{esc(label[:6])}</text>'
    return (f'<g transform="translate({_f(x)} {_f(y)})"><g class="fn-crate{" stuck" if stuck else ""}">{inner}</g></g>')


# ---------------------------------------------------------------- the buildings
def _station(sid, label, o, word, href, at, kind) -> str:
    x, y, _ = at
    refs = " · ".join(o["refs"][:3])
    name = label if len(label) <= 14 else label[:13] + "…"
    room = max(4, 16 - len(word))                                    # the refs share the line with the state word
    refs = refs if len(refs) <= room else refs[:room - 1] + "…"
    return (f'<a class="fm-m {o["state"]}" href="{esc(href)}" aria-label="{esc(label)}: {int(o["count"])} ticket(s), {esc(word)}">'
            f'<rect class="fm-box" x="{_f(x)}" y="{_f(y)}" width="{MW}" height="{MH}" rx="3"/>'
            + buildings.art(kind, o["state"], x + 2, y + 2) +
            f'<rect class="fn-plate" x="{_f(x + 2)}" y="{_f(y + 63)}" width="{MW - 4}" height="{MH - 65}"/>'
            f'<text class="fn-n" x="{_f(x + 9)}" y="{_f(y + 79)}">{esc(name)}</text>'
            f'<text class="fn-c" x="{_f(x + MW - 9)}" y="{_f(y + 79)}">{int(o["count"])}</text>'
            f'<text class="fn-s" x="{_f(x + 9)}" y="{_f(y + 93)}">{esc(word)}</text>'
            f'<text class="fn-r" x="{_f(x + MW - 9)}" y="{_f(y + 93)}">{esc(refs)}</text></a>')


# ---------------------------------------------------------------- the Verify yard
def yard_geometry(n: int, ax: float) -> dict:
    """Platforms, lanes and lines for n workers, laid out round the feeder that comes down from Build at x=ax."""
    plat = [BOT_Y + MH + 120 + 60 * k for k in range(n)]
    mid = (plat[0] + plat[-1]) / 2
    ret = plat[-1] + 50
    tx = ax + 230                                                    # the trunk
    lines = [ret + 170 + 80 * k for k in range(n)]
    xs = [tx - 235 - 170 * k for k in range(n)]
    return {"ax": ax, "plat": plat, "mid": mid, "ret": ret, "tx": tx, "lines": lines, "xs": xs, "park": ax - 50,
            "bottom": lines[-1] + 74}


def _exit_fan(p, g, pk):
    ax, mid = g["ax"], g["mid"]
    return p.L(ax - 230, mid) if pk == mid else p.C(ax - 180, pk, ax - 190, mid, ax - 230, mid)


def train_route(g: dict, k: int) -> "Path":
    """Train k's whole loop, starting with its locomotive at its platform, heading west."""
    ax, pk, ret, tx, y, xs = g["ax"], g["plat"][k], g["ret"], g["tx"], g["lines"][k], g["xs"][k]
    j = ax + 110
    p = Path(g["park"], pk).L(ax - 140, pk)
    _exit_fan(p, g, pk)
    p.A((ret - g["mid"]) / 2, 0, ax - 230, ret).L(j, ret).L(ax + 170, ret).A(60, 1, tx, ret + 60).L(tx, y - 40).mark("peel")
    p.A(40, 1, tx - 40, y).L(xs, y).mark("stop").L(xs - 70, y).A(16, 0, xs - 70, y + 32).L(xs + 20, y + 32)
    p.C(xs + 45, y + 32, xs + 45, y, xs + 70, y).L(xs + 100, y).mark("signal").L(tx - 40, y).A(40, 0, tx, y - 40)
    p.L(tx, ret + 60).A(60, 0, ax + 170, ret).L(j, ret).mark("junction")
    p.C(j - 30, ret, j - 50, pk, ax + 30, pk).L(g["park"], pk)
    return p


def _tracks(g: dict) -> list[str]:
    ax, mid, ret, tx = g["ax"], g["mid"], g["ret"], g["tx"]
    j, out = ax + 110, []
    for pk in g["plat"]:
        out.append(Path(ax - 160, pk).L(ax + 30, pk).d)
        out.append(Path(j, ret).C(j - 30, ret, j - 50, pk, ax + 30, pk).d)
        if pk != mid:
            out.append(Path(ax - 140, pk).C(ax - 180, pk, ax - 190, mid, ax - 230, mid).d)
    out.append(Path(ax - 140 if mid in g["plat"] else ax - 230, mid).L(ax - 230, mid).A((ret - mid) / 2, 0, ax - 230, ret)
               .L(ax + 170, ret).A(60, 1, tx, ret + 60).L(tx, g["lines"][-1] - 40).d)
    for y, xs in zip(g["lines"], g["xs"]):
        out.append(Path(tx, y - 40).A(40, 1, tx - 40, y).L(xs - 70, y).A(16, 0, xs - 70, y + 32).L(xs + 20, y + 32)
                   .C(xs + 45, y + 32, xs + 45, y, xs + 70, y).d)
    return out


LOCO = ('<rect class="fm-loco" x="-15" y="-9" width="30" height="18" rx="4"/><rect class="fn-cab" x="-12" y="-6" width="9" height="12" rx="1"/>'
        '<circle class="fm-head" cx="12" cy="0" r="2.4"/>')
WAGON = '<rect class="fm-wagon" x="-13" y="-9" width="26" height="18" rx="2"/><rect class="fm-load" x="-9" y="-5" width="18" height="10"/>'


def _train(p: "Path", points, T: float, begin: str, cars: int) -> str:
    """Each car runs the loop on its own a fixed distance behind the one ahead, so the train bends round every curve. The loop is
    drawn twice so a car behind the locomotive never has to wrap past the start."""
    twice = p.d + " L" + p.d[1:]
    out = ""
    for c in range(cars):
        times, kp = [], []
        for t, d in points:
            kt = round(t / T, 4)
            if times and kt <= times[-1]:
                continue
            times.append(kt)
            kp.append(round((p.len + d - c * GAP) / (2 * p.len), 5))
        times[0], times[-1] = 0.0, 1.0
        out += (f'<g class="fn-car">{LOCO if c == 0 else WAGON}<animateMotion path="{twice}" dur="{T}s" begin="{begin}" rotate="auto" '
                f'calcMode="spline" keyTimes="{";".join(f"{v:g}" for v in times)}" keyPoints="{";".join(f"{v:g}" for v in kp)}" '
                f'keySplines="{";".join([SPLINE] * (len(times) - 1))}" repeatCount="indefinite"/></g>')
    return out


def _yard(g: dict, shown: list[dict], now: float) -> tuple[str, str]:
    """(the yard's track and buildings, its moving parts)."""
    ax, n = g["ax"], len(shown)
    routes = [train_route(g, k) for k in range(n)]
    T, plan = timetable(routes, [bool(w["online"] and w.get("job")) for w in shown])
    begin = f"{-(now % T):.2f}s" if T else "0s"
    rails = "".join(f'<g class="{c}">' + "".join(f'<path d="{d}"/>' for d in _tracks(g)) + "</g>"
                    for c in ("fm-ballast", "fm-ties", "fm-rail", "fm-rail-in", "fm-ties-in"))
    lanes = [pk - 34 for pk in g["plat"]]
    under = [(pk - 15, pk + 15) for pk in g["plat"][:-1]]
    top = BOT_Y + MH + 30
    stops = [top] + [v for u in under for v in u] + [lanes[-1] - 12]
    feeder = "".join(belt(f"M {_f(ax)} {_f(stops[i])} L {_f(ax)} {_f(stops[i + 1])}", "fn-belt thin") for i in range(0, len(stops), 2))
    feeder += "".join(belt(f"M {_f(ax)} {_f(L - 12)} A 12 12 0 0 1 {_f(ax - 12)} {_f(L)} L {_f(g['park'] + GAP)} {_f(L)}", "fn-belt thin") for L in lanes)
    feeder += "".join(underground((ax, a), (ax, b)) for a, b in under)
    still, moving = [rails, feeder, _stop(ax - 170, g["plat"][0] - 70, "Verify stop")], []
    Pf = 8.4
    for k, (w, pk, L) in enumerate(zip(shown, g["plat"], lanes)):
        y, xs, r = g["lines"][k], g["xs"][k], plan[k]
        still.append(_signal(ax - 126, pk - 13, f"Signal at platform {k + 1}", _lamp(r.get("depart"), T, begin)))
        still.append(_signal(xs + 100, y - 21, f'Signal before the trunk, {w["name"]}', _lamp(r.get("go"), T, begin)))
        job = w.get("job")
        still.append(_worker(w, (xs - 80, y - 136), f'#{int(job["issue"])}' if job else "") + _stop(xs - 54, y - 38, w["name"])
                     + belt(f"M {_f(xs)} {_f(y - 38)} L {_f(xs)} {_f(y - 60)}", "fn-belt thin"))
        cars = CARS[k % len(CARS)]
        if r:
            p = routes[k]
            pts = [(0, 0), (r["depart"], 0), (r["arrive"], p.marks["stop"]), (r["leave"], p.marks["stop"]),
                   (r["at_signal"], p.marks["signal"]), (r["go"], p.marks["signal"]), (r["home"], p.len), (T, p.len)]
            moving.append(_train(p, pts, T, begin, cars))
            # Build's work comes down the feeder (under the tracks before it) and is lifted into this train's first wagon
            trip = Path(ax, top).L(ax, L - 12).A(12, 1, ax - 12, L).L(g["park"] + GAP + 2, L)
            b = -((now + k * Pf / max(1, n)) % Pf)
            crate, Tg = crate_trip(trip, [(a - top, c - top) for a, c in under[:k]], "", Pf, b, small=True)
            moving.append(crate + taking_arm(g["park"] + GAP, pk - 17, False, Tg, Pf, b))
        else:
            if w["online"]:
                moving.append("".join(f'<g class="fn-car" transform="translate({_f(g["park"] + c * GAP)} {_f(pk)}) rotate(180)">'
                                      f'{LOCO if c == 0 else WAGON}</g>' for c in range(cars)))
            moving.append(_arm(g["park"] + GAP, pk - 17, False))
    return "".join(still), "".join(moving)


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


DRONE = ('<g class="fm-rotor"><circle cx="-8" cy="-8" r="4.5"/><circle cx="8" cy="-8" r="4.5"/><circle cx="-8" cy="8" r="4.5"/>'
         '<circle cx="8" cy="8" r="4.5"/></g><rect class="fm-dbody" x="-5" y="-5" width="10" height="10" rx="2"/>')


def _drone(path: str, load: str, dur: float, begin: float) -> str:
    return (f'<g class="fm-drone">{DRONE}<rect class="fm-dload {load}" x="-3" y="-3" width="6" height="6"/>'
            f'<animateMotion path="{path}" dur="{_f(dur)}s" begin="{begin:.2f}s" rotate="auto" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.1;.88;1" dur="{_f(dur)}s" begin="{begin:.2f}s" repeatCount="indefinite"/></g>')


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
