"""Agent output is derived from untrusted ticket text and is posted to GitHub, so it is cleaned first:
no @mentions (they would ping people or teams), no images (a rendered image URL can leak data to a third party),
no active HTML, and a length cap. Code spans and fences are left untouched."""
import html
import re

IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", re.I)
DANGEROUS = re.compile(r"<(script|iframe|style|form|object|embed|link|meta)\b.*?(?:</\1\s*>|$)", re.I | re.S)
MENTION = re.compile(r"(?<![\w`/])@(?=[A-Za-z0-9])")
CODE = re.compile(r"(`{3}.*?`{3}|`[^`\n]+`)", re.S)


def sanitize_markdown(text: str, limit: int = 60000) -> str:
    parts = CODE.split(text)
    for i in range(0, len(parts), 2):                       # even indices are prose, odd are code
        p = IMG.sub("[image removed]", parts[i])
        p = DANGEROUS.sub("[html removed]", p)
        parts[i] = MENTION.sub("@​", p)
    text = "".join(parts)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n_(truncated)_"
    return text


INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
LINK = re.compile(r"\[([^\]\n]+)\]\((https://github\.com/[A-Za-z0-9._/#?=&;%~+-]*)\)")
HEADING = re.compile(r"(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
BULLET = re.compile(r"[ \t]*[-*+][ \t]+(.*)")
NUMBERED = re.compile(r"[ \t]*\d{1,3}[.)][ \t]+(.*)")
TABLE_SEP = re.compile(r"\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$")


def _inline(s: str) -> str:
    """Everything is escaped first, so the only markup is what is added here: code, bold and github.com links."""
    s = html.escape(s)
    parts = INLINE_CODE.split(s)
    for i in range(0, len(parts), 2):
        p = BOLD.sub(r"<strong>\1</strong>", parts[i])
        parts[i] = LINK.sub(r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', p)
    for i in range(1, len(parts), 2):
        parts[i] = f"<code>{parts[i]}</code>"
    return "".join(parts)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_render(text: str) -> tuple[str, list[tuple[str, str]]]:
    """A document as HTML and its headings as (anchor id, text). A small subset of Markdown (headings, paragraphs, bold, code,
    fences, lists, tables, github.com links). The text is untrusted: it is escaped before anything is added, raw HTML is shown as
    text, images are never rendered, and a link is made only for an https://github.com URL. Document headings start at h2."""
    lines, out, heads = text.replace("\r\n", "\n").split("\n"), [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1                                          # the closing fence; an unclosed one runs to the end
            out.append(f'<pre tabindex="0"><code>{html.escape(chr(10).join(code))}</code></pre>')
        elif not line.strip():
            i += 1
        elif (m := HEADING.fullmatch(line.strip())):
            level, ident = min(len(m.group(1)) + 1, 6), f"h-{len(heads)}"
            heads.append((ident, m.group(2)))
            out.append(f'<h{level} id="{ident}">{_inline(m.group(2))}</h{level}>')
            i += 1
        elif "|" in line and i + 1 < len(lines) and TABLE_SEP.fullmatch(lines[i + 1].strip()):
            head, rows = _cells(line), []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            tr = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>')
        elif BULLET.fullmatch(line) or NUMBERED.fullmatch(line):
            pat, tag = (BULLET, "ul") if BULLET.fullmatch(line) else (NUMBERED, "ol")
            items = []
            while i < len(lines) and (m := pat.fullmatch(lines[i])):
                items.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith("```") and not HEADING.fullmatch(lines[i].strip()) \
                    and not BULLET.fullmatch(lines[i]) and not NUMBERED.fullmatch(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
    return "".join(out), heads
