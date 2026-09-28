-- 0064_train_phase_eta.sql -- train phase state + live ETA on watchlist_entries.
--
-- Closes parity breaks 1 and 2 from docs/TRAIN_PARITY_DESIGN_2026-09-23.md,
-- written under the standing rule that aviation is the parity benchmark
-- (second brain 20260923T181652Z.md).
--
-- BREAK 1 -- PHASE STATE. Flights carry OOOI (oooi_phase, oooi_phase_updated_at,
-- oooi_source), monotonic and authority-gated. Trains carried no phase concept
-- at all: measured 2026-09-23, zero of 304 train entries had any phase set, and
-- every phase-ish column on the table (oooi_*, last_fdps_status,
-- last_tbfm_status, uas_*) is aviation- or UAS-specific.
--
-- BREAK 2 -- LIVE ETA. scheduled_arrival is written once at add time and never
-- refreshed; the live estimate was read from the feed, used for one comparison,
-- and discarded. Flights get continuous refresh via the FIDS/FDPS/TBFM columns.
--
-- scheduled_arrival is deliberately NOT repurposed. The schedule and the
-- estimate are different facts and conflating them destroys the ability to say
-- "40 minutes late" -- which is the entire point of tracking either.
--
-- Phase vocabulary (4 states, mirroring OUT/OFF/ON/IN):
--     scheduled   <- trainState == Predeparture              (pre-OUT)
--     departed    <- origin station status == Departed        (OUT/OFF)
--     approaching <- destination is first station not Departed (ON)
--     arrived     <- destination status == Station,
--                    or trainState == Completed               (IN)
--
-- Deliberately NOT indexed: the train sweep loads active entries by
-- entry_type and filters in Python; there is no query that selects on phase
-- or eta, and an index on a column written every sweep costs more than it
-- returns. Same reasoning as 0061_site_origin.sql.

ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS train_phase TEXT;

ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS train_phase_updated_at TEXT;

-- Which provider adapter asserted the phase ('amtrak', 'marc', 'vre', ...).
--
-- CORRECTION 2026-09-24: an earlier version of this comment claimed this
-- column "mirrors oooi_source so a later, lower-authority provider cannot
-- silently overwrite a higher-authority one". That is NOT what shipped and
-- was never true. db.update_train_phase() writes this column but never reads
-- or compares it; its only guard is _TRAIN_PHASE_ORDER, i.e. monotonic
-- forward movement. A lower-authority provider writing a forward phase is
-- accepted unconditionally. The function's own docstring says "MONOTONIC"
-- and is accurate -- this comment was the overclaim.
--
-- It is harmless TODAY because exactly one provider writes trains (amtrak,
-- via _check_train_amtraker). It stops being harmless the moment a second
-- provider lands: MARC and VRE train numbers COLLIDE with Amtrak's, which is
-- the precise condition under which one provider silently overwriting
-- another does real damage. Enforce authority here BEFORE the MARC/VRE
-- fetchers ship -- see docs/TRAIN_PARITY_DESIGN_2026-09-23.md break 5.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS train_phase_source TEXT;

-- Live arrival estimate at the DESTINATION station (amtraker stations[].arr,
-- or the provider-normalised equivalent). ISO-8601 text, matching every other
-- timestamp column on this table.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS live_eta TEXT;

ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS live_eta_updated_at TEXT;

-- arr - schArr at the destination, in minutes. Signed: negative is early.
-- Distinct from departure_delay_min, which is a flight concept keyed on OUT.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS arrival_delay_min INTEGER;
