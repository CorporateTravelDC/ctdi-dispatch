---
name: ua925-adsb-track
description: UA925 ADS-C to ADS-B handoff tracker — monitors satellite→ground ADS-B acquisition for N783UA
---

ADS-C TO ADS-B HANDOFF TRACKER — UA925 (N783UA, ICAO aa9d6e, UAL925)

This task tracks the transition from satellite ADS-C coverage to ground-based ADS-B reception.

DESIGN NOTE — HEX ID vs CALLSIGN:
Querying by ICAO hex (aa9d6e) rather than callsign (UAL925) bypasses two problems:
1. Callsign endpoint caching — airplanes.live and similar aggregators cache callsign lookups;
   hex lookups go directly to the raw ADS-B record and return live data.
2. Privacy filters — most tail-number/callsign privacy blocks (FlightAware, FlightRadar24,
   etc.) operate on registration or callsign. Unfiltered aggregators (airplanes.live) expose
   the hex ID regardless, so hex queries return real-time position even when the callsign
   query would be suppressed or stale. This is the same reason hex is preferred for any
   aircraft where privacy filtering or feed caching is suspected.

STEP 1 — Fetch position via ICAO hex (bypasses callsign cache and privacy filters):
GET https://api.airplanes.live/v2/hex/aa9d6e

If that returns empty, also try:
GET https://api.airplanes.live/v2/callsign/UAL925

Extract from first "ac" entry: lat, lon, alt_baro, gs, track, seen, rssi, type (adsb_icao vs adsc or mlat).

STEP 2 — Determine signal type:
- "adsb_icao" = ground-based ADS-B receiver (target state)
- "adsc" or "mlat" or absent = satellite/multilateration (not yet ground ADS-B)
- seen > 60 seconds = stale/cached data — note this explicitly

STEP 3 — Decision:
IF type is "adsb_icao" AND seen < 30 AND rssi is present (ground receiver confirmed):
  POST http://100.94.80.100:8000/admin/push-test-alert
  Authorization: Bearer ctdc_cowork_5NC2G5DLI8CONLZCFWO5TLM5CEABD7OQ
  Content-Type: application/json
  Body: {"message": "UA925 ADS-B acquired (ground): [lat]N [lon]W [alt_baro]ft [gs]kts RSSI:[rssi]dBm"}
  Report: "ADS-B GROUND ACQUIRED — alert fired."

IF type is NOT "adsb_icao" OR seen >= 30:
  Do not fire alert.
  Report: "Still ADS-C/MLAT or stale. type=[type] seen=[seen]s lat=[lat] lon=[lon] alt=[alt_baro]ft"

IF ac array empty:
  POST alert with body {"message": "UA925 N783UA off ADS-B — landed IAD or feed lost"}
  Report: "UA925 not found — landed or feed lost."