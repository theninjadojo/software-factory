import { onlineManager } from '@tanstack/react-query';
import { router } from 'expo-router';

import { ItemForm } from '../components/ItemForm';
import { useCreateItem } from '../hooks';

export function NewItemScreen() {
  const createItem = useCreateItem();

  const submit = (name: string) => {
    createItem.mutate({ name }, { onSuccess: () => router.back() });
    // Offline, the mutation waits and is sent on reconnect: go back to the list straight away.
    if (!onlineManager.isOnline()) router.back();
  };

  return (
    <ItemForm
      onSubmit={submit}
      submitting={createItem.isPending && !createItem.isPaused}
      error={createItem.isError ? 'Could not save the item. Try again.' : undefined}
    />
  );
}
