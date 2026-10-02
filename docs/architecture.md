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
| `ci.py` | Watches CI on factory PRs; reports; drives the optional fix round. |
| `proxy.py` | The egress proxy: a CONNECT-only tunnel on a unix socket that allows only listed host names on port 443. |
| `sanitize.py` | Cleans agent-written markdown before it is posted to GitHub. |
| `telegram.py` / `events.py` | Alerts, commands and buttons; event categories and verbosity levels. |
| `github.py` | A minimal GitHub client. Writes are limited to comments, labels and PRs (branches are pushed with git). |
| `db.py` / `pause.py` / `ctl.py` | SQLite state (decisions, approvals, watched PRs), the pause switch, and a tiny operator CLI. |

## One issue, end to end

1. A person applies a trigger label. Each poll lists issues per trigger label (stage labels first, then `factory:auto`,
   then `factory:ready`); an issue with several is handled one at a time.
2. **Trust gate.** The issue timeline says who applied *that* label; their repo permission must be in `trusted_permissions`.
3. **Classify.** The ticket, its human comments and the project description go to the classifier. Output is typed and
   validated. A stage label skips this (a person already chose); `factory:auto` uses the classifier's stage.
4. **Claim.** The trigger label is swapped for a working label, so a crash or restart leaves a visible, recoverable state.
5. **Run.** The orchestrator clones each repo of the project (twice: a pristine `base/` it controls and a `work/` copy
   for the agent), writes the prompt, and starts the sandbox. The agent edits `work/` and exits; a `git diff` per repo is
   written to the output directory.
6. **Result.**
   - *Build:* each patch is validated and applied to the pristine clone; if all pass, branches `factory/issue-N-...` are
     pushed and one PR per changed repo is opened and cross-linked.
   - *Role:* the agent's final message is sanitized and posted as a comment with a hidden marker; a `stage:*` label records
     it; the classifier is asked what should happen next.
7. **CI.** Each PR is watched; the result is posted; on failure the agent may get a fix round that adds a commit to the
   same branch.

## Why the sandbox looks the way it does

- *No network except a socket to a proxy*: the model needs one host. A socket (not a network) means there is nothing to
  scan, no DNS, no way to reach the host's other services.
- *Workspace copies, pristine clones*: the agent can scribble anywhere in its copy, including `.git` (hooks and config
  there would execute on whoever runs git next). The orchestrator therefore never runs git on the agent's copy: it takes
  only a text diff and applies it to a clone the agent never touched.
- *No GitHub credentials in the sandbox*: the orchestrator pushes. A hijacked agent has nothing to push with.

## State

SQLite in the state directory: `decisions` (one row per issue state seen, so nothing is processed twice), `approvals`
(Telegram Run/Skip), `prs` (PRs being watched for CI). A lock file ensures one orchestrator at a time.

## Engines

`[runner] engine` is `podman` or `docker`. Podman maps the host user into the container (`--userns=keep-id`); Docker runs
the container as uid 1000 and relies on the per-task workspace being world-writable. Everything else is identical, and
both are exercised by tests (`tests/test_runner.py::Engines`) and by the smoke checks described in docs/operations.md.
