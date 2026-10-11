"""Runs INSIDE the render container: /in/<name>.html -> /out/<name>.png. No network, read-only root. The input files were already
validated by the factory (no scripts, no forms, no external resources except fonts, which cannot load here anyway)."""
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.html$")
BACKGROUND = (255, 255, 255)
MAX_W, MAX_H = 1600, 6000
MARGIN = 24


def crop_on_white(img: Image.Image) -> Image.Image:
    """The page is rendered on a transparent background: crop to what was painted and put it on white, which is what a browser shows
    behind a design with no background of its own. A background the design sets itself is kept."""
    img = img.convert("RGBA")
    box = img.getchannel("A").getbbox()
    if not box:
        raise ValueError("nothing was drawn")
    base = Image.new("RGBA", img.size, BACKGROUND + (255,))
    flat = Image.alpha_composite(base, img).convert("RGB")
    if box != (0, 0) + img.size:
        return flat.crop(box)
    # The design paints the whole window (a body colour or min-height:100vh): trim the empty page colour, keeping MARGIN around the content.
    content = ImageChops.difference(flat, Image.new("RGB", flat.size, flat.getpixel((0, 0)))).getbbox()
    if not content:                                     # one flat colour: nothing to tell content from page
        return flat
    left, top, right, bottom = content
    return flat.crop((max(0, left - MARGIN), max(0, top - MARGIN), min(flat.width, right + MARGIN), min(flat.height, bottom + MARGIN)))


def main() -> int:
    src, out, work = Path("/in"), Path("/out"), Path("/tmp/work")
    work.mkdir(parents=True, exist_ok=True)
    failed = 0
    for f in sorted(src.iterdir()):
        if not NAME.match(f.name) or f.is_symlink() or not f.is_file():
            continue
        stem = f.stem
        html = f.read_text(errors="replace")
        page, shot = work / f"{stem}.html", work / f"{stem}.png"
        page.write_text(html)
        try:
            subprocess.run(["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars", "--default-background-color=00000000",
                            "--force-device-scale-factor=1", f"--window-size={MAX_W},{MAX_H}", "--virtual-time-budget=3000",
                            "--user-data-dir=/tmp/profile", f"--screenshot={shot}", f"file://{page}"],
                           check=False, timeout=60, capture_output=True)
            crop_on_white(Image.open(shot)).save(out / f"{stem}.png", optimize=True)
        except Exception as e:
            failed += 1
            print(f"{stem}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
