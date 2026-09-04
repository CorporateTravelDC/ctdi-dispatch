Due-Diligence FAQ — v1.5

CTDI for corporate travel management, executive concierge, and traveler-care teams

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Answers are graded LIVE and VERIFIED, CODE-COMPLETE, or ROADMAP, and every factual figure traces to a live re-verification of the running production system on 2026-08-24, a bounded security re-validation and adversarial re-verification the same day, or a live-recomputed cost analysis. Continuously-drifting counts are timestamped samples. Where something is not built, this document says so plainly — the point of the grading discipline is that diligence should confirm these answers, not contradict them.

# 1. How does CTDI integrate with our booking or reservation system, and what is the integration effort?

Thin, by design. CTDI watches what you tell it to watch and notifies you when something changes; it does not try to replace your booking or livery software. Three paths:

- **Reservation webhooks (CODE-COMPLETE, awaiting per-vendor credentials).** CTDI ships receiver endpoints for LimoAnywhere (`/webhooks/limoanywhere/reservations`), RingCentral (`/webhooks/ringcentral/events`), and 3CX (`/webhooks/3cx/events`). Each is gated by a shared-secret header and returns HTTP 503 until you set its per-vendor secret — off by default, live only when provisioned. On a reservation, the traveler's flight or train is auto-added to the watchlist. The code is written and reviewed; it has not yet been exercised end-to-end against live vendor credentials, which is exactly the kind of work we would do with a design partner.
- **Direct watchlist API (LIVE).** Any platform can call `POST /api/v1/watchlist/flights` (or `/trains`) with an admin bearer token — for example flight identifier, origin, destination, and an auto-remove time. The add is idempotent: a repeat is a clean refresh returning HTTP 201 again, not a 409 error, so a naive re-sync cannot break. The response returns a 14-day real airline-reported on-time history for that flight where enough data exists.
- **Cron poll (LIVE surface).** A platform without native webhooks can poll its own bookings API and sync the same watchlist endpoints. No CTDI-side change required.

The API surface a booking integration touches is small: add/remove watchlist entries, and receive pushes. Recurring principals can instead live as permanent JSON watchlist files that hot-reload within about a minute.

# 2. How fresh is the data, and what is the alert latency?

The freshness registry exposes 19 feeds live, each with its age, staleness threshold, and error state — so freshness is observable, not asserted. At the 2026-08-24 check the overall data snapshot age was 3 seconds. Internally, push feeds stamp heartbeats every 30 seconds and the notification sender polls the database every 30 seconds, so a watched-entry state change generally becomes a push within that cadence. For flights specifically, the FAA TFMS SWIM push reports actual pushback faster than any periodic sweep would, and CTDI forces identity resolution at that moment rather than waiting on its 120-second sweep — so your desk gets a confirmed tail number and a live tracking link close to the real "wheels-about-to-move" moment. The honest caveat: latency is only as good as feed availability, and feeds are duty-cycled under contention (see question 8).

# 3. What actually happens when a watched flight or train is disrupted?

The path is short and self-hosted end to end:

- A watched flight's phase advances through a state machine (pre-departure to out to off to on to in) that never reverts; a watched Amtrak train reports delay minutes and position, and watchlist-station logic surfaces only the stations your desk cares about.
- On a material change (a delay, a ground stop touching the origin, a resolved aircraft identity at pushback), CTDI fires a dual push — a domain topic (flight-alerts or train-alerts) plus a concise everything-feed — with five-minute content-aware de-duplication so the same event does not spam the desk.
- The flight identity-resolution push carries the resolved hex/registration and a click-through tracking link, fired once per entry.
- Transient watchlist entries auto-expire, and that expiry is delay-aware: a late departure extends the tracking window off the real departure delay so a traveler is not dropped mid-air.
- Delivery is via a self-hosted push server (14-topic catalog plus escalating per-family/zone topics with per-topic throttles). There is no third-party notification SaaS in the path.

# 4. Which of this is live today versus code-complete, for multi-modal coverage?

- **Flights and Amtrak trains: LIVE.** The watchlist engine, the flight phase machine, and live rail tracking with delays and positions are running in production. Real airline-reported on-time history (14-day, OOOI-based, with delay-drift flags) was deployed 2026-08-24 and is live but young by design — it accumulates only from 2026-08-20, only on days a flight is actually watchlisted, and no backfill is possible, so an "insufficient data" response for a new flight number in the first week or two is the correct, expected answer, not a bug.
- **Reservation webhooks: CODE-COMPLETE**, awaiting per-vendor credentials (question 1).
- **Maritime/vessel tracking: NOT live.** The code path exists but has zero live data, an empty watchlist, and no running unit. We do not claim it as a capability anywhere; it is roadmap only.

# 5. How is our clients' travel data secured?

