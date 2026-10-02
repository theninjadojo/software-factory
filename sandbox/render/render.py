"""Runs INSIDE the render container: /in/<name>.html -> /out/<name>.png. No network, read-only root. The input files were already
validated by the factory (no scripts, no forms, no external resources except fonts, which cannot load here anyway)."""
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.html$")
SENTINEL = (255, 0, 255)
MAX_W, MAX_H = 1600, 6000
SHEET = "<style>html,body{background:#ff00ff !important}</style>"


def main() -> int:
    src, out, work = Path("/in"), Path("/out"), Path("/tmp/work")
    work.mkdir(parents=True, exist_ok=True)
    failed = 0
    for f in sorted(src.iterdir()):
        if not NAME.match(f.name) or f.is_symlink() or not f.is_file():
            continue
        stem = f.stem
        html = f.read_text(errors="replace")
        # The page background is a colour no design uses, so the design's own box can be cropped out of the window.
        html = re.sub(r"(?i)</head>", SHEET + "</head>", html, count=1) if re.search(r"(?i)</head>", html) else SHEET + html
        page, shot = work / f"{stem}.html", work / f"{stem}.png"
        page.write_text(html)
        try:
            subprocess.run(["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars",
                            "--force-device-scale-factor=1", f"--window-size={MAX_W},{MAX_H}", "--virtual-time-budget=3000",
                            "--user-data-dir=/tmp/profile", f"--screenshot={shot}", f"file://{page}"],
                           check=False, timeout=60, capture_output=True)
            img = Image.open(shot).convert("RGB")
            box = ImageChops.difference(img, Image.new("RGB", img.size, SENTINEL)).getbbox()
            if not box:
                raise ValueError("nothing was drawn")
            img.crop(box).save(out / f"{stem}.png", optimize=True)
        except Exception as e:
            failed += 1
            print(f"{stem}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
