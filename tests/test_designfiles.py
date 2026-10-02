import unittest
from factory import designfiles

from factory.designfiles import (DesignFileRejected, MAX_BYTES, check_name, check_patch_adds_only, valid_dir, validate_html)

VALID = '''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spline+Sans:wght@400;700&display=swap">
  <style>
    body { margin: 0; font-family: 'Spline Sans', ui-sans-serif, system-ui, sans-serif; }
    a { color: #4C6EF5; text-decoration: none; }
  </style>
</helmet>
<div style="width: 1184px; box-sizing: border-box; background: #FFFBF5; color: #241E1A; padding: 24px;">
  <h1 style="margin: 0; font-size: 30px;">Settings</h1>
  <p>Three things you can change. <a href="#">Do the same here</a></p>
  <span style="display: inline-flex; border-radius: 16px; border: 1px solid #E8DFD3;">Change</span>
  <svg width="16" height="16" viewBox="0 0 16 16"><path d="M2 8h12" stroke="#000" fill="none"/></svg>
  <button>Save</button> <input placeholder="Name" value="Sam">
</div>
</x-dc>
</body>
</html>'''


def evil(fragment: str) -> str:
    return VALID.replace("<h1", fragment + "<h1", 1)


class Validator(unittest.TestCase):
    def test_a_canvas_like_the_existing_ones_is_accepted(self):
        validate_html(VALID)

    def test_everything_that_could_run_code_or_phone_home_is_rejected(self):
        hostile = {
            "inline script": "<script>alert(1)</script>",
            "remote script": '<script src="https://evil.example/x.js"></script>',
            "support.js with a payload": '<script src="./support.js">alert(1)</script>',
            "event handler": '<div onclick="alert(1)">x</div>',
            "handler on svg": '<svg onload="alert(1)"></svg>',
            "img": '<img src="x" onerror="alert(1)">',
            "tracking pixel": '<img src="https://evil.example/p.png">',
            "iframe": '<iframe src="https://evil.example"></iframe>',
            "object": '<object data="x"></object>',
            "embed": '<embed src="x">',
            "form": '<form action="https://evil.example"><input></form>',
            "base": '<base href="https://evil.example/">',
            "meta refresh": '<meta http-equiv="refresh" content="0;url=https://evil.example">',
            "remote stylesheet": '<link rel="stylesheet" href="https://evil.example/x.css">',
            "javascript link": '<a href="javascript:alert(1)">x</a>',
            "entity-hidden javascript link": '<a href="&#106;avascript:alert(1)">x</a>',
            "external link": '<a href="https://evil.example">x</a>',
            "css url in style attr": '<div style="background:url(https://evil.example/x)">x</div>',
            "css import": "<style>@import url('https://evil.example/x.css');</style>",
            "css url in style tag": "<style>div{background:url(https://evil.example/x)}</style>",
            "css escape": "<style>div{background:\\75rl(https://evil.example/x)}</style>",
            "css expression": '<div style="width:expression(alert(1))">x</div>',
            "svg use": '<svg><use href="https://evil.example/s.svg#a"></use></svg>',
            "formaction": '<button formaction="https://evil.example">x</button>',
            "srcdoc": '<div srcdoc="x">x</div>',
            "video": '<video src="x"></video>',
            "unknown element": "<marquee>x</marquee>",
        }
        for name, frag in hostile.items():
            with self.assertRaises(DesignFileRejected, msg=name):
                validate_html(evil(frag))

    def test_structure_and_size_rules(self):
        for bad in (VALID.replace("<!doctype html>", ""), VALID.replace("<x-dc>", "<div>").replace("</x-dc>", "</div>"),
                    VALID + "<?php echo 1 ?>", VALID.replace("<!doctype html>", "<!doctype html SYSTEM 'x'>"),
                    VALID + "\x00", VALID.replace("</div>", "x" * MAX_BYTES + "</div>")):
            with self.assertRaises(DesignFileRejected):
                validate_html(bad)


class Names(unittest.TestCase):
    def test_name_pattern_and_ticket_number(self):
        check_name("docs/design/factory-975-proration-basis.dc.html", 975)
        for bad in ("docs/design/factory-976-x.dc.html", "design/x.dc.html", "docs/design/factory-975-.dc.html", "docs/design/factory-975-X.dc.html",
                    "design/sub/factory-975-x.dc.html", "../docs/design/factory-975-x.dc.html", "docs/design/factory-975-x.html",
                    "docs/design/factory-975-" + "a" * 60 + ".dc.html", "web/docs/design/factory-975-x.dc.html"):
            with self.assertRaises(DesignFileRejected, msg=bad):
                check_name(bad, 975)


