---
name: "hub-arrivals-lookup"
description: "Look up forward-looking arrivals (carrier/time-window filtered) at DCA, IAD, BWI, or Union Station. Use for \"what flights are landing at X in the next N minutes\", gate/baggage/carrier questions, or Amtrak status at WAS."
---

## Hub arrivals lookup (DCA / IAD / BWI / Union Station)

Updated 2026-07-20 with the new layered SWIM+website+AeroAPI resolver. Prefer the MCP tools below over the raw script now that they exist.

### Aviation (DCA / IAD / BWI)

**Primary path — `mcp__corporatetravel-dispatch__dispatch_get_fids_arrivals`** (if the corporatetravel-dispatch MCP is connected):
Three-tier layered resolver, in order (first non-empty wins):
1. FAA SWIM (`flight_events` table, ingest-populated) — primary, all three hubs
2. MWAA airport-website FIDS scrape — fallback, DCA/IAD only (BWI isn't MWAA-operated)
3. FlightAware AeroAPI — fallback, all three hubs (funded 2026-07-20, key at `~/.secrets/flightaware_aeroapi.key` → `dispatch-secrets.env` `FLIGHTAWARE_API_KEY`)

Call with `airport` ('DCA'/'IAD'/'BWI'), optional `carriers` (comma IATA codes, e.g. 'AA,UA'), optional `within_minutes` (default 90). Response tells you which tier served the data (`source_used`) and why via a `note` field — a genuinely-empty window looks different from a missing-source situation.

**Generalized path — `get_airport_arrivals_tool`** (agentic-tools MCP, works for ANY airport, not just DC-area):
Two-tier: MWAA website (DCA/IAD only, free) → FlightAware AeroAPI (any airport, needs `FLIGHTAWARE_API_KEY` env var set for the agentic-tools process). No SWIM tier here — that's specific to the dispatch platform's own ingest. Use this when asking about a non-DC airport, or when the dispatch platform itself isn't reachable.

**Fallback — raw script** (`scripts/hub_arrivals_lookup.py` on the Pi, DCA/IAD only, no AeroAPI/SWIM):
```
PYTHONPATH=src python3 scripts/hub_arrivals_lookup.py --airports DCA,IAD --carriers AA,UA --within 90
```
Kept for when neither MCP is available; doesn't have the AeroAPI or SWIM tiers.

### Parser status (as of 2026-07-20 — check before trusting "source_used: swim")

- TBFM (arrival sequencing/metering) — **fixed, confirmed live**. Real schema was `env > tma > air[apt=DCA/IAD/BWI] > eta[mfx,eta_rwy]`, nothing like the original guessed tags.
- TAIS (terminal radar tracks) — **fixed, confirmed live**. Real schema was `TATrackAndFlightPlan > src > record > track[trackNum,lat,lon,reportedAltitude,reportedBeaconCode]`.
- FDPS (the feed that populates `flight_events`, i.e. the SWIM tier arrivals actually come from) — **confirmed broken**, targets FIXM 4.2 namespaces but the live feed is FIXM 3.0. Needs a real rewrite, not yet done. Until fixed, DCA/IAD effectively run on the website tier and BWI runs on AeroAPI, same as before the SWIM work started.
- SMES (ASDE-X surface tracks) — unconfirmed, no real sample captured yet.
- TFMS (NAS programs) and ITWS (terminal weather) — not investigated this session.

### Union Station / Amtrak (WAS)

Different data shape entirely (train delay/status, not gate-scheduled arrivals) — use `mcp__corporatetravel-dispatch__dispatch_get_amtrak` directly. Not unified into the airport arrivals tools; don't try to fake-fit trains into an airport arrivals shape. No fallback source is wired for Amtrak beyond the existing amtraker.com-backed API — a future fallback (Amtrak's own site scrape, a third-party aggregator) is a design placeholder, not built.

### Known gaps

- BWI's only *free* aviation source is AeroAPI right now (MWAA doesn't operate it; SWIM would cover it for free once FDPS is fixed, since KBWI is already in the ingest geo-filter's core-airport allowlist).
- AeroAPI is paid/metered — the layered resolvers only call it when the free tiers came up empty, so a healthy SWIM+website day means it's rarely invoked.

