# Shikumi App

A static content site built with Astro, TypeScript, MDX and Tailwind CSS. It ships HTML and CSS, and no JavaScript
unless a page needs it.

## Layout

```
src/
  content.config.ts      content collections and their schemas (bad front matter fails the build)
  content/blog/          posts, one .md or .mdx file each; the file name is the URL slug
  assets/                images that go through astro:assets (resized, converted to WebP, sized)
  components/            small presentational pieces: Hero, Section, PostCard, Callout, Header, Footer, ...
  layouts/               BaseLayout (every page), PostLayout (a blog post)
  lib/                   plain TypeScript: site settings (site.ts), URL helper (url.ts), post helpers (posts.ts)
  pages/                 routes; blog/[...slug].astro renders each post; rss.xml.ts is the feed
  styles/global.css      Tailwind entry point and theme tokens
public/                  files copied as-is (favicon, robots.txt)
scripts/                 check-budget.mjs (performance budget), check-links.mjs (link check of dist/)
tests/unit/              Vitest: lib functions and components (Astro container API)
tests/e2e/               Playwright against the built site, with an axe accessibility check on each key page
```

## Conventions

- Link to internal pages with `href('/path/')` from `src/lib/url.ts`, never a bare `/path/`: the site may be served
  under a base path (a GitHub Pages project page). URLs end with a slash (`trailingSlash: 'always'`).
- Put images in `src/assets/` and render them with `<Image>` from `astro:assets` (or `hero` in a post's front matter),
  with real `alt` text. Use `public/` only for files that must keep their exact name.
- New content types get a collection with a schema in `src/content.config.ts`.
- Pages compose layouts and section components; keep logic in `src/lib/` where Vitest can test it.
- Style with Tailwind utility classes. Theme tokens live in `src/styles/global.css`.
- Keep the budget in `scripts/check-budget.mjs` green: shrink the asset rather than raise the limit.
- A new key page gets an entry in `tests/e2e/pages.spec.ts` (it then gets the accessibility check too).

## Commands

Install (needs the network): `npm ci`. The browser for end-to-end tests (needs the network, once):
`npx playwright install chromium`.

Everything below works offline once installed:

| What | Command |
|---|---|
| Dev server, http://localhost:4321 | `npm run dev` |
| Lint | `npm run lint` |
| Type-check (TypeScript and .astro files) | `npm run check` |
| Unit tests | `npm test` |
| Build into `dist/` | `npm run build` |
| Performance budget (after build) | `npm run budget` |
| Link check of `dist/` (after build) | `npm run links` |
| End-to-end and accessibility tests (after build) | `npm run test:e2e` |

Before you finish a change, run: `npm run lint && npm run check && npm test && npm run build && npm run budget && npm run links && npm run test:e2e`.

`astro dev` and `astro preview` start in the background when they detect a coding agent; stop them with
`npx astro dev stop` / `npx astro preview stop`, or pass `--ignore-lock` to keep them in the foreground.

Configuration (see `.env.example`): `SITE_URL` (canonical URLs, sitemap, RSS) and `BASE_PATH` (sub-path hosting).
Deployment is described in `DEPLOY.md` when the project has one.
