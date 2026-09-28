-- 0051_widen_feed_data_usage_bytes_in.sql
-- Same class of bug as 0050 (SQLite INTEGER is dynamically-sized/64-bit
-- transparent, Postgres integer is a fixed 32-bit type). Found live during
-- the real production copy, 2026-09-18: feed_data_usage.bytes_in holds
-- cumulative raw-bytes-received counters that have grown well past the
-- 32-bit signed max (2,147,483,647) -- observed value 16,445,361,964.
-- records_in/records_accepted are left INTEGER: verified against live
-- data, nowhere near the boundary (message/record counts, not raw bytes),
-- not touched speculatively.
ALTER TABLE feed_data_usage ALTER COLUMN bytes_in TYPE BIGINT;
