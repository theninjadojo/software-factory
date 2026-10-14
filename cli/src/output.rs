//! Printing for people: tables, blocks of text, times. Every function here takes the factory's text as it came and cleans it
//! itself (sanitize.rs), so no caller can print ticket text raw by mistake. Colour only on a terminal, and never with NO_COLOR.

use crate::sanitize::{clean, clean_line};
use std::io::IsTerminal;
use std::time::{SystemTime, UNIX_EPOCH};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

#[derive(Debug, Clone, Copy)]
pub struct Style {
    pub color: bool,
    pub width: usize,
}

impl Style {
    pub fn detect(no_color: bool) -> Style {
        let tty = std::io::stdout().is_terminal();
        let color = tty
            && !no_color
            && std::env::var_os("NO_COLOR").is_none()
            && std::env::var("TERM").map(|t| t != "dumb").unwrap_or(false);
        let width = std::env::var("COLUMNS")
            .ok()
            .and_then(|c| c.parse().ok())
            .filter(|w: &usize| *w >= 40)
            .unwrap_or(if tty { 100 } else { 160 });
        Style { color, width }
    }

    #[cfg(test)]
    pub fn plain() -> Style {
        Style {
            color: false,
            width: 120,
        }
    }

    fn paint(&self, code: &str, s: &str) -> String {
        if self.color {
            format!("\x1b[{code}m{s}\x1b[0m")
        } else {
            s.to_string()
        }
    }

    pub fn bold(&self, s: &str) -> String {
        self.paint("1", &clean_line(s))
    }

    pub fn dim(&self, s: &str) -> String {
        self.paint("2", &clean_line(s))
    }

    /// A ticket state or run status, coloured by what it means.
    pub fn state(&self, s: &str) -> String {
        let code = match s {
            "needs" | "waiting" | "queued" | "watching" => "33",
            "failed" | "fail" | "error" | "rejected" | "interrupted" => "31",
            "done" | "passed" | "pr" | "stage" | "merged" => "32",
            "working" | "running" | "prs" => "36",
            _ => "",
        };
        if code.is_empty() {
            clean_line(s)
        } else {
            self.paint(code, &clean_line(s))
        }
    }
}

/// Cut to a display width, with an ellipsis.
pub fn cut(s: &str, max: usize) -> String {
    let s = clean_line(s);
    if UnicodeWidthStr::width(s.as_str()) <= max {
        return s;
    }
    let mut out = String::new();
    let mut w = 0;
    for c in s.chars() {
        let cw = c.width().unwrap_or(0);
        if w + cw + 1 > max {
            break;
        }
        out.push(c);
        w += cw;
    }
    out.push('…');
    out
}

/// How a table cell is shown. The text is always the factory's raw text; the table cleans it before painting.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Paint {
    Plain,
    Dim,
    Bold,
    State,
}

#[derive(Debug, Clone)]
pub struct Cell {
    pub text: String,
    pub paint: Paint,
}

impl Cell {
    pub fn state(s: &str) -> Cell {
        Cell {
            text: s.to_string(),
            paint: Paint::State,
        }
    }
    pub fn dim(s: &str) -> Cell {
        Cell {
            text: s.to_string(),
            paint: Paint::Dim,
        }
    }
    pub fn bold(s: &str) -> Cell {
        Cell {
            text: s.to_string(),
            paint: Paint::Bold,
        }
    }
}

impl From<String> for Cell {
    fn from(text: String) -> Cell {
        Cell {
            text,
            paint: Paint::Plain,
        }
    }
}

impl From<&str> for Cell {
    fn from(text: &str) -> Cell {
        Cell {
            text: text.to_string(),
            paint: Paint::Plain,
        }
    }
}

