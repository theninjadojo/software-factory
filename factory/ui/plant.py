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

from . import buildings, yard
from .views import esc
from .yard import Path, _f, belt, underground, splitter, pipe, crate_trip, _still_crate, _station

MW, MH, PITCH = 140, 100, 160
TOP, BUS, PLB, STREET, QA, RET = 30, 170, 236, 296, 465, 635
BOT = 500                                 # the quality and shipping row
BUILD = (860, 330)
SEA_W, LAND_W = 300, 240
FX = LAND_W + SEA_W                       # where the plant starts on the whole floor
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
        out.append(f'<rect class="f3-dist" x="{_f(x)}" y="{y}" width="{_f(w)}" height="{h}" rx="4"/><text class="f3-dlab" x="{_f(x + 8)}" y="{y + 14}">{n}</text>')
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


def sea(trips: list[dict], height: float, dock_y: float = DOCK[1]) -> str:
    """In the sea's own coordinates (x 0..300; Receiving's sea wall is at x 300). dock_y: the height of the dock and the crane, which
    a saved layout sets to Receiving's."""
    dock = (DOCK[0], dock_y)
    out = [f'<rect class="sh-sea" x="0" y="0" width="{SEA_W}" height="{_f(height)}"/>', f'<text class="fm-lab" x="16" y="24">THE SEA · SCHEDULED JOBS</text>']
    out += [f'<path class="sh-wave w{i % 3}" d="M {20 + (i * 61) % 250} {60 + (i * 137) % int(height - 80)} q 6 -4 12 0 t 12 0"/>' for i in range(30)]
    lines = marks = ships = ""
    for k, t in enumerate(trips[:4]):
        xo, xr = 26 + 66 * k, 60 + 66 * k
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
    crane = f'<g class="sh-crane"><line class="sh-jib" x1="{SEA_W}" y1="{_f(dock[1])}" x2="{SEA_W - 22}" y2="{_f(dock[1])}"/><circle class="sh-base" cx="{SEA_W}" cy="{_f(dock[1])}" r="7"/></g>'
    if any(t["due"] for t in trips):
        Tc = 4.0
        crane = (f'<g class="sh-crane"><g><animateTransform attributeName="transform" type="rotate" values="0 {SEA_W} {_f(dock[1])};0 {SEA_W} {_f(dock[1])};180 {SEA_W} {_f(dock[1])};180 {SEA_W} {_f(dock[1])};0 {SEA_W} {_f(dock[1])}" '
                 f'keyTimes="0;.15;.5;.6;1" dur="{Tc}s" repeatCount="indefinite"/><line class="sh-jib" x1="{SEA_W}" y1="{_f(dock[1])}" x2="{SEA_W - 22}" y2="{_f(dock[1])}"/>'
                 f'<rect class="sh-cargo" x="{SEA_W - 27}" y="{_f(dock[1] - 5)}" width="10" height="10" opacity="0"><animate attributeName="opacity" values="0;1;0" keyTimes="0;.15;.5" '
                 f'calcMode="discrete" dur="{Tc}s" repeatCount="indefinite"/></rect></g><circle class="sh-base" cx="{SEA_W}" cy="{_f(dock[1])}" r="7"/></g>')
    if not trips:
        lines = (f'<text class="sh-when" x="20" y="60">No scheduled jobs yet.</text>'
                 f'<a class="sh-add" href="/schedules"><text x="20" y="76">Add one in Settings, Schedules</text></a>')
    return "".join(out) + lines + marks + ships + f'<rect class="sh-wall" x="{SEA_W - 4}" y="{_f(dock[1] - 54)}" width="8" height="108"/>' + crane


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
    plant = (sources(len(queued)) + districts(lay) + airfield() + pipe_svg + belts(lay, len(queued), steam, bool(shown))
             + f'<g aria-hidden="true">{yard_still}</g>' + plant_svg
             + f'<g aria-hidden="true">{crates(lay, ids, fl, ex, now)}{yard_moving}</g>'
             + "".join(_station(sid, label, fl[sid], word(sid, fl[sid]), href(sid), (*lay["pos"][sid], True), sid)
                       for sid, label, _ in order if sid in lay["pos"])
             + f'<g aria-hidden="true">{drones(now, poll.get("every"), poll.get("last"))}</g>')
    width = FX + lay["width"]
    flights = "".join(flight(f["age"], f["label"]) for f in (extras.get("flights") or []) if 0 <= f["age"] < FLIGHT_SECONDS)
    return (f'<svg class="fm" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'<rect class="fm-ground" width="{_f(width)}" height="{_f(height)}"/>'
            f'<g aria-hidden="true">{mainland(height)}</g><g transform="translate({LAND_W} 0)">{sea(extras.get("schedules") or [], height)}</g>'
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


def trace(pts, under=()) -> tuple[Path, list]:
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
        r = min(R, math.dist(a, b) / 2, math.dist(b, c) / 2)
        p.L(b[0] - d1[0] * r, b[1] - d1[1] * r)
        p.A(r, 1 if cross > 0 else 0, b[0] + d2[0] * r, b[1] + d2[1] * r)
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
            out += (f'<rect class="f3-dist" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="4"/>'
                    f'<text class="f3-dlab" x="{_f(x + 8)}" y="{_f(y + 14)}">{name}</text>')
    return out


def scenery(walls, trees) -> str:
    """Walls and trees a person drew on the floor (pixels): they only decorate it."""
    out = "".join(f'<path class="fp-wall" d="M {" L ".join(f"{_f(x)} {_f(y)}" for x, y in w)}"/>' for w in walls)
    out += "".join(f'<g class="fp-tree"><circle class="fp-crown" cx="{_f(x)}" cy="{_f(y)}" r="9"/><circle class="fp-leaf" cx="{_f(x - 3)}" '
                   f'cy="{_f(y - 3)}" r="4"/></g>' for x, y in trees)
    return f'<g aria-hidden="true">{out}</g>' if out else ""


def node_art(nid: str, label: str, trains: int = 0) -> str:
    """The picture of a building or an area as the floor draws it, idle, with its top left at the origin and at its own size (the
    layout editor stretches it to the box a person gives it). The yard is drawn with its trains, or not at all without any."""
    kind, _, name = nid.partition(":")
    if nid == "yard":
        if trains <= 0:
            return ""
        from .floorplan import G, yard_box
        g = yard.yard_geometry(trains, 0)
        still, _ = yard._yard(g, [{"name": f"worker {k + 1}", "online": False} for k in range(trains)], 0)
        return f'<g transform="translate({-yard_box(trains)[0] * G} {-(yard.BOT_Y + yard.MH + 30)})">{still}</g>'
    idle = {"state": "idle", "refs": [], "count": 0}
    if kind == "station":
        return _station(name, label, idle, "idle", "#", (0, 0, True), name)
    if kind == "power":
        return power_station(0, 0, {"name": name, "on": False}, [])
    return {"mainland": lambda: mainland(1320), "sea": lambda: sea([], 1320),
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
    S = lambda nid: sc.get(nid, (1, 1))

    def tr(nid, x0, y0):
        """Draws a part made with its top left at (x0, y0) where the layout put it, stretched when it was resized."""
        if nid not in sc:
            return f'translate({_f(at[nid][0] - x0)} {_f(at[nid][1] - y0)})'
        return f'translate({_f(at[nid][0])} {_f(at[nid][1])}) scale({_f(S(nid)[0])} {_f(S(nid)[1])}) translate({_f(-x0)} {_f(-y0)})'

    def node(nid, draw):
        """A station or a power station: drawn in place, or at the origin and stretched when it was resized."""
        return draw(*at[nid]) if nid not in sc else f'<g transform="{tr(nid, 0, 0)}">{draw(0, 0)}</g>'
    loose = [p for hb in hops.values() for p in hb["pts"]] + [p for w in C.get("walls") or [] for p in w] + list(C.get("trees") or [])
    right = max([x + w for x, _, w, _ in bx.values()] + [p[0] for p in loose])
    bottom = max([y + h for _, y, _, h in bx.values()] + [p[1] for p in loose])
    width, height = right + 40, bottom + 40
    # the sea's dock and crane at Receiving's height, and a belt from the crane to Receiving when they stand apart
    sx, sy = at["sea"]
    rx0, ry0 = at["receiving"]
    rh = bx["receiving"][3]
    dock_y = max(100.0, min(1320 - 420.0, (ry0 + rh / 2 - sy) / S("sea")[1]))
    qy, wall = sy + dock_y * S("sea")[1], sx + bx["sea"][2]
    quay = ""
    if rx0 - wall > 24:
        cy = ry0 + rh / 2
        quay = belt(f"M {_f(wall)} {_f(qy)} " + (f"H {_f(rx0)}" if abs(cy - qy) < 1 else f"H {_f((wall + rx0) / 2)} V {_f(cy)} H {_f(rx0)}"),
                    "fn-belt thin")
    # the ground: the mainland and the sea, the districts, the buildings that are not stations
    back = (f'<g aria-hidden="true" transform="{tr("mainland", 0, 0)}">{mainland(1320)}</g>'
            f'<g transform="{tr("sea", 0, 0)}">{sea(extras.get("schedules") or [], 1320, dock_y)}</g>{quay}' + _plan_districts(C.get("districts") or {})
            + f'<g transform="{tr("airfield", 20, AF_Y)}">{airfield(conveyor=False)}</g>'
            + f'<text class="fm-lab" x="{_f(at["sources"][0])}" y="{_f(at["sources"][1] - 8)}">SOURCES</text>'
            + f'<g transform="{tr("sources", *SRC)}">{github()}</g><g transform="{tr("receiving", 12, 200)}">{receiving(len(queued))}</g>'
            + f'<g transform="{tr("queue", 0, 250)}">{_queue_chest(30, len(queued))}</g>' + scenery(C.get("walls") or [], C.get("trees") or []))
    # the belts, cut where they go under, and the pieces on them
    belts, hoods = [], set()
    for hid, hb in hops.items():
        pts = hb["pts"] + ([at["yard"]] if hb["dst"] == "yard" else [])
        cls = "fn-belt thin" if "airfield" in (hb["src"], hb["dst"]) or hb["dst"] == "yard" else "fn-belt"
        belts += [belt(trace(q)[0].d, cls) for q in _visible(pts, hb["under"])]
        hoods |= set(hb["under"])
    belts += [underground(u, v) for u, v in sorted(hoods)] + [_piece(p) for p in C["pieces"]]
    # the yard, under the feeder from Build
    yard_still = yard_moving = ""
    if "yard" in at:
        ax, ay = at["yard"]
        x, y, w, h = bx["yard"]
        if shown:
            g = yard.yard_geometry(len(shown), 0)
            still, moving = yard._yard(g, shown, now)
            move = f'translate({_f(ax)} {_f(ay - yard.BOT_Y - yard.MH - 30)})'
            yard_still = (f'<rect class="fm-zone" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"/>'
                          f'<text class="fm-lab" x="{_f(x + 16)}" y="{_f(y + 22)}">WORKERS BY TRAIN OVER THE SSH TUNNEL</text><g transform="{move}">{still}</g>')
            yard_moving = f'<g transform="{move}">{moving}</g>'
        elif workers_on:
            yard_still = (f'<a class="fm-add" href="/workers"><rect x="{_f(ax - 90)}" y="{_f(ay + 8)}" width="180" height="44" rx="2"/>'
                          f'<text x="{_f(ax)}" y="{_f(ay + 35)}">+ Connect a worker</text></a>')
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
    flights = "".join(flight(f["age"], f["label"], at["mainland"], (at["airfield"][0] - 20, at["airfield"][1] - AF_Y), conv, S("mainland"), S("airfield"))
                      for f in (extras.get("flights") or []) if 0 <= f["age"] < FLIGHT_SECONDS)
    poll = extras.get("poll") or {}
    pad = lambda nid, p, o: (at[nid][0] + (p[0] - o[0]) * S(nid)[0], at[nid][1] + (p[1] - o[1]) * S(nid)[1])
    plant = (back + "".join(belts) + f'<g aria-hidden="true">{yard_still}</g>' + pw
             + f'<g aria-hidden="true">{"".join(parts)}{yard_moving}</g>'
             + "".join(node(st(sid), lambda x, y: _station(sid, label, fl[sid], word(sid, fl[sid]), href(sid), (x, y, True), sid))
                       for sid, label, _ in order if st(sid) in at)
             + f'<g aria-hidden="true">{"".join(arms.values())}</g>'
             + f'<g aria-hidden="true">{drones(now, poll.get("every"), poll.get("last"), pad("sources", PAD, SRC), pad("receiving", RECV_PAD, (12, 200)))}</g>')
    return (f'<svg class="fm" viewBox="0 0 {_f(width)} {_f(height)}" width="{_f(width)}" height="{_f(height)}" role="group" aria-label="The factory floor">'
            f'<rect class="fm-ground" width="{_f(width)}" height="{_f(height)}"/>{plant}<g aria-hidden="true">{flights}</g></svg>')
