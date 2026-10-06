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

- [ ] Pull-request CI jobs run on GitHub-hosted runners, not self-hosted ones (`.github/workflows/test.yml`); self-hosted only for `release.yml`.
- [ ] Settings → Actions → General: require approval for all outside collaborators.
- [ ] The full git history is scanned for secrets (for example `gitleaks detect --log-opts="--all"`); anything found is rotated and, if needed, history rewritten.
- [ ] Screenshots under `screens/` show no real project names, tokens or ticket text.
- [ ] Private vulnerability reporting is enabled (Security tab), as `SECURITY.md` promises.
- [ ] The `ghcr.io/theninjadojo/shikumi*` packages (five, see [releasing.md](releasing.md)) are public, then the repository.
- [ ] Issue templates are added under `.github/ISSUE_TEMPLATE/` by a person (the factory cannot write to `.github/`).
- [ ] After going public: run the install script and `docker pull` anonymously.
