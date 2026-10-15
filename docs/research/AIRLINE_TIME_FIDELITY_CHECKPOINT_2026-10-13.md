# Airline time fidelity — checkpoint 1 (data 2026-09-23 → 2026-10-13 UTC)

Rolling monitor, first review checkpoint. It was due Monday 2026-10-12 and was run on 2026-10-13. The figures cover **anchor carriers only, mainline-operated** (the operating carrier is the anchor). Sign convention: **reported − observed, in minutes, UTC.** This is about data fidelity only. Nothing here establishes intent.

## Method

- **Airline-posted times.** These are the OUT, OFF, ON and IN times the airline reports and FAA TFMS carries (`flight_ooooi_times`, the `flightTimeData` message). We kept flight instances whose filed departure falls between 2026-09-23 00:00Z and 2026-10-13 17:30Z, with status COMPLETED. A reported time counts only if it is not later than 17:30Z (TFMS also carries future scheduled times).
- **What we compared them with (SWIM).** "Observed" means ASDE-X surface events (STDDS SMES) at the 43 covered airports, about 4,095 rows kept per airport:
  - ramp exit, compared with OUT
  - takeoff, compared with OFF
  - landing, compared with ON
  - runway exit, a secondary ON check
  - ramp entry, compared with IN

  The FDPS (ERAM) runway actuals come from the monitor's own snapshots of DC-area and watched flights since 2026-10-08. ADS-B was considered for OFF only. Under the standing rule, ADS-B never asserts ON or IN, and SWIM outranks airline-posted times.
- **Pairing by flight instance, not by callsign.** An instance is: operating carrier + flight number + origin + destination + the UTC date of the filed departure. A surface event is used only if all of these hold:
  - it falls in that instance's time window;
  - its SMES route, where known, matches the instance;
  - it cannot also belong to another instance of the same flight number;
  - it is the only candidate.

  A ramp-exit-to-OFF pair must also be in the right order (0 < takeoff − ramp exit ≤ 90 min). The exact rule is in the private ledger and the method appendix.
- **Before / after.** The earlier collector paired on callsign alone, taking the nearest event within ±4 h. We report both the original and the re-paired numbers where they differ.

## Coverage

| | AAL | DAL | UAL |
|---|---|---|---|
| Completed instances, 09-23 → 10-13 | 60,069 | 63,187 | 57,333 |
| OFF paired with an ASDE-X takeoff | 4,624 | 4,865 | 4,513 |
| OUT paired with a ramp exit | 937 | 1,002 | 1,038 |
| ON paired with an ASDE-X landing | 470 | 542 * | 485 |
| ON paired with a runway exit | 2,090 | 2,132 * | 2,140 |
| IN paired with a ramp entry | 4,956 | 5,103 | 4,905 |

\* Delta ON figures use 09-30 → 10-13 only (see finding 4).

**European and Gulf anchors:** TFMS carries no airline OOOI times for them in this period or earlier. They appear in FDPS only, so there is nothing airline-posted to compare.

## Results by anchor and phase (median, interquartile range, share within ±1.5 min)

| Phase comparison | AAL | DAL | UAL |
|---|---|---|---|
| OUT − ramp exit (expected negative) | −8.4 [−10.6, −6.8] | −8.3 [−10.8, −6.6] | −7.7 [−12.8, −3.8] |
| **OFF − takeoff** (expected about 0) | **−0.6 [−0.9, −0.4], 99%** | **−0.6 [−0.8, −0.3], 100%** | **+2.6 [−4.4, +7.9], 12%** |
|   OFF − takeoff, OFF = filed time ("frozen") | −0.6, 98% (n=297) | −0.5, 99% (n=467) | +3.7 [−1.9, +8.5], 13% (n=3,363) |
|   OFF − takeoff, not frozen | −0.6, 99% | −0.6, 100% | −3.0 [−14.8, +4.9], 9% (n=1,150) |
| ON − landing (expected about 0) | −0.3 [−0.6, −0.1], 97% | −0.7 [−2.4, +0.7], 50% * | −0.2 [−1.1, +0.4], 69% |
| ON − runway exit (expected about −1) | −1.4 [−3.1, −1.0] | −2.3 [−4.2, −0.7] * | −1.8 [−4.3, −0.8] |
| IN − ramp entry (expected positive) | +2.7 [+0.8, +4.3] | +1.7 [−0.5, +3.3] | +1.6 [0.0, +3.0] |
| Share of flights with OFF = filed departure | 6.3% | 8.0% | **75.8%** |
| Reported taxi-out (OFF − OUT) | 17 [14, 22] | 15 [12, 20] | 20 [16, 24] |
| ON − filed arrival / share more than 15 min late | −7.0 / 4.8% | −2.0 / 3.8% * | −10.0 / 4.3% |

