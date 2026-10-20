//! What each command does: one or two API calls, then a table or a block of text (or the factory's JSON with --json).

use crate::api::*;
use crate::cli::*;
use crate::client::{check_url, ApiError, Client, API, VERSION};
use crate::config::{valid_name, Config, Env, Instance, Target};
use crate::output::{ago, block, cut, duration, now, table, tokens, Cell, Style};
use crate::prompt::{self, Answer};
use crate::refs::{self, TicketRef};
use crate::sanitize::{clean_line, escape_json_text};
use anyhow::{anyhow, bail, Context, Result};
use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use std::fmt;
use std::io::{self, IsTerminal, Read};
use std::path::Path;

/// A command that was written wrong (exit code 2).
#[derive(Debug)]
pub struct Usage(pub String);

impl fmt::Display for Usage {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for Usage {}

fn usage(msg: impl Into<String>) -> anyhow::Error {
    anyhow::Error::new(Usage(msg.into()))
}

struct Ctx {
    json: bool,
    yes: bool,
    style: Style,
    target: Target,
    client: Client,
}

fn interactive() -> bool {
    io::stdin().is_terminal() && io::stdout().is_terminal()
}

fn print_json(v: &Value) {
    println!(
        "{}",
        escape_json_text(&serde_json::to_string_pretty(v).unwrap_or_default())
    );
}

impl Ctx {
    /// GET a path: printed as JSON with --json (and None returned), else read as T.
    fn get<T: DeserializeOwned>(&self, path: &str, query: &[(&str, String)]) -> Result<Option<T>> {
        let v: Value = self.client.get(path, query)?;
        if self.json {
            print_json(&v);
            return Ok(None);
        }
        Ok(Some(serde_json::from_value(v).context(
            "the factory's reply was not what this client expects; update the client",
        )?))
    }

    /// POST and say what the factory said (or print its JSON).
    fn act(&self, path: &str, body: Value) -> Result<Done> {
        let v: Value = self.client.post(path, &body)?;
        if self.json {
            print_json(&v);
        }
        let d: Done = serde_json::from_value(v).unwrap_or_default();
        if !self.json {
            println!("{}", clean_line(&d.message));
        }
        Ok(d)
    }

    fn default_repo(&self) -> Result<Option<String>> {
        if let Some(r) = &self.target.repo {
            return Ok(Some(r.clone()));
        }
        let meta: Meta = self.client.get("meta", &[])?;
        Ok(if meta.repos.len() == 1 {
            meta.repos.into_iter().next()
        } else {
            None
        })
    }

    fn ticket(&self, s: &str) -> Result<TicketRef> {
        let full = s.contains('/');
        let repo = if full { None } else { self.default_repo()? };
        refs::parse(s, repo.as_deref()).map_err(|e| usage(e.to_string()))
    }

