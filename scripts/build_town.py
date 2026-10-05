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
}


def rename(s: str) -> str:
    return re.sub(r"\b(?:bl|ma)-", "tw-", s)


def main(paths) -> None:
    src = [Path(p).read_text() for p in paths]
    styles: dict = {}

    def cls(m):
        body = m.group(1).strip().rstrip(";")
        return styles.setdefault(body, f"tws{len(styles)}")

    art = {}
    for text in src:
        for fig in re.findall(r'<figure class="tile">(.*?)</figure>', text, flags=re.S):
            cap = re.search(r'class="cap">([^<]*)', fig)
            svg = re.search(r'<svg width="\d+" height="\d+" viewBox="0 0 (\d+) (\d+)"[^>]*>(.*)</svg>', fig, flags=re.S)
            if not cap or not svg or cap.group(1) not in ITEMS or "st-" in fig and "<figcaption" not in fig:
                continue
            kind, group = ITEMS[cap.group(1)]
            kind = "t-" + kind                                        # a kind of its own, apart from the editor tools
            w, h, body = int(svg.group(1)), int(svg.group(2)), svg.group(3)
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
            r = rename(rule.strip())
            head = r.split("{")[0].strip()
            body_ = r[r.find("{") + 1:-1]
            if r.startswith("@media") or "," in head or body_ in ("animation:none", "opacity:.6"):    # the boards' reduced-motion rules, rebuilt below
                continue
            if (head.startswith(".tw-") or head.startswith("@keyframes tw-")) and r not in seen:
                seen.add(r)
                css.append(r)
    for body, name in styles.items():
        if name in used:
            css.append(f".{name}{{{rename(body)}}}")
    css.append("@media (prefers-reduced-motion:reduce){.tw-art *{animation:none !important}.tw-art .tw-steam{opacity:.6}}")
    (OUT / "static" / "town.css").write_text("\n".join(css) + "\n")
    data = json.dumps(art, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    (OUT / "static" / "town.js").write_text("/* generated by scripts/build_town.py */\nwindow.TOWN_ART = " + data + ";\n")
    (OUT / "town_art.py").write_text('"""The town art, generated by scripts/build_town.py from the design canvas: kind -> name, group, box, svg."""\nART = '
                                     + pprint.pformat(art, width=140, sort_dicts=False) + "\n")
    print(len(art), "pieces,", len(css), "css rules")


if __name__ == "__main__":
    main(sys.argv[1:])
