---
name: "flight-hifi-track"
description: "Default handler for ANY flight query — hifi position snapshot + auto OOOI watchlist entry + baggage claim ETA push + dual ntfy push (flight-alerts: short hex/pos/OOOI; dispatch-debriefs: full table). Trigger on any mention of a flight number, airline + flight, or track/status/where-is queries — no explicit hifi required. Position/identity: local sources first, airplanes.live only through the shared fallback, tails from SWIM only (FDPS, TDES-linked FDPS plan, TBFM, TFMS), never from FIDS — see Step 1."
---

# Skill: flight-hifi-track

## Purpose
Default handler for any flight query. Resolves position/identity from local sources, fires a short push to `flight-alerts` (hex + position + OOOI status) and a full debrief to `dispatch-debriefs`, and automatically adds the flight to the transient watchlist for OOOI milestone tracking. No explicit "hifi" trigger required — this runs for any flight mention.

## Rewritten 2026-08-31 — the old version of this file called `api.airplanes.live` directly (Steps 1b/1c/2a). That's a real, closed-out standing rule violation: **"no third-party position/lookup APIs" was decided and implemented 2026-08-27/28** (second-brain `corporatetraveldc/01-Sources/manual/20260828T042841Z.md`) — every position/identity resolution site in the actual codebase (`poller/main.py`, `pusher/main.py`, `shared/watchlist.py`, `web/main.py`, `runner/main.py`) was rewired off airplanes.live that session. This file was never updated to match, and following it verbatim just now re-introduced the exact call the rule bans. Fixed below by reusing the real, already-correct resolution code instead of re-deriving equivalent curl calls that can drift again.

## Corrected 2026-10-15 — read this before Step 1

Refreshed against HEAD 1491bf5a plus the batch staged for the next relay and the live Pi (2026-10-15 ~01:50Z / 2026-10-14 21:50 ET). Four things changed since 2026-08-31; the steps below are corrected in place, old text struck.

1. **FIDS is not an identity source** (operator, 2026-10-14). MWAA FIDS is gate, ~~baggage and schedule~~ terminal and baggage enrichment only (2026-10-18: never a time or a status either): never OOOI, never a tail. Aircraft tails come from SWIM only: the FDPS flight plan, the FDPS plan linked to this flight's TDES departure clearance at DCA/IAD/BWI, TBFM and TFMS (`AUTHORITATIVE_TAIL_SOURCES` in ~~`src/common/flight_instance.py:914`~~ (corrected 2026-10-25: `src/common/flight_instance.py:1021` at 5f7fca01, `{"fdps", "tdes", "tbfm", "tfms"}`); guardrail `tests/common/test_flight_instance.py::test_fids_is_not_a_tail_source`). ~~The FIDS response still has a `tail` field; do not use it to look up a hex, and do not send it as `registration`/`tail` on the watchlist add.~~ (2026-10-18: the FIDS route no longer returns `tail`, `status`, `scheduled`, `estimated` or `remaining`, and the watchlist add ignores a `tail` field. Read gate and baggage from the assignments route, below.)
2. **A watch may be added without a hex.** `POST /api/v1/watchlist/flights` takes `hex_id`/`registration` as optional. The poller resolves and locks the airframe itself from local ADS-B, FDPS and the TDES-linked plan, and repoints it only on a SWIM source showing a different tail on this flight. Add the watch as soon as the flight and leg are known; do not wait for a hex.
3. **airplanes.live is back as a code-side fallback** (operator, 2026-10-08): `src/common/airplanes_live.py`, off unless `AIRPLANES_LIVE_FALLBACK=1`, asked only after every local source has no contact, never sent a LADD-listed identifier, position/identity only, no OOOI authority. Reach it only through `shared.watchlist._remote_ac_by_hex()` / `_remote_ac_by_callsign()`, never with a direct HTTP call. The 2026-08-31 "never call airplanes.live" note above stays right for direct calls.
4. **The database is Postgres** (`corporatetraveldc-pgsql`, since 2026-09-19/20). Step 1h's SQLite queries are replaced with Postgres ones.

## Corrected 2026-10-22 — read with the 2026-10-15 block

