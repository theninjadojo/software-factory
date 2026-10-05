"""The Factory floor as a working plant (one SVG, so the page policy allows its motion; app.js holds it still for people who ask
for reduced motion). Laid out from what the factory has, not a fixed picture.

West to east: the mainland, where tickets you add are loaded into a 747; the sea, where each schedule's ship sails a round trip
to its source (it sits at its share of the interval since the job last ran, and docks at Receiving when the next run is due); then
the plant itself:

- Sources: GitHub, whose drones lift off when the poll comes round and fly the new issues to Receiving.
- Intake (Poll, Classify, Route): one belt from Receiving (under the compound wall) past all three. A splitter after Classify sends
  tickets with no free agent to the queue chest; they rejoin before Route. A splitter after Route sends stage work into Planning and
  the rest down a bypass that side-loads onto the main street to Build.
- Planning (each stage role in the config): a loop that tickets circle until each stage they need has taken them; a splitter after
  the last lets them out, under the loop's own bend, to the main street.
- Production (Build): in from the main street, out to the quality line, and out its other side to the feeder for the trains.
- Quality (Review and CI, when they are on) and Shipping (the pull request): Build → Review → CI → Shipping. CI's fix rounds ride a
  return belt under the quality line back into Build's side.
- Power: a station per enabled AI agent harness, lit while one of its runs is going, with lines to the stations whose agents use it;
  Claude Code's steam plant pipes to Planning, and the belts go under the pipe.
- The cargo airfield south of the compound, where the 747 lands and its crate rides a conveyor into Receiving, and below it the Verify
  yard and the workers' trains (yard.py).

Every station has an inserter that takes its ticket's crate off the belt and one that puts it back; a crate rides only to the
station it is for. Animations start at the server clock's phase of their cycle, so the five-second refresh does not restart them.
All text is escaped; no style attributes."""
import math

from . import buildings, terrain, yard
from .views import esc
from .yard import Path, _f, belt, underground, splitter, pipe, crate_trip, _still_crate, _station

MW, MH, PITCH = 140, 100, 160
TOP, BUS, PLB, STREET, QA, RET = 30, 170, 236, 296, 465, 635
BOT = 500                                 # the quality and shipping row
BUILD = (860, 330)
SEA_W, LAND_W = 300, 240
FX = LAND_W + SEA_W                       # where the plant starts on the whole floor
G_ = 20                                    # a saved layout's grid cell (floorplan.G)
INTAKE = ("poll", "classify", "route")


# ---------------------------------------------------------------- where everything stands
def layout(order: list[str]) -> dict:
    roles = [s for s in order if s not in INTAKE + ("build", "review", "ci", "pr")]
    pos = {"poll": (200, TOP), "classify": (360, TOP), "route": (520, TOP), "build": BUILD, "pr": (200, BOT)}
    for i, r in enumerate(roles):
        pos[r] = (720 + PITCH * i, TOP)
    quality = [s for s in ("review", "ci") if s in order]
    for j, s in enumerate(quality):
        pos[s] = (600 - 180 * j, BOT)
    pos = {s: p for s, p in pos.items() if s in order}
    r = len(roles)
    s3 = pos[roles[-1]][0] + MW / 2 + 28 + 22 if roles else 690          # the splitter out of Planning
    exit_x = s3 + 95
    px = 720 + PITCH * (r - 1) - 10 if r >= 2 else None                  # where the steam pipe climbs to Planning
    width = max(exit_x + 45 + 230, BUILD[0] + 176 + 230 + 60)
    return {"pos": pos, "roles": roles, "quality": quality, "s3": s3, "exit_x": exit_x, "pipe_x": px, "power_x": exit_x + 45, "width": width}


def cx(lay, sid):
    return lay["pos"][sid][0] + MW / 2


def pick(lay, sid):
    """(input pick point, output drop point, inserter pivot y, angle from the pivot to the belt)."""
    x, y = lay["pos"][sid]
    c = x + MW / 2
    if y == TOP:
        return (c - 28, BUS), (c + 28, BUS), TOP + MH + 20, 90
    return (c + 28, QA), (c - 28, QA), BOT - 18, -90


# ---------------------------------------------------------------- inserters at any angle (the angle points at where it takes from)
def arm(x, y, ang, rot="", carry="", reach=18):
    """reach: from the pivot to the hand (a saved layout stands the pivot halfway between the building and its belt)."""
    hx, hy = x + reach * math.cos(math.radians(ang)), y + reach * math.sin(math.radians(ang))
    n = 6 if reach >= 18 else 5
    return (f'<g class="fn-ins"><rect class="fn-ins-base" x="{_f(x - n)}" y="{_f(y - n)}" width="{2 * n}" height="{2 * n}" rx="2"/>'
            f'<g>{rot}<line class="fn-ins-l" x1="{_f(x)}" y1="{_f(y)}" x2="{_f(hx)}" y2="{_f(hy)}"/>'
            f'<rect class="fn-ins-c" x="{_f(hx - 4)}" y="{_f(hy - 4)}" width="8" height="8" opacity="0">{carry}</rect>'
            f'{yard.hand(hx, hy, ang)}</g><circle class="fn-ins-p" cx="{_f(x)}" cy="{_f(y)}" r="3"/></g>')


def taking(x, y, ang, Tg, P, b, reach=18):
    k = lambda t: f"{min(t / P, 1):.4f}"
    return arm(x, y, ang, yard._rot(x, y, (0, 0, 180, 0, 0), f"0;{k(Tg)};{k(Tg + yard.SWING)};{k(Tg + 2 * yard.SWING)};1", P, b),
               yard._anim("opacity", "0;1;0", f"0;{k(Tg)};{k(Tg + yard.SWING - .02)}", P, b), reach)


def giving(x, y, ang, P, b, reach=18):
    k = lambda t: f"{min(t / P, 1):.4f}"
    return arm(x, y, ang, yard._rot(x, y, (180, 0, 0, 180), f"0;{k(yard.SWING)};{k(P - yard.SWING)};1", P, b),
               yard._anim("opacity", "0;1", f"0;{k(P - yard.SWING)}", P, b), reach)


# ---------------------------------------------------------------- belts
def belts(lay, queued: int, steam: bool, trains: bool = True) -> str:
    b, pos, roles = [], lay["pos"], lay["roles"]
    bx, by = BUILD
    bin_x = bx + MW / 2 - 28
    out = []
    # intake: Receiving's belt under the compound wall, up, and along under Poll, Classify and Route
    out.append(belt("M 70 334 L 136 334") + belt("M 178 334 L 188 334 A 12 12 0 0 0 200 322 L 200 182 A 12 12 0 0 1 212 170 L 645 170", "fn-belt bus"))
    out.append(underground((136, 334), (178, 334)))
    # the queue after Classify
    q = cx(lay, "classify") + 28 + 27 if "classify" in pos else 485
    out.append(belt(f"M {_f(q)} 200 L {_f(q + 10)} 200 A 15 15 0 0 1 {_f(q + 25)} 215 L {_f(q + 25)} 222"))
    out.append(belt(f"M {_f(q + 25)} 336 L {_f(q + 45)} 336 A 15 15 0 0 0 {_f(q + 60)} 321 L {_f(q + 60)} 180"))
    out.append(splitter(q, BUS, 200, "Splitter: tickets with no free agent go to the queue") + _queue_chest(q + 25, queued))
    px = lay["pipe_x"] if steam else None
    cut = (px - 15, px + 15) if px else None
    s3, exit_x = lay["s3"], lay["exit_x"]
    if roles:
        # Planning's loop: along the top under the stages, round the bend, back along the bottom, up and side-loaded in again
        top = [(645, s3 + 10)]
        bottom = [(s3 + 10, 730)]
        out.append(_run_with_cut(645, s3 + 10, BUS, cut, "fn-belt bus"))
        out.append(belt(f"M {_f(s3 + 10)} {BUS} A 33 33 0 0 1 {_f(s3 + 10)} {PLB}", "fn-belt bus"))
        out.append(_run_with_cut(s3 + 10, 730, PLB, cut, "fn-belt bus"))
        out.append(belt(f"M 730 {PLB} A 33 33 0 0 1 697 203 L 697 182", "fn-belt bus"))
        out.append(splitter(645, BUS, 200, "Splitter: stage work into Planning, the rest down the bypass"))
        out.append(splitter(s3, BUS, 200, "Splitter: tickets done with Planning leave for Build"))
        # the exit goes under the loop's bend, then down to the main street
        out.append(belt(f"M {_f(s3 + 7)} 200 L {_f(s3 + 16)} 200") + underground((s3 + 16, 200), (s3 + 66, 200))
                   + belt(f"M {_f(s3 + 66)} 200 L {_f(exit_x - 15)} 200 A 15 15 0 0 1 {_f(exit_x)} 215 L {_f(exit_x)} {STREET - 15} "
                          f"A 15 15 0 0 1 {_f(exit_x - 15)} {STREET}", "fn-belt bus"))
        out.append(_run_with_cut(exit_x - 15, bin_x - 12, STREET, cut, "fn-belt bus"))
        # the bypass: down from the splitter, along under Planning, side-loaded onto the main street east of Build's input
        sl = bx + MW / 2 + 20
        out.append(belt(f"M 652 200 A 12 12 0 0 1 664 212 L 664 254 A 12 12 0 0 0 676 266 L {_f(sl - 15)} 266 A 15 15 0 0 1 {_f(sl)} 281 L {_f(sl)} 286"))
    else:
        out.append(belt(f"M 645 {BUS} L 980 {BUS} A 15 15 0 0 1 995 {BUS + 15} L 995 {STREET - 15} A 15 15 0 0 1 980 {STREET} L {_f(bin_x - 12)} {STREET}", "fn-belt bus"))
    # quality: from Build west through Review and CI to Shipping
    out.append(belt(f"M {_f(bx + MW / 2 + 28)} {QA} L 190 {QA}", "fn-belt bus"))
    # CI's fix rounds back into Build's side, under the quality line
    if "ci" in pos:
        rx = bx - 30
        out.append(belt(f"M {_f(cx(lay, 'ci'))} {RET} L {_f(rx - 12)} {RET} A 12 12 0 0 0 {_f(rx)} {RET - 12} L {_f(rx)} 490") + belt(f"M {_f(rx)} 440 L {_f(rx)} 380"))
        out.append(underground((rx, 490), (rx, 440)))
    # Build's work for the trains: out of its other side, down to the yard
    if trains:                                                     # only when there are workers to check it
        fx = bx + MW + 36
        out.append(belt(f"M {_f(fx)} 380 L {_f(fx)} {yard.BOT_Y + yard.MH + 30}", "fn-belt thin"))
    if cut:
        out.append(underground((cut[0], BUS), (cut[1], BUS)))
        if roles and cut[1] < s3 + 10 and cut[0] > 730:
            out.append(underground((cut[0], PLB), (cut[1], PLB)))
        if bin_x < cut[0] and cut[1] < exit_x - 15:
            out.append(underground((cut[0], STREET), (cut[1], STREET)))
    return "".join(out)


def _run_with_cut(x0, x1, y, cut, cls):
    """A straight east-west run, broken where it goes under the pipe."""
    lo, hi = min(x0, x1), max(x0, x1)
    if cut and lo < cut[0] and cut[1] < hi:
        a, b = (cut[0], cut[1]) if x1 > x0 else (cut[1], cut[0])
        return belt(f"M {_f(x0)} {y} L {_f(a)} {y}", cls) + belt(f"M {_f(b)} {y} L {_f(x1)} {y}", cls)
    return belt(f"M {_f(x0)} {y} L {_f(x1)} {y}", cls)


def _queue_chest(c, n):
    return (f'<g class="fn-queue" role="img" aria-label="The queue: {int(n)} ticket(s) waiting for a free agent">'
            f'<rect class="fn-chest" x="{_f(c - 30)}" y="250" width="60" height="52" rx="3"/><path class="fn-band" d="M {_f(c - 30)} 268 h 60 M {_f(c - 30)} 284 h 60"/>'
            f'<rect class="fn-lock" x="{_f(c - 6)}" y="260" width="12" height="10" rx="2"/><text class="fn-tag" x="{_f(c - 66)}" y="246">QUEUE · {int(n)}</text></g>')


DIST = {"INTAKE": ("poll", "route"), "PLANNING": None, "PRODUCTION": None, "QUALITY": None, "SHIPPING": None}


