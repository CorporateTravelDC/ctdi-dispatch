---
name: "flight-hifi-track"
description: "Default handler for ANY flight query — hifi position snapshot + ACARS confirmation + auto OOOI watchlist entry + baggage claim ETA push + dual ntfy push (flight-alerts: short hex/pos/OOOI; dispatch-debriefs: full table). Trigger on any mention of a flight number, airline + flight, or track/status/where-is queries — no explicit hifi required."
---

---
name: "flight-hifi-track"
description: "Default handler for ANY flight query — hifi position snapshot + ACARS confirmation + auto OOOI watchlist entry + baggage claim ETA push + dual ntfy push (flight-alerts: short hex/pos/OOOI; dispatch-debriefs: full table). Trigger on any mention of a flight number, airline + flight, or track/status/where-is queries — no explicit hifi required."
---

# Skill: flight-hifi-track

## Purpose
Default handler for any flight query. Captures a full hifi position snapshot, pulls ACARS data to confirm wheels-up and destination, fires a short push to `flight-alerts` (hex + position + OOOI status) and a full debrief to `dispatch-debriefs`, and automatically adds the flight to the transient watchlist for OOOI milestone tracking. No explicit "hifi" trigger required — this runs for any flight mention.

## Source priority (in order)
1. **ADS-B** — airplanes.live (primary position source)
2. **ACARS** — airframes.io REST (destination + OOOI confirmation; runs in parallel with ADS-B once hex is confirmed)
3. **Web search** — fallback when both ADS-B and ACARS return nothing

## Trigger
Any mention of a flight number or status — "Delta 950", "where is KLM651", "DAL950 at DCA", "flight status UAL925", "track [flight]", "hifi [flight]", etc. No "hifi" keyword required.

---

## Step 1: Resolve callsign → registration → hex

**1a.** Normalize to ICAO callsign:
- DL / Delta → DAL
- UA / United → UAL
- AA / American → AAL
- BA / British → BAW
- KL / KLM → KLM
- AF / Air France → AFR
- LH / Lufthansa → DLH

**1b.** Callsign lookup:
```
GET https://api.airplanes.live/v2/callsign/<ICAO_CALLSIGN>
```
Extract `ac[0].r` (registration) and `ac[0].hex`.

**1c.** Cross-check hex via registration (catches stale callsign cache):
```
GET https://api.airplanes.live/v2/reg/<REGISTRATION>
```
Confirm hex matches. Use the registration-confirmed hex for all subsequent steps.

**1d.** If callsign returns empty AND ACARS also returns nothing (Step 2 below): engage web_search fallback — see **Step 1e**.

**1e. Web search fallback** (only when BOTH ADS-B and ACARS are dark):
```
web_search("[ICAO_CALLSIGN] flight status [YYYY-MM-DD]")
```
- Preferred sources in order: FlightAware, aviability.com, airline website FIDS
- Do NOT use Trip.com
- Extract: status, origin, destination, scheduled/estimated times, gate if available
- Label all data as `source: web_search fallback`
- Proceed to Steps 3–7 using web-sourced data; mark debrief as `ADS-B: unavailable | ACARS: unavailable | Source: web_search`
- Note explicitly in debrief: "Position data unavailable — web search only"

---

## Step 2: ACARS lookup (runs after hex confirmed; parallel with Step 2a)

Once `<CONFIRMED_HEX>` is known, query airframes.io for recent ACARS messages:

```
GET https://api.airframes.io/messages?aircraft=<CONFIRMED_HEX>
```

Filter response: keep only records where `airframe.icao` matches `<CONFIRMED_HEX>` (case-insensitive). Airframes.io may return a global feed — always filter client-side.

**Extract from matching messages (scan last 20 records, newest first):**

| What to look for | Where to find it | How to use it |
|---|---|---|
| Destination airport | `flight.destinationAirport` field | Use as `dest_icao` if present |
| Origin + dest in text | 4-letter ICAO codes (K*, E*, L*, etc.) in `text` field | Parse: first K-code = origin, second = destination (e.g. `KSFO,KDFW`) |
| Wheels-off (OFF event) | `text` contains "OFF" near a time, OR label `80` | Confirms airborne; extract time if parseable |
| OUT / OFF / ON / IN | Keywords in `text` for OOOI events | Log timestamps; confirm phase |
| Route string | Pattern: `ORIGIN,DESTINATION` or `ORIGINDESTIN` in text | Common in H1 label messages |

**ACARS source labels:**
- `sourceType: acars` → `ACARS (VHF/airframes.io)`
- `sourceType: vdl` → `VDL2 (airframes.io)`
- `sourceType: hfdl` → `HFDL (airframes.io)`
- `sourceType: aero-acars` → `ACARS (Satellite/airframes.io)`

