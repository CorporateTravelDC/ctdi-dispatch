# CTDI Dispatch Platform — Live COGS & Subscription-Replacement Analysis (2026-09-03)

> **Status.** Investor-facing draft — the dated successor to
> `COGS_SUBSCRIPTION_REPLACEMENT_2026-08-24.md` (same directory), which is
> **untouched** and stands as the historical 08-24 baseline. This file is a
> **live recomputation as of 2026-09-03 (~19:55–20:30 EDT)**, not a copy:
> every input was either re-derived against the running system today,
> re-fetched from the vendor's own page today, or explicitly labeled
> "carried" with its vintage. The same discipline as the source docs
> (`docs/COST_STRUCTURE.md`, `docs/COGS_VENDOR_COMPARISON_2026-08-18.md`
> incl. its 08-19 re-verification and Sections A–E) applies throughout:
> every commercial price cites a real vendor source with an access
> date/time; every business-judgement figure is flagged **ASSUMPTION**;
> quote-only vendors are excluded from totals rather than guessed.
>
> **What is NEW in this pass vs. 08-24:** §4 — a live, cited
> **SR1/SR2 frontier-provider API cost counterfactual** (what the measured
> local-LLM workload would cost on Anthropic / OpenAI / Google Gemini
> current published pricing, computed both without and with each provider's
> prompt-caching terms). No prior dated file contains this analysis; an
> informal conversational version existed earlier on 2026-09-03 but was
> never derived or cited — §4 is from scratch.
>
> **What is carried vs. re-derived:** headline deltas vs. 08-24 are called
> out per-row in §1 and per-input in §5 (provenance). Where a figure did
> not change, that is stated plainly rather than padded.
>
> **Read-only posture:** nothing on the live system was modified to produce
> these numbers (REST/health on loopback, `systemctl --user` state,
> read-only SQLite, the SR-1 usage CSV, tracked Modelfiles, and direct
> vendor-page fetches). Box clock verified before any time claim:
> `timedatectl` — 2026-09-03, America/New_York (EDT), NTP-synchronized.

---

## 1. Headline numbers (each derived below)

| Quantity | Figure (2026-09-03) | vs. 2026-08-24 baseline | Basis |
|---|---|---|---|
| **One-time hardware, actually deployed** | **≈ $765** | unchanged | §2, operator-itemized BOM, single node re-confirmed today |
| **Actual recurring cost** | **≈ $22 – $38 / yr** (electricity only; midpoint ~$30) | ↓ ~$1 (was $23–$39): EIA tariff moved 25.40¢ → **24.39¢/kWh** (June-2026 data, EPM released 2026-08-26) | §3.1 |
| Recurring data-feed fees | **$0** | unchanged, re-confirmed live | §3.2 |
| Recurring cloud-LLM spend | **$0** — measured, not assumed | unchanged, re-confirmed: usage log now **35,217 rows / 56.9 days continuous**, still **zero** cloud rows | §3.3 |
| **NEW — hypothetical frontier-API cost of the measured workload** (counterfactual, not an incurred cost) | **≈ $96 – $945 / yr** across the three providers' small tiers; fair-peer anchor (Claude Haiku 4.5) **$444 – $945 / yr**; prompt-caching delta at those tiers **≈ $0** (mechanistic — §4.6) | **new section — no 08-24 equivalent** | §4 |
| **Defensible avoided cost (this instance, conservative)** | **≈ $2,160 – $2,656 / yr net** | effectively unchanged (±$1, from the electricity netting) | §6 |
| Cost of a strict no-data-sharing policy | **+$399 – $2,118 / yr** | unchanged (largest component re-confirmed today) | §6 |
| **Subscription-replacement floor (purchasable subset of live capability)** | **≈ $55,200 – $113,000 / yr** | high end +~$83 (was ≈$112,900): LLM-replacement row re-derived at today's higher measured call rate | §7 |
| — of which commodity infrastructure | $32,559 – $41,467 / yr | unchanged | §7.1 |
| — of which intelligence-automation layer | $22,624 – $71,512 / yr | +$1 low / +$83 high (re-derived LLM row) | §7.2 |
| + documented-but-**unbuilt** maritime tier (excluded from live totals) | up to ≈ +$55,000 / yr | unchanged; AIS still not live (0 rows, no unit — re-confirmed today) | §7.3 |
| Capabilities not purchasable at any price | **10** (6 data/ops + 4 intelligence-layer) | unchanged | §8 |

**The one-line claim that survives hostile review** (all inputs re-verified
today):

> *This platform runs on ≈ $765 of owned hardware and ≈ $22–$38/yr of
> electricity, with $0 in data-feed fees and $0 in cloud-LLM spend — and its
> measured 57-day inference workload, priced at today's published frontier
> API rates, would cost only ≈ $0.1k–$1k/yr to buy from the cloud, while the
> purchasable subset of the platform's full live capability lists at roughly
> $55k–$113k/yr in commercial subscriptions, and its most operationally
> valuable elements (NAS flow/metering/terminal-alert data, unfiltered
> blocked-aircraft visibility, receive-side ACARS, a permanently-owned
> longitudinal corpus, and scheduled-brief automation over it) cannot be
> bought at any price.*

