-- 0081_tfr_push_reason_acars_flags.sql (2026-10-18, operator: TFR widening + ACARS flags).
-- src/common/db.py init_db_v50 is the SQLite twin (same columns).
-- Tables: tfrs, acars_messages
--
-- tfrs.vip_push_reason: why poller/fetchers/tfr.py classed a TFR VIP-class for
-- the pusher -- vip_type | vip_text | security_presidential | security_zdc |
-- security_legal_basis -- or NULL (no VIP-class push). is_vip keeps its meaning
-- (FAA type VIP or a VIP text pattern) for the briefs and route impact.
-- acars_messages.acars_flags: comma-separated message-type flags written by the
-- ingest ACARS writer (acars_watcher/acars_flags.py); NULL on older rows.
--
-- Nullable columns with no default: a catalog-only change on Postgres (no table
-- rewrite, no backfill). lock_timeout 10 s so a long reader makes the migration
-- fail fast (a rerun retries) instead of queueing writers behind the ALTER.

SET LOCAL lock_timeout = '10s';

ALTER TABLE tfrs ADD COLUMN IF NOT EXISTS vip_push_reason TEXT;
ALTER TABLE acars_messages ADD COLUMN IF NOT EXISTS acars_flags TEXT;