Client travel data sits behind a bearer-token-only authorization boundary that was probed live on 2026-08-24 and held on every check: anonymous, forged-token, malformed-token, and spoofed-public-origin requests were all rejected. Network origin grants no tier — a request's source network confers no access. Credentials are stored only as one-way SHA-256 hashes (the token table has no plaintext column); the secrets file is owner-only (mode 600); git history is clean of credentials; and code that runs on the platform is gated by a GPG-signed whole-tree manifest that refuses to execute on any mismatch. Every administrative action — including watchlist mutations — is audit-logged with actor, IP, and payload, retained 90 days (4,397 rows at check, 3,384 in the prior 24 hours; this control had 12 rows total two weeks earlier). The platform self-hosts, so client itineraries are not pushed into a third-party SaaS.

# 6. Was this penetration tested?

Yes, with an honest qualifier that we consider more credible than a "zero findings" claim: it has been through **bounded, non-destructive, founder-run security self-assessments** on 2026-08-13 and 2026-08-24 — not an external red-team. The honest story is find-fix-re-verify, demonstrated same-day:

- Every open finding from the 08-13 pass was closed and independently re-verified, including its single highest-severity issue (an anonymously readable knowledge-vault surface), fixed at two independent layers.
- The 08-24 pass found new, real issues. A remediation made that day briefly introduced two genuine regressions on the public demo surface (a production-database mount and a shared chat file); the same day's re-verification caught both; both were structurally fixed at the mount layer; and a final adversarial pass — which assumed every "resolved" label was wrong until skeptically disproved under header-injection, encoding, and config-drift pressure — reopened nothing and found no new exploitable exposure, verified against the actually-running containers.

What we do not claim: no third-party audit, no SOC 2 or ISO 27001 certification (an ISO 42001 alignment document exists as positioning, not certification), and a first external penetration test is an un-started, fundable line item. Open, tracked hygiene items: issued tokens do not yet expire; one retired integration's admin token awaits revocation; a header-trust hardening candidate; and two founder policy calls on deliberately-public read surfaces. None touches the production operational feeds or the client-facing watchlist store.

# 7. Is using CTDI as part of our paid concierge service free, or do we need a commercial license?

You need a commercial license, and this is the answer we are most careful to state accurately rather than favorably. CTDI is under the Business Source License 1.1. The Additional Use Grant makes internal relay/middleware use free for an organization of any size **only when no part of that use serves, supports, or contributes to a fee-based product or service rendered to a third-party client or customer.** Clause (iv) is named the controlling rule for any paid-service scenario, and it is explicit: a commercial license is required for use "in connection with any fee-based service rendered to a third-party client or customer, **including solely as an internal relay or middleware step never surfaced to the client directly, regardless of whether that use is billed as a separate, itemized charge or bundled into an overall fee, retainer, or rate.**"

In plain terms for a travel management company or concierge firm: if you charge clients for your service and CTDI is used to watch those clients' trips — whether the client ever sees CTDI or not, and whether its cost is itemized or folded into your overall fee or retainer — that is fee-based client-service use and requires a commercial license from [operator LLC], LLC. Hosted resale, white-labeling, and absorbing CTDI into your own platform independently require a commercial license as well. The genuinely free production cases are personal self-hosting and purely-internal operational use that never touches a paid client engagement. Each version also converts automatically to GPL v3-or-later four years after first public distribution (current Change Date 2030-08-24). Disclosed: the use-grant language is a working draft under legal review, not yet counsel-confirmed — confirm current terms with [operator LLC], LLC before relying on it.

# 8. What is the data-freshness or availability SLA?

There is no SLA today, and we will not imply one. The platform deliberately sheds its ingest tier under compute contention on this single node: in the ~32 hours before the 2026-08-24 check it executed 10 automatic shed-and-restore cycles of about 9-11 minutes each, every one restoring cleanly without intervention, all triggered by local compute contention rather than by hitting a temperature or hard-load limit. Feeds are therefore governed and duty-cycled under contention — accurate language is "governed/duty-cycled," not "always-on." The trigger calibration is a documented open tuning item, and true continuous-availability requires resource headroom (a larger node or a second node) that is a priced roadmap line, not a shipped capability. This is the single biggest availability caveat in the materials, and it is stated up front on purpose.

# 9. Who built this, and isn't a single developer a fatal risk?

CTDI was built and is operated by the operator (USMC veteran; owner, [operator LLC], LLC, Arlington, VA), who built it to run his own executive-chauffeur operation — the founder is the user. Bus factor is one: 635 commits, single author, June 7 through August 24, 2026; no CI pipeline. We state this as a real risk, and the funding ask explicitly includes a first engineering hire to retire it. The mitigating context is unusual engineering discipline for a solo build — rootless containers, tiered bearer-only auth, a full admin audit trail, a signed whole-tree integrity chain, and a self-hosted stack with measured-zero cloud dependency for core operations — and a compiled second-brain knowledge layer (6,742 indexed documents) that captures operational memory outside any one person's head.

# 10. What does CTDI cost to run, and what is the economic story?

