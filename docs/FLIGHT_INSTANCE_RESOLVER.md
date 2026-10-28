# Flight-instance resolver

2026-10-13, refreshed 2026-10-15 against main 1491bf5a plus the batch staged for the next relay. Code: `src/common/flight_instance.py`, wiring in `src/poller/main.py`. Operator requests 2026-10-11/12, decisions 2026-10-13/14.

## Why

A flight number is not a flight. These cases are confirmed live:

- **Shuttles.** One number flies several times a day.
- **Both directions.** One American flight number flies DCA to Florida in the morning and back to DCA in the afternoon. The TFMS/TBFM watch matchers compared callsigns only, so the afternoon leg's messages advanced a watch on the morning leg.
- **Regional operators.** A United-marketed four-digit number flew under its regional operator's callsign (GJS). On the same night another regional flew the same number on a different route, and `codeshare_map` held both rows.

## The key

A **flight instance** is identified by:

```
operating carrier (ICAO) + flight number + origin + destination + scheduled date
```

- The scheduled date is the UTC date of the scheduled departure. Airports are normalized so that `KDCA` and `DCA` count as the same airport.
- Marketing numbers translate to operating numbers through `codeshare_map`. The translation is filtered by route: those rows store an origin and destination, and a row whose route contradicts the watch is dropped (`operating_identities`).
- `same_instance(a, b)` is strict. Both airports must positively agree, and both dates must be known and equal. An unknown component never proves two instances are the same.
- `airports_agree` returns True, False or None. It returns None when one side is missing or when the two codes come from different systems (`EGLL` vs `LHR`). Callers reject only on False.

## Sources (read-only)

