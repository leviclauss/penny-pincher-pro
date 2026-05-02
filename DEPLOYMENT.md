# Deployment

How code on `main` gets to the Lightsail box. The first-time provision /
backups / Tailscale setup live in [`docs/deploy.md`](docs/deploy.md);
this file is just the build-and-ship loop.

## Flow

```
git push main
   │
   ▼
GitHub Actions  ─►  build backend + frontend in parallel
                    (docker/build-push-action, type=gha cache)
   │
   ▼
GHCR            ─►  ghcr.io/leviclauss/penny-pincher-pro-backend :latest, :<sha>
                    ghcr.io/leviclauss/penny-pincher-pro-frontend :latest, :<sha>
   │
   ▼
deploy job      ─►  Tailscale OAuth → SSH admin@wheel-server →
                    git fetch + checkout the new compose file →
                    IMAGE_TAG=<sha> docker compose pull && up -d
```

The Lightsail micro never compiles anything. The frontend's `npm run
build` runs on a GitHub-hosted runner, not on the box.

## Why the `IMAGE_TAG` env var

`docker-compose.prod.yml` references images as
`ghcr.io/.../...-{backend,frontend}:${IMAGE_TAG:-latest}`. The deploy
job sets `IMAGE_TAG=$GITHUB_SHA`, pinning each deploy to an exact
commit. Floating `:latest` is the fallback for manual `docker compose
pull` invocations.

This is what makes rollback a one-liner.

## Required GitHub repo secrets

Settings → Secrets and variables → Actions:

| Name | Value |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client ID. Tailscale admin → Settings → OAuth clients. Scope: `auth_keys`. Tag: `tag:ci`. |
| `TS_OAUTH_SECRET` | Matching OAuth secret. |
| `LIGHTSAIL_HOST` | `wheel-server` (Tailscale MagicDNS name). |
| `LIGHTSAIL_USER` | `admin`. |
| `LIGHTSAIL_SSH_KEY` | PEM private key. Generate a fresh keypair just for the runner; append the public half to `~admin/.ssh/authorized_keys` on the box. |

App secrets (`ALPACA_*`, `FINNHUB_*`, `TELEGRAM_*`, etc.) live in
`/opt/penny-pincher-pro/.env` on the server. They are **not** stored in
GitHub and **not** baked into images.

## One-time prep

On the Tailscale admin console:
1. Create an OAuth client with `auth_keys` scope; record id + secret.
2. Add an ACL rule allowing `tag:ci` to reach `wheel-server:22`.

On your laptop:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/penny_pincher_deploy -C "gha-deploy"
ssh admin@wheel-server "cat >> ~/.ssh/authorized_keys" < ~/.ssh/penny_pincher_deploy.pub
```
Paste the contents of `~/.ssh/penny_pincher_deploy` into the
`LIGHTSAIL_SSH_KEY` GitHub secret.

On the server (only the very first time switching to pull-mode):
```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
git pull                                          # get the new compose file
IMAGE_TAG=$(git rev-parse HEAD) \
  docker compose -f docker-compose.prod.yml pull
IMAGE_TAG=$(git rev-parse HEAD) \
  docker compose -f docker-compose.prod.yml up -d
```

After this, every `git push main` deploys automatically.

## Manual deploy

GitHub UI → Actions → "Build and deploy production images" → **Run
workflow** → pick a branch (or commit, by selecting the workflow run on
that ref). This rebuilds + redeploys without needing a code change.

## Rollback

Pick the SHA you want to roll back to (from `git log` or the commit
list in GitHub).

**Option 1 — re-run the workflow on that commit.** Easier; same
mechanism as forward deploys. Workflow Run page → "Re-run all jobs".

**Option 2 — pin on the server.** Faster when the rollback target was
already built:
```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
export IMAGE_TAG=<old-sha>
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

The pinned tag survives container restarts but reverts to whatever the
next GHA deploy pushes — so option 2 is for incident response, not
permanent state. Cement a rollback by reverting the offending commit
on `main`.

## Debugging failed deploys

**The build job failed.** Read the GHA log. Usually a `Dockerfile`
syntax error, a failing `npm run build`, or a transient GHCR push
issue. Re-run on transient failures.

**The deploy job failed.** Check which step:
- *Connect to Tailscale*: OAuth secrets wrong, or `tag:ci` is missing
  from the Tailscale ACL.
- *Pull and restart on Lightsail*: SSH key mismatch, host unreachable
  (`tailscale status` on the box), or the `docker compose` command
  failed. The action prints the full server-side stdout/stderr.

**The deploy succeeded but the app is broken.** SSH in:
```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=200 backend
docker compose -f docker-compose.prod.yml logs --tail=200 frontend
docker images | grep ghcr
curl -fsS http://localhost:8000/api/system/health | jq
```
Most "broken after deploy" issues are migration failures (read the
backend log) or env-var changes that landed in `.env.example` but not
in `.env`. Roll back per above and fix forward.
