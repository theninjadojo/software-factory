import unittest

from factory.jev import JevClassifier, build_state, effort_from_score, parse_answers


def answers(kind="bug", kconf=0.9, tier="light", tconf=0.95, effort=0.2, noul=0.1):
    return {
        "kind": {"type": "choice", "choice": kind, "confidence": kconf, "probabilities": {kind: 1}},
        "tier": {"type": "choice", "choice": tier, "confidence": tconf, "probabilities": {tier: 1}},
        "effort": {"type": "score", "score": effort, "confidence": 0.9, "probabilities": {}},
        "needs_human": {"type": "noul", "noul": noul},
    }


class T(unittest.TestCase):
    def test_tier_maps_to_route_level_and_effort(self):
        for tier, level in (("light", "low"), ("standard", "medium"), ("deep", "high")):
            self.assertEqual(parse_answers(answers(tier=tier), set()).complexity, level)
        c = parse_answers(answers(effort=1.8), set())
        self.assertEqual(c.effort, "high")

    def test_confidence_is_min(self):
        self.assertAlmostEqual(parse_answers(answers(tconf=0.55), set()).confidence, 0.55)
        self.assertAlmostEqual(parse_answers(answers(noul=0.6), set()).confidence, 0.6)

    def test_needs_human(self):
        self.assertTrue(parse_answers(answers(noul=0.95), set()).needs_human)

    def test_kind_label_overrides_model(self):
        self.assertEqual(parse_answers(answers(kind="feature"), {"bug"}).kind, "bug")

    def test_complexity_label_is_floor_not_ceiling(self):
        self.assertEqual(parse_answers(answers(tier="light"), {"complexity:high"}).complexity, "high")   # raises
        self.assertEqual(parse_answers(answers(tier="deep"), {"complexity:low"}).complexity, "high")     # cannot lower

    def test_invalid_answers_rejected(self):
        for bad in (answers(kind="rm -rf /"), answers(tier="godmode")):
            with self.assertRaises(ValueError):
                parse_answers(bad, set())

    def test_effort_thresholds(self):
        self.assertEqual([effort_from_score(x) for x in (0.0, 0.59, 0.6, 1.39, 1.4, 2.0)],
                         ["low", "low", "medium", "medium", "high", "high"])

    def test_falls_back_on_error(self):
        def boom(_): raise OSError("down")
        c = JevClassifier("k", post=boom).classify("t", "b", ["bug", "complexity:high"])
        self.assertEqual((c.kind, c.complexity, c.source), ("bug", "high", "labels"))

    def test_falls_back_on_garbage(self):
        c = JevClassifier("k", post=lambda b: {"answers": {"kind": {"type": "choice", "choice": "zzz", "confidence": 1}}}).classify("t", "b", [])
        self.assertLess(c.confidence, 0.6)

    def test_request_includes_discussion_and_questions(self):
        seen = {}
        def post(b): seen.update(b); return {"answers": answers(), "usage": {"cost": 0.00002}}
        c = JevClassifier("k", model="typesafe/jev-1.13", post=post).classify("title", "body", ["x"], ["a comment"])
        self.assertEqual(set(seen["questions"]), {"kind", "tier", "effort", "stage", "needs_human"})
        self.assertEqual(seen["state"]["discussion"], ["a comment"])
        self.assertEqual(c.source, "typesafe/jev-1.13")

    def test_state_truncation(self):
        s = build_state("t" * 999, "b" * 99999, [], ["c" * 9999] * 20)
        self.assertEqual((len(s["title"]), len(s["body"]), len(s["discussion"]), len(s["discussion"][0])), (300, 15000, 10, 1500))
        self.assertNotIn("discussion", build_state("t", "b", [], None))


if __name__ == "__main__":
    unittest.main()


class ProjectContext(unittest.TestCase):
    def test_project_goes_into_state(self):
        s = build_state("t", "b", [], None, {"name": "p", "repositories": {"web": "the web app"}})
        self.assertEqual(s["project"]["repositories"], {"web": "the web app"})
        self.assertNotIn("project", build_state("t", "b", [], None))
