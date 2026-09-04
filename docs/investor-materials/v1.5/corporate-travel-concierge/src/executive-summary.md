Executive Summary — v1.5

CTDI for corporate travel management, executive concierge, and traveler-care teams

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Every factual claim below was re-derived from a live re-verification of the running production system performed on 2026-08-24 (continuing the v1.1 to 2026-08-09 to 2026-08-24 verification chain), from a bounded security re-validation and adversarial re-verification completed the same day, and from a live-recomputed cost analysis. Counts that drift continuously (rows, containers, commits) are timestamped samples, not fixed facts. Where something is not built, it says so. Capability is graded: LIVE and VERIFIED (personally observed on the production system on 2026-08-24), CODE-COMPLETE (real code reviewed, not exercised end-to-end), or ROADMAP (intent, not capability).

# What CTDI is, for a travel desk

CTDI (Corporate Travel Dispatch Intelligence) is a 24/7 self-hosted operational dispatch-intelligence platform built and operated by the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA). It ingests aviation, rail, weather, and airspace data — including all six FAA SWIM data services under real, approved credentials — computes a deterministic go/no-go operational risk score, and pushes real-time disruption alerts. It was built to run the founder's own executive-chauffeur operation and is being packaged for adjacent operators, including corporate travel management companies and executive-concierge services.

For a travel desk or concierge team, the proposition is proactive traveler care: the moment a booking is created in your reservation system, CTDI can begin watching that traveler's flight and Amtrak train; it knows about the disruption — a delay, a ground stop, a diverted arrival — before the traveler calls to ask; and it pushes the alert to your desk while the recovery options (earlier train, alternate airport, driver re-route) are still open. The platform is designed to sit behind your existing booking and livery software as a relay, not to replace it.

The whole-stack story, at current scale: a solo founder-operator designed, built, secured, and runs a multi-domain intelligence platform — 51,070 lines of Python, a 27-component React progressive web app, 102 REST route registrations, 21 dedicated local LLM models, and a compiled semantic knowledge layer — on a single Raspberry Pi 5 at the network edge. 635 commits, June 7 through August 24, 2026, single author. The signed 706-file code manifest was re-verified the morning of this document's fact-check.

# How it plugs into your reservation system

CTDI's integration model is deliberately thin: it watches what you tell it to watch, and it tells you when something changes. Three concrete paths exist today.

- **Inbound reservation webhooks (CODE-COMPLETE, awaiting per-vendor credentials).** CTDI ships receiver endpoints for LimoAnywhere (`/webhooks/limoanywhere/reservations`), RingCentral (`/webhooks/ringcentral/events`), and 3CX (`/webhooks/3cx/events`). Each is credential-gated by a shared-secret header and returns HTTP 503 until its per-vendor secret is configured — off by default, on only when you provision it. When a reservation is created, the traveler's flight or train is added to the watchlist automatically. The receiver code is written and reviewed; it awaits per-vendor credentials to run end-to-end.
- **Direct watchlist API (LIVE).** Any booking platform can call `POST /api/v1/watchlist/flights` (or `/trains`) with an admin bearer token to arm tracking directly — for example on flight identifier, origin, destination, and an auto-remove time. The add is idempotent (a repeat add is a clean refresh, not an error), and the response returns a 14-day real airline-reported on-time history for that flight number where enough data exists.
- **Cron poll (LIVE surface).** A reservation platform without native webhooks can poll its own bookings API on a schedule and sync the same watchlist API. No CTDI-side change is required.

Entries come in two forms. **Permanent** watchlist entries (recurring principals) are JSON files that hot-reload within about a minute, no restart. **Transient** entries (a single trip) are added over REST and auto-expire on their own schedule — and that expiry is now delay-aware: if a watched flight departs late, CTDI extends the tracking window off the real departure delay rather than dropping the traveler while they are still in the air.

# What happens when a trip is disrupted

