"""Scheduler: runs the periodic jobs (design §9.3) so nobody has to start them by hand.

    python -m canesat.scheduler            # long-running loop (Railway second service)
    canesat schedule run [--once]          # same, or a single pass (cron / debugging)
    canesat schedule status                # what ran last, what is due, what is blocked

Each job is an existing `canesat` CLI command run in a subprocess, so a crash or a hang in one
job can't take the scheduler down and the commands behave exactly as when run by hand.

| job          | runs                                  | command                       |
|--------------|---------------------------------------|-------------------------------|
| s2_fetch     | every 12 h                            | ingest --all (last 14 days)   |
| ndvi_rules   | after each successful s2_fetch        | detect --all                  |
| rain_check   | daily from 08:00 Asia/Bangkok         | rain-check --all              |
| notify       | every 30 min, 06:00-19:00, push on    | notify                        |

What is due is decided from the `job_runs` table, so restarts and redeploys never repeat a job
that already ran. A Postgres advisory lock keeps two scheduler instances from running at once.

Safety: the scheduler never turns push on. `notify` is only run when ENABLE_PUSH_ALERTS is
already true in the environment (a dry run every 30 min would just pile up `dry_run` rows), and
the command itself still enforces quiet hours and the monthly quota. `rain_check` is skipped
while EARTHDATA_TOKEN is unset.

Deliberately not here yet: `backfill_plot` and `cane_mask_refresh` (design §9.3).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger("canesat.scheduler")

BANGKOK = ZoneInfo("Asia/Bangkok")
LOCK_KEY = 7_204_001  # arbitrary app-wide Postgres advisory-lock id for "the scheduler"
DETAIL_MAX = 2000  # characters of job output kept in job_runs.detail
HISTORY_DAYS = 3  # how far back run history is read when deciding what is due
S2_LOOKBACK_DAYS = 14  # Sentinel-2 revisits every ~5 days; stored scenes are skipped anyway
RAIN_CHECK_HOUR = 8  # Asia/Bangkok hour from which the daily rain job may run
# Secrets that must never be written into job_runs.detail if a job happens to echo them.
SECRET_ENV_KEYS = (
    "DATABASE_URL",
    "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_CHANNEL_SECRET",
    "EARTHDATA_TOKEN",
    "CANESAT_PHONE_KEY",
)

# keepalives: a job can run for a long time while the scheduler's connection sits idle
CONNECT_KWARGS: dict[str, Any] = {
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 20,
    "keepalives_count": 5,
}


@dataclass(frozen=True)
class Run:
    job: str
    started_at: datetime  # timezone-aware
    status: str  # running | ok | failed | abandoned
    finished_at: datetime | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class Job:
    """A scheduled job. Exactly one of `every`, `daily_at_hour`, `after` says when it is due."""

    name: str
    args: Callable[[datetime], list[str]]  # arguments to `python -m canesat.cli`
    every: timedelta | None = None  # fixed interval since the last attempt
    daily_at_hour: int | None = None  # once per Bangkok day, from this hour
    after: str | None = None  # run once after each successful run of this job
    retry_after: timedelta = timedelta(minutes=30)  # wait before retrying a failed run
    max_attempts: int = 4  # then wait for the next cycle instead of retrying
    timeout_s: int = 3600
    requires_env: tuple[str, ...] = ()  # blocked (not failed) while any of these is unset
    needs_push: bool = False  # blocked while ENABLE_PUSH_ALERTS is off
    send_window_only: bool = False  # blocked outside the quiet-hours window


def _s2_fetch_args(now: datetime) -> list[str]:
    end = now.date()
    start = end - timedelta(days=S2_LOOKBACK_DAYS)
    return ["ingest", "--all", "--start", start.isoformat(), "--end", end.isoformat()]


# Order matters: within one pass jobs run top to bottom, so fresh alerts go out in the same pass.
JOBS: tuple[Job, ...] = (
    Job("s2_fetch", _s2_fetch_args, every=timedelta(hours=12), timeout_s=4 * 3600),
    Job("ndvi_rules", lambda now: ["detect", "--all"], after="s2_fetch", timeout_s=1800),
    Job(
        "rain_check",
        lambda now: ["rain-check", "--all"],
        daily_at_hour=RAIN_CHECK_HOUR,
        requires_env=("EARTHDATA_TOKEN",),
        timeout_s=3600,
    ),
    Job(
        "notify",
        lambda now: ["notify"],
        every=timedelta(minutes=30),
        needs_push=True,
        send_window_only=True,
        timeout_s=900,
    ),
)


# ---------------------------------------------------------------- deciding what is due (pure)
def blocked_reason(job: Job, now: datetime) -> str | None:
    """Why `job` must not run right now (not a failure), or None."""
    for key in job.requires_env:
        if not os.environ.get(key):
            return f"{key} is not set"
    if job.needs_push or job.send_window_only:
        from . import notify  # lazy: pulls in psycopg/LINE helpers

        if job.needs_push and not notify.push_alerts_enabled():
            return "ENABLE_PUSH_ALERTS is off"
        if job.send_window_only and not notify.within_send_window(now):
            return "outside the send window"
    return None


def _newest_first(runs: Sequence[Run]) -> list[Run]:
    return sorted(runs, key=lambda r: r.started_at, reverse=True)


def _retry_due(job: Job, attempts: Sequence[Run], now: datetime) -> bool:
    """`attempts` are this job's runs within the current trigger/day/cycle, newest first."""
    if not attempts:
        return True
    if any(r.status == "ok" for r in attempts):
        return False
    if len(attempts) >= job.max_attempts:
        return False
    return now - attempts[0].started_at >= job.retry_after


