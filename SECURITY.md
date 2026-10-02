# Security model

software-factory runs AI agents on text written by other people (ticket bodies, comments, repo contents). Any of that
text can try to take over the agent. The design goal is not to detect that, it is to make a taken-over agent unable to do
anything that matters.

## What is protected, and how

| Boundary | Control | Tested in |
|---|---|---|
| Who can start work | A trigger label counts only if a user with write access applied it (read from the issue timeline, fail closed) | `tests/test_core.py`, `tests/test_roles.py` |
| What the classifier decides | Typed answers validated against fixed sets; a fixed table turns them into a model and effort. Ticket text cannot name a model, a command or a path | `tests/test_jev.py` |
| Fallback models | The fallback chain is admin configuration (`fallback_models`, same harness, at most 3); ticket text and the classifier cannot select, add or reorder it | `tests/test_fallback.py` |
| What the agent can reach | Sandbox: no network (only a unix socket to an allowlist proxy), read-only root, no capabilities, non-root, memory/CPU/pid limits, no GitHub credentials, per-task workspace copies | `tests/test_runner.py` |
| What the agent can send out | The proxy tunnels TLS only to allowlisted host names on 443 | `tests/test_runner.py` |
| What the agent's output can do | It is data. Patches are size- and file-count-limited, must not touch protected paths (`.git`, `.github`, `.claude`, `.githooks`, `.agents`, `.husky`, `.mcp.json`, CODEOWNERS ...) or add symlinks/submodules, and are applied by the orchestrator to **pristine clones**. Git is never run on the agent's own `.git`, and hooks are disabled | `tests/test_runner.py` |
| What the factory may write to GitHub | Only `factory/*` branches (code-enforced), PRs, issue comments and labels, plus (opt-in, `[subtasks] enabled`) step sub-issues with a fixed `[factory] ` title prefix and a body built only from factory fields (no ticket text, agent output, labels or assignees). No Workflows or Administration permission | `tests/test_runner.py`, `tests/test_subtasks.py` |
| Documents posted to tickets | Sanitized: no @mentions (they ping people), no images (a rendered URL can leak data), no active HTML, length-capped | `tests/test_roles.py` |
| Earlier stage outputs fed to later agents | Trusted only if authored by the factory's own account; a forged comment with the marker is ignored | `tests/test_roles.py` |
| Prompt structure | Untrusted text cannot close the prompt's wrapper tags | `tests/test_runner.py` |
| Operator prompts (`[prompts]`) | Trusted config only (config.toml or the authenticated UI), never ticket, comment or repository text; placed before the built-in rules, which say they win; each agent gets only `all` plus its own entry; length- and character-checked; changing one in the UI needs the confirmation box | `tests/test_prompts.py` |
| Telegram | Only the configured user id; a fixed set of commands; strict callback parsing; plain-text messages | `tests/test_telegram.py` |
| Multi-repo changes | All patches validated before anything is pushed | `tests/test_runner.py` |
| Design mockups written by the designer | Only brand-new `design/factory-<ticket>-<slug>.dc.html` files (at most 3, 150 KB each); the patch must add nothing else. Each file is parsed against a tag allowlist: no scripts except the fixed `support.js` include, no event handlers, forms, iframes, images or `url()`, links are `#` only, the only external resource is Google Fonts. A failing file is dropped and the document still posts. Published on a draft PR from a `factory/*` branch, linked by commit SHA. The orchestrator records each file per run, and the admin UI links it (to GitHub, new tab) only after re-checking it with `designfiles.link_ok`; the UI never serves or renders the files, and a designer run with no mockups gets a fixed line on the ticket | `tests/test_designfiles.py`, `tests/test_ui.py`| `tests/test_designfiles.py`, `tests/test_designer_files.py` |
| Rendered previews of design canvases | After a canvas passes the design-file checks, a second container renders it to a PNG: no network, read-only root, no capabilities, no credentials or proxy, only that validated file mounted read-only and one output folder. The PNG is untrusted: it must be a real PNG within size limits, and the orchestrator commits it under a fixed name next to the canvas (`previews/factory-<n>-<slug>.png`) on the same draft-PR branch. A missing image, crash or timeout only means no preview | `tests/test_render.py` |
| The code reviewer | Read-only role: it posts a sanitized comment and nothing else. The GitHub client has no approve / request-changes call, it only checks out branches the factory created, and a reviewer failure cannot undo a build | `tests/test_review.py` |
| The project manager | Read-only role. Its `factory-priorities` block is data, validated entry by entry: only tickets of the backlog it was given, a priority from a fixed set, at most 5 blockers that are open issues of the same repository; anything else is dropped, and a missing or malformed block changes nothing. Its only GitHub writes are adding or removing the fixed `priority: high` / `priority: low` labels it applied itself (a person's priority label is never touched) and a sanitized comment built from validated values. A priority only orders tickets that are already eligible; a blocker only holds a build back (never starts, stops or fails work), counts only while the blocker is open, and a trusted `factory:unblocked` label overrides it | `tests/test_pm.py` |
| Merge-conflict resolution | Only open PRs on `factory/*` branches in the same repository (the label alone is not enough). The orchestrator merges the base branch into the branch on its pristine clone with no repository-defined merge drivers or hooks; the agent's patch may touch only the repos with conflicts, is checked against the merged tree (so protected paths and limits apply to the agent's own changes), and is refused if any conflict marker remains or a design canvas fails its checks. Conflicts in protected paths or binary files, and merges that change `.github/workflows/`, go to a person. The result is a normal merge commit pushed as a fast-forward: no rebase, no force-push. At most `max_attempts` runs per PR | `tests/test_conflicts.py` |
| The admin UI | Password (scrypt), CSRF on every POST, HttpOnly/SameSite=Strict cookies, login throttling, Host allowlist, strict CSP, read-only database access, escaped output, GitHub-only links | `tests/test_ui.py` |
| The UI's Floor page | Read-only picture of the orchestrator's database; the "Needs you" tray reads open tickets from GitHub with the server-side token (cached 20s, fixed error messages) and its buttons post to the same validated `/tickets/start` and `/tickets/answer` actions as the Tickets page. Questions are answered through `/tickets/answer` (several at once, validated against the questions on GitHub) and `/tickets/answer-all` (a fixed list of configured tickets, each re-read). Build and the stage buttons queue the same approval row the Telegram buttons write (the UI's only database write; repo, issue and action are validated first), because a label would send the ticket back through the classifier. All text is escaped; the CSP forbids inline styles, so positions live in the stylesheet | `tests/test_floor.py` |
| The UI's Tickets page (start buttons, label editing, new and close ticket) | Acts as the factory's GitHub account, server-side only: the repo must be configured, the issue number is validated, only labels that already exist can be added, search text is free text (no qualifiers), a new ticket has a title and body only (length-limited, no labels or assignees, so it cannot start work; repeats within 10s are refused), Close re-reads the ticket, refuses one that is running, queued or waiting for a person, then posts a fixed comment and closes it (never a permanent delete), errors are fixed messages | `tests/test_ui_admin.py`, `tests/test_ci.py` |
| Credentials in the UI | Write-only: stored 0600, never rendered back, scrubbed from error messages | `tests/test_ui_admin.py` |
| Settings changes | Validated by loading the merged config before writing; confirmation for going live, widening the sandbox hosts, changing a harness, or changing an agent prompt | `tests/test_ui_admin.py`, `tests/test_ui_harness.py` |
| Which hosts a sandbox can reach | The proxy allowlist follows config, and keeps the last good list if a reload fails | `tests/test_harness.py` |

## Residual risks (read these)

- **Prompt injection can still produce bad output**: harmful code in a PR, or a misleading analysis. The control is human
  review of every PR and branch protection on your default branch. The factory never merges.
- **Model credentials are inside the sandbox environment.** Egress is limited to the model API host, so they cannot be
  sent elsewhere, but a hijacked agent can spend your quota or rate limit.
- **The engine socket (Docker mode).** Mounting it into the orchestrator is root-equivalent on a rootful Docker. Use
  rootless Docker, or the native Podman install, which shares no socket.
- **Data leaves your machine** to the model provider, and (if Jev is enabled) ticket titles, bodies and comments go to
  OpenRouter and TypeSafe for classification. Do not use free or logging tiers for private text without reading their terms.
- **CI runs your repos' code on factory PRs** with whatever secrets your workflows expose to pull requests. That is your
  repos' configuration, not the factory's, but protected paths stop the agent editing workflows.
- **The admin UI is an admin surface.** Anyone who can sign in can replace credentials and change what the factory does. Keep it on
  loopback or behind a VPN/TLS proxy, with a strong password.
- **The UI can start work.** Its Tickets page applies labels as the factory's GitHub account, which the write-access check accepts, so a
  signed-in UI admin can trigger builds exactly as if they had applied the label on GitHub. The UI therefore also holds the GitHub token.
- **Harness commands are shell commands you configure**, run inside the sandbox. They come from config, never from ticket text,
  but a bad one weakens the agent's behaviour (not the sandbox).
- **Token scope.** The GitHub token can write branches and PRs in every repo it is installed on. Scope it narrowly and
  prefer a dedicated bot account.

## Hardening checklist

- [ ] Branch protection or rulesets on the default branch of every repo (required review, no force-push, no bypass).
- [ ] A dedicated GitHub bot account; a fine-grained token limited to the repos you need; short expiry.
- [ ] Model credentials with a spend limit (an API key in its own workspace).
- [ ] `allow_hosts` contains only the model API host unless tasks truly need a registry.
- [ ] Secrets are files with mode 0600 owned by the factory user, outside any repo.
- [ ] The orchestrator runs as an unprivileged user with no sudo.
- [ ] Only people you trust have write access to the repos (they decide what gets labeled).

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting (Security tab → Report a vulnerability) rather than a public issue.
