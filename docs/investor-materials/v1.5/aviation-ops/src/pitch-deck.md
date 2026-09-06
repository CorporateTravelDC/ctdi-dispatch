## CTDI — Dispatch Intelligence for Aviation Ops

- A self-hosted ops-desk intelligence layer for Part 135 charter, HEMS programs, FBOs, trip-support desks, and airline ops/dispatch
- All six FAA SWIM data services as native push feeds — deterministic go/no-go scoring — real airline-reported on-time data — on edge hardware you own
- Built and operated by a single founder — [operator LLC], LLC, Arlington, VA
- Investor briefing — aviation-ops overview — August 2026
- Every claim re-verified live against the production system 2026-08-24; economics re-derived live 2026-09-03
- DECISION-SUPPORT ONLY. Not an FAA-certified dispatch system; not a substitute for a certificated dispatcher or operational control; no regulatory claim. Part 135.609 minimums used solely as a published threshold reference.

## The problem — the ops desk flies blind between fragmented sources

- TFRs and VIP movements: pop-up TFRs can strand a launch decision made an hour earlier — 121 active FAA TFRs at a single check
- Weather at minimums: METAR/TAF, AIRMETs, convective SIGMETs, ITWS wind-shear — each in a different tool, none scored against your minimums
- NAS status and flow: ground stops, GDPs, and ATCSCC ops plans arrive as separate advisories, not a single go/no-go signal
- On-time reality: most trackers show estimate-vs-estimate delay proxies, not real airline-reported times
- The critical go/no-go call is made on gut feel; when a client, insurer, or lawyer asks "why did you roll?", there is no defensible record

## The solution — one deterministic score over the FAA's own feeds

- CTDI ingests aviation, weather, and airspace data — including all six FAA SWIM data services under real, approved credentials
- Computes the CPS: a deterministic 6-factor go/no-go score anchored to published Part 135.609 minimums — a 288-line auditable rule engine, not ML, not a black box
- Fuses TFR, NAS status, and ITWS terminal weather into a single GREEN / MARGINAL / VIOLATED state, worst factor wins
- Pushes real-time alerts to operators via self-hosted channels; 21 on-device LLM models write narrative briefs at $0 cloud spend — measured
- Runs entirely on ~$765 of edge hardware you own — no cloud dependency for core ops

## The six-feed SWIM push architecture — native ingest, not a wrapper

- FDPS — nationwide flight-plan / flight-event data (839,101 events; 32,459 in 24 h; 1,270 airlines)
- STDDS — surface and terminal movement, ASDE-X class (120,071 surface-movement events)
- TFMS — flow programs: ground stops, GDPs, airspace flow programs (24,588 NAS program records; AFP parsing added 08-22)
- TBFM — arrival metering and meter-fix sequencing (35,615 sequences)
- ITWS — terminal wind-shear / microburst alerts; severity ≥ 5 escalates the score to VIOLATED
- AIM / FNS — digital NOTAMs by push (5,424 NOTAMs, 308 facilities)
- Six separate Solace PubSub+ sessions, each in its own resource-capped container; push primary with REST fallback, 30 s heartbeats, backlog fast-forward on reconnect; a 19-feed registry surfaces every feed's age and error state honestly

## The differentiator — real airline-reported on-time data, not a proxy

- Since 2026-08-20: CTDI durably captures TFMS's genuine airline-reported OOOI times (out/off/on/in) plus TFMS's own original schedule
- On-time delay is computed as actual OOOI vs. authoritative schedule — a real measurement, not the estimate-vs-estimate proxy most products show
- 14-day on-time history per flight number surfaced on the watchlist API, plus a delay-drift flag (oldest-third vs. newest-third average, past 10 min) that catches a flight sliding from 5 to 18 min late while its on-time rate barely moves
- At OUT: forced hex/registration lock + one-time push with a live tracking link; at OFF: real departure delay auto-extends the watch window
- Honest by construction: watchlist-gated capture, no backfill possible (nothing retained before 08-20), young dataset today (2 matched records at check) — the API returns "insufficient data," never a guess; regression-tested (a synthetic 5→28 min progression correctly flagged +20 min)

