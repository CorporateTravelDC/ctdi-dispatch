## Due-Diligence FAQ — Hard Questions, Straight Answers

[operator LLC], LLC · Arlington, VA · August 2026 · Prepared for executive-protection, secure-transportation, and corporate-GSOC diligence

*Answers derive from live verification of the running production system on 2026-08-24 plus source inspection. Where the evidence is silent, the answer is "Unknown — needs founder input" rather than a guess. No radio frequency, sensitive program value, secret, token, or GPS coordinate appears in this document, by rule and by platform design.*

## 1. How is a principal's location and travel data actually protected?

Structurally, not by promise. **The receiver's real GPS coordinates live only in the owner-only secrets file (`dispatch-secrets.env`, mode 600), never in any tracked file, and are on the public-mirror scrub list.** This is a verifiable architectural fact, confirmed in the 2026-08-24 security work: the endpoint that serves receiver coordinates trust-gates them (an untrusted caller gets a fixed DC-area placeholder, not the real location), and the public demo container is structurally incapable of leaking them because it loads only the non-secret config file — it has no secret to leak whether a caller is trusted or not. More broadly, the platform is self-hosted on hardware the client controls; principal itineraries, watchlists, and movement data are held on that hardware, and the private production surface is Tailscale-only and bearer-token-gated with no network-origin trust. Under the intended client-held-subscription model, each client holds its own source credentials and CTDI does not redistribute third-party data.

## 2. What is the actual audit trail?

Real and demonstrable. Every administrative action is audit-logged via a `require_admin(action)` factory across **32 endpoints**, capturing actor, action name, tier, source IP, and — for mutating requests — the request payload, written before the route handler runs, with a 90-day prune. This is not aspirational: the audit log went from **12 total rows on 2026-08-19 to thousands of real entries within days** (over 3,300 in the 24 hours before the 08-24 check). It was verified live against the running container, not just in the source. There is no SOC 2 or third-party audit, and none is claimed; an ISO 42001 alignment document exists as positioning, not certification.

## 3. What is your security-testing process — and is it a real pentest?

It is a **self-directed, founder-run, bounded, non-destructive security program — not a third-party penetration test and not a certification.** We say that first, because for an EP buyer the distinction is the whole point. The methodology is deliberately conservative: read-only and source-inspection checks, 1–2 requests per endpoint with no retries, no fuzzing or brute-forcing or high-volume scanning, and no secret/token/key/coordinate value printed anywhere. It runs on a recurring cadence — the two most recent passes were 2026-08-13 and 2026-08-24 — and on 08-24 it ran as four stacked passes: a bounded pentest, a basic re-verification, an **adversarial** re-verification (default posture: every "resolved" label is wrong until skeptically disproved under pressure), and a live economic re-derivation. A first **external** penetration test / SOC-2-style review is an un-started, fundable roadmap item.

## 4. Did the security testing find anything? Tell me the worst of it.

Yes — and that is the honest strength, not a weakness. The 08-24 work found two real issues **in our own system**: the recently-repaired public demo container had been mounted onto the real production data directory (putting the full 24 GB production database — VIP watchlist/movement data, audit logs, the second brain — one file-open away, and writable), and an ungated chat endpoint on the public demo shared the production runner's chat file so an anonymous visitor could read or wipe it. **Both were fixed the same day** — the demo was relocated onto a hardened sibling directory (mode 700) that contains no production file at all — and then an **adversarial re-verification confirmed the fix live against the running container**: the demo can no longer see, read, or write any production file, the chat files are now two distinct files on different inodes, and **nothing reopened, no new exploitable exposure was found.** The destructive delete capability was identified by code and reachability and **never invoked** during testing. A review that finds nothing usually means the review was shallow; this one found real things and closed them under adversarial pressure.

## 5. What was tested, specifically?

Authorization boundaries, credential storage, the integrity chain, board-key write gates, GPS/coordinate exposure, and demo-vs-production isolation. Concrete live results: no-token, forged-bearer, malformed-token, and spoofed-public-origin (`X-CTDI-Public: 1`) requests to admin and Tier-1 routes all returned **403**, including header-case and multi-header variants under the adversarial pass; the token table has no plaintext column (hashes only); the secrets file is mode 600; the signed manifest verifies in an isolated keyring and raises on mismatch; the coordination message board's write path is constant-time key-compared with a scrub gate and rate limit while its reads are anonymous by design; and the previously-anonymous vault research-read surface is now board-key gated (verified 401 without a key, 200 with one).

## 6. What are the authorization tiers, and what gates admin?

Auth is enforced as FastAPI dependencies in `src/auth/auth.py`, **bearer-token only — network origin grants no tier** (the older spoofable header/IP grant was removed): T0 anonymous (also forced for any request carrying `X-CTDI-Public: 1`), T1 (`tier=cert`), T2/SHARES (`tier=shares`, audit-logged), and Admin (`tier=admin`). Token format is `ctdc_<user>_<random>`; only the SHA-256 hash is stored. One `/admin/*` endpoint is deliberately unauthenticated by design — the passwordless-approval resolve link, secured by a 122-bit unguessable UUID and single-use enforcement, because Cloudflare strips the `Authorization` header through the tunnel and a token-gated endpoint would be untappable from a phone off the tailnet.

