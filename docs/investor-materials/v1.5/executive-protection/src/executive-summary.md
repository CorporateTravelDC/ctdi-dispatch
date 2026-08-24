## Executive Summary — Sovereign Advance-Work & Movement-Monitoring Intelligence

[operator LLC], LLC · Arlington, VA · August 2026 · Prepared for executive-protection, secure-transportation, and corporate-GSOC audiences

*Every factual claim in this document was re-verified live against the running production system on 2026-08-24 (read-only checks: health/REST endpoints, systemd/podman state, read-only SQLite, git history, source inspection, and the full test suite). Numbers that drift continuously — row counts, restart counters, container counts — are timestamped samples, not fixed facts.*

## Why executive-protection teams should care

CTDI is a 24/7 self-hosted, multi-domain dispatch-intelligence platform built and operated daily by its founder to run his own executive-chauffeur operation. For EP firms, secure-transport providers, and corporate GSOCs, its defining property is **sovereignty**: the entire stack — data ingestion, deterministic risk scoring, alerting, a local LLM, and an institutional-memory knowledge layer — runs on roughly **$765 of owned edge hardware** (a single Raspberry Pi 5) that the operator controls and can physically unplug. Principal itineraries, watchlists, and movement data are held on hardware the client controls; the private production surface is reachable only over Tailscale, bearer-token-gated, with no network-origin trust.

The platform ingests aviation, rail, weather, and airspace data across a **19-feed freshness registry** — including all six FAA SWIM data services under real, approved credentials — computes a deterministic go/no-go operational risk score, and pushes real-time alerts through self-hosted infrastructure. The scoring engine is deliberately **not** machine learning: it is an auditable, worst-factor-wins threshold engine anchored to a published federal minimum standard (14 CFR 135.609, used as a conservative threshold reference). It is decision-support only and makes no regulatory claim — but every state it produces can be explained to a client, an insurer, or counsel.

**For this audience, the differentiator that matters most is not a feature — it is the security discipline.** EP professionals evaluate their own tooling the way they evaluate a venue: by looking for what is wrong with it. This document is written to reward that scrutiny. It reports a self-directed security program honestly, including issues found in our own system and fixed the same day, and it names the items that are still open rather than hiding them.

## Advance-work-relevant capability

- **Real-time flight tracking** — all six FAA SWIM feeds (FDPS, STDDS, TFMS, TBFM, ITWS, AIM/FNS) received under approved credentials, plus own-RF ADS-B reception; per-feed freshness registry with age, stale threshold, and error state on every feed.
- **Aircraft watchlists with real airline-reported on-time history** — hex-resolution discipline (callsign → registration → hex), OOOI phase tracking (out–off–on–in, never reverting), and genuine airline-reported OOOI on-time history over a 14-day window with delay-drift flagging (SCHEMA_V34/V37). Forced identity resolution at pushback fires a one-time resolved-identity push carrying the resolved hex/registration and a live tracking link; auto-expiry extends automatically when a flight actually departs late.
- **Ground-route awareness** — live Amtrak tracking with delay minutes and watchlist-station logic.
- **Weather and airspace picture** — METARs across DC-area stations, NWS/NWWS-OI push weather with severe-weather products (now writing real DC-area alerts after two parser bugs were root-caused and fixed), TFR and NAS-status awareness, and terminal wind-shear/microburst data (ITWS) that has no commercial equivalent.
- **Protective-movement briefs on-device** — scheduled `ep-advance` / `ep` briefs written by a local LLM on the Pi; nothing leaves the hardware. 21 dedicated on-device models; **$0 cloud-LLM spend, measured** across 25,147 logged calls over 46 days (zero cloud-model rows in the usage log).
- **Cross-category intelligence gathering for advance work** — an entity-tracking layer runs inside six daily-watch intelligence categories (AAM, aviation, concierge-travel, executive-protection, gig-economy, trains-yachts) plus an OSINT monitor, detecting the same entity recurring across independent sources, with threshold-based auto-promotion gated by independent-feed corroboration, a human review step, and silence/embargo detection. A consolidated digest aggregates findings every six hours.
- **Institutional memory** — a self-hosted knowledge vault (6,742 documents) under a compiled semantic layer (99 concepts, 51,317 note-to-concept edges) and a causal derivation graph with multi-hop trace queries, recompiled daily. Advance-work knowledge accumulates on hardware the client controls instead of evaporating with each engagement.

