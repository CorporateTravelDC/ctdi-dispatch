-- 0089_swim_census_and_expiry.sql -- 2026-11-19 (operator: sweep every subscription's message types).
-- swim_message_census: per-hour message-type counts written in one batched flush by ingest/census.py
-- (no per-message writes). SQLite twin: db._SWIM_CENSUS_DDL. Idempotent.
CREATE TABLE IF NOT EXISTS swim_message_census (
    hour_start  DOUBLE PRECISION NOT NULL,
    feed        TEXT NOT NULL,
    msg_type    TEXT NOT NULL,
    handled     INTEGER NOT NULL,
    n           INTEGER NOT NULL DEFAULT 0,
    updated_at  DOUBLE PRECISION,
    PRIMARY KEY (hour_start, feed, msg_type, handled)
);

-- flight_expiry_state: the expiry sweep's backlog cursor (common/flight_expiry.py). No new index on
-- flight_events: migrations run in a transaction, so a non-concurrent build on the 5 GB table would block
-- ingest writes; the sweep uses the existing updated_at index with bounded ranges instead.
CREATE TABLE IF NOT EXISTS flight_expiry_state (
    name   TEXT PRIMARY KEY,
    value  DOUBLE PRECISION
);
