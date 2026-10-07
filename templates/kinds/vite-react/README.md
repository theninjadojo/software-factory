# Shikumi App

<!-- shikumi:summary -->

The browser front end: a single-page app (Vite, React, TanStack Router and Query) that talks to a separate HTTP API.

## Run it locally

You need Node 24 or newer, and the API running (by default at http://localhost:8000; see the API repository's README).

```sh
npm install
cp .env.example .env.local      # only if the API is not at http://localhost:8000
npm run dev                     # http://localhost:5173
```

The API must allow this origin (CORS): `http://localhost:5173` in development.

## Test it

```sh
npm run lint
npm run typecheck
npm test                        # component tests (Vitest, Testing Library), the API client mocked
npm run build
npx playwright install chromium # once
npm run test:e2e                # Playwright against the built app (`vite preview`), the API faked in the browser
```

None of the tests need the API.

## The API client

`src/api/schema.d.ts` holds the API's types, generated from its OpenAPI document. When the API changes, run it locally and
regenerate them, then fix what the type-check finds:

```sh
npm run api:types               # reads $VITE_API_URL/openapi.json (default http://localhost:8000)
npm run typecheck
```

## Deploy

See [DEPLOY.md](DEPLOY.md).
