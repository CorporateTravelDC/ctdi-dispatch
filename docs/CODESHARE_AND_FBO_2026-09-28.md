# Codeshare Resolution + FBO / Private-Jet Lane

**Written 2026-09-28.** Code-validated against the live repo. Documents two
related-but-distinct subsystems that both grew out of the operator directive of
2026-09-27 (executive pickups at the FBO / DC-metro codeshare coverage):

1. the `codeshare_map` marketing↔operating resolution layer, and
2. the FBO / tarmac private-jet monitor lane (tail→hex).

---

## 1. Why codeshare resolution exists: FDPS files the OPERATING callsign only

FAA FDPS (SWIM/SFDPS filed flight plans) files a flight under its **operating**
callsign only — e.g. `EDV5134` (Endeavor), never the marketing/codeshare
`DAL5134` (Delta Connection) that a rider actually books and searches under.
The marketing identifier never appears in FDPS at all. This is stated verbatim
in `scripts/seed-dc-codeshare.py:6-12` and again at
`src/poller/main.py:1514-1516`.

Consequence: a marketing→operating mapping **cannot be discovered** from FDPS —
it has to be **inferred**. For US regional "Connection/Express" flights the
marketing flight number **equals** the operating flight number (confirmed live:
`DAL5134/EDV5134`, `DAL5770/RPA5770`, `AAL5265/JIA5265` — see
`scripts/seed-dc-codeshare.py:10-12`), so the only genuine unknown is the
marketing **carrier** — which mainline the regional is flying for on that leg.

A blind flight-number match is unsafe: bare `flight_num` 5265 alone matched
CAL/SKW/EDV/JIA — four different routes — on the same day
(`src/poller/main.py:1524-1527`). Number-alone guessing risks locking the
wrong physical flight onto a watchlist entry.

---

## 2. `codeshare_map` table + upsert / lookup / decay (`src/common/db.py`)

Schema `SCHEMA_V25` / `init_db_v25()` — `src/common/db.py:4795-4834`.

```
codeshare_map(
  id, marketing_carrier NOT NULL, marketing_flight_num,
  operating_carrier, operating_flight_num, origin, destination,
  confidence DEFAULT 1, source, first_seen_at, last_confirmed_at)
```
Indexed on `(marketing_carrier, marketing_flight_num)` and
`(operating_carrier, operating_flight_num)` — `db.py:4821-4824`.

- **`marketing_flight_num` / `operating_carrier` are legitimately NULL** for
  coarse carrier-level-only signals (e.g. a future AeroAPI `codeshares_iata`
  capture). Because SQLite's index treats every NULL as distinct, matching uses
  **`IS`, not `=`** throughout `upsert_codeshare_mapping()` so those don't
  silently accumulate duplicate rows (`db.py:4796-4807`, `4841-4855`).

- **`upsert_codeshare_mapping()`** (`db.py:4837-4874`): explicit
  SELECT-then-INSERT/UPDATE. On an existing pair it bumps `confidence += 1`,
  refreshes `last_confirmed_at`, and `COALESCE`s in newly-learned origin /
  destination / operating_flight_num. Every confirmation is real evidence:
  the pair was observed live in production again.

- **Lookups** — `get_codeshare_mapping_by_marketing()` (`db.py:4877-4887`) and
  `get_codeshare_mapping_by_operating()` (`db.py:4890-4900`). Both order by
  `confidence DESC` and **exclude `confidence = 0` by default**
  (`include_zero_confidence=True` overrides).

- **`decay_stale_codeshare_mappings()`** (`db.py:4903-…`, Phase 3): any mapping
  not reconfirmed within `stale_after_days` (default 90) loses `decay_amount`
  (default 1) confidence, floored at 0. **Zeroed mappings are not deleted** —
  the historical record stays, but the default-excluding lookups stop trusting
  it. Intended for a 12–24 h schedule; as of that docstring it is the callable
  primitive, **not yet wired to a timer** (`db.py:4911-4914`).

---

## 3. The DC-metro seeder — `scripts/seed-dc-codeshare.py` (NEW)

