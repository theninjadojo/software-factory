"""The Factory floor drawn as a map (one SVG, so the page policy allows its motion; app.js holds it still for people who ask for
reduced motion).

Everything is laid out from what the factory has, not from a fixed picture:

- the stations (poll, classify, route, each stage role in the config, build, review and CI when they are on, the pull request) are
  small buildings (buildings.py) in two rows around one belt loop that runs clockwise. Every station has two inserters: one takes
  its ticket off the belt, one puts it back on. A ticket's crate rides from the station before it to the station it is going to and
  is taken there; a finished one can ride the whole loop back to an earlier station (a CI fix round goes back to Build that way).
  Crates back up at a station waiting for a person and shake at one that failed;
- inside the loop: a splitter sends tickets with no free agent to the queue chest (a return belt side-loads them back before Route),
  a second splitter sends tickets that skip the stages down a bypass that a merger joins back before Build, and a power plant whose
  pipes the belts cross on underground belts (and a pipe-to-ground under the loop);
- GitHub, the ticket source, is a depot in a walled compound: drones bring issues to it and take pull requests back, and its belt
  goes under the wall onto the loop;
- below, the Verify yard: an inserter puts Build's work on a feeder that goes under the tracks to a lane at each worker's platform,
  where an inserter loads the waiting wagon. Every verification worker the factory has seen has a train of a few cars that bend round
  the curves. A train only ever drives forwards: it leaves its platform, rounds the turning loop, runs down the one shared trunk,
  turns off onto its own branch, unloads at the worker's stop, goes round the worker's turning loop and comes back. Signals let one
  train onto the trunk at a time, so the others wait at red. Only a worker with a job drives; an idle one waits at its platform and
  an offline one has no train.

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




# ---------------------------------------------------------------- the factory layout
def layout(n: int) -> dict:
    """Where each station stands: the first half in the top row left to right, the rest in the bottom row right to left (the
    loop runs clockwise, so that is the order a ticket meets them)."""
    per = max(3, (n + 1) // 2)
    pos = {}
    for i in range(n):
        if i < per:
            pos[i] = (X0 + i * PITCH, TOP_Y, True)
        else:
            pos[i] = (X0 + (per - 1 - (i - per)) * PITCH, BOT_Y, False)
    right = X0 + (per - 1) * PITCH + MW + 40
    return {"pos": pos, "per": per, "right": right}


def pick(at) -> tuple:
    """(input pick point, output drop point, inserter pivot y, belt below the building) for a station at (x, y, top)."""
    x, _, top = at
    cx = x + MW / 2
    if top:
        return (cx - 28, T), (cx + 28, T), T - 20, True
    return (cx + 28, B), (cx - 28, B), B + 20, False


class Loop:
    """The belt loop, clockwise: top run, right curve, down, bottom run, left curve, up. Points on it are measured as a distance
    from the start of the top run, and any trip between two points can be drawn and timed."""

    def __init__(self, right: float):
        self.right = right
        r, L, R = 60, LEFT, right
        self.pieces = [("run", (L + r, T), (R - r, T)), ("arc", (R - r, T), (R, T + r)), ("run", (R, T + r), (R, B - r)),
                       ("arc", (R, B - r), (R - r, B)), ("run", (R - r, B), (L + r, B)), ("arc", (L + r, B), (L, B - r)),
                       ("run", (L, B - r), (L, T + r)), ("arc", (L, T + r), (L + r, T))]
        self.lens = [math.dist(a, b) if k == "run" else math.pi * r / 2 for k, a, b in self.pieces]
        self.total = sum(self.lens)
        self.starts = [sum(self.lens[:i]) for i in range(len(self.lens))]

    def at(self, pt) -> float:
        """Distance along the loop of a point on one of its straight runs."""
        for i, (k, a, b) in enumerate(self.pieces):
            if k == "run" and min(a[0], b[0]) - .5 <= pt[0] <= max(a[0], b[0]) + .5 and min(a[1], b[1]) - .5 <= pt[1] <= max(a[1], b[1]) + .5:
                return self.starts[i] + math.dist(a, pt)
        raise ValueError(f"{pt} is not on the loop")

    def trip(self, path, d0: float, d1: float):
        """Extend `path` (a Path already at the point at distance d0) along the loop to distance d1, going forwards."""
        d = d0
        end = d0 + ((d1 - d0) % self.total or self.total)
        while d < end - 1e-6:
            i = max(j for j in range(len(self.starts)) if self.starts[j] <= d % self.total + 1e-6)
            k, a, b = self.pieces[i]
            piece_end = self.starts[i] + self.lens[i] + (d - d % self.total)
            stop = min(end, piece_end)
            if k == "arc" and stop >= piece_end - 1e-6:
                path.A(60, 1, *b)
            else:
                frac = (stop - (self.starts[i] + (d - d % self.total))) / self.lens[i]
                path.L(a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
            d = stop
        return path

    def d(self, cut=None) -> str:
        """The whole loop, drawn from just past `cut` (an x on the top run where it goes underground) round to it."""
        x0 = cut[1] if cut else LEFT + 60
        p = Path(x0, T)
        d0 = self.at((x0, T))
        self.trip(p, d0, self.at((cut[0], T)) if cut else d0)             # no cut: all the way round, closing on the curve
        return p.d


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
            f'<circle class="fn-ins-h" cx="{_f(hx)}" cy="{_f(hy)}" r="2.6"/></g><circle class="fn-ins-p" cx="{_f(x)}" cy="{_f(y)}" r="3"/></g>')


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


def _power(px) -> str:
    return (f'<g class="ma on fn-power" role="img" aria-label="Power: a boiler and a steam engine">'
            f'<rect class="body" x="{px}" y="240" width="64" height="86" rx="3"/><rect class="roof" x="{px - 4}" y="234" width="72" height="8"/>'
            f'<rect class="panel" x="{px + 12}" y="290" width="40" height="24" rx="2"/><rect class="win" x="{px + 18}" y="296" width="28" height="12"/>'
            f'<rect class="body" x="{px + 44}" y="212" width="12" height="24"/>{buildings.smoke(px + 50, 210)}'
            f'<rect class="body" x="{px + 72}" y="256" width="88" height="70" rx="3"/><rect class="panel" x="{px + 80}" y="264" width="44" height="54" rx="2"/>'
            f'{buildings.cog(px + 102, 291, 22, 12, "cog fn-cog")}<rect class="body" x="{px + 128}" y="276" width="26" height="14" rx="2"/>'
            f'<rect class="rod" x="{px + 120}" y="280" width="16" height="6"/>{buildings.light(px + 150, 266)}'
            f'<text class="fn-tag" x="{px + 24}" y="342">POWER</text></g>')


def _queue(cx, n) -> str:
    return (f'<g class="fn-queue" role="img" aria-label="The queue: {n} ticket(s) waiting for a free agent">'
            f'<rect class="fn-chest" x="{_f(cx - 30)}" y="240" width="60" height="52" rx="3"/><path class="fn-band" d="M {_f(cx - 30)} 258 h 60 M {_f(cx - 30)} 274 h 60"/>'
            f'<rect class="fn-lock" x="{_f(cx - 6)}" y="250" width="12" height="10" rx="2"/><text class="fn-tag" x="{_f(cx - 66)}" y="236">QUEUE · {int(n)}</text></g>')


def _depot(x, y, queued: int) -> str:
    return (f'<g class="fm-m run fn-depot" role="img" aria-label="GitHub, where tickets come from and pull requests go">'
            f'<rect class="fm-box" x="{x}" y="{y}" width="{MW}" height="{MH}" rx="3"/>' + buildings.art("github", "run", x + 2, y + 2)
            + f'<rect class="fn-plate" x="{x + 2}" y="{y + 63}" width="{MW - 4}" height="{MH - 65}"/>'
            f'<text class="fn-n" x="{x + 9}" y="{y + 79}">GitHub</text><text class="fn-c" x="{x + MW - 9}" y="{y + 79}">{int(queued)}</text>'
            f'<text class="fn-s" x="{x + 9}" y="{y + 93}">Ticket source</text></g>')


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


# ---------------------------------------------------------------- the whole floor
def floor_map(order: list[tuple[str, str, str]], fl: dict, workers: list[dict], workers_on: bool, now: float,
              word=None, href=None, extras: dict | None = None) -> str:
    """order: (station id, label, icon path) in route order. fl: {station: {"state", "refs", "count"}}. workers: [{"name",
    "online", "job": {"issue", "recipe"} | None}]. extras: {"bypass": [refs that skipped the stages], "fix": [refs on a CI fix
    round], "queued": [refs waiting for a free agent]}."""
    word = word or (lambda sid, o: o["state"])
    href = href or (lambda sid: "#")
    extras = extras or {}
    ids = [sid for sid, _, _ in order]
    n = len(order)
    lay = layout(n)
    pos, right = lay["pos"], lay["right"]
    loop = Loop(right)
    io = {sid: pick(pos[i]) for i, sid in enumerate(ids)}
    at_top = {sid: pos[i][2] for i, sid in enumerate(ids)}
    s, moving = [], []

    # the inside of the loop: the queue after Classify, the bypass after Route, the power plant and its pipes
    q = io["classify"][1][0] + 27 if "classify" in io and "route" in io and at_top["classify"] and at_top["route"] else None
    sx = io["route"][1][0] + 30 if q is not None and "build" in io and not at_top["build"] else None
    xbr = right - 90
    mx = io["build"][0][0] + 50 if sx is not None else None
    bypass = sx is not None and sx < xbr - 60 and mx < xbr - 30
    px = q + 145 if q is not None else None
    power = px is not None and px + 160 < (xbr - 40 if bypass else right - 40)
    cut = None                                                        # where the loop's top run goes under the pipe
    if power:
        gaps = [X0 + j * PITCH + MW + 15 for j in range(lay["per"] - 1)]
        lo = (sx + 60) if bypass else px + 170                      # well clear of the bypass splitter
        gx = next((g for g in gaps if lo < g < (xbr - 30 if bypass else right - 70)), None)
        bots = sorted((io[sid][1][0] + 22 for sid in ids if not at_top[sid] and io[sid][1][0] + 22 < px - 20), reverse=True)
        x2 = bots[0] if bots else None
        if gx is not None:
            cut = (gx - 15, gx + 15)
            s.append(pipe(f"M {px + 160} 270 H {gx} V {TOP_Y + MH + 8} M {gx - 20} {TOP_Y + MH + 8} H {gx + 20}"))
        if x2 is not None:
            s.append(pipe(f"M {px} 345 H {x2} V {B - 22} M {x2} {B + 24} V {BOT_Y}"))
    s.append(belt(loop.d(cut), "fn-belt bus"))
    if cut:
        s.append(underground((cut[0], T), (cut[1], T)))
    if power and x2 is not None:
        s.append(pipe_to_ground(x2, B - 22, "s") + pipe_to_ground(x2, B + 24, "n"))
    queued = extras.get("queued") or []
    if q is not None:
        s.append(belt(f"M {_f(q)} {T + 30} L {_f(q + 10)} {T + 30} A 15 15 0 0 1 {_f(q + 25)} {T + 45} L {_f(q + 25)} {T + 52}"))
        s.append(belt(f"M {_f(q + 25)} {T + 166} L {_f(q + 45)} {T + 166} A 15 15 0 0 0 {_f(q + 60)} {T + 151} L {_f(q + 60)} {T + 10}"))
        s.append(splitter(q, T, T + 30, "Splitter: tickets with no free agent go to the queue") + _queue(q + 25, len(queued)))
    if bypass:
        y1, y2 = T + 30, B - 30
        if cut and sx < cut[0]:
            s.append(belt(f"M {_f(sx)} {y1} L {_f(cut[0])} {y1}") + underground((cut[0], y1), (cut[1], y1)))
            s.append(belt(f"M {_f(cut[1])} {y1} L {_f(xbr - 15)} {y1} A 15 15 0 0 1 {_f(xbr)} {y1 + 15} L {_f(xbr)} {y2 - 15} "
                          f"A 15 15 0 0 1 {_f(xbr - 15)} {y2} L {_f(mx)} {y2}"))
        else:
            s.append(belt(f"M {_f(sx)} {y1} L {_f(xbr - 15)} {y1} A 15 15 0 0 1 {_f(xbr)} {y1 + 15} L {_f(xbr)} {y2 - 15} "
                          f"A 15 15 0 0 1 {_f(xbr - 15)} {y2} L {_f(mx)} {y2}"))
        s.append(splitter(sx, T, y1, "Splitter: stage work one way, tickets that skip the stages the other")
                 + splitter(mx, y2, B, "Merger: the bypass joins the loop into Build"))
        s.append(f'<text class="fn-tag" x="{_f(sx + 12)}" y="{T + 50}">BYPASS</text>')
    if power:
        s.append(_power(px))
    s.append(f'<text class="fn-tag" x="{LEFT + 100}" y="{T + 40}">THE LOOP · CLOCKWISE</text>')

    # GitHub's compound: the depot, its belt under the wall onto the loop
    s.insert(0, f'<rect class="fn-compound" x="0" y="0" width="164" height="{BOT_Y + MH + 16}"/>'
                f'<rect class="fn-wall" x="152" y="0" width="12" height="{BOT_Y + MH + 16}"/><text class="fm-lab" x="12" y="24">TICKET SOURCE</text>')
    s.append(belt(f"M 40 334 L 136 334") + belt("M 178 334 L 200 334") + underground((136, 334), (178, 334)))
    s.append(_depot(12, 200, len(queued)))

    def hidden_on(path_start_d, length, gaps):
        out = []
        for a, b in gaps:
            ra = (a - path_start_d) % loop.total
            if ra + (b - a) <= length:
                out.append((ra, ra + (b - a)))
        return out
    cut_d = [(loop.at((cut[0], T)), loop.at((cut[1], T)))] if cut else []

    # crates: each ticket rides from the station before it to the one taking it
    taken, given, plans = set(), set(), []
    for i, sid in enumerate(ids):
        o = fl[sid]
        if o["state"] != "run" or not o["refs"]:
            continue
        ref = o["refs"][0]
        dst = io[sid][0]
        if sid == "build" and ref in (extras.get("fix") or []) and "ci" in io:
            src, start = "ci", io["ci"][1]
        elif sid == "build" and ref in (extras.get("bypass") or []) and bypass:
            src, start = "route", None
        elif i == 0:
            src, start = "depot", None
        else:
            src, start = ids[i - 1], io[ids[i - 1]][1]
        if src == "route":
            o0 = io["route"][1]
            path = (Path(*o0).L(sx, T).L(sx + 7, T + 30))
            hid = []
            if cut and sx < cut[0]:
                path.L(cut[0], T + 30).mark("a").L(cut[1], T + 30).mark("b")
                hid = [(path.marks["a"], path.marks["b"])]
            path.L(xbr - 15, T + 30).A(15, 1, xbr, T + 45).L(xbr, B - 45).A(15, 1, xbr - 15, B - 30).L(mx + 7, B - 30).L(mx - 7, B).L(*dst)
        elif src == "depot":
            path = Path(82, 334).L(136, 334).mark("a").L(178, 334).mark("b").L(LEFT, 334)
            hid = [(path.marks["a"], path.marks["b"])]
            d0 = loop.at((LEFT, 334))
            base = path.len
            loop.trip(path, d0, loop.at(dst))
            hid += [(base + a, base + b) for a, b in hidden_on(d0, path.len - base, cut_d)]
        else:
            path = Path(*start)
            d0 = loop.at(start)
            loop.trip(path, d0, loop.at(dst))
            hid = hidden_on(d0, path.len, cut_d)
        plans.append((src, sid, path, hid, ref))
    if queued and q is not None:
        path = Path(82, 334).L(136, 334).mark("a").L(178, 334).mark("b").L(LEFT, 334)
        hid = [(path.marks["a"], path.marks["b"])]
        d0, base = loop.at((LEFT, 334)), path.len
        loop.trip(path, d0, loop.at((q, T)))
        hid += [(base + a, base + b) for a, b in hidden_on(d0, path.len - base, cut_d)]
        path.L(q + 7, T + 30).L(q + 10, T + 30).A(15, 1, q + 25, T + 45).L(q + 25, T + 47)
        plans.append(("depot", "queue", path, hid, queued[0]))
    for k, (src, dst, path, hid, ref) in enumerate(plans):
        if dst in taken:
            continue
        P = path.len / BELT_V + GRAB + 2 * SWING + 0.6
        begin = -((now + k * 1.9) % P)
        crate, Tg = crate_trip(path, hid, ref, P, begin)
        moving.append(crate)
        if dst == "queue":
            moving.append(taking_arm(q + 25, T + 64, False, Tg, P, begin))
        else:
            _, _, py, below = io[dst]
            moving.append(taking_arm(io[dst][0][0], py, below, Tg, P, begin))
        taken.add(dst)
        if src == "depot":
            moving.append(giving_arm(82, 316, False, P, begin))
        elif src not in given:
            _, (ox, _), py, below = io[src]
            moving.append(giving_arm(ox, py, not below, P, begin))
        given.add(src)
    for sid in ids:
        (ix, _), (ox, _), py, below = io[sid]
        if sid not in taken:
            moving.append(_arm(ix, py, below))
        if sid not in given:
            moving.append(_arm(ox, py, not below))
    if "depot" not in given:
        moving.append(_arm(82, 316, False))
    if q is not None:
        if "queue" not in taken:
            moving.append(_arm(q + 25, T + 64, False))
        moving.append(_arm(q + 25, T + 148, False))               # the way out of the queue waits for a free agent
    for sid in ids:
        o, (ix, iy) = fl[sid], io[sid][0]
        away = -1 if at_top[sid] else 1
        if o["state"] == "wait":
            moving += [_still_crate(ix + away * (12 + j * 26), iy, ref) for j, ref in enumerate(o["refs"][:2])]
        elif o["state"] == "fail" and o["refs"]:
            moving.append(_still_crate(ix + away * 14, iy, o["refs"][0], stuck=True))

    # the stations themselves, over the belts
    stations = "".join(_station(sid, label, fl[sid], word(sid, fl[sid]), href(sid), pos[i], sid)
                       for i, (sid, label, _) in enumerate(order))

    # the yard and the workers
    shown = (workers or [])[:MAX_WORKERS]
    width = right + 30
    height = BOT_Y + MH + 120
    yard_still = yard_moving = ""
    build_x = io["build"][1][0] + 10 + 18 if "build" in io else right - 300
    feed_arm = ""
    if shown:
        g = yard_geometry(len(shown), build_x)
        fit = sum(1 for xs in g["xs"] if xs - 86 >= 0)
        if fit < len(shown):
            shown = shown[:max(1, fit)]
            g = yard_geometry(len(shown), build_x)
        yard_still, yard_moving = _yard(g, shown, now)
        width = max(width, g["tx"] + 40)
        height = g["bottom"] + 10
        zone = g["ret"] + 40
        busy = any(w["online"] and w.get("job") for w in shown)
        Pf3 = 8.4 / max(1, len(shown))
        feed_arm = (giving_arm(build_x, BOT_Y + MH + 12, False, Pf3, -(now % Pf3)) if busy else _arm(build_x, BOT_Y + MH + 12, False))
        if workers_on and len(shown) < MAX_WORKERS:
            yard_still += (f'<a class="fm-add" href="/workers"><rect x="40" y="{_f(zone + 40)}" width="180" height="44" rx="2"/>'
                           f'<text x="130" y="{_f(zone + 67)}">+ Connect a worker</text></a>')
        more = len(workers) - len(shown)
        if more > 0:
            yard_still += f'<text class="fm-r" x="40" y="{_f(zone + 110)}">and {more} more worker{"s" if more != 1 else ""}</text>'
        outside = (f'<rect class="fm-zone" x="0" y="{_f(zone)}" width="{_f(width)}" height="{_f(height - zone)}"/>'
                   f'<text class="fm-lab" x="16" y="{_f(zone + 22)}">OUTSIDE THE FACTORY · WORKERS BY TRAIN OVER THE SSH TUNNEL</text>'
                   f'<text class="fm-lab" x="{_f(build_x - 330)}" y="{_f(g["plat"][0] - 52)}">VERIFY YARD</text>')
        yard_still = outside + yard_still
    elif workers_on:
        yard_still = (f'<a class="fm-add" href="/workers"><rect x="{_f(build_x - 90)}" y="{BOT_Y + MH + 40}" width="180" height="44" rx="2"/>'
                      f'<text x="{_f(build_x)}" y="{BOT_Y + MH + 67}">+ Connect a worker</text></a>')
    more = len(workers or []) - len(shown)
    if not shown and more > 0:
        yard_still += f'<text class="fm-r" x="40" y="{BOT_Y + MH + 110}">and {more} more workers</text>'

    # GitHub's drones: issues in from beyond the map, pull requests from the dock back to the pad
    pad = (94, 221)
    pr = (io["pr"][0][0], BOT_Y + 50) if "pr" in io else (LEFT + 70, BOT_Y + 50)
    din = f"M -40 -30 C 40 40 {pad[0] - 40} {pad[1] - 120} {pad[0]} {pad[1]}"
    dout = f"M {_f(pr[0])} {_f(pr[1])} C {_f(pr[0] - 40)} {_f(pr[1] - 120)} {pad[0] + 100} {pad[1] + 140} {pad[0]} {pad[1]}"
    drones = _drone(din, "in", 5, -(now % 5)) + _drone(din, "in", 5, -((now + 2.5) % 5))
    if fl.get("pr", {}).get("count") or fl.get("ci", {}).get("count"):
        drones += _drone(dout, "out", 6, -((now + 1) % 6))
    return (f'<svg class="fm" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'<rect class="fm-ground" width="{_f(width)}" height="{_f(height)}"/>'
            f'<g aria-hidden="true">{"".join(s)}</g>{yard_still}<g aria-hidden="true">{yard_moving}{"".join(moving)}{feed_arm}</g>'
            f'{stations}<g aria-hidden="true">{drones}</g></svg>')


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
