-- 0072_es_invites.sql -- Executive Standard reader invites + promo codes
-- (2026-10-05). Logic + sqlite twin: src/common/es_invites.py. Public host:
-- invite.executivestandard.example.com; gate: members host verifier
-- (executivestandard-website/verifier/verify.py). Only hashes of invite codes,
-- promo codes, hand-offs and sessions are stored. exec_standard_sources is the
-- site repo's db/001 table (same database); it is recreated here only if absent.

CREATE TABLE IF NOT EXISTS exec_standard_sources (
    source  TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT true,
    cap     INTEGER,
    label   TEXT,
    notes   TEXT
);
INSERT INTO exec_standard_sources (source, enabled, cap, label, notes) VALUES
  ('direct', true, NULL, 'Direct invite', 'Personal invitations issued by the publisher'),
  ('promo',  true, NULL, 'Promo code',    'Anonymous short-lived promo redemptions')
ON CONFLICT (source) DO NOTHING;

-- one reader entitlement: a personal invite, or one promo redemption
CREATE TABLE IF NOT EXISTS es_grants (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('invite', 'promo')),
    email         TEXT,
    label         TEXT,
    source        TEXT NOT NULL,
    campaign      TEXT,
    promo_id      TEXT,
    code_hash     TEXT UNIQUE,                       -- invite link (NULL for promo grants)
    max_devices   INTEGER NOT NULL DEFAULT 3,
    created_at    DOUBLE PRECISION NOT NULL,
    created_by    TEXT,
    expires_at    DOUBLE PRECISION,                  -- NULL = permanent until revoked
    claimed_at    DOUBLE PRECISION,
    claims        INTEGER NOT NULL DEFAULT 0,
    last_seen     DOUBLE PRECISION,
    revoked_at    DOUBLE PRECISION,
    revoked_by    TEXT,
    revoke_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_es_grants_email ON es_grants(email);
CREATE INDEX IF NOT EXISTS idx_es_grants_promo ON es_grants(promo_id);

CREATE TABLE IF NOT EXISTS es_promos (
    id            TEXT PRIMARY KEY,
    code_hash     TEXT NOT NULL UNIQUE,
    label         TEXT,
    source        TEXT NOT NULL,
    campaign      TEXT,
    created_at    DOUBLE PRECISION NOT NULL,
    created_by    TEXT,
    expires_at    DOUBLE PRECISION NOT NULL,         -- the code stops working
    access_days   INTEGER NOT NULL,                  -- what one redemption grants
    max_uses      INTEGER NOT NULL,
    uses          INTEGER NOT NULL DEFAULT 0,
    revoked_at    DOUBLE PRECISION,
    revoked_by    TEXT,
    revoke_reason TEXT
);

-- invite host -> members host hand-off: single use, 2 minutes
CREATE TABLE IF NOT EXISTS es_exchanges (
    code_hash  TEXT PRIMARY KEY,
    grant_id   TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    used_at    DOUBLE PRECISION
);

-- one signed-in device
CREATE TABLE IF NOT EXISTS es_sessions (
    id_hash    TEXT PRIMARY KEY,
    grant_id   TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    last_seen  DOUBLE PRECISION NOT NULL,
    revoked_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_es_sessions_grant ON es_sessions(grant_id, revoked_at);

-- publisher switches (frozen = no new sign-ins; signed-in readers keep reading)
CREATE TABLE IF NOT EXISTS es_invite_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    updated_by TEXT
);

-- publisher activity log (shown on the console)
CREATE TABLE IF NOT EXISTS es_invite_events (
    id     BIGSERIAL PRIMARY KEY,
    ts     DOUBLE PRECISION NOT NULL,
    actor  TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_es_invite_events_ts ON es_invite_events(ts);
