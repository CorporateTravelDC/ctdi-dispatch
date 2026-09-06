# CTDI Dispatch Platform — Live COGS & Subscription-Replacement Analysis (2026-08-24)

> **Status.** Investor-facing draft, promoted from two internal documents at
> the founder's direction (2026-08-24): `docs/COST_STRUCTURE.md` (hardware
> tiers, previously HELD) and `docs/COGS_VENDOR_COMPARISON_2026-08-18.md`
> (cost-avoidance / subscription-replacement valuation, previously HELD;
> already independently re-verified once, 2026-08-19). This file is a
> **live recomputation as of 2026-08-24**, not a copy: every input was
> either re-derived against the running system today, re-fetched from the
> vendor's own page today, or explicitly labeled "carried" with its vintage.
> The same discipline as the source docs applies throughout — every
> commercial price cites a real vendor source with an access date; every
> business-judgement figure is flagged **ASSUMPTION**; quote-only vendors
> are excluded from totals rather than guessed.
>
> **Same read-only posture as the re-verification pass** (see
> `REVERIFICATION_2026-08-24.md`, same directory): nothing on the live
> system was modified to produce these numbers.

---

## 1. Headline numbers (each derived below)

| Quantity | Figure | Basis |
|---|---|---|
| **One-time hardware, actually deployed** | **≈ $765** | §2, operator-itemized BOM, single node |
| **Actual recurring cost** | **≈ $23 – $39 / yr** (electricity only; midpoint ~$31) | §3, re-derived; tariff re-confirmed today |
| Recurring data-feed fees | **$0** | §3, re-confirmed today |
| Recurring cloud-LLM spend | **$0** — measured, not assumed | §3, re-confirmed today (46-day usage log, zero cloud rows) |
| **Defensible avoided cost (this instance, conservative)** | **≈ $2,160 – $2,655 / yr net** | §5, Section-A basis, key prices re-confirmed today |
| Cost of a strict no-data-sharing policy | **+$399 – $2,118 / yr** | §5, Section-C basis, largest component re-confirmed today |
| **Subscription-replacement floor (purchasable subset of live capability)** | **≈ $55,200 – $112,900 / yr** | §6, Sections D+E basis, anchors re-checked today |
| — of which commodity infrastructure | $32,559 – $41,467 / yr | §6 |
| — of which intelligence-automation layer | $22,623 – $71,429 / yr | §6 |
| + documented-but-**unbuilt** maritime tier (excluded from live totals) | up to ≈ +$55,000 / yr | §6, AIS not live — listed only for the addressable ceiling |
| Capabilities not purchasable at any price | **10** (6 data/ops + 4 intelligence-layer) | §7 |

