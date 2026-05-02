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
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client ID. Tailscale admin → Settings → Trust credentials → + Credential. Scope: `auth_keys` (write). Tag: `tag:ci`. |
| `TS_OAUTH_SECRET` | Matching OAuth secret (shown once, on creation). |
| `LIGHTSAIL_HOST` | `wheel-server` (Tailscale MagicDNS name). |
| `LIGHTSAIL_USER` | `admin`. |

No SSH key secret is required: the deploy job reaches the server via
**Tailscale SSH** (the server runs `tailscaled --ssh`), which authenticates
by Tailscale identity, not `~/.ssh/authorized_keys`.

App secrets (`ALPACA_*`, `FINNHUB_*`, `TELEGRAM_*`, etc.) live in
`/opt/penny-pincher-pro/.env` on the server. They are **not** stored in
GitHub and **not** baked into images.

## One-time prep

**On the Tailscale admin console** (https://login.tailscale.com/admin):

1. **Settings → Trust credentials → + Credential** → OAuth client. Scope:
   `auth_keys` (write). Tag: `tag:ci`. Copy the client id + secret into
   GitHub secrets immediately — the secret is shown once.
2. **Access controls (policy file):** the policy must declare `tag:ci`
   and `tag:server` as tag owners, allow `tag:ci` to reach
   `tag:server:22`, and grant Tailscale SSH from both `autogroup:member`
   (you) and `tag:ci` (the runner) to `tag:server` as user `admin`. The
   working policy lives in this repo's history; the relevant blocks are
   `tagOwners`, `acls`, and `ssh`.
3. **Machines → wheel-server:** make sure the device carries `tag:server`
   (not `tag:ci`, not untagged). If you need to change tags on a device
   that's already joined the tailnet, edit them in the admin UI; if
   that's blocked, run `sudo tailscale up --advertise-tags=tag:server`
   on the box (browser re-auth required).

**On the server (only the very first time switching to pull-mode):**
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
  from the policy's `tagOwners` block.
- *Pull and restart on Lightsail*: most common causes:
  - "tailnet policy does not permit you to SSH to this node" — the
    policy `ssh` block is missing a `tag:ci → tag:server` rule, or
    wheel-server isn't tagged `tag:server`. Verify with
    `tailscale whois 100.64.236.98` from your laptop.
  - Host unreachable — `tailscale status` on the box, or
    `tailscale ping wheel-server` from your laptop.
  - `docker compose` command failed — the runner streams the full
    server-side stdout/stderr in the job log.

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
