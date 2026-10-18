-- 0078_drop_duplicate_flight_events_index.sql (2026-10-18, lean-out L1).
-- Tables: flight_events
--
-- idx_flight_events_callsign (0022, SCHEMA_V22) and idx_flight_events_airline_num
-- (0059) are the same index: both btree (airline, flight_num). Verified live
-- 2026-10-18 03:40Z with pg_get_indexdef: identical definitions, both valid;
-- idx_flight_events_callsign 0 scans since the 10-15 reboot (18 MB),
-- idx_flight_events_airline_num 83k scans. Every non-HOT flight_events upsert
-- (~6.45 M in 59 h) wrote an entry into both. The planner already uses
-- airline_num for every (airline, flight_num) lookup, so no plan changes.
--
-- DROP INDEX takes an ACCESS EXCLUSIVE lock on flight_events for the
-- transaction (a catalog change, no data scan). lock_timeout 10 s: if a long
-- reader holds the table, the migration fails fast and a rerun retries,
-- rather than every ingest writer queueing behind the DROP.
-- SQLite keeps creating the index from SCHEMA_V22 (test DBs only; harmless).

SET LOCAL lock_timeout = '10s';

DROP INDEX IF EXISTS idx_flight_events_callsign;
