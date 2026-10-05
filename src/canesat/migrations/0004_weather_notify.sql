-- 0004_weather_notify.sql — GPM/SMAP weather series, alert deliveries, consent confirm

-- Daily weather at each registered plot centroid (GPM IMERG rain + SMAP soil moisture).
CREATE TABLE IF NOT EXISTS weather_daily (
    plot_id        BIGINT NOT NULL REFERENCES plots (id) ON DELETE CASCADE,
    obs_date       DATE NOT NULL,
    precip_mm      REAL,                          -- GPM IMERG Late daily (mm/day)
    soil_moisture  REAL,                          -- SMAP SPL3SMP_E AM volumetric (m³/m³)
    sm_quality     INTEGER,                       -- retrieval_qual_flag (0 = best)
    dry_streak     INTEGER,                       -- consecutive days with precip < threshold (ending today)
    source_rain    TEXT,
    source_sm      TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plot_id, obs_date)
);
CREATE INDEX IF NOT EXISTS weather_daily_date_idx ON weather_daily (obs_date);

-- Push / dry-run delivery log (design §10 `delivery`). Quota is counted only for kind='push'.
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id           BIGSERIAL PRIMARY KEY,
    alert_id     BIGINT NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    user_id      BIGINT NOT NULL REFERENCES users (id),
    kind         TEXT NOT NULL CHECK (kind IN ('push', 'dry_run')),
    status       TEXT NOT NULL CHECK (status IN ('queued', 'sent', 'skipped', 'failed')),
    quota_month  TEXT,                            -- YYYY-MM (Asia/Bangkok)
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS alert_deliveries_alert_idx ON alert_deliveries (alert_id);
CREATE INDEX IF NOT EXISTS alert_deliveries_quota_idx ON alert_deliveries (quota_month)
    WHERE kind = 'push' AND status = 'sent';

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;

-- Member can confirm leader-assisted consent later (design §6.1).
ALTER TABLE consents
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS confirm_token TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS consents_confirm_token_uq
    ON consents (confirm_token) WHERE confirm_token IS NOT NULL;
