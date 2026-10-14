//! The commands. Each one maps to a page or a button of the admin UI; settings are changed in the UI only.

use clap::{Args, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "shikumi", version = crate::client::VERSION, about = "Manage tickets on a Shikumi software factory from the terminal",
          after_help = "Set up a factory first: shikumi instance add <name> --url <address> (the token comes from the factory's Settings → Credentials).")]
pub struct Cli {
    /// The factory to talk to (see `shikumi instance list`)
    #[arg(short, long, global = true, value_name = "NAME")]
    pub instance: Option<String>,
    /// Print the factory's JSON instead of a table
    #[arg(long, global = true)]
    pub json: bool,
    /// Do not ask before closing or merging
    #[arg(short, long, global = true)]
    pub yes: bool,
    /// No colour
    #[arg(long, global = true)]
    pub no_color: bool,
    /// Allow plain http to another machine (the token is then sent unencrypted)
    #[arg(long, global = true)]
    pub allow_insecure_http: bool,
    /// The client's own settings file (default ~/.config/shikumi/cli.toml)
    #[arg(long, global = true, value_name = "FILE")]
    pub config: Option<PathBuf>,
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// The factories this client knows
    #[command(subcommand)]
    Instance(InstanceCmd),
    /// The client's and the factory's versions
    Version,
    /// What the factory is doing: mode, health, running and queued work, what needs you
    Status,
    /// Stop taking new work (running work finishes)
    Pause,
    /// Take new work again
    Resume,
    /// The tickets, as on the Tickets page
    Tickets(ListArgs),
    /// One ticket, and what you can do to it
    #[command(subcommand)]
    Ticket(TicketCmd),
    /// Tickets waiting for you: questions to answer, or a decision
    Needs,
    /// Answer the questions a stage left on a ticket
    Answer(AnswerArgs),
    /// Accept the recommended answers on several tickets
    AnswerAll(AnswerAllArgs),
    /// Agent runs
    Runs(RunsArgs),
    /// One run: what it did, its output and the end of its log
    Run(RunArgs),
    /// What happened, newest first
    Events(EventsArgs),
    /// Pull requests the factory opened
    #[command(subcommand)]
    Pr(PrCmd),
}

