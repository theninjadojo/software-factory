# Releasing with Expo EAS

The app ships two ways:

- **Store builds** with [EAS Build](https://docs.expo.dev/build/introduction/): the native app that goes to
  TestFlight / the App Store and Play internal testing / Google Play. Started by hand from the **Build** workflow
  (`.github/workflows/build.yml`) or from your machine. Needed for the first release, and again whenever native code,
  a config plugin or the app version changes.
- **Over-the-air updates** with [EAS Update](https://docs.expo.dev/eas-update/introduction/): after CI passes on
  `main`, the **Deploy** workflow (`.github/workflows/deploy.yml`) publishes the JavaScript and assets to the
  `production` channel. Installed production builds with the same runtime version download it on launch and use it
  on the next start.

The runtime version is the app version (`version` in `app.config.ts`, policy `appVersion`). Bump it when a change
needs a new native build, so an update never reaches a binary it does not fit.

Until the setup below is done, Deploy and Build skip with a notice and stay green.

## Set it up once

You need Node.js and an [Expo account](https://expo.dev/signup). For iOS, an
[Apple Developer Program](https://developer.apple.com/programs/) membership; for Android, a
[Google Play Console](https://play.google.com/console) account.

1. **Log in and link the project.** In a clone of this repository:
   ```sh
   npm ci
   npx eas-cli@latest login
   npx eas-cli@latest init
   ```
   `eas init` creates the project on expo.dev and prints its ID. Because the app config is `app.config.ts`, it cannot
   write the ID itself: paste it into `app.config.ts` (`const easProjectId: string = '<the ID>';`) and commit. That
   turns on EAS Update (`updates.url`) for the next builds.

2. **Choose the app identifier, now.** iOS calls it the bundle identifier and Android the package name; it cannot
   change once the app is in a store. `app.config.ts` derives a default from the GitHub owner and the app's name with
   everything but letters and digits removed, because the two platforms allow different characters (iOS: letters,
   digits, `-` and `.`; Android: letters, digits and `_`, each part starting with a letter): an owner `my-org` and
   an app `my-app` give `com.myorg.myapp`. Check it with `npx expo config --type public | grep -E 'bundleIdentifier|package'`. To
   use your own reverse-DNS name (for example `com.yourcompany.app`), replace `defaultAppId` in `app.config.ts` and
   commit. (The `APP_ID` environment variable overrides it too, but it must then be set everywhere the config is
   read: on your machine, in CI and in the EAS environments. Editing the file is simpler.)

3. **Point each build profile at its API.** `EXPO_PUBLIC_API_URL` is inlined into the JavaScript when it is bundled,
   so it is set per EAS environment (the profiles in `eas.json` use `development`, `preview` and `production`, and so
   does Deploy's `eas update --environment production`):
   ```sh
   npx eas-cli@latest env:set production  --name EXPO_PUBLIC_API_URL --value https://api.example.com --visibility plaintext
   npx eas-cli@latest env:set preview     --name EXPO_PUBLIC_API_URL --value https://staging-api.example.com --visibility plaintext
   npx eas-cli@latest env:set development --name EXPO_PUBLIC_API_URL --value http://192.168.1.10:8000 --visibility plaintext
   ```
   Or on expo.dev: the project → *Environment variables*. Never put a secret in an `EXPO_PUBLIC_` variable: it ends
   up in the app.

4. **Signing credentials.** Let EAS manage them. Run the first build of each platform from your machine, so it can
   ask questions:
   ```sh
   npx eas-cli@latest build --platform ios --profile production       # logs in to Apple, creates the certificate and profile
   npx eas-cli@latest build --platform android --profile production   # generates the upload keystore
   ```
   `npx eas-cli@latest credentials` shows them later. EAS keeps the Android keystore: download a backup from there.

5. **A token for GitHub Actions.** On expo.dev: account settings → *Access tokens* → *Create token* (a robot user's
   token is best for a team). In GitHub: *Settings* → *Secrets and variables* → *Actions* → *New repository secret*,
   name `EXPO_TOKEN`.

## TestFlight (iOS)

1. After the first production iOS build finishes: `npx eas-cli@latest submit --platform ios --latest`. Sign in with
   your Apple ID when asked; EAS creates the app in App Store Connect if needed and can set up an App Store Connect API
   key so later submissions run unattended (from CI with *submit* ticked).
2. In [App Store Connect](https://appstoreconnect.apple.com): the app → *TestFlight*: answer the export compliance
   question, add internal testers (people in your team) or create an external group (needs a short beta review).
3. Testers install the build with the TestFlight app.

## Play internal testing (Android)

1. In the [Play Console](https://play.google.com/console), create the app with the same package name as step 2.
2. Google requires the **first** upload by hand: download the `.aab` from the build's page on expo.dev and upload it
   in *Test and release* → *Testing* → *Internal testing* → *Create new release*. Add testers by email list.
3. For later submissions, create a Google Cloud service account with access to the Play Console app
   (https://expo.fyi/creating-google-service-account), then add its JSON key with
   `npx eas-cli@latest credentials` → Android → production → *Google Service Account*. After that,
   `npx eas-cli@latest submit --platform android --latest` (or *submit* in the Build workflow) sends builds to the
   internal track (`eas.json` → `submit.production.android.track`).

## Day to day

- **JavaScript changes**: merge to `main`. CI passes, Deploy publishes an update to the `production` channel.
- **New native build** (a native module, a config plugin, an SDK upgrade): bump `version` in `app.config.ts`, merge,
  then *Actions* → *Build* → *Run workflow* (platform, `production`, tick *submit* to send it on to TestFlight / Play
  internal testing). Build numbers increase automatically (`autoIncrement`, remote app versions).
- **Testers' builds**: the `preview` profile makes internal-distribution builds (an Android APK, an ad hoc iOS build
  for registered devices: `npx eas-cli@latest device:create`), on the `preview` channel:
  `npx eas-cli@latest update --channel preview --environment preview` sends them an update.
- **Development builds**: `npx eas-cli@latest build --profile development --platform ios` (or android), then
  `npm start`.
- **Store release**: promote the TestFlight build / the internal-testing release in App Store Connect and the Play
  Console when ready.

## Roll back

- **An update**: `npx eas-cli@latest update:rollback` (pick the update group; it republishes the one before it, or the
  build's embedded JavaScript if there is none). On expo.dev: the project → *Updates* shows the groups and their IDs.
  Then revert the commit on `main`, or the next Deploy publishes it again.
- **A store build**: stores do not roll back. Expire the build in TestFlight / halt the rollout in the Play Console,
  and ship a fixed build with a higher build number.
