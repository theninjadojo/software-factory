# Shikumi App

<!-- shikumi:summary -->

A Next.js web app with a PostgreSQL database and sign-in with GitHub.

## Run it locally

You need Node 24 or newer and Docker (for Postgres).

```sh
npm install
cp .env.example .env            # then set BETTER_AUTH_SECRET: openssl rand -base64 32
docker run -d --name shikumi-app-db -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=app postgres:18
npm run db:migrate
npm run dev                     # http://localhost:3000
```

Sign-in with GitHub is off until you set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in `.env`
(see `.env.example` for the OAuth app's callback URL). Everything else works without it.

## Test it

```sh
npm run lint
npm run typecheck
npm test                        # unit tests (Vitest), no database needed
npm run build
npx playwright install chromium # once
npm run test:e2e                # end to end (Playwright) against the built app and the Postgres above
```

`npm run test:e2e` uses `DATABASE_URL` if it is set, otherwise `postgres://postgres:postgres@localhost:5432/app`.

## Deploy

See [DEPLOY.md](DEPLOY.md).
