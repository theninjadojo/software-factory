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
| Factory | The dashboard: health and mode (with the last poll), pause/resume, five tiles (Needs you, Working now, PRs & CI, Done today, Failed) that open Tickets filtered, the floor (a working plant laid out from the config, west to east: the mainland, where tickets you add with *Add a ticket* are loaded into a 747 that flies across the sea to the plant's cargo airfield and offloads into Receiving; the sea, where each enabled schedule's ship sails a round trip to its source, placed at its share of the time since its last run, and docks at Receiving when the next run is due; then the plant. GitHub is a source whose drones fly new issues to Receiving when the poll comes round. The stations are small buildings grouped into districts, each with its own belts: Intake (Poll, Classify, Route, with a splitter to the queue chest), Planning (a loop past each stage role), Production (Build), Quality (Review and CI when they are on) and Shipping (the pull request); splitters, a side-loaded bypass and underground belts join them, and CI's fix rounds ride a return belt back to Build. Every station has an inserter that takes its ticket's crate off the belt and one that puts it back; crates back up at a station waiting for a person and shake at one that failed; each station opens Tickets at that station. A power station stands for each enabled AI agent harness, lit while one of its runs is going, with lines to the stations whose agents use it. Below, the Verify yard: each verification worker has a platform and a train of a few cars; a worker with a job sends its train round its loop and back, signals letting one train onto the shared track at a time. The floor fills the width of the page and scrolls sideways when the window is narrower), the Needs-you tray (answer, decide, or accept every recommendation), what is running now and the Claude plan usage. Refreshes itself. |
| Runs | Every agent run: ticket, model and effort, classification, status, duration, token counts (in includes cache reads and writes; a dash when the harness reported none, with a total for today above the list), PR links. A run's page has the classifier's answer, the output document and the agent log tail. |
| Tickets | Every ticket the factory has worked on, with chips for Needs you, Working, PRs & CI, Failed, Done and All (with counts), a station filter, sort and search (a number finds that ticket). Each card shows why it is where it is and a ten-step progress strip. The selected ticket opens beside the list (its own address is `/ticket?repo=&n=`): status tiles, its open questions answered in place, its journey on the same stations as the floor (a crate rides into the working station; when a worker checks the build, a small railway runs from Build to it and its train drives while the worker has the job), the design mockups, every step with agent, time, tokens and run link, its pull requests with their checks and fix rounds, and its activity. On a phone the list and the ticket are two screens. The label table (start, build, skip, edit labels by hand) is at `/labels`. |
| PRs & CI | Not a page of its own: the **PRs & CI** chip on Tickets lists the pull requests being watched (CI status, fix rounds used), and each ticket's page has its own *Pull requests and checks* section. `/prs` redirects there. |
| Events | The full timeline: decisions, every alert (including ones Telegram did not send), run starts and ends, errors, restarts. |
| Screens | Every screen listed under `[[screens.pages]]`, grouped by `journey` in `step` order, each shown as designed (its `design` canvas, rendered) and as built on its repository's default branch, at the viewport you pick. The orchestrator takes the shots in the same sealed containers as a build, on the `board_every` timer or when you press *Refresh now*; a shot is kept by commit, and an older one stays while an open note is on it. Click a screen to mark it up in the same review tool as a ticket's screens (`/screens/review`); those notes belong to the board, not a ticket, and each card shows how many are open. *Turn into a ticket* creates a GitHub issue from the notes you tick (each note quoted with its screen, commit and area) and moves them onto it, so the new ticket's review page shows them and its design run gets them with their screens; it can also start the ticket (Auto, or straight to the designer). For repos listed under `[[screens.captures]]` the top of the board has **Playwright runs**, grouped by project: *Build screens* (per project, or *Build all*) queues the project's Playwright suite on a verification worker, and the full-page screenshot of every test it ran appears under the repo when it finishes (also when some tests fail), with the commit, how long it took, which worker ran it and a link to the log. The screens of a finished run are kept as board screens, grouped by page and viewport (`home-desktop`, `home-tablet`, `home-phone` become the page *home* at three viewports): a thumbnail opens the review tool, and **Open all on the canvas** (`/screens/canvas?repo=`) shows every screen of the repo's newest run on one surface, a row per page with its viewports side by side, with Small/Medium/Large zoom and each screen's open-note count. Click a screen, drag over an area (or click for a pin), write what should change, and *Turn into a ticket* creates the GitHub issue from the notes you tick, exactly as for any board screen. A note keeps its screen after a newer run replaces it. The page updates itself while a run is waiting or running. The *Set up Playwright runs* box on the same tab does the whole setup: a checklist (workers on, a worker with the recipe online, repositories chosen) and the tick boxes for the repositories; nothing is edited in a file. See `docs/workers.md`, *Playwright screens*. |