| Source | Gives | Notes |
|---|---|---|
| `flight_ooooi_times` (TFMS) | scheduled departure/arrival (`original_*`), airline OOOI times, status | Persisted nationally since 2026-09-22. This is the source that carries the date. |
| `flight_events` (FDPS) | hex + registration (from the row's own `<flight>` element), runway ETA, status, filed departure time | DC-area only. ~~`departure_time` is written as NULL today, so an FDPS-only instance usually has no date.~~ (corrected 2026-10-15: since 1491bf5a FH messages store the proposed runway departure time, first filing wins, so FDPS-only instances have a date; see Done 2026-10-14. The docstring of `instance_from_flight_event` still says NULL.) |
| `codeshare_map` | marketing -> operating, per route; since 2026-10-20 also public number -> ATC callsign (`relation`) | (2026-10-20, pg_schema/0082) `relation` = `codeshare` (marketing -> operating flight, as before) or `atc_callsign` (public number -> the alphanumeric callsign it is filed under: EK231 = UAE3MX, LH414 = DLH3Y, seeded from the IAD FIDS 2026-10-19); `season`, `retired_at`, `superseded_by`. Readers see current rows only (`retired_at IS NULL`); a relink retires the old row and keeps it. |
| `tdes_departure_events` (STDDS TDES) | departure clearance at DCA/IAD/BWI with `sfdps_gufi` | No tail of its own; links to the FDPS plan (`tdes_clearance_plan`). |
| FAA / OpenSky registries | tail <-> hex | `registration_for_hex`, `hex_for_registration` |

`resolve(identifier, origin, destination, sched_date, hex_id=)` returns the candidate instances. Rows with the same GUFI from TFMS and FDPS are merged before filtering, so an undated FDPS row inherits its TFMS twin's date. A row is dropped only when its route, date or hex positively contradicts the query.

`instances_for_hex(hex)` returns every FDPS filing whose own `<flight>` element carries the given hex. `raw_json` holds batches, so a `LIKE` match is only a prefilter and each hit is re-parsed. ~~This function runs on a tail-change event, never once per tick.~~ (corrected 2026-10-15: the scan takes about 20 s live. The FDPS repoint still calls it once per tail-change event, but the inbound tracker and the TDES tail check call it periodically, so those go through `poller.main._hex_instances`, a 10-minute per-hex cache (`HEX_INSTANCES_TTL_S = 600`), next relay.)

## Lookaside learning (2026-10-20, next relay)

`common/callsign_lookaside.py` keeps the `atc_callsign` links current. Nothing it stores is a FIDS time, status or tail.

- **FIDS leg match** (`learn_from_fids`, from the poller's `_check_flight_fids` after `airport_fids.match_leg`). When the watch already resolves to a SWIM leg and the FIDS row matched inside that leg's window, the public number is linked to the leg's callsign (`fids_leg_match`; a `codeshare` row when the carrier differs). When nothing links yet (at most every 6 h per watch side): skipped if the number is filed under its own ICAO form on the route; else each of the carrier's SWIM legs on the route in the last 36 h (minus every number the board shows as its own flight) is tried with its own window; exactly one leg picking the row links it (`fids_route_unique` when it is the only such leg, `fids_leg_match` otherwise); two or more = ambiguous, nothing (AF50/AF52, QR707/QR709). LADD-listed identifiers are never linked (fail closed).
- **Route continuity** (`relink_by_route_continuity`, daily from the poller `flight-cleanup` skill). A linked callsign absent on its route on each of the last 2 UTC days, with exactly one same-carrier callsign that is new to the route (not seen in the 14-day baseline) flying it inside +/-30 min of the old callsign's typical time (median FDPS estimated arrival) on both days: the old row is retired with `superseded_by`, the new one inserted (`route_continuity`, lower confidence), and a bellwether watch gets one p3 `atc_callsign_relink` push on `bellwether-alerts`. If the carrier went back to filing the public number itself, the link is just retired.
- The existing `scripts/seed-dc-codeshare.py` FIDS codeshare pass already maps FIDS rows that share one leg (codeshares) onto the primary flight's current operating callsign, `atc_callsign` links included.

## Rules

### 1. Watch matching (TFMS, TBFM, FDPS, TFMS FADT; SMES in its own form below): wired

`match_watch_entry(entries, identifier, origin=, destination=, hex_id=, sched_dep=)` returns the first watch that meets both conditions:

- **It answers to the identifier.** The match can be exact, by ICAO normalization (`AA<n>` = `AAL<n>`), through a route-filtered `codeshare_map` operating callsign, or by hex. (2026-10-20) Also through a current route-filtered `atc_callsign` link (EK231 answers to UAE3MX; `parse_flight_ident` now reads the alphanumeric ATC form as the FDPS split, UAE / 3MX). A match through the lookaside (`codeshare` or `atc_callsign`) must be **proven** on the watch's route: at least one message airport agrees with the watch (`lookaside_route_unproven`; SMES: the event's side airport), so a recycled alphanumeric or a message with no airports never advances the watch. Tests: `tests/ingest/test_watch_matcher_atc_callsign.py`.
- **`leg_rejection` raises no objection:**
  - `origin_mismatch` / `cross_direction`: the message's origin positively disagrees with the watch's origin.
  - `other_time_of_day`: both scheduled departures are known and are more than 3 h apart (`WATCH_SCHED_TOLERANCE_H`).
  - `destination_mismatch`: the destination positively disagrees. There is one exception, for diversions. If the origin agrees and both scheduled departures are known and agree, a different destination counts as this leg diverting, and the message still matches. Without that time proof it does not match.

Messages that carry no airports still match on identity, as before.

**Which watches get leg checks** (next relay, swarm audit NB-02). `is_leg_watch(entry)` is True only for a flight-number identifier whose watch has both an origin and a destination. Every other watch (a tail or aircraft identifier, or a permanent watch with only a home origin) skips `leg_rejection` and the SMES side rules and matches on identity alone, as before 2026-10-13, so it follows the aircraft or number on every leg. On 2026-10-15 01:30Z the live table held 16 flight watches, all with an origin and only 4 with a destination, ~~so most watches are not leg watches~~ (superseded 2026-10-25 15:49Z: 60 permanent flight watches and 0 transient, all with an origin and 47 with a destination, so most permanent watches are now leg watches).

**Diversions without a schedule** (next relay, NB-03). `leg_rejection(..., diversion_without_sched=True)` also accepts a different destination when the origin agrees and the schedule is unknown. FDPS (`check_fdps_watchlist`) passes it, because FDPS plans carry no scheduled time to compare; SMES passes it for arrival events on the message's own route. TFMS and TBFM stay strict: without the time proof a different destination does not match.

Wired call sites:

- `tfms_parser._match_watchlist_flight`. Every fltdMessage handler passes `_leg_context(...)`: the `depArpt`/`arrArpt` attributes, with qualifiedAircraftId's departurePoint/arrivalPoint as fallback, plus `igtd` as the scheduled departure.
  - `TMI_FLIGHT_LIST` reads the `flight` element's departurePoint, arrivalPoint and igtd.
  - `TMI_UPDATE` reads the two end airports of the amended route string.
- `tbfm_parser._match_watchlist_flight` reads `<air dap>` for the departure and `apt` for the metered arrival airport. TBFM carries no scheduled time, so the same number flown in the same direction twice in one day cannot be told apart here.
- `fdps_parser.check_fdps_watchlist` (origin/destination of the plan, `diversion_without_sched=True`) and the TFMS FADT EDCT slot loop (`tfms_parser`, around line 2260). See Done 2026-10-14.

The codeshare aliases are cached for 5 minutes per (identifier, route), so the national TFMS stream costs one dict lookup per message. The watch list itself is still read on every call, as it was before.

### 2. Tail-change guard: wired into the FDPS repoint

`decide_tail_change(...)` is a pure function. It accepts a change only when all three conditions hold:

- **(a) Authoritative source.** The source must be SWIM: `fdps` (the FAA flight plan), `tdes` (the plan linked to this flight's departure clearance), `tbfm` or `tfms`. FIDS is never a tail source. A third-party tracker such as airplanes.live, OpenSky or AeroAPI can add corroboration but is never enough on its own.
- **(b) Tail not filed elsewhere.** The candidate tail must not be filed on a different instance that overlaps the watched leg. Overlap means the other leg's [departure, arrival] window intersects [watched departure - minimum turn, watched arrival]. These do not count as conflicts:
  - the watched instance itself
  - its previous leg
  - cancelled legs
  - untimed filings that are not active
  - the tail's next leg after the watched arrival
- **(c) Previous leg reaches the origin in time.** The previous leg must:
  - end at the watched origin
  - not be cancelled
  - arrive at least the minimum turn before the watched departure, using the latest estimate if there is one and the schedule otherwise (`late_tolerance_min`, default 0)

  The caller can satisfy (c) instead by passing `on_ground_at_origin=True` as positive evidence. If the previous leg is unknown, the change is rejected. The rule is "show it gets there", not "assume it does".

  **(c) does not run today** (2026-10-15). Since the 2026-10-13 decision, (c) is skipped whenever a SWIM source shows the tail, and since 2026-10-14 every accepted source is SWIM (`AUTHORITATIVE_TAIL_SOURCES` and `SWIM_TAIL_SOURCES` are the same set, `fdps`/`tdes`/`tbfm`/`tfms`, ~~flight_instance.py:914 and :920~~ flight_instance.py:1021 and :1027 (2026-10-25)). The previous-leg branch and `on_ground_at_origin` (no caller passes it) are unreachable and kept only for a future non-SWIM source. Accepting a change today needs (a) and (b) only.

Outcomes:

- **Accepted:** the hex is repointed and a push goes out with the reason.
- **Rejected:** the watch keeps its hex and gets a note "tail change NOT accepted (...): reason".

Wiring in `poller.main._check_flight_fdps_cache`, the only existing hex-repoint path:

- **Other-leg plans are ignored.** If an FDPS plan's origin contradicts the watch, the plan belongs to another leg and is ignored completely: no tail change, no status change, no "destination changed" diversion push. The destination is not gated, because a destination change on the right leg is the diversion signal.
- **Route-filtered codeshare lookup.** The periodic codeshare lookup now uses the route-filtered `operating_identities` instead of `resolve_operating_callsign`, which took the highest-confidence row regardless of route.
- **Repoint goes through the guard.** `_fdps_tail_change_decision` builds the inputs: the watched leg (keyed on the plan's operating callsign plus the watch's route and schedule), `instances_for_hex`, and `previous_leg`.
  - Accepted: the existing `fdps_tail_change` push now carries the reason. A hex-only FDPS plan (the `ADSB_02M_52B` shape) gets its tail looked up instead of writing `registration=None`.
  - Rejected: a `fdps_tail_change_rejected` note, priority 3, sent once per (watch, candidate hex) through `PushDedup("fdps_tail_guard")`.
  - A guard error counts as a rejection and is logged. It never repoints silently.

~~**Behaviour change to expect.** `flight_events` is DC-area only. For a watch whose origin is outside DC (an inbound to DCA, for example), the new tail's previous leg is usually not on file, so (c) rejects an FDPS tail change that would previously have been applied. The watch keeps its old hex and the operator gets the NOT-accepted note. See Open questions.~~ (corrected 2026-10-15: true from 10-13 until the same day's decision; FDPS is a SWIM source, so (c) is skipped and an FDPS tail change outside DC is accepted unless rule (b) finds the tail filed on another overlapping leg.)

**TDES path.** `_check_flight_tdes_tail` sends its candidate through `_guarded_tail_change` (source `tdes`), the same guard and the same `fdps_tail_guard` dedup; a rejection note carries trigger `tdes_tail_change_rejected`, priority 3. An accepted TDES change is recorded with `hex_source="tdes"` from the next relay (NB-08; before it, `fdps`).

**FIDS tail: not a source, by rule.** FIDS is gate and baggage enrichment only (2026-09-27, restated 2026-10-14). The tail comes from SWIM: FDPS, the TDES departure clearance's plan, TBFM and TFMS.

### 3. Time-fidelity pairing: implemented

`pair_events(reference, candidates, sched_tolerance_min=90)` pairs each reference event with at most one candidate from the same instance. Same instance means the same operating flight, both airports agreeing, the same scheduled date, scheduled departures within the tolerance, and the same `phase` when both events carry one. Candidates are taken nearest scheduled departure first. An unpaired event records its most informative rejection, in this order:

1. `different_phase`
2. `other_time_of_day`
3. `different_date`
4. `cross_direction`
5. `no_sched`
6. `different_route`
7. `different_flight`
8. `no_key`

The key is the instance key above. The separate read-only time-fidelity analysis documents its own pairing rule. Compare the two before choosing a single rule.

### 4. Inbound-aircraft tracker ("where's my inbound"): ~~implemented, not wired~~ wired (corrected 2026-10-15: wired in 1491bf5a, `poller.main._check_flight_inbound`, called from the flight sweep at ~~main.py:642~~ main.py:650 (2026-10-25))

`assess_inbound(watched, inbound, min_turn_min=, aircraft_class=)` is a pure function:

- **Inbound ETA** at the watched origin: actual arrival if known, then estimated, then scheduled.
- **Ready time:** ETA + minimum turn.
- **Propagated departure:** max(watched departure estimate or schedule, ready time).
- **Slack:** minutes to spare. A negative value means late.
- **Status:** one of `on_time`, `late_beyond_turn`, `at_origin`, `diverted`, `cancelled`, `unknown`.
- **Flags:** `inbound_late_beyond_turn_buffer`, `inbound_diverted`, `inbound_cancelled`, `inbound_unknown`, `inbound_eta_unknown`.

`inbound` is the assigned tail's current instance. Get it with `previous_leg(instances_for_hex(watch.hex_id), watched)`, or with `resolve()` on the inbound's flight number. The poller uses `previous_leg(_hex_instances(hex), watched)` (cached scan, next relay).

Minimum turn defaults are 35 min for narrowbody, 30 for regional and 75 for widebody. Override them with `CTDC_MIN_TURN_MIN_<CLASS>`, which is clamped to 10-240.

~~**Wiring point (follow-up):** a per-tick step in the poller's flight-watch loop, next to `_check_flight_fdps_cache`, for watches that have a hex and have not departed yet. It would push once per status transition, with the status in the dedup content hash, so it does not repeat.~~ (corrected 2026-10-15: done; see "Inbound tracker wired" under Done 2026-10-14.)

## Standing rules respected

- OOOI authority: nothing here writes a phase. The tracker only reads airline/SWIM times. Any future OOOI writer must use `db.update_watchlist_oooi_phase_authoritative`. FIDS remains enrichment-only. ~~A FIDS tail is valid input to the guard because it is identity, not an OOOI phase.~~ (corrected 2026-10-15: FIDS is not a tail source either, operator 2026-10-14; the guard rejects it as not authoritative.)
- OOOI phase writers, as of 2026-10-15 (inventory requested by the 10-12 docs audit, DP-A11):
  - Gated (`db.update_watchlist_oooi_phase_authoritative`): TFMS achieved milestones (`tfms_parser.py:982`, source `tfms`), SMES surface events (`smes_parser.py:1245`, `smes`), the overdue-OFF check (`poller/main.py:1474`, `acars`/`fdps`/`tfms_airline`), the add-time TFMS seed (`shared/watchlist.py:1345`, `tfms_airline`). (2026-10-25: the `update_watchlist_oooi_phase_authoritative` call sites are now ~~`tfms_parser.py:936,992`~~ (corrected 2026-10-26: `tfms_parser.py:992`, one call; :936 is a comment), `smes_parser.py:1234,1258`, `poller/main.py:1215,1446`, `shared/watchlist.py:947,1428`; the four line numbers in this bullet are as of 1491bf5a.)
  - ~~Ungated (plain `db.update_watchlist_oooi_phase`, last write wins): the airplanes.live/ACARS phase step (`poller/main.py:1211`), the schedule-inferred IN, which needs ACARS confirmation and never FIDS (`poller/main.py:1423`), and the expiry sweep's ACARS rescue (`shared/watchlist.py:884`).~~ (corrected 2026-10-16: no ungated writer remains. The airplanes.live/ACARS phase step (`poller/main.py`, source `acars` or `fdps`, fires only if the gate accepted) and the expiry sweep's ACARS rescue (`shared/watchlist.py`, source `acars`) go through `update_watchlist_oooi_phase_authoritative`; the schedule-inferred IN no longer writes a phase at all (a `watchlist_history` row `oooi_in_inferred` with `asserted=false`, once per entry); `db.update_watchlist_oooi_phase` requires a `source` and delegates to the gate. `tests/poller/test_oooi_writers_gated.py`.)
