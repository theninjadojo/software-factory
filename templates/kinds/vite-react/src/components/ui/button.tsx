import type { ComponentProps } from "react";

export function Button({ className = "", ...props }: ComponentProps<"button">) {
  return (
    <button
      className={`inline-flex h-9 shrink-0 items-center whitespace-nowrap justify-center rounded-md bg-neutral-900 px-4 text-sm font-medium text-white hover:bg-neutral-800 focus-visible:ring-2 focus-visible:ring-neutral-400 focus-visible:outline-none disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 ${className}`}
      {...props}
    />
  );
}
