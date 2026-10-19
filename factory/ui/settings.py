"""Settings the UI can edit. Every save is validated by loading the merged configuration before anything is written,
and only the overrides file is touched (the hand-written config.toml and its comments are never rewritten)."""
import copy
import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from ..classifier import KIND_ALIASES, KINDS
from ..config import (DEFAULT_ROLES, EFFORT_FLOOR, QUESTION_CATEGORIES, ChatCfg, NewProjectsCfg, CommentsCfg, IntakeCfg, QuestionsCfg, DesignLinksCfg, EvaluateCfg, HealthCfg, ScannerCfg, SplitCfg, SubtasksCfg, TicketReviewCfg, UpdatesCfg, VideosCfg, MAX_FALLBACKS, MODEL_RE, PROMPT_MAX, CiCfg, ConflictsCfg, PmCfg, PromptsCfg, ReviewCfg, RunnerCfg, ScreensCfg, WorkersCfg, deep_merge,
                      default_harnesses, load_raw, overrides_path, parse, prompt_problem)
from ..events import ALL_EVENTS
from ..schedules import parse_every
from ..tomlw import dumps

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
HOST_RE = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
EFFORTS = ("low", "medium", "high")
CHECK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


class SettingsError(Exception):
    def __init__(self, messages: list[str], fields: dict | None = None):
        super().__init__("; ".join(messages))
        self.messages = messages
        self.fields = fields or {}                    # form field name -> what is wrong with it, for a form that marks its fields


@dataclass(frozen=True)
class Field:
    key: str                 # dotted path in the config
    label: str
    kind: str                # int float bool text url labellist select checks repos repolist hosts hostlist paths kv models prompt wchecks every viewports
    help: str = ""
    choices: tuple = ()
    lo: float | None = None
    hi: float | None = None
    danger: str = ""         # non-empty: changing it requires an explicit confirmation
    group: str = ""          # a heading the page puts it under (fields of one group are listed together)
    adv: bool = False        # shown under Advanced, closed until opened

FALLBACK_HELP = ("Tried in order, on the same harness and effort, when the model hits a usage limit or an API error. "
                 "One model id per line, up to 3; empty for none.")


def _role_fields(harnesses: tuple = ("claude-code",)) -> list[Field]:
    out = []
    for r in DEFAULT_ROLES:
        out += [Field(f"roles.{r.name}.label", f"{r.name.title()}: trigger label", "text"),
                Field(f"roles.{r.name}.done_label", f"{r.name.title()}: done label", "text"),
                Field(f"roles.{r.name}.model", f"{r.name.title()}: model", "text", "sonnet, opus, haiku (or a full model id)"),
                Field(f"roles.{r.name}.effort", f"{r.name.title()}: effort", "select", choices=EFFORTS),
                Field(f"roles.{r.name}.harness", f"{r.name.title()}: agent harness", "select", choices=harnesses, adv=True),
                Field(f"roles.{r.name}.fallback_models", f"{r.name.title()}: fallback models", "models", FALLBACK_HELP, adv=True)]
        if r.name == "designer":
            out.append(Field("roles.designer.design_files", "Designer: write design mockup files", "bool",
                             "In a repo that has Claude Design canvases (*.dc.html), add static mockups on a draft PR and link them from the ticket."))
            out.append(Field("roles.designer.design_pr", "Designer: put mockups on a draft PR", "bool",
                             "Off keeps them in the factory only: the rendered images and the canvas files are on the ticket's pages here, "
                             "the build and review agents still get the images, and nothing is added to the repository."))
            out.append(Field("roles.designer.design_dir", "Designer: mockup folder", "text",
                             "Relative folder in the repo, for example docs/design. Avoid design/ if your repo git-ignores it."))
    return out + [Field("auto.label", "Auto-allocate label", "text", "A person applies it and the classifier picks the next stage."),
                  Field("split.notes_label", "Meeting notes label", "text",
                        "Put on a ticket by 'These are meeting notes': the analyst always considers splitting it into several tickets, "
                        "which a person approves."),
                  Field("split.architect", "Architect: propose splitting a large plan", "bool",
                        "On: when its plan is large, the architect proposes an ordered list of smaller tickets, each with its own scope and "
                        "acceptance checks. Nothing is created until a person approves it on the ticket page."),
                  Field("split.architect_min_areas", "Architect: split from this many areas", "int",
                        "A split is proposed only when the plan touches at least this many apps, modules or components, or reaches the "
                        "number of plan items below.", lo=2, hi=20),
                  Field("split.architect_min_items", "Architect: split from this many plan items", "int",
                        "Or when the plan has at least this many items.", lo=2, hi=50),
                  Field("auto.chain", "Auto continues to the next stage", "bool",
                        "On (default): after a stage, if nothing needs a person, the next stage starts by itself. Off: one stage per label."),
                  Field("auto.confirm_stages", "Ask before auto runs a stage", "bool",
                        "Off (default): analysis, design and architecture run straight away, since they only write a document. A person is "
                        "always asked before a build the classifier is unsure about. On: ask for stages too.")]


def _routing_fields(harnesses: tuple = ("claude-code",)) -> list[Field]:
    out = []
    for tier in ("low", "medium", "high"):
        out += [Field(f"routing.{tier}.harness", f"{tier.title()} tier: agent harness", "select", choices=harnesses, adv=True),
                Field(f"routing.{tier}.model", f"{tier.title()} tier: model", "text"),
                Field(f"routing.{tier}.effort", f"{tier.title()} tier: effort", "select", choices=EFFORTS),
                Field(f"routing.{tier}.fallback_models", f"{tier.title()} tier: fallback models", "models", FALLBACK_HELP, adv=True)]
    return out + [Field(f"effort_floor.{step}", label, "select", help, choices=("none",) + EFFORTS, adv=adv) for step, label, help, adv in FLOOR_FIELDS]


FLOOR_HELP = "Runs of this step get at least this effort; a higher one is kept. none: no minimum."
FLOOR_FIELDS = (("implement", "Minimum effort: build", "Every build runs with at least this effort, whatever the ticket's size or the "
                 "classifier chose. A higher effort is kept; none: no minimum.", False),
                ("ci-fix", "Minimum effort: CI fixes", FLOOR_HELP, True), ("conflicts", "Minimum effort: merge conflicts", FLOOR_HELP, True),
                ("analyze", "Minimum effort: analyst", FLOOR_HELP, True), ("design", "Minimum effort: designer", FLOOR_HELP, True),
                ("architect", "Minimum effort: architect", FLOOR_HELP, True), ("review", "Minimum effort: code review", FLOOR_HELP, True))


PROMPT_DANGER = "Agents follow this text on every run, with write access to their sandbox workspace."
PROMPT_LABELS = {"all": "All agents", "analyst": "Analyst", "designer": "Designer", "architect": "Architect", "reviewer": "Code reviewer",
                 "implementer": "Implementer (builds)", "ci_fix": "CI fix runs", "conflicts": "Merge-conflict runs"}


def _prompt_fields() -> list[Field]:
    return [Field(f"prompts.{k}", label, "prompt",
                  "Added to every agent, before its own text below." if k == "all" else
                  "Put before this agent's built-in instructions, which always apply and win if they conflict.", danger=PROMPT_DANGER)
            for k, label in PROMPT_LABELS.items()]


