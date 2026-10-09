# Repository settings

Things that live in GitHub Settings, not in the code, recorded so they can be redone.

## About

- Description: `Label a GitHub issue and sandboxed AI agents analyse, build and propose the pull request. Agents run with no network and no credentials.`
- Topics: `ai-agents`, `github-automation`, `llm`, `sandbox`, `prompt-injection`, `claude-code`, `python`

## Social preview

`docs/assets/social-preview.html` is the source (1280×640, system fonts only). Export it and upload the PNG (under 1 MB) in Settings → General → Social preview:

```bash
chromium --headless --screenshot=docs/assets/social-preview.png --window-size=1280,640 docs/assets/social-preview.html
```

## Before making the repository public

- [x] Every workflow runs on GitHub-hosted runners (a fork's pull request can edit any workflow, so a self-hosted runner on this repository could run its code), and the self-hosted runner is removed from the repository.
- [ ] Settings → Actions → General: require approval for all outside collaborators.
- [x] The full git history is scanned for secrets (for example `gitleaks detect --log-opts="--all"`); anything found is rotated and, if needed, history rewritten.
- [x] Screenshots under `screens/` show no real project names, tokens or ticket text.
- [ ] Private vulnerability reporting is enabled (Security tab), as `SECURITY.md` promises.
- [ ] The `ghcr.io/theninjadojo/shikumi*` packages (five, see [releasing.md](releasing.md)) are public, then the repository.
- [x] Issue templates are added under `.github/ISSUE_TEMPLATE/` by a person (the factory cannot write to `.github/`).
- [ ] After going public: run the install script and `docker pull` anonymously.

## Project site (GitHub Pages)

The landing page lives in `site/` and is deployed by `.github/workflows/pages.yml` on every push to `main` that touches it. One-time setup: **Settings → Pages → Source: GitHub Actions**. The site is then at `https://theninjadojo.github.io/software-factory/`.
