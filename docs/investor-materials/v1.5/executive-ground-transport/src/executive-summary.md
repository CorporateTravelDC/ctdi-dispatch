Executive Summary — v1.5

CTDI for executive ground transportation — flight and train intelligence, relayed into the dispatch software you already run

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Every factual claim below was re-derived from a live re-verification of the running production system performed on 2026-08-24 (continuing the v1.1 → 2026-08-09 → 2026-08-24 verification chain), from a bounded security re-validation and adversarial re-verification completed the same day, and from a live-recomputed cost analysis. Counts that drift continuously (rows, containers, commits) are timestamped samples, not fixed facts. Where something is not built, it says so.

# What CTDI is — and what it deliberately is not

CTDI (Corporate Travel Dispatch Intelligence) is a 24/7 self-hosted dispatch-intelligence platform built and operated by the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA) to run his own executive-chauffeur operation. It ingests aviation, rail, weather, and airspace data — including all six FAA SWIM data services under real, approved credentials — watches the specific flights and trains your reservations depend on, and pushes real-time alerts to dispatcher and driver phones the moment an inbound movement changes.

What it deliberately is not: a replacement for your dispatch software. CTDI is architected as a **relay and middleware layer** — it feeds real-time flight/train intelligence to and from LimoAnywhere (and, by the same pattern, any reservation or dispatch platform), and it never absorbs, rebrands, or replaces the system your operation already runs on. Your reservations, clients, billing, and driver assignments stay where they are. CTDI's job is to make sure the dispatcher knows the inbound slipped before the client does. This relay relationship is not just architecture — it is written directly into the product's license (see Licensing below).

# The integration model: webhook in, alerts out, no code changes on your side

- **Reservation created → flight watched, automatically.** A credential-gated inbound webhook (`/webhooks/limoanywhere/reservations`, shared-secret header, returns 503 until its secret is configured) accepts reservation events from the LimoAnywhere Customer API and adds the associated flight or train to CTDI's watchlist. RingCentral and 3CX phone-event webhooks follow the same pattern.
- **Or call the watchlist API directly.** `POST /api/v1/watchlist/flights` (admin bearer token) with a flight identifier, origin/destination, and an auto-expiry; `/api/v1/watchlist/trains` for Amtrak. The call is idempotent — a repeat add is a refresh, not an error. Platforms without native webhooks can poll their own reservations API on cron and sync the same way. Permanent VIP entries live in hot-reloaded JSON files.
- **Alerts reach the curb.** Watchlist hits push over self-hosted ntfy topics (flight-alerts, train-alerts, and a concise everything-feed) to dispatcher and driver phones, with 5-minute content-aware deduplication — no third-party SaaS in the delivery path.

Honest status of the webhook receivers: the LimoAnywhere/RingCentral/3CX endpoints are real, credential-gated code in the live route table, but no external vendor account has exercised them end-to-end in production — the reference deployment adds watchlist entries via the API and permanent lists. Treat the webhook path as code-complete and integration-ready, not field-proven with a live LimoAnywhere tenant.

# Dispatch-timing intelligence — what a dispatcher actually gets

This is where the platform earned its most operationally relevant capability set, all added and live-verified in the 2026-08-20 → 08-24 window:

- **Real airline-reported on-time history, not estimates.** CTDI durably captures TFMS's genuine airline-reported OOOI times (out/off/on/in) for watchlisted flights alongside TFMS's own scheduled times, and computes real departure/arrival delay from them — actual-vs-schedule, not estimate-vs-estimate. Surfaced as `ontime_history_14d` directly on the watchlist-add response, so the number is in hand at reservation time.
- **Delay drift flagging.** Per leg, the engine compares the oldest third of matched records against the newest third and flags a trend shift past a 10-minute default threshold. This answers a different question than the on-time rate: a flight can hold a stable on-time percentage while steadily trending worse — steady 5 minutes late three weeks ago, steady 18 minutes late now. That trend is exactly what decides whether the driver leaves the garage on schedule or 20 minutes later.
- **Identity resolution at pushback.** The moment TFMS reports the airline's own OUT (pushback) time, CTDI force-resolves the aircraft's hex code and tail number and fires a one-time "identity resolved" push carrying a live tracking link — the dispatcher can watch the actual airframe from gate departure to touchdown. Fires once per entry, never spams.
- **Delay-extended watch windows.** When the airline's OFF (wheels-up) time arrives, the watch entry's expiry recomputes onto the real schedule and extends by the actual departure delay — a flight that departs two hours late is watched two hours longer, never silently dropped mid-inbound. Never shrinks the window for an early departure.

