import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import { createItem, fetchItems, itemKeys, type NewItem } from './api';

export function useItems() {
  return useQuery({ queryKey: itemKeys.all, queryFn: fetchItems });
}

export function useCreateItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: itemKeys.create,
    mutationFn: (body: NewItem) => createItem(body),
    onSettled: () => queryClient.invalidateQueries({ queryKey: itemKeys.all }),
  });
}

/**
 * Registered on the QueryClient so an item created while offline is sent when the app is next online, even after
 * a restart (a persisted, paused mutation needs its function from here).
 */
export function registerItemMutations(queryClient: QueryClient) {
  queryClient.setMutationDefaults(itemKeys.create, { mutationFn: (body: NewItem) => createItem(body) });
}
