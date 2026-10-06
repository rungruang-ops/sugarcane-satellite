-- 0001_init.sql — core schema for the NDVI pipeline + anomaly engine
-- Follows docs/design.md §10 (farmer-only version). LINE delivery, consent and
-- weather tables come in later migrations.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- people
CREATE TABLE users (
    id               BIGSERIAL PRIMARY KEY,
    line_user_id     TEXT UNIQUE,                     -- NULL until LINE milestone / for members without LINE
    display_name     TEXT NOT NULL,
    phone_enc        BYTEA,                           -- encrypted at app level (PDPA)
    role             TEXT NOT NULL DEFAULT 'farmer'
                     CHECK (role IN ('farmer', 'leader', 'admin')),
    has_water_source BOOLEAN,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ
);

CREATE TABLE farmer_groups (
    id             BIGSERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    tambon_code    TEXT,
    leader_user_id BIGINT REFERENCES users (id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id           BIGINT NOT NULL REFERENCES farmer_groups (id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    leader_can_view    BOOLEAN NOT NULL DEFAULT FALSE,   -- separate consent (design §6.2)
    leader_gets_alerts BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (group_id, user_id)
);

-- ---------------------------------------------------------------- plots
CREATE TABLE plots (
    id                BIGSERIAL PRIMARY KEY,
    owner_user_id     BIGINT REFERENCES users (id),
    group_id          BIGINT REFERENCES farmer_groups (id),
    name              TEXT NOT NULL,
    geom              geometry(MultiPolygon, 4326) NOT NULL,
    area_rai          NUMERIC(10, 2),                   -- 1 rai = 1,600 m²
    tambon_code       TEXT,
    cane_type         TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (cane_type IN ('plant', 'ratoon', 'unknown')),
    planting_date     DATE,                             -- plant cane: (approx.) planting date
    ratoon_no         SMALLINT CHECK (ratoon_no IS NULL OR ratoon_no >= 1),
    last_harvest_date DATE,                             -- ratoon: last cut (approx. month is fine)
    registered_by     BIGINT REFERENCES users (id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at       TIMESTAMPTZ,
    CONSTRAINT plots_geom_valid CHECK (ST_IsValid(geom))
);
CREATE INDEX plots_geom_gix ON plots USING GIST (geom);
CREATE INDEX plots_geog_gix ON plots USING GIST ((geom::geography));

-- ---------------------------------------------------------------- satellite
CREATE TABLE scenes (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL DEFAULT 'planetary-computer:sentinel-2-l2a',
    item_id             TEXT NOT NULL UNIQUE,
    acquired_at         TIMESTAMPTZ NOT NULL,
    mgrs_tile           TEXT,
    cloud_cover         REAL,
    processing_baseline TEXT,
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX scenes_acquired_idx ON scenes (acquired_at);

CREATE TABLE ndvi_observations (
    plot_id             BIGINT NOT NULL REFERENCES plots (id) ON DELETE CASCADE,
    scene_id            BIGINT NOT NULL REFERENCES scenes (id),
    obs_date            DATE NOT NULL,                 -- acquisition date (UTC)
    -- plot statistics (inner buffer -10 m, SCL classes 4/5 only)
    median_ndvi         REAL,
    p10_ndvi            REAL,
    p90_ndvi            REAL,
    n_px                INTEGER NOT NULL,              -- pixels inside (buffered) plot
    n_clear_px          INTEGER NOT NULL,
    clear_fraction      REAL NOT NULL,
    is_clear            BOOLEAN NOT NULL,              -- passes clear_fraction / min-pixel thresholds
    flags               TEXT[] NOT NULL DEFAULT '{}',  -- e.g. low_clear_fraction, small_plot, no_buffer
    -- likely-cane pixels in a ring around the plot, same scene (fallback neighbour baseline)
    ring_median         REAL,
    ring_mad            REAL,
    ring_n_px           INTEGER,
    ring_clear_fraction REAL,
    -- evaluation (filled by `canesat detect`)
    nb_source           TEXT CHECK (nb_source IN ('registered', 'cane_pixels')),
    nb_median           REAL,
    nb_mad              REAL,
    nb_n                INTEGER,
    gap_nb              REAL,
    z_nb                REAL,
    hist_mean           REAL,
    hist_n_years        SMALLINT,
    z_hist              REAL,
    status              TEXT,                          -- normal|watch|alert|insufficient|...
    evaluated_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plot_id, scene_id)
);
CREATE INDEX ndvi_obs_plot_date_idx ON ndvi_observations (plot_id, obs_date);
CREATE INDEX ndvi_obs_date_idx ON ndvi_observations (obs_date);

-- ---------------------------------------------------------------- alerts
CREATE TABLE alerts (
    id            BIGSERIAL PRIMARY KEY,
    plot_id       BIGINT REFERENCES plots (id) ON DELETE CASCADE,
    tambon_code   TEXT,                                 -- area-level alerts (rain, later)
    type          TEXT NOT NULL
                  CHECK (type IN ('greenness', 'harvest_check', 'rain_gap', 'rain_back')),
    severity      TEXT NOT NULL DEFAULT 'red' CHECK (severity IN ('red', 'orange', 'yellow')),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'sent', 'suppressed', 'resolved')),
    obs_date      DATE NOT NULL,                        -- satellite date that triggered it
    z_score       REAL,                                 -- z_nb at trigger
    gap           REAL,                                 -- gap_nb at trigger
    details       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- metrics, scene ids, thresholds used
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    feedback      TEXT CHECK (feedback IN
                  ('borer', 'white_leaf', 'weed', 'water_stress', 'no_problem', 'harvested', 'other')),
    feedback_note TEXT,
    feedback_at   TIMESTAMPTZ,
    CONSTRAINT alerts_target CHECK (plot_id IS NOT NULL OR tambon_code IS NOT NULL)
);
CREATE UNIQUE INDEX alerts_plot_type_date_uq ON alerts (plot_id, type, obs_date)
    WHERE plot_id IS NOT NULL;
CREATE INDEX alerts_created_idx ON alerts (created_at);

-- ---------------------------------------------------------------- settings
-- Thresholds tunable without a deploy (design §10 `setting`). Key 'anomaly' holds a JSON object
-- whose fields override canesat.config.AnomalyConfig defaults.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
