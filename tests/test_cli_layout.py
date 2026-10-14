"""The command-line client (cli/) is built and released with the factory: its version is the repository's VERSION, and CI checks
it. Skipped where cli/ is not present (the factory's own image does not copy it)."""
import tomllib
import unittest
from pathlib import Path

CLI = Path("cli")


@unittest.skipUnless((CLI / "Cargo.toml").exists(), "cli/ is not in this copy of the repository")
class CliLayout(unittest.TestCase):
    def test_the_version_comes_from_the_repository(self):
        cargo = tomllib.loads((CLI / "Cargo.toml").read_text())
        self.assertEqual(cargo["package"]["version"], "0.0.0")          # never bumped: a release only edits VERSION
        self.assertIs(cargo["package"]["publish"], False)
        self.assertEqual([b["name"] for b in cargo["bin"]], ["shikumi"])
        self.assertIn('"../VERSION"', (CLI / "build.rs").read_text())
        self.assertTrue((CLI / "Cargo.lock").exists(), "commit Cargo.lock: CI and releases build with --locked")

    def test_ci_checks_it_and_git_and_docker_leave_its_build_out(self):
        ci = Path(".github/workflows/test.yml").read_text()
        job = ci[ci.index("  cli:"):]
        for step in ("cargo fmt --check", "cargo clippy --locked --all-targets -- -D warnings", "cargo test --locked",
                     "x86_64-unknown-linux-musl", "tests.test_cli_e2e"):
            self.assertIn(step, job)
        self.assertIn("runs-on: ubuntu-", job)
        self.assertIn("cli/target/", Path(".gitignore").read_text().splitlines())
        self.assertIn("cli", Path(".dockerignore").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
