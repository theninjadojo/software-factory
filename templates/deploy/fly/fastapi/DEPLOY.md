# Deploying to Fly.io

Every push to `main` that passes CI is deployed by `.github/workflows/deploy.yml`: `flyctl deploy` builds the `Dockerfile` on
Fly's builders, runs the migrations (`release_command = "alembic upgrade head"` in `fly.toml`) and then replaces the machines.
Two process groups run from the same image: `app` (the API) and `worker` (the job worker).

Until the `FLY_API_TOKEN` secret exists, the Deploy workflow skips with a notice and stays green.

## Set it up once

1. **Account and CLI.** Sign up at <https://fly.io> and add a payment method. Install `flyctl`
   (<https://fly.io/docs/flyctl/install/>) and run `fly auth login`.
2. **Create the app.** In this repository:

   ```sh
   fly apps create shikumi-app
   ```

   App names are global. If `shikumi-app` is taken, pick another name and put it in `app = "..."` in `fly.toml`.
   Change `primary_region` in `fly.toml` to the region nearest your users (`fly platform regions` lists them).
3. **Create the database.** Fly Managed Postgres:

   ```sh
   fly mpg create --name shikumi-app-db --region ams
   fly mpg attach <cluster id from the output> --app shikumi-app
   ```

   `attach` stores the connection string as the app's `DATABASE_URL` secret. Any other PostgreSQL works too:
   `fly secrets set DATABASE_URL='postgresql://user:password@host:5432/dbname' --app shikumi-app`. (The app accepts
   `postgres://` and `postgresql://` URLs.)
4. **Other settings.** The front ends that call the API, as a JSON list:

   ```sh
   fly secrets set CORS_ORIGINS='["https://www.example.com"]' --app shikumi-app
   ```

5. **A deploy token for GitHub.**

   ```sh
   fly tokens create deploy --app shikumi-app --expiry 8760h
   ```

   Copy the whole output (it starts with `FlyV1`). In GitHub, open the repository's **Settings > Secrets and variables >
   Actions > New repository secret**, name it `FLY_API_TOKEN` and paste the token. It expires after a year; make a new one
   then.
6. **First deploy.** In GitHub, open **Actions > Deploy > Run workflow** (or push to `main`). Watch the log: the release
   command runs the migrations, then the machines start. Check it:

   ```sh
   fly status --app shikumi-app
   curl https://shikumi-app.fly.dev/ready
   ```

   `fly deploy` creates one machine per process group. For more, `fly scale count app=2 worker=1 --app shikumi-app`.

## A custom domain

```sh
fly certs add api.example.com --app shikumi-app
```

Then create the DNS records it prints (a CNAME to `shikumi-app.fly.dev`, or the A and AAAA records) at your DNS provider.
`fly certs show api.example.com --app shikumi-app` shows when the certificate is ready.

## Logs and secrets

- `fly logs --app shikumi-app` streams the JSON logs of both process groups.
- `fly secrets list --app shikumi-app`; setting a secret restarts the machines.

## Roll back

Each deploy's image is labelled with its commit. To go back to an earlier one:

```sh
fly releases --app shikumi-app --image                       # find the image of the good release
fly deploy --app shikumi-app --image registry.fly.io/shikumi-app:<commit sha>
```

The release command runs `alembic upgrade head` again, which changes nothing; it does not undo a migration. If the bad release
changed the schema in a way the old code cannot use, write a new migration that fixes it and deploy forwards (or run
`fly ssh console --app shikumi-app -C "alembic downgrade -1"` before rolling back, if the migration's downgrade is safe).
Reverting the bad commit on `main` also deploys the previous code.
