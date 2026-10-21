-- 0083_flight_ooooi_tfms_eta.sql -- TFMS's own arrival estimate (2026-10-20,
-- operator decision 2026-10-16: watch ETA from SWIM only, TBFM destination-center
-- STA first, then TFMS, then FDPS; common/watch_eta.py).
-- src/common/db.py init_db_v52 is the SQLite twin.
-- Tables: flight_ooooi_times
--
-- tfms_parser._handle_flight_times has always parsed FlightModify/FlightTimes,
-- whose body carries eta[etaType, timeValue], but only the OOOI and original
-- (scheduled) times were stored, so the TFMS tier of the watch ETA had nothing to
-- read. tfms_eta is the timeValue as sent (ISO UTC), tfms_eta_type the etaType
-- (ESTIMATED / ACTUAL / SCHEDULED / ...), tfms_eta_at the epoch it was last written.
-- Nullable, no default: a cheap metadata-only ALTER.

SET LOCAL lock_timeout = '10s';

ALTER TABLE flight_ooooi_times ADD COLUMN IF NOT EXISTS tfms_eta TEXT;
ALTER TABLE flight_ooooi_times ADD COLUMN IF NOT EXISTS tfms_eta_type TEXT;
ALTER TABLE flight_ooooi_times ADD COLUMN IF NOT EXISTS tfms_eta_at DOUBLE PRECISION;
