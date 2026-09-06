# CTDI Dispatch Platform — Multi-Vertical Floors, Quote-Only Vendor Resolution, Regulatory-Standing Cost & Second-Brain Retention Floor (2026-09-03, late pass)

> **Status.** Investor-facing research draft — the **companion extension** to
> `COGS_SUBSCRIPTION_REPLACEMENT_2026-09-03.md` (same directory, "the 09-03
> baseline"), written the same evening (~21:00–23:45 EDT; box clock verified
> twice via `timedatectl`: 21:01 and 23:36 EDT, America/New_York,
> NTP-synchronized). A companion file was chosen over in-place extension
> because the baseline already carries its own §1–§9 structure and stands as
> a complete live recomputation; this file **continues its numbering at
> §10** and never restates a headline the baseline owns — where this pass
> *changes* a baseline number (it changes one: the platform-wide
> replacement-floor high end, §10.5), the baseline carries a one-line
> superseded-pointer and this file carries the number.
>
> **Scope (operator direction, this pass):** (1) vertical-specific
> subscription-replacement floors for aviation-ops,
> corporate-travel-concierge, executive-ground-transport, and
> executive-protection — closing the gap that all four vertical documents
> currently cite the same platform-wide ~$55.2k–$113k/yr figure with no
> vertical-specific breakdown; (2) real citable floors for the vendors this
> repo's convention previously **excluded as quote-only** — FlightAware
> Firehose and SWIM-class feeds above all — replacing exclusion with cited
> anchors wherever a genuine multi-source search could find one, and with a
> documented exhausted-search finding where it could not; (3) a citable
> approximation floor for the second-brain automated-retention capability;
> (4) the regulatory-compliance cost a buyer would bear to obtain
> equivalent standing. Items (3) and (4) are **core-thesis material, not
> footnotes** — each section leads with why the capability is structurally
> hard to replicate and uses the dollar floor as supporting evidence.
>
> **Method (unchanged from the source docs):** every commercial price cites
> a real, dated source; business-judgement figures are flagged
> **ASSUMPTION**; substitutes are never summed within a category,
> complements are summed across categories; forked assumptions show both
> totals. Source categories: **(a)** vendor's own page/legal doc, fetched
> directly · **(b)** credible secondary (aggregator/press/review site) ·
> **(c)** government record — federal procurement (USASpending.gov), GSA
> schedule, Federal Register, statute, or audited public filing. Category
> (c) is new this pass and is the workhorse: procurement obligations are
> real, dated, public dollar amounts for exactly the vendors that publish
> none. All web sources accessed 2026-09-03 (EDT evening) by this pass's
> research unless a different vintage is stated.
>
> **Read-only posture:** nothing on the live system was modified. Live
> inputs were read-only (`timedatectl`, `systemctl --user list-timers`, the
> second-brain FTS/semantic index via `scripts/second-brain-search.sh`,
> tracked docs). The second-brain vault was queried for prior pricing
> research before any web search (semantic + literal: 0 prior notes on
> these topics — this is first-capture research).

---

## §10. Quote-only vendors, re-priced — and the revised platform-wide band

### 10.1 FlightAware Firehose — first citable anchor found (federal procurement)

