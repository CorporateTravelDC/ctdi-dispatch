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

> **Corrected 2026-10-15 — Steps 1-2 as first written called `api.airplanes.live` directly from the agent.** That is not allowed: this box resolves position and identity from its own sources first, and airplanes.live is reached only through the shared fallback (`src/common/airplanes_live.py`, reinstated 2026-10-08, off unless `AIRPLANES_LIVE_FALLBACK=1`), which never sends a LADD-listed identifier, rate-limits itself and caches for 30 s. A direct call skips the LADD check. The original steps are kept struck below; use these instead.

**1. Resolve target aircraft (local first).**
Extract the flight identifier (callsign, tail, flight number or hex). Format a callsign as ICAO (UA925 -> UAL925). Then, with the repo's helpers run inside `scripts/with-dispatch-env.sh python3 - <<'EOF' … EOF` from the repo root:
- callsign on this box's receiver: `shared.watchlist._local_ac_by_callsign("<ICAO_CALLSIGN>")`
- FAA flight plan (NAS-wide): `shared.watchlist._local_fdps_ac("<ICAO_CALLSIGN>")` (the plan's hex is the aircraft address on the FAA plan; check the plan is today's leg, see flight-hifi-track Step 1c)
- tail to hex: `shared.watchlist._local_registry_hex_lookup("<REG>")`
- only when all of those are empty: `shared.watchlist._remote_ac_by_callsign(...)` / `_remote_ac_by_hex(...)`, the airplanes.live fallback (returns `None` when disabled or LADD-listed).

**2. Poll by hex.** `shared.watchlist._local_ac_by_hex("<HEX>")` reads this box's own readsb `aircraft.json`. Its record carries readsb's `seen`, `rssi` and `type` fields: a `type` of `adsb_icao` there is ground ADS-B reception on this box's own receiver, which has no satellite input; `mlat` is multilateration, not yet the handoff. A remote (airplanes.live) hit is normalized to `hex/r/t/flight/lat/lon/alt_baro/gs/track/squawk/seen_pos` and has **no** `type` or `rssi`, so it can show position while the aircraft is still out of this box's range but cannot prove the handoff.

Original steps (struck 2026-10-15):

**1. Resolve target aircraft.**
~~Extract the flight identifier from the operator's message (callsign, tail number, or flight number). If a callsign is given (e.g. UA925), format it as ICAO callsign (UAL925). If a tail number is given (e.g. Q5CXO), look up its ICAO hex. If only a hex is given, use it directly.~~

~~To resolve ICAO hex from callsign:~~
~~GET https://api.airplanes.live/v2/callsign/<ICAO_CALLSIGN>~~
~~Extract `hex` from the first `ac` entry.~~

**DESIGN NOTE — always use ICAO hex, not callsign, for subsequent queries:**
~~Hex queries bypass two problems common to callsign queries:~~
~~1. Callsign endpoint caching — aggregators cache callsign lookups; hex goes to the raw ADS-B record directly.~~
~~2. Privacy filters — FlightAware, FlightRadar24, and others block by tail number or callsign. Unfiltered aggregators (airplanes.live) expose the hex ID regardless, so hex returns real-time position in ~90% of privacy-filtered cases. Apply this to any aircraft, not just specific flights.~~

**2. Fetch live position via ICAO hex.**
~~GET https://api.airplanes.live/v2/hex/<TARGET_HEX>~~

~~If empty, fallback:~~
~~GET https://api.airplanes.live/v2/callsign/<TARGET_CALLSIGN>~~

~~Extract: lat, lon, alt_baro, gs, track, seen, rssi, type (adsb_icao / adsc / mlat).~~

~~Flag if seen > 60s: STALE CACHE — note explicitly.~~

**3. Determine signal type and decide whether to alert.**
- `adsb_icao` = ground ADS-B receiver (target state)
- `adsc` or `mlat` or absent = satellite/multilateration (not yet on ground ADS-B)

