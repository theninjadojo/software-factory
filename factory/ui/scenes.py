"""Scene packs: pictures and little stories people upload for the floor (the editor's "Your scenes"), like the bench whose old man walks
to the water to feed the ducks.

A pack is a zip of a scene.json and SVG files. It carries no code: scene.json only fills in a fixed set of steps (show a pose for some
seconds, walk to the nearest water or tree, walk home), and every SVG is rebuilt here from an allowlist of drawing and animation
elements and attributes, so no script, link, style, event handler or outside reference survives. What is kept is written out again
from scratch (never the uploaded text), its ids are renamed into the pack's own, and each picture is drawn inside a fixed clipped box,
so a pack cannot reach anything on the page or draw over the rest of the floor. The page's Content-Security-Policy stays as it is
behind all of that.

The compiled scene (one JSON file per pack in <state>/scenes/) is all the floor and the editor need: terrain.py and static/terrain.js
both place it from the same strings. docs/scene-packs.md describes the format; docs/scene-packs/duck-feeder is an example."""
import html
import io
import json
import re
import threading
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import terrain as T

DIR = "scenes"
FORMAT = 1
MAX_UPLOAD = 512 * 1024                    # the zip as uploaded
MAX_UNPACKED = 1024 * 1024                 # all its files together, unpacked
MAX_FILES = 16
MAX_SVG = 200 * 1024
MAX_ELEMENTS = 1500                        # in one SVG
MAX_ALL_ELEMENTS = 3000                    # in the whole pack
MAX_DEPTH = 24
MAX_COMPILED = 400 * 1024
MAX_SCENES = 40
BOX = ((20, 400), (20, 300))               # a scene's width and height, in floor pixels
POSE_BOX = (-60, -80, 120, 100)            # a pose is drawn round its feet at (0, 0) and clipped to this box (x, y, w, h)
TARGETS = ("water", "tree")
MAX_STEPS, MAX_POSES = 12, 8
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
POSE_NAME = re.compile(r"[a-z][a-z0-9-]{0,15}")
FILE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,60}\.svg")
WAY, BACK = "__WAY__", "__BACK__"          # filled in where the scene stands: which way the actor faces going out and coming home

SVG_NS = "{http://www.w3.org/2000/svg}"
ELEMENTS = {"g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "defs", "linearGradient", "radialGradient", "stop",
            "animate", "animateTransform", "animateMotion"}
SHAPE_ATTRS = {"x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2", "points", "d", "transform", "opacity",
               "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin",
               "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit", "offset", "stop-color", "stop-opacity", "gradientUnits",
               "gradientTransform", "spreadMethod", "fx", "fy", "fr", "id"}
ANIM_ATTRS = {"attributeName", "type", "values", "from", "to", "by", "dur", "begin", "end", "repeatCount", "repeatDur", "keyTimes",
              "keySplines", "keyPoints", "calcMode", "additive", "accumulate", "fill", "path", "rotate"}
ANIMATED = {"opacity", "fill", "stroke", "transform", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "d", "points",
            "stroke-width", "fill-opacity", "stroke-opacity", "stroke-dashoffset", "offset", "stop-color", "stop-opacity", "x1", "y1", "x2", "y2"}
TIME = re.compile(r"-?\d{1,6}(\.\d{1,4})?(ms|s)?")
TIMES = re.compile(rf"{TIME.pattern}(\s*;\s*{TIME.pattern}){{0,7}}")
URL = re.compile(r"url\(\s*#([A-Za-z][\w-]{0,40})\s*\)")
NS = re.compile(r"^\{.*\}")                  # an XML namespace in front of a name
LOCAL_ID = re.compile(r"[A-Za-z][\w-]{0,40}")
BAD = re.compile(r"javascript:|data:|expression|@import|\\", re.I)
LOCK = threading.Lock()
_SEEN: dict = {}


class PackError(ValueError):
    """A pack that is not taken: the message is safe to show."""


def _f(v: float) -> str:
    return T.f(v)


