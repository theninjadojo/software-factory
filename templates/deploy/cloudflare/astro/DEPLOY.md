# Deploying to Cloudflare Pages

The site is static: `npm run build` writes it to `dist/`, and `.github/workflows/deploy.yml` uploads that folder to
Cloudflare Pages with `wrangler pages deploy` after CI passes on `main`. Until the secrets below exist, the Deploy
workflow skips with a notice and stays green.

## Set it up once

1. **Cloudflare account.** Sign up at https://dash.cloudflare.com (the free plan is enough).
2. **Create the Pages project** (named in `wrangler.toml`). Either in the dashboard: *Workers & Pages* → *Create* →
   *Pages* → *Use direct upload*, name it `shikumi-app`, production branch `main`; or from a terminal:
   ```sh
   npx wrangler login
   npx wrangler pages project create shikumi-app --production-branch=main
   ```
   Do not connect the Git repository in Cloudflare: GitHub Actions does the deploys, after CI.
3. **API token.** *My Profile* → *API Tokens* → *Create Token* → *Create Custom Token*, with the permission
   *Account* → *Cloudflare Pages* → *Edit*, limited to your account. Copy the token.
4. **Account ID.** Shown on the *Workers & Pages* overview page (right-hand side), or in the URL of the dashboard.
5. **Add them to GitHub.** In the repository: *Settings* → *Secrets and variables* → *Actions* → *New repository
   secret*:
   - `CLOUDFLARE_API_TOKEN`: the token from step 3
   - `CLOUDFLARE_ACCOUNT_ID`: the ID from step 4
6. **The site's URL** (optional). Under *Variables*, add `SITE_URL` with the public URL, for example
   `https://www.example.com`. It is used for canonical links, the sitemap and the RSS feed. Without it the build uses
   `https://shikumi-app.pages.dev`.

## First deploy

*Actions* → *Deploy* → *Run workflow* on `main`. When it finishes, the site is at `https://shikumi-app.pages.dev`
(Cloudflare may add a suffix if the name was taken; the dashboard shows the real URL). From then on, every push to
`main` deploys once CI is green.

## Custom domain

In the Pages project: *Custom domains* → *Set up a custom domain*, and follow the DNS instructions (automatic when
the domain's DNS is on Cloudflare). Then set the `SITE_URL` variable to the new URL and run Deploy again.

## Roll back

In the Pages project: *Deployments*, pick an earlier production deployment → *⋯* → *Rollback to this deployment*. It is
instant. To roll back the code too, revert the commit on `main`; the next deploy publishes the reverted site.
