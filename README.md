# sugarcane-satellite (`canesat`)

ระบบแจ้งเตือนจากดาวเทียมสำหรับชาวไร่อ้อยผ่าน LINE — โครงการนำร่อง จ.ขอนแก่น (อ.หนองเรือ / อ.มัญจาคีรี)
Satellite-based alerts for sugarcane farmers via LINE — pilot in Khon Kaen, Thailand.

> **สถานะ / Status:** Milestone 1 — NDVI pipeline + anomaly engine · Milestone 2 (in progress) —
> LINE webhook, Rich Menu and LIFF plot registration for the **เบิ่งไฮ่** bot
> ([`docs/line-webhook.md`](docs/line-webhook.md)).
> Design: [`docs/design.md`](docs/design.md) (Thai, farmer-only version) ·
> mill version: [`docs/mill-version/design.md`](docs/mill-version/design.md)

![architecture](docs/architecture.png)

---

## ภาษาไทย

### คืออะไร
`canesat` คือส่วน "งานตามเวลา" ในแผนภาพสถาปัตยกรรม (design §9): ดึงภาพ Sentinel-2 L2A ฟรีจาก
Microsoft Planetary Computer (ไม่ต้องล็อกอิน) → คำนวณ NDVI รายแปลง → เทียบกับ "เพื่อนบ้าน" และปีก่อน →
สร้าง alert "แปลงเขียวน้อยกว่าเพื่อนบ้าน" (F1) เก็บลง PostgreSQL + PostGIS

ขั้นตอนต่อภาพ (ตาม design §5.1 และสคริปต์สำรวจขอนแก่นที่พิสูจน์แล้ว — ดู `docs/exploratory/`):
1. อ่าน B04, B08, SCL เฉพาะ window รอบแปลง (COG windowed read)
2. Cloud mask ด้วย SCL: เก็บเฉพาะ class 4 (พืช) และ 5 (ดินโล่ง) + ขยายขอบเมฆ/เงาเมฆ 1 พิกเซล
3. ลบ offset 1000 สำหรับ processing baseline ≥ 04.00 → NDVI = (B08 − B04)/(B08 + B04)
4. หดขอบแปลงเข้า 10 ม. → ค่ามัธยฐาน NDVI + สัดส่วนพิกเซลปลอดเมฆ (clear fraction); ภาพที่ clear < 60% หรือ < 4 พิกเซล ถูกติดธงและไม่ใช้ตัดสิน
5. เพื่อนบ้าน: (ก) แปลงที่ลงทะเบียน ≥ 8 แปลงในรัศมี 5 กม. อายุอ้อยต่างกัน ≤ 45 วัน ในภาพเดียวกัน หรือ (ข) ถ้าแปลงไม่พอ ใช้ "พิกเซลที่น่าจะเป็นอ้อย" (WorldCover 2021 cropland + NDVI > 0.6 ต้น ธ.ค.) ในวงแหวน 50 ม.–2.5 กม. รอบแปลง
6. ปีก่อน: ค่าของแปลงเดียวกัน ±10 วัน ในช่วงเดียวกันของ 1–3 ปีก่อน (`z_hist`)
7. กฎ: `gap_nb ≤ −15%` **และ** `z_nb ≤ −2` ติดต่อกัน 2 ภาพที่ใช้ได้ (ห่างกัน ≤ 20 วัน) → alert 🔴; cooldown 14 วัน; ไม่แจ้งอ้อยอายุ < 60 วัน; ช่วงเปิดหีบถ้า NDVI ลดฮวบ → `harvest_check` ("ตัดแล้วหรือยัง?"); ถ้า > 40% ของแปลงในตำบลผิดปกติพร้อมกัน → ระงับ (น่าจะเป็นเหตุระดับพื้นที่) — ค่าทั้งหมดปรับได้