# ---------------------------------------------------------------- the SVG rebuild
def _seconds(v: str) -> float:
    return float(v[:-2]) / 1000 if v.endswith("ms") else float(v.rstrip("s"))


def _value(tag: str, name: str, v: str, ids: dict, dropped: set) -> str | None:
    """The attribute's value as it will be written, or None to leave it out."""
    v = " ".join(v.split())
    if len(v) > (20000 if name in ("d", "values", "path", "points") else 400) or BAD.search(v):
        dropped.add(f"{tag} {name}")
        return None
    if "url(" in v:
        if name not in ("fill", "stroke", "values", "from", "to") or any(m.group(1) not in ids for m in URL.finditer(v)) \
                or "url(" in URL.sub("", v):
            dropped.add(f"{tag} {name}")
            return None
        v = URL.sub(lambda m: f"url(#{ids[m.group(1)]})", v)
    if name == "id":
        return ids.get(v)
    if tag.startswith("animate"):
        if name == "attributeName" and v not in ANIMATED:
            dropped.add(f"{tag} attributeName={v[:30]}")
            return None
        if name in ("begin", "end") and not TIMES.fullmatch(v):
            dropped.add(f"{tag} {name}={v[:30]}")             # only times: no events, no "indefinite", nothing that waits on the page
            return None
        if name == "dur" and (not TIME.fullmatch(v) or _seconds(v) < 0.1):
            dropped.add(f"{tag} dur={v[:30]}")
            return None
        if name == "fill" and v not in ("freeze", "remove"):
            return None
    return v


