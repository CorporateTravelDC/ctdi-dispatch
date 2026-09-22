-- Migration 0001: bootstrap the schema_migrations tracking table itself.
-- scripts/pg_migrate.py creates this before applying anything else and
-- records every migration file (including this one) in it by filename.
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename     TEXT PRIMARY KEY,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum     TEXT NOT NULL   -- sha256 of the file's bytes at apply time
);