def districts(lay) -> str:
    out, pos = [], lay["pos"]
    boxes = [("INTAKE", 184, 6, 492, 350)]
    if lay["roles"]:
        boxes.append(("PLANNING", 700, 6, lay["exit_x"] + 29 - 700, 270))
    boxes.append(("PRODUCTION", BUILD[0] - 20, 312, MW + 44, 136))
    if lay["quality"]:
        xs = [pos[s][0] for s in lay["quality"]]
        boxes.append(("QUALITY", min(xs) - 20, BOT - 18, max(xs) + MW + 20 - (min(xs) - 20), 140))
    if "pr" in pos:
        boxes.append(("SHIPPING", 184, BOT - 18, 196, 140))
    for n, x, y, w, h in boxes:
        out.append(f'<rect class="f3-dist d-{n.lower()}" x="{_f(x)}" y="{y}" width="{_f(w)}" height="{h}" rx="4"/><text class="f3-dlab d-{n.lower()}" x="{_f(x + 8)}" y="{y + 14}">{n}</text>')
    return "".join(out)


# ---------------------------------------------------------------- crates: each ticket rides from the station before it to the one taking it
def crates(lay, order, fl, extras, now) -> str:
    pos, roles = lay["pos"], lay["roles"]
    parts, took, gave = [], set(), set()
    bx = BUILD[0]
    bin_x = bx + MW / 2 - 28
    px = lay["pipe_x"] if extras.get("steam") else None
    cut = (px - 15, px + 15) if px else None

    def along(path, y, x_to):
        """Extend a path east or west along y, marking where it goes under the pipe."""
        hid = []
        x_from = path.cur[0]
        if cut and min(x_from, x_to) < cut[0] and cut[1] < max(x_from, x_to):
            a, b = (cut[0], cut[1]) if x_to > x_from else (cut[1], cut[0])
            path.L(a, y)
            s = path.len
            path.L(b, y)
            hid.append((s, path.len))
        path.L(x_to, y)
        return hid

    def go(src_arm, dst_arm, path, hid, label, k):
        P = path.len / yard.BELT_V + yard.GRAB + 2 * yard.SWING + 0.6
        b = -((now + k * 1.9) % P)
        crate, Tg = crate_trip(path, hid, label, P, b)
        parts.append(crate + taking(*dst_arm, Tg, P, b))
        if src_arm:
            parts.append(giving(*src_arm, P, b))

    k = 0
    for i, sid in enumerate(order):
        o = fl.get(sid) or {}
        if o.get("state") != "run" or not o.get("refs") or sid in took:
            continue
        ref = o["refs"][0]
        if sid == "build":
            dst = (bin_x, STREET + 17, -90)
            if ref in (extras.get("fix") or []) and "ci" in pos:
                rx = bx - 30
                p = Path(cx(lay, "ci"), RET).L(rx - 12, RET).A(12, 0, rx, RET - 12).L(rx, 490)
                s = p.len
                p.L(rx, 440)
                hid = [(s, p.len)]
                p.L(rx, 380)
                go((cx(lay, "ci"), 617, -90), (bx - 12, 380, 180), p, hid, ref, k)
                took.add("build-side")
            elif ref in (extras.get("bypass") or []) and roles and "route" in pos:
                o0 = pick(lay, "route")[1]
                sl = bx + MW / 2 + 20
                p = (Path(*o0).L(645, BUS).L(652, 200).A(12, 1, 664, 212).L(664, 254).A(12, 0, 676, 266).L(sl - 15, 266)
                     .A(15, 1, sl, 281).L(sl, STREET).L(bin_x, STREET))
                go((o0[0], TOP + MH + 20, -90), dst, p, [], ref, k)
                gave.add("route")
                took.add("build")
            elif roles:
                last = roles[-1]
                o0 = pick(lay, last)[1]
                s3, ex = lay["s3"], lay["exit_x"]
                p = Path(*o0).L(s3, BUS).L(s3 + 7, 200).L(s3 + 16, 200)
                s = p.len
                p.L(s3 + 66, 200)
                hid = [(s, p.len)]
                p.L(ex - 15, 200).A(15, 1, ex, 215).L(ex, STREET - 15).A(15, 1, ex - 15, STREET)
                hid += along(p, STREET, bin_x)
                go((o0[0], TOP + MH + 20, -90), dst, p, hid, ref, k)
                gave.add(last)
                took.add("build")
            k += 1
            continue
        if i == 0 or sid not in pos:
            continue
        prev = order[i - 1]
        if prev not in pos or prev == "build":
            if prev == "build" and sid in pos:                 # Build → the first quality station (or shipping)
                d = pick(lay, sid)[0]
                p = Path(bx + MW / 2 + 28, QA).L(*d)
                go((bx + MW / 2 + 28, 447, -90), (d[0], BOT - 18, -90), p, [], ref, k)
                took.add(sid)
                k += 1
            continue
        (_, o0, py0, ang0), (d, _, py1, ang1) = (pick(lay, prev)[0], pick(lay, prev)[1], pick(lay, prev)[2], pick(lay, prev)[3]), \
                                                  (pick(lay, sid)[0], None, pick(lay, sid)[2], pick(lay, sid)[3])
        if o0[1] == d[1] and ((o0[1] == BUS and d[0] > o0[0]) or (o0[1] == QA and d[0] < o0[0])):
            p = Path(*o0)
            hid = along(p, o0[1], d[0])
            go((o0[0], py0, -ang0), (d[0], py1, ang1), p, hid, ref, k)
            took.add(sid)
            gave.add(prev)
            k += 1
    # a queued ticket: from Receiving under the wall, onto the belt, split off to the queue
    queued = extras.get("queued") or []
    if queued and "classify" in pos:
        q = cx(lay, "classify") + 28 + 27
        p = Path(80, 334).L(136, 334)
        s = p.len
        p.L(178, 334)
        hid = [(s, p.len)]
        p.L(188, 334).A(12, 0, 200, 322).L(200, 182).A(12, 1, 212, 170).L(q, BUS).L(q + 7, 200).L(q + 10, 200).A(15, 1, q + 25, 215).L(q + 25, 222)
        go((80, 316, -90), (q + 25, 234, -90), p, hid, queued[0], k)
        took.add("queue")
    # every other inserter waits
    for sid in order:
        if sid not in pos or sid == "build":
            continue
        (ix, _), (ox, _), py, ang = pick(lay, sid)
        if sid not in took:
            parts.append(arm(ix, py, ang))
        if sid not in gave:
            parts.append(arm(ox, py, -ang))
    if "build" in pos:
        if "build" not in took:
            parts.append(arm(bin_x, STREET + 17, -90))
        parts.append(arm(bx + MW / 2 + 28, 447, -90))
        if "build-side" not in took and "ci" in pos:
            parts.append(arm(bx - 12, 380, 180))
        if "ci" in pos:
            parts.append(arm(cx(lay, "ci"), 617, -90))
    if "queue" not in took and "classify" in pos:
        q = cx(lay, "classify") + 28 + 27
        parts.append(arm(q + 25, 234, -90))
    if "classify" in pos:
        q = cx(lay, "classify") + 28 + 27
        parts.append(arm(q + 25, 318, -90))
    if not queued:
        parts.append(arm(80, 316, -90))
    parts.append(arm(22, 316, 90))                               # Receiving takes in the airfield's conveyor
    # waiting and stuck crates at their doors
    for sid in order:
        o = fl.get(sid) or {}
        if sid not in pos or sid == "build":
            continue
        (ix, iy), _, _, _ = pick(lay, sid)
        away = -1 if iy == BUS else 1
        if o.get("state") == "wait":
            parts += [_still_crate(ix + away * (12 + j * 26), iy, r) for j, r in enumerate(o.get("refs", [])[:2])]
        elif o.get("state") == "fail" and o.get("refs"):
            parts.append(_still_crate(ix + away * 14, iy, o["refs"][0], stuck=True))
    return "".join(parts)


# ---------------------------------------------------------------- power: a station per enabled AI agent harness
PW, PH = 200, 92
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "gemini": "Gemini", "opencode-openrouter": "OpenRouter", "opencode-zen": "OpenCode Zen"}


def _steam(x, y):
    return (f'<rect class="body" x="{x + 8}" y="{y + 14}" width="40" height="52" rx="2"/><rect class="win" x="{x + 16}" y="{y + 44}" width="24" height="10"/>'
            f'<rect class="body" x="{x + 30}" y="{y + 2}" width="8" height="14"/>{buildings.smoke(x + 34, y + 2)}'
            f'<rect class="body" x="{x + 54}" y="{y + 24}" width="52" height="42" rx="2"/>{buildings.cog(x + 74, y + 45, 15, 10, "cog pw-cog")}')


def _solar(x, y):
    return "".join(f'<rect class="pw-panel" x="{x + 8 + c * 27}" y="{y + 10 + r * 20}" width="22" height="14" rx="1"/>'
                   f'<path class="pw-glint g{(r + c) % 3}" d="M {x + 10 + c * 27} {y + 22 + r * 20} l 8 -10"/>' for r in range(2) for c in range(3))


def _wind(x, y):
    out = ""
    for k, (tx, h) in enumerate(((x + 28, 50), (x + 68, 42))):
        hy = y + 62 - h
        blades = "".join(f'<path class="pw-blade" d="M {tx} {hy} l -2 -17 l 4 0 z" transform="rotate({a} {tx} {hy})"/>' for a in (0, 120, 240))
        out += (f'<line class="pw-mast" x1="{tx}" y1="{y + 64}" x2="{tx}" y2="{hy}"/><g class="pw-rotor">{blades}'
                f'<animateTransform attributeName="transform" type="rotate" from="0 {tx} {hy}" to="360 {tx} {hy}" dur="{2.2 - k * .5}s" repeatCount="indefinite"/></g>'
                f'<circle class="pw-hub" cx="{tx}" cy="{hy}" r="2.5"/>')
    return out


def _nuclear(x, y):
    return (f'<path class="pw-tower" d="M {x + 52} {y + 64} Q {x + 60} {y + 34} {x + 54} {y + 14} H {x + 84} Q {x + 78} {y + 34} {x + 86} {y + 64} Z"/>'
            f'<rect class="pw-reactor" x="{x + 8}" y="{y + 26}" width="38" height="38" rx="3"/><circle class="pw-core" cx="{x + 27}" cy="{y + 45}" r="9"/>'
            f'<circle class="pw-ring" cx="{x + 27}" cy="{y + 45}" r="13"/>')


def _accum(x, y):
    return "".join(f'<rect class="pw-acc" x="{x + 10 + k * 26}" y="{y + 18}" width="20" height="44" rx="2"/>'
                   f'<rect class="pw-charge c{k}" x="{x + 13 + k * 26}" y="{y + 22}" width="14" height="36"/>' for k in range(3))


ART = {"claude-code": _steam, "codex": _solar, "gemini": _wind, "opencode-openrouter": _nuclear, "opencode-zen": _accum}


def _pole_line(points) -> str:
    out = ""
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        out += f'<path class="pw-wire" d="M {_f(x1)} {_f(y1 - 6)} Q {_f((x1 + x2) / 2)} {_f((y1 + y2) / 2 + 4)} {_f(x2)} {_f(y2 - 6)}"/>'
    return out + "".join(f'<g class="pw-pole"><line x1="{_f(x)}" y1="{_f(y)}" x2="{_f(x)}" y2="{_f(y - 8)}"/><line x1="{_f(x - 4)}" y1="{_f(y - 7)}" x2="{_f(x + 4)}" y2="{_f(y - 7)}"/></g>'
                         for x, y in points)


