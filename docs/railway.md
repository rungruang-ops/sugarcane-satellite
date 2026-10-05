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
| `PUBLIC_BASE_URL` | optional HTTPS origin for chart deep links (else Railway domain) |

Never commit secret values; set them as Railway service variables.

Jobs (rain-check / anomaly-notify / notify): see [`docs/jobs.md`](jobs.md).

## Redeploy

```bash
railway up --ci --service <app service> --environment production   # from the repo root
```
