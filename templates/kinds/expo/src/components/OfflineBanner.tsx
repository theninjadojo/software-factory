import { onlineManager } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { StyleSheet, Text, View } from 'react-native';

export function OfflineBanner() {
  const online = useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
  if (online) return null;
  return (
    <View style={styles.banner} accessibilityRole="alert">
      <Text style={styles.text}>You are offline. Showing saved data; changes are sent when you reconnect.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { backgroundColor: '#fef3c7', padding: 12 },
  text: { color: '#78350f' },
});
