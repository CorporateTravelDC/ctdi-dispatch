# Investor Materials — Live Re-Verification & v1.5-draft Refresh (2026-08-24)

> **What this is.** On 2026-08-24, at the operator's direction, every claim in
> the most recent investor-materials re-verification
> (`docs/INVESTOR_MATERIALS_REVERIFICATION_2026-08-09.md`) was re-verified
> **live against this platform's own running production system and its own
> codebase**, continuing the established methodology (v1.1 → 2026-08-09 →
> this pass). This is a documentation-accuracy exercise — pressure-testing
> claims against reality — performed by the operator's own AI tooling on the
> operator's own infrastructure. It is **not** a security assessment, not a
> penetration test, and not an external audit of any kind; no exploitation,
> scanning, or defense-testing was performed. **Read-only checks only:**
> REST/health endpoints on loopback/tailnet, systemd/podman state, SQLite
> read-only queries (`?mode=ro`), git history, source inspection, the full
> pytest suite (isolated test DBs), and live vendor-pricing page fetches.
> Nothing on the live system was modified.
>
> **Output:** this local draft set at
> `docs/investor-materials/v1.5/` (this repo), mirroring the canonical
> Nextcloud convention (`corporatetraveldc/Docs/investor-materials/vX.X/`).
> The canonical Nextcloud folders were **not** touched. Promotion of this
> draft into the canonical set is the operator's call.
>
> **What changed in scope since 08-09, per direct operator instruction
> (2026-08-24):** the standing HOLD on promoting hardware-cost-tier content
> (`docs/COST_STRUCTURE.md`) and the cost-avoidance / subscription-replacement
> valuation (`docs/COGS_VENDOR_COMPARISON_2026-08-18.md`) into
> investor-facing material is **lifted**. A live-recomputed version of both
> is in the companion file:
> **`COGS_SUBSCRIPTION_REPLACEMENT_2026-08-24.md`** (same directory). The two
> source docs themselves remain untouched, uncommitted, and internal.

## Re-verification snapshot (2026-08-24, ~08:20–08:30 ET)

Columns: the claim as it stood after the 2026-08-09 pass → what the live
system shows today → verdict. Where a number is expected to drift
continuously (row counts, container counts, restart counters), the number is
a timestamped sample, not a fact — this repo's own CLAUDE.md is explicit
about which counts must be re-derived rather than trusted.

