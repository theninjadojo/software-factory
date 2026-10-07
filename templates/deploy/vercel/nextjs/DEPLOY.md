# Deploying to Vercel

Production deploys come from `.github/workflows/deploy.yml`: after CI passes on `main` it applies new database migrations, then
builds with the Vercel CLI and deploys to production. Pull requests get preview deployments from Vercel's Git integration;
`vercel.json` turns that integration off for `main`, so production only ever gets commits that passed CI. Until the steps below
are done, the Deploy workflow skips with a notice and stays green.

## Set it up once

1. **Account and project.** Sign up at https://vercel.com with GitHub. Add New → Project → import this repository (the
   framework is detected as Next.js; leave the build settings as they are). The first deploy may fail for want of a database:
   that is fine.
2. **Database.** In the project: Storage → Create Database → Neon (Postgres) → connect it to the project for all environments.
   This sets `DATABASE_URL` in the project's environment variables. (Any Postgres works: set `DATABASE_URL` yourself.) Copy the
   production connection string from the Neon dashboard for step 5.
3. **The app's settings.** Project → Settings → Environment Variables, for Production (and Preview if you want previews to sign in):
   - `BETTER_AUTH_SECRET`: the output of `openssl rand -base64 32`.
   - `BETTER_AUTH_URL`: the production URL, for example `https://shikumi-app.vercel.app`.
   - For sign-in with GitHub (optional): `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` of an OAuth app made at
     https://github.com/settings/developers with the callback URL `<BETTER_AUTH_URL>/api/auth/callback/github`.
4. **IDs and a token.**
   - A token: Account Settings → Tokens → Create (scope: your team).
   - The IDs: on your machine run `npx vercel link` in the repository, then read `orgId` and `projectId` from
     `.vercel/project.json` (the folder is git-ignored). Or: Team Settings → General → Team ID, and Project → Settings →
     General → Project ID.
5. **GitHub secrets.** In the repository: Settings → Secrets and variables → Actions → New repository secret:
   `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, and `DATABASE_URL` (the production connection string, used to run
   migrations before each deploy).
6. **First deploy.** Actions → Deploy → Run workflow (or merge to `main`). Then open the production URL and `/api/health`.

## Migrations

`npm run db:migrate` runs in the Deploy workflow before each production deploy, against the `DATABASE_URL` secret. Previews share
whatever database their environment points at and do not migrate it: give Preview its own database (Neon branches work well)
and run `DATABASE_URL=... npm run db:migrate` yourself when a pull request adds a migration.

## A custom domain

Project → Settings → Domains → Add, and create the DNS record Vercel shows. Then set `BETTER_AUTH_URL` to the new URL and
update the GitHub OAuth app.

## Roll back

Project → Deployments → pick the last good production deployment → Instant Rollback (or `npx vercel rollback`). Or revert the
commit on `main` and let CI and Deploy ship the revert. Migrations are not rolled back: write them so the previous version still
works with the new schema (add columns first, remove them in a later release).