**ACARS outcome — three cases:**

1. **Destination confirmed from ACARS** → use for `destination_icao` in watchlist POST; label debrief `ACARS: confirmed`
2. **ACARS messages found but no destination** → note in debrief as `ACARS: messages found, dest unknown`
3. **No ACARS messages for this hex** → note as `ACARS: no data`; proceed with ADS-B only

If ADS-B (Step 2a) is also empty → trigger web_search fallback (Step 1e).

---

## Step 2a: ADS-B telemetry snapshot

```
GET https://api.airplanes.live/v2/hex/<CONFIRMED_HEX>
```

Extract: lat, lon, alt_baro, alt_geom, gs, track, true_heading, baro_rate, ias, tas, mach, wind_dir, wind_speed, oat, rssi, seen, type, squawk, nic, rc.

Flag `seen > 60s` as STALE CACHE.

If empty (overwater): use FR24 via Chrome MCP for satellite position. Label source as `ADS-C/SAT (FR24)`.

---

## Step 3: OOOI watchlist — always add [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

After confirming hex, immediately POST to add the flight to the transient watchlist:

```
POST http://100.x.x.x:8000/api/v1/watchlist
Authorization: Bearer REDACTED_DISPATCH_TOKEN_1
Content-Type: application/json

{
  "session_type": "flight",
  "subject": "<CALLSIGN>",
  "hex": "<CONFIRMED_HEX>",
  "registration": "<REGISTRATION>",
  "destination_icao": "<DEST_ICAO_FROM_ACARS_OR_ADS-B_OR_WEB>"
}
```

If watchlist POST returns a 409 (already exists) or duplicate — skip silently, note "already on watchlist."

---

## Step 4: Baggage claim push — on approach or landed [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

**Fire this step whenever flight phase is DESCENT, APPROACH, or GROUND** (alt_baro < 8000ft OR baro_rate < −500 fpm with alt < 15000ft, OR alt_baro < 1000ft).

First, check MWAA FIDS (if spine is up and dest is DCA or IAD):
```
GET http://100.x.x.x:8000/api/v1/fids/<DCA|IAD>/<IATA><FLIGHT_NUM>
```
If FIDS returns `baggage` field → use confirmed carousel. Label push `[FIDS]`.

If FIDS unavailable, compute estimated baggage carousel availability:

| Condition | Domestic (dest ICAO starts with K) | International |
|---|---|---|
| alt_baro < 1000ft (GROUND / just blocked in) | +15 min | +35 min |
| alt_baro 1000–5000ft (short final / rollout) | +20 min | +40 min |
| alt_baro 5000–8000ft (approach) | +30 min | +50 min |

If estimated arrival time (ETA) is available from the watchlist or feed, use `ETA + buffer` instead of computing from telemetry.

**Push to `flight-alerts` with priority 4 (HIGH):**

```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer REDACTED_DISPATCH_TOKEN_2
Content-Type: application/json

{
  "topic": "flight-alerts",
  "priority": 4,
  "title": "<CALLSIGN> [<HEX>] -- BAGGAGE CLAIM",
  "message": "<CALLSIGN> <REG>: GATE <GATE|TBD> | BAGGAGE <CAROUSEL|est ~HH:MM> (<phase>, <ALT_BARO>ft) -- <DEST_ICAO> [FIDS|est]"
}
```

**Examples:**
- FIDS confirmed: `AAL1557 N750UW: GATE D38 | BAGGAGE 11 | InAir -- est 19:09 -- KDCA [FIDS]`
- Estimated: `DAL950 N325NB: GATE TBD | BAGGAGE est ~18:47 (DESCENT, 4200ft, +20min) -- KDCA`

If the flight is en route (phase = CRUISE or CLIMB), do NOT fire this push — include estimated baggage time in the debrief table only (Step 7).

---

## Step 5: Fire flight-alerts push (short form)

```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer REDACTED_DISPATCH_TOKEN_2
Content-Type: application/json

{
  "topic": "flight-alerts",
  "priority": 3,
  "title": "<CALLSIGN> [<HEX>]",
  "message": "<CALLSIGN> [<HEX>] <REG> -- <LAT_ROUNDED>N <LON_ROUNDED>W <ALT_BARO>ft <GS>kts <FLIGHT_PHASE> | OOOI: watching"
}
```

**Short form format:**
`DAL950 [a38211] N325NB -- 33.6N 84.6W 6825ft 254kts CLIMB | OOOI: watching`