1. **AeroAPI and airplanes.live are FALLBACKS and CROSS-CHECKS only** (operator 2026-10-22). They answer only after local ADS-B, FDPS/SWIM and the registry tables have nothing; in a tail conflict they are a corroborating vote, never the authority (tails come from SWIM). airplanes.live is re-allowed now that this box's feed carries its feeder UUID (the late-September 403s were the anonymous feed); still only through the shared helpers, never a LADD identifier. ADSB.fi is not a source.
2. **Public links are ICAO-hex only** (operator hard rule 2026-10-22): `https://globe.airplanes.live/?icao=<hex>` or `https://map.opensky-network.org/?icao=<hex>`. Never a tail, flight number or callsign in a URL (e.g. never `airnavradar.com/flight/<ident>`). No hex, no link.
3. **Watch-add tail fill only from today's leg** (fix in the 2026-10-22 batch): the route no longer copies a tail from an older FDPS plan. ~~Until that build is live, check the 201's tail against today's plan~~ (corrected 2026-10-25: superseded — that build is live: the running web image (built ~~2026-10-25 23:28Z~~ (corrected 2026-10-25: 2026-10-25 23:29Z)) carries `_fdps_plan_is_current_leg()`, which takes a plan's identity only when its departure time (else `updated_at`) is within 18 h of the sent `scheduled_departure`, else of now; `src/web/routes/watchlist.py:90-118,402-409`) (it filled a prior day's airframe twice on 2026-10-21/22).
4. **Locking a hex:** take the tail from today's FDPS plan, the registry hex for it, and corroborate with a live position under the same callsign (local ADS-B first, airplanes.live callsign lookup as the cross-check) before re-adding the watch with `hex_id` + `registration`. Watch ids are date-keyed; ~~until the ET-date fix is live, a re-add after 20:00 ET creates a second entry -- delete the stale one (`DELETE /api/v1/watchlist/<entry_id>`).~~ (corrected 2026-10-25: superseded — the ET-date fix is live: the id's date is the America/New_York date of the leg's `scheduled_departure`, else of now (`src/common/flight_instance.py:139`, `src/web/routes/watchlist.py:41-47`; present in the running web image), so a same-evening re-add no longer splits. A stray entry still goes with `DELETE /api/v1/watchlist/<entry_id>` (admin).)

## Source priority (in order)
1. **Local ADS-B** — this box's own ultrafeeder/readsb receiver (range-limited to near the box)
2. **Local FDPS** — already-ingested FAA SWIM data (NAS-wide, no range limit, but doesn't always carry hex/position). With the TDES-linked plan, TBFM and TFMS, the only sources of an aircraft tail (2026-10-15).
3. **Local FAA/OpenSky registry tables** — registration → hex, for the cross-check step (the registration must come from a SWIM source, 2026-10-15)
3a. **airplanes.live fallback** (added 2026-10-15) — only through the shared helpers, only when 1–3 found nothing; see the correction block above.
4. ~~**MWAA FIDS** — DCA/IAD only, for gate/baggage/schedule/status (separate from identity resolution; 2026-10-15: never a tail, never OOOI)~~ **Gate / baggage assignments** (2026-10-18) — `GET /api/v1/flights/<ident>/assignments` (see "Gate / baggage lookup" below): ~~SWIM TDES > ACARS > AeroAPI fallback > MWAA FIDS~~ (corrected 2026-10-25: SWIM TDES = SWIM TFDM > ACARS > AeroAPI fallback > MWAA FIDS), gate / terminal / baggage only; never a time, a status, a tail or OOOI
5. **Web search** — only when 1–3 are ALL dark. Airline website FIDS or FlightAware in search results is fine to read; this box does not call any flight-tracking API directly beyond what's listed above.

The `globe.airplanes.live/?icao=<hex>` link in ntfy pushes is fine to keep — a click-through convenience URL for the operator's phone, not a lookup this box performs.

## Trigger
Any mention of a flight number or status — "Delta 950", "where is KLM651", "DAL950 at DCA", "flight status UAL925", "track [flight]", "hifi [flight]", etc. No "hifi" keyword required.

---

## Step 1: Resolve callsign → hex → registration, LOCAL ONLY

**1a.** Normalize to ICAO callsign:
- DL / Delta → DAL — **but check the tail's actual operator before assuming DAL will ever broadcast.** Delta Connection flight numbers are frequently operated by a regional partner (Republic/RPA, Endeavor/EDV, SkyWest/SKW) broadcasting under THEIR OWN ICAO callsign, not DAL. If a DAL-prefixed callsign query comes back empty, ~~check FIDS (below) for the assigned tail, then reverse-lookup that tail's *current* callsign~~ (corrected 2026-10-15: FIDS is not a tail source) resolve the operating callsign through `codeshare_map` (Step 1a2) and query FDPS under it; or add the watch without a hex and let the poller resolve it — don't assume "no contact" means "not yet flying."
- UA / United → UAL
- AA / American → AAL
- BA / British → BAW
- KL / KLM → KLM
- AF / Air France → AFR
- LH / Lufthansa → DLH

**1a2. Check the codeshare translation table before guessing.** This box already builds and maintains a real marketing↔operating carrier/flight-number map — `codeshare_map` (`src/common/db.py`, `SCHEMA_V25`; in Postgres since 2026-09-19, `pg_schema/0054`; `db.resolve_operating_callsign(ident)` returns the operating callsign directly, 2026-10-15), seeded automatically both when a flight is added to the watchlist (`web/routes/watchlist.py`'s FDPS fallback match) and by `poller/main.py`'s periodic FDPS recheck. Query it before falling back to guesswork:
```python
from common import db
mappings = db.get_codeshare_mapping_by_marketing("<MARKETING_CARRIER>", "<NUM>")
```
If it returns a confirmed mapping, use its `operating_carrier`+`operating_flight_num` for Steps 1b/1c instead of the marketing callsign. **Note, confirmed live 2026-08-31 on a Delta Connection flight** (flight numbers generalized 2026-10-15 for the public mirror)**:** `codeshare_map`'s `operating_flight_num` (e.g. `RPA<same number>`) is the operating carrier's number for *that specific leg* — if the aircraft is caught still airborne on a DIFFERENT leg of the same rotation (see Step 1h), it'll be broadcasting a completely different operating flight number for THAT leg (e.g. `RPA<another number>`) — not a discrepancy or drift, just a different flight instance under the same tail, same regional operator. `codeshare_map` correctly gives you the number for the leg you asked about; it isn't meant to match whatever the tail happens to be broadcasting right now if that's a prior/different leg. Once a hex is known (via any path), Step 1e's hex-based tracking is what actually follows the physical aircraft across leg/callsign changes. If a resolution succeeds through a path other than an existing `codeshare_map` hit, don't worry about writing it back manually — the same add-watchlist and periodic-recheck code paths that seed this table already do it as a side effect.

**1b. Local ADS-B, by callsign.** This box's own receiver, range-limited to roughly the DC area — will be empty for anything not currently nearby:
```python
from shared.watchlist import _local_ac_by_callsign
ac = _local_ac_by_callsign("<ICAO_CALLSIGN>")
```
Run via ~~`PYTHONPATH=src python3 -c "..."` with `dispatch.env`/`dispatch-secrets.env` sourced~~ (corrected 2026-10-15: never `source` the env files; values are unquoted and a shell can leak them) `scripts/with-dispatch-env.sh python3 - <<'EOF' … EOF` from the repo root, which loads both files verbatim and sets `PYTHONPATH=src`. Returns an `ac`-shaped dict (`hex`/`r`/`lat`/`lon`/`alt_baro`/`gs`/`dst`) or `None`.

**1c. Local FDPS, by callsign** (NAS-wide, use when 1b is empty — most first-queries on a flight that isn't near DC yet will land here):
```python
from shared.watchlist import _local_fdps_ac
ac = _local_fdps_ac("<ICAO_CALLSIGN>")
```
`None` means FDPS has no plan for this callsign, or only a bare plan with no hex/position yet (pre-filing).

**A non-`None` result is NOT proof of freshness — confirmed live 2026-08-31 on a daily Delta flight, and again 2026-10-15 on a daily regional flight** (flight numbers generalized 2026-10-15 for the public mirror)**.** `_local_fdps_ac()`/`get_flight_plan_by_callsign()` return the *most recent* row for that callsign~~, full stop — no same-day check (still true 2026-10-15, `src/common/db.py:4800`; it prefers a DC-area destination and a non-cancelled row, nothing about the date)~~ (corrected 2026-10-25: `src/common/db.py:5180` at 5f7fca01. With no destination hint it first prefers a non-cancelled DC-area-bound row updated in the last 20 h, but its last fallback still takes the newest row of any age, so the trap below stands). For a route that flies daily, that can be a FULL CALENDAR DAY stale (the 2026-08-31 case: the "match" was the previous night's already-completed flight — its own embedded FDPS timestamp and actual departure/arrival times were plainly from the day before, not today). This is a third, distinct trap from the multi-leg-rotation case (Step 1h) and the shuttle-frequency case (also Step 1h) — always sanity-check the returned record's actual timestamp/position against what you'd expect for *today's* specific instance before treating it as current. If in doubt, run Step 1h's Query 2 (~~`date(updated_at,...)=date('now','localtime')`~~ the Postgres same-UTC-day filter, corrected 2026-10-15) explicitly rather than trusting this shortcut's freshness. The same trap applies to the watchlist add: when the body carries no hex or tail, the route fills them from this lookup's plan, ~~checking only that the plan's origin does not contradict the one you sent (`src/web/routes/watchlist.py`, 2026-10-13 block)~~ (corrected 2026-10-25: superseded — it checks that the plan's origin does not contradict the one you sent and, since commit 93b6b6b0 (2026-10-23 01:36Z), that the plan is the current leg: departure time (else `updated_at`) within 18 h of the sent `scheduled_departure`, else of now; `src/web/routes/watchlist.py:90-118,402-409`, live in the running web image). So always send `origin` (and `scheduled_departure` when known), and check the stored identity in the 201 response.

**1d. Registration → hex cross-check** (once a registration is known, ~~e.g. from FIDS~~ corrected 2026-10-15: from a SWIM source, i.e. the FDPS plan's `r` or a TBFM/TFMS record, never FIDS): local registry tables only, no live call:
```python
from shared.watchlist import _local_registry_hex_lookup
hex_id = _local_registry_hex_lookup("<REGISTRATION>")
```

**1e. Once a hex is known, confirm live position by hex** (works regardless of which callsign the aircraft is currently broadcasting — this is the whole point of hex-based tracking, and it's *more* important now that regional-operator callsign switches are a known, confirmed failure mode, not a hypothetical):
```python
from shared.watchlist import _local_ac_by_hex
ac = _local_ac_by_hex("<HEX>")
```

**1f. Both 1b and 1c empty** — ~~check MWAA FIDS if the destination is DCA or IAD (see FIDS lookup below) before giving up; it often has schedule/gate~~/tail~~ data even when nothing is airborne yet (corrected 2026-10-15: use it for schedule and gate only; its tail is not an identity source).~~ (2026-10-18) check the assignments route (see "Gate / baggage lookup" below) for gate and baggage; it never answers schedule, status or identity questions -- the schedule comes from TFMS / the FDPS plan. Add the watch anyway (Step 2): the poller keeps trying the SWIM sources and the airplanes.live fallback. Only fall back to web search (1g) once ~~FIDS~~ the assignments route is also unhelpful (2026-10-18).

**1g. Web search fallback** (only when local ADS-B, local FDPS, AND ~~FIDS~~ the assignments route (2026-10-18) are all dark or inapplicable):
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

> **Corrected 2026-10-15:** the three queries below were SQLite (`datetime(…,'unixepoch','localtime')`, `unixepoch()`), and `localtime` on this host is UTC anyway. The live table is in Postgres (968,821 rows on 2026-10-15 (unverified 2026-10-25: a past count; the pg_stat estimate was about 1,057,000 rows at 2026-10-26 14:21Z); `updated_at`, `departure_time` and `arrival_time` are epoch floats). Run them read-only, SELECT only, through the container's own credentials:
> `podman exec corporatetraveldc-pgsql sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SET statement_timeout=30000;" -c "<query>"' </dev/null`
> The struck SQLite versions are kept under each Postgres one.

**Query 1 — same-callsign history across ALL routes (detects multi-leg rotations):**
```sql
SELECT flight_id, origin, destination, status, registration,
       to_timestamp(updated_at) AT TIME ZONE 'UTC'     AS updated_utc,
       to_timestamp(departure_time) AT TIME ZONE 'UTC' AS filed_dep_utc
FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
ORDER BY updated_at DESC LIMIT 10;
```
~~`SELECT flight_id, origin, destination, status, raw_json, datetime(updated_at,'unixepoch','localtime') FROM flight_events WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>' ORDER BY updated_at DESC LIMIT 10;`~~ (SQLite, struck 2026-10-15; `raw_json` dropped from the default select because it is large)

Airlines commonly reuse ONE flight number for multiple legs of the same aircraft's rotation in a single day. If the most recent rows show the aircraft completing a DIFFERENT leg that just landed at the ORIGIN of the leg you care about, that's "still on the ground / airborne on a prior leg, hasn't re-filed yet" — report it as such, don't just say "not found."

**The opposite, equally real trap: shuttle-frequency routes reuse the SAME flight number for MULTIPLE INDEPENDENT departures on the same route, same day** (confirmed live 2026-08-31: a United shuttle-route flight into KIAD (flight number and origin generalized 2026-10-15 for the public mirror) had an `active` FDPS row with real live position — 4800ft, descending, near IAD — hours before the actual departure being tracked, because that flight number runs multiple times daily on that route, not because it was mid-rotation). This is NOT the same failure mode as Step 1a's regional-callsign case or the multi-leg rotation above — here `flight_num`/`airline` genuinely, correctly matches, it's just a *different instance* of that number from earlier or later the same day. An `active` hit under the right flight number and route is NOT sufficient confirmation you've found the right departure — cross-check the row's actual `updated_at` timestamp and position against the SCHEDULED time ~~from FIDS~~ (2026-10-18: FIDS is not a time source) from TFMS / the FDPS plan (or what the operator told you; 2026-10-15: the plan's own filed departure, `departure_time`, is stored too) before treating it as live status for the flight you're tracking. Hex-locking to a same-day-but-wrong-instance hex is exactly the 2026-07-23 mistake (see Design notes below) — verify before locking, don't just take the first `active` row.

**Query 2 — has the SPECIFIC route been filed yet today:**
```sql
SELECT flight_id, status, registration,
       to_timestamp(updated_at) AT TIME ZONE 'UTC' AS updated_utc
FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
  AND origin='<ORIGIN_ICAO>' AND destination='<DEST_ICAO>'
  AND updated_at >= extract(epoch FROM date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC');
```
~~`… AND date(updated_at,'unixepoch','localtime')=date('now','localtime');`~~ (SQLite, struck 2026-10-15). "Today" here is the UTC day; an evening ET departure is already the next UTC day, so widen to the last 24 h (`updated_at > extract(epoch FROM now()) - 86400`) when the flight is late in the ET evening.

**Query 3 — recurrence + cancellation-rate check (trailing 30 days):**
```sql
SELECT status, COUNT(*) FROM flight_events
WHERE airline='<ICAO_AIRLINE>' AND flight_num='<NUM>'
  AND origin='<ORIGIN_ICAO>' AND destination='<DEST_ICAO>'
  AND updated_at > extract(epoch FROM now()) - 30*86400
GROUP BY status;
```
~~`… AND updated_at > unixepoch('now','-30 days')`~~ (SQLite, struck 2026-10-15). `flight_events` keeps about 30 days (`flight-cleanup` archives older rows), so this is the whole window.

If this route/flight-number has a high cancelled/dropped fraction, say so in the debrief and log a durable note to the second brain if it's a genuinely notable pattern (`poller/skills/transport_pattern_digest.py` territory) — don't let it evaporate after just this one chat response.

**DB access notes:**
- ~~Live DB sees heavy write contention from concurrent SWIM ingest containers — use a short `.timeout` (`sqlite3 -cmd ".timeout 3000"`) and retry rather than blocking indefinitely;~~ (corrected 2026-10-15: Postgres has no SQLite write lock; set `statement_timeout` as above instead.) `airline`+`flight_num` is indexed (~~`idx_flight_events_callsign`, plus `idx_flight_events_airline_num`~~ (corrected 2026-10-25: superseded — `idx_flight_events_airline_num` (airline, flight_num) only; the duplicate `idx_flight_events_callsign` was dropped by `src/common/pg_schema/0078_drop_duplicate_flight_events_index.sql`, absent from `pg_indexes` 2026-10-26 14:22Z)), a bare `flight_id LIKE` scan is not and can time out on this table's size (~~900K+~~ about 970K rows, 2026-10-15 (unverified 2026-10-25: a past count; about 1,057,000 by the pg_stat estimate at 2026-10-26 14:21Z)) — always filter on `airline`/`flight_num` directly.
- `raw_json` is the raw FIXM/NAS XML — `departurePoint`/`arrivalPoint` under `<departure>`/`<arrival>` give the actual leg's airports, more reliable than the summary columns for multi-leg detection.

---

## Gate / baggage lookup (2026-10-18; was "FIDS lookup")

```
GET http://100.x.x.x:8000/api/v1/flights/<IDENT>/assignments?origin=<ORIG>&destination=<DEST>&date=<YYYY-MM-DD>
GET http://100.x.x.x:8000/api/v1/flights/<IDENT>/<YYYY-MM-DD>/assignments
```
Tier 1 ~~(tailnet or a token)~~ (corrected 2026-10-25: a `cert`-tier or higher bearer token; network origin grants no tier — a tailnet call without a token gets 403 `This endpoint requires tier tier1`, live 2026-10-26 14:18Z; `src/web/routes/assignments.py:72-89`, `src/auth/auth.py:61-84`). `<IDENT>` is the marketing or operating number (`UA1234`, `UAL1234`; codeshares resolve through `codeshare_map`); `date` is the scheduled departure date in UTC. **Always 200** -- a field no source has reads `unknown`, never a 404, so a FIDS outage is not "no gate answer". Per field -- `arrival_gate`, `arrival_terminal`, `arrival_baggage`, `departure_gate` -- it returns `value`, `status` (`observed` | `provisional` | `conflicting` | `unknown`; earlier values are listed under `superseded`), `source` (~~SWIM TDES | ACARS | AeroAPI | MWAA FIDS~~ (corrected 2026-10-25: SWIM TDES | SWIM TFDM | ACARS | AeroAPI | MWAA FIDS)), `observed_at` (UTC) and `freshness`. Precedence: ~~SWIM TDES > ACARS~~ (corrected 2026-10-25: SWIM TDES = SWIM TFDM, same tier) > ACARS (airframes.io = Jumpseat) > AeroAPI (fallback, budgeted) > MWAA FIDS (DCA/IAD only; the only DC carousel source besides proven ACARS formats). `conflicting` = two equal-tier sources disagree: say so, do not pick one. Top-level `fields` = the first (newest) leg; `legs` lists every match -- pass origin/destination/date to narrow. A LADD-listed flight answers `unknown` everywhere.

Nothing here is a time, a status, a tail or an OOOI phase; never feed it to Step 1d or the watchlist add.

~~```
GET http://100.x.x.x:8000/api/v1/fids/<DCA|IAD>/<IATA><FLIGHT_NUM>
```
No auth required (Tier 0). Returns `status`/`gate`/`terminal`/`baggage`/`scheduled`/`estimated`/`tail`/`dep_airport`/`dep_gate`/`dep_terminal` when MWAA has the flight.~~ (2026-10-18: `GET /api/v1/fids/...` is now a FIDS diagnostic returning gate / terminal / baggage / dep_* only -- no status, time or tail -- and 404s when MWAA has no row. Use the assignments route above.) ~~Often the *best* early source for a flight that hasn't departed yet~~ ~~— it has the assigned tail before ADS-B or FDPS have anything, which unblocks Step 1e's hex-based tracking immediately via Step 1d's registry lookup~~ ~~(corrected 2026-10-15: for schedule, gate and baggage only. Its `tail` is not an identity source, operator 2026-10-14; never feed it to Step 1d or the watchlist add. Its `status` is not OOOI.) Optional `?date=YYYY-MM-DD`.~~

---

## Step 2: OOOI watchlist — always add [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

~~After confirming hex, immediately POST~~ (corrected 2026-10-15: as soon as the flight and leg are known, with or without a hex,) POST to add the flight to the transient watchlist:

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

(2026-10-15) `hex_id` and `registration` are optional: omit them when no local ADS-B or SWIM source has shown the airframe yet, and never fill them from FIDS. ~~`tail` is accepted as an alias of `registration`.~~ (2026-10-18: a `tail` field is ignored and never stored -- the response says `ignored_fields: ["tail"]`; send `registration` from a SWIM source only.) Also optional: `scheduled_departure`, `scheduled_arrival` (ISO, with offset), `auto_remove_at`, `notes`. Sending `origin` matters: it is the only check that stops the route filling the identity from a stale earlier plan (Step 1c).

~~Returns **201**, always~~ (corrected 2026-10-25: Returns **201** on success; 403 without an admin token (`require_admin`), 400 on a blank identifier, 422 on a malformed body) — re-adding the same flight refreshes it in place (`entry_id` deterministic, `INSERT … ON CONFLICT(id) DO UPDATE`, `src/common/db.py`). Never branch on a `409`; there isn't one. The response carries `ontime_history_14d` — surface it in the debrief when it isn't `{"insufficient_data": true}`.

Use `/api/v1/watchlist/flights` (`FlightWatchlistRequest`, `src/web/routes/watchlist.py`) — **not** bare `POST /api/v1/watchlist` (a different, legacy Tier-1 endpoint, `start_watchlist()`, that silently drops `hex`/`registration`/`destination_icao` and writes to the separate, effectively-dead `watchlist_sessions` table instead of `watchlist_entries`).

Both this route and Step 3/4/5's `/admin/push-alert` require an **admin-tier** bearer token, and every call is audit-logged (`watchlist.flight.add`, `admin.alert.push`). Never paste a literal token into this file — resolve it from the environment at call time.

---

## Step 3: Baggage claim push — on approach or landed [IMMUTABLE]

> ⚠️ **IMMUTABLE STEP** — This step must not be removed, skipped, or modified by any code commit or skill update unless the operator explicitly requests it.

**Fire this step whenever flight phase is DESCENT, APPROACH, or GROUND** (alt_baro < 8000ft OR baro_rate < −500 fpm with alt < 15000ft, OR alt_baro < 1000ft) **and the aircraft is actually flying the leg being tracked** — not a prior rotation leg under a different callsign (see Step 1a's regional-operator note; check the destination in the live `ac`/FDPS data matches, not just that a hex-locked aircraft is airborne somewhere).

~~Check MWAA FIDS first (see FIDS lookup above, if dest is DCA or IAD). If `baggage` is present → use it, label push `[FIDS]`.~~ (2026-10-18) Read the assignments route first (see "Gate / baggage lookup" above). If `arrival_baggage.status` is `observed` or `provisional` → use its value, label the push with its `source` (`[MWAA FIDS]`, `[ACARS]`, `[AeroAPI]`; add `provisional` when it is). Take the gate from `arrival_gate` the same way (`TBD` when `unknown`; on `conflicting` write both values and `unconfirmed`).

If no source has the baggage claim (`unknown` or `conflicting`), estimate:

| Condition | Domestic (dest ICAO starts with K) | International |
|---|---|---|
| alt_baro < 1000ft (GROUND / just blocked in) | +15 min | +35 min |
| alt_baro 1000–5000ft (short final / rollout) | +20 min | +40 min |
| alt_baro 5000–8000ft (approach) | +30 min | +50 min |

If an ETA is available from the watchlist entry ~~or FIDS~~ (2026-10-18: or TFMS / TBFM -- never FIDS, which is not a time source), use `ETA + buffer` instead.

**Push to `flight-alerts` with priority 4 (HIGH):**
```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer <admin-tier token>
Content-Type: application/json

{
  "topic": "flight-alerts",
  "priority": 4,
  "title": "<CALLSIGN> [<HEX>] -- BAGGAGE CLAIM",
  "message": "<CALLSIGN> <REG>: GATE <GATE|TBD> | BAGGAGE <CAROUSEL|est ~HH:MM> (<phase>, <ALT_BARO>ft) -- <DEST_ICAO> [<assignments source>|est]"
}
```
Example (synthetic registration; flight number generalized 2026-10-15 for the public mirror): ~~`AAL<n> Q7FJP: GATE D38 | BAGGAGE 11 | InAir -- est 23:09Z/19:09 ET -- KDCA [FIDS]`~~ (2026-10-18: no FIDS status word -- `<phase>` is the tracked phase from Step 1) `AAL<n> Q7FJP: GATE D38 | BAGGAGE 11 (approach, 4200ft) -- KDCA [MWAA FIDS, provisional]` (time label corrected 2026-10-15: UTC + ET, never ET alone)

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
Format: `DAL<n> [6f***] Q1XPP -- 33.6N 84.6W 6825ft 254kts CLIMB | OOOI: watching` (hex masked 2026-10-15; flight number generalized 2026-10-15 for the public mirror). If the flight hasn't departed yet, skip the position fields entirely rather than padding — `<CALLSIGN> [<HEX>] <REG> -- not yet airborne. sched ~~<HH:MM> EDT~~ <HH:MM>Z/<HH:MM> ET <DEST>. OOOI: watching` (corrected 2026-10-15: UTC + ET) fits the cap and doesn't fabricate telemetry that doesn't exist yet. With no hex yet, write `[hex pending]`.

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
Registration:     <REG>  (source: FDPS | TDES plan | TBFM | TFMS | pending; never FIDS)
ICAO hex:         <HEX>
Type:             <TYPE> (<YEAR>)
Operator:         <OPERATOR>
Lat / Lon:        <LAT 6dp>N, <LON 6dp>W
Alt baro/geo:     <ALT_BARO>ft / <ALT_GEOM>ft
Ground speed:     <GS>kts
Track:            <TRACK> deg
Baro rate:        <BARO_RATE> fpm
Squawk:           <SQUAWK>
Position source:  Local ADS-B | Local FDPS | airplanes.live fallback | unavailable
Phase:            CLIMB | CRUISE | DESCENT | GROUND  (flag if on a prior rotation leg, not yet the tracked leg)
OOOI:             Added to watchlist (<entry id>) | [SKIP] Pi offline
Destination:      <DEST_ICAO>, source: FDPS | TFMS | ADS-B | web | unknown   (2026-10-18: never FIDS)
Schedule:         sched <HH:MM>Z/<HH:MM> ET / est <HH:MM>Z/<HH:MM> ET, source: TFMS | FDPS plan | TBFM  (2026-10-18: was "Schedule (FIDS)"; FIDS is not a time source)
Gate / baggage:   arr gate <GATE> (T<T>) / baggage <CAROUSEL> / dep gate <GATE>, each with its assignments status + source (~~SWIM TDES | ACARS | AeroAPI | MWAA FIDS~~ (corrected 2026-10-25: SWIM TDES | SWIM TFDM | ACARS | AeroAPI | MWAA FIDS)) | unknown
Baggage ETA:      Carousel <N> [<assignments source>] | est ~<HH:MM>Z/<HH:MM> ET | not yet applicable -- <reason>
```

(Template time fields corrected 2026-10-15 to match this rule; they read `sched <HH:MM> / est <HH:MM> ET` and `est ~<HH:MM> local`.) Every time given is either UTC AND Eastern together (e.g. `13:50Z / 09:50 ET`) or explicitly UTC-only (`13:50Z`) -- never Eastern alone, never bare (operator standing rule, 2026-10-06; supersedes 2026-08-27). Containers AND the host run in UTC: never print `time.localtime()`/`date` output with an ET label -- convert with America/New_York explicitly.

---

## Overwater ADS-B handoff

For North Atlantic inbounds (EHAM, EGLL, LFPG → KIAD/KJFK/KBOS): ADS-B typically acquired at lat > 43°N, lon > -67°W (Maritime Canada / NE Maine). On acquisition, fire `flight-alerts`: `"<FLIGHT> ADS-B ACQUIRED: <lat>N <lon>W <alt>ft -- switching from ADS-C"`. (This box has no ADS-C/satellite feed of its own — this documents the handoff point for when local ADS-B first picks the flight up, not a live satellite lookup.)

---

## Design notes

**Source hierarchy is strict and entirely local through the first four tiers:** local ADS-B → local FDPS → local registry tables → ~~FIDS~~ the assignments route (2026-10-18; gate / terminal / baggage only) → web search, in that order, web search only as a genuine last resort. Never mix sources without labeling which one answered. (2026-10-15: the airplanes.live fallback sits after the registry tables, through the shared helpers only; ~~FIDS answers schedule, gate and baggage questions, not identity ones.~~ 2026-10-18: FIDS answers gate, terminal and baggage questions only -- through the assignments route -- never schedule, status or identity ones.)

**Regional-operator callsigns are a confirmed, not hypothetical, failure mode.** A branded flight number (DAL<n>) can be operated by a wholly different ICAO callsign (RPA<m>) for the entire flight (example numbers generalized 2026-10-15 for the public mirror). Hex-based tracking (Step 1e) is what makes this transparent — resolve the hex once (~~via FIDS tail → registry lookup, or~~ corrected 2026-10-15: via a SWIM tail → registry lookup, or a direct ADS-B/FDPS hit under either callsign) and track by hex from then on.

**Why hex/FDPS authority, not callsign — standing operator directive, 2026-07-23 (second brain: `corporatetraveldc/04-Syntheses/project-knowledge-synthesis-2026-07-23.md`, "Flight/vehicle watchlist — hex-only sweep authority"):** once a flight is locked to a hex, that hex is the *sole* authority for this entry going forward — the callsign/flight-number is notification-only from that point on, not something later sweeps re-trust. This isn't theoretical: the same day this rule was set, tracking an American flight into DCA (flight number and origin generalized 2026-10-15 for the public mirror) hit a real stale-callsign trap — a callsign lookup returned a completely unrelated aircraft sitting on the ground at DCA (a different flight that had used the same callsign earlier), while the FIDS-confirmed tail for that specific dated leg cross-referenced clean through the FAA/OpenSky registries to the actual, correct, airborne aircraft. (2026-10-15: that was the right call then; the same check today uses a SWIM-sourced tail, because FIDS tails are no longer trusted for identity.) Caught and discarded before the watchlist entry got hex-locked to the wrong airframe. Registration is airframe-bound and doesn't change mid-flight, unlike a callsign, which is only ever the *currently broadcasting* value and can be stale-cached, reused by an unrelated flight, or (see the regional-operator note above) simply not the callsign you were expecting for this leg at all — hex derived from registration is correct regardless of which callsign is live right now. `sweep_landed_flights()`/`sweep_landed_trains()` (`shared/watchlist.py`) both require a live re-check before finalizing a "dead" determination, for the same reason.

**OOOI always, no prompt:** every hifi track creates a watchlist entry. No need to ask. If already on watchlist, skip silently.

**Steps 2 (OOOI watchlist) and 3 (baggage claim) are immutable** — cannot be reverted or removed by routine code commits or skill updates, only by explicit operator instruction.

**ntfy channel split:** `flight-alerts` → short, glanceable (hex + pos + phase); baggage claim fires HERE at priority 4 (HIGH). `dispatch-debriefs` → full table, same channel as OPS brief reload.

**Pi-down fallback:** if ALL ntfy pushes and watchlist POSTs fail or time out, dump the full debrief table to chat inline. Do not ask first.

**ACARS aggregator-before-local priority is intentional, not stale (operator confirmed 2026-08-31):** `common/acars.py`'s `get_latest_phase()` checks Jumpseat, then airframes.io, before this box's own local ACARS/VDL2 receiver (acarsrouter/dumpvdl2). This is correct as designed — the local receiver's primary job is bidirectional feeder-status (confirming this box's own feed is alive and contributing upstream), not serving as the default OOOI data source. It's a genuine last-resort fallback, not something that needs to flip to local-first. Not in scope of the 2026-08-27/28 "no third-party position/lookup APIs" rule either way — that rule covers position/identity lookups (airplanes.live), not ACARS content aggregation.
