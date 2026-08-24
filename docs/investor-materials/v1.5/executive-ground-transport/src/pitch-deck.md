## CTDI — flight and train intelligence for executive ground transport

- Real-time flight, rail, weather, and airspace intelligence — relayed into the dispatch software you already run
- A relay/middleware layer by design and by license: CTDI feeds LimoAnywhere-class dispatch platforms, it never replaces them
- Built and operated by a chauffeur-operation principal — [operator LLC], LLC, Arlington, VA
- Investor briefing — executive ground transportation — August 2026
- Every claim re-verified live against the production system 2026-08-24

## The problem: dispatch finds out last

- An executive fleet's margin lives in the gap between when the traveler actually lands and when the dispatcher finds out
- The inbound slips; the chauffeur idles at the curb; wait-time policy collides with the client experience; later pickups cascade
- Today the picture is stitched from FIDS screens, airline apps, Amtrak pages, weather sites, and phone calls — none of it pushes to the people on the curb
- The dispatch software of record (LimoAnywhere and its peers) manages reservations and drivers superbly — it does not watch the national airspace system
- The data that decides the day (ground stops, flow programs, arrival metering, real airline-reported delays) is not in any consumer app

## The solution: a relay into your existing stack

- CTDI ingests all six FAA SWIM data services under real, approved credentials, plus Amtrak, weather, TFR/NOTAM, and its own RF receivers
- Reservations flow in via a credential-gated LimoAnywhere webhook or a simple watchlist API — no code changes on the dispatch side
- Watched flights and trains push real-time alerts to dispatcher and driver phones over self-hosted channels, deduplicated, throttled per topic
- Your reservations, clients, billing, and driver assignments never move — CTDI is the intelligence feed, not a second dispatch system
- The relay boundary is contractual, not just architectural: the license itself is written around the relay/middleware relationship

## Dispatch-timing intelligence — new and live-verified

- Real airline-reported on-time history: actual out/off/on/in times from FAA TFMS against the airline's own schedule — actual-vs-schedule, not estimate-vs-estimate — returned on the watchlist-add call at reservation time
- Delay-drift flags: oldest-third vs newest-third trend per leg (10-minute default) — catches a flight trending worse while its on-time rate still looks fine
- Identity resolution at pushback: the moment the airline reports OUT, CTDI resolves the actual airframe and pushes a one-time live tracking link
- Delay-extended watch windows: a flight departing two hours late is watched two hours longer — never silently dropped mid-inbound
- Disclosed: history capture is watchlist-gated and began 2026-08-20 — young data, honestly labeled insufficient until it accumulates

## Live proof — verified 2026-08-24

- /healthz ok, data snapshot age 3 seconds; CPS risk score GREEN/GO at check
- 839,101 flight events (32,459 in 24 h, 1,270 airlines); 822,317 Amtrak train events
- 121 active TFRs; 5,424 NOTAMs across 308 facilities; live METARs; NWS push weather writing real DC-area products
- 24,588 NAS program records (ground stops, GDPs, airspace flow programs); 35,615 TBFM arrival-metering sequences; 120,071 surface-movement events
- Deterministic 6-factor go/no-go score (288-line auditable rule engine, Part 135.609-anchored) — explainable to a client, an insurer, or a lawyer
- 51,070 Python LOC, 102 REST routes, 635 single-author commits, 218 tests (217 pass, 1 known)

## The economics

- Runs on ~$765 one-time hardware + ~$23-39/yr electricity; $0 data-feed fees; $0 cloud-LLM spend — measured over 25,147 logged calls, 46 days
- Purchasable subset of live capability lists at ~$55k-113k/yr in commercial subscriptions — a floor, since the largest equivalents are quote-only
- Honest split: actual avoided cost is ~$2.2-2.7k/yr (mostly barter); the $55k-113k figure is replacement cost for a buyer without FAA vetting — never presented as avoided spend
- Self-hosted means client movement data never leaves the operator's premises to get the intelligence

## What money cannot buy — and a dispatcher needs

