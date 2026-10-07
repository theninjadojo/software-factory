import AsyncStorage from '@react-native-async-storage/async-storage';
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import { focusManager, onlineManager, QueryClient } from '@tanstack/react-query';
import * as Network from 'expo-network';
import { AppState, Platform } from 'react-native';

const DAY = 24 * 60 * 60 * 1000;

/**
 * Offline-friendly defaults: data stays cached for a day and is persisted to AsyncStorage, so the app opens with
 * the last data it saw; queries and mutations pause while offline and resume when the network comes back.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: DAY,
        staleTime: 30 * 1000,
        retry: 2,
        networkMode: 'offlineFirst',
      },
      mutations: {
        networkMode: 'offlineFirst',
      },
    },
  });
}

export const persister = createAsyncStoragePersister({ storage: AsyncStorage, key: 'shikumi-app-query-cache' });
export const persistMaxAge = DAY;

/** Tell TanStack Query when the device goes online or offline, and when the app comes to the foreground. */
export function connectQueryToDevice(): () => void {
  onlineManager.setEventListener((setOnline) => {
    const subscription = Network.addNetworkStateListener((state) => setOnline(!!state.isConnected));
    return () => subscription.remove();
  });
  const appState = AppState.addEventListener('change', (status) => {
    if (Platform.OS !== 'web') focusManager.setFocused(status === 'active');
  });
  return () => appState.remove();
}
