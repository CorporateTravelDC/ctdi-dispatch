Executive Summary — v1.5

Platform overview for investors, strategic acquirers, and white-label partners

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Every factual claim below was re-derived from a live re-verification of the running production system performed on 2026-08-24 (continuing the v1.1 → 2026-08-09 → 2026-08-24 verification chain), from a bounded security re-validation and adversarial re-verification completed the same day, and from a live-recomputed cost analysis. Counts that drift continuously (rows, containers, commits) are timestamped samples, not fixed facts. Where something is not built, it says so.

# What CTDI is

CTDI (Corporate Travel Dispatch Intelligence) is a 24/7 self-hosted operational dispatch-intelligence platform built and operated by the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA). It ingests aviation, rail, weather, and airspace data — including all six FAA SWIM data services under real, approved credentials — computes a deterministic go/no-go operational risk score (the CPS, Critical Predictability State), and pushes real-time alerts to operators. It was built to run the founder's own executive-chauffeur operation and is being packaged for white-label use by ground-transportation and executive-services operators.

The whole-stack story, at current scale: a solo founder-operator designed, built, secured, and runs a multi-domain intelligence platform — 51,070 lines of Python, a 27-component React PWA, 102 REST route registrations, 21 dedicated local LLM models, and a compiled semantic knowledge layer — on a single Raspberry Pi 5 at the network edge. 635 commits, June 7 through August 24, 2026, single author. The signed 706-file code manifest was re-verified the morning of this document's fact-check.

Evidence discipline is the house style: capability claims are graded (live-verified, code-complete, or roadmap), the platform's own documentation records its incidents and open decisions rather than hiding them, and this document carries its caveats in the body, not in footnotes.

# Verified capability snapshot (2026-08-24)

