# Shikumi App

The browser front end of a two-repository project: Vite, React and TypeScript, TanStack Router (file-based routes) and
TanStack Query, Tailwind CSS. It talks to a separate FastAPI API at `VITE_API_URL` through a typed client generated from the
API's OpenAPI document (openapi-typescript and openapi-fetch).

## Layout

```
src/
  main.tsx                 the app: QueryClient, router
  routes/                  one file per route (TanStack Router). Keep them thin: they render a feature's page.
  routeTree.gen.ts         generated from routes/ by the Vite plugin on dev/build/test; committed; never edit
  api/schema.d.ts          generated API types (`npm run api:types`); committed; never edit
  api/client.ts            the openapi-fetch client, ApiError and toApiError
  features/<feature>/
    api.ts                 its queries and mutations (TanStack Query) through the client
    components/            its UI, with tests next to them (*.test.tsx)
  components/ui/           shared UI components
  lib/config.ts            build-time configuration (VITE_ variables)
e2e/                       Playwright tests; the API is faked with page.route
```

`features/items` is the example: copy its shape for a new feature.

## Conventions

- Components never call `fetch` or the client directly: they use the hooks and query options in their feature's `api.ts`.
- Every API call goes through `api` from `src/api/client.ts`, so paths, parameters and bodies are type-checked against the
  API's contract. An error answer becomes an `ApiError` (`toApiError`) with a message fit to show.
- When the API changes: `npm run api:types` (needs the API running, at `VITE_API_URL`), then `npm run typecheck`, and commit
  the new `src/api/schema.d.ts` with the code that uses it. Without network or the API, do not hand-edit the schema: say the
  API change is needed.
- Server state lives in TanStack Query (invalidate the query after a mutation); local UI state in React state.
- Configuration is read only in `src/lib/config.ts`. `VITE_` variables are public: they are compiled into the bundle.
- Component tests mock `@/api/client` (see `items-page.test.tsx`); e2e tests fake the API with `page.route`.

## Commands

Install needs the network; everything else runs offline once `node_modules` and the Playwright browser are installed.

```sh
npm ci                          # install from the lockfile (network)
npm run dev                     # dev server on :5173 (expects the API at VITE_API_URL, default http://localhost:8000)
npm run lint                    # ESLint
npm run typecheck               # tsc
npm test                        # Vitest component and unit tests
npm run build                   # production build into dist/
npx playwright install chromium # once (network)
npm run test:e2e                # Playwright against `vite preview` of dist/; run `npm run build` first
npm run api:types               # regenerate src/api/schema.d.ts from $VITE_API_URL/openapi.json (needs the API)
```
