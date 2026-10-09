# Repository settings

Things that live in GitHub Settings, not in the code, recorded so they can be redone.

## About

- Description: `Open-source, self-hosted AI coding agents: label a GitHub issue and sandboxed agents analyse, build and open the pull request. No network, no credentials.`
- Website: `https://theninjadojo.github.io/software-factory/`
- Topics: `ai-agents`, `ai-coding-agent`, `coding-agent`, `autonomous-agents`, `claude-code`, `claude`, `anthropic`, `github-automation`, `pull-requests`, `llm`, `sandbox`, `prompt-injection`, `self-hosted`, `devtools`, `automation`, `devin-alternative`, `open-source`, `python`

## Social preview

`docs/assets/social-preview.html` is the source (1280×640, the site's fonts). The exported PNG is `site/assets/og.png`: the project site
uses it as its link preview (`og:image`), and the same file is uploaded in Settings → General → Social preview (under 1 MB). After
changing the source, export it again:

```bash
chromium --headless=new --hide-scrollbars --allow-file-access-from-files --window-size=1280,640 \
  --screenshot=site/assets/og.png "file://$PWD/docs/assets/social-preview.html"
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
