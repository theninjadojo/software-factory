# Plan: the dashboard and settings UI

> **Historical.** This is the original planning document, kept for context. It may not match the UI as built; see [ui.md](ui.md) for the current behaviour.

## Goals (from the maintainer)

1. See **what is happening now and what has happened**: running tasks, history, decisions, PRs and CI, alerts.
2. **Change configuration** without editing files: poll time, classification, routing, roles, projects, CI.
3. **Set up credentials** (GitHub, Claude, OpenRouter, Telegram) and **Telegram itself**, including how chatty it is.
4. **Connect other agent harnesses** (Codex CLI, Gemini CLI) beside Claude Code.
5. Contained in Docker, shareable, standard library only like the rest.

## Constraints

This UI is an admin surface for a system that holds credentials and can act on repositories, so:

- **Authenticated** (password, scrypt-hashed), CSRF-protected, strict headers and CSP, Host-header allowlist, login throttling.
- **Loopback by default.** Remote access is an explicit choice (SSH tunnel, VPN or reverse proxy with TLS).
- **Secrets are write-only**: stored 0600 and never rendered back. Status pages show "set / not set / updated when".
- **No shell, no code execution from the UI.** It edits validated settings and files, nothing else.
- **Every dynamic string is escaped.** Ticket text, agent output and logs are untrusted.
- The factory keeps working if the UI is down; the UI works (read-only) if the factory is down.

## Design

**Data.** The orchestrator records `runs` (one row per agent run: model, effort, classification, status, output, log tail,
PR links), `events` (an append-only timeline: decisions, alerts, errors, config reloads) and a `status` heartbeat
(last poll, mode, running task). SQLite in WAL mode so the UI reads while the orchestrator writes.

**Config.** `config.toml` stays human-edited and keeps its comments. The UI writes only an **overrides file** next to it
(`config.overrides.toml`), deep-merged over the base at load. Every save is validated by loading the merged result first,
backed up, and written atomically. Reverting is deleting a key from the overrides.

**Applying changes.** The UI drops a `RESTART` marker; the orchestrator re-executes itself at its next *idle* moment
(never mid-task), so every setting, credential and classifier choice is picked up the same way.

**Process.** `python3 -m factory.ui` is a separate process (separate container in compose). It shares the state directory,
the config files and the secrets directory with the orchestrator.

**Server.** `http.server` with server-rendered HTML, a small stylesheet and ~100 lines of JavaScript that refreshes the
overview fragment every few seconds. No framework, no build step.

**Harness adapters.** The sandbox entrypoint runs a configured command (`AGENT_COMMAND`) instead of a hard-coded
`claude -p`. A harness is `{image, env file, command template, allowed hosts}`; routes and roles pick one by name. Claude
Code is built in; Codex and Gemini ship as templates marked experimental until verified end to end.

## Milestones

1. **Data layer**: runs, events, heartbeat, overrides, restart marker, instrumentation. Tests.
2. **Read-only dashboard**: auth, overview (live), runs and run detail, tickets, PRs/CI, events. Tests for auth, CSRF, escaping.
3. **Settings and credentials**: schema-driven forms, validation, overrides writer, write-only secrets, Telegram setup
   (find my id, test message, verbosity), classifier tester. Tests.
4. **Harness adapters**: generic agent command, Codex/Gemini templates, harness page, per-route/role harness choice.
5. **Packaging**: compose service, systemd unit, docs, deploy to the reference install, verify in a browser.

Later (milestone 2 of the project): the Mac verification worker.
