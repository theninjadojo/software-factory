import { Link } from 'expo-router';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { OfflineBanner } from '@/components/OfflineBanner';

import { ItemList } from '../components/ItemList';
import { useItems } from '../hooks';

export function ItemsScreen() {
  const items = useItems();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.screen, { paddingBottom: insets.bottom }]}>
      <OfflineBanner />
      {items.isPending ? (
        <ActivityIndicator style={styles.center} accessibilityLabel="Loading items" />
      ) : items.isError && !items.data ? (
        <View style={styles.center}>
          <Text>Could not load items.</Text>
          <Pressable accessibilityRole="button" onPress={() => items.refetch()}>
            <Text style={styles.link}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <ItemList items={items.data ?? []} refreshing={items.isRefetching} onRefresh={() => items.refetch()} />
      )}
      <Link href="/items/new" asChild>
        <Pressable style={styles.add}>
          <Text style={styles.addText}>Add item</Text>
        </Pressable>
      </Link>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  link: { color: '#0369a1', fontWeight: '600', padding: 8 },
  add: { margin: 16, backgroundColor: '#0f172a', borderRadius: 8, padding: 14, alignItems: 'center' },
  addText: { color: 'white', fontWeight: '600', fontSize: 16 },
});