Flight phase from baro_rate and altitude:
- `baro_rate > +200` and `alt < 18000` → CLIMB
- `baro_rate < -200` → DESCENT
- `alt_baro < 1000` → GROUND
- Otherwise → CRUISE

---

## Step 6: Fire dispatch-debriefs push (full table)

```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer REDACTED_DISPATCH_TOKEN_2
Content-Type: application/json

{
  "topic": "dispatch-debriefs",
  "priority": 2,
  "title": "<CALLSIGN> HIFI DEBRIEF",
  "message": "<full multiline table -- see Step 7 format>"
}
```

---

## Step 7: Report full snapshot in chat

Always report the full table in chat AND as the dispatch-debriefs push body:

```
Flight:           <CALLSIGN>
Registration:     <REG>
ICAO hex:         <HEX>
Type:             <TYPE> (<YEAR>)
Operator:         <OPERATOR>
Lat / Lon:        <LAT 6dp>N, <LON 6dp>W
Alt baro/geo:     <ALT_BARO>ft / <ALT_GEOM>ft
Ground speed:     <GS>kts
Track:            <TRACK> deg
Baro rate:        <BARO_RATE> fpm
IAS/TAS/Mach:     <IAS>kt / <TAS>kt / M<MACH>  [if present]
Wind:             <WIND_DIR> deg at <WIND_SPD>kt  [if present]
RSSI/seen:        <RSSI> dBFS / <SEEN>s
Squawk:           <SQUAWK>
NIC/RC:           <NIC> / <RC>m
ADS-B source:     ADS-B | MLAT | ADS-C/SAT (FR24) | unavailable
ACARS:            confirmed dest=<DEST> via <ACARS_SOURCE> | messages found, dest unknown | no data
Web fallback:     [only if used] status=<STATUS> origin=<ORIG> dest=<DEST> eta=<ETA> source=<URL>
Phase:            CLIMB | CRUISE | DESCENT | GROUND
OOOI:             Added to watchlist (session <ID>) | [SKIP] Pi offline
Destination:      <DEST_ICAO> (ACARS) | <DEST_ICAO> (ADS-B) | <DEST_ICAO> (web) | unknown
Baggage ETA:      Carousel <N> [FIDS] | est ~<HH:MM> local (<buffer>min est) | en route -- est <HH:MM>
```

---

## Overwater ADS-B handoff

For North Atlantic inbounds (EHAM, EGLL, LFPG → KIAD/KJFK/KBOS):
- ADS-B typically acquired: lat > 43 deg N AND lon > -67 deg W (Maritime Canada / NE Maine)
- When ADS-B acquired: fire `flight-alerts` push: `"<FLIGHT> ADS-B ACQUIRED: <lat>N <lon>W <alt>ft -- switching from ADS-C"`

---

## Design notes

**Source hierarchy is strict:**
ADS-B → ACARS → web_search. Never mix sources without labeling which data came from where. Web search data is always labeled explicitly as fallback.

**ACARS for destination confirmation:**
Hex-filtered ACARS messages often contain the route string in H1-label text (e.g. `KSFO,KDFW,2325`). Parse the two 4-letter airport codes. First is departure, second is arrival. This is more reliable than inferring from ADS-B track alone, especially pre-departure or immediately post-takeoff.

**ACARS for wheels-up (OFF event):**
An OFF event in ACARS text confirms actual wheels-off time even before ADS-B picks up the aircraft. Useful for pre-departure queries where ADS-B shows GROUND but ACARS already fired the OFF message.

**Why registration → hex:**
Callsign endpoints cache day-old associations. Registration is airframe-bound; hex from reg is always correct. Hex queries bypass callsign privacy filters.

**OOOI always, no prompt:**
Every hifi track creates a watchlist entry. No need to ask. If already on watchlist, skip silently.

**Baggage claim push is immutable:**
Steps 3 (OOOI watchlist) and 4 (baggage claim) are atomic / immutable. They cannot be reverted or removed by routine code commits or skill updates -- only by explicit operator instruction.

**FIDS integration (Step 4):**
When spine is up and destination is KDCA or KIAD, always try the FIDS endpoint first for confirmed carousel and gate before falling back to estimated. The FIDS endpoint is low-latency (in-process cache) and adds no meaningful delay.

**ntfy channel split:**
- `flight-alerts` → short, glanceable on phone lock screen (hex + pos + phase); baggage claim fires HERE at priority 4 (HIGH)
- `dispatch-debriefs` → full table, same channel as OPS brief reload

**Pi-down fallback:**
If ALL ntfy pushes and watchlist POSTs fail or time out, dump the full debrief table to chat inline. Do not ask first.