The vendor page remains quote-only, re-confirmed this pass: pricing
"established on a per customer basis"
(<https://flightaware.com/commercial/firehose/>, (a)). GSA eLibrary shows
**no published schedule-line pricing** for FlightAware. But USASpending.gov
carries real, dated obligations under FlightAware's GSA MAS vehicle
(IDV 47QTCA21D003F, ceiling $475,000):

| Award | Buyer | Amount | Term | Description | Source |
|---|---|---|---|---|---|
| 70US0925F2GSA2192 | U.S. Secret Service (DHS) | **$295,963.20 obligated** (ceiling $447,282.60) | 2-yr base, 2025-05-31 → 2027-05-30, signed 2025-05-30 | "FLIGHTAWARE FIREHOSE DATA SUBSCRIPTIONS" | <https://www.usaspending.gov/award/CONT_AWD_70US0925F2GSA2192_7009_47QTCA21D003F_4732> (c) |
| 70US0924F2GSA2315 | U.S. Secret Service (DHS) | $144,000.00 | 1 month, signed 2024-06-13 | "FLIGHTAWARE FIREHOSE SUBSCRIPTIONS" | USASpending.gov (c) |

**Annualized anchor: ≈ $147,982/yr (obligated) to ≈ $223,641/yr (contract
ceiling).** The one-month $144,000 bridge is disclosed but **not
annualized** — a bridge/renewal lump is not a monthly run-rate. Scope
caveats that must travel with this anchor: the award says "subscriptions"
(plural — seat/stream count unknown), it is government GSA pricing, and a
Secret Service deployment is plausibly at or above the platform's
national-scope fidelity rather than below it. Direction of error:
**this is a floor for a national-scope streaming buyer, not a ceiling.**

Adjacent published lower bound, for scale (re-confirmed (a),
<https://flightaware.com/commercial/aeroapi/>): AeroAPI Standard
**$100/mo minimum** ($1,200/yr; per-query $0.005–$0.060/result set; push
alerts $0.020/result set), Premium **$1,000/mo minimum** ($12,000/yr,
adds Foresight + Aireon). Corroborating federal buy: State Dept AeroAPI
Premium, **$11,700/12 mo** (PIID 19AQMM25F0657, (c)) — consistent with the
published $12,000/yr floor (small discount/partial-year, not a
contradiction). Firehose forum threads (discussions.flightaware.com,
incl. `/t/firehose-cost/16722`) and a 2017 Wayback snapshot of the
Firehose page: zero dollar figures anywhere — the procurement record is
the **only** public price signal that exists.

### 10.2 FAA SWIM access — $0, now cited twice from FAA primary sources

- FAA SWIM Q&A: *"Currently there is no cost for data. Costs to develop a
  SWIM interface are the responsibility of the party interested in
  consuming the data."* — <https://www.faa.gov/air_traffic/technology/swim/questions_answers>
  (a, .gov; fetched **directly** this pass — upgrading the 08-18/08-19
  "indirectly verified via search index" flag on this same quote).
- SCDS Guideline v1.1 §3.4.1: the no-cost tier is capped at
  **200 GB/day or 2 TB/month** per subscriber (a, .gov). (The platform's
  measured ~2.7 GB/day SWIM ingest sits far inside the free tier.)

So the SWIM-class line's honest structure is now fully cited: **the data
itself is $0 from the FAA**; what a commercial buyer pays for is either
(i) the vetting/onboarding/engineering path (§12) or (ii) a commercial
streaming equivalent (§10.1/§10.3). Commercial SWIM-as-a-service
intermediaries (L3Harris Orion, Mosaic ATM Fuser, AWS Data Exchange and
Snowflake Marketplace SWIM-derived listings): **EXHAUSTED — no citable
dollar figure anywhere** after direct fetches and multiple searches;
documented negative, carried as quote-only.

### 10.3 Spire Aviation — citable band from three consecutive Navy renewals

Spire's own pricing page names tiers, no dollars (a). USASpending (c):

| Award | Amount | Signed | Description |
|---|---|---|---|
| N6523624P0053 | $158,976.00 | 2024-08-28 | "SPIRE DATA SUBSCRIPTION PACKAGE" |
| N6523625P0022 | $199,586.00 | 2025-03-12 | "SPIRE DATA PACKAGE" |
| N6523626PE013 | $199,586.00 | 2026-04-02 | "SPIRE RENEWAL SATELITTE AND TERRESTRIAL ADS-B" |

Three consecutive annual buys by the same Navy office ⇒
**≈ $159k–$200k/yr** for a satellite+terrestrial ADS-B data package —
independently corroborating the Firehose-anchor's price region for
national/global-scope streaming flight data.

### 10.4 The rest of the quote-only sweep (every excluded vendor, dispositioned)

| Vendor | This pass's result |
|---|---|
| **Cirium** | Vendr avg **$30,530/yr** re-confirmed; now **corroborated by two federal buys**: EPA "CIRIUM FLEETS ANALYZER DATABASE" $91,302 / up-to-5-yr (68HE0M19P0061, 2019) ≈ $18,260/yr for a narrower product; DOE "Global Cancellation Disruption Monitor" $30,000 / 6 mo (89303020PEI000052, 2020) (c). No longer single-source. |
| **ADS-B Exchange Enterprise** | **EXHAUSTED** — including a 2022-08-10 Wayback snapshot that was *already* "contact for pricing" (genuine historical negative). Adjacent published lower bound only: RapidAPI Basic $10/mo / 10k calls (entry tier, not Enterprise). Remains excluded from totals; the finding is now documented rather than assumed. |
| **OAG Flight Info API / FlightView business** | Quote-only confirmed; flightview.oag.com repeatedly unreachable (timeout + DNS fail ×2); developers.oag.com free signup, zero paid pricing published. EXHAUSTED. |
| **Cirium FlightStats APIs** | No published tiers (confirmed absence, (a)). Vendr Cirium median stands as the anchor. |
| **International SOS (institutional)** | No institutional dollar found; retail floor "$67/trip"; a 2006 SEC-filed $60,000/yr corporate contract is too stale to headline. EXHAUSTED for a current institutional figure; per-traveler bands used instead (§11.2, flagged). |
| **Samdesk / Base Operations** | Samdesk: EXHAUSTED. Base Operations: published tier *structure* only ($/$$/$$$), no numbers (a). |
| **FlightBridge operator tiers / TripTracker / LimoExpress overage / Samsara (Vendr)** | All no-citable-price, logged in §11.3's research; FlightBridge FBO Premium **is** published ($300/mo/location). (TripTracker note: direct fetch was intercepted by the box's own tailnet DNS rewrite — resolved to the local dispatch host — retry off-tailnet some day; searches found no price.) |
| **Weather quote-onlys (DTN, Meteomatics, Baron, CheckWX/AVWX paid)** | **Not re-attempted this pass** — the weather category already carries priced substitutes (Visual Crossing → IBM/TWC, baseline §7.1), so a quote-only alternate in a priced category moves nothing. Stated for completeness, not silently dropped. |

### 10.5 What this does to the platform-wide floor — the one headline change

The 09-03 baseline's ≈ $55,200–$113,000/yr floor was a floor *because*
Firehose/SWIM-class streaming was excluded as quote-only, leaving Cirium
FlightStats Flex ($30,530) as the only priced anchor in the SWIM-class
category. That category's citable range is now:

> **SWIM-class flight data: $30,530/yr (Cirium, Vendr avg, lower-fidelity
> REST) → $147,982–$223,641/yr (FlightAware Firehose, USSS award
> annualized obligated→ceiling), with Spire at $158,976–$199,586/yr
> corroborating the upper region.**

Substitutes are never summed within a category, so the recomputation
replaces one number on the high side only (low side keeps the cheapest
substitute, exactly as before):

| Platform-wide band | Low | High |
|---|---:|---:|
| Commodity infrastructure (baseline §7.1, SWIM-class line swapped on high side) | $32,559 | $41,467 − $30,530 + $147,982 = **$158,919** |
| Intelligence-automation (baseline §7.2, unchanged) | $22,624 | $71,512 |
| **Combined — revised** | **$55,183** | **$230,431** |

**Recommended revised headline: ≈ $55,200 – $230,400/yr** (was ≈ $55,200 –
$113,000). Disclosed variants, not headlined: Spire-anchored high
≈ $282,000/yr; Firehose contract-ceiling high ≈ $306,100/yr. **This is a
material headline change, not a footnote** — every vertical executive
summary and the platform-generic materials currently cite the old band
(fold-in directives in §14). The low end is unchanged and the honest
framing is unchanged: this is replacement cost for a buyer **without**
FAA vetting, never avoided spend (baseline §9.1 governs).

---

## §11. Multi-vertical subscription-replacement floors

**Construction rule, stated before any number:** each vertical floor
answers *"what would a buyer of ONLY this vertical's live capability pay
per year in commercial subscriptions?"* Each is an **independent
single-vertical calculation** that shares commodity lines (flight data,
rail, push, LLM) with the platform-wide floor and adds vertical-specific
platforms the platform-wide floor only proxies through its single
correlation-platform line. Therefore: **(i)** vertical floors must never
be summed with each other or with the platform-wide number — shared
infrastructure would be multiple-counted (the naive sum of the four highs
is ≈ $485k/yr and must never appear in any material); **(ii)** a vertical
floor may legitimately exceed the old platform-wide high (EP does),
because the platform-wide intelligence layer prices *one* correlation
platform (substitutes, take-one) while a vertical buyer needs
*complementary* tools summed; **(iii)** the platform-generic vertical
keeps the platform-wide band (§10.5 revised) as the umbrella claim.

### 11.1 Aviation-ops: ≈ $32,000 – $157,600/yr

The closest vertical to the platform-wide analysis; its floor is the
flight-data slice plus aviation-specific complements, with rail/OSINT/
cross-vertical correlation excluded:

| Line (complements, summed) | Annual | Basis |
|---|---|---|
| SWIM-class flight data (substitutes: take one) | $30,530 → $147,982 | Cirium (b, Vendr) → Firehose USSS annualized (c) — §10.1/§10.5 |
| Prosumer ADS-B accounts | $399 – $2,118 | baseline §7.1, carried |
| Aviation weather (METAR+forecast) | $420 – $6,000 | baseline §7.1, carried |
| NOTAM | $130 – $299 | baseline §7.1, carried |
| Push delivery | $120 – $240 | baseline §7.1, carried |
| LLM inference at measured volume | $444 – $945 | baseline §4/§7.2, carried |
| **Aviation-ops floor** | **$32,043 – $157,584** | ceiling-variant high $233,243 disclosed, not headlined |

Reconciliation: a strict subset of the revised platform-wide band (every
line is in §10.5's calculation; nothing vertical-specific had to be
added, because the platform's aviation capability *is* the commodity
core). What is **not** in the floor, and cannot be at any price: TFMS
flow programs, TBFM metering, ITWS terminal alerts, unfiltered LADD
visibility, receive-side ACARS (baseline §8 — unchanged and still the
stronger claim for this audience).

### 11.2 Corporate-travel-concierge: ≈ $4,200 – $64,300/yr

A travel desk buying only this vertical's capability needs a flight
status/alerts API, rail status, a travel-risk/traveler-care platform, and
push delivery:

| Line | Low | Mid | High | Basis |
|---|---:|---:|---:|---|
| Flight status/alerts API (substitutes) | $1,200 (AeroAPI Standard) | $1,200 | $30,530 (Cirium) | (a) flightaware.com/commercial/aeroapi; (b) Vendr — adjacent published: AeroDataBox $228–$5,988/yr, aviationstack ~$540–$5,100/yr, FlightLabs $3,000–$60,000/yr (all (a)) |
| Passenger-rail realtime | $360 | $360 | $12,000 | Parse.bot $30–$100/mo (baseline, carried); provider "Company" tier $12,000/yr (a). Scope flag: schedules/fares-oriented; live-delay fidelity below the platform's Amtrak feed |
| Travel-risk / traveler-care platform (substitutes) | $2,500 **ASSUMPTION** (100 travelers × $25 PTPY, editorial per-traveler band $15–$250 PTPY across ISOS/Crisis24/Riskline/Safeture, (b)) | $15,000 (Navan, Vendr median, n=400, range $2,727–$73,827, (b)) | $21,493 (Everbridge, Vendr median, (b)) | Real enterprise contracts run **six figures**: Safeture €383,000/3 yr ≈ **€127.7k/yr** (audited filings + company PR, (c)-class; Safeture ARR 61,759 kSEK end Q1 2026); Everbridge Anvil UK FCA award **£86,500 total, term unconfirmed** (c). Cited as evidence the high end is a floor — not summed, and no FX conversion applied (rate not sourced this pass) |
| Push delivery | $120 | $120 | $240 | baseline, carried |
| **Concierge floor** | **$4,180** | **$16,680** | **$64,263** | |

Reconciliation: shares the flight/rail/push lines with the platform-wide
band; **adds** the traveler-care platform line (which the platform-wide
floor does not carry — its Dataminr/RF line is a different category).
Not a subset, not additive: an independent single-vertical calculation.
What no product in this basket does: watch a booking's *specific*
flight+train automatically from a reservation webhook and push
recovery-window alerts through self-hosted infrastructure — the
integration capability is the product here, and the honest low end
($4.2k) says so.

### 11.3 Executive-ground-transport: ≈ $1,700 – $8,000/yr

The relay posture governs the comparison: CTDI deliberately does **not**
replace dispatch software, so the honest comparable is what an operator
pays for the *flight/train-intelligence and alerting* capability — via a
dispatch platform that bundles it, or standalone:

| Line | Annual | Basis |
|---|---|---|
| Flight intelligence + alerting (substitutes: take one) | $1,200 – $6,588 | AeroAPI Standard $1,200/yr min (a) → Limo Anywhere Black $549/mo = $6,588/yr (+$899 one-time setup, yr-1) (a, limoanywhere.com/pricing; Capterra 2026 corroborates tiers (b)). Between them: Limo Anywhere Core $1,188/yr, FASTTRAK flight-bearing plans $1,188–$3,828/yr (a), Moovs paid tiers $1,788–$11,988/yr with "FlightAware flight tracking" (a), FlightBridge FBO Premium $3,600/yr/location (a) |
| Passenger-rail realtime | $360 – $1,200 | Parse.bot (baseline, carried) — **no limo/dispatch platform in the sweep offers rail tracking at all**; a commercial buyer must bolt it on |
| Push delivery | $120 – $240 | baseline, carried |
| **Ground-transport floor** | **$1,680 – $8,028** | bare-feature minimum **$240/yr** disclosed below |

Findings with teeth from this sweep (all (a) unless noted):
- **The bundled-feature price of flight tracking is citable at $240/yr**:
  Book Rides Online prices the identical platform with and without
  "Flight Status Tracking" ($59 vs $79/mo) — a clean $20/mo feature delta.
- **The only citable per-tracked-flight metered rate is $0.06**: FASTTRAK
  publishes "FlightView-Server Calls $.06 >1K, scales to $.004 at 60K".
  The "$0.10–$0.25 per tracked flight" figure sometimes heard in the
  industry has **no citable support anywhere** — do not use it.
- Limo Anywhere's bundled tracking runs on **FlightStats** (vendor KB,
  kb.limoanywhere.com), i.e., the estimate-vs-estimate proxy class — not
  airline-reported OOOI, not TFMS flow programs, not LADD-unfiltered.
  The floor buys alerting on *estimated* times; the platform's
  differentiator (real OOOI + flow-program awareness at the dispatch
  desk) is not in the purchasable basket at any of these prices.