def power(lay, harnesses: list[dict]) -> tuple[str, str]:
    """(the power column with its lines, the steam pipe to Planning or "")."""
    x0 = lay["power_x"]
    out = [f'<rect class="pw-area" x="{_f(x0 - 16)}" y="0" width="{PW + 32}" height="{_f(14 + 106 * len(harnesses) + 74)}"/>']
    pipe_d = ""
    for k, h in enumerate(harnesses):
        y = 14 + k * (PH + 14)
        uses = [s for s in h.get("uses", []) if s in lay["pos"]]
        out.append(power_station(x0, y, h, uses))
        cy = y + PH / 2
        top = [s for s in uses if lay["pos"][s][1] == TOP]
        low = [s for s in uses if lay["pos"][s][1] != TOP]
        if h["name"] == "claude-code" and lay["pipe_x"] and any(s in lay["roles"] for s in uses):
            px = lay["pipe_x"]
            pipe_d = f"M {_f(x0)} {_f(cy)} H {_f(x0 - 30)} V 316 H {_f(px)} V {TOP + MH - 2} M {_f(px - 30)} {TOP + MH - 2} H {_f(px + 30)}"
            top = [s for s in top if s not in lay["roles"][-2:]]
        if top:
            yc = 8 + 5 * k
            pts = [(x0 - 10 - 6 * k, cy), (x0 - 10 - 6 * k, yc)] + [(cx(lay, s), yc) for s in sorted(top, key=lambda s: -cx(lay, s))]
            out.append(_pole_line(pts) + "".join(f'<path class="pw-wire" d="M {_f(cx(lay, s))} {_f(yc - 6)} V {TOP}"/>' for s in top))
        if low:
            yc = 650 + 5 * k
            pts = [(x0 - 10 - 6 * k, cy), (x0 - 10 - 6 * k, yc)] + [(cx(lay, s), yc) for s in sorted(low, key=lambda s: -cx(lay, s))]
            out.append(_pole_line(pts) + "".join(f'<path class="pw-wire" d="M {_f(cx(lay, s))} {_f(yc - 6)} V {lay["pos"][s][1] + MH}"/>' for s in low))
    by = 14 + len(harnesses) * (PH + 14)
    out.append(f'<a class="pw-add" href="/harnesses"><rect x="{_f(x0)}" y="{by}" width="{PW}" height="44" rx="2"/>'
               f'<text x="{_f(x0 + PW / 2)}" y="{by + 27}">+ Connect an AI agent</text></a>'
               f'<text class="fm-lab" x="{_f(x0)}" y="{by + 66}">POWER · AI AGENTS</text>')
    return "".join(out), (pipe(pipe_d) if pipe_d else "")


def power_station(x0, y, h: dict, uses: list[str]) -> str:
    state = "on" if h.get("on") else "idle"
    sub = ("running" if h.get("on") else "idle") + (f" · {len(uses)} station{'s' if len(uses) != 1 else ''}" if uses else "")
    name = NAMES.get(h["name"], h["name"])
    return (f'<g class="pw {state}" role="img" aria-label="Power for {esc(name)}: {esc(sub)}">'
            f'<rect class="pw-box" x="{_f(x0)}" y="{y}" width="{PW}" height="{PH}" rx="3"/>'
            f'<g class="ma{" on" if state == "on" else ""}">{ART.get(h["name"], _accum)(x0, y + 8)}</g>'
            f'<text class="pw-n" x="{_f(x0 + 112)}" y="{y + 36}">{esc(name[:14])}</text><text class="pw-s" x="{_f(x0 + 112)}" y="{y + 52}">{esc(sub)}</text></g>')


# ---------------------------------------------------------------- sources: GitHub and its drones, Receiving
SRC, PAD, RECV_PAD = (12, 40), (106, 60), (94, 221)


def sources(queued: int) -> str:
    return (f'<rect class="fn-compound" x="0" y="0" width="164" height="{BOT + MH + 16}"/><rect class="fn-wall" x="152" y="0" width="12" height="{BOT + MH + 16}"/>'
            f'<text class="fm-lab" x="12" y="24">SOURCES</text>' + github() + receiving(queued))


def github() -> str:
    """GitHub's building and drone pad, at SRC."""
    x, y = SRC
    return (f'<g class="sw-src" role="img" aria-label="Source: GitHub, where tickets come from"><rect class="sw-box" x="{x}" y="{y}" width="140" height="66" rx="3"/>'
            f'<rect class="sw-rack" x="{x + 10}" y="{y + 12}" width="22" height="34" rx="2"/><rect class="sw-rack" x="{x + 36}" y="{y + 12}" width="22" height="34" rx="2"/>'
            + "".join(f'<circle class="sw-led l{k % 3}" cx="{x + 16 + (k % 2) * 26}" cy="{y + 18 + (k // 2) * 8}" r="1.6"/>' for k in range(8))
            + f'<ellipse class="sw-pad" cx="{PAD[0]}" cy="{PAD[1]}" rx="24" ry="9"/><path class="sw-h" d="M {PAD[0] - 6} {PAD[1] - 4} v 8 M {PAD[0] + 6} {PAD[1] - 4} v 8 M {PAD[0] - 6} {PAD[1]} h 12"/>'
            f'<text class="sw-n" x="{x + 10}" y="{y + 60}">GitHub</text><text class="sw-s" x="{x + 132}" y="{y + 60}">SOURCE</text></g>'
            f'<g class="sw-add"><rect x="{x}" y="{y + 76}" width="140" height="30" rx="2"/><text x="{x + 70}" y="{y + 95}">More sources later</text></g>')


def receiving(queued: int) -> str:
    """Receiving, at (12, 200)."""
    return (f'<g class="fm-m run fn-depot" role="img" aria-label="Receiving: tickets the drones, ships and planes bring in">'
            f'<rect class="fm-box" x="12" y="200" width="{MW}" height="{MH}" rx="3"/>' + buildings.art("github", "run", 14, 202)
            + f'<rect class="fn-plate" x="14" y="263" width="{MW - 4}" height="{MH - 65}"/>'
            f'<text class="fn-n" x="21" y="279">Receiving</text><text class="fn-c" x="{12 + MW - 9}" y="279">{int(queued)}</text>'
            f'<text class="fn-s" x="21" y="293">Ticket intake</text></g>')


def drones(now: float, every: float, last: float | None, PAD=PAD, RECV_PAD=RECV_PAD) -> str:
    """Parked on GitHub's pad; when the poll comes round a swarm of five flies out, lands at Receiving with the issues, and comes back.
    A saved layout passes where the two pads stand."""
    T = max(20.0, float(every or 60))
    p = (Path(*PAD).C(PAD[0] + 120, PAD[1] - 70, PAD[0] + 260, PAD[1] + 40, PAD[0] + 150, RECV_PAD[1] - 50)
         .C(PAD[0] + 90, RECV_PAD[1] - 80, RECV_PAD[0] + 40, RECV_PAD[1] - 20, *RECV_PAD).mark("land")
         .C(RECV_PAD[0] - 60, RECV_PAD[1] - 40, PAD[0] - 80, PAD[1] + 40, *PAD))
    land = p.marks["land"] / p.len
    begin = -((now - (last or now)) % T) if last else -(now % T)
    out, parked = "", ""
    for k in range(5):
        d = k * 0.35
        t0, t1, t2, t3 = (1.0 + d) / T, (6.0 + d) / T, (7.2 + d) / T, (11.0 + d) / T
        fly = yard._anim("opacity", "0;1;0", f"0;{t0:.4f};{t3:.4f}", T, begin)
        out += (f'<g class="fm-drone" opacity="0"><g transform="translate(0 {(k - 2) * 9})">{yard.DRONE}</g>{fly}'
                f'<animateMotion path="{p.d}" dur="{T:g}s" begin="{begin:.2f}s" rotate="auto" calcMode="spline" keyTimes="0;{t0:.4f};{t1:.4f};{t2:.4f};{t3:.4f};1" '
                f'keyPoints="0;0;{land:.4f};{land:.4f};1;1" keySplines="{";".join([yard.SPLINE] * 5)}" repeatCount="indefinite"/></g>')
        sit = yard._anim("opacity", "1;0;1", f"0;{t0:.4f};{t3:.4f}", T, begin)
        parked += f'<g class="fm-drone" transform="translate({PAD[0] - 16 + (k % 3) * 16} {PAD[1] - 5 + (k // 3) * 10}) scale(.6)">{yard.DRONE}{sit}</g>'
    return parked + out


# ---------------------------------------------------------------- the sea: a ship per schedule
SHIP = ('<path class="sh-hull" d="M -24 -8 L 14 -8 Q 26 0 14 8 L -24 8 Z"/><rect class="sh-deck" x="-20" y="-5" width="26" height="10" rx="1"/>'
        '<rect class="sh-cab" x="-20" y="-4" width="7" height="8" rx="1"/>')
CARGO = '<rect class="sh-load" x="-10" y="-4" width="12" height="8"/><rect class="sh-load b" x="3" y="-3.5" width="7" height="7"/>'
DOCK = (276, 250)


# The coast, as the design draws it on a sea 200 wide and 400 tall (y from -20 to 420): five curves, water to the west of them.
COAST = ((138, -20), ((160, 10), (176, 38), (160, 72)), ((146, 102), (182, 128), (178, 168)), ((175, 200), (150, 222), (164, 258)),
         ((178, 292), (192, 318), (170, 350)), ((156, 372), (168, 396), (176, 420)))


def _coast(w: float, h: float) -> list:
    """The coastline of a sea w by h, as points from top to bottom."""
    sx, sy = w / 200, h / 400
    p0, out = COAST[0], []
    for c1, c2, p3 in COAST[1:]:
        for i in range(12):
            t = i / 12
            x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t ** 2 * c2[0] + t ** 3 * p3[0]
            y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t ** 2 * c2[1] + t ** 3 * p3[1]
            out.append((x * sx, y * sy))
        p0 = p3
    return out + [(p0[0] * sx, p0[1] * sy)]


def coast_x(w: float, h: float, y: float) -> float:
    """Where the coast of a sea w by h is at height y."""
    pts = _coast(w, h)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if y0 <= y <= y1:
            return x0 + (x1 - x0) * ((y - y0) / (y1 - y0) if y1 > y0 else 0)
    return pts[-1][0] if y > pts[-1][1] else pts[0][0]


def sea_water(w: float, h: float) -> str:
    """The sea's water, w by h: deep to the west and lighter towards a winding coast with a sandy beach and foam where the waves
    break; east of the beach is land (the floor shows through)."""
    pts = _coast(w, h)
    line = " ".join(f"L {_f(x)} {_f(y)}" for x, y in pts[1:])
    shore = f"M {_f(pts[0][0])} {_f(pts[0][1])} {line}"
    body = f"M 0 {_f(pts[0][1])} L {_f(pts[0][0])} {_f(pts[0][1])} {line} L 0 {_f(pts[-1][1])} Z"
    return (f'<defs><linearGradient id="sh-depth" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0a1a26"/>'
            f'<stop offset=".65" stop-color="#0f2c3d"/><stop offset="1" stop-color="#1b4a5e"/></linearGradient></defs>'
            f'<clipPath id="sh-clip"><rect x="0" y="0" width="{_f(w)}" height="{_f(h)}"/></clipPath><g clip-path="url(#sh-clip)">'
            f'<path class="sh-sand" d="{body}" transform="translate({_f(7 * w / 200)} 0)"/><path class="sh-water" d="{body}"/>'
            f'<path class="sh-foam" d="{shore}" transform="translate({_f(-4 * w / 200)} 0)"/></g>')


