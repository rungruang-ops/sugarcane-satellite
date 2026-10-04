-- 0003_liff_consent.sql — LIFF plot registration + PDPA consent (design §6, §10)

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- users.phone_enc = pgp_sym_encrypt(phone, app key)

CREATE TABLE IF NOT EXISTS consents (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,            -- service | leader_view | research | share:<org>
    granted     BOOLEAN NOT NULL,
    version     TEXT NOT NULL,            -- consent text version shown
    method      TEXT NOT NULL,            -- liff | assisted_pending | sms_otp | line
    assisted_by BIGINT REFERENCES users (id),
    plot_id     BIGINT REFERENCES plots (id) ON DELETE SET NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS consents_user_idx ON consents (user_id, purpose, at DESC);

ALTER TABLE plots
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'cli';   -- cli | liff
