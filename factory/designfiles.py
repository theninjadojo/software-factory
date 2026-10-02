"""Design mockups a designer agent may add to a repository's design/ folder.

The files are Claude Design canvas documents (*.dc.html): static HTML with inline styles that a person will later open in a
design tool. They are written by an agent that has read untrusted ticket text, so they are validated as strictly as the
patches that carry them: new files only, a fixed name pattern, an allowlist of tags, no scripts, no event handlers, no way to
load anything from the network (apart from the Google Fonts stylesheet the existing canvases use). A file that breaks a rule is
dropped; the written design document is still posted."""
import html
import re
from html.parser import HTMLParser

MAX_FILES = 3
MAX_BYTES = 150_000
NAME_RE = re.compile(r"^design/factory-(\d+)-[a-z0-9][a-z0-9-]{0,40}\.dc\.html$")

TAGS = {
    "html", "head", "meta", "title", "link", "style", "body", "x-dc", "helmet", "script",
    "div", "span", "p", "a", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em", "b", "i", "u", "s", "small", "br", "hr",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col", "button", "input", "label", "select", "option",
    "textarea", "nav", "header", "footer", "main", "section", "article", "aside", "figure", "figcaption", "code", "pre", "blockquote",
    "dl", "dt", "dd", "sup", "sub", "abbr", "time", "mark", "kbd", "details", "summary", "fieldset", "legend", "progress", "meter",
    "svg", "path", "circle", "rect", "line", "polyline", "polygon", "g", "text", "tspan", "defs", "lineargradient", "radialgradient",
    "stop", "ellipse", "clippath", "title", "desc",
}
BLOCKED_ATTRS = {"src", "srcdoc", "srcset", "action", "formaction", "poster", "ping", "data", "xlink:href", "background", "manifest",
                 "http-equiv", "autofocus", "form"}
FONT_HOSTS = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com")
CSS_BAD = re.compile(r"url\s*\(|@import|expression\s*\(|behavior\s*:|-moz-binding|javascript:|vbscript:|\\", re.I)
VALUE_BAD = re.compile(r"javascript:|vbscript:|data:text/html|data:application", re.I)


class DesignFileRejected(Exception):
    pass


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.saw_xdc = False
        self.in_style = False
        self.in_script = False

    def _bad(self, why: str):
        raise DesignFileRejected(why)

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self._bad(f"tag <{tag}> is not allowed")
        if tag == "x-dc":
            self.saw_xdc = True
        d = {k: (v or "") for k, v in attrs}
        for name, value in d.items():
            n = name.lower()
            if n.startswith("on"):
                self._bad(f"event handler {name}")
            if n in BLOCKED_ATTRS and not (tag == "script" and n == "src"):
                self._bad(f"attribute {name} is not allowed")
            if VALUE_BAD.search(value) or (n == "style" and CSS_BAD.search(value)):
                self._bad(f"a dangerous value in {name}")
            if n == "href" and tag != "link" and not value.startswith("#"):
                self._bad("links must be # anchors")
        if tag == "script":
            if d.get("src") != "./support.js" or any(k not in ("src",) for k in d):
                self._bad("the only script allowed is <script src=\"./support.js\">")
        if tag == "link":
            rel, href = d.get("rel", "").lower(), d.get("href", "")
            if rel not in ("stylesheet", "preconnect") or not href.startswith(FONT_HOSTS):
                self._bad("the only <link> allowed is a Google Fonts stylesheet or preconnect")
        if tag == "meta" and set(d) - {"charset", "name", "content"}:
            self._bad("meta may only set charset, name and content")
        if tag == "style":
            self.in_style = True
        if tag == "script":
            self.in_script = True

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False
        if tag == "script":
            self.in_script = False

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self.in_script and data.strip():
            self._bad("script elements must be empty")
        if self.in_style and CSS_BAD.search(data):
            self._bad("a style rule loads a resource or uses an escape")

    def handle_pi(self, data):
        self._bad("processing instructions are not allowed")

    def handle_decl(self, decl):
        if decl.strip().lower() != "doctype html":
            self._bad("only <!doctype html> is allowed")


def validate_html(text: str) -> None:
    if len(text.encode()) > MAX_BYTES:
        raise DesignFileRejected("file is too large")
    if "\x00" in text:
        raise DesignFileRejected("binary content")
    if not re.match(r"\s*<!doctype html>", text, re.I):
        raise DesignFileRejected("must start with <!doctype html>")
    c = _Checker()
    try:
        c.feed(text)
        c.close()
    except DesignFileRejected:
        raise
    except Exception as e:
        raise DesignFileRejected(f"could not parse: {e}")
    if not c.saw_xdc:
        raise DesignFileRejected("must wrap its content in <x-dc>, like the other canvases")


def check_name(path: str, ticket_number: int) -> None:
    m = NAME_RE.match(path)
    if not m or int(m.group(1)) != ticket_number:
        raise DesignFileRejected(f"{path}: design files must be named design/factory-{ticket_number}-<slug>.dc.html")


def check_patch_adds_only(patch: str, ticket_number: int) -> list[str]:
    """The patch may ADD (never modify, delete, rename or change the mode of) at most MAX_FILES files with allowed names."""
    sections = re.split(r"(?m)^diff --git ", patch)[1:]
    if not sections:
        raise DesignFileRejected("empty patch")
    paths = []
    for sec in sections:
        header = sec.splitlines()[0]
        m = re.match(r"a/(\S+) b/(\S+)$", header)
        if not m or m.group(1) != m.group(2):
            raise DesignFileRejected("renames and odd paths are not allowed")
        if not re.search(r"(?m)^new file mode 100644$", sec) or re.search(r"(?m)^(deleted file mode|old mode|new mode|rename |copy |similarity )", sec):
            raise DesignFileRejected(f"{m.group(1)}: only brand-new regular files may be added")
        check_name(m.group(1), ticket_number)
        paths.append(m.group(1))
    if len(paths) > MAX_FILES:
        raise DesignFileRejected(f"at most {MAX_FILES} design files")
    return paths
