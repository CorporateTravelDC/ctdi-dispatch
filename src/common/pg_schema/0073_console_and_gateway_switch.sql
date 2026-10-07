-- 0073_console_and_gateway_switch.sql (2026-10-05)
-- * agent_gateway_settings: the gateway kill switch (common/agent_gateway.py
--   kill_all / freeze). Frozen = every OAuth + MCP endpoint refuses; thawing
--   needs the operator's SSH-signed approval (kind gateway-thaw).
-- * console_sessions: the operator's phone console (/console,
--   src/web/routes/console.py). A session exists only after a human-signed
--   approval (kind console-login) and only for the browser holding the
--   sign-in nonce; hashes only; 8 h.

CREATE TABLE IF NOT EXISTS agent_gateway_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS console_sessions (
    id_hash     TEXT PRIMARY KEY,
    signer      TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE,     -- one sign-in approval mints at most one session
    created_at  DOUBLE PRECISION NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL,
    last_seen   DOUBLE PRECISION,
    revoked_at  DOUBLE PRECISION
);
