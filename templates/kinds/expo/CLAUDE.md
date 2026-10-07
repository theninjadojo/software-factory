# Shikumi App

An iOS and Android app built with Expo (React Native), TypeScript and Expo Router. It talks to the FastAPI backend
(a separate repository) through a typed client generated from the API's OpenAPI schema.

Expo changes between SDK releases. Before using an Expo or React Native API, check the docs for the SDK in
`package.json` (`expo` ~57): https://docs.expo.dev/versions/v57.0.0/ (needs the network).

## Layout

```
app.config.ts                app name, slug, scheme, bundle identifier (APP_ID), EAS project, plugins
src/
  app/                       Expo Router routes, one file per screen. Keep them thin: import a screen from a feature.
    _layout.tsx              providers (TanStack Query with a persisted cache) and the navigation stack
    index.tsx                -> features/items/screens/ItemsScreen
    items/new.tsx            -> features/items/screens/NewItemScreen (a modal)
  features/<feature>/
    api.ts                   calls to the API through the typed client, and query keys
    hooks.ts                 TanStack Query hooks (useQuery / useMutation) the screens use
    components/              presentational components: props in, callbacks out, no data fetching
    screens/                 a screen: wires hooks to components, handles navigation
    __tests__/               Jest and React Native Testing Library tests for the feature
  components/                shared components (OfflineBanner)
  lib/api/                   openapi.json (the API's schema), schema.ts (generated types), client.ts (openapi-fetch)
  lib/query.ts               QueryClient defaults, AsyncStorage persister, online and app-focus wiring
.maestro/                    Maestro flows for key journeys (run on a simulator or device, not in CI)
```

## Conventions

- A new feature gets a folder under `src/features/` with the same shape as `items`, and route files in `src/app/`
  that only re-export its screens.
- Never call `fetch` directly: use `api` from `src/lib/api/client.ts` and `unwrap()` the result. When the API
  changes, regenerate the types (below) and let `tsc` show what to update. Do not edit `schema.ts` by hand.
- Data fetching goes through TanStack Query hooks with keys from the feature's `api.ts`. The cache is persisted, so
  the app opens offline with the last data it saw; queries and mutations pause offline and resume on reconnect. A
  mutation that should survive a restart while offline needs `setMutationDefaults` (see `registerItemMutations`).
- Configuration comes from `EXPO_PUBLIC_*` variables (see `.env.example`). They are inlined into the bundle: never
  put a secret in one.
- Add packages with `npx expo install <package>` (it picks versions that match the SDK), not `npm install`.
- `ios/` and `android/` are generated (Continuous Native Generation) and ignored: configure native behaviour in
  `app.config.ts` and config plugins, never by editing native folders.
- Tests mock the API client (`jest.mock('@/lib/api/client')`), not the hooks; query elements by role, label or text.
- Give interactive elements an accessible role and label; Maestro flows tap by text or `testID`.

## Commands

Install (needs the network): `npm ci`. Everything else below works offline once installed, unless marked.

| What | Command |
|---|---|
| Dev server (then `i` / `a` / `w`; needs a simulator, device or browser) | `npm start` |
| Lint | `npm run lint` |
| Type-check | `npm run typecheck` |
| Unit and component tests | `npm test` |
| Bundle check (what CI's build step runs) | `npx expo export --platform web` |
| Dependency and config check (needs the network) | `npm run doctor` |
| Regenerate API types from the running API (needs the API) | `npm run api:types` |
| Regenerate API types from `src/lib/api/openapi.json` (needs the network once, for npx) | `npm run api:types:local` |
| Maestro flows (needs Maestro, a simulator with a dev build, the API) | `npm run e2e` |

Before you finish a change, run: `npm run lint && npm run typecheck && npm test`.

The app has `expo-dev-client`, so `npm start` serves a development build; press `s` to switch to Expo Go.
Releases (EAS Build, EAS Update) are described in `DEPLOY.md` when the project has one.
