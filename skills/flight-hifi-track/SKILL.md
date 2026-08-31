---
name: "flight-hifi-track"
description: "Default handler for ANY flight query — hifi position snapshot + auto OOOI watchlist entry + baggage claim ETA push + dual ntfy push (flight-alerts: short hex/pos/OOOI; dispatch-debriefs: full table). Trigger on any mention of a flight number, airline + flight, or track/status/where-is queries — no explicit hifi required. All position/identity resolution is LOCAL ONLY — see Step 1."
---

# Skill: flight-hifi-track

## Purpose
Default handler for any flight query. Resolves position/identity from local sources, fires a short push to `flight-alerts` (hex + position + OOOI status) and a full debrief to `dispatch-debriefs`, and automatically adds the flight to the transient watchlist for OOOI milestone tracking. No explicit "hifi" trigger required — this runs for any flight mention.

## Rewritten 2026-08-31 — the old version of this file called `api.airplanes.live` directly (Steps 1b/1c/2a). That's a real, closed-out standing rule violation: **"no third-party position/lookup APIs" was decided and implemented 2026-08-27/28** (second-brain `corporatetraveldc/01-Sources/manual/20260828T042841Z.md`) — every position/identity resolution site in the actual codebase (`poller/main.py`, `pusher/main.py`, `shared/watchlist.py`, `web/main.py`, `runner/main.py`) was rewired off airplanes.live that session. This file was never updated to match, and following it verbatim just now re-introduced the exact call the rule bans. Fixed below by reusing the real, already-correct resolution code instead of re-deriving equivalent curl calls that can drift again.

