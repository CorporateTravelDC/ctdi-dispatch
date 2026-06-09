---
name: ua925-hifi-track
description: UA925 high-fidelity 2-min ADS-B/ADS-C position track — demo and ops use
---

HIGH-FIDELITY POSITION TRACKER — UA925 (N783UA, ICAO aa9d6e)
Trigger keywords: high-fidelity, hifi, demo, demo track, high fidelity track

Poll every 2 minutes regardless of signal type (ADS-B or ADS-C). Report full position snapshot and always fire an ntfy alert with current state.

DESIGN NOTE — HEX ID vs CALLSIGN:
Querying by ICAO hex (aa9d6e) rather than callsign (UAL925) bypasses two problems:
1. Callsign endpoint caching — airplanes.live and similar aggregators cache callsign lookups;
   hex lookups go directly to the raw ADS-B record and return live data.
2. Privacy filters — most tail-number/callsign privacy blocks (FlightAware, FlightRadar24,
   etc.) operate on registration or callsign. Unfiltered aggregators (airplanes.live) expose
   the hex ID regardless, so hex queries return real-time position even when the callsign
   query would be suppressed or stale. This is the same reason hex is preferred for any
   aircraft where privacy filtering or feed caching is suspected.

STEP 1 — Fetch via hex (primary, avoids callsign cache and privacy filters):
GET https://api.airplanes.live/v2/hex/aa9d6e

If empty, fallback:
GET https://api.airplanes.live/v2/callsign/UAL925

Extract: lat, lon, alt_baro, alt_geom, gs, track, true_heading, rssi, seen, type (adsb_icao/adsc/mlat), squawk, hex, r.

STEP 2 — Always fire position alert regardless of signal type or altitude:
POST http://100.94.80.100:8000/admin/push-test-alert
Authorization: Bearer ctdc_cowork_5NC2G5DLI8CONLZCFWO5TLM5CEABD7OQ
Content-Type: application/json
Body: {"message": "UA925 HIFI: [lat]N [lon]W [alt_baro]ft [gs]kts hdg[track] type:[type] seen:[seen]s"}

STEP 3 — Report full snapshot:
- Registration, hex, signal type
- Lat/lon (6 decimal places)
- Altitude baro + geometric
- Ground speed, track, true heading
- RSSI (if present), seen seconds
- Squawk
- Derived: approximate phase (cruise/descent/approach/ground) based on altitude and speed
- If seen > 60s: flag as STALE CACHE

IF ac array empty:
  Alert body: {"message": "UA925 HIFI: N783UA not on feed — landed IAD or feed gap"}
  Report: "Not found on feed."