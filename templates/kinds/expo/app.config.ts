import type { ExpoConfig } from 'expo/config';

// The iOS bundle identifier and the Android package name. Both must be reverse-DNS: iOS allows letters, digits, '-'
// and '.', Android allows letters, digits and '_' with each part starting with a letter. Letters and digits suit
// both, so the default strips everything else from the owner and the app's name. Set APP_ID to choose your own
// (it cannot change once the app is in a store). See DEPLOY.md.
function idPart(text: string): string {
  const part = text.toLowerCase().replace(/[^a-z0-9]/g, '');
  return /^[a-z]/.test(part) ? part : `app${part}`;
}
export const defaultAppId = `com.${idPart('shikumi-org')}.${idPart('shikumi-app')}`;
const appId = process.env.APP_ID || defaultAppId;

// The EAS project this app belongs to: paste the ID that `eas init` prints (see DEPLOY.md). Until then, EAS Update
// is off and the app only loads the JavaScript it was built with.
const easProjectId: string = '';

const config: ExpoConfig = {
  name: 'Shikumi App',
  slug: 'shikumi-app',
  scheme: idPart('shikumi-app'),
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/images/icon.png',
  userInterfaceStyle: 'light', // add a dark theme before switching to 'automatic'
  ios: {
    bundleIdentifier: appId,
    supportsTablet: true,
  },
  android: {
    package: appId,
    adaptiveIcon: {
      foregroundImage: './assets/images/adaptive-icon.png',
      backgroundColor: '#0f172a',
    },
  },
  web: {
    output: 'single',
    favicon: './assets/images/favicon.png',
  },
  plugins: [
    'expo-router',
    [
      'expo-splash-screen',
      {
        image: './assets/images/splash-icon.png',
        imageWidth: 120,
        backgroundColor: '#0f172a',
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
  },
  runtimeVersion: { policy: 'appVersion' },
  ...(easProjectId && {
    extra: { eas: { projectId: easProjectId } },
    updates: { url: `https://u.expo.dev/${easProjectId}` },
  }),
};

export default config;
