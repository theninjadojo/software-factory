"""Small factory buildings for the floor: one per kind of station (and the GitHub depot), drawn in a 140x62 box.

`art(kind, state, x, y, w, h)` returns a nested SVG. state: "idle" (dark and still), "on" (powered: windows glow, cogs turn, the
moving part moves), "hold" (waiting for a person: lit blue, still) or "fault" (failed: dark, a red light). The motion is CSS in
style.css (`.ma ...`), so the page policy holds: no style attributes here. A station the config adds gets the assembler."""
import math

def cog(cx, cy, r, teeth, cls="cog", hub=True):
    pts = []
    ro, ri = r, r * 0.78
    n = teeth * 4
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = ro if (i % 4) in (1, 2) else ri
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    s = f'<g class="{cls}"><polygon class="cog-b" points="{" ".join(pts)}"/>'
    if hub:
        s += f'<circle class="cog-h" cx="{cx}" cy="{cy}" r="{r * 0.36:.1f}"/><circle class="cog-a" cx="{cx}" cy="{cy}" r="{r * 0.12:.1f}"/>'
    return s + "</g>"

def smoke(x, y, n=3):
    return "".join(f'<circle class="smoke s{k}" cx="{x}" cy="{y}" r="4"/>' for k in range(n))

GROUND = '<rect class="ground" x="0" y="57" width="140" height="5"/>'
def light(x, y): return f'<circle class="pl" cx="{x}" cy="{y}" r="2.2"/>'
def win(x, y, w=8, h=6, extra=""): return f'<rect class="win{extra}" x="{x}" y="{y}" width="{w}" height="{h}" rx="1"/>'

ART = {}
ART["poll"] = (  # radar: a hut and a tower whose dish sweeps
    '<rect class="body" x="16" y="34" width="52" height="23" rx="2"/><polygon class="roof" points="12,35 42,24 72,35"/>'
    + win(24, 41) + win(38, 41) + win(52, 41)
    + '<rect class="body" x="88" y="22" width="8" height="35"/><path class="strut" d="M84 57 L92 30 L100 57"/>'
    + '<g class="sweep"><path class="dish" d="M78 12 Q92 30 106 12 Z"/><line class="strut" x1="92" y1="20" x2="92" y2="10"/><circle class="tip" cx="92" cy="9" r="2"/></g>'
    + light(64, 30))
ART["classify"] = (  # sorter: a hopper over two meshing cogs, three chutes
    '<polygon class="roof" points="38,4 102,4 90,20 50,20"/><rect class="body" x="30" y="20" width="80" height="37" rx="2"/>'
    + '<rect class="panel" x="36" y="25" width="46" height="28" rx="2"/>' + cog(52, 39, 13, 8) + cog(72, 44, 9, 6, "cog ccw")
    + '<path class="chute a" d="M110 28 h16 v5 h-16z"/><path class="chute b" d="M110 38 h20 v5 h-20z"/><path class="chute c" d="M110 48 h14 v5 h-14z"/>'
    + win(88, 27, 16, 5) + light(104, 16))
ART["route"] = (  # signal box: a cabin, a big cog, a mast with two arms that swap
    '<rect class="body" x="22" y="28" width="66" height="29" rx="2"/><polygon class="roof" points="18,29 55,16 92,29"/>'
    + win(30, 34, 10, 7) + win(46, 34, 10, 7) + cog(72, 46, 11, 8)
    + '<rect class="body" x="108" y="6" width="5" height="51"/>'
    + '<g class="arm a"><rect class="sig" x="110" y="12" width="22" height="5" rx="1"/></g>'
    + '<g class="arm b"><rect class="sig" x="110" y="28" width="22" height="5" rx="1"/></g>'
    + light(100, 22))
ART["analyst"] = (  # lab: a dome with a lens that scans, an antenna
    '<rect class="body" x="18" y="30" width="100" height="27" rx="2"/><path class="roof" d="M48 30 A22 22 0 0 1 92 30 Z"/>'
    + '<g class="scan"><circle class="lens" cx="70" cy="22" r="8"/><line class="strut" x1="76" y1="28" x2="84" y2="36"/></g>'
    + win(26, 38) + win(38, 38) + win(98, 38)
    + '<line class="strut" x1="108" y1="30" x2="108" y2="10"/><circle class="tip blink" cx="108" cy="9" r="2"/>' + light(26, 50))
ART["designer"] = (  # drafting studio: a sawtooth roof, a drafting arm that swings, a cog
    '<rect class="body" x="16" y="30" width="108" height="27" rx="2"/>'
    + '<path class="roof" d="M16 30 L16 18 L42 30 L42 18 L68 30 L68 18 L94 30 Z"/>'
    + win(18, 21, 6, 6) + win(44, 21, 6, 6) + win(70, 21, 6, 6)
    + '<g class="swing"><line class="arm-l" x1="104" y1="30" x2="118" y2="10"/><line class="arm-l" x1="118" y1="10" x2="130" y2="22"/><path class="pen" d="M128 20 l5 6 l-6 -2z"/></g>'
    + cog(34, 46, 10, 8) + win(52, 40, 10, 8) + win(68, 40, 10, 8) + light(88, 36))
