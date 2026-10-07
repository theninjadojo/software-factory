import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useState } from 'react';

import { registerItemMutations } from '@/features/items/hooks';
import { connectQueryToDevice, createQueryClient, persistMaxAge, persister } from '@/lib/query';

export default function RootLayout() {
  const [queryClient] = useState(() => {
    const client = createQueryClient();
    registerItemMutations(client);
    return client;
  });
  useEffect(connectQueryToDevice, []);

  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister, maxAge: persistMaxAge }}
      onSuccess={() => queryClient.resumePausedMutations()}
    >
      <StatusBar style="auto" />
      <Stack>
        <Stack.Screen name="index" options={{ title: 'Items' }} />
        <Stack.Screen name="items/new" options={{ title: 'New item', presentation: 'modal' }} />
      </Stack>
    </PersistQueryClientProvider>
  );
}
