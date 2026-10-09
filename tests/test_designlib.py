"""Design libraries: presets, checking what a person uploads or types, versions, which library a project gets, the mockup check, what the
agents are told, and the admin pages that manage them."""
import http.client
import io
import json
import re
import sqlite3
import tempfile
import tomllib
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import designfiles
from factory import designlib as D
from factory import runner
from factory.config import DesignCfg, Project, ProjectRepo, load, overrides_path, parse
from factory.runner import build_prompt, run_task
from test_designer_files import DesignerFlow, ROUTE, TICKET
from test_designfiles import VALID
from test_ui_admin import AdminCase

GOOD = {"name": "Acme brand",
        "colors": {"page": "#FFF", "surface": "#f3f5f8", "ink": "#0b1f3a", "muted": "#5b6b80", "line": "#c9d2de", "primary": "#1d6bf3",
                   "on-primary": "#ffffff", "error": "#c8102e", "accent": "#ff8a3d"},
        "fonts": {"heading": "Manrope", "body": "Manrope"},
        "type": {"title": {"size": 28, "weight": 800}, "body": 16},
        "spacing": [4, 8, 16], "radius": {"md": 8}}


def zipped(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(top + k, v)
    return buf.getvalue()


def memdb():
    db = sqlite3.connect(":memory:")
    D.ensure_tables(db)
    return db


class Tokens(unittest.TestCase):
    def test_every_preset_is_valid_readable_and_draws_a_valid_canvas(self):
        self.assertIn(D.DEFAULT, [p["id"] for p in D.presets()])
        for p in D.presets():
            self.assertEqual(D.clean_tokens(p["tokens"]), p["tokens"], p["id"])
            designfiles.validate_html(p["components"])
            self.assertEqual(D.contrast_notes(p["tokens"]), {}, p["id"])
            self.assertEqual(D.check_html(p["components"], p["tokens"]), [], p["id"])     # its own canvas keeps to it

    def test_tokens_are_normalised(self):
        t = D.clean_tokens({k: v for k, v in GOOD.items() if k != "name"})
        self.assertEqual(t["colors"]["page"], "#ffffff")
        self.assertEqual(t["type"]["body"], {"size": 16, "weight": 400})

    def test_each_problem_is_named_so_a_person_can_fix_it(self):
        bad = {**GOOD, "colors": {**GOOD["colors"], "primary": "blue"}, "spacing": [8, 4], "extra": 1}
        bad.pop("name")
        del bad["colors"]["error"]
        with self.assertRaises(D.LibraryRejected) as e:
            D.clean_tokens(bad)
        text = " ".join(e.exception.problems)
        for want in ("colour 'primary' is 'blue'. Use a hex value like", "missing error", "spacing must be", "unknown section 'extra'"):
            self.assertIn(want, text)

    def test_shadows_and_fonts_cannot_load_anything(self):
        for shadow in ({"x": "url(https://e.com/a.png)"}, {"x": "0 0 1px red; background: x"}):
            with self.assertRaises(D.LibraryRejected):
                D.clean_tokens({**{k: v for k, v in GOOD.items() if k != "name"}, "shadow": shadow})
        with self.assertRaises(D.LibraryRejected):
            D.clean_tokens({**{k: v for k, v in GOOD.items() if k != "name"}, "fonts": {"heading": "X'; }", "body": "Manrope"}})


class Zips(unittest.TestCase):
    def test_a_good_zip_in_a_folder_is_read_and_unknown_files_are_named(self):
        got, notes = D.read_zip(zipped({"tokens.json": json.dumps(GOOD), "README.md": "Be kind.\n", "logo.png": "x"}, "acme/"))
        self.assertEqual((got["name"], got["rules"], got["components"]), ("Acme brand", "Be kind.", ""))
        self.assertEqual(notes, ["Ignored logo.png: only tokens.json, README.md, components.dc.html are read."])

    def test_every_problem_is_reported_and_nothing_is_kept(self):
        bad_canvas = VALID.replace("<h1", '<img src="x.png"><h1', 1)
        with self.assertRaises(D.LibraryRejected) as e:
            D.read_zip(zipped({"tokens.json": json.dumps({**GOOD, "colors": {**GOOD["colors"], "primary": "blue"}}), "components.dc.html": bad_canvas}))
        text = " ".join(e.exception.problems)
        self.assertIn("tokens.json: colour 'primary' is 'blue'", text)
        self.assertIn("components.dc.html: tag <img> is not allowed", text)
        for data, want in ((b"not a zip", "not a zip"), (zipped({"README.md": "x"}), "no tokens.json"),
                           (zipped({"tokens.json": "{nope"}), "not valid JSON"), (b"x" * (D.MAX_ZIP + 1), "at most")):
            with self.assertRaises(D.LibraryRejected) as e:
                D.read_zip(data)
            self.assertIn(want, " ".join(e.exception.problems))

    def test_a_download_reads_back_as_the_same_library(self):
        lib = D.preset("friendly")
        got, notes = D.read_zip(D.to_zip(lib))
        self.assertEqual((got["name"], got["tokens"], got["rules"], notes), (lib["name"], lib["tokens"], lib["rules"], []))


class Store(unittest.TestCase):
    def test_versions_drafts_and_names(self):
        db = memdb()
        t = D.preset("neutral")["tokens"]
        lid = D.create(db, "Neutral", t, "rule", "", "edit", "neutral")
        self.assertEqual(lid, "neutral-2")                                              # never the id of a preset
        v = D.add_version(db, lid, {**t, "spacing": [2, 4]}, "", "", "tighter", "Ours")
        self.assertEqual((v, D.get(db, lid)["name"], D.get(db, lid)["tokens"]["spacing"], D.get(db, lid, 1)["tokens"]["spacing"]),
                         (2, "Ours", [2, 4], t["spacing"]))
        self.assertEqual([x["version"] for x in D.versions(db, lid)], [2, 1])
        self.assertIsNone(D.get(db, lid, 9))
        with self.assertRaises(D.LibraryRejected):
            D.add_version(db, "neutral", t, "", "", "")                                 # presets are read-only
        draft = D.create(db, "From code", t, "", "", "agent", "o/web", status="draft")
        with self.assertRaises(D.LibraryRejected):
            D.add_version(db, draft, t, "", "", "")
        self.assertEqual([x["id"] for x in D.own(db)], [draft, lid])                   # drafts first
        self.assertTrue(D.approve(db, draft))
        self.assertTrue(D.remove(db, draft))
        self.assertFalse(D.remove(db, "neutral"))

    def test_a_project_gets_its_library_and_falls_back_when_it_is_gone(self):
        db = memdb()
        lid = D.create(db, "Acme", D.preset("neutral")["tokens"])
        cfg = mock.Mock(design=DesignCfg("shikumi", "warn"), projects=())
        p = Project("shop", (ProjectRepo("o/web"),), design_library=lid, design_version=1, design_check="reject")
        self.assertEqual((D.resolve(db, cfg, p)["id"], D.resolve(db, cfg, p)["check"]), (lid, "reject"))
        self.assertEqual(D.resolve(db, cfg, replace(p, design_version=7))["version"], 1)     # a version that is gone: the newest
        self.assertEqual(D.resolve(db, cfg, replace(p, design_library="gone"))["id"], "shikumi")
        self.assertEqual(D.resolve(db, cfg, Project("x", ()))["check"], "warn")
        draft = D.create(db, "Draft", D.preset("neutral")["tokens"], status="draft")
        self.assertEqual(D.resolve(db, cfg, replace(p, design_library=draft))["id"], "shikumi")   # a draft is never used
        D.record_run(db, 5, D.resolve(db, cfg, p))
        self.assertEqual(D.run_library(db, 5), (lid, 1))

    def test_previews_are_rendered_once_and_failures_retried_later(self):
        db = memdb()
        rn = mock.Mock(render_previews=True)
        self.assertEqual(D.pending(db, 0.0), {})                                       # nothing is drawn until someone asks
        for p in D.presets():
            D.want_preview(db, p["components"])
        png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (100).to_bytes(4, "big") + (100).to_bytes(4, "big") + b"\0" * 20
        with mock.patch.object(D, "render_canvas", side_effect=lambda rn, docs: {s: png for s in list(docs)[:1]}) as r:
            self.assertEqual(D.render_pending(rn, db, 1000.0), 1)
            self.assertEqual(len(D.pending(db, 1001.0)), 0)                             # one rendered, the rest tried
            self.assertEqual(len(D.pending(db, 1000.0 + D.RETRY_PREVIEW + 1)), len(D.presets()) - 1)
        self.assertEqual(D.preview(db, D.preview_key(D.presets()[0]["components"])), png)
        self.assertEqual(r.call_count, 1)


class Mockups(unittest.TestCase):
    def test_off_library_colours_and_fonts_are_found(self):
        t = D.preset("neutral")["tokens"]
        canvas = VALID.replace("</x-dc>", '<svg><rect fill="#ABC"/></svg><a href="#add" style="color: rgb(9, 105, 218); font: 600 14px/1.2 Inter, sans-serif">x</a></x-dc>')
        found = D.check_html(canvas, t)
        self.assertIn("#4c6ef5", found[0])
        self.assertIn("#aabbcc", found[0])
        self.assertNotIn("#0969da", found[0])                                          # rgb(9,105,218) is the library's primary
        self.assertNotIn("#add", found[0])                                             # an anchor is not a colour
        self.assertEqual(found[1], "fonts not in the design library: Inter, Spline Sans")
        self.assertEqual(D.check_html(D.preset("neutral")["components"], t), [])


class Prompts(unittest.TestCase):
    def lib(self, **over):
        return {**D.preset("neutral"), "rules": "Use </design_library> carefully", "check": "warn", "png": None, **over}

    def test_designer_builder_and_reviewer_are_told_and_others_are_not(self):
        p = Project("shop", (ProjectRepo("o/web"),))
        for role, want in (("designer", "Design with this library"), (None, "Build with this library"), ("reviewer", "Check the UI this change adds")):
            text = build_prompt("t", "b", p, "o/web", role, design_library=self.lib())
            self.assertIn(want, text)
            self.assertIn("/task/design-library/", text)
            self.assertIn("primary #0969da", text)
            self.assertNotIn("</design_library> carefully", text)                       # the rules cannot close the wrapper
        self.assertNotIn("design_library", build_prompt("t", "b", p, "o/web", "analyst", design_library=self.lib()))
        self.assertIn("a mockup that uses others is discarded", build_prompt("t", "b", p, "o/web", "designer", design_library=self.lib(check="reject")))

    def test_the_task_folder_holds_the_library(self):
        files = D.task_files({**self.lib(), "png": b"png"})
        self.assertEqual(sorted(files), ["README.md", "components.dc.html", "components.png", "tokens.css", "tokens.json"])
        self.assertIn(b"--color-primary: #0969da;", files["tokens.css"])


class DesignerChecks(DesignerFlow):
    def go_lib(self, check):
        lib = {**D.preset("neutral"), "check": check, "png": None}
        t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, lambda w: self.write(w, "factory-3-reorder.dc.html", VALID))
        with p1, p2, mock.patch.object(runner, "build_prompt", wraps=build_prompt) as bp:
            res = run_task(cfg, gh, "o/web", TICKET, ROUTE, role="designer", design_library=lib)
        self.assertEqual(bp.call_args.kwargs["design_library"]["id"], "neutral")
        return res, gh

    def test_warn_publishes_and_says_what_is_off(self):
        res, gh = self.go_lib("warn")
        self.assertEqual(len(gh.prs), 1)
        self.assertIn("web: factory-3-reorder.dc.html uses colours not in the design library: ", res.notes)
        self.assertIn("#4c6ef5", res.notes)
        self.assertIn("fonts not in the design library: Spline Sans", res.notes)

    def test_reject_drops_the_mockup(self):
        res, gh = self.go_lib("reject")
        self.assertEqual((res.files, gh.prs), ([], []))
        self.assertIn("not published (factory-3-reorder.dc.html uses colours not in the design library", res.notes)


