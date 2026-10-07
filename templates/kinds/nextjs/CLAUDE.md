# Shikumi App

Next.js (App Router, React Server Components) with TypeScript, Tailwind CSS and shadcn/ui-style components, PostgreSQL through
Drizzle ORM, Better Auth for sign-in, Zod for validation.

## Layout

```
src/
  app/                    routes only: pages, layouts, error boundaries, route handlers (api/health, api/auth)
  features/<feature>/     everything one feature owns
    components/           its UI (server components unless they need state: then "use client")
    actions.ts            its server actions ("use server"): read the form, call the service, return an ActionResult
    service.ts            its business rules and validation; the only caller of the repository
    repository.ts         its database queries (Drizzle), behind an interface the service depends on
    schema.ts             its Zod schemas and types
    service.test.ts       unit tests of the service with a fake repository
  components/ui/          shared shadcn/ui-style components (add more with `npx shadcn@latest add <name>`, needs network)
  db/schema.ts            all tables (Drizzle); db/index.ts the connection
  lib/env.ts              typed environment (Zod); lib/errors.ts AppError and ActionResult; lib/auth.ts Better Auth
drizzle/                  SQL migrations, generated, committed, never edited by hand once merged
e2e/                      Playwright tests
```

`features/notes` is the example: copy its shape for a new feature.

## Conventions

- Pages and actions call a feature's service, never `db()` or a repository directly. A service takes its repository as an argument.
- Validate every input with Zod in the service (or at the edge of a route handler). Throw `AppError` for expected failures
  (`AppError.validation(message, fields)`, `AppError.notFound()`); server actions wrap their work in `toActionResult`, so the UI
  gets `{ ok, data }` or `{ ok: false, error }`. Any other error is a bug: it is logged and the user sees a generic message.
- Read configuration only through `env()` from `src/lib/env.ts`; add new variables to its schema and to `.env.example`.
  It is read lazily, so the build and unit tests need no configuration; the server checks it at start-up and exits if it is wrong.
- Code that needs a signed-in user calls `requireUser()` (or `currentUser()`) from `src/lib/auth.ts`.
- Changing the database: edit `src/db/schema.ts`, run `npm run db:generate`, commit the new file in `drizzle/`.
- Every new feature gets unit tests for its service and, for a user-visible flow, an e2e test.

## Commands

Install needs the network; everything else runs offline once `node_modules` and the Playwright browser are installed.

```sh
npm ci                          # install from the lockfile (network)
npm run dev                     # dev server on :3000 (needs .env and Postgres, see README.md)
npm run lint                    # ESLint
npm run typecheck               # tsc
npm test                        # Vitest unit tests (no database)
npm run build                   # production build (no database or configuration needed)
npm run db:generate             # new migration from src/db/schema.ts (no database)
npm run db:migrate              # apply migrations to DATABASE_URL
npx playwright install chromium # once (network)
npm run test:e2e                # Playwright against the built app; needs Postgres at DATABASE_URL
                                # (default postgres://postgres:postgres@localhost:5432/app); run `npm run build` first
```
