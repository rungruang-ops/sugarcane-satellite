# Scheduled / manual jobs (เบิ่งไฮ่)

Push alerts are **off by default**. `ENABLE_PUSH_ALERTS` must stay `false` on Railway
until Sam explicitly says yes (LINE OA Free plan = 300 messages/month).

## Scheduler (`python -m canesat.scheduler`)

Runs the jobs below automatically (design §9.3) as a second Railway service
([`docs/railway.md`](railway.md)). Each job is the existing CLI command run in a subprocess, so
behaviour is identical to running it by hand.

| Job | When | Command | Skipped ("blocked") while |
|---|---|---|---|
| `s2_fetch` | every 12 h | `ingest --all --start <today-14d> --end <today>` (stored scenes are skipped) | — |
| `ndvi_rules` | once after each successful `s2_fetch` | `detect --all` | no successful `s2_fetch` yet |
| `rain_check` | once per day from 08:00 Asia/Bangkok | `rain-check --all` | `EARTHDATA_TOKEN` is unset |
| `notify` | every 30 min | `notify` | `ENABLE_PUSH_ALERTS` is off, or outside the send window (06:00–19:00) |

* **Push stays opt-in.** The scheduler never turns push on and never passes `--enable-push`;
  `notify` only runs when `ENABLE_PUSH_ALERTS` is already `true`, and the command itself still applies
  quiet hours and the monthly quota. (Running `notify` as a dry run every 30 min would only pile up
  `dry_run` rows, so it is not scheduled while push is off.)
* **Failures** are retried 30 min later, at most 4 attempts per cycle (per day for `rain_check`, per
  `s2_fetch` result for `ndvi_rules`), then the job waits for its next normal slot.
* **No double runs.** What is due comes from the `job_runs` table, so restarts and redeploys don't
  repeat a job that just ran; a Postgres advisory lock keeps two scheduler instances from working at
  once. A run left `running` by a crash is marked `abandoned` and the job runs again.
* `job_runs.detail` keeps the last 2000 characters of a job's output (known secrets are masked).

```bash
canesat schedule status          # last attempt + due / waiting / blocked per job
canesat schedule run --once      # run whatever is due right now, then exit
canesat schedule run             # same loop as the service (--poll-s, default 60)
```

The service itself reads `SCHEDULER_POLL_S` (seconds between passes, default 60).

Not scheduled yet: `backfill_plot` (new plots still get a 92-day background ingest from the LIFF
registration) and `cane_mask_refresh` (yearly).

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

### Quiet hours

With push enabled, `notify` only sends when the Asia/Bangkok hour is in `[START, END)`
(default **06:00–19:00**, design §9.3). Outside the window it sends nothing and writes nothing:
pending alerts stay `pending`, the report shows `"in_send_window": false` and a `deferred`
count, and the first run inside the next window delivers them (priority order is unchanged).
Dry runs are not gated.

| Variable | Default | Meaning |
|---|---|---|
| `PUSH_WINDOW_START_HOUR` | `6` | first hour (inclusive) pushes may go out |
| `PUSH_WINDOW_END_HOUR` | `19` | hour (exclusive) pushes stop; `24` = until midnight |

Invalid values (non-integer, `start >= end`, outside 0–24) fall back to 06–19 with a log
warning, so a typo can never turn into night-time pushes.

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
