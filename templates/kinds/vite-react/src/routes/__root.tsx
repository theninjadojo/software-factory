import { createRootRoute, Link, Outlet } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: RootLayout,
  notFoundComponent: () => (
    <div className="grid gap-2">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Link to="/" className="underline">
        Back to the items
      </Link>
    </div>
  ),
});

function RootLayout() {
  return (
    <div className="min-h-dvh">
      <header className="border-b border-neutral-200 dark:border-neutral-800">
        <div className="mx-auto max-w-2xl px-4 py-3">
          <Link to="/" className="font-semibold">
            Shikumi App
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