def sea(trips: list[dict], height: float, dock_y: float = DOCK[1], water: bool = True, coast=None, pier: bool = False) -> str:
    """In the sea's own coordinates (x 0..300). dock_y: the height of the dock and the crane, at the coast (a saved layout sets it to
    the harbor's). water: draw the water here (a saved layout draws it under the whole box instead; coast then says where its coast
    is in these coordinates). pier: a quay from the crane east to x 300, where Receiving stands on the default floor."""
    cx = (coast or (lambda y: coast_x(SEA_W, height, y)))
    wall = cx(dock_y)
    dock = (wall - 24, dock_y)
    out = ([sea_water(SEA_W, height)] if water else []) + [f'<text class="fm-lab sh-lab" x="16" y="24">THE SEA · SCHEDULED JOBS</text>']
    for i in range(30):
        x, y = 20 + (i * 61) % 250, 60 + (i * 137) % int(height - 80)
        if x + 34 < cx(y):                                           # waves only on the water
            out.append(f'<path class="sh-wave w{i % 3}" d="M {x} {y} q 6 -4 12 0 t 12 0"/>')
    low = min(cx(y) for y in range(0, int(height), 20))
    step = max(20.0, min(66.0, (low - 100) / 3))                     # the ships' lanes stay off the beach
    lines = marks = ships = ""
    for k, t in enumerate(trips[:4]):
        xo, xr = 26 + step * k, 60 + step * k
        yf = height - 200 - 40 * k
        p = (Path(*dock).L(xo + 14, dock[1] - 14).A(14, 0, xo, dock[1]).L(xo, yf).A(17, 0, xr, yf).L(xr, dock[1] + 28)
             .A(14, 1, xr + 14, dock[1] + 14).L(*dock))
        due = t["due"]
        lines += f'<path class="sh-course{" due" if due else ""}" d="{p.d}"/>'
        mx = (xo + xr) / 2
        marks += (f'<g class="sh-src"><circle cx="{_f(mx)}" cy="{_f(yf + 17)}" r="7"/><text class="sh-srcname" x="{_f(mx)}" y="{_f(yf + 40)}">{esc(t["source"][:8])}</text></g>'
                  f'<g transform="translate({_f(mx + 4)} {_f(dock[1] + 80)}) rotate(90)"><text class="sh-name" x="0" y="0">{esc(t["name"][:24])}</text>'
                  f'<text class="sh-when{" due" if due else ""}" x="0" y="11">{esc(t["when"])}</text></g>')
        f = max(0.0, min(1.0, t["progress"]))
        place = f'<animateMotion path="{p.d}" keyPoints="{f:.4f};{f:.4f}" keyTimes="0;1" calcMode="linear" dur="1s" rotate="auto" fill="freeze"/>'
        ships += (f'<g class="sh-ship{" due" if due else ""}" role="img" aria-label="Schedule {esc(t["name"])}: {esc(t["when"])}">{place}'
                  f'<g class="sh-rock">{SHIP}{CARGO if f >= 0.5 else ""}</g></g>')
    crane = f'<g class="sh-crane"><line class="sh-jib" x1="{_f(wall)}" y1="{_f(dock[1])}" x2="{_f(wall - 22)}" y2="{_f(dock[1])}"/><circle class="sh-base" cx="{_f(wall)}" cy="{_f(dock[1])}" r="7"/></g>'
    if any(t["due"] for t in trips):
        Tc = 4.0
        crane = (f'<g class="sh-crane"><g><animateTransform attributeName="transform" type="rotate" values="0 {_f(wall)} {_f(dock[1])};0 {_f(wall)} {_f(dock[1])};180 {_f(wall)} {_f(dock[1])};180 {_f(wall)} {_f(dock[1])};0 {_f(wall)} {_f(dock[1])}" '
                 f'keyTimes="0;.15;.5;.6;1" dur="{Tc}s" repeatCount="indefinite"/><line class="sh-jib" x1="{_f(wall)}" y1="{_f(dock[1])}" x2="{_f(wall - 22)}" y2="{_f(dock[1])}"/>'
                 f'<rect class="sh-cargo" x="{_f(wall - 27)}" y="{_f(dock[1] - 5)}" width="10" height="10" opacity="0"><animate attributeName="opacity" values="0;1;0" keyTimes="0;.15;.5" '
                 f'calcMode="discrete" dur="{Tc}s" repeatCount="indefinite"/></rect></g><circle class="sh-base" cx="{_f(wall)}" cy="{_f(dock[1])}" r="7"/></g>')
    if not trips:
        lines = (f'<text class="sh-when" x="20" y="60">No scheduled jobs yet.</text>'
                 f'<a class="sh-add" href="/schedules"><text x="20" y="76">Add one in Settings, Schedules</text></a>')
    quay = f'<rect class="sh-wall" x="{_f(wall)}" y="{_f(dock_y - 6)}" width="{_f(SEA_W - wall)}" height="12"/>' if pier and wall < SEA_W else ""
    return "".join(out) + lines + marks + ships + quay + f'<rect class="sh-wall" x="{_f(wall - 4)}" y="{_f(dock[1] - 54)}" width="8" height="108"/>' + crane


def harbor(x, y, due: bool = False) -> str:
    """The harbor, 120 by 80 at (x, y): a basin and its quay, where the scheduled jobs' ships unload. A saved layout puts it anywhere and
    a belt takes its tickets to Receiving; when a run is due a ship lies at the quay and the crane swings its cargo ashore."""
    cx, cy = x + 98, y + 50
    ship = (f'<g class="sh-ship due" transform="translate({_f(x + 52)} {_f(y + 30)})"><g class="sh-rock">{SHIP}{CARGO}</g></g>' if due else "")
    crane = f'<line class="sh-jib" x1="{_f(cx)}" y1="{_f(cy)}" x2="{_f(cx - 22)}" y2="{_f(cy - 14)}"/>'
    if due:
        crane = (f'<g><animateTransform attributeName="transform" type="rotate" values="0 {_f(cx)} {_f(cy)};0 {_f(cx)} {_f(cy)};70 {_f(cx)} {_f(cy)};70 {_f(cx)} {_f(cy)};0 {_f(cx)} {_f(cy)}" '
                 f'keyTimes="0;.15;.5;.6;1" dur="4s" repeatCount="indefinite"/>{crane}</g>')
    return (f'<g class="hb{" due" if due else ""}" role="img" aria-label="The harbor: ships with the scheduled jobs unload here{", a ship is in" if due else ""}">'
            f'<rect class="hb-box" x="{_f(x)}" y="{_f(y)}" width="120" height="80" rx="3"/><rect class="sh-sea" x="{_f(x + 4)}" y="{_f(y + 4)}" width="112" height="40"/>'
            f'<path class="sh-wave w0" d="M {_f(x + 14)} {_f(y + 16)} q 6 -4 12 0 t 12 0"/><path class="sh-wave w1" d="M {_f(x + 70)} {_f(y + 30)} q 6 -4 12 0 t 12 0"/>'
            f'{ship}<rect class="sh-wall" x="{_f(x + 4)}" y="{_f(y + 44)}" width="112" height="8"/>'
            + "".join(f'<circle class="hb-bollard" cx="{_f(x + 16 + 22 * k)}" cy="{_f(y + 48)}" r="2"/>' for k in range(3))
            + f'<g class="sh-crane">{crane}<circle class="sh-base" cx="{_f(cx)}" cy="{_f(cy)}" r="6"/></g>'
            f'<text class="fn-n" x="{_f(x + 8)}" y="{_f(y + 66)}">Harbor</text><text class="fn-s" x="{_f(x + 8)}" y="{_f(y + 76)}">Ships dock</text></g>')


# ---------------------------------------------------------------- notifiers: wireless, so they stand anywhere and take no belt
NOTIFY_LINK = {"telegram": "/telegram"}


def _arc(cx, cy, r) -> str:
    return f'M {_f(cx - r * .72)} {_f(cy - r * .1)} A {r} {r} 0 0 1 {_f(cx + r * .72)} {_f(cy - r * .1)}'


def notifier(x, y, n: dict) -> str:
    """A notifier, 120 by 80 at (x, y): a mast that broadcasts while its channel is set up (questions, failures and pull requests reach
    a person there wherever it stands)."""
    from .floorplan import NOTIFY_NAMES
    on = bool(n.get("on"))
    name = NOTIFY_NAMES.get(n["name"], n["name"])
    tx, ty = x + 30, y + 22
    waves = "".join(f'<path class="nt-wave w{k}" d="{_arc(tx, ty, 7 + 6 * k)}"/>' for k in range(3))
    return (f'<g class="nt{" on" if on else ""}" role="img" aria-label="Notifier {esc(name)}: {"set up, wireless" if on else "not set up"}">'
            f'<rect class="nt-box" x="{_f(x)}" y="{_f(y)}" width="120" height="80" rx="3"/>'
            f'<rect class="nt-body" x="{_f(x + 12)}" y="{_f(y + 44)}" width="36" height="24" rx="2"/><rect class="win" x="{_f(x + 18)}" y="{_f(y + 50)}" width="10" height="6" rx="1"/>'
            f'<rect class="win" x="{_f(x + 32)}" y="{_f(y + 50)}" width="10" height="6" rx="1"/>'
            f'<path class="nt-mast" d="M {_f(tx)} {_f(y + 44)} V {_f(ty + 4)} M {_f(tx - 7)} {_f(y + 44)} L {_f(tx)} {_f(ty + 10)} L {_f(tx + 7)} {_f(y + 44)}"/>'
            f'<circle class="nt-tip" cx="{_f(tx)}" cy="{_f(ty + 2)}" r="2.5"/>{waves}'
            f'<text class="nt-n" x="{_f(x + 56)}" y="{_f(y + 50)}">{esc(name)}</text>'
            f'<text class="nt-s" x="{_f(x + 56)}" y="{_f(y + 64)}">{"wireless · on" if on else "not set up"}</text></g>')


def notifiers(x0, y, items: list[dict]) -> str:
    """The default floor's notifiers, in a column under power."""
    if not items:
        return ""
    out = "".join(notifier(x0 + 40, y + k * 92, n) for k, n in enumerate(items))
    return out + f'<text class="fm-lab" x="{_f(x0)}" y="{_f(y + len(items) * 92 + 12)}">NOTIFIERS · WIRELESS</text>'


# ---------------------------------------------------------------- the mainland's airport, the plant's airfield, and a 747
PLANE = ('<path class="ap-wing" d="M 2 0 L -10 -30 L -16 -30 L -8 0 L -16 30 L -10 30 Z"/><path class="ap-tail" d="M -24 0 L -30 -11 L -33 -11 L -29 0 L -33 11 L -30 11 Z"/>'
         '<rect class="ap-eng" x="-5" y="-21" width="7" height="3.5" rx="1.5"/><rect class="ap-eng" x="-8" y="-13" width="7" height="3.5" rx="1.5"/>'
         '<rect class="ap-eng" x="-5" y="17.5" width="7" height="3.5" rx="1.5"/><rect class="ap-eng" x="-8" y="9.5" width="7" height="3.5" rx="1.5"/>'
         '<path class="ap-body" d="M 26 0 Q 26 -4 18 -4.5 L -30 -3 Q -34 0 -30 3 L 18 4.5 Q 26 4 26 0 Z"/>'
         '<path class="ap-hump" d="M 22 -1.5 Q 21 -3 14 -3.2 L 6 -3 L 6 3 L 14 3.2 Q 21 3 22 1.5 Z"/>')
MR = (100, 250, 40, 820)                   # the mainland runway (whole-floor coordinates)
GATE = (120, 1160)
AF_Y = 662                                  # the airfield's top (plant coordinates)
AR = (60, AF_Y + 108, 600, 38)              # the airfield runway (plant coordinates)
APRON = (170, AF_Y + 62)


def _tower(x, y):
    return (f'<rect class="ap-tw" x="{_f(x - 5)}" y="{_f(y)}" width="10" height="44"/><path class="ap-twc" d="M {_f(x - 12)} {_f(y)} h 24 l -4 -14 h -16 z"/>'
            f'<rect class="ap-glass" x="{_f(x - 9)}" y="{_f(y - 11)}" width="18" height="6"/><circle class="ap-bea" cx="{_f(x)}" cy="{_f(y - 18)}" r="2.5"/>')


def mainland(height: float) -> str:
    rx, ry, rw, rh = MR
    return (f'<rect class="ap-land" x="0" y="0" width="{LAND_W}" height="{_f(height)}"/><text class="fm-lab" x="14" y="24">THE MAINLAND</text>'
            f'<rect class="ap-strip" x="{rx}" y="{ry}" width="{rw}" height="{rh}" rx="3"/><path class="ap-cl" d="M {rx + rw / 2} {ry + 14} V {ry + rh - 14}"/>'
            f'<path class="ap-edge" d="M {rx + 3} {ry} V {ry + rh} M {rx + rw - 3} {ry} V {ry + rh}"/>'
            + "".join(f'<rect class="ap-thr" x="{rx + 6 + k * 7}" y="{ry + 6}" width="4" height="18"/><rect class="ap-thr" x="{rx + 6 + k * 7}" y="{ry + rh - 24}" width="4" height="18"/>' for k in range(4))
            + f'<path class="ap-taxi" d="M {GATE[0]} {GATE[1]} V {ry + rh}"/><rect class="ap-apron" x="20" y="1120" width="200" height="90" rx="4"/>'
            f'<rect class="ap-term" x="26" y="1214" width="150" height="56" rx="4"/><rect class="ap-glass" x="34" y="1222" width="134" height="10"/>'
            f'<text class="ap-name" x="34" y="1258">DEPARTURES</text>' + _tower(196, 1220)
            + f'<text class="fm-lab" x="14" y="1300">AIRPORT · TICKETS YOU ADD</text>')


