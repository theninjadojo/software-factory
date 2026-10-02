"""Agent output is derived from untrusted ticket text and is posted to GitHub, so it is cleaned first:
no @mentions (they would ping people or teams), no images (a rendered image URL can leak data to a third party),
no active HTML, and a length cap. Code spans and fences are left untouched."""
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