SECTIONS: dict[str, tuple[str, list[Field]]] = {
    "general": ("General", [
        Field("general.poll_seconds", "Check for work every (seconds)", "int", "How often the factory looks for new work.", lo=5, hi=3600, group="Mode"),
        Field("general.dry_run", "Dry run", "bool", "On: decisions are only logged, nothing is run or written. Turn off to go live.",
              danger="Going live means the factory will start agents and open PRs for labeled issues.", group="Mode"),
        Field("general.confidence_threshold", "Confidence threshold", "float", "Below this the classifier's call goes to a person.", lo=0, hi=1, group="Mode"),
        Field("github.trigger_label", "Build label", "text", "Applying it makes the factory build the ticket.", group="Labels"),
        Field("github.trusted_permissions", "Who may apply labels", "checks", "A label counts only if its author has one of these repository roles.",
              choices=("admin", "maintain", "write", "triage"), group="Labels"),
        Field("github.repos", "Standalone repositories", "repolist", "Repositories the factory works on one at a time. Repositories of a project (see Projects) are added automatically.",
              group="Where tickets come from"),
        Field("github.issues_enabled", "Work from GitHub issues", "bool",
              "Off: GitHub issues are not read or acted on (no polling, approvals, schedules or sub-issues; Import still works). Running jobs finish. "
              "Pull requests and CI still use GitHub. Turn on local tickets or nothing has tickets to work on.", group="Where tickets come from"),
        Field("local.enabled", "Keep tickets in the factory", "bool",
              "On: tickets can live in the factory's own database (L-1, L-2 ...) instead of GitHub issues. New ticket offers both, and Import "
              "moves GitHub issues across. Pull requests still go to GitHub.", group="Where tickets come from"),
        Field("local.auto_import", "Import GitHub issues automatically", "bool",
              "Only with GitHub issues off and local tickets on. On a slow schedule, open GitHub issues whose author has one of the roles in "
              "Who may apply labels are copied here with their labels; other authors' issues are skipped. The first runs move the "
              "whole open backlog, 25 at a time.", group="Where tickets come from"),
        Field("subtasks.enabled", "Mirror pipeline steps as GitHub sub-issues", "bool",
              "Each stage of a ticket also shows as a sub-issue on GitHub. Off by default: it adds writes and notifications.", group="Where tickets come from"),
        Field("local.max_attachment_mb", "Largest attachment (MB)", "int", "Files attached to a local ticket.", lo=1, hi=50, adv=True),
        Field("local.max_attachments", "Attachments per ticket", "int", "Files attached to one local ticket.", lo=1, hi=20, adv=True),
        Field("local.max_attachments_total_mb", "All attachments of a ticket (MB)", "int", "Together, for one local ticket.", lo=1, hi=200, adv=True),
        Field("board.done_days", "Keep done tickets on the board (days)", "int",
              "Done cards leave the Tickets board this many days after their last change. The list still shows every ticket.", lo=1, hi=365, adv=True),
        Field("github.poll_seconds", "Read GitHub every (seconds)", "int",
              "Local tickets are handled at every poll; GitHub issues can be read less often to spare API calls. "
              "The same as the poll interval reads GitHub at every poll.", lo=5, hi=86400, adv=True),
        Field("local.auto_import_seconds", "Import GitHub issues every (seconds)", "int",
              "How often the automatic import reads GitHub (default 3600).", lo=600, hi=86400, adv=True),
    ]),
    "intake": ("Thin tickets", [
        Field("intake.enabled", "Ask for more before starting on a thin ticket", "bool",
              "On: when a ticket gets a factory label and its title and description together are shorter than the length below, no "
              "agent starts. The "
              "factory comments once asking for the goal, the scope, how the result is checked and any constraints, and adds "
              "factory:needs-answers. It starts once the description is long enough, or a person with write access comments "
              "'go ahead'. Tickets the factory already worked on, and tickets it made from an approved split, are never held."),
        Field("intake.min_chars", "Shortest description it starts on (characters)", "int",
              "Characters of text in the title and description together, not counting markup, links or template comments.", lo=1, hi=2000),
    ]),
    "routing": ("Models by ticket size", _routing_fields()),
    "roles": ("Stage agents", _role_fields()),     # the harness choices are filled in by fields_for()
    "classifier": ("Classifier", [
        Field("classifier.backend", "Classifier", "select", "Jev reads the whole ticket; labels uses only your labels.", choices=("rules", "jev")),
        Field("classifier.model", "Jev model", "text", "typesafe/jev-1.13 pins a version; ~typesafe/jev-latest follows the newest."),
        Field("classifier.kind_aliases", "Label → kind aliases", "kv",
              "One per line, label=kind, where kind is bug, feature, docs, chore or question. Lets existing labels (like GitHub's enhancement) count.", adv=True),
    ]),
    "runner": ("Sandbox & limits", [
        Field("runner.max_parallel", "Agents at once", "int", "Sandboxes that may run at the same time. Each may use the memory and CPUs below.", lo=1, hi=8),
        Field("runner.timeout_seconds", "Task timeout (seconds)", "int", lo=60, hi=14400),
        Field("runner.max_turns", "Max agent turns", "int", lo=1, hi=200, adv=True),
        Field("runner.rate_limit_backoff_seconds", "Pause after a rate limit (seconds)", "int", lo=60, hi=86400, adv=True),
        Field("runner.memory", "Sandbox memory", "text", "For example 3g or 512m."),
        Field("runner.cpus", "Sandbox CPUs", "text", "For example 2 or 1.5."),
        Field("runner.allow_hosts", "Hosts the sandbox may reach", "hosts",
              "One per line. This is the ONLY way out of the sandbox. Keep it to the model API unless tasks truly need a registry.",
              danger="Widening the sandbox's reachable hosts weakens its isolation.", adv=True),
        Field("runner.render_previews", "Render mockup previews", "bool",
              "Turn the designer's canvases into PNGs in a sealed container and add them to the draft PR. Turns itself off if the render image is missing."),
        Field("runner.thinking_tokens.low", "Thinking budget at low effort (tokens)", "int", lo=0, hi=200000, adv=True),
        Field("runner.thinking_tokens.medium", "Thinking budget at medium effort (tokens)", "int", lo=0, hi=200000, adv=True),
        Field("runner.thinking_tokens.high", "Thinking budget at high effort (tokens)", "int", lo=0, hi=200000, adv=True),
        Field("runner.max_patch_bytes", "Largest patch an agent may produce (bytes)", "int", "A bigger change fails instead of being pushed.", lo=10000, hi=50_000_000, adv=True),
        Field("runner.max_files", "Most files one patch may change", "int", lo=1, hi=5000, adv=True),
        Field("runner.render_timeout_seconds", "Preview render timeout (seconds)", "int", lo=10, hi=3600, adv=True),
        Field("runner.engine", "Container engine", "select", "What runs the sandboxes on this host.", choices=("podman", "docker"), adv=True,
              danger="A wrong engine stops every run until it is changed back."),
        Field("runner.image", "Sandbox image", "text", "The container image agents run in.", adv=True, danger="A wrong image stops every run until it is changed back."),
        Field("runner.render_image", "Preview render image", "text", adv=True),
    ]),
    "review": ("Code review", [
        Field("review.enabled", "Enable the code reviewer", "bool", "An independent agent reviews the factory's PRs and comments. It never approves or changes code."),
        Field("review.auto", "Review every PR the factory opens", "bool", "PRs open as drafts until the review (see the next switch). Off: PRs open ready; only the review label starts a review."),
        Field("review.gate", "Keep a pull request a draft until the review says Looks good", "bool",
              "On: Needs changes or Blocking issues keeps the PRs drafts and runs the fix rounds below. Off: the PRs are marked ready "
              "after any review, whatever it says, and no fix rounds run."),
        Field("review.fix_rounds", "Fix rounds after Needs changes or Blocking issues", "int",
              "Only with the switch above on. The build fixes the review's findings on the same branch and the review runs again, at most this many times per PR. Then the "
              "PRs stay drafts, the ticket is labelled factory:review-held and you are told. 0 = no fix rounds: a person decides at once.", lo=0, hi=5),
        Field("review.label", "Review label", "text", "Apply it to a ticket to (re-)review its open factory PRs."),
        Field("review.done_label", "Reviewed label", "text"),
        Field("review.model", "Reviewer model", "text", "Use a different harness and model family from the builder for a genuinely independent opinion."),
        Field("review.effort", "Reviewer effort", "select", choices=EFFORTS),
        Field("review.harness", "Reviewer agent harness", "select", choices=("claude-code",), adv=True),
        Field("review.fallback_models", "Reviewer: fallback models", "models", FALLBACK_HELP, adv=True),
    ]),
    "pm": ("Project manager", [
        Field("pm.enabled", "Enable the project manager", "bool",
              "On a sweep an agent ranks each repository's factory tickets with priority labels and records blockers; while it is on, "
              "a build waits for its open blockers. A person's priority label always wins. It never starts or stops work."),
        Field("pm.interval_minutes", "Minutes between sweeps", "int", "Only when the tickets changed; each sweep is one run per repository.", lo=10, hi=10080),
        Field("pm.max_tickets", "Tickets per sweep", "int", lo=1, hi=200, adv=True),
        Field("pm.body_chars", "Characters of each ticket body", "int", lo=200, hi=10000, adv=True),
        Field("pm.unblock_label", "Unblock label", "text", "A person with write access applies it to make the factory ignore the project manager's blockers on a ticket."),
        Field("pm.model", "Project manager model", "text"),
        Field("pm.effort", "Project manager effort", "select", choices=EFFORTS),
        Field("pm.harness", "Project manager agent harness", "select", choices=("claude-code",), adv=True),
    ]),
    "mockups": ("Design mockups", [
        Field("mockups.mode", "When a design stage left no mockup", "select",
              "block: the build does not start. warn: build anyway and say so on the ticket. off: never check. Tickets whose design said "
              "there are no screens are never held.", choices=("block", "warn", "off")),
        Field("mockups.bypass_label", "Bypass label", "text", "Put it on one ticket to build without mockups."),
        Field("mockups.require_approval", "Mockups need a person's approval", "bool",
              "When on (and mode is block), a build waits until the approval label is on the ticket or the design PR is merged."),
        Field("mockups.approve_label", "Approval label", "text"),
        Field("mockups.request_label", "Screens requested label", "text",
              "Put on a ticket by 'Make screens for me to review': design runs before the build, and the build waits for approval."),
    ]),
    "ci": ("CI feedback", [
        Field("ci.enabled", "Watch CI on factory PRs", "bool"),
        Field("ci.fix_rounds", "Agent fix rounds after a failure", "int", "0 = report only.", lo=0, hi=5),
        Field("ci.wait_for_checks_minutes", "Wait for checks to appear (minutes)", "int", lo=1, hi=120, adv=True),
        Field("ci.timeout_minutes", "Give up on pending checks after (minutes)", "int", lo=5, hi=720, adv=True),
        Field("ci.log_tail_chars", "Failing-log excerpt size", "int", lo=1000, hi=20000, adv=True),
        Field("ci.queued_warn_minutes", "Warn when CI is queued for (minutes)", "int", "Self-hosted runners may be offline.", lo=1, hi=1440, adv=True),
        Field("ci.manual_wait_hours", "Keep watching for hand-started CI (hours)", "int", "How long to wait for CI that a person starts by hand.", lo=1, hi=168, adv=True),
    ]),
    "conflicts": ("Merge conflicts", [
        Field("conflicts.enabled", "Resolve merge conflicts on factory PRs", "bool",
              "Checks every PR the factory opened. The base branch is merged into the PR branch (never rebased or force-pushed); an agent edits only the conflicted files."),
        Field("conflicts.auto", "Apply the label automatically", "bool", "Off: the factory only reports a conflict and a person applies the label."),
        Field("conflicts.label", "Conflicts label", "text", "Apply it to a ticket to resolve the conflicts in its open factory PRs."),
        Field("conflicts.max_attempts", "Resolution attempts per PR", "int", "Then a person is asked.", lo=0, hi=50),
    ]),
    "workers": ("Workers", [
        Field("workers.enabled", "Verify patches on workers", "bool",
              "On: for every repo with a check below, a patch must pass on a worker before anything is pushed. The worker API process and a worker must be running.",
              danger="Builds of repos with checks will wait for a worker, and fail without one."),
        Field("workers.mode", "When a required check fails or cannot run", "select",
              "block: the run fails and nothing is pushed. warn: the PR is opened anyway, with the failure noted on it.", choices=("block", "warn"),
              danger="Warn lets a change that failed its checks be pushed as a PR."),
        Field("workers.fix_rounds", "Agent fix rounds after a failed check", "int",
              "When a required check really FAILS (not when no worker could run it), the agent tries again with the log. 0 = report only.", lo=0, hi=3),
        Field("workers.claim_wait_seconds", "Give up if no worker claims a job (seconds)", "int", lo=10, hi=86400, adv=True),
        Field("workers.max_wait_seconds", "Give up on a check after (seconds)", "int", lo=30, hi=86400, adv=True),
        Field("workers.lease_seconds", "A worker must report in every (seconds)", "int", lo=10, hi=3600, adv=True),
        Field("workers.max_attempts", "Claims per job", "int", "How often a job may be picked up again after a worker lost it.", lo=1, hi=5, adv=True),
        Field("workers.checks", "Checks", "wchecks",
              "One per line: owner/name recipe platform, with an optional fourth word, advisory (a failure is noted on the PR but never blocks). "
              "The recipe is a name defined on the worker; platform is macos, linux ... or any.",
              danger="Removing or loosening a check lets changes through that would have been verified."),
    ]),
    "screens": ("Screens", [
        Field("screens.board_every", "Refresh the Screens board every", "every",
              "6h, 1d or 1w. Empty: only when someone presses Refresh now."),
        Field("screens.viewports", "Viewports", "viewports",
              "One per line: name widthxheight, for example desktop 1440x900. Every screen is shot at each one unless it picks some."),
        Field("screens.baseline_dir", "Baseline folder", "text", "Where each repository keeps its approved shots, as <name>-<viewport>.png."),
        Field("screens.threshold", "Pixel tolerance", "float", "Colour distance (0 to 1) above which a pixel counts as different.", lo=0, hi=1, adv=True),
        Field("screens.max_diff_ratio", "Share of pixels allowed to differ", "float", "0.001 is 0.1 %.", lo=0, hi=1, adv=True),
        Field("screens.timeout_seconds", "Shoot timeout (seconds)", "int", lo=10, hi=3600, adv=True),
        Field("screens.image", "Screenshot image", "text", "The container image screens are shot in.", adv=True),
        Field("screens.label", "Screens-changed label", "text", "Put on a draft PR whose patch changes baseline images."),
    ]),
    "prompts": ("Agent instructions", _prompt_fields()),
    "comments": ("Comment triage", [
        Field("comments.enabled", "Triage new comments", "bool",
              "A read-only agent reads each new comment a person with write access adds to a ticket the factory worked on, and decides: nothing to do, "
              "needs a person, send it back to a stage, or open a follow-up. The last two wait for a person to confirm."),
        Field("comments.max_per_day", "Triage runs per ticket per day", "int", "Later comments wait.", lo=1, hi=100),
        Field("comments.model", "Triage model", "text"),
        Field("comments.effort", "Triage effort", "select", choices=EFFORTS),
        Field("comments.harness", "Triage agent harness", "select", choices=("claude-code",), adv=True),
    ]),
    "questions": ("Second opinions", [
        Field("questions.auto_answer", "Answer questions when the judges agree", "bool",
              "Before a stage's question waits for you, independent judges answer it. When they agree with the stage agent's recommendation "
              "the factory records that answer and moves on. You can change it afterwards, and the stage runs again with yours."),
        Field("questions.min_confidence", "How sure each judge must be", "float", "From 0.5 to 1.", lo=0.5, hi=1),
        Field("questions.require", "Judges that must agree", "select",
              "all: every judge agrees. any: one agrees and none disagrees.", choices=("all", "any")),
        Field("questions.max_auto_per_ticket", "Automatic answers per ticket", "int", "More than this and you answer them all.", lo=1, hi=20),
        Field("questions.always_ask", "Always ask me about", "checks",
              "Questions that mention these never go to the judges.", choices=QUESTION_CATEGORIES),
        Field("questions.jev", "Blind judge (Jev)", "bool",
              "Sees the ticket, the document and the options but not the recommendation. Needs the classifier's OpenRouter key."),
        Field("questions.agent", "Judge that reads the code", "bool",
              "An agent argues against the recommendation and looks for evidence in the repositories. It is a run."),
        Field("questions.use_past_answers", "Show the judges my earlier answers", "bool", "Your answers to earlier questions in the same repository."),
        Field("questions.model", "Code judge model", "text", "sonnet, opus, haiku (or a full model id). Best a different one from the stage agents."),
        Field("questions.effort", "Code judge effort", "select", choices=EFFORTS),
        Field("questions.harness", "Code judge harness", "select", choices=("claude-code",), adv=True),
    ]),
    "ticket_review": ("Ticket review", [
        Field("ticket_review.enabled", "Allow ticket reviews", "bool",
              "On request, an agent looks at every open ticket of a repository and proposes which are already built or duplicates. A person picks what to close."),
        Field("ticket_review.max_tickets", "Tickets per review", "int", lo=1, hi=500),
        Field("ticket_review.body_chars", "Characters of each ticket body", "int", lo=100, hi=20000, adv=True),
        Field("ticket_review.model", "Review model", "text"),
        Field("ticket_review.effort", "Review effort", "select", choices=EFFORTS),
        Field("ticket_review.harness", "Review agent harness", "select", choices=("claude-code",), adv=True),
    ]),
    "evaluate": ("Evaluation", [
        Field("evaluate.enabled", "Evaluate finished tickets", "bool",
              "Once a ticket's work merged, was closed or failed, an agent with a clean context scores how each step went and suggests "
              "what to change. One ticket at a time, only while the factory has nothing else to do. Each evaluation is a run. See the Evaluate page."),
        Field("evaluate.max_usage_percent", "Hold off above this plan usage (%)", "int",
              "No evaluation starts while the Claude plan's session or week is at least this used.", lo=1, hi=100),
        Field("evaluate.settle_minutes", "Minutes to wait after a ticket's last run", "int", "In case more work follows.", lo=0, hi=100000),
        Field("evaluate.backfill_days", "Evaluate tickets finished in the last (days)", "int",
              "Older tickets are evaluated only when you press Evaluate now.", lo=1, hi=3650),
        Field("evaluate.low_score", "A low score is this or lower", "int",
              "From 1 to 5. A low score needs a suggestion and is listed under What to improve.", lo=1, hi=4),
        Field("evaluate.model", "Evaluator model", "text", "sonnet, opus, haiku (or a full model id)."),
        Field("evaluate.effort", "Evaluator effort", "select", choices=EFFORTS),
        Field("evaluate.body_chars", "Characters of the ticket body", "int", lo=100, hi=100000, adv=True),
        Field("evaluate.doc_chars", "Characters of each run's document", "int", lo=500, hi=100000, adv=True),
        Field("evaluate.max_turns", "Turns per evaluation", "int", lo=1, hi=50, adv=True),
        Field("evaluate.timeout_seconds", "Seconds per evaluation", "int", lo=60, hi=7200, adv=True),
        Field("evaluate.harness", "Evaluator harness", "select", choices=("claude-code",), adv=True),
    ]),
    "design_links": ("Design links", [
        Field("design_links.enabled", "Fetch linked design exports", "bool",
              "Design exports (single HTML files) linked in a ticket are fetched and shown to agents as intent. The UI shows them in a sandboxed tab without scripts."),
        Field("design_links.hosts", "Hosts links may come from", "hostlist", "One per line, plain host names, no wildcards. Needed when this is on.",
              danger="Every listed host can put content in front of the agents."),
        Field("design_links.max_links", "Links per ticket", "int", lo=1, hi=5),
        Field("design_links.max_bytes", "Largest export (bytes)", "int", lo=1, hi=5_000_000, adv=True),
        Field("design_links.ttl_hours", "Fetch again after (hours)", "int", lo=1, hi=720, adv=True),
    ]),
    "scanner": ("Scan limits", [
        Field("scanner.max_tickets", "Tickets per scan run", "int", "The rest is considered again next run.", lo=1, hi=100),
        Field("scanner.max_findings_per_ticket", "Findings per ticket", "int", lo=1, hi=1000),
        Field("scanner.max_lines", "A file is long from (lines)", "int", "Used by the long-files check.", lo=50, hi=100000),
        Field("scanner.labels", "Labels on scan tickets", "labellist",
              "Comma separated. Empty: the analyst label, so scan tickets are analysed, not built."),
        Field("scanner.recipe", "Worker recipe", "text", "The recipe on the worker that runs scans.", adv=True),
        Field("scanner.platform", "Worker platform", "text", "any, or a platform such as linux or macos.", adv=True),
    ]),
    "chat": ("Ticket chat", [
        Field("chat.enabled", "Enable ticket chat", "bool",
              "Quick replies about a ticket from a short read-only run, with no repository clone and no GitHub token. Each reply is a model run."),
        Field("chat.model", "Chat model", "text"),
        Field("chat.effort", "Chat effort", "select", choices=EFFORTS),
        Field("chat.max_per_hour", "Messages per ticket per hour", "int", lo=1, hi=1000),
        Field("chat.max_parallel", "Chats at once", "int", "Their own lane, on top of Agents at once.", lo=1, hi=20),
        Field("chat.factory_enabled", "Enable factory chat", "bool",
              "Ask about every ticket and the settings on the Chat page (/chat, in the main menu). Read-only: it changes nothing. Uses the chat model "
              "and harness above; each reply is a model run.", group="Factory chat"),
        Field("chat.factory_max_per_hour", "Factory chat messages per hour", "int", "All admins together.", lo=1, hi=1000, group="Factory chat"),
        Field("chat.factory_code", "Let the factory chat read the code", "bool",
              "A read-only copy of each repository's default branch, refreshed at most every 15 minutes, for the factory chat and for the "
              "videos it proposes. The runs still have no network and no GitHub token.", group="Factory chat"),
        Field("chat.factory_max_turns", "Factory chat: max turns per reply", "int", "Reading code takes more turns than a ticket chat. "
              "Also used for writing a video's narration.", lo=1, hi=1000, group="Factory chat", adv=True),
        Field("chat.factory_timeout_seconds", "Factory chat: reply timeout (seconds)", "int", lo=10, hi=10000, group="Factory chat", adv=True),
        Field("chat.harness", "Chat agent harness", "select", choices=("claude-code",), adv=True),
        Field("chat.max_turns", "Max turns per reply", "int", lo=1, hi=1000, adv=True),
        Field("chat.timeout_seconds", "Reply timeout (seconds)", "int", lo=10, hi=10000, adv=True),
        Field("chat.check_seconds", "Look for new messages every (seconds)", "int", lo=1, hi=1000, adv=True),
        Field("chat.context_turns", "Chat turns later stages read", "int", "The newest turns that stage prompts carry.", lo=1, hi=1000, adv=True),
    ]),
    "videos": ("Review videos", [
        Field("videos.enabled", "Enable review videos", "bool",
              "A ticket's page then offers a short narrated walkthrough of each of its pull requests. Needs the video image (built below)."),
        Field("videos.voice", "Default voice", "select",
              "Piper runs offline on this machine. ElevenLabs needs its key (below) and a voice id; it receives the narration text. "
              "A ticket can choose the other.", choices=("piper", "elevenlabs"), group="Voice"),
        Field("videos.elevenlabs_voice_id", "ElevenLabs voice id", "text",
              "8 to 40 letters and digits, from your ElevenLabs voice library. Used unless a ticket names another.", group="Voice"),
        Field("videos.elevenlabs_model", "ElevenLabs model", "text", "For example eleven_multilingual_v2.", group="Voice", adv=True),
        Field("videos.music", "Background music", "select",
              "A quiet instrumental track under the voice, made for each video by ElevenLabs Music (needs the ElevenLabs key, whichever "
              "voice speaks). Calm, bright or focused. If it cannot be made, the video is made without it.",
              choices=("off", "calm", "bright", "focused"), group="Voice"),
        Field("videos.max_per_day", "Videos per day", "int", "Videos started in any 24 hours, Make again included.", lo=1, hi=1000, group="Limits"),
        Field("videos.max_seconds", "Longest video (seconds)", "int", "A longer video is not kept.", lo=10, hi=600, group="Limits"),
        Field("videos.max_mb", "Largest video (MB)", "int", "A larger video is not kept.", lo=1, hi=500, group="Limits"),
        Field("videos.retention_days", "Keep videos (days)", "int", "Video files are removed this long after they were made.", lo=1, hi=3650, group="Limits"),
        Field("videos.timeout_seconds", "Time to make one video (seconds)", "int", lo=60, hi=7200, adv=True),
        Field("videos.ui_url", "Admin UI address for links", "url",
              "Used in the links posted on pull requests and tickets. Empty: the Telegram UI address.", adv=True),
        Field("videos.image", "Video image", "text",
              "The container image the videos are made in. Only the default one can be built from this page.", adv=True),
    ]),
    "new_projects": ("New project interviews", [
        Field("new_projects.enabled", "Enable new-project interviews", "bool",
              "An agent interviews you about a new project and recommends a pattern. Each reply is a read-only model run with no repository."),
        Field("new_projects.model", "Interviewer model", "text"),
        Field("new_projects.effort", "Interviewer effort", "select", choices=EFFORTS),
        Field("new_projects.max_rounds", "Rounds before a plan", "int", "Your replies (the brief counts) before the interviewer must give its plan.", lo=1, hi=20),
        Field("new_projects.max_parallel", "Interviews at once", "int", "Their own lane, on top of Agents at once.", lo=1, hi=20),
        Field("new_projects.harness", "Interviewer agent harness", "select", choices=("claude-code",), adv=True),
        Field("new_projects.max_turns", "Max turns per reply", "int", lo=1, hi=1000, adv=True),
        Field("new_projects.timeout_seconds", "Reply timeout (seconds)", "int", lo=10, hi=10000, adv=True),
        Field("new_projects.check_seconds", "Look for new messages every (seconds)", "int", lo=1, hi=1000, adv=True),
    ]),
    "health": ("Health alerts", [
        Field("health.enabled", "Health alerts", "bool", "Alerts on Telegram when something that would stop the factory goes wrong."),
        Field("health.disk_warn_percent", "Warn when free disk is below (%)", "int", "Free space is checked in % and in GB; the lower threshold trips first.",
              lo=1, hi=100, group="Disk and memory"),
        Field("health.disk_crit_percent", "Critical when free disk is below (%)", "int", lo=1, hi=100, group="Disk and memory"),
        Field("health.disk_warn_gb", "Warn when free disk is below (GB)", "int", lo=1, hi=100000, group="Disk and memory"),
        Field("health.disk_crit_gb", "Critical when free disk is below (GB)", "int", lo=1, hi=100000, group="Disk and memory"),
        Field("health.mem_warn_mb", "Warn when free memory is below (MB)", "int", lo=1, hi=100000, group="Disk and memory"),
        Field("health.mem_crit_mb", "Critical when free memory is below (MB)", "int", lo=1, hi=100000, group="Disk and memory"),
        Field("health.stale_poll_minutes", "Alert when the factory has not checked for work in (minutes)", "int", "It is hung or down.", lo=1, hi=100000, group="The factory"),
        Field("health.failed_runs_warn", "Alert after this many failed runs in a row", "int", lo=1, hi=100000, group="The factory"),
        Field("health.github_rate_warn", "Alert when GitHub API calls left this hour drop below", "int", lo=1, hi=100000, group="The factory"),
        Field("health.repeat_hours", "Remind every (hours) while a problem lasts", "int", lo=1, hi=100000, group="The factory"),
        Field("health.heartbeat_url", "Heartbeat address", "url",
              "Optional dead-man's switch such as healthchecks.io, called each check while nothing is critical. Must start with https://.", adv=True),
        Field("health.extra_disk_paths", "More folders to watch", "paths",
              "One absolute path per line. State, work and container storage are watched already.", adv=True),
    ]),
    "update_check": ("Update checks", [
        Field("updates.check", "Check for new releases", "bool", "One cached read of GitHub's public releases list every few hours; a banner says when one is out."),
        Field("updates.repo", "Release repository", "text", "owner/name of the repository releases come from.", adv=True),
    ]),
}
OPTIONAL_TEXT = frozenset({"videos.elevenlabs_voice_id"})      # text settings that may be left empty
VIEWPORT_LINE = re.compile(r"([a-z0-9][a-z0-9-]{0,60})\s+(\d{1,5})\s*[x×]\s*(\d{1,5})")