def is_due(job: Job, runs: Mapping[str, Sequence[Run]], now: datetime) -> bool:
    """Pure schedule decision from run history. `now` must be timezone-aware (Asia/Bangkok)."""
    own = runs.get(job.name, ())
    if any(r.status == "running" for r in own):
        return False
    done = _newest_first([r for r in own if r.status in ("ok", "failed")])  # abandoned: ignored

    if job.after:
        upstream = [
            r.finished_at
            for r in runs.get(job.after, ())
            if r.status == "ok" and r.finished_at is not None
        ]
        if not upstream:
            return False
        trigger = max(upstream)  # a new upstream result starts a new cycle for this job
        return _retry_due(job, [r for r in done if r.started_at >= trigger], now)

    if job.daily_at_hour is not None:
        if now.hour < job.daily_at_hour:
            return False
        today = [
            r
            for r in done
            if (local := r.started_at.astimezone(BANGKOK)).date() == now.date()
            and local.hour >= job.daily_at_hour
        ]
        return _retry_due(job, today, now)

    if job.every is None:
        raise ValueError(f"job {job.name!r} has no schedule")
    if not done:
        return True
    last = done[0]
    if last.status == "ok":
        return now - last.started_at >= job.every
    streak = 0
    for r in done:
        if r.status != "failed":
            break
        streak += 1
    wait = job.retry_after if streak < job.max_attempts else job.every
    return now - last.started_at >= wait


# ---------------------------------------------------------------- running one job
def _redact(text: str) -> str:
    for key in SECRET_ENV_KEYS:
        value = os.environ.get(key)
        if value and len(value) >= 6:
            text = text.replace(value, f"<{key}>")
    return text


def _tail(text: str) -> str:
    text = _redact(text.strip())
    return text if len(text) <= DETAIL_MAX else "…" + text[-DETAIL_MAX:]


