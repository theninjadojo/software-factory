"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

// Catches errors thrown while rendering a page; the layout around it stays.
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => console.error(error), [error]);
  return (
    <div role="alert" className="grid gap-4">
      <h1 className="text-xl font-semibold">Something went wrong</h1>
      <p className="text-muted-foreground text-sm">Please try again. If it keeps happening, tell us{error.digest ? ` (reference ${error.digest})` : ""}.</p>
      <Button onClick={reset} className="justify-self-start">
        Try again
      </Button>
    </div>
  );
}
