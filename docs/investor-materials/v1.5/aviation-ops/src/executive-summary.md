Executive Summary — v1.5

CTDI for Aviation Operations — Part 135 charter desks, HEMS programs, FBOs, trip-support providers, and airline ops/dispatch desks

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Every factual claim below was re-derived from a live re-verification of the running production system performed on 2026-08-24 (continuing the v1.1 → 2026-08-09 → 2026-08-24 verification chain), from a bounded security re-validation and adversarial re-verification completed the same day, and from a live-recomputed cost analysis. Counts that drift continuously (event rows, containers, commits) are timestamped samples, not fixed facts. Where a capability is young, sparse, or not built, this document says so in the body, not in a footnote.

# What CTDI is

CTDI (Corporate Travel Dispatch Intelligence) is a 24/7 self-hosted operational dispatch-intelligence platform built and operated by the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA). For an aviation operations audience the core proposition is direct: CTDI consumes all six FAA SWIM data services as native push feeds under real, approved credentials — FDPS flight data, STDDS surface/terminal tracks, TFMS traffic-flow management, TBFM arrival metering, ITWS terminal weather, and AIM/FNS digital NOTAMs — fuses them with weather, TFR, NAS-status, rail, and own-RF ADS-B/ACARS data, computes a deterministic go/no-go risk score, and pushes real-time alerts to the ops desk. The entire stack, including 21 dedicated local LLM models for narrative briefs, runs on a single Raspberry Pi 5-class edge node the operator owns — no cloud dependency for core operations, and a measured $0 in cloud-LLM spend across 25,147 logged inference calls over 46 days.

Scale at the 2026-08-24 verification: 51,070 lines of Python, 102 REST routes, 218 tests (217 passing, 1 known pre-existing failure), a GPG-signed 706-file code manifest re-verified that morning, and 635 single-author commits since June 7, 2026. The founder runs his own executive-transport dispatch operation on this system daily — it is a production tool first and a product second.

# The six-feed SWIM push architecture

Most small vendors poll REST APIs. CTDI consumes FAA SWIM the way it is designed to be consumed: six separate Solace PubSub+ sessions, one per feed, each with its own durable queue, each running in its own resource-capped container that can be stopped, started, or restarted independently without dropping the other five. Verified writing live on 2026-08-24:

| SWIM feed | What the ops desk gets | Live evidence at check |
|---|---|---|
| FDPS | Nationwide flight-plan and flight-event data | 839,101 flight events; 32,459 in the prior 24 h; 1,270 distinct airlines |
| STDDS | Surface and terminal movement (ASDE-X class) | 120,071 surface-movement events |
| TFMS | Flow programs — ground stops, GDPs, airspace flow programs — plus airline-reported OOOI times | 24,588 NAS program records; AFP parsing added 2026-08-22 |
| TBFM | Arrival metering and meter-fix sequencing | 35,615 arrival sequences |
| ITWS | Terminal wind-shear/microburst alerts | Live and writing; severity ≥ 5 escalates the risk score to VIOLATED |
| AIM / FNS | Digital NOTAMs by push | 5,424 NOTAMs across 308 facilities |

Push is primary and REST is fallback, with 30-second heartbeats and automatic failover when push goes stale; a separate guardrail timer force-refreshes on double failure. On reconnect after any outage a backlog-triage mechanism fast-forwards past stale queue content rather than replaying hours of dead data. A 19-feed freshness registry surfaces every feed's age, threshold, and error state honestly on the console — staleness is displayed, never hidden.

Independently priced context (see the economics section): no commercial vendor at any price sells TFMS flow-program data, TBFM metering, or ITWS terminal alerts. This is the data an ops desk actually dispatches on, and the only way to have it is to be an approved SWIM subscriber and do the ingestion engineering — which is what CTDI is.

# Real on-time data, not an estimate-vs-estimate proxy

New since 2026-08-20 and specifically built for this audience: CTDI durably captures TFMS's genuine airline-reported OOOI times (out, off, on, in) alongside TFMS's own original scheduled departure and arrival for every watchlisted flight. On-time performance is then computed as actual OOOI against the authoritative schedule — a real delay measurement, not the estimated-vs-estimated proxy most trackers show. The watchlist API surfaces a 14-day on-time history per flight number, and a delay-drift detector flags legs whose average delay is trending worse (oldest-third vs. newest-third comparison, flagged past a 10-minute default) — catching the flight that holds a stable on-time percentage while quietly sliding from 5 minutes late to 18.

Three honest disclosures travel with this capability. First, capture is watchlist-gated by design — a flight number accumulates history only on days it was actually watched, which avoids firehose-volume storage but means coverage follows usage. Second, no backfill exists or is possible: the platform never retained OOOI history before 2026-08-20, so the dataset is young and sparse today (the verification pass found 2 matched records), and the API says "insufficient data" honestly rather than inventing a number. Third, the drift detector needs at least 3 matched records before it reports anything. The mechanism is deployed, regression-tested (including against a synthetic 5-to-28-minute delay progression it correctly flagged as a +20-minute shift), and accumulating.

The same TFMS message stream drives two operational conveniences: the moment a watched flight reports OUT, the platform forces identity resolution (hex/registration lock) and pushes a one-time notification with a live tracking link; and the moment it reports OFF, the real departure delay automatically extends the watch window — a flight two hours late no longer expires out of its watchlist before it lands.

# The CPS engine — deterministic and auditable