| Capability | Observed live 2026-08-24 | Status |
| Service health | /healthz ok, data snapshot age 3 seconds; CPS GREEN/GO; 38 rootless containers running at check (count swings ~30-40 by design as timers fire and load-governors act) | LIVE and VERIFIED |
| CPS risk engine | GREEN/GO across 6 deterministic factors; 288-line auditable rule engine anchored to published Part 135.609 minimums as a conservative threshold reference; 1,141 historical scores retained | LIVE and VERIFIED |
| Feed freshness registry | 19 feeds, each with age, staleness threshold, and error state | LIVE and VERIFIED |
| FAA SWIM ingest | All six SWIM data services (FDPS, TFMS, TBFM, STDDS, ITWS, AIM/FNS) provisioned with real credentials; all 7 ingest containers active and writing at check | LIVE and VERIFIED, with the availability caveat below |
| Accumulated operational corpus | 839,101 flight events (32,459 in the prior 24 h, 1,270 distinct airlines, 30-day rolling retention); 822,317 train events; 5,424 NOTAMs across 308 facilities; 35,615 TBFM arrival-metering sequences; 120,071 surface-movement events; 24,588 NAS program records | LIVE and VERIFIED |
| Airspace and weather | 121 active FAA TFRs at check; live METARs; NWS/NWWS-OI push alerts writing real DC-area products | LIVE and VERIFIED |
| Own-RF layer | ADS-B: 17 aircraft in view, ~108 messages/s at check. ACARS/VDL receive current to the minute (48,716-row rolling store). Fusion of ACARS into the platform DB is still pending an off-box fix — receive capability is claimed, DB fusion is not | LIVE (receive side); fusion DISCLOSED as pending |
| Local LLM intelligence layer | 21 dedicated Ollama models on-device; 272 scheduled briefs in the prior 7 days; honest disclosure: 41.7% of all skill LLM calls in that window ran the labeled deterministic-template fallback under contention | LIVE, with fallback rate disclosed |
| Admin audit trail | 32 audit-logged admin endpoints with actor/IP/payload capture and 90-day retention; 4,397 audit rows, 3,384 in the prior 24 h (this control had 12 rows total two weeks earlier) | LIVE and VERIFIED |
| Second brain / knowledge layer | 6,742 indexed vault documents; compiled semantic layer of 99 concepts and 51,317 note-to-concept edges; causal derivation graph of 26,448 edges with multi-hop trace queries; recompiled daily by timer (that morning's run: success, 8.6 s) | LIVE and VERIFIED |
| Integrity chain | GPG-signed whole-tree manifest (706 files); 33 skill containers refuse to execute on any mismatch; 15-minute integrity sweep; drift checkers with their own blind spots documented rather than hidden | LIVE and VERIFIED |
| Test posture | 218 tests, 217 passing (1 known pre-existing failure, tracked); still no CPS-engine unit test and no CI pipeline — both stated as gaps | DISCLOSED GAP |

**The availability caveat that travels with every feed claim:** the platform deliberately sheds its ingest tier under compute contention. In the ~32 hours before the 2026-08-24 check it executed 10 automatic "LOCKDOWN" shed-and-restore cycles of ~9-11 minutes each, every one restoring cleanly without intervention. Feeds are governed and duty-cycled under contention on this single node — they are not "always-on," and no SLA exists yet. Trigger calibration is a known, documented open tuning item.

# The economics: near-zero COGS against a five-figure subscription floor

New since the prior verification pass, and promoted to investor-facing at the founder's direction: a live-recomputed cost analysis (2026-08-24) in which every commercial price cites a dated vendor source, business-judgment figures are flagged as assumptions, and quote-only vendors are excluded from totals rather than guessed.

| Quantity | Figure |
| One-time hardware, actually deployed (single node, itemized BOM) | ~$765 |
| Actual recurring cost (electricity only; DC tariff re-confirmed 2026-08-24) | ~$23-39 / yr |
| Recurring data-feed fees | $0 |
| Recurring cloud-LLM spend | $0 — measured, not assumed: 25,147 logged LLM invocations over 46.4 days of continuous production contain zero cloud-model rows |
| Defensible avoided cost, this instance, conservative | ~$2,160-2,655 / yr net |
| Subscription-replacement floor (purchasable subset of live capability) | ~$55,200-112,900 / yr |
| Live capabilities not purchasable at any price | 10 |

The one-line version: the platform runs on ~$765 of owned hardware and ~$23-39/yr of electricity, with $0 in data-feed fees and $0 in cloud-LLM spend, while the purchasable subset of its live capability lists at roughly $55k-113k/yr in commercial subscriptions — and its most operationally valuable elements cannot be bought at any price: TFMS ground-stop/GDP/airspace-flow-program data, TBFM arrival metering, ITWS terminal wind-shear alerts, unfiltered (LADD-unbound) blocked-aircraft visibility from its own receivers, receive-side ACARS, a permanently-owned longitudinal corpus (commercial term licenses require destruction on exit), scheduled LLM briefs over a private multi-source corpus, corroborated entity auto-promotion, and the signed-manifest execution gates.

Honest boundaries, stated with the numbers as the source analysis requires: most of the ingested data is free at the source to any approved subscriber, so the avoided cost is an integration cost, not a data-license cost — the $55k-113k figure is a replacement-cost floor for a buyer without FAA vetting, and must not be presented as avoided spend (the honest avoided-spend number is the $2.2-2.7k/yr line, roughly two-thirds of which is reciprocal data-sharing barter rather than cash). Cost avoidance does not carry a valuation, and the analysis says so.

# Security posture: tested, broken, fixed, re-verified — same day

Security is treated as a running discipline, not a checkbox, and the honest story is more credible than a clean-sheet claim: the platform's controls are re-tested against the live production system on a recurring cadence — bounded, non-destructive passes on 2026-08-13 and 2026-08-24 probed authorization boundaries, credential storage, and the integrity chain directly.

- **Every open finding from the prior (08-13) pass was closed and independently re-verified** — including its single highest-severity issue (an anonymously readable knowledge-vault surface), fixed at two independent layers (code-level tier gates plus restored edge access control) and confirmed by live external request.
- **Authorization held on every probe:** anonymous, forged-token, malformed-token, and spoofed-public-origin requests were all rejected; auth is bearer-token-only, and network origin grants no tier. Credentials exist only as one-way hashes; the secrets file is owner-only; git history is clean of credentials.
- **The 08-24 pass found new, real issues — and the find-fix-re-verify loop worked the same day.** A remediation made that day briefly introduced two genuine regressions on the public demo surface (a production-database mount and a shared chat file). The same day's re-verification pass caught both; both were structurally fixed at the mount layer; and a final adversarial pass — whose stated default was that every "resolved" label is wrong until skeptically disproved under header-injection, encoding, and config-drift pressure — reopened nothing and found no new exploitable exposure, verified against the actually-running containers rather than the config files.
- **Code that runs on the platform is gated by a signed whole-tree integrity chain** that refuses to execute on any mismatch; every administrative action is audit-logged.

What this is not, stated plainly: this is founder-run self-assessment using a bounded, non-destructive methodology — not an external red-team, and no compliance certification (SOC 2, ISO 27001) is claimed; an ISO 42001 alignment document exists as positioning, not certification. A first third-party penetration test is an un-started, fundable line item. Known open items are tracked, bounded, and documented: credential-lifecycle hygiene (issued tokens do not yet expire; one retired integration's admin token awaits revocation), a hardening candidate around edge-header trust, and two founder policy calls on deliberately-public read surfaces. None touches the production operational feeds or the credential store.

# Licensing and IP: Business Source License 1.1

New as of 2026-08-24, closing a gap the prior verification passes flagged repeatedly ("no LICENSE file"): the platform is now licensed under the **Business Source License 1.1** (Licensor: [operator LLC], LLC), verified as canonical, unmodified BSL text with a correctly-formed parameters block.

- **Free always** for non-production use — evaluation, development, testing.
- **Free in production** for personal self-hosted deployments, and for internal relay/middleware use by an organization of any size — provided that use never serves, supports, or contributes to any fee-based product or service rendered to a third-party client or customer.
- **Commercial license required** for hosted/managed/white-label/embedded resale, rebranding or redistribution, platform absorption, or any use — even invisibly, as an internal middleware step, and even when bundled into an overall fee or retainer — in connection with a fee-based client service. This is the deliberate revenue boundary: operators pay when CTDI touches their paid client work.
- **Becomes open source on a clock:** each version converts automatically to GPL v3-or-later four years after its first public distribution (Change Date for the current version: 2030-08-24). The IP is protected now and credibly open later — the BSL structure used by MariaDB, HashiCorp, and Sentry.

Disclosed: the Additional Use Grant language is a working draft currently under legal review; it reflects intended terms but has not yet been confirmed by counsel.

# Why it is defensible

- **Deterministic, auditable go/no-go scoring** anchored to a published federal minimum standard — explainable to clients, insurers, and lawyers, in deliberate contrast to black-box AI scoring. The CPS engine is a rule engine, not ML; the local LLM writes narrative around the data and never computes the score.
- **Data assets money cannot buy.** Six live FAA SWIM services (verified negative finding across every vendor checked: no commercial product sells TFMS flow programs, TBFM metering, or ITWS terminal alerts), own-RF visibility unbound by LADD filtering, and a permanently-owned longitudinal corpus that term-licensed alternatives contractually destroy on exit.
- **Sovereignty as a product property.** The entire stack — including 21 local LLM models — self-hosts on sub-$900 owned hardware with measured-zero cloud inference spend; attractive to privacy-sensitive executive-services and security-conscious buyers.
- **An integrity chain rare at any size:** GPG-signed whole-tree manifest gating skill execution and inference, tiered bearer-only auth, full admin audit logging, and a CUI/PII scrub gate that blocks rather than redacts.
- **A compounding knowledge layer.** The second brain accumulates operational memory (6,742 documents) under a compiled concept graph with causal derivation edges — correlations invisible to any real-time view, growing daily by timer.
- **Founder-market fit.** Built by the operator it serves; the platform has run the founder's real dispatch operation continuously since June 2026.

# Risk summary (stated plainly)

| Risk | Facts |
| Bus factor = 1 | Single developer-operator; 635 commits, one author; no CI pipeline. The funding ask explicitly includes a first engineering hire. |
| Single-node infrastructure | One Raspberry Pi 5 on residential power/ISP; no backup system, no second node, no documented disaster recovery. Every resource limit is tuned to this one box (documented single-edge-unit assumption set); scaling out means re-measuring, and DR is a priced roadmap item. |
| Availability posture | Feeds are deliberately duty-cycled under contention (10 shed/restore cycles in ~32 h at the 08-24 check); no SLA. Governor calibration is a documented open item. |
| Pre-revenue, demand not demonstrated | No billing, subscription, CRM, or entitlement code exists. The operational runsheet's newest entry records one trip — output is capacity, not yet realized demand. |
| Verification gaps | No dedicated CPS unit test; no CI; one known pre-existing test failure (tracked); several cost-analysis inputs are secondary-sourced and the largest true equivalents are quote-only. |
| Security assessment is internal | Bounded founder-run self-testing only; no third-party pentest or audit yet; token-expiry hygiene and two public-surface policy decisions open. |
| Licensing | BSL 1.1 in place, but the Additional Use Grant is under legal review — not yet counsel-confirmed. |
| Data provenance | FAA/NWS/NOAA data is public-domain or free-to-approved-subscriber (clean). DCA/IAD airport boards are scraped from undocumented JSON (fallback tier only); Amtrak is a community API; both need formalization or replacement for commercial deployment. |
| Regulatory posture | Decision-support software only. Not an FAA-certified dispatch system; Part 135.609 is used as a published threshold reference, not a compliance claim. Insurance, GDPR/DPA posture: not assessed, none claimed. |

# Business status and roadmap

CTDI is pre-revenue. Four adjacent verticals are identified: executive ground transportation, corporate travel and concierge platforms, aviation operations (decision support only), and executive protection. The intended commercial architecture remains client-held subscriptions: each client operator obtains and holds its own credentials for the underlying data sources (FAA SWIM, NWS, and similar); [operator LLC] supplies the software, integration, and decision-support layer and does not redistribute third-party data. Per-source access instructions and credential-request templates exist in the platform's data-source runbook today; automated tenant onboarding does not.

The public demo surface returned to service on 2026-08-24 after a nine-day outage, with its isolation from production data structurally enforced at the mount layer and adversarially verified the same day; its public access-gating policy is a pending founder decision, so demo access is arranged in diligence conversations rather than linked here. The demo replay corpus (2.45 GB of recorded operational history) is real and growing.

Near-term roadmap, in priority order: (1) productize — multi-tenancy, billing/entitlements, backup and DR, CI, and closure of the documented security hygiene items; (2) commission a first third-party security assessment; (3) formalize data rights for the fallback sources and finalize the BSL Additional Use Grant with counsel; (4) sign 2-3 design partners across the identified verticals using the replay-demo machinery; (5) de-risk the team — fund the founder full-time and make a first engineering hire. Maritime (AIS) ingest is documented, priced, and deliberately excluded from every live-capability claim until it is real; roadmap targets (AIS by end 2026, eVTOL operational awareness spring 2027) are founder-authoritative.

Live production read-outs can be arranged for diligence, as was done for the 2026-08-24 verification, pentest, and adversarial re-verification passes behind this document.
