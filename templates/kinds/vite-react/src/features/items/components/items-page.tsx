import { ItemForm } from "./item-form";
import { ItemList } from "./item-list";

export function ItemsPage() {
  return (
    <div className="grid gap-6">
      <h1 className="text-2xl font-semibold">Items</h1>
      <ItemForm />
      <ItemList />
    </div>
  );
}