## Source priority (in order)
1. **Local ADS-B** — this box's own ultrafeeder/readsb receiver (range-limited to near the box)
2. **Local FDPS** — already-ingested FAA SWIM data (NAS-wide, no range limit, but doesn't always carry hex/position)
3. **Local FAA/OpenSky registry tables** — registration → hex, for the cross-check step
4. **MWAA FIDS** — DCA/IAD only, for gate/baggage/schedule/status (separate from identity resolution)
5. **Web search** — only when 1–3 are ALL dark. Airline website FIDS or FlightAware in search results is fine to read; this box does not call any flight-tracking API directly beyond what's listed above.

The `globe.airplanes.live/?icao=<hex>` link in ntfy pushes is fine to keep — a click-through convenience URL for the operator's phone, not a lookup this box performs.

## Trigger
Any mention of a flight number or status — "Delta 950", "where is KLM651", "DAL950 at DCA", "flight status UAL925", "track [flight]", "hifi [flight]", etc. No "hifi" keyword required.

---

## Step 1: Resolve callsign → hex → registration, LOCAL ONLY

**1a.** Normalize to ICAO callsign:
- DL / Delta → DAL — **but check the tail's actual operator before assuming DAL will ever broadcast.** Delta Connection flight numbers are frequently operated by a regional partner (Republic/RPA, Endeavor/EDV, SkyWest/SKW) broadcasting under THEIR OWN ICAO callsign, not DAL. If a DAL-prefixed callsign query comes back empty, check FIDS (below) for the assigned tail, then reverse-lookup that tail's *current* callsign — don't assume "no contact" means "not yet flying."
- UA / United → UAL
- AA / American → AAL
- BA / British → BAW
- KL / KLM → KLM
- AF / Air France → AFR
- LH / Lufthansa → DLH

**1a2. Check the codeshare translation table before guessing.** This box already builds and maintains a real marketing↔operating carrier/flight-number map — `codeshare_map` (`src/common/db.py`, `SCHEMA_V25`), seeded automatically both when a flight is added to the watchlist (`web/routes/watchlist.py`'s FDPS fallback match) and by `poller/main.py`'s periodic FDPS recheck. Query it before falling back to guesswork:
```python
from common import db
mappings = db.get_codeshare_mapping_by_marketing("<MARKETING_CARRIER>", "<NUM>")
```
If it returns a confirmed mapping, use its `operating_carrier`+`operating_flight_num` for Steps 1b/1c instead of the marketing callsign. **Note, confirmed live 2026-08-31 on DAL5704:** `codeshare_map`'s `operating_flight_num` (e.g. `RPA5704`) is the operating carrier's number for *that specific leg* — if the aircraft is caught still airborne on a DIFFERENT leg of the same rotation (see Step 1h), it'll be broadcasting a completely different operating flight number for THAT leg (e.g. `RPA5665`) — not a discrepancy or drift, just a different flight instance under the same tail, same regional operator. `codeshare_map` correctly gives you the number for the leg you asked about; it isn't meant to match whatever the tail happens to be broadcasting right now if that's a prior/different leg. Once a hex is known (via any path), Step 1e's hex-based tracking is what actually follows the physical aircraft across leg/callsign changes. If a resolution succeeds through a path other than an existing `codeshare_map` hit, don't worry about writing it back manually — the same add-watchlist and periodic-recheck code paths that seed this table already do it as a side effect.

**1b. Local ADS-B, by callsign.** This box's own receiver, range-limited to roughly the DC area — will be empty for anything not currently nearby:
```python
from shared.watchlist import _local_ac_by_callsign
ac = _local_ac_by_callsign("<ICAO_CALLSIGN>")
```
Run via `PYTHONPATH=src python3 -c "..."` with `dispatch.env`/`dispatch-secrets.env` sourced. Returns an `ac`-shaped dict (`hex`/`r`/`lat`/`lon`/`alt_baro`/`gs`/`dst`) or `None`.

**1c. Local FDPS, by callsign** (NAS-wide, use when 1b is empty — most first-queries on a flight that isn't near DC yet will land here):
```python
from shared.watchlist import _local_fdps_ac
ac = _local_fdps_ac("<ICAO_CALLSIGN>")
```
`None` means FDPS has no plan for this callsign, or only a bare plan with no hex/position yet (pre-filing).

**1d. Registration → hex cross-check** (once a registration is known, e.g. from FIDS): local registry tables only, no live call:
```python
from shared.watchlist import _local_registry_hex_lookup
hex_id = _local_registry_hex_lookup("<REGISTRATION>")
```

**1e. Once a hex is known, confirm live position by hex** (works regardless of which callsign the aircraft is currently broadcasting — this is the whole point of hex-based tracking, and it's *more* important now that regional-operator callsign switches are a known, confirmed failure mode, not a hypothetical):
```python
from shared.watchlist import _local_ac_by_hex
ac = _local_ac_by_hex("<HEX>")
```

**1f. Both 1b and 1c empty** — check MWAA FIDS if the destination is DCA or IAD (see FIDS lookup below) before giving up; it often has schedule/gate/tail data even when nothing is airborne yet. Only fall back to web search (1g) once FIDS is also unhelpful or the destination isn't DCA/IAD.

**1g. Web search fallback** (only when local ADS-B, local FDPS, AND FIDS are all dark or inapplicable):
```
web_search("[ICAO_CALLSIGN] flight status [YYYY-MM-DD]")
```
- Preferred sources in order: FlightAware, aviability.com, airline website FIDS
- Do NOT use Trip.com
- Extract: status, origin, destination, scheduled/estimated times, gate if available
- Label all data as `source: web_search fallback`
- Proceed to later steps using web-sourced data; mark debrief as `ADS-B: unavailable | FDPS: unavailable | Source: web_search`
- Note explicitly in debrief: "Position data unavailable — web search only"

---

## Step 1h: FDPS local-DB rotation + cancellation-history check

Use this when Step 1c returns nothing for a flight number that recurs daily — it's usually not a dead trail, it's a rotation that hasn't reached this leg yet.

**Query 1 — same-callsign history across ALL routes (detects multi-leg rotations):**
```sql
SELECT flight_id, origin, destination, status, raw_json,
       datetime(updated_at,'unixepoch','localtime')
FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
ORDER BY updated_at DESC LIMIT 10;
```
Airlines commonly reuse ONE flight number for multiple legs of the same aircraft's rotation in a single day. If the most recent rows show the aircraft completing a DIFFERENT leg that just landed at the ORIGIN of the leg you care about, that's "still on the ground / airborne on a prior leg, hasn't re-filed yet" — report it as such, don't just say "not found."

**The opposite, equally real trap: shuttle-frequency routes reuse the SAME flight number for MULTIPLE INDEPENDENT departures on the same route, same day** (confirmed live 2026-08-31: UAL2136 KBOS→KIAD had an `active` FDPS row with real live position — 4800ft, descending, near IAD — hours before the actual departure being tracked, because that flight number runs multiple times daily on that route, not because it was mid-rotation). This is NOT the same failure mode as Step 1a's regional-callsign case or the multi-leg rotation above — here `flight_num`/`airline` genuinely, correctly matches, it's just a *different instance* of that number from earlier or later the same day. An `active` hit under the right flight number and route is NOT sufficient confirmation you've found the right departure — cross-check the row's actual `updated_at` timestamp and position against the SCHEDULED time from FIDS (or what the operator told you) before treating it as live status for the flight you're tracking. Hex-locking to a same-day-but-wrong-instance hex is exactly the AAL2773 mistake (see Design notes below) — verify before locking, don't just take the first `active` row.

**Query 2 — has the SPECIFIC route been filed yet today:**
```sql
SELECT flight_id, status, datetime(updated_at,'unixepoch','localtime')
FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
  AND origin='<ORIGIN_ICAO>' AND destination='<DEST_ICAO>'
  AND date(updated_at,'unixepoch','localtime')=date('now','localtime');
```

**Query 3 — recurrence + cancellation-rate check (trailing 30 days):**
```sql
SELECT status, COUNT(*) FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
  AND origin='<ORIGIN_ICAO>' AND destination='<DEST_ICAO>'
  AND updated_at > unixepoch('now','-30 days')
GROUP BY status;
```
If this route/flight-number has a high cancelled/dropped fraction, say so in the debrief and log a durable note to the second brain if it's a genuinely notable pattern (`poller/skills/transport_pattern_digest.py` territory) — don't let it evaporate after just this one chat response.

**DB access notes:**
- Live DB sees heavy write contention from concurrent SWIM ingest containers — use a short `.timeout` (`sqlite3 -cmd ".timeout 3000"`) and retry rather than blocking indefinitely; `airline`+`flight_num` is indexed (`idx_flight_events_callsign`), a bare `flight_id LIKE` scan is not and can time out on this table's size (900K+ rows) — always filter on `airline`/`flight_num` directly.
- `raw_json` is the raw FIXM/NAS XML — `departurePoint`/`arrivalPoint` under `<departure>`/`<arrival>` give the actual leg's airports, more reliable than the summary columns for multi-leg detection.

---

## FIDS lookup (DCA/IAD only)

```
GET http://100.x.x.x:8000/api/v1/fids/<DCA|IAD>/<IATA><FLIGHT_NUM>
```
No auth required (Tier 0). Returns `status`/`gate`/`terminal`/`baggage`/`scheduled`/`estimated`/`tail`/`dep_airport`/`dep_gate`/`dep_terminal` when MWAA has the flight. Often the *best* early source for a flight that hasn't departed yet — it has the assigned tail before ADS-B or FDPS have anything, which unblocks Step 1e's hex-based tracking immediately via Step 1d's registry lookup.

---

## Step 2: OOOI watchlist — always add [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

After confirming hex, immediately POST to add the flight to the transient watchlist:

```
POST http://100.x.x.x:8000/api/v1/watchlist/flights
Authorization: Bearer <admin-tier token>
Content-Type: application/json

{
  "identifier": "<ICAO_CALLSIGN>",
  "hex_id": "<CONFIRMED_HEX>",
  "registration": "<REGISTRATION>",
  "origin": "<ORIGIN_ICAO>",
  "destination": "<DEST_ICAO>",
  "added_by": "flight-hifi-track"
}
```

Returns **201**, always — re-adding the same flight refreshes it in place (`entry_id` deterministic, `INSERT … ON CONFLICT(id) DO UPDATE`, `src/common/db.py`). Never branch on a `409`; there isn't one. The response carries `ontime_history_14d` — surface it in the debrief when it isn't `{"insufficient_data": true}`.

Use `/api/v1/watchlist/flights` (`FlightWatchlistRequest`, `src/web/routes/watchlist.py`) — **not** bare `POST /api/v1/watchlist` (a different, legacy Tier-1 endpoint, `start_watchlist()`, that silently drops `hex`/`registration`/`destination_icao` and writes to the separate, effectively-dead `watchlist_sessions` table instead of `watchlist_entries`).

Both this route and Step 3/4/5's `/admin/push-alert` require an **admin-tier** bearer token, and every call is audit-logged (`watchlist.flight.add`, `admin.alert.push`). Never paste a literal token into this file — resolve it from the environment at call time.

---

## Step 3: Baggage claim push — on approach or landed [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

**Fire this step whenever flight phase is DESCENT, APPROACH, or GROUND** (alt_baro < 8000ft OR baro_rate < −500 fpm with alt < 15000ft, OR alt_baro < 1000ft) **and the aircraft is actually flying the leg being tracked** — not a prior rotation leg under a different callsign (see Step 1a's regional-operator note; check the destination in the live `ac`/FDPS data matches, not just that a hex-locked aircraft is airborne somewhere).

Check MWAA FIDS first (see FIDS lookup above, if dest is DCA or IAD). If `baggage` is present → use it, label push `[FIDS]`.

If FIDS unavailable, estimate:

| Condition | Domestic (dest ICAO starts with K) | International |
|---|---|---|
| alt_baro < 1000ft (GROUND / just blocked in) | +15 min | +35 min |
| alt_baro 1000–5000ft (short final / rollout) | +20 min | +40 min |
| alt_baro 5000–8000ft (approach) | +30 min | +50 min |

If an ETA is available from the watchlist entry or FIDS, use `ETA + buffer` instead.

**Push to `flight-alerts` with priority 4 (HIGH):**
```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer <admin-tier token>
Content-Type: application/json

{
  "topic": "flight-alerts",
  "priority": 4,
  "title": "<CALLSIGN> [<HEX>] -- BAGGAGE CLAIM",
  "message": "<CALLSIGN> <REG>: GATE <GATE|TBD> | BAGGAGE <CAROUSEL|est ~HH:MM> (<phase>, <ALT_BARO>ft) -- <DEST_ICAO> [FIDS|est]"
}
```
Example (FIDS confirmed): `AAL1557 N750UW: GATE D38 | BAGGAGE 11 | InAir -- est 19:09 -- KDCA [FIDS]`

If the flight is en route (CRUISE or CLIMB, or still on a prior rotation leg), do NOT fire this push — include the estimated baggage time in the debrief table only (Step 6).

---

## Step 4 & 5: Fire the flight-alerts and dispatch-debriefs pushes

> ⚠️ **The 200-character cap is on the `/admin/push-alert` endpoint itself (`src/web/main.py`: `if len(body.message) > 200: raise HTTPException(400, ...)`), not just the debrief step.** It applies to EVERY call regardless of topic — confirmed live 2026-08-31: a flight-alerts short-form push with a longer, context-heavy message (e.g. noting a shuttle-frequency flight-number caveat) got the same 400 the debrief step already warns about. Keep BOTH messages ≤200 chars; put anything that doesn't fit in the chat report (Step 6) instead. Successful calls return **202** (queued for async delivery by the poller), not 200 — not a delivery confirmation.

**Step 4 — flight-alerts (short form):**
```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer <admin-tier token>
Content-Type: application/json

{
  "topic": "flight-alerts",
  "priority": 3,
  "title": "<CALLSIGN> [<HEX>]",
  "message": "<CALLSIGN> [<HEX>] <REG> -- <LAT_ROUNDED>N <LON_ROUNDED>W <ALT_BARO>ft <GS>kts <FLIGHT_PHASE> | OOOI: watching"
}
```
Format: `DAL950 [a38211] N325NB -- 33.6N 84.6W 6825ft 254kts CLIMB | OOOI: watching`. If the flight hasn't departed yet, skip the position fields entirely rather than padding — `<CALLSIGN> [<HEX>] <REG> -- not yet airborne. sched <HH:MM> EDT <DEST>. OOOI: watching` fits the cap and doesn't fabricate telemetry that doesn't exist yet.

Phase from baro_rate/altitude: `baro_rate > +200` and `alt < 18000` → CLIMB; `baro_rate < -200` → DESCENT; `alt_baro < 1000` → GROUND; else CRUISE.

**Step 5 — dispatch-debriefs (full table, condensed to fit the cap):**
```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer <admin-tier token>
Content-Type: application/json

{
  "topic": "dispatch-debriefs",
  "priority": 2,
  "title": "<CALLSIGN> HIFI DEBRIEF",
  "message": "<condensed debrief -- MAX 200 CHARS>"
}
```

---

## Step 6: Report full snapshot in chat

Always report the full table in chat AND as the dispatch-debriefs push body:

```
Flight:           <CALLSIGN>  (note operating carrier if different from the branded callsign)
Registration:     <REG>
ICAO hex:         <HEX>
Type:             <TYPE> (<YEAR>)
Operator:         <OPERATOR>
Lat / Lon:        <LAT 6dp>N, <LON 6dp>W
Alt baro/geo:     <ALT_BARO>ft / <ALT_GEOM>ft
Ground speed:     <GS>kts
Track:            <TRACK> deg
Baro rate:        <BARO_RATE> fpm
Squawk:           <SQUAWK>
Position source:  Local ADS-B | Local FDPS | unavailable
Phase:            CLIMB | CRUISE | DESCENT | GROUND  (flag if on a prior rotation leg, not yet the tracked leg)
OOOI:             Added to watchlist (<entry id>) | [SKIP] Pi offline
Destination:      <DEST_ICAO>, source: FIDS | FDPS | ADS-B | web | unknown
Schedule (FIDS):  sched <HH:MM> / est <HH:MM> ET -- gate <GATE>, terminal <T>, baggage <CAROUSEL>  [if DCA/IAD]
Baggage ETA:      Carousel <N> [FIDS] | est ~<HH:MM> local | not yet applicable -- <reason>
```

Label every timestamp explicitly as ET or UTC — never a bare/unlabeled time (operator standing rule, 2026-08-27).

---

## Overwater ADS-B handoff

For North Atlantic inbounds (EHAM, EGLL, LFPG → KIAD/KJFK/KBOS): ADS-B typically acquired at lat > 43°N, lon > -67°W (Maritime Canada / NE Maine). On acquisition, fire `flight-alerts`: `"<FLIGHT> ADS-B ACQUIRED: <lat>N <lon>W <alt>ft -- switching from ADS-C"`. (This box has no ADS-C/satellite feed of its own — this documents the handoff point for when local ADS-B first picks the flight up, not a live satellite lookup.)

---

## Design notes

**Source hierarchy is strict and entirely local through the first four tiers:** local ADS-B → local FDPS → local registry tables → FIDS → web search, in that order, web search only as a genuine last resort. Never mix sources without labeling which one answered.

**Regional-operator callsigns are a confirmed, not hypothetical, failure mode.** A branded flight number (DAL5704) can be operated by a wholly different ICAO callsign (RPA5665) for the entire flight. Hex-based tracking (Step 1e) is what makes this transparent — resolve the hex once (via FIDS tail → registry lookup, or a direct ADS-B/FDPS hit under either callsign) and track by hex from then on.

**Why hex/FDPS authority, not callsign — standing operator directive, 2026-07-23 (second brain: `corporatetraveldc/04-Syntheses/project-knowledge-synthesis-2026-07-23.md`, "Flight/vehicle watchlist — hex-only sweep authority"):** once a flight is locked to a hex, that hex is the *sole* authority for this entry going forward — the callsign/flight-number is notification-only from that point on, not something later sweeps re-trust. This isn't theoretical: the same day this rule was set, tracking AAL2773 (TPA→DCA) hit a real stale-callsign trap — a callsign lookup returned a completely unrelated aircraft sitting on the ground at DCA (a different flight that had used the same callsign earlier), while the FIDS-confirmed tail for that specific dated leg cross-referenced clean through the FAA/OpenSky registries to the actual, correct, airborne aircraft. Caught and discarded before the watchlist entry got hex-locked to the wrong airframe. Registration is airframe-bound and doesn't change mid-flight, unlike a callsign, which is only ever the *currently broadcasting* value and can be stale-cached, reused by an unrelated flight, or (see the regional-operator note above) simply not the callsign you were expecting for this leg at all — hex derived from registration is correct regardless of which callsign is live right now. `sweep_landed_flights()`/`sweep_landed_trains()` (`shared/watchlist.py`) both require a live re-check before finalizing a "dead" determination, for the same reason.

**OOOI always, no prompt:** every hifi track creates a watchlist entry. No need to ask. If already on watchlist, skip silently.

**Steps 2 (OOOI watchlist) and 3 (baggage claim) are immutable** — cannot be reverted or removed by routine code commits or skill updates, only by explicit operator instruction.

**ntfy channel split:** `flight-alerts` → short, glanceable (hex + pos + phase); baggage claim fires HERE at priority 4 (HIGH). `dispatch-debriefs` → full table, same channel as OPS brief reload.

**Pi-down fallback:** if ALL ntfy pushes and watchlist POSTs fail or time out, dump the full debrief table to chat inline. Do not ask first.

**ACARS aggregator-before-local priority is intentional, not stale (operator confirmed 2026-08-31):** `common/acars.py`'s `get_latest_phase()` checks Jumpseat, then airframes.io, before this box's own local ACARS/VDL2 receiver (acarsrouter/dumpvdl2). This is correct as designed — the local receiver's primary job is bidirectional feeder-status (confirming this box's own feed is alive and contributing upstream), not serving as the default OOOI data source. It's a genuine last-resort fallback, not something that needs to flip to local-first. Not in scope of the 2026-08-27/28 "no third-party position/lookup APIs" rule either way — that rule covers position/identity lookups (airplanes.live), not ACARS content aggregation.