The alert path is short and self-hosted end to end. Push feeds stamp freshness heartbeats every 30 seconds; the notification sender polls the database every 30 seconds and fires the moment a watched entry degrades. For flights, TFMS's SWIM push reports actual pushback (the "OUT" moment) far faster than a periodic sweep would, and CTDI now forces identity resolution at that moment — locking the aircraft's hex/registration and firing a one-time "resolved identity" push with a live tracking link, so your desk gets a confirmed tail number and a click-through the instant the aircraft moves. Every alert fires as a dual push (a domain topic plus a concise everything-feed), with five-minute content-aware de-duplication so a desk is not spammed by the same event. Delivery is via a self-hosted push server with a 14-topic catalog plus escalating per-family and per-zone topics; flight, train, risk-state, and ops-brief channels are all first-class.

# Verified capability snapshot (2026-08-24)

| Capability | Observed live 2026-08-24 | Status |
| Service health | /healthz ok, data snapshot age 3 seconds; risk state GREEN/GO; 38 rootless containers running at check (count swings ~30-40 by design as timers fire and load-governors act) | LIVE and VERIFIED |
| Multi-modal tracking | Flight watchlists with a phase machine (pre-departure to out to off to on to in) that never reverts; live Amtrak train tracking with delay minutes and positions; watchlist-station logic | LIVE (rail); LIVE watchlist engine, real airline on-time history deployed 2026-08-24 |
| Real airline-reported on-time history | 14-day real OOOI-based departure/arrival delay per watchlisted flight number, plus delay-drift flags; surfaced on the watchlist API response | LIVE (young by design — accumulates from 2026-08-20, no backfill possible) |
| Feed freshness registry | 19 feeds, each with age, staleness threshold, and error state | LIVE and VERIFIED |
| FAA SWIM ingest | All six SWIM data services (FDPS, TFMS, TBFM, STDDS, ITWS, AIM/FNS) provisioned with real credentials; all 7 ingest containers active and writing at check | LIVE and VERIFIED, with the availability caveat below |
| Accumulated operational corpus | 839,101 flight events (32,459 in prior 24 h, 1,270 distinct airlines, 30-day rolling retention); 822,317 train events; 5,424 NOTAMs across 308 facilities; 35,615 TBFM arrival-metering sequences; 120,071 surface-movement events; 24,588 NAS program records | LIVE and VERIFIED |
| Airspace and weather | 121 active FAA TFRs at check; live METARs; NWS/NWWS-OI push alerts writing real DC-area products | LIVE and VERIFIED |
| Self-hosted push alerting | 14-topic catalog plus per-family/zone topics with per-topic throttles; dual push with 5-minute content-aware dedup | LIVE and VERIFIED |
| Reservation webhooks | LimoAnywhere, RingCentral, 3CX receivers, each shared-secret-gated (503 until provisioned) | CODE-COMPLETE, awaiting per-vendor credentials |
| Deterministic risk engine | GREEN/GO across 6 deterministic factors; 288-line auditable rule engine anchored to published Part 135.609 minimums as a conservative threshold reference | LIVE and VERIFIED |
| Local LLM daily brief | 21 dedicated on-device models; 272 scheduled briefs in prior 7 days; honest disclosure: 41.7% of skill LLM calls in that window ran the labeled deterministic-template fallback under contention | LIVE, with fallback rate disclosed |
| Admin audit trail | 32 audit-logged admin endpoints with actor/IP/payload capture and 90-day retention; 4,397 audit rows, 3,384 in prior 24 h (12 rows total two weeks earlier) | LIVE and VERIFIED |
| Test posture | 218 tests, 217 passing (1 known pre-existing failure, tracked); no CPS-engine unit test, no CI pipeline — both stated as gaps | DISCLOSED GAP |

**The availability caveat that travels with every feed claim:** the platform deliberately sheds its ingest tier under compute contention. In the ~32 hours before the 2026-08-24 check it executed 10 automatic shed-and-restore cycles of ~9-11 minutes each, every one restoring cleanly without intervention. Feeds are governed and duty-cycled under contention on this single node — they are not "always-on," and no SLA exists yet. Trigger calibration is a known, documented open tuning item.

# The economics: near-zero cost against a commercial subscription floor

A live-recomputed cost analysis (2026-08-24; re-derived live 2026-09-03, the source of every figure below) in which every commercial price cites a dated vendor source, business-judgment figures are flagged as assumptions, and vendors with no citable price anywhere are excluded from totals rather than guessed.

