"""Regenerate the town art (factory/ui/town_art.py, static/town.js, static/town.css) from the design canvas's asset boards.

    python scripts/build_town.py Assets2.dc.html Assets3.dc.html

Each tile of the boards is a 120x96 (vehicles 120x56) side-on picture. The page policy bans style attributes, so each distinct
style="..." becomes a generated class; the board's bl-* / ma-* animation names become tw-*. Writes the three files; commit them."""
import json
import pprint
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "factory" / "ui"
# caption -> (kind, group); anything not listed is left out (the people and park pieces exist already)
ITEMS = {
    "Residential · house": ("house", "Zones"), "Residential · apartments": ("apartments", "Zones"), "Commercial · offices": ("offices", "Zones"),
    "Industrial · factory": ("factory", "Zones"), "Industrial · warehouse": ("warehouse", "Zones"),
    "Coffee shop": ("coffee", "Shops"), "Fuel station": ("fuel", "Shops"), "Restaurant": ("restaurant", "Shops"), "Grocery": ("grocery", "Shops"),
    "Clothing": ("clothing", "Shops"),
    "School": ("school", "Civic"), "Hospital": ("hospital", "Civic"), "Library": ("library", "Civic"), "Town hall": ("townhall", "Civic"),
    "Post office": ("postoffice", "Civic"), "Bank": ("bank", "Civic"), "Fire station": ("firestation", "Civic"),
    "Police station": ("policestation", "Civic"),
    "Cinema": ("cinema", "Leisure"), "Gym": ("gym", "Leisure"), "Hotel": ("hotel", "Leisure"), "Pub": ("pub", "Leisure"),
    "Bakery": ("bakery", "Leisure"), "Pharmacy": ("pharmacy", "Leisure"), "Car wash": ("carwash", "Leisure"),
    "Water tower": ("watertower", "Utilities"), "Substation": ("substation", "Utilities"), "Recycling plant": ("recycling", "Utilities"),
    "Waste depot": ("wastedepot", "Utilities"),
    "Church": ("church", "Landmarks"), "Clock tower": ("clocktower", "Landmarks"), "Stadium": ("stadium", "Landmarks"),
    "Windmill": ("windmill", "Landmarks"), "Lighthouse": ("lighthouse", "Landmarks"),
    "Tree · spring": ("tree-spring", "Nature"), "Tree · summer": ("tree-summer", "Nature"), "Tree · autumn": ("tree-autumn", "Nature"),
    "Tree · winter": ("tree-winter", "Nature"), "Flower bed": ("flowerbed", "Nature"), "Pond": ("lilypond", "Nature"), "Farm": ("farm", "Nature"),
    "Fences and hedges": ("hedges", "Nature"),
    "Fire truck": ("firetruck", "Vehicles"), "Ambulance": ("ambulance", "Vehicles"), "Police car": ("policecar", "Vehicles"),
    "Bus": ("bus", "Vehicles"), "Tram": ("tram", "Vehicles"), "Delivery truck": ("delivery", "Vehicles"),
    "Food truck": ("foodtruck", "Vehicles"), "Tractor": ("tractor", "Vehicles"), "Bus stop": ("busstop", "Vehicles"),
    "Street musician": ("musician", "Moments"), "Kids on bikes": ("bikes", "Moments"), "Seagulls": ("seagulls", "Moments"),
    "Cat on the roof": ("cat", "Moments"),
    "Cat": ("animal-cat", "Animals"), "Rabbit": ("animal-rabbit", "Animals"), "Fox": ("animal-fox", "Animals"), "Sheep": ("animal-sheep", "Animals"),
    "Deer": ("animal-deer", "Animals"), "Horse": ("animal-horse", "Animals"), "Penguin": ("animal-penguin", "Animals"),
    "Unicorn": ("creature-unicorn", "Creatures"), "Pegasus": ("creature-pegasus", "Creatures"), "Dragon": ("creature-dragon", "Creatures"),
    "Yeti": ("creature-yeti", "Creatures"),
    "Classic": ("snowman", "Snowmen"), "Beanie and mittens": ("snowman-beanie", "Snowmen"), "Little one": ("snowman-little", "Snowmen"),
    "Snow family": ("snowman-family", "Snowmen"),
}
# animals, creatures and snowmen are drawn in a viewBox centred on their feet at a small scale; they are placed at this many times that size
SCALE = {"Animals": 2.2, "Creatures": 2.2, "Snowmen": 1.3}


def rename(s: str) -> str:
    s = re.sub(r"\b(?:bl|ma)-", "tw-", s)
    s = re.sub(r"(?<![\w-])(ch|sn)-(?=[a-z])", r"tw-\1-", s)                  # the creature and snow animations
    s = re.sub(r"(?<![\w-])\.?(walk|lg|am)\b(?![-\w])(?=[\s\"{.,]|$)", lambda m: m.group(0).replace(m.group(1), "tw-" + m.group(1)), s)
    return re.sub(r"(?<![\w-])legs\b", "tw-legs", s)