- Fleet telematics (Samsara ~$324–$396/veh/yr, Motive ~$300–$600/veh/yr,
  both (b) secondary — no published list price, Vendr sample empty) is a
  different capability and is **excluded** from this floor; additive only
  if fleet visibility is claimed.
- Live-traffic/routing feeds: **deliberately excluded** — the live
  19-feed registry contains no road-traffic feed, so crediting one would
  price a capability the platform does not currently have. (Reference
  only: Google Maps Routes API Essentials $5/1k requests, (a), page
  updated 2026-09-01.)

Reconciliation: near-subset of the platform-wide commodity core at far
lower fidelity (FlightStats-class estimates vs. SWIM push); the vertical
floor is small *because the purchasable version of this capability is
shallow*, which is the sales argument, stated honestly.

### 11.4 Executive-protection: ≈ $59,300 – $255,300/yr

An EP/GSOC buyer of only this vertical's capability needs a real-time
event-detection feed, an OSINT threat-monitoring platform, a critical-event
management/mass-notification layer (complements — summed), plus movement
monitoring (flight tracking API) and push:

| Line | Low | Mid (medians) | High (range tops) | Basis |
|---|---:|---:|---:|---|
| Real-time event detection | $22,000 | $22,000 | $62,500 | Dataminr, Vendr median/range (b, re-confirmed in baseline §5) |
| OSINT threat monitoring | $36,000 | $36,000 | $77,000 | LifeRaft Navigator, Vendr median $36,000, range $11,500–$77,000 (b) |
| Critical-event mgmt / notification | — (omitted at low) | $21,493 | $100,000 | Everbridge, Vendr median $21,493; mid-market band top $100k (b, Feb 2026) |
| Movement monitoring (flight API) | $1,200 | $1,200 | $12,000 | AeroAPI Standard → Premium (a); per-tail option FA Global $600–$3,540/tail/yr (a), 1-tail **ASSUMPTION**, disclosed not summed |
| Push delivery | $120 | $120 | $240 | baseline, carried |
| **EP floor** | **$59,320** | **$80,813** | **$251,740** | high +$3,540 with the per-tail option ⇒ ≈ $255,280 |