## 7. What security items are still open? Name them.

Four, none of which touches the production operational feeds or the credential store, and none found exploitable from the untrusted internet in the current topology:

- **The trust boundary depends on Cloudflare's `CF-Connecting-IP` header, with no app-layer backstop.** In today's topology this grants no escalation (the only paths without an authoritative Cloudflare header are loopback and the tailnet, both already trusted), but a future non-Cloudflare ingress could convert it into an origin-spoofing bypass. Documented `NEEDS-HUMAN-REVIEW`; hardening candidates are narrowing the forwarded-IP allow-list and requiring the header's presence for any public-hostname request.
- **Credential-lifecycle hygiene**: issued tokens do not expire by policy (all currently carry a null expiry), and one retired MCP-bridge integration's admin token is still un-revoked — tracked, not reachable from outside the tailnet, but should be closed.
- **A stated personal-vs-business data-separation boundary** in the personal-export automation is a founder policy call. The anonymous-read exposure it once composed with is closed; the separation-boundary decision itself is open.
- **The public demo's password-gating decision.** The demo was repaired and its isolation adversarially verified on 08-24; the gating decision is the remaining operator call before it is promoted as an investor demo link.

## 8. What happens to data on the public demo surface? Is it isolated?

Yes — and this was the sharpest question the 08-24 testing answered. The demo is designed to serve a separate recorded-replay database over its own API, never production. During the crash-loop repair a mount briefly weakened that boundary (see FAQ 4); the fix relocated the demo onto a hardened sibling directory that contains **no production file at all**. Verified live from inside the running demo container: the real production database and second-brain index are **absent** — not merely unopened, but not mounted — so the worst case for any future arbitrary-file-read or path-traversal bug in the demo is "the empty demo directory," not "all of production." The demo's own chat file is a different file on a different inode from production's. This is now the strongest isolation layer in the system, verified against the actually-running container, not just the tracked config.

## 9. Is the go/no-go scoring AI? Is it certified?

Neither. The Critical Predictability State (CPS) engine is a deterministic, 288-line rule engine: hardcoded thresholds drawn from 14 CFR 135.609 VFR minimums as a published, conservative reference, with marginal bands, precipitation classification, ground-stop/GDP handling, and worst-factor-wins aggregation — no weights, no ML, no black box. Every GREEN, MARGINAL, or violated state is explainable to a client, insurer, or counsel. CTDI is decision-support only — not an FAA-certified dispatch system, not a Part 135 operation, no regulatory claim. Known verification gap, restated: the engine still has **no dedicated unit test**, and there is **no CI**.

## 10. Where does the flight, rail, and weather data come from — and what are the terms-of-service exposures?

Mostly clean, with a few items worth diligence. Clean and public-domain: FAA SWIM (all six feeds, under an approved subscription), FAA TFR/NOTAM, and NWS/NWWS-OI/aviationweather. Community/unofficial and worth review: Amtrak (community API — no federal SWIM rail equivalent exists), DCA/IAD airport-board scrapes (undocumented JSON, no SLA, disclosed), and ADS-B aggregator accounts (reciprocal barter, earned by feeding our own RF data back). FAA SWIM is primary; the scraped/community sources are fallback tiers. The intended deployment model largely moots redistribution: each client holds its own source credentials and CTDI supplies software, not redistributed data. Formalizing these relationships in client agreements still needs founder input.

## 11. What intelligence-gathering does it do for advance work, beyond live tracking?

A cross-category entity-tracking layer runs inside six daily-watch intelligence categories (AAM, aviation, concierge-travel, executive-protection, gig-economy, trains-yachts) plus an OSINT monitor. It detects the same entity recurring across independent sources and applies threshold-based auto-promotion gated by **independent-feed corroboration, a human review step, and silence/embargo detection** — a discipline that, per the platform's own vendor analysis, zero of ten enterprise competitive-intelligence products document. Findings are consolidated into a digest every six hours. Underneath sits a self-hosted knowledge vault (6,742 documents) with a compiled semantic layer (99 concepts, 51,317 edges) and a causal derivation graph supporting multi-hop trace queries, recompiled daily. For advance work, the value is that pattern knowledge accumulates on client-controlled hardware across engagements instead of evaporating.

## 12. Do any LLM components send data off-device?

No — and this is measured, not asserted. Brief generation (`ops-brief`, `ep-advance`, and others) targets a local Ollama instance on the Pi with 21 dedicated on-device models. The cloud-fallback flag is set to false, and every container that runs a skill loads that setting; the usage log shows **zero cloud-model rows across 25,147 logged calls over 46 days** ($0 cloud-LLM spend, measured). Honest caveat: about 42% of last week's skill LLM calls fell back to deterministic templates under Ollama contention — labeled, monitored, and alerted, so briefs degrade honestly rather than silently, but the inference layer is capacity with disclosed degradation, not 100% duty.

