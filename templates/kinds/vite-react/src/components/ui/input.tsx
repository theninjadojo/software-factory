import type { ComponentProps } from "react";

export function Input({ className = "", ...props }: ComponentProps<"input">) {
  return (
    <input
      className={`h-9 w-full rounded-md border border-neutral-300 bg-transparent px-3 text-sm focus-visible:ring-2 focus-visible:ring-neutral-400 focus-visible:outline-none dark:border-neutral-700 ${className}`}
      {...props}
    />
  );
}
