# Deploying to your own server

Every push to `main` that passes CI is deployed by `.github/workflows/deploy.yml`:

1. it builds the `Dockerfile` and pushes the image to `ghcr.io/shikumi-org/shikumi-app`, tagged `sha-<commit>` and `latest`;
2. over SSH, it copies `compose.yaml` to the server, sets `IMAGE` in the `.env` file next to it and pulls the image;
3. it runs the migrations (`docker compose run --rm api alembic upgrade head`) and then `docker compose up -d`, which starts
   or replaces `api` (the HTTP API), `worker` (the job worker) and `postgres` (data in the `postgres-data` volume).

Until the `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY` secrets exist, the Deploy workflow skips with a notice and stays
green.

## Set it up once

1. **A server.** Any Linux machine you can reach over SSH, with Docker Engine and the Compose plugin installed
   (<https://docs.docker.com/engine/install/>). 1 GB of memory is enough to start.
2. **A user for deploys.** On the server, as root:

   ```sh
   adduser --disabled-password deploy
   usermod -aG docker deploy
   ```

3. **An SSH key for GitHub.** On your own computer:

   ```sh
   ssh-keygen -t ed25519 -N "" -C "github-deploy" -f shikumi-app-deploy
   ssh-copy-id -i shikumi-app-deploy.pub deploy@your.server.example
   ssh -i shikumi-app-deploy deploy@your.server.example docker ps   # must work without a password
   ```

4. **The server's settings.** On the server, as `deploy`, make the directory and its `.env` file (it is never in git):

   ```sh
   mkdir -p ~/shikumi-app && cd ~/shikumi-app
   cat > .env <<EOF
   POSTGRES_PASSWORD=$(openssl rand -hex 24)
   CORS_ORIGINS=["https://www.example.com"]
   EOF
   chmod 600 .env
   ```

   Use letters and digits only in `POSTGRES_PASSWORD` (it goes into a URL). The workflow adds an `IMAGE=` line to this file;
   leave the rest as it is. `API_BIND` (default `127.0.0.1:8000`) sets where the API listens on the server.
5. **The GitHub secrets.** In the repository, open **Settings > Secrets and variables > Actions > New repository secret** and
   add:

   | Secret | Value |
   |---|---|
   | `DEPLOY_HOST` | the server's host name or IP address |
   | `DEPLOY_USER` | `deploy` |
   | `DEPLOY_SSH_KEY` | the whole private key file `shikumi-app-deploy` (including the `BEGIN` and `END` lines) |
   | `DEPLOY_PATH` | optional: the directory on the server, if not `~/shikumi-app` |
   | `DEPLOY_KNOWN_HOSTS` | optional but better: the output of `ssh-keyscan your.server.example`. Without it, the workflow trusts whatever key the host shows on each run. |

   Then delete the private key from your computer, or keep it somewhere safe.
6. **First deploy.** Open **Actions > Deploy > Run workflow** (or push to `main`). Then, on the server:

   ```sh
   cd ~/shikumi-app && docker compose ps
   curl localhost:8000/ready
   ```

   The image is private if the repository is. The workflow logs in to pull it and logs out again; to pull by hand, log in with
   a personal access token that has `read:packages`.

## A domain and HTTPS

The API listens on `127.0.0.1:8000`, so put a reverse proxy in front of it. With [Caddy](https://caddyserver.com/docs/install)
on the server, point the domain's DNS A (and AAAA) record at the server, then put this in `/etc/caddy/Caddyfile` and run
`systemctl reload caddy`:

```
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy gets and renews the certificate itself. Open ports 80 and 443 in the firewall and keep 5432 and 8000 closed.

## Day to day

```sh
cd ~/shikumi-app
docker compose logs -f api worker        # JSON logs
docker compose exec postgres pg_dump -U app shikumi_app > backup.sql
```

Back up the database regularly (for example that `pg_dump` from cron, copied off the server).

## Roll back

Every deploy's image stays in the registry as `ghcr.io/shikumi-org/shikumi-app:sha-<commit>`. On the server:

```sh
cd ~/shikumi-app
sed -i 's|^IMAGE=.*|IMAGE=ghcr.io/shikumi-org/shikumi-app:sha-<good commit>|' .env
docker compose up -d
```

This does not undo migrations. If the bad version changed the schema in a way the old code cannot use, run
`docker compose run --rm api alembic downgrade -1` with the bad image still in place (only if that migration's downgrade is
safe), or fix forwards with a new commit. Reverting the bad commit on `main` deploys the previous code the usual way.