Scale anchors from federal procurement (**cited, not summed** — they show
where enterprise EP-intelligence spend actually lands): Ontic ≈
**$725,000/yr** (DOJ, $2.9M/4 yr, 15DDHQ23P00000934, (c)); Factal
**$635,381/yr** (State Dept, 19AQMM25P1183, (c)); Seerist ≈
**$99,900/yr for 10 licenses** (Army NG, W912L726FA016, (c)); Crisis24 ≈
**$40,000/yr** (FAA, $220,475/~5.5 yr, 693KA921C00011, (c)).
Ambient.ai was evaluated and **excluded as not comparable** (camera/PACS
video analytics; no capability overlap with EP intelligence watches).

Reconciliation — and the explanation the fold-in must carry: **this
vertical floor's high end exceeds the old platform-wide high** ($113k)
and brackets the revised one, legitimately: the platform-wide
intelligence layer prices *one* correlation platform
(Dataminr $22,000 … Recorded Future $70,375, take-one), while an EP
buyer replicating the platform's *combined* live behavior (event
detection + OSINT entity tracking with corroboration + notification +
movement monitoring) needs complementary products summed. Also stated
plainly: **no product in this basket does TFR/airspace-threat
correlation or airline-reported OOOI movement monitoring at all** — the
EP floor buys adjacent capability, not equivalence — and none of it
touches the ten unpurchasables (baseline §8).