Committed `5e96779` ("feat(codeshare): seed codeshare_map from live DC-metro
traffic"). Idempotent, re-runnable, "the core a daily timer calls"
(`seed-dc-codeshare.py:2-4`). Scans `flight_events` for legs touching the
DC-metro airports `KBWI/KIAD/KDCA/KHEF/KJYO/KCGS/KFDK`
(`seed-dc-codeshare.py:39`) over a lookback window
(`DC_CODESHARE_LOOKBACK_SECONDS`, default 48 h — line 66).

**Wholly-owned regionals are auto-seeded** — deterministic, one mainline each
(`EXCLUSIVE_REGIONAL_MARKETING`, lines 45-53):

| operating | marketing | note |
|---|---|---|
| EDV | DAL | Endeavor — Delta-owned, Delta Connection only |
| JIA | AAL | PSA — American-owned, American Eagle only |
| ENY | AAL | Envoy — American-owned |
| PDT | AAL | Piedmont — American-owned |
| UCA | UAL | CommutAir — United Express only |
| QXE | ASA | Horizon — Alaska-owned |
| JZA | ACA | Jazz — Air Canada Express |

For these, `marketing_flight_num == operating_flight_num` and the seed writes
`source="dc_metro_seed"` (`seed-dc-codeshare.py:92-106`).

**Contract regionals are reported, NOT guessed** — `AMBIGUOUS_REGIONALS`
(lines 58-64): `RPA` (Republic AAL/DAL/UAL), `SKW` (SkyWest DAL/AAL/UAL/ASA),
`ASH` (Mesa UAL/AAL), `GJS` (GoJet UAL/DAL), `AWI` (Air Wisconsin AAL/UAL).
These fly for several mainlines, so the carrier is genuinely ambiguous from the
operating callsign + number alone. They are **counted and printed** for a later
route/AeroAPI disambiguation pass, never auto-mapped — "Guessing would lock the
wrong mainline onto a flight" (`seed-dc-codeshare.py:16-19`, `89-91`,
`112-116`).

Mainlines (own callsign, nothing to resolve) and private/fractional operators
(NetJets/EJA, Flexjet/LXJ, bare N-numbers) are skipped by construction
(`seed-dc-codeshare.py:22-30`, `93-94`).

---

## 4. The poller's own FDPS-fallback codeshare resolution (`poller/main.py`)

The periodic FDPS re-check (`_check_flight_fdps_cache`) resolves marketing→
operating at poll time — `src/poller/main.py:1511-1562`:

1. Try FDPS by the ident's own callsign. If found, done.
2. If not (the marketing carrier never appears in FDPS), parse
   `ident` into `(marketing_carrier, marketing_num)` and **prefer a confirmed
   `codeshare_map` mapping** via `get_codeshare_mapping_by_marketing()`
   (top by confidence), then look up FDPS under `{op_carrier}{op_num}`
   (`main.py:1537-1543`).
3. Only if no mapping exists yet does it fall back to the broad
   `get_flight_plan_by_flight_num(origin=…)` discovery scan
   (`main.py:1544-1546`).
4. A successful fallback hit **seeds `codeshare_map`** via
   `upsert_codeshare_mapping(..., source="fdps_periodic_recheck_fallback")`
   (`main.py:1551-1559`), so future ticks for that marketing identifier go
   straight to the precise path — same shape `add_flight_watchlist` writes at
   add time (`src/web/routes/watchlist.py:198-212`, the other opportunistic
   seeder referenced in the schema comment `db.py:4801-4803`).

This is the live, self-reinforcing loop: every FDPS fallback that fires has just
**proven** a marketing identifier and an FAA-filed operating identifier are the
same physical flight, and records it.

---

## 5. FBO / tarmac private-jet monitor lane (tail→hex via hifi-track)

Per the same 2026-09-27 operator directive (executive pickups at the FBO),
private-jet arrivals into the DC-metro fields are a **separate monitor lane**,
explicitly NOT part of codeshare resolution — private/fractional operators have
no marketing↔operating codeshare, so they do not belong in `codeshare_map`
(`seed-dc-codeshare.py:24-30`).

**Resolution path — tail → hex → track:** a private tail (N-number) resolves to
its ICAO mode-S hex through the FAA registry, then the flight-hifi-track path
follows the hex. This is live:

- `faa_lookup_by_n_number()` — `src/common/db.py:3605-3616` — returns the
  `faa_aircraft_registry` row, which carries `mode_s_hex` (table + index:
  `db.py:3426-3444`, ~316k rows per `db.py:3561`). LADD privacy status is
  attached (`faa_is_ladd()`, `db.py:3633-3640`).
- The reverse, `faa_lookup_by_hex()` (`db.py:3619-3630`), backs hex→tail for
  display. `_extract_aircraft_hex_registration()` (`db.py:4448`) and
  `update_watchlist_hex_registration()` (`db.py:7322`) carry the pairing on
  watchlist entries.
- The `flight-hifi-track` skill (`skills/flight-hifi-track/SKILL.md`) is the
  high-fidelity per-tail follow path.

### FBO / ramp-parking PREDICTOR — this is a SPEC, not built

The **polygon layer has been acquired**; the **predictor has not been built.**

- **Acquired:** `data/dc-metro-aeroway.geojson` — 343 features
  (266 `gate`, 51 `apron`, 26 `terminal`), `source: "OpenStreetMap via
  Overpass"`, `fetched: 2026-09-28T01:05:47Z`, spanning KDCA/KIAD/KBWI plus the
  GA/reliever fields KFDK/KJYO/KHEF. Committed `7866014`
  ("data(fbo): DC-metro apron/terminal/gate footprints from OpenStreetMap").
- **Not yet built:** there is no code that consumes the geojson — no
  track→tail correlation into a footprint, no per-tail FBO-visit history, and
  no local/remote model that predicts which FBO/ramp a given inbound tail will
  park at. That correlation + history + model work is pending.

Treat everything in this last subsection as **intended design plus acquired
data**, not live behavior.
