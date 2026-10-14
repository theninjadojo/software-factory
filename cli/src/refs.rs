//! Naming a ticket on the command line: `owner/repo#12`, `owner/repo#L-3`, or just `#12`, `L-3`, `12` when the repository is
//! known (the instance's `repo`, or the factory's only one). A local ticket's number is LOCAL_BASE + n, as in the factory.

use anyhow::{bail, Result};

pub const LOCAL_BASE: u64 = 100_000_000;

#[derive(Debug, Clone, PartialEq)]
pub struct TicketRef {
    pub repo: String,
    pub number: u64,
}

impl TicketRef {
    /// `#12` or `L-3`, as the factory shows it.
    pub fn display(&self) -> String {
        display(self.number)
    }

    /// The API path of the ticket: tickets/<owner>/<name>/<number>.
    pub fn path(&self) -> String {
        format!("tickets/{}/{}", self.repo, self.number)
    }
}

pub fn display(number: u64) -> String {
    if number > LOCAL_BASE {
        format!("L-{}", number - LOCAL_BASE)
    } else {
        format!("#{number}")
    }
}

fn valid_repo(repo: &str) -> bool {
    let mut parts = repo.split('/');
    let ok = |p: Option<&str>| {
        p.is_some_and(|p| {
            !p.is_empty()
                && p != "."
                && p != ".."
                && p.len() <= 100
                && p.chars()
                    .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
        })
    };
    ok(parts.next()) && ok(parts.next()) && parts.next().is_none()
}

fn number(s: &str) -> Option<u64> {
    let local = s.strip_prefix("L-").or_else(|| s.strip_prefix("l-"));
    let digits = local.unwrap_or_else(|| s.strip_prefix('#').unwrap_or(s));
    if digits.is_empty() || digits.len() > 9 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let n: u64 = digits.parse().ok()?;
    match local {
        Some(_) if n > 0 => Some(LOCAL_BASE + n),
        Some(_) => None,
        None if n > 0 => Some(n),
        None => None,
    }
}

/// Parse a ticket reference. `default_repo` is used when only a number is given.
pub fn parse(s: &str, default_repo: Option<&str>) -> Result<TicketRef> {
    let s = s.trim();
    let (repo, num) = match s.rsplit_once('#') {
        Some((repo, num)) if !repo.is_empty() => (Some(repo.to_string()), num),
        _ => match s.rsplit_once(' ') {
            Some((repo, num)) => (Some(repo.trim().to_string()), num),
            None => (None, s),
        },
    };
    let n = number(num).ok_or_else(|| {
        anyhow::anyhow!(
            "{s:?} is not a ticket: write it as owner/repo#12, owner/repo#L-3, #12 or L-3"
        )
    })?;
    let repo = match repo.or_else(|| default_repo.map(str::to_string)) {
        Some(r) => r,
        None => bail!("{s:?}: which repository? Write owner/repo#{}, or set one with `shikumi instance add ... --repo owner/repo`", num.trim_start_matches('#')),
    };
    if !valid_repo(&repo) {
        bail!("{repo:?} is not a repository: write it as owner/name");
    }
    Ok(TicketRef { repo, number: n })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forms() {
        let r = |s: &str| parse(s, Some("o/r")).unwrap();
        assert_eq!(
            r("acme/web#12"),
            TicketRef {
                repo: "acme/web".into(),
                number: 12
            }
        );
        assert_eq!(
            r("acme/web#L-3"),
            TicketRef {
                repo: "acme/web".into(),
                number: LOCAL_BASE + 3
            }
        );
        assert_eq!(r("acme/web L-3").number, LOCAL_BASE + 3);
        assert_eq!(
            r("#12"),
            TicketRef {
                repo: "o/r".into(),
                number: 12
            }
        );
        assert_eq!(r("12").number, 12);
        assert_eq!(r("L-3").number, LOCAL_BASE + 3);
        assert_eq!(r("l-3").number, LOCAL_BASE + 3);
        assert_eq!(r("L-3").display(), "L-3");
        assert_eq!(r("#12").display(), "#12");
        assert_eq!(r("acme/web#L-3").path(), "tickets/acme/web/100000003");
    }

    #[test]
    fn refused() {
        for bad in [
            "",
            "#",
            "#0",
            "L-0",
            "abc",
            "#12x",
            "acme#12",
            "a/b/c#1",
            "../x#1",
            "a/..#1",
            "./b#1",
            "acme/web#1234567890",
            "acme/w b#1",
        ] {
            assert!(parse(bad, Some("o/r")).is_err(), "{bad}");
        }
        assert!(parse("#12", None).is_err());
    }
}
