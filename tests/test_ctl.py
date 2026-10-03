import unittest

from factory import config, ctl


class Labels(unittest.TestCase):
    def cfg(self):
        return config.parse(__import__("tomllib").loads(open("config.example.toml").read()))

    def test_every_configured_label_is_listed_once(self):
        names = [n for n, _, _ in ctl.factory_labels(self.cfg())]
        for want in ("factory:ready", "factory:analyze", "factory:design", "factory:architect", "factory:auto", "stage:designed",
                     "factory:review", "factory:fix-conflicts", "factory:skip-mockup", "factory:design-approved", "factory:screens-changed",
                     "factory:failed", "priority: high"):
            self.assertIn(want, names)
        self.assertEqual(len(names), len(set(names)))

    def test_create_labels_continues_and_reports_a_failing_repo(self):
        made = []
        class GH:
            def create_label(self, repo, name, color, desc):
                if repo == "o/bad":
                    raise OSError("403")
                made.append((repo, name))
        self.assertEqual(ctl.create_labels(self.cfg(), GH(), ["o/a", "o/bad", "o/b"]), 1)
        self.assertEqual({r for r, _ in made}, {"o/a", "o/b"})


if __name__ == "__main__":
    unittest.main()