## What you can change

The top bar has four tabs: Factory, Tickets (with the count of tickets that need you), Events and Settings. Needs you, Runs and PRs & CI are part of Tickets: those pages keep their URLs and light up the Tickets tab, the Tickets page has *Needs you* and *PRs & CI* chips, and a ticket's own page shows its open questions, its journey, every run and its pull requests with their checks in one place. Crates ride the belts on the journey map into the station that is working. Settings opens on an overview of every feature, grouped by what it is for (where work comes from, how it is done, the checks before a pull request, keeping you in the loop, running the factory). Each card says whether the feature is on, off or needs set-up, which screens it changes, and links to where it is set; simple on/off features (local tickets, CI feedback, code review, merge conflicts, project manager) switch right there after a confirmation. A search box and On / Off / Needs set-up filters narrow the list. The screens a setting shapes say so in a strip: Tickets shows where tickets live, the build label, schedules and local tickets (switchable there, and New ticket links to it while it is off); the Factory shows the mode, whether it takes new work, agents at once (with − and +) and where it tells you, and lists the features that are off; a ticket shows how it is handled (its size, model and effort, harness, stages and checks). Settings also has a side list (Overview, General, Routing, Role agents, Projects, Harnesses, Credentials, Telegram, Slack, Labels, then the remaining sections); the Harnesses, Credentials, Telegram and Slack pages keep their URLs and forms and render inside it, with the Settings tab highlighted.

The General settings have *Work from GitHub issues* (`github.issues_enabled`). Off, the factory does not poll GitHub issues, take chat approvals for them, run schedules, mirror sub-issues or import; jobs already running finish, and pull requests and CI still use GitHub. GitHub tickets are also hidden from Tickets, the Factory page and the Needs-you tray (nothing is deleted; turn it on again to see them), and a direct link to one says it is hidden. A config without the key keeps working from GitHub issues; `config.example.toml` shows it off. With it and local tickets both off the factory logs a warning and has nothing to work on.

| Page | Changes |
|---|---|
| Settings | Poll interval, dry run (go live), confidence threshold, labels, who may apply them, repositories, projects, routing (tier → harness/model/effort), role agents, classifier (Jev or labels, label → kind aliases, with a **Try it** box), the code reviewer (on/off, automatic or by label, model, harness), sandbox limits (including agents at once) and allowed hosts, design mockups, CI feedback, merge conflicts, agent prompts (your own instructions per agent, with the built-in prompt shown read-only). |
| Harnesses | Enable or disable an agent harness; edit its image, command and hosts; set its key. Routes and roles choose among the enabled ones. |
| Credentials | GitHub token, Claude credential (subscription token or API key), OpenRouter key, Telegram bot token, Slack bot and app tokens (also on the Slack page). Write-only. GitHub, OpenRouter and both Slack tokens have a Test button. |
| Telegram | Your chat id (with *Find my chat id*), a test message, and how chatty it is: a level, or an explicit list of events. |
| Slack | The whole set-up: a link that creates the Slack app from its manifest, the bot and app-level tokens (each with a Test button), whether the factory is connected, the people who typed `/factory` (*Use this* sets your member id and the channel), the channel and member ids by hand, the UI address for *Open in UI*, how chatty it is, and a test message. |
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

