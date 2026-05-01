# 08 — Deployment

Single-user, runs locally during development, deployed to a small VPS or home server for production.

## Local dev

```
wheel-screener/
  backend/                 # FastAPI app
  frontend/                # React app
  docker-compose.yml
  .env.example
  Makefile
  README.md
```

`docker-compose.yml` for dev:

```yaml
services:
  backend:
    build: ./backend
    volumes:
      - ./backend:/app
      - ./data:/data       # SQLite lives here
    env_file: .env
    ports:
      - "8000:8000"
    command: uvicorn main:app --reload --host 0.0.0.0

  frontend:
    build: ./frontend
    volumes:
      - ./frontend:/app
      - /app/node_modules
    ports:
      - "5173:5173"
    command: npm run dev
```

Production compose drops the volumes/reload, builds the React app to static files served by FastAPI directly.

## Secrets management

`.env` for local dev (gitignored):

```
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # paper for now
FINNHUB_API_KEY=...
SMTP_HOST=...
SMTP_USER=...
SMTP_PASS=...
NTFY_TOPIC=...
APP_USERNAME=danny
APP_PASSWORD_HASH=$2b$...
DATABASE_URL=sqlite:///data/wheel.db
TIMEZONE=America/Los_Angeles
```

For production: SOPS-encrypted .env, or use the host's secret manager (Doppler, 1Password CLI). Don't commit a `.env`, ever.

## Hosting options

| Option | Cost | Pros | Cons |
|---|---|---|---|
| Home server / Raspberry Pi 5 | $0 (own hardware) | Cheap, full control | Power/internet outages = missed alerts |
| Hetzner CX11 / similar | ~$5/mo | Reliable, fast | Tiny extra cost |
| Fly.io | ~$5/mo | Easy deploy, good DX | Cold starts on free tier |
| AWS Lightsail / DO Droplet | ~$5/mo | Standard, lots of docs | Manual ops |

Given the data is small (SQLite is fine to ~10GB) and CPU is light, any of these work. **Recommendation:** Hetzner or DO for production reliability. Run on home hardware first to validate, migrate when you trust it.

## Backups

Critical: positions table represents real money. Set up:
- Nightly `sqlite3 .backup` to a separate volume
- Weekly off-site backup (rsync to S3-compatible storage like Backblaze B2)
- `filter_configs` and `tickers` exported as JSON to git periodically — easy versioning of strategy evolution

## Monitoring

For a single-user system, simple is fine:
- **Healthcheck endpoint:** `GET /api/system/health` returns 200 + last successful job times
- **Uptime monitor:** UptimeRobot or healthchecks.io free tier pinging the endpoint
- **Cron heartbeat:** healthchecks.io has a "did this job run?" mode — point each scheduled job at it

If a job hasn't run in 25h, get paged.

## Reverse proxy / TLS

Production: Caddy in front of FastAPI. Auto-TLS via Let's Encrypt:

```
wheel.yourdomain.com {
  reverse_proxy backend:8000
  basic_auth { ... }
}
```

Or skip the public exposure entirely and use Tailscale — much safer for a personal financial tool.

## CI/CD

GitHub Actions for:
- Lint + type check (ruff, mypy)
- Unit tests (pytest)
- Frontend build + tests
- Build Docker image, push to GHCR
- SSH deploy on `main` branch (or use Watchtower to pull updates)

Given your SDET background, lean into testing — the screener and management rules especially benefit from snapshot tests against fixed historical data.

## Database migration strategy

Start with SQLAlchemy + Alembic. Even for a personal project, you'll want migrations once schema evolves and you have real position data you don't want to lose.

```
alembic revision --autogenerate -m "add position_snapshots"
alembic upgrade head
```

## Observability cheap-stack

If you want more than logs:
- **Logs:** Loki + Grafana, or just `journalctl` if running on systemd
- **Metrics:** Prometheus client built into FastAPI middleware, scraped by Grafana Cloud free tier
- **Errors:** Sentry free tier — invaluable for catching that 5:31 PM exception you'd otherwise miss

Don't build all this on day one. Logs to file + Sentry is enough for v1.

## Pre-prod checklist

Before relying on alerts for real trades:
- [ ] Backfill at least 1 year of bars for full watchlist
- [ ] Run evening pipeline manually 5x successfully
- [ ] Verify morning + evening digest delivered to chosen channels
- [ ] Trigger a test position alert end-to-end
- [ ] Confirm timezone handling — DST transitions matter
- [ ] Confirm market holidays handled — try forcing a run on a known holiday
- [ ] Document recovery: "if SQLite is corrupted, what do I do?"
- [ ] Document API key rotation procedure
- [ ] Set up backups and verify restore works
