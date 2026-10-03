# Architecture

A small Python program (standard library only) plus a sandbox image. Nothing else runs.

## Modules (`factory/`)

| Module | Responsibility |
|---|---|
| `main.py` | The poll loop. Finds labeled issues, applies the trust gate, classifies, dispatches stages or builds, posts results, watches CI, recovers after restarts. |
| `config.py` | Typed config: routes, roles, projects, runner, CI, Telegram. Validates engine, verbosity and events. |
| `classifier.py` / `jev.py` | The `Classification` (kind, tier, effort, needs-a-person, stage, confidence). `jev.py` asks OpenRouter's Decisions API typed questions and parses the answers strictly; any error falls back to label-based rules. |
| `router.py` | Turns a classification into a route (model + effort) from a fixed table, or sends it to a person. |
| `runner.py` | Runs one task: clones every repo of the project, starts the sandbox, then either validates and applies patches and opens PRs, or returns a role's document. Also contains the patch validator and the push guard. |
| `roles.py` | Prompts for the analyst, designer and architect, and the stage names. |
| `pm.py` | The project manager: validates its `factory-priorities` block, applies only the priority labels it owns, records blockers (`pm_assessments`), and answers "which open tickets hold this one back" for the poll loop. |
| `ci.py` | Watches CI on factory PRs; reports; drives the optional fix round. |
| `proxy.py` | The egress proxy: a CONNECT-only tunnel on a unix socket that allows only listed host names on port 443. |
| `sanitize.py` | Cleans agent-written markdown before it is posted to GitHub. |
| `telegram.py` / `events.py` | Alerts, commands and buttons; event categories and verbosity levels. |
| `github.py` | A minimal GitHub client. Writes are limited to comments, labels, PRs and (when `[subtasks] enabled`) the step sub-issues the factory creates itself (branches are pushed with git). `subtasks.py` mirrors each pipeline step of a ticket as one sub-issue; the database stays the source of truth. |
| `ui/` | The admin UI: `server.py` (HTTP, auth, headers), `views.py`/`forms.py` (escaped HTML), `settings.py` (validated edits to the overrides file), `integrations.py` (credentials and outbound checks), `admin.py` (routes). See [ui.md](ui.md). |
| `tomlw.py` | A minimal TOML writer for the overrides file. |
| `db.py` / `pause.py` / `ctl.py` | SQLite state (decisions, approvals, watched PRs), the pause switch, and a tiny operator CLI. |

## One issue, end to end

1. A person applies a trigger label. Each poll lists issues per trigger label (stage labels first, then `factory:auto`,
   then `factory:ready`); an issue with several is handled one at a time.
2. **Trust gate.** The issue timeline says who applied *that* label; their repo permission must be in `trusted_permissions`.
3. **Classify.** The ticket, its human comments and the project description go to the classifier. Output is typed and
   validated. A stage label skips this (a person already chose); `factory:auto` uses the classifier's stage, and may skip stages straight to a build (high complexity goes to the architect first; no stage means the analyst).
4. **Claim.** The trigger label is swapped for a working label, so a crash or restart leaves a visible, recoverable state.
5. **Run.** The orchestrator clones each repo of the project (twice: a pristine `base/` it controls and a `work/` copy
   for the agent), writes the prompt, and starts the sandbox. The agent edits `work/` and exits; a `git diff` per repo is
   written to the output directory.
6. **Result.**
   - *Build:* each patch is validated and applied to the pristine clone; if all pass, branches `factory/issue-N-...` are
     pushed and one PR per changed repo is opened and cross-linked.
   - *Role:* the agent's final message is sanitized and posted as a comment with a hidden marker; a `stage:*` label records
     it; the classifier is asked what should happen next.
7. **Design files (designer only).** The designer's workspace may contain new `design/*.dc.html` files. They are collected as a patch, which
   may only ADD files with the allowed name; each file is validated (see SECURITY.md), then committed to a `factory/design-*` branch and opened as a
   draft PR, and the ticket comment links them by commit SHA (a designer run with none gets a fixed explanatory line). The files are also recorded per run in the `design_files` table, and the admin UI links them on the run and ticket pages.
8. **Review (optional).** After a build opens PRs, or when a person labels the ticket `factory:review`, a read-only reviewer is run with the PR
   branches checked out and its comment is posted on every PR of the change. It is a role like the analyst, so it uses the same sandbox,
   sanitizer and harness selection; it only ever comments.
. **Merge conflicts (optional).** Every PR the factory opens (build and design) is checked for conflicts with its base branch, whether
    or not CI is watched. GitHub's `mergeable` is read on each poll (`null` means it is still computing and is never treated as a
    conflict). A conflict is reported once per head commit, and the factory applies `factory:fix-conflicts` to the ticket, which goes
    through the normal trust gate. The run groups the ticket's conflicting PRs by branch (sibling PRs share one), merges each base
    branch into the pristine clone with `--no-commit` and stages the files with conflict markers. If git merged cleanly no agent runs;
    otherwise the agent gets a copy and a list of the conflicted files, and its diff (against that staged state) is validated, applied,
    and checked for leftover markers. The merge commit is pushed to the same branch, CI is watched again, and the attempt is counted
    (10 per PR by default).

