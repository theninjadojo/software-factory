//! Text from the factory is untrusted: ticket titles, bodies, comments and agent output are written by anyone who can open an
//! issue, and by agents that read them. Before any of it reaches the terminal, the characters a terminal acts on are taken out:
//! C0 controls (ESC starts colour, cursor and title sequences), DEL, C1 controls (U+009B is a one-character CSI on some
//! terminals) and the bidirectional overrides that make text read differently from how it is stored.

fn is_bidi(c: char) -> bool {
    matches!(c, '\u{061C}' | '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}')
}

/// Whether the terminal could act on this character (newline and tab are kept by `clean`).
pub fn is_unsafe(c: char) -> bool {
    (c.is_control() && c != '\n' && c != '\t') || is_bidi(c)
}

/// Text for a block: lines and tabs kept, everything a terminal would act on removed.
pub fn clean(s: &str) -> String {
    s.chars().filter(|&c| !is_unsafe(c)).collect()
}

/// Text for one line (a title, a table cell): as `clean`, with line breaks and tabs turned into spaces and runs of space
/// collapsed.
pub fn clean_line(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut space = false;
    for c in s.chars() {
        if c == '\n' || c == '\t' || c == '\r' || c.is_whitespace() {
            space = !out.is_empty();
            continue;
        }
        if is_unsafe(c) {
            continue;
        }
        if space {
            out.push(' ');
            space = false;
        }
        out.push(c);
    }
    out
}

/// JSON printed for scripts (`--json`) stays valid JSON, but the characters a terminal acts on are written as \u escapes
/// (serde_json escapes C0 already; this covers C1, DEL and the bidi controls).
pub fn escape_json_text(json: &str) -> String {
    let mut out = String::with_capacity(json.len());
    for c in json.chars() {
        if (c == '\u{7f}' || ('\u{80}'..='\u{9f}').contains(&c)) || is_bidi(c) {
            out.push_str(&format!("\\u{:04x}", c as u32));
        } else {
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escape_sequences_and_bidi_controls_are_removed() {
        let evil = "a\x1b[31mred\x1b]0;title\x07\u{9b}2J\u{202e}gnp.exe\u{2066}x\u{7f}";
        let got = clean(evil);
        assert!(
            !got.chars().any(|c| c.is_control() || is_bidi(c)),
            "{got:?}"
        );
        assert_eq!(got, "a[31mred]0;title2Jgnp.exex");
    }

    #[test]
    fn lines_are_kept_in_blocks_and_folded_in_lines() {
        assert_eq!(clean("one\n\ttwo\r\n"), "one\n\ttwo\n");
        assert_eq!(clean_line("  one\n\ttwo   three \r\n"), "one two three");
        assert_eq!(clean_line("é ✓ 日本"), "é ✓ 日本");
    }

    #[test]
    fn json_stays_json() {
        let v = serde_json::json!({"t": "x\u{9b}y\u{202e}z\u{1b}"});
        let s = escape_json_text(&serde_json::to_string(&v).unwrap());
        assert!(!s.chars().any(|c| c.is_control() || is_bidi(c)));
        let back: serde_json::Value = serde_json::from_str(&s).unwrap();
        assert_eq!(back, v);
    }
}
