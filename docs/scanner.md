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

Presets: `todo-debt`, `long-files` (over `max_lines`, default 800), `missing-tests`. Also, for Python and JS/TS and with a default fix each (opt-in per scan, never added to existing scans):
security (`hardcoded-secret`, whose matched text is redacted to `[REDACTED]` by the recipe; `dynamic-eval`, `shell-injection`, `tls-verify-off`,
`weak-hash`, `sql-concat`), rough structure checks (`long-parameter-list`, `deep-nesting`), and `test-title-not-user-story` (JS/TS `test(...)`/`it(...)` titles in
spec and test files under an `e2e`, `playwright` or `cypress` folder that do not start "As a", for user-story titles such as "As a coach, I want to mark a child absent"). These are per-line patterns, so approximations: there is
no real SOLID or design-pattern detection. Update the worker's copy of `smell-scan.py` too: an older recipe ignores the redacting rule. Custom smells are a regular expression per line plus file globs.
Instruction (agent-evaluated) smells are not implemented yet.

## Behaviour and safety

- Requires `[workers] enabled = true`. A worker needs the `smell-scan` recipe (`worker/worker.example.toml`); with none, the job fails after
  `claim_wait_seconds`, the scan retries every 30 minutes, 3 times, and alerts. Verification jobs are claimed before scan jobs.
- Nothing runs in dry-run mode or while paused. The clock starts when a scan is first seen; it is never run retroactively.
- Repository content is untrusted. It goes into a ticket only inside a fence labelled untrusted, with mentions and images neutralised and a size cap.
  Labels come only from the admin's config (default: the analyst role's label).
- `python3 -m factory.ctl scans` lists scans; `scans run <name>` asks for one at the next poll.

## Managing scans and smells in the UI

Settings → **Scans** lists the scans (status, next run, **Run now**) and has a form to add, edit or delete one. The **Smell library** tab lists the
three presets and your own smells, with a form for a smell: a name (lower-case id, fixed once created), a regular expression matched per line, file
globs, and an optional recommended fix. Anything saved goes to `config.overrides.toml` (the first save copies the hand-written `[[scanner.smells]]`
and `[[scanner.scans]]` across, because a list in the overrides replaces the one in `config.toml`); the factory applies it when next idle. The Scans page
turns the scanner on and off (`[scanner] enabled`, saved to the overrides, applied when next idle); it refuses while `[workers] enabled` is
off and links to Workers instead. **Test on sample text** runs the real recipe on pasted lines in a throw-away folder: nothing
is saved and no repository is read. Anyone who can sign in to the UI can change smells and scans.

## Recommended fix

A smell can carry `advice` (a preset takes it from `[scanner.advice]`, keyed by its id): plain text of at most 2000 characters, written by an admin.
Each ticket for that smell shows it under **Recommended fix (from the smell definition)**, after the fenced, untrusted list of findings and never
inside it. The ticket still goes to the analyst role, which proposes a fix for each finding. Without advice the ticket is unchanged.

## Slow patterns

A custom pattern runs on a worker, so a pattern that backtracks without end could stall a scan. Two limits apply:

- The UI refuses patterns outside a safe subset (`scanner.regex_problem`): no backreferences, lookahead or lookbehind, conditional groups or inline
  flags other than `(?i)`; no repeat count above 1000; and no repeated group that itself holds a repeat or an alternation, such as `(a+)+` or
  `(a|ab)*`. A hand-written pattern outside the subset still loads (it is logged as a warning), so existing config keeps working.
- The recipe stops a smell after 2 seconds on one file (`SIGALRM`) and skips that file. After 3 such files the smell is dropped for the rest of
  the run and the job log says `smell <id> dropped: too slow`. Workers that have not updated yet have no such limit, only the recipe timeout.