## The CPS engine — a deterministic go/no-go score

- Six factors, worst factor wins — no weights, no ML (cps_recompute.py, 288 lines, read in full during verification)
- Ceiling ≥ 1,000 ft, Visibility ≥ 3 SM, Wind ≤ 30 kt — Part 135.609 VFR references, each with a marginal band
- Precipitation classified by type/intensity; Ground Stop = VIOLATED; GDP = MARGINAL; ITWS wind-shear/microburst severity ≥ 5 = VIOLATED
- Output: GREEN/GO · MARGINAL · VIOLATED — recomputed hourly and on demand, with a plain-language narrative and trend history
- Why deterministic matters: every state change traces to an explicit published threshold — explainable to clients, insurers, and counsel, the opposite of a black-box AI score
- Verified GREEN/GO live 2026-08-24. Candor: the engine still has no dedicated unit test (parser/watchlist suites exist)

## Live proof — verified 2026-08-24

- /healthz ok, snapshot age 3 s; CPS GREEN/GO at check
- 839,101 flight events; 120,071 surface-movement events; 35,615 TBFM arrival sequences; 24,588 NAS program records; 5,424 NOTAMs / 308 facilities; 121 active TFRs
- NWS push writing real DC-area alerts; own-RF ADS-B 17 aircraft in view at ~108 msg/s; ACARS/VDL receive current to the minute
- 272 LLM briefs in 7 days (41.7% deterministic-fallback rate disclosed); audit log 3,384 entries in 24 h
- 51,070 Python LOC, 102 REST routes, 635 single-author commits, 218 tests (217 pass, 1 known pre-existing)

## Feed reliability — a governed edge deployment, stated honestly

- The whole plant is one Raspberry Pi 5 on residential power and ISP — real constraint, managed openly
- An automatic thermal/load governor sheds ingest under contention and restores it when pressure clears — 10 clean shed/restore cycles in ~32 h at the check, each ~9–11 min, every one self-restoring
- A shed feed shows as cleanly stopped, not failed — surfaced by the platform's own 19-feed freshness registry, never hidden
- Reliability story = verified self-restore + per-feed freshness surfacing + push/REST failover. What it is NOT: an SLA — none exists, and these materials do not claim always-on availability
- Governor tuning and true multi-node redundancy are documented, priced roadmap items

## The economics — measured, then honestly split

- Runs on ~$765 one-time hardware + ~$22–38/yr electricity; $0 data-feed fees (SWIM is free to approved subscribers); $0 cloud-LLM spend — measured over 35,217 calls, 57 days
- Honest avoided cost for this instance: ~$2.2–2.7k/yr net (conservative, mostly reciprocal barter) — never presented as the big number
- Subscription-replacement floor for a buyer of this vertical without FAA vetting: ~$32.0k–157.6k/yr — the high end now anchored by the first citable FlightAware Firehose price (~$148k–224k/yr, U.S. Secret Service federal contract; Spire ~$159k–200k/yr corroborates); FAA SWIM data itself is $0 by the FAA's own policy
- Independent single-vertical figure — a strict subset of the platform-wide ~$55.2k–230.4k/yr band, never summed with it
- The point for an ops desk: the data you actually dispatch on is not for sale (next slide)

## What money cannot buy — the aviation-ops moat

- TFMS: ground stops, ground-delay programs, airspace flow programs — no commercial product exists at any price
- TBFM arrival metering / meter-fix sequencing — none
- ITWS terminal wind-shear / microburst alerts — none (generic aviation weather is a different data class)
- Unfiltered blocked-aircraft (LADD) visibility: own RF is not FAA-source-derived, not LADD-bound; every commercial feed obfuscates — paying more makes it strictly worse
- Receive-side ACARS/VDL — no third-party receive feed exists globally (RF reception real and current; DB fusion pends an off-box fix — disclosed)
- A permanently-owned longitudinal corpus — commercial term licenses require destruction on exit

