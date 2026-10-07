# Shikumi App

<!-- shikumi:summary -->

A static site built with [Astro](https://astro.build), TypeScript, MDX and Tailwind CSS.

## Run it locally

You need Node.js 22.12 or newer (CI uses the current LTS).

```sh
npm ci
npm run dev        # http://localhost:4321
```

Write posts in `src/content/blog/` as Markdown or MDX. The front matter is checked against the schema in
`src/content.config.ts`; the build fails on a missing title or a bad date.

## Test it

```sh
npm run lint
npm run check                         # TypeScript and .astro type-check
npm test                              # Vitest
npm run build
npm run budget                        # performance budget of dist/
npm run links                         # broken links in dist/
npx playwright install chromium       # once
npm run test:e2e                      # Playwright and axe accessibility checks against astro preview
```

CI (`.github/workflows/ci.yml`) runs all of these on every pull request and push to `main`.

## Deploy

See `DEPLOY.md`.
