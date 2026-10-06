"""Scene packs (factory/ui/scenes.py): the example pack compiles and is drawn like the bench's old man; hostile pictures, zips and
scene.json files are cleaned or refused; uploading, using and removing a pack through the editor."""
import html
import http.client
import io
import json
import re
import unittest
import zipfile
from pathlib import Path

from factory.ui import floorplan as F, scenes as S, terrain as T
from test_floorplan import CTX
from test_ui_admin import AdminCase

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "scene-packs" / "duck-feeder"
SPEC = {"format": 1, "name": "Tiny", "size": [40, 30], "art": "art.svg"}


def pack(files: dict, folder: str = "") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(folder + name, data if isinstance(data, (bytes, str)) else json.dumps(data))
    return b.getvalue()


def example() -> bytes:
    return pack({p.name: p.read_bytes() for p in EXAMPLE.iterdir()}, "duck-feeder/")


def art(body: str, spec=None) -> tuple[dict, list]:
    """A one-picture pack with this inside its <svg>, compiled."""
    return S.compile_pack(pack({"scene.json": spec or SPEC, "art.svg": f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'}))


class Example(unittest.TestCase):
    def tearDown(self):
        T.set_scenes({})

    def test_the_duck_feeder_compiles_whole_and_walks_to_the_water(self):
        sc, dropped = S.compile_pack(example())
        self.assertEqual(dropped, [])
        self.assertEqual((sc["id"], sc["w"], sc["h"], sc["actor"]["target"]), ("duck-feeder", 70, 40, "water"))
        self.assertIn('keyPoints="0;0;1;1;0;0"', sc["actor"]["keys"])            # sit, walk out, feed, walk back, sit: as the old man
        self.assertIn('dur="28s"', sc["actor"]["keys"])
        T.set_scenes({"sc-duck-feeder": sc})
        self.assertIn("sc-duck-feeder", T.KINDS)
        self.assertIn("sc-duck-feeder", T.SOLID)
        self.assertEqual(T.footprint(["sc-duck-feeder", 400, 400, 70, 2]), (365, 380, 70, 40))
        near = T.svg({"items": [["sc-duck-feeder", 400, 400, 70, 2], ["pond", 600, 500, 90, 1]]}, 20, 2000, 2000, layer="over")
        self.assertIn('class="pk-scene"', near)
        self.assertIn('<g class="sc-actor"><animateMotion path="M 35 37 L ', near)
        self.assertNotIn("__WAY__", near)
        far = T.svg({"items": [["sc-duck-feeder", 400, 400, 70, 3]]}, 20, 2000, 2000, layer="over")
        self.assertNotIn("sc-actor", far)                                     # no water in reach: he stays on his bench
        self.assertIn("scale(-1 1)", far)                                     # an odd seed mirrors it
        self.assertNotIn("style=", near + far)

    def test_a_layout_may_use_an_installed_scene_and_not_an_unknown_one(self):
        sc, _ = S.compile_pack(example())
        t = {"items": [["sc-duck-feeder", 400, 400, 70, 2]]}
        self.assertTrue(F.terrain_shape(t))
        T.set_scenes({"sc-duck-feeder": sc})
        self.assertEqual(F.terrain_shape(t), [])
        self.assertTrue(F.terrain_shape({"items": [["sc-duck-feeder", 400, 400, 71, 2]]}))   # its size is its width


class Hostile(unittest.TestCase):
    def test_scripts_handlers_links_and_styles_are_left_out(self):
        out, dropped = art('<script>alert(1)</script><rect width="5" height="5" onload="alert(1)" style="fill:red" class="fe-svg"/>'
                           '<foreignObject><body xmlns="http://www.w3.org/1999/xhtml"><iframe src="x"/></body></foreignObject>'
                           '<a href="javascript:alert(1)"><circle r="3"/></a><use href="#x"/><image href="https://evil.example/x.png"/>'
                           '<style>svg{display:none}</style><text>hi</text><set attributeName="href" to="javascript:alert(1)"/>')
        a = out["art"]
        for bad in ("script", "onload", "style", "class=", "foreignObject", "iframe", "href", "javascript", "<a", "<use", "<image", "<text", "<set"):
            self.assertNotIn(bad, a, bad)
        self.assertIn('<rect width="5" height="5"/>', a)
        self.assertIn("script", dropped)
        self.assertIn("rect onload", dropped)

    def test_animations_may_not_touch_links_or_wait_on_events(self):
        out, dropped = art('<rect width="5" height="5"><animate attributeName="href" values="a;b" dur="1s"/>'
                           '<animate attributeName="opacity" values="0;1" dur="1s" begin="click"/>'
                           '<animate attributeName="opacity" values="0;1" dur="1s" begin="other.end"/>'
                           '<animate attributeName="opacity" values="0;1" dur="0.001s"/>'
                           '<animate attributeName="opacity" values="0;1" dur="2s" begin="-0.5s" repeatCount="indefinite"/></rect>')
        a = out["art"]
        self.assertNotIn('attributeName="href"', a)
        self.assertNotIn("click", a)
        self.assertNotIn("other.end", a)
        self.assertNotIn('dur="0.001s"', a)
        self.assertIn('<animate attributeName="opacity" values="0;1" dur="2s" begin="-0.5s" repeatCount="indefinite"/>', a)

    def test_only_its_own_gradients_and_renamed_so_it_cannot_reach_the_page(self):
        out, dropped = art('<defs><linearGradient id="tl-grass"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
                           '<rect width="5" height="5" fill="url(#tl-grass)"/><rect width="5" height="5" fill="url(#lg-lamp)"/>'
                           '<rect width="5" height="5" fill="url(https://evil.example/a)"/><rect width="5" height="5" stroke="url(#tl-grass) url(#x)"/>')
        a = out["art"]
        self.assertIn('id="sc-tiny-0-tl-grass"', a)
        self.assertIn('fill="url(#sc-tiny-0-tl-grass)"', a)
        self.assertNotIn('"tl-grass"', a)
        self.assertNotIn("lg-lamp", a)
        self.assertNotIn("evil", a)
        self.assertEqual(a.count("url("), 1)

    def test_quotes_and_markup_in_values_are_written_safely(self):
        out, _ = art('<rect width="5" height="5" fill="#fff&quot; onload=&quot;alert(1)"/><path d="M0 0 L 1 1 &lt;/svg&gt;&lt;script&gt;"/>')
        a = out["art"]
        self.assertNotIn('" onload', a)
        self.assertNotIn("<script", a)
        self.assertIn("&quot;", a)

    def test_doctypes_entities_and_broken_files_are_refused(self):
        lol = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;">]><svg>&b;</svg>'
        for bad in (lol, '<svg><!DOCTYPE x></svg>', '<?xml-stylesheet href="x.css"?><svg/>', "<svg><rect></svg>", "<html/>", b"\xff\xfe<svg/>"):
            with self.assertRaises(S.PackError, msg=bad):
                S.compile_pack(pack({"scene.json": SPEC, "art.svg": bad}))

    def test_too_many_elements_or_too_deep(self):
        with self.assertRaises(S.PackError):
            art("<g>" + '<circle r="1"/>' * (S.MAX_ELEMENTS + 1) + "</g>")
        out, dropped = art("<g>" * (S.MAX_DEPTH + 5) + '<circle r="1"/>' + "</g>" * (S.MAX_DEPTH + 5))
        self.assertNotIn("circle", out["art"])

    def test_zip_tricks_are_refused_and_nothing_is_written_from_one(self):
        good = {"scene.json": SPEC, "art.svg": "<svg/>"}
        self.assertEqual(S.compile_pack(pack(good, "../../etc/"))[0]["id"], "tiny")   # only a file's own name is used, and nothing is extracted
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("scene.json", json.dumps(SPEC))
            z.writestr("art.svg", b"<svg>" + b" " * (50 * 1024 * 1024) + b"</svg>")
        cases = [b"not a zip", bomb.getvalue(), pack({"art.svg": "<svg/>"}), pack({"scene.json": SPEC}),
                 pack({**good, "run.sh": "rm -rf /"}), pack({**good, "x.js": "alert(1)"}), pack({**good, **{f"{i}.svg": "<svg/>" for i in range(20)}}),
                 pack({"scene.json": "{nope", "art.svg": "<svg/>"}), b"x" * (S.MAX_UPLOAD + 1)]
        for data in cases:
            with self.assertRaises(S.PackError):
                S.compile_pack(data)

    def test_scene_json_is_checked(self):
        actor = {"at": [10, 10], "poses": {"a": "a.svg"}, "rest": "a", "target": "water",
                 "steps": [{"pose": "a", "seconds": 1, "walk": "there"}, {"pose": "a", "seconds": 1, "walk": "home"}]}
        self.assertEqual(S.check({**SPEC, "actor": actor}), [])
        bad = [{**SPEC, "script": "alert(1)"}, {**SPEC, "format": 2}, {**SPEC, "name": "!!!"}, {**SPEC, "size": [5000, 30]},
               {**SPEC, "art": "../x.svg"}, {"format": 1, "name": "x", "size": [40, 30]},
               {**SPEC, "actor": {**actor, "target": "people"}}, {**SPEC, "actor": {**actor, "rest": "b"}},
               {**SPEC, "actor": {**actor, "at": [99, 10]}}, {**SPEC, "actor": {**actor, "poses": {"a": "a.js"}}},
               {**SPEC, "actor": {**actor, "steps": actor["steps"][:1]}},                                  # never comes home
               {**SPEC, "actor": {**actor, "steps": [{"pose": "a", "seconds": 1, "walk": "home"}]}},       # home already
               {**SPEC, "actor": {**actor, "steps": [{"pose": "a", "seconds": 0.01}]}},
               {**SPEC, "actor": {**actor, "steps": [{"pose": "a", "seconds": 1, "run": "x"}]}}]
        for spec in bad:
            self.assertTrue(S.check(spec), spec)


class Uploading(AdminCase):
    def tearDown(self):
        T.set_scenes({})
        S._SEEN.clear()
        super().tearDown()

    def upload(self, cookie, csrf, data: bytes, name="pack.zip"):
        token = "XBOUNDARYX"
        body = (f'--{token}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{csrf}\r\n'
                f'--{token}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/zip\r\n\r\n').encode()
        body += data + f"\r\n--{token}--\r\n".encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/floor/scenes/upload", body=body, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": cookie,
                                                                     "Content-Type": f"multipart/form-data; boundary={token}"})
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace"))
        c.close()
        return out

    def page(self, cookie):
        s, _, page = self.req("GET", "/floor/edit", cookie=cookie)
        self.assertEqual(s, 200)
        return page

    def test_upload_place_and_remove_a_pack(self):
        cookie, csrf = self.session()
        self.assertIn("No scene packs yet.", self.page(cookie))
        self.assertEqual(self.upload(cookie, "wrong", example())[0], 403)
        s, h, _ = self.upload(cookie, csrf, example())
        self.assertEqual((s, h["Location"]), (303, "/floor/edit?ok=scene_added"))
        self.assertEqual([p.name for p in (self.state / "scenes").iterdir()], ["duck-feeder.json"])
        page = self.page(cookie)
        self.assertIn('data-tool="sc-duck-feeder"', page)
        self.assertIn('data-cat="Your scenes"', page)
        self.assertNotIn("style=", page)
        scenes = json.loads(html.unescape(re.search(r'data-scenes="([^"]*)"', page).group(1)))
        self.assertEqual(list(scenes), ["sc-duck-feeder"])
        # place it on the layout and save: the floor draws it
        text = html.unescape(re.search(r'<textarea name="plan"[^>]*>(.*?)</textarea>', page, re.S).group(1))
        rev = re.search(r'name="rev" value="([^"]*)"', page).group(1)
        doc = json.loads(text)
        doc["terrain"] = {"items": [["sc-duck-feeder", 3900, 2350, 70, 2]]}
        s, _, body = self.post(cookie, csrf, "/floor/layout/save", {"plan": json.dumps(doc), "rev": rev})
        self.assertEqual(s, 303, body[:2000])
        self.assertIn('class="pk-scene"', self.req("GET", "/", cookie=cookie)[2])
        # in use: not removed
        s, _, body = self.post(cookie, csrf, "/floor/scenes/remove", {"id": "duck-feeder"})
        self.assertEqual(s, 409)
        self.assertIn("still on the floor", body)
        doc["terrain"] = {}
        rev = F.rev(self.state)
        self.assertEqual(self.post(cookie, csrf, "/floor/layout/save", {"plan": json.dumps(doc), "rev": rev})[0], 303)
        self.assertEqual(self.post(cookie, csrf, "/floor/scenes/remove", {"id": "duck-feeder"})[0], 303)
        self.assertEqual(list((self.state / "scenes").iterdir()), [])
        self.assertEqual(self.post(cookie, csrf, "/floor/scenes/remove", {"id": "../floor-layout"})[0], 404)

    def test_a_bad_pack_is_refused_with_the_reason(self):
        cookie, csrf = self.session()
        s, _, body = self.upload(cookie, csrf, b"not a zip")
        self.assertEqual(s, 422)
        self.assertIn("The scene pack was not added: The pack is not a zip file.", html.unescape(body))
        s, _, body = self.upload(cookie, csrf, pack({"scene.json": SPEC, "art.svg": '<svg><script>x</script><rect width="2" height="2"/></svg>'}))
        self.assertEqual(s, 200)
        self.assertIn("without what is not allowed in a pack: script", html.unescape(body))
        self.assertEqual(self.upload(cookie, csrf, b"x" * (S.MAX_UPLOAD + 10))[0], 413)
