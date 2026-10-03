# Working in this repo

## Use a worktree for every change

The main checkout often holds someone's uncommitted work. Do not edit, stash, commit or pull in it.

1. `git fetch origin && git worktree add -b <branch> ../sf-<topic> origin/main` (start from the latest `main`).
2. Make the change there. Run the tests with `.venv/bin/python -m pytest -q --ignore=tests/e2e` (link `.venv` in from the main checkout if the worktree has none).
3. Before merging: `git fetch origin && git rebase origin/main`, fix any conflicts, run the tests again.
4. Push the branch and open a pull request against `main` (`git push -u origin HEAD`, then `gh pr create`). Do not push to `main` directly.
5. Once the PR is merged, clean up: `git worktree remove ../sf-<topic>`, `git branch -D <branch>`, then `git worktree prune`. Leave the worktree in place until then.

Stage only the files that belong to the change (never `git add -A` over someone else's work).
