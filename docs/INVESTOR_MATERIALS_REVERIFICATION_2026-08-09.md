# Investor Materials — Live Re-Verification & v1.2-draft Refresh (2026-08-09)

> **What this is.** On 2026-08-09, at the operator's direction, every factual
> and technical claim in the canonical v1.1 investor materials
> (`Nextcloud: corporatetraveldc/Docs/investor-materials/v1.1/`, 16 files,
> last regenerated 2026-08-05) was re-verified **live against this
> platform's own running production system and its own codebase**. This was
> a documentation-accuracy exercise — pressure-testing the docs' claims
> against reality — performed by the operator's own AI tooling on the
> operator's own infrastructure. It was **not** a security assessment, not
> a penetration test, and not an external audit of any kind; no
> exploitation, scanning, or defense-testing was performed. Read-only
> checks only: REST/health endpoints over the tailnet, systemd/podman
> state, SQLite read-only queries, git history, and source inspection.
>
> **Output:** a refreshed draft set at
> `Nextcloud: corporatetraveldc/Docs/investor-materials/v1.2-draft/`
> (canonical v1.1 untouched), with a local review copy at
> `/opt/corporatetraveldc/investor-materials/v1.2-draft-2026-08-09/`.
> Promotion of the draft over v1.1 is the operator's call.

## Re-verification snapshot (2026-08-09, ~16:15Z)

| Claim (v1.1) | Observed 2026-08-09 | Verdict |
|---|---|---|
| Service healthy, `/healthz` OK | OK; CPS GREEN/GO; snapshot age 8 s | ✅ holds |
| CPS 6-factor deterministic engine, GREEN/GO | GREEN/GO, all 6 factors ok, Part 135.609 narrative; engine 288 lines | ✅ holds (289→288 lines, trivial) |
| 19-feed freshness registry | 19 feeds, all with age/threshold/error state | ✅ holds |
| SWIM push receiving; FDPS/ITWS/STDDS stale at 08-04 check (thermal shed) | **All 7 SWIM push feeds fresh (5–29 s)** — client-ack fix (2026-08-05) also cured STDDS/TFMS crash-loop | ✅ improved — docs updated |
| 132 TFRs / 17 Amtrak / DCA 454-641 / METAR 7 stations (point-in-time) | 151 TFRs / 71 trains / DCA 880-868 / 7 stations | ✅ historical figures kept, new snapshot added |
| ~20 containers, ~55 systemd units | 28 containers, 62 quadlet units | 🔄 updated |
| 545 commits, single author, Jun 7–Aug 3 | 583 commits, single author, Jun 7–Aug 9 | 🔄 updated |
| ~37k Python LOC; 27-component PWA; ~60 REST endpoints | ~42k LOC; 25 JSX components; ~70+ routes | 🔄 updated |
| 15 pytest files ~2,128 lines; **no CPS unit test; no CI** | 16 files, 2,288 lines; still no CPS test; still no CI | 🔄 counts updated; gaps unchanged & restated |
| No LICENSE files; SECURITY.md template | Still true (SECURITY.md is 21-line template) | ✅ unchanged, restated |
| 4 active API tokens | 4 active | ✅ holds |
| Tailscale-tier header-trust **spoofing caveat (open)** | **Remediated 2026-08-05** — nginx-authoritative network-layer marker; trust model documented; auth tests promoted internal | 🔄 docs updated (caveat → disclosed-and-closed) |
| LLM briefs CODE-COMPLETE (Ollama offline at 08-04 check) | **Hourly** ops + EP-advance briefs live on production timers; 48 h sample: ops 38/47 and EP 59/64 runs produced full LLM narratives (~87%), honest deterministic fallback otherwise; fallback monitor timer live | 🔄 upgraded with candor (fallback % stated) |
| Self-healing watchdogs: no run recorded | Watchdog **fleet** observed running (ADS-B link-flap, ACARS-silence, container-mem, ntfy-topic-count, brief-fallback, feed-DB integrity, pull-path verifier, freshness audit); legacy `/admin/watchdog/status` still `no run recorded yet` | 🔄 updated, nuance kept |
| ntfy 9 topics | 15 mapped topic channels | 🔄 updated |
| Dispatch MCP 33+ tools; safety-rails ~43 tools | 34 named tools; 55 `@tool` registrations | ✅ "33+" kept; ~43→~55 |
| Second brain: 2,754 indexed docs | 3,750 docs; nightly scan + diary + weekly synthesis timers all scheduled; notes→vault import (2-min) live | 🔄 updated |
| Demo = labeled 14-day replay | Unchanged; login gate now carries explicit not-live-data disclosure (2026-08-07) | ✅ strengthened |
| "~$100 edge hardware" | Deployed node is a 16 GB Pi 5; operator's own internal cost doc (`docs/COST_STRUCTURE.md`) puts real BOM well above $100 | 🔄 softened to "few-hundred-dollar / sub-$1k" pending founder decision (see open items) |
| `anthropic` SDK cloud-path "Unknown" (EP FAQ) | Now explicit: `ANTHROPIC_FALLBACK_ENABLED` gate (2026-08-07), off by default, SR-1/SR-2 governed | 🔄 answered in draft |
| NOTAM REST pull awaiting FAA key | Still awaiting (`notam` feed 2.3 d old); NOTAMs flow via SWIM `push:fns` | ✅ unchanged, restated |
| No billing / multi-tenancy / DR; pre-revenue | Unchanged | ✅ restated |
| Roadmap dates (EV tolling spring 2026; eVTOL spring 2027; AIS end 2026) | Operator-authoritative; unchanged | ✅ untouched |

