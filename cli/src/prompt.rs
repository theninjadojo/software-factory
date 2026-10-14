//! Asking the person: a yes/no before something that cannot be undone from here (closing, merging), and the questions a stage
//! left on a ticket, one at a time. Reads and writes through the streams it is given, so the flows are tested without a terminal.

use crate::api::{Question, Questions};
use crate::output::block;
use crate::sanitize::clean_line;
use anyhow::{bail, Result};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};

/// One answer: an option id, or the person's own words.
#[derive(Debug, Clone, PartialEq)]
pub enum Answer {
    Option(String),
    Other(String),
}

fn read_line(input: &mut dyn BufRead) -> Result<Option<String>> {
    let mut s = String::new();
    if input.read_line(&mut s)? == 0 {
        return Ok(None);
    }
    Ok(Some(s.trim().to_string()))
}

/// "Close #12? [y/N]": only y or yes goes on.
pub fn confirm(input: &mut dyn BufRead, out: &mut dyn Write, question: &str) -> Result<bool> {
    write!(out, "{} [y/N] ", clean_line(question))?;
    out.flush()?;
    Ok(matches!(
        read_line(input)?
            .as_deref()
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("y" | "yes")
    ))
}

/// Pick one of several choices by number; Enter takes the first. None when the person gives up (empty input at the end, q).
pub fn choose(
    input: &mut dyn BufRead,
    out: &mut dyn Write,
    title: &str,
    items: &[(String, String)],
) -> Result<Option<usize>> {
    writeln!(out, "{}", clean_line(title))?;
    for (i, (word, says)) in items.iter().enumerate() {
        let says = clean_line(says);
        writeln!(
            out,
            "  {}. {}{}",
            i + 1,
            clean_line(word),
            if says.is_empty() {
                String::new()
            } else {
                format!(" — {says}")
            }
        )?;
    }
    loop {
        write!(out, "Choose 1-{} (Enter for 1, q to stop): ", items.len())?;
        out.flush()?;
        let Some(line) = read_line(input)? else {
            return Ok(None);
        };
        if line.is_empty() {
            return Ok(Some(0));
        }
        if line == "q" {
            return Ok(None);
        }
        match line.parse::<usize>() {
            Ok(n) if (1..=items.len()).contains(&n) => return Ok(Some(n - 1)),
            _ => writeln!(out, "That is not one of the numbers.")?,
        }
    }
}

fn ask_one(
    input: &mut dyn BufRead,
    out: &mut dyn Write,
    q: &Question,
    k: usize,
    n: usize,
) -> Result<Option<Answer>> {
    writeln!(
        out,
        "\nQuestion {k} of {n}{}",
        if q.needs_person {
            " · needs a person"
        } else {
            ""
        }
    )?;
    writeln!(out, "{}", block(&q.text, "  "))?;
    if !q.why.is_empty() {
        writeln!(out, "  ({})", clean_line(&q.why))?;
    }
    let rec = q.options.iter().position(|o| o.id == q.recommended);
    for (i, o) in q.options.iter().enumerate() {
        writeln!(
            out,
            "  {}. {}{}",
            i + 1,
            clean_line(&o.label),
            if Some(i) == rec {
                "  (recommended)"
            } else {
                ""
            }
        )?;
    }
    if !q.reason.is_empty() && rec.is_some() {
        writeln!(out, "  Why recommended: {}", clean_line(&q.reason))?;
    }
    loop {
        write!(
            out,
            "Answer 1-{}, o to write your own, s to skip{}: ",
            q.options.len(),
            if rec.is_some() {
                ", Enter for the recommendation"
            } else {
                ""
            }
        )?;
        out.flush()?;
        let Some(line) = read_line(input)? else {
            bail!("stopped: nothing was sent")
        };
        match line.as_str() {
            "" if rec.is_some() => return Ok(Some(Answer::Option(q.recommended.clone()))),
            "s" => return Ok(None),
            "o" => {
                write!(out, "Your answer: ")?;
                out.flush()?;
                match read_line(input)? {
                    Some(t) if !t.is_empty() => return Ok(Some(Answer::Other(t))),
                    _ => writeln!(out, "Nothing written.")?,
                }
            }
            n => match n.parse::<usize>() {
                Ok(i) if (1..=q.options.len()).contains(&i) => {
                    return Ok(Some(Answer::Option(q.options[i - 1].id.clone())))
                }
                _ => writeln!(out, "That is not one of the answers.")?,
            },
        }
    }
}

