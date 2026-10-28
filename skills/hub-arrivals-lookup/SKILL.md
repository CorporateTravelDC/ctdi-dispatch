---
name: "hub-arrivals-lookup"
description: "Look up forward-looking arrivals (carrier/time-window filtered) at DCA, IAD, BWI, or Union Station. Use for \"what flights are landing at X in the next N minutes\", gate/baggage/carrier questions, or Amtrak status at WAS."
---

## Hub arrivals lookup (DCA / IAD / BWI / Union Station)

Updated 2026-07-20 with the new layered SWIM+website+AeroAPI resolver. ~~Prefer the MCP tools below over the raw script now that they exist.~~ (corrected 2026-10-15: the `corporatetravel-dispatch` MCP server was archived 2026-08-17, so its tools no longer exist. The same resolver is served over plain HTTP; use the route below.)

### Aviation (DCA / IAD / BWI)

**Primary path (2026-10-15) — the dispatch API, Tier 0, no token:**
```
GET http://100.x.x.x:8000/api/v1/fids/<DCA|IAD|BWI>/arrivals?carriers=AA,UA&within_minutes=90
```
`within_minutes` 1-720 (default 90). It calls `common.flight_resolver.resolve_arrivals()`, ~~the same three tiers listed below~~ (corrected 2026-10-25: superseded — one tier only now, SWIM `flight_events`; `src/common/flight_resolver.py:164-206`), and returns `source_used`, `note` and `results` (live check 2026-10-15: BWI answered `source_used: "swim"`). ~~For one arrival's gate, carousel and status at DCA or IAD: `GET /api/v1/fids/<DCA|IAD>/<IATA flight>` (also Tier 0). FIDS is gate, baggage and schedule enrichment only: never OOOI, and its `tail` field is not an aircraft identity source (tails come from SWIM: FDPS, the TDES-linked FDPS plan, TBFM, TFMS).~~ (2026-10-18, operator) For one flight's gate, terminal and carousel from every source: `GET /api/v1/flights/<IDENT>/assignments` (Tier 1, always 200; SWIM TDES > ACARS > AeroAPI > MWAA FIDS; see the flight-hifi-track skill's "Gate / baggage lookup"). FIDS is gate, terminal and baggage only -- never a time, a status, a tail or OOOI: ~~the board results and `GET /api/v1/fids/...` no longer carry `status`, `scheduled` or `tail`.~~ (corrected 2026-10-25: `GET /api/v1/fids/<DCA|IAD>/<flight>` drops `status`, `scheduled`, `estimated`, `remaining` and `tail` (`src/web/routes/fids.py:44,165-166`). The arrivals board rows now come only from SWIM and do carry SWIM's `status` plus a `scheduled` key that holds the SWIM arrival time (`flight_events.arrival_time`, UTC), but no `tail` (`src/common/flight_resolver.py:144-158`; live keys 2026-10-26 14:18Z).)

