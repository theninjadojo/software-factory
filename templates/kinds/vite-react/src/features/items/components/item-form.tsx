import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCreateItem } from "../api";

export function ItemForm() {
  const [name, setName] = useState("");
  const createItem = useCreateItem();

  function submit(event: FormEvent) {
    event.preventDefault();
    createItem.mutate({ name: name.trim() }, { onSuccess: () => setName("") });
  }

  return (
    <form onSubmit={submit} className="grid gap-2">
      <label htmlFor="item-name" className="text-sm font-medium">
        Name
      </label>
      <div className="flex gap-2">
        <Input id="item-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
        <Button type="submit" disabled={createItem.isPending}>
          {createItem.isPending ? "Adding…" : "Add item"}
        </Button>
      </div>
      {createItem.error && (
        <p role="alert" className="text-sm text-red-600">
          {createItem.error.message}
        </p>
      )}
    </form>
  );
}
