import { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

type Props = {
  onSubmit: (name: string) => void;
  submitting?: boolean;
  error?: string;
};

export const MAX_NAME = 200;

export function ItemForm({ onSubmit, submitting = false, error }: Props) {
  const [name, setName] = useState('');
  const trimmed = name.trim();
  const valid = trimmed.length > 0 && trimmed.length <= MAX_NAME;

  return (
    <View style={styles.form}>
      <Text style={styles.label} nativeID="item-name-label">
        Name
      </Text>
      <TextInput
        testID="item-name-input"
        accessibilityLabelledBy="item-name-label"
        accessibilityLabel="Name"
        value={name}
        onChangeText={setName}
        maxLength={MAX_NAME}
        autoFocus
        returnKeyType="done"
        onSubmitEditing={() => valid && onSubmit(trimmed)}
        style={styles.input}
      />
      {error ? (
        <Text style={styles.error} accessibilityRole="alert">
          {error}
        </Text>
      ) : null}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: !valid || submitting }}
        disabled={!valid || submitting}
        onPress={() => onSubmit(trimmed)}
        style={[styles.button, (!valid || submitting) && styles.disabled]}
      >
        <Text style={styles.buttonText}>{submitting ? 'Saving…' : 'Save'}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  form: { padding: 16, gap: 8 },
  label: { fontWeight: '600' },
  input: { borderWidth: 1, borderColor: '#94a3b8', borderRadius: 8, padding: 12, fontSize: 16 },
  error: { color: '#b91c1c' },
  button: { marginTop: 8, backgroundColor: '#0f172a', borderRadius: 8, padding: 14, alignItems: 'center' },
  disabled: { opacity: 0.5 },
  buttonText: { color: 'white', fontWeight: '600', fontSize: 16 },
});
