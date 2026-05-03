# Deployment runbook

Single-user production deploy: AWS Lightsail VM + Tailscale + Docker
Compose. Read this once start-to-finish before running anything; the
ordering matters (especially closing port 22 *after* Tailscale is up).

**Total ongoing cost:** ~$7/month (Lightsail micro). Tailscale is free
at this scale; off-site backups are pulled to your laptop, so no extra
service fees.

## Pre-requisites on your laptop

- AWS account with billing enabled
- `awscli` installed and `aws configure` set up
- `ssh` (Tailscale handles laptop ↔ server reachability for backup pulls)

## Decisions baked into this runbook

- **Region:** `us-east-1` (Virginia). Closest to Alpaca + Finnhub data
  centers, lowest API latency.
- **Lightsail bundle:** `micro_3_0` (1GB RAM, 40GB SSD, 2TB transfer,
  $7/mo). The `nano` tier risks OOM during full backfill.
- **Blueprint:** `debian_12` (smaller, less Ubuntu-noise than the AMI).
- **Auth model:** Tailscale only — the server is **not** reachable from
  the public internet after Phase 3.

## Deploy mode

`docker-compose.prod.yml` is **pull-only**. Images are built and pushed
to GHCR by GitHub Actions; the server only runs `docker compose pull
&& up -d`. The Lightsail micro never compiles anything (the local
`npm run build` is what was eating CPU credits).

Full flow and operator docs: [`DEPLOYMENT.md`](../DEPLOYMENT.md).

---

## Phase 1 — Provision (5 minutes)

Pure CLI. From your laptop:

```bash
# Create the instance.
aws lightsail create-instances \
  --instance-names wheel-server \
  --availability-zone us-east-1a \
  --blueprint-id debian_12 \
  --bundle-id micro_3_0 \
  --region us-east-1

# Wait until it's running (poll until 'state.name' = 'running').
aws lightsail get-instance --instance-name wheel-server \
  --query 'instance.state.name' --output text --region us-east-1

# Allocate + attach a static IP (free while attached).
aws lightsail allocate-static-ip \
  --static-ip-name wheel-static --region us-east-1
aws lightsail attach-static-ip \
  --static-ip-name wheel-static --instance-name wheel-server \
  --region us-east-1

# Note the public IP for SSH bootstrap.
PUBLIC_IP=$(aws lightsail get-static-ip \
  --static-ip-name wheel-static --region us-east-1 \
  --query 'staticIp.ipAddress' --output text)
echo "$PUBLIC_IP"

# Open SSH only briefly for the bootstrap session.
aws lightsail put-instance-public-ports \
  --instance-name wheel-server \
  --port-infos fromPort=22,toPort=22,protocol=TCP \
  --region us-east-1

# Pull the SSH key. Despite the field name, `privateKeyBase64` is
# already a PEM-formatted key — do NOT base64-decode it.
aws lightsail download-default-key-pair \
  --region us-east-1 \
  --query 'privateKeyBase64' --output text \
  > ~/.ssh/lightsail.pem
chmod 600 ~/.ssh/lightsail.pem
```

## Phase 2 — Server bootstrap (10 minutes)

```bash
ssh -i ~/.ssh/lightsail.pem admin@$PUBLIC_IP
```

On the server:

```bash
# System updates.
sudo apt-get update
sudo apt-get -y upgrade

# Docker.
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker admin

# Tools we need on the host (for backups + scheduled jobs).
# Debian 12 Lightsail images don't ship cron by default.
sudo apt-get install -y sqlite3 zstd git cron
sudo systemctl enable --now cron

# Tailscale.
curl -fsSL https://tailscale.com/install.sh | sudo sh

# Join your tailnet. The flag --ssh enables Tailscale SSH so you can
# drop the Lightsail key after this. The command will print a URL to
# auth in your browser.
sudo tailscale up --hostname=wheel-server --ssh

# Verify from your laptop:
#   tailscale status   # should show wheel-server
#   ssh admin@wheel-server   # should work without -i

# Re-login so docker-group membership takes effect.
exit
```

