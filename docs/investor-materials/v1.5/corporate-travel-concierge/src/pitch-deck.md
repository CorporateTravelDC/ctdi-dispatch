## CTDI — Corporate Travel Dispatch Intelligence

- Know about the disruption before your traveler does
- A 24/7 self-hosted dispatch-intelligence platform that watches every principal's flight and Amtrak train, scores operational risk, and pushes the alert — built to sit behind your existing booking system
- For corporate travel management, executive concierge, and traveler-care teams
- [operator LLC], LLC · Arlington, VA · Investor briefing — August 2026
- Every claim re-verified live against the production system 2026-08-24; economics re-derived live 2026-09-03

## The problem — today, the traveler finds out first

- Disruption intel is scattered: airline apps, airport boards, rail status, weather, airspace closures — no travel desk or EA can watch them all, for every principal, all day
- Care is reactive: the desk usually learns of a delay when the traveler calls, by which point the recovery options (earlier train, alternate airport, driver re-route) have narrowed
- Monitoring is manual: browser tabs and group chats, coverage depending on who is at the desk and what they happen to refresh
- The booking system knows the itinerary but does nothing proactive with it once the reservation is made

## The solution — one dispatch desk that never blinks

- The moment a reservation is created, CTDI can begin watching that traveler's flight and train automatically
- It scores operational go/no-go risk on a deterministic, auditable engine and pushes the alert the instant a watched trip degrades
- It plugs in behind your booking/livery software as a relay — retrieving and submitting data — rather than replacing it
- Self-hosted end to end: aviation, rail, weather, and airspace data in; real-time push out; no third-party notification SaaS in the path
- Built to run the founder's own executive-chauffeur operation; packaged for travel and concierge operators

## Integration model — watch on booking, notify on change

- Reservation webhooks (CODE-COMPLETE): LimoAnywhere, RingCentral, 3CX receivers — each shared-secret-gated, off until you provision it; a new reservation auto-adds the trip to the watchlist
- Direct watchlist API (LIVE): POST a flight or train to /api/v1/watchlist — idempotent, returns real 14-day airline on-time history where available
- Cron poll (LIVE): a platform without webhooks polls its own bookings API and syncs the same endpoints
- Permanent entries: recurring principals live as JSON watchlist files that hot-reload within about a minute — no restart
- Transient entries auto-expire, and expiry is delay-aware: a late departure extends the tracking window so a traveler is never dropped mid-air

## Multi-modal coverage — flights and trains, live

- Flight watch (LIVE engine): a phase machine (pre-departure to out to off to on to in) that never reverts; forced identity resolution at pushback locks the tail number and fires a one-time push with a live tracking link
- Real airline-reported on-time history (LIVE, young by design): 14-day OOOI-based departure/arrival delay per watched flight, plus delay-drift flags — accumulates from 2026-08-20, no backfill, so early "insufficient data" is expected and honest
- Rail watch (LIVE and VERIFIED): live Amtrak trains with delay minutes and positions; watchlist-station logic surfaces only what your desk cares about; region is portable (DC today, documented porting guide)
- Maritime/vessel: NOT live — zero data, empty watchlist, no unit; roadmap only, claimed nowhere

## The alert flow — what happens when a trip is disrupted

- Push feeds stamp freshness heartbeats every 30 s; the notification sender polls the database every 30 s and fires on a watched-entry change
- FAA TFMS SWIM push reports actual pushback faster than a periodic sweep — CTDI resolves the aircraft identity at that moment, not on its 120 s cycle
- Every alert is a dual push (domain topic plus a concise everything-feed) with 5-minute content-aware de-duplication — no spam
- Self-hosted push server: 14-topic catalog plus escalating per-family and per-zone topics with per-topic throttles
- Delivery is observable: a 19-feed freshness registry exposes each feed's age, staleness threshold, and error state

## Live proof — verified 2026-08-24

- /healthz ok, data snapshot age 3 seconds; risk state GREEN/GO at check; 38 rootless containers running
- 839,101 flight events (32,459 in 24 h, 1,270 distinct airlines, 30-day retention); 822,317 train events
- 5,424 NOTAMs across 308 facilities; 121 active FAA TFRs; 35,615 TBFM arrival-metering sequences
- All six FAA SWIM data services provisioned with real credentials; all 7 ingest containers active and writing at check
- 272 LLM briefs in 7 days (41.7% deterministic-fallback rate disclosed); audit log 3,384 entries in 24 h
- 51,070 Python LOC, 102 REST routes, 635 single-author commits, 218 tests (217 pass, 1 known)

## The intelligence layer — a brief and an auditable score

