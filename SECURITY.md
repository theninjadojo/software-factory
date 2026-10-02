# Security model

software-factory runs AI agents on text written by other people (ticket bodies, comments, repo contents). Any of that
text can try to take over the agent. The design goal is not to detect that, it is to make a taken-over agent unable to do
anything that matters.

## What is protected, and how

| Boundary | Control | Tested in |
|---|---|---|
| Who can start work | A trigger label counts only if a user with write access applied it (read from the issue timeline, fail closed) | `tests/test_core.py`, `tests/test_roles.py` |
| What the classifier decides | Typed answers validated against fixed sets; a fixed table turns them into a model and effort. Ticket text cannot name a model, a command or a path | `tests/test_jev.py` |
| What the agent can reach | Sandbox: no network (only a unix socket to an allowlist proxy), read-only root, no capabilities, non-root, memory/CPU/pid limits, no GitHub credentials, per-task workspace copies | `tests/test_runner.py` |
| What the agent can send out | The proxy tunnels TLS only to allowlisted host names on 443 | `tests/test_runner.py` |
| What the agent's output can do | It is data. Patches are size- and file-count-limited, must not touch protected paths (`.git`, `.github`, `.claude`, `.githooks`, `.agents`, `.husky`, `.mcp.json`, CODEOWNERS ...) or add symlinks/submodules, and are applied by the orchestrator to **pristine clones**. Git is never run on the agent's own `.git`, and hooks are disabled | `tests/test_runner.py` |
| What the factory may write to GitHub | Only `factory/*` branches (code-enforced), PRs, issue comments and labels. No Workflows or Administration permission | `tests/test_runner.py` |
| Documents posted to tickets | Sanitized: no @mentions (they ping people), no images (a rendered URL can leak data), no active HTML, length-capped | `tests/test_roles.py` |
| Earlier stage outputs fed to later agents | Trusted only if authored by the factory's own account; a forged comment with the marker is ignored | `tests/test_roles.py` |
| Prompt structure | Untrusted text cannot close the prompt's wrapper tags | `tests/test_runner.py` |
| Telegram | Only the configured user id; a fixed set of commands; strict callback parsing; plain-text messages | `tests/test_telegram.py` |
| Multi-repo changes | All patches validated before anything is pushed | `tests/test_runner.py` |
| The admin UI | Password (scrypt), CSRF on every POST, HttpOnly/SameSite=Strict cookies, login throttling, Host allowlist, strict CSP, read-only database access, escaped output, GitHub-only links | `tests/test_ui.py` |
| Credentials in the UI | Write-only: stored 0600, never rendered back, scrubbed from error messages | `tests/test_ui_admin.py` |
| Settings changes | Validated by loading the merged config before writing; confirmation for going live, widening the sandbox hosts, or changing a harness | `tests/test_ui_admin.py`, `tests/test_ui_harness.py` |
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