What that claim is **not**: a valuation, a revenue claim, or "we avoid
$100k/yr of subscriptions." §4's frontier figure in particular cuts *both*
ways and is presented as such (§4.7): the LLM-inference line is cheap to
buy; the data and intelligence layers are not. The governing structural
finding (§9) is stated plainly, as in the source docs.

---

## 2. One-time hardware — cost tiers (carried; deployment re-confirmed)

All hardware figures are operator planning estimates (basis 2026-08-05,
`~` = approximate), **unchanged and not re-priced this pass** — the deployed
hardware itself has not changed, re-confirmed today: single node
(`corporatetraveldc-dispatch`, Pi 5, 4 cores / 16 GB, NVMe boot), Ollama
served from the same box (no offload node built). One hardware event since
08-24, disclosed for completeness: the ADS-B RTL-SDR dongle dropped off the
USB bus 2026-08-29/30 and was restored by physical reseat + reboot on
2026-08-30 (see `docs/LIVE_STATE_CHECK_2026-09-03.md`) — a reliability
datum, not a BOM change.

| Tier | RF ingest | Approx. total (one-time) |
|---|---|---|
| Floor / core-only (no SDR) | none | ≈ $700 + NVMe |
| **Reference — actual current buildout** | ADS-B + VDL2 SDRs, modest indoor antennas | **sub-$900** |
| Full-quality RF build | full SDR + tuned antennas + roof runs | ≈ $1,100 – $1,200 |

**Deployed-node BOM as actually running** (operator's itemized line items;
the figure used throughout): Pi 5 16 GB ~$350 + case share ~$45 + PSU share
~$30 + NVMe ~$180 + RF adder (2 × RTL-SDR + modest antennas) ~$160 ⇒
**≈ $765 one-time**. **No change since 08-24.**

---

## 3. Actual recurring cost — re-derived 2026-09-03

### 3.1 Electricity ≈ $22 – $38 / yr (midpoint ~$30) — **tariff delta vs. 08-24**

- **Wattage bounds unchanged** (hardware unchanged): 10.5 W low bound
  (Pi 5 stressed 6.8 W + 2 SDRs 2.7 W + NVMe ~1 W) to 17.6 W
  ceiling-argument high bound (8.8 W + 2.8 W + 5 W PCIe ceiling + ~1 W
  cooler). Sources carried from the 08-19 derivation (Tom's Hardware Pi 5
  review 2023-10-23; Jeff Geerling; RTL-SDR Blog V3 datasheet).
- **Sustained-load basis re-confirmed today** — the box is not idling
  (13 GB of 16 GB RAM in use at check; multiple deploy/rebuild cycles ran
  today). Stressed-wattage figures remain the correct basis.
- **Tariff re-fetched today (delta):** EIA Electric Power Monthly, Table
  5.6.A — District of Columbia residential **24.39 ¢/kWh** (June 2026;
  June 2025 was 22.70 ¢). The 08-24 pass used 25.40 ¢ (May-2026 data);
  EIA released the June-2026 table 2026-08-26.
  <https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a>
  (accessed 2026-09-03 ~20:05 EDT, value read from the table).
- Arithmetic: 10.5 W × 8,760 h = 92.0 kWh → **$22.44/yr**;
  17.6 W × 8,760 h = 154.2 kWh → **$37.61/yr**.
- Still open (carried caveat): NVMe and cooler draws are ceiling arguments,
  not measurements; a ~$15 inline USB-C power meter would retire this.

### 3.2 Data-feed fees: $0 — re-confirmed

19 feeds in the live registry (`/api/v1/feeds`, checked today); `/healthz`
`{"status":"ok"}`, CPS GREEN/GO, snapshot age 4 s at check. Every live feed
is free government/public-domain data (FAA SWIM ×6 — all six ingest units
`active (running)` at check — TFR, NAS status, ATCSCC, NOAA
NWWS-OI/METAR/NWS, FAA registry), a free community API (Amtrak), scraped
public JSON (DCA/IAD FIDS — fragile, no SLA, disclosed), self-polled RSS
(OSINT), or the platform's own RF receivers (ADS-B, ACARS/VDL). No invoice
exists anywhere in the stack. The ADS-B aggregator accounts remain
**barter, not free** (5 feeder containers + OpenSky confirmed running
today) — see §6. Availability caveat carried and updated: the SWIM tier
suffered **two total feed outages today** (~13 min and ~41 min) from
abandoned deploy cycles, restored by the drift-check passes
(`docs/LIVE_STATE_CHECK_2026-09-03.md` Passes 4–5) — the "no SLA" honesty
point stands.

### 3.3 Cloud-LLM spend: $0 — measured, re-confirmed today

- `/etc/corporatetraveldc/dispatch.env` sets
  `ANTHROPIC_FALLBACK_ENABLED=false` (direct read today), and **no
  `ANTHROPIC_API_KEY` value is present** in either env file (presence
  checked, values not read) — the cloud gate is closed twice over.