- Tests use only synthetic identities: hex `000001`/`000002`, tails `N000TS`/`N001TS`, flights `ZZZ…`, plus the operator's own example flight numbers.

## Also fixed in this pass: POST /api/v1/watchlist/flights stored registration=None

Seen on a live flight watch (watch id and date removed 2026-10-15: they named a watched flight). The only thing that ever set `registration` was the caller's own field, so three common add patterns lost it:

1. **Hex-only add.** The caller had the ADS-B hex but not the tail, and the tail was never reverse-resolved.
2. **Body with `"tail"`.** That is FIDS's name for the field, and pydantic silently ignored the extra key.
3. **No identity in the body.** The FAA plan found through `codeshare_map` (the GJS operating callsign) carried both the hex and the tail, and both were discarded.

Fixes:

- `tail` is accepted as an alias of `registration`, on both the single and batch adds.
- A missing tail is reverse-resolved from the hex: first from the FAA plan when its hex matches, then from the registries.
- When the caller supplies no identity and the plan's origin does not contradict the request, the hex and tail are taken from the FAA plan, recorded with `hex_source="fdps"`.
- A value supplied by the caller is never overwritten.

The live entry had already expired before this was investigated, and the request body was not available to read. So which of the three paths hit that watch is inferred, not observed. All three are fixed.