ART["architect"] = (  # blueprint office with a crane whose hook goes up and down
    '<rect class="body" x="14" y="26" width="76" height="31" rx="2"/><rect class="roof" x="12" y="22" width="80" height="5"/>'
    + '<rect class="blueprint" x="22" y="32" width="26" height="16" rx="1"/><path class="bp-l" d="M25 44 h8 v-8 h10"/>'
    + cog(70, 44, 11, 8)
    + '<rect class="body" x="104" y="6" width="6" height="51"/><rect class="body" x="82" y="5" width="50" height="4"/>'
    + '<g class="hook"><line class="strut" x1="126" y1="9" x2="126" y2="30"/><rect class="tip" x="122" y="30" width="8" height="5"/></g>' + light(86, 30))
ART["build"] = (  # assembler: two big cogs behind a window, a chimney with smoke, hazard trim
    '<rect class="body" x="24" y="12" width="92" height="45" rx="3"/><rect class="hazard" x="24" y="50" width="92" height="7"/>'
    + '<rect class="panel" x="32" y="18" width="62" height="30" rx="2"/>' + cog(52, 33, 15, 9) + cog(77, 33, 11, 7, "cog ccw")
    + '<rect class="body" x="100" y="0" width="10" height="14"/>' + smoke(105, 0) + win(98, 22, 12, 6) + win(98, 32, 12, 6) + light(110, 44))
ART["review"] = (  # inspection arch: a tunnel, a scanner eye on a mast, a beam that sweeps
    '<path class="body" d="M24 57 V30 Q24 22 32 22 H108 Q116 22 116 30 V57 H96 V40 Q96 34 90 34 H50 Q44 34 44 40 V57 Z"/>'
    + '<rect class="body" x="68" y="8" width="4" height="14"/><ellipse class="eye" cx="70" cy="8" rx="9" ry="5"/><circle class="pupil" cx="70" cy="8" r="2.4"/>'
    + '<g class="beamg"><polygon class="beam" points="70,13 60,52 80,52"/></g>'
    + win(28, 28) + win(104, 28) + cog(34, 48, 7, 6) + cog(106, 48, 7, 6, "cog ccw"))
ART["ci"] = (  # test rig: a row of check lights in turn, a pumping piston, a cog
    '<rect class="body" x="18" y="24" width="72" height="33" rx="2"/><rect class="roof" x="16" y="20" width="76" height="5"/>'
    + "".join(f'<circle class="chk c{i}" cx="{30 + i * 12}" cy="32" r="3.2"/>' for i in range(5))
    + cog(36, 47, 9, 7) + win(52, 42, 30, 8)
    + '<rect class="body" x="98" y="14" width="22" height="43" rx="2"/><rect class="panel" x="102" y="18" width="14" height="30"/>'
    + '<g class="piston"><rect class="rod" x="106" y="10" width="6" height="22"/><rect class="tip" x="103" y="8" width="12" height="5"/></g>' + light(112, 52))
ART["pr"] = (  # dispatch: a warehouse with a roller door, a rocket on its pad
    '<rect class="body" x="14" y="30" width="66" height="27" rx="2"/><polygon class="roof" points="10,31 47,20 84,31"/>'
    + '<rect class="door" x="24" y="38" width="22" height="19"/><path class="bp-l" d="M24 42 h22 M24 46 h22 M24 50 h22"/>' + win(56, 38, 14, 7)
    + '<rect class="pad" x="92" y="52" width="36" height="5"/>'
    + '<g class="rocket"><path class="rk" d="M110 6 Q118 16 118 34 L118 46 L102 46 L102 34 Q102 16 110 6 Z"/><circle class="win" cx="110" cy="24" r="3"/>'
    + '<path class="fin" d="M102 38 L96 48 L102 46 Z M118 38 L124 48 L118 46 Z"/><path class="flame" d="M104 46 Q110 60 116 46 Z"/></g>' + light(76, 26))


ART["github"] = (  # the ticket source: a warehouse with a drone pad on the roof and an intake door
    '<rect class="body" x="12" y="28" width="96" height="29" rx="2"/><rect class="roof" x="10" y="24" width="100" height="5"/>'
    '<rect class="door" x="22" y="36" width="26" height="21"/><path class="bp-l" d="M22 41 h26 M22 46 h26 M22 51 h26"/>'
    + win(58, 36, 10, 7) + win(74, 36, 10, 7) + win(90, 36, 10, 7)
    + '<ellipse class="pad" cx="84" cy="22" rx="22" ry="4"/><path class="padh" d="M78 20 v4 M90 20 v4 M78 22 h12"/>'
    '<circle class="padl" cx="64" cy="22" r="1.8"/><circle class="padl p1" cx="104" cy="22" r="1.8"/>'
    '<line class="strut" x1="24" y1="24" x2="24" y2="8"/><circle class="tip blink" cx="24" cy="7" r="2"/>'
    '<rect class="body" x="114" y="40" width="20" height="17" rx="1"/>' + light(30, 32))


STATE = {"run": "on", "wait": "hold", "fail": "fault", "none": "idle", "done": "idle"}


def art(kind: str, state: str, x: float, y: float, w: float = 136, h: float = 60) -> str:
    body = ART.get(kind, ART["build"])
    return (f'<svg class="ma {STATE.get(state, state)}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" viewBox="0 0 140 62" aria-hidden="true">'
            f'{GROUND}{body}</svg>')
