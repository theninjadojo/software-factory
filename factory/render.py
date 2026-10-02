"""Rendered previews of design canvases.

The designer's `.dc.html` files are validated first (designfiles.validate_html: no scripts, handlers, forms, frames or external
resources). A validated file is then turned into a PNG in a sealed container: no network, a read-only root, no capabilities, only
the validated file mounted (read-only) and one output folder. The PNG that comes back is untrusted too: it is accepted only if it is
a real PNG of a sane size, and it is committed by the orchestrator under a fixed name, never taken from the container's say-so."""
import logging
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import RunnerCfg

log = logging.getLogger("factory.render")
STEM = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES, MAX_W, MAX_H = 3_000_000, 1600, 6000


def png_ok(data: bytes) -> bool:
    """A real PNG of a reasonable size: the signature, an IHDR chunk first, dimensions within limits, and not too many bytes."""
    if not isinstance(data, bytes) or len(data) > MAX_PNG_BYTES or len(data) < 33 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        return False
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return 16 <= w <= MAX_W and 16 <= h <= MAX_H


def render_cmd(rn: RunnerCfg, name: str, work: Path) -> list[str]:
    user = ["--userns=keep-id:uid=1000,gid=1000"] if rn.engine == "podman" else ["--user", "1000:1000"]
    return [rn.engine, "run", "--rm", "--name", name, "--network=none", *user, "--read-only", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=1g", "--cpus=1",
            "--tmpfs", "/tmp:rw,size=512m,mode=1777", "-v", f"{work / 'in'}:/in:ro", "-v", f"{work / 'out'}:/out:rw", rn.render_image]


def render(rn: RunnerCfg, docs: dict[str, str], run=subprocess.run) -> dict[str, bytes]:
    """docs: file stem -> already-validated HTML text. Returns stem -> PNG bytes for the ones that rendered; the rest are left out
    (a failure to render never stops the design files from being published)."""
    if not rn.render_previews or not docs:
        return {}
    if any(not STEM.fullmatch(s) for s in docs):
        raise ValueError("unusable file name")
    Path(rn.work_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="render-", dir=rn.work_dir))
    name = f"factory-render-{secrets.token_hex(4)}"           # factory-*: the deploy guard and recovery see it like a run
    try:
        (work / "in").mkdir()
        (work / "out").mkdir()
        for stem, text in docs.items():
            (work / "in" / f"{stem}.html").write_text(text)
        run(["chmod", "-R", "a+rX", str(work / "in")], check=True)
        run(["chmod", "a+rwX", str(work / "out")], check=True)
        try:
            res = run(render_cmd(rn, name, work), capture_output=True, text=True, timeout=rn.render_timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            log.warning("preview rendering timed out after %ss", rn.render_timeout_seconds)
            run([rn.engine, "rm", "-f", name], capture_output=True, check=False)
            return {}
        except OSError:
            log.warning("could not start the preview renderer (is %s installed?)", rn.engine)
            return {}
        if getattr(res, "returncode", 0) not in (0, 1):
            log.warning("preview renderer exited %s: %s", res.returncode, (getattr(res, "stderr", "") or "")[-300:])
        out = {}
        for stem in docs:
            p = work / "out" / f"{stem}.png"
            if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_PNG_BYTES:
                continue
            data = p.read_bytes()
            if png_ok(data):
                out[stem] = data
            else:
                log.warning("preview for %s was not a usable PNG; dropped", stem)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)