| Claim (post-08-09) | Observed 2026-08-24 | Verdict |
|---|---|---|
| Service healthy, `/healthz` OK; CPS GREEN/GO; snapshot age 8 s | `{"status":"ok"}`, snapshot age **3 s**, CPS **GREEN/GO**, `audit_count_24h` **3,384**, `token_count_active` 6 | ✅ holds |
| CPS 6-factor deterministic engine (288 lines) | GREEN/GO live; engine (`src/poller/skills/cps_recompute.py`) still **288 lines**; `cps_scores` 1,141 rows | ✅ holds |
| 19-feed freshness registry | **19 feeds** in `/api/v1/feeds`, all with age/threshold/error state (composition shifted: `push:amtrak` and `push:nws` now first-class registry rows) | ✅ holds |
| All 7 SWIM push feeds fresh (client-ack fix cured crash-loops) | All 7 ingest containers `active (running)`, thermal guard `tier: 0`, `/healthz` `ok` at check time. **Material new caveat:** since the 2026-08-23 thermal-guard redesign the entire stack (all 6 SWIM feeds + core + poller/pusher/runner + Ollama) is deliberately shed under contention — **10 LOCKDOWN trips in the ~32 h before this check**, each ~9–11 min, all triggered by the Ollama-contention (brief-fallback) signal, none by temperature or load. Restore path verified working end-to-end each time | 🔄 holds at check time; **availability posture changed** — feeds are duty-cycled by design, disclose rather than claim continuous |
| Point-in-time snapshot (151 TFRs / 71 trains / 7 METAR stations) | New snapshot: **121 TFRs**; `flight_events` 839,101 rows (**32,459 in 24 h**, 1,270 distinct airlines, 30-day rolling retention); `train_events` 822,317; `notams` 5,424 / 308 facilities; `tbfm_sequences` 35,615; `surface_movement_events` 120,071; `nas_programs` 24,588 | ✅ historical figures kept, new snapshot added |
| 28 containers, 62 quadlet units | **38 containers running** at check; **64 live Quadlet `.container` files** (one, `ccw-demo`, deliberately untracked and unrelated to the platform). Count swings ~30–40 by design (LOCKDOWN sheds remove containers; timer-oneshots add them) — no stable "fully up" ceiling exists | 🔄 updated, with the drift mechanics stated |
| 583 commits, single author, Jun 7 – Aug 9 | **635 commits**, single author, **Jun 7 – Aug 24** (last commit + manifest re-sign this morning 08:19 ET) | 🔄 updated |
| ~42k Python LOC; 25 JSX components; ~70+ REST routes | **51,070 Python LOC**; **27** JSX components; **102** REST route registrations | 🔄 updated |
| 16 test files, 2,288 lines; no CPS unit test; no CI | **23 test files, 3,956 lines; 218 tests — 217 pass, 1 pre-existing known failure** (`test_smes_parser_basic`, unrelated marine-detection assertion, tracked in CLAUDE.md). Suite re-run this pass (11.4 s). Still **no CPS unit test; still no CI** | 🔄 counts up ~73 % since 08-09; gaps unchanged & restated |
| No LICENSE; SECURITY.md is a 21-line template | Still no LICENSE. **SECURITY.md is now a real 72-line document** (names both commit-signing keys and the agent manifest-signing key) | 🔄 improved (SECURITY.md), LICENSE gap restated |
| 4 active API tokens | **19 issued / 6 active** (SHA-256-hashed, bearer-only). Internal hygiene flags (not investor-facing claims): every token has `expires_at IS NULL`, and the admin token of the retired MCP bridge is still unrevoked — carried as an operator to-do in CLAUDE.md | 🔄 updated; hygiene items internal |
| Tailscale header-trust spoofing remediated (2026-08-05) | Holds — auth is bearer-token-only, network origin grants no tier (`src/auth/auth.py`). **New since 08-09:** every admin action is now audit-logged via a `require_admin(action)` factory across **32 endpoints** with actor/IP/payload capture and a 90-day prune — `audit_log` **4,397 rows, 3,384 in last 24 h** (on 08-19 the audit log had 12 rows; this is now a real, demonstrable control) | ✅ holds, materially **improved** |
| LLM briefs live on hourly timers; ~87 % full-narrative rate (48 h sample) | Briefs still live: **272 briefs in the last 7 days** (`brief_archive`). Honest fallback disclosure updated: across **all** skill LLM calls in the last 7 days, **2,217 of 5,316 (41.7 %) ran the deterministic template fallback** rather than local inference — driven by Ollama contention and the LOCKDOWN duty-cycling above (not directly comparable to 08-09's per-brief 87 %, which measured briefs only, but the direction is real). Fallback is honest-by-design (labeled, monitored, alerts) | 🔄 restated with current candor; fallback share **worsened** — do not oversell the inference layer |
| Self-healing watchdog fleet observed running | Fleet intact and **substantially hardened since 08-09**: a root-scope 90 s platform watchdog now exists (`corporatetraveldc-watchdog`), with three rounds of real fixes from live incidents — restart-only-what-failed, a 5-cycle (~7.5 min) debounce, and thermal-guard-state suppression so it no longer fights deliberate load-sheds. All three fixes verified against live journals | ✅ holds, improved (and honestly scarred — incident history documented in CLAUDE.md) |
| ntfy 15 mapped topic channels | Core 14-topic catalog plus escalating per-family/zone topics with per-topic throttles (`docs/ALERT_REFERENCE.md`); self-hosted ntfy server | ✅ holds (taxonomy refined) |
| Dispatch MCP 33+ tools; safety-rails ~55 registrations | **RETIRED.** Both MCP bridges (`mcpo`/`mcpo-public`) were decommissioned 2026-08-18; ports refuse connections; the server checkout is archived. **All MCP claims must be removed from investor materials** | 🔄 capability deliberately removed — drop from decks |
| Second brain: 3,750 indexed docs; nightly scan + weekly synthesis | **6,742 vault documents / 6,500 FTS notes.** New since 08-09: a compiled **semantic layer** — 99 concepts, **51,317 note↔concept edges**, and a causal **derivation graph** (26,448 edges incl. evidenced `leans_on`/`derives_from`/`reutilizes` links and chronology edges) with multi-hop `--trace`/`--depends-on` queries; recompiled **daily by timer** (this morning's 03:47 run: success, 8.6 s) | 🔄 updated, major new capability |
| Demo = labeled 14-day replay, explicit not-live-data disclosure | Recorder `demo.db` now **2.45 GB** of snapshot history and growing. **Caveat that must gate any "live public demo" claim:** the public demo runner instance (`runner-demo`, port 8005) has been **crash-looping since 2026-08-15** (NRestarts ≈ 57,000) and its `DEMO_MODE` password gate is not set — the public hostname 502s. An operator decision on fixing + gating it is pending. Do **not** claim a live public demo in v1.5 | 🔄 downgraded pending operator decision |
| "~$100 edge hardware" softened to "few-hundred-dollar / sub-$1k" pending founder decision | **Founder decision now given (2026-08-24): promote real cost tiers.** Deployed single-node BOM ≈ **$765** one-time; tier table ($700 floor / sub-$900 reference / $1,100–1,200 full-RF) now investor-facing. Full derivation in the companion COGS file | 🔄 **resolved** — see companion file |
| `ANTHROPIC_FALLBACK_ENABLED` gate, off by default | Confirmed live: `dispatch.env` sets `ANTHROPIC_FALLBACK_ENABLED=false`; SR-1 usage log (25,147 rows, 2026-07-09 → today, 46.4 days) contains **zero** cloud-model rows (`grep -icE 'claude|anthropic|gpt|openai'` → 0). $0 cloud-LLM spend is **measured, not assumed** | ✅ holds, strengthened |
| NOTAM REST pull awaiting FAA key; NOTAMs flow via SWIM `push:fns` | Unchanged — `notam` REST fetcher still `awaiting_credentials`; `push:fns` live (5,424 NOTAMs / 308 facilities) | ✅ unchanged, restated |
| No billing / multi-tenancy / DR; pre-revenue | Unchanged. The honest consumption datum also stands: the operational runsheet's newest entry is still `run_date 2026-07-28, trip_count 1` — output is capacity, not yet realized demand | ✅ restated |
| Roadmap dates (EV tolling spring 2026; eVTOL spring 2027; AIS end 2026) | Operator-authoritative; unchanged. AIS remains **not live** (0 vessel rows, empty watchlist, no unit) — roadmap item only | ✅ untouched |

## New capability folded into the v1.5 draft (all observed live 2026-08-24)

1. **Real airline-reported on-time history** (SCHEMA_V34, 2026-08-20):
   TFMS OOOI times durably captured per watchlisted flight;
   `ontime_history_14d` + delay-drift flags surfaced on the watchlist API;
   forced identity-resolution at pushback (hex-lock + one-time resolved-identity
   push with live tracking link); delay-extended auto-expiry (SCHEMA_V37).
   Watchlist-gated by design; history accumulates from 08-20 (2 rows so far —
   young, disclosed as such).
2. **Second-brain semantic + causal layer** — concept graph (99 concepts,
   51,317 edges), deterministic derivation/provenance edges, chronology
   baked into the same causal chain, multi-hop trace queries, daily
   recompile timer (verified firing clean).
3. **Admin audit trail, real** — 32 audited admin endpoints, actor/IP/payload
   rows, 90-day prune; 3,384 audit rows in the last 24 h (was 12 rows total
   two weeks ago).
4. **Ingest hardening from live root-causes** — NWWS-OI now writing real
   DC-area alerts (two independent parser bugs fixed; `nws_alerts` 1 → 20
   rows); TFMS AFP (airspace flow program) messages now parsed;
   GDP/GS program keying fixed with an additive `key_scheme` migration
   (SCHEMA_V36) preserving legacy queryability; runsheet duplicate-insert
   bug fixed (unbounded growth stopped, verified).
5. **Thermal/contention governance redesign** — DDoS-style LOCKDOWN shed with
   verified full-stack restore, Ollama-contention trigger, watchdog/guard
   conflict resolved with debounce + guard-state suppression; host Ollama
   under systemd resource governance (drop-in confirmed active in
   `DropInPaths` today).
6. **ADS-B/ACARS RF layer current** — 17 aircraft in view, ~108 msg/s
   (6,492 valid messages in the last minute at check), ACARS/VDL receiving
   to the minute (48,716-row rolling store, newest 12:24 UTC today).
7. **Integrity chain hardening** — atomic manifest+signature writes, 33
   verified-exec-gated skill quadlets, 15-min integrity sweep, manifest at
   706 files signed this morning; drift checkers (daily CLAUDE.md check +
   weekly doc-drift, both proven end-to-end) — with their known blind spots
   documented rather than hidden.
8. **Hardware cost tiers + live-recomputed COGS and subscription-replacement
   valuation** — newly promoted per founder decision; see companion file.

## Deliberately NOT folded in (pending operator decision or not claimable)

- **Any "live public demo" claim** — `runner-demo` crash loop + unset
  `DEMO_MODE` (public hostname 502s). Needs the documented operator decision
  (fix + gate, or retire the vhost) before the demo is claimable again.
  The *recorded replay* capability and 2.45 GB corpus are claimable; the
  public URL is not.
- **MCP integration** — retired 2026-08-18; strip from all materials.
- **AIS / maritime** — fully dormant (0 rows, empty watchlist, no unit);
  roadmap only. Priced separately and excluded from live totals in the
  COGS file.
- **ACARS-into-platform-DB** — RF reception is real and current, but
  `acars_messages` in the platform DB is still 0 rows (instrumentation
  added this week shows the upstream router genuinely silent — root cause
  is off-box). Claim "receive-side ACARS/VDL RF capability," not "ACARS
  fused into the platform DB."
- **Continuous-availability language for SWIM feeds** — 10 LOCKDOWNs in
  ~32 h at ~9–11 min each is the current real cadence; the
  fallback-trigger calibration (`FALLBACK_TRIGGER_COUNT`/`WINDOW`) and
  skill-timer bunching are open operator decisions. Until tuned, describe
  feeds as "governed/duty-cycled under contention," not "always-on."
- **Firehose/Cirium/Spire-anchored valuations beyond the labeled
  secondary anchors** — still quote-only; getting a real quote remains a
  founder action (unchanged since 08-19).
- Carried from v1.1/08-09: client-held-subscription contractual language,
  pricing validation, IP assignment, insurance/GDPR posture, DR plan,
  succession.

## Method & QA

Live checks: `/healthz` + `/api/v1/feeds`; `systemctl --user` unit sweep +
`podman ps`; thermal-guard state + journal (LOCKDOWN cadence measured from
the guard's own journal); read-only SQLite over the production DB and the
second-brain index; SR-1 usage-log analysis (model-mix by 7-day window);
`git log`/`shortlog`; `gpg --verify` on the manifest; full pytest run;
LOC/route/component counts from the tree; vendor pricing pages re-fetched
directly today where reachable (detailed in the companion COGS file —
notably, several pages that 403'd on 08-19 were directly readable today,
so more of the pricing basis is primary-sourced in this pass than in the
source doc). No production file, container, unit, or config was modified.
This draft is deliberately left **unstaged/uncommitted**; the operator
commits (signed) personally or not at all.

## Open items needing founder input (carried + new)

1. Promote this v1.5 draft into the canonical Nextcloud
   `investor-materials/` set (operator's call, as with v1.2-draft).
2. Decide the `runner-demo` question (public + gated, or retired) before any
   demo link goes in front of an investor.
3. LOCKDOWN trigger calibration / daily-watch timer spreading — the current
   cadence is the single biggest availability caveat in the materials.
4. Request an actual FlightAware Firehose or Cirium quote — still the
   largest unpriced number in the valuation basis.
5. Carried: CI, CPS unit test, LICENSE file, DR/succession/insurance
   posture.
