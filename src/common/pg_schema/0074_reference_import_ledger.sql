-- 0074_reference_import_ledger.sql -- hash-chained private ledger of reference-data imports.
--
-- 2026-10-09 (operator): every LADD (CUI) filter/remove-file import and every
-- weekly FAA registry download leaves a provable record -- WHICH source
-- (basename + SHA-256 + size) produced WHAT table state (row counts), WHEN.
-- Before this, faa_ladd_aircraft carried no source at all, so "is the 10-06
-- list loaded?" could only be inferred from timestamps.
--
-- Holds hashes, basenames and counts only, never an identifier: a SHA-256 of
-- a CUI file is not CUI. entry_hash = sha256(prev_hash | canonical body), the
-- same construction as the backup ledger (scripts/backup/backup_ledger.py),
-- whose "#" stamp records this chain's head at every backup.
--
-- UNIQUE(seq) and UNIQUE(prev_hash) make a concurrent second writer FAIL
-- rather than fork the chain (review 09's audit-chain finding), and the
-- writer also takes a transaction advisory lock on Postgres.

CREATE TABLE IF NOT EXISTS reference_import_ledger (
    seq         INTEGER PRIMARY KEY,
    at          TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    prev_hash   TEXT    NOT NULL UNIQUE,
    entry_hash  TEXT    NOT NULL UNIQUE
);