Disclosed candidly: on-time history capture is watchlist-gated by design (a flight number only accumulates history on days it was watched) and began 2026-08-20 — at the 08-24 verification the store held 2 matched records, so `insufficient_data` responses are the correct, expected behavior in the feature's first weeks, not a fault. The delay-extension logic is deployed and regression-tested against the operator's own worked example (scheduled 14:00 departure, wheels-up 16:00 → watch window extends exactly 2 hours), but no in-service flight had exercised it between deployment and this document's fact-check.

# Verified live, 2026-08-24

| Capability | Observed live | Status |
| Service health | /healthz ok, data snapshot age 3 seconds; CPS GREEN/GO; 38 rootless containers at check | LIVE and VERIFIED |
| Flight data | All six FAA SWIM services provisioned and writing; 839,101 flight events (32,459 in the prior 24 h, 1,270 distinct airlines, 30-day rolling retention) | LIVE and VERIFIED |
| Rail data | 822,317 Amtrak train events; train watchlist and delay alerting live | LIVE and VERIFIED |
| Airspace and weather | 121 active FAA TFRs; 5,424 NOTAMs across 308 facilities; live METARs; NWS/NWWS-OI push alerts writing real DC-area products | LIVE and VERIFIED |
| Flow-program awareness | 35,615 TBFM arrival-metering sequences; 120,071 surface-movement events; 24,588 NAS program records (ground stops, ground-delay programs, airspace flow programs) | LIVE and VERIFIED |
| Risk scoring | CPS: deterministic 288-line 6-factor go/no-go engine anchored to published Part 135.609 minimums as a conservative threshold reference — auditable, not ML | LIVE and VERIFIED |
| Alerting | Self-hosted ntfy push with per-topic throttles and dedup; 32 audit-logged admin endpoints; 218 tests, 217 passing (1 known pre-existing failure, tracked) | LIVE and VERIFIED |

**The availability caveat that travels with every feed claim:** the platform runs on a single Raspberry Pi 5 and deliberately sheds its ingest tier under compute contention. In the ~32 hours before the 2026-08-24 check it executed 10 automatic shed-and-restore cycles of ~9-11 minutes each, every one restoring cleanly without intervention. Feeds are governed and duty-cycled under contention — they are not "always-on," and no SLA exists yet. Governor calibration is a known, documented open tuning item.

# The economics an operator actually cares about

From the live-recomputed cost analysis (2026-08-24; re-derived live 2026-09-03, the source of every figure below — every commercial price cites a dated vendor source; vendors with no citable price anywhere are excluded from totals rather than guessed):

