# Real end-to-end run — Nong Ruea reference box (หนองเรือ)

Run on 2026-10-04 (Asia/Bangkok) against live Sentinel-2 L2A from Microsoft Planetary Computer,
stored in local PostGIS (`docker compose up -d db`), reproducible with `scripts/e2e_nong_ruea.sh`.
**Nothing here is synthetic.** Full command output: [`run_log.txt`](run_log.txt).

| | |
|---|---|
| Plot | "Nong Ruea 1x1 km reference box", centre 16.5589 N, 102.4372 E, 624.34 rai (a 1 km² box, *not* a real farmer's plot; ~79 % of it is "likely cane" per the exploratory work) |
| Windows | 2025-06-01 → 2025-10-04 (prior-year baseline) and 2026-06-01 → 2026-10-04 (current season) |
| Scenes | 36 acquisition dates (20 + 16), all tile 48QTD, 0 read errors |
| Plot pixels | 9,604 (10 m pixels after the −10 m inner buffer) |
| Cane-pixel ring | 92,494 likely-cane pixels of 293,947 ring pixels (50 m – 2.5 km), mask from `S2C_MSIL2A_20251201T034131_R061_T48QTD_20251201T063318` |
| Clear observations (≥ 60 % clear) | **13 of 36** (6 in 2025, 7 in 2026) — rainy season: 15 dates had no clear pixel in the plot, 8 were below 60 % |
| Plot median NDVI (clear obs) | 0.479 – 0.793 |
| `gap_nb` vs neighbour ring | −5.5 % … +15.2 % |
| `z_nb` | −0.37 … +0.68 |
| `z_hist` (2026 dates with a 2025 ±10-day match, 6 obs) | −1.02 … +1.59 |
| Status | all 13 clear observations `normal` |
| **Alerts** | **0** |

Batch mode (`ingest --all`) also processed the Mancha Khiri reference box (16.0988 N, 102.4899 E,
tile 48PTC) for 2026-06-01 → 2026-10-04: 17 dates, 3 clear, all `normal`, 0 alerts.

## What it found
The Nong Ruea box tracks its surrounding likely-cane pixels closely all season (|gap| ≤ 15 %,
|z| < 1), and the 2026 season is within normal range of 2025 for the same dates, so the engine
correctly raises **no alert**. This is the expected outcome for a large, mostly-cane reference area
and shows the pipeline runs end to end on real data (search → SCL mask → plot + ring stats → DB →
baselines → rules → statuses). It does **not** demonstrate that alerts are accurate; that needs real
farmer plots and field feedback (design §13–14). The alert paths themselves are covered by synthetic
unit tests (`tests/test_anomaly.py`, `tests/test_detect.py`, `tests/test_db.py`).

Notable real-data observations:
- 2025-08-28 (scene cloud 95 %) the plot median dropped to 0.479 but the ring dropped equally
  (0.507), so `gap_nb` stayed at −5.5 % — a scene-wide haze effect correctly *not* treated as a plot
  problem. This is why neighbour comparison is done within the same scene.
- Scene-level `eo:cloud_cover` is a poor filter: 2026-08-18 (81 % scene cloud) and 2026-09-27 (92 %)
  were fully clear over the plot, while 2026-07-19 (51 %) had no clear pixel at all.
- Values agree with the exploratory script for the same dates (e.g. 2026-09-29: 0.779 median here vs
  0.759 cropland mean there; 2026-10-02 rejected here at 54 % clear after 1-pixel cloud dilation,
  59 % valid there without dilation).

## Files
- [`nong_ruea_ndvi.csv`](nong_ruea_ndvi.csv) — all 36 observations incl. rejected ones (`is_clear`, `flags`), baselines, `gap_nb`, `z_nb`, `z_hist`, `status`
- [`nong_ruea_result.json`](nong_ruea_result.json) — plot (with geometry) + observations + alerts (empty)
- [`run_log.txt`](run_log.txt) — ingest/detect output