Verify Tailscale SSH works *before* closing the public port:

```bash
# From laptop, no key needed:
ssh admin@wheel-server 'docker --version'
```

If that succeeded:

```bash
# Close public SSH. From laptop:
aws lightsail close-instance-public-ports \
  --instance-name wheel-server \
  --port-info fromPort=22,toPort=22,protocol=TCP \
  --region us-east-1
```

The server is now invisible to the public internet.

## Phase 3 — First deploy (10 minutes)

SSH via Tailscale and clone the repo:

```bash
ssh admin@wheel-server
sudo mkdir -p /opt/penny-pincher-pro
sudo chown admin:admin /opt/penny-pincher-pro
git clone https://github.com/leviclauss/penny-pincher-pro.git /opt/penny-pincher-pro
cd /opt/penny-pincher-pro
```

Configure secrets:

```bash
cp .env.example .env
nano .env
```

Fill in at minimum:

- `ALPACA_API_KEY`, `ALPACA_API_SECRET`
- `FINNHUB_API_KEY` (or leave empty to skip earnings ingestion)
- `TIMEZONE` (likely `America/Los_Angeles` per existing default)

Off-site backups are handled by pulling snapshots to your laptop over
Tailscale (see Phase 5) — no additional cloud-storage credentials are
needed. Leave `BACKUP_REMOTE` unset.

Pull images and bring up the stack. The backend container runs
`alembic upgrade head` automatically as part of its `CMD`, so no
separate migration step is needed. Images are public, so no GHCR
login is required.

```bash
# Pull the latest images that GitHub Actions pushed.
docker compose -f docker-compose.prod.yml pull

# Seed the dev watchlist (or skip and add tickers via the UI later).
docker compose -f docker-compose.prod.yml run --rm backend python -m scripts.seed_dev

# Start.
docker compose -f docker-compose.prod.yml up -d
```

Verify:

```bash
# Backend health (scheduler running, last bar date present).
curl -fsS http://localhost:8000/api/system/health | jq

# Frontend (nginx serving the SPA).
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost/

# Logs.
docker compose -f docker-compose.prod.yml logs -f --tail=200
```

From your laptop / phone (with Tailscale running):

```
http://wheel-server/         # frontend
http://wheel-server/api/system/health   # backend
```

## Phase 4 — Initial data load (15 minutes)

The scheduler will run automatically at 5:30 PM PT, but for the first
load you'll want a full backfill:

```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
docker compose -f docker-compose.prod.yml exec backend \
  python -m ingestion.pipeline --full
```

Expect ~5 years of bars + indicators across the seeded watchlist
(~12k rows + same-shape indicators), plus options + IV for today.
Takes 30–90 seconds depending on Alpaca latency.

## Phase 5 — Backups + heartbeats

### Nightly local backup (server) + pull to laptop

The strategy: the server takes a nightly local snapshot at 3 AM; your
laptop pulls new snapshots at 4 AM over Tailscale into a directory that
your existing personal backup (iCloud Drive, Time Machine, etc.) already
covers. That last hop is what makes them "off-site" — no third-party
storage account required.

#### On the server: nightly snapshot cron

```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro

# Verify it works once manually.
DATABASE_PATH=/var/lib/docker/volumes/penny-pincher-pro_data/_data/wheel.db \
BACKUP_DIR=/var/lib/docker/volumes/penny-pincher-pro_backups/_data \
./scripts/backup_sqlite.sh

# Install the cron entry.
crontab -e
```

Add:

```
0 3 * * * cd /opt/penny-pincher-pro && DATABASE_PATH=/var/lib/docker/volumes/penny-pincher-pro_data/_data/wheel.db BACKUP_DIR=/var/lib/docker/volumes/penny-pincher-pro_backups/_data BACKUP_RETENTION_DAYS=30 ./scripts/backup_sqlite.sh >> /var/log/wheel-backup.log 2>&1
```