## Security and trust posture

- Controls re-tested live on a recurring cadence: bounded, non-destructive passes 2026-08-13 and 2026-08-24
- Every open finding from the prior pass closed and independently re-verified — including its highest-severity issue
- Authorization held on every probe: anonymous, forged-token, malformed, and spoofed-origin requests all rejected (403)
- Bearer-token-only auth (no network-origin trust); SHA-256 token storage; owner-only secrets; signed 706-file manifest gating execution and inference; 32 audit-logged admin endpoints
- The find-fix-verify loop demonstrably works: same-day findings fixed same-day, then survived a hostile adversarial re-verification — nothing reopened
- Stated plainly: founder-run self-assessment, not a third-party audit; no certification claimed; first external pentest is a funded roadmap item

## Licensing model — BSL 1.1

- Business Source License 1.1 adopted 2026-08-24 — Licensor: [operator LLC], LLC (same structure as MariaDB, HashiCorp, Sentry)
- Free always for non-production use; free in production for personal self-hosting and internal relay/middleware use
- Commercial license required the moment CTDI touches any fee-based client service — even invisibly, even bundled into a retainer
- Hosted resale, white-labeling, and platform absorption always require a commercial license
- Each version converts to GPL v3-or-later after four years (current Change Date: 2030-08-24) — protected now, credibly open later
- Disclosed: use-grant language under legal review, not yet counsel-confirmed

## Current state — honest tiers

- LIVE and VERIFIED: six SWIM feeds, CPS engine, real OOOI on-time capture, alerting, own-RF receive, LLM briefs, audit trail, integrity chain
- LIVE with disclosed caveats: feeds duty-cycled under load governance (10 cycles in ~32 h — no SLA); 41.7% LLM fallback rate; on-time dataset young (no backfill possible); ACARS DB fusion pending
- ROADMAP / ABSENT — stated plainly: multi-tenancy, billing, CRM, backup/DR, CI, CPS unit test, maritime AIS (fully dormant), demand (runsheet shows one trip)
- Withdrawn claims: MCP integration (retired 2026-08-18), always-on availability, live public demo link (gating decision pending)

## Roadmap

- Productize: multi-tenancy, billing/entitlements, backup and DR, CI, CPS unit test, security-hygiene closure
- Commission a first third-party security assessment
- Formalize data rights (fallback sources under agreement) and finalize BSL terms with counsel
- Sign 2–3 aviation-ops design partners (Part 135 desks, HEMS, FBOs, trip-support) via the replay-demo motion; co-define pricing
- First engineering hire to retire bus-factor-1
- Mode expansion, founder-authoritative targets: maritime AIS by end 2026; eVTOL operational awareness spring 2027

## Founder and the ask

- the operator — USMC veteran; owner, [operator LLC], LLC, Arlington, VA — built CTDI to run his own dispatch operation and dispatches on it daily
- Security practitioner: the tiered auth, audit trail, signed manifest, and scrub gates are his daily working environment, not a compliance checkbox
- The ask — pilot partners: Part 135 charter desks, HEMS programs, FBOs, and trip-support providers willing to run CTDI as a decision-support layer beside their existing tools and shape the aviation-ops roadmap
- The ask — seed conversation: capital to close the honestly-stated gaps (CI, CPS test, backup/DR, tenant isolation, data-rights formalization, a second engineer) and a first third-party security assessment. No valuation or revenue projection is presented — the fact base does not support one, and we will not invent numbers
- Diligence access: live production walkthrough over screen-share, the replay demo, the five 2026-08-24 verification documents, and the signed manifest and audit log
- Contact: info@example.com
