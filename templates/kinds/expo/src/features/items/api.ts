import { api, unwrap, type Schemas } from '@/lib/api/client';

export type Item = Schemas['ItemOut'];
export type NewItem = Schemas['ItemCreate'];

export const itemKeys = {
  all: ['items'] as const,
  create: ['items', 'create'] as const,
};

export async function fetchItems(): Promise<Item[]> {
  return unwrap(await api.GET('/items', { params: { query: { limit: 100 } } }));
}

export async function createItem(body: NewItem): Promise<Item> {
  return unwrap(await api.POST('/items', { body }));
}
