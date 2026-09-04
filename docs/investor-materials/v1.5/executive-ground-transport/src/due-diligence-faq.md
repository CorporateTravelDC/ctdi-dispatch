Due-Diligence FAQ — v1.5

Hard questions, answered honestly — executive ground-transportation audience (limo, black-car, and chauffeur dispatch operators and their investors)

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Answers are grounded in a live re-verification of the production system, a bounded security re-validation, and an adversarial re-verification, all performed 2026-08-24, plus the platform's own continuously-maintained internal state documentation. Where a count drifts continuously (rows, containers, commits) it is a timestamped sample. Where the honest answer is "not built" or "needs founder input," it says exactly that. No market sizes, revenue projections, or user counts appear anywhere: none exist in the fact base.

# Q1. Does this replace LimoAnywhere (or whatever dispatch software we run)?

No — explicitly, architecturally, and by license. CTDI is a relay/middleware layer: it feeds real-time flight and train intelligence to and from your existing reservation and dispatch platform and never absorbs or replaces it. Your reservations, client records, billing, driver assignments, and dispatch workflow stay in the software you already run. CTDI's job is the intelligence gap — knowing the inbound flight slipped before your client calls. The relationship is deliberate enough that the product's license (BSL 1.1 Additional Use Grant) is written around the relay/middleware model as its central use case, and one of its carve-outs (clause iii) specifically disallows anyone absorbing CTDI into their own platform "beyond a distinct, independently-operated relay." The relay boundary cuts both ways, on purpose.

# Q2. What does integration actually require on our side?

No code changes to your dispatch software. Two paths:

- **Webhook path:** CTDI exposes a credential-gated inbound webhook for LimoAnywhere reservation events (`/webhooks/limoanywhere/reservations`, shared-secret header; the endpoint returns 503 until its secret is configured, so it cannot be probed open). A reservation created in LimoAnywhere lands in CTDI and the associated flight or train goes on the watchlist automatically. RingCentral and 3CX phone-event webhooks follow the same pattern.
- **API path:** any system that can make an HTTP call can add a watch directly — `POST /api/v1/watchlist/flights` (admin bearer token) with flight identifier, origin/destination, and auto-expiry; `/api/v1/watchlist/trains` for Amtrak. The call is idempotent (a repeat add refreshes rather than errors). Platforms without native webhooks can poll their own reservations API on cron and sync the same way. Permanent VIP watches live in hot-reloaded JSON files.

Honest status: the webhook receivers are real, credential-gated code in the live route table, but no external vendor account has exercised them end-to-end in production — the reference deployment feeds its watchlist via the API and permanent lists. Vendor-native HMAC signature verification (beyond the shared secret) remains a documented to-do. Treat the webhook path as code-complete and integration-ready, not field-proven against a live LimoAnywhere tenant.

# Q3. How does CTDI actually help a dispatcher decide when to roll a car?

Four mechanisms, all live-verified in the 2026-08-20 → 08-24 window:

- **Real on-time history at booking time.** CTDI durably captures the airline's own reported out/off/on/in (OOOI) times from FAA TFMS for watchlisted flights, alongside TFMS's own scheduled times, and computes real delay — actual-vs-schedule, not estimate-vs-estimate. The 14-day history (`ontime_history_14d`) comes back on the same API response that adds the watch, so it is in hand at reservation time.
- **Delay-drift flags.** Per leg, the engine compares the oldest third of matched records against the newest third and flags a shift past a 10-minute default. A flight can hold a stable on-time rate while trending steadily worse — 5 minutes late three weeks ago, 18 minutes late now — and it is the trend, not the rate, that decides when the driver should leave.
- **Identity resolution at pushback.** The moment TFMS reports the airline's OUT time, CTDI force-resolves the actual airframe (hex code and tail number) and fires a one-time push with a live tracking link, so the dispatcher watches the specific aircraft gate-to-gate. It fires once per entry — no alert spam.
- **Delay-extended watch windows.** At wheels-up, the watch entry's expiry recomputes onto the real schedule and extends by the actual departure delay — a flight departing two hours late is watched two hours longer, never silently dropped mid-inbound, and the window never shrinks for an early departure.

# Q4. How fresh and accurate is the data behind those alerts?

The flight side is FAA SWIM push data — the same national-airspace data stream the FAA distributes to approved subscribers, received continuously rather than polled: all six SWIM services (FDPS, TFMS, TBFM, STDDS, ITWS, AIM/FNS) are provisioned under real credentials and were writing at the 2026-08-24 check (839,101 flight events on hand, 32,459 in the prior 24 hours, 1,270 distinct airlines). Delay math uses the airline's own reported times against TFMS's own schedule — no estimate-vs-estimate proxying. The platform's health snapshot was 3 seconds old at check.

Honest caveats that travel with that: (1) on-time history is watchlist-gated by design — a flight number only accumulates history on days it was actually watched — and capture began 2026-08-20, so early responses will honestly say `insufficient_data` (2 matched records existed at the 08-24 check); (2) the delay-extension logic is deployed and regression-tested but no in-service flight had exercised it between deployment and this document's fact-check; (3) feeds are duty-cycled under load governance (see Q6), so "received continuously" is subject to the disclosed shed cycles.

