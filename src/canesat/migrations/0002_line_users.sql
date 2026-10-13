-- 0002_line_users.sql — LINE OA webhook milestone (เบิ่งไฮ่ / BerngHai bot)
-- Follow/unfollow state for LINE users and who gave feedback on an alert.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS followed_at   TIMESTAMPTZ,          -- last LINE follow event
    ADD COLUMN IF NOT EXISTS unfollowed_at TIMESTAMPTZ,          -- last unfollow / block
    ADD COLUMN IF NOT EXISTS is_active     BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS feedback_user_id BIGINT REFERENCES users (id);

CREATE INDEX IF NOT EXISTS users_active_idx ON users (is_active) WHERE line_user_id IS NOT NULL;
