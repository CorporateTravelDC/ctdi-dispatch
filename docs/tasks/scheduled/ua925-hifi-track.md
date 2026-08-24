---
name: flight-hifi-track
description: High-fidelity 2-min ADS-B/ADS-C position tracker — any international, overwater, or demo flight
converted_from: ua925-hifi-track (UA925-specific)
converted_to_skill: flight-hifi-track
---

FLIGHT HIGH-FIDELITY POSITION TRACKER

[Re-verified 2026-08-23] Both external calls below still work as written:
`GET https://api.airplanes.live/v2/hex/<hex>` and `/v2/callsign/<callsign>`
each return 200 with the documented `{"ac":[...]}` shape (empty `ac` when the
aircraft is not currently on feed — the "not found on feed" case, not an
error). `/v2/reg/` is NOT used here and should not be substituted: it is the
flaky endpoint the platform's own identity-resolution chain deliberately
falls back to last. The `POST /admin/push-test-alert` call is still live but
is a **legacy alias** for `/admin/push-alert` and requires an **admin**-tier
token (a cert/T1 token 403s); it also 400s on a `message` over 200 chars —
see the endpoint note in this directory's README.md.

Use this task for:
- International or overwater flights where continuous 2-min position telemetry is needed
- ADS-C satellite-tracked flights inbound from oceanic airspace
- Demo tracking of any specific inbound aircraft
- Any flight where signal type (ADS-B vs ADS-C vs MLAT) needs to be logged alongside position

CONFIGURE BEFORE ACTIVATING — set these three values for the target flight:
  TARGET_HEX      = "<6-char ICAO hex>"   # e.g. aa9d6e
  TARGET_CALLSIGN = "<ICAO callsign>"     # e.g. UAL925
  TARGET_LABEL    = "<friendly label>"    # e.g. UA925 or N783UA

DESIGN NOTE — HEX ID vs CALLSIGN:
Always query by ICAO hex rather than callsign. This bypasses two problems:
1. Callsign endpoint caching — airplanes.live and similar aggregators cache callsign lookups;
   hex lookups go directly to the raw ADS-B record and return live data.
2. Privacy filters — most tail-number/callsign privacy blocks (FlightAware, FlightRadar24,
   etc.) operate on registration or callsign. Unfiltered aggregators (airplanes.live) expose
   the hex ID regardless, so hex queries return real-time position even when the callsign
   query would be suppressed or stale. In ~90% of privacy-filtered cases, hex bypasses
   the block entirely. This applies to any aircraft, not just specific flights.

Poll every 2 minutes regardless of signal type (ADS-B or ADS-C). Always fire ntfy.

STEP 1 — Fetch via hex (primary, avoids callsign cache and privacy filters):
GET https://api.airplanes.live/v2/hex/<TARGET_HEX>

If empty, fallback:
GET https://api.airplanes.live/v2/callsign/<TARGET_CALLSIGN>

Extract: lat, lon, alt_baro, alt_geom, gs, track, true_heading, rssi, seen, type (adsb_icao/adsc/mlat), squawk, hex, r.

STEP 2 — Always fire position alert regardless of signal type or altitude:
POST http://100.x.x.x:8000/admin/push-test-alert
Authorization: Bearer REDACTED_DISPATCH_TOKEN_2
Content-Type: application/json
Body: {"message": "<TARGET_LABEL> HIFI: [lat]N [lon]W [alt_baro]ft [gs]kts hdg[track] type:[type] seen:[seen]s"}

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
  Alert body: {"message": "<TARGET_LABEL> not on feed — landed or feed gap"}
  Report: "Not found on feed."
