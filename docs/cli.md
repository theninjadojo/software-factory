# Command-line access

The factory can be managed from a terminal with the `shikumi` command-line client. You can see what it is doing, read tickets and
their documents, answer its questions, and start, send back, close or create tickets. You don't need a browser, and one client
can talk to several factories. Settings are only changed in the admin UI.

The client talks to the factory's JSON API, which the admin UI serves under `/api/v1/` on the same port.

## Install

Each release has a static Linux x86_64 binary, `shikumi-cli-x86_64-unknown-linux-musl`, with its `.sha256`, on the release page.
It needs nothing else on the machine.

    curl -fLO https://github.com/theninjadojo/software-factory/releases/latest/download/shikumi-cli-x86_64-unknown-linux-musl
    curl -fLO https://github.com/theninjadojo/software-factory/releases/latest/download/shikumi-cli-x86_64-unknown-linux-musl.sha256
    sha256sum -c shikumi-cli-x86_64-unknown-linux-musl.sha256
    install -m 755 shikumi-cli-x86_64-unknown-linux-musl ~/.local/bin/shikumi

Elsewhere, build it from the repository with Rust: `cd cli && cargo build --release` gives `cli/target/release/shikumi`.
The client's version is the factory release it came with (`shikumi --version`). Any client works with any factory that speaks
the same API version (`shikumi version` warns when they differ).

## Set up

