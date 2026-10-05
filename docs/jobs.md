# Scheduled / manual jobs (เบิ่งไฮ่)

Push alerts are **off by default**. `ENABLE_PUSH_ALERTS` must stay `false` on Railway
until Sam explicitly says yes (LINE OA Free plan = 300 messages/month).

## Rain-gap / drought (`canesat rain-check`)

Fetches GPM IMERG Late daily rain + SMAP SPL3SMP_E soil moisture for registered plot
centroids, upserts `weather_daily`, and creates `rain_gap` / `rain_back` rows in `alerts`
(status=`pending`). Does **not** push.

```bash
export DATABASE_URL=...
export EARTHDATA_TOKEN=...   # from box-secrets / URS Generate Token
canesat db migrate
canesat rain-check --all -v
# fixture / offline demo:
canesat rain-check --plot-id 1 --as-of 2026-09-20 \
  --fixture-rain /path/rain.json --fixture-soil /path/soil.json
```

Thresholds (design §5.2, starting points): dry day &lt; 1 mm, ≥ 14 consecutive dry days,
SMAP &lt; 0.15 m³/m³, both required; rain-back = 3-day sum ≥ 20 mm after a prior gap.
Override via `--config` TOML `[rain]` or `settings` key `rain`.

## NDVI anomaly → alert queue (`canesat anomaly-notify`)

Runs `detect` then the notify dry-run/push gate for `greenness` / `harvest_check`.

```bash
canesat anomaly-notify --all          # detect + dry-run deliveries
canesat detect --all && canesat notify --dry-run
```

## Notify / push gate (`canesat notify`)

```bash
canesat notify --dry-run              # default; writes alert_deliveries kind=dry_run
# ONLY when Sam has approved push on this OA:
ENABLE_PUSH_ALERTS=true canesat notify --enable-push
```

How Sam turns push on later:
1. Confirm LINE OA Free quota headroom (or upgrade plan).
2. Set Railway variable `ENABLE_PUSH_ALERTS=true` on `bernghai-bot` (or run the CLI with
   that env once from a trusted host).
3. Keep monthly quota soft-cap in code (default 280) so we do not blow the 300 limit.

## Comparison chart

- LIFF/web: `GET /liff/chart?plot_id=<id>` (LIFF ID token required)
- API: `GET /api/plots/<id>/chart` (Bearer LINE ID token; owner, registrar, or group leader)
- Bot keyword `เทียบเพื่อนบ้าน` links to `/liff/chart` when `PUBLIC_BASE_URL` / Railway domain is set

## Member consent confirmation

When a group leader registers a plot on behalf of a member, `consents.method=assisted_pending`
and `service.granted=false` until the member opens `/liff/consent?token=...` and confirms
with their own LINE account (`POST /api/consents/confirm`).