## New capability folded into the draft (all observed live 2026-08-09)

1. **Hourly local-LLM intelligence briefs** (ops + EP-advance, hourly timers,
   Ollama prewarm scheduling, brief-fallback monitor).
2. **Agent coordination message board** — `/api/v1/board*`: token-gated,
   nonce-enrolled agent-to-agent handoff board; live since 2026-08-07 with
   real coord/research threads; hourly dispatch-side sweep.
3. **Daily-watch intelligence fleet + RAG-lite novelty pipeline** — aviation
   & AAM watches twice daily plus EP / concierge-travel / gig-economy /
   trains-yachts categories; lexical retrieval over the vault; alias-aware
   cross-link dedup; novel findings auto-routed to a vault review inbox.
4. **SWIM client-acknowledgement fix** + per-topic dedup + DC-local vs.
   nationwide STDDS alert tiering.
5. **Reliability/observability hardening** — pull-path verifier (12 h),
   feed-vs-DB integrity check, TBFM→arrival-time FIDS enrichment (15 min),
   ADS-B/ACARS watchdogs, container memory-pressure watch, hard resource
   governance (memory ceilings, zero-swap, CPU quotas).
6. **Second-brain automation** — one-way personal-notes→vault import;
   dedicated vault storage account behind split external-access
   architecture (cloud./dav.).
7. **Auth hardening** — XFF fix (2026-08-05) + documented trust model.

## Deliberately NOT folded in (operator decisions pending)

- **Hardware cost tiers & build-vs-buy comparison** — `docs/COST_STRUCTURE.md`
  carries an explicit HOLD ("do not promote into investor-materials")
  pending the AIS/AAM roadmap decision. The draft only *softens* the stale
  "~$100" claims to "few-hundred-dollar / sub-$1k" wording; exact tier
  figures ($700 floor / sub-$900 reference / $1,100–1,200 full-RF) and the
  subscription-comparison slide await the founder's explicit go.
- **Honeypot/fail2ban work, research-board-mirror** — uncommitted in the
  working tree; not claimed anywhere until committed and verified.
- **gemma3-SWA root fix** (rebuild brief models from qwen2.5:3b/llama3.2:3b)
  — greenlight pending; drafts state the measured ~87% narrative rate honestly.

## Editing method & QA

Byte-level XML surgery on copies (docx `word/document.xml`, pptx slide XML);
every edited part XML-validated on write; full-text extraction diff + stale-
string audit across all 16 files (clean); guardrail grep confirmed no radio
frequencies or sensitive program identifiers introduced (only 135.609
present, as intended); every deck slide footer now reads "verified live
2026-08-04 · re-verified 2026-08-09"; every docx gained a
"Verified Update — 2026-08-09 Live Re-Verification" addendum. All 16 vault
uploads hash-verified (SHA-256 round-trip).

## Open items needing founder input (carried + new)

1. Promote `v1.2-draft/` → replace v1.1 contents (or keep as draft for the
   ~8/13 pitch refresh). Decks did **not** gain new slides — new capability
   lives in the docx addenda; say the word if you want a "shipped since
   verification" slide added to any deck.
2. Hardware-cost figures: bless promoting COST_STRUCTURE numbers (and the
   build-vs-buy slide) or keep the qualitative wording.
3. gemma3-SWA model rebuild greenlight (briefs to ~100% narrative).
4. Carried from v1.1: client-held-subscription contractual language, pricing
   validation, IP assignment, insurance/GDPR posture, DR plan, succession.
