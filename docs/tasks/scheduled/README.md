# Scheduled Tasks

These are Cowork-managed scheduled tasks that run against the dispatch platform.
Each file is a SKILL.md that Cowork executes on its cron schedule.

## Tasks

| Task | Schedule | Purpose |
|------|----------|---------|
| `ua925-adsb-track.md` | `*/2 * * * *` | ADS-C → ADS-B handoff tracker for UA925 (N783UA). Fires ntfy only on confirmed ground ADS-B acquisition (adsb_icao, seen<30s, RSSI present). Silent otherwise. |
| `ua925-hifi-track.md` | `*/2 * * * *` | High-fidelity 2-min position tracker. Fires ntfy every cycle regardless of signal type. Full telemetry for demo/ops use. |
| `nec-train-hifi-track.md` | `*/2 * * * *` | NEC corridor Acela + NE Regional train tracker to WAS. Polls dispatch Amtrak endpoint, fires ntfy every cycle. |

## Design Rationale — Hex ID / Primary Key Queries

All aircraft tasks query by **ICAO hex** (`/v2/hex/<hex>`) rather than callsign.
This bypasses two issues:

1. **Callsign caching** — aggregators like airplanes.live cache callsign lookups and may serve
   stale positions for minutes. Hex queries go to the raw ADS-B record directly.

2. **Privacy filters** — FlightAware, FlightRadar24, and similar services apply privacy blocks
   based on tail number or callsign. Unfiltered aggregators expose the hex ID regardless,
   so a hex query returns real-time position even when the callsign query is suppressed.
   This is effective in ~90% of privacy-filtered cases.

The same principle applies to trains: always query by **numeric train ID** (2155, 137) rather
than service name ("Acela"). Service names are marketing labels, not unique per departure.
Train number is the primary key in GTFS-RT and Amtrak's native APIs.
