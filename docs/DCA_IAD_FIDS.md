# DCA / IAD FIDS Integration

**Added:** 2026-06-24 · **Verified/updated:** 2026-08-23

## Discovery

MWAA (Metropolitan Washington Airports Authority) runs both Reagan National
(DCA) and Dulles (IAD) on the same Drupal + Cloudflare stack.  The FIDS
data that powers flyreagan.com and flydulles.com is available via a
plain unauthenticated JSON endpoint, gated only by a JS-set cookie.

| Airport | JSON endpoint |
|---------|--------------|
| DCA (Reagan) | `https://www.flyreagan.com/arrivals-and-departures/json` |
| IAD (Dulles) | `https://www.flydulles.com/arrivals-and-departures/json` |

**Auth:** `Cookie: flight-info=1`  (static value; no session required)  
**DCA cache:** `max-age=60, public` (CDN)  
**IAD cache:** `no-cache, private` (per-request)  
**Payload:** `{"arrivals": [...], "departures": [...]}` -- ~2.2 MB DCA, ~1.5 MB IAD

> **Note on IAD path:** The MWAA JS conditionally prefixes `/flydulles/` only
> when `window.location.pathname.split('/')[1]` equals `"flydulles"` -- i.e.
> on shared-domain embeds only.  On `flydulles.com` itself the page lives at
> `/arrivals-and-departures` so the path remains plain.  `/flydulles/arrivals-
> and-departures/json` returns 404.

## Fields of interest per arrival

| Field | Notes |
|-------|-------|
| `IATA` | 2-letter carrier code |
| `flightnumber` | Numeric string |
| `status` | `Scheduled`, `InAir`, `InGate`, `Landed`, `OutGate`, `Delayed`, `Cancelled`, `In Customs` |
| `gate` / `mod_gate` | `mod_gate` takes precedence (MWAA override) |
| `arr_terminal` | Terminal at DCA/IAD |
| `baggage` / `claim` / `claim1-3` | Carousel number -- see IAD remap below |
| `publishedTime` | Scheduled datetime |
| `actualtime` | Estimated/actual datetime |
| `mwaaTime` | MWAA override time (takes precedence over actualtime) |
| `aircraftInfo.tail_number` | Registration |
| `arrivalInfo[0].remaining_time` | HH:MM:SS when InAir |

### IAD carousel remap

The MWAA Twig template applies `transformBaggageClaim()` to IAD arrivals:
carousel IDs 16-21 and single-letter values remap to carousel 15.
`common/airport_fids.py` replicates this in `_iad_remap()`.

## Architecture

```
src/common/airport_fids.py          -- shared fetch + cache + lookup logic
src/common/flight_resolver.py       -- layered SWIM->website resolver (see /arrivals below)
src/poller/fetchers/airport_fids.py -- run_for(airport) base
src/poller/fetchers/dca_fids.py     -- thin wrapper: run() -> run_for("DCA")
src/poller/fetchers/iad_fids.py     -- thin wrapper: run() -> run_for("IAD")
src/web/routes/fids.py              -- FastAPI router (3 routes)
```

## REST endpoints

### `GET /api/v1/fids/{airport}`
Feed health snapshot. Tier 0.

```json
{"airport": "DCA", "arrivals_count": 885, "departures_count": 928, "ts": "..."}
```

Live 2026-08-23 16:48Z: DCA 818/810, IAD 759/749.

### `GET /api/v1/fids/{airport}/arrivals`

Layered arrivals lookup. Tier 0. **Not documented in earlier revisions of
this file — added here 2026-08-23 after finding it live in
`src/web/routes/fids.py:62`.**

Query params: `carriers` (comma-separated IATA codes, default all),
`within_minutes` (1–720, default 90).

Unlike the two endpoints above, this one is **not MWAA-FIDS-only**. It
calls `common/flight_resolver.py::resolve_arrivals()`, which tries
sources in order and reports which one answered:

1. **FAA SWIM** (`flight_events`, ingest-populated) — works for all three
   hubs.
2. **MWAA website FIDS** (this document's scrape) — DCA/IAD only.
3. Neither → `source_used: "none"` with an explanatory `note`.

The response always carries `source_used` (`swim` / `website` / `none`)
and a `note`, so a caller can distinguish a genuinely-empty window from a
missing source. Verified live 2026-08-23:
`GET /api/v1/fids/DCA/arrivals?carriers=AA&within_minutes=90` →
`source_used: "website"` with real AA arrivals (3915 from ATL, 5332 from
BHM, gates/terminals/baggage populated).

Airport must be in `SUPPORTED_HUB_AIRPORTS`
(`flight_resolver.py:73`, derived from `HUB_ICAO`) = **DCA, IAD, BWI** —
anything else is a 400.

### `GET /api/v1/fids/{airport}/{flight}`
Single-flight lookup. Tier 0.

```
GET /api/v1/fids/DCA/AA1557
GET /api/v1/fids/IAD/UA2085
GET /api/v1/fids/DCA/AA1557?date=2026-06-24
```

Response:
```json
{
  "airport":       "DCA",
  "iata":          "AA",
  "flight_number": "1557",
  "status":        "InAir",
  "gate":          "D38",
  "terminal":      "2",
  "baggage":       "11",
  "scheduled":     "2026-06-24 19:14:00",
  "estimated":     "2026-06-24 19:09:00",
  "remaining":     "00:38:46",
  "tail":          "N750UW",
  "dep_airport":   "BOS",
  "dep_gate":      "B15",
  "dep_terminal":  "B"
}
```

Confirmed live against AA1557 BOS->DCA (2026-06-24):
baggage=11, gate=D38, terminal=2, tail=N750UW -- matched ADS-B and
manual FIDS verification.

## Poller schedule

`dca_fids` and `iad_fids` are registered in `FETCH_SCHEDULE`
(`src/poller/main.py`) at a **300 s** interval. Feed health appears in
`GET /api/v1/feeds` alongside other feeds. Stale threshold: **600 s**
(2× the poll interval, set 2026-08-10 in `src/web/main.py` — the previous
180 s value was tighter than the real 300 s interval and guaranteed a false
"stale" for the last ~2 minutes of every cycle).

## BWI

BWI is operated by the Maryland Aviation Administration, not MWAA.
Different backend (bwiairport.com -- discovery not yet done), so **there
is no MWAA-style FIDS scrape for BWI**: no `bwi_fids` fetcher and no
`FETCH_SCHEDULE` entry. `GET /api/v1/fids/BWI` (snapshot) and
`/api/v1/fids/BWI/{flight}` do not return empty data — they **reject with
HTTP 400** `{"detail":"airport must be one of: DCA, IAD"}`, because both
routes validate against the MWAA-only airport set rather than
`SUPPORTED_HUB_AIRPORTS`. Verified live 2026-08-23 (`curl -w '%{http_code}'`
against both paths).

**But BWI is not entirely absent** — corrected 2026-08-23, an earlier
revision's flat "not wired in this integration" was misleading. BWI *is*
in `SUPPORTED_HUB_AIRPORTS`, so `GET /api/v1/fids/BWI/arrivals` is a
valid, working call that answers from **FAA SWIM** instead. Verified
live 2026-08-23: it returned `source_used: "swim"` with real
`flight_events` rows (e.g. SWA1481 from KCHS). Note the shape difference
between sources — SWIM rows carry a `flight_id` GUFI and ICAO origins
(`KCHS`) but `gate`/`terminal`/`baggage_claim` are `null`, because those
are website-FIDS-only fields.
