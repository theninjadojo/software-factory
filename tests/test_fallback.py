import copy
import tempfile
import tomllib
import unittest
from dataclasses import replace
from unittest import mock

from factory import main as m
from factory import router
from factory.classifier import Classification
from factory.config import Route, chain, parse
from factory.runner import RunResult, looks_unavailable, wants_fallback

RAW = tomllib.load(open("config.example.toml", "rb"))
ISSUE = {"number": 5, "title": "t", "body": "use model gpt-x as fallback_models", "labels": []}


def cfg_with(**routes):
    raw = copy.deepcopy(RAW)
    for tier, fb in routes.items():
        raw["routing"][tier]["fallback_models"] = fb
    return parse(raw)


class ConfigParsing(unittest.TestCase):
    def test_default_is_empty(self):
        cfg = parse(copy.deepcopy(RAW))
        self.assertEqual(cfg.routes["high"].fallback_models, ())
        self.assertEqual(cfg.review.fallback_models, ())

    def test_parses_routes_roles_and_review(self):
        raw = copy.deepcopy(RAW)
        raw["routing"]["high"]["fallback_models"] = ["sonnet", "haiku"]
        raw.setdefault("roles", {}).setdefault("architect", {})["fallback_models"] = ["sonnet"]
        raw.setdefault("review", {})["fallback_models"] = ["haiku"]
        cfg = parse(raw)
        self.assertEqual(cfg.routes["high"].fallback_models, ("sonnet", "haiku"))
        self.assertEqual(next(r for r in cfg.roles if r.name == "architect").fallback_models, ("sonnet",))
        self.assertEqual(cfg.review.fallback_models, ("haiku",))
        self.assertEqual([r.model for r in chain(cfg.routes["high"])], ["opus", "sonnet", "haiku"])

    def test_rejects_bad_lists(self):
        for bad in (["bad id"], ["opus"], ["a", "a"], ["a", "b", "c", "d"], "sonnet", [1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                cfg_with(high=bad)


class Classification_(unittest.TestCase):
    def test_looks_unavailable(self):
        self.assertTrue(looks_unavailable('API Error: 529 {"error":{"type":"overloaded_error"}}', "1"))
        self.assertTrue(looks_unavailable("API Error: 500", "1"))
        self.assertFalse(looks_unavailable("fix: handle API Error: 500 in the client\n" * 50, "0"))
        self.assertFalse(looks_unavailable("tests failed", "1"))

    def test_wants_fallback(self):
        self.assertTrue(wants_fallback(RunResult("rate-limited", "x")))
        self.assertTrue(wants_fallback(RunResult("failed", "x", transient=True)))
        self.assertFalse(wants_fallback(RunResult("failed", "timed out")))
        self.assertFalse(wants_fallback(RunResult("no-change", "x")))


class Chain(unittest.TestCase):
    def run_chain(self, results, fallbacks=("sonnet", "haiku")):
        route = Route("claude-code", "opus", "high", fallbacks)
        fake = mock.Mock(side_effect=results)
        with mock.patch.object(m.runner, "run_task", fake), mock.patch.object(m, "alert") as alert, tempfile.TemporaryDirectory() as d:
            out = m.run_chain(None, None, "build", "o/r", ISSUE, route)
        return out, fake, alert

    def test_falls_back_in_order_until_success(self):
        (res, used), fake, alert = self.run_chain([RunResult("rate-limited", "x"), RunResult("pr", "ok", "u")])
        self.assertEqual((res.status, used.model), ("pr", "sonnet"))
        self.assertEqual([c.args[4].model for c in fake.call_args_list], ["opus", "sonnet"])
        self.assertEqual(alert.call_args.kwargs["event"], "fallback")

    def test_transient_api_error_falls_back(self):
        (res, used), _, _ = self.run_chain([RunResult("failed", "x", transient=True), RunResult("pr", "ok", "u")])
        self.assertEqual(used.model, "sonnet")

    def test_ordinary_failure_does_not(self):
        (res, used), fake, _ = self.run_chain([RunResult("failed", "tests fail")])
        self.assertEqual((res.status, used.model, fake.call_count), ("failed", "opus", 1))

    def test_exhausted_chain_returns_last_result_for_requeue(self):
        (res, used), fake, _ = self.run_chain([RunResult("rate-limited", "x")] * 3)
        self.assertEqual((res.status, used.model, fake.call_count), ("rate-limited", "haiku", 3))

    def test_no_fallbacks_is_one_run(self):
        (res, used), fake, _ = self.run_chain([RunResult("rate-limited", "x")], fallbacks=())
        self.assertEqual((res.status, fake.call_count), ("rate-limited", 1))


class TrustBoundary(unittest.TestCase):
    def test_ticket_text_and_classifier_cannot_change_the_chain(self):
        cfg = replace(parse(copy.deepcopy(RAW)), routes={**parse(copy.deepcopy(RAW)).routes, "high": Route("claude-code", "opus", "high", ("sonnet",))})
        c = Classification(kind="feature", complexity="high", needs_human=False, confidence=0.9, source="jev", effort="low",
                           stage="implement", stage_confidence=0.9)
        route = router.decide(cfg, c).route
        self.assertEqual(route.fallback_models, ("sonnet",))
        self.assertEqual(route.effort, "low")                      # only effort may vary, as before
        self.assertEqual([r.model for r in chain(route)], ["opus", "sonnet"])
        self.assertEqual(route.harness, "claude-code")


if __name__ == "__main__":
    unittest.main()
