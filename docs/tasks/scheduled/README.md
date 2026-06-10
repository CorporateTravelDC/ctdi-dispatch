# Scheduled Tasks → Skills

These tasks were originally Cowork scheduled tasks (2-min cron) and have been converted to
on-demand **skills**. The scheduled tasks are now disabled; invoke the skills by trigger phrase.

## Skills

| Skill | Trigger phrases | Purpose |
|-------|----------------|---------|
| `overwater-adsb-handoff-track` | overwater track, adsb handoff, adsc to adsb | ADS-C → ADS-B handoff tracker. Fires ntfy only on confirmed ground ADS-B acquisition. Silent while still on satellite. |
| `flight-hifi-track` | hifi, high-fidelity, demo track, overwater track | Continuous 2-min position telemetry. Fires ntfy every cycle regardless of signal type. |
| `nec-train-hifi-track` | train track, nec track, acela track, hifi trains | NEC corridor Acela + NE Regional tracker to WAS. Polls dispatch Amtrak endpoint. |

## Design Rationale — Hex ID / Primary Key Queries

### Aircraft — ICAO Hex vs Callsign

All aircraft tasks query by **ICAO hex** (`/v2/hex/<hex>`) rather than callsign.
This bypasses two issues:

1. **Callsign caching** — aggregators like airplanes.live cache callsign lookups and may serve
   stale positions for minutes. Hex queries go to the raw ADS-B record directly.

2. **Privacy filters** — FlightAware, FlightRadar24, and similar services apply privacy blocks
   based on tail number or callsign. Unfiltered aggregators (airplanes.live) expose the hex ID
   regardless, so a hex query returns real-time position even when the callsign query is
   suppressed or stale. This is effective in ~90% of privacy-filtered cases. The operator
   confirmed: "It bypasses privacy blocks because most privacies only do the tail number, not
   the hex. In 90% of the cases, it bypasses the caching."

### Trains — Numeric Train ID vs Service Name

Always query by **numeric train ID** (2155, 137) rather than service name ("Acela").

1. Service names are not unique — "Acela" refers to dozens of departures; "2155" is one
   specific departure. GTFS-RT and Amtrak's native APIs use numeric train ID as primary key.

2. Feed caching — aggregators may cache service-name lookups the same way aircraft aggregators
   cache callsign lookups.

3. **Consist / engine number as fallback** (train equivalent of ICAO hex): if the numeric
   Amtrak train ID is not resolving or returning stale data, and the physical consist number
   or locomotive road number for that train is known (e.g. from yard sheets or crew manifest),
   use that as the primary identifier instead. Consist numbers are immutable hardware
   identifiers — they do not change with schedule, route renaming, or equipment substitutions.
   This is the train-side equivalent of switching from callsign to ICAO hex: it bypasses
   scheduling-layer aliasing and goes to the physical asset record.

## Task Files (archived)

| File | Formerly | Now |
|------|----------|-----|
| `ua925-adsb-track.md` | Scheduled task, UA925-specific | Generic template, converted to skill `overwater-adsb-handoff-track` |
| `ua925-hifi-track.md` | Scheduled task, UA925-specific | Generic template, converted to skill `flight-hifi-track` |
| `nec-train-hifi-track.md` | Scheduled task | Converted to skill `nec-train-hifi-track` |
