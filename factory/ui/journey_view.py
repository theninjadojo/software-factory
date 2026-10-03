"""The ticket journey page body: summary tiles, a map of the stations with belts along the route the ticket really took, and the steps
in order with agent, a timeline bar, time and tokens (the accessible alternative to the map). Built from dbm.journey(); every text is
escaped, colours and motion are CSS classes (the CSP forbids inline styles), the map is SVG with presentation attributes only."""
from .. import db as dbm
from .views import esc, tok

# machine id -> (label, column, row); steps whose station is not a machine ("trust") are drawn on "classify"
MACHINES = {"classify": ("Classify & route", 0, 0), "analyst": ("Analyst", 1, 0), "designer": ("Designer", 2, 0), "architect": ("Architect", 3, 0),
            "build": ("Build", 3, 1), "review": ("Review", 2, 1), "ci": ("CI", 1, 1), "needs": ("Needs you", 0, 1)}
ALIAS = {"trust": "classify"}
DEFAULT_PATH = ("classify", "analyst", "designer", "architect", "build", "review", "ci")
STEP_LABEL = {"trust": "Trust", **{k: v[0] for k, v in MACHINES.items()}}
W, H, GAP, X0, Y0, ROW = 176, 132, 52, 16, 44, 200
STATE_CLASS = {"done": "ok", "running": "run", "failed": "fail", "waiting": "wait", "queued": "wait"}
STATE_TEXT = {"none": "Not reached", "done": "Done", "running": "Running", "failed": "Failed", "waiting": "Needs you", "queued": "Queued", "retried": "Failed, then retried"}
STATUS_TEXT = {"none": "—", "running": "Running", "waiting": "Needs you", "failed": "Failed", "queued": "Queued", "done": "Done"}


def secs(s) -> str:
    if s is None:
        return "—"
    s = int(s)
    return "—" if s < 1 else f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s" if s < 3600 else f"{s // 3600}h {(s % 3600) // 60:02d}m"


def tokens(t: dict) -> str:
    return "—" if t["in"] is None and t["out"] is None else f'{tok(t["in"])} / {tok(t["out"])}'


def machine_of(step: dict) -> str | None:
    s = ALIAS.get(step["station"], step["station"])
    return s if s in MACHINES else None


def _xy(m: str) -> tuple[int, int]:
    _, c, r = MACHINES[m]
    return X0 + c * (W + GAP), Y0 + r * ROW


def _route(a: str, b: str, lane: int) -> list[tuple[float, float, float, float]]:
    """Orthogonal belt segments (x1, y1, x2, y2) from machine a to machine b; `lane` keeps repeat trips side by side."""
    ax, ay = _xy(a)
    bx, by = _xy(b)
    acx, bcx, acy, bcy = ax + W / 2, bx + W / 2, ay + H / 2, by + H / 2
    ca, cb = MACHINES[a][1], MACHINES[b][1]
    ra, rb = MACHINES[a][2], MACHINES[b][2]
    if ra == rb and abs(ca - cb) == 1:
        y = acy + (lane - 1) * 12
        return [(ax + W, y, bx, y)] if ca < cb else [(ax, y, bx + W, y)]
    if ca == cb:
        x = acx + (lane - 1) * 12
        return [(x, ay + H, x, by)] if rb > ra else [(x, ay, x, by + H)]
    if ra != rb:
        down = rb > ra
        cy = Y0 + H + (ROW - H) / 2 + (lane - 1) * 10
        return [(acx, ay + H if down else ay, acx, cy), (acx, cy, bcx, cy), (bcx, cy, bcx, by if down else by + H)]
    top = ra == 0
    ly = (Y0 - 22 - lane * 10) if top else (Y0 + ROW + H + 22 + lane * 10)
    edge = ay if top else ay + H
    return [(acx, edge, acx, ly), (acx, ly, bcx, ly), (bcx, ly, bcx, edge)]


def _path(segs) -> str:
    return " ".join(f"M{x1:.0f} {y1:.0f} L{x2:.0f} {y2:.0f}" for x1, y1, x2, y2 in segs)


def _visits(steps: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in steps:
        m = machine_of(s)
        if m:
            out.setdefault(m, []).append(s)
    return out


def _machine_state(v: list[dict]) -> str:
    if not v:
        return "none"
    last = v[-1]["state"]
    if last == "done" and any(x["state"] == "failed" for x in v):
        return "retried"
    return last


def map_svg(j: dict) -> str:
    steps, visits = j["steps"], _visits(j["steps"])
    belts, badges, lanes = "", "", {}
    for a, b in zip(DEFAULT_PATH, DEFAULT_PATH[1:]):
        belts += f'<path class="jy-belt dim" d="{_path(_route(a, b, 1))}"/>'
    prev = None
    for s in steps:
        m = machine_of(s)
        if m is None:
            continue
        if prev is not None and prev[0] != m:
            key = tuple(sorted((prev[0], m)))
            lane = lanes[key] = lanes.get(key, 0) + 1
            kind = "run" if s["state"] == "running" else "fail" if prev[1]["state"] == "failed" else "ok"
            segs = _route(prev[0], m, (lane - 1) % 3 + 1)
            belts += f'<path class="jy-belt {kind}" d="{_path(segs)}"/>'
            x1, y1, x2, y2 = max(segs, key=lambda q: abs(q[2] - q[0]) + abs(q[3] - q[1]))
            badges += (f'<g class="jy-badge {kind}"><circle cx="{(x1 + x2) / 2:.0f}" cy="{(y1 + y2) / 2:.0f}" r="11"/>'
                       f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2 + 4:.0f}" text-anchor="middle">{int(s["n"])}</text></g>')
        prev = (m, s)
    nodes = ""
    for m, (label, _, _) in MACHINES.items():
        x, y = _xy(m)
        v = visits.get(m, [])
        st = _machine_state(v)
        cls = "none" if st == "none" else "fail" if st == "retried" else STATE_CLASS.get(st, "none")
        sec = sum(s["seconds"] or 0 for s in v)
        tin = sum(s["tokens"]["in"] or 0 for s in v if s["kind"] == "run")
        tout = sum(s["tokens"]["out"] or 0 for s in v if s["kind"] == "run")
        t_line, k_line = (secs(sec) if sec else ""), (f"{tok(tin + tout)} tokens" if tin + tout else "")
        nodes += (f'<g class="jy-m {cls}"><rect x="{x}" y="{y}" width="{W}" height="{H}" rx="6"/>'
                  f'<text class="jy-ml" x="{x + 12}" y="{y + 28}">{esc(label)}</text>'
                  f'<text class="jy-ms" x="{x + 12}" y="{y + 52}">{esc(STATE_TEXT[st].upper())}</text>'
                  f'<text class="jy-mo" x="{x + 12}" y="{y + 76}">{esc(" · ".join(str(int(s["n"])) for s in v))}</text>'
                  f'<text class="jy-md" x="{x + 12}" y="{y + 98}">{esc(t_line)}</text>'
                  f'<text class="jy-md" x="{x + 12}" y="{y + 116}">{esc(k_line)}</text></g>')
    return (f'<svg class="jy-map" viewBox="0 0 {X0 * 2 + 4 * W + 3 * GAP} {Y0 + ROW + H + 80}" role="img" '
            f'aria-label="The route this ticket took, step by step. The list below has the same information.">{belts}{badges}{nodes}</svg>')


def legend() -> str:
    items = (("ok", "Finished"), ("run", "Running now"), ("wait", "Waiting for a person"), ("fail", "Failed, then retried"), ("none", "Not reached"))
    return ('<ul class="jy-legend">' + "".join(f'<li><span class="jy-dot {k}" aria-hidden="true"></span>{esc(t)}</li>' for k, t in items)
            + '<li class="muted">Numbers show the order a machine was visited in</li></ul>')


def tiles(j: dict) -> str:
    st, tk, models = j["status"], j["tokens"], j["models"]
    runs = [s for s in j["steps"] if s["kind"] == "run"]
    sub = ""
    if st == "running" and runs:
        sub = f'on the {STEP_LABEL.get(runs[-1]["station"], "next")} step'
    elif st == "waiting" and j["waiting"]:
        sub = j["waiting"]["message"][:60]
    elif st == "done":
        sub = "finished"
    cls = STATE_CLASS.get({"done": "done", "running": "running", "waiting": "waiting", "failed": "failed"}.get(st, ""), "none")
    live = st == "running"
    tin, tout = tk["in"], tk["out"]
    mod = ", ".join(sorted(models)) if models else "—"
    msub = " · ".join(f"{n}× {m}" for m, n in sorted(models.items()))
    return ('<div class="jy-tiles">'
            f'<div class="jy-tile card"><span class="lab">Status</span><b class="jy-st {cls}">{esc(STATUS_TEXT[st])}</b><span class="muted">{esc(sub)}</span></div>'
            f'<div class="jy-tile card"><span class="lab">{"Time so far" if live else "Total time"}</span><b class="mono">{esc(secs(j["seconds"]))}</b><span class="muted">{len(j["steps"])} steps</span></div>'
            f'<div class="jy-tile card"><span class="lab">Tokens</span><b class="mono">{esc(tok((tin or 0) + (tout or 0)) if tin is not None or tout is not None else "—")}</b>'
            f'<span class="muted">{esc(tok(tin))} in · {esc(tok(tout))} out{" so far" if live else ""}</span></div>'
            f'<div class="jy-tile card"><span class="lab">Agents</span><b>{esc(mod)}</b><span class="muted">{esc(msub)}</span></div></div>')


def _bar(s: dict, t0: float, span: float) -> str:
    if s["seconds"] is None or span <= 0:
        return '<span class="muted">—</span>'
    x = max(0.0, min(99.0, (s["started"] - t0) / span * 100))
    w = max(1.0, min(100 - x, s["seconds"] / span * 100))
    cls = STATE_CLASS.get(s["state"], "ok")
    return (f'<svg class="jy-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="jy-track" width="100" height="10"/>'
            f'<rect class="jy-fill {cls}" x="{x:.2f}" width="{w:.2f}" height="10"/></svg>')


def _label(s: dict) -> tuple[str, str]:
    if s["kind"] == "run":
        base = STEP_LABEL.get(s["station"], s["station"] or "Run")
        if s.get("run_kind") == "fix":
            return base, "CI fix round"
        if s.get("run_kind") == "conflicts":
            return base, "merge conflicts"
        return base, str(s.get("status") or "")
    if s["kind"] == "person":
        return "Needs you", (s.get("message") or "")[:100]
    return STEP_LABEL.get(s["station"], "Step"), (s.get("message") or "")[:100]


def steps_table(j: dict) -> str:
    steps = j["steps"]
    t0 = steps[0]["started"]
    end = max([s["finished"] for s in steps if s["finished"] is not None] + [s["started"] + (s["seconds"] or 0) for s in steps])
    span = max(1.0, end - t0)
    rows = ""
    for s in steps:
        label, note = _label(s)
        cls = {"running": "jy-live", "queued": "jy-dim"}.get(s["state"], "")
        if s["kind"] == "run":
            agent = f'{s["harness"]} · {s["model"]} · {s["effort"]}' if s.get("model") else "—"
            link = f'<a href="/runs/{int(s["run_id"])}">{esc(label)}</a>'
        else:
            agent, link = "—", esc(label)
        so_far = " so far" if s["state"] == "running" else ""
        rows += (f'<tr class="{cls}"><td data-l="#"><span class="jy-n {STATE_CLASS.get(s["state"], "ok")}">{int(s["n"])}</span></td>'
                 f'<td data-l="Step"><strong>{link}</strong> <span class="muted">{esc(note)}</span></td>'
                 f'<td data-l="Agent" class="mono">{esc(agent)}</td><td data-l="When and how long" class="jy-when">{_bar(s, t0, span)}</td>'
                 f'<td data-l="Time" class="mono num">{esc(secs(s["seconds"]))}{so_far}</td>'
                 f'<td data-l="Tokens in / out" class="mono num">{esc(tokens(s["tokens"]) if s["kind"] == "run" else "—")}{so_far if s["kind"] == "run" else ""}</td></tr>')
    total = (f'<tr class="jy-total"><td></td><td><strong>Total</strong></td><td class="muted">'
             f'{esc(" · ".join(f"{n}× {m}" for m, n in sorted(j["models"].items())))}</td><td></td>'
             f'<td class="mono num">{esc(secs(j["seconds"]))}</td><td class="mono num">{esc(tokens(j["tokens"]))}</td></tr>')
    heads = "".join(f"<th>{esc(h)}</th>" for h in ("#", "Step", "Agent", "When and how long", "Time", "Tokens in / out"))
    return (f'<section class="card jy-steps" aria-label="Steps in order"><div class="scroll"><table class="stack jy"><thead><tr>{heads}</tr></thead>'
            f'<tbody>{rows}</tbody><tfoot>{total}</tfoot></table></div></section>')


def timeline(j: dict) -> str:
    """The phone layout: a vertical numbered timeline with the same facts as the table (shown instead of the map and the table below 760px)."""
    items = ""
    for s in j["steps"]:
        label, note = _label(s)
        c = STATE_CLASS.get(s["state"], "ok")
        agent = f'{s["harness"]} · {s["model"]} · {s["effort"]}' if s["kind"] == "run" and s.get("model") else ""
        tk = tokens(s["tokens"]) if s["kind"] == "run" and s["tokens"]["in"] is not None else ""
        name = f'<a href="/runs/{int(s["run_id"])}">{esc(label)}</a>' if s["kind"] == "run" else esc(label)
        so_far = " so far" if s["state"] == "running" else ""
        items += (f'<li class="{"jy-live" if s["state"] == "running" else ""}"><span class="jy-n {c}">{int(s["n"])}</span><div><div class="jy-tl-top"><strong>{name}</strong>'
                  f'<span class="mono">{esc(secs(s["seconds"]))}{so_far}</span></div>'
                  + (f'<div class="muted">{esc(note)}</div>' if note else "")
                  + (f'<div class="mono muted">{esc(agent)}</div>' if agent else "")
                  + (f'<div class="mono muted">{esc(tk)} tokens{so_far}</div>' if tk else "")
                  + ('<div class="jy-stripe"></div>' if s["state"] == "running" else "") + "</div></li>")
    return (f'<ol class="jy-tl" aria-label="Steps in order">{items}</ol>')


def journey_html(j: dict | None) -> str:
    if not j or not j["steps"]:
        return '<p class="muted">The factory has not worked on this ticket yet. Its journey appears here when it starts.</p>'
    return (tiles(j) + f'<div class="jy-board">{map_svg(j)}{legend()}</div>' + steps_table(j) + timeline(j))


def live_wrap(j: dict | None, body: str) -> str:
    """While the ticket is running or waiting, the same refresh as the home page (the #live element with its fragment url)."""
    if j and j["status"] in ("running", "waiting", "queued"):
        return f'<div id="live" data-src="/fragment/journey">{body}</div>'
    return body