**The one-line claim that survives hostile review** (updated from the source
doc's own bottom line, all inputs re-verified today):

> *This platform runs on ≈ $765 of owned hardware and ≈ $23–$39/yr of
> electricity, with $0 in data-feed fees and $0 in cloud-LLM spend — while
> the purchasable subset of its live capability lists at roughly
> $55k–$113k/yr in commercial subscriptions, and its most operationally
> valuable elements (NAS flow/metering/terminal-alert data, unfiltered
> blocked-aircraft visibility, receive-side ACARS, a permanently-owned
> longitudinal corpus, and scheduled-brief automation over it) cannot be
> bought at any price.*

What that claim is **not**: a valuation, a revenue claim, or "we avoid
$100k/yr of subscriptions." The governing structural finding (§8) is stated
plainly, as in the source doc.

---

## 2. One-time hardware — cost tiers (promoted from COST_STRUCTURE.md)

All hardware figures are operator planning estimates (basis 2026-08-05,
`~` = approximate), **unchanged and not re-priced this pass** — the deployed
hardware itself has not changed, confirmed today: single node
(`corporatetraveldc-dispatch`, Pi 5, 4 cores / 16 GB, NVMe boot), Ollama
served from the same box (no offload node built).

| Tier | RF ingest | Approx. total (one-time) |
|---|---|---|
| Floor / core-only (no SDR) | none | ≈ $700 + NVMe |
| **Reference — actual current buildout** | ADS-B + VDL2 SDRs, modest indoor antennas | **sub-$900** |
| Full-quality RF build | full SDR + tuned antennas + roof runs | ≈ $1,100 – $1,200 |

**Deployed-node BOM as actually running** (from the operator's itemized line
items; the figure used throughout this analysis):

Pi 5 16 GB ~$350 + case share ~$45 + PSU share ~$30 + NVMe ~$180 +
RF adder (2 × RTL-SDR + modest antennas) ~$160 ⇒ **≈ $765 one-time**.

Cost model: one-time CapEx, then near-zero marginal cost. The "no per-token
charges" half of that claim has live evidence — see §3.

---

## 3. Actual recurring cost — re-derived 2026-08-24

### 3.1 Electricity ≈ $23 – $39 / yr (midpoint ~$31)

- **Wattage bounds unchanged** (hardware unchanged): 10.5 W low bound
  (Pi 5 stressed 6.8 W + 2 SDRs 2.7 W + NVMe ~1 W) to 17.6 W ceiling-argument
  high bound (8.8 W + 2.8 W + 5 W PCIe ceiling + ~1 W cooler). Sources:
  Tom's Hardware Pi 5 review (2023-10-23), Jeff Geerling measurements,
  RTL-SDR Blog V3 datasheet — carried from the 08-19 derivation.
- **Sustained-load basis re-confirmed today:** the box is not idling —
  1-min load average 14–16 on 4 cores at check time, CPU 67 °C. The
  stressed-wattage figures remain the correct basis.
- **Tariff re-confirmed by direct fetch today (not carried):** EIA Electric
  Power Monthly, Table 5.6.A — District of Columbia residential
  **25.40 ¢/kWh** (May 2026; May 2025 was 20.43 ¢).
  <https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a>
  (accessed 2026-08-24, value read directly from the table).
- Arithmetic: 10.5 W × 8,760 h = 92.0 kWh → **$23.36/yr**;
  17.6 W × 8,760 h = 154.2 kWh → **$39.17/yr**.
- Still open (carried caveat): NVMe and cooler draws are ceiling arguments,
  not measurements; a ~$15 inline USB-C power meter would retire this.

### 3.2 Data-feed fees: $0 — re-confirmed

All 19 registry feeds checked live today: every live feed is either free
government/public-domain data (FAA SWIM ×6, TFR, NAS status, ATCSCC, NOAA
NWWS-OI/METAR/NWS, FAA registry), a free community API (Amtrak), scraped
public JSON (DCA/IAD FIDS — fragile, no SLA, disclosed), self-polled RSS
(OSINT), or the platform's own RF receivers (ADS-B, ACARS/VDL). No invoice
exists anywhere in the stack. The ADS-B aggregator accounts are **barter,
not free** — see §5.

### 3.3 Cloud-LLM spend: $0 — measured, re-confirmed today

- `/etc/corporatetraveldc/dispatch.env` sets `ANTHROPIC_FALLBACK_ENABLED=false`
  (confirmed by direct read today).
- SR-1 usage log: **25,147 logged skill LLM invocations, 2026-07-09 →
  2026-08-24 (46.4 days of continuous production)**;
  `grep -icE 'claude|anthropic|gpt|openai'` over the log → **0**. Every
  row's model is a local `corporatetraveldc-pi5-*` Ollama tag (21 models,
  confirmed via `ollama list` today) or the literal `deterministic`
  (template fallback).
- Honest discount that travels with this: **41.7 % of the last 7 days'
  skill calls (2,217 / 5,316) were deterministic-template fallbacks**, not
  inference — Ollama contention and deliberate load-shedding are real. The
  $0 figure is unaffected (the fallback is a local template render, not a
  billed call), but the inference layer should not be sold at 100 % duty.

### 3.4 Quietly-omitted small costs (carried, still true)

Domain registration (shared business cost, registrar/renewal not recorded
in-repo — UNVERIFIED); internet (pre-existing business expense, not
marginal to the platform — the platform does push multi-GB/day of SWIM
ingest across it); FAA SCDS approval ($0 but real lead-time/agreement
friction).

---

## 4. What was re-verified today vs. carried (pricing provenance)

A material improvement over the 08-19 pass: **several vendor pages that
returned HTTP 403 to automated fetch on 08-19 were directly readable
today**, so more of this basis is primary-sourced than in the source doc.

