//! What the factory's API returns (factory/ui/apijson.py). Every field has a default and unknown fields are ignored: within API
//! v1 the factory only adds fields, so a newer factory never breaks an older client.

use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Version {
    pub api: u64,
    pub factory: String,
    pub token: TokenInfo,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct TokenInfo {
    pub name: String,
    pub scope: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Choice {
    pub value: String,
    pub label: String,
    pub text: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Meta {
    pub repos: Vec<String>,
    pub projects: Vec<Choice>,
    pub roles: Vec<String>,
    pub start_options: Vec<Choice>,
    pub filters: Vec<Choice>,
    pub priorities: Vec<String>,
    pub local_tickets: bool,
    pub github_issues: bool,
    pub github_token: bool,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Tokens {
    #[serde(rename = "in")]
    pub input: Option<u64>,
    pub out: Option<u64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Run {
    pub id: u64,
    pub kind: String,
    pub stage: Option<String>,
    pub repo: String,
    pub issue: Option<u64>,
    #[serde(rename = "ref")]
    pub reference: String,
    pub title: String,
    pub harness: Option<String>,
    pub model: Option<String>,
    pub effort: Option<String>,
    pub started: f64,
    pub finished: Option<f64>,
    pub status: String,
    pub detail: String,
    pub pr_urls: Vec<String>,
    pub tokens: Tokens,
    pub output: String,
    pub log_tail: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Tiles {
    pub needs: u64,
    pub asking: u64,
    pub deciding: u64,
    pub running: u64,
    pub working_at: Vec<String>,
    pub open_prs: u64,
    pub checks_running: u64,
    pub checks_failing: u64,
    pub passed_today: u64,
    pub runs_today: u64,
    pub failed: u64,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Queued {
    pub repo: String,
    pub issue: u64,
    pub why: Option<String>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Status {
    pub version: String,
    pub live: bool,
    pub paused: Option<String>,
    pub poll_seconds: f64,
    pub healthy: bool,
    pub last_poll: Option<f64>,
    pub max_parallel: u64,
    pub running: Vec<Run>,
    pub queued: Vec<Queued>,
    pub tiles: Tiles,
    pub counts: BTreeMap<String, u64>,
    pub usage: Option<serde_json::Value>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Pr {
    pub repo: String,
    pub number: u64,
    pub status: String,
    pub rounds: Option<u64>,
    pub summary: String,
    pub open: bool,
    pub mergeable: bool,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct NeedBrief {
    pub kind: String,
    pub reason: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Row {
    pub repo: String,
    pub issue: u64,
    #[serde(rename = "ref")]
    pub reference: String,
    pub local: bool,
    pub title: String,
    pub state: String,
    pub closed: bool,
    pub at: String,
    pub at_label: String,
    pub when: Option<f64>,
    pub why: String,
    pub priority: String,
    pub priority_source: String,
    pub prs: Vec<Pr>,
    pub need: Option<NeedBrief>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct TicketList {
    pub stage: String,
    pub project: String,
    pub counts: BTreeMap<String, u64>,
    pub total: u64,
    pub tickets: Vec<Row>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct OptionChoice {
    pub id: String,
    pub label: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Question {
    pub id: String,
    pub text: String,
    pub options: Vec<OptionChoice>,
    pub recommended: String,
    pub reason: String,
    pub needs_person: bool,
    pub why: String,
    pub pending: bool,
    pub answer: Option<BTreeMap<String, String>>,
    pub auto: Option<String>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Questions {
    pub stage: String,
    pub questions: Vec<Question>,
    pub pending: Vec<String>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Action {
    pub action: String,
    pub text: String,
    pub does: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Need {
    pub kind: String,
    pub reason: String,
    pub questions: Option<Questions>,
    pub actions: Vec<Action>,
    pub ticket_kind: Option<String>,
    pub complexity: Option<String>,
    pub confidence: Option<f64>,
    pub scores: Option<Scores>,
    pub why: String,
}

/// Why the classifier handed a ticket to a person (factory/why.py): each answer's choice and score (`None`: set by a label).
#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Scores {
    pub reason: String,
    pub threshold: f64,
    pub confidence: f64,
    pub source: String,
    pub answers: BTreeMap<String, Option<(String, f64)>>,
    pub missing: Vec<String>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct NeedsRow {
    pub repo: String,
    pub issue: u64,
    #[serde(rename = "ref")]
    pub reference: String,
    pub title: String,
    pub kind: String,
    pub reason: String,
    pub questions: Option<Questions>,
    pub actions: Vec<Action>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct NeedsList {
    pub available: bool,
    pub reason: String,
    pub tickets: Vec<NeedsRow>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Step {
    pub n: u64,
    pub kind: String,
    pub station: String,
    pub state: String,
    pub run_id: Option<u64>,
    pub stage: Option<String>,
    pub status: Option<String>,
    pub model: Option<String>,
    pub effort: Option<String>,
    pub started: Option<f64>,
    pub finished: Option<f64>,
    pub seconds: Option<f64>,
    pub tokens: Option<Tokens>,
    pub message: String,
    pub repeat: Option<u64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Journey {
    pub status: String,
    pub seconds: Option<f64>,
    pub tokens: Tokens,
    pub steps: Vec<Step>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Offers {
    pub note: String,
    pub start: Vec<Action>,
    pub close: bool,
    pub reopen: bool,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Comment {
    pub author: String,
    pub at: Option<serde_json::Value>,
    pub body: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Local {
    pub state: String,
    pub body: String,
    pub labels: Vec<String>,
    pub comments: Vec<Comment>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Outcome {
    pub headline: String,
    pub reason: String,
    pub sentence: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Event {
    pub id: u64,
    pub ts: f64,
    pub kind: String,
    pub repo: Option<String>,
    pub issue: Option<u64>,
    pub message: String,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Ticket {
    #[serde(flatten)]
    pub row: TicketRowFields,
    pub journey: Journey,
    pub need: Option<Need>,
    pub docs: Vec<String>,
    pub offers: Offers,
    pub queued: bool,
    pub outcome: Option<Outcome>,
    pub local: Option<Local>,
    pub events: Vec<Event>,
}

/// The list fields a ticket's detail repeats (Row, without `need`, which the detail gives in full).
#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct TicketRowFields {
    pub repo: String,
    pub issue: u64,
    #[serde(rename = "ref")]
    pub reference: String,
    pub local: bool,
    pub title: String,
    pub state: String,
    pub closed: bool,
    pub at_label: String,
    pub when: Option<f64>,
    pub why: String,
    pub priority: String,
    pub priority_source: String,
    pub prs: Vec<Pr>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Doc {
    pub stage: String,
    pub text: String,
    pub source: String,
    pub cut_short: bool,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct RunList {
    pub page: u64,
    pub more: bool,
    pub runs: Vec<Run>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct EventList {
    pub kind: String,
    pub events: Vec<Event>,
    pub before: Option<u64>,
}

#[derive(Debug, Default, Clone, Deserialize)]
#[serde(default)]
pub struct Done {
    pub ok: bool,
    pub message: String,
    pub done: Option<serde_json::Value>,
    pub failed: Vec<String>,
    pub issue: Option<u64>,
    #[serde(rename = "ref")]
    pub reference: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_ticket_reads_with_fields_missing_and_extra() {
        let t: Ticket = serde_json::from_str(r##"{"repo":"o/r","issue":7,"ref":"#7","title":"T","future":{"x":1},
            "need":{"kind":"questions","questions":{"stage":"analyst","questions":[{"id":"q1","text":"Which?","options":[{"id":"a","label":"A"}],
            "recommended":"a","needs_person":true,"pending":true}],"pending":["q1"]}},"journey":{"steps":[{"n":1,"kind":"run","tokens":{"in":3,"out":null}}]}}"##).unwrap();
        assert_eq!((t.row.reference.as_str(), t.row.issue), ("#7", 7));
        let q = t.need.unwrap().questions.unwrap();
        assert_eq!(
            (q.stage.as_str(), q.questions[0].options[0].label.as_str()),
            ("analyst", "A")
        );
        assert_eq!(t.journey.steps[0].tokens.as_ref().unwrap().input, Some(3));
        let _: Status = serde_json::from_str("{}").unwrap();
        let _: TicketList =
            serde_json::from_str(r#"{"tickets":[{"need":null,"prs":[{"number":3}]}]}"#).unwrap();
    }

    #[test]
    fn a_decision_reads_its_scores() {
        let n: Need = serde_json::from_str(r#"{"kind":"decision","why":"low confidence 0.54 (threshold 0.60)","scores":{"reason":"low",
            "threshold":0.6,"confidence":0.54,"source":"jev","answers":{"size":["deep",0.54],"kind":null},"missing":[]}}"#).unwrap();
        let s = n.scores.unwrap();
        assert_eq!(
            (s.threshold, s.answers["size"].clone()),
            (0.6, Some(("deep".to_string(), 0.54)))
        );
        assert_eq!(s.answers["kind"], None);
        let n: Need = serde_json::from_str(r#"{"kind":"decision","scores":null}"#).unwrap();
        assert!(n.scores.is_none() && n.why.is_empty());
    }
}