## 13. Is it always available? What are the reliability implications of single-Pi hosting?

Honestly: **it is not always-on, and we do not claim it is.** The single Raspberry Pi 5 deliberately sheds its ingest tier under CPU/Ollama contention — a designed load-shed, not a fault — with 10 shed/restore cycles of ~9–11 minutes each in the ~32 hours before the 08-24 check. The restore path is verified working end-to-end every time, and a hardened watchdog (restart-only-what-failed, a ~7.5-minute debounce, and load-shed-aware suppression) no longer fights the load governor. But this is duty-cycled availability without an SLA, on single-node residential-class hosting with no evidenced backup/DR. Two framing points: client deployments run on client-controlled premises, and the load-shed calibration plus DR are acknowledged, funded roadmap items — not solved problems. An EP buyer who needs SLA-grade continuity should read this as a real, current limit.

## 14. What is the single-developer risk?

Real and disclosed: bus factor is 1. The codebase is ~51,070 lines of Python plus a React front end (27 components), written across ~2.5 months (635 single-author commits, June–August 2026). Mitigants: a GPG-signed whole-tree manifest with an execution gate, a 218-test suite (217 pass, 1 known pre-existing unrelated failure), automated scrub-and-mirror to a public repository, and a self-describing, heavily-documented codebase. Succession or key-person insurance: Unknown — needs founder input. Retiring bus-factor-1 via a first engineering hire is an explicit roadmap item.

## 15. What certifications, insurance, and compliance exist?

None are claimed. No SOC 2, no ISO certification (the ISO 42001 document is alignment/positioning, not a certificate), no FAA certification (none required for decision-support). FAA LADD handling exists for aircraft-privacy compliance — and notably, the platform's own-RF reception is not LADD-bound, so it retains visibility that every commercial feed obfuscates. Business insurance, DPA/GDPR posture, and data-processing agreements: Unknown — needs founder input.

## 16. What's the licensing and IP posture?

Now real, and disclosed as a draft. The platform carries a **Business Source License 1.1** (adopted 2026-08-24; Licensor: [operator LLC], LLC): free for all non-production use; free in production for personal self-hosting and internal relay/middleware use within an organization of any size, unless that use serves a fee-based third-party client service — in which case, along with any hosted resale, white-labeling, or platform absorption, a commercial license is required. Each version converts to GPL v3-or-later four years after first publication (Change Date 2030-08-24), so the platform opens over time rather than staying locked indefinitely. **Disclosed: the Additional Use Grant language is a working draft under legal review and not yet counsel-confirmed** — do not treat it as final for a production licensing decision. Formal IP assignment to the LLC: Unknown — needs founder input.

## 17. Can it serve multiple clients today? (Multi-tenancy / white-label)

Not as a configuration toggle. Today it is a single shared database with operator-scoped naming and feed-visibility scoping — no tenant isolation, no per-tenant branding. White-label today means redeploy-per-operator on hardware the client controls, which happens to align with how EP buyers want their movement data held anyway. True multi-tenancy, billing, and entitlements are roadmap.

## 18. Where is the billing, CRM, and customer machinery?

There is none — no billing, pricing, subscription, entitlement, or customer-account code exists anywhere. The company is pre-revenue on this product and the website deliberately does not market it. The operational runsheet's newest entry still shows effectively **one recorded trip** — output is capacity, not consumed demand. Node economics are favorable (≈ $765 one-time hardware, ≈ $23–$39/yr electricity, $0 feed fees, $0 cloud spend), but that is a bill of materials and a cost story, not a business model.

## 19. What does the money story actually say — is CTDI cheap, or valuable?

Both, told separately and never conflated. **Cheap to run:** ≈ $765 one-time + ≈ $23–$39/yr, with $0 in data-feed fees and $0 in measured cloud-LLM spend. **Valuable to replace:** the purchasable subset of its live capability lists at roughly **$55k–$113k/yr** in commercial subscriptions — but that is a replacement-cost floor for a buyer *without* FAA vetting, explicitly **not** presented as avoided spend. The platform's genuine avoided cost is ≈ $2.2k–$2.7k/yr, mostly reciprocal ADS-B barter. And ten live capabilities — TFMS flow programs, TBFM arrival metering, ITWS terminal wind-shear alerts, unfiltered blocked-aircraft visibility, receive-side ACARS/VDL, a permanently-owned corpus, and the signed execution gate among them — **cannot be bought at any price.** Cost avoidance does not carry a valuation, and none is claimed.

## 20. What would investment actually fund?

The verified gap list, in priority order visible from the codebase and the security work itself: multi-tenancy and white-label configuration; disaster recovery and backup; CI and verification hardening (including a CPS unit test); a **first third-party security assessment**; closure of the disclosed trust-boundary dependency and credential-lifecycle hygiene; formalized data-source agreements and finalized BSL terms with counsel; and the entirely-absent commercial machinery (billing, entitlements, customer management). A specific raise amount and valuation: Unknown — needs founder input; no figures are invented in these materials.