1. On the factory, make a token (see [Tokens](#tokens)).
2. Reach the factory from this computer (see [Reaching the factory](#reaching-the-factory)). With an SSH tunnel:
   `ssh -N -L 8787:127.0.0.1:8787 <user>@<factory-host>`.
3. Add it, and paste the token when it asks:

       shikumi instance add home --url http://127.0.0.1:8787 --repo your-org/app

   `--repo` is the repository a short ticket reference (`#12`, `L-3`) means. `--token-stdin` reads the token from standard input
   instead, for scripts. The client checks the address and the token before it saves them, in `~/.config/shikumi/cli.toml`
   (mode 0600; `$SHIKUMI_CONFIG` or `$XDG_CONFIG_HOME` move it). It refuses to read a file that others can read.

Add more factories the same way. The first is the default. `shikumi instance default <name>` changes it, and `-i <name>` picks
one for a single command. `shikumi instance list` shows them, `instance test` checks one, and `instance remove` forgets one
(revoke its token on the factory too). For scripts, `SHIKUMI_URL` with `SHIKUMI_TOKEN` names a factory without the file,
`SHIKUMI_TOKEN` alone replaces the chosen factory's token, and `SHIKUMI_INSTANCE` picks one. There is no `--token` flag, so a
token never ends up in your shell history.

The client refuses plain `http` to any machine but this one: the token would cross the network unencrypted. Use an SSH tunnel or
https, or pass `--allow-insecure-http` on a network you trust.

## Commands

Tickets are named `owner/repo#12` or `owner/repo#L-3` (a local ticket), or `#12` / `L-3` when the factory has one repository or
the instance has `--repo`.

| Command | What it does |
|---|---|
| `shikumi status` | Live or dry run, paused, healthy, what is running and queued, what needs you, today's runs, Claude plan use |
| `shikumi pause`, `resume` | Stop taking new work (running work finishes), and take it again |
| `shikumi tickets [-s needs\|working\|prs\|failed\|new\|done\|all] [-p project] [-q text] [--sort ...] [--at station] [--fresh]` | The Tickets page as a table |
| `shikumi ticket show <ticket> [--events] [--cached]` | Where it is, what it needs (for a decision, why the classifier did not go on by itself: each answer's score against the threshold), what it can start, its PRs, its steps with models, time and tokens, and a local ticket's text and comments |
| `shikumi ticket doc <ticket> <analyst\|designer\|architect>` | A stage's document |
| `shikumi ticket create --title ... [--body ... \| --body-file F\|-] [--repo ...] [--start auto\|<stage>] [--where local\|github]` | Open a ticket; nothing starts unless `--start` says so |
| `shikumi ticket start <ticket> [action]` | Start work. Without an action, choose from what the ticket offers (`auto`, a stage, `build`, `review`, `conflicts`, or a decision's `stage:<name>` / `skip`) |
| `shikumi ticket send-back <ticket> <stage>` | Run a stage that has run again, and the stages after it |
| `shikumi ticket close <ticket>` | Close it; work on it stops (asks first) |
| `shikumi ticket reopen\|comment\|edit <ticket> ...` | A local ticket: reopen, comment (`--body` or `--body-file`), change the title or description |
| `shikumi ticket priority <ticket> high\|normal\|low\|pm` | Pin a priority, or let the project manager decide |
| `shikumi needs` | Tickets waiting for you: questions to answer, or a decision to make |
| `shikumi answer <ticket>` | Answer the open questions one at a time: Enter takes the recommendation, a number picks an option, `o` writes your own, `s` skips. Then you confirm |
| `shikumi answer <ticket> --pick Q=OPTION --other Q=TEXT` / `--accept-all` | The same without questions asked, for scripts |
| `shikumi answer-all <ticket>... \| --all` | Accept the recommendations on several tickets |
| `shikumi runs [-s status] [-r repo] [-p page]`, `shikumi run <id> [--output] [--log]` | Agent runs, and one run with its output and the end of its log |
| `shikumi events [-k important\|all\|alerts\|prefix] [--before id]` | What happened, newest first |
| `shikumi pr merge <owner/repo#N>` | Merge a factory pull request whose checks passed (asks first) |

Every command takes `--json`, which prints the factory's JSON for scripts, and `-y` to skip the question before closing or
merging (it is required when there is no terminal to ask in). Ticket text is untrusted, so the client removes control characters
and bidirectional overrides before printing anything the factory sent, including inside `--json`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done |
| 1 | A problem on this machine (the config file, the address) |
| 2 | The command was written wrong |
| 3 | The token was refused |
| 4 | No such ticket, run or document |
| 5 | The factory refused: the ticket's state does not allow it now, or a value it cannot use (the message says which) |
| 6 | The factory could not be reached, failed, or has no API (too old) |

## Tokens

Each computer gets its own token. A token can read and write, or only read.

- **In the UI:** **Settings → Credentials → Command-line access**. Give it a name (for example `work-laptop`), tick *Read only*
  if it should only look, and enter the UI password. The token is shown once, with the commands to add the factory to the
  client. The same card lists the tokens, when each was made and last used, and revokes one at once.
- **On the factory host:** `python3 -m factory.ctl tokens add <name> [--read-only]` prints a new token. `tokens list` shows
  them, and `tokens revoke <name or id>` removes one. In Docker:

      docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml ui python3 -m factory.ctl tokens add work-laptop

Tokens start with `shk_`. The factory keeps only a hash of each (in `state/api-tokens.json`), so a lost token cannot be shown
again: revoke it and make a new one. A change is picked up at once, with no restart. With no tokens, the API refuses every request.

## Reaching the factory

The UI, and with it the API, listens on loopback by default. From another computer:

- **SSH tunnel** (simplest): `ssh -N -L 8787:127.0.0.1:8787 <user>@<factory-host>`, then use `http://127.0.0.1:8787`.
- **VPN or LAN:** start the UI with `--listen 0.0.0.0:8787 --allowed-host <name>` (see [ui.md](ui.md#reaching-the-ui-from-your-lan)).
  Plain HTTP on a network exposes the token like the password, so use a network you trust.
- **A reverse proxy with TLS** (Caddy, nginx): use its `https://` address. The name must be on the UI's Host allowlist
  (`--allowed-host` or `FACTORY_UI_ALLOWED_HOSTS`). Otherwise every request gets `421`.

## API v1 reference

Every request needs `Authorization: Bearer <token>`. A write is a `POST` with a JSON object body (`Content-Type:
application/json`). Responses are JSON. Ticket text (titles, bodies, comments, agent output) is returned exactly as stored. It is
untrusted, and a client should strip control characters before showing it in a terminal.

A ticket is addressed as `/api/v1/tickets/<owner>/<name>/<number>`. A local ticket uses its full number (`L-3` is `100000003`), and
every ticket also comes with its `ref` (`#12` or `L-3`).

| Method | Path | What it does |
|---|---|---|
| GET | `/api/v1/version` | `{api, factory, token: {name, scope}}` |
| GET | `/api/v1/meta` | Repositories, projects, stages, start options, list filters, stations and priorities |
| GET | `/api/v1/status` | Live or dry run, paused, healthy, running and queued work, the Floor's tiles, ticket counts and Claude plan use. The *Needs you* count comes from GitHub: when the last read is more than two minutes old the request reads it again (`needs_known` is false only when GitHub could not be read, and the count is then the factory's own, possibly stale, record) |
| POST | `/api/v1/pause`, `/api/v1/resume` | Pause or resume the factory (work in progress finishes) |
| GET | `/api/v1/tickets` | The ticket list. Filters: `stage` (`needs`, `working`, `prs`, `failed`, `new`, `done`, `all`), `project`, `q`, `sort` (`latest`, `oldest`, `priority`), `at` (a station), `age` (`recent`, `older`, `all`; default `all`), `limit`, `fresh=1` (ask GitHub now). The reply adds `age`, `archive_days`, `age_counts` and a per-ticket `archived` |
| POST | `/api/v1/tickets` | Create a ticket: `{repo, title, body?, start?, where?: "local" \| "github"}` → `201` with `issue` and `ref` |
| GET | `/api/v1/tickets/<o>/<r>/<n>` | One ticket: its journey, what it needs (questions, or a decision with the classifier's `scores` and a one-line `why`), PRs, stage documents, offered actions, outcome, and a local ticket's body and comments. `cached=1` uses the last GitHub read |
| GET | `.../<n>/docs/<stage>` | A stage document (`analyst`, `designer`, `architect`) as text |
| POST | `.../<n>/start` | `{action}`: one the ticket offers (`auto`, a stage name, `build`, `review`, `conflicts`, `stage:<name>`, `skip`) |
| POST | `.../<n>/send-back` | `{stage}`: run that stage and the later ones again |
| POST | `.../<n>/answer` | `{stage, answers: {question id: {"option": id} \| {"other": text}}}` or `{accept_all: true}` |
| POST | `/api/v1/answer-all` | `{tickets: ["owner/name#n", ...]}` (at most 20): accept the recommendations on each |
| POST | `.../<n>/close` | Close a ticket (work in progress on it stops) |
| POST | `.../<n>/reopen`, `comment` `{body}`, `edit` `{title?, body?}` | Local tickets only |
| POST | `.../<n>/priority` | `{priority}`: `high`, `normal`, `low`, or `pm` to let the project manager decide |
| GET | `/api/v1/needs` | Tickets waiting for you, with their questions or choices (read from GitHub) |
| GET | `/api/v1/runs`, `/api/v1/runs/<id>` | Runs (`status`, `repo`, `page`); one run with its output and log tail |
| GET | `/api/v1/events` | Events (`kind`: `important`, `all`, `alerts` or a prefix; `before` an id for the next page) |
| POST | `/api/v1/prs/<o>/<r>/<n>/merge` | Merge a factory PR whose checks passed |

A successful write answers `{"ok": true, "message": ...}`, with the same message the page would show. An error answers
`{"error": message, "code": word}`, plus `fields` for a value that cannot be used:

| Status | `code` | When |
|---|---|---|
| 400 | `bad_json` | The body is not a JSON object |
| 401 | `unauthorized` | No token, or not a valid one |
| 403 | `read_only` | A read-only token tried to change something |
| 404 | `not_found` | No such path, ticket, run, document or repository |
| 405 | `method` | GET on a write, or POST on a read |
| 409 | `refused` | The ticket's state does not allow it now (the message says why) |
| 409 | `no_github_token` | The factory has no GitHub token for this (questions and merging need one, even for local tickets) |
| 411, 413, 415 | | No `Content-Length`, a body over 64 KB, or not JSON |
| 422 | `invalid` | A value that cannot be used |
| 429 | `throttled` | Too many wrong tokens from this address (wait five minutes) |
| 502 | `github` | GitHub failed or refused |
| 503 | `no_database` | The orchestrator has not created its database yet |

Within v1, fields are only added, never removed or renamed. A factory older than the API answers `/api/...` with a redirect to
`/login`.