def clean_svg(data: bytes, prefix: str, counter: list, dropped: set) -> str:
    """An uploaded SVG rebuilt from the allowlist: the root's children only (the root and its viewBox are left behind), each element
    and attribute written out afresh. counter[0] counts the pack's elements; dropped collects what was left out, to tell the person."""
    if len(data) > MAX_SVG:
        raise PackError(f"An SVG file is larger than {MAX_SVG // 1024} KB.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PackError("An SVG file is not UTF-8 text.")
    if re.search(r"<!(DOCTYPE|ENTITY)|<\?xml-stylesheet", text, re.I):
        raise PackError("An SVG file declares a DOCTYPE, entities or a stylesheet; packs may not.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise PackError("An SVG file is not well-formed XML.")
    if root.tag not in ("svg", SVG_NS + "svg"):
        raise PackError("An SVG file's outer element must be <svg>.")
    ids = {}
    for el in root.iter():
        v = el.get("id")
        if v and LOCAL_ID.fullmatch(v):
            ids[v] = f"{prefix}-{v}"

    def build(el, depth: int) -> str:
        tag = el.tag[len(SVG_NS):] if el.tag.startswith(SVG_NS) else el.tag
        if tag not in ELEMENTS or depth > MAX_DEPTH:
            dropped.add(NS.sub("", tag)[:30] or "?")
            return ""
        counter[0] += 1
        if counter[0] > MAX_ALL_ELEMENTS:
            raise PackError(f"The pack's pictures have more than {MAX_ALL_ELEMENTS} elements.")
        allowed = ANIM_ATTRS if tag.startswith("animate") else SHAPE_ATTRS
        attrs = ""
        for name, v in el.attrib.items():
            if name not in allowed:
                dropped.add(f"{tag} {NS.sub('', name)[:30]}")
                continue
            v = _value(tag, name, v, ids, dropped)
            if v is not None:
                attrs += f' {name}="{html.escape(v, quote=True)}"'
        inner = "".join(build(c, depth + 1) for c in el)
        return f"<{tag}{attrs}>{inner}</{tag}>" if inner else f"<{tag}{attrs}/>"

    before = counter[0]
    out = "".join(build(c, 1) for c in root)
    if counter[0] - before > MAX_ELEMENTS:
        raise PackError(f"An SVG file has more than {MAX_ELEMENTS} elements.")
    return out


# ---------------------------------------------------------------- scene.json and the steps
def _num(v, lo, hi) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def check(spec) -> list[str]:
    """What is wrong with scene.json's shape (nothing: it can be compiled)."""
    if not isinstance(spec, dict):
        return ["scene.json must be a JSON object."]
    errs = []
    extra = set(spec) - {"format", "name", "size", "art", "actor"}
    if extra:
        errs.append("scene.json has unknown keys: " + ", ".join(sorted(str(k)[:20] for k in extra)[:5]) + ".")
    if spec.get("format") != FORMAT:
        errs.append(f'scene.json needs "format": {FORMAT}.')
    name = spec.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or not name.isprintable() or not ID.fullmatch(slug(name)):
        errs.append('"name" is 1 to 40 characters, with at least one letter or digit.')
    size = spec.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in size)
            and BOX[0][0] <= size[0] <= BOX[0][1] and BOX[1][0] <= size[1] <= BOX[1][1]):
        return errs + [f'"size" is [width, height] in whole pixels, {BOX[0][0]} to {BOX[0][1]} wide and {BOX[1][0]} to {BOX[1][1]} high.']
    if "art" in spec and not (isinstance(spec["art"], str) and FILE_NAME.fullmatch(spec["art"])):
        errs.append('"art" is the name of an .svg file in the pack.')
    a = spec.get("actor")
    if a is None:
        if "art" not in spec:
            errs.append('A scene needs "art", an "actor", or both.')
        return errs
    if not isinstance(a, dict):
        return errs + ['"actor" is an object.']
    extra = set(a) - {"at", "poses", "rest", "target", "reach", "steps"}
    if extra:
        errs.append("The actor has unknown keys: " + ", ".join(sorted(str(k)[:20] for k in extra)[:5]) + ".")
    at = a.get("at")
    if not (isinstance(at, list) and len(at) == 2 and _num(at[0], 0, size[0]) and _num(at[1], 0, size[1])):
        errs.append('The actor\'s "at" is [x, y] inside the scene: where the actor stands at home.')
    poses = a.get("poses")
    if not (isinstance(poses, dict) and 1 <= len(poses) <= MAX_POSES and all(isinstance(k, str) and POSE_NAME.fullmatch(k)
                                                                            and isinstance(v, str) and FILE_NAME.fullmatch(v) for k, v in poses.items())):
        return errs + [f'The actor\'s "poses" are 1 to {MAX_POSES} names (lower-case letters, digits, dashes), each an .svg file in the pack.']
    if a.get("rest") not in poses:
        errs.append('The actor\'s "rest" is one of its poses: the one shown when there is nowhere to go.')
    if a.get("target") not in TARGETS:
        errs.append('The actor\'s "target" is ' + " or ".join(f'"{t}"' for t in TARGETS) + ": where it walks to.")
    if "reach" in a and not (isinstance(a["reach"], int) and not isinstance(a["reach"], bool) and 50 <= a["reach"] <= 1000):
        errs.append('The actor\'s "reach" is how far it will walk: 50 to 1000 pixels.')
    steps = a.get("steps")
    if not (isinstance(steps, list) and 1 <= len(steps) <= MAX_STEPS):
        return errs + [f'The actor\'s "steps" are a list of 1 to {MAX_STEPS} steps.']
    where = 0
    for i, s in enumerate(steps, 1):
        if not (isinstance(s, dict) and set(s) <= {"pose", "seconds", "walk"} and s.get("pose") in poses and _num(s.get("seconds"), 0.2, 120)
                and s.get("walk", None) in (None, "there", "home")):
            errs.append(f'Step {i} is {{"pose": one of the poses, "seconds": 0.2 to 120}}, with "walk": "there" or "home" to walk while '
                        'showing it.')
            continue
        if s.get("walk") == "there":
            if where == 1:
                errs.append(f"Step {i} walks there, but the actor is already there.")
            where = 1
        elif s.get("walk") == "home":
            if where == 0:
                errs.append(f"Step {i} walks home, but the actor is already home.")
            where = 0
    if where != 0:
        errs.append("The steps must end with the actor back home, so the story can start again.")
    return errs


def slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.strip().lower())).strip("-")[:24].strip("-")