### 11.5 The four floors, side by side

| Vertical | Floor (low – high) | Relation to platform-wide band (§10.5: $55.2k–$230.4k) |
|---|---|---|
| Aviation-ops | **$32.0k – $157.6k/yr** | strict subset (commodity core) |
| Corporate-travel-concierge | **$4.2k – $64.3k/yr** | shares flight/rail/push lines; adds traveler-care platform line |
| Executive-ground-transport | **$1.7k – $8.0k/yr** | shallow-fidelity near-subset; bare-feature min $240/yr |
| Executive-protection | **$59.3k – $255.3k/yr** | shares movement/push lines; EP intel basket sums complements the platform-wide band takes one-of |
| *(platform-generic)* | *$55.2k – $230.4k/yr (revised umbrella, §10.5)* | *the cross-vertical case; unchanged low, revised high* |

**Never sum the vertical floors.** Each is what a single-vertical buyer
pays; the umbrella is what a cross-vertical buyer pays once.

---

## §12. Regulatory standing — what a buyer would spend to get where this platform already is

> **Core thesis, stated first.** The platform's cheapest-looking asset is
> its most defensible: it is an **approved FAA SWIM subscriber under a
> signed, annually-renewed Service Access Agreement**, it operates the
> LADD compliance discipline that agreement family imposes (weekly
> CUI-marked filter imports, tier-gated exposure —
> `docs/LADD_CUI_HANDLING.md`), and its own-RF reception sits **lawfully
> outside LADD's contractual reach**. None of that appears on an invoice,
> which is exactly why a replacement-cost analysis that only counts
> subscriptions understates the moat. This section prices the path a
> buyer must walk to obtain equivalent standing — and identifies the one
> piece no agreement can confer.

### 12.1 The SWIM onboarding path: $0 in fees, 2–6 months, agreement-bound

All from FAA primary sources ((a), .gov), fetched directly this pass:

- **Fee: none, documented twice** — the SWIM Q&A quote (§10.2) plus SCDS
  Guideline v1.1 §3.4.1 (no-cost tier: 200 GB/day / 2 TB/mo).
- **Process**: SWIFT Portal account → SCDS subscription wizard → **signed
  Service Access Agreement (SAA)** → provisioning. The SAA must be
  **renewed annually** (30-day grace, then the subscription is disabled);
  60 days of inactivity auto-disables a subscription; 90 days without
  login disables the account. Standing is *maintained*, not just obtained.
- **Gated tiers**: CDM data requires a separate FAA CDM Memorandum of
  Agreement; "Sensitive Data" requires **National Data Release Board
  (NDRB) approval** plus an NESG connection — the vetting the source docs
  have always referenced is real and documented.
