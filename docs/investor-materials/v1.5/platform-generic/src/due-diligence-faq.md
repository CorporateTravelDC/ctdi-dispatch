Due-Diligence FAQ — v1.5

Hard questions, answered honestly — platform audience (investors, acquirers, white-label partners)

[operator LLC], LLC · Arlington, VA · August 2026 · info@example.com

**How to read this document.** Answers are grounded in a live re-verification of the production system, a bounded security re-validation, and an adversarial re-verification, all performed 2026-08-24, plus the platform's own continuously-maintained internal state documentation. Where a count drifts continuously (rows, containers, commits) it is a timestamped sample. Where the honest answer is "not built" or "needs founder input," it says exactly that. No market sizes, revenue projections, or user counts appear anywhere: none exist in the fact base.

# Q1. Who maintains this platform? What is the bus factor?

One person. the operator (USMC veteran; owner, [operator LLC], LLC) designed, built, and operates the entire stack; git history shows 635 commits from a single author between 2026-06-07 and 2026-08-24. Bus factor is 1. This remains the project's single largest risk, and the funding ask explicitly includes a first engineering hire to retire it. Partial mitigant: administration is fully headless and documented (no physical presence required for normal operation), and the platform's operational knowledge is written down aggressively — the internal state documentation records incidents, root causes, and open decisions in enough detail for a successor to pick up.

# Q2. The codebase is under three months old. How mature can it be?

Honest framing: it is young. What offsets the age: the platform has run continuously as the founder's real dispatch operation since June 2026, and its scale and hardening are measurable — 51,070 lines of Python, 102 REST routes, 218 tests, a signed 706-file manifest, and a documented history of real incidents found, root-caused, and fixed (feed parser bugs, watchdog/governor conflicts, a duplicate-insert bug) rather than a pristine surface. Maturity is demonstrated operationally, not by calendar age — but a buyer should weight the short history accordingly.

# Q3. Is there CI/CD? What is the test posture?

There is still no CI configuration — a genuine, twice-restated gap. Tests exist and have grown ~73% since the 2026-08-09 verification: 23 pytest files, 3,956 lines, 218 tests, 217 passing (the single failure is a known, tracked, pre-existing marine-detection assertion unrelated to core function; the suite was re-run during the 08-24 verification, 11.4 s). Notably still absent: a dedicated unit test for the CPS engine itself. Deployment is scripted and integrity-gated (GPG-signed manifest, verified-exec gates on 33 skill containers, a 15-minute integrity sweep) but not gated by automated CI checks.

# Q4. What actually runs in production today — verified, not claimed?

Verified live on 2026-08-24: service health (/healthz ok, snapshot age 3 s); the CPS engine computing GREEN/GO across 6 factors; a 19-feed freshness registry; all six FAA SWIM data services provisioned and writing (839,101 flight events, 32,459 in the prior 24 h); 822,317 train events; 5,424 NOTAMs across 308 facilities; 121 active TFRs; NWS push alerts writing real products; TBFM arrival metering (35,615 sequences) and surface-movement data (120,071 events); own-RF ADS-B (17 aircraft in view at check) and ACARS/VDL receive current to the minute; 21 local LLM models producing 272 scheduled briefs over 7 days; a 6,742-document knowledge vault with a compiled semantic/causal graph recompiled daily; and a full admin audit trail (4,397 rows). All observed directly, not claimed from documentation.

# Q5. Is the CPS risk score AI? Is it predictive?

No on both counts, deliberately. CPS is a deterministic rule engine (288 lines, read in full during verification): published Part 135.609 VFR minimums as conservative threshold references, marginal bands, precipitation classification, ground-stop/GDP handling, and severity escalation, combined worst-factor-wins with no weights and no ML. Every score is reproducible and explainable to a client, insurer, or court. The local LLM is used only to write narrative briefs around the data — never to compute the score. Known gap, unchanged: the engine has no dedicated unit test yet (see Q3); it is small, deterministic, and observed computing live hourly.

# Q6. What is your penetration-test history?

Real, recent, and honest about what it is: **founder-directed, bounded, non-destructive security testing of the live production system — not a third-party audit.** The record:

