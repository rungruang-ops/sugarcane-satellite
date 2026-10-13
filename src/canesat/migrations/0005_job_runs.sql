-- 0005_job_runs.sql — run log for the scheduler (canesat.scheduler)
--
-- One row per attempt of a scheduled job (s2_fetch, ndvi_rules, rain_check, notify).
-- The scheduler decides what is due from the latest rows here, so a restart or redeploy
-- never re-runs a job that already ran recently. `running` rows left behind by a crash are
-- marked `abandoned` by the next scheduler that holds the lock.

CREATE TABLE IF NOT EXISTS job_runs (
    id           BIGSERIAL PRIMARY KEY,
    job          TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'ok', 'failed', 'abandoned')),
    exit_code    INTEGER,
    detail       TEXT                              -- tail of the job's output (truncated)
);
CREATE INDEX IF NOT EXISTS job_runs_job_started_idx ON job_runs (job, started_at DESC);