def main(paths) -> None:
    src = [Path(p).read_text() for p in paths]
    styles: dict = {}

    def cls(m):
        body = m.group(1).strip().rstrip(";")
        return styles.setdefault(body, f"tws{len(styles)}")

    art = {}
    for text in src:
        for fig in re.findall(r'<figure class="tile">(.*?)</figure>', text, flags=re.S):
            cap = re.search(r'class="cap[^"]*">([^<]*)', fig)
            svg = re.search(r'<svg width="\d+" height="\d+" viewBox="(-?[\d.]+) (-?[\d.]+) (\d+) (\d+)"[^>]*>(.*)</svg>', fig, flags=re.S)
            if not cap or not svg or cap.group(1) not in ITEMS or "st-" in fig and "<figcaption" not in fig:
                continue
            kind, group = ITEMS[cap.group(1)]
            kind = "t-" + kind                                        # a kind of its own, apart from the editor tools
            x0, y0, w, h, body = float(svg.group(1)), float(svg.group(2)), int(svg.group(3)), int(svg.group(4)), svg.group(5)
            sc = SCALE.get(group)
            if sc:                                                  # centred on the feet: move to the box's corner and enlarge
                body = f'<g transform="scale({sc}) translate({-x0:g} {-y0:g})">{body}</g>'
                w, h = round(w * sc), round(h * sc)
            def fix(m):
                c = cls(re.search(r'style="([^"]*)"', m.group(0)))
                return m.group(0)[:0] + f'@@{c}@@'
            body = re.sub(r"<(\w+)([^>]*?)\sstyle=\"([^\"]*)\"([^>]*)>",
                          lambda m: f'<{m.group(1)}{m.group(2)}{m.group(4)} @@{styles.setdefault(m.group(3).strip().rstrip(";"), "tws" + str(len(styles)))}@@>', body)
            def merge(m):                                           # @@c@@ joins an existing class attribute, or makes one
                return m.group(0)
            body = re.sub(r'class="([^"]*)"([^>]*?) @@(tws\d+)@@', r'class="\1 \3"\2', body)
            body = re.sub(r' @@(tws\d+)@@', r' class="\1"', body)
            body = re.sub(r"></(?:circle|rect|path|ellipse|g|line|polygon)>", lambda m: m.group(0), body)
            art[kind] = {"name": cap.group(1).replace("Residential · ", "").replace("Commercial · ", "").replace("Industrial · ", "").capitalize()
                         if group == "Zones" else cap.group(1), "group": group, "w": w, "h": h,                          "svg": rename(re.sub(r"\s+", " ", body).strip())}
    missing = ["t-" + v[0] for v in ITEMS.values() if "t-" + v[0] not in art]
    if missing:
        sys.exit("not found on the boards: " + ", ".join(missing))
    used = {c for a in art.values() for c in re.findall(r"tws\d+", a["svg"])}
    css = ["/* generated by scripts/build_town.py: the town art's animations and the classes its style attributes became */"]
    seen = set()
    for text in src:
        sheet = "".join(re.findall(r"<style>(.*?)</style>", text, flags=re.S))
        for rule in re.findall(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}|[^{}@]+\{[^{}]*\}", sheet):
            r = re.sub(r"^(?:/\*.*?\*/\s*)+", "", rename(rule.strip()), flags=re.S)
            head = r.split("{")[0].strip()
            body_ = r[r.find("{") + 1:-1]
            if r.startswith("@media") or "," in head or body_ in ("animation:none", "opacity:.6"):    # the boards' reduced-motion rules, rebuilt below
                continue
            if (head.startswith(".tw-") or head.startswith("@keyframes tw-")) and r not in seen:
                seen.add(r)
                css.append(r)
    css.append(".tw-lg,.tw-am{transform-box:fill-box;transform-origin:50% 0%}")      # the board's comma rules are skipped above
    css.append(".tw-walk .tw-lg.b,.tw-walk .tw-am{animation:tw-legs .5s ease-in-out infinite alternate-reverse}")
    for body, name in styles.items():
        if name in used:
            css.append(f".{name}{{{rename(body)}}}")
    css.append("@media (prefers-reduced-motion:reduce){.tw-art *{animation:none !important}.tw-art .tw-steam{opacity:.6}.tw-art .tw-ch-breath{opacity:0}}")
    (OUT / "static" / "town.css").write_text("\n".join(css) + "\n")
    data = json.dumps(art, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    (OUT / "static" / "town.js").write_text("/* generated by scripts/build_town.py */\nwindow.TOWN_ART = " + data + ";\n")
    (OUT / "town_art.py").write_text('"""The town art, generated by scripts/build_town.py from the design canvas: kind -> name, group, box, svg."""\nART = '
                                     + pprint.pformat(art, width=140, sort_dicts=False) + "\n")
    print(len(art), "pieces,", len(css), "css rules")


if __name__ == "__main__":
    main(sys.argv[1:])