- **Timeline, FAA's own words**: **"2–6 months, depending on time to
  develop a mature interface … and approval from the NAS Data Release
  Group."** No third-party corroboration of actual elapsed onboarding
  time was found anywhere (documented negative); the FAA's own figure is
  the citable one. (Consistent internal precedent:
  `docs/DATA_SOURCES.md` — "approval typically takes several weeks…no
  fee for qualified requestors.")

### 12.2 LADD's legal mechanics — corrected, cited to the enacted text

This pass corrects the record versus loose internal shorthand
("a 2019 LADD Federal Register notice" — **no such notice exists**):

- **Statute**: FAA Reauthorization Act of 2018, **Pub. L. 115-254 § 566**
  (132 Stat. 3385), codified as a note to **49 U.S.C. § 44103** (not
  § 40103): an owner/operator may request that registration data be
  withheld from public dissemination. The FAA's current LADD page cites
  FAA Reauthorization Act of 2024, **Pub. L. 118-63 § 803** ("Data
  Privacy") as implementing authority.
- **Federal Register trail (the binding contractual mechanism)**: docket
  **FAA-2011-0183** — interim policy 76 FR 78328 (2011), proposal
  77 FR 27269 (2012), **final notice 78 FR 51804 (Aug 21, 2013)** —
  establishing block-list compliance as a condition of FAA data access
  (the program was renamed from BARR to LADD administratively in 2019,
  with no FR notice).
- **The live instrument**: SCDS Service Access Agreement v1.0, verbatim:
  *"no data … may be displayed or distributed for any aircraft
  registration or call sign while … listed on the FAA Limiting Aircraft
  Data Displayed (LADD) program list"* — with **flow-down to indirect
  consumers**, monthly list updates (first Thursday), a 5-business-day
  compliance window, and termination as the remedy. This is why **every**
  commercial FAA-derived feed is LADD-filtered and paying more never
  unfilters it (baseline §8 item 4, now with the contract language cited).
- **The boundary that cannot be bought**: FAA's own ADS-B privacy page
  confirms the broadcast is receivable by *"any individual with an ADS-B
  receiver"*; NBAA states LADD *"only addresses the use of data through
  FAA data systems."* Own-RF reception is outside LADD's scope by the
  FAA's own description. (No FAA primary source naming ADS-B Exchange
  specifically was found — flagged; do not cite one.) A buyer can spend
  the full §12.3 figure and hold every agreement, and still be
  contractually **forbidden** from displaying the blocked aircraft — the
  platform's receivers see them because physics is not a party to the SAA.

### 12.3 The imputed cost of replicating the standing (rates cited; hours ASSUMPTION)

No FAA or industry document states onboarding labor hours (searched;
negative documented) — so hours are **ASSUMPTION**, shown separately from
the fully-cited rates:

| Component | Hours (**ASSUMPTION**) | Rate (cited) | Range |
|---|---|---|---|
| SWIM/SCDS onboarding engineering + data governance | 160 – 480 | $197.69/hr — Guidehouse Senior Consultant II, GSA MAS GS-00F-045DA, Year-10 rates eff. 2025-01-07 (c) | $31.6k – $94.9k |
| Regulatory program design (LADD filter compliance, annual SAA renewal cycle) | 40 – 120 | $301.87/hr — Guidehouse Director III, same schedule (c) | $12.1k – $36.2k |
| Legal review (SAA terms, flow-down agreements) | 10 – 30 | $492/hr — DC average attorney hourly rate, Clio Legal Trends 2025 (b); US average $349/hr for context | $4.9k – $14.8k |
| **Imputed replication cost** | | | **≈ $48.6k – $145.9k** |

Presented as a predominantly **one-time** standing-acquisition cost with
a real recurring residue (annual SAA renewal; monthly LADD list handling
within a 5-business-day window — the platform's own weekly CUI import
discipline is the live demonstration) that is *not* separately priced —
imputing recurring hours would be a guess on a guess. GSA ceiling rates
run up to $374.67/hr (Engagement Executive) on the same schedule; the
mid-tier rate election is itself an **ASSUMPTION**, conservative in
direction.

**The §12 claim that survives hostile review:** *equivalent data access
costs a buyer $0 in FAA fees but ≈ $48.6k–$145.9k in imputed professional
labor and 2–6 months of FAA-stated lead time, renewed annually — and at
the end of it the buyer holds LADD-bound access, meaning the
blocked-aircraft visibility this platform has from its own receivers is
still not part of what they obtained.*

---

## §13. The second-brain automated-retention capability — approximation floor for an unpurchasable asset

> **Core thesis, stated first.** The second brain is the platform's
> compounding asset: a self-hosted vault of **9,579 indexed documents**
> (live index summary, read 2026-09-03 ~20:57 EDT: 8,743 inbox · 317
> sources · 221 syntheses · 128 entities · 102 business-uncategorized ·
> 64 reference), written to by **seven automated units on live timers**
> (verified via `systemctl --user list-timers` this evening: daily digest
> 23:45 ET, RSS poller, entity-tracking digest every 6 h, index scan,
> demo-archiver daily, weekly dump, weekly synthesis Sun 18:15 ET), under
> a compiled **semantic layer v1.1.0** (6 facets, 99 concepts, 424
> surface forms, 18 producing agents; the 51,317 note-to-concept edge
> count is the 08-24 sample, vintage disclosed) with a CUI/PII scrub gate
> on every ingestion path. What makes it structurally unpurchasable is
> not the software category — it is **permanent ownership plus autonomous
> accumulation**: every commercial approximation below is request-driven
> (a human asks, it answers; none runs scheduled synthesis writes over a
> private multi-domain ops corpus), and every one of them contractually
> **takes the corpus back at exit** (§13.2). The floor below prices the
> closest purchasable approximation; it does not price the asset.

### 13.1 The closest commercial approximations, priced

| Product (category) | Annual anchor (smallest honest config) | Source |
|---|---|---|
| Tettra (KM + AI answers) | **$960/yr** — $8/user/mo, published 10-user minimum (a, tettra.com/pricing) | primary |
| Notion Business (KM; full AI = Enterprise) | $1,200/yr @ 5 seats **ASSUMPTION** ($20/member/mo, (a)) | primary |
| Slite Pro (KM + AI Q&A) | $1,200/yr @ 5 seats **ASSUMPTION** ($20/u/mo, (a)) | primary |
| Confluence Premium | ≈ $1,584/yr @ 10 users monthly billing (a, Atlassian page pricing payload: $13.20/u/mo) | primary |
| Microsoft 365 Copilot (synthesis assistant) | **$360/user/yr** (a, "$30.00 user/month, paid yearly"; requires a qualifying M365 license) | primary |
| Gemini Enterprise | ≈ $252–$360+/seat/yr ("from $21/seat/mo" Business) — (b) secondary citing Google materials; no reachable public Google pricing page | secondary |
| Guru (KM; page now quote-only) | **$39,168/yr** Vendr median (range $8,159–$121,023; "Last updated Feb 2026", 167 purchases) (b) | secondary |
| Glean (enterprise AI knowledge platform) | **$98,890/yr** Vendr median (range $29,880–$208,897; Feb 2026, 174 purchases; smallest typical deployment 100–250 users) (b) | secondary |
| Bloomfire | Vendr median $158,018/yr vs. aggregator entry $6,000–$9,600/yr — spread too wide, **used cautiously, not headlined** (b) | secondary |
| Smarsh (compliance archiving) | $22,759/yr Vendr median (range $3,288–$131,276) (b) | secondary |
| Global Relay (archiving) | $6,793/yr Vendr median (range $975–$7,800) (b). The folk "$10/user/mo published" claim could **not** be confirmed — do not cite it | secondary |
| Proofpoint Essentials Professional (archive incl.) | $5.33/user/mo US MSRP (a, price-list PDF) | primary |
| Mimecast | $30,242/yr Vendr median (b); ignore Capterra's $99/u/mo outlier | secondary |
| Datadog logs (pure data retention) | **≈ $1–2/yr** for this corpus: ~1 GB / ~9.6k events (**ASSUMPTION**: ~100 KB avg/doc) at $0.10/GB ingest + Flex Logs $0.05/M events/mo to 15-month retention (a, datadoghq.com/pricing) | primary |

The Datadog line is the structural finding: **storing this corpus is
economically free (~$1–2/yr); the entire commercial price is seats,
contract minimums, and the synthesis layer** — i.e., what a buyer pays
for is the software's *answering*, and even that is request-driven.

### 13.2 Destruction-on-exit, cited from the vendors' own legal terms

- **Guru** ToS (updated 2026-03-25): "Guru will delete your Content from
  the Service promptly after ninety (90) days has elapsed from date of
  expiration or termination"; "Guru cannot recover your data once deleted
  after the 90-day period" — export only on written request ahead of the
  cutoff. (a)
- **Glean** ToS (2025-05-30 PDF): "Upon termination of the Agreement,
  Glean will delete the Customer Data stored in the Service." (a)
- **Smarsh** Services Agreement v10/25: retains Client Data up to six
  months post-termination, "Thereafter, Smarsh may delete Client Data in
  its sole discretion"; data **return** requires a separate Order Form at
  "then-current data extraction and exportation fees." (a)

Together with the baseline's data-license findings (OAG §10.4, Kpler
§13.3–13.4, ADS-B Exchange §14(d) — destruction on termination), the
pattern is complete across both data feeds *and* knowledge platforms:
**commercial terms convey access, never ownership. Exit = an export
window, then deletion.** The platform's corpus has no such clock.

