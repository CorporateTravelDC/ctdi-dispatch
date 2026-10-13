# Flight-instance resolver

2026-10-13. Code: `src/common/flight_instance.py`. Operator requests 2026-10-11/12.

## Why

A flight number is not a flight. These cases are confirmed live:

- **Shuttles.** One number flies several times a day.
- **Both directions.** AA1893 flies DCA->FLL in the morning and FLL->DCA in the afternoon. The TFMS/TBFM watch matchers compared callsigns only, so the afternoon leg's messages advanced a watch on the morning leg.
- **Regional operators.** UAL4520 flew as GJS4520. On the same night RPA4520 flew a different route, and `codeshare_map` held both rows.

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
| `flight_events` (FDPS) | hex + registration (from the row's own `<flight>` element), runway ETA, status | DC-area only. `departure_time` is written as NULL today, so an FDPS-only instance usually has no date. |
| `codeshare_map` | marketing -> operating, per route | |
| FAA / OpenSky registries | tail <-> hex | `registration_for_hex`, `hex_for_registration` |

`resolve(identifier, origin, destination, sched_date, hex_id=)` returns the candidate instances. Rows with the same GUFI from TFMS and FDPS are merged before filtering, so an undated FDPS row inherits its TFMS twin's date. A row is dropped only when its route, date or hex positively contradicts the query.

`instances_for_hex(hex)` returns every FDPS filing whose own `<flight>` element carries the given hex. `raw_json` holds batches, so a `LIKE` match is only a prefilter and each hit is re-parsed. This function runs on a tail-change event, never once per tick.

## Rules

### 1. Watch matching (TFMS, TBFM): wired

`match_watch_entry(entries, identifier, origin=, destination=, hex_id=, sched_dep=)` returns the first watch that meets both conditions:

- **It answers to the identifier.** The match can be exact, by ICAO normalization (`AA1893` = `AAL1893`), through a route-filtered `codeshare_map` operating callsign, or by hex.
- **`leg_rejection` raises no objection:**
  - `origin_mismatch` / `cross_direction`: the message's origin positively disagrees with the watch's origin.
  - `other_time_of_day`: both scheduled departures are known and are more than 3 h apart (`WATCH_SCHED_TOLERANCE_H`).
  - `destination_mismatch`: the destination positively disagrees. There is one exception, for diversions. If the origin agrees and both scheduled departures are known and agree, a different destination counts as this leg diverting, and the message still matches. Without that time proof it does not match.

Messages that carry no airports still match on identity, as before.

Wired call sites:

- `tfms_parser._match_watchlist_flight`. Every fltdMessage handler passes `_leg_context(...)`: the `depArpt`/`arrArpt` attributes, with qualifiedAircraftId's departurePoint/arrivalPoint as fallback, plus `igtd` as the scheduled departure.
  - `TMI_FLIGHT_LIST` reads the `flight` element's departurePoint, arrivalPoint and igtd.
  - `TMI_UPDATE` reads the two end airports of the amended route string.
- `tbfm_parser._match_watchlist_flight` reads `<air dap>` for the departure and `apt` for the metered arrival airport. TBFM carries no scheduled time, so the same number flown in the same direction twice in one day cannot be told apart here.

The codeshare aliases are cached for 5 minutes per (identifier, route), so the national TFMS stream costs one dict lookup per message. The watch list itself is still read on every call, as it was before.

### 2. Tail-change guard: wired into the FDPS repoint

`decide_tail_change(...)` is a pure function. It accepts a change only when all three conditions hold:

- **(a) Authoritative source.** The source must be `fids_destination` (the destination airport's FIDS tail for this leg) or `fdps` (the FAA flight plan). A third-party tracker such as airplanes.live, OpenSky or AeroAPI can add corroboration but is never enough on its own.
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

**Behaviour change to expect.** `flight_events` is DC-area only. For a watch whose origin is outside DC (an inbound to DCA, for example), the new tail's previous leg is usually not on file, so (c) rejects an FDPS tail change that would previously have been applied. The watch keeps its old hex and the operator gets the NOT-accepted note. See Open questions.

**Not wired: FIDS tail.** No FIDS tail reaches watches today. `_check_flight_fids` uses only the gate and baggage fields. The natural wiring point is `_check_flight_fids` when `airport == destination`: look up `hex_for_registration(result["tail"])`, then call `decide_tail_change(sources=("fids_destination",), ...)`. It is not wired yet because `lookup_arrival` is keyed by flight number at the airport. A shuttle with two arrivals at DCA on the same day needs its arrival row tied to the instance first, using the scheduled time on the FIDS row.

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

### 4. Inbound-aircraft tracker ("where's my inbound"): implemented, not wired

`assess_inbound(watched, inbound, min_turn_min=, aircraft_class=)` is a pure function:

- **Inbound ETA** at the watched origin: actual arrival if known, then estimated, then scheduled.
- **Ready time:** ETA + minimum turn.
- **Propagated departure:** max(watched departure estimate or schedule, ready time).
- **Slack:** minutes to spare. A negative value means late.
- **Status:** one of `on_time`, `late_beyond_turn`, `at_origin`, `diverted`, `cancelled`, `unknown`.
- **Flags:** `inbound_late_beyond_turn_buffer`, `inbound_diverted`, `inbound_cancelled`, `inbound_unknown`, `inbound_eta_unknown`.

`inbound` is the assigned tail's current instance. Get it with `previous_leg(instances_for_hex(watch.hex_id), watched)`, or with `resolve()` on the inbound's flight number.

Minimum turn defaults are 35 min for narrowbody, 30 for regional and 75 for widebody. Override them with `CTDC_MIN_TURN_MIN_<CLASS>`, which is clamped to 10-240.

**Wiring point (follow-up):** a per-tick step in the poller's flight-watch loop, next to `_check_flight_fdps_cache`, for watches that have a hex and have not departed yet. It would push once per status transition, with the status in the dedup content hash, so it does not repeat.

## Standing rules respected

- OOOI authority: nothing here writes a phase. The tracker only reads airline/SWIM times. Any future OOOI writer must use `db.update_watchlist_oooi_phase_authoritative`. FIDS remains enrichment-only. A FIDS tail is valid input to the guard because it is identity, not an OOOI phase.
- Tests use only synthetic identities: hex `000001`/`000002`, tails `N000TS`/`N001TS`, flights `ZZZ…`, plus the operator's own example flight numbers.

## Also fixed in this pass: POST /api/v1/watchlist/flights stored registration=None

Seen on `wl-flight-ual4520-20261011`. The only thing that ever set `registration` was the caller's own field, so three common add patterns lost it:

1. **Hex-only add.** The caller had the ADS-B hex but not the tail, and the tail was never reverse-resolved.
2. **Body with `"tail"`.** That is FIDS's name for the field, and pydantic silently ignored the extra key.
3. **No identity in the body.** The FAA plan found through `codeshare_map` (GJS4520) carried both the hex and the tail, and both were discarded.

Fixes:

- `tail` is accepted as an alias of `registration`, on both the single and batch adds.
- A missing tail is reverse-resolved from the hex: first from the FAA plan when its hex matches, then from the registries.
- When the caller supplies no identity and the plan's origin does not contradict the request, the hex and tail are taken from the FAA plan, recorded with `hex_source="fdps"`.
- A value supplied by the caller is never overwritten.

The live entry had already expired before this was investigated, and the request body was not available to read. So which of the three paths hit UAL4520 is inferred, not observed. All three are fixed.

## What remains

- **SMES matcher.** `smes_parser._match_watchlist_flight` is still callsign-only. SMES sees one airport per message, which is both legs' endpoint for a shuttle, so it needs the scheduled time (or the watch's phase) to separate them.
- **Other identifier-only lookups.** `fdps_parser.check_fdps_watchlist` and the TFMS FADT slot loop (its own `watchlist_by_ident` prefetch) still match on the identifier alone. The FADT loop is storage plus a hit and does not advance OOOI.
- **FIDS destination tail into the guard.** See section 2.
- **Inbound tracker.** Wire it into pushes (see section 4) and pick the airframe class from `aircraft_type`.
- **FDPS scheduled times.** `flight_events.departure_time` is never written. Parsing a confirmed FIXM departure-time field from a captured sample would give FDPS-only instances a date. Don't guess the schema.

## Open questions

1. Rule (c) on watches whose origin is outside DC FDPS coverage. Should a lone FAA-plan tail change be rejected, as it is now, or accepted with an "unverified inbound" note?
2. Is the 3 h `WATCH_SCHED_TOLERANCE_H` right for shuttle numbers? The hourly DCA-LGA/BOS shuttles use distinct numbers today, and this tolerance only matters when a number repeats in the same direction.
3. Should rule (c) allow a late inbound? `late_tolerance_min` is 0 now, so a tail whose inbound is running late is rejected even though airlines usually just delay the outbound.

## Pairing rule for time-fidelity: INSTANCE-v1 (agreed 2026-10-13)

The 2026-10-13 time-fidelity checkpoint (`docs/research/AIRLINE_TIME_FIDELITY_CHECKPOINT_2026-10-13.md`;
full rule, SQL and rejection counts in `docs/research/private/`) paired surface events with airline-posted
times by **instance key plus a uniqueness and sequence rule**. The instance key alone was not enough: the
collector's 122-min American ramp-exit-to-OFF median was a single same-instance pair (push at the original
schedule, return to gate, second push two hours later), which only the sequence rule rejects.

`pair_events()` above pairs instance records with each other and takes the **nearest** same-instance
candidate. When the collector is moved onto this module (checkpoint next step 1), surface-event pairing must
follow INSTANCE-v1 instead:
- event side and airport: departure events at the origin, arrival events at the destination;
- window: [filed - 2 h, filed + 6 h] on the relevant side;
- route: a SMES event that carries airports must match the instance exactly;
- siblings: an event that also fits another instance of the same number is rejected, never assigned;
- **exactly one** surviving candidate, or no pair (never "nearest to the reported time", which biases toward
  agreement);
- sequence: 0 < takeoff - ramp exit <= 90 min; 0 < ramp entry - runway exit <= 60 min.

The collector also needs `track_id`, `departure_airport` and `destination_airport` stored per surface event and
a time-bounded, instance-keyed TBFM lookup.