- SR-1 usage log (`/var/lib/corporatetraveldc/api-usage.csv`): **35,217
  logged skill LLM invocations**; continuous production series
  **2026-07-09 → 2026-09-03 (56.9 days)** plus one isolated 12-row burst on
  2026-06-27 (§4.2). `grep -icE 'claude|anthropic|gpt|openai|gemini|google'`
  over the whole log → **0**. Every row's model is a local
  `corporatetraveldc-pi5-*` Ollama tag, the literal `deterministic`
  (template fallback), or `none`/legacy local tags. 21 dedicated Modelfiles
  tracked in-repo (the Ollama HTTP API was unreachable at check time —
  consistent with the deliberate on-demand/idle-shedding design, so the
  21-model count is confirmed from the signed tree, not `ollama list`,
  this pass).
- Honest discount that travels with this: **34.2 % of the last 7 days'
  skill calls (2,335 / 6,819) were deterministic-template fallbacks**, not
  inference (was 41.7 % on 08-24 — improved, still real). The $0 figure is
  unaffected (the fallback is a local template render, not a billed call),
  but the inference layer should not be sold at 100 % duty.

### 3.4 Quietly-omitted small costs (carried, still true)

Domain registration (shared business cost, registrar/renewal not recorded
in-repo — UNVERIFIED); internet (pre-existing business expense, not
marginal to the platform); FAA SCDS approval ($0 but real
lead-time/agreement friction).

---

## 4. NEW — SR1/SR2 frontier-provider API cost counterfactual

> **Framing, stated first because it governs everything below.** This
> platform's actual cloud-LLM spend is **$0, measured** (§3.3), and that
> was re-verified today rather than assumed. This section prices a
> **HYPOTHETICAL**: *what the measured SR1-logged inference workload would
> cost if it ran against frontier cloud APIs instead of the self-hosted
> Ollama stack*, at each provider's currently published rates, with and
> without their prompt-caching discounts. It is a counterfactual for
> cost-comparison and roadmap purposes (the SR2 hybrid-offload seam in
> `src/common/guardrails.py` exists precisely so a future frontier call
> path is a caller, not a redesign). Nothing here is an incurred cost.

### 4.1 Where SR1/SR2 call volume is actually accounted

Two distinct mechanisms share the SR1/SR2 name; both were inspected:

1. **`src/common/guardrails.py`** — SR1 = mutation gate, SR2 = model-tier
   routing (`tier_1`/`tier_2` = local Ollama; `tier_3`/`tier_4` = the
   frontier hybrid-offload seam, deliberately **not wired to any call
   site**). Both log to the platform `audit_log`. Live count today:
   **5 rows ever** (1 `SR1_INTERCEPT`, 1 `SR1_ALLOWED`, 2 `SR2_ROUTE`,
   1 `SR2_BLOCK`) — the 2026-08-16 port-validation invocations. **SR2 has
   never routed a production call to a frontier tier.** So the guardrail
   log proves the seam exists; it is not the volume ledger.
2. **`src/common/sr1_log.py`** — the SR-1 usage logger: every automated
   skill appends one row per LLM invocation to
   `/var/lib/corporatetraveldc/api-usage.csv` in a `finally` block. **This
   is the volume ledger**, and it is what this section prices. Known
   limitation, carried from the source docs and re-confirmed today: all
   four token columns sum to **0 across all 35,217 rows** — the log proves
   which model served each call, never token counts. Tokens are therefore
   **modelled, not measured** (§4.3), exactly as in the source doc's
   §2.19, and flagged as such.

### 4.2 Measured call volume, June 2026 → 2026-09-03 (live derivation)

From the SR-1 log, derived today (rows exclude the CSV header):

| Month | All logged rows | Real model calls (excl. `deterministic`/`none`) |
|---|---:|---:|
| 2026-06 | 12 | 6 |
| 2026-07 | 7,743 | 7,111 |
| 2026-08 | 24,365 | 17,225 |
| 2026-09 (through 09-03 23:54 UTC) | 3,097 | 1,827 |
| **Total** | **35,217** | **26,169** |

**Substitution flagged — June 2026 data effectively does not exist.** The
log holds exactly **12 rows for June**, all within 2026-06-27
00:00–00:03 UTC (an isolated one-off burst), then nothing until the
continuous series begins **2026-07-09 03:07 UTC**. There is no other
queryable per-call LLM usage record for June on this system (the guardrail
`audit_log` holds 5 SR rows ever; §4.1). The annualization below therefore
uses the **continuous 56.87-day series (2026-07-09 → 2026-09-03)** rather
than a fabricated June-inclusive rate, and the 6 June real-model rows are
excluded from the rate:

- **Real model calls in the continuous span: 26,163** ⇒ **460.1/day** ⇒
  **≈ 167,900/yr**. (08-24 measured 423.2/day ≈ 154,500/yr over 46.4 days —
  the rate has risen ~9 % with the platform's growth; this is the largest
  driver of the LLM-row delta in §7.2.)
