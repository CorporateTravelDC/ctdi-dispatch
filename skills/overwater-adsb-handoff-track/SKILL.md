---
name: "overwater-adsb-handoff-track"
description: "Track any international, overwater, or inbound flight for ADS-C to ADS-B handoff — detects the moment satellite coverage hands off to ground receivers, fires ntfy alert on acquisition. Trigger: \"ADS-B handoff [flight]\", \"track inbound [flight]\", \"overwater track [flight]\", \"when does [flight] hit ADS-B\""
---

---
name: "overwater-adsb-handoff-track"
description: "Track any international, overwater, or inbound flight for ADS-C to ADS-B handoff — detects the moment satellite coverage hands off to ground receivers, fires ntfy alert on acquisition. Trigger: \"ADS-B handoff [flight]\", \"track inbound [flight]\", \"overwater track [flight]\", \"when does [flight] hit ADS-B\""
---

# Skill: overwater-adsb-handoff-track

## Purpose
Detect and alert the moment a specified flight transitions from satellite ADS-C coverage to ground-based ADS-B reception. For any international, overwater, or inbound flight.

## Trigger phrases
"ADS-B handoff [flight]", "track inbound [flight]", "overwater track [flight]", "when does [flight] hit ADS-B", "ADS-C to ADS-B [flight]", "handoff track [flight]"

## Steps

**1. Resolve target aircraft.**
Extract the flight identifier from the operator's message (callsign, tail number, or flight number). If a callsign is given (e.g. UA925), format it as ICAO callsign (UAL925). If a tail number is given (e.g. Q5CXO), look up its ICAO hex. If only a hex is given, use it directly.

To resolve ICAO hex from callsign:
GET https://api.airplanes.live/v2/callsign/<ICAO_CALLSIGN>
Extract `hex` from the first `ac` entry.

**DESIGN NOTE — always use ICAO hex, not callsign, for subsequent queries:**
Hex queries bypass two problems common to callsign queries:
1. Callsign endpoint caching — aggregators cache callsign lookups; hex goes to the raw ADS-B record directly.
2. Privacy filters — FlightAware, FlightRadar24, and others block by tail number or callsign. Unfiltered aggregators (airplanes.live) expose the hex ID regardless, so hex returns real-time position in ~90% of privacy-filtered cases. Apply this to any aircraft, not just specific flights.

**2. Fetch live position via ICAO hex.**
GET https://api.airplanes.live/v2/hex/<TARGET_HEX>

If empty, fallback:
GET https://api.airplanes.live/v2/callsign/<TARGET_CALLSIGN>

Extract: lat, lon, alt_baro, gs, track, seen, rssi, type (adsb_icao / adsc / mlat).

Flag if seen > 60s: STALE CACHE — note explicitly.

**3. Determine signal type and decide whether to alert.**
- `adsb_icao` = ground ADS-B receiver (target state)
- `adsc` or `mlat` or absent = satellite/multilateration (not yet on ground ADS-B)

IF type is `adsb_icao` AND seen < 30 AND rssi is present:
  Fire ntfy alert:
  POST http://100.x.x.x:8000/admin/push-alert
  Authorization: Bearer $CTDC_ADMIN_TOKEN
  Content-Type: application/json
  Body: {"message": "<LABEL> ADS-B acquired (ground): [lat]N [lon]W [alt_baro]ft [gs]kts RSSI:[rssi]dBm"}
  Report: "ADS-B GROUND ACQUIRED — alert fired."

IF still ADS-C/MLAT or stale:
  Do not fire alert.
  Report: "Still satellite. type=[type] seen=[seen]s lat=[lat] lon=[lon] alt=[alt_baro]ft"

IF ac array empty:
  Fire alert: {"message": "<LABEL> off feed — landed or feed lost"}
  Report: "Not found on feed."

**4. Report.**
Summarize: signal type, position, altitude, speed, RSSI, data age, alert disposition.

---

## Auth token

All admin API calls use:
```
Authorization: Bearer $CTDC_ADMIN_TOKEN
```

This is the same shared admin token used by flight-hifi-track, nec-train-hifi-track, and second-brain-remember (token ID 3, label "cowork-dispatch-ops", live-verified active 2026-07-27). **Fixed 2026-07-27:** this skill previously carried a different, dead token (`ctdc_cowork_<REDACTED-revoked-2026-08-16-id3>`) that returned 403 on every admin call — live-tested against `/admin/healthz` and confirmed invalid before this fix. That meant the alert-fire step in Step 3 would have silently failed every time a real handoff was detected. If this token stops working (403 "Admin tier required"), run on Pi: `podman exec systemd-corporatetraveldc-web python3 /app/ctdc_token/cli.py create --user cowork --tier admin --label "cowork-dispatch-ops"` and update this skill with the new token — verify with a cheap call like `/admin/healthz` before assuming a 403 means expiry vs. a token that was never valid.

**Also fixed 2026-07-27:** the push endpoint was `/admin/push-test-alert`, a legacy alias. Switched to the canonical `/admin/push-alert` (same endpoint every other flight/train skill uses) — the legacy alias still works, but there's no reason to be the one skill using it.

**Reach:** All admin calls must go to `http://127.0.0.1:8000` or `http://100.x.x.x:8000` (Tailscale) — Cloudflare strips Authorization headers.

**Note on the corporatetravel-dispatch MCP tools:** they proxy through a separate "dispatch-runner" relay that has shown intermittent JSON-decode errors, timeouts, and even 405s independent of the underlying API's health. If an MCP tool call in this skill fails oddly, fall back to a direct `curl` via SSH to the Pi rather than retrying the relay repeatedly.

---

## Overwater ADS-B handoff — coverage boundary reference

For North Atlantic inbounds (EHAM, EGLL, LFPG → KIAD/KJFK/KBOS):
- ADS-B typically acquired: lat > 43°N AND lon > −67°W (Maritime Canada / NE Maine)
- When ADS-B acquired: fire a separate push: `"<FLIGHT> ADS-B ACQUIRED: <lat>N <lon>W <alt>ft"`

---

## Design notes

**Why hex over callsign:**
The FAA registry / airplanes.live callsign endpoint can carry stale day-old associations; hex queries go straight to the raw ADS-B record and bypass most privacy filters. Always resolve to hex before repeated polling.

**Trigger queue:**
`/admin/push-alert` writes a `.json` trigger file to `/run/corporatetraveldc/triggers/`. The **poller** reads and processes it, firing ntfy (corrected 2026-09-24: this said *the pusher*; the pusher has no trigger-path code and its quadlet mounts no triggers volume) — expect up to ~30s delay.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- **Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20. Read it inside the command, never echo it:
  ```bash
  TOK="$(grep -oE 'ctdc_[A-Za-z0-9._-]+' ~/.secrets/remote-admin-agentic.token | head -1)"
  ```
- **MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
