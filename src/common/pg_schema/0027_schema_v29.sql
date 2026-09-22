-- Migration 0027: SCHEMA_V29 (src/common/db.py)
-- Auto-translated from the SQLite schema block of the same name
-- (docs/POSTGRES_MIGRATION.md Appendix A). Tables: audit_log
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS egress_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS egress_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS egress_last_error TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_log_egress_status ON audit_log(egress_status);