- Call mix (real model calls, whole log): tfr-enrichment 9,701 ·
  route-impact 7,919 · flight-impact 3,419 · ops-brief 1,604 · ep-advance
  1,267 · osint-monitor 1,193 · 16 lower-volume skills 1,066.
- Status mix among real calls: 25,383 ok · 670 deferred · 69 error ·
  26 fallback · 21 partial. Gate results: 26,142 `new`, 25 `n/a`,
  2 `forced` (and 1 `skipped` log-wide) — the SR-2 content-hash gate rarely
  suppresses, so pricing all real calls is the conservative direction.

### 4.3 Token model (re-derived live; ASSUMPTION, arithmetic shown)

SR-1 logs no token counts (§4.1), so per-call tokens are modelled from the
**tracked Modelfiles** (repo root `corporatetraveldc.<skill>`, all under
the signed manifest), weighted by the call mix above, using the platform's
own 4-chars/token estimator (`src/common/llm.py`,
`_CHARS_PER_TOKEN_ESTIMATE = 4.0`). Same construction as the source doc's
§2.19, re-derived from scratch against today's Modelfiles and mix:

| Quantity | Value (2026-09-03) | 08-19 model (for comparison) |
|---|---|---|
| Weighted `num_ctx` | 4,196 | 4,208 |
| Weighted `num_predict` (output ceiling) | 357 | 356 |
| Weighted static SYSTEM prefix | **850 tokens** | 1,103 |
| Input/call — low (SYSTEM + ~900-token payload) | 1,750 | 2,003 |
| Input/call — high (fills context: `num_ctx − num_predict`) | 3,840 | 3,852 |
| Output/call — low (50 % of ceiling) / high (at ceiling) | 178 / 357 | 178 / 356 |
| **Annual volume @ 167,900 calls/yr** | **294 – 645 MTok in · 30.0 – 60.0 MTok out** | 307–590 in · 27.2–54.5 out |

Flagged assumptions: **(a)** the 4-chars/token estimator (the platform's
own, conservative for prose); **(b)** the ~900-token low-bound payload and
context-filling high bound (unchanged from the source doc — actual spend
lands between); **(c)** two skills with no current dedicated Modelfile
(flight-impact, freshness-audit — both resolve to the legacy OSINT model
tag at runtime) are assigned osint-monitor-class parameters
(4,096/512/3,273 chars) — **ASSUMPTION**, ~13 % of calls. Note the
weighted SYSTEM prefix is **850 tokens today vs. 1,103 in the 08-19
model** — today's figure is measured from the current signed Modelfiles
(per-skill SYSTEM blocks run ~680–1,412 tokens; the two dominant skills
sit at ~820–830). This difference is load-bearing for caching (§4.6).

### 4.4 Current published frontier pricing (all accessed 2026-09-03, ~20:00–20:15 EDT)

Category key as in the source docs: **(a)** vendor's own page, fetched
directly · **(b)** credible secondary after the vendor page refused
automated fetch.

| Provider / model | Input $/MTok | Output $/MTok | Cache terms (mechanism differs per provider — quoted, not assumed) | Source · category |
|---|---:|---:|---|---|
| **Anthropic Claude Haiku 4.5** | $1.00 | $5.00 | Cache read **$0.10** (0.1×); cache write **$1.25** (1.25×, 5-min TTL; 1-h TTL is 2×). **Minimum cacheable prefix on Haiku 4.5: 4,096 tokens** — shorter prefixes silently don't cache. Batch −50 %. | <https://claude.com/pricing#api> (a), direct fetch; cache mechanics & minimums per Anthropic docs |
| Anthropic Claude Sonnet 5 | $2.00 | $10.00 | read $0.20 / write $2.50; minimum cacheable prefix **1,024 tokens** | same (a). Note: 08-24 recorded $2/$10 as introductory through 2026-08-31 (then $3/$15); **the vendor page still lists $2/$10 today** — priced as published |
| Anthropic Claude Opus 5 | $5.00 | $25.00 | read $0.50 / write $6.25; minimum cacheable prefix **512 tokens** | same (a) |
| **OpenAI GPT-5.4 mini** | $0.75 | $4.50 | Cached input **$0.075** (10 % of input rate), automatic prefix caching — **engages only on identical prompt prefixes ≥ 1,024 tokens**; secondary source reports a 1.25× write premium on 5.4–5.6 tiers (unconfirmed on vendor page). Batch −50 %. | ⚠️ **(b)** — `openai.com/api/pricing` returned **HTTP 403** to every direct attempt (same failure class as prior passes); figures from benchlm.ai pricing table ("Last updated Sep 3 2026") cross-checked against a second aggregator via search index |
| OpenAI GPT-5.4 nano | $0.20 | $1.25 | cached input $0.02; same mechanism | same (b) |
| OpenAI GPT-5.4 | $2.50 | $15.00 | cached input $0.25 | same (b) |
| **Google Gemini 3.8 Flash** | $0.75 (promo through 2026-12-31; **$1.50 after**) | $3.75 (promo; **$7.50 after**) | Context caching $0.075/MTok cached + $0.50/MTok/h storage (explicit); **implicit caching automatic on 2.5+ models at no storage cost, but minimum 4,096 tokens for 3.x Flash** (2,048 on 2.5) | <https://ai.google.dev/gemini-api/docs/pricing> and `/docs/caching` (a), direct fetch |
| Google Gemini 3.5 Flash-Lite | $0.30 | $2.50 | caching $0.03/MTok + $1.00/MTok/h storage; same implicit-cache minimums | same (a) |

