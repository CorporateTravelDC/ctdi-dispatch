---
name: "flight-hifi-track"
description: "Handle flight tracking/status requests: resolve the current leg through local dispatch sources, add an OOOI milestone watch through the account's granted action, cross-check SWIM identity against local registries, and use the privacy-gated platform fallback when needed. Notifications require separate authorization."
---

# Flight tracking and milestone watches

Use the dispatch platform under the current account's identity. This edition
replaces the accumulated historical corrections and obsolete Step 1b env-loading
instructions. The API sequence below was exercised on-box on 2026-10-29 UTC;
that is evidence for those calls, not certification of all tracking paths.
Historical rationale remains in git and the platform casebook.

## Authentication and capability checks

- Dispatch base: `http://100.x.x.x:8000`. Network location alone grants no tier.
- A skill is instructions. Check the current account's connector tool list before
  using it; the gateway catalog reviewed for this run has eight tools and no
  flight-watch operation. `skill_read` retrieves text, not execution capability.
- On-box agents use an operator-issued personal token at their own
  `~/.config/ctdc/api-token`. It is separate from the group env subset, and
  `with-dispatch-env.sh` does not load it. Do not read production env files or
  expect `DISPATCH_ADMIN_TOKEN` in the agent subset.
- `scripts/dispatch_api.py` beside this skill implements the request-time token
  read: regular file, current UID owner, no group/other permissions, no symlink,
  nonempty content. It uses the fixed dispatch base, blocks redirects, keeps the
  bearer out of argv/output, sanitizes failures and never retries a request.
  Import `request_json` from that script in your agent-owned scratch script;
  add this skill's `scripts/` directory to Python's import path if needed.
- Public reads can use `auth=False`. Tier-gated reads and mutations require the
  account's token. Missing/denied capability means report the gap and continue
  only permitted reads; never self-mint or seek a shared/operator credential.
- Watch creation requires an admin-tier personal token whose explicit scope
  allows `watchlist.flight.add`. This is an action scope, not a human approval
  binding one exact flight. Other route tiers and actions remain independent.
- A dispatch-web 403 may mean missing, expired, revoked, wrong-tier or out-of-scope
  credentials. Do not infer the cause from that status alone. An uncertain write
  outcome requires an authorized read-back before any retry.

Operator provisioning and limits: `docs/PER_AGENT_API_ACTIONS.md` and
`docs/AGENT_SEGMENTATION.md`. No token minting is performed by this skill.

## Step 1 — establish the dated flight leg

**1a. Normalize and disambiguate.** Use the operating ICAO callsign (UA → UAL,
AA → AAL, DL → DAL), not an assumed regional carrier. If needed, use the
platform's `GET /api/v1/swim/resolve?ident=<input>` (Tier 1+) for callsign
candidates. A marketing carrier may differ from the operating carrier. Record
requested origin, destination and departure date/time when supplied. Never
invent a schedule; distinguish a prior rotation or a different day's reuse of
the same number from the requested leg.

**1b. Check the local receiver through the API.**
`GET /api/v1/adsb` is a public read of this box's receiver. Filter the returned
`aircraft` array in memory to the requested callsign and any known hex. Do not
dump all traffic. An empty match can mean the aircraft is outside local range;
it does not mean the flight or aircraft does not exist. Do not import internal
watchlist code or load production env files just to make this lookup.

**1c. Read FDPS through the API.**
`GET /api/v1/flightplan/<ICAO_CALLSIGN>` is a public, privacy-filtered read. It
returns `confirmed`, source, route, status and available departure/arrival/update
times. This route does **not** return a tail or hex. A confirmed plan can be
stale or belong to another occurrence: check its route and timestamps. Latest
surveillance messages can carry position without aircraft identity; missing
identity is not proof that an earlier stored identity is wrong.

For a recent SWIM position/leg, `GET /api/v1/swim/last-leg?ident=<callsign>`
returns the latest stored leg within 24 hours and applies privacy filtering to
public callers. Check its route/time too. Hex-keyed historical queries may be
slow: a timeout establishes no absence. No direct production SQL/container
access is required by this workflow; if an authorized read-only database path
is unavailable, report that limit and use the API.

**1d. Inspect the requested watch only.**
`GET /api/v1/watchlist` requires Tier 1+. Select the matching identifier, route
and dated occurrence in memory; do not print other entries. Inspect
`registration`, `hex_id`, `hex_source`, `hex_updated_at` and
`hex_corroborated_at` together. A stored pair is not current confirmation merely
because its source label says FDPS. No corroboration timestamp means no persisted
corroboration receipt, not proof that no real-world confirmation ever occurred.

