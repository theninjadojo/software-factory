# Deploying to Render

`render.yaml` is a Render Blueprint: a web service built from `Dockerfile` and a Render Postgres database. Every merge to `main`
that passes CI triggers a deploy through a deploy hook (`.github/workflows/deploy.yml`); Render's own auto-deploy is off, so a
commit that fails CI is never deployed. Each deploy runs `node scripts/migrate.mjs` before starting the server and checks
`/api/health`. Until the steps below are done, the Deploy workflow skips with a notice and stays green.

## Set it up once

1. **Account.** Sign up at https://render.com and connect your GitHub account (Render asks for access to this repository).
2. **Create the Blueprint.** Dashboard → New → Blueprint → pick this repository. Render reads `render.yaml` and shows the
   `shikumi-app` web service and the `shikumi-app-db` database. It asks for the values marked `sync: false`:
   - `BETTER_AUTH_URL`: `https://shikumi-app.onrender.com` (the service's URL; Render shows it once created, adjust if it differs).
   - `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`: leave empty to turn sign-in with GitHub off, or create an OAuth app at
     https://github.com/settings/developers with the callback URL `<BETTER_AUTH_URL>/api/auth/callback/github`.

   `BETTER_AUTH_SECRET` is generated and `DATABASE_URL` comes from the database. Apply. The first deploy starts at once.
3. **The deploy hook.** Open the `shikumi-app` service → Settings → Deploy Hook, and copy the URL.
4. **GitHub secret.** In the repository: Settings → Secrets and variables → Actions → New repository secret, name
   `RENDER_DEPLOY_HOOK_URL`, value: the hook URL. Keep it secret: anyone with it can trigger a deploy.
5. **Check.** Actions → Deploy → Run workflow. The service's Events tab shows the deploy; then open
   `https://shikumi-app.onrender.com/api/health`.

The free plans are for trying it out: a free web service sleeps when idle and a free database expires after 30 days. Change
`plan` in `render.yaml` (for example `starter` for the service and `basic-256mb` for the database) before real use.

## A custom domain

Service → Settings → Custom Domains → Add, then create the DNS record Render shows (a `CNAME` to `shikumi-app.onrender.com`).
Render issues the certificate. Set `BETTER_AUTH_URL` to the new URL (Environment tab) and update the GitHub OAuth app.

## Roll back

Service → Events → pick the last good deploy → Rollback. Or revert the commit on `main` and let CI and Deploy ship the revert.
Migrations are not rolled back: write them so the previous version still works with the new schema (add columns first, remove
them in a later release).