def harness_choices(eff: dict) -> tuple:
    """Names of the enabled harnesses, for the selectors on the routing and roles pages."""
    try:
        return tuple(n for n, h in parse(eff).harnesses.items() if h.enabled) or ("claude-code",)
    except (ValueError, KeyError, TypeError):
        return ("claude-code",)


def fields_for(section: str, eff: dict) -> list[Field]:
    if section == "routing":
        return _routing_fields(harness_choices(eff))
    if section == "roles":
        return _role_fields(harness_choices(eff))
    fields = SECTIONS[section][1]
    if any(f.kind == "select" and f.key.endswith(".harness") for f in fields):
        hc = harness_choices(eff)
        fields = [replace(f, choices=hc) if f.kind == "select" and f.key.endswith(".harness") else f for f in fields]
    return fields


# ---------------------------------------------------------------- dotted-path helpers
def get_in(d: dict, dotted: str, default=None):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_in(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def del_in(d: dict, dotted: str) -> None:
    parts = dotted.split(".")
    chain = [d]
    for part in parts[:-1]:
        if part not in chain[-1]:
            return
        chain.append(chain[-1][part])
    chain[-1].pop(parts[-1], None)
    for i in range(len(parts) - 1, 0, -1):               # prune tables left empty
        if not chain[i]:
            chain[i - 1].pop(parts[i - 1], None)


DATACLASS_DEFAULTS = {"chat": ChatCfg, "new_projects": NewProjectsCfg, "comments": CommentsCfg, "questions": QuestionsCfg, "ticket_review": TicketReviewCfg, "evaluate": EvaluateCfg, "health": HealthCfg, "design_links": DesignLinksCfg,
                      "updates": UpdatesCfg, "subtasks": SubtasksCfg, "scanner": ScannerCfg, "split": SplitCfg, "videos": VideosCfg, "intake": IntakeCfg}


def default_for(key: str):
    section, _, name = key.partition(".")
    if section == "effort_floor":
        return EFFORT_FLOOR.get(name, "none")
    if section == "harnesses":
        hname, _, field = name.partition(".")
        h = default_harnesses(RunnerCfg()).get(hname)
        v = getattr(h, field, None) if h else None
        return list(v) if isinstance(v, tuple) else v
    if section == "runner" and name.startswith("thinking_tokens."):
        return RunnerCfg().thinking_tokens.get(name.partition(".")[2])
    if section in DATACLASS_DEFAULTS:
        v = getattr(DATACLASS_DEFAULTS[section](), name, None)
        return list(v) if isinstance(v, tuple) else v
    if section == "runner":
        v = getattr(RunnerCfg(), name, None)
        return list(v) if isinstance(v, tuple) else v
    if section == "ci":
        return getattr(CiCfg(), name, None)
    if section == "review":
        v = getattr(ReviewCfg(), name, None)
        return list(v) if isinstance(v, tuple) else v
    if section == "conflicts":
        return getattr(ConflictsCfg(), name, None)
    if section == "workers":
        v = getattr(WorkersCfg(), name, None)
        return [] if name == "checks" else v
    if section == "prompts":
        return getattr(PromptsCfg(), name, None)
    if section == "pm":
        return getattr(PmCfg(), name, None)
    if section == "screens":
        v = getattr(ScreensCfg(), name, None)
        return [{"name": x.name, "width": x.width, "height": x.height} for x in v] if name == "viewports" else v
    if section == "roles":
        role, _, field = name.partition(".")
        v = next((getattr(r, field) for r in DEFAULT_ROLES if r.name == role), None)
        return list(v) if isinstance(v, tuple) else v
    return {"auto.label": "factory:auto", "routing.low.fallback_models": [], "routing.medium.fallback_models": [], "routing.high.fallback_models": [],
            "classifier.backend": "rules", "classifier.model": "typesafe/jev-1.13",
            "classifier.kind_aliases": dict(KIND_ALIASES), "telegram.verbosity": "normal", "slack.verbosity": "normal", "local.enabled": False, "local.auto_import": False, "local.auto_import_seconds": 3600, "local.max_attachment_mb": 5, "local.max_attachments": 5, "local.max_attachments_total_mb": 20, "board.done_days": 7, "github.issues_enabled": True, "auto.confirm_stages": False, "auto.chain": True}.get(key)


def canon(key: str, v):
    """Viewports are a table in a hand-written config.toml and a list in the overrides; compare and show them as the list."""
    if key == "screens.viewports" and isinstance(v, dict):
        return [{"name": k, "width": x.get("width"), "height": x.get("height")} for k, x in v.items()]
    return v


def fallback(raw: dict, key: str):
    """A setting whose default is another setting: GitHub is read at every poll unless told otherwise."""
    return get_in(raw, "general.poll_seconds") if key == "github.poll_seconds" else None


def effective(raw: dict, key: str):
    v = canon(key, get_in(raw, key))
    if v is None:
        v = fallback(raw, key)
    return default_for(key) if v is None else v


# ---------------------------------------------------------------- reading and writing the files
def base_raw(cfg_path: str) -> dict:
    return tomllib.loads(Path(cfg_path).read_text())


def overrides_raw(cfg_path: str) -> dict:
    p = overrides_path(cfg_path)
    return tomllib.loads(p.read_text()) if p.exists() else {}


def write_overrides(cfg_path: str, data: dict) -> None:
    p = overrides_path(cfg_path)
    if p.exists():
        p.with_suffix(".toml.bak").write_text(p.read_text())     # one-step undo
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Written by the Shikumi UI. Safe to delete a key to fall back to config.toml.\n" + dumps(data))
    os.replace(tmp, p)


def request_restart(state_dir: Path) -> None:
    """The orchestrator re-executes itself at its next idle moment, applying every setting and credential."""
    (Path(state_dir) / "RESTART").write_text("")


# ---------------------------------------------------------------- parsing form values
class Form(dict):
    """Form values: the first value of each field, with getall() for repeated ones (checkboxes)."""
    lists: dict

    def getall(self, key: str) -> list[str]:
        return self.lists.get(key, [])


def _lines(text: str) -> list[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def parse_value(f: Field, form: Form):
    raw = form.get(f.key, "")
    try:
        if f.kind == "bool":
            return raw == "1"
        if f.kind == "int":
            v = int(raw.strip())
            if (f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi):
                raise ValueError
            return v
        if f.kind == "float":
            v = float(raw.strip())
            if (f.lo is not None and v < f.lo) or (f.hi is not None and v > f.hi):
                raise ValueError
            return v
        if f.kind == "text":
            v = raw.strip()
            if not v and f.key in OPTIONAL_TEXT:
                return ""
            if not v or len(v) > 200 or "\n" in v or "\r" in v:
                raise ValueError
            if f.key == "runner.memory" and not re.fullmatch(r"\d+[kKmMgG]", v):
                raise ValueError
            if f.key == "runner.cpus" and not re.fullmatch(r"\d+(\.\d+)?", v):
                raise ValueError
            return v
        if f.kind == "prompt":
            v = raw.replace("\r\n", "\n")       # may be empty; kept as typed so an unchanged value from config.toml compares equal
            if prompt_problem(v):
                raise ValueError
            return v
        if f.kind == "select":
            if raw not in f.choices:
                raise ValueError
            return raw
        if f.kind == "checks":
            vals = [v for v in form.getall(f.key) if v in f.choices]
            if not vals:
                raise ValueError
            return vals
        if f.kind == "repos":
            vals = _lines(raw)
            if len(vals) > 100 or not all(REPO_RE.match(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "hosts":
            vals = [v.lower() for v in _lines(raw)]
            if not vals or len(vals) > 50 or not all(HOST_RE.match(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "hostlist":                # like hosts, but may be empty
            vals = [v.lower() for v in _lines(raw)]
            if len(vals) > 50 or not all(HOST_RE.match(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "paths":
            vals = _lines(raw)
            if len(vals) > 20 or not all(v.startswith("/") and len(v) < 300 and "\0" not in v for v in vals):
                raise ValueError
            return vals
        if f.kind == "labellist":               # empty: None, the built-in default
            vals = [v.strip() for v in raw.replace("\n", ",").split(",") if v.strip()]
            if len(vals) > 10 or any(len(v) > 50 for v in vals):
                raise ValueError
            return vals or None
        if f.kind == "url":
            v = raw.strip()
            if v and (not v.startswith("https://") or len(v) > 500 or any(c.isspace() for c in v)):
                raise ValueError
            return v
        if f.kind == "models":
            vals = _lines(raw)
            if len(vals) > MAX_FALLBACKS or len(set(vals)) != len(vals) or not all(MODEL_RE.fullmatch(v) for v in vals):
                raise ValueError
            return vals
        if f.kind == "wchecks":
            out, seen = [], set()
            for line in _lines(raw):
                parts = line.split()
                if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "advisory"):
                    raise ValueError
                repo, recipe, platform = parts[:3]
                if not REPO_RE.match(repo) or not CHECK_NAME.fullmatch(recipe) or not CHECK_NAME.fullmatch(platform) or (repo, recipe) in seen:
                    raise ValueError
                seen.add((repo, recipe))
                out.append({"repo": repo, "recipe": recipe, "platform": platform, **({"required": False} if len(parts) == 4 else {})})
            if len(out) > 50:
                raise ValueError
            return out
        if f.kind == "every":
            v = raw.strip()
            if v and (len(v) > 10 or parse_every(v) is None):
                raise ValueError
            return v
        if f.kind == "viewports":
            out, seen = [], set()
            for line in _lines(raw):
                m = VIEWPORT_LINE.fullmatch(line)
                if not m or m.group(1) in seen or m.group(1) == "design":
                    raise ValueError
                seen.add(m.group(1))
                out.append({"name": m.group(1), "width": int(m.group(2)), "height": int(m.group(3))})
            if not out or len(out) > 10:
                raise ValueError
            return out
        if f.kind == "kv":
            out = {}
            for line in _lines(raw):
                k, sep, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if not sep or not k or len(k) > 100 or v not in KINDS:
                    raise ValueError
                out[k] = v
            return out
    except (ValueError, AttributeError):
        pass
    hint = {"int": f"a whole number between {f.lo} and {f.hi}", "float": f"a number between {f.lo} and {f.hi}",
            "select": "one of " + ", ".join(f.choices), "checks": "at least one choice", "repos": "owner/name per line",
            "hosts": "valid host names, one per line, at least one", "hostlist": "valid host names, one per line",
            "paths": "absolute paths, one per line, at most 20", "url": "empty, or an https:// address", "labellist": "at most 10 labels, comma separated, each at most 50 characters", "kv": "label=kind per line, kind one of " + ", ".join(sorted(KINDS)),
            "every": "like 6h, 1d or 1w, or empty",
            "viewports": "name widthxheight per line, 1 to 10 of them, distinct names of lowercase letters, digits and dashes (not design)",
            "wchecks": "owner/name recipe platform [advisory] per line, names of lowercase letters, digits and dashes, no repeated repo and recipe",
            "models": f"up to {MAX_FALLBACKS} distinct model ids, one per line (may be empty)",
            "text": "a short single-line value",
            "prompt": f"at most {PROMPT_MAX} characters, with no control characters other than line breaks and tabs"}.get(f.kind, "a valid value")
    raise ValueError(f"{f.label}: must be {hint}")


def _commit(cfg_path: str, state_dir: Path, new_overrides: dict) -> None:
    """Validate the merged result exactly as the orchestrator will load it, then write and ask for a restart."""
    try:
        parse(deep_merge(base_raw(cfg_path), new_overrides))
    except (ValueError, KeyError, TypeError) as e:
        raise SettingsError([f"The configuration would be invalid: {e}"])
    write_overrides(cfg_path, new_overrides)
    request_restart(state_dir)


def _store(new_ov: dict, base: dict, key: str, value) -> None:
    """Keep an override only when it differs from what the file (or the built-in default) already gives."""
    current = canon(key, get_in(base, key))
    if current is None:
        current = fallback(base, key)
    if current is None:
        current = default_for(key)
    if value is None or current == value or (isinstance(current, tuple) and list(current) == value):
        del_in(new_ov, key)
    else:
        set_in(new_ov, key, value)


MAX_REPOS = 100


def read_repos(form) -> list[dict]:
    """The standalone repositories as typed, without the rows whose repository is empty (clearing it is how one is removed).
    Field names in check_repos count what is left, not the form's rows."""
    rows = [{"repo": repo_slug(form.get(f"repo{j}") or ""), "role": " ".join((form.get(f"repo{j}_role") or "").split())} for j in range(MAX_REPOS)]
    return [r for r in rows if r["repo"]]


def check_repos(rows: list[dict]) -> dict:
    """What is wrong with read_repos' result: the form field's name -> one sentence. app.js makes the same checks; keep the two alike."""
    bad: dict[str, str] = {}
    seen: set[str] = set()
    for j, r in enumerate(rows):
        if not REPO_RE.match(r["repo"]):
            bad[f"repo{j}"] = "Write the repository as owner/name, like your-org/shop-web."
        elif r["repo"].lower() in seen:
            bad[f"repo{j}"] = "This repository is listed twice."
        seen.add(r["repo"].lower())
        if len(r["role"]) > 300:
            bad[f"repo{j}_role"] = "Keep the description under 300 characters."
    return bad


def _save_repos(form: Form, base: dict, eff: dict, access) -> tuple[list[str], dict, dict]:
    """The repolist field: (the repositories, their descriptions as the override to store, what is wrong per field).
    access(repos) -> {repo: why the token cannot use it}, asked only about repositories that were not listed before."""
    rows = read_repos(form)
    bad = check_repos(rows)
    if not bad and access:
        had = {r.lower() for r in effective(eff, "github.repos") or []}
        refused = access([r["repo"] for r in rows if r["repo"].lower() not in had])
        bad = {f"repo{j}": refused[r["repo"]] for j, r in enumerate(rows) if r["repo"] in refused}
    want = {r["repo"]: r["role"] for r in rows if r["role"]}
    pinned = get_in(base, "github.repo_roles") or {}
    roles = {k: v for k, v in want.items() if pinned.get(k) != v}
    roles.update({k: "" for k, v in pinned.items() if v and k not in want})          # cleared here: an empty line hides config.toml's
    return [r["repo"] for r in rows], roles, bad


def save_section(cfg_path: str, state_dir: Path, section: str, form: Form, access=None) -> list[str]:
    """Returns human-readable notes (for example 'switched to live'). Raises SettingsError.
    access: asked whether the GitHub token can use newly listed repositories (see _save_repos); None skips the question."""
    if section not in SECTIONS:
        raise SettingsError(["Unknown settings section."])
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    eff = deep_merge(base, ov)
    fields = fields_for(section, eff)
    values, errors, marked, roles = {}, [], {}, None
    for f in fields:
        if f.kind == "repolist":
            values[f.key], roles, marked = _save_repos(form, base, eff, access)
            if marked:
                errors.append(f"{f.label}: correct the {'field' if len(marked) == 1 else f'{len(marked)} fields'} marked below.")
            continue
        try:
            values[f.key] = parse_value(f, form)
        except ValueError as e:
            errors.append(str(e))
    for f in fields:
        if f.danger and f.key in values and values[f.key] != effective(eff, f.key):
            # Dry run turning OFF is the dangerous direction; for other fields any change needs confirming.
            risky = (f.key != "general.dry_run") or values[f.key] is False
            if risky and form.get("confirm__" + f.key) != "1":
                errors.append(f"{f.label}: tick the confirmation box. {f.danger}")
    if errors:
        raise SettingsError(errors, marked)
    new_ov, notes = copy.deepcopy(ov), []
    for key, value in values.items():
        if value != effective(eff, key):
            notes.append(key)
        _store(new_ov, base, key, value)
    if roles is not None:
        set_in(new_ov, "github.repo_roles", roles) if roles else del_in(new_ov, "github.repo_roles")
    if get_in(base, "github.poll_seconds") is None and "github.poll_seconds" in values and values["github.poll_seconds"] == values.get("general.poll_seconds"):
        del_in(new_ov, "github.poll_seconds")          # equal to the poll interval: keep following it
    _commit(cfg_path, state_dir, new_ov)
    return notes


def set_dry_run(cfg_path: str, state_dir: Path, dry: bool) -> bool:
    """Switch between dry run and live (the Floor page's switch). Returns True when the value changed. The factory applies it at its next idle moment."""
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    if bool(effective(deep_merge(base, ov), "general.dry_run")) == dry:
        return False
    new_ov = copy.deepcopy(ov)
    _store(new_ov, base, "general.dry_run", dry)
    _commit(cfg_path, state_dir, new_ov)
    return True


def set_switch(cfg_path: str, state_dir: Path, key: str, on: bool) -> bool:
    """Turn one on/off feature (features.SWITCHES) on or off from the screen it shapes. Returns True when the value changed."""
    from .features import SWITCHES
    if key not in SWITCHES:
        raise SettingsError(["That setting cannot be switched here."])
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    if bool(effective(deep_merge(base, ov), key)) == on:
        return False
    new_ov = copy.deepcopy(ov)
    _store(new_ov, base, key, on)
    _commit(cfg_path, state_dir, new_ov)
    return True


def set_parallel(cfg_path: str, state_dir: Path, value: int) -> None:
    """The Factory's agents-at-once stepper."""
    if not 1 <= value <= 8:
        raise SettingsError(["Agents at once must be from 1 to 8."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "runner.max_parallel", value)
    _commit(cfg_path, state_dir, new_ov)


MAX_PROJECTS, MAX_PROJECT_REPOS = 20, 30
PROJECT_NAME_RE = re.compile(r"[\w .-]{1,60}")
_GITHUB_URL = re.compile(r"^(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:)([^/\s]+)/([^/\s#?]+)", re.I)


def repo_slug(text: str) -> str:
    """What a person typed for a repository, as owner/name: a pasted GitHub address loses its host, its .git and any path after the name."""
    text = (text or "").strip()
    m = _GITHUB_URL.match(text)
    if m:
        text = f"{m.group(1)}/{m.group(2)}"
    return text[:-4] if text.endswith(".git") else text


def read_project(form, i: int) -> dict:
    """One project's form as typed (its fields are p{i}_...), without its empty rows: a row with no repository is dropped (clearing it
    is how a repository is removed, whatever its role says). Field names in check_projects count what is left, not the form's rows."""
    rows = [{"repo": repo_slug(form.get(f"p{i}_r{j}_repo") or ""), "role": (form.get(f"p{i}_r{j}_role") or "").strip()} for j in range(MAX_PROJECT_REPOS)]
    return {"name": (form.get(f"p{i}_name") or "").strip(), "description": (form.get(f"p{i}_desc") or "").strip(),
            "repos": [r for r in rows if r["repo"]]}


def form_slot(form) -> int:
    """Which project's form was sent: the p{i}_ of its fields."""
    try:
        return min(max(int(form.get("pj") or 0), 0), MAX_PROJECTS)
    except ValueError:
        return 0


def check_projects(projects: list[dict]) -> dict:
    """What is wrong with a list of read_project results: the form field's name -> one sentence. Empty when it can be saved.
    app.js makes the same checks while the form is filled in; keep the two alike."""
    bad: dict[str, str] = {}
    names: dict[str, int] = {}
    owner: dict[str, tuple[int, str]] = {}
    for i, p in enumerate(projects):
        name, label = p["name"], p["name"] or f"project {i + 1}"
        if not name:
            bad[f"p{i}_name"] = "Give the project a name."
        elif not PROJECT_NAME_RE.fullmatch(name):
            bad[f"p{i}_name"] = "Use a short name: letters, digits, spaces, dot or dash, 60 characters at most."
        elif name.casefold() in names:
            bad[f"p{i}_name"] = "Another project already has this name."
        names.setdefault(name.casefold(), i)
        if len(p["description"]) > 500:
            bad[f"p{i}_desc"] = "Keep the description under 500 characters."
        if not p["repos"]:
            bad[f"p{i}_r0_repo"] = "Add at least one repository, as owner/name."
        for j, r in enumerate(p["repos"]):
            key, repo = f"p{i}_r{j}_repo", r["repo"]
            if not REPO_RE.match(repo):
                bad[key] = "Write the repository as owner/name, like your-org/shop-web."
            elif repo.lower() in owner:
                at, other = owner[repo.lower()]
                bad[key] = "This repository is listed twice." if at == i else f"{repo} is in two projects: {other} and {label}."
            else:
                owner[repo.lower()] = (i, label)
            if len(r["role"]) > 300:
                bad[f"p{i}_r{j}_role"] = "Keep the role under 300 characters."
    return bad


def save_project(cfg_path: str, state_dir: Path, form: Form) -> str:
    """One project's form: the New project form (`was` empty) or a saved project's (`was` its name when the page was drawn), with
    action=remove to drop it. The form edits the name, description, repos and roles; a repo's other keys (depends_on, publish) and
    the other projects are kept as they are. Returns the project's name, '' once removed.
    Refused fields are named p{slot}_..., slot being where the project is drawn on the page (projects_form)."""
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    eff = deep_merge(base, new_ov)
    projects = copy.deepcopy(eff.get("projects", []))
    was = (form.get("was") or "").strip()
    at = next((k for k, p in enumerate(projects) if p.get("name") == was), None) if was else None
    if was and at is None:
        raise SettingsError([f"There is no project called {was!r} any more: it was renamed or removed since this page was loaded. Reload the page."])
    typed = read_project(form, form_slot(form))
    if form.get("action") == "remove" or (at is not None and not typed["name"] and not typed["repos"]):
        if at is None:
            raise SettingsError(["Nothing to remove: this project is not saved yet."])
        del projects[at]
        _save_projects_list(cfg_path, state_dir, base, new_ov, projects)
        return ""
    if at is None and len(projects) >= MAX_PROJECTS:
        raise SettingsError([f"{MAX_PROJECTS} projects is the most the factory takes. Remove one first."])
    others = [{"name": p.get("name", ""), "description": p.get("description", ""),
               "repos": [{"repo": r if isinstance(r, str) else r.get("repo", ""), "role": "" if isinstance(r, str) else r.get("role", "")} for r in p.get("repos", [])]}
              for k, p in enumerate(projects) if k != at]
    slot = at if at is not None else len(projects)
    last = f"p{len(others)}_"                           # checked after the others, so a clash is marked on the project being saved
    bad = {f"p{slot}_" + k[len(last):]: why for k, why in check_projects(others + [typed]).items() if k.startswith(last)}
    if bad:
        raise SettingsError([f"Nothing was saved. Correct the {len(bad)} fields marked below." if len(bad) > 1 else "Nothing was saved. Correct the field marked below."], bad)
    extra = {r["repo"]: {k: v for k, v in r.items() if k not in ("repo", "role")}
             for p in eff.get("projects", []) for r in p.get("repos", []) if isinstance(r, dict)}
    repos = [{"repo": r["repo"], "role": r["role"], **extra.get(r["repo"], {})} for r in typed["repos"]]
    kept = {x for r in repos for x in (r["repo"], r["repo"].split("/")[-1])}
    for r in repos:                                     # a dependency on a repo removed here goes with it
        if "depends_on" in r:
            r["depends_on"] = [d for d in r["depends_on"] if d in kept]
            if not r["depends_on"]:
                del r["depends_on"]
    entry = {**(projects[at] if at is not None else {}), "name": typed["name"], "description": typed["description"], "repos": repos}
    if at is None:
        projects.append(entry)
    else:
        projects[at] = entry
    _save_projects_list(cfg_path, state_dir, base, new_ov, projects)
    return typed["name"]


def save_telegram(cfg_path: str, state_dir: Path, form: Form) -> None:
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    try:
        chat = int((form.get("telegram.chat_id") or "0").strip() or 0)
        if chat < 0:
            raise ValueError
    except ValueError:
        raise SettingsError(["Chat id must be a whole number (0 turns Telegram off)."])
    verbosity = form.get("telegram.verbosity", "")
    if verbosity not in ("quiet", "normal", "verbose"):
        raise SettingsError(["Choose quiet, normal or verbose."])
    _store(new_ov, base, "telegram.chat_id", chat)
    _store(new_ov, base, "telegram.verbosity", verbosity)
    ui_url = (form.get("telegram.ui_url") or "").strip()
    if ui_url and not re.fullmatch(r"https?://[^\s\"'<>|]{1,200}", ui_url):
        raise SettingsError(["The address of this UI must be an http(s) URL (or empty)."])
    if ui_url or "ui_url" in base.get("telegram", {}):
        _store(new_ov, base, "telegram.ui_url", ui_url.rstrip("/"))
    else:
        del_in(new_ov, "telegram.ui_url")
    if form.get("telegram.mode") == "events":
        chosen = [e for e in form.getall("telegram.events") if e in ALL_EVENTS]
        if not chosen:
            raise SettingsError(["Pick at least one event, or choose the verbosity level instead."])
        _store(new_ov, base, "telegram.events", sorted(chosen))
    elif "events" in base.get("telegram", {}):
        set_in(new_ov, "telegram.events", "level")           # the base file pins a list; this switches back to the level
    else:
        del_in(new_ov, "telegram.events")
    _commit(cfg_path, state_dir, new_ov)


def set_chat_id(cfg_path: str, state_dir: Path, chat_id: int) -> None:
    if chat_id < 0:
        raise SettingsError(["Chat id must not be negative."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "telegram.chat_id", chat_id)
    _commit(cfg_path, state_dir, new_ov)


def save_slack(cfg_path: str, state_dir: Path, form: Form) -> None:
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    channel, user = (form.get("slack.channel") or "").strip(), (form.get("slack.user_id") or "").strip()
    if channel and not re.fullmatch(r"[CGD][A-Z0-9]{6,20}", channel):
        raise SettingsError(["The channel must be a Slack id such as C0123ABCDEF (empty turns Slack off)."])
    if user and not re.fullmatch(r"[UW][A-Z0-9]{6,20}", user):
        raise SettingsError(["Your user id must be a Slack member id such as U0123ABCDEF (empty turns Slack off)."])
    verbosity = form.get("slack.verbosity", "")
    if verbosity not in ("quiet", "normal", "verbose"):
        raise SettingsError(["Choose quiet, normal or verbose."])
    ui_url = (form.get("slack.ui_url") or "").strip()
    if ui_url and not re.fullmatch(r"https?://[^\s\"'<>|]{1,200}", ui_url):
        raise SettingsError(["The address of this UI must be an http(s) URL (or empty)."])
    _store(new_ov, base, "slack.channel", channel)
    _store(new_ov, base, "slack.user_id", user)
    if ui_url or "ui_url" in base.get("slack", {}):
        _store(new_ov, base, "slack.ui_url", ui_url.rstrip("/"))
    else:
        del_in(new_ov, "slack.ui_url")
    _store(new_ov, base, "slack.verbosity", verbosity)
    if form.get("slack.mode") == "events":
        chosen = [e for e in form.getall("slack.events") if e in ALL_EVENTS]
        if not chosen:
            raise SettingsError(["Pick at least one event, or choose the verbosity level instead."])
        _store(new_ov, base, "slack.events", sorted(chosen))
    elif "events" in base.get("slack", {}):
        set_in(new_ov, "slack.events", "level")
    else:
        del_in(new_ov, "slack.events")
    _commit(cfg_path, state_dir, new_ov)


def set_slack_user(cfg_path: str, state_dir: Path, user_id: str, channel: str = "") -> None:
    """Obey this member, and send to the channel they typed /factory in (when one was recorded)."""
    if not re.fullmatch(r"[UW][A-Z0-9]{6,20}", user_id):
        raise SettingsError(["That is not a Slack member id."])
    if channel and not re.fullmatch(r"[CGD][A-Z0-9]{6,20}", channel):
        raise SettingsError(["That is not a Slack channel id."])
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    _store(new_ov, base, "slack.user_id", user_id)
    if channel:
        _store(new_ov, base, "slack.channel", channel)
    _commit(cfg_path, state_dir, new_ov)


IMAGE_RE = re.compile(r"^[\w./:@-]{1,200}$")


def save_harness(cfg_path: str, state_dir: Path, name: str, form: Form) -> None:
    base, ov = base_raw(cfg_path), overrides_raw(cfg_path)
    eff = deep_merge(base, ov)
    known = parse(eff).harnesses
    if name not in known:
        raise SettingsError(["Unknown harness."])
    cur = known[name]
    enabled = form.get("enabled") == "1"
    image = (form.get("image") or "").strip()
    command = (form.get("command") or "").strip()
    errors = []
    if not IMAGE_RE.match(image):
        errors.append("Image: a container image name such as localhost/factory-agent-codex:latest.")
    if not command or len(command) > 4000 or "\x00" in command or not ("/task/prompt.txt" in command or "/task/user.txt" in command):
        errors.append("Command: required, and it must read the task from /task/prompt.txt (or /task/user.txt with /task/system.txt).")
    try:
        hosts = parse_value(Field("hosts", "Allowed hosts", "hosts"), Form({"hosts": form.get("allow_hosts", "")}))
    except ValueError as e:
        hosts = None
        errors.append(str(e))
    if name == "claude-code" and not enabled:
        errors.append("claude-code is the default harness and stays enabled.")
    if not errors:
        widened = (enabled and not cur.enabled) or hosts != list(cur.allow_hosts) or command != cur.command or image != cur.image
        if widened and form.get("confirm") != "1":
            errors.append("Tick the confirmation box: enabling a harness, or changing its command, image or hosts, changes what runs "
                          "in the sandbox and what it can reach.")
    if errors:
        raise SettingsError(errors)
    new_ov = copy.deepcopy(ov)
    for field, value in (("enabled", enabled), ("image", image), ("command", command), ("allow_hosts", hosts)):
        _store(new_ov, base, f"harnesses.{name}.{field}", value)
    _commit(cfg_path, state_dir, new_ov)


PACKAGE_RE = re.compile(r"^(@[\w.-]+/)?[\w.-]+$")
WORKFLOW_RE = re.compile(r"^[\w.-]+\.ya?ml$")


def _project_entry(cfg_path: str, name: str) -> tuple[dict, dict, list, dict]:
    """(base, new overrides, the effective projects list (a copy), the named project in it, its repos as dicts)."""
    base, new_ov = base_raw(cfg_path), copy.deepcopy(overrides_raw(cfg_path))
    projects = copy.deepcopy(deep_merge(base, new_ov).get("projects", []))
    p = next((x for x in projects if x.get("name") == name), None)
    if p is None:
        raise SettingsError([f"There is no project called {name!r}."])
    p["repos"] = [r if isinstance(r, dict) else {"repo": r} for r in p.get("repos", [])]
    return base, new_ov, projects, p


def _save_projects_list(cfg_path: str, state_dir: Path, base: dict, new_ov: dict, projects: list) -> None:
    if projects == base.get("projects", []):
        new_ov.pop("projects", None)
    else:
        new_ov["projects"] = projects
    _commit(cfg_path, state_dir, new_ov)


def apply_dependency(cfg_path: str, state_dir: Path, project: str, publisher: str, detect: str, workflow: str, packages: list,
                     consumers: list, lockfiles: bool) -> None:
    """Write a suggested build order (depdetect.Suggestion): the publisher's `publish` table, `depends_on` on each consumer and, if
    asked, a [[workers.lockfiles]] entry per consumer. Everything is checked again here: the form fields came back from a browser."""
    from ..config import PUBLISH_DETECT
    base, new_ov, projects, p = _project_entry(cfg_path, project)
    names = {r["repo"] for r in p["repos"]}
    packages = [x.strip() for x in packages if x.strip()]
    consumers = [c for c in dict.fromkeys(consumers) if c]
    problems = ([] if publisher in names else [f"{publisher} is not in {project}."]) \
        + ([] if consumers and set(consumers) <= names - {publisher} else ["Each repo that uses the packages must be another repo of the project."]) \
        + ([] if detect in PUBLISH_DETECT else ["Unknown way to detect the publish."]) \
        + ([] if detect != "workflow" or WORKFLOW_RE.match(workflow) else ["The workflow must be a file name like publish.yml."]) \
        + ([] if 0 < len(packages) <= 100 and all(PACKAGE_RE.match(x) for x in packages) else ["The package names are not valid."])
    if problems:
        raise SettingsError(problems)
    short = publisher.split("/")[1]
    for r in p["repos"]:
        if r["repo"] == publisher:
            r["publish"] = {"detect": detect, "packages": packages, **({"workflow": workflow} if detect == "workflow" else {})}
        elif r["repo"] in consumers and not {publisher, short} & set(r.get("depends_on", [])):
            r["depends_on"] = list(r.get("depends_on", [])) + [short]
    if lockfiles:
        have = list(deep_merge(base, new_ov).get("workers", {}).get("lockfiles", []))
        add = [{"repo": c} for c in consumers if c not in {x.get("repo") for x in have}]
        if add:
            new_ov.setdefault("workers", {})["lockfiles"] = have + add
    _save_projects_list(cfg_path, state_dir, base, new_ov, projects)


def remove_dependency(cfg_path: str, state_dir: Path, project: str, publisher: str) -> None:
    """Undo apply_dependency for one publisher: its `publish` table and every `depends_on` naming it (lockfile entries stay: harmless)."""
    base, new_ov, projects, p = _project_entry(cfg_path, project)
    short = publisher.split("/")[-1]
    for r in p["repos"]:
        if r["repo"] == publisher:
            r.pop("publish", None)
        if "depends_on" in r:
            r["depends_on"] = [d for d in r["depends_on"] if d not in (publisher, short)]
            if not r["depends_on"]:
                del r["depends_on"]
    _save_projects_list(cfg_path, state_dir, base, new_ov, projects)