def airfield(conveyor: bool = True) -> str:
    """In the plant's coordinates, south of the compound: runway, taxiway, cargo terminal, hangar, tower, and the conveyor to Receiving
    (a saved layout draws that as a belt of its own)."""
    ax, ay, aw, ah = AR
    conv = f"M 52 {AF_Y + 18} V 600 H 22 V 336"
    return (f'<rect class="ap-field" x="20" y="{AF_Y}" width="680" height="210" rx="6"/><text class="fm-lab" x="120" y="{AF_Y + 22}">AIRFIELD · CARGO</text>'
            f'<rect class="ap-strip" x="{ax}" y="{ay}" width="{aw}" height="{ah}" rx="3"/><path class="ap-cl" d="M {ax + 16} {ay + ah / 2} H {ax + aw - 16}"/>'
            f'<path class="ap-edge" d="M {ax} {ay + 3} H {ax + aw} M {ax} {ay + ah - 3} H {ax + aw}"/>'
            + "".join(f'<rect class="ap-thr" x="{ax + 6}" y="{ay + 6 + k * 7}" width="18" height="4"/><rect class="ap-thr" x="{ax + aw - 24}" y="{ay + 6 + k * 7}" width="18" height="4"/>' for k in range(4))
            + f'<path class="ap-taxi" d="M {ax + aw - 20} {ay} V {APRON[1] + 16} H {APRON[0]}"/><rect class="ap-apron" x="120" y="{AF_Y + 40}" width="140" height="48" rx="4"/>'
            f'<rect class="ap-term" x="40" y="{AF_Y + 30}" width="70" height="60" rx="4"/><rect class="ap-glass" x="46" y="{AF_Y + 38}" width="58" height="8"/>'
            f'<text class="ap-name" x="46" y="{AF_Y + 80}">CARGO</text><rect class="ap-hangar" x="300" y="{AF_Y + 32}" width="110" height="56" rx="26"/>'
            + _tower(460, AF_Y + 44) + (belt(conv, "fn-belt thin") if conveyor else ""))