class Config(unittest.TestCase):
    def raw(self):
        return tomllib.loads(Path("config.example.toml").read_text())

    def test_projects_and_the_default_are_checked(self):
        raw = self.raw()
        raw["projects"] = [{"name": "shop", "repos": [{"repo": "o/web"}], "design_library": "acme", "design_version": 2, "design_check": "reject"}]
        p = parse(raw).projects[0]
        self.assertEqual((p.design_library, p.design_version, p.design_check), ("acme", 2, "reject"))
        for bad in ({"design_library": "Bad Id"}, {"design_check": "maybe"}, {"design_version": -1}):
            raw["projects"] = [{"name": "shop", "repos": [{"repo": "o/web"}], **bad}]
            with self.assertRaises(ValueError):
                parse(raw)
        raw["projects"] = []
        raw["design"] = {"check": "never"}
        with self.assertRaises(ValueError):
            parse(raw)


def proposal(extra: dict | None = None, name: str = "Shop") -> str:
    body = {**{k: v for k, v in GOOD.items() if k != "name"}, "name": name, "rules": ["Short labels.", "  One   primary button. "], **(extra or {})}
    return "Found the tokens in web/src/theme.css.\n```factory-design-library\n" + json.dumps(body) + "\n```\n"


class FromCode(unittest.TestCase):
    def test_the_block_is_checked_like_an_upload(self):
        got = D.parse_extract(proposal(), "shop (from code)")
        self.assertEqual((got["name"], got["rules"], got["tokens"]["colors"]["page"]), ("Shop (from code)", "Short labels.\nOne primary button.", "#ffffff"))
        for text, want in (("no block", "did not end with"), ("```factory-design-library\n{oops\n```", "not valid JSON"),
                           (proposal({"colors": {"page": "red"}}), "Use a hex value"), (proposal({"rules": "x"}), "rules must be a list")):
            with self.assertRaises(D.LibraryRejected) as e:
                D.parse_extract(text, "x")
            self.assertIn(want, " ".join(e.exception.problems))

    def test_one_request_per_project_and_lost_ones_expire(self):
        db = memdb()
        rid = D.request_extract(db, "shop", 1.0)
        self.assertIsNone(D.request_extract(db, "shop", 2.0))
        D.set_extract(db, rid, "running")
        self.assertEqual(D.requested_extracts(db), [])                                 # created long ago: lost in a restart
        self.assertEqual(D.recent_extracts(db, 3.0)[0]["detail"], "lost in a restart")
        self.assertIsNotNone(D.request_extract(db, "shop", 4.0))

    def job(self, res):
        from factory import main as m
        from factory.runner import RunResult
        cfg = load("config.example.toml")
        db = memdb()
        rid = D.request_extract(db, "shop", 1.0)
        with mock.patch.object(m.runner, "run_task", return_value=RunResult(*res)) as rt, mock.patch.object(m, "begin_run", return_value=7), \
                mock.patch.object(m, "end_run"), mock.patch.object(m, "emit"):
            m.design_extract_job(cfg, None, db, rid, cfg.projects[0])
        self.assertEqual(rt.call_args.kwargs["role"], "design-extract")
        self.assertEqual(rt.call_args.args[3]["number"], 0)
        return db, db.execute("SELECT status, detail, library FROM design_extracts WHERE id=?", (rid,)).fetchone()

    def test_a_valid_proposal_becomes_a_draft_and_a_bad_one_says_why(self):
        db, row = self.job(("stage", "done", None, proposal()))
        self.assertEqual(row[0], "done")
        lib = D.get(db, row[2])
        self.assertEqual((lib["status"], lib["source"], lib["origin"], lib["name"]), ("draft", "agent", "shop", "Shop (from code)"))
        db, row = self.job(("stage", "done", None, proposal({"colors": {"page": "red"}})))
        self.assertEqual(row[0], "failed")
        self.assertIn("did not pass the checks", row[1])
        self.assertEqual(D.own(db), [])
        db, row = self.job(("failed", "boom"))
        self.assertEqual(row[:2], ("failed", "the run ended failed"))

    def test_the_role_asks_for_the_block(self):
        text = build_prompt("Design library from the code of shop", "", Project("shop", (ProjectRepo("o/web"),)), "o/web", "design-extract")
        self.assertIn("factory-design-library", text)
        self.assertIn("do not create, edit or delete any files", text)