## Why the sandbox looks the way it does

- *No network except a socket to a proxy*: the model needs one host. A socket (not a network) means there is nothing to
  scan, no DNS, no way to reach the host's other services.
- *Workspace copies, pristine clones*: the agent can scribble anywhere in its copy, including `.git` (hooks and config
  there would execute on whoever runs git next). The orchestrator therefore never runs git on the agent's copy: it takes
  only a text diff and applies it to a clone the agent never touched.
- *No GitHub credentials in the sandbox*: the orchestrator pushes. A hijacked agent has nothing to push with.

## State

SQLite (WAL) in the state directory: `runs` (one row per agent run, with output, log tail and the token counts the harness reported: `NULL` when it reports none; claude-code is run with `--output-format json` for this, see `runner.parse_usage`), `events` (the timeline, including `ci:passed`, `ci:failed` and `ci:timed-out` on the ticket for each CI round; `db.journey` builds a ticket's ordered route from runs, decisions and these), `status` (heartbeat), `decisions` (one row per issue state seen, so nothing is processed twice), `approvals`
(Telegram Run/Skip), `prs` (PRs being watched for CI), `pr_conflicts` (every factory PR, its merge-conflict state and resolution attempts). A lock file ensures one orchestrator at a time.

## Harnesses

A harness is `{image, credential file, command, allowed hosts}`. The sandbox entrypoint runs the command from the harness
selected by the route or role (`AGENT_COMMAND`); everything around it (workspace, patch validation, proxy) is the same for every
agent. The proxy allows the runner's hosts plus those of enabled harnesses.

## Engines

`[runner] engine` is `podman` or `docker`. Podman maps the host user into the container (`--userns=keep-id`); Docker runs
the container as uid 1000 and relies on the per-task workspace being world-writable. Everything else is identical, and
both are exercised by tests (`tests/test_runner.py::Engines`) and by the smoke checks described in docs/operations.md.

## Rendered previews of design mockups

The designer writes static `.dc.html` canvases. Once they pass `designfiles.validate_html`, `render.py` runs them through a sealed
container (`sandbox/render/`: headless Chromium, no network, read-only, no capabilities) and gets a PNG back for each. The orchestrator
checks the PNG (signature, size limits), commits it as `<design dir>/previews/<name>.png` on the same draft-PR branch, lists it in the PR
body, and links it in the designer's ticket comment and on the run/pipeline pages (all through `designfiles.link_ok`). A missing renderer
image, a crash or a timeout only means no preview: the canvases are still published.

## Screen verification

For a repo with `[[screens.pages]]` configured, `runner.run_task` runs `screens.verify` after the agent's patch is validated and applied
to the clean clone, before anything is pushed. Screens are listed in trusted config, never in the repo. Two sealed containers of the
`sandbox/screens/` image (no network, read-only, no capabilities) do the work: `shoot.js` serves a copy of the repo (no `.git`) on
127.0.0.1 and screenshots each page at each viewport, then `compare.js`, which sees only PNGs, diffs them against the baselines committed
under `screens.baseline_dir`. The orchestrator validates every PNG (`render.png_ok`, same size as its baseline) and re-judges the numeric
verdict against `max_diff_ratio`. It fails closed: a missing image, baseline or screenshot fails the run and nothing is pushed; diff PNGs
are kept on the host under `<work_dir>/../artifacts/screens/`. A patch that changes baseline images opens a draft PR with the
`screens.label` label so a person reviews them; in a CI fix round it is rejected. Not covered yet: an automatic fix round on mismatch, a
`ctl` command to create baselines, and the screens tool in the agent image.


## Mockups and screens in the build loop

1. The designer's rendered mockup PNGs are stored in the `mockup_images` table when the design PR is published (the UI serves them at `/mockup`, behind the login).
2. A build is held when the design stage ran but no mockup exists (`[mockups] mode = "block"`; `warn` and `off` also exist; the `bypass_label` skips it per ticket;
   `require_approval` additionally needs the `approve_label` or a merged design PR). A designer that said the ticket has no screens is never held.
3. The implementer and the reviewer get the mockups as images in `/task/mockups/`. The reviewer also gets the built pages (`/task/built/`), rendered from the PR
   branch by the screen check's own container.
4. After the patch is applied the screen check renders each configured page (desktop and mobile) and compares it with the committed baselines. The screenshots of what
   was built, and the diff images, are kept per run (`run_images`, shown on the run page). A mismatch gets one fix round: the agent is run again with the diff images
   in `/task/diffs/`; a second mismatch fails the run.
5. `python3 -m factory.ctl screens baseline <owner/repo> <checkout>` renders the configured screens of a local checkout and writes them as the baselines for a person to review and commit.

Both images (`factory-render`, `factory-screens`) are built by `docker compose --profile build build` and by `scripts/deploy.sh --image`.