# Q5. What about trains?

Live and long-running: 822,317 Amtrak train events were on hand at the 08-24 check, with per-train positions and delay minutes, station watchlists, and the same push-alert path as flights (`/api/v1/watchlist/trains`). Train pattern analysis includes schedule-drift flagging (the mechanism the flight-side delay-drift feature was deliberately modeled on). Disclosed: Amtrak data comes via a free community API, not a federal feed (no rail equivalent of SWIM exists), and the platform currently has a single live ingest path for it — an automatic failover for that one path is a documented gap, not yet built.

# Q6. Is this always-on? What is the availability story?

Stated plainly: no. The platform runs on a single Raspberry Pi 5 at the founder's residence and deliberately sheds its ingest tier under compute contention — in the ~32 hours before the 2026-08-24 check it executed 10 automatic shed-and-restore cycles of ~9-11 minutes each, every one restoring cleanly without human intervention. There is no SLA. The shed-trigger calibration is a documented open tuning item, and single-node residential hosting with no backup/DR system is a disclosed, funded-roadmap risk (Q12). What offsets this honestly: the degradation is visible (per-feed freshness registry with staleness thresholds and error states, health endpoint, alerting on its own faults) rather than silent, and the restore path is verified against real events, not assumed.

# Q7. Can we trust it with client movement data? What is the security history?

The honest record, which we consider more credible than a clean-sheet claim:

- **2026-08-13:** first bounded, non-destructive, founder-run security pass against the live production system. Found real issues, including one high-severity finding (an anonymously readable knowledge-vault surface) and a documented-but-not-installed pre-commit credential hook.
- **2026-08-24:** full re-validation. Both open 08-13 findings confirmed closed and independently re-verified — the vault exposure at two independent layers, confirmed by live external request. Everything confirmed-working on 08-13 still held: anonymous, forged-token, malformed-token, and spoofed-origin requests all rejected; tokens stored as one-way hashes only; secrets file owner-only; git history clean of credentials.
- **Same day:** the pass hunted new surface and found real items; all were remediated the same day. One remediation briefly introduced two genuine regressions on the demo surface (a production-database mount; a shared chat file) — the follow-up re-verification caught both, and both were structurally closed at the mount layer.
- **Same day, adversarial pass:** a final re-verification whose stated default was that every "resolved" label is wrong until skeptically disproved — header-case variants, encoding tricks, multi-header injection, repo-vs-live drift checks against the actually-running containers. Verdict: nothing reopened, no new exploitable exposure.