| Input | Status today (2026-08-24) |
|---|---|
| FlightAware Enterprise $99.95/mo ($1,199.40/yr) | ✅ **Re-confirmed by direct fetch today** — schedule still headed "As of 1/17/2023" (current but 3 yrs unrevised; disclosed) |
| FlightAware Firehose quote-only | ✅ Re-confirmed today — page still says pricing "established on a customer basis" |
| Cirium ~$30,530/yr avg contract (Vendr) | ✅ Re-confirmed by direct fetch today ($30,530 ACV; page prose also says "about $31,000 annually") — still (b) secondary, still the only SWIM-class anchor |
| Notamify Pro $24.90/mo ⇒ $298.80/yr | ✅ Re-confirmed by direct fetch today |
| NewsCatcher Starter $50/mo | ✅ Re-confirmed by direct fetch today |
| ntfy.sh Pro/Business tiers | ✅ Re-confirmed today ($10/mo Pro, $20/mo Business on annual billing ⇒ $120–$240/yr range holds) |
| EIA DC residential 25.40 ¢/kWh | ✅ Re-confirmed by direct fetch today |
| Claude Haiku 4.5 $1/$5 per MTok (LLM-replacement row) | ✅ Re-confirmed today. One correction to the source doc: Sonnet 5's $2/$10 is **introductory pricing through 2026-08-31** (then $3/$15), not a cancelled increase — does not affect the total (the total uses Haiku, unchanged) |
| AirNav RadarBox Business $399/yr | ⚠️ Page reachable today but the price string was not present in the fetched HTML — **carried** at (b) vendor-blog, 08-19 vintage |
| FR24 Business $499.99 / PlaneFinder $19.99 | ⚠️ Carried (search-index snapshots, 08-19) — not re-fetched today |
| Visual Crossing / WeatherAPI / IBM weather tiers | ⚠️ Carried (08-19); Visual Crossing page is a JS shell to direct fetch |
| Parse.bot rail $30–$100/mo; findtrain ≈€3,000/yr | ⚠️ Carried (08-19) |
| Vendr CI-platform medians (Dataminr $22k … Recorded Future $70,375) | ⚠️ Carried (08-19, Vendr (b)) |
| Hardware BOM line items | Carried (operator estimates, 2026-08-05; hardware unchanged) |
| SR-1 call-rate input for the LLM row | ✅ **Re-derived live today**: 19,632 real model calls / 46.39 days = **423.2/day ≈ 154,500/yr** (08-19 input was 419.4/day — within 1 %, so the $443–$862/yr Haiku-equivalent row stands) |

Live-state gate re-run today (this affects which verticals count as
"live"): **all six SWIM feeds active and writing** (on 08-19, STDDS and
TFMS were dead and credited $0); NWWS-OI **live and writing** (was down);
ACARS RF **current to the minute** (was intermittent); AIS still **not
live** (0 rows, no unit — still excluded); `acars_messages` in the
platform DB still 0 (RF capability claimable, DB fusion not).

---

## 5. COGS view 1 — what this instance avoids paying today (conservative)

Basis: Section A of the source doc — only **published, dated, scope-matched**
prices are summed; quote-only equivalents (Firehose, Cirium, Spire, ADS-B
Exchange Enterprise, L3Harris, Mosaic ATM, DTN…) are excluded rather than
guessed; free-to-anyone data is credited at $0 by definition.

| Item | Annual | Confidence today |
|---|---|---|
| FlightAware Enterprise (reciprocal feeder benefit) | $1,199.40 | direct fetch today; schedule dated 1/17/2023 |
| AirNav RadarBox Business (reciprocal) | $399.00 | (b) carried |
| OSINT/news API equivalent, scope-matched **down** to actual volume | $600 – $1,080 | direct fetch today (NewsCatcher); Event Registry carried |
| **Tier-1 total (summable)** | **≈ $2,198 – $2,678 / yr** | |
| **Net of actual recurring cost (§3)** | **≈ $2,160 – $2,655 / yr** | |

Qualifications that must travel with this number (all still true today):

1. **~60–73 % of it is reciprocal barter, not avoided cost** — the
   FlightAware/RadarBox accounts are earned by continuously feeding those
   vendors our own ADS-B data (5 aggregator containers + OpenSky confirmed
   running today). Turn the receiver off and they lapse.
