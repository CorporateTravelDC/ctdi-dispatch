-- Migration 0059: geometric-reasoning Phase 0 instance-reference table.
--
-- docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md's assign_geometry() needs
-- to know which real flight/train instance a vault note actually
-- references before it can compute real spatial/temporal proximity.
-- No existing mechanism resolves this (checked live 2026-09-20:
-- semantic_concepts.entity_subtype is only topic/org/project/agent/
-- system/person/place; the ontology's entity_domains lexicon is
-- company/system-level only -- "United Airlines", not "AAL1284"), so
-- this is new surface, deliberately NOT folded into semantic_note_concepts
-- (that table is for STABLE, curated ontology concepts; an individual
-- flight/train instance is an ephemeral extracted fact about a note, the
-- same category of thing semantic_note_derivations already holds for
-- derivation/chronological/geometric edges -- this table is that same
-- kind of fact, just one-sided (note -> real-world instance) rather than
-- two-sided (note -> note), so it gets its own small table rather than
-- overloading either existing one).
--
-- One row per (note, instance) the deterministic regex extractor in
-- compile.py::assign_geometry() finds. lat/lon are resolved where
-- possible (flight_events for flights, station_coordinates for trains);
-- NULL when the extractor found a real identifier but no live position
-- data existed for it (e.g. a flight instance outside flight_events'
-- ~30-35 day live retention window) -- an honest "insufficient_data" by
-- omission, not a bug, per this design's established convention.
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS. Wholesale-recomputed
-- every compile run, same convention as semantic_note_derivations.
CREATE TABLE IF NOT EXISTS semantic_note_instance_refs (
    path            TEXT NOT NULL,
    instance_type   TEXT NOT NULL,   -- 'flight' | 'train'
    identifier      TEXT NOT NULL,   -- flight: "AAL1284" (airline+flight_num); train: bare train number
    note_ts         TEXT NOT NULL,   -- the note's own real timestamp (same resolution as assign_chronology's _sort_ts)
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    position_source TEXT,            -- 'flight_events' | 'station_coordinates' | NULL
    PRIMARY KEY (path, instance_type, identifier)
);
CREATE INDEX IF NOT EXISTS idx_snir_path ON semantic_note_instance_refs(path);
CREATE INDEX IF NOT EXISTS idx_snir_type_id ON semantic_note_instance_refs(instance_type, identifier);

-- assign_geometry() resolves a note's referenced flight's live position via
-- flight_events WHERE airline=? AND flight_num=?, gated by proximity to the
-- note's own real timestamp (NOT flight_id -- confirmed live 2026-09-20 that
-- flight_id is a GUFI/UUID, e.g. "1298dd90-9e19-...", not the FAA ACID the
-- original 0005_schema_v4.sql comment claims; airline+flight_num is the real,
-- usable join key, per flight_events_cleanup.py's own documented bug writeup
-- for the same mismatch). No existing index covers (airline, flight_num) --
-- every prior query pattern went through flight_id, destination, or origin.
-- At 960k+ live rows this is otherwise a sequential scan per lookup.
CREATE INDEX IF NOT EXISTS idx_flight_events_airline_num
    ON flight_events(airline, flight_num);
