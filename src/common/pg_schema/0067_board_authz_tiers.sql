-- 0067_board_authz_tiers.sql -- roles/kinds for board signers, token
-- revocation stamps. Operator directive 2026-10-04 (authorisation model):
--
--   "It should be an AND, not an OR from a liveness stance." An account is
--   alive only while login AND registered key AND (for high impact) a
--   time-based token all hold; any one revocation kills the chain.
--   "Anything high sensitivity requires a time-based key ... agentic key
--   and/or agentic plus operator key." -> BOARD_AUTH_POLICY tiers normal /
--   high / cosign in src/web/main.py.
--   "A two-man or three-man human override of an admin ... a quorum of at
--   least two." -> kill orders (scripts/kill-order.sh, executed by
--   scripts/team-liveness.sh) need the signer's role to apply the quorum.
--
-- role : admin   -- may kill members/services/agents alone; can only be
--                   killed by a quorum (QUORUM_FOR_ADMIN, default 2)
--        member  -- default; humans and agents
--        service -- service accounts (same kill rules as member)
-- kind : human | agent | service -- what the account IS (role is what it MAY do)
--
-- board_tokens.revoked_at: board_revoke_token() used to only set
-- expires_at := now, which is indistinguishable from natural expiry; the
-- liveness switch treats "a token of this account was REVOKED in the last
-- 24h" as a kill signal, so revocation must be stamped explicitly.
ALTER TABLE board_signers ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'member';
ALTER TABLE board_signers ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE board_signers DROP CONSTRAINT IF EXISTS board_signers_role_check;
ALTER TABLE board_signers ADD CONSTRAINT board_signers_role_check CHECK (role IN ('admin', 'member', 'service'));
ALTER TABLE board_signers DROP CONSTRAINT IF EXISTS board_signers_kind_check;
ALTER TABLE board_signers ADD CONSTRAINT board_signers_kind_check CHECK (kind IN ('human', 'agent', 'service'));
ALTER TABLE board_tokens ADD COLUMN IF NOT EXISTS revoked_at DOUBLE PRECISION;