IF type is `adsb_icao` AND seen < 30 AND rssi is present (2026-10-15: i.e. the hex is on this box's own receiver, Step 2):
  Fire ntfy alert:
  POST http://100.x.x.x:8000/admin/push-alert
  Authorization: Bearer $CTDC_ADMIN_TOKEN
  Content-Type: application/json
  Body: {"topic": "flight-alerts", "title": "<LABEL> ADS-B", "message": "<LABEL> ADS-B acquired (ground): [lat]N [lon]W [alt_baro]ft [gs]kts RSSI:[rssi]dBm"}
  (corrected 2026-10-15: the body as first written had no `topic`, and `/admin/push-alert` then defaults to `ops-health`, `src/web/main.py:2594`. Keep `message` at 200 characters or less.)
  Report: "ADS-B GROUND ACQUIRED — alert fired."

IF still ADS-C/MLAT or stale:
  Do not fire alert.
  Report: "Still satellite. type=[type] seen=[seen]s lat=[lat] lon=[lon] alt=[alt_baro]ft"

IF ac array empty:
  Fire alert: {"topic": "flight-alerts", "message": "<LABEL> off feed — landed or feed lost"}
  Report: "Not found on feed."

**4. Report.**
Summarize: signal type, position, altitude, speed, RSSI, data age, alert disposition.

---

## Auth token

All admin API calls use:
```
Authorization: Bearer $CTDC_ADMIN_TOKEN
```

~~This is the same shared admin token used by flight-hifi-track, nec-train-hifi-track, and second-brain-remember (token ID 3, label "cowork-dispatch-ops", live-verified active 2026-07-27).~~ (corrected 2026-10-15: id 3 was revoked 2026-08-16; current resolution is in "Token resolution" below.) **Fixed 2026-07-27:** this skill previously carried a different, dead token (`ctdc_cowork_<REDACTED-revoked-2026-08-16-id3>`) that returned 403 on every admin call — live-tested against `/admin/healthz` and confirmed invalid before this fix. That meant the alert-fire step in Step 3 would have silently failed every time a real handoff was detected. If this token stops working (403 "Admin tier required"), ~~run on Pi: `podman exec systemd-corporatetraveldc-web python3 /app/ctdc_token/cli.py create --user cowork --tier admin --label "cowork-dispatch-ops"` and update this skill with the new token~~ (corrected 2026-10-15: the CLI path is `/app/src/ctdc_token/cli.py`, minting is an operator decision, and a token never goes into this file) — verify with a cheap call like `/admin/healthz` before assuming a 403 means expiry vs. a token that was never valid.

**Also fixed 2026-07-27:** the push endpoint was `/admin/push-test-alert`, a legacy alias. Switched to the canonical `/admin/push-alert` (same endpoint every other flight/train skill uses) — the legacy alias still works, but there's no reason to be the one skill using it.

**Reach:** All admin calls must go to `http://127.0.0.1:8000` or `http://100.x.x.x:8000` (Tailscale) — Cloudflare strips Authorization headers.

**Note on the corporatetravel-dispatch MCP tools** (2026-10-15: that MCP server was archived 2026-08-17; this note is history)**:** they proxy through a separate "dispatch-runner" relay that has shown intermittent JSON-decode errors, timeouts, and even 405s independent of the underlying API's health. If an MCP tool call in this skill fails oddly, fall back to a direct `curl` via SSH to the Pi rather than retrying the relay repeatedly.

---

## Overwater ADS-B handoff — coverage boundary reference

For North Atlantic inbounds (EHAM, EGLL, LFPG → KIAD/KJFK/KBOS):
- ADS-B typically acquired: lat > 43°N AND lon > −67°W (Maritime Canada / NE Maine)
- When ADS-B acquired: fire a separate push: `"<FLIGHT> ADS-B ACQUIRED: <lat>N <lon>W <alt>ft"`

---

## Design notes

**Why hex over callsign:**
The FAA registry / airplanes.live callsign endpoint can carry stale day-old associations; hex queries go straight to the raw ADS-B record ~~and bypass most privacy filters~~ (struck 2026-10-15: bypassing privacy filters is not a goal here; the fallback refuses LADD-listed aircraft on purpose). Always resolve to hex before repeated polling.

**Trigger queue:**
`/admin/push-alert` writes a `.json` trigger file to `/run/corporatetraveldc/triggers/` (inside the web and poller containers, named volume `corporatetraveldc-triggers`). The **poller** reads and processes it, firing ntfy (corrected 2026-09-24: this said *the pusher*; the pusher has no trigger-path code and its quadlet mounts no triggers volume) — expect up to ~30s delay.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- ~~**Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20.~~ (corrected 2026-10-15: `auth_tokens` id 23
  was **revoked 2026-10-05 19:41Z** (live row, and
  `docs/CREDENTIALS_REGISTER.md`). The file still exists but holds that
  revoked token, so reading it gets a 401/403. Do not use it.)
- **Shell on the Pi, now (2026-10-15):** the platform's own admin callers get
  `DISPATCH_ADMIN_TOKEN` (`auth_tokens` id 24, active) from
  `scripts/with-dispatch-env.sh` (run from the repo root), which loads the env
  files without a shell `source`. Keep the token off every command line (no `curl -H "Bearer
  $TOK"`): run the call inside the wrapper so the value stays in the
  process environment, e.g.
  ```bash
  scripts/with-dispatch-env.sh python3 - <<'EOF'
  import os, requests
  h = {"Authorization": "Bearer " + os.environ["DISPATCH_ADMIN_TOKEN"]}
  r = requests.get("http://100.x.x.x:8000/admin/healthz", headers=h, timeout=10)
  print(r.status_code)   # never print the headers
  EOF
  ```
  Whether an interactive skill session should use that service token or be
  issued its own is an operator decision; nothing here mints one.
- ~~**MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.~~
  (corrected 2026-10-15: the `corporatetravel-dispatch` MCP server that read
  `DISPATCH_TOKEN` was archived 2026-08-17; no MCP path takes an admin token
  today. Cloud agents use the agent gateway tools, which are not admin.)

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
