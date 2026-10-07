# Deploying to Render

`render.yaml` is a Render Blueprint for a static site: Render runs `npm ci && npm run build` with `VITE_API_URL` set and serves
`dist/`, sending `index.html` for every path that is not a file (so client-side routes work on reload). Every merge to `main`
that passes CI triggers a deploy through a deploy hook (`.github/workflows/deploy.yml`); Render's own auto-deploy is off, so a
commit that fails CI is never deployed. Until the steps below are done, the Deploy workflow skips with a notice and stays green.
Static sites are free on Render.

## Set it up once

1. **Account.** Sign up at https://render.com and connect your GitHub account (Render asks for access to this repository).
2. **Create the Blueprint.** Dashboard → New → Blueprint → pick this repository. Render reads `render.yaml` and shows the
   `shikumi-app` static site. It asks for `VITE_API_URL`: the public URL of the API, for example
   `https://api.example.com` (no trailing slash). It is compiled into the app, so it is not a secret. Apply; the
   first deploy starts at once.
3. **Let the API accept the site.** Add `https://shikumi-app.onrender.com` (the site's URL, shown in the dashboard) to the API's
   allowed CORS origins.
4. **The deploy hook.** Open the `shikumi-app` site → Settings → Deploy Hook, and copy the URL.
5. **GitHub secret.** In the repository: Settings → Secrets and variables → Actions → New repository secret, name
   `RENDER_DEPLOY_HOOK_URL`, value: the hook URL. Keep it secret: anyone with it can trigger a deploy.
6. **Check.** Actions → Deploy → Run workflow. The site's Events tab shows the deploy; then open the site.

A change to `VITE_API_URL` (Environment tab) only takes effect with the next build: Manual Deploy → Deploy latest commit.

## A custom domain

Site → Settings → Custom Domains → Add, then create the DNS record Render shows (a `CNAME` to `shikumi-app.onrender.com`).
Render issues the certificate. Add the new origin to the API's CORS origins.

## Roll back

Site → Events → pick the last good deploy → Rollback. Or revert the commit on `main` and let CI and Deploy ship the revert.