~~**Primary path — `mcp__corporatetravel-dispatch__dispatch_get_fids_arrivals`** (if the corporatetravel-dispatch MCP is connected):~~ (struck 2026-10-15, archived MCP)
~~Three-tier layered resolver, in order (first non-empty wins):~~ (corrected 2026-10-25: superseded — a one-tier resolver now; only tier 1 below remains, `src/common/flight_resolver.py:164-206`.)
1. FAA SWIM (`flight_events` table, ingest-populated) — primary, all three hubs
2. ~~MWAA airport-website FIDS scrape — fallback, DCA/IAD only (BWI isn't MWAA-operated); 2026-10-18: gate / terminal / baggage only, rows picked by an airport-local time window, no status or time returned~~ (removed 2026-10-19, operator: the FIDS published time no longer selects forward-window rows; FIDS-only arrivals drop out, `source_used` is `swim` or `none`. Gate / baggage for one flight: `GET /api/v1/flights/<IDENT>/assignments`.)
3. ~~FlightAware AeroAPI — fallback, all three hubs (funded 2026-07-20, key at `~/.secrets/flightaware_aeroapi.key` → `dispatch-secrets.env` `FLIGHTAWARE_API_KEY`) (2026-10-15: still tier 3 in `flight_resolver.py:352`; whether a key value is set now was not checked, names only)~~ (removed 2026-10-18, operator: AeroAPI is only the budgeted per-watched-leg gate/baggage fallback, `common/gate_aeroapi.py`)

Call with `airport` ('DCA'/'IAD'/'BWI'), optional `carriers` (comma IATA codes, e.g. 'AA,UA'), optional `within_minutes` (default 90). Response tells you which tier served the data (`source_used`) and why via a `note` field — a genuinely-empty window looks different from a missing-source situation.

**Generalized path — `get_airport_arrivals_tool`** (agentic-tools MCP, works for ANY airport, not just DC-area; 2026-10-15: the tool still exists in the separate `agentic-management-tooling-mcp` repo, ~~`server.py:319`~~ (corrected 2026-10-25: `server.py:298` at that repo's HEAD 8f0f29f; its working tree carries uncommitted edits that put the definition at `:556`); whether that MCP is connected in a given session was not checked):
Two-tier: ~~MWAA website (DCA/IAD only, free)~~ (corrected 2026-10-25: superseded — an airport-website tier only for airports configured in `LOCAL_AIRPORT_WEBSITE_FIDS`, none when it is unset; the DCA/IAD hard-coding left at that repo's commit d2a1f6f, 2026-08-17 11:08Z; `tools/airport_arrivals.py:54-68,206-249` at HEAD 8f0f29f) → FlightAware AeroAPI (any airport, needs `FLIGHTAWARE_API_KEY` env var set for the agentic-tools process). No SWIM tier here — that's specific to the dispatch platform's own ingest. Use this when asking about a non-DC airport, or when the dispatch platform itself isn't reachable.

**Fallback — raw script** (`scripts/hub_arrivals_lookup.py` on the Pi, ~~DCA/IAD only, no AeroAPI/SWIM~~ (corrected 2026-10-25: SWIM only, through `resolve_arrivals()`; DCA/IAD/BWI, with `--airports DCA,IAD` the default; no AeroAPI; `scripts/hub_arrivals_lookup.py:58,76-79,99`)):
```
PYTHONPATH=src python3 scripts/hub_arrivals_lookup.py --airports DCA,IAD --carriers AA,UA --within 90
```
~~Kept for when neither MCP is available; doesn't have the AeroAPI or SWIM tiers.~~ (2026-10-19: it now reads `resolve_arrivals()` -- SWIM-timed arrivals only, DCA/IAD/BWI, SWIM times in UTC; no FIDS board read; `--direction departures` returns no rows.) (2026-10-18: no `status` / `scheduled` columns any more -- FIDS is not a time or status source.) (2026-10-15: run it through `scripts/with-dispatch-env.sh` from the repo root rather than sourcing env files.)

### Parser status (as of 2026-07-20 — check before trusting "source_used: swim")

> **Status (2026-10-15):** this list is history. The FDPS FIXM 3.0 path was implemented 2026-07-20 (`src/ingest/parsers/fdps_parser.py`), ~~all seven SWIM ingest containers are running~~ (corrected 2026-10-25: six SWIM-feed ingest containers plus `ingest-core`, which runs no SWIM feed (`SWIM_NMS_ENABLED=false` in its quadlet); all seven were running at 2026-10-26 14:34Z), and `flight_events` took 4,303 updates in the hour before 2026-10-15 ~01:50Z. `source_used: "swim"` is the normal answer now, BWI included.

- TBFM (arrival sequencing/metering) — **fixed, confirmed live**. Real schema was `env > tma > air[apt=DCA/IAD/BWI] > eta[mfx,eta_rwy]`, nothing like the original guessed tags.
- TAIS (terminal radar tracks) — **fixed, confirmed live**. Real schema was `TATrackAndFlightPlan > src > record > track[trackNum,lat,lon,reportedAltitude,reportedBeaconCode]`.
- ~~FDPS (the feed that populates `flight_events`, i.e. the SWIM tier arrivals actually come from) — **confirmed broken**, targets FIXM 4.2 namespaces but the live feed is FIXM 3.0. Needs a real rewrite, not yet done. Until fixed, DCA/IAD effectively run on the website tier and BWI runs on AeroAPI, same as before the SWIM work started.~~ (corrected 2026-10-15: fixed the same day, see the status note above.)
- SMES (ASDE-X surface tracks) — unconfirmed, no real sample captured yet.
- TFMS (NAS programs) and ITWS (terminal weather) — not investigated this session.

### Union Station / Amtrak (WAS)

Different data shape entirely (train delay/status, not gate-scheduled arrivals) — use ~~`mcp__corporatetravel-dispatch__dispatch_get_amtrak`~~ `GET http://100.x.x.x:8000/api/v1/amtrak` (Tier 0; corrected 2026-10-15, the MCP is archived; see `nec-train-hifi-track` for the live field list) directly. Not unified into the airport arrivals tools; don't try to fake-fit trains into an airport arrivals shape. No fallback source is wired for Amtrak beyond the existing amtraker.com-backed API (2026-10-15: amtraker.com is polled by the `amtrak-tracker` container, with the poller's REST fetcher as a fallback when its heartbeat goes stale) — a future fallback (Amtrak's own site scrape, a third-party aggregator) is a design placeholder, not built.

### Known gaps

- ~~BWI's only *free* aviation source is AeroAPI right now (MWAA doesn't operate it; SWIM would cover it for free once FDPS is fixed, since KBWI is already in the ingest geo-filter's core-airport allowlist).~~ (corrected 2026-10-15: SWIM covers BWI; AeroAPI is paid, not free, ~~and is only the last tier~~ (corrected 2026-10-25: superseded — it is no tier of the arrivals resolver at all, only the per-watched-leg gate/baggage fallback in `src/common/gate_aeroapi.py`). BWI still has no website tier.)
- AeroAPI is paid/metered — ~~the layered resolvers only call it when the free tiers came up empty, so a healthy SWIM+website day means it's rarely invoked.~~ (corrected 2026-10-25: superseded — the arrivals resolver never calls it. The live caller is the gate/baggage fallback `src/common/gate_aeroapi.py`, which asks only for a field no SWIM or ACARS source has answered (an MWAA FIDS value does not stop it), at most two calls per watched leg (pre-ETA, and post-ON within 3 h) and `AEROAPI_GATE_DAILY_CAP` calls a day (default 50; no env file sets it); `gate_aeroapi.py:37-47,91-125`. A second, position-lookup path in `src/poller/main.py:628-663,831-834` needs `FLIGHTAWARE_AEROAPI_KEY`, which no env file sets.)

