import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import main as m
from factory.classifier import Classification
from factory.config import Config, DEFAULT_ROLES, Route, deep_merge, load, overrides_path
from factory.runner import RunResult
from factory.telegram import Telegram
from factory.tomlw import dumps
from test_roles import CFG, FakeClf, FakeGH, issue


class Storage(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")

    def test_run_lifecycle(self):
        rid = dbm.start_run(self.db, "build", "o/r", 5, "Add thing", "claude-code", "haiku", "low", '{"kind":"bug"}')
        self.assertEqual(dbm.get_run(self.db, rid)["status"], "running")
        dbm.finish_run(self.db, rid, "pr", "ok", "http://pr", "x" * 70000, "L" * 9000)
        run = dbm.get_run(self.db, rid)
        self.assertEqual((run["status"], run["pr_urls"]), ("pr", "http://pr"))
        self.assertEqual((len(run["output"]), len(run["log_tail"])), (dbm.MAX_OUTPUT, dbm.MAX_LOG))   # bounded
        self.assertIsNotNone(run["finished"])

    def test_interrupted_runs_are_marked_on_restart(self):
        a = dbm.start_run(self.db, "build", "o/r", 1, "t", None, None, None)
        b = dbm.start_run(self.db, "stage", "o/r", 2, "t", None, None, None)
        dbm.finish_run(self.db, b, "stage")
        self.assertEqual(dbm.mark_interrupted(self.db), 1)
        self.assertEqual(dbm.get_run(self.db, a)["status"], "interrupted")
        self.assertEqual(dbm.get_run(self.db, b)["status"], "stage")

    def test_filters_events_status_and_decisions(self):
        for i, st in enumerate(("pr", "failed", "pr")):
            dbm.finish_run(self.db, dbm.start_run(self.db, "build", "o/a" if i < 2 else "o/b", i, "t", None, None, None), st)
        self.assertEqual(len(dbm.recent_runs(self.db, status="pr")), 2)
        self.assertEqual(len(dbm.recent_runs(self.db, repo="o/b")), 1)
        self.assertEqual(dbm.recent_runs(self.db, limit=1)[0]["issue"], 2)           # newest first
        dbm.add_event(self.db, "alert:failure", "boom"); dbm.add_event(self.db, "decision", "ignored")
        self.assertEqual([e["kind"] for e in dbm.recent_events(self.db, kind_prefix="alert")], ["alert:failure"])
        dbm.set_status(self.db, "mode", "LIVE"); dbm.set_status(self.db, "mode", "dry-run")
        self.assertEqual(dbm.get_status(self.db)["mode"]["value"], "dry-run")
        dbm.record(self.db, "o/r", 1, "t1", "ignored", "why")
        self.assertEqual(dbm.latest_decisions(self.db)[0]["outcome"], "ignored")


class ConfigOverrides(unittest.TestCase):
    def test_deep_merge_replaces_lists_and_merges_tables(self):
        base = {"general": {"poll_seconds": 60, "dry_run": True}, "github": {"repos": ["a/b"]}}
        out = deep_merge(base, {"general": {"poll_seconds": 30}, "github": {"repos": ["c/d"]}})
        self.assertEqual(out, {"general": {"poll_seconds": 30, "dry_run": True}, "github": {"repos": ["c/d"]}})
        self.assertEqual(base["general"]["poll_seconds"], 60)                      # inputs untouched

    def test_load_applies_overrides_and_never_rewrites_the_base(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            text = Path("config.example.toml").read_text()
            p.write_text(text)
            overrides_path(str(p)).write_text(dumps({"general": {"poll_seconds": 15}, "telegram": {"verbosity": "quiet"}}))
            cfg = load(str(p))
            self.assertEqual((cfg.poll_seconds, cfg.telegram_verbosity), (15, "quiet"))
            self.assertEqual(p.read_text(), text)

    def test_invalid_override_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(Path("config.example.toml").read_text())
            overrides_path(str(p)).write_text(dumps({"runner": {"engine": "rm -rf /"}}))
            with self.assertRaises(ValueError):
                load(str(p))

    def test_toml_writer_roundtrips_awkward_values(self):
        d = {"projects": [{"name": "shop", "description": 'say "hi"\nnewline \\ back ü',
                           "repos": [{"repo": "o/web", "role": "the web app"}]}],
             "roles": {"architect": {"model": "sonnet"}}, "general": {"poll_seconds": 30, "dry_run": False, "x": 0.65},
             "telegram": {"events": ["a", "b"], "empty": []}}
        self.assertEqual(tomllib.loads(dumps(d)), d)


class Instrumentation(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")
        self.patch = mock.patch.object(m, "ev", self.conn)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def poll(self, gh, clf, result):
        def fake_run(*a, **k):
            if k.get("sink") is not None:
                k["sink"]["log"] = "agent said: all done"
            return result
        from dataclasses import replace
        with mock.patch.object(m.runner, "run_task", side_effect=fake_run), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(CFG, db_path=d + "/f.db"), gh, self.conn, clf)

    def test_stage_run_is_recorded_with_output_log_and_classification(self):
        gh = FakeGH({"factory:auto": [issue(labels=["factory:auto"])]})
        self.poll(gh, FakeClf(stage="design"), RunResult("stage", "designer document ready", output="# Design"))
        run = dbm.recent_runs(self.conn)[0]
        full = dbm.get_run(self.conn, run["id"])
        self.assertEqual((full["kind"], full["stage"], full["status"], full["model"]), ("stage", "designer", "stage", "sonnet"))
        self.assertEqual((full["output"], full["log_tail"]), ("# Design", "agent said: all done"))
        self.assertEqual(json.loads(full["classification"])["stage"], "design")
        self.assertEqual(dbm.recent_runs(self.conn, 20, status="running"), [])           # nothing running afterwards
        kinds = [e["kind"] for e in dbm.recent_events(self.conn)]
        self.assertIn("run:start", kinds)
        self.assertIn("run:stage", kinds)

    def test_build_run_records_pr_links_and_decisions_are_on_the_timeline(self):
        gh = FakeGH({"factory:ready": [issue(labels=["factory:ready"])]})
        self.poll(gh, FakeClf(), RunResult("pr", "1 pull request(s) opened", "https://github.com/o/r/pull/9"))
        run = dbm.recent_runs(self.conn)[0]
        self.assertEqual((run["kind"], run["status"], run["pr_urls"]), ("build", "pr", "https://github.com/o/r/pull/9"))
        self.assertTrue(any(e["kind"].startswith("alert:") for e in dbm.recent_events(self.conn)))

    def test_ignored_issue_leaves_a_reason_on_the_timeline(self):
        class Untrusted(FakeGH):
            def permission(self, repo, login): return "read"
        self.poll(Untrusted({"factory:ready": [issue(labels=["factory:ready"])]}), FakeClf(), RunResult("pr", "x"))
        self.assertEqual(dbm.recent_runs(self.conn), [])
        self.assertIn("ignored", dbm.recent_events(self.conn, kind_prefix="decision")[0]["message"])

    def test_suppressed_alerts_are_still_recorded(self):
        with mock.patch.object(m, "tg", mock.Mock()), mock.patch.object(m, "alert_filter", lambda e: False):
            m.alert("quiet please", event="started")
        e = dbm.recent_events(self.conn)[0]
        self.assertEqual(e["kind"], "alert:started")
        self.assertIn("[not sent to Telegram]", e["message"])

    def test_observability_failures_never_break_the_orchestrator(self):
        with mock.patch.object(dbm, "add_event", side_effect=RuntimeError("db locked")):
            m.emit("x", "y")                                                         # must not raise


class RestartMarker(unittest.TestCase):
    def test_restart_only_when_marker_exists_and_marker_is_consumed(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(m.os, "execv") as ex:
            m.maybe_restart(Path(d))
            ex.assert_not_called()
            (Path(d) / "RESTART").write_text("")
            m.maybe_restart(Path(d))
            ex.assert_called_once()
            self.assertFalse((Path(d) / "RESTART").exists())
            self.assertEqual(ex.call_args.args[1][1:3], ["-m", "factory.main"])


class UnknownSenders(unittest.TestCase):
    def test_other_senders_are_remembered_without_their_text_and_not_acted_on(self):
        with tempfile.TemporaryDirectory() as d:
            db = f"{d}/f.db"
            tg = Telegram("t", 111, d, db, ["o/r"])
            with mock.patch.object(tg, "send") as send:
                tg.handle({"update_id": 1, "message": {"chat": {"id": 222, "type": "private"}, "from": {"id": 222, "first_name": "Eve"}, "text": "/pause secret"}})
                tg.handle({"update_id": 2, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Me"}, "text": "/help"}})
            send.assert_called_once()                                                  # only the owner got a reply
            known = json.loads(dbm.get_status(dbm.connect(db))["telegram_unknown_senders"]["value"])
            self.assertEqual([(k["id"], k["name"]) for k in known], [(222, "Eve")])
            self.assertNotIn("secret", json.dumps(known))
            self.assertFalse((Path(d) / "PAUSED").exists())


if __name__ == "__main__":
    unittest.main()


class KindAliasConfig(unittest.TestCase):
    def test_aliases_are_configurable_and_validated(self):
        from factory.classifier import RuleClassifier, kind_from_labels
        self.assertEqual(kind_from_labels({"enhancement"}), "feature")                        # default aliases
        self.assertEqual(kind_from_labels({"enhancement"}, {}), None)                         # turned off
        self.assertEqual(kind_from_labels({"story"}, {"story": "feature"}), "feature")
        self.assertEqual(RuleClassifier({"story": "feature"}).classify("t", "b", ["story", "complexity:low"]).kind, "feature")
        base = Path("config.example.toml").read_text()
        for aliases, ok in (('{ story = "feature" }', True), ('{ story = "rm -rf" }', False)):
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
                f.write(base.replace('[classifier]\n', f'[classifier]\nkind_aliases = {aliases}\n', 1))
            if ok:
                self.assertEqual(load(f.name).kind_aliases, {"story": "feature"})
            else:
                with self.assertRaises(ValueError):
                    load(f.name)
