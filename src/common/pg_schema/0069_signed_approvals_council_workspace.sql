-- 0069_signed_approvals_council_workspace.sql -- Wave 2 (2026-10-04):
-- human-signed approvals, council/arena convenes, shared-workspace grants.
-- Logic and the sqlite twin: src/common/governance.py. Docs:
-- docs/AGENT_SEGMENTATION.md "Approvals, council/arena and the shared workspace".
--
-- Operator directives (2026-10-04):
--   "An agent can request it, but it has to be signed off by a human,
--    preferably with a clear signed message, so that any future approval gate
--    to the phone can't be directly bypassed."
--   "A persistent directory that all agents can read, but I can still go ahead
--    and restrict write or execution on a per-agent, per-task, or flat-out
--    basis ... they could not necessarily go ahead and erase each other's
--    findings."
--
-- approval_signers: a HUMAN's approval key, deliberately separate from
-- board_signers. The operator's board key is passphrase-less and every
-- process running as the operator (Claude Code included) can use it, so it
-- can never be what approves. An approval key must be passphrase-protected or
-- live off-box; approver-ctl.sh refuses a key equal to any board key.
CREATE TABLE IF NOT EXISTS approval_signers (
    account        TEXT PRIMARY KEY,
    pubkey         TEXT NOT NULL,
    key_comment    TEXT,
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at  DOUBLE PRECISION,
    deactivated_at DOUBLE PRECISION,
    note           TEXT
);

-- approval_requests gains: what kind of thing is being approved, who asked,
-- and who approved with which signature (audit). The phone tap can now only
-- DENY; allow needs a signature over the exact request (governance.py).
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sudo';
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS requester TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS resolved_by TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS resolution_sig TEXT;

-- council / arena convenes. participants = JSON list of
-- {"account": ..., "required": bool}. status: requested -> active -> closed,
-- or requested -> denied / expired.
CREATE TABLE IF NOT EXISTS council_sessions (
    id                 TEXT PRIMARY KEY,
    mode               TEXT NOT NULL CHECK (mode IN ('council', 'arena')),
    subject            TEXT NOT NULL,
    brief              TEXT NOT NULL DEFAULT '',
    participants       TEXT NOT NULL,
    requester          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'requested',
    created_at         DOUBLE PRECISION NOT NULL,
    deadline           DOUBLE PRECISION NOT NULL,
    activated_at       DOUBLE PRECISION,
    closed_at          DOUBLE PRECISION,
    approval_id        TEXT,
    close_approval_id  TEXT,
    missed_reported_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_council_sessions_status ON council_sessions(status, deadline);

-- shared-workspace write grants: effect grant|deny, account or '*', task or
-- '*', optional expiry. Deny wins; no matching grant = no write. "workspace
-- lock" is the row deny * * (note 'lock').
CREATE TABLE IF NOT EXISTS workspace_grants (
    id          TEXT PRIMARY KEY,
    effect      TEXT NOT NULL CHECK (effect IN ('grant', 'deny')),
    account     TEXT NOT NULL,
    task        TEXT NOT NULL DEFAULT '*',
    until       DOUBLE PRECISION,
    created_at  DOUBLE PRECISION NOT NULL,
    created_by  TEXT,
    note        TEXT
);
-- flat-on by default ("they all have access to the same directory"); claw
-- back with deny rows or `workspace-grants.sh lock`.
INSERT INTO workspace_grants (id, effect, account, task, until, created_at, created_by, note)
VALUES ('wg-default-all', 'grant', '*', '*', NULL, extract(epoch from now()), 'migration-0069', 'default: every team account may contribute')
ON CONFLICT (id) DO NOTHING;
