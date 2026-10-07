# Publishing to PyPI

Publishing a GitHub release runs `.github/workflows/release.yml` (named `Release`). It checks that the release tag matches the
version in `pyproject.toml`, runs the tests, builds the wheel and sdist with `uv build`, and uploads them to PyPI with
[trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts this repository's workflow directly, so there is
no API token to create, store or leak. There is no secret to set either, so this workflow has no "skip when a secret is
missing" step. Until PyPI is set up (below), only a release would fail, and nothing runs until you publish one.

## Set it up once

1. **Accounts.** Make an account on <https://pypi.org> (turn on two-factor authentication: PyPI requires it). Check that the
   name `shikumi-app` is free: <https://pypi.org/project/shikumi-app/> should say "Not Found". If it is taken, change `name`
   in `pyproject.toml` (and the URL in `release.yml`).
2. **A pending trusted publisher.** The project does not exist on PyPI yet, so register a *pending* publisher: on PyPI, open
   **Your account > Publishing > Add a new pending publisher > GitHub**, and enter:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `shikumi-app` |
   | Owner | `shikumi-org` |
   | Repository name | `shikumi-app` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The first successful upload creates the project and turns this into a normal trusted publisher.
3. **The GitHub environment.** In the repository, open **Settings > Environments > New environment**, name it `pypi`. Under
   **Deployment protection rules** you can add yourself as a required reviewer, so every upload waits for your approval.
   (If you skip this step, GitHub creates the environment on the first run, without rules.)
4. **Optional: try TestPyPI first.** Do steps 1 and 2 on <https://test.pypi.org> as well, and add
   `with: repository-url: https://test.pypi.org/legacy/` to the `pypa/gh-action-pypi-publish` step for one release.

## Cut a release

1. Decide the new version from the changes under `## [Unreleased]` in `CHANGELOG.md` (semantic versioning): a fix is a
   patch (`0.1.0` to `0.1.1`), a new feature a minor (`0.2.0`), a breaking change a major (`1.0.0`; while the version is
   `0.x`, a minor).
2. On a branch, set the version and move the changelog entries:

   ```sh
   uv version 0.2.0                 # or: uv version --bump minor
   ```

   In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.2.0] - YYYY-MM-DD`, add a new empty `## [Unreleased]` above it, and
   update the links at the bottom. Merge it to `main` through a pull request as usual.
3. On GitHub, open **Releases > Draft a new release**. Create the tag `v0.2.0` (a `v` and the exact version) on `main`, paste
   the changelog entries as the description, and click **Publish release**.
4. Watch **Actions > Release**. When it is green, the version is on <https://pypi.org/project/shikumi-app/> and
   `pip install shikumi-app==0.2.0` works.

If the tag and the version differ, the workflow stops before uploading anything: delete the release and the tag, fix the
version, and release again.

## Roll back

PyPI never lets you upload the same version twice, and deleting a release breaks anyone who pinned it. To take back a bad
release, **yank** it: on PyPI, open the project, **Manage > Releases > (the version) > Options > Yank**. Installers then skip
it unless it is pinned exactly. Then fix the problem and publish a new patch version.
