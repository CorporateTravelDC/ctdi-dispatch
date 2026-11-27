# SWIM parser spec: the types the census sees and nobody parses

Added 2026-11-27 (operator 2026-11-26: "parsers for every unparsed SWIM message type in the census", spec
committed before the 03:40Z backup). This is a design spec, not live behaviour: nothing below is built yet.
It follows `docs/SWIM_CENSUS.md` (the census that found these types). Counts are the census's own, last 7
days to 2026-11-27T02:00Z (`swim_message_census`, `handled = 0`), read through the EA read mirror.

Structure was confirmed from the census's redacted samples (element and attribute NAMES only; no value from
a sample appears here). Types marked "report only" have no samples: the census does not sample a type the
parser already accepts, so their structure comes from the code and the SFDPS reference manual and must be
confirmed from real samples before the parser lands (step 0 of the FDPS build below).

## Why

On 2026-11-26, in the last 24 h, the census counted 70,518 FDPS flights, 411,206 TFMS messages and 10,902
ITWS products that reached a parser and were dropped. Most of the TFMS volume is one type
(`fltd/FlightSectors`, about 7 a second). Several dropped types carry exactly what the watch path lacks:
actual departure and arrival times (FDPS `DH`, `HV`, `NU`), beacon code changes (`NP`, `RE`), TFMS ETA with
SID/STAR and arrival fix (`flightPlanInformation`), and runway wind-shear and microburst alerts (ITWS
Configured Alerts).

## Rules every new parser follows

- **Field merge, never overwrite.** `upsert_flight_event` (`src/common/db.py:3193`) sets `airline`,
  `origin`, `destination`, `aircraft_type` and `status` from the new row unconditionally, so a partial
  message (most of the types below carry only a few fields) would blank the row. FDPS partial types go
  through a new field-merge writer: only fields the message actually carries are written, each guarded by
  the message's own timestamp (an older message never replaces a newer value).
- **OOOI only through the authority gate.** Actual off/on times go to `upsert_flight_ooooi`
  (`src/common/db.py:7722`) and watch phases through `update_watchlist_oooi_phase_authoritative`
  (`src/common/db.py:7367`), whose `_oooi_authority_check` decides. FDPS is a SWIM source and may assert
  phases; it never overrides a higher-precedence source the gate already holds.
- **Privacy on read.** LADD and the other withheld categories are filtered when read
  (`src/common/withheld.py`), so stored rows may hold listed flights; no new public route may bypass that
  filter. A tail comes from SWIM only; FDPS `registration` is a tail source, TFMS is not a new one here.
- **Watched-only where marked.** High-volume per-flight types store rows only for flights on the watchlist
  (or within the DC area where marked), so table growth is bounded by what we watch, not by the NAS.
- **Census stays truthful.** A type is recorded `handled = 1` only when its row was written or deliberately
  merged; a type parsed and then discarded by a filter is recorded `handled = 1` with the filter counted
  separately in the hourly INFO line, never silently.
- **Times in UTC** (ISO 8601 `Z` or epoch); never ET alone.
- **Load.** Parse with the element tree the handler already has; never re-serialise a whole message
  (`ET.tostring`) on the hot path; no per-message database round trip for types above 1,000 an hour
  (batch, or write on change).

## Step 0: hygiene, before any new parser

