-- 0071_agent_gateway.sql -- OAuth + remote-MCP gateway for CLOUD agents
-- (Cowork via Claude custom connectors, ChatGPT via developer-mode MCP / GPT
-- Actions). Logic + sqlite twin: src/common/agent_gateway.py. Docs:
-- docs/AGENT_SEGMENTATION.md "Agent gateway (cloud agents)".
--
-- Operator directives (2026-10-05):
--   * no weekly nonce: the vendor keeps the link fresh with OAuth refresh;
--   * linking needs the operator's SSH-signed approval (approve.sh), never a click;
--   * OUR side is the authority: a vendor-side lapse (subscription between
--     paychecks, a silently disabled feature) makes the SESSION dormant, never
--     the ACCOUNT inert; an operator-signed hold keeps our side of the link
--     (client registration + refresh grant) from expiring until the vendor
--     comes back, and the session resumes on the vendor's next refresh;
--   * any human revoke on our side (signer deactivate, revoke-tokens, kill
--     order, liveness inert) ends the chain immediately, hold or not.

-- one MCP endpoint per agent identity: https://<gateway>/mcp/<slug>
CREATE TABLE IF NOT EXISTS agent_connectors (
    slug        TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    vendor      TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL,
    disabled_at DOUBLE PRECISION
);

-- OAuth clients: Dynamic Client Registration (ChatGPT) or a Client ID
-- Metadata Document fetched from the https client_id (Claude's published identity)
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT,
    redirect_uris TEXT NOT NULL,
    source        TEXT NOT NULL,
    created_at    DOUBLE PRECISION NOT NULL
);

-- an authorize request waiting on the operator's signed approval
CREATE TABLE IF NOT EXISTS oauth_pending (
    req_id         TEXT PRIMARY KEY,
    slug           TEXT NOT NULL,
    client_id      TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    state          TEXT,
    scope          TEXT,
    approval_id    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    code_hash      TEXT,
    created_at     DOUBLE PRECISION NOT NULL,
    expires_at     DOUBLE PRECISION NOT NULL
);

-- a linked session: active | dormant | held | revoked
CREATE TABLE IF NOT EXISTS agent_connections (
    id               TEXT PRIMARY KEY,
    slug             TEXT NOT NULL,
    account          TEXT NOT NULL,
    client_id        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       DOUBLE PRECISION NOT NULL,
    last_renewal_at  DOUBLE PRECISION,
    last_call_at     DOUBLE PRECISION,
    hold_until       DOUBLE PRECISION,
    hold_approval_id TEXT,
    revoked_at       DOUBLE PRECISION,
    revoke_reason    TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_connections_account ON agent_connections(account, status);

-- access (1 h) and refresh (rotating; 30 d idle unless held) tokens -- hashes only
CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash    TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    connection_id TEXT NOT NULL,
    created_at    DOUBLE PRECISION NOT NULL,
    expires_at    DOUBLE PRECISION NOT NULL,
    rotated_at    DOUBLE PRECISION,
    revoked_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_connection ON oauth_tokens(connection_id, kind);
