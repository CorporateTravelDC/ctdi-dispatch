-- 0068_approval_resolve_keys.sql -- per-request, per-action resolve keys for
-- the approval gate (adversarial duel 2026-10-04, finding X1).
--
-- Before: GET /admin/approval-requests/{id}/resolve?action=allow was Tier 0
-- and "secured purely by the unguessable id". The id is printed to stdout by
-- scripts/sudo-approval-gate.sh (journals, transcripts) and is readable by any
-- holder of the admin token via GET /admin/approval-requests/{id}; and the
-- deny link could be rewritten into an allow link. Now creation mints two
-- random keys, returns them ONCE to the creator (which puts them only into the
-- ntfy push on the approval-gate topic), and stores only their SHA-256. The
-- resolve route requires the key for the requested action. No list/get route
-- ever returns a key or a hash. Rows without hashes (created before this
-- migration) can no longer be resolved -- they expire in <=10 min anyway and
-- silence is never consent.
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS allow_key_hash TEXT;
ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS deny_key_hash TEXT;