`BACKUP_RETENTION_DAYS=30` widens the window so a laptop that's been
offline for travel still has snapshots left to pull. (The script only
prunes after a successful off-site upload, so with `BACKUP_REMOTE`
unset nothing prunes — but keep the var set in case you re-enable
remote upload later.)

#### On your laptop: pull snapshots over Tailscale

Tailscale already gives the laptop reachability to `wheel-server`, and
the snapshot directory is owned by `admin`, so `rsync` over SSH works
without extra setup. Pick a destination inside something your existing
personal backup covers (iCloud Drive, Time Machine target, etc.).

```bash
mkdir -p ~/Backups/wheel-server
```

Add a laptop-side cron (`crontab -e`) running an hour after the server
snapshot finishes:

```
0 4 * * * rsync -az --ignore-existing admin@wheel-server:/var/lib/docker/volumes/penny-pincher-pro_backups/_data/ ~/Backups/wheel-server/ >> ~/Library/Logs/wheel-backup-pull.log 2>&1
```

`--ignore-existing` keeps the laptop copy authoritative for already-
pulled snapshots even after the server prunes them, so the laptop
accumulates the long-tail history.

### Heartbeat monitoring (healthchecks.io)

Every job that wraps with `scheduler.context.job_run` pings healthchecks.io
on entry (`/start`), success (bare URL), and failure (`/fail`, with the
exception text in the request body). Per-job URLs are read from
`HEALTHCHECKS_URL_<JOB_NAME>` env vars; missing URLs short-circuit
silently so this is safe to wire incrementally.

1. Sign up at https://healthchecks.io (free tier: 20 checks).
2. Create one check per job you care about. Suggested set:
   - `wheel-evening-pipeline` — `Cron 30 17 * * 1-5`, grace 1h
   - `wheel-morning-digest` — `Cron 0 8 * * 1-5`, grace 30m
   - `wheel-evening-digest` — `Cron 30 18 * * 1-5`, grace 30m
   - `wheel-sqlite-backup` — `Cron 0 3 * * *`, grace 1h
   Set the timezone on each to match `TIMEZONE`.
3. Copy each ping URL into the matching env var in `.env` on the server:
   ```
   HEALTHCHECKS_URL_EVENING_PIPELINE=https://hc-ping.com/<uuid>
   HEALTHCHECKS_URL_MORNING_DIGEST=https://hc-ping.com/<uuid>
   HEALTHCHECKS_URL_EVENING_DIGEST=https://hc-ping.com/<uuid>
   HEALTHCHECKS_URL_SQLITE_BACKUP=https://hc-ping.com/<uuid>
   ```
4. Restart the backend: `docker compose -f docker-compose.prod.yml up -d`.

If a job stops pinging (or pings `/fail`), healthchecks.io will
email/push you per the channel preferences on the check.

Local job-failure alerts are independent of healthchecks: every failure
also dispatches a `job_failed` Telegram alert (deduped to one per job
per UTC day) so you'll see the breakage on your phone even if
healthchecks.io is down.

### Off-site backup to S3 / Backblaze B2 (alternative to laptop pull)

If you don't want to rely on the laptop-pull strategy above, the nightly
backup job can ship snapshots straight to S3 or B2. Install the optional
extra into the running image (or rebuild with it baked in) and set the
provider env vars:

```bash
docker compose -f docker-compose.prod.yml exec backend \
  pip install -e .[backup-s3]
```

In `.env`:

```
BACKUP_OFFSITE_ENABLED=true
BACKUP_OFFSITE_PROVIDER=b2                # or s3
BACKUP_OFFSITE_BUCKET=wheel-snapshots
BACKUP_OFFSITE_PREFIX=prod/
BACKUP_OFFSITE_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com   # B2 only; AWS leaves blank
BACKUP_OFFSITE_REGION=us-west-002
BACKUP_OFFSITE_ACCESS_KEY_ID=<key id>
BACKUP_OFFSITE_SECRET_ACCESS_KEY=<application key>
```

