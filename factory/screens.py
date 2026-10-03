"""Screen verification during a build: render configured pages at each viewport and compare them with committed baselines.

Runs in the orchestrator on the clean clone *after* an agent's patch has been validated and applied, before anything is pushed.
The screen list comes from trusted config ([screens]), never from the repo, so a patch cannot drop a screen.

Two sealed containers (no network, read-only root, no capabilities, no credentials), from the same pinned image:
  1. shoot: the repo copy (without .git) mounted read-only; page code, which the agent wrote, runs here and only here.
  2. compare: receives only PNGs (baselines and fresh shots), never page code, so a compromised page cannot forge a verdict.
Everything that comes back is untrusted: each PNG must pass render.png_ok and match its baseline's size, and the verdict is only
numbers that are re-checked and re-judged here against the configured limit. A missing image, baseline or renderer fails the
check (closed), unlike previews. A repo with no configured screens is untouched."""
import json
import logging
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import RunnerCfg, ScreensCfg
from .render import MAX_PNG_BYTES, png_ok

log = logging.getLogger("factory.screens")


@dataclass
class Report:
    ok: bool = True
    lines: list[str] = field(default_factory=list)
    diffs: dict[str, bytes] = field(default_factory=dict)      # shot id -> diff PNG (validated), for the host artifacts folder

    def fail(self, line: str) -> "Report":
        self.ok = False
        self.lines.append(line)
        return self

    def text(self) -> str:
        return "\n".join(self.lines)


def shots_for(sc: ScreensCfg, repo: str) -> list[dict]:
    """Every (page, viewport) of `repo`: id `<page>-<viewport>`, as the containers and the baseline files name them."""
    vps = {v.name: v for v in sc.viewports}
    out = []
    for p in sc.pages:
        if p.repo != repo:
            continue
        for name in (p.viewports or tuple(vps)):
            out.append({"id": f"{p.name}-{name}", "path": p.path, "width": vps[name].width, "height": vps[name].height,
                        "mask": list(p.mask), "wait_for": p.wait_for})
    return out


def baselines_touched(paths: list[str], sc: ScreensCfg) -> bool:
    """True when a patch adds, changes or deletes anything under the baseline folder (a person must review those)."""
    return any(p.startswith(sc.baseline_dir.rstrip("/") + "/") for p in paths)


def _cmd(rn: RunnerCfg, sc: ScreensCfg, name: str, mounts: list[tuple[Path, str, str]], args: list[str]) -> list[str]:
    user = ["--userns=keep-id:uid=1000,gid=1000"] if rn.engine == "podman" else ["--user", "1000:1000"]
    vols = [x for d, target, mode in mounts for x in ("-v", f"{d}:{target}:{mode}")]
    return [rn.engine, "run", "--rm", "--name", name, "--network=none", *user, "--read-only", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--pids-limit=512", "--memory=2g", "--cpus=1",
            "--tmpfs", "/tmp:rw,size=512m,mode=1777", *vols, sc.image, *args]