## Done 2026-10-14 (was "What remains")

- **SMES matcher.** `smes_parser._match_watchlist_flight` now uses `match_watch_surface_event`: identifier or route-filtered codeshare, then `surface_leg_rejection`. A departure event (spotout/off) must be at the watch's origin and an arrival event (on/runwayout/spotin) at its destination. With a scheduled departure on the watch, a departure event must fall in [sched - 2 h, sched + 6 h] and an arrival event in [sched - 2 h, sched + 20 h]. The message's own route (enhancedData), when present, must not contradict the watch. The afternoon leg of an out-and-back no longer advances the morning watch. From the next relay these rules apply only to leg watches (`is_leg_watch`, rule 1), and an arrival event at an airport other than the watch's destination is accepted when the message's own route starts at the watch's origin (a diversion, NB-03).
  - ~~**Known broken (2026-10-15, code bug, not fixed in the staged batch):**~~ (superseded 2026-10-25: fixed -- `handle_tdes_apds_record` (`smes_parser.py:1676`) no longer passes `rec.get("callsign")` to the matcher, and live `tdes_departure_events` has rows to 2026-10-25 15:40:56Z, `tdls_messages` to 15:48:14Z; the rest of this bullet is the 2026-10-15 record.) 1491bf5a changed `_match_watchlist_flight` to take the whole record, but the TDES departure and TDLS branches of `smes_parser.handle_tdes_apds_record` (lines 1698 and 1750) still pass `rec.get("callsign")`. Each such record raises `'str' object has no attribute 'get'` before it is stored. Live: no `tdes_departure_events` row after 00:01Z and no `tdls_messages` row after 00:03Z on 10-15, while `corporatetraveldc-ingest-stdds` (restarted 00:04:30Z) logged 1,440 of these errors in 2 h. Until it is fixed the TDES tail source below has no new clearances to read.