2. The genuinely large equivalents (SWIM-class firehose data) remain
   **unpriceable from public sources** — excluded, so this is a floor built
   from the small end of the market.
3. **The price of making "no data leaves the box" literally true** (stop
   feeding, buy the accounts back) is **$399 – $2,118 / yr** — the
   single-account-vs-four-account choice is an **ASSUMPTION**. That policy
   would also be a capability *downgrade*: paid accounts are LADD-filtered;
   our own receiver is not. Some access is not for sale at any price.

---

## 6. COGS view 2 — the subscription-replacement argument (standalone)

**The question:** what would it cost, per year, to replicate this
platform's live capability buying only commercial subscriptions and data
licenses — no FAA-vetted access assumed, no data shared with anyone, same
fidelity? This is a **replacement-cost floor**, not a valuation.

**Method guardrails** (inherited from the source doc §2.17, enforced here):
within a category vendors are substitutes (take one, never sum); across
categories products are complements (these sum); every price cites its own
source for its own scope; where an assumption forks the total, both totals
are shown; quote-only with no credible anchor ⇒ reported as a finding, not
a number.

### 6.1 Commodity infrastructure (Section-D basis)

| Vertical (live fidelity) | Commercial substitute | Annual |
|---|---|---|
| SWIM-class flight data (national FDPS-class feed) | Cirium FlightStats Flex — the **only** anchor with any price; Firehose/Spire/ADS-B Exchange all unpriceable | $30,530 |
| Consumer/prosumer ADS-B accounts | FA Enterprise + FR24 + RadarBox + PlaneFinder (or one of them) | $399 – $2,118 |
| Weather (METAR + forecast) | Visual Crossing → IBM/TWC Standard | $420 – $6,000 |
| NOTAM | ForeFlight Starter → Notamify Pro | $130 – $299 |
| Passenger rail realtime | Parse.bot Amtrak API | $360 – $1,200 |
| OSINT / news API | NewsCatcher / Event Registry (scope-matched down) | $600 – $1,080 |
| Push delivery | ntfy.sh Pro/Business | $120 – $240 |
| **Commodity subtotal** (D core minus rows superseded by §6.2) | | **$32,559 – $41,467** |

### 6.2 Intelligence-automation layer (Section-E basis — the correction that matters)

Pricing this layer as commodity SKUs (LLM API + file storage + a vector DB
≈ $1,163–$1,642/yr) was tested and rejected in the source doc as a 19–44×
undervaluation: the capability actually running is a cross-vertical
signal-detection system over a permanently-owned corpus. The nearest real
substitutes are enterprise competitive/threat-intelligence platforms:

| Line | Annual |
|---|---|
| Cross-vertical correlation platform (one of: Dataminr $22,000 … Recorded Future $70,375 — Vendr medians, (b), 08-19 vintage) | $22,000 – $70,375 |
| LLM inference at measured volume (154,500 real local calls/yr re-derived today; Haiku 4.5 $1/$5 re-confirmed today; token model is an **ASSUMPTION**, arithmetic shown in source §2.19) | $443 – $862 |
| Vault storage | $180 |
| Security stack, honestly scale-matched (Cloudflare Free, Tailscale Personal, free-tier IAM; Datadog logs <$1/mo) | $0 – $12 |
| **Intelligence subtotal** | **$22,623 – $71,429** |

Note what the CI-platform money does **not** buy: none of those vendors
ingests flight/rail/weather operational feeds at all — the correlation
substrate itself would still have to be built.

### 6.3 The floor

| Layer | Low | High |
|---|---:|---:|
| Commodity infrastructure | $32,559 | $41,467 |
| Intelligence-automation capability | $22,623 | $71,429 |
| **Combined — live verticals** | **$55,182** | **$112,896** |
| + maritime/AIS (documented roadmap, **not live** — excluded from the claim) | — | up to +$55,000 |

**Why this is a floor, not an estimate:** (1) FlightAware Firehose — the
only vendor covering surface positions — is unpriceable and therefore
absent; (2) enterprise actual-buyer prices run far above published tiers
(weather actual-buyer median ~$72k vs. the $6k list tier used); (3) ten
capabilities cannot be bought at all (§7), so the money purchases a
thinner platform, not this one.

### 6.4 Against the actual cost base

