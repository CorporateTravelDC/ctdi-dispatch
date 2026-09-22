-- 0063_ladd_removals.sql -- persistent LADD removal list.
--
-- The FAA distributes LADD as two filter files (FAA Source, Industry) plus a
-- separate "remove" file. scripts/import-ladd-filter.py does a FULL REPLACE of
-- faa_ladd_aircraft from the filter files, so a one-time DELETE of the remove
-- entries is undone by the very next weekly import.
--
-- That is not hypothetical. Verified 2026-09-22: of the 29 entries in
-- LADD_remove_CUI_SP_PRVCY_20260915.txt, 2 were STILL present in the
-- 2026-09-22 Industry filter file. The FAA's own remove list contradicts its
-- own filter list for those entries, so the removal has to outlive the import
-- rather than be folded into it.
--
-- Removals are therefore recorded here permanently and re-applied after every
-- import. An entry stays suppressed until explicitly un-removed, regardless of
-- how many times it reappears upstream.
--
-- This table holds identifiers only -- the same shape as faa_ladd_aircraft's
-- n_number column (US N-numbers, foreign registration marks, and flight-ID/
-- callsign strings all appear). It carries no CUI content beyond the
-- identifiers themselves, which is the same exposure faa_ladd_aircraft already
-- has; see docs/LADD_CUI_HANDLING.md.

CREATE TABLE IF NOT EXISTS faa_ladd_removals (
    n_number    TEXT PRIMARY KEY,
    removed_at  DOUBLE PRECISION NOT NULL,
    -- Basename only, never a full path: the path leaks the operator's home
    -- directory layout into a table that gets dumped for support.
    source_file TEXT,
    -- Free-text why, for the case where an operator removes something by hand
    -- rather than from an FAA remove file.
    note        TEXT
);
