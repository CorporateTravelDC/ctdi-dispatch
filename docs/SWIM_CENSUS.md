# SWIM message-type census and flight-row expiry

Added 2026-11-19 (operator: "a sweep of what EVERY subscription's message types are and parse them all to
enrich all our current data"). Code: `src/ingest/census.py`, `src/common/flight_expiry.py`. Migration:
`src/common/pg_schema/0089_swim_census_and_expiry.sql`. Inventory and rationale:
the operator's `SWIM-MESSAGE-TYPE-SWEEP.md` (EA private folder, 2026-11-19).

## Why

Every feed parser dropped message types it did not know with a debug-level log line, so whole message
families went unseen for months: FDPS `HF`/`RH` until 2026-08-17, `HU` until 08-30. On 2026-11-19 the FDPS
handler still threw away `AH` and `HU` (parsed, then not written), and never parsed `NP`, `NU`, `DH`, `HV`
or `HR`. Rows were never closed, so a leg that landed the day before still read "active".

## The census

- Every message (FDPS: every flight inside a batched document) is counted per feed and type, in memory.
- Every 5 minutes the counts go to `swim_message_census` in one batched write: hour, feed, type, handled
  (1 = parsed and stored by its handler, 0 = dropped), count.
- Once an hour, one INFO line per feed lists the counts; dropped types are marked `(DROPPED)`.
- A dropped type is warned once per feed and type per hour.
- Samples: up to 5 a day of each dropped type (and of FDPS `AH`/`HU` while their mapping is new), and of
  every document type on feeds whose parsers have no per-type hook, are kept under
  `SWIM_SAMPLE_DIR` (default `/var/lib/corporatetraveldc/swim-samples/<feed>/<date>/`), mode 0600, with
  aircraft identifiers (callsign, registration, ICAO address, beacon code) replaced by `REDACTED`. They are
  deleted after 14 days.
- A dropped type above `SWIM_CENSUS_DROP_ALERT_PER_HOUR` (default 100) in one hour raises one ops-health
  alert per feed and type per UTC day.
- Type names: FDPS uses the flight's `source` code; TFMS `fi/<msgType>` and `fltd/<msgType>`; ITWS the
  product name; TFDM `ttp:` plus its data group and message type headers; the other feeds `doc:<root element>`.

Read the census (read-only): `select feed, msg_type, handled, sum(n) from swim_message_census where hour_start >
extract(epoch from now()) - 86400 group by 1, 2, 3 order by 4 desc;`

## FDPS: amendments and plan updates written

`AH` (Flight Amendment Information) and `HU` (Flight Plan Update Information) carry the same fields and
structure as `FH` (SFDPS Data Consumer Reference Manual v2.1.4). They now go through the same write path
as `FH`, including the filed departure time. The other types the parser accepts but does not write are
counted, not stored. `NP`, `NU`, `DH`, `HV`, `HR` and any other unknown code are counted and sampled; their
parsers follow once real samples confirm the structure.

## Expiry sweep

The poller runs `common/flight_expiry.sweep()` every 30 minutes. A row still `active` is set to
`expired` when its last update is older than 3 h and its arrival/ETA is more than 3 h past, older than
6 h and it carried a position, or older than 24 h. Two bounded passes per run (2,000 rows each) on the
existing `updated_at` index: a recent window (30 h) and the older backlog from a cursor in
`flight_expiry_state`. `db.get_flight_plan_by_callsign` never returns an expired row. A new message for
the same GUFI writes the row's status again.