def added(path, extra=""):
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..1111111\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n{extra}"


class Directory(unittest.TestCase):
    def test_the_folder_is_configurable_and_safe(self):
        check_name("mockups/factory-5-a.dc.html", 5, "mockups")
        check_name("docs/ux/mocks/factory-5-a.dc.html", 5, "docs/ux/mocks")
        with self.assertRaises(DesignFileRejected):
            check_name("docs/design/factory-5-a.dc.html", 5, "mockups")                # the folder must match the setting
        for ok in ("docs/design", "mockups", "a/b/c"):
            self.assertTrue(valid_dir(ok), ok)
        for bad in ("", "/abs", "../x", "a/../b", ".github/x", ".git", "Docs", "a b", "a//b", "a/b/c/d/e", "x" * 40):
            self.assertFalse(valid_dir(bad), bad)
        for bad in ("../etc", ".github/workflows", "/tmp"):
            with self.assertRaises(DesignFileRejected):
                check_name(bad + "/factory-5-a.dc.html", 5, bad)


class PatchRules(unittest.TestCase):
    def test_only_brand_new_regular_files_with_good_names_are_accepted(self):
        ok = added("docs/design/factory-5-a.dc.html") + added("docs/design/factory-5-b.dc.html")
        self.assertEqual(check_patch_adds_only(ok, 5), ["docs/design/factory-5-a.dc.html", "docs/design/factory-5-b.dc.html"])

    def test_edits_deletes_renames_modes_and_extra_files_are_rejected(self):
        modify = "diff --git a/docs/design/factory-5-a.dc.html b/docs/design/factory-5-a.dc.html\nindex 1..2 100644\n--- a/docs/design/factory-5-a.dc.html\n+++ b/docs/design/factory-5-a.dc.html\n@@ -1 +1 @@\n-a\n+b\n"
        delete = "diff --git a/docs/design/factory-5-a.dc.html b/docs/design/factory-5-a.dc.html\ndeleted file mode 100644\nindex 1..0\n"
        rename = "diff --git a/docs/design/factory-5-a.dc.html b/docs/design/factory-5-b.dc.html\nsimilarity index 100%\nrename from docs/design/factory-5-a.dc.html\nrename to docs/design/factory-5-b.dc.html\n"
        exe = added("docs/design/factory-5-a.dc.html").replace("100644", "100755")
        for name, bad in (("modify", modify), ("delete", delete), ("rename", rename), ("exec", exe), ("canvas.json", added("design/canvas.json")),
                          ("existing-style name", added("design/Main.dc.html")), ("other dir", added("web/src/x.ts")),
                          ("empty", ""), ("too many", "".join(added(f"docs/design/factory-5-{c}.dc.html") for c in "abcd")),
                          ("symlink", added("docs/design/factory-5-a.dc.html").replace("100644", "120000"))):
            with self.assertRaises(DesignFileRejected, msg=name):
                check_patch_adds_only(bad, 5)
        with self.assertRaises(DesignFileRejected):
            check_patch_adds_only(added("docs/design/factory-5-a.dc.html"), 6)             # a different ticket's number


if __name__ == "__main__":
    unittest.main()


class LinkOk(unittest.TestCase):
    SHA = "a" * 40
    P = "docs/design/factory-3-x.dc.html"

    def f(self, **kw):
        d = {"repo": "o/web", "path": self.P, "url": f"https://github.com/o/web/blob/{self.SHA}/{self.P}", "pr": "https://github.com/o/web/pull/9"}
        return {**d, **kw}

    def test_accepts_the_sha_and_branch_forms(self):
        self.assertTrue(designfiles.link_ok(self.f()))
        self.assertTrue(designfiles.link_ok(self.f(url=f"https://github.com/o/web/blob/factory/design-3-t1/{self.P}", pr="")))

    def test_rejects_hostile_or_mismatched_values(self):
        for bad in (self.f(url="javascript:alert(1)"), self.f(url=f"http://github.com/o/web/blob/{self.SHA}/{self.P}"),
                    self.f(url=f"https://evil.example/o/web/blob/{self.SHA}/{self.P}"), self.f(repo="o/other"),
                    self.f(path="docs/design/../x/factory-3-x.dc.html"), self.f(pr="https://evil.example/pull/1"),
                    self.f(pr="https://github.com/o/other/pull/9"), self.f(url=f'https://github.com/o/web/blob/{self.SHA}/x"/{self.P}'),
                    {"repo": "o/web"}, None):
            self.assertFalse(designfiles.link_ok(bad), bad)
