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
| Tickets | The latest decision per ticket, including *ignored* ones and why (for example the label was applied by someone without write access), and a `done/total` pipeline-steps count. Each count opens the ticket's read-only pipeline view (stations for analyze, design, architect, implement, review, CI fix: status, agent, attempts, run and PR links), built from the run history. |
| PRs & CI | Pull requests being watched, CI status, fix rounds used. |
| Events | The full timeline: decisions, every alert (including ones Telegram did not send), run starts and ends, errors, restarts. |

## What you can change

| Page | Changes |
|---|---|
| Settings | Poll interval, dry run (go live), confidence threshold, labels, who may apply them, repositories, projects, routing (tier → harness/model/effort), role agents, classifier (Jev or labels, label → kind aliases, with a **Try it** box), the code reviewer (on/off, automatic or by label, model, harness), sandbox limits and allowed hosts, CI feedback. |
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