### ติดตั้งและรันในเครื่อง
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # แก้ค่าตามต้องการ
docker compose up -d db         # PostGIS ที่ localhost:5433
export DATABASE_URL=postgresql://canesat:canesat@localhost:5433/canesat
canesat db migrate
canesat plot add "แปลงทดสอบหนองเรือ" --center 16.5589 102.4372 --size-m 1000
canesat ingest --plot-id 1 --start 2026-06-01 --end 2026-10-04
canesat detect --plot-id 1
canesat export --plot-id 1 --out out/plot1.csv
```
ผลการรันจริงกับกล่องหนองเรือ: [`examples/`](examples/README.md)

### แผนงานถัดไป
ดู [Roadmap](#roadmap) ด้านล่าง และ timeline ใน design §16

---

## English

### What it is
`canesat` is the scheduled-jobs part of the architecture (design §9): it searches Sentinel-2 L2A on
Microsoft Planetary Computer (anonymous, no login), computes per-plot NDVI with an SCL cloud mask,
compares each plot with its neighbours and with its own prior years, and creates
"less green than neighbours" alerts (feature F1). Results live in PostgreSQL + PostGIS.

### Layout
| Path | What |
|---|---|
| `src/canesat/ndvi.py` | pure raster math: BOA offset, NDVI, SCL clear mask + cloud dilation, zonal stats, likely-cane mask |
| `src/canesat/geometry.py` | reprojection, −10 m inner buffer, 20 m-snapped pixel grids, plot/ring masks |
| `src/canesat/stac.py` | Planetary Computer STAC search, scene de-duplication (reprocessed items, overlapping MGRS tiles), windowed COG reads |
| `src/canesat/ingest.py` | per-plot ingestion: plot stats + likely-cane ring stats for every scene |
| `src/canesat/baseline.py` | neighbour baseline (registered plots → cane-pixel ring fallback) and prior-year baseline |
| `src/canesat/anomaly.py` | **pure** anomaly engine (2-consecutive rule, cooldown, young cane, harvest check, area-wide suppression) |
| `src/canesat/detect.py` | runs the engine over stored observations, writes statuses + alerts |
| `src/canesat/db.py`, `migrations/` | psycopg 3 data access + plain-SQL migrations |
| `src/canesat/cli.py` | `canesat` CLI |
| `docs/` | design docs, mockups, architecture, exploratory Khon Kaen NDVI work |

### Data model (`src/canesat/migrations/0001_init.sql`)
`users`, `farmer_groups`, `group_members`, **`plots`** (MultiPolygon 4326, owner/group, tambon,
`cane_type` plant/ratoon, `planting_date`, `ratoon_no`, `last_harvest_date`), `scenes`,
**`ndvi_observations`** (plot × scene: `obs_date`, `median_ndvi`, p10/p90, `clear_fraction`,
`is_clear`, `flags`, ring baseline, and evaluation columns `gap_nb`, `z_nb`, `z_hist`, `status`),
**`alerts`** (`type`, `severity`, `status`, `obs_date`, `z_score`, `gap`, `details` JSONB,
`feedback`, `feedback_note`, `feedback_at`), `settings` (JSONB thresholds, key `anomaly`).

### Setup & running locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db                     # PostGIS 16-3.4 on localhost:5433 (POSTGIS_PORT to change)
export DATABASE_URL=postgresql://canesat:canesat@localhost:5433/canesat
canesat db migrate
canesat plot add "My plot" --geojson plot.geojson --cane-type ratoon --last-harvest-date 2026-02-15
canesat plot add "Nong Ruea box" --center 16.5589 102.4372 --size-m 1000
canesat ingest --plot-id 1 --start 2026-06-01 --end 2026-10-04     # one plot
canesat ingest --all --start 2026-09-01 --end 2026-10-04           # batch; already-stored scenes are skipped
canesat detect --all
canesat alerts
canesat export --plot-id 1 --out out/plot1.json                   # .csv or .json
canesat preview --center 16.5589 102.4372 --start 2026-08-01 --end 2026-10-04   # no DB needed
```
Thresholds: `canesat --config config/thresholds.example.toml detect --all`, or store JSON in
`settings` (`key='anomaly'`). Defaults are the design-doc starting values, **not validated
agronomic thresholds**.

### Tests
```bash
ruff check . && ruff format --check .
pytest                       # unit tests; DB tests run only when DATABASE_URL is set
pytest -m network            # live Planetary Computer test (skipped by default and in CI)
```
CI (GitHub Actions) runs ruff + pytest on Python 3.11 and 3.13 with a PostGIS service container.

### Real end-to-end run
See [`examples/README.md`](examples/README.md) — Nong Ruea 1×1 km reference box, real Sentinel-2 data.
Reproduce with `scripts/e2e_nong_ruea.sh`.

### Known limitations
- The "likely cane" mask is an approximation (WorldCover 2021 cropland + NDVI > 0.6 on one
  early-December scene, default 2025-11-20/2025-12-20), not a verified cane map; one mask is used for
  all dates of a plot and should be refreshed every season (`cane_mask_refresh`, design §9.3).
- Plot statistics use pixel centres inside the buffered polygon (no fractional coverage yet);
  tiny plots fall back to the unbuffered polygon and get the `small_plot_no_buffer` flag.
- Ring baseline MAD is a per-pixel spread, so `z_nb` vs. the ring is conservative compared with the
  registered-plot baseline (spread of plot medians).
- Per-user push limits / quiet hours / LINE quota belong to the notify job (next milestone).

---

## Roadmap
Following [`docs/design.md`](docs/design.md) §16:
1. ✅ **Milestone 1 (this):** scaffold, PostGIS schema, Sentinel-2 ingestion, neighbour & prior-year
   baselines, anomaly engine, CLI, tests, CI.
2. 🚧 **LINE OA webhook** — done: FastAPI `/callback` (`X-Line-Signature` check), follow/unfollow →
   `users`, keyword replies, feedback postbacks → `alerts.feedback`, Flex alert builder, Rich Menu
   spec ([`docs/line-webhook.md`](docs/line-webhook.md)). Todo: notify job with push quota counter
   (300/month free tier) and quiet hours.
3. 🚧 **LIFF**: ✅ plot registration, ✅ comparison chart (`/liff/chart`), ✅ member consent confirm
   (`/liff/consent`); group page for leaders still open.
4. ✅ **GPM IMERG + SMAP drought / rain-back alerts** (F2) — `canesat rain-check` + gated
   `canesat notify` (`ENABLE_PUSH_ALERTS=false` by default). See [`docs/jobs.md`](docs/jobs.md).
5. Scheduler (cron in Docker: `s2_fetch` every 12 h, `backfill_plot` on registration,
   `cane_mask_refresh` yearly), monitoring, Earth Search fallback STAC.
