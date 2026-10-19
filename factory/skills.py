"""Skills: instructions an agent reads only when they fit the work, instead of carrying them in every prompt.

Each run gets its skills as /task/skills/<name>/SKILL.md (the Agent Skills format: YAML front matter with a name and a
description, then Markdown). The sandbox's entrypoint copies the folder to where each agent CLI looks for user skills, and
the prompt lists every skill with its path, so an agent without skill support can still read one. Two kinds:
- the factory's own (named factory-*): long how-to text that only some runs need, such as the design-mockup format;
- the operator's ([[skills]] in the config, edited on the Skills page): trusted configuration, like [prompts].
What every run must obey (the read-only rules, untrusted ticket text, the machine-read output blocks) stays in the prompt:
a skill is read when the agent decides it is relevant, so it never carries a rule that must always hold."""
import json
from dataclasses import dataclass
from pathlib import Path

TASK_DIR = "/task/skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str

    def path(self) -> str:
        return f"{TASK_DIR}/{self.name}/SKILL.md"

    def text(self) -> str:
        # json.dumps gives a double-quoted string that is also valid YAML, so a colon or quote in the description is safe
        return f"---\nname: {self.name}\ndescription: {json.dumps(self.description)}\n---\n\n{self.body.strip()}\n"


DESIGN_MOCKUPS = Skill(
    "factory-design-mockups",
    "How to write the static design mockups (*.dc.html) a designer creates for a ticket: the page structure, the visual style "
    "and the rules the factory enforces on every file. Read it before creating any design file.",
    "# Design mockups\n\n"
    "Write them into the design folder named in your instructions, in the repository whose screens change, using your "
    "file-writing tools, before you write your final answer. Name each one `<design folder>/factory-<ticket number>-<short-slug>.dc.html` "
    "(lower-case letters, digits and dashes in the slug; the ticket number is given in the ticket header).\n\n"
    "## Style\n\n"
    "When `*.dc.html` files exist, open two or three of them first and copy their structure and visual style (the <head>, fonts, "
    "colours, spacing, corner radii, the way cards and buttons look), so yours sit naturally beside them. When none exist, use a "
    "clean, neutral style that follows what you can see of the existing app's look in its code (its CSS, colours, fonts and "
    "spacing): a system or single Google font, a light background, one accent colour, consistent spacing.\n\n"
    "## Structure\n\n"
    "A mockup is a COMPLETE static page: `<!doctype html>`, `<html>`, a `<head>` containing `<meta charset=\"utf-8\">` and "
    "`<script src=\"./support.js\"></script>`, then `<body><x-dc>` with an optional `<helmet>` holding the Google Fonts "
    "`<link rel=\"stylesheet\" ...>` and a `<style>` block, then your markup with INLINE styles, then `</x-dc></body></html>`.\n\n"
    "## Enforced rules\n\n"
    "These are checked mechanically, and a file that breaks any of them is discarded (your document is still posted):\n"
    "- static HTML only; the ONLY script allowed is that `support.js` include;\n"
    "- no event handlers, forms, iframes, objects, <img>, video, or `url(...)` / `@import` / backslashes in CSS;\n"
    "- links are `#` only;\n"
    "- the only external resource is the Google Fonts stylesheet the existing files already use (draw icons and shapes with CSS "
    "or inline <svg>);\n"
    "- at most three files of 150 KB each.\n\n"
    "## What to show\n\n"
    "Show the realistic states that matter (for example default, empty, error) as separate frames in one file or as separate "
    "files. When the screen is responsive, also make a phone version as its own file (name it `...-mobile`) laid out in a frame "
    "390 px wide, beside the 1440 px wide desktop one. Do not give `body` or `html` a full-height background or "
    "`min-height:100vh`: colour a wrapper that fits its content, because empty page colour is kept in the preview. Do not modify, "
    "rename or delete any existing file.\n",
)


def for_run(cfg, agent: str, project: str, design_files: bool = False) -> list[Skill]:
    """The skills one run is given. agent is the [prompts] key of the run (roles.agent_key); project the project's name."""
    out = [DESIGN_MOCKUPS] if design_files else []
    for s in getattr(cfg, "skills", ()):
        if (not s.agents or agent in s.agents) and (not s.projects or project in s.projects):
            out.append(Skill(s.name, s.description, s.body))
    return out


def write(task_dir: Path, skills: list[Skill]) -> None:
    """Into the run's /task folder (mounted read-only into the sandbox)."""
    for s in skills:
        (task_dir / "skills" / s.name).mkdir(parents=True, exist_ok=True)
        (task_dir / "skills" / s.name / "SKILL.md").write_text(s.text())


def prompt_index(skills: list[Skill]) -> str:
    """Told to the agent after the built-in rules. Empty when there are no skills, so the prompt is then unchanged."""
    if not skills:
        return ""
    return ("\n\nSKILLS. These instruction files are available for this run. Read a skill's file before doing work its description "
            "covers. They add to the rules above and never override them.\n"
            + "".join(f"- {s.name} ({s.path()}): {s.description}\n" for s in skills).rstrip("\n"))