/// A table: every cell is cleaned, measured and cut first, and only then painted, so text from the factory can never carry
/// an escape sequence through. The last column takes what is left of the width.
pub fn table(style: &Style, head: &[&str], rows: &[Vec<Cell>]) -> String {
    let cols = head.len();
    let rows: Vec<Vec<(String, Paint)>> = rows
        .iter()
        .map(|r| r.iter().map(|c| (clean_line(&c.text), c.paint)).collect())
        .collect();
    let mut widths: Vec<usize> = head.iter().map(|h| h.len()).collect();
    for r in &rows {
        for (i, (t, _)) in r.iter().enumerate().take(cols.saturating_sub(1)) {
            widths[i] = widths[i].max(UnicodeWidthStr::width(t.as_str()));
        }
    }
    let used: usize = widths
        .iter()
        .take(cols.saturating_sub(1))
        .map(|w| w + 2)
        .sum();
    let last = style.width.saturating_sub(used).max(20);
    let paint = |t: &str, p: Paint| match p {
        Paint::Plain => t.to_string(),
        Paint::Dim => style.dim(t),
        Paint::Bold => style.bold(t),
        Paint::State => style.state(t),
    };
    let line = |cells: &[(String, Paint)]| -> String {
        let mut l = String::new();
        for (i, (t, p)) in cells.iter().enumerate().take(cols) {
            if i + 1 == cols {
                l.push_str(&paint(&cut(t, last), *p));
            } else {
                l.push_str(&paint(t, *p));
                l.push_str(&" ".repeat(widths[i] - UnicodeWidthStr::width(t.as_str()) + 2));
            }
        }
        l.trim_end().to_string()
    };
    let mut out = String::new();
    out.push_str(&line(
        &head
            .iter()
            .map(|h| (h.to_uppercase(), Paint::Dim))
            .collect::<Vec<_>>(),
    ));
    out.push('\n');
    for r in &rows {
        out.push_str(&line(r));
        out.push('\n');
    }
    out
}

/// A block of text (a document, a comment, a log), cleaned and indented.
pub fn block(text: &str, indent: &str) -> String {
    clean(text)
        .lines()
        .map(|l| format!("{indent}{l}"))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// "3m ago", "2h ago", "5d ago".
pub fn ago(ts: Option<f64>, now: f64) -> String {
    let Some(ts) = ts.filter(|t| *t > 0.0) else {
        return "—".into();
    };
    let s = (now - ts).max(0.0) as u64;
    match s {
        0..=59 => format!("{s}s ago"),
        60..=3599 => format!("{}m ago", s / 60),
        3600..=86_399 => format!("{}h ago", s / 3600),
        _ => format!("{}d ago", s / 86_400),
    }
}

/// "45s", "12m 3s", "1h 4m".
pub fn duration(secs: Option<f64>) -> String {
    let Some(s) = secs.filter(|s| *s >= 0.0) else {
        return "—".into();
    };
    let s = s as u64;
    if s < 60 {
        format!("{s}s")
    } else if s < 3600 {
        format!("{}m {}s", s / 60, s % 60)
    } else {
        format!("{}h {}m", s / 3600, (s % 3600) / 60)
    }
}

pub fn tokens(input: Option<u64>, out: Option<u64>) -> String {
    let k = |n: u64| {
        if n >= 10_000 {
            format!("{}k", n / 1000)
        } else {
            n.to_string()
        }
    };
    match (input, out) {
        (None, None) => "—".into(),
        (i, o) => format!(
            "{} in / {} out",
            i.map(k).unwrap_or("?".into()),
            o.map(k).unwrap_or("?".into())
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_clean_their_cells_and_fit() {
        let st = Style {
            color: false,
            width: 40,
        };
        let t = table(
            &st,
            &["ref", "title"],
            &[vec![
                "#1".into(),
                "evil\x1b[2Jtitle that is far too long to fit in forty columns".into(),
            ]],
        );
        assert!(!t.contains('\x1b'));
        let c = Style {
            color: true,
            width: 40,
        };
        let painted = table(
            &c,
            &["state", "title"],
            &[vec![Cell::state("fail\x1b[2Jed"), Cell::dim("x\u{9b}y")]],
        );
        assert!(
            !painted.contains("\x1b[2J") && !painted.contains('\u{9b}'),
            "{painted:?}"
        );
        assert!(t.lines().all(|l| UnicodeWidthStr::width(l) <= 40), "{t}");
        assert!(t.contains("evil[2Jtitle"));
        assert!(t.contains('…'));
    }

    #[test]
    fn wide_characters_are_measured() {
        assert_eq!(cut("日本語のチケット", 7), "日本語…");
    }

    #[test]
    fn times() {
        assert_eq!(ago(Some(100.0), 130.0), "30s ago");
        assert_eq!(ago(Some(0.0), 130.0), "—");
        assert_eq!(ago(Some(100.0), 100.0 + 7200.0), "2h ago");
        assert_eq!(duration(Some(3725.0)), "1h 2m");
        assert_eq!(tokens(Some(12_345), Some(800)), "12k in / 800 out");
    }

    #[test]
    fn colour_only_when_asked() {
        assert_eq!(Style::plain().state("failed"), "failed");
        let c = Style {
            color: true,
            width: 80,
        };
        assert_eq!(c.state("failed"), "\x1b[31mfailed\x1b[0m");
        assert_eq!(c.state("x\x1b[2J"), "x[2J");
    }
}