TFMS times are whole minutes and ASDE-X times are sub-second. A median of about −0.5 is what truncating to the minute alone would produce.

## Ramp exit → OFF (observed taxi from ramp exit to takeoff), original and re-paired

| | AAL | DAL | UAL |
|---|---|---|---|
| Original (collector's 30-day rollup, DC area, callsign-only) | **122.3 min (n=1)** | 9.5 (n=2) | 23.4 (n=13) |
| Callsign-only, nationwide, same data as below | 13 pairs | 8 pairs | 33 pairs (90th percentile 217 min) |
| **Re-paired: instance + sequence rule, nationwide** | **17.1 [12.5, 23.7] (n=12)** | **16.3 [9.3, 29.6] (n=6)** | **20.5 [15.2, 27.3] (n=26)** |
| Pairs rejected by the instance key or the sequence rule | 1 | 2 | 7 |

The American 122-min figure was one event: a ramp exit at the original schedule, then a takeoff after a later second pushback. It was a real surface event measured as the wrong thing, and the sequence rule removes it.

These samples are too small to publish as carrier statistics. The surface table keeps only each track's latest event, so a ramp exit survives only when the ASDE-X track broke before takeoff. That also biases these samples toward irregular taxis.

## Dulles vs Reagan vs BWI (anchor mainline; departures for OFF and OUT, arrivals for ON and IN)

| | OFF = filed | OFF − takeoff (within 1.5) | ON − runway exit | IN − ramp entry | Late >15 |
|---|---|---|---|---|---|
| AAL DCA | 6.8% | −0.4 (99%, n=328) | −1.1 (n=103) | +2.0 (n=308) | 2.8% |
| AAL IAD | 13.2% | −0.7 (100%, n=28) | −1.1 (n=5) | +2.4 (n=23) | 2.5% |
| AAL BWI | 7.1% | −0.6 (100%, n=56) | −1.4 (n=24) | +1.6 (n=67) | 3.5% |
| DAL DCA | 11.8% | −0.4 (100%, n=143) | −1.7 (n=48) | −0.2 (n=143) | 3.1% |
| DAL IAD | 13.7% | −0.7 (96%, n=46) | −2.6 (n=22) | +0.6 (n=46) | 1.0% |
| DAL BWI | 11.1% | −0.3 (100%, n=88) | −3.0 (n=39) | 0.0 (n=80) | 0.7% |
| UAL DCA | 36.6% | −0.9 (13%, n=85) | −1.0 (n=37) | −0.2 (n=91) | 7.4% |
| UAL IAD | **74.2%** | −0.6 (11%, n=552) | −0.8 (n=204) | +2.1 (n=635) | 1.3% |
| UAL BWI | 59.1% | −0.8 (22%, n=59) | −1.2 (n=26) | +0.8 (n=67) | 0.4% |

United's reported taxi-out at BWI is 11–13 min for 80% of flights (10th–90th percentile). At DCA it is 13–19 min.

## Findings

1. **American and Delta report takeoff accurately.** OFF is within 1.5 min of the ASDE-X takeoff on 99–100% of flights. When their OFF equals the filed time (6–8% of flights), the takeoff really did happen in that minute: observed takeoff − filed is +0.5 to +0.6 min, interquartile range under 1 min. It is a coincidence of the minute, not a frozen value.
2. **United's reported OFF is not a takeoff observation.** This holds whether or not it equals the filed time:
   - Only 12% of United OFF times are within 1.5 min of the observed takeoff.
   - United's reported OUT→OFF gap equals that flight's own usual gap (to within 1 min) on **99%** of flights, frozen or not. For American and Delta the figure is 35–36%. United's OFF behaves like OUT plus a fixed taxi allowance per flight.
   - In the DC-area snapshots, United's OUT and OFF were already present before departure (status PLANNED). They stayed unchanged through completion in 232 of 256 such flights. American's OFF changed after the first snapshot in 45% of flights seen before completion; United's changed in about 3%.
3. **Where a frozen United takeoff time originates (spec question 2).** On the airline side, not in FAA processing:
   - On frozen United flights, the ERAM (FDPS) departure actual equals the filed minute only 4% of the time.
   - The ERAM actual matches the ASDE-X takeoff within 1.5 min 99% of the time (median −0.4).

   The frozen value does not hide delays in most cases. 67% of frozen United flights took off **before** the filed time (median 3.7 min early). 2.2% took off more than 15 min after it.
4. **Arrival side: no anchor posts ON or IN early in a systematic way.**
   - ON is within about a minute of the landing for American (97% within 1.5) and United (69%).
   - Delta is centred close to zero but looser (50% within 1.5).
   - IN follows ramp entry by 1.6–2.7 min, as expected.
   - One caveat for Delta: until 2026-09-28 its reported ON equalled the filed arrival on about 60% of flights. From 2026-09-30 that dropped to about 5% and stayed there. The cause is not determined, and Delta ON figures here use 09-30 onward.
5. **Pairing.** Callsign-only pairing contaminates arrival-side runway-exit comparisons on about 3–4% of pairs, and departure-side comparisons much less. The contamination comes from the same flight number flying both directions in one day: 15% of American and 22% of Delta flight-number-days have more than one instance, almost all of them out-and-back pairs. The medians above are re-paired. The headline OFF and IN medians barely move. Only the tiny ramp-exit-to-OFF samples changed materially.
6. **Stability over time.** Week by week (09-23 to 09-29, 09-30 to 10-06, 10-07 to 10-13) every anchor's OFF accuracy and frozen share is stable. For United: frozen share 75.5% / 75.7% / 76.3%, and 11–12% of OFF times within 1.5 min.

## Caveats

- The surface table is a slot map (one row per ASDE-X track number) and is sampled. A missing event is not evidence. Landings and ramp exits survive only when a track breaks, so the ON − landing and ramp-exit samples are small and not random. Runway exit is the main check on ON.
- FDPS comparisons cover only DC-area and watched flights snapshotted since 2026-10-08, and keep the latest message per flight.
- Origins outside ASDE-X coverage have no independent observation.
- FIDS (airline/airport display) times are not kept as history, so they could not be compared.
- ADS-B first-airborne times are not kept as history (only 4 watch-level OFF events), so ADS-B is not used for OFF here.
- TBFM runway ETAs are stored by callsign with no route. Without an instance key they mix legs, so they are not reported.
- Three weeks of data cannot show seasonality.

## Next steps

1. Make the collector record SMES `track_id`, `departure_airport` and `destination_airport`. Then apply the instance key and the sequence rule (shared with the code-level instance resolver) in the collector and rollup. Time-bound and instance-key the TBFM lookup.
2. Capture ramp exits before they are overwritten: snapshot more often for DCA/IAD/BWI, or keep an append-only surface event log. Without this, ramp-exit-to-OFF stays anecdotal.
3. Find out why Delta's ON = filed share changed on 2026-09-29.
4. Taxi analysis by hour (spec question 4) once ramp-exit samples allow it.
5. Next checkpoint after two more weeks of collection.

**Status 2026-10-14.** Steps 1 and 2 are done.
- The collector pairs surface events by INSTANCE-v1 (`common.flight_instance.v1_pair_instance`, shared with the code resolver) and records `track_id` and the route for every event.
- An append-only `surface_event_log` (DCA/IAD/BWI, `pg_schema/0075`, 120-day retention) keeps the ramp exits the slot table overwrites.
- The TBFM lookup is bounded to the instance's arrival window.
- Rows carry `pairing_rule` and per-kind rejection reasons. The rollup counts `instance_v1_flights`, so the windows show how much of the data uses the new rule.

Step 3 (Delta ON = filed, 2026-09-29) and step 4 (taxi by hour) wait for the next checkpoint (~2026-10-27).