| Quantity | Figure |
| One-time hardware, actually deployed (single node, itemized BOM) | ~$765 |
| Actual recurring cost (electricity only; DC tariff re-fetched 2026-09-03) | ~$22-38 / yr |
| Recurring data-feed fees | $0 |
| Recurring cloud-LLM spend | $0 — measured over 35,217 logged calls across 56.9 days, zero cloud-model rows |
| Subscription-replacement floor (this vertical's purchasable subset; independent of the platform-wide ~$55,200-230,400/yr band) | ~$1,700-8,000 / yr (bare-feature minimum $240/yr) |

The vertical figure is honest about why it is small, and that is the sales argument: the purchasable version of this capability is shallow. Bundled "flight tracking" in commercial dispatch platforms runs on FlightStats-class estimates — not airline-reported OOOI times, not TFMS flow programs, not SWIM push — and its citable bundled-feature price is $240/yr (the published price delta for "Flight Status Tracking" as a dispatch-platform add-on); no limo/dispatch platform in the sweep offers rail tracking at all. Because CTDI deliberately relays into dispatch software rather than replacing it, the floor prices the flight/train-intelligence and alerting capability alone. It is an independent single-vertical calculation, never summed with the platform-wide band (which prices the full cross-vertical capability) or with any other vertical's floor.

The parts that matter most to a dispatch decision cannot be bought at any price: no commercial vendor sells TFMS ground-stop/GDP/airspace-flow-program data, TBFM arrival metering, or ITWS terminal wind-shear alerts, and every commercial flight feed obfuscates LADD-blocked aircraft that CTDI's own receivers see directly — precisely the tail numbers an executive fleet is most likely to meet.

Honest boundary, stated with the numbers: most of the ingested data is free at the source to any approved subscriber, so both replacement figures are floors for a buyer without FAA vetting — not avoided spend (the honest avoided-spend number for this instance is ~$2,160-2,656/yr, roughly two-thirds of it reciprocal data-sharing barter). Cost avoidance does not carry a valuation, and the analysis says so.

# Security posture — tested, broken, fixed, re-verified, same day

Client movement data deserves better than a checkbox claim, so here is the actual record. The platform's controls are re-tested against the live production system on a recurring cadence — bounded, non-destructive, founder-run passes on 2026-08-13 and 2026-08-24 probed authorization boundaries, credential storage, and the integrity chain directly. Every open finding from the prior pass was closed and independently re-verified, including its single highest-severity issue. The 08-24 pass found new, real issues — and the find-fix-re-verify loop worked the same day: two genuine regressions briefly introduced by a same-day remediation were caught, structurally fixed at the mount layer, and a final adversarial pass — whose stated default was that every "resolved" label is wrong until skeptically disproved — reopened nothing and found no new exploitable exposure. Authorization held on every probe (anonymous, forged-token, malformed, and spoofed-origin requests all rejected); credentials exist only as one-way hashes; the codebase runs behind a GPG-signed 706-file integrity manifest that skill containers and the inference layer refuse to execute against on any mismatch; every administrative action is audit-logged.

What this is not: an external red-team or a compliance certification. No SOC 2 or ISO 27001 is claimed; a first third-party penetration test is an explicit, fundable roadmap item. Known open items (token-expiry hygiene, two public-surface policy decisions, an edge-header hardening candidate) are tracked and documented; none touches the operational feeds or the credential store. Because the whole stack — including all 21 local LLM models — self-hosts on the operator's own hardware with measured-zero cloud inference, client movement data never has to leave premises to get the intelligence.

# Licensing — Business Source License 1.1, built around exactly this relationship

New as of 2026-08-24: CTDI is licensed under the **Business Source License 1.1** (Licensor: [operator LLC], LLC), and its Additional Use Grant is written directly around the relay/middleware relationship described above. Stated accurately, because the boundary matters:

- **Free, always:** all non-production use — evaluation, development, testing. You can stand CTDI up next to your dispatch stack, wire the webhooks, and pilot it end-to-end at no cost and with full source.
- **Free in production:** personal self-hosted use, and internal relay/middleware use by an organization of any size for its own internal operations — for example, a corporate travel department or family office dispatching for its own executives, with no third-party client being billed.
- **Commercial license required — the controlling rule (clause iv):** any production use in connection with a fee-based service rendered to a third-party client or customer, even when CTDI is solely an invisible internal relay step never surfaced to the client, and even when its cost is bundled into an overall fee, retainer, or rate. For a for-hire ground-transport operation, that means production use feeding your paid client dispatch is exactly the commercially licensed scenario — that is the deliberate revenue boundary, not a loophole to engineer around. White-labeling, hosted resale, and absorbing CTDI into another platform each independently require a commercial license, fee or no fee.
- **Open source on a clock:** each version converts automatically to GPL v3-or-later four years after first public distribution (current Change Date: 2030-08-24) — the same BSL structure used by MariaDB, HashiCorp, and Sentry.

Disclosed: the Additional Use Grant language is a working draft currently under legal review; it reflects intended terms but has not yet been confirmed by counsel. Prospective commercial licensees should confirm current terms with [operator LLC], LLC directly.

# What is not built — stated plainly

No billing, subscription, or entitlement code; no multi-tenancy (one deployment per operator today); no backup/disaster-recovery system; no CI pipeline; no dedicated CPS unit test. Pre-revenue: the operational runsheet's newest entry records one trip — output is capacity, not yet demonstrated demand. Maritime/AIS vessel tracking is code-present but fully dormant (zero live data) and is claimed only as roadmap. The public demo returned to service 2026-08-24 with its production isolation adversarially verified, but its public access-gating policy is a pending founder decision — demo access is arranged in diligence conversations rather than linked here.

# Contact

the operator — Founder and operator, [operator LLC], LLC, Arlington, VA · info@example.com. Live production read-outs can be arranged for diligence, as was done for the 2026-08-24 verification, security, and adversarial re-verification passes behind this document.
