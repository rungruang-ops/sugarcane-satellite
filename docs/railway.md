# Deploying the webhook on Railway

The LINE webhook + LIFF API runs as one container (`Dockerfile`, `railway.json`) next to a
PostGIS service (`postgis/postgis:16-3.4` with a volume on `/var/lib/postgresql/data`).

* Start command: `python -m canesat.line.serve` — applies pending migrations
  (`CREATE EXTENSION postgis/pgcrypto` + schema) with retries, then binds `0.0.0.0:$PORT`.
* Health check: `GET /healthz` (`{"status":"ok","db":"ok|unavailable|disabled"}`).
* LINE webhook URL: `https://<service domain>/callback`.

## App service variables

| Variable | Value |
|---|---|
| `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN` | Messaging API channel (secret) |
| `DATABASE_URL` | `postgresql://<user>:<pass>@<postgis-service>.railway.internal:5432/<db>` (private network) |
| `LINE_LOGIN_CHANNEL_ID` | LINE Login channel that owns the LIFF app (default `2011859249`) |
| `LIFF_ID` | LIFF app id; without it "เพิ่มแปลง" answers "coming soon" |
| `CANESAT_PHONE_KEY` | optional pgcrypto key for members' phone numbers (not stored if unset) |
| `CANESAT_INGEST_ON_REGISTER` | `1` (default) = NDVI backfill for newly registered plots |
| `EARTHDATA_TOKEN` | NASA Earthdata bearer token (GPM IMERG + SMAP) |
| `ENABLE_PUSH_ALERTS` | **`false` (default)** — do not set `true` without Sam saying yes |
| `PUSH_WINDOW_START_HOUR`, `PUSH_WINDOW_END_HOUR` | optional quiet hours, Asia/Bangkok hours `[start, end)`; default `6` / `19` ([`docs/jobs.md`](jobs.md)) |
| `PUBLIC_BASE_URL` | optional HTTPS origin for chart deep links (else Railway domain) |

Never commit secret values; set them as Railway service variables.

Jobs (rain-check / anomaly-notify / notify): see [`docs/jobs.md`](jobs.md).

## Scheduler service

The periodic jobs ([`docs/jobs.md`](jobs.md)) run as a **second service from the same repo**:

1. New service → same GitHub repo, same branch.
2. Settings → *Config as Code* → file path `/railway.scheduler.json` (build is the same
   `Dockerfile`; start command `python -m canesat.scheduler`; no HTTP health check, since it serves
   nothing).
3. Variables: `DATABASE_URL`, plus the ones the jobs need — `EARTHDATA_TOKEN` (otherwise `rain_check`
   is skipped), `LINE_CHANNEL_ACCESS_TOKEN`, `PUBLIC_BASE_URL`, and `ENABLE_PUSH_ALERTS`
   (**`false`** — while it is off, `notify` is not scheduled at all). Optional `SCHEDULER_POLL_S`.
4. Run **one** replica. A second one is harmless (an advisory lock makes it wait) but pointless.

The scheduler applies migrations on start like the webhook does. On the very first deploy, start
the webhook service first: if both apply migrations at the same moment one of them may crash once
and recover on Railway's automatic restart.

## Redeploy

```bash
railway up --ci --service <app service> --environment production   # from the repo root
```
