# Deploying to your own server

Every merge to `main` that passes CI is deployed by `.github/workflows/deploy.yml`: it builds the image from `Dockerfile` (the app
compiled with `VITE_API_URL`, served by nginx with `nginx.conf`), pushes it to GitHub's container registry as
`ghcr.io/shikumi-org/shikumi-app`, copies `compose.yaml` to the server over SSH and runs
`docker compose pull && docker compose up -d`. Until the steps below are done, the Deploy workflow skips with a notice and stays
green.

## Set it up once

1. **A server.** Any Linux machine you can SSH into, with Docker Engine and the Compose plugin:
   https://docs.docker.com/engine/install/. It can be the same server as the API.
2. **A deploy user and key.** On the server, use a user that may run Docker (`sudo usermod -aG docker <user>`, then log in
   again). On your own machine create a key just for deploys and install it on the server:
   ```sh
   ssh-keygen -t ed25519 -N '' -C shikumi-app-deploy -f shikumi-app-deploy
   ssh-copy-id -i shikumi-app-deploy.pub <user>@<server>
   ```
3. **The port.** The site listens on port 8080 of the server. To change it, create `~/shikumi-app/.env` on the server with
   `HTTP_PORT=...`. The workflow adds `IMAGE` and `IMAGE_TAG` lines to that file itself; leave them.
4. **GitHub settings.** In the repository: Settings → Secrets and variables → Actions.
   - Secrets tab → New repository secret:
     - `DEPLOY_HOST`: the server's host name or IP address.
     - `DEPLOY_USER`: the user from step 2.
     - `DEPLOY_SSH_KEY`: the whole private key file `shikumi-app-deploy` (including the `BEGIN` and `END` lines).
     - `DEPLOY_PATH` (optional): the deploy directory, absolute or relative to the user's home. Default `shikumi-app`.
     - `DEPLOY_KNOWN_HOSTS` (optional, recommended): the output of `ssh-keyscan <server>` checked against the server's real
       host keys. Without it the workflow trusts whatever key the server presents on each run.
   - Variables tab → New repository variable: `VITE_API_URL`, the public URL of the API, for example
     `https://api.example.com` (no trailing slash). It is compiled into the app, so it is not a secret.
5. **Let the API accept the site.** Add the site's public origin (for example `https://app.example.com`) to the API's allowed
   CORS origins.
6. **First deploy.** Actions → Deploy → Run workflow (or merge to `main`). On the server, `docker compose ps` in the deploy
   directory shows `web` running; `curl localhost:8080/healthz` answers `ok`.

The image is private if the repository is; the workflow logs the server in to the registry for each deploy with the job's
token, so no long-lived registry credentials are stored there.

## HTTPS and a custom domain

Put a reverse proxy in front for HTTPS, for example Caddy (https://caddyserver.com/docs/install), which gets certificates by
itself. Point your domain's `A` record at the server, then in `/etc/caddy/Caddyfile`:
```
app.example.com {
	reverse_proxy localhost:8080
}
```
and run `sudo systemctl reload caddy`. If the API is on the same server, give it its own host name (for example
`api.example.com`) in the same Caddyfile.

## Roll back

Every deploy is tagged with its commit. On the server, set `IMAGE_TAG` in `.env` to the commit SHA of the last good version
(see the Deploy runs or `docker image ls`) and run `docker compose up -d`. The next deploy sets it again. Or revert the commit on
`main` and let CI and Deploy ship the revert.