### 13.3 The approximation floor

| Tier | Annual | Construction |
|---|---:|---|
| **Low** | **≈ $1,320/yr** | Tettra published 10-seat minimum ($960) + one M365 Copilot seat ($360) — a two-product composite (**ASSUMPTION**: no single low-end product offers vault + synthesis); ≈ $2,295/yr adding archiving at Global Relay's Vendr low ($975) |
| **Mid** | **≈ $39,200/yr** | Guru Vendr median — the mid-market KM-with-AI contract |
| **High** | **≈ $98,900/yr** | Glean Vendr median — the enterprise AI-knowledge-platform contract (even Glean's *lowest* transacted deal is $29,880/yr) |

**Headline: the closest purchasable approximation of the second-brain
capability runs ≈ $1.3k/yr (seat-minimum composite) to ≈ $98.9k/yr
(enterprise median) — and none of it is equivalent**: no scheduled
autonomous synthesis, no compiled private ontology, and contractual
deletion on exit. The differentiator — a permanently-owned, continuously
self-writing longitudinal corpus — remains in the baseline §8
not-purchasable list, now with the price of the nearest substitute
attached instead of a bare qualitative claim. This floor is
**approximation, not equivalence** — presented alongside, never inside,
the §10.5 platform band (it prices a different buyer question).

---

## §14. Fold-in directives and honest limits of this pass

### 14.1 What the docs-fold-in agent must change (and why)

1. **Headline change, all five verticals**: every executive summary
   (aviation-ops, corporate-travel-concierge, executive-ground-transport,
   executive-protection, platform-generic) currently cites
   "~$55,200–113,000/yr". The defensible band is now
   **≈ $55,200 – $230,400/yr** (§10.5) — the old high end understated by
   ~2× because Firehose/Spire were unpriceable then and are cited now.
   The "it is a floor because the largest equivalents are quote-only and
   excluded" sentence must be **rewritten**, not deleted: Firehose now
   carries a federal-contract anchor; ADS-B Exchange Enterprise and the
   SWIM-intermediaries remain excluded on a documented exhausted search.
2. **Per-vertical floors** (§11.5 table) replace the copy-pasted
   platform-wide figure in each vertical's economics section; the
   platform-generic doc keeps the umbrella band. Carry the
   never-sum-the-verticals rule and each vertical's reconciliation
   sentence.
3. **Prominence shift (operator steering, this pass)**: regulatory
   standing (§12) and second-brain retention (§13) are **core thesis
   material** — in each vertical doc they should lead with the
   structural-moat argument (agreement-bound access + LADD flow-down +
   own-RF exception; permanent ownership + autonomous accumulation +
   destruction-on-exit clauses) and use the dollar figures
   ($48.6k–$145.9k imputed standing; $1.3k–$98.9k retention
   approximation) as supporting evidence, not as the claim itself.
4. **Correction to propagate**: any prose implying a "2019 LADD Federal
   Register notice" or citing 49 U.S.C. § 40103 for LADD is wrong — the
   chain is Pub. L. 115-254 § 566 (49 U.S.C. § 44103 note), docket
   FAA-2011-0183 (final: 78 FR 51804), Pub. L. 118-63 § 803 (§12.2).
5. **New honest-limit rows to carry**: USSS/Navy scope caveats on the
   Firehose/Spire anchors (§10.1/§10.3); the concierge floor's PTPY low
   is ASSUMPTION-built; §12.3 hours are ASSUMPTION; §13's low tier is a
   two-product composite ASSUMPTION.

### 14.2 Honest limits of this extension

1. **Procurement anchors are scope-opaque.** The USSS Firehose and Navy
   Spire awards prove real annual dollar magnitudes but not
   seat/stream/coverage scope; government pricing may differ from
   commercial. They are floors-with-caveats, not quotes. A single real
   commercial Firehose quote would still beat them.
2. **Vendr medians are secondary** (flagged (b) throughout) with the
   usual sample-size caveats (Cirium n≈3–5; Everbridge/Guru/Glean much
   larger). Where a federal record corroborates (Cirium, §10.4), that is
   said; where it does not, the median stands alone.
3. **Currency figures left unconverted** (Safeture €, Anvil £) — no FX
   rate was sourced this pass; converting without one would violate the
   citation rule. The Anvil award's term is unconfirmed.
4. **Every imputed-hour figure is ASSUMPTION** (§12.3) — rates are cited,
   effort is judgment. No industry source states SWIM onboarding labor;
   the search for one is a documented negative.
5. **The vertical floors price adjacency, not equivalence** — stated
   per-vertical in §11 and stronger than it sounds: the honest finding of
   the whole pass is that the purchasable versions of these capabilities
   are *shallower* than the platform's (FlightStats estimates vs. OOOI;
   no TFR correlation in any EP product; no rail in any dispatch
   platform), and the ten baseline-§8 unpurchasables are untouched by
   every dollar figure in this file.
