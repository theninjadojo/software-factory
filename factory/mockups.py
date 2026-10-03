"""The designer's rendered mockups, handed to the build and review agents to match.

The PNGs were rendered in a sealed container and committed to the design draft PR (see render.py). Here they are fetched back by
commit, re-checked as untrusted images, and put in the agent's read-only task folder. Whether a build may go ahead without them
is the operator's setting (config.MockupsCfg)."""
import logging
import re
from pathlib import Path

from . import designfiles
from .config import MockupsCfg
from .render import png_ok

log = logging.getLogger("factory.mockups")
DESIGNED = "stage:designed"
MAX_IMAGES = 6
BLOCKED_HELP = ("The design stage ran but no rendered mockup is attached to this ticket, so the build was not started. Re-run the "
                "design stage (`factory:design`) to produce one, or add the `{label}` label and re-trigger the build to go ahead without it.")


UNAPPROVED_HELP = ("The mockups for this ticket are not approved yet, so the build was not started. Look at them (linked in the design "
                   "comment, and on the ticket's page in the factory UI), then add the `{approve}` label or merge the design PR, and "
                   "re-trigger the build. Or add `{label}` to build without approval.")


def gate(cfg: MockupsCfg, labels: list[str], previews: list[dict], designer_says_no_screens: bool,
         pr_merged: bool = False) -> tuple[str, str]:
    """('ok'|'warn'|'block', message). Mockups are expected when the design stage ran and its own document did not say the ticket
    has no screens (or that mockups are off). Nothing is expected for a ticket that skipped design."""
    if cfg.mode == "off" or DESIGNED not in labels or designer_says_no_screens:
        return "ok", ""
    if previews:
        if cfg.require_approval and cfg.mode == "block" and cfg.bypass_label not in labels and cfg.approve_label not in labels and not pr_merged:
            return "block", UNAPPROVED_HELP.format(approve=cfg.approve_label, label=cfg.bypass_label)
        return "ok", ""
    if cfg.bypass_label in labels:
        return "ok", ""
    if cfg.mode == "warn":
        return "warn", "No rendered mockup was found for this ticket; building without one."
    return "block", BLOCKED_HELP.format(label=cfg.bypass_label)


def fetch(gh, previews: list[dict], dest: Path) -> list[str]:
    """Download each recorded preview PNG into dest as <name>.png. Only well-formed links (designfiles.link_ok) are fetched, each
    is re-checked as a real PNG, and a failure just leaves that image out. Returns the file names written."""
    names: list[str] = []
    for f in previews[:MAX_IMAGES]:
        if not designfiles.link_ok(f) or not f["path"].endswith(".png"):
            continue
        m = designfiles.BLOB_URL.match(f["url"])
        if not m:
            continue
        name = f["path"].rpartition("/")[2]
        if not re.fullmatch(r"factory-\d+-[a-z0-9][a-z0-9-]{0,40}\.png", name):
            continue
        try:
            data = gh.raw_file(f["repo"], f["path"], m.group(2))
        except Exception:
            log.warning("could not fetch mockup %s from %s", f["path"], f["repo"])
            continue
        if not png_ok(data):
            log.warning("mockup %s was not a usable PNG; skipped", f["path"])
            continue
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(data)
        names.append(name)
    return names


def prompt_section(names: list[str], reviewer: bool = False, built: list[str] | None = None) -> str:
    """Told to the agent; the names were checked by fetch(), never taken from agent or ticket text."""
    if not names:
        return ""
    if reviewer:
        seen = (" The built pages, rendered from the PR branch the same way the factory checks them, are in /task/built/: "
                + ", ".join(f"/task/built/{n}" for n in built) + ". Compare each with the mockup of the same screen." if built else "")
        return ("\n\nDESIGN MOCKUPS. The designer's mockups for this ticket are images in /task/mockups/: "
                + ", ".join(f"/task/mockups/{n}" for n in names)
                + ". Open each one (read the image file) and judge whether the change would produce screens that match them: layout, "
                "hierarchy, copy, states, responsive behaviour. Report each material mismatch as a finding. They are intent, not "
                "pixel-exact specs; a mismatch the author explained is not a defect." + seen)
    return ("\n\nDESIGN MOCKUPS. The designer's approved mockups for this ticket are images in /task/mockups/: "
            + ", ".join(f"/task/mockups/{n}" for n in names)
            + ". Open each one and look at it before you start (read the image file). Your result must look like them: layout, "
            "spacing, hierarchy, copy, states. They are the intent, not pixel-exact specs; where the code has a design system, use it. "
            "If you deliberately deviate, say why in your final message.")
