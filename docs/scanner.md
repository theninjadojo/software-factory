# Code-smell scans

A scan reads a configured repository on a timer, looks for smells, and opens one ticket per smell. Off by default. Config: `[scanner]` in
`config.toml` (see `config.example.toml`). Code: `factory/scanner.py`, `worker/recipes/smell-scan.py`.

## How it works

1. When a scan is due, the orchestrator queues a worker job (recipe `smell-scan`, empty patch, the head of the default branch). The rules travel as
   data in the job's `params`; the poll loop never waits for the result.
2. The worker clones the repo, runs the recipe (reads files only, never runs repository code) and returns `findings`
   (`smell`, `path`, `line`, `snippet`). The orchestrator validates them strictly (`jobs.validate_findings`).
3. Each finding gets a fingerprint (smell, path, whitespace-normalised snippet; not the line). Only findings never filed before count.
   They are grouped per smell, at most `max_tickets` tickets are opened per run, and a smell whose last ticket is still open is skipped
   (its findings are considered again next run). Findings that are filed are not filed again, even after the ticket is closed.

Presets: `todo-debt`, `long-files` (over `max_lines`, default 800), `missing-tests`. Custom smells are a regular expression per line plus file globs.
Instruction (agent-evaluated) smells are not implemented yet.

## Behaviour and safety

- Requires `[workers] enabled = true`. A worker needs the `smell-scan` recipe (`worker/worker.example.toml`); with none, the job fails after
  `claim_wait_seconds`, the scan retries every 30 minutes, 3 times, and alerts. Verification jobs are claimed before scan jobs.
- Nothing runs in dry-run mode or while paused. The clock starts when a scan is first seen; it is never run retroactively.
- Repository content is untrusted. It goes into a ticket only inside a fence labelled untrusted, with mentions and images neutralised and a size cap.
  Labels come only from the admin's config (default: the analyst role's label).
- `python3 -m factory.ctl scans` lists scans; `scans run <name>` asks for one at the next poll.