class Pages(AdminCase):
    def setUp(self):
        super().setUp()
        self.cookie, self.csrf = self.session()

    def get(self, path):
        return self.req("GET", path, cookie=self.cookie)

    def overrides(self):
        p = overrides_path(str(self.root / "config.toml"))
        return tomllib.loads(p.read_text()) if p.exists() else {}

    def upload(self, data, fields=(), name="acme-brand.zip"):
        b = "XBOUNDARYX"
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in [("csrf", self.csrf), *fields]]
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/zip\r\n\r\n'.encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{b}--\r\n".encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/design/upload", body=body, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": self.cookie,
                                                              "Content-Type": f"multipart/form-data; boundary={b}"})
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace"))
        c.close()
        return out

    def test_the_list_shows_presets_projects_and_the_menu_link(self):
        s, _, html = self.get("/design/libraries")
        self.assertEqual(s, 200)
        for want in ("<h1>Design libraries</h1>", "Neutral", "Dense dashboard", 'href="/design/libraries"', "shop", "Projects that have not picked one use"):
            self.assertIn(want, html)
        self.assertNotIn('style="', html)                                              # the admin pages allow no inline styles

    def test_upload_adds_a_library_and_a_bad_zip_explains_itself(self):
        s, h, _ = self.upload(zipped({"tokens.json": json.dumps(GOOD)}))
        self.assertEqual((s, h["Location"]), (303, "/design/library?id=acme-brand"))
        s, _, html = self.get(h["Location"])
        self.assertIn("Added Acme brand.", html)
        self.assertIn("Version 1", html)
        s, _, html = self.upload(zipped({"tokens.json": "{}"}))
        self.assertEqual(s, 422)
        self.assertIn("Not added:", html)
        self.assertIn("colors must be an object", html)
        s, h, _ = self.upload(zipped({"tokens.json": json.dumps({**GOOD, "radius": {"md": 2}})}), [("as", "acme-brand")])
        self.assertEqual(h["Location"], "/design/library?id=acme-brand")
        self.assertIn("version 2", self.get(h["Location"])[2])

    @staticmethod
    def inputs(html: str) -> dict:
        """Every text field of a page as the browser would send it."""
        fields = {}
        for tag in re.findall(r"<input [^>]*>", html):
            name, value = re.search(r'name="([^"]+)"', tag), re.search(r'value="([^"]*)"', tag)
            if name and 'type="checkbox"' not in tag and 'type="radio"' not in tag:
                fields[name.group(1)] = value.group(1).replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"') if value else ""
        return fields

    def test_duplicating_a_preset_then_editing_makes_versions(self):
        s, _, html = self.get("/design/edit?from=neutral")
        self.assertEqual(s, 200)
        fields = self.inputs(html)
        fields.update({"name": "Ours", "rules": "Short labels.", "c5_hex": "#123456"})
        s, h, _ = self.post(self.cookie, self.csrf, "/design/edit/save", fields)
        self.assertEqual((s, h["Location"]), (303, "/design/library?id=ours"))
        s, _, html = self.post(self.cookie, self.csrf, "/design/edit/save", {**fields, "from": "ours", "mode": "version", "c5_hex": "navy"})
        self.assertEqual(s, 422)
        self.assertIn("Use a hex value like", html)
        self.assertIn('value="navy"', html)                                            # what was typed is kept
        s, h, _ = self.post(self.cookie, self.csrf, "/design/edit/save", {**fields, "from": "ours", "mode": "version", "note": "darker"})
        self.assertEqual(h["Location"], "/design/library?id=ours")
        self.assertIn("Saved Ours, version 2.", self.get(h["Location"])[2])

    def test_a_project_picks_a_library_and_a_used_one_cannot_be_deleted(self):
        self.upload(zipped({"tokens.json": json.dumps(GOOD)}))
        s, _, html = self.get("/design/project?name=shop")
        self.assertEqual(s, 200)
        s, h, _ = self.post(self.cookie, self.csrf, "/design/project/save", {"name": "shop", "library": "acme-brand", "pin": "1", "check": "reject"})
        self.assertEqual(s, 303)
        p = self.overrides()["projects"][0]
        self.assertEqual((p["design_library"], p["design_version"], p["design_check"]), ("acme-brand", 1, "reject"))
        self.assertEqual(load(str(self.root / "config.toml")).projects[0].design_library, "acme-brand")
        _, h, _ = self.post(self.cookie, self.csrf, "/design/library/delete", {"id": "acme-brand"})
        self.assertIn("A project uses this library", self.get(h["Location"])[2])
        self.post(self.cookie, self.csrf, "/settings/projects", self.inputs(self.get("/settings?section=projects")[2]))
        self.assertEqual(self.overrides()["projects"][0]["design_library"], "acme-brand")   # saving Projects keeps the choice
        s, _, html = self.post(self.cookie, self.csrf, "/design/project/save", {"name": "shop", "library": "nope", "pin": "0", "check": ""})
        self.assertEqual(s, 422)

    def test_the_default_library_and_check(self):
        s, h, _ = self.post(self.cookie, self.csrf, "/design/default", {"library": "dense", "check": "off"})
        self.assertEqual(s, 303)
        self.assertEqual(self.overrides()["design"], {"library": "dense", "check": "off"})
        self.post(self.cookie, self.csrf, "/design/default", {"library": "neutral", "check": "warn"})
        self.assertNotIn("design", self.overrides())                                   # back to the built-in default: nothing stored

    def test_drafts_downloads_and_previews(self):
        db = sqlite3.connect(self.db_path)
        D.ensure_tables(db)
        lid = D.create(db, "From code", D.preset("neutral")["tokens"], source="agent", origin="o/web", status="draft")
        db.close()
        self.assertIn("Proposed by an agent from o/web", self.get("/design/libraries")[2])
        self.post(self.cookie, self.csrf, "/design/library/approve", {"id": lid})
        self.assertIn("Edit", self.get(f"/design/library?id={lid}")[2])
        s, h, body = self.get("/design/library/download?id=neutral")
        self.assertEqual((s, h["Content-Type"]), (200, "application/zip"))
        self.assertEqual(self.get("/design/preview?key=" + "0" * 32)[0], 404)
        self.assertEqual(self.get("/design/library?id=nope")[0], 404)

    def test_asking_for_a_library_from_the_code(self):
        s, h, _ = self.post(self.cookie, self.csrf, "/design/from-code", {"project": "shop"})
        html = self.get(h["Location"])[2]
        self.assertIn("Asked an agent to propose a library from shop", html)
        self.assertIn("Waiting to read shop", html)
        _, h, _ = self.post(self.cookie, self.csrf, "/design/from-code", {"project": "shop"})
        self.assertIn("already waiting or running", self.get(h["Location"])[2])
        _, h, _ = self.post(self.cookie, self.csrf, "/design/from-code", {"project": "nope"})
        self.assertIn("There is no such project", self.get(h["Location"])[2])


if __name__ == "__main__":
    unittest.main()
