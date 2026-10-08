# Airline time fidelity — research spec and rolling monitor (opened 2026-10-08)

Status: **rolling monitor, running indefinitely** (operator, 2026-10-08), rebuilt like the 30-day facility disruption baseline. Monday 2026-10-12 is the first review checkpoint, not an end date. Nothing here alerts, and nothing writes to the database.

**Publication rule (operator, 2026-10-08).** This public document and the public rolling output carry **anchor-carrier aggregates only**: the US big three plus major European and Gulf carriers. The marketing-vs-operating (codeshare) breakdown, regional operators flying under a brand, watched flights and aircraft, and the bellwether flights live in a **private ledger sibling** (`docs/research/private/`, dropped from the public mirror). The data exists and the numbers are real; the attribution detail stays private.

## 1. Question

The operator's hypothesis: airlines, especially at Dulles, may be shaping reported airborne and on-time times to protect slots.

The research question is narrower and testable: **do the OOOI times airlines report (as carried by FAA TFMS) agree with what FAA systems observe, and where they disagree, how, for whom, and how consistently?** Disagreement is measured. Intent is not inferred from data.

Bellwethers: two operator-chosen commercial flights into Dulles on which the reported takeoff time equalled the filed time to the minute every day for two weeks, while landing and gate-in times varied normally (identities in the private ledger).

## 2. Sources

| Source | Table | What it gives | Coverage |
|---|---|---|---|
| Airline-reported OOOI (TFMS `flightTimeData`) | `flight_ooooi_times` | OUT, OFF, ON, IN as reported; filed departure/arrival | NAS-wide, **effectively from 2026-09-23** |
| FDPS (ERAM) | `flight_events.raw_json` | departure/arrival runway times, positions | NAS-wide from 2026-09-08; latest message per flight |
| ASDE-X surface (STDDS SMES) | `surface_movement_events` | ramp exit, takeoff, landing, ramp entry, runway entries/exits | 43 airports; about 4,095 rows kept per airport (DC airports back to 09-08, hubs about two weeks); sampled |
| TDES (STDDS) | `tdes_departure_events` | clearance delivery time, parking gate | 70 towers, since 08-30 |
| TBFM | `tbfm_sequences` | meter-fix ETAs | TBFM facilities |
| Our watches | `watchlist_entries`, `watchlist_history` | OOOI lock and authority decision for watched flights | watched flights |

## 3. Comparisons (reported − observed, minutes)

| Comparison | Expected when accurate |
|---|---|
| OFF vs surface takeoff | about 0 (validated: −0.6 median for most carriers) |
| ON vs surface landing | about 0 |
| OUT vs ramp exit | negative |
| IN vs ramp entry | positive |
| OFF vs FDPS departure runway actual | to be characterised (§5 q2) |
| "Frozen" | OFF exactly the filed departure; ON exactly the filed arrival |
| Taxi | reported OFF − OUT vs observed takeoff − ramp exit; observed ramp entry − landing |
| Arrival lateness | ON more than 15 minutes after the filed arrival |

## 4. Anchor baseline (2026-09-23 → 2026-10-08)

| Anchor | OFF = filed time | OFF within 1.5 min of observed takeoff (not frozen / frozen) | ON vs observed landing |
|---|---|---|---|
| American | 10.3 % | 99 % / 65 % | within about a minute |
| Delta | 10.2 % | 100 % / 82 % | within about a minute |
| United | **77.5 %** | **8 % / 12 %** (median error 6–9 min) | within about a minute |

United's reported takeoff time is usually the filed time rather than the observed takeoff; American and Delta report takeoff to within about half a minute. Landing, gate-in and pushback show no United-specific difference. European and Gulf anchors are in scope; their coverage in TFMS airline OOOI is to be measured.

## 5. Open questions (first checkpoint Monday)

1. Does a frozen takeoff time accompany a schedule-shaped pushback time (fixed OUT-to-OFF gap)? On-time departure statistics use pushback, not takeoff.
2. Where does the frozen value originate: the airline's message, or TFMS/ERAM filling it from the filed time? Compare with the FDPS departure actual and the first airborne position for DC departures.
3. Dulles vs Reagan vs BWI vs the hubs.
4. Taxi: reported vs observed taxi-out, by airport and hour.
5. Brand vs operator for regional carriers (private ledger).
6. Stability over the rolling windows; early-warning signs (route and hour patterns that precede lateness).

## 6. Monitor

- **Collector** (`scripts/research/airline_time_fidelity_collect.py`, read-only, inside the web container) via `scripts/research/airline-time-fidelity-collect.sh`, timer `corporatetraveldc-airline-time-fidelity.timer` at 00:20/06:20/12:20/18:20 UTC, 8-hour lookback.
- **Scope:** every flight to or from KDCA, KIAD or KBWI; every flight watched in the last 14 days (by callsign or tail) and every route those flights fly, all carriers; the shuttle pairs BOS↔DCA and LGA↔DCA; the private bellwethers.
- **Raw snapshots** are kept, not rolled off: they are the only lasting copy of the surface events.
- **Rolling windows** (`scripts/research/airline_time_fidelity_rollup.py`), rebuilt every run: 30, 60 and 90 days. Private ledger output: by operating carrier, brand, route, route × carrier, weekday, hour. Public output: by anchor and by anchor × DC airport only.
- Output under `/var/lib/corporatetraveldc/research/airline-time-fidelity/` (raw `<date>.jsonl`, `rolling/`).

## 7. Limits

- Surface data is sampled and capped per airport; absence of an event is not evidence.
- FDPS keeps the latest message per flight, not the track.
- Origins outside ASDE-X coverage have no independent observation.
- Callsign-and-airport matching within ±3–4 h can mis-pair a same-callsign second leg; Monday's analysis drops ambiguous pairs.
- Four to six weeks of data cannot show seasonality; the rolling windows exist so that it can later.
- Data fidelity only: nothing here establishes intent or is an accusation.

## Superseded (kept for the record)

Corrected 2026-10-08 ~22:45Z, the same evening the spec was first committed (`3366743`):

- <del>Status: research monitor running through Monday 2026-10-12; analysis and any alerting are decided then.</del> The monitor is rolling and runs indefinitely; Monday is a checkpoint.
- <del>Airline-reported OOOI: NAS-wide, since 2026-08-23.</del> Effectively from 2026-09-23 (four stray rows before). The baseline figures were computed on that data, so they stand; the date range was misstated.
- <del>Per-operator baseline tables including regional operators by brand, and the bellwether flight numbers, in this document.</del> Moved to the private ledger under the publication rule.
