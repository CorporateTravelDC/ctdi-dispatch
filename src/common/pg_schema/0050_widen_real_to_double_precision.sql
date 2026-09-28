-- Migration 0050: REAL -> DOUBLE PRECISION on every numeric column that
-- mirrors a SQLite REAL column -- Phase 2 pre-copy audit fix.
--
-- Found during Phase 2 (data-copy rehearsal, docs/POSTGRES_MIGRATION.md §5)
-- small-scale copy validation, 2026-09-18: scripts/migrate-sqlite-to-pg.py's
-- post-copy spot-hash verification failed on feed_state and
-- flight_ooooi_times immediately, on the very first rows checked. Root
-- cause is a type-name translation bug, not a data bug: SQLite has exactly
-- one floating-point storage class (always IEEE-754 8-byte double,
-- regardless of the column's declared type name), so every `REAL` column
-- in the SQLite schema legitimately holds double-precision values -- e.g.
-- feed_state.fetched_at was observed as 1789714804.6621852. Phase 1's
-- hand-translation of these CREATE TABLE statements (0002-0048) mapped the
-- SQLite type NAME "REAL" to the Postgres type NAME "REAL" without
-- accounting for the two engines meaning different things by it: Postgres
-- REAL is `float4`, single precision, ~6-7 significant decimal digits. A
-- unix-epoch value like 1789714804.6621852 already needs 10 digits before
-- the decimal point, so float4 rounds it to 1789714800.0 -- silently
-- discarding several seconds of precision on every timestamp, and
-- similarly degrading lat/lon/altitude precision on every position column.
-- This is exactly the corruption class docs/POSTGRES_MIGRATION.md §4 warns
-- about ("PG rejects text in numeric columns") one level up: not rejected,
-- silently narrowed, which is worse -- caught here only because the
-- spot-hash check compares the actual copied value, not just success/
-- failure of the COPY.
--
-- Scope: every column in the live schema typed `real` (queried from
-- information_schema.columns 2026-09-18, matches SQLite's REAL 1:1 --
-- confirmed zero SQLite REAL columns were intentionally float4), 101
-- columns across 45 tables. Every one is either a REAL-epoch timestamp
-- (Appendix A's "REAL epoch" list and more -- Phase 1's translation was
-- schema-wide, not limited to Appendix A's illustrative subset) or a
-- position/measurement float (lat/lon/altitude/distance/speed/etc.) with
-- the identical double-vs-single mismatch. No column in this list was
-- excluded.
--
-- Additive only, does not touch or re-run 0002-0049 (already applied/
-- tracked by their own checksums) -- same idempotent-ALTER shape as every
-- ALTER TABLE in this codebase. Safe/lossless: PG's real->double
-- widening is an implicit, always-succeeding cast (verified against
-- flight_events.updated_at's `DEFAULT (extract(epoch from now()))`
-- expression, itself already double precision, carrying over unchanged);
-- there is no data to migrate here since Phase 2 has not yet done its
-- first real copy under this fix. Re-running this file (ALTER COLUMN TYPE
-- double precision when a column is already double precision) is a
-- no-op, so it is also safe to apply twice if schema_migrations' own
-- checksum-tracked skip is ever bypassed by hand.
-- Tables: acars_messages, amtrak_status, approval_requests, atcscc_opsplan,
--   audit_log, auth_tokens, bandwidth_priority_state, board_enroll_nonces,
--   board_presence, board_refresh_grace, board_tokens, cps_scores,
--   fdps_route_versions, feed_data_usage, feed_state, flight_events,
--   flight_ooooi_times, hot_alerts, international_aviation_feed,
--   local_aircraft, local_airspace_alerts, metar_history, metar_snapshot,
--   nas_programs, notams, nws_alerts, nws_forecast, ops_plan, osint_items,
--   osint_scopes, pull_path_status, runsheet, session_grants,
--   surface_movement_events, surface_tracks, terminal_tracks,
--   tfms_param_delay_stats, tfms_plan_removals, tfrs, train_events,
--   trigger_log, ustrains_departures, vessel_events, watchlist_sessions,
--   webhook_events, wpc_discussions (column addition only -- no rows).
-- Idempotent: ALTER COLUMN TYPE is a no-op once already double precision.

ALTER TABLE acars_messages ALTER COLUMN freq_mhz TYPE DOUBLE PRECISION;
ALTER TABLE amtrak_status ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE approval_requests ALTER COLUMN created_at TYPE DOUBLE PRECISION;
ALTER TABLE approval_requests ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE approval_requests ALTER COLUMN resolved_at TYPE DOUBLE PRECISION;
ALTER TABLE atcscc_opsplan ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE audit_log ALTER COLUMN event_time TYPE DOUBLE PRECISION;
ALTER TABLE auth_tokens ALTER COLUMN created_at TYPE DOUBLE PRECISION;
ALTER TABLE auth_tokens ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE auth_tokens ALTER COLUMN revoked_at TYPE DOUBLE PRECISION;
ALTER TABLE bandwidth_priority_state ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE bandwidth_priority_state ALTER COLUMN set_at TYPE DOUBLE PRECISION;
ALTER TABLE board_enroll_nonces ALTER COLUMN consumed_at TYPE DOUBLE PRECISION;
ALTER TABLE board_enroll_nonces ALTER COLUMN created_at TYPE DOUBLE PRECISION;
ALTER TABLE board_enroll_nonces ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE board_presence ALTER COLUMN issued_at TYPE DOUBLE PRECISION;
ALTER TABLE board_presence ALTER COLUMN recorded_at TYPE DOUBLE PRECISION;
ALTER TABLE board_presence ALTER COLUMN valid_until TYPE DOUBLE PRECISION;
ALTER TABLE board_refresh_grace ALTER COLUMN grace_expires_at TYPE DOUBLE PRECISION;
ALTER TABLE board_refresh_grace ALTER COLUMN new_expires_at TYPE DOUBLE PRECISION;
ALTER TABLE board_tokens ALTER COLUMN created_at TYPE DOUBLE PRECISION;
ALTER TABLE board_tokens ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE cps_scores ALTER COLUMN computed_at TYPE DOUBLE PRECISION;
ALTER TABLE fdps_route_versions ALTER COLUMN eta_delta_min TYPE DOUBLE PRECISION;
ALTER TABLE feed_data_usage ALTER COLUMN updated_at TYPE DOUBLE PRECISION;
ALTER TABLE feed_data_usage ALTER COLUMN window_start TYPE DOUBLE PRECISION;
ALTER TABLE feed_state ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE flight_events ALTER COLUMN arrival_time TYPE DOUBLE PRECISION;
ALTER TABLE flight_events ALTER COLUMN departure_time TYPE DOUBLE PRECISION;
ALTER TABLE flight_events ALTER COLUMN position_lat TYPE DOUBLE PRECISION;
ALTER TABLE flight_events ALTER COLUMN position_lon TYPE DOUBLE PRECISION;
ALTER TABLE flight_events ALTER COLUMN updated_at TYPE DOUBLE PRECISION;
ALTER TABLE flight_ooooi_times ALTER COLUMN updated_at TYPE DOUBLE PRECISION;
ALTER TABLE hot_alerts ALTER COLUMN computed_at TYPE DOUBLE PRECISION;
ALTER TABLE international_aviation_feed ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE local_aircraft ALTER COLUMN distance_nm TYPE DOUBLE PRECISION;
ALTER TABLE local_aircraft ALTER COLUMN latitude TYPE DOUBLE PRECISION;
ALTER TABLE local_aircraft ALTER COLUMN longitude TYPE DOUBLE PRECISION;
ALTER TABLE local_aircraft ALTER COLUMN rssi TYPE DOUBLE PRECISION;
ALTER TABLE local_aircraft ALTER COLUMN track_deg TYPE DOUBLE PRECISION;
ALTER TABLE local_airspace_alerts ALTER COLUMN distance_nm TYPE DOUBLE PRECISION;
ALTER TABLE metar_history ALTER COLUMN obs_time TYPE DOUBLE PRECISION;
ALTER TABLE metar_history ALTER COLUMN recorded_at TYPE DOUBLE PRECISION;
ALTER TABLE metar_history ALTER COLUMN visibility_sm TYPE DOUBLE PRECISION;
ALTER TABLE metar_snapshot ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE metar_snapshot ALTER COLUMN obs_time TYPE DOUBLE PRECISION;
ALTER TABLE metar_snapshot ALTER COLUMN visibility_sm TYPE DOUBLE PRECISION;
ALTER TABLE nas_programs ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE notams ALTER COLUMN effective_end TYPE DOUBLE PRECISION;
ALTER TABLE notams ALTER COLUMN effective_start TYPE DOUBLE PRECISION;
ALTER TABLE notams ALTER COLUMN inserted_at TYPE DOUBLE PRECISION;
ALTER TABLE notams ALTER COLUMN last_seen_at TYPE DOUBLE PRECISION;
ALTER TABLE nws_alerts ALTER COLUMN effective TYPE DOUBLE PRECISION;
ALTER TABLE nws_alerts ALTER COLUMN expires TYPE DOUBLE PRECISION;
ALTER TABLE nws_alerts ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE nws_forecast ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE ops_plan ALTER COLUMN loaded_at TYPE DOUBLE PRECISION;
ALTER TABLE osint_items ALTER COLUMN ingested_at TYPE DOUBLE PRECISION;
ALTER TABLE osint_items ALTER COLUMN published_at TYPE DOUBLE PRECISION;
ALTER TABLE osint_items ALTER COLUMN pushed_at TYPE DOUBLE PRECISION;
ALTER TABLE osint_scopes ALTER COLUMN created_at TYPE DOUBLE PRECISION;
ALTER TABLE pull_path_status ALTER COLUMN checked_at TYPE DOUBLE PRECISION;
ALTER TABLE runsheet ALTER COLUMN loaded_at TYPE DOUBLE PRECISION;
ALTER TABLE session_grants ALTER COLUMN expires_at TYPE DOUBLE PRECISION;
ALTER TABLE session_grants ALTER COLUMN granted_at TYPE DOUBLE PRECISION;
ALTER TABLE session_grants ALTER COLUMN revoked_at TYPE DOUBLE PRECISION;
ALTER TABLE surface_movement_events ALTER COLUMN altitude_ft TYPE DOUBLE PRECISION;
ALTER TABLE surface_movement_events ALTER COLUMN latitude TYPE DOUBLE PRECISION;
ALTER TABLE surface_movement_events ALTER COLUMN longitude TYPE DOUBLE PRECISION;
ALTER TABLE surface_tracks ALTER COLUMN altitude_ft TYPE DOUBLE PRECISION;
ALTER TABLE surface_tracks ALTER COLUMN heading_deg TYPE DOUBLE PRECISION;
ALTER TABLE surface_tracks ALTER COLUMN latitude TYPE DOUBLE PRECISION;
ALTER TABLE surface_tracks ALTER COLUMN longitude TYPE DOUBLE PRECISION;
ALTER TABLE terminal_tracks ALTER COLUMN altitude_ft TYPE DOUBLE PRECISION;
ALTER TABLE terminal_tracks ALTER COLUMN latitude TYPE DOUBLE PRECISION;
ALTER TABLE terminal_tracks ALTER COLUMN longitude TYPE DOUBLE PRECISION;
ALTER TABLE tfms_param_delay_stats ALTER COLUMN avg_delay_after_min TYPE DOUBLE PRECISION;
ALTER TABLE tfms_param_delay_stats ALTER COLUMN avg_delay_before_min TYPE DOUBLE PRECISION;
ALTER TABLE tfms_plan_removals ALTER COLUMN filed_lead_h TYPE DOUBLE PRECISION;
ALTER TABLE tfrs ALTER COLUMN effective_end TYPE DOUBLE PRECISION;
ALTER TABLE tfrs ALTER COLUMN effective_start TYPE DOUBLE PRECISION;
ALTER TABLE tfrs ALTER COLUMN enriched_at TYPE DOUBLE PRECISION;
ALTER TABLE tfrs ALTER COLUMN inserted_at TYPE DOUBLE PRECISION;
ALTER TABLE train_events ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE trigger_log ALTER COLUMN queued_at TYPE DOUBLE PRECISION;
ALTER TABLE trigger_log ALTER COLUMN resolved_at TYPE DOUBLE PRECISION;
ALTER TABLE ustrains_departures ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN beam_m TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN cog TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN draught_m TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN hdg TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN lat TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN loa_m TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN lon TYPE DOUBLE PRECISION;
ALTER TABLE vessel_events ALTER COLUMN sog TYPE DOUBLE PRECISION;
ALTER TABLE watchlist_sessions ALTER COLUMN started_at TYPE DOUBLE PRECISION;
ALTER TABLE watchlist_sessions ALTER COLUMN terminated_at TYPE DOUBLE PRECISION;
ALTER TABLE webhook_events ALTER COLUMN received_at TYPE DOUBLE PRECISION;
ALTER TABLE wpc_discussions ALTER COLUMN fetched_at TYPE DOUBLE PRECISION;
ALTER TABLE wpc_discussions ALTER COLUMN issued_at TYPE DOUBLE PRECISION;