Standing controls, live-verified: bearer-token-only auth (network origin grants no tier), full admin audit logging (32 endpoints, actor/IP/payload, 90-day retention), a GPG-signed 706-file whole-tree manifest that gates skill execution and LLM inference, a 15-minute integrity sweep, rootless containers, and a scrub gate that blocks rather than redacts sensitive content. What we do not claim: this is founder-run self-assessment, not an external red-team; no compliance certification (SOC 2, ISO 27001) exists or is implied; known open hygiene items (token expiry unused, one retired integration's admin token pending revocation) are tracked and documented.

# Q8. Where does our data go? Any cloud dependency?

Core operations have none. The entire stack — ingest, scoring, alerting, and all 21 local LLM models — runs on owned hardware. Cloud-LLM spend is $0 measured, not assumed (re-verified 2026-09-03): 35,217 logged LLM invocations over 56.9 days of continuous production contain zero cloud-model rows, and the cloud fallback is explicitly disabled in configuration. For a fleet moving privacy-sensitive principals, client movement data never has to leave premises to get the intelligence. Disclosed trade: the platform's own ADS-B receivers feed community flight-data aggregators in exchange for reciprocal account benefits — that is aircraft RF data from the antenna, not client or reservation data, and stopping it is a configuration choice with a priced consequence (~$399-2,118/yr to buy the accounts back).

# Q9. What does the license let us do for free, and when do we owe money?

Business Source License 1.1 (adopted 2026-08-24; Licensor: [operator LLC], LLC). The accurate boundary, because it is easy to oversell:

- **Free always:** all non-production use — evaluation, development, testing. You can pilot CTDI against your live dispatch stack, with full source, at no cost.
- **Free in production:** personal self-hosted use; and internal relay/middleware use by an organization of any size, solely for its own internal operations, where no part of that use serves, supports, or contributes to a fee-based product or service rendered to a third-party client. Example that qualifies: a corporate travel department or family office dispatching cars for its own executives — nobody external is billed.
- **Commercial license required — clause (iv), the controlling rule:** any use in connection with any fee-based service rendered to a third-party client or customer, even where CTDI is solely an internal relay step never surfaced to the client, and even where its cost is bundled into an overall fee, retainer, or rate. For a for-hire livery operation, production use that informs dispatch of paid client rides is that scenario. This is the deliberate revenue boundary — the license is designed so the free tier funds evaluation and genuinely internal use, and commercial operators license the exact relay use they make. Separately and independently of fees: hosted/managed/white-label/embedded resale, rebranding or redistribution, and absorbing CTDI into your own platform beyond a distinct relay each require a commercial license.
- **Open source on a clock:** each version converts to GPL v3-or-later four years after first public distribution (current Change Date: 2030-08-24).

Disclosed: the Additional Use Grant is a working draft under legal review — it reflects intended terms but is not yet counsel-confirmed. Confirm current terms with [operator LLC], LLC before relying on it for a production licensing decision. The license text was independently verified 2026-08-24 as canonical, unmodified BSL 1.1 with a correctly-formed parameters block.

# Q10. What does it cost to run?

From the live-recomputed cost analysis (2026-08-24; re-derived live 2026-09-03 — every commercial price cites a dated vendor source; vendors with no citable price anywhere excluded from totals rather than guessed): the deployed node is ~$765 of one-time hardware; actual recurring cost is ~$22-38/yr of electricity; data-feed fees are $0 (FAA SWIM, NWS/NOAA, and FAA NOTAM data are free to approved subscribers; Amtrak is a free community API; the rest is own-RF). The purchasable subset of this vertical's live capability lists at roughly $1,700-8,000/yr (bare-feature minimum $240/yr — the published price delta for "Flight Status Tracking" as a dispatch-platform add-on): because CTDI deliberately relays into dispatch software rather than replacing it, the honest comparable is the flight/train-intelligence and alerting capability alone, and the figure is small because the purchasable version is shallow — bundled tracking runs on FlightStats-class estimates, not airline-reported OOOI or SWIM push, and no limo/dispatch platform in the sweep offers rail tracking at all. It is an independent single-vertical figure, never summed with the platform-wide ~$55,200-230,400/yr band (which prices the full cross-vertical capability for a buyer without FAA vetting) or with any other vertical's floor — and both are replacement-cost floors, never avoided spend (the honest avoided-spend figure is ~$2,160-2,656/yr, most of it reciprocal barter). And the most dispatch-relevant elements are not purchasable at any price: no commercial vendor sells TFMS ground-stop/GDP/flow-program data, TBFM arrival metering, or ITWS terminal wind-shear alerts, and every commercial feed obfuscates LADD-blocked aircraft that the platform's own receivers see directly — disproportionately the tail numbers an executive fleet meets.

# Q11. Who is behind this? What is the bus factor?

One person. the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA) designed, built, and operates the entire stack — 635 commits, single author, 2026-06-07 through 2026-08-24; 51,070 lines of Python; 102 REST routes; 218 tests (217 passing, 1 known pre-existing failure, tracked). He is also the reference customer: CTDI has run his own executive-chauffeur operation continuously since June 2026, which is exactly why the product is shaped around a dispatcher's actual decisions. Bus factor is 1 and it is the top disclosed risk; the funding ask explicitly includes a first engineering hire. Partial mitigants: fully headless administration, aggressive written-down operational knowledge (incidents, root causes, and open decisions are documented in the repository itself), and a signed-manifest integrity chain that makes the deployed code state verifiable by a successor.

# Q12. What is not built? What are we not being told?

Stated plainly, nothing held back: no billing, subscription, entitlement, or customer-account code of any kind (pre-revenue by construction); no multi-tenancy — serving multiple operators today means one dedicated deployment each; no database backup or disaster-recovery system; no CI pipeline and no dedicated unit test for the CPS engine; demand is not demonstrated — the operational runsheet's newest entry records one trip. Maritime/AIS vessel tracking is code-present but fully dormant (zero live vessel data; roadmap only). A previously-marketed MCP/AI-agent bridge was retired 2026-08-18 and is no longer claimed. There is no live public demo link in these materials: the demo instance returned to service 2026-08-24 with its production isolation adversarially verified, but its public access-gating policy is a pending founder decision, so demo access is arranged directly.

# Q13. Can we see it working before committing?

Yes, two ways, both honest about what they are. (1) A live production read-out: the same walkthrough performed for the 2026-08-24 verification passes — real feeds, real watchlist, real alerts, arranged directly. (2) The demo replay: a recorder has accumulated 2.45 GB of real operational history that replays as a labeled not-live-data demonstration; its isolation from production data is structurally enforced at the mount layer and was adversarially verified 2026-08-24. Public demo URL access awaits the gating decision above.

# Q14. If we fund or partner, what gets hardened first?

In priority order, from the platform's own documented gap list: (1) productization — multi-tenancy, billing/entitlements, backup and DR, CI, and closure of the tracked security-hygiene items; (2) a first third-party security assessment (all testing to date is founder-run); (3) data-rights formalization — counsel sign-off on the BSL Additional Use Grant, plus formalizing or replacing the community/scraped fallback sources (Amtrak community API, airport-board scraping); (4) field-proving the LimoAnywhere webhook path end-to-end with a design-partner fleet, and vendor-native HMAC verification; (5) a first engineering hire against bus-factor-1. Amounts and sequencing beyond that ordering need founder input — no figures are invented here.

End of FAQ. Companion documents: the executive summary and pitch deck for this audience, both grounded in the same 2026-08-24 verification chain. Contact: info@example.com.
