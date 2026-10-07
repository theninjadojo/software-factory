"use client";

import { useActionState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createNote } from "../actions";

export function NoteForm() {
  const [result, action, pending] = useActionState(createNote, null);
  const error = result && !result.ok ? result.error : null;

  return (
    <form action={action} className="grid gap-3">
      <div className="grid gap-1.5">
        <label htmlFor="title" className="text-sm font-medium">
          Title
        </label>
        <Input id="title" name="title" maxLength={120} aria-invalid={!!error?.fields?.title} />
        {error?.fields?.title && <p className="text-destructive text-sm">{error.fields.title[0]}</p>}
      </div>
      <div className="grid gap-1.5">
        <label htmlFor="body" className="text-sm font-medium">
          Note
        </label>
        <textarea
          id="body"
          name="body"
          rows={3}
          maxLength={2000}
          className="border-input focus-visible:ring-ring rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-2"
        />
      </div>
      {error && !error.fields && (
        <p role="alert" className="text-destructive text-sm">
          {error.message}
        </p>
      )}
      <Button type="submit" disabled={pending} className="justify-self-start">
        {pending ? "Saving…" : "Add note"}
      </Button>
    </form>
  );
}