Fair-peer election (**ASSUMPTION**, carried from the source doc's
reasoning): the local models are 3.8B-parameter `phi3:mini` builds, so the
honest peer is each provider's **small tier** — Claude Haiku 4.5,
GPT-5.4 mini, Gemini 3.8 Flash — not a frontier flagship. Flagship rows are
shown for the ceiling, not summed into any claim.

### 4.5 Scenario (a) — annual cost WITHOUT caching

167,900 calls/yr × the §4.3 token model (low – high bounds):

| Provider / model | Annual (no caching) |
|---|---:|
| **Claude Haiku 4.5** (fair peer, Anthropic) | **$444 – $945** |
| Claude Sonnet 5 | $888 – $1,889 |
| Claude Opus 5 | $2,219 – $4,723 |
| **GPT-5.4 mini** (fair peer, OpenAI) ⚠️(b) | **$355 – $753** |
| GPT-5.4 nano ⚠️(b) | $96 – $204 |
| GPT-5.4 ⚠️(b) | $1,184 – $2,512 |
| **Gemini 3.8 Flash** (fair peer, Google; promo pricing) | **$333 – $708** |
| Gemini 3.8 Flash at post-2026 standard rates | $666 – $1,417 |
| Gemini 3.5 Flash-Lite | $163 – $343 |

**Small-tier band across all three providers: ≈ $96 – $945 / yr.**
(For reference, batch/async pricing — all three providers publish −50 % —
would halve these for the latency-tolerant subset of the workload: Haiku
$222–$472; GPT-5.4 mini $178–$377. The scheduled digests/briefs are
batch-shaped; the push-alert paths are not. Not applied to the headline.)

### 4.6 Scenario (b) — annual cost WITH each provider's published caching, and the delta

The deltas were computed per provider against the workload's **actual
prompt shape** — per-skill static SYSTEM prefixes of ~680–1,412 tokens
(weighted 850), with everything after them volatile per call. The result
is the finding:

| Provider / model | With caching | Delta vs. (a) | Why |
|---|---:|---:|---|
| Claude Haiku 4.5 | **$444 – $945** | **≈ $0** | Haiku 4.5's minimum cacheable prefix is **4,096 tokens**; every per-skill prefix is below it — the cache **silently never engages** |
| Claude Sonnet 5 | $888 – $1,889 | ≈ −$0 to −$25 | 1,024-token minimum; only ep-advance (1,412-token prefix, 4.8 % of calls) qualifies, and its ~65-min call cadence sits at the edge of even the 1-h TTL |
| **Claude Opus 5** | **≈ $1,720 – $4,470** | **≈ −$250 to −$500** (−11 %/−5 %) | 512-token minimum — caching engages on the 4 high-cadence skills (86.5 % of calls, ~823-token prefixes, cadence 8–51 min): ~119.6 MTok/yr of prefix reads at $0.50 instead of $5.00 (−$538), less 1-h-TTL write overhead (~$36–$288 depending on refresh behavior) |
| GPT-5.4 mini ⚠️(b) | $355 – $753 | ≈ −$8 | OpenAI prefix caching needs an identical prefix ≥ **1,024 tokens**; only ep-advance qualifies (~11.5 MTok/yr at the 90 % cached-input discount ≈ $7.8/yr) |
| Gemini 3.8 Flash | $333 – $708 | **≈ $0** | Implicit caching minimum on 3.x Flash is **4,096 tokens** — never engages at this prompt shape. Explicit caching not priced: per-skill prefixes sit below the documented implicit minimums, explicit-cache minimum sizes were not confirmed on the vendor page this pass, and its $/MTok/h storage meter would run 8,760 h/yr against near-zero read savings — **excluded rather than guessed** |

> **The point of computing both ways:** the caching delta everyone assumes
> is material turns out to be **≈ $0 at every provider's fair-peer tier**,
> for a mechanistic and citable reason — this workload's cacheable static
> prefixes (~700–1,400 tokens per skill) sit **below every small-tier
> caching-eligibility threshold** (Haiku 4.5: 4,096; OpenAI: 1,024
> identical-prefix; Gemini 3.x Flash: 4,096 implicit). Caching only
> becomes real money on Claude Opus 5 (512-token minimum), where it trims
> ~5–11 % from a bill that is itself 5× the fair-peer cost. This
> **supersedes the source doc's 08-19 remark** that the (then ~1,103-token)
> system block "sits just above the ~1,024-token minimum" — measured
> against today's signed Modelfiles the weighted prefix is 850 tokens, and
> the per-provider minimums differ by tier. A real port could restructure
> or pad prompts to cross the thresholds; that is an engineering choice
> with its own token cost, and is not assumed here.

