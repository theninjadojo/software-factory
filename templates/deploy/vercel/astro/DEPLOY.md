# Deploying to Vercel

The site is static. After CI passes on `main`, `.github/workflows/deploy.yml` builds it with the Vercel CLI
(`vercel build`) and publishes it as the production deployment (`vercel deploy --prebuilt --prod`). Until the secrets
below exist, the Deploy workflow skips with a notice and stays green.

`vercel.json` turns off Vercel's own Git deploys of `main`, so production only changes after CI is green. If you
connect the Git repository in Vercel, pull requests still get preview deployments from Vercel.

## Set it up once

1. **Vercel account.** Sign up at https://vercel.com (the Hobby plan is enough for a personal site).
2. **Create and link the project.** On your machine, in a clone of this repository:
   ```sh
   npx vercel login
   npx vercel link        # "Link to a new project?" yes; name it shikumi-app
   ```
   This writes `.vercel/project.json` (ignored by Git) with `orgId` and `projectId`. In the project's *Settings* →
   *Build and Deployment*, the framework preset should read *Astro* (it comes from `vercel.json`).
3. **Token.** https://vercel.com/account/tokens → *Create*, scoped to the team that owns the project. Copy it.
4. **Add them to GitHub.** In the repository: *Settings* → *Secrets and variables* → *Actions* → *New repository
   secret*:
   - `VERCEL_TOKEN`: the token from step 3
   - `VERCEL_ORG_ID`: `orgId` from `.vercel/project.json`
   - `VERCEL_PROJECT_ID`: `projectId` from `.vercel/project.json`
5. **The site's URL** (optional). Under *Variables*, add `SITE_URL` with the public URL, for example
   `https://www.example.com`. It is used for canonical links, the sitemap and the RSS feed. Without it the build uses
   `https://shikumi-app.vercel.app`.
6. **Pull request previews** (optional). In the Vercel project: *Settings* → *Git* → connect this GitHub repository.

## First deploy

*Actions* → *Deploy* → *Run workflow* on `main`. The run's summary shows the deployment URL; the production URL is on
the project's page in Vercel. From then on, every push to `main` deploys once CI is green.

## Custom domain

In the Vercel project: *Settings* → *Domains* → *Add*, and follow the DNS instructions. Then set the `SITE_URL`
variable to the new URL and run Deploy again.

## Roll back

In the Vercel project: *Deployments*, pick an earlier production deployment → *⋯* → *Instant Rollback* (or
`npx vercel rollback <deployment-url>`). To roll back the code too, revert the commit on `main`; the next deploy
publishes the reverted site.