CPS (Critical Predictability State) is a 288-line deterministic rule engine — not ML, not predictive AI. Six factors are evaluated against explicit thresholds anchored to the published Part 135.609 HEMS VFR minimums as a conservative, citable reference (ceiling ≥ 1,000 ft, visibility ≥ 3 SM, wind ≤ 30 kt, each with a marginal band); precipitation is classified by type and intensity; a Ground Stop scores VIOLATED and a GDP MARGINAL; ITWS wind-shear/microburst severity ≥ 5 escalates to VIOLATED. Worst factor wins, no weights. Output is GREEN/GO, MARGINAL, or VIOLATED with a plain-language narrative and trend history — every state change traces to a published threshold, which makes the score explainable to clients, insurers, and counsel. Verified computing GREEN/GO live at the 2026-08-24 check. Known gap, stated plainly: the engine still has no dedicated unit test (the 218-test suite covers parsers, watchlist, and webhooks).

# Feed reliability, honestly: a governed edge deployment, not always-on infrastructure

This platform runs on one Raspberry Pi 5 on residential power and ISP, and it manages that constraint openly rather than pretending it away. An automatic thermal/load governor sheds ingest containers under contention and restores them when pressure clears — and at the 2026-08-24 check the measured cadence was 10 shed/restore cycles in roughly 32 hours, each lasting about 9–11 minutes, every one restoring cleanly without human intervention. The verified restore path, per-feed freshness surfacing, and push/REST failover are the reliability story; an SLA is not — none exists, and these materials do not claim continuous availability. Feeds should be described as governed and duty-cycled under contention. Tuning the governor's trigger calibration is a documented open item, and multi-node deployment with real redundancy is a priced roadmap item.

# The economics — measured, then honestly split

Measured as of 2026-08-24: roughly $765 in one-time hardware, $23–39/yr in electricity, $0 in data-feed fees across all 19 registry feeds (FAA SWIM is free to approved subscribers), and $0 in cloud-LLM spend — measured from a 46-day usage log containing zero cloud-model rows, not assumed.

Two distinct numbers, never conflated. The honest avoided-cost figure for this instance is ~$2,160–2,655/yr net — conservative, published-price-only, and roughly two-thirds of it reciprocal barter from feeding our own ADS-B data to aggregators. The subscription-replacement floor — what a commercial buyer without FAA vetting would pay to replicate the purchasable subset of this live capability — is ~$55,200–112,900/yr, and it is a floor because the largest true equivalents (FlightAware Firehose, SWIM-class feeds) are quote-only and excluded. Most importantly for an aviation audience: ten live capabilities are not purchasable at any price, including TFMS flow programs, TBFM metering, ITWS terminal alerts, unfiltered blocked-aircraft visibility (own RF is not LADD-bound; every commercial feed obfuscates), receive-side ACARS, and a permanently-owned longitudinal corpus (commercial term licenses require data destruction on exit).

# Security posture — a demonstrated find-fix-verify loop, not a certificate

Stated exactly: CTDI's security record is founder-directed, bounded, non-destructive testing of the live production system — not a third-party audit, and no compliance certification is claimed. What that testing shows: a first bounded pass on 2026-08-13 found real issues, including one high-severity anonymous-read exposure; a full re-validation on 2026-08-24 confirmed every open finding closed at independent layers and everything previously working still holding (anonymous, forged-token, and spoofed-origin requests all rejected; tokens stored hash-only; secrets owner-only; a signed whole-tree manifest gating skill execution and inference). The same day's pass found new, real items — and they were fixed the same day, after which a deliberately hostile adversarial re-verification pass (default posture: every "resolved" label is wrong until disproved) reopened nothing and found no new exploitable exposure. Every administrative action is audit-logged (32 endpoints, actor/IP/payload, 3,384 rows in the 24 hours before the check). Known open items are documented rather than hidden: internal-only testing to date, token-expiry hygiene, and a first external penetration test as an explicit fundable roadmap item.

# Licensing — Business Source License 1.1

CTDI adopted BSL 1.1 on 2026-08-24 (the same structure used by MariaDB, HashiCorp, and Sentry), closing the "no LICENSE file" gap flagged in every prior verification. Licensor: [operator LLC], LLC; Change Date 2030-08-24; each version converts to GPL v3-or-later four years after first public distribution. Free always for non-production use; free in production for personal self-hosting and for internal relay/middleware use by an organization of any size — provided that use never serves a fee-based product or service to a third-party client. Hosted resale, white-labeling, platform absorption, and any use in connection with a fee-based client service (even invisibly, even bundled into a retainer) require a commercial license. Disclosed plainly: the Additional Use Grant language is a working draft under legal review; prospective commercial licensees should confirm terms directly.

# What is not built — stated plainly

No billing, subscription, or entitlement code; no multi-tenancy (white-label today means redeploy-per-operator); no backup/DR; no CI; no CPS unit test; no SLA. Pre-revenue: no external customers, and the operational runsheet records effectively one trip — output is capacity, not demonstrated demand. Maritime/AIS awareness is fully dormant (zero live data; roadmap only) and is not claimed. ACARS/VDL RF reception is real and current to the minute, but fusion of ACARS into the platform database is still pending an off-box fix — the claim is receive-side RF capability, nothing more. The platform's former MCP integration was deliberately retired 2026-08-18 and all prior MCP claims are withdrawn. The public demo returned to service 2026-08-24 after a nine-day outage with its production isolation adversarially verified, but its public access-gating policy is a pending founder decision — demo access is arranged directly in diligence conversations, not via a public link.

# Regulatory disclaimer

DECISION-SUPPORT ONLY. CTDI is not an FAA-certified dispatch system, is not a substitute for a certificated aircraft dispatcher or for operational control under an operator's certificate, and makes no regulatory claim. Part 135.609 HEMS VFR minimums are used solely as a published, conservative threshold reference inside a deterministic scoring engine.

# Next steps

Diligence access includes the five 2026-08-24 verification documents behind this summary (live re-verification, cost analysis, security passes), a live production read-out over screen-share, the replay demo, and the signed manifest and audit log. Contact: info@example.com.