| Quantity | Figure |
| One-time hardware, actually deployed (single node, itemized BOM) | ~$765 |
| Actual recurring cost (electricity only; DC tariff re-fetched 2026-09-03) | ~$22-38 / yr |
| Recurring data-feed fees | $0 |
| Recurring cloud-LLM spend | $0 — measured: 35,217 logged LLM invocations over 56.9 days of continuous production contain zero cloud-model rows |
| Defensible avoided cost, this instance, conservative | ~$2,160-2,656 / yr net |
| Subscription-replacement floor (this vertical's purchasable subset; independent of the platform-wide ~$55,200-230,400/yr band) | ~$4,200-64,300 / yr (mid ~$16,700) |
| Live capabilities not purchasable at any price | 10 |

The one-line version: the platform runs on ~$765 of owned hardware and ~$22-38/yr of electricity, with $0 in data-feed fees and $0 in cloud-LLM spend, while the purchasable subset of this vertical's live capability — a flight status/alerts API, passenger-rail realtime, a travel-risk/traveler-care platform, and push delivery — lists at roughly $4.2k-64.3k/yr in commercial subscriptions (mid ~$16.7k). This is an independent single-vertical figure, new in the 2026-09-03 extension pass: it shares the flight/rail/push lines with the platform-wide ~$55.2k-230.4k/yr band and adds a traveler-care platform line that band does not carry — not a subset, not additive, and never summed with the platform-wide number or any other vertical's floor. The honest low end says what the analysis found: no product in this basket watches a booking's specific flight and train automatically from a reservation webhook and pushes recovery-window alerts through self-hosted infrastructure — the integration capability is the product here. Honest boundary, stated as the source analysis requires: most ingested data is free at the source to any approved subscriber, so the avoided cost is an integration cost, not a data-license cost. The $4.2k-64.3k figure is a replacement-cost floor for a buyer without FAA vetting and must not be presented as avoided spend; the honest avoided-spend number is the ~$2.2-2.7k/yr line, roughly two-thirds of which is reciprocal data-sharing barter rather than cash. Cost avoidance does not carry a valuation, and the analysis says so.

# Security posture: tested, broken, fixed, re-verified — same day

Security is treated as a running discipline, and the honest story is more credible than a clean-sheet claim. The platform's controls are re-tested against the live production system on a recurring cadence — bounded, non-destructive passes on 2026-08-13 and 2026-08-24 probed authorization boundaries, credential storage, and the integrity chain directly.

- **Every open finding from the prior (08-13) pass was closed and independently re-verified** — including its single highest-severity issue (an anonymously readable knowledge-vault surface), fixed at two independent layers and confirmed by live external request.
- **Authorization held on every probe:** anonymous, forged-token, malformed-token, and spoofed-public-origin requests were all rejected; auth is bearer-token-only, and network origin grants no tier. Client travel data lives behind that boundary. Credentials exist only as one-way hashes; the secrets file is owner-only; git history is clean of credentials.
- **The 08-24 pass found new, real issues — and the find-fix-re-verify loop worked the same day.** A remediation made that day briefly introduced two genuine regressions on the public demo surface (a production-database mount and a shared chat file). The same day's re-verification caught both; both were structurally fixed at the mount layer; and a final adversarial pass — whose stated default was that every "resolved" label is wrong until skeptically disproved under header-injection, encoding, and config-drift pressure — reopened nothing and found no new exploitable exposure, verified against the actually-running containers rather than the config files.
- **Code that runs on the platform is gated by a signed whole-tree integrity chain** that refuses to execute on any mismatch; every administrative action is audit-logged.

What this is not, stated plainly: founder-run self-assessment using a bounded, non-destructive methodology — not an external red-team, and no compliance certification (SOC 2, ISO 27001) is claimed. A first third-party penetration test is an un-started, fundable line item. Known open items are tracked and documented: credential-lifecycle hygiene (issued tokens do not yet expire; one retired integration's admin token awaits revocation), a hardening candidate around edge-header trust, and two founder policy calls on deliberately-public read surfaces. None touches the production operational feeds or the client-facing watchlist credential store.

# Licensing: Business Source License 1.1 — and what it means for a paid concierge service

The platform is licensed under the Business Source License 1.1 (Licensor: [operator LLC], LLC), verified 2026-08-24 as canonical, unmodified BSL text. The relay/middleware framing fits how a concierge team would deploy CTDI — but the fee-based boundary is precise, and this document states it accurately rather than favorably.

- **Free always** for non-production use — evaluation, development, testing.
- **Free in production** for a personal self-hosted deployment, and for internal relay/middleware use by an organization of any size that retrieves data from or submits data to CTDI for its own internal operational purposes — **provided that use never serves, supports, or contributes to any fee-based product or service rendered to a third-party client or customer.**
- **A commercial license is required** the moment CTDI is used in connection with a fee-based service to a client — and the license is explicit that this holds **even when CTDI is used solely as an internal relay or middleware step never surfaced to the client directly, and even when its cost is bundled into an overall fee, retainer, or rate rather than billed as a separate line item.** Clause (iv) of the Additional Use Grant is named the controlling rule for any paid-service scenario. For a corporate travel management company or concierge firm that charges clients for its service, using CTDI to watch those clients' trips — visibly or invisibly, itemized or bundled — falls under this clause and requires a commercial license from [operator LLC], LLC. Hosted resale, white-labeling, and platform absorption independently require a commercial license as well.
- **Becomes open source on a clock:** each version converts automatically to GPL v3-or-later four years after its first public distribution (Change Date for the current version: 2030-08-24).

Disclosed: the Additional Use Grant language is a working draft currently under legal review; it reflects intended terms but has not yet been confirmed by counsel. Contact [operator LLC], LLC to confirm current terms before relying on it.

# Honest limitations

- Single developer-operator; 635 commits over ~2.5 months, single author; no CI; bus factor of one — and a reason the funding ask includes a first engineering hire.
- Single-node hosting on one Raspberry Pi 5 on residential power and ISP; no backup system, no second node, no documented disaster recovery. Every resource limit is tuned to this one box.
- Feeds are duty-cycled under contention (10 shed/restore cycles in ~32 h at check); no SLA yet.
- ~42% of the prior week's skill LLM calls fell back to deterministic templates (labeled, monitored, alerted) — the inference layer is honest capacity with disclosed degradation, not 100% duty.
- Some inputs are fallback-tier community or scraped sources (DCA/IAD airport boards are scraped from undocumented JSON and are a fallback tier only; Amtrak is a community API) — both need formalization for commercial deployment.
- Reservation webhooks are code-complete but not yet exercised end-to-end against live vendor credentials.
- Pre-revenue: no billing, pricing, subscriptions, or customer-account code exists. Pricing will be co-defined with design partners.
- Maritime/vessel tracking is NOT live: the code path exists but has zero live data, an empty watchlist, and no running unit. It is roadmap only and is not counted anywhere in this document.
- The prior deck's "agent-native / MCP integration" claim is withdrawn: both MCP bridges were retired 2026-08-18. Integration today is via the webhooks and watchlist REST API above.

# Data rights model — client-held subscriptions

Intended commercial architecture: each client operator obtains and holds its own subscriptions and credentials for the underlying data sources (FAA SWIM, NWS/NWWS-OI, FAA NOTAM API, and similar). [operator LLC] provides the software, integration, and decision-support layer — it does not redistribute third-party data. This is template-supported today: the platform's data-source runbook contains per-source access instructions and credential-request email templates parameterized for any organization. Automated multi-tenant onboarding is not yet built — ROADMAP.

# Roadmap (intent, not capability)

- Productize: multi-tenancy and per-tenant isolation, billing and entitlements, backup and DR, CI, and closure of the documented security-hygiene items.
- Finish the reservation-integration loop: run the LimoAnywhere/RingCentral/3CX webhooks end-to-end against live vendor credentials with design partners.
- Commission a first third-party security assessment.
- Formalize data rights for the fallback sources and finalize the BSL Additional Use Grant with counsel.
- Sign 2-3 corporate travel or concierge design partners against real programs, using the replay-demo machinery, and co-define pricing.
- Mode expansion, founder-authoritative targets: maritime AIS by end 2026; eVTOL operational awareness spring 2027.

# Contact

the operator — Founder, [operator LLC], LLC · operator@example.com · info@example.com. Live production read-outs can be arranged for diligence, as was done for the 2026-08-24 verification, pentest, and adversarial re-verification passes behind this document. The demo replay corpus (2.45 GB of recorded operational history) is real; a public demo link is withheld pending a founder gating decision, so demo access is arranged in diligence conversations.