### 4.7 What §4 establishes — and what it does not

1. **It is a counterfactual.** Actual measured cloud-LLM spend is $0
   (§3.3, re-verified today, gate + key-absence + zero log rows). No
   frontier call path is wired (SR2 tiers 3/4 are an unwired seam; 5
   guardrail audit rows ever).
2. **The honest comparison it supports:** replacing the *inference layer
   alone* with a frontier small-tier API would list at **≈ $0.1k–$1k/yr**
   (before any caching benefit, which is ≈$0 at those tiers, and before a
   −50 % batch discount on the latency-tolerant subset). That is the
   *cheapest* line in the whole replacement stack (§7) — the platform's
   moat is **not** "cloud inference is expensive"; it is data sovereignty
   (no operational content leaves the box — the standing design rationale),
   the unpurchasable data/intelligence capabilities (§8), and the owned
   corpus. Any pitch that leans on avoided cloud-LLM spend as a big number
   is contradicted by this section and should not be made.
3. **Symmetrically, it bounds the hybrid-offload roadmap:** wiring SR2
   tier-3/4 escalation to a frontier API for the hardest ~5 % of calls
   would cost on the order of **tens of dollars per year** at small-tier
   rates — cost is not the blocker; the no-egress policy is, and that is a
   deliberate policy choice, not an economic one.
4. Token counts are modelled (SR-1 logs zeros), OpenAI prices are
   secondary-sourced after a 403 (⚠️ flagged), and the June share of the
   window is a disclosed data gap — all three limits stated rather than
   smoothed over.

---

## 5. What was re-verified today vs. carried (pricing provenance)

