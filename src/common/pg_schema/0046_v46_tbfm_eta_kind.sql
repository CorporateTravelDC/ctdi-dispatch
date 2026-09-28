-- Migration 0046: v46 (src/common/db.py init_db_v46, no SCHEMA_V46
-- constant -- inline PRAGMA-guarded ALTER, hand-translated).
-- Tables: tbfm_sequences
-- Idempotent: ADD COLUMN IF NOT EXISTS.

ALTER TABLE tbfm_sequences ADD COLUMN IF NOT EXISTS eta_kind TEXT;