The platform runs on approximately $765 of owned hardware (one-time, single-node itemized BOM) and about $22-38/yr of electricity, with $0 in recurring data-feed fees and $0 in cloud-LLM spend — the last measured, not assumed (re-verified 2026-09-03): 35,217 logged LLM invocations over 56.9 days of continuous production contain zero cloud-model rows. Against that, the purchasable subset of this vertical's live capability — a flight status/alerts API, passenger-rail realtime, a travel-risk/traveler-care platform, and push delivery — lists at roughly $4,200-64,300/yr in commercial subscriptions (mid ~$16,700; the low end is assumption-flagged per-traveler pricing). This is an independent single-vertical figure from the 2026-09-03 extension pass: it shares the flight/rail/push lines with the platform-wide ~$55,200-230,400/yr band and adds a traveler-care platform line that band does not carry — never summed with the platform-wide number or any other vertical's floor. What no product in the basket does at any of these prices: watch a booking's specific flight and train automatically from a reservation webhook and push recovery-window alerts through self-hosted infrastructure — the integration capability is the product. The honest boundaries, stated as the source analysis requires: most ingested data is free at the source to any approved subscriber, so what is owned is the integration and the accumulated corpus, not a data license; the honest avoided-spend number for this instance is ~$2.2-2.7k/yr (roughly two-thirds of which is reciprocal data-sharing barter, not cash); the $4.2k-64.3k floor is replacement cost for a buyer without FAA vetting and must not be read as avoided spend; and cost avoidance does not carry a valuation. Pricing to your clients is not set — no billing or subscription code exists, and pricing will be co-defined with design partners.

# 11. What is deliberately NOT built?

Stated plainly so diligence confirms rather than discovers it:

- **No billing, subscriptions, CRM, or entitlement code.** Pre-revenue by construction; the operational runsheet's newest entry records one trip — output is capacity, not yet realized demand.
- **No multi-tenancy.** One instance serves one operator today; white-label means redeploy-per-operator (real install scripts exist). Config-level multi-tenancy is roadmap.
- **No disaster recovery, no backup system, no second node.** Single Raspberry Pi 5 on residential power and ISP.
- **No CI, no dedicated CPS-engine unit test.** 218 tests, 217 passing, 1 known pre-existing failure (tracked).
- **No maritime/AIS capability** (dormant: zero rows, empty watchlist, no unit).
- **No MCP / agent-native integration** — both MCP bridges were retired 2026-08-18; integration is the webhooks and watchlist REST API instead.
- **No SLA, no compliance certification, no third-party security audit yet.**

# 12. Can multiple clients or departments share one instance today?

Not with real tenant isolation. The platform is single-tenant: watchlists, tokens, and the database are one shared operational context. Multiple client programs can be watched on one instance, but there is no per-tenant data partition, per-tenant branding, or per-tenant billing — those are roadmap. For a period, an operator running distinct clients would either accept a shared context or run redeploy-per-operator instances. This is one of the specific things seed funding is asked to build.

# 13. Is the risk score AI, and can we audit it?

The go/no-go risk score is deliberately NOT machine learning. It is a 288-line deterministic rule engine that evaluates six factors (ceiling, visibility, wind, precipitation, airspace, traffic programs) on a worst-factor-wins basis, anchored to published Part 135.609 minimums as a conservative threshold reference. It was GREEN/GO at the 2026-08-24 check with 1,141 historical scores retained. It is explainable and auditable — a client, insurer, or lawyer can read the rule, in deliberate contrast to a black-box model. The local LLM writes narrative around the data; it never computes the score. Disclosed gap: there is still no dedicated unit test for the CPS engine.

# 14. Are there regulatory claims we should worry about?

CTDI is decision-support software. It is not an FAA-certified dispatch system, and Part 135.609 is used as a published threshold reference, not a compliance claim. No compliance-framework certification (SOC 2, ISO 27001) is claimed; an ISO 42001 alignment document exists as positioning, not certification. Insurance and GDPR/DPA posture have not been assessed and none is claimed. For flights, the platform's own-RF aircraft visibility is not FAA-source-derived and is not LADD-bound, which is a capability advantage but should be understood by counsel in a client-privacy context.

# 15. Is the public demo real data or a mock-up?

Real data, replayed — not synthetic. The demo is a recorded window of genuine DC-metro operational data (a 2.45 GB and growing replay corpus) played back so patterns unfold in minutes; it is a replay, not live feeds, and it is labeled as such. The demo instance returned to service on 2026-08-24 after a nine-day outage, and its isolation from production data is now structurally enforced at the mount layer and was adversarially verified the same day (it cannot see, read, or write any production file). A public demo link is withheld pending a founder gating decision, so demo access and live production read-outs are arranged in diligence conversations, as was done for the 2026-08-24 verification passes behind these materials.

# Contact

the operator — Founder, [operator LLC], LLC · operator@example.com · info@example.com.