- **Other identifier-only lookups.** `fdps_parser.check_fdps_watchlist` and the TFMS FADT slot loop now use `match_watch_entry` (identifier or route-filtered codeshare, airports must agree). An EDCT or a filed plan for the other leg no longer hits the watch.
- **Departure clearance (TDES) as a tail source** (operator 2026-10-14). For every watched flight departing DCA, IAD or BWI, `_check_flight_tdes_tail` takes the TDES departure clearance within 3 h of the scheduled departure (the watch's own, or the derived one, see below). It links by the FAA-internal GUFI (`sfdps_gufi`, carried in the FDPS plan's `FDPS_GUFI`) to the FDPS plan, whose aircraft address is the tail (`fi.tdes_clearance_plan`). More than one linked plan means no answer. The plan's destination must not contradict the watch. `tdes` is a SWIM source: rule (b) applies, rule (c) does not. It runs before takeoff only, at most every 10 minutes per watch, and (next relay) skips the guard when the clearance yields the same candidate hex and plan as last time (`_TDES_LAST_CANDIDATE`). On 10-14, 12 of 21 recent DCA clearances linked to a stored plan; the rest had none on file.
- **Scheduled departure when the watch has none** (next relay, NB-04). Most watches carry no `scheduled_departure` (3 of 16 flight rows live on 10-15), which left the inbound tracker and the TDES check inert. `poller.main._watch_sched_dep` uses the watch's own value when present; otherwise it takes `resolve()` for the watch's flight and route and keeps instances from the watch's origin scheduled in [now - 6 h, now + 24 h]. Exactly one distinct scheduled departure is used; zero or several give None, and the check does not run. The inbound tracker, the TDES check and `_guarded_tail_change` use it. The FDPS repoint keeps its own fallback (the watch's value, else the plan's `departure_time`).
- **FIDS is not a tail source** (operator 2026-10-14: "strictly TDES and FDPS based"). FIDS stays gate and baggage enrichment only (rule of 2026-09-27). Tail sources are `fdps`, `tdes`, `tbfm` and `tfms`, all SWIM. `fids_destination` was removed from `AUTHORITATIVE_TAIL_SOURCES`; the assistant had added it on 10-13 without approval (casebook P33). Guardrail: `tests/poller/test_fids_not_a_tail_source.py`. Open finding for the operator: at the origin, `_check_flight_fids` reads that airport's arrivals board for the flight number, which for an out-and-back is the other leg's gate. Unchanged pending an operator decision.
- **Inbound tracker wired.** `_check_flight_inbound` runs in the flight sweep for watches that have a hex and have not pushed back, from 6 h before scheduled departure to 1 h after. It runs at most every 10 minutes per watch, because `instances_for_hex` scans `raw_json` (and, next relay, the scan itself is cached per hex for 10 minutes and shared with the tail guard). The turn time comes from the inbound's aircraft type (`aircraft_class_for_type`). It pushes once per status transition. The dedup content is the status plus the inbound's instance key, never times or slack. `late_beyond_turn`/`diverted`/`cancelled` push at priority 4, `on_time`/`at_origin` at 2. `unknown` is logged, not pushed.
- **FDPS scheduled times.** Confirmed on live FH (filed plan) messages: the only departure time carried is `departure > runwayPositionAndTime > runwayTime > estimated/@time`, the proposed runway departure. `flight_events.departure_time` now stores it from FH messages. The first filing wins (`COALESCE` in the upsert), so a later delay never moves the instance's date. FDPS-only instances now have a date.