## Honesty framework: capability tiers

Every capability claim carries one of three labels, consistent with how this platform documents itself internally: **LIVE & VERIFIED** (personally observed working on the running system), **LIVE WITH DISCLOSED CAVEAT** (real and running, but with an operational limit stated in the same breath), and **ROADMAP / NOT BUILT** (staged, dormant, or absent — never presented as working).

| Capability | Tier |
|---|---|
| Deterministic go/no-go risk scoring (CPS): 6-factor, worst-factor-wins, 288-line rule engine; GREEN/GO live at check | LIVE & VERIFIED |
| 19-feed freshness registry, all six FAA SWIM feeds active and writing at check | LIVE & VERIFIED |
| Aircraft watchlists: hex-resolution, OOOI phase tracking, real airline-reported 14-day on-time history + delay-drift | LIVE & VERIFIED |
| Live Amtrak rail tracking; NWS/NWWS-OI push weather writing real DC-area alerts; TFR/NAS airspace awareness | LIVE & VERIFIED |
| Own-RF ADS-B / ACARS-VDL reception (RF receive current to the minute; DB fusion pending, disclosed) | LIVE WITH DISCLOSED CAVEAT |
| Scheduled on-device LLM briefs (`ep-advance`/`ep`); local Ollama only, $0 cloud | LIVE WITH DISCLOSED CAVEAT (41.7% deterministic-template fallback under contention, labeled) |
| Second-brain semantic/causal knowledge layer + cross-category entity tracking | LIVE & VERIFIED |
| Admin audit trail (32 endpoints, actor/action/tier/IP/payload, 90-day prune) | LIVE & VERIFIED |
| GPG-signed whole-tree manifest gating skill execution and inference | LIVE & VERIFIED |
| Continuous SWIM availability | ROADMAP — feeds are duty-cycled under load governance today (no SLA); disclosed, not claimed |
| Maritime / AIS vessel tracking | ROADMAP — fully dormant (0 rows, empty watchlist, no running unit) |
| Multi-tenancy, billing, CRM, backup/DR, CI | NOT BUILT — pre-revenue by construction |

## Security posture — read this section closely

This is the section this audience will actually read. It is written to be checked line by line.

**What the security program is — and is not.** CTDI's own security controls are re-tested against the live production system on a recurring cadence. The two most recent bounded, non-destructive passes were 2026-08-13 and 2026-08-24. The methodology is deliberately conservative: read-only and source-inspection checks, **1–2 requests per endpoint, no retries**, no fuzzing, no brute-forcing, no high-volume scanning, and no secret, token, key, or coordinate value printed anywhere. **This is founder-run self-assessment, not a third-party penetration test and not a certification.** A first external penetration test / SOC-2-style review is an un-started, fundable line item. We state that plainly because for this audience an honest boundary is worth more than a borrowed credential.

**A demonstrated find → fix → re-verify → adversarially re-verify loop.** On 2026-08-24 the security work ran four stacked passes: a bounded pentest, a basic re-verification, an adversarial re-verification whose default posture was that every "resolved" label is wrong until skeptically disproved under pressure, and a live COGS re-derivation. That discipline is the product here — not a suspiciously clean record.

