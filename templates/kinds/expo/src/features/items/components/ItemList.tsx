import { FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';

import type { Item } from '../api';

type Props = {
  items: Item[];
  refreshing: boolean;
  onRefresh: () => void;
};

export function ItemList({ items, refreshing, onRefresh }: Props) {
  return (
    <FlatList
      data={items}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListEmptyComponent={<Text style={styles.empty}>No items yet. Add the first one.</Text>}
      renderItem={({ item }) => (
        <View style={styles.row}>
          <Text style={styles.name}>{item.name}</Text>
          <Text style={styles.date}>{new Date(item.created_at).toLocaleString()}</Text>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth, borderColor: '#cbd5e1' },
  name: { fontSize: 16, fontWeight: '600' },
  date: { marginTop: 2, color: '#475569' },
  empty: { padding: 16, color: '#475569' },
});