def run_job(
    job: Job,
    now: datetime,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[str, int | None, str]:
    """Run the job's CLI command in a subprocess -> (status, exit_code, output tail)."""
    cmd = [sys.executable, "-m", "canesat.cli", *job.args(now)]
    try:
        proc = run(
            cmd,
            capture_output=True,
            text=True,
            timeout=job.timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        return "failed", None, _tail(f"timed out after {job.timeout_s}s\n{out or ''}")
    except OSError as exc:
        return "failed", None, _tail(f"could not start: {exc}")
    output = (proc.stdout or "") + (proc.stderr or "")
    return ("ok" if proc.returncode == 0 else "failed"), proc.returncode, _tail(output)


# ---------------------------------------------------------------- run log (job_runs)
class Store(Protocol):
    def abandon_stale(self) -> int: ...
    def load_runs(self, now: datetime) -> dict[str, list[Run]]: ...
    def start_run(self, job: str, now: datetime) -> int: ...
    def finish_run(
        self,
        run_id: int,
        status: str,
        exit_code: int | None,
        detail: str,
        finished_at: datetime,
    ) -> None: ...


class PgStore:
    """`Store` on the job_runs table (migration 0005). `conn` is a psycopg connection."""

    def __init__(self, conn):
        self.conn = conn

    def abandon_stale(self) -> int:
        """Only call while holding the advisory lock: every `running` row is then a leftover."""
        cur = self.conn.execute(
            "UPDATE job_runs SET status = 'abandoned', finished_at = now() WHERE status = 'running'"
        )
        self.conn.commit()
        return cur.rowcount

    def load_runs(self, now: datetime) -> dict[str, list[Run]]:
        rows = self.conn.execute(
            "SELECT job, started_at, finished_at, status, exit_code FROM job_runs"
            " WHERE started_at >= %s ORDER BY started_at DESC",
            (now - timedelta(days=HISTORY_DAYS),),
        ).fetchall()
        self.conn.commit()
        out: dict[str, list[Run]] = {}
        for r in rows:
            out.setdefault(r["job"], []).append(
                Run(r["job"], r["started_at"], r["status"], r["finished_at"], r["exit_code"])
            )
        return out

    def start_run(self, job: str, now: datetime) -> int:
        row = self.conn.execute(
            "INSERT INTO job_runs (job, started_at, status) VALUES (%s, %s, 'running')"
            " RETURNING id",
            (job, now),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def finish_run(
        self,
        run_id: int,
        status: str,
        exit_code: int | None,
        detail: str,
        finished_at: datetime,
    ) -> None:
        # finished_at comes from the scheduler's clock (like started_at) so the "new upstream
        # result" comparison never mixes this clock with the database server's.
        self.conn.execute(
            "UPDATE job_runs SET status = %s, exit_code = %s, detail = %s, finished_at = %s"
            " WHERE id = %s",
            (status, exit_code, detail, finished_at, run_id),
        )
        self.conn.commit()


def try_lock(conn) -> bool:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,)).fetchone()
    return bool(row["ok"])


def unlock(conn) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


# ---------------------------------------------------------------- one pass / the loop
def _now() -> datetime:
    return datetime.now(tz=BANGKOK)


def tick(
    store: Store,
    *,
    jobs: Sequence[Job] = JOBS,
    clock: Callable[[], datetime] = _now,
    runner: Callable[[Job, datetime], tuple[str, int | None, str]] = run_job,
) -> list[dict[str, Any]]:
    """One pass over all jobs: run whatever is due. The caller must hold the advisory lock."""
    stale = store.abandon_stale()
    if stale:
        log.warning("marked %d leftover running job(s) as abandoned", stale)
    report: list[dict[str, Any]] = []
    for job in jobs:
        now = clock()  # re-read: earlier jobs in this pass may have taken a long time
        reason = blocked_reason(job, now)
        if reason:
            report.append({"job": job.name, "action": "blocked", "reason": reason})
            continue
        if not is_due(job, store.load_runs(now), now):
            report.append({"job": job.name, "action": "not_due"})
            continue
        run_id = store.start_run(job.name, now)
        log.info("job %s: start", job.name)
        status, code, detail = runner(job, now)
        store.finish_run(run_id, status, code, detail, clock())
        log.log(
            logging.INFO if status == "ok" else logging.ERROR,
            "job %s: %s (exit %s)%s",
            job.name,
            status,
            code,
            "" if status == "ok" else f"\n{detail}",
        )
        report.append({"job": job.name, "action": "ran", "status": status, "exit_code": code})
    return report


def run(
    dsn: str,
    *,
    once: bool = False,
    poll_s: float = 60.0,
    stop: threading.Event | None = None,
    jobs: Sequence[Job] = JOBS,
) -> None:
    """Loop until `stop` is set (or one pass when `once`). Errors in a pass are logged, not fatal,
    except with `once`, where they propagate so the exit code reflects them."""
    from . import db

    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            with db.connect(dsn, **CONNECT_KWARGS) as conn:
                if try_lock(conn):
                    try:
                        tick(PgStore(conn), jobs=jobs)
                    finally:
                        unlock(conn)
                else:
                    log.info("another scheduler instance holds the lock; skipping this pass")
        except Exception:
            if once:
                raise
            log.exception("scheduler pass failed; will retry")
        if once:
            return
        stop.wait(poll_s)


def describe(
    store: Store, *, jobs: Sequence[Job] = JOBS, now: datetime | None = None
) -> list[dict]:
    """Per job: last attempt, whether it is due, or why it is blocked (for `schedule status`)."""
    now = now or _now()
    runs = store.load_runs(now)
    out = []
    for job in jobs:
        last = next(iter(_newest_first(runs.get(job.name, []))), None)
        reason = blocked_reason(job, now)
        out.append(
            {
                "job": job.name,
                "last_status": last.status if last else None,
                "last_started": last.started_at.astimezone(BANGKOK).isoformat() if last else None,
                "state": f"blocked: {reason}"
                if reason
                else ("due" if is_due(job, runs, now) else "waiting"),
            }
        )
    return out


def main() -> None:
    """Entry point: `python -m canesat.scheduler` (settings from the environment)."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL is required")
    if os.environ.get("CANESAT_MIGRATE_ON_START", "1") not in ("0", "false"):
        from .line.serve import migrate_with_retry

        if migrate_with_retry(dsn) is None:
            sys.exit("could not apply migrations (job_runs table needed); exiting")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    poll_s = float(os.environ.get("SCHEDULER_POLL_S", "60"))
    log.info("scheduler started: %s", ", ".join(j.name for j in JOBS))
    run(dsn, poll_s=poll_s, stop=stop)
    log.info("scheduler stopped")


if __name__ == "__main__":
    main()