/// Ask each open question of the stage and return the answers the person gave (none for one they skipped). Questions the
/// factory already answered by itself are only listed.
pub fn answer_questions(
    input: &mut dyn BufRead,
    out: &mut dyn Write,
    qs: &Questions,
) -> Result<BTreeMap<String, Answer>> {
    let open: Vec<&Question> = qs.questions.iter().filter(|q| q.pending).collect();
    let auto: Vec<&Question> = qs.questions.iter().filter(|q| q.auto.is_some()).collect();
    if !auto.is_empty() {
        writeln!(
            out,
            "{} question(s) were answered automatically (see `shikumi ticket doc` to change one).",
            auto.len()
        )?;
    }
    let mut got = BTreeMap::new();
    for (k, q) in open.iter().enumerate() {
        if let Some(a) = ask_one(input, out, q, k + 1, open.len())? {
            got.insert(q.id.clone(), a);
        }
    }
    Ok(got)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::OptionChoice;
    use std::io::Cursor;

    fn qs() -> Questions {
        let q = |id: &str, rec: &str| Question {
            id: id.into(),
            text: format!("Question {id}?\x1b[2J"),
            options: vec![
                OptionChoice {
                    id: "a".into(),
                    label: "Alpha".into(),
                },
                OptionChoice {
                    id: "b".into(),
                    label: "Beta\u{202e}".into(),
                },
            ],
            recommended: rec.into(),
            needs_person: true,
            pending: true,
            ..Default::default()
        };
        Questions {
            stage: "analyst".into(),
            questions: vec![q("q1", "b"), q("q2", ""), q("q3", "a")],
            pending: vec![],
        }
    }

    #[test]
    fn enter_takes_the_recommendation_numbers_pick_o_writes_s_skips() {
        let mut out = Vec::new();
        let got = answer_questions(
            &mut Cursor::new("\n9\no\nmy own words\ns\n"),
            &mut out,
            &qs(),
        )
        .unwrap();
        assert_eq!(got.get("q1"), Some(&Answer::Option("b".into())));
        assert_eq!(got.get("q2"), Some(&Answer::Other("my own words".into())));
        assert_eq!(got.get("q3"), None);
        let text = String::from_utf8(out).unwrap();
        assert!(text.contains("2. Beta  (recommended)"));
        assert!(text.contains("That is not one of the answers."));
        assert!(!text.contains('\x1b') && !text.contains('\u{202e}'));
    }

    #[test]
    fn running_out_of_input_sends_nothing() {
        assert!(answer_questions(&mut Cursor::new("1\n"), &mut Vec::new(), &qs()).is_err());
    }

    #[test]
    fn confirm_and_choose() {
        assert!(confirm(&mut Cursor::new("y\n"), &mut Vec::new(), "Close?").unwrap());
        assert!(!confirm(&mut Cursor::new("\n"), &mut Vec::new(), "Close?").unwrap());
        assert!(!confirm(&mut Cursor::new(""), &mut Vec::new(), "Close?").unwrap());
        let items = vec![
            ("Auto".to_string(), "picks".to_string()),
            ("Build".to_string(), String::new()),
        ];
        assert_eq!(
            choose(&mut Cursor::new("\n"), &mut Vec::new(), "Start", &items).unwrap(),
            Some(0)
        );
        assert_eq!(
            choose(&mut Cursor::new("5\n2\n"), &mut Vec::new(), "Start", &items).unwrap(),
            Some(1)
        );
        assert_eq!(
            choose(&mut Cursor::new("q\n"), &mut Vec::new(), "Start", &items).unwrap(),
            None
        );
    }
}
