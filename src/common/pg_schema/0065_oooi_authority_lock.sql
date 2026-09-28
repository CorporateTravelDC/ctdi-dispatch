-- 0065_oooi_authority_lock.sql -- source-authority lock for OOOI phase.
--
-- Operator directive 2026-09-23, written after two live false-positive
-- landings on the same evening:
--
--   UA1240  -- swept "landed (oooi_phase=in, ACARS/ADS-B confirmed)" while the
--             aircraft was verifiably at FL370 over southern Ohio, 504 kts,
--             ADS-B age 0.0s. SWIM said flight_status=ACTIVE with ON at
--             01:06Z and IN at 01:11Z, both still in the FUTURE. The one
--             source with real authority was disagreeing and was never asked.
--   UA2408  -- same sweep reason, 3.5 HOURS before scheduled arrival.
--
-- Root cause was not a bad reading. It was that NOTHING recorded which source
-- was entitled to assert the phase. watchlist.py's sweep fires on
-- `phase == "in"` alone, and its reason string "ACARS/ADS-B confirmed" is a
-- HARDCODED literal -- it claims corroboration regardless of what actually
-- wrote the phase. Four writers (_check_flight_airplanes_live,
-- _check_flight_schedule_inference, _check_flight_fdps_cache,
-- _check_flight_fids) all write oooi_phase, the last two unconditionally
-- every tick, last-write-wins.
--
-- THE RULE THIS ENCODES (operator, verbatim intent):
--   * SWIM -- FDPS and SWIM-borne FIDS -- is the ultimate validator.
--   * Local receivers may NEVER authoritatively claim off/on/in unless BOTH:
--       (a) SWIM is demonstrably down -- measured from feed_state, not assumed
--       (b) that same local receiver already held a prior active track on this
--           entry. A local receiver that has never seen the aircraft cannot
--           become its authority just because SWIM went quiet.
--   * Whichever source established the initial fix lock is recorded, and
--     nothing BELOW that tier may ever authorize off/on/in while that source
--     is still alive.
--
-- Deliberately additive: no existing column changes meaning, and every column
-- is nullable so entries written before this migration keep working and simply
-- carry no lock until their next authoritative write.

-- Source that established the authoritative lock for this entry (e.g. 'fdps',
-- 'acars', 'adsb', 'local_adsb'). Distinct from oooi_source, which records
-- whoever wrote the CURRENT phase -- this records who is entitled to.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_lock_source TEXT;

-- That source's tier AT LOCK TIME, denormalised on purpose. If the tier table
-- is ever re-ranked, existing locks keep the authority they were granted under
-- rather than silently gaining or losing it on deploy.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_lock_tier INTEGER;

ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_lock_at TEXT;

-- Precondition (b) above. Set ONLY when a local receiver observes a real track
-- for this entry. Gates whether a local source may ever be promoted during a
-- SWIM outage. Without this, "SWIM is down" alone would be enough to let a
-- receiver that has never seen the aircraft declare it landed -- which is the
-- UA2408 failure with extra steps.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_local_track_at TEXT;

ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_local_track_source TEXT;

-- Audit: why the last authoritative write was accepted or rejected. Exists so
-- a false positive is diagnosable after the fact instead of requiring the
-- aircraft to still be in the air to catch it, which is the only reason
-- tonight's two were caught at all.
ALTER TABLE watchlist_entries
    ADD COLUMN IF NOT EXISTS oooi_authority_note TEXT;

-- Not indexed: these are read per-entry during a sweep that already loads the
-- row, and written on the same path. An index would cost more than it returns.
-- Same reasoning as 0061_site_origin.sql and 0064_train_phase_eta.sql.