## Open questions

1. ~~Rule (c) on watches whose origin is outside DC FDPS coverage. Should a lone FAA-plan tail change be rejected, as it is now, or accepted with an "unverified inbound" note?~~ Answered 2026-10-13 (operator): neither; SWIM evidence skips (c), and there is no "unverified inbound" acceptance. See Decided 2026-10-13.
2. Is the 3 h `WATCH_SCHED_TOLERANCE_H` right for shuttle numbers? The hourly DCA-LGA/BOS shuttles use distinct numbers today, and this tolerance only matters when a number repeats in the same direction.
3. ~~Should rule (c) allow a late inbound? `late_tolerance_min` is 0 now, so a tail whose inbound is running late is rejected even though airlines usually just delay the outbound.~~ Moot since 2026-10-13/14: (c) no longer runs for any accepted source. A late inbound is reported by the inbound tracker (`late_beyond_turn`, priority 4), not by the guard.
4. At the origin, `_check_flight_fids` reads that airport's arrivals board for the flight number, which for an out-and-back is the other leg's gate (see Done 2026-10-14). Open, pending an operator decision.

## Pairing rule for time-fidelity: INSTANCE-v1 (agreed 2026-10-13)

The 2026-10-13 time-fidelity checkpoint (`docs/research/AIRLINE_TIME_FIDELITY_CHECKPOINT_2026-10-13.md`;
full rule, SQL and rejection counts in `docs/research/private/`) paired surface events with airline-posted
times by **instance key plus a uniqueness and sequence rule**. The instance key alone was not enough: the
collector's 122-min American ramp-exit-to-OFF median was a single same-instance pair (push at the original
schedule, return to gate, second push two hours later), which only the sequence rule rejects.