def _visible(a: float, b: float) -> tuple[str, str]:
    """(opacity at the start, an animation) that shows a step's picture from a to b of the loop (fractions), hidden otherwise."""
    if a <= 0 and b >= 1:
        return "1", ""
    if a <= 0:
        vals, keys = "1;0", f"0;{b:.6g}"
    elif b >= 1:
        vals, keys = "0;1", f"0;{a:.6g}"
    else:
        vals, keys = "0;1;0", f"0;{a:.6g};{b:.6g}"
    return ("1" if a <= 0 else "0"), f'values="{vals}" keyTimes="{keys}"'


def actor(a: dict, poses: dict) -> dict:
    """The actor's story as markup: one group per step, shown only during its step, facing the way it goes; and the motion's key
    points, out to the target (1) and home (0)."""
    steps = a["steps"]
    total = sum(s["seconds"] for s in steps)
    dur = f'dur="{total:.6g}s" repeatCount="indefinite"'
    x0, y0, w, h = POSE_BOX
    box = lambda pose: f'<svg x="{x0}" y="{y0}" width="{w}" height="{h}" viewBox="{x0} {y0} {w} {h}">{poses[pose]}</svg>'
    t, where, times, points, moving = 0.0, 0, ["0"], ["0"], ""
    for s in steps:
        a0, t = t / total, t + s["seconds"]
        a1 = t / total
        walk = s.get("walk")
        if walk:
            where = 1 if walk == "there" else 0
        face = BACK if walk == "home" else WAY if (walk == "there" or where == 1) else ""
        start, anim = _visible(a0, a1)
        moving += (f'<g opacity="{start}">' + (f'<animate attributeName="opacity" {anim} calcMode="discrete" {dur}/>' if anim else "")
                   + (f'<g transform="{face}">' if face else "<g>") + box(s["pose"]) + "</g></g>")
        times.append(f"{a1:.6g}")
        points.append(str(where))
    times[-1] = "1"
    return {"x": a["at"][0], "y": a["at"][1], "target": a["target"], "reach": a.get("reach", 600),
            "rest": box(a["rest"]), "moving": moving,
            "keys": f'keyPoints="{";".join(points)}" keyTimes="{";".join(times)}" calcMode="linear" {dur}'}


