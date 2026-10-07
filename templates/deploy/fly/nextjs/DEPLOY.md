# Deploying to Fly.io

Every merge to `main` that passes CI is deployed to Fly.io by `.github/workflows/deploy.yml`. Until the steps below are done, the
Deploy workflow skips with a notice and stays green. Fly builds the image from `Dockerfile`, runs `node scripts/migrate.mjs` as the
release command (new migrations are applied before the new version takes traffic) and checks `/api/health`.

## Set it up once

1. **Account and CLI.** Sign up at https://fly.io (a card is required) and install `flyctl`:
   https://fly.io/docs/flyctl/install/. Run `fly auth login`.
2. **Create the app.** From the repository root:
   ```sh
   fly apps create shikumi-app
   ```
   App names are global on Fly. If it is taken, pick another and change `app` and `BETTER_AUTH_URL` in `fly.toml` to match.
   Change `primary_region` in `fly.toml` to the region closest to your users (`fly platform regions`).
3. **Create the database.** Fly Managed Postgres:
   ```sh
   fly mpg create --name shikumi-app-db --region ams
   fly mpg attach <cluster id from the output> --app shikumi-app   # sets the DATABASE_URL secret
   ```
   Any other Postgres works too (Neon, Supabase, ...): `fly secrets set --app shikumi-app DATABASE_URL='postgres://...'`.
4. **Set the app's secrets.**
   ```sh
   fly secrets set --app shikumi-app --stage BETTER_AUTH_SECRET="$(openssl rand -base64 32)"
   ```
   For sign-in with GitHub, create an OAuth app at https://github.com/settings/developers with the homepage
   `https://shikumi-app.fly.dev` and the callback URL `https://shikumi-app.fly.dev/api/auth/callback/github`, then:
   ```sh
   fly secrets set --app shikumi-app --stage GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...
   ```
5. **Let GitHub deploy.** Create a deploy token and add it to GitHub:
   ```sh
   fly tokens create deploy --app shikumi-app --expiry 8760h
   ```
   In the repository on GitHub: Settings → Secrets and variables → Actions → New repository secret, name `FLY_API_TOKEN`,
   value: the whole token printed above (it starts with `FlyV1`).
6. **First deploy.** Actions → Deploy → Run workflow (or merge to `main`). Then open https://shikumi-app.fly.dev and
   https://shikumi-app.fly.dev/api/health. Logs: `fly logs --app shikumi-app`.

## A custom domain

```sh
fly certs add app.example.com --app shikumi-app
```
Create the DNS records it prints (an `A`/`AAAA` record, or a `CNAME` to `shikumi-app.fly.dev`), wait for `fly certs show`
to say the certificate is issued, then set `BETTER_AUTH_URL` in `fly.toml` to `https://app.example.com` and update the GitHub
OAuth app's URLs.

## Roll back

```sh
fly releases --app shikumi-app --image      # find the image of the last good release
fly deploy --app shikumi-app --image registry.fly.io/shikumi-app:deployment-...
```
Or revert the commit on `main`; CI and Deploy ship the revert. Migrations are not rolled back: write them so the previous
version still works with the new schema (add columns first, remove them in a later release).
