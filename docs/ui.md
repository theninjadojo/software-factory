# The admin UI

A dashboard and settings editor, in the same standard-library-only style as the rest. It is a separate process
(`python3 -m factory.ui`, or the `ui` service in the compose stack). The factory keeps working if the UI is down.

## Starting it

1. Set the password once (minimum 10 characters; stored as an scrypt hash, mode 0600, in the state directory):
   - native: `python3 -m factory.ui --config /srv/factory/config.toml --set-password`
   - compose: `docker compose run --rm ui python3 -m factory.ui --config /etc/factory/config.toml --set-password`
2. Start it: `systemctl --user enable --now factory-ui` (native, see `deploy/systemd/factory-ui.service`) or
   `docker compose up -d ui`.
3. It listens on **loopback only** by default. Reach it with an SSH tunnel
   (`ssh -L 8787:127.0.0.1:8787 your-host`, then open http://localhost:8787), a VPN, or a reverse proxy that terminates TLS.
   To listen elsewhere, set `--listen host:port` and tell it which Host names to accept (`--allowed-host`, or
   `FACTORY_UI_ALLOWED_HOSTS`); add `--secure-cookie` once it is served over HTTPS.

## What you can see

| Page | Shows |
|---|---|
| Overview | Health (is the orchestrator polling?), mode (dry-run or LIVE), pause/resume, what is running now, recent runs, watched PRs, the timeline. Refreshes itself. |
| Runs | Every agent run: ticket, model and effort, classification, status, duration, PR links. A run's page has the classifier's answer, the output document and the agent log tail. |
| Tickets | The latest decision per ticket, including *ignored* ones and why (for example the label was applied by someone without write access), and a `done/total` pipeline-steps count. Each count opens the ticket's read-only pipeline view (stations for analyze, design, architect, implement, review, CI fix: status, agent, attempts, run and PR links, and the design files as GitHub links), built from the run history. A run page lists the design files it published (links to GitHub only; mockups are never served by the UI). |
| PRs & CI | Pull requests being watched, CI status, fix rounds used. |
| Events | The full timeline: decisions, every alert (including ones Telegram did not send), run starts and ends, errors, restarts. |

## What you can change

| Page | Changes |
|---|---|
| Settings | Poll interval, dry run (go live), confidence threshold, labels, who may apply them, repositories, projects, routing (tier → harness/model/effort), role agents, classifier (Jev or labels, label → kind aliases, with a **Try it** box), the code reviewer (on/off, automatic or by label, model, harness), sandbox limits and allowed hosts, CI feedback, merge conflicts, agent prompts (your own instructions per agent, with the built-in prompt shown read-only). |
| Harnesses | Enable or disable an agent harness; edit its image, command and hosts; set its key. Routes and roles choose among the enabled ones. |
| Credentials | GitHub token, Claude credential (subscription token or API key), OpenRouter key, Telegram bot token. Write-only. GitHub and OpenRouter have a Test button. |
| Telegram | Your chat id (with *Find my chat id*), a test message, and how chatty it is: a level, or an explicit list of events. |
| Labels | Add, remove or swap labels on issues, one repository at a time (filter by state, label, title or `#number`). Only labels that already exist in the repository can be applied. Writes happen on the server with the stored GitHub token, so GitHub shows them as the factory's account; the factory trusts that account for triggers when it has write access. The write is applied directly (not via the read-only database), and logged to the UI log. |

### How a change is applied

- Settings are saved to `config.overrides.toml` **next to** your `config.toml`, which is never rewritten (your comments
  survive). The overrides are deep-merged over it at load. Delete a key from the overrides to fall back to the file.
- Every save is **validated by loading the merged result exactly as the orchestrator will**, before anything is written.
  A value equal to what the file already says is not stored. The previous overrides are kept as `.bak`.
- The UI then drops a `RESTART` marker. The orchestrator re-executes itself at its next idle moment, **never in the middle
  of a task**, so settings, credentials and classifier choices are all picked up the same way. The egress proxy re-reads
  its allowlist by itself.
- Risky changes need a confirmation tick: going live, widening the sandbox's allowed hosts, enabling a harness or changing
  its command, image or hosts.

## Security

The UI can change what the factory does and what it can reach, so:

- A password (scrypt), per-session CSRF tokens on every POST, `HttpOnly` + `SameSite=Strict` cookies, login throttling,
  a Host-header allowlist (DNS-rebinding defence), a strict Content-Security-Policy, and no framing.
- Dashboards open the database **read-only**. Everything dynamic is HTML-escaped: ticket text, agent output and logs are
  untrusted. Only `https://github.com/...` links are rendered as links.
- **Secrets are write-only**: stored 0600 and never rendered back, not even masked. A page shows "set" and when it changed.
  Error messages from outbound checks have the token scrubbed.
- The UI has no shell and starts no processes. It writes validated settings and credential files, and (on the Tickets page) adds and
  removes issue labels through GitHub as the factory's account. That needs the GitHub token, which the UI reads on the server and never
  sends to the browser. Because the write-access check accepts the factory's account, applying a trigger label here **starts work**.
- It needs write access to the config directory, the state directory and the secrets directory. It does **not** need the
  container engine socket or the work directory, and the compose service does not mount them.
- Anyone who can sign in can replace your credentials, so treat the password like one. Keep it off the open internet.

## Tickets: start work with a button

`/tickets` lists the open issues of a configured repository with their labels and the factory's latest decision. Each row has
buttons for what can be started right now: **Auto**, the stages not yet done (**Analyze**, **Design**, **Architect**), **Build**,
and **Review** (when enabled and a PR is open). A button applies the matching trigger label as the factory's account, and the
orchestrator starts the work at its next poll. A ticket that is running or queued shows its status instead of buttons. The browser
only names an action; the label comes from config, and the ticket is re-read before the label is applied.

**Needs a person.** When the factory has asked a person (the Telegram prompt), the same choices appear on the ticket row: the
recommended stage, **Build anyway**, and **Skip**. They work from the labels, not the database, so they behave the same whichever
place you answer: a stage or build applies its label and clears the trigger labels; Skip clears them and the factory leaves the
ticket alone.

## Reaching the UI from your LAN

By default the UI listens on loopback only. To open it from other machines on a trusted home network, add a systemd drop-in
(`~/.config/systemd/user/factory-ui.service.d/lan.conf` for the factory user) that replaces `ExecStart`:

    [Service]
    ExecStart=
    ExecStart=/usr/bin/python3 -m factory.ui --config /srv/factory/config.toml --listen 0.0.0.0:8787 --allowed-host <ip> --allowed-host <hostname>

Then `systemctl --user daemon-reload && systemctl --user restart factory-ui`. Requests whose `Host` header is not on the list get a
421, and failed logins are rate-limited per client address. The connection is plain HTTP, so on the LAN the password and session
cookie are readable by anyone who can sniff that network. Use it only on a network you trust, or put TLS in front (and start the UI
with `--secure-cookie`). Do not forward the port to the internet.

## The Floor (default page)

`/` is a live picture of the factory: tickets travel along belts through poll, trust gate, classify and the stages to pull requests,
CI and conflict handling. Running stations glow and show a progress bar; a crate carries the ticket number. Click a station to see
what it does, what it is working on, its numbers for the last day and its recent runs (`/?station=architect`; the selection
survives the page's 5-second refresh). Under the map, **Needs you** lists the tickets waiting for a person with the same buttons as
Telegram (Run <stage>, Build anyway, Skip, or Accept recommendations for stage questions).

The map is read-only: stations are links, not controls, so pause and reroute can be added later without redrawing it. On a phone (under 760px, tested at 360-430px) the
navigation becomes a bottom tab bar (Factory, Needs you with a count badge, Tickets, and **More** for Runs, PRs & CI, Events, Settings and the
rest; every target is at least 44px) and the Factory page shows a phone screen instead of the map: health dot, a title and sentence, three
tiles (working, need you, PRs open), a card for what is running with a progress bar, the pipeline as a vertical list (Intake, Analyst,
Designer, Architect, Build, PRs and CI, each with a state dot and word) and the top Needs-you ticket with *Review →*. The belt diagram,
the pause button and the long tables are desktop-only. On the Needs you screen each ticket is a card with full-width buttons (the primary
action first, then two side by side) and a back link. Tables on the other pages become stacked cards. It is all CSS media queries over
server-rendered markup, so it works without JavaScript, and animation is switched off under `prefers-reduced-motion`. The theme is dark only, using system fonts and no inline styles (the page's CSP forbids them).

**What the buttons do.** *Auto* and *Review* apply their trigger label (the classifier decides). *Build*, *Build anyway* and the stage
buttons queue a person's approval in the factory's database, the same row the Telegram buttons write, and the factory runs the ticket
at its next poll without asking the classifier again (a label would send it back through the classifier, which can answer
"needs a person" again). While an approval is waiting the ticket shows *starting* and has no buttons.

**Where you land.** An action returns you to the page you clicked from (the Floor, with the same station selected, or the Tickets page
with the same filters) and the result is shown there once. The message is kept on the server for your session; nothing in the URL can
make a page say something. The return address is checked against the Floor and Tickets pages only.

**Finishing on the Floor.** A ticket with open questions shows its question cards right in the *Needs you* tray: one button per option, a
free-text answer, and *Accept recommendations*. A ticket the factory asked about shows Run <stage> / Build anyway / Skip there too. Nothing
sends you to another page, and the live refresh pauses while you are typing or a field has focus, so an answer in progress is never lost.

## Needs you (page and Floor section)

`/needs` (also the section at the bottom of the Floor, and a tab with a count badge on phones) lists every ticket waiting for a person,
newest first, with filters for *Questions* and *Decisions*. Each row says who and why, and puts the recommendation first:

- **A decision** (the factory was unsure): the reason, a confidence meter, and *Run <stage>* (recommended) / *Build anyway* / *Skip*.
- **Questions** from a stage: how many need a person and how many were safe defaults. *Accept recommendations* records the
  recommended option for every question not yet answered. *Answer* opens the questions: an option per question (the recommended one is
  tagged), or your own words, then **Send answers**, which records what you chose (the button counts how many questions have an
  answer). A ticket with more than two questions opens them in a popup; with scripts off, the form is on the Tickets page. The Tickets list always uses the popup, never an inline row.
- **Accept recommendations on N tickets** (two or more tickets with questions) first shows exactly which answer each question will
  get, then asks you to confirm. Safe defaults are never listed because the factory has already accepted them.

**Reading the stage document.** Each question card has a *Read the analysis* (or design, architecture) link that opens `/ticket/doc?repo=&n=&stage=` in a
new tab, so an answer in progress is never lost. The page shows the stage's latest document from the stored run output, and fetches the factory's own
stage comment from GitHub when the stored text was cut short or is missing. It is rendered from a small escaped Markdown subset (raw HTML is
shown as text, no images, links only to github.com) without the questions block. The ticket view (`/ticket`) and the run page link to it too.
The repository must be configured and the document is read only from stored data, never from the request.

Everything is checked on the server like the Tickets page's answers: the repository must be configured, ticket numbers are validated,
and each ticket's questions are re-read from GitHub, so a page that is out of date cannot record a stale answer.
The page refreshes itself, but never while a popup is open, a field is focused, or an option is chosen.