    fn confirm(&self, question: &str) -> Result<()> {
        if self.yes {
            return Ok(());
        }
        if !interactive() {
            return Err(usage(format!(
                "{question} Add --yes to do it without asking."
            )));
        }
        if !prompt::confirm(&mut io::stdin().lock(), &mut io::stdout(), question)? {
            bail!("nothing was done");
        }
        Ok(())
    }
}

fn body_from(body: &Option<String>, file: &Option<std::path::PathBuf>) -> Result<Option<String>> {
    match (body, file) {
        (Some(b), _) => Ok(Some(b.clone())),
        (None, Some(f)) if f.as_os_str() == "-" => {
            let mut s = String::new();
            io::stdin().read_to_string(&mut s)?;
            Ok(Some(s))
        }
        (None, Some(f)) => Ok(Some(
            std::fs::read_to_string(f).with_context(|| format!("cannot read {}", f.display()))?,
        )),
        (None, None) => Ok(None),
    }
}

pub fn run(cli: Cli) -> Result<()> {
    let env = Env::from_process();
    let path = match &cli.config {
        Some(p) => p.clone(),
        None => env.config_path()?,
    };
    if let Command::Instance(cmd) = &cli.command {
        return instance(&cli, cmd, &path, &env);
    }
    let cfg = Config::load(&path)?;
    let target = cfg.resolve(cli.instance.as_deref(), &env)?;
    let base = check_url(
        &target.url,
        target.allow_insecure_http || cli.allow_insecure_http,
    )?;
    if base.insecure {
        eprintln!(
            "warning: plain http to {} sends the token unencrypted",
            base.url
        );
    }
    let ctx = Ctx {
        json: cli.json,
        yes: cli.yes,
        style: Style::detect(cli.no_color),
        client: Client::new(&base, &target.token),
        target,
    };
    match &cli.command {
        Command::Instance(_) => unreachable!(),
        Command::Version => version(&ctx),
        Command::Status => status(&ctx),
        Command::Pause => ctx.act("pause", json!({})).map(drop),
        Command::Resume => ctx.act("resume", json!({})).map(drop),
        Command::Tickets(a) => tickets(&ctx, a),
        Command::Ticket(t) => ticket(&ctx, t),
        Command::Needs => needs(&ctx),
        Command::Answer(a) => answer(&ctx, a),
        Command::AnswerAll(a) => answer_all(&ctx, a),
        Command::Runs(a) => runs(&ctx, a),
        Command::Run(a) => run_one(&ctx, a),
        Command::Events(a) => events(&ctx, a),
        Command::Pr(PrCmd::Merge { pr }) => merge(&ctx, pr),
    }
}

// ------------------------------------------------------------------ instances

fn read_token(from_stdin: bool) -> Result<String> {
    let token = if from_stdin {
        let mut s = String::new();
        io::stdin().read_to_string(&mut s)?;
        s
    } else if io::stdin().is_terminal() {
        rpassword::prompt_password(
            "Token (from the factory's Settings → Credentials → Command-line access): ",
        )?
    } else {
        return Err(usage(
            "give the token with --token-stdin, or run this in a terminal to be asked for it",
        ));
    };
    let token = token.trim().to_string();
    if !token.starts_with("shk_")
        || token.len() < 20
        || token.chars().any(|c| c.is_whitespace() || c.is_control())
    {
        return Err(usage(
            "that is not a factory API token (they start with shk_)",
        ));
    }
    Ok(token)
}

fn instance(cli: &Cli, cmd: &InstanceCmd, path: &Path, env: &Env) -> Result<()> {
    let mut cfg = Config::load(path)?;
    let style = Style::detect(cli.no_color);
    match cmd {
        InstanceCmd::Add {
            name,
            url,
            token_stdin,
            repo,
            default,
        } => {
            if !valid_name(name) {
                return Err(usage(
                    "a factory's name is lowercase letters, digits and dashes",
                ));
            }
            if let Some(r) = repo {
                refs::parse(&format!("{r}#1"), None).map_err(|_| {
                    usage(format!("{r:?} is not a repository: write it as owner/name"))
                })?;
            }
            let base = check_url(url, cli.allow_insecure_http)?;
            let token = read_token(*token_stdin)?;
            match Client::new(&base, &token).get::<Version>("version", &[]) {
                Ok(v) => println!("Connected: factory {} (API {}), token {} ({}).", clean_line(&v.factory), v.api, clean_line(&v.token.name),
                                  if v.token.scope == "write" { "read and write" } else { "read only" }),
                Err(e @ ApiError::Api { status: 401 | 403, .. }) => return Err(anyhow!(e).context("the factory refused the token; nothing was saved")),
                Err(e @ ApiError::NoApi(_)) => return Err(anyhow!(e).context("nothing was saved")),
                Err(e) => eprintln!("warning: could not check it now ({e}). Saved anyway; try `shikumi instance test {name}` later."),
            }
            let first = cfg.instances.is_empty();
            cfg.instances.insert(
                name.clone(),
                Instance {
                    url: base.url.clone(),
                    token: Some(token),
                    token_file: None,
                    repo: repo.clone(),
                    allow_insecure_http: base.insecure,
                },
            );
            if *default || first {
                cfg.default = Some(name.clone());
            }
            cfg.save(path)?;
            println!(
                "Saved {name} in {}{}.",
                path.display(),
                if cfg.default.as_deref() == Some(name) {
                    " (the default)"
                } else {
                    ""
                }
            );
            Ok(())
        }
        InstanceCmd::List => {
            if cli.json {
                let list: Vec<Value> = cfg
                    .instances
                    .iter()
                    .map(|(n, i)| {
                        json!({"name": n, "url": i.url, "repo": i.repo,
                    "default": cfg.default.as_deref() == Some(n.as_str())})
                    })
                    .collect();
                print_json(&Value::Array(list));
                return Ok(());
            }
            if cfg.instances.is_empty() {
                println!("No factory yet. Add one: shikumi instance add <name> --url <address>");
                return Ok(());
            }
            let rows: Vec<Vec<Cell>> = cfg
                .instances
                .iter()
                .map(|(n, i)| {
                    vec![
                        Cell::from(if cfg.default.as_deref() == Some(n.as_str()) {
                            "*"
                        } else {
                            ""
                        }),
                        Cell::bold(n),
                        Cell::from(i.url.as_str()),
                        Cell::from(i.repo.clone().unwrap_or_default()),
                    ]
                })
                .collect();
            print!(
                "{}",
                table(&style, &["", "name", "address", "repository"], &rows)
            );
            Ok(())
        }
        InstanceCmd::Remove { name } => {
            if cfg.instances.remove(name).is_none() {
                bail!("there is no factory called {name}");
            }
            if cfg.default.as_deref() == Some(name) {
                cfg.default = None;
            }
            cfg.save(path)?;
            println!("Removed {name}. Its token still works until it is revoked on the factory (Settings → Credentials).");
            Ok(())
        }
        InstanceCmd::Default { name } => {
            if !cfg.instances.contains_key(name) {
                bail!("there is no factory called {name}");
            }
            cfg.default = Some(name.clone());
            cfg.save(path)?;
            println!("{name} is the default.");
            Ok(())
        }
        InstanceCmd::Test { name } => {
            let t = cfg.resolve(name.as_deref().or(cli.instance.as_deref()), env)?;
            let base = check_url(&t.url, t.allow_insecure_http || cli.allow_insecure_http)?;
            let v: Version = Client::new(&base, &t.token).get("version", &[])?;
            println!(
                "{}: factory {} at {}, token {} ({}).",
                t.name,
                clean_line(&v.factory),
                base.url,
                clean_line(&v.token.name),
                clean_line(&v.token.scope)
            );
            if v.api != API {
                eprintln!("warning: the factory speaks API {} and this client {API}: update the one that is older", v.api);
            }
            Ok(())
        }
    }
}

// ------------------------------------------------------------------ the factory

fn version(ctx: &Ctx) -> Result<()> {
    let Some(v) = ctx.get::<Version>("version", &[])? else {
        return Ok(());
    };
    println!("client  {VERSION}");
    println!("factory {} ({})", clean_line(&v.factory), ctx.target.name);
    if v.api != API {
        eprintln!("warning: the factory speaks API {} and this client {API}: update the one that is older", v.api);
    } else if v.factory != VERSION && VERSION != "dev" {
        eprintln!("note: the client and the factory are different releases; both speak API {API}");
    }
    Ok(())
}

fn status(ctx: &Ctx) -> Result<()> {
    let Some(s) = ctx.get::<Status>("status", &[])? else {
        return Ok(());
    };
    let st = &ctx.style;
    let t = now();
    let mode = if s.live {
        st.state("done").replace("done", "live")
    } else {
        "dry run".into()
    };
    let run = match &s.paused {
        Some(p) => st
            .state("needs")
            .replace("needs", &format!("paused ({})", clean_line(p))),
        None => "taking work".into(),
    };
    let health = if s.healthy {
        format!("healthy, last poll {}", ago(s.last_poll, t))
    } else {
        st.state("failed").replace(
            "failed",
            &format!("not reporting, last poll {}", ago(s.last_poll, t)),
        )
    };
    println!(
        "{}  {mode} · {run} · {health} · {}",
        st.bold(&ctx.target.name),
        clean_line(&s.version)
    );
    let tl = &s.tiles;
    println!(
        "Working   {} run{}{}",
        tl.running,
        if tl.running == 1 { "" } else { "s" },
        if tl.working_at.is_empty() {
            String::new()
        } else {
            format!(": {}", clean_line(&tl.working_at.join(", ")))
        }
    );
    println!(
        "Needs you {} ({} with questions, {} to decide)",
        tl.needs, tl.asking, tl.deciding
    );
    println!(
        "PRs & CI  {} open · {} running checks · {} failing",
        tl.open_prs, tl.checks_running, tl.checks_failing
    );
    println!(
        "Today     {} passed of {} runs · {} failed ticket{}",
        tl.passed_today,
        tl.runs_today,
        tl.failed,
        if tl.failed == 1 { "" } else { "s" }
    );
    if let Some(Value::Object(u)) = &s.usage {
        let parts: Vec<String> = u
            .iter()
            .filter_map(|(k, v)| {
                v.get("used")
                    .and_then(Value::as_f64)
                    .map(|p| format!("this {} {:.0}%", clean_line(k), p))
            })
            .collect();
        let why = u
            .get("reason")
            .or_else(|| u.get("error"))
            .and_then(Value::as_str)
            .map(clean_line);
        match (parts.is_empty(), why) {
            (false, None) => println!("Claude    {}", parts.join(" · ")),
            (false, Some(w)) => println!("Claude    {} (last known; {w})", parts.join(" · ")),
            (true, Some(w)) => println!("Claude    usage unavailable: {w}"),
            (true, None) => {}
        }
    }
    if !s.running.is_empty() {
        println!();
        let rows: Vec<Vec<Cell>> = s
            .running
            .iter()
            .map(|r| {
                vec![
                    Cell::from(r.id.to_string()),
                    Cell::from(format!("{} {}", short_repo(&r.repo), r.reference)),
                    Cell::from(r.stage.clone().unwrap_or_else(|| r.kind.clone())),
                    Cell::dim(r.model.as_deref().unwrap_or("")),
                    Cell::from(duration(Some(t - r.started))),
                    Cell::from(r.title.as_str()),
                ]
            })
            .collect();
        print!(
            "{}",
            table(
                st,
                &["run", "ticket", "doing", "model", "for", "title"],
                &rows
            )
        );
    }
    if !s.queued.is_empty() {
        println!(
            "\nWaiting for a free agent: {}",
            s.queued
                .iter()
                .map(|q| format!("{} {}", short_repo(&q.repo), refs::display(q.issue)))
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    Ok(())
}

/// A time the factory sent as seconds (a run, an event) or as an ISO date (a comment), for people.
fn when_value(v: Option<&Value>, now_: f64) -> String {
    match v {
        Some(Value::Number(n)) => ago(n.as_f64(), now_),
        Some(Value::String(s)) => clean_line(&s.get(..16).unwrap_or(s).replace('T', " ")),
        _ => String::new(),
    }
}

fn short_repo(repo: &str) -> String {
    clean_line(repo.rsplit('/').next().unwrap_or(repo))
}

// ------------------------------------------------------------------ tickets

fn tickets(ctx: &Ctx, a: &ListArgs) -> Result<()> {
    let stage = a
        .stage
        .map(|s| format!("{s:?}").to_lowercase())
        .unwrap_or_default();
    let sort = a
        .sort
        .map(|s| format!("{s:?}").to_lowercase())
        .unwrap_or_default();
    let project = match a.project.as_deref() {
        Some("standalone") => "_standalone".to_string(),
        Some(p) => p.to_string(),
        None => String::new(),
    };
    let q = [
        ("stage", stage),
        ("project", project),
        ("q", a.query.clone().unwrap_or_default()),
        ("sort", sort),
        ("at", a.at.clone().unwrap_or_default()),
        ("limit", a.limit.to_string()),
        ("fresh", if a.fresh { "1".into() } else { String::new() }),
    ];
    let Some(list) = ctx.get::<TicketList>("tickets", &q)? else {
        return Ok(());
    };
    let t = now();
    if list.tickets.is_empty() {
        println!("No tickets ({}).", clean_line(&list.stage));
    } else {
        let rows: Vec<Vec<Cell>> = list
            .tickets
            .iter()
            .map(|r| {
                vec![
                    Cell::from(format!("{} {}", short_repo(&r.repo), r.reference)),
                    Cell::state(&r.state),
                    Cell::from(r.at_label.as_str()),
                    Cell::from(ago(r.when, t)),
                    Cell::from(if r.priority == "normal" {
                        String::new()
                    } else {
                        r.priority.clone()
                    }),
                    Cell::from(format!(
                        "{}{}",
                        r.title,
                        if r.why.is_empty() {
                            String::new()
                        } else {
                            format!(" — {}", r.why)
                        }
                    )),
                ]
            })
            .collect();
        print!(
            "{}",
            table(
                &ctx.style,
                &["ticket", "state", "at", "last", "prio", "title"],
                &rows
            )
        );
    }
    let counts: Vec<String> = ["needs", "working", "prs", "failed", "new", "done"]
        .iter()
        .filter_map(|k| list.counts.get(*k).map(|n| format!("{k} {n}")))
        .collect();
    println!(
        "{}",
        ctx.style.dim(&format!(
            "{} shown of {} ({}) · {}",
            list.tickets.len(),
            list.total,
            list.stage,
            counts.join(" · ")
        ))
    );
    Ok(())
}

fn ticket(ctx: &Ctx, cmd: &TicketCmd) -> Result<()> {
    match cmd {
        TicketCmd::Show {
            ticket,
            events,
            cached,
        } => show(ctx, &ctx.ticket(ticket)?, *events, *cached),
        TicketCmd::Doc { ticket, stage } => {
            let r = ctx.ticket(ticket)?;
            let Some(d) = ctx.get::<Doc>(&format!("{}/docs/{}", r.path(), stage), &[])? else {
                return Ok(());
            };
            println!(
                "{}",
                ctx.style.dim(&format!(
                    "{} {} · {} · {}",
                    r.repo,
                    r.display(),
                    d.stage,
                    d.source
                ))
            );
            if d.cut_short {
                eprintln!("note: this document was cut short when it was stored, and the full text could not be fetched from GitHub");
            }
            println!("{}", block(&d.text, ""));
            Ok(())
        }
        TicketCmd::Create {
            repo,
            title,
            body,
            body_file,
            start,
            place,
        } => {
            let repo = match repo {
                Some(r) => r.clone(),
                None => ctx
                    .default_repo()?
                    .ok_or_else(|| usage("which repository? add --repo owner/name"))?,
            };
            let body = body_from(body, body_file)?.unwrap_or_default();
            let place = match place {
                Some(Where::Local) => "local",
                Some(Where::Github) => "github",
                None => "",
            };
            ctx.act("tickets", json!({"repo": repo, "title": title, "body": body, "start": start.clone().unwrap_or_default(), "where": place})).map(drop)
        }
        TicketCmd::Start { ticket, action } => start(ctx, &ctx.ticket(ticket)?, action.as_deref()),
        TicketCmd::SendBack { ticket, stage } => {
            let r = ctx.ticket(ticket)?;
            ctx.act(&format!("{}/send-back", r.path()), json!({"stage": stage}))
                .map(drop)
        }
        TicketCmd::Close { ticket } => {
            let r = ctx.ticket(ticket)?;
            ctx.confirm(&format!(
                "Close {} {}? Work on it stops.",
                r.repo,
                r.display()
            ))?;
            ctx.act(&format!("{}/close", r.path()), json!({})).map(drop)
        }
        TicketCmd::Reopen { ticket } => {
            let r = ctx.ticket(ticket)?;
            ctx.act(&format!("{}/reopen", r.path()), json!({}))
                .map(drop)
        }
        TicketCmd::Comment {
            ticket,
            body,
            body_file,
        } => {
            let r = ctx.ticket(ticket)?;
            let body = body_from(body, body_file)?.ok_or_else(|| {
                usage("write the comment with --body, or --body-file (- for standard input)")
            })?;
            ctx.act(&format!("{}/comment", r.path()), json!({"body": body}))
                .map(drop)
        }
        TicketCmd::Edit {
            ticket,
            title,
            body,
            body_file,
        } => {
            let r = ctx.ticket(ticket)?;
            let mut v = serde_json::Map::new();
            if let Some(t) = title {
                v.insert("title".into(), json!(t));
            }
            if let Some(b) = body_from(body, body_file)? {
                v.insert("body".into(), json!(b));
            }
            if v.is_empty() {
                return Err(usage("give --title, --body or --body-file"));
            }
            ctx.act(&format!("{}/edit", r.path()), Value::Object(v))
                .map(drop)
        }
        TicketCmd::Priority { ticket, priority } => {
            let r = ctx.ticket(ticket)?;
            ctx.act(
                &format!("{}/priority", r.path()),
                json!({"priority": format!("{priority:?}").to_lowercase()}),
            )
            .map(drop)
        }
    }
}

fn show(ctx: &Ctx, r: &TicketRef, with_events: bool, cached: bool) -> Result<()> {
    let q = [(
        "cached",
        if cached {
            "1".to_string()
        } else {
            String::new()
        },
    )];
    let Some(t) = ctx.get::<Ticket>(&r.path(), &q)? else {
        return Ok(());
    };
    let st = &ctx.style;
    let now_ = now();
    let row = &t.row;
    println!(
        "{} {}  {}",
        st.bold(&row.repo),
        st.bold(&row.reference),
        st.bold(&row.title)
    );
    println!(
        "{}{} · last {}{}",
        st.state(&row.state),
        if row.at_label.is_empty() {
            String::new()
        } else {
            format!(" · at {}", clean_line(&row.at_label))
        },
        ago(row.when, now_),
        if row.why.is_empty() {
            String::new()
        } else {
            format!(" · {}", clean_line(&row.why))
        }
    );
    if row.priority != "normal" || !row.priority_source.is_empty() {
        println!(
            "Priority {}{}",
            clean_line(&row.priority),
            if row.priority_source.is_empty() {
                String::new()
            } else {
                format!(" ({})", clean_line(&row.priority_source))
            }
        );
    }
    if let Some(o) = &t.outcome {
        println!("\n{}: {}", st.bold(&o.headline), clean_line(&o.reason));
        if !o.sentence.is_empty() {
            println!("{}", block(&o.sentence, "  "));
        }
    }
    if let Some(n) = &t.need {
        println!(
            "\n{} {}",
            st.state("needs").replace("needs", "Needs you:"),
            clean_line(&n.reason)
        );
        if let Some(qs) = &n.questions {
            for q in qs.questions.iter().filter(|q| q.pending) {
                let rec = q
                    .options
                    .iter()
                    .find(|o| o.id == q.recommended)
                    .map(|o| format!(" (recommended: {})", clean_line(&o.label)))
                    .unwrap_or_default();
                println!("  [{}] {}{rec}", clean_line(&q.id), clean_line(&q.text));
            }
            println!(
                "  Answer with: shikumi answer {}#{}",
                r.repo,
                r.display().trim_start_matches('#')
            );
        }
        for a in &n.actions {
            println!(
                "  {} — shikumi ticket start {}#{} {}",
                clean_line(&a.text),
                r.repo,
                r.display().trim_start_matches('#'),
                clean_line(&a.action)
            );
        }
    }
    if !t.offers.start.is_empty() && t.need.is_none() {
        println!(
            "\nCan start: {}",
            t.offers
                .start
                .iter()
                .map(|a| clean_line(&a.action))
                .collect::<Vec<_>>()
                .join(", ")
        );
    } else if !t.offers.note.is_empty() {
        println!("\n{}", st.dim(&t.offers.note));
    }
    if !row.prs.is_empty() {
        println!("\nPull requests");
        for p in &row.prs {
            println!(
                "  {}#{}  {}{}{}",
                clean_line(&p.repo),
                p.number,
                st.state(&p.status),
                p.rounds
                    .filter(|n| *n > 0)
                    .map(|n| format!(" · {n} fix round(s)"))
                    .unwrap_or_default(),
                if p.mergeable {
                    " · checks passed: shikumi pr merge"
                } else {
                    ""
                }
            );
        }
    }
    if !t.journey.steps.is_empty() {
        println!();
        let rows: Vec<Vec<Cell>> = t
            .journey
            .steps
            .iter()
            .map(|s| {
                vec![
                    Cell::from(s.n.to_string()),
                    Cell::from(s.station.as_str()),
                    Cell::state(&s.state),
                    Cell::dim(s.model.as_deref().unwrap_or("")),
                    Cell::from(duration(s.seconds)),
                    Cell::from(match (&s.run_id, &s.tokens) {
                        (Some(id), Some(tk)) => format!("run {id} · {}", tokens(tk.input, tk.out)),
                        _ => format!(
                            "{}{}",
                            s.message,
                            s.repeat.map(|n| format!(" (×{n})")).unwrap_or_default()
                        ),
                    }),
                ]
            })
            .collect();
        print!(
            "{}",
            table(
                st,
                &["#", "station", "state", "model", "took", "what"],
                &rows
            )
        );
        println!(
            "{}",
            st.dim(&format!(
                "{} in all · {}",
                duration(t.journey.seconds),
                tokens(t.journey.tokens.input, t.journey.tokens.out)
            ))
        );
    }
    if !t.docs.is_empty() {
        println!(
            "\nDocuments: {}  (shikumi ticket doc {}#{} <stage>)",
            clean_line(&t.docs.join(", ")),
            r.repo,
            r.display().trim_start_matches('#')
        );
    }
    if let Some(l) = &t.local {
        println!("\n{} ({})", st.bold("Description"), clean_line(&l.state));
        println!(
            "{}",
            if l.body.trim().is_empty() {
                "  (none)".to_string()
            } else {
                block(&l.body, "  ")
            }
        );
        for c in &l.comments {
            println!(
                "\n  {} {}",
                st.bold(&c.author),
                st.dim(&when_value(c.at.as_ref(), now_))
            );
            println!("{}", block(&c.body, "    "));
        }
    }
    if with_events && !t.events.is_empty() {
        println!();
        let rows: Vec<Vec<Cell>> = t
            .events
            .iter()
            .map(|e| {
                vec![
                    Cell::from(ago(Some(e.ts), now_)),
                    Cell::dim(&e.kind),
                    Cell::from(e.message.as_str()),
                ]
            })
            .collect();
        print!("{}", table(st, &["when", "kind", "message"], &rows));
    }
    Ok(())
}

fn start(ctx: &Ctx, r: &TicketRef, action: Option<&str>) -> Result<()> {
    let action = match action {
        Some(a) => a.to_string(),
        None => {
            let t: Ticket = ctx.client.get(&r.path(), &[("cached", "1".to_string())])?;
            let mut items: Vec<(String, String, String)> = t
                .need
                .as_ref()
                .map(|n| {
                    n.actions
                        .iter()
                        .map(|a| (a.action.clone(), a.text.clone(), String::new()))
                        .collect()
                })
                .unwrap_or_default();
            if items.is_empty() {
                items = t
                    .offers
                    .start
                    .iter()
                    .map(|a| (a.action.clone(), a.text.clone(), a.does.clone()))
                    .collect();
            }
            if items.is_empty() {
                let why = if t.offers.note.is_empty() {
                    "nothing can be started on it now".to_string()
                } else {
                    clean_line(&t.offers.note)
                };
                bail!("{} {}: {why}", r.repo, r.display());
            }
            if !interactive() {
                return Err(usage(format!(
                    "name the action: {}",
                    items
                        .iter()
                        .map(|i| clean_line(&i.0))
                        .collect::<Vec<_>>()
                        .join(", ")
                )));
            }
            let shown: Vec<(String, String)> = items
                .iter()
                .map(|(_, word, does)| (word.clone(), does.clone()))
                .collect();
            match prompt::choose(
                &mut io::stdin().lock(),
                &mut io::stdout(),
                &format!("Start {} {}:", r.repo, r.display()),
                &shown,
            )? {
                Some(i) => items[i].0.clone(),
                None => bail!("nothing was done"),
            }
        }
    };
    if let Some(stage) = action.strip_prefix("redirect:") {
        return ctx
            .act(&format!("{}/send-back", r.path()), json!({"stage": stage}))
            .map(drop);
    }
    ctx.act(&format!("{}/start", r.path()), json!({"action": action}))
        .map(drop)
}

// ------------------------------------------------------------------ needs and answers

fn needs(ctx: &Ctx) -> Result<()> {
    let Some(n) = ctx.get::<NeedsList>("needs", &[])? else {
        return Ok(());
    };
    if !n.available {
        println!("Not available: {}", clean_line(&n.reason));
        return Ok(());
    }
    if n.tickets.is_empty() {
        println!("Nothing needs you.");
        return Ok(());
    }
    let rows: Vec<Vec<Cell>> = n
        .tickets
        .iter()
        .map(|t| {
            let what = match (&t.questions, t.kind.as_str()) {
                (Some(q), _) => format!("{} question(s) from the {}", q.pending.len(), q.stage),
                (None, _) => format!(
                    "decide: {}",
                    t.actions
                        .iter()
                        .map(|a| a.action.clone())
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            };
            vec![
                Cell::from(format!("{} {}", short_repo(&t.repo), t.reference)),
                Cell::state(&t.kind),
                Cell::from(what),
                Cell::from(t.title.as_str()),
            ]
        })
        .collect();
    print!(
        "{}",
        table(&ctx.style, &["ticket", "kind", "what", "title"], &rows)
    );
    println!("{}", ctx.style.dim("Answer with `shikumi answer <ticket>`, or decide with `shikumi ticket start <ticket>`."));
    Ok(())
}

fn split_pair(s: &str) -> Result<(String, String)> {
    s.split_once('=')
        .map(|(a, b)| (a.trim().to_string(), b.trim().to_string()))
        .filter(|(a, b)| !a.is_empty() && !b.is_empty())
        .ok_or_else(|| usage(format!("{s:?}: write it as QUESTION=ANSWER")))
}

fn answer(ctx: &Ctx, a: &AnswerArgs) -> Result<()> {
    let r = ctx.ticket(&a.ticket)?;
    let path = format!("{}/answer", r.path());
    if a.accept_all {
        return ctx.act(&path, json!({"accept_all": true})).map(drop);
    }
    let t: Ticket = ctx.client.get(&r.path(), &[])?;
    let qs = t
        .need
        .and_then(|n| n.questions)
        .filter(|q| !q.pending.is_empty())
        .ok_or_else(|| anyhow!("{} {} has no open questions", r.repo, r.display()))?;
    let mut answers: Vec<(String, Answer)> = Vec::new();
    if !a.pick.is_empty() || !a.other.is_empty() {
        for p in &a.pick {
            let (qid, opt) = split_pair(p)?;
            let q = qs
                .questions
                .iter()
                .find(|q| q.id == qid)
                .ok_or_else(|| usage(format!("there is no question {qid}")))?;
            if !q.options.iter().any(|o| o.id == opt) {
                return Err(usage(format!(
                    "{qid} has no option {opt} (it has {})",
                    q.options
                        .iter()
                        .map(|o| o.id.clone())
                        .collect::<Vec<_>>()
                        .join(", ")
                )));
            }
            answers.push((qid, Answer::Option(opt)));
        }
        for o in &a.other {
            let (qid, text) = split_pair(o)?;
            if !qs.questions.iter().any(|q| q.id == qid) {
                return Err(usage(format!("there is no question {qid}")));
            }
            answers.push((qid, Answer::Other(text)));
        }
    } else {
        if !interactive() {
            return Err(usage(
                "in a script, answer with --pick Q=OPTION, --other Q=TEXT or --accept-all",
            ));
        }
        let got = prompt::answer_questions(&mut io::stdin().lock(), &mut io::stdout(), &qs)?;
        if got.is_empty() {
            bail!("no answer given: nothing was sent");
        }
        answers = got.into_iter().collect();
        println!();
        for (qid, ans) in &answers {
            let shown = match ans {
                Answer::Option(o) => o.clone(),
                Answer::Other(t) => format!("\"{}\"", cut(t, 60)),
            };
            println!("  {} → {}", clean_line(qid), clean_line(&shown));
        }
        ctx.confirm("Send these answers?")?;
    }
    let map: serde_json::Map<String, Value> = answers
        .into_iter()
        .map(|(q, a)| {
            (
                q,
                match a {
                    Answer::Option(o) => json!({"option": o}),
                    Answer::Other(t) => json!({"other": t}),
                },
            )
        })
        .collect();
    ctx.act(&path, json!({"stage": qs.stage, "answers": map}))
        .map(drop)
}

fn answer_all(ctx: &Ctx, a: &AnswerAllArgs) -> Result<()> {
    let refs: Vec<String> = if a.all {
        let n: NeedsList = ctx.client.get("needs", &[])?;
        if !n.available {
            bail!("{}", clean_line(&n.reason));
        }
        n.tickets
            .iter()
            .filter(|t| t.questions.is_some())
            .map(|t| format!("{}#{}", t.repo, t.issue))
            .collect()
    } else {
        a.tickets
            .iter()
            .map(|s| ctx.ticket(s).map(|r| format!("{}#{}", r.repo, r.number)))
            .collect::<Result<_>>()?
    };
    if refs.is_empty() {
        return Err(usage(if a.all {
            "no ticket is waiting for answers"
        } else {
            "name the tickets, or use --all"
        }));
    }
    let d = ctx.act("answer-all", json!({"tickets": refs}))?;
    if !d.failed.is_empty()
        && d.done
            .as_ref()
            .and_then(Value::as_array)
            .is_none_or(|v| v.is_empty())
    {
        bail!("no recommendation could be accepted");
    }
    Ok(())
}

// ------------------------------------------------------------------ runs, events, PRs

fn runs(ctx: &Ctx, a: &RunsArgs) -> Result<()> {
    let q = [
        ("status", a.status.clone().unwrap_or_default()),
        ("repo", a.repo.clone().unwrap_or_default()),
        ("page", a.page.to_string()),
    ];
    let Some(l) = ctx.get::<RunList>("runs", &q)? else {
        return Ok(());
    };
    let t = now();
    let rows: Vec<Vec<Cell>> = l
        .runs
        .iter()
        .map(|r| {
            vec![
                Cell::from(r.id.to_string()),
                Cell::state(&r.status),
                Cell::from(format!("{} {}", short_repo(&r.repo), r.reference)),
                Cell::from(r.stage.clone().unwrap_or_else(|| r.kind.clone())),
                Cell::dim(r.model.as_deref().unwrap_or("")),
                Cell::from(ago(Some(r.started), t)),
                Cell::from(duration(r.finished.map(|f| f - r.started))),
                Cell::from(r.title.as_str()),
            ]
        })
        .collect();
    print!(
        "{}",
        table(
            &ctx.style,
            &["run", "status", "ticket", "kind", "model", "started", "took", "title"],
            &rows
        )
    );
    if l.more {
        println!(
            "{}",
            ctx.style
                .dim(&format!("More: shikumi runs --page {}", l.page + 1))
        );
    }
    Ok(())
}

fn run_one(ctx: &Ctx, a: &RunArgs) -> Result<()> {
    let Some(r) = ctx.get::<Run>(&format!("runs/{}", a.id), &[])? else {
        return Ok(());
    };
    let st = &ctx.style;
    println!(
        "{} {}  {}",
        st.bold(&format!("Run {}", r.id)),
        st.state(&r.status),
        clean_line(&r.title)
    );
    println!(
        "{} {} · {} · {}",
        clean_line(&r.repo),
        r.reference,
        clean_line(&r.stage.clone().unwrap_or(r.kind.clone())),
        clean_line(
            &[r.harness.clone(), r.model.clone(), r.effort.clone()]
                .into_iter()
                .flatten()
                .collect::<Vec<_>>()
                .join(" / ")
        )
    );
    println!(
        "started {} · took {} · {}",
        ago(Some(r.started), now()),
        duration(r.finished.map(|f| f - r.started)),
        tokens(r.tokens.input, r.tokens.out)
    );
    if !r.detail.is_empty() {
        println!("{}", block(&r.detail, ""));
    }
    for u in &r.pr_urls {
        println!("PR {}", clean_line(u));
    }
    if a.output {
        println!(
            "\n{}\n{}",
            st.bold("Output"),
            if r.output.is_empty() {
                "(none)".to_string()
            } else {
                block(&r.output, "")
            }
        );
    }
    if a.log {
        println!(
            "\n{}\n{}",
            st.bold("Log (the end)"),
            if r.log_tail.is_empty() {
                "(none)".to_string()
            } else {
                block(&r.log_tail, "")
            }
        );
    }
    if !a.output && !a.log {
        println!(
            "{}",
            st.dim("--output for what it produced, --log for the end of its log")
        );
    }
    Ok(())
}

fn events(ctx: &Ctx, a: &EventsArgs) -> Result<()> {
    let q = [
        ("kind", a.kind.clone().unwrap_or_default()),
        (
            "before",
            a.before.map(|b| b.to_string()).unwrap_or_default(),
        ),
    ];
    let Some(l) = ctx.get::<EventList>("events", &q)? else {
        return Ok(());
    };
    let t = now();
    let rows: Vec<Vec<Cell>> = l
        .events
        .iter()
        .map(|e| {
            vec![
                Cell::from(ago(Some(e.ts), t)),
                Cell::dim(&e.kind),
                Cell::from(match (&e.repo, e.issue) {
                    (Some(r), Some(i)) => format!("{} {}", short_repo(r), refs::display(i)),
                    _ => String::new(),
                }),
                Cell::from(e.message.as_str()),
            ]
        })
        .collect();
    print!(
        "{}",
        table(&ctx.style, &["when", "kind", "ticket", "message"], &rows)
    );
    if let Some(b) = l.before {
        println!(
            "{}",
            ctx.style
                .dim(&format!("Older: shikumi events --before {b}"))
        );
    }
    Ok(())
}

fn merge(ctx: &Ctx, pr: &str) -> Result<()> {
    let r = refs::parse(pr, None)
        .map_err(|_| usage(format!("{pr:?}: name the pull request as owner/repo#12")))?;
    if r.number > refs::LOCAL_BASE {
        return Err(usage("that is a local ticket, not a pull request"));
    }
    ctx.confirm(&format!("Merge {}#{}?", r.repo, r.number))?;
    ctx.act(&format!("prs/{}/{}/merge", r.repo, r.number), json!({}))
        .map(drop)
}