| | This platform (today) | Subscription replication |
|---|---|---|
| One-time | ≈ $765 | — |
| Recurring | **≈ $23 – $39 / yr** | **≈ $55,200 – $112,900 / yr** (floor) |
| Corpus ownership | Permanent (raw payloads, reprocessable) | Contractually **destroyed on termination** — OAG §10.4 (reaches derivatives), Kpler §13.3–13.4, ADS-B Exchange §14(d), all with written-certification duties |
| Blocked/LADD-filtered aircraft | Visible (own RF, not FAA-source-derived) | Filtered — "obfuscated" even on the enterprise feed |

---

## 7. What no amount of money buys (re-stated; this list matters more than the totals)

From the platform's live capability set, verified negative findings across
every vendor checked in the source passes:

1. **TFMS** — ground stops, GDPs, airspace flow programs (now including AFP
   parsing, added 2026-08-22): no commercial product exists.
2. **TBFM** — arrival metering / meter-fix sequencing: none.
3. **ITWS** — terminal wind-shear/microburst alerts: none (generic aviation
   weather is a different data class).
4. **LADD / blocked-aircraft visibility** — own-RF is not FAA-source-derived
   and not LADD-bound; every commercial feed obfuscates. Paying more makes
   this strictly worse.
5. **Receive-side ACARS/VDL** — SITA/Collins sell the send side only;
   no third-party receive feed exists globally. (Disclosed: this platform's
   RF layer receives currently; fusion into the platform DB is still
   pending an off-box fix.)
6. **Scheduled LLM briefs over a private multi-source corpus with an
   auto-built concept graph** — no single SKU; enterprise RAG products are
   chat-on-demand.
7. **A permanently-owned longitudinal corpus** — term licenses require
   destruction on exit; you cannot own this even if you pay.
8. **Threshold-based entity auto-promotion with independent-feed
   corroboration, a human review gate, and silence/embargo detection** —
   zero of ten CI vendors document any of it.
9. **A signed whole-tree manifest with an execution gate** (33 skill
   quadlets gated today; 706-file manifest, signature verified this
   morning).
10. **SR-2's per-skill content-hash execution gate.**

---

## 8. Honest limits (the argument's own boundaries, stated up front)

1. **Most of the ingested data is free at the source.** FAA SWIM is free to
   any approved subscriber; NOAA data is public domain; FAA NOTAM data is
   free. The avoided cost is an *integration* cost, not a data-license
   cost — a competitor with the same approvals and hardware pays the same
   $0 for the same bytes. What is owned is the integration, the automation,
   and the accumulated corpus. §6's replacement framing (a commercial buyer
   *without* FAA vetting) is the honest way to use the big number; §5's
   $2.2–2.7k/yr is the honest "what we actually avoid" number. **Do not
   present the §6 floor as avoided spend.**
2. **Cost avoidance does not carry a valuation.** Capitalizing §5 at even a
   generous multiple lands an order of magnitude below historical headline
   bands; the source doc says so and this pass re-affirms it.
3. **Availability is not a subscription's availability.** All six SWIM
   feeds were live at today's check, but the platform deliberately sheds
   its ingest tier under Ollama contention — 10 LOCKDOWN events of ~9–11
   min in the ~32 h before this check. A commercial feed carries an SLA;
   this does not, yet.
4. **~42 % of last week's skill LLM calls fell back to deterministic
   templates** (labeled, monitored, alert-on-fallback — but real). The
   inference layer is capacity with honest degradation, not 100 % duty.
5. **Demand is not yet demonstrated.** The operational runsheet still shows
   effectively one recorded trip; output is capacity, not consumed product.
6. **Several carried prices are secondary-sourced** (flagged per-row in §4)
   and the largest true equivalents remain quote-only. A single real
   Firehose or Cirium quote would move §6 more than any other action.

---

*Sources: `docs/COST_STRUCTURE.md` (internal, operator estimates 2026-08-05);
`docs/COGS_VENDOR_COMPARISON_2026-08-18.md` (internal, incl. its 2026-08-19
re-verification addendum and Sections A–E) — both intentionally untouched by
this pass; live system checks and direct vendor-page fetches of 2026-08-24 as
itemized in §3–§4. Companion re-verification: `REVERIFICATION_2026-08-24.md`,
same directory.*