**1e. Cross-check the local registries in both directions.**
`GET /api/v1/aircraft/<SWIM-derived-tail>` and
`GET /api/v1/aircraft/<hex>` consult local FAA and OpenSky caches. Compare
`hex_id`, `hex_mismatch`, the FAA `mode_s_hex` and OpenSky `icao24`/registration.
For a US aircraft, FAA is preferred, OpenSky corroborates. Surface disagreements;
do not silently choose a conflicting value. Registry agreement proves the
mapping in those caches, not assignment to this particular flight. Do not print
registrant names, owner, address, serial number, unrelated records or raw JSON.

**1f. Use only the platform fallback for missing identity/contact or a cross-check.**
After local receiver, FDPS and registry checks, the authenticated routes
`GET /api/v1/swim/aggregator?hex=<hex>` and
`GET /api/v1/swim/aggregator?callsigns=<ICAO_CALLSIGN>` reach airplanes.live
through the platform's shared helpers. This is a fallback/corroborator, never
OOOI authority. It honors configuration, privacy refusal, backoff and cache.
A 404 can mean disabled, blocked, backed off or no contact; it does not prove
there is no aircraft. Compare returned callsign, hex, registration, type and
`seen_pos`; record when you checked. Do not call an external tracking API
directly or bypass a refusal. Never send a protected identifier externally.

**1g. Identity acceptance.** A tail comes only from SWIM (FDPS, TDES-linked plan,
TBFM or TFMS). ADS-B/fallback contact corroborates position and association;
registries resolve tail↔hex. FIDS supplies gate/terminal/baggage only: never
tail, time, status or OOOI. A pair agreeing across registries and a fresh contact
under the requested callsign is stronger than either alone. A blank external
app search alone does not establish a platform resolution defect. On conflict,
report it; do not repoint the watch using an aggregator as sole authority.

**1h. No flight plan filed yet, but a known arrival (2026-11-03, operator).** A
marketing flight number (e.g. a UA 4xxx regional) can exist on an airport board
or another anchor (FIDS arrival, a booking, an operator-given ETA) hours before
the operating carrier files its plan. That anchor proves the leg exists and gives
origin, destination and an expected arrival; it never supplies identity, times,
status or OOOI (1g). Do not add a callsign-only watch, and do not stop at "not
found":
- Anchor order (operator 2026-11-03), strongest first; anchors ENABLE a SWIM
  monitor, they never replace SWIM identity or times:
  1. SWIM (FDPS/TBFM/TFMS) -- authority; once present, it wins.
  2. FlightStats "View Flight Details" (operator screenshot): operating carrier
     ("Operated by ..."), scheduled/estimated gate and runway times, gates,
     equipment and a tail lead. Use it to pick the operating callsign and the
     departure window.
  3. Airplanes.live Android app (operator screenshot): live callsign, hex,
     registration and type are good identity LEADS; its origin/destination is
     often stale (free-tier route lookups) -- never use it for the route.
  4. FIDS (airport board): proves the leg exists; gate, terminal, baggage only.
  A tail or hex from 2 or 3 is a lead: confirm it against the local registries
  (1e), check LADD, and accept it for the watch only with a live contact under
  the operating callsign at the anchor's origin (position, not the app's route)
  or a matching SWIM plan.
- **Same flight number, different day (2026-11-04).** Regional flight numbers fly
  the same route daily, so FDPS and `swim/last-leg` can still hold YESTERDAY's
  plan for the same callsign and route. Accept a plan only if its arrival time is
  still ahead (or within the last 30 min) and it was updated within the last few
  hours; never take a hex from a leg that has already arrived. An anchor's tail
  for today (FlightStats) that differs from the SWIM leg's is the tell.
- Record the anchor: marketing ident, origin/destination ICAO, the anchor's
  arrival time (UTC with ET), and where it came from.
- Estimate the departure window from the anchor arrival minus block time; plans
  are normally filed about 1-2 h before departure.
- Monitor, load-aware and bounded (until the anchor arrival plus a margin), for
  the operating carrier's plan: FDPS `GET /api/v1/flightplan/<PFX><number>` for
  each candidate regional prefix and any `swim/resolve` callsign, accepting only
  a confirmed plan whose origin and destination match the anchor. TBFM/TFMS
  will follow once the flight is active.
- On a match, take the hex from SWIM `last-leg` for that callsign and route
  (fallback contact only as corroboration), the tail from the local registries
  (1e), check LADD status, then add the watch (Step 2) under the OPERATING
  callsign with the verified hex. Withhold tail and hex in any report if the
  aircraft is privacy-listed.
- Report the anchor and that monitoring is running; when the watch is added,
  give the one-line confirmation. If the deadline passes with no plan, report
  that nothing was added.

## Step 2 — add the OOOI milestone watch