def flight(age: float, label: str, mo=(0, 0), ao=(FX, 0), conveyor=None, ms=(1, 1), as_=(1, 1)) -> str:
    """A 747 from the mainland's gate to the plant's cargo terminal (14 s), `age` seconds in; the crate then rides into Receiving.
    mo, ao: where the mainland and the airfield are drawn from (a saved layout moves them); ms, as_: how much they are stretched (a
    saved layout resizes them); conveyor: the points of the belt from the airfield to Receiving when a saved layout routes it."""
    mx, my = mo
    ox, oy = ao
    M = lambda x, y: (mx + x * ms[0], my + y * ms[1])
    A = lambda x, y: (ox + 20 + (x - 20) * as_[0], oy + AF_Y + (y - AF_Y) * as_[1])
    rx, ry = M(*MR[:2])
    rw, rh = MR[2] * ms[0], MR[3] * ms[1]
    ax, ay = A(*AR[:2])
    aw, ah = AR[2] * as_[0], AR[3] * as_[1]
    c = rx + rw / 2
    y1 = ay + ah / 2
    apx, apy = A(*APRON)
    p = (Path(*M(*GATE)).L(c, ry + rh - 30 * ms[1]).mark("lined").L(c, ry + 380 * ms[1]).mark("lift")
         .C(c, ry + 160 * ms[1], c + 140, 300 + my, 300 + mx, 330 + my).C(420 + mx, 360 + my, 480 + ox - FX, y1 - 40, ax - 120, y1)
         .L(ax + 30, y1).mark("touch").L(ax + aw - 60, y1).mark("roll")
         .A(24, 0, ax + aw - 20, y1 - 30).L(ax + aw - 20, apy + 30).A(14, 0, ax + aw - 34, apy + 16).L(apx + 30, apy + 16))
    k = lambda m: f"{p.marks[m] / p.len:.4f}"
    T, b = 14.0, f"{-age:.2f}s"
    motion = (f'<animateMotion path="{p.d}" dur="{T}s" begin="{b}" fill="freeze" rotate="auto" calcMode="spline" '
              f'keyTimes="0;0.08;0.16;0.26;0.66;0.74;0.90;1" keyPoints="0;0;{k("lined")};{k("lift")};{k("touch")};{k("roll")};1;1" '
              f'keySplines=".4 0 .6 1;.4 0 .6 1;.6 0 1 1;.3 0 .7 1;0 0 .4 1;.4 0 .6 1;0 0 1 1"/>')
    keys = 'keyTimes="0;0.08;0.16;0.26;0.38;0.56;0.66;0.9;1"'
    climb = f'<animateTransform attributeName="transform" type="scale" values="1.4;1.4;1.4;1.4;2.3;2.3;1.4;1.4;1.4" {keys} dur="{T}s" begin="{b}" fill="freeze"/>'
    shadow = (f'<animateTransform attributeName="transform" type="translate" values="3 3;3 3;3 3;3 3;30 36;30 36;3 3;3 3;3 3" {keys} '
              f'dur="{T}s" begin="{b}" fill="freeze"/>')
    plane = (f'<g class="ap-plane">{motion}<g class="ap-shadow">{shadow}<g transform="scale(1.4)">{PLANE}</g></g><g>{climb}{PLANE}</g></g>'
             f'<g class="ap-tag">{motion.replace(" rotate=\"auto\"", "")}<text x="0" y="-40">{esc(label)}</text></g>')
    tx, ty = A(70, AF_Y + 50)
    ride = (f"M {_f(apx + 20)} {_f(apy + 16)} L {_f(tx)} {_f(ty)} " + (" ".join(f"L {_f(x)} {_f(y)}" for x, y in conveyor) if conveyor
            else f"L {ox + 52} {oy + AF_Y + 18} V {oy + 600} H {ox + 22} V {oy + 336}"))
    off = (f'<rect class="fn-item" x="-7" y="-7" width="14" height="14" opacity="0">'
           f'<animateMotion path="{ride}" dur="3s" begin="{T * 0.92 - age:.2f}s" fill="freeze"/>'
           f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.03;.97;1" dur="3s" begin="{T * 0.92 - age:.2f}s" fill="freeze"/></rect>')
    return plane + off


FLIGHT_SECONDS = 17.0


# ---------------------------------------------------------------- the whole floor
def _night(tn: dict, width: float, height: float) -> str:
    """The time of day over the floor: a tint (dusk and night; clear by day) and a warm glow round each lamp that comes on as it gets
    dark. Both are clear until the page's Time buttons (static/app.js, data-tod on the page) say otherwise, so the picture is the
    daytime one without a script."""
    lamps = "".join(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(s)}"/>' for kind, x, y, s, _ in tn.get("items") or [] if kind == "lamp")
    return (f'<rect class="fm-tod" width="{_f(width)}" height="{_f(height)}" aria-hidden="true"/>'
            f'<g class="fm-nglow" aria-hidden="true">{lamps}</g>')


def floor_map(order: list[tuple[str, str, str]], fl: dict, workers: list[dict], workers_on: bool, now: float,
              word=None, href=None, extras: dict | None = None, plan: dict | None = None) -> str:
    """order: (station id, label, icon path) in route order. fl: {station: {"state", "refs", "count"}}. workers: [{"name", "online",
    "job"}]. extras: bypass, fix, queued (ticket refs); power ([{"name", "uses", "on"}]); schedules ([{"name", "source", "when",
    "progress", "due"}]); poll ({"every", "last"}); flights ([{"label", "age"}]). plan: a saved layout (floorplan.compile_plan),
    drawn instead of the default arrangement."""
    word = word or (lambda sid, o: o["state"])
    href = href or (lambda sid: "#")
    extras = extras or {}
    if plan:
        return planned_map(order, fl, workers, workers_on, now, word, href, extras, plan)
    ids = [sid for sid, _, _ in order]
    lay = layout(ids)
    harnesses = extras.get("power") or []
    steam = any(h["name"] == "claude-code" and any(s in lay["roles"] for s in h.get("uses", [])) for h in harnesses) and bool(lay["pipe_x"])
    ex = {**extras, "steam": steam}
    # the yard under Build's feeder
    shown = (workers or [])[:yard.MAX_WORKERS]
    feed_x = BUILD[0] + MW + 36
    yard_still = yard_moving = ""
    height = AF_Y + 240
    if shown:
        g = yard.yard_geometry(len(shown), feed_x)
        fit = sum(1 for xs in g["xs"] if xs - 86 >= 0)
        if fit < len(shown):
            shown = shown[:max(1, fit)]
            g = yard.yard_geometry(len(shown), feed_x)
        yard_still, yard_moving = yard._yard(g, shown, now)
        height = max(height, g["bottom"] + 10)
        zone = g["ret"] + 40
        busy = any(w["online"] and w.get("job") for w in shown)
        Pf = 8.4 / max(1, len(shown))
        yard_moving += giving(BUILD[0] + MW + 18, 380, 180, Pf, -(now % Pf)) if busy else arm(BUILD[0] + MW + 18, 380, 180)
        yard_still = (f'<rect class="fm-zone" x="0" y="{_f(zone)}" width="{_f(lay["width"])}" height="{_f(height - zone)}"/>'
                      f'<text class="fm-lab" x="16" y="{_f(zone + 22)}">OUTSIDE THE FACTORY · WORKERS BY TRAIN OVER THE SSH TUNNEL</text>' + yard_still)
        if workers_on and len(shown) < yard.MAX_WORKERS:
            yard_still += (f'<a class="fm-add" href="/workers"><rect x="40" y="{_f(zone + 40)}" width="180" height="44" rx="2"/>'
                           f'<text x="130" y="{_f(zone + 67)}">+ Connect a worker</text></a>')
        more = len(workers) - len(shown)
        if more > 0:
            yard_still += f'<text class="fm-r" x="40" y="{_f(zone + 110)}">and {more} more worker{"s" if more != 1 else ""}</text>'
    else:
        if workers_on:
            yard_still = (f'<a class="fm-add" href="/workers"><rect x="{_f(feed_x - 90)}" y="{AF_Y + 230}" width="180" height="44" rx="2"/>'
                          f'<text x="{_f(feed_x)}" y="{AF_Y + 257}">+ Connect a worker</text></a>')
            height = AF_Y + 290
    height = max(height, 1320)                                       # the mainland's airport needs the room
    queued = extras.get("queued") or []
    plant_svg, pipe_svg = power(lay, harnesses)
    poll = extras.get("poll") or {}
    nt = notifiers(lay["power_x"], 14 + len(harnesses) * (PH + 14) + 90, extras.get("notify") or [])
    plant = (sources(len(queued)) + districts(lay) + airfield() + pipe_svg + belts(lay, len(queued), steam, bool(shown))
             + f'<g aria-hidden="true">{yard_still}</g>' + plant_svg + nt
             + f'<g aria-hidden="true">{crates(lay, ids, fl, ex, now)}{yard_moving}</g>'
             + "".join(_station(sid, label, fl[sid], word(sid, fl[sid]), href(sid), (*lay["pos"][sid], True), sid)
                       for sid, label, _ in order if sid in lay["pos"])
             + f'<g aria-hidden="true">{drones(now, poll.get("every"), poll.get("last"))}</g>')
    width = FX + lay["width"]
    flights = "".join(flight(f["age"], f["label"]) for f in (extras.get("flights") or []) if 0 <= f["age"] < FLIGHT_SECONDS)
    return (f'<svg class="fm" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'<rect class="fm-ground" width="{_f(width)}" height="{_f(height)}"/>'
            f'<g aria-hidden="true">{mainland(height)}</g><g transform="translate({LAND_W} 0)">{sea(extras.get("schedules") or [], height, pier=True)}</g>'
            f'<g transform="translate({FX} 0)">{plant}</g><g aria-hidden="true">{flights}</g></svg>')


# ---------------------------------------------------------------- a saved layout (floorplan.py): the same parts, where a person put them
R = 10                                      # a belt's corner radius


def _unit(a, b):
    d = math.dist(a, b)
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _on_seg(a, b, u) -> bool:
    return ((a[0] == b[0] == u[0] and min(a[1], b[1]) <= u[1] <= max(a[1], b[1]))
            or (a[1] == b[1] == u[1] and min(a[0], b[0]) <= u[0] <= max(a[0], b[0])))


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def trace(pts, under=(), r=R) -> tuple[Path, list]:
    """A belt's line through its points with rounded corners, and the stretches (from, to distances) it runs underground."""
    pts = _dedupe(pts)
    p, hidden = Path(*pts[0]), []
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        base = p.len - math.dist(p.cur, a)                          # the distance at a, as if the corner were square
        hidden += [(base + math.dist(a, u), base + math.dist(a, v)) for u, v in under if _on_seg(a, b, u) and _on_seg(a, b, v)]
        if i == len(pts) - 1:
            p.L(*b)
            break
        c = pts[i + 1]
        d1, d2 = _unit(a, b), _unit(b, c)
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-9:
            p.L(*b)
            continue
        rr = min(r, math.dist(a, b) / 2, math.dist(b, c) / 2)
        p.L(b[0] - d1[0] * rr, b[1] - d1[1] * rr)
        p.A(rr, 1 if cross > 0 else 0, b[0] + d2[0] * rr, b[1] + d2[1] * rr)
    return p, sorted((a, b) for a, b in hidden if a < b)


def _visible(pts, under) -> list[list]:
    """The belt's points cut where it goes underground: the runs drawn on top."""
    parts, cur = [], [pts[0]]
    for a, b in zip(pts, pts[1:]):
        cuts = sorted(((u, v) for u, v in under if _on_seg(a, b, u) and _on_seg(a, b, v)), key=lambda uv: math.dist(a, uv[0]))
        for u, v in cuts:
            cur.append(u)
            parts.append(cur)
            cur = [v]
        cur.append(b)
    parts.append(cur)
    return [q for q in (_dedupe(q) for q in parts) if len(q) >= 2]


REACH = 10                                  # half the cell between a building and its belt


def _pivot(box, pt):
    """Where an inserter stands, halfway between a building and the belt point beside it (its hand reaches the belt one way and the
    building's edge the other), and its angle towards the belt."""
    x, y, w, h = box
    if pt[1] < y:
        return (pt[0], y - REACH, -90)
    if pt[1] > y + h:
        return (pt[0], y + h + REACH, 90)
    if pt[0] < x:
        return (x - REACH, pt[1], 180)
    return (x + w + REACH, pt[1], 0)


def _piece(p) -> str:
    x, y = p["at"]
    d = p["dir"]
    name = {"splitter": "Splitter", "merger": "Merger", "sideload": "Side-load"}[p["kind"]]
    if p["kind"] == "sideload":
        return (f'<g class="fn-split" role="img" aria-label="{name}"><rect x="{_f(x - 9)}" y="{_f(y - 9)}" width="18" height="18" rx="3"/>'
                f'<path class="fn-arrow" transform="translate({_f(x)} {_f(y)})" d="{yard.ARROW[d]}"/></g>')
    across = d in ("e", "w")
    w, h = (20, 44) if across else (44, 20)
    (c1, c2) = ((x, y - 11), (x, y + 11)) if across else ((x - 11, y), (x + 11, y))
    return (f'<g class="fn-split ma on" role="img" aria-label="{name}"><rect x="{_f(x - w / 2)}" y="{_f(y - h / 2)}" width="{w}" height="{h}" rx="3"/>'
            f'{buildings.cog(*c1, 5.5, 6, "cog fn-cog")}{buildings.cog(*c2, 5.5, 6, "cog ccw fn-cog")}</g>')


def _plan_districts(dist: dict) -> str:
    out = ""
    for name in ("INTAKE", "PLANNING", "PRODUCTION", "QUALITY", "SHIPPING"):
        if name in dist:
            x, y, w, h = dist[name]
            out += (f'<rect class="f3-dist d-{name.lower()}" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="4"/>'
                    f'<text class="f3-dlab d-{name.lower()}" x="{_f(x + 8)}" y="{_f(y + 14)}">{name}</text>')
    return out


def scenery(walls, trees) -> str:
    """Walls and trees a person drew on the floor (pixels): they only decorate it."""
    out = "".join(f'<path class="fp-wall" d="M {" L ".join(f"{_f(x)} {_f(y)}" for x, y in w)}"/>' for w in walls)
    out += "".join(f'<g class="fp-tree"><circle class="fp-crown" cx="{_f(x)}" cy="{_f(y)}" r="9"/><circle class="fp-leaf" cx="{_f(x - 3)}" '
                   f'cy="{_f(y - 3)}" r="4"/></g>' for x, y in trees)
    return f'<g aria-hidden="true">{out}</g>' if out else ""


# ---------------------------------------------------------------- the railway of a saved layout: the yard, workers, junctions, track
RING = 20                                   # a rail building's turnaround loop runs one cell outside it, through the points beside it


CURVE = 40                                  # track bends in wide curves, as the yard's always did
PLAT0, PLAT_GAP, PARK, FEED = 40, 60, 50, 280  # the yard: first platform, platform spacing, where a train's head stops, the feeder (px)


def platform_y(y, k: int) -> float:
    return y + PLAT0 + PLAT_GAP * k


def yard_picture(x, y, n: int) -> str:
    """The yard at (x, y), with a platform for each of n trains: Build's work comes down the feeder on the right and along a lane
    to each platform, where an inserter puts it in the waiting train's wagon. The platform tracks run west onto the yard's loop."""
    n = max(1, n)
    ax = x + FEED
    lanes = [platform_y(y, k) - 34 for k in range(n)]
    out = [f'<text class="fm-lab" x="{_f(x)}" y="{_f(y - 26)}">VERIFY YARD</text>', yard._stop(x + 100, y + 6, "Verify stop")]
    out.append(rails([f"M {_f(x - RING)} {_f(platform_y(y, k))} H {_f(x + 230)}" for k in range(n)]))
    out += [f'<rect class="ry-buffer" x="{_f(x + 230)}" y="{_f(platform_y(y, k) - 9)}" width="6" height="18" rx="1"/>' for k in range(n)]
    out.append(belt(f"M {_f(ax)} {_f(y)} L {_f(ax)} {_f(lanes[-1] - 12)}", "fn-belt thin"))
    out += [belt(f"M {_f(ax)} {_f(L - 12)} A 12 12 0 0 1 {_f(ax - 12)} {_f(L)} L {_f(x + PARK + yard.GAP)} {_f(L)}", "fn-belt thin") for L in lanes]
    return f'<g class="ry-yard" role="img" aria-label="The Verify yard: Build\'s work is loaded into the workers\' trains on its platforms">{"".join(out)}</g>'


def train_station(x, y) -> str:
    """The Train station, 160 by 80 at (x, y): trains offload the checked builds here and go back to the yard empty."""
    return (f'<g class="ry-depot" role="img" aria-label="The Train station: trains offload the checked builds here">'
            f'<rect class="fm-box" x="{_f(x)}" y="{_f(y)}" width="160" height="80" rx="3"/>'
            f'<path class="ry-roof" d="M {_f(x + 10)} {_f(y + 30)} L {_f(x + 40)} {_f(y + 10)} H {_f(x + 120)} L {_f(x + 150)} {_f(y + 30)} Z"/>'
            f'<rect class="ry-plat" x="{_f(x + 16)}" y="{_f(y + 32)}" width="128" height="10" rx="2"/>'
            f'<text class="fn-n" x="{_f(x + 10)}" y="{_f(y + 60)}">Train station</text><text class="fn-s" x="{_f(x + 10)}" y="{_f(y + 73)}">Trains offload here</text></g>')


WORKER_ICON = "M3 4h18v12H3zM8 20h8M12 16v4"


def worker_stop(x, y, w: dict) -> str:
    """A worker, 180 by 80 at (x, y): a train stop outside the factory, reached over the SSH tunnel. Its stop plate and the loader
    that hands the train's crates in stand on its loop above it, where the train stops."""
    on = w.get("online")
    job = w.get("job")
    state = ("Online · " + (f'{job["recipe"]} check' if job else "idle")) if on else "Offline"
    ref = f'job #{int(job["issue"])} · {job["recipe"]}' if job else "stop on the yard's railway"
    cx = x + 90
    return (f'<g class="ry-stop">{yard._stop(x - 24, y - 32, w["name"])}'
            + belt(f"M {_f(cx)} {_f(y - RING)} L {_f(cx)} {_f(y)}", "fn-belt thin")
            + f'<rect class="ry-hood" x="{_f(cx - 7)}" y="{_f(y - 6)}" width="14" height="8" rx="2"/></g>'
            f'<a class="fm-w{"" if on else " off"}" href="/workers" aria-label="Worker {esc(w["name"])}: {esc(state)}">'
            f'<rect class="fm-box" x="{_f(x)}" y="{_f(y)}" width="180" height="80" rx="3"/>'
            f'<path class="ry-wicon" transform="translate({_f(x + 10)} {_f(y + 9)}) scale(.75)" d="{WORKER_ICON}"/>'
            f'<circle class="fm-on" cx="{_f(x + 164)}" cy="{_f(y + 18)}" r="4"/>'
            f'<text class="fm-t" x="{_f(x + 32)}" y="{_f(y + 24)}">{esc(w["name"][:16])}</text>'
            f'<text class="fm-s" x="{_f(x + 12)}" y="{_f(y + 46)}">{esc(state[:26])}</text>'
            f'<text class="fm-r" x="{_f(x + 12)}" y="{_f(y + 64)}">{esc(ref[:26])}</text></a>')


def junction(x, y, name: str) -> str:
    """A junction, 40 by 40 at (x, y): tracks join or split here."""
    return (f'<g class="ry-junction" role="img" aria-label="Junction {esc(name.upper())}"><circle cx="{_f(x + 20)}" cy="{_f(y + 20)}" r="19"/>'
            f'<path d="M {_f(x + 20)} {_f(y + 8)} v 9 M {_f(x + 20)} {_f(y + 17)} l -7 14 M {_f(x + 20)} {_f(y + 17)} l 7 14"/></g>')


def outside(x, y, w, h) -> str:
    return (f'<rect class="fm-zone" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"/>'
            f'<text class="fm-lab" x="{_f(x + 16)}" y="{_f(y + 22)}">OUTSIDE THE FACTORY · WORKERS BY TRAIN OVER THE SSH TUNNEL</text>')


def ring_box(b):
    x, y, w, h = b
    return (x - RING, y - RING, x + w + RING, y + h + RING)


def ring_d(b) -> str:
    L, T, R, B = ring_box(b)
    r = min(CURVE, (R - L) / 2, (B - T) / 2)
    return (f"M {_f(L + r)} {_f(T)} H {_f(R - r)} A {r} {r} 0 0 1 {_f(R)} {_f(T + r)} V {_f(B - r)} A {r} {r} 0 0 1 {_f(R - r)} {_f(B)} "
            f"H {_f(L + r)} A {r} {r} 0 0 1 {_f(L)} {_f(B - r)} V {_f(T + r)} A {r} {r} 0 0 1 {_f(L + r)} {_f(T)} Z")


def ring_route(b, p, q) -> list:
    """Round a rail building's loop clockwise from point p to point q (both on the loop): the corners passed on the way."""
    L, T, R, B = ring_box(b)
    W, H = R - L, B - T
    per = 2 * (W + H)

    def t(pt):
        x, y = pt
        if abs(y - T) < 1:
            return x - L
        if abs(x - R) < 1:
            return W + (y - T)
        if abs(y - B) < 1:
            return W + H + (R - x)
        return 2 * W + H + (B - y)
    ta, tb = t(p), t(q)
    span = (tb - ta) % per or per
    corners = [(W, (R, T)), (W + H, (R, B)), (2 * W + H, (L, B)), (per, (L, T))]
    hits = sorted(((c - ta) % per, pt) for c, pt in corners if 0 < (c - ta) % per < span)
    return [pt for _, pt in hits]


def rails(ds: list[str]) -> str:
    return "".join(f'<g class="{c}">' + "".join(f'<path d="{d}"/>' for d in ds) + "</g>"
                   for c in ("fm-ballast", "fm-ties", "fm-rail", "fm-rail-in", "fm-ties-in"))


def circuit_marks(C: dict, w: str) -> tuple[list, dict]:
    """A worker's loop as points and where things are along it, in px from the start: each stretch of track (start, end), the stop
    at the worker and the stop at the Train station (half way round their loops), and the whole length. It starts and ends with
    the train's head at its platform in the yard: west off the platform onto the yard's loop, out to the worker, on to the Train
    station, home, round the yard's loop and back onto the platform."""
    tr, bx = C["tracks"], C["box"]
    pts, at, run = [], {}, [0.0]

    def add(q):
        q = tuple(q)
        if pts:
            if q == pts[-1]:
                return
            run[0] += math.dist(pts[-1], q)
        pts.append(q)
    yx, yy = bx["yard"][:2]
    py = platform_y(yy, C["workers"].index(w))
    park, edge = (yx + PARK, py), (yx - RING, py)
    legs = []
    for edges in C["circuits"][w]:
        leg = []
        for i, tid in enumerate(edges):
            if i:
                jx, jy, jw, jh = bx[tr[tid]["src"]]
                leg.append(("j", (jx + jw / 2, jy + jh / 2)))
            leg.append((tid, tr[tid]["pts"]))
        legs.append(leg)
    add(park)
    add(edge)
    for c in ring_route(bx["yard"], edge, tuple(legs[0][0][1][0])):
        add(c)
    stops = {}
    rings = [(w, legs[1][0][1][0]), ("depot", legs[2][0][1][0]), ("yard", edge)]
    for k, leg in enumerate(legs):
        for tid, q in leg:
            if tid == "j":
                add(q)
                continue
            add(q[0])
            start = run[0]
            for p in q[1:]:
                add(p)
            at[tid] = (start, run[0])
        name, nxt = rings[k]
        before = run[0]
        for c in ring_route(bx[name], pts[-1], tuple(nxt)):
            add(c)
        add(nxt)
        stops[name] = (before + run[0]) / 2
    add(park)
    return pts, {"tracks": at, "stop": stops[w], "off": stops["depot"], "len": run[0]}


def timetable(C: dict, running: list[str]) -> tuple[float, dict]:
    """One cycle of the trains that drive. The track two or more loops share is one block, as in the default yard: a train leaves
    the yard when the block is clear, unloads at its worker, waits at its signal until the block is clear again, offloads at the
    Train station and comes home. Returns (cycle seconds, {worker: [(time, px along its loop), ...]})."""
    marks = {w: circuit_marks(C, w)[1] for w in C["circuits"]}
    uses = {}
    for w in C["circuits"]:
        for tid in marks[w]["tracks"]:
            uses[tid] = uses.get(tid, 0) + 1
    shared = {t for t, n in uses.items() if n > 1}
    plan, free, t = {}, 0.0, 0.0
    tail = lambda w: (yard.CARS[sorted(C["circuits"]).index(w) % len(yard.CARS)] * yard.GAP) / yard.SPEED + 0.6
    for w in running:
        m = marks[w]
        out = [m["tracks"][tid] for tid in C["circuits"][w][0] if tid in shared]
        out_end = max((e for _, e in out), default=0.0)
        home, prev = m["off"], None                                  # where it waits: at the signal before the junction into the block
        for tid in C["circuits"][w][1] + C["circuits"][w][2]:
            if tid in shared and m["tracks"][tid][0] > m["stop"]:
                if prev and C["tracks"][prev]["dst"].startswith("junction:"):
                    s0, e0 = m["tracks"][prev]
                    home = e0 - min(26.0, (e0 - s0) / 2)
                else:
                    home = m["tracks"][tid][0]
                break
            prev = tid
        r = plan[w] = {"m": m, "home_at": home}
        r["depart"] = max(t, free)
        r["arrive"] = r["depart"] + m["stop"] / yard.SPEED
        r["leave"] = r["arrive"] + yard.DWELL
        r["signal"] = r["leave"] + (home - m["stop"]) / yard.SPEED
        free = r["depart"] + out_end / yard.SPEED + tail(w)
        t = r["depart"]
    for w in sorted(running, key=lambda w: plan[w]["signal"]):
        r, m = plan[w], plan[w]["m"]
        r["go"] = max(r["signal"], free)
        r["at_off"] = r["go"] + (m["off"] - r["home_at"]) / yard.SPEED
        r["off_leave"] = r["at_off"] + yard.DWELL
        r["home"] = r["off_leave"] + (m["len"] - m["off"]) / yard.SPEED
        free = r["home"] + tail(w)
    T = round(max((r["home"] for r in plan.values()), default=0.0) + yard.HOME_DWELL, 1)
    out = {}
    for w, r in plan.items():
        m = r["m"]
        out[w] = [(0.0, 0.0), (r["depart"], 0.0), (r["arrive"], m["stop"]), (r["leave"], m["stop"]), (r["signal"], r["home_at"]),
                  (r["go"], r["home_at"]), (r["at_off"], m["off"]), (r["off_leave"], m["off"]), (r["home"], m["len"]), (T, m["len"])]
    return T, out


def at_time(points: list, d: float):
    """When a train whose motion is points [(time, px)] passes px d (None if it never does)."""
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if d1 > d0 and d0 <= d <= d1:
            return t0 + (t1 - t0) * (d - d0) / (d1 - d0)
    return None


def lamps(times: list, T: float, begin: str) -> str:
    """A two-lamp signal that turns green for a moment as each of its trains is let through, red the rest of the cycle."""
    if not times or not T:
        return '<circle class="r" cx="0" cy="-4" r="2.4"/><circle class="g off" cx="0" cy="4" r="2.4"/>'
    spans = []
    for t in sorted(times):                                          # green from just before to just after each train; overlaps join
        a, b = max(0.0, t - 0.5) / T, min(T, t + 0.9) / T
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    keys, gv = [0.0], [0.15]
    for a, b in spans:
        if a <= keys[-1]:
            gv[-1] = 1
        else:
            keys.append(a)
            gv.append(1)
        if b < 1:
            keys.append(b)
            gv.append(0.15)
    keys = [round(k, 4) for k in keys]
    ks = ";".join(f"{k:g}" for k in keys)
    g = ";".join(f"{v:g}" for v in gv)
    r = ";".join("1" if v != 1 else "0.15" for v in gv)
    return (f'<circle class="r" cx="0" cy="-4" r="2.4"><animate attributeName="opacity" values="{r}" keyTimes="{ks}" calcMode="discrete" dur="{_f(T)}s" begin="{begin}" repeatCount="indefinite"/></circle>'
            f'<circle class="g" cx="0" cy="4" r="2.4"><animate attributeName="opacity" values="{g}" keyTimes="{ks}" calcMode="discrete" dur="{_f(T)}s" begin="{begin}" repeatCount="indefinite"/></circle>')


def railway(C: dict, workers: list[dict], now: float) -> tuple[str, str]:
    """(track, loops, the yard and the signals; the trains and the loading). A worker's train waits at its platform in the yard and
    runs its loop while the worker has a job; the trains keep to the timetable, so only one is ever on the shared track. A signal at
    each platform turns green as its train leaves, and one on every track into a junction as it lets a train on."""
    at, bx = C["at"], C["box"]
    ds = [trace(t["pts"], r=CURVE)[0].d for t in C["tracks"].values()]
    for t in C["tracks"].values():                                  # into the middle of a junction
        for nid, (px_, py_) in ((t["src"], t["pts"][0]), (t["dst"], t["pts"][-1])):
            if nid.startswith("junction:"):
                jx, jy, jw, jh = bx[nid]
                ds.append(f"M {_f(px_)} {_f(py_)} L {_f(jx + jw / 2)} {_f(jy + jh / 2)}")
    ds += [ring_d(bx[n]) for n in bx if n in ("yard", "depot") or n.startswith("worker:")]
    yx, yy, _, _ = bx["yard"]
    ws = C.get("workers") or []
    still = rails(ds) + yard_picture(yx, yy, len(ws))
    moving = ""
    live = {f'worker:{w["name"]}': w for w in workers}
    running = [w for w in sorted(C["circuits"]) if (live.get(w) or {}).get("online") and (live.get(w) or {}).get("job")]
    T, motion = timetable(C, running)
    begin = f"{-(now % T):.2f}s" if T else "0s"
    Pf = 8.4 / max(1, len(running))
    for k, w in enumerate(ws):
        cars = yard.CARS[k % len(yard.CARS)]
        py = platform_y(yy, k)
        lane, arm_x, arm_y = py - 34, yx + PARK + yard.GAP, py - 17
        depart = motion[w][1][0] if w in motion else None
        still += yard._signal(yx + 20, py - 13, f"Signal at {w.split(':', 1)[1]}'s platform", yard._lamp(depart, T, begin))
        if w in motion:
            pts, m = circuit_marks(C, w)
            p, _ = trace(pts, r=CURVE)
            scale = p.len / m["len"] if m["len"] else 1.0              # the drawn line rounds its corners, a little shorter
            moving += yard._train(p, [(t, d * scale) for t, d in motion[w]], T, begin, cars)
            # Build's work comes down the feeder and along the lane, and is lifted into the train's first wagon
            trip = Path(yx + FEED, yy).L(yx + FEED, lane - 12).A(12, 1, yx + FEED - 12, lane).L(arm_x + 2, lane)
            b = -((now + running.index(w) * Pf / max(1, len(running))) % Pf)
            crate, Tg = crate_trip(trip, [], "", Pf, b, small=True)
            moving += crate + yard.taking_arm(arm_x, arm_y, False, Tg, Pf, b)
        else:                                                        # waiting at its platform, facing out of the yard
            moving += "".join(f'<g class="fn-car" transform="translate({_f(yx + PARK + c * yard.GAP)} {_f(py)}) rotate(180)">'
                              f'{yard.LOCO if c == 0 else yard.WAGON}</g>' for c in range(cars)) if w in C["circuits"] else ""
            moving += yard._arm(arm_x, arm_y, False)
    for j in (n for n in bx if n.startswith("junction:")):
        ins = [(tid, t) for tid, t in C["tracks"].items() if t["dst"] == j]
        for tid, t in ins:
            (ax, ay), (zx, zy) = t["pts"][-2], t["pts"][-1]
            d = math.dist((ax, ay), (zx, zy)) or 1
            ux, uy = (zx - ax) / d, (zy - ay) / d
            back = min(26.0, d / 2)
            sx, sy = zx - ux * back - uy * 14, zy - uy * back + ux * 14
            passes = []                                              # when each train that runs this track goes by the signal
            for w, pts_ in motion.items():
                m = circuit_marks(C, w)[1]
                if tid in m["tracks"]:
                    when = at_time(pts_, m["tracks"][tid][1] - back)
                    if when is not None:
                        passes.append(when)
            still += yard._signal(sx, sy, f"Signal into junction {j.split(':')[1].upper()}", lamps(passes, T, begin))
    return still, moving


def node_art(nid: str, label: str, trains: int = 0) -> str:
    """The picture of a building or an area as the floor draws it, idle, with its top left at the origin and at its own size (the
    layout editor stretches it to the box a person gives it). The yard is drawn with its trains, or not at all without any."""
    kind, _, name = nid.partition(":")
    if nid == "yard":
        return yard_picture(0, 0, trains)
    if nid == "depot":
        return train_station(0, 0)
    if nid == "outside":
        from .floorplan import G, SIZE
        return outside(0, 0, SIZE["outside"][0] * G, SIZE["outside"][1] * G)
    if kind == "worker":
        return worker_stop(0, 0, {"name": name, "online": True})
    if kind == "junction":
        return junction(0, 0, name)
    idle = {"state": "idle", "refs": [], "count": 0}
    if kind == "station":
        return _station(name, label, idle, "idle", "#", (0, 0, True), name)
    if kind == "power":
        return power_station(0, 0, {"name": name, "on": False}, [])
    if kind == "notify":
        return notifier(0, 0, {"name": name, "on": False})
    return {"harbor": lambda: harbor(0, 0), "mainland": lambda: mainland(1320), "sea": lambda: sea([], 1320),
            "sources": lambda: f'<g transform="translate({-SRC[0]} {-SRC[1]})">{github()}</g>',
            "receiving": lambda: f'<g transform="translate(-12 -200)">{receiving(0)}</g>',
            "queue": lambda: f'<g transform="translate(0 -250)">{_queue_chest(30, 0)}</g>',
            "airfield": lambda: f'<g transform="translate(-20 {-AF_Y})">{airfield(conveyor=False)}</g>'}.get(nid, lambda: "")()


def planned_map(order, fl, workers, workers_on, now, word, href, extras, C) -> str:
    """The floor drawn from a saved layout (floorplan.compile_plan): every building where it was put, a belt per hop of the route,
    and the crates, inserters, drones, trains and the plane following them."""
    ids = [sid for sid, _, _ in order]
    at, bx, hops = C["at"], C["box"], C["hops"]
    st = lambda s: f"station:{s}"
    harnesses = extras.get("power") or []
    queued = extras.get("queued") or []
    shown = (workers or [])[:yard.MAX_WORKERS]
    sc = C.get("scale") or {}
    S = lambda nid: sc.get(nid, (1, 0, 0))                           # (scale, x offset, y offset): floorplan.fit_art
    top = lambda nid: (at[nid][0] + S(nid)[1], at[nid][1] + S(nid)[2])  # where the picture's top left lands

    def M(nid, x, y, x0=0, y0=0):
        """A point of a part made with its top left at (x0, y0), where the layout drew it."""
        (tx, ty), s = top(nid), S(nid)[0]
        return (tx + (x - x0) * s, ty + (y - y0) * s)

    def tr(nid, x0, y0):
        """Draws a part made with its top left at (x0, y0) where the layout put it, scaled evenly and centred when it was resized."""
        if nid not in sc:
            return f'translate({_f(at[nid][0] - x0)} {_f(at[nid][1] - y0)})'
        tx, ty = top(nid)
        return f'translate({_f(tx)} {_f(ty)}) scale({_f(S(nid)[0])}) translate({_f(-x0)} {_f(-y0)})'

    def node(nid, draw):
        """A station or a power station: drawn in place, or at the origin and scaled when it was resized."""
        return draw(*at[nid]) if nid not in sc else f'<g transform="{tr(nid, 0, 0)}">{draw(0, 0)}</g>'

    def ground(nid, cls, rx=0):
        """A resized area's ground under its picture, filling its whole box."""
        if nid not in sc:
            return ""
        x, y, w, h = bx[nid]
        return f'<rect class="{cls}" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{rx}"/>'
    tn = C.get("terrain") or {}
    loose = [p for hb in hops.values() for p in hb["pts"]] + [p for w in C.get("walls") or [] for p in w] + list(C.get("trees") or [])
    loose += [(it[1] + it[3] / 2, it[2] + it[3] / 2) for it in tn.get("items") or []] + [tuple(p) for r in tn.get("rivers") or [] for p in r]
    loose += [((c + n) * terrain.TILE, (r + 1) * terrain.TILE) for rs in (tn.get("tiles") or {}).values() for c, r, n in rs]
    loose += [(p[0] * G_, p[1] * G_) for key in ("fences", "roads") for ln in tn.get(key) or [] for p in ln]
    loose += [((x + w) * G_, (y + h) * G_) for x, y, w, h in tn.get("hazards") or []]
    right = max([x + w for x, _, w, _ in bx.values()] + [p[0] for p in loose])
    bottom = max([y + h for _, y, _, h in bx.values()] + [p[1] for p in loose])
    width, height = right + 40, bottom + 40
    rails_ = [t["pts"] for t in (C.get("tracks") or {}).values()]
    land = lambda layer: terrain.svg(tn, G_, width, height, C.get("wall_cells") or [], C.get("tree_cells") or [], layer, rails_)
    # the sea's dock and crane at the harbor's height (the harbor stands where a person put it; its belt takes the tickets on)
    trips = extras.get("schedules") or []
    dock = bx.get("harbor") or bx["receiving"]
    dock_y = max(100.0, min(1320 - 420.0, (dock[1] + dock[3] / 2 - top("sea")[1]) / S("sea")[0]))
    # the water fills the sea's whole box; the picture (ships, labels, the crane) is drawn over it at its own scale
    sbx, sby, sbw, sbh = bx["sea"]
    (stx, sty), ss = top("sea"), S("sea")[0]
    water = f'<g transform="translate({_f(sbx)} {_f(sby)})">{sea_water(sbw, sbh)}</g>'
    coast = lambda y: (sbx + coast_x(sbw, sbh, sty + y * ss - sby) - stx) / ss
    harbor_svg = node("harbor", lambda x, y: harbor(x, y, any(t["due"] for t in trips))) if "harbor" in at else ""
    told = {n["name"]: n for n in extras.get("notify") or []}
    radios = "".join(node(nid, lambda x, y, n=nid.split(":", 1)[1]: notifier(x, y, told.get(n, {"name": n})))
                     for nid in at if nid.startswith("notify:"))
    # the outside area, where the workers stand: ground, so under the terrain painted on it (roads and their cars, ponds, trees)
    area = ""
    if "yard" in at and "outside" in at:
        area = (f'<g transform="{tr("outside", 0, 0)}">{outside(0, 0, *bx["outside"][2:])}</g>' if "outside" not in sc else outside(*bx["outside"]))
    # the ground: the mainland and the sea, the districts, the buildings that are not stations
    back = (ground("mainland", "ap-land") + f'<g aria-hidden="true" transform="{tr("mainland", 0, 0)}">{mainland(1320)}</g>'
            + water + f'<g transform="{tr("sea", 0, 0)}">{sea(trips, 1320, dock_y, water=False, coast=coast)}</g>'
            + area + f'<g aria-hidden="true">{land("under")}</g>' + _plan_districts(C.get("districts") or {})
            + ground("airfield", "ap-field", 6) + f'<g transform="{tr("airfield", 20, AF_Y)}">{airfield(conveyor=False)}</g>'
            + f'<text class="fm-lab" x="{_f(at["sources"][0])}" y="{_f(at["sources"][1] - 8)}">SOURCES</text>'
            + f'<g transform="{tr("sources", *SRC)}">{github()}</g><g transform="{tr("receiving", 12, 200)}">{receiving(len(queued))}</g>'
            + f'<g transform="{tr("queue", 0, 250)}">{_queue_chest(30, len(queued))}</g>' + harbor_svg + radios + f'<g aria-hidden="true">{land("over")}</g>')
    # the belts, cut where they go under, and the pieces on them
    belts, hoods = [], set()
    for hid, hb in hops.items():
        pts = hb["pts"] + ([at["yard"]] if hb["dst"] == "yard" else [])
        cls = "fn-belt thin" if hb["src"] in ("airfield", "harbor") or hb["dst"] == "yard" else "fn-belt"
        belts += [belt(trace(q)[0].d, cls) for q in _visible(pts, hb["under"])]
        hoods |= set(hb["under"])
    belts += [underground(u, v) for u, v in sorted(hoods)] + [_piece(p) for p in C["pieces"]]
    # the railway: the yard under the feeder from Build, the Train station, the workers outside, junctions, track and trains
    yard_still = yard_moving = ""
    if "yard" in at:
        live = {f'worker:{w["name"]}': w for w in shown}
        track, yard_moving = railway(C, shown, now)
        parts_ = []
        parts_ += [node("depot", train_station)] if "depot" in at else []
        parts_ += [node(n, lambda x, y, n=n: worker_stop(x, y, live.get(n) or {"name": n.split(":", 1)[1], "online": False})) for n in at if n.startswith("worker:")]
        parts_ += [node(n, lambda x, y, n=n: junction(x, y, n.split(":", 1)[1])) for n in at if n.startswith("junction:")]
        yard_still = track + "".join(parts_)
        if workers_on and not any(n.startswith("worker:") for n in at):
            x, y, w, h = bx["yard"]
            yard_still += (f'<a class="fm-add" href="/workers"><rect x="{_f(x + 10)}" y="{_f(y + h + 30)}" width="180" height="44" rx="2"/>'
                           f'<text x="{_f(x + 100)}" y="{_f(y + h + 57)}">+ Connect a worker</text></a>')
    # power: a station per harness where it was put, with a line to each station that uses it
    pw = ""
    for k, h in enumerate(harnesses):
        nid = f'power:{h["name"]}'
        if nid not in at:
            continue
        x0, y0, pwid, ph = bx[nid]
        uses = [s for s in h.get("uses", []) if st(s) in bx]
        pw += node(nid, lambda x, y: power_station(x, y, h, uses))
        cy = y0 + ph / 2
        for s in uses:
            sx, sy, sw, sh = bx[st(s)]
            scx = sx + sw / 2 + 8 * k
            edge = sy if sy > cy else sy + sh
            pw += _pole_line([(x0 if scx < x0 else x0 + pwid, cy), (scx, cy)]) + f'<path class="pw-wire" d="M {_f(scx)} {_f(cy - 6)} V {_f(edge)}"/>'
    # crates: each ticket rides the hop into the station taking it; every other inserter waits
    arms, parts, k = {}, [], 0
    ends = lambda hb: ((hb["src"], hb["pts"][0]), (hb["dst"], hb["pts"][-1]))
    armed = lambda nid: nid not in ("airfield", "yard")

    def ride(hid, ref, k):
        hb = hops[hid]
        p, hidden = trace(hb["pts"], hb["under"])
        P = p.len / yard.BELT_V + yard.GRAB + 2 * yard.SWING + 0.6
        b = -((now + k * 1.9) % P)
        crate, Tg = crate_trip(p, hidden, ref, P, b)
        parts.append(crate)
        (src, a), (dst, z) = ends(hb)
        if armed(dst):
            x, y, ang = _pivot(bx[dst], z)
            arms[(x, y)] = taking(x, y, ang, Tg, P, b, REACH)
        if armed(src):
            x, y, ang = _pivot(bx[src], a)
            arms[(x, y)] = giving(x, y, (ang + 180) % 360, P, b, REACH)

    def into(i, sid):
        return f"receiving>{st(sid)}" if i == 0 else f"{st(ids[i - 1])}>{st(sid)}"

    for i, sid in enumerate(ids):
        o = fl.get(sid) or {}
        if o.get("state") != "run" or not o.get("refs"):
            continue
        ref, hid = o["refs"][0], into(i, sid)
        if sid == "build" and ref in (extras.get("fix") or []) and f"{st('ci')}>{st('build')}" in hops:
            hid = f"{st('ci')}>{st('build')}"
        elif sid == "build" and ref in (extras.get("bypass") or []) and f"{st('route')}>{st('build')}" in hops:
            hid = f"{st('route')}>{st('build')}"
        if hid in hops:
            ride(hid, ref, k)
            k += 1
    if queued and f"{st('classify')}>queue" in hops:
        ride(f"{st('classify')}>queue", queued[0], k)
    feed = f"{st('build')}>yard"
    if feed in hops and any(w["online"] and w.get("job") for w in shown):
        x, y, ang = _pivot(bx[st("build")], hops[feed]["pts"][0])
        Pf = 8.4 / max(1, len(shown))
        arms[(x, y)] = giving(x, y, (ang + 180) % 360, Pf, -(now % Pf), REACH)
    for hb in hops.values():
        for n, (nid, pt) in enumerate(ends(hb)):
            if armed(nid):
                x, y, ang = _pivot(bx[nid], pt)
                arms.setdefault((x, y), arm(x, y, ang if n else (ang + 180) % 360, reach=REACH))
    # waiting and stuck crates at their doors, backed up along the belt in
    for i, sid in enumerate(ids):
        o, hid = fl.get(sid) or {}, into(i, sid)
        if hid not in hops or o.get("state") not in ("wait", "fail") or not o.get("refs"):
            continue
        pts = hops[hid]["pts"]
        (zx, zy), (dx, dy) = pts[-1], _unit(pts[-2], pts[-1])
        if o["state"] == "wait":
            parts += [_still_crate(zx - dx * (12 + j * 26), zy - dy * (12 + j * 26), r) for j, r in enumerate(o["refs"][:2])]
        else:
            parts.append(_still_crate(zx - dx * 14, zy - dy * 14, o["refs"][0], stuck=True))
    # the plane, its crate riding the conveyor into Receiving
    conv = hops.get("airfield>receiving", {}).get("pts")
    af = top("airfield")
    flights = "".join(flight(f["age"], f["label"], top("mainland"), (af[0] - 20, af[1] - AF_Y), conv, (S("mainland")[0],) * 2, (S("airfield")[0],) * 2)
                      for f in (extras.get("flights") or []) if 0 <= f["age"] < FLIGHT_SECONDS)
    poll = extras.get("poll") or {}
    pad = lambda nid, p, o: M(nid, *p, *o)
    plant = (back + "".join(belts) + f'<g aria-hidden="true">{land("bridge")}{yard_still}</g>' + pw
             + f'<g aria-hidden="true">{"".join(parts)}{yard_moving}</g>'
             + "".join(node(st(sid), lambda x, y: _station(sid, label, fl[sid], word(sid, fl[sid]), href(sid), (x, y, True), sid))
                       for sid, label, _ in order if st(sid) in at)
             + f'<g aria-hidden="true">{"".join(arms.values())}</g>'
             + f'<g aria-hidden="true">{drones(now, poll.get("every"), poll.get("last"), pad("sources", PAD, SRC), pad("receiving", RECV_PAD, (12, 200)))}</g>'
             + f'<g aria-hidden="true">{land("top")}</g>')
    return (f'<svg class="fm" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'{terrain.defs()}<rect class="fm-ground" width="{_f(width)}" height="{_f(height)}"/>{plant}{_night(tn, width, height)}'
            f'<g aria-hidden="true">{flights}</g></svg>')
