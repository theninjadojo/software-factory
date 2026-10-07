import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, toApiError, type Schemas } from "@/api/client";

export type Item = Schemas["ItemOut"];
export type NewItem = Schemas["ItemCreate"];

// The items feature's server state: one query and one mutation, both through the generated client.
export const itemsQuery = queryOptions({
  queryKey: ["items"],
  queryFn: async (): Promise<Item[]> => {
    const { data, error, response } = await api.GET("/items");
    if (error !== undefined) throw toApiError(response, error);
    return data;
  },
});

export function useCreateItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: NewItem): Promise<Item> => {
      const { data, error, response } = await api.POST("/items", { body });
      if (error !== undefined) throw toApiError(response, error);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itemsQuery.queryKey }),
  });
}