- **Every open finding from the prior (08-13) pass was closed and independently re-verified**, including that pass's single highest-severity issue: a whole knowledge-vault surface that was anonymously readable from the public internet. It is now closed at two independent layers — the endpoints are tier-gated in code, and Cloudflare Access was restored on the public hostname (verified by a live external request that now returns an access-control redirect where it returned data six weeks earlier).
- **Authorization boundaries were probed live and held on every check.** No-token, forged-bearer-token, malformed-token, and spoofed-public-origin (`X-CTDI-Public: 1`) requests to admin and Tier-1 routes all returned **403** — including header-case and multi-header variants under the adversarial pass. The model is **bearer-token-only; network origin grants no tier.**
- **Credentials are stored only as one-way hashes** (the token table has no plaintext column); the secrets file is owner-only (mode 600); prior full git-history credential scans are clean; and the documented pre-commit / pre-push credential hooks — a gap flagged on 08-13 — are now actually installed.
- **Code that runs on the platform is gated by a signed, whole-tree integrity chain** — a GPG-signed ~706-file manifest, verified in an isolated keyring, that **raises and refuses to execute** on any mismatch. This gates local inference and scheduled skills; it was re-signed and signature-verified the morning of the test.
- **Every administrative action is now audit-logged** — actor, action, tier, source IP, and request payload across 32 endpoints, with a 90-day prune. This control went from 12 total rows on 08-19 to thousands of real entries within days; it is a demonstrable control, not a design intention.

**The demo-isolation incident — told straight, because for EP it is a credibility signal, not a liability.** In the course of the 08-24 work, the re-verification found that the recently-repaired public demo container had been given a mount to the real production data directory — putting the full production database (VIP watchlist/movement data, audit logs, the second brain) one file-open away, and writable — and that an ungated chat endpoint on the public demo shared the production runner's chat file. **These were real findings in our own system.** They were fixed the **same day** — the demo was relocated onto a hardened sibling directory (mode 700) that contains no production file at all — and then an **adversarial re-verification confirmed the fix live against the running container**: the demo can no longer see, read, or write any production file; the shared chat file is now two different files on different inodes; **nothing reopened; no new exploitable exposure was found.** The isolation is now structurally stronger than the model it replaced. This is exactly the kind of issue a superficial review misses and a real one catches — and we catch our own.

**Principal-location data protection is an architectural fact, not a promise.** The receiver's real GPS coordinates live only in the owner-only secrets file (`dispatch-secrets.env`), never in tracked files, and are on the public-mirror scrub list. The endpoint that serves receiver coordinates trust-gates them (an untrusted caller gets a fixed placeholder), and the public demo container structurally cannot leak them because it loads only the non-secret config file — it has no secret to leak. This is verifiable in the code, not asserted in prose.

**What remains open — named honestly.** None of these touches the production operational feeds or the credential store, and none was found exploitable from the untrusted internet in the current topology:

- **The trust boundary depends on Cloudflare's `CF-Connecting-IP` header** with no app-layer backstop. In today's topology this grants no escalation (the only paths lacking an authoritative Cloudflare header are loopback and the tailnet, which are already trusted), but a future ingress reconfiguration could turn it into an origin-spoofing bypass. It is a documented `NEEDS-HUMAN-REVIEW` hardening candidate — this is the one dependency we most want an EP buyer to see us disclose rather than gloss over.
- **Basic credential-lifecycle hygiene is outstanding**: issued tokens do not expire by policy, and one retired integration's admin token is still un-revoked — tracked, not reachable from outside the tailnet, but should be closed.
- **A stated personal-vs-business data-separation boundary** in the personal-export automation is a founder policy call (the anonymous-read exposure it once composed with is closed; the boundary decision itself is open).
- **The public demo's password-gating decision is pending.** The demo surface was repaired and its isolation adversarially verified on 08-24, but until the gating decision lands we do not present a live demo link.

## Data-rights model — client-held subscriptions