**New ticket.** The *New ticket* form (and the floor's *Add a ticket*) has a **Start** choice: *Just open the ticket* (the default, no labels), **Auto**, or one of the stages (**Analyze**, **Design**, **Architect**). Build is not offered. The browser names a choice; the server looks it up in the configured list and sends that one label with the new issue, so a forged value is refused and nothing is created.

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

Stations on the floor are links into Tickets, not controls. **Edit layout** (under the floor, wide screens only) opens `/floor/edit`, where a signed-in admin can drag the buildings (stations, Sources, Receiving, the queue, the power stations, the airfield, the yard, the mainland and the sea) on a 20px grid, move a district (its stations go with it) or resize it by its corner (it always holds its stations, and grows when one is moved out), resize any building but the yard by its corner (Shift+arrows from the keyboard; each has a minimum size; a building keeps its proportions, and an area (the mainland, the sea, the airfield) may take any shape, its picture scaled evenly in the middle and the rest filled with its ground, so no text is ever squashed; pieces on a belt move with it, and one a move leaves off its belt is removed), draw the belt for each hop of the route, place splitters, mergers, side-loads and underground belts, draw walls and plant trees (scenery: they block nothing), undo and redo (Ctrl/Cmd+Z, Shift+Z), and save or reset to the default. The editor fills the screen (or goes full screen), shows each building's own picture, pans by dragging the ground (or with the middle button) and zooms with its buttons or Ctrl and the wheel. Each hop needs a belt from beside the station a ticket leaves to beside the one it reaches, so every station stays reachable; the server checks this on save and refuses a save made over a newer one from another tab. The layout is shared by everyone, survives restarts and deploys (it is a file in the state directory), and each save writes a line to the UI's log. The sea's dock and crane follow Receiving. Moving a station changes the picture, not the workflow. The default floor (what shows until a layout is saved, and what the editor opens) is spread out: the stations stand in islands (intake, planning, production, quality and shipping, the yard and the workers) with open ground between them, and the ground is filled in with two parks (a fenced one with a gate), ponds and a lake, forest, roads (track crosses them on bridges), lamps and a hazard zone. It is laid round the buildings, belts and track in a fixed order, so it is always the same and never touches a belt. A station, agent or worker added in the config gets a default spot until it is placed, and a layout that no longer fits is ignored (the floor is drawn as before). Without JavaScript the page is a form of the layout's JSON. On a phone (under 760px, tested at 360-430px) the navigation moves into the header's menu button (Factory, Tickets with the needs-you count, Events, Settings and Sign out; every target is at least 44px). The dashboard's tiles and cards stack and the floor becomes a column of its stations joined by belts (the map, its trains and drones are for wider screens); Tickets opens as the list (led by a card of what needs you, with decision buttons on the cards) and a ticket as its own screen, whose journey is a numbered list of the ten stations with the mockups in the Designer step.

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

**Reviewing the screens.** *Review the screens* (on the ticket, the design document and the images page) opens `/ticket/review?repo=&n=`:
the latest design run's mockups and the latest run's screenshots, one at a time. Drag over an area to mark it, or click for a pin (on a
phone, tap; the area can also be typed in percentages under *Set the area by numbers*), then write what should change and **Add note**.
Notes are numbered and drawn on their screen; click one to highlight its area, or delete it while it is open. **Send N notes to the
designer** queues a design run (the same approval as the stage's Run button; refused while the factory works on the ticket or when it is
closed). Every design run takes the ticket's open notes, whether started here or not: the screens they are on are put in its task folder,
it is told each area in the image's own pixels, and its document gets a *Review notes* section answering each note by number. Once the
run has its document the notes are kept under *Sent earlier* and can no longer be deleted; a failed run leaves them open. A note can only
name an image the ticket has, its text (up to 1000 characters, 30 open notes per ticket) reaches the agent as quoted data. Saving a note is a small write to the
factory's database, like a queued approval.

Everything is checked on the server like the Tickets page's answers: the repository must be configured, ticket numbers are validated,
and each ticket's questions are re-read from GitHub, so a page that is out of date cannot record a stale answer.
The page refreshes itself, but never while a popup is open, a field is focused, or an option is chosen.