- **2026-08-13:** first bounded pentest pass. Found real issues, including one high-severity finding — a knowledge-vault surface anonymously readable from the public internet — plus a documented-but-not-installed pre-commit credential hook.
- **2026-08-24:** full re-validation. Both open 08-13 findings confirmed closed and independently re-verified — the vault exposure at two independent layers (code-level tier gates, verified by live probe, plus restored Cloudflare Access on the public hostname, verified by live external request). Everything that was confirmed-working on 08-13 still held: anonymous, forged-token, and spoofed-origin requests all rejected; tokens stored hash-only; secrets file owner-only.
- **Same day, new findings:** the pass also hunted new surface and found real items — a keyless research-read API whose scope was broader than intended, a latent personal-data write path, and an unauthenticated receiver-GPS endpoint. All were remediated the same day (credential-gating, trust-gating, structural env-file omission).
- **Same day, the loop caught its own remediation:** the fix that revived the public demo briefly introduced two genuine regressions (a production-database mount into the demo container; a shared chat file reachable through an ungated endpoint). The follow-up re-verification pass caught both; both were structurally closed at the mount layer.
- **Same day, adversarial re-verification:** a final pass whose stated default was that every "resolved" label is wrong until skeptically disproved — header-case variants, encoding tricks, multi-header injection, repo-vs-live drift checks against the actually-running containers. Verdict: nothing reopened, no new exploitable exposure.

Why we present it this way: "found real issues, fixed them same-day, adversarially re-verified" is a demonstrated find-fix-verify loop. We do not claim "zero findings ever," we do not call this an external red-team, and a first third-party penetration test is an explicit, fundable roadmap item.

# Q7. What are the security strengths?

Verified live on 2026-08-24: bearer-token-only authentication where network origin grants no tier (a prior header-trust design was removed as spoofable); tokens stored as SHA-256 hashes only, no plaintext column; secrets in an owner-only (0600) file with a git history clean of credentials; every administrative action audit-logged across 32 endpoints with actor/IP/payload capture and 90-day retention (this control went from 12 total rows to thousands of daily rows in two weeks — a real control, demonstrably exercised); a GPG-signed whole-tree manifest (706 files) that skill containers and the inference layer refuse to execute against on any mismatch, re-verified every 15 minutes; rootless containers under systemd; a CUI/PII scrub gate that blocks rather than redacts; and pre-commit/pre-push credential hooks now actually installed (closing an 08-13 finding). The public demo is structurally isolated from production at the mount layer — adversarially verified against the running container, not the config file.

# Q8. And the security weaknesses?

Known, tracked, and stated: (1) all security testing to date is internal self-assessment — no external pentest or audit yet; (2) credential-lifecycle hygiene is open — issued API tokens do not expire (expiry is implemented but never used at mint time), and one retired integration's admin token still awaits revocation; (3) the public-origin trust model depends on Cloudflare's edge header with no app-layer backstop — not exploitable in the current topology (verified adversarially), but a documented hardening candidate; (4) two deliberately-public keyless read surfaces (a coordination board; a research-read API, now credential-gated) await final founder scope decisions; (5) no vulnerability-disclosure bounty program. None of the open items touches the production operational feeds or the credential store.

# Q9. What is the license? What does it actually protect?

**Business Source License 1.1**, adopted 2026-08-24 — closing the "no LICENSE file" gap flagged in every prior verification pass. Verified as canonical, unmodified BSL 1.1 text (the same structure used by MariaDB, HashiCorp, and Sentry) with a correctly-formed parameters block: Licensor is [operator LLC], LLC; the Change Date is 2030-08-24; the Change License is GPL v3-or-later, which satisfies the BSL covenants.

What is free: all non-production use (evaluation, development, testing), always; and production use limited to (a) personal self-hosted deployments, or (b) internal relay/middleware use by an organization of any size for its own internal operations — provided no part of that use serves, supports, or contributes to any fee-based product or service rendered to a third-party client or customer.

What requires a commercial license: offering CTDI (or anything substantially derived from it) as a hosted, managed, white-labeled, or embedded service, whether or not for a fee; rebranding or redistributing it as part of another product; absorbing it into a platform beyond a distinct relay; and — the controlling rule — any use in connection with any fee-based client service, even solely as an invisible internal middleware step, and even when bundled into an overall fee, retainer, or rate.

