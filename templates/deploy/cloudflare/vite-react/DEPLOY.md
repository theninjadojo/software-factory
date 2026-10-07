# Deploying to Cloudflare Pages

Every merge to `main` that passes CI is built with `VITE_API_URL` set to your API's address and uploaded to Cloudflare Pages by
`.github/workflows/deploy.yml` (`wrangler pages deploy`). `wrangler.toml` names the project and the output folder; Pages serves
`index.html` for any path that is not a file, so routes such as `/items/12` work on reload. Until the steps below are done, the
Deploy workflow skips with a notice and stays green.

## Set it up once

1. **Account.** Sign up at https://dash.cloudflare.com (the free plan is enough).
2. **Create the Pages project** (once, from your machine, in this repository):
   ```sh
   npx wrangler login
   npx wrangler pages project create shikumi-app --production-branch main
   ```
   Project names are unique within your account. If you pick another, change `name` in `wrangler.toml` and `--project-name`
   in `.github/workflows/deploy.yml`. The site will be at `https://shikumi-app.pages.dev`.
3. **An API token.** My Profile → API Tokens → Create Token → the "Edit Cloudflare Workers" template, or a custom token with
   the permission Account → Cloudflare Pages → Edit. Copy it.
4. **Your account ID.** Shown on the right of Workers & Pages → Overview, or by `npx wrangler whoami`.
5. **GitHub settings.** In the repository: Settings → Secrets and variables → Actions.
   - Secrets tab → New repository secret: `CLOUDFLARE_API_TOKEN` (step 3) and `CLOUDFLARE_ACCOUNT_ID` (step 4).
   - Variables tab → New repository variable: `VITE_API_URL`, the public URL of the API, for example
     `https://api.example.com` (no trailing slash). It is compiled into the app, so it is not a secret.
6. **Let the API accept the site.** Add `https://shikumi-app.pages.dev` (and your custom domain, later) to the API's allowed
   CORS origins.
7. **First deploy.** Actions → Deploy → Run workflow (or merge to `main`), then open `https://shikumi-app.pages.dev`.

## A custom domain

Workers & Pages → shikumi-app → Custom domains → Set up a custom domain. If the domain's DNS is on Cloudflare the record is
created for you; otherwise add the `CNAME` it shows. Add the new origin to the API's CORS origins.

## Roll back

Workers & Pages → shikumi-app → Deployments → the last good production deployment → ⋯ → Rollback to this deployment. Or revert
the commit on `main` and let CI and Deploy ship the revert.
