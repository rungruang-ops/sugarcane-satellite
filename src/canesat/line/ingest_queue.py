"""Lightweight background NDVI backfill for plots registered from LIFF.

One worker thread, FIFO queue: never blocks the request. Each job ingests the last ~3 months
of Sentinel-2 for the plot (reusing canesat.ingest), then evaluates it (canesat.detect).
Detection may create *pending* alerts; nothing is sent to LINE from here.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import date, timedelta

log = logging.getLogger(__name__)

BACKFILL_DAYS = 92


def ingest_plot(dsn: str, plot_id: int, days: int = BACKFILL_DAYS) -> dict:
    from .. import db
    from ..config import AnomalyConfig, IngestConfig, Settings
    from ..detect import run_detect
    from ..geometry import geom_from_geojson
    from ..ingest import PlotIngestor

    end = date.today()
    start = end - timedelta(days=days)
    ingestor = PlotIngestor(IngestConfig(), cache_dir=Settings().cache_dir)
    with db.connect(dsn) as conn:
        row = db.get_plot(conn, plot_id)
        if row is None:
            raise LookupError(f"plot {plot_id} not found")
        geom = geom_from_geojson(row["geojson"])
        obs, meta = ingestor.ingest(
            geom,
            start.isoformat(),
            end.isoformat(),
            db.ingested_item_ids(conn, plot_id),
            workers=4,
            use_ring=True,
        )
        n = db.upsert_observations(conn, plot_id, obs)
        cfg = AnomalyConfig().merged(db.get_setting(conn, "anomaly") or {})
        summary = run_detect(conn, [plot_id], cfg)
        conn.commit()
    return {
        "plot_id": plot_id,
        "stored": n,
        "clear": sum(o.is_clear for o in obs),
        "scenes_found": meta.get("n_scenes_found"),
        "errors": meta.get("n_errors"),
        "detect": summary.get("plots", {}).get(plot_id),
    }


class IngestQueue:
    def __init__(self, job: Callable[[int], dict]) -> None:
        self._job = job
        self._q: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, plot_id: int) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._worker, name="ndvi-ingest", daemon=True
                )
                self._thread.start()
        self._q.put(plot_id)
        log.info("ingest queued plot=%s (queue=%d)", plot_id, self._q.qsize())

    def _worker(self) -> None:
        while True:
            plot_id = self._q.get()
            try:
                log.info("ingest start plot=%s", plot_id)
                log.info("ingest done %s", self._job(plot_id))
            except Exception as exc:
                log.warning("ingest failed plot=%s: %s: %s", plot_id, type(exc).__name__, exc)
            finally:
                self._q.task_done()

    def join(self) -> None:
        self._q.join()