- TFMS ground stops, ground-delay programs, and airspace flow programs — no commercial product sells them
- TBFM arrival metering and ITWS terminal wind-shear alerts — none
- Unfiltered blocked-aircraft visibility: own RF is not LADD-bound; every commercial feed obfuscates exactly the tails an executive fleet meets
- Real airline-reported OOOI delay data tied to your own watchlist, accumulating into a permanently-owned history
- A longitudinal operational corpus you own outright — commercial term licenses require destruction on exit

## Security and trust

- Controls re-tested live on a recurring cadence: bounded, non-destructive passes 2026-08-13 and 2026-08-24
- Every open finding from the prior pass closed and independently re-verified — including its highest-severity issue
- The find-fix-verify loop demonstrably works: new 08-24 findings fixed same-day and survived a hostile adversarial re-verification — nothing reopened
- Bearer-token-only auth, hash-only credential storage, full admin audit trail, GPG-signed whole-tree execution gating
- Stated plainly: founder-run self-assessment, not a third-party audit; no certification claimed; first external pentest is a funded roadmap item

## Licensing — BSL 1.1, built around your integration model

- Free, always, for evaluation, development, and testing — pilot CTDI against your live dispatch stack at no cost, full source
- Free in production for internal relay use that serves no fee-based client work (e.g. a corporate travel desk dispatching its own executives)
- The controlling rule, stated accurately: production use in connection with any fee-based client service — even as an invisible relay step, even bundled into a retainer — requires a commercial license; for a for-hire fleet, that is the deliberate revenue boundary
- White-labeling, hosted resale, and platform absorption always require a commercial license
- Each version converts to GPL v3-or-later after four years (current Change Date: 2030-08-24) — protected now, credibly open later
- Disclosed: use-grant language under legal review, not yet counsel-confirmed

## Current state — honest tiers

- LIVE and VERIFIED: six SWIM feeds, Amtrak, weather/TFR/NOTAM, watchlist alerting, on-time history and identity resolution, CPS engine, audit trail, integrity chain
- LIVE with disclosed caveats: feeds duty-cycled under load governance on a single node (10 shed/restore cycles in ~32 h, each self-restoring — no SLA); on-time history young and watchlist-gated
- CODE-COMPLETE, not field-proven: the LimoAnywhere/RingCentral/3CX webhook receivers — real, credential-gated code, no live vendor tenant exercised yet
- ROADMAP / ABSENT — stated plainly: multi-tenancy, billing, backup/DR, CI, maritime AIS (fully dormant), demonstrated demand (runsheet shows one trip)
- Withdrawn claims: MCP integration (retired 2026-08-18), always-on availability, public demo link (gating decision pending)

## Business status

- Pre-revenue by construction: no billing or customer-account code; the company site deliberately does not market CTDI yet
- Reference deployment: the founder's own chauffeur operation, running continuously since June 2026 — built by the operator it serves
- Data-rights model: each client fleet holds its own source credentials (FAA SWIM, weather); CTDI ships software, not redistributed data
- Demo machinery real: 2.45 GB replay corpus, production isolation adversarially verified 2026-08-24; access arranged in diligence conversations

## Roadmap

- Field-prove the LimoAnywhere webhook path end-to-end with 2-3 design-partner fleets; add vendor-native HMAC verification
- Productize: multi-tenancy, billing/entitlements, backup and DR, CI, security-hygiene closure
- Commission a first third-party security assessment
- Finalize BSL terms with counsel; formalize or replace community/scraped fallback data sources
- First engineering hire to retire bus-factor-1
- Mode expansion, founder-authoritative targets: maritime AIS by end 2026; eVTOL operational awareness spring 2027

## The ask

- Seed capital and/or design-partner fleets to turn a verified platform into a business
- No valuation, revenue projection, or market-size figure is presented — the fact base does not yet support one, and we will not invent numbers
- What you get today: a running, re-verified, integrity-gated platform; dispatch-timing data no subscription sells; a license built around the relay model; and an evidence culture built for diligence
- Next step: live walkthrough of the production system and demo replay — info@example.com
