//! shikumi: manage tickets on a Shikumi software factory from the terminal, through the factory's JSON API (/api/v1/).
//!
//! Exit codes: 0 done, 1 an error here (a file, an address), 2 how the command was written, 3 the token was refused,
//! 4 no such ticket or run, 5 the factory refused (the ticket's state does not allow it, or a value), 6 the factory could not be
//! reached or failed.

mod api;
mod cli;
mod client;
mod commands;
mod config;
mod output;
mod prompt;
mod refs;
mod sanitize;

use clap::Parser;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args = cli::Cli::parse();
    match commands::run(args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            let code = e
                .downcast_ref::<client::ApiError>()
                .map(|a| a.exit_code())
                .unwrap_or_else(|| {
                    if e.downcast_ref::<commands::Usage>().is_some() {
                        2
                    } else {
                        1
                    }
                });
            eprintln!("shikumi: {}", sanitize::clean(&format!("{e:#}")));
            ExitCode::from(code as u8)
        }
    }
}