Off-site failures are recorded in the `sqlite_backup` job_runs row
(`offsite="failed"`, with the exception in `offsite_error`) but never
fail the job — the local snapshot is the primary recovery artefact.

## Operations

### Tail logs

```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
docker compose -f docker-compose.prod.yml logs -f --tail=200
docker compose -f docker-compose.prod.yml logs -f backend  # just one service
```

### Manually run a job

```bash
curl -X POST http://wheel-server/api/system/jobs/evening_pipeline/run
curl http://wheel-server/api/system/job-runs?limit=5 | jq
```

### Redeploy after pushing to main

Automatic. The `Build and deploy production images` workflow runs on
every push to `main`, builds + pushes the images to GHCR, then SSHes
in via Tailscale and runs `docker compose pull && up -d`. No manual
action needed.

To force a deploy without a code change, trigger the workflow from the
GitHub UI (Actions → "Build and deploy production images" → Run
workflow). To deploy by hand from the server:

```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro
git pull
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f docker-compose.prod.yml pull
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f docker-compose.prod.yml up -d
```

Rollback and debugging: see [`DEPLOYMENT.md`](../DEPLOYMENT.md).

### Restore from a backup

```bash
ssh admin@wheel-server
cd /opt/penny-pincher-pro

# 1. Stop the app so nothing is writing.
docker compose -f docker-compose.prod.yml stop backend

# 2. Pick the snapshot you want.
ls /var/lib/docker/volumes/penny-pincher-pro_backups/_data/    # on the server
ls ~/Backups/wheel-server/                                     # on your laptop

# 3. If the snapshot only exists on the laptop, copy it back to the server.
#    (From your laptop, not the SSH session above.)
scp ~/Backups/wheel-server/wheel-20260601T030000Z.db.zst admin@wheel-server:/tmp/

# 4. Decompress (on the server).
zstd -d /tmp/wheel-20260601T030000Z.db.zst -o /tmp/wheel-restored.db

# 5. Replace.
docker run --rm -v penny-pincher-pro_data:/data -v /tmp:/host alpine \
  sh -c 'cp /host/wheel-restored.db /data/wheel.db'

# 6. Start.
docker compose -f docker-compose.prod.yml up -d backend
```

### Rotate API keys

Edit `.env` on the server, then `docker compose -f docker-compose.prod.yml up -d`.
APScheduler restarts; the next pipeline run uses the new keys.

## Pre-prod checklist

Before relying on alerts for real trades:

- [ ] All ingestion runs have green status in `/api/system/job-runs`
- [ ] `make ingest-incremental` (or `evening_pipeline` cron) runs
      successfully 5 weekdays in a row
- [ ] Verify a holiday is correctly skipped (force a run on a known
      market closure; expect `skipped="holiday"` in the job_runs row)
- [ ] DST transition tested (run across the spring/fall boundary,
      confirm cron fires at the right local time)
- [ ] healthchecks.io receives pings
- [ ] One end-to-end backup → restore drill (see *Restore from a
      backup* above)
- [ ] API key rotation drill (rotate Alpaca, confirm next run works)

## Troubleshooting

| Symptom | First thing to check |
|---|---|
| `health` returns 503 | `docker compose logs backend` — usually a DB migration didn't run |
| `last_bar_date` stale | `/api/system/job-runs` — find the failed run, read `error` |
| Frontend 502 | Backend container is down or unhealthy. `docker compose ps`. |
| Out of disk | `df -h`, `du -sh /var/lib/docker/volumes/*` — old backups not pruning, or Docker image cache. `docker system prune -a` (won't touch volumes). |
| Tailscale unreachable | `sudo tailscale status` on the server. If it's offline, `sudo systemctl restart tailscaled`. |
| Alpaca 401 | Keys expired or wrong env. Edit `.env`, restart compose. |