`pair_events()` above pairs instance records with each other and takes the **nearest** same-instance
candidate. ~~When the collector is moved onto this module (checkpoint next step 1), surface-event pairing must
follow INSTANCE-v1 instead:~~ (corrected 2026-10-15: done in 1491bf5a. `scripts/research/airline_time_fidelity_collect.py`
pairs through `flight_instance.v1_pair_instance` / `v1_pair_surface`, which follow INSTANCE-v1:)
- event side and airport: departure events at the origin, arrival events at the destination;
- window: [filed - 2 h, filed + 6 h] on the relevant side;
- route: a SMES event that carries airports must match the instance exactly;
- siblings: an event that also fits another instance of the same number is rejected, never assigned;
- **exactly one** surviving candidate, or no pair (never "nearest to the reported time", which biases toward
  agreement);
- sequence: 0 < takeoff - ramp exit <= 90 min; 0 < ramp entry - runway exit <= 60 min.

~~The collector also needs `track_id`, `departure_airport` and `destination_airport` stored per surface event and
a time-bounded, instance-keyed TBFM lookup.~~ (corrected 2026-10-15: done in 1491bf5a. `surface_event_log`
(`src/common/pg_schema/0075_surface_event_log.sql`, writer `db.append_surface_event_log`) stores `track_id`,
`departure_airport` and `destination_airport` per event, append-only, for KDCA/KIAD/KBWI and the events
spotout/runwayin/runwayout/off/on/spotin, pruned at 120 days by `retention_prune`; ~~320 rows live on 10-15~~ (superseded 2026-10-25: ~~41,397~~ 47,009 rows at 2026-10-26 21:58Z). The
collector's TBFM lookup is bounded to the arrival window.)

## Decided 2026-10-13 (operator)

- **Tail changes are strict, based on SWIM.** A change is accepted when FDPS, TBFM or TFMS shows the
  candidate tail on the watched flight (`SWIM_TAIL_SOURCES`), provided the tail is not filed on another
  overlapping flight (rule b). The previous-leg rule (c) is not required for SWIM evidence; ~~it still
  applies to a FIDS-only change.~~ (corrected 2026-10-15: since 2026-10-14 FIDS is not a tail source at
  all, so no FIDS-only change exists and (c) has no remaining case.) There is no "unverified inbound"
  acceptance. ~~Today only the FDPS path passes a tail;~~ (corrected 2026-10-15: the FDPS repoint and the
  TDES check, which counts as SWIM since 2026-10-14, pass a tail;) TBFM/TFMS count as soon as a caller
  supplies one.
- Code text still out of step (2026-10-15, reported, not changed here): ~~the guard's not-authoritative
  rejection text (flight_instance.py:1031-1032) still offers "or the destination FIDS tail", the comment at
  flight_instance.py:919 says "A FIDS-only change still needs (c)",~~ (superseded 2026-10-25: both strings are gone
  from `flight_instance.py`; its remaining FIDS mentions, `:1017-1019`, say FIDS is NOT a tail source) and the
  ~~`_fdps_tail_change_decision` docstring (main.py:1501-1506) still says an outside-DC watch is rejected by rule (c)
  (re-checked 2026-10-25: the docstring still mentions FIDS and rule (c)).~~ (corrected 2026-10-26: the function is at `poller/main.py:1470`; its docstring now says rule (c) does not apply to SWIM sources.)

