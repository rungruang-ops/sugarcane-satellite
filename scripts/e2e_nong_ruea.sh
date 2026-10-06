#!/usr/bin/env bash
# Reproduce the real end-to-end run saved in examples/ (needs internet + local PostGIS).
#   docker compose up -d db && pip install -e ".[dev]" && scripts/e2e_nong_ruea.sh
set -euo pipefail
export DATABASE_URL="${DATABASE_URL:-postgresql://canesat:canesat@localhost:5433/canesat}"
export CANESAT_CACHE_DIR="${CANESAT_CACHE_DIR:-.cache/canesat}"

canesat db migrate
NR=$(canesat plot add "Nong Ruea 1x1 km reference box" --center 16.5589 102.4372 --size-m 1000)
canesat plot add "Mancha Khiri 1x1 km reference box" --center 16.0988 102.4899 --size-m 1000
# prior-year same period (for z_hist), then the current season in batch mode for all plots
canesat -v ingest --plot-id "$NR" --start 2025-06-01 --end 2025-10-04 --workers 4
canesat -v ingest --all --start 2026-06-01 --end 2026-10-04 --workers 4
canesat detect --all
canesat export --plot-id "$NR" --out examples/nong_ruea_ndvi.csv
canesat export --plot-id "$NR" --out examples/nong_ruea_result.json
canesat alerts