| Input | Status today (2026-09-03) |
|---|---|
| EIA DC residential tariff | ✅ **Re-fetched — CHANGED**: 24.39 ¢/kWh (June-2026 table, released 2026-08-26); 08-24 used 25.40 ¢ (May 2026) |
| FlightAware Enterprise $99.95/mo ($1,199.40/yr) | ✅ Re-confirmed by direct fetch today — schedule still headed "As of 1/17/2023" (current but 3.5 yrs unrevised; disclosed) |
| FlightAware Firehose quote-only | ✅ Re-confirmed by direct fetch today — "established on a per customer basis". **Superseded the same evening:** a first citable federal-contract anchor (USSS, ≈$148k–224k/yr annualized) was found in the late pass — see `COGS_MULTIVERTICAL_REGULATORY_2026-09-03.md` §10.1 |
| Cirium ~$30,530/yr avg contract (Vendr) | ✅ Re-confirmed by direct fetch today (5+ deals / 3 buyers) — still (b) secondary, still the only SWIM-class anchor |
| Dataminr $22,000 median (Vendr) | ✅ **Re-confirmed by direct fetch today** (range $15,000–$62,500) — was carried on 08-24 |
| Recorded Future $70,375 median (Vendr) | ✅ **Re-confirmed by direct fetch today** (n=47, range $27,000–$216,385) — was carried on 08-24 |
| Notamify Pro $24.90/mo ⇒ $298.80/yr | ✅ Re-confirmed by direct fetch today |
| NewsCatcher Starter $50/mo | ✅ Re-confirmed by direct fetch today |
| ntfy Pro/Business | ✅ Re-confirmed today ($10/mo Pro, $20/mo Business on annual billing ⇒ $120–$240/yr holds; Pro still 10 reserved topics vs. the platform's 14-topic catalog — disclosed) |
| Anthropic Claude pricing + cache terms | ✅ **Fetched fresh today** (claude.com/pricing): Haiku 4.5 $1/$5, Sonnet 5 $2/$10, Opus 5 $5/$25, cache read 0.1× / write 1.25× (5-min TTL), batch −50 %. Sonnet 5 still shows $2/$10 despite the 08-24-recorded 08-31 introductory end date — priced as published |
| OpenAI pricing | ⚠️ **(b)** — vendor page 403 to every direct attempt; GPT-5.4 mini $0.75/$4.50 (cached $0.075) et al. from two aggregators, one dated today — disclosed per-row in §4.4 |
| Google Gemini pricing + caching mechanics | ✅ Fetched fresh today (ai.google.dev, both pricing and caching pages) |
| AirNav RadarBox Business $399/yr · FR24 Business $499.99 · PlaneFinder $19.99 | ⚠️ Carried (08-19 vintage, (b)/search-index) — not re-fetched this pass |
| Visual Crossing / WeatherAPI / IBM weather tiers; Parse.bot rail $30–$100/mo | ⚠️ Carried (08-19) |
| Hardware BOM line items | Carried (operator estimates 2026-08-05; hardware unchanged, node re-confirmed) |
| SR-1 call-rate input for the LLM rows | ✅ **Re-derived live today**: 26,163 real model calls / 56.87 days = **460.1/day ≈ 167,900/yr** (08-24: 423.2/day ≈ 154,500/yr — up ~9 %) |

Live-state gate re-run today (affects which verticals count as "live"):
**all six SWIM feeds active and writing** at check (with today's two
deploy-gap outages disclosed in §3.2); NWWS-OI live (`nws_alerts` 333
rows — was 20 on 08-24); NOTAMs 6,254 / 323 facilities; `flight_events`
961,444 rows (32,438 in 24 h); briefs 225 in last 7 days; AIS still **not
live** (0 rows, no unit — still excluded); `acars_messages` in the
platform DB still **0 rows** (RF capability claimable, DB fusion not —
unchanged); runsheet newest entry still `2026-07-28, trip_count 1`
(demand datum unchanged).

---

## 6. COGS view 1 — what this instance avoids paying today (conservative)

Basis unchanged from 08-24 (Section A of the source doc): only published,
dated, scope-matched prices are summed; quote-only equivalents excluded;
free-to-anyone data credited at $0 by definition.

| Item | Annual | Confidence today |
|---|---|---|
| FlightAware Enterprise (reciprocal feeder benefit) | $1,199.40 | direct fetch today; schedule dated 1/17/2023 |
| AirNav RadarBox Business (reciprocal) | $399.00 | (b) carried |
| OSINT/news API equivalent, scope-matched **down** to actual volume | $600 – $1,080 | direct fetch today (NewsCatcher); Event Registry carried |
| **Tier-1 total (summable)** | **≈ $2,198 – $2,678 / yr** | unchanged |
| **Net of actual recurring cost (§3)** | **≈ $2,160 – $2,656 / yr** | effectively unchanged (electricity netting moved ±$1) |

Qualifications that must travel with this number (all re-confirmed today):
**~60–73 % is reciprocal barter**, not avoided cost (5 aggregator feeder
containers + OpenSky running today; turn the receiver off and the accounts
lapse); the genuinely large equivalents (SWIM-class firehose data) remain
**unpriceable from public sources**; and the price of making "no data
leaves the box" literally true is **$399 – $2,118 / yr** (the
single-vs-four-account choice is an **ASSUMPTION**) — a policy that would
also be a capability *downgrade* (paid accounts are LADD-filtered; the
platform's own receiver is not).

---

## 7. COGS view 2 — the subscription-replacement floor (standalone)

Same question and method guardrails as 08-24 (§2.17 of the source doc:
substitutes never summed within a category; complements summed across;
every price cites its own scope; forked assumptions show both totals;
quote-only with no anchor ⇒ finding, not number). A **replacement-cost
floor**, not a valuation.

### 7.1 Commodity infrastructure — unchanged

| Vertical (live fidelity) | Commercial substitute | Annual |
|---|---|---|
| SWIM-class flight data | Cirium FlightStats Flex (only priced anchor; Firehose/Spire/ADS-B Exchange unpriceable) | $30,530 |
| Consumer/prosumer ADS-B accounts | FA Enterprise + FR24 + RadarBox + PlaneFinder (or one) | $399 – $2,118 |
| Weather (METAR + forecast) | Visual Crossing → IBM/TWC Standard | $420 – $6,000 |
| NOTAM | ForeFlight Starter → Notamify Pro | $130 – $299 |
| Passenger rail realtime | Parse.bot Amtrak API | $360 – $1,200 |
| OSINT / news API | NewsCatcher / Event Registry (scope-matched down) | $600 – $1,080 |
| Push delivery | ntfy Pro/Business | $120 – $240 |
| **Commodity subtotal** | | **$32,559 – $41,467** — **no change vs. 08-24** |

### 7.2 Intelligence-automation layer — LLM row re-derived (small delta)

| Line | Annual |
|---|---|
| Cross-vertical correlation platform (one of: Dataminr $22,000 … Recorded Future $70,375 — Vendr medians, **re-confirmed by direct fetch today**) | $22,000 – $70,375 |
| **LLM inference at measured volume — re-derived §4**: 167,900 real local calls/yr; Claude Haiku 4.5 $1/$5 re-confirmed today; token model **ASSUMPTION** (§4.3); caching delta ≈$0 at this tier (§4.6) | **$444 – $945** (was $443–$862 — call rate up ~9 %, token model re-derived) |
| Vault storage | $180 |
| Security stack, honestly scale-matched | $0 – $12 |
| **Intelligence subtotal** | **$22,624 – $71,512** (was $22,623 – $71,429) |

### 7.3 The floor

| Layer | Low | High |
|---|---:|---:|
| Commodity infrastructure | $32,559 | $41,467 |
| Intelligence-automation capability | $22,624 | $71,512 |
| **Combined — live verticals** | **$55,183** | **$112,979** |
| + maritime/AIS (documented roadmap, **not live** — excluded from the claim) | — | up to +$55,000 |

Rounded headline: **≈ $55,200 – $113,000 / yr** (08-24: ≈$55,200–$112,900;
the +$83 high-end delta is entirely the re-derived LLM row). Why it is a
floor, unchanged: Firehose unpriceable and absent; enterprise actual-buyer
prices run above published tiers; ten capabilities cannot be bought at all
(§8).

> **Superseded the same evening (high end only):** the late 2026-09-03
> pass found the first citable Firehose and Spire anchors (federal
> procurement records), which raise the defensible high end to
> **≈ $230,400/yr** with the low end unchanged. That file now carries the
> headline: `COGS_MULTIVERTICAL_REGULATORY_2026-09-03.md` §10.5.

### 7.4 Against the actual cost base

| | This platform (today) | Subscription replication |
|---|---|---|
| One-time | ≈ $765 | — |
| Recurring | **≈ $22 – $38 / yr** | **≈ $55,200 – $113,000 / yr** (floor) |
| Inference layer alone | $0 measured (local) | ≈ $0.1k – $1k/yr at frontier small tiers (§4) — the cheap line, disclosed as such |
| Corpus ownership | Permanent (raw payloads, reprocessable) | Contractually **destroyed on termination** — OAG §10.4 (reaches derivatives), Kpler §13.3–13.4, ADS-B Exchange §14(d) |
| Blocked/LADD-filtered aircraft | Visible (own RF, not FAA-source-derived) | Filtered — "obfuscated" even on the enterprise feed |

---

## 8. What no amount of money buys (carried; re-stated — this list matters more than the totals)

Unchanged from 08-24, verified negative findings across every vendor
checked in the source passes: **1)** TFMS (ground stops, GDPs, AFPs) —
no commercial product; **2)** TBFM arrival metering — none; **3)** ITWS
terminal wind-shear/microburst alerts — none; **4)** LADD/blocked-aircraft
visibility — every commercial feed obfuscates, paying more makes it
strictly worse; **5)** receive-side ACARS/VDL — send-side carriers only
(disclosed: platform DB fusion still pending, RF layer real); **6)**
scheduled LLM briefs over a private multi-source corpus with an auto-built
concept graph — no single SKU; **7)** a permanently-owned longitudinal
corpus — term licences require destruction on exit; **8)** threshold-based
entity auto-promotion with independent-feed corroboration, human review
gate, silence/embargo detection — zero of ten CI vendors; **9)** a signed
whole-tree manifest with an execution gate (manifest at **896 files**,
verified OK at 19:26 EDT today); **10)** SR-2's per-skill content-hash
execution gate.

