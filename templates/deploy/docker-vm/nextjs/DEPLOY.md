# Deploying to your own server

Every merge to `main` that passes CI is deployed by `.github/workflows/deploy.yml`: it builds the image from `Dockerfile`, pushes
it to GitHub's container registry as `ghcr.io/shikumi-org/shikumi-app`, copies `compose.yaml` to the server over SSH and runs
`docker compose pull && docker compose up -d`. On the server, Compose starts Postgres (data in the `db-data` volume), runs the
migrations once (`migrate`) and then starts the app (`web`). Until the steps below are done, the Deploy workflow skips with a
notice and stays green.

## Set it up once

1. **A server.** Any Linux machine you can SSH into (a VPS with 1 GB of memory is enough), with Docker Engine and the Compose
   plugin: https://docs.docker.com/engine/install/. Check with `docker compose version`.
2. **A deploy user and key.** On the server, use a user that may run Docker (`sudo usermod -aG docker <user>`, then log in
   again). On your own machine create a key just for deploys and install it on the server:
   ```sh
   ssh-keygen -t ed25519 -N '' -C shikumi-app-deploy -f shikumi-app-deploy
   ssh-copy-id -i shikumi-app-deploy.pub <user>@<server>
   ```
3. **The app's configuration.** On the server, create the deploy directory (by default `~/shikumi-app`) and a `.env` in it:
   ```sh
   mkdir -p ~/shikumi-app && cd ~/shikumi-app
   cat > .env <<ENV
   POSTGRES_PASSWORD=$(openssl rand -hex 24)
   BETTER_AUTH_SECRET=$(openssl rand -base64 32)
   BETTER_AUTH_URL=https://app.example.com
   HTTP_PORT=3000
   ENV
   chmod 600 .env
   ```
   For sign-in with GitHub, create an OAuth app at https://github.com/settings/developers with the callback URL
   `<BETTER_AUTH_URL>/api/auth/callback/github`, and add `GITHUB_CLIENT_ID=...` and `GITHUB_CLIENT_SECRET=...` to `.env`.
   The workflow adds `IMAGE` and `IMAGE_TAG` lines itself; leave them.
4. **GitHub secrets.** In the repository: Settings → Secrets and variables → Actions → New repository secret:
   - `DEPLOY_HOST`: the server's host name or IP address.
   - `DEPLOY_USER`: the user from step 2.
   - `DEPLOY_SSH_KEY`: the whole private key file `shikumi-app-deploy` (including the `BEGIN` and `END` lines).
   - `DEPLOY_PATH` (optional): the deploy directory, absolute or relative to the user's home. Default `shikumi-app`.
   - `DEPLOY_KNOWN_HOSTS` (optional, recommended): the output of `ssh-keyscan <server>` checked against the server's real
     host keys. Without it the workflow trusts whatever key the server presents on each run.
5. **First deploy.** Actions → Deploy → Run workflow (or merge to `main`). On the server, `docker compose ps` shows `db` and
   `web` running and `migrate` exited with 0; `curl localhost:3000/api/health` answers `{"status":"ok"}`. Logs:
   `docker compose logs -f web`.

The image is private if the repository is; the workflow logs the server in to the registry for each deploy with the job's
token, so no long-lived registry credentials are stored there.

## HTTPS and a custom domain

The app listens on plain HTTP on `HTTP_PORT`. Put a reverse proxy in front of it for HTTPS, for example Caddy
(https://caddyserver.com/docs/install), which gets certificates by itself. Point your domain's `A` record at the server, then in
`/etc/caddy/Caddyfile`:
```
app.example.com {
	reverse_proxy localhost:3000
}
```
Run `sudo systemctl reload caddy`, and make sure `BETTER_AUTH_URL` in `.env` is `https://app.example.com`
(then `docker compose up -d` to apply it).

## Roll back

Every deploy is tagged with its commit. On the server, set `IMAGE_TAG` in `.env` to the commit SHA of the last good
version (see the Deploy runs or `docker image ls`) and run `docker compose up -d`. The next deploy sets it again. Or revert the
commit on `main` and let CI and Deploy ship the revert. Migrations are not rolled back: write them so the previous version still
works with the new schema (add columns first, remove them in a later release).

## Backups

The database lives in the `shikumi-app_db-data` volume. A daily dump is a good start:
`docker compose exec -T db pg_dump -U app app | gzip > backup-$(date +%F).sql.gz`.
