-- 0082_codeshare_atc_callsign_lookaside.sql -- flight-number lookaside (2026-10-20,
-- bellwether batch; operator decisions 2026-10-19/20).
-- src/common/db.py init_db_v51 is the SQLite twin (same columns, same seed rows).
-- Tables: codeshare_map, watchlist_entries
--
-- Watch identifiers are PUBLIC flight numbers (EK231, LH414). Some carriers file
-- an alphanumeric ATC callsign instead (EK231 flies as UAE3MX, LH414 as DLH3Y), so a
-- matcher that only knew the public number never saw those flights in SWIM.
-- codeshare_map now carries both kinds of link:
--   relation      'codeshare'    marketing number -> operating carrier/number (as before)
--                 'atc_callsign' public number -> the ATC callsign it is filed under
--                                (marketing_* = public number in ICAO-carrier form,
--                                 operating_* = the callsign split 3 + rest)
--   season        IATA season the link was seen in (S26, W26, 'W26-pending')
--   retired_at    when the link stopped being current (relinked or retired); rows are
--                 never deleted -- the W26 season change (2026-10-25) recycles
--                 alphanumerics, and the history is evidence
--   superseded_by id of the row that replaced it (relink), NULL when just retired
-- Every reader filters retired_at IS NULL unless it asks for history
-- (db.get_codeshare_mapping_by_marketing / get_current_atc_callsign).
--
-- watchlist_entries.alert_topic: the permanent file's per-entry push topic
-- ("bellwether-alerts"), copied by shared.watchlist.WatchlistFileWatcher so containers
-- without the watchlists volume (ingest, pusher) route the same way
-- (shared.watch_topic). NULL = the entry type's own topic.
--
-- Seed: the two route-unique links read off the IAD FIDS 2026-10-19 (one FIDS row and
-- one SWIM-verified leg on the route that day), source 'fids_route_unique'. Inserted
-- only when no current link for that public number and route exists (idempotent).
--
-- relation is NOT NULL with a constant default and the CHECK scans a few hundred rows;
-- lock_timeout 10 s so a long reader makes the migration fail fast (a rerun retries).

SET LOCAL lock_timeout = '10s';

ALTER TABLE codeshare_map ADD COLUMN IF NOT EXISTS relation TEXT NOT NULL DEFAULT 'codeshare'
    CHECK (relation IN ('codeshare', 'atc_callsign'));
ALTER TABLE codeshare_map ADD COLUMN IF NOT EXISTS season TEXT;
ALTER TABLE codeshare_map ADD COLUMN IF NOT EXISTS retired_at TEXT;
ALTER TABLE codeshare_map ADD COLUMN IF NOT EXISTS superseded_by INTEGER REFERENCES codeshare_map(id);

CREATE INDEX IF NOT EXISTS idx_codeshare_current_atc
    ON codeshare_map(marketing_carrier, marketing_flight_num)
    WHERE relation = 'atc_callsign' AND retired_at IS NULL;

ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS alert_topic TEXT;

INSERT INTO codeshare_map
    (marketing_carrier, marketing_flight_num, operating_carrier, operating_flight_num,
     origin, destination, confidence, source, first_seen_at, last_confirmed_at,
     relation, season)
SELECT v.mc, v.mn, v.oc, v.onum, v.org, v.dst, 1, 'fids_route_unique',
       '2026-10-19T00:00:00Z', '2026-10-19T00:00:00Z', 'atc_callsign', 'W26-pending'
FROM (VALUES ('UAE', '231', 'UAE', '3MX', 'OMDB', 'KIAD'),
             ('DLH', '414', 'DLH', '3Y',  'EDDM', 'KIAD'))
     AS v(mc, mn, oc, onum, org, dst)
WHERE NOT EXISTS (
    SELECT 1 FROM codeshare_map m
    WHERE m.relation = 'atc_callsign' AND m.retired_at IS NULL
      AND m.marketing_carrier = v.mc AND m.marketing_flight_num = v.mn
      AND m.origin = v.org AND m.destination = v.dst);
