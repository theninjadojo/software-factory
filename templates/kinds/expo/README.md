# Shikumi App

<!-- shikumi:summary -->

The iOS and Android app, built with [Expo](https://expo.dev) (React Native), TypeScript and Expo Router. Data comes
from the project's FastAPI backend through a typed client generated from its OpenAPI schema.

## Run it locally

You need Node.js 22 or newer (CI uses the current LTS), and the API running (see the API repository's README).

```sh
npm ci
cp .env.example .env            # set EXPO_PUBLIC_API_URL; on a phone use your computer's LAN IP, not localhost
npm start                       # then press i (iOS simulator), a (Android emulator) or w (web)
```

The app includes `expo-dev-client`: the first time, build a development app with `npx expo run:ios` /
`npx expo run:android` (needs Xcode / Android Studio) or on EAS with `npx eas-cli build --profile development`.
To try it quickly in [Expo Go](https://expo.dev/go) instead, press `s` in the dev server to switch to Expo Go.

When the API changes, regenerate the client types and fix what `tsc` reports:

```sh
EXPO_PUBLIC_API_URL=http://localhost:8000 npm run api:types
npm run typecheck
```

## Test it

```sh
npm run lint
npm run typecheck
npm test                         # Jest, jest-expo and React Native Testing Library
npx expo export --platform web   # bundles the app, as CI does
```

CI (`.github/workflows/ci.yml`) runs these, plus `expo-doctor`, on every pull request and push to `main`.

### Maestro flows

`.maestro/` holds [Maestro](https://maestro.dev) flows for the key journeys. CI does not run them: they need a
simulator or a device.

Locally:

1. Install Maestro: `curl -fsSL "https://get.maestro.mobile.dev" | bash`.
2. Start the API, and install a development build on a booted simulator or emulator (`npx expo run:ios` or
   `npx expo run:android`), with the dev server running (`npm start`).
3. `npm run e2e` (runs every flow; `npm run e2e -- .maestro/items.yaml` runs one). It uses the bundle identifier
   from `app.config.ts`; set `APP_ID` to override.

On EAS: EAS Workflows can build a simulator app and run these flows in the cloud with a `maestro` job. See
https://docs.expo.dev/eas/workflows/examples/e2e-tests/.

## Deploy

See `DEPLOY.md`.