def compile_pack(data: bytes) -> tuple[dict, list[str]]:
    """(the compiled scene, what was left out of its pictures) from an uploaded zip, or PackError."""
    if len(data) > MAX_UPLOAD:
        raise PackError(f"The pack is larger than {MAX_UPLOAD // 1024} KB.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in z.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, ValueError):
        raise PackError("The pack is not a zip file.")
    if len(infos) > MAX_FILES:
        raise PackError(f"The pack holds more than {MAX_FILES} files.")
    files, total = {}, 0
    for i in infos:
        name = i.filename.replace("\\", "/").rsplit("/", 1)[-1]           # a folder inside the zip is fine; only the name is used
        if name.startswith(".") or name.startswith("__MACOSX") or "__MACOSX/" in i.filename:
            continue
        if name != "scene.json" and not FILE_NAME.fullmatch(name):
            raise PackError(f"The pack may hold only scene.json and .svg files ({name[:40]!r} is neither).")
        if name in files:
            raise PackError(f"The pack holds two files called {name}.")
        try:
            with z.open(i) as fh:
                body = fh.read(MAX_SVG + 1)                              # never more than this, whatever the zip says the size is
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
            raise PackError(f"{name[:40]} could not be read from the zip (encrypted or damaged?).")
        if len(body) > MAX_SVG:
            raise PackError(f"{name[:40]} is larger than {MAX_SVG // 1024} KB.")
        total += len(body)
        if total > MAX_UNPACKED:
            raise PackError(f"The pack is larger than {MAX_UNPACKED // 1024} KB unpacked.")
        files[name] = body
    if "scene.json" not in files:
        raise PackError("The pack has no scene.json.")
    try:
        spec = json.loads(files["scene.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PackError("scene.json is not valid JSON.")
    errs = check(spec)
    if errs:
        raise PackError(errs[0])
    sid = slug(spec["name"])
    need = ([spec["art"]] if "art" in spec else []) + (sorted(set(spec["actor"]["poses"].values())) if spec.get("actor") else [])
    for n in need:
        if n not in files:
            raise PackError(f"scene.json names {n}, which is not in the pack.")
    counter, dropped, svgs = [0], set(), {}
    for n in need:
        svgs[n] = clean_svg(files[n], f"sc-{sid}-{len(svgs)}", counter, dropped)
    w, h = spec["size"]
    out = {"format": FORMAT, "id": sid, "name": " ".join(spec["name"].split()), "w": w, "h": h,
           "art": f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{svgs[spec["art"]]}</svg>' if "art" in spec else "",
           "actor": None}
    if spec.get("actor"):
        out["actor"] = actor(spec["actor"], {k: svgs[v] for k, v in spec["actor"]["poses"].items()})
    if len(json.dumps(out)) > MAX_COMPILED:
        raise PackError(f"The scene is larger than {MAX_COMPILED // 1024} KB once its steps are drawn; use fewer steps or simpler pictures.")
    return out, sorted(dropped)


# ---------------------------------------------------------------- the installed scenes
def folder(state_dir) -> Path:
    return Path(state_dir) / DIR


def _valid(doc) -> bool:
    """A compiled scene read back from disk has the shape compile_pack writes (the file is ours, but it is checked all the same)."""
    ok = (isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("id"), str) and ID.fullmatch(doc["id"])
          and isinstance(doc.get("name"), str) and isinstance(doc.get("art"), str)
          and isinstance(doc.get("w"), int) and BOX[0][0] <= doc["w"] <= BOX[0][1] and isinstance(doc.get("h"), int) and BOX[1][0] <= doc["h"] <= BOX[1][1])
    a = doc.get("actor") if ok else None
    return ok and (a is None or (isinstance(a, dict) and a.get("target") in TARGETS and all(isinstance(a.get(k), str) for k in ("rest", "moving", "keys"))
                                 and all(_num(a.get(k), 0, 1000) for k in ("x", "y", "reach"))))


def installed(state_dir) -> dict:
    """{kind ("sc-<id>"): compiled scene} for the packs in the state directory, read again only when the folder changes."""
    d = folder(state_dir)
    try:
        key = (str(d), d.stat().st_mtime_ns, tuple(sorted((p.name, p.stat().st_mtime_ns) for p in d.glob("*.json"))))
    except OSError:
        key = (str(d), None, ())
    if _SEEN.get("key") != key:
        out = {}
        for name, _ in key[2][:MAX_SCENES]:
            try:
                doc = json.loads((d / name).read_text())
            except (OSError, ValueError, RecursionError):
                continue
            if _valid(doc) and name == f"{doc['id']}.json":
                out["sc-" + doc["id"]] = doc
        _SEEN.clear()
        _SEEN.update(key=key, scenes=out)
    return _SEEN["scenes"]


def sync(state_dir) -> dict:
    """Load the installed scenes into the terrain (so it draws and checks them) and return them."""
    scenes = installed(state_dir)
    if T.SCENES is not scenes:
        T.set_scenes(scenes)
    return scenes


def add(state_dir, scene: dict) -> bool:
    """Keep a compiled scene (replacing one of the same name); False when there are already as many as allowed."""
    d = folder(state_dir)
    with LOCK:
        d.mkdir(mode=0o700, exist_ok=True)
        p = d / f"{scene['id']}.json"
        if not p.exists() and len(list(d.glob("*.json"))) >= MAX_SCENES:
            return False
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(scene, separators=(",", ":")))
        tmp.chmod(0o600)
        tmp.replace(p)
    sync(state_dir)
    return True


def remove(state_dir, sid: str) -> bool:
    if not ID.fullmatch(sid or ""):
        return False
    with LOCK:
        try:
            (folder(state_dir) / f"{sid}.json").unlink()
        except OSError:
            return False
    sync(state_dir)
    return True