Disclosed plainly, matching the repository's own license notice: the Additional Use Grant is a working draft currently under legal review; it reflects intended terms but has not yet been confirmed by counsel. Prospective commercial licensees should contact [operator LLC], LLC directly.

# Q10. It runs on one Raspberry Pi. Is that a toy?

It is a real constraint and a real proof, and we present it as both. The constraint: one Pi 5 (4 cores, 16 GB) on residential power and ISP is the entire production plant — no second node, no backup system, no DR plan. Every resource limit, timeout, and thermal threshold is tuned to shared contention on this one box, and the repository documents this explicitly as a single-edge-unit assumption set: de-consolidating onto more nodes means re-measuring, not copy-pasting. The proof: the full stack — six SWIM feeds, 21 local LLM models, the semantic layer, ~38 containers — genuinely runs 24/7 on ~$765 of hardware at ~$23-39/yr of electricity, with the load governed by an automatic shed/restore mechanism that was observed executing 10 clean cycles in ~32 hours. That governance is also the honest availability caveat: feeds are duty-cycled under contention, not always-on, and no SLA exists. Multi-node deployment, DR, and an availability target are priced roadmap items, not promises.

# Q11. What are the data sources, and what are the provenance risks?

Itemized: six FAA SWIM data services (FDPS, TFMS, TBFM, STDDS, ITWS, AIM/FNS) under real, approved credentials — free to approved subscribers, $0 in fees, verified writing live; NOAA/NWS public-domain weather including the NWWS-OI push channel; FAA TFR and NAS status; the platform's own RF receivers (ADS-B, ACARS/VDL — owned data, no license terms at all); Amtrak via a community API (no federal equivalent exists for rail — needs formalization); DCA/IAD airport boards scraped from undocumented airport-site JSON (fallback tier only, fragile, no SLA, disclosed); and self-polled public RSS for OSINT. The NOTAM REST pull path still awaits an FAA API key; NOTAM data flows via SWIM push regardless. The intended commercial model largely moots redistribution questions: each client holds its own source credentials, and [operator LLC] ships software, not third-party data. ADS-B aggregator accounts (FlightAware et al.) are barter — earned by feeding our own receiver data — and are disclosed as such in the cost analysis, not counted as free.

# Q12. What does it cost to run, and what is the economic argument?

Measured, not modeled, as of 2026-08-24: ~$765 one-time hardware (itemized BOM); ~$23-39/yr electricity (wattage bounds from published measurements, DC tariff re-confirmed from the EIA table that day); $0 data-feed fees across all 19 registry feeds; $0 cloud-LLM spend — measured across 25,147 logged LLM invocations over 46.4 days containing zero cloud-model rows.

Two distinct claims, never conflated: the honest **avoided-cost** number for this instance is ~$2,160-2,655/yr net (conservative, published-price-only, and roughly two-thirds of it is reciprocal barter). The **subscription-replacement floor** — what a buyer without FAA vetting would pay to replicate the purchasable subset of live capability — is ~$55,200-112,900/yr, and it is a floor because the largest true equivalents are quote-only and excluded, and because ten live capabilities are not purchasable at all (TFMS/TBFM/ITWS-class data, LADD-unbound visibility, receive-side ACARS, a permanently-owned corpus, and the automation layer over it). The analysis itself forbids presenting the floor as avoided spend, and so do we.

# Q13. What is NOT built? What should we not believe?

Stated plainly, unchanged in kind since the 2026-08-09 pass: **no billing, no subscription or entitlement code, no CRM, no multi-tenancy** (single-tenant; white-label today means redeploy-per-operator), **no backup/DR**, **no CI**, **no CPS unit test**, **no SLA**. Pre-revenue: there are no external customers, and the operational runsheet's newest entry records one trip — the platform's output is capacity, not yet realized demand, and we say so. Also deliberately not claimed: maritime/AIS (fully dormant — zero rows, roadmap only, priced separately in the cost analysis); ACARS fused into the platform database (receive-side RF is real; DB fusion pends an off-box fix); continuous feed availability (see Q10); and any MCP integration — the platform's MCP bridges were deliberately retired 2026-08-18 and all prior MCP claims are withdrawn from these materials.

# Q14. Is there a demo we can see?