def _exec(rn: RunnerCfg, sc: ScreensCfg, mounts, args, run) -> str | None:
    """Run one sealed container. None on success, else why it is unavailable. Never raises for a container failure."""
    name = f"factory-screens-{secrets.token_hex(4)}"           # factory-*: the deploy guard and recovery see it like a run
    try:
        res = run(_cmd(rn, sc, name, mounts, args), capture_output=True, text=True, timeout=sc.timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        run([rn.engine, "rm", "-f", name], capture_output=True, check=False)
        return f"timed out after {sc.timeout_seconds}s"
    except OSError:
        return f"could not start the container (is {rn.engine} installed?)"
    if getattr(res, "returncode", 0) != 0:
        return f"exited {res.returncode}: {(getattr(res, 'stderr', '') or '')[-300:]}"
    return None


def _png(path: Path) -> bytes | None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PNG_BYTES:
        return None
    data = path.read_bytes()
    return data if png_ok(data) else None


def _size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _verdict(path: Path, ids: list[str]) -> dict[str, int] | None:
    """verdict.json -> {id: differing pixels}. Strict: exactly the ids asked for, whole non-negative numbers, nothing else."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
            return None
        data = json.loads(path.read_text())
        rows = data["results"]
        out = {r["id"]: r["diff_pixels"] for r in rows}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(rows) != len(ids) or set(out) != set(ids) or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in out.values()):
        return None
    return out


def verify(rn: RunnerCfg, sc: ScreensCfg, repo: str, src: Path, run=subprocess.run) -> Report:
    """Check every configured screen of `repo` in the checkout `src` (patch already applied). Report.ok only when every
    (page, viewport) rendered, has a baseline of the same size, and differs from it in at most max_diff_ratio of its pixels."""
    rep = Report()
    shots = shots_for(sc, repo)
    if not shots:
        return rep
    Path(rn.work_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="screens-", dir=rn.work_dir))
    try:
        for sub in ("site", "spec", "actual", "base", "out", "cmp"):
            (work / sub).mkdir()
        shutil.copytree(src, work / "site", symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        (work / "spec" / "spec.json").write_text(json.dumps({"shots": shots, "max_h": 6000, "threshold": sc.threshold}))
        run(["chmod", "-R", "a+rX", str(work / "site"), str(work / "spec")], check=True)
        run(["chmod", "a+rwX", str(work / "actual"), str(work / "out")], check=True)
        why = _exec(rn, sc, [(work / "site", "/in", "ro"), (work / "spec", "/spec", "ro"), (work / "actual", "/out", "rw")],
                    ["/app/shoot.js"], run)
        if why:
            return rep.fail(f"screen verification unavailable: rendering {why}")
        todo, sizes = [], {}
        for s in shots:
            actual = _png(work / "actual" / f"{s['id']}.png")
            base = _png(src / sc.baseline_dir / f"{s['id']}.png")
            if actual is None:
                rep.fail(f"{s['id']}: no usable screenshot was produced ({s['path']})")
            elif base is None:
                rep.fail(f"{s['id']}: no baseline at {sc.baseline_dir}/{s['id']}.png")
            elif _size(actual) != _size(base):
                rep.fail(f"{s['id']}: size changed from {'x'.join(map(str, _size(base)))} to {'x'.join(map(str, _size(actual)))}")
            else:
                todo.append(s["id"])
                sizes[s["id"]] = _size(base)
                (work / "base" / f"{s['id']}.png").write_bytes(base)
                (work / "cmp" / f"{s['id']}.png").write_bytes(actual)
        if not todo:
            return rep
        (work / "spec" / "spec.json").write_text(json.dumps({"shots": [{"id": i} for i in todo], "threshold": sc.threshold}))
        run(["chmod", "-R", "a+rX", str(work / "base"), str(work / "cmp"), str(work / "spec")], check=True)
        why = _exec(rn, sc, [(work / "base", "/base", "ro"), (work / "cmp", "/actual", "ro"), (work / "spec", "/spec", "ro"),
                             (work / "out", "/out", "rw")], ["/app/compare.js"], run)
        if why:
            return rep.fail(f"screen verification unavailable: comparing {why}")
        diffs = _verdict(work / "out" / "verdict.json", todo)
        if diffs is None:
            return rep.fail("screen verification unavailable: the comparison returned no usable verdict")
        for i in todo:
            w, h = sizes[i]
            if diffs[i] > w * h:
                rep.fail(f"{i}: the comparison returned an impossible count")
            elif diffs[i] > sc.max_diff_ratio * w * h:
                rep.fail(f"{i}: {100 * diffs[i] / (w * h):.2f}% of pixels differ (limit {100 * sc.max_diff_ratio:.2f}%)")
                if (d := _png(work / "out" / f"{i}-diff.png")) is not None:
                    rep.diffs[i] = d
            else:
                rep.lines.append(f"{i}: ok ({diffs[i]} of {w * h} pixels differ)")
        return rep
    finally:
        shutil.rmtree(work, ignore_errors=True)