Intended commercial architecture: each client operator obtains and holds its own subscriptions and credentials for the underlying data sources (FAA SWIM, NWS/NWWS-OI, FAA NOTAM, and similar). [operator LLC] provides the software, integration, and decision-support layer — **it does not redistribute third-party data.** This is template-supported today: the platform's data-source runbook (`docs/DATA_SOURCES.md`) carries per-source access instructions and credential-request templates parameterized for any organization. Automated multi-tenant onboarding is not yet built — this is documented, template-supported intent, not shipped tenant machinery.

## Economics — measured, not projected

- Runs on **≈ $765 one-time hardware** + **≈ $23–$39/yr electricity**; **$0** data-feed fees; **$0** cloud-LLM spend (measured over 25,147 logged calls across 46 days).
- The purchasable subset of live capability lists at roughly **$55k–$113k/yr** in commercial subscriptions — a floor, since the largest SWIM-class equivalents are quote-only and excluded rather than guessed.
- Stated honestly: the platform's **actual avoided cost** is ≈ $2.2k–$2.7k/yr (mostly reciprocal ADS-B barter). The $55k–$113k figure is replacement cost for a buyer *without* FAA vetting — it is never presented as avoided spend.
- **Ten live capabilities cannot be purchased at any price** — including TFMS flow programs, TBFM arrival metering, ITWS terminal wind-shear alerts, unfiltered blocked-aircraft (LADD) visibility, receive-side ACARS/VDL, a permanently-owned longitudinal corpus, and the signed whole-tree execution gate.

## Honest limitations

- Single developer-operator (bus factor 1); ~2.5-month code history (635 single-author commits, June–August 2026); still **no CI, no CPS unit-test**.
- **Not always-on.** The single Pi 5 deliberately sheds its ingest tier under CPU/Ollama contention — 10 load-shed / restore cycles of ~9–11 minutes in the ~32 hours before the 08-24 check. The restore path is verified working end-to-end, but feeds are duty-cycled, not SLA-backed. We disclose this rather than claim continuous availability.
- **~42% of last week's skill LLM calls fell back to deterministic templates** (labeled, monitored, alert-on-fallback) under that same contention. The inference layer is capacity with honest degradation, not 100% duty.
- Own-RF ACARS/VDL is received to the minute, but fusion of ACARS into the platform database is not complete (upstream router silent — root cause is off-box). Claim "receive-side RF capability," not "ACARS fused into the DB."
- **No backup/disaster-recovery**, no multi-tenancy, no billing/CRM. **Pre-revenue by construction** — the operational runsheet still shows effectively one recorded trip; output is capacity, not consumed demand.
- Maritime/AIS is fully dormant (0 rows) and roadmap-only. The retired MCP integration has been decommissioned and is not claimed anywhere.
- **Licensing** is now real (Business Source License 1.1, adopted 2026-08-24), but the Additional Use Grant language is a working draft under legal review, not yet counsel-confirmed.

## Licensing

The platform is released under the **Business Source License 1.1** (source-available; not OSI-approved open source). Free always for non-production use; free in production for personal self-hosting and for internal relay/middleware use within an organization of any size, **provided** that use never serves a fee-based product or service to a third-party client. Hosted resale, white-labeling, and platform absorption always require a commercial license from [operator LLC], LLC — as does any use touching a fee-based client service, even invisibly or bundled into a retainer. Each release converts automatically to **GPL v3-or-later four years** after first publication (current Change Date 2030-08-24). *Disclosed: the use-grant language is under legal review and not yet counsel-confirmed.*

## Business status and the ask

Pre-revenue; founder-operated; the company site deliberately does not market CTDI yet. The reference deployment is the founder's own operation, running continuously since June 2026. **The ask:** EP, secure-transport, and GSOC design partners to pilot CTDI on hardware they control, and seed conversations to fund the roadmap — productization (multi-tenancy, billing, backup/DR, CI), a first third-party security assessment, credential-hygiene closure, and the disclosed trust-boundary hardening. No valuation, revenue projection, or market-size figure is presented — the fact base does not yet support one, and we do not invent numbers.

*Contact: info@example.com*
