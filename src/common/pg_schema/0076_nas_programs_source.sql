-- 0076_nas_programs_source.sql -- nas_programs.source (2026-10-18, lean-out L1).
-- src/common/db.py init_db_v47 is the SQLite twin (same statements).
-- Tables: nas_programs
--
-- deactivate_absent_programs() (the FAA NAS status REST fetcher,
-- poller/fetchers/nas.py, every 300 s) rewrote every row not in the REST
-- list, the ~59k already-inactive ones and every TFMS-sourced program
-- (MIT/STOP/APREQ/TBM/MINIT/DSP/AFP, key_scheme=2 GS/GDP) included:
-- 39.4 M of the DB's 63.8 M row updates in the 59 h after the 10-15 reboot,
-- and TFMS programs were switched off within 5 minutes of being written.
-- `source` records which writer owns the row's lifecycle ('rest' | 'tfms');
-- the REST job now deactivates only active REST rows, and TFMS rows end at
-- their own end_time (db.expire_ended_tfms_programs).
--
-- Backfill: only rows of the REST program_id shape get 'rest' (db.py
-- NAS_REST_SHAPE_SQL, the same predicate; ~450 rows on 2026-10-18). TFMS rows
-- keep NULL, which every reader classifies by the same shape, so the UPDATE
-- stays small (no rewrite of the ~59k TFMS rows). Idempotent: ADD COLUMN IF
-- NOT EXISTS; the UPDATE only touches source IS NULL rows.
--
-- The heap scan for the backfill reads the whole (bloated, ~305 MB) table;
-- the 60 s global statement_timeout is lifted for this transaction only, and
-- the ALTER waits at most 10 s for its lock (the migration fails and a rerun
-- retries, instead of queueing every writer behind it).

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '300s';

ALTER TABLE nas_programs ADD COLUMN IF NOT EXISTS source TEXT;

UPDATE nas_programs SET source = 'rest'
 WHERE source IS NULL
   AND type IN ('DEP', 'ARR', 'Delay', 'Closure', 'GS', 'GDP')
   AND (program_id = facility || '-Closure' OR program_id LIKE facility || '-' || type || '-%');