#[derive(Subcommand, Debug)]
pub enum InstanceCmd {
    /// Add a factory (or replace one): its address and a token from its Settings → Credentials page
    Add {
        name: String,
        /// The factory's address: http://127.0.0.1:8787 through an SSH tunnel, or its https address
        #[arg(long)]
        url: String,
        /// Read the token from standard input instead of asking for it
        #[arg(long)]
        token_stdin: bool,
        /// The repository short refs (#12, L-3) mean on this factory
        #[arg(long, value_name = "OWNER/NAME")]
        repo: Option<String>,
        /// Use it when no --instance is given
        #[arg(long)]
        default: bool,
    },
    /// The factories this client knows
    List,
    /// Forget a factory (its token stays valid until it is revoked on the factory)
    Remove { name: String },
    /// Use this factory when no --instance is given
    Default { name: String },
    /// Check the address and the token
    Test { name: Option<String> },
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
pub enum Stage {
    Needs,
    Working,
    Prs,
    Failed,
    New,
    Done,
    All,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
pub enum Sort {
    Latest,
    Oldest,
    Priority,
}

#[derive(Args, Debug)]
pub struct ListArgs {
    /// Which tickets (default: needs while anything needs you, else all)
    #[arg(short, long, value_enum)]
    pub stage: Option<Stage>,
    /// Only one project's tickets (`standalone` for the rest)
    #[arg(short, long)]
    pub project: Option<String>,
    /// Search the titles, or a ticket number
    #[arg(short, long)]
    pub query: Option<String>,
    #[arg(long, value_enum)]
    pub sort: Option<Sort>,
    /// Only tickets at this station (classify, analyst, build, ci, ...)
    #[arg(long)]
    pub at: Option<String>,
    /// At most this many
    #[arg(short, long, default_value_t = 50)]
    pub limit: u32,
    /// Ask GitHub now instead of using the factory's last read
    #[arg(long)]
    pub fresh: bool,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
pub enum Where {
    Local,
    Github,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, ValueEnum)]
pub enum Priority {
    High,
    Normal,
    Low,
    /// Let the project manager decide
    Pm,
}

#[derive(Subcommand, Debug)]
pub enum TicketCmd {
    /// Everything about a ticket: where it is, what it needs, its steps and PRs
    Show {
        /// owner/repo#12, owner/repo#L-3, or #12 / L-3 with a default repository
        #[arg(value_name = "TICKET")]
        ticket: String,
        /// Also list its recent events
        #[arg(long)]
        events: bool,
        /// Do not ask GitHub for its questions now (faster, may be a little old)
        #[arg(long)]
        cached: bool,
    },
    /// A stage's document (analyst, designer, architect)
    Doc {
        #[arg(value_name = "TICKET")]
        ticket: String,
        stage: String,
    },
    /// Open a new ticket
    Create {
        #[arg(long, value_name = "OWNER/NAME")]
        repo: Option<String>,
        #[arg(long)]
        title: String,
        /// The description
        #[arg(long, conflicts_with = "body_file")]
        body: Option<String>,
        /// Read the description from a file (- for standard input)
        #[arg(long, value_name = "FILE")]
        body_file: Option<PathBuf>,
        /// Start it at once: auto, or a stage (analyst, designer, architect). Nothing starts by default
        #[arg(long)]
        start: Option<String>,
        /// A local ticket (in the factory) or a GitHub issue (default: the factory's choice)
        #[arg(long = "where", value_enum)]
        place: Option<Where>,
    },
    /// Start work on a ticket: without ACTION, choose from what it offers
    Start {
        #[arg(value_name = "TICKET")]
        ticket: String,
        /// auto, a stage name, build, review, conflicts, stage:<name>, skip
        action: Option<String>,
    },
    /// Run a stage that has run again, and the stages after it
    SendBack {
        #[arg(value_name = "TICKET")]
        ticket: String,
        stage: String,
    },
    /// Close a ticket (work on it stops)
    Close {
        #[arg(value_name = "TICKET")]
        ticket: String,
    },
    /// Reopen a closed local ticket
    Reopen {
        #[arg(value_name = "TICKET")]
        ticket: String,
    },
    /// Comment on a local ticket
    Comment {
        #[arg(value_name = "TICKET")]
        ticket: String,
        #[arg(long, conflicts_with = "body_file")]
        body: Option<String>,
        #[arg(long, value_name = "FILE")]
        body_file: Option<PathBuf>,
    },
    /// Change a local ticket's title or description
    Edit {
        #[arg(value_name = "TICKET")]
        ticket: String,
        #[arg(long)]
        title: Option<String>,
        #[arg(long, conflicts_with = "body_file")]
        body: Option<String>,
        #[arg(long, value_name = "FILE")]
        body_file: Option<PathBuf>,
    },
    /// Pin a ticket's priority, or let the project manager decide
    Priority {
        #[arg(value_name = "TICKET")]
        ticket: String,
        #[arg(value_enum)]
        priority: Priority,
    },
}

#[derive(Args, Debug)]
pub struct AnswerArgs {
    #[arg(value_name = "TICKET")]
    pub ticket: String,
    /// Accept every recommendation
    #[arg(long, conflicts_with_all = ["pick", "other"])]
    pub accept_all: bool,
    /// Answer a question with an option: QUESTION=OPTION (repeat for each)
    #[arg(long, value_name = "Q=OPTION")]
    pub pick: Vec<String>,
    /// Answer a question in your own words: QUESTION=TEXT
    #[arg(long, value_name = "Q=TEXT")]
    pub other: Vec<String>,
}

#[derive(Args, Debug)]
pub struct AnswerAllArgs {
    /// The tickets (owner/repo#12); with --all, every ticket waiting for answers
    #[arg(value_name = "TICKET")]
    pub tickets: Vec<String>,
    #[arg(long, conflicts_with = "tickets")]
    pub all: bool,
}

#[derive(Args, Debug)]
pub struct RunsArgs {
    /// running, passed, failed, rejected, no-change, rate-limited, interrupted, cancelled
    #[arg(short, long)]
    pub status: Option<String>,
    #[arg(short, long, value_name = "OWNER/NAME")]
    pub repo: Option<String>,
    #[arg(short, long, default_value_t = 1)]
    pub page: u32,
}

#[derive(Args, Debug)]
pub struct RunArgs {
    pub id: u64,
    /// Print the document or summary the run produced
    #[arg(long)]
    pub output: bool,
    /// Print the end of the agent's log
    #[arg(long)]
    pub log: bool,
}

#[derive(Args, Debug)]
pub struct EventsArgs {
    /// important (default), all, alerts, or the start of a kind (decision, ci:)
    #[arg(short, long)]
    pub kind: Option<String>,
    /// Older than this event id (the next page)
    #[arg(long)]
    pub before: Option<u64>,
}

#[derive(Subcommand, Debug)]
pub enum PrCmd {
    /// Merge a pull request whose checks passed
    Merge {
        /// owner/repo#12
        #[arg(value_name = "PR")]
        pr: String,
    },
}
