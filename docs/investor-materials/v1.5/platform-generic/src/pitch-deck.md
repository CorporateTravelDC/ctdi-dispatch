## CTDI — Corporate Travel Dispatch Intelligence

- A 24/7 multi-domain dispatch-intelligence platform: air, rail, weather, airspace
- Self-hosted on ~$765 of owned edge hardware; zero cloud dependency for core ops
- Built and operated by a single founder — [operator LLC], LLC, Arlington, VA
- Investor briefing — platform overview — August 2026
- Every claim re-verified live against the production system 2026-08-24; economics re-derived live 2026-09-03

## The problem

- A single ground or air movement depends on flight status, rail status, weather minimums, and airspace restrictions — today those live in separate apps, tabs, and phone calls
- The critical go/no-go dispatch call is made on gut feel; when a client, insurer, or lawyer asks "why did you roll?", there is no defensible record
- Executive-services and security-conscious operators handle movement data they cannot push into third-party SaaS — sovereign options barely exist
- The data that matters most (FAA flow programs, arrival metering, terminal wind-shear alerts) is not for sale from any commercial vendor at any price

## The solution

- CTDI ingests aviation, rail, weather, and airspace data — including all six FAA SWIM data services under real, approved credentials
- Computes the CPS: a deterministic 6-factor go/no-go score anchored to published Part 135.609 minimums — a 288-line auditable rule engine, not ML, not a black box
- Pushes real-time alerts to operators via self-hosted channels; local LLM writes narrative briefs (21 on-device models, $0 cloud spend — measured)
- Accumulates a permanently-owned operational corpus and knowledge graph that compounds daily
- Built to run the founder's own executive-chauffeur operation; packaged for white-label use by ground-transport and executive-services operators

## How it works

- Ingest: 19-feed registry — 6 FAA SWIM services, NWS/NWWS-OI push weather, TFR/NAS status, Amtrak, airport boards, own-RF ADS-B and ACARS/VDL receivers
- Intelligence: deterministic CPS engine, watchlists with real airline-reported on-time history, entity tracking, scheduled LLM briefs
- Memory: 6,742-document knowledge vault under a compiled semantic layer — 99 concepts, 51,317 edges, causal derivation graph, recompiled daily by timer
- Integrity: GPG-signed 706-file manifest gates skill execution and inference; 32 audit-logged admin endpoints; bearer-token-only auth
- Platform: one Raspberry Pi 5, rootless containers, ~38 running at check, automatic load-shed governance with verified self-restore

## Live proof — verified 2026-08-24

- /healthz ok, snapshot age 3 seconds; CPS GREEN/GO at check
- 839,101 flight events (32,459 in 24 h, 1,270 airlines); 822,317 train events; 5,424 NOTAMs; 121 active TFRs
- 35,615 TBFM arrival-metering sequences; 120,071 surface-movement events; 24,588 NAS program records
- Own-RF: 17 aircraft in view at ~108 messages/s; ACARS/VDL receive current to the minute
- 272 LLM briefs in 7 days (41.7% deterministic-fallback rate disclosed); audit log 3,384 entries in 24 h
- 51,070 Python LOC, 102 REST routes, 635 single-author commits, 218 tests (217 pass, 1 known)

## The economics

- Runs on ~$765 one-time hardware + ~$22-38/yr electricity; $0 data-feed fees; $0 cloud-LLM spend — measured over 35,217 logged calls, 57 days
- Purchasable subset of live capability lists at ~$55k-230k/yr in commercial subscriptions — still a floor: the largest equivalents (FlightAware Firehose, Spire) are now priced from federal-contract records; the remainder stay excluded after a documented exhausted search
- Honest split: actual avoided cost is ~$2.2-2.7k/yr (mostly barter); the $55k-230k figure is replacement cost for a buyer without FAA vetting — never presented as avoided spend
- Ten live capabilities are not purchasable at any price (next slide)

## What money cannot buy

- TFMS: ground stops, ground-delay programs, airspace flow programs — no commercial product exists
- TBFM arrival metering and ITWS terminal wind-shear alerts — none
- Unfiltered blocked-aircraft visibility: own RF is not LADD-bound; every commercial feed obfuscates
- Receive-side ACARS/VDL — no third-party receive feed exists globally
- A permanently-owned longitudinal corpus — term licenses require destruction on exit
- Scheduled LLM briefs over a private multi-source corpus with an auto-built concept graph
- Signed whole-tree execution gating and per-skill content-hash gates

## Security and trust posture

- Controls re-tested live on a recurring cadence: bounded, non-destructive passes 2026-08-13 and 2026-08-24
- Every open finding from the prior pass closed and independently re-verified — including its highest-severity issue
- Authorization held on every probe: anonymous, forged, malformed, and spoofed-origin requests all rejected
- The find-fix-verify loop demonstrably works: new findings on 08-24 were fixed same-day and survived a hostile adversarial re-verification — nothing reopened
- Stated plainly: founder-run self-assessment, not a third-party audit; no certification claimed; first external pentest is a funded roadmap item

## Licensing model — BSL 1.1

- Business Source License 1.1 adopted 2026-08-24 — Licensor: [operator LLC], LLC
- Free always for non-production use; free in production for personal self-hosting and internal relay/middleware use
- Commercial license required the moment CTDI touches any fee-based client service — even invisibly, even bundled into a retainer
- Hosted resale, white-labeling, and platform absorption always require a commercial license
- Each version converts to GPL v3-or-later after four years (current Change Date: 2030-08-24) — protected now, credibly open later
- Disclosed: use-grant language under legal review, not yet counsel-confirmed

## Current state — honest tiers

- LIVE and VERIFIED: six SWIM feeds, CPS engine, alerting, own-RF receive, LLM briefs, audit trail, knowledge layer, integrity chain
- LIVE with disclosed caveats: feeds duty-cycled under load governance (10 shed/restore cycles in ~32 h — no SLA); 41.7% LLM fallback rate; ACARS DB fusion pending
- ROADMAP / ABSENT — stated plainly: multi-tenancy, billing, CRM, backup/DR, CI, CPS unit test, maritime AIS (fully dormant), demand (runsheet shows one trip)
- Withdrawn claims: MCP integration (retired 2026-08-18), always-on availability, live public demo link (gating decision pending)

## Business status

- Pre-revenue by construction: no billing or customer-account code; the company site deliberately does not market CTDI yet
- Reference deployment: the founder's own operation, running continuously since June 2026
- Four adjacent verticals identified: executive ground transport, corporate travel and concierge, aviation ops decision support, executive protection
- Data-rights model: each client holds its own source credentials; CTDI ships software, not redistributed data — template-supported today
- Demo machinery real: 2.45 GB replay corpus, isolation adversarially verified; access arranged in diligence conversations

## Roadmap

- Productize: multi-tenancy, billing/entitlements, backup and DR, CI, security-hygiene closure
- Commission a first third-party security assessment
- Formalize data rights and finalize BSL terms with counsel
- Sign 2-3 design partners via the replay-demo motion; co-define pricing
- First engineering hire to retire bus-factor-1
- Mode expansion, founder-authoritative targets: maritime AIS by end 2026; eVTOL operational awareness spring 2027

## The ask

- Seed capital and/or strategic partners to turn a verified platform into a business
- No valuation, revenue projection, or market-size figure is presented — the fact base does not yet support one, and we will not invent numbers
- What you get today: a running, re-verified, integrity-gated platform; a permanently-owned data corpus; a defensible economic story; and an evidence culture built for diligence
- Next step: live walkthrough of the production system and demo replay — info@example.com