1. **TFMS re-serialises every dropped fltd message.** `src/ingest/parsers/tfms_parser.py:725-726` calls
   `ET.tostring(fltd_message)` for every message with no handler, only so `census.record` can decide
   whether to keep a sample (it keeps 5 a day). At `FlightSectors` volume that is about 600,000
   serialisations a day thrown away. Fix: pass a callable (or check the census's daily sample quota first)
   so the bytes are built only when a sample will actually be kept. Same pattern for FDPS
   (`src/ingest/parsers/fdps_parser.py:657-661`), lower volume.
2. **RAPT is in the wrong set.** `RAPT` sits in `_KNOWN_UNHANDLED_FLTD_TYPES`
   (`src/ingest/parsers/tfms_parser.py:3256-3261`), but it arrives as an `fiMessage` (`raptTimelineMessage`
   under `fiOutput`), so the fi path treats it as unknown on every message: census `fi/RAPT`, a log line
   and a debug capture (`tfms_parser.py:690-695`). `DICE` and `FXASF` take the same path. Fix: add them to
   `_KNOWN_MSG_TYPES` (handled or explicitly known-unhandled) and remove `RAPT` from the fltd set.
3. **Census redaction misses two identifiers.** `_ID_ATTRS` (`src/ingest/census.py:60-62`) has no
   `aircraftId` (the TFMS `qualifiedAircraftId/aircraftId` element) and no `gufi`. In the current samples
   every TFMS `aircraftId` element and every FDPS and TFMS `gufi` element is unredacted (the `acid`,
   `aircraftIdentification`, `registration` and `aircraftAddress` values are all redacted). Fix: add both,
   plus `igtd` and `computerId/idNumber`, with a test that runs `redact` over one real sample of each feed.
   Existing samples age out in 14 days; delete the current ones when the fix lands.
4. **Two debug capture directories are world-readable and unredacted.**
   `/var/lib/corporatetraveldc/tfms_debug_unknown_msgtype/` (205 files) and
   `/var/lib/corporatetraveldc/itws_debug_by_product/` (10 files) are mode 0755 with 0644 files, written
   raw by `_capture_unknown_msgtype_sample` (`tfms_parser.py:398-415`) and the ITWS product capture
   (`src/ingest/parsers/itws_parser.py:181-200`). The TFMS ones hold whole TFMS documents, identifiers
   included. Fix: write through `census.redact`, create the directory 0700 and files 0600, and have the
   relay that lands it `chmod -R go-rwx` both directories and delete TFMS captures older than 14 days. The
   census now samples every one of these types, so the captures add nothing the census lacks.
5. **Samples are cut at 64 KiB.** All nine `fi/RAPT` samples are exactly 65,536 bytes or close and do not
   parse. Raise the per-sample cap for TFMS fi documents (or keep the first complete child element) so the
   RAPT parser can be built against whole samples.

## FDPS (SFDPS FIXM 3.0 record codes)

Today: `HX`, `HZ`, `LH`, `HF`, `RH`, `BA` are in `_KNOWN_SOURCES_FIXM30`
(`src/ingest/parsers/fdps_parser.py:156`), so they parse, but `src/ingest/swim_client.py:869`
(`_FDPS_WRITE_SOURCES`) does not write them and the census does not sample them. Every other code is
dropped at `fdps_parser.py:657-661`. Each message is one `flight` element with `centre`, `source`,
`system`, `timestamp`, `type` attributes, plus `gufi`, `flightIdentification` and `flightStatus`
(`fdpsFlightStatus`) and `supplementalData/additionalFlightInformation/nameValue` pairs. "Carries" lists
what each type adds to that common part.

| code | 7 d | carries (beyond the common part) | target and writer | priority |
|---|---:|---|---|---|
| `DH` departure | 16,851 | `departure/runwayPositionAndTime/runwayTime/actual@time`, arrival estimated time, `aircraftDescription` type, `coordination`, operator | actual OFF to `upsert_flight_ooooi` via the authority gate; arrival estimate and type by field merge | 1 |
| `HV` arrival | 14,192 | departure `actual` AND `arrival/runwayPositionAndTime/runwayTime/actual@time` | actual ON via the gate; closes the row (`status` landed) by field merge | 1 |
| `NU` | 36 | departure `actual`, `assignedAltitude/simple` | actual OFF via the gate; assigned altitude by merge | 1 |
| `NP` | 446 | `enRoute/beaconCodeAssignment/currentBeaconCode` | `squawk` by field merge, guarded by message time | 2 |
| `RE` | 112 | `currentBeaconCode` and `previousBeaconCode`, both runway times | `squawk` by merge; previous code kept in `raw_json` | 2 |
| `BA` | 2,472 | report only (expected: beacon code assignment) | `squawk` by merge (same writer as `NP`) | 2 |
| `DBRTFPI` | 2,401 | a full plan: `agreed/route` (`nasRouteText`, `expandedRoute/routePoint`, `adaptedArrivalDepartureRoute`), `aircraftDescription` incl. `registration` and `aircraftAddress`, capabilities, requested speed and altitude, alternate, coordination fix | same path as `FH` (it is a full plan), but only when `timestamp` is newer than the row's; route to `fdps_route_versions` | 2 |
| `RH` | 14,187 | report only (expected from the code name: route or status change) | `status` by field merge | 3 |
| `ET` | 204 | departure `estimated` and `controlled` time (an EDCT-style controlled departure), arrival estimate | controlled time beside the watch's EDCT (TFMS `tfms_edct_slots` stays the EDCT authority; FDPS corroborates) | 3 |
| `HH` hold | 320 | `agreed/route/holdFix@fix`, `enRoute/expectedFurtherClearanceTime@time` | new per-flight hold record (fix, EFC time), watched flights and DC-area arrivals only | 3 |
| `HT`, `PT` | 17,885 / 2,325 | `enRoute/pointout` with `originatingUnit` / `receivingUnit` (`unitIdentifier`, `sectorIdentifier`) | `controlling_facility` / sector by merge, watched only; otherwise counted | 4 |
| `IH` | 96 | departure `actual` and `estimated`, endpoints | actual OFF via the gate (as `NU`) | 3 |
| `HZ` | 158,844 | report only (parsed today as the heartbeat code) | merge `updated_at` only (keeps the expiry sweep honest); no other field | 4 |
| `HX` | 235,879 | report only | watched only, merge; confirm structure from samples first | 4 |
| `LH`, `HF` | 72,765 / 32,999 | report only | confirm from samples, then merge or record as known-unwritten | 4 |
| `NL` | 493 | departure and arrival points only | counted as handled-no-write (nothing to store) | 5 |
| `NI` | 7 | the common part only | counted as handled-no-write | 5 |

Build notes:
- Step 0 for FDPS: add `HX`, `HZ`, `LH`, `HF`, `RH`, `BA` to the census sample path for one day
  (`swim_client.py:905-909` already samples `AH`/`HU` this way), read the samples, then fix the table.
- One field-merge writer for all partial codes (a db helper beside `upsert_flight_event`), never the
  full upsert. `DBRTFPI` is the only full-plan code here.
- `HV` and `DH` are the payoff: watched flights get SWIM-asserted OFF and ON from FDPS where TFMS or TBFM
  has not already set them.

## TFMS

| type | 7 d | carries | target and writer | priority |
|---|---:|---|---|---|
| `fltd/flightPlanInformation` | 129,066 | `qualifiedAircraftId` (aircraftId, computerId, gufi, igtd, endpoints), `ncsmRouteData`: `etd@etdType`, `eta@etaType`, `dp@routeName` and `star@routeName` with transition fixes, `arrivalFixAndTime@fixName,arrTime`, `departureFixAndTime`, `diversionIndicator`, `rvsmData`, `flightTraversalData2`, filed speed and altitude, `routeOfFlight` | watched only: TFMS ETA and type to `upsert_flight_ooooi` (`tfms_eta`, `tfms_eta_type` already exist); SID, STAR and arrival fix to the watch's route fields; arrival-fix time stays a fix time and is never shown as an arrival time | 1 |
| `fltd/boundaryCrossingUpdate` | 78,666 | `boundaryPosition@boundaryCrossingTime` with `latLong` (DMS) or `namedFix` / `fixRadialDistance`, assigned altitude, filed speed, `routeOfFlight` | watched only: latest crossing per flight (centre boundary, time, altitude) for the watch timeline; position is not ADS-B and asserts no OOOI phase | 2 |
| `fi/DICE` | 244 | `deicingMessage`: `eventTime`, `entryTime`, `facility`, `airport`, `status`, `remarks` | new de-icing status table keyed by airport, write on change; DC airports alert on status change | 2 |
| `fi/RAPT` | 3,735 | `raptTimelineMessage/RAPTData`: `Metroplex`, `CIWSProductTime`, `RAPTTimelineData` per `RouteName` with `RouteTrend`, `PIGTimer`, and `RAPTBlockageData` per `DepartureTime` with `BlockageStatus`, `BlockagePhase`, `EchoTop`, trends | aggregate only: per metroplex and route, latest blockage summary (write on change); confirm whether a DC metroplex is ever present before any alerting (the module docstring says only NYC was seen) | 3 |
| `fltd/FlightSectors` | 4,291,015 | `ncsmFlightSectors`: `qualifiedAircraftId`, `flightTraversalData2` (`fix`, `waypoint` with lat/long, `airway`, `center@elapsedEntryTime`, `sector@elapsedEntryTime`) | watched only, latest message per flight kept in memory with a TTL (sector and centre entry times for the watch timeline); never a row per message | 4 |
| `fi/FXASF` | 15 | report only (no sample in the last 3 days): FCA show-filter metadata for `FXA` | attach to the existing `_FXA_REGISTRY` entry or record known-unhandled | 5 |

Build notes: the `fltd` common attributes (`acid`, `airline`, `depArpt`, `arrArpt`, `msgType`,
`sourceTimeStamp`, `flightRef`, `fdTrigger`) are enough to decide "watched?" before walking the body, so
the watched-only filter costs one dict probe per message. `FlightSectors` must not be parsed beyond that
probe for an unwatched flight.

## ITWS

All four are in `_KNOWN_UNHANDLED_PRODUCTS` (`src/ingest/parsers/itws_parser.py:461`). Every product has the
same `packet_header` and `product_header` (site, airports, product type and status, generation, expiration
and received times), so one shared header reader serves all four.

| product | 7 d | carries | target and writer | priority |
|---|---:|---|---|---|
| Configured Alerts | 23,318 | `configured_alert`: alert time, `ca_rwy_name`, airport wind direction, speed and gust, `ca_radar_impaired`, `ca_llwas_impaired`, `ca_num_rwys`, and per runway `ca_rwy_alert`: `ca_ra_region_id`, `ca_ra_type`, `ca_ra_value`, first and last location, LLWAS wind | new runway-alert table (airport, runway, alert type, value, valid and expiry time), write on change; a wind-shear or microburst alert for a DC runway also goes to `itws_alerts` (`WIND_SHEAR` / `MICROBURST` already exist there) | 1 |
| Forecast Accuracy | 5,841 | `forecast_accuracy`: `fca_sa30`, `fca_sa60`, `fca_sr30`, `fca_sr60`, `fca_wa30`, `fca_wa60`, `fca_wr30`, `fca_wr60` | small table per site and generation time (eight scores); cheapest product, build first as the template | 1 |
| Precipitation 5nm | 8,448 | `precip`: grid geometry (`prcp_dx/dy/dz`, rows, cols, levels, offsets, rotation, radar reference point), quality flags (`prcp_attenuated`, `prcp_ap_detected`, bad and no-coverage values), `prcp_nws_levels` with a compressed grid (`prcp_grid_compression_encoding_scheme`, `prcp_grid_compressed`, `prcp_grid_max_precip_level`) | decode in memory to a summary only (max NWS level, cells at each level within fixed radii of each DC airport); hourly maxima to a new hourly weather table; never store the grid | 2 |
| Wind Profile | 5,877 | `wind_profile`: `tw_num_profiles`, per `tw_profile` a location name, grid row and column, and per altitude line `twln_altitude`, `twln_direction`, `twln_speed`, `twln_quality` | latest profile per location (write on change) for approach briefs | 3 |

Build note: every sample carries `packet_header_packet_index` and `packet_header_packets_in_msg`; check
whether the census counts a multi-packet product once per packet, and reassemble before decoding
Precipitation.

## Build plan

One commit per feed, each with its own tests built from the redacted samples (copied into the test tree
after a second redaction check), so each can be reverted alone:

1. Hygiene (step 0, all five items) and the census redaction test. No new tables.
2. ITWS: Forecast Accuracy, Configured Alerts, Precipitation summary, Wind Profile. New migration.
3. TFMS: `_KNOWN_MSG_TYPES` fix, `flightPlanInformation`, `boundaryCrossingUpdate`, `DICE`, `RAPT`,
   `FlightSectors` (in memory), `FXASF`. New migration for the de-icing and RAPT tables.
4. FDPS: sample the six report-only codes for a day, then the field-merge writer, `DH`/`HV`/`NU`/`IH`,
   then beacon codes, `DBRTFPI`, the rest. New migration only for the hold record.

Migration numbers are assigned when each commit is built: the pending step 2 (`PG_TABLES`) and step 3
(ERAM part A) relays land first. Each commit's acceptance: the census shows the type `handled = 1` within
one hour of rollout, the feed's hourly INFO line shows no new drop, and the per-type write rate matches
the census count (watched-only types: matches the watched share).