---

## 9. Honest limits (the argument's own boundaries)

1. **Most of the ingested data is free at the source.** FAA SWIM is free to
   any approved subscriber; NOAA is public domain; FAA NOTAM data is free.
   §7's replacement framing (a commercial buyer *without* FAA vetting) is
   the honest use of the big number; §6's $2.2–2.7k/yr is the honest "what
   we actually avoid" number. **Do not present the §7 floor as avoided
   spend.**
2. **The inference layer is cheap to buy (§4 — new this pass).** The
   measured workload prices at ≈$0.1k–$1k/yr on frontier small tiers. The
   LLM line must never be the headline of a replacement-cost pitch; the
   data/intelligence/corpus lines carry it.
3. **Cost avoidance does not carry a valuation** — unchanged.
4. **Availability is not a subscription's availability.** All six SWIM
   feeds live at today's check, but the platform took two total feed
   outages today (~13 and ~41 min) from abandoned deploy cycles, on a
   forward-only pipeline with no backfill. No SLA exists, yet.
5. **~34 % of last week's skill LLM calls fell back to deterministic
   templates** (improved from ~42 % on 08-24; labeled and monitored, but
   real). Capacity with honest degradation, not 100 % duty.
6. **Demand is not yet demonstrated.** Runsheet still shows effectively one
   recorded trip (re-checked today).
7. **Several carried prices are secondary-sourced** (flagged per-row in §5)
   and the largest true equivalents remain quote-only; OpenAI's own pricing
   page refused automated fetch and is secondary-sourced this pass. A
   single real Firehose or Cirium quote would still move §7 more than any
   other action. *(Landed the same evening: federal-procurement anchors
   for Firehose, Spire, and Cirium —
   `COGS_MULTIVERTICAL_REGULATORY_2026-09-03.md` §10.)*

---

*Sources: `docs/COST_STRUCTURE.md` (internal, operator estimates
2026-08-05); `docs/COGS_VENDOR_COMPARISON_2026-08-18.md` (internal, incl.
its 2026-08-19 re-verification addendum and Sections A–E);
`COGS_SUBSCRIPTION_REPLACEMENT_2026-08-24.md` and
`REVERIFICATION_2026-08-24.md` (same directory — the 08-24 baseline, both
untouched by this pass); `src/common/guardrails.py`, `src/common/sr1_log.py`,
`src/common/llm.py`, tracked `corporatetraveldc.*` Modelfiles; live system
checks (read-only) and direct vendor-page fetches of 2026-09-03 as itemized
in §3–§5. This file is staged but deliberately uncommitted; the operator
commits (signed) personally or not at all.*
