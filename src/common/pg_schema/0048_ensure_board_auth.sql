-- Migration 0048: board_enroll_nonces, board_tokens, board_presence,
-- board_refresh_grace.
-- Hand-translated -- same lazy inline-DDL pattern as 0047 (see that
-- file's header). All four tables are created by
-- src/common/db.py's _ensure_board_auth(c) and board_refresh_grace's own
-- inline CREATE TABLE (same function body), never via a SCHEMA_V*
-- executescript block.
-- Tables: board_enroll_nonces, board_tokens, board_presence, board_refresh_grace
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS board_enroll_nonces (
    nonce_hash        TEXT PRIMARY KEY,
    created_at        REAL,
    expires_at        REAL,
    consumed_at       REAL,
    minted_token_hash TEXT,
    label             TEXT
);

CREATE TABLE IF NOT EXISTS board_tokens (
    token_hash  TEXT PRIMARY KEY,
    created_at  REAL,
    expires_at  REAL,
    scope       TEXT,
    label       TEXT,
    via_nonce   TEXT
);

CREATE TABLE IF NOT EXISTS board_presence (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    attestation_text  TEXT,
    issued_at         REAL,
    valid_until       REAL,
    key_fingerprint   TEXT,
    recorded_at       REAL
);

CREATE TABLE IF NOT EXISTS board_refresh_grace (
    old_token_hash    TEXT PRIMARY KEY,
    new_token         TEXT NOT NULL,
    new_expires_at    REAL NOT NULL,
    grace_expires_at  REAL NOT NULL
);
