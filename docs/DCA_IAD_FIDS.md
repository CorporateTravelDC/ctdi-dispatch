# DCA / IAD FIDS Integration

**Added:** 2026-06-24

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
src/poller/fetchers/airport_fids.py -- run_for(airport) base
src/poller/fetchers/dca_fids.py     -- thin wrapper: run() -> run_for("DCA")
src/poller/fetchers/iad_fids.py     -- thin wrapper: run() -> run_for("IAD")
src/web/routes/fids.py              -- FastAPI router
```

## REST endpoints

### `GET /api/v1/fids/{airport}`
Feed health snapshot. Tier 0.

```json
{"airport": "DCA", "arrivals_count": 885, "departures_count": 928, "ts": "..."}
```

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

`dca_fids` and `iad_fids` are registered in `FETCH_SCHEDULE` at 60s interval.
Feed health appears in `GET /api/v1/feeds` alongside other feeds.
Stale threshold: 180s (3x poll interval).

## BWI

BWI is operated by the Maryland Aviation Administration, not MWAA.
Different backend (bwiairport.com -- discovery not yet done).
Not wired in this integration.
