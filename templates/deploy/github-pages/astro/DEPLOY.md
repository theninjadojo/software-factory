# Deploying to GitHub Pages

The site is static. After CI passes on `main`, `.github/workflows/deploy.yml` builds it and publishes `dist/` to GitHub
Pages. No secrets are needed. Until Pages is switched on (step 1), the Deploy workflow skips with a notice and stays
green.

## Set it up once

1. **Switch Pages on.** In the repository: *Settings* → *Pages* → *Build and deployment* → *Source*: choose
   **GitHub Actions**. (Not "Deploy from a branch": that publishes the source files, not the built site.)
   GitHub Pages is free for public repositories; a private repository needs a paid plan.
2. That's all. The workflow asks GitHub for the site's address (`actions/configure-pages`) and builds with it:
   - `SITE_URL`, for canonical links, the sitemap and the RSS feed, for example `https://shikumi-org.github.io`
   - `BASE_PATH`, the sub-path of a project page, `/shikumi-app`

   That is why internal links go through `href()` in `src/lib/url.ts`: on a project page the site lives at
   `https://shikumi-org.github.io/shikumi-app/`, not at the root.

## First deploy

*Actions* → *Deploy* → *Run workflow* on `main`. The `deploy` job links to the site when it finishes, and *Settings* →
*Pages* shows the address too. From then on, every push to `main` deploys once CI is green.

To check a project-page build locally: `BASE_PATH=/shikumi-app npm run build && npx astro preview --ignore-lock`, then
open http://localhost:4321/shikumi-app/.

## Custom domain

1. *Settings* → *Pages* → *Custom domain*: enter it (for example `www.example.com`) and save.
2. At your DNS provider, add a `CNAME` record from `www` to `shikumi-org.github.io` (for an apex domain, the `A`
   records listed in GitHub's docs: https://docs.github.com/pages/configuring-a-custom-domain-for-your-github-pages-site).
3. Once the certificate is issued, tick *Enforce HTTPS*.
4. Run Deploy again: the site is then built for the domain, with no base path. No `CNAME` file is needed when Pages
   deploys from Actions.

## Roll back

Revert the commit on `main` (`git revert <sha>` and push); CI and Deploy publish the previous site. To republish an
older build without touching `main`, re-run that commit's Deploy run from the *Actions* tab (*Re-run all jobs*).