6. **This file changes no incurred-cost claim.** Actual recurring cost
   remains ≈ $22–$38/yr (baseline §3); avoided cost remains
   ≈ $2.2k–$2.7k/yr (baseline §6). Everything here is replacement-cost
   analysis for a buyer without FAA vetting, and must never be presented
   as avoided spend (baseline §9.1 governs, unchanged).

---

*Sources: `COGS_SUBSCRIPTION_REPLACEMENT_2026-09-03.md` (the 09-03
baseline, same directory — carried lines cited there);
`docs/COGS_VENDOR_COMPARISON_2026-08-18.md` (methodology + §2.1);
`docs/DATA_SOURCES.md`, `docs/LADD_CUI_HANDLING.md` (internal standing
evidence); live read-only checks of 2026-09-03 evening (`timedatectl`,
`systemctl --user list-timers`, second-brain index/semantic queries); and
the per-figure web citations inline above (vendor pages, USASpending.gov
award records, GSA MAS schedule rates, Federal Register/statute, Vendr
marketplace pages, audited Safeture filings), all accessed 2026-09-03
unless a vintage is stated. Raw fetched pages from the Firehose/SWIM
workstream are preserved in the session scratchpad for re-verification.
This file is staged but deliberately uncommitted; the operator commits
(signed) personally or not at all.*
