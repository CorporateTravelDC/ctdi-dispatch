-- 0066_board_signers.sql -- identity-based board signing registry.
--
-- Operator directive 2026-10-04: keep X-Board-Key (master BOARD_KEY + minted
-- scoped tokens) for platform/API clients, and add a second path with NO
-- shared secret: the poster signs the request with its own SSH ed25519 key
-- (one key per account, comment <account>@corporatetraveldc-dispatch, per
-- docs/AGENT_SEGMENTATION.md); the server verifies against this registry and
-- attributes the post to the account. Either path, or both for privileged
-- routes (src/web/main.py BOARD_AUTH_POLICY). See docs/BOARD_SIGNING.md.
--
-- active=false is revocation: scripts/board-signer-ctl.sh deactivate, which
-- the team-liveness dead-man switch calls automatically when an account goes
-- inert, so a stale account cannot sign even if its key leaks.
CREATE TABLE IF NOT EXISTS board_signers (
    account        TEXT PRIMARY KEY,
    pubkey         TEXT NOT NULL,            -- 'ssh-ed25519 AAAA... comment'
    key_comment    TEXT,
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at  DOUBLE PRECISION,
    deactivated_at DOUBLE PRECISION,
    note           TEXT
);
