"""Ticket journey (#71): token counts per run, CI rounds on the ticket, and the ordered route a ticket took."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory import ci
from factory import db as dbm
from factory.config import RunnerCfg, _harnesses, default_harnesses
from factory.runner import parse_usage
from test_ci import CFG, FakeGH, run as check

T0 = 1_000_000.0


def claude_json(result="# The plan", **usage):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result,
                       "usage": usage or {"input_tokens": 12, "output_tokens": 3400, "cache_read_input_tokens": 50000,
                                          "cache_creation_input_tokens": 700}})


class Usage(unittest.TestCase):
    """The agent writes agent.log: its usage figures are untrusted and only validated whole numbers are kept."""

    def test_claude_json_gives_the_document_and_the_counts(self):
        text, usage = parse_usage(claude_json(), "claude-json")
        self.assertEqual(text, "# The plan")
        self.assertEqual(usage, {"tokens_in": 12, "tokens_out": 3400, "tokens_cache_read": 50000, "tokens_cache_write": 700})

    def test_stderr_before_the_result_line_is_kept_as_text(self):
        text, usage = parse_usage("warning: something\n" + claude_json("done") + "\n", "claude-json")
        self.assertEqual(text, "warning: something\ndone")
        self.assertEqual(usage["tokens_out"], 3400)

    def test_plain_text_falls_back_with_no_counts(self):
        for fmt in ("claude-json", ""):
            self.assertEqual(parse_usage("Summary: did the thing", fmt), ("Summary: did the thing", None))
        self.assertEqual(parse_usage(claude_json(), ""), (claude_json(), None))      # the harness does not report usage
        self.assertEqual(parse_usage("", "claude-json"), ("", None))

    def test_bad_numbers_are_dropped(self):
        _, usage = parse_usage(claude_json(input_tokens=True, output_tokens=-1, cache_read_input_tokens=10 ** 12,
                                           cache_creation_input_tokens="9"), "claude-json")
        self.assertIsNone(usage)
        _, usage = parse_usage(claude_json(input_tokens=5, output_tokens=2.5), "claude-json")
        self.assertEqual(usage, {"tokens_in": 5})

    def test_other_json_is_not_a_result(self):
        for raw in ('{"type": "assistant", "usage": {"input_tokens": 1}}', "[1, 2]", '"text"', "{not json"):
            self.assertEqual(parse_usage(raw, "claude-json"), (raw, None))
        deep = "[" * 100000
        self.assertEqual(parse_usage(deep, "claude-json"), (deep, None))
        big = claude_json("x" * (dbm.MAX_OUTPUT * 40))
        self.assertEqual(parse_usage(big, "claude-json"), (big, None))              # too large to parse

    def test_claude_code_reports_usage_and_others_do_not(self):
        hs = default_harnesses(RunnerCfg())
        self.assertEqual(hs["claude-code"].usage_format, "claude-json")
        self.assertIn("--output-format json", hs["claude-code"].command)
        self.assertEqual({h.usage_format for n, h in hs.items() if n != "claude-code"}, {""})

    def test_unknown_usage_format_is_refused(self):
        self.assertEqual(_harnesses({"harnesses": {"codex": {"usage_format": "claude-json"}}}, RunnerCfg())["codex"].usage_format, "claude-json")
        with self.assertRaises(ValueError):
            _harnesses({"harnesses": {"claude-code": {"usage_format": "eval"}}}, RunnerCfg())


class Storage(unittest.TestCase):
    def test_finish_run_stores_tokens_or_null(self):
        db = dbm.connect(":memory:")
        a = dbm.start_run(db, "build", "o/r", 1, "t", "claude-code", "opus", "high")
        b = dbm.start_run(db, "build", "o/r", 1, "t", "codex", "gpt", "high")
        dbm.finish_run(db, a, "pr", usage={"tokens_in": 5, "tokens_out": 7, "tokens_cache_read": "x"})
        dbm.finish_run(db, b, "pr")
        self.assertEqual(db.execute("SELECT tokens_in, tokens_out, tokens_cache_read, tokens_cache_write FROM runs WHERE id=?", (a,)).fetchone(),
                         (5, 7, None, None))
        self.assertEqual(db.execute("SELECT tokens_in, tokens_out FROM runs WHERE id=?", (b,)).fetchone(), (None, None))

    def test_an_older_database_is_upgraded_in_place(self):
        with tempfile.TemporaryDirectory() as t:
            path = str(Path(t) / "f.db")
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stage TEXT, repo TEXT NOT NULL, "
                        "issue INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', harness TEXT, model TEXT, effort TEXT, "
                        "classification TEXT NOT NULL DEFAULT '', started REAL NOT NULL, finished REAL, status TEXT NOT NULL DEFAULT 'running', "
                        "detail TEXT NOT NULL DEFAULT '', pr_urls TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '', "
                        "log_tail TEXT NOT NULL DEFAULT '')")
            old.execute("CREATE TABLE prs (repo TEXT, number INTEGER, issue_repo TEXT, issue_num INTEGER)")
            old.execute("INSERT INTO runs (kind, repo, issue, model, started, finished, status) VALUES ('build','o/r',1,'opus',?,?,'pr')", (T0, T0 + 60))
            old.commit()
            j = dbm.journey(old, "o/r", 1, now=T0 + 100)                            # the read-only UI before the upgrade
            self.assertEqual((len(j["steps"]), j["steps"][0]["tokens"], j["tokens"]), (1, {"in": None, "out": None}, {"in": None, "out": None}))
            old.close()
            dbm.connect(path).close()
            db = dbm.connect(path)                                                  # twice: the upgrade is idempotent
            cols = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            self.assertTrue(set(dbm.TOKEN_COLS) <= cols)
            self.assertIsNone(db.execute("SELECT tokens_in FROM runs").fetchone()[0])


class CiEvents(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")
        dbm.watch_pr(self.conn, "o/web", 3, "o/tickets", 8)                        # the PR's repo is not the ticket's

    def go(self, gh):
        ci.watch_ci(CFG, gh, self.conn, lambda *a: None, lambda *a: True)

    def events(self):
        return self.conn.execute("SELECT kind, repo, issue, message FROM events WHERE kind LIKE 'ci:%' ORDER BY id").fetchall()

    def test_each_round_is_recorded_on_the_ticket(self):
        self.go(FakeGH([check("unit", conclusion="failure")]))
        self.conn.execute("UPDATE prs SET watch_started=?", (0,))
        self.go(FakeGH([check("unit"), check("lint", i=2)]))
        self.assertEqual(self.events(), [("ci:failed", "o/tickets", 8, "o/web#3: failing: unit"),
                                         ("ci:passed", "o/tickets", 8, "o/web#3: 2 checks passed")])


class Journey(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")

    def run_(self, at, kind, stage=None, status="pr", took=60, model="opus", usage=None, issue=4):
        rid = dbm.start_run(self.db, kind, "o/r", issue, "t", "claude-code", model, "high", stage=stage)
        self.db.execute("UPDATE runs SET started=? WHERE id=?", (T0 + at, rid))
        if status != "running":
            dbm.finish_run(self.db, rid, status, usage=usage)
            self.db.execute("UPDATE runs SET finished=? WHERE id=?", (T0 + at + took, rid))
        self.db.commit()
        return rid

    def event(self, at, kind, message, issue=4):
        dbm.add_event(self.db, kind, message, "o/r", issue)
        self.db.execute("UPDATE events SET ts=? WHERE id=(SELECT MAX(id) FROM events)", (T0 + at,))
        self.db.commit()

    def route(self, now=10_000):
        j = dbm.journey(self.db, "o/r", 4, now=T0 + now)
        return j, [(s["n"], s["station"], s["state"]) for s in j["steps"]]

    def test_skipped_stages_go_straight_from_routing_to_build(self):
        self.event(0, "decision", "auto: build (build, 0.91, rules); skipped analyst, designer, architect")
        self.run_(10, "build", took=300, usage={"tokens_in": 10, "tokens_out": 2000, "tokens_cache_read": 900, "tokens_cache_write": 90})
        self.event(400, "ci:passed", "o/r#5: 3 checks passed")
        self.run_(500, "build", issue=99)                                          # another ticket
        j, steps = self.route()
        self.assertEqual(steps, [(1, "classify", "done"), (2, "build", "done"), (3, "ci", "done")])
        self.assertEqual(j["status"], "done")
        self.assertEqual((j["steps"][1]["seconds"], j["steps"][1]["tokens"]), (300, {"in": 1000, "out": 2000}))   # cache counts as in
        self.assertEqual((j["seconds"], j["tokens"], j["models"]), (400, {"in": 1000, "out": 2000}, {"opus": 1}))

    def test_a_failed_ci_loop_is_numbered_visit_by_visit(self):
        self.event(0, "decision", "auto: build (build, 0.9, rules); skipped analyst")
        self.run_(10, "build", usage={"tokens_in": 1, "tokens_out": 2})
        self.event(200, "ci:failed", "o/r#5: failing: unit")
        self.run_(200, "fix", model="sonnet", usage={"tokens_in": 3, "tokens_out": 4})   # same tick: the result comes first
        self.event(400, "ci:passed", "o/r#5: 1 checks passed")
        j, steps = self.route()
        self.assertEqual(steps, [(1, "classify", "done"), (2, "build", "done"), (3, "ci", "failed"), (4, "build", "done"), (5, "ci", "done")])
        self.assertEqual(j["steps"][3]["run_kind"], "fix")
        self.assertEqual((j["status"], j["tokens"], j["models"]), ("done", {"in": 4, "out": 6}, {"opus": 1, "sonnet": 1}))

    def test_a_running_ticket_shows_time_so_far_and_no_tokens_yet(self):
        self.run_(0, "stage", "analyst", status="stage", usage={"tokens_in": 5, "tokens_out": 5})
        self.run_(100, "stage", "architect", status="running")
        j, steps = self.route(now=160)
        self.assertEqual(steps, [(1, "analyst", "done"), (2, "architect", "running")])
        self.assertEqual((j["status"], j["finished"], j["seconds"]), ("running", None, 160))
        self.assertEqual((j["steps"][1]["seconds"], j["steps"][1]["tokens"]), (60, {"in": None, "out": None}))   # dash until it ends
        self.assertEqual(j["tokens"], {"in": 5, "out": 5})

    def test_a_ticket_waiting_on_questions_ends_at_the_stage_that_asked(self):
        self.run_(0, "stage", "analyst", status="stage")
        dbm.set_questions(self.db, "o/r", 4, "analyst", 2)
        self.db.execute("UPDATE open_questions SET updated=?", (T0 + 70,))
        self.db.commit()
        j, steps = self.route()
        self.assertEqual(steps, [(1, "analyst", "done"), (2, "needs", "waiting")])
        self.assertEqual((j["status"], j["waiting"]["stage"], j["waiting"]["pending"]), ("waiting", "analyst", 2))
        self.assertIsNone(j["finished"])
        dbm.set_questions(self.db, "o/r", 4, "analyst", 0)                          # answered, and the next stage runs
        self.run_(200, "stage", "architect", status="stage")
        self.assertEqual(self.route()[1], [(1, "analyst", "done"), (2, "architect", "done")])

    def test_a_ticket_the_router_handed_to_a_person(self):
        self.event(0, "decision", "human: low confidence (bug/high, 0.41)")
        j, steps = self.route()
        self.assertEqual((steps, j["status"]), ([(1, "needs", "waiting")], "waiting"))
        self.run_(100, "build")                                                     # a person said build
        self.assertEqual(self.route()[1], [(1, "needs", "done"), (2, "build", "done")])

    def test_failed_and_requeued_runs(self):
        self.run_(0, "build", status="rate-limited")
        self.assertEqual(self.route()[0]["status"], "queued")
        self.run_(100, "build", status="failed")
        j, steps = self.route()
        self.assertEqual((steps, j["status"]), ([(1, "build", "queued"), (2, "build", "failed")], "failed"))

    def test_nothing_recorded(self):
        j = dbm.journey(self.db, "o/r", 4)
        self.assertEqual((j["steps"], j["status"], j["seconds"], j["tokens"], j["waiting"]), ([], "none", None, {"in": None, "out": None}, None))


if __name__ == "__main__":
    unittest.main()