The demo infrastructure is real: a recorder captures live operational windows into a separate replay database (2.45 GB and growing), and the demo runner replays them — honest by construction, labeled as replay rather than live feeds. Current status, disclosed: the public demo instance returned to service on 2026-08-24 after a nine-day crash-loop outage; its isolation from production data is now structurally enforced at the mount layer and was adversarially verified the same day (the demo container cannot see any production file). Its public access-gating policy is a pending founder decision, so demo access is arranged directly in diligence conversations. A live read-out of the production system itself can also be arranged, as was done for the verification passes behind these materials.

# Q15. What is the "second brain," and why does it matter commercially?

A self-hosted knowledge vault the platform writes to on its own schedule: 6,742 indexed documents as of 2026-08-24, fed by nightly diaries, weekly syntheses, daily category watches, and entity-tracking digests. New since the prior verification: a compiled semantic layer — 99 curated concepts, 51,317 note-to-concept edges, and a causal derivation graph (26,448 edges) supporting multi-hop "what led to this / what depends on this" queries — recompiled daily by a verified timer. Every write passes a scrub gate that blocks rather than redacts. Commercially it is the compounding asset: the corpus is permanently owned (commercial data licenses require destruction on exit; this does not), and the accumulated history surfaces patterns no real-time view can — with the platform's own digests honestly stating when they lack enough history to conclude anything.

# Q16. What is the regulatory posture?

Decision-support software only. CTDI is not an FAA-certified dispatch system and makes no regulatory claim; Part 135.609 (a published HEMS weather-minimums standard) is used as a conservative, citable threshold reference. Aircraft-privacy handling: the platform's own-RF layer is not FAA-source-derived and therefore not LADD-bound — a documented capability differentiator; the platform separately honors privacy norms in what it publishes (GPS coordinates, feeder IDs, and identifiers are scrubbed from all public artifacts by an enforced pipeline). Not assessed and not claimed: GDPR/DPA posture, insurance coverage, SOC 2 / ISO 27001. An ISO 42001 alignment document exists as positioning and says of itself that it is not a certification.

# Q17. What is actually defensible? Couldn't a funded team rebuild this?

A funded team could rebuild the code. The honest moat: (1) data access and assets that money cannot buy — the cost analysis's verified negative finding is that no commercial vendor at any price sells TFMS flow programs, TBFM metering, ITWS terminal alerts, LADD-unfiltered visibility, or receive-side ACARS, and a rebuilt platform starts with zero corpus while this one owns its history permanently; (2) FAA SWIM approval and the operational know-how encoded in feed SLAs, failover, dedup, load governance, and the incident history — decisions learned from running a real dispatch desk on real edge constraints; (3) the deterministic, standards-anchored scoring approach, defensible in front of clients, insurers, and lawyers; (4) sovereignty on sub-$900 hardware as a proven configuration with measured-zero cloud spend; (5) BSL 1.1 licensing that keeps commercial use gated while committing to eventual open source; (6) founder-market fit in a discreet, trust-driven vertical. No patents are claimed. Speed to design partners matters more than secrecy.

# Q18. What would an investment actually fund?

In priority order: (1) productization of the known, well-scoped gaps — multi-tenancy, billing/entitlements, backup and DR, CI, closure of the documented security-hygiene items; (2) a first third-party security assessment to convert the internal testing record into external assurance; (3) data-rights formalization (fallback sources under agreement; BSL Additional Use Grant finalized with counsel); (4) 2-3 design partners recruited via the replay-demo motion, with pricing co-defined — no pricing model exists yet, deliberately; (5) first engineering hire to retire bus-factor-1. Specific budget allocation and valuation expectations: needs founder input — the fact base does not support invented numbers, and none appear in these materials.

# Q19. If diligence proceeds, what can we inspect directly?

The private repository and its GPG-signed public mirror (scrub pipeline enforced); the five 2026-08-24 verification documents behind this FAQ (live re-verification, cost analysis, pentest, pentest re-verification, adversarial re-verification — each recording its methodology, its evidence, and what it could not confirm); a live production read-out over a screen-share; the demo replay; the signed manifest and audit log; and the platform's own internal state documentation, which records incidents and open decisions with a candor that is itself diligence evidence.