This step requires both authorization and aircraft identity. The operator
requires a verified hex ID (preferred) or a verified registration/tail for every
aircraft watch. Resolve the dated operating leg and its aircraft first. A
callsign-only record is incomplete and must not be presented as a ready watch.
If neither identity is verified, report **NOT ADDED — IDENTITY REQUIRED** and
continue permitted resolution; never invent an identity to satisfy the rule.
If the exact watch already exists, inspect it without an unnecessary refresh;
report a missing identity as a defect requiring reconciliation. If the action is
not granted, report **NOT ADDED** rather than treating a read as a watch.

```python
import pwd
import os
from dispatch_api import request_json

status, entry = request_json(
    "/api/v1/watchlist/flights", method="POST",
    payload={
        "identifier": "UAL1234",
        "origin": "KSFO",
        "destination": "KDCA",
        "added_by": pwd.getpwuid(os.getuid()).pw_name,
    },
)
```

The number above is a synthetic request example, not a command to execute.
The abbreviated example must be completed with a verified `hex_id` (preferred)
or `registration` before execution. Include `scheduled_departure` with a timezone
when actually known. Aircraft identity requires independently checked, current-leg
evidence from permitted sources. Never fill it from FIDS. Send origin
and destination. Watch IDs use the America/New_York departure date, or that date
of now if no departure was supplied. A missing schedule can leave occurrence
ambiguity: disclose it.

The correct add route is `/api/v1/watchlist/flights`; bare
`POST /api/v1/watchlist` is a different legacy session store. Success is **201**
(added/refreshed), not a 409 convention, future milestone or push confirmation.
Inspect the returned identity and use a read-back when appropriate; never dump
its whole response. A stale/ambiguous current-leg check is a reason to leave
the watch incomplete and blocked on identity, not invent or forcibly lock one. A client/server error is not
success. The personal action token does not grant deletion or batch addition.

## Step 3 — gate, baggage and ETA when requested

Read `GET /api/v1/flights/<ident>/assignments?origin=<origin>&destination=<dest>&date=<UTC-departure-date>`
(Tier 1+). Preserve each field's observed/provisional/conflicting/unknown status,
source and freshness. Unknown is not a missing flight. Source precedence is
SWIM TDES/TFDM, ACARS, authorized AeroAPI fallback, then FIDS enrichment.

Request a watch ETA only when asked, with `GET /api/v1/watchlist?include=eta`;
use the privacy-safe public behavior when required by the audience. It is SWIM
only: destination-center TBFM STA, then TFMS, then FDPS. A fix-crossing time names
its fix and is never relabelled runway arrival. ADS-B never establishes ON/IN.

On the correct leg's approach/landing, report available baggage assignment.
If a baggage estimate is needed, label it an estimate: domestic +15 minutes
from confirmed arrival/ground, +20 on short final, +30 on approach; international
adds +35/+40/+50 respectively. Do not apply these to a prior rotation leg.

## Steps 4–5 — notifications only with separate authorization

The watch-add grant does not grant `admin.alert.push` or a shared ntfy token.
Where the current account is separately authorized, `/admin/push-alert` uses
`flight-alerts` for a short snapshot/baggage update and `dispatch-debriefs` for
a concise debrief. Keep each message within the endpoint's 200-character cap.
HTTP **202** means queued, not delivered. Never retry a push automatically.
Without this capability, report the result in chat and label manual pushes
**NOT SENT**. Never send an approval request or public publication as a fallback.

## Step 6 — report the evidence and its limits

Report the requested flight/leg, current identity if permitted, source/time of
each observation, whether the watch was added/already present/not added, and
whether manual pushes were queued/not sent/delivery verified. Distinguish
registry mapping, SWIM identity, observed position, ETA and confirmed OOOI.
Use UTC, optionally with America/New_York beside it; never an unlabelled local
time. Include gate/baggage or ETA only as relevant/requested.

Never display a LADD-listed identifier; withhold identifiers whenever privacy
clearance is uncertain. Do not dump watch lists, raw registry responses, process
arguments, credentials or customer notes. Public aircraft links may contain
only a cleared ICAO hex (`https://globe.airplanes.live/?icao=<hex>` or
`https://map.opensky-network.org/?icao=<hex>`), never tail/callsign in a URL.

## Verification scope of this refresh

The 2026-10-29 run executed the personal-token watch add (201), requested-watch
read, FDPS plan/last-leg reads, local receiver read, bidirectional FAA/OpenSky
lookups and both platform fallback routes. Registry and live fallback identity
agreed; the operator independently attributed the empty app search to that app.
A hex-keyed SWIM history query timed out and the agent read-only DB connection
was unavailable; neither proves absence of data. No aircraft-resolution code
was changed. Future scheduled milestones, push delivery, revocation/expiry and
architecture portability were not tested by these calls. This skill update
still needs the operator's signed distribution/runtime release.