- AI daily ops brief (LIVE): 6-hour risk history with trend direction, generated on-device by a local LLM — no cloud dependency; degrades gracefully and honestly when the model is busy
- Deterministic risk score (LIVE and VERIFIED): 6-factor go/no-go (ceiling, visibility, wind, precipitation, airspace, traffic programs), worst-factor-wins, anchored to published Part 135.609 minimums as a conservative reference
- Deliberately NOT machine learning: a 288-line rule engine your clients, insurers, and lawyers can read — explainable, not a black box
- A compounding memory: a 6,742-document second-brain knowledge vault under a compiled semantic layer (99 concepts, 51,317 edges), recompiled daily — seasonal patterns your dashboard cannot show

## The economics

- Runs on ~$765 one-time hardware plus ~$22-38/yr electricity; $0 data-feed fees; $0 cloud-LLM spend — measured over 35,217 logged calls across 57 days
- Purchasable subset of this vertical's live capability (flight status API + rail realtime + traveler-care platform + push) lists at ~$4.2k-64.3k/yr, mid ~$16.7k — an independent single-vertical figure, never summed with the platform-wide ~$55.2k-230.4k/yr band
- No product in that basket watches a booking's specific flight and train from a reservation webhook — the integration is the product
- Honest split: actual avoided cost is ~$2.2-2.7k/yr (mostly barter); the $4.2k-64.3k figure is replacement cost for a buyer without FAA vetting — never presented as avoided spend
- Ten live capabilities are not purchasable at any price: FAA flow programs (TFMS), arrival metering (TBFM), terminal wind-shear alerts (ITWS), unfiltered blocked-aircraft visibility, receive-side ACARS, a permanently-owned corpus, and the integrity gates

## Security and trust posture

- Controls re-tested live on a recurring cadence: bounded, non-destructive passes 2026-08-13 and 2026-08-24
- Client travel data sits behind a bearer-token-only boundary that held on every probe: anonymous, forged, malformed, and spoofed-origin requests all rejected
- Every open finding from the prior pass closed and independently re-verified — including its highest-severity issue
- The find-fix-verify loop demonstrably works: new findings on 08-24 were fixed same-day and survived a hostile adversarial re-verification — nothing reopened
- Stated plainly: founder-run self-assessment, not a third-party audit; no certification claimed; a first external pentest is a funded roadmap item

## Licensing — BSL 1.1, and honest about the paid-service trigger

- Business Source License 1.1, Licensor [operator LLC], LLC — free always for non-production use
- Free in production for personal self-hosting and purely-internal relay/middleware use by an org of any size
- The important line for a concierge firm: a commercial license is required the moment CTDI touches a fee-based client service — even solely as an invisible internal relay, even when bundled into an overall fee or retainer (clause iv, the controlling rule)
- Hosted resale, white-labeling, and platform absorption always require a commercial license — this is the deliberate revenue boundary
- Each version converts to GPL v3-or-later after four years (Change Date 2030-08-24); use-grant language is under legal review, not yet counsel-confirmed

## Current state — honest tiers

- LIVE and VERIFIED: six SWIM feeds, watchlist engine, rail tracking, self-hosted alerting, risk engine, daily brief, audit trail, knowledge layer, integrity chain
- LIVE with disclosed caveats: real airline on-time history (young, no backfill); feeds duty-cycled under load (10 shed/restore cycles in ~32 h — no SLA); 41.7% LLM fallback rate
- CODE-COMPLETE: reservation webhooks (LimoAnywhere/RingCentral/3CX), awaiting per-vendor credentials
- ROADMAP / ABSENT — stated plainly: multi-tenancy, billing, CRM, backup/DR, CI, CPS unit test, maritime AIS (dormant), demand (runsheet shows one trip)
- Withdrawn claims: MCP integration (retired 2026-08-18), always-on availability, live public demo link (gating decision pending)

## Where the business stands

- Pre-revenue by construction: no billing or customer-account code; reference deployment is the founder's own operation, running continuously since June 2026
- the operator — USMC veteran, Arlington VA; the founder is the user, dispatching real principals on this system
- Solo builder: 635 commits, single author; bus factor of one is a real risk and a reason the ask includes a first hire
- Data-rights model: each client holds its own source credentials; CTDI ships software, not redistributed data — template-supported today
- Demo machinery real: 2.45 GB replay corpus, isolation adversarially verified; access arranged in diligence conversations

## The ask

- Design partners: 2-3 corporate travel desks, EA/concierge services, or travel platforms to pilot booking-driven watchlists, proactive alerting, and the reservation-webhook loop against real programs — and to co-define pricing with us, not at them
- Seed conversation: capital to take a working single-operator platform to a multi-tenant product — tenant isolation, billing and entitlements, backup/DR, CI, security-hygiene closure, and a first engineering hire
- No valuation, revenue projection, or market-size figure is presented — the fact base does not yet support one, and we will not invent numbers
- Next step: live walkthrough of the production system and demo replay — operator@example.com · info@example.com
