# Deploying to Render

`render.yaml` is a Render Blueprint: a web service `shikumi-app` (the API), a background worker `shikumi-app-worker` (the
job worker) and a PostgreSQL database `shikumi-app-db`. Both services build the `Dockerfile`. Render's own auto-deploy is off:
every push to `main` that passes CI runs `.github/workflows/deploy.yml`, which calls the two services' deploy hooks. Before a
new API version goes live, Render runs its pre-deploy command, `alembic upgrade head`.

Until the `RENDER_DEPLOY_HOOK_URL` and `RENDER_WORKER_DEPLOY_HOOK_URL` secrets exist, the Deploy workflow skips with a notice
and stays green.

The worker deploys at the same time as the API, so it can start before the API's migration has run. Write migrations that
the running code survives (add a column first, use it in the next deploy; drop things a deploy after the code stops using them).

## Set it up once

1. **Account.** Sign up at <https://render.com> and connect your GitHub account, giving Render access to this repository.
2. **Create the Blueprint.** In the Render dashboard, **New > Blueprint**, pick this repository and the `main` branch. Render
   reads `render.yaml` and lists what it will create. Fill in `CORS_ORIGINS` when asked: the front ends that call the API,
   as a JSON list such as `["https://www.example.com"]` (or `[]`). Click **Apply**. Render creates the database and both
   services and deploys them once.

   The plans in `render.yaml` (`starter` for the services, `basic-256mb` for the database) are paid. The free web plan does
   not run pre-deploy commands and there is no free worker, so change them only knowing that. Pick the region in the
   dashboard or add `region:` to each service and the database.
3. **The deploy hooks.** For each service, open it in the dashboard, then **Settings > Deploy Hook** and copy the URL. In
   GitHub, open **Settings > Secrets and variables > Actions > New repository secret** and add:

   | Secret | Value |
   |---|---|
   | `RENDER_DEPLOY_HOOK_URL` | the deploy hook of `shikumi-app` (the API) |
   | `RENDER_WORKER_DEPLOY_HOOK_URL` | the deploy hook of `shikumi-app-worker` |

   A deploy hook URL is a secret: anyone who has it can start a deploy.
4. **First deploy through GitHub.** Open **Actions > Deploy > Run workflow**. In Render, each service shows a new deploy;
   the API's log shows the migrations running first. Check `https://shikumi-app.onrender.com/ready` (the address is on the
   service's page).

Changes to `render.yaml` itself are applied when Render syncs the Blueprint (the Blueprint's page shows them).

## A custom domain

Open the `shikumi-app` service, **Settings > Custom Domains > Add Custom Domain**, enter `api.example.com`, and create the
CNAME record it shows at your DNS provider. Render issues the certificate once DNS resolves.

## Logs and settings

Each service's **Logs** tab shows its JSON logs. Settings live under **Environment**; changing one redeploys the service.

## Roll back

Open the service, **Events**, find the last good deploy and click **Rollback**. Do it for both services. A rollback does not
undo migrations: if the bad version changed the schema in a way the old code cannot use, fix forwards with a new commit, or
run `alembic downgrade -1` from the API's **Shell** tab before rolling back (only if that migration's downgrade is safe).
Reverting the bad commit on `main` also deploys the previous code the usual way.
