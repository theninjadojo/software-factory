import { useQuery } from "@tanstack/react-query";
import { itemsQuery } from "../api";

export function ItemList() {
  const { data: items, error, isPending } = useQuery(itemsQuery);

  if (isPending) return <p className="text-sm text-neutral-500">Loading…</p>;
  if (error) {
    return (
      <p role="alert" className="text-sm text-red-600">
        Could not load the items: {error.message}
      </p>
    );
  }
  if (items.length === 0) return <p className="text-sm text-neutral-500">No items yet. Add the first one.</p>;

  return (
    <ul aria-label="Items" className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
      {items.map((item) => (
        <li key={item.id} className="flex items-baseline justify-between gap-4 px-4 py-3">
          <span>{item.name}</span>
          <time className="shrink-0 text-xs whitespace-nowrap text-neutral-500" dateTime={item.created_at}>
            {new Date(item.created_at).toLocaleDateString("en-GB", { dateStyle: "medium" })}
          </time>
        </li>
      ))}
    </ul>
  );
}
