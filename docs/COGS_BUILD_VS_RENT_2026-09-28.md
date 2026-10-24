# CTDI Dispatch — COGS (self-hosted) vs. Subscription-Only Replacement Cost, 2026-09-28

> **Status (2026-10-15):** dated record, not re-derived. Read these before quoting it: (1) the "cloud-LLM spend is a measured $0" claims (§0, §1 line "Cloud LLM inference") are wrong in scope: a weekly Claude Opus job runs via the Claude CLI and the agents run on vendor plans, and the SR-1 log records configured model labels, not inference (see `docs/COST_STRUCTURE.md` §1 correction); (2) Column 1 has no line for the Proton Drive off-box backup, live since 2026-10-12 (`docs/BACKUP.md`); (3) the internal totals disagree: the "Monthly equivalent ≈ $5,125 – $11,820 / mo" line and the two later "$61.5k–$141.8k" figures disagree with the §0/§2 headline range ($61,529 – $334,955/yr; $5,127 – $27,913/mo); (4) live inputs have moved (38–40 running containers on 2026-10-15, varies with oneshot jobs; not 35). The current cost picture and its open [UNVERIFIED] lines are in `docs/COST_STRUCTURE.md`.

> **Internal planning estimate — NOT investor materials, NOT a vendor quote,
> NOT an appraisal.** Same posture and HOLD status as
> `docs/COST_STRUCTURE.md` §2 and `docs/COGS_VENDOR_COMPARISON_2026-08-18.md`:
> promotion of anything here into `investor-materials/` is the founder's call.
> Every commercial price is carried from the 2026-08-18 vendor-comparison pass
> (each cited to a real vendor page/date there) or newly estimated this pass
> and marked **[EST]** with its basis. **Where a figure is estimated it is
> labelled; precise vendor figures are never invented.**
>
> **This document folds ~6 weeks of new platform work into the two-column
> model.** It supersedes the 2026-08-18 comparison's totals for the *live*
> deployment (that pass was written against the SQLite-era codebase, before
> the Postgres cutover, the 954k-edge geometric graph, OOOI-authority,
> train parity, the codeshare/FBO/DC-metro layers, tamper-evident integrity,
> and the webhook receiver). Sections A–E of the 2026-08-18 doc remain the
> line-by-line source of record for the pricing methodology.

**Grounding basis (live-verified 2026-09-28):** repo `HEAD` `78660147`;
Postgres database **11 GB**; `semantic_note_derivations` **1,008,002**
(of which `geometry_edges` **954,148**); `flight_events` **913,669**;
`train_events` **688,047**; `vault_documents` **13,169**; `codeshare_map`
**341**; **35** running containers; SR-1 usage log
(`/var/lib/corporatetraveldc/api-usage.csv`) **61,417 rows**,
2026-07-09 → 2026-09-28 (81.3 days), **0 cloud-LLM rows**
(`grep -icE 'claude|anthropic|gpt|openai|sonnet|haiku|opus'` = 0). See
`docs/CODEBASE_REFERENCE_2026-09-28.md` for the full subsystem inventory.

---

## 0. Headline — the two columns

| | Monthly | Annualized |
|---|---:|---:|
| **COLUMN 1 — Self-hosted COGS** (hardware amortization + power + domain share) | **≈ $16 – $25 / mo** | **≈ $190 – $305 / yr** |
| — strictly-marginal recurring cash only (power + domain share) | ≈ $3 – $4 / mo | ≈ $36 – $50 / yr |
| — all-in incl. shared ProtonMail relay (see §1) | ≈ $20 – $38 / mo | ≈ $245 – $460 / yr |
| **COLUMN 2 — Subscription-only replacement (live verticals)** | **≈ $5,127 – $27,913 / mo** | **≈ $61,529 – $334,955 / yr** |
| — deployment-ready incl. **code-complete, hardware-gated** maritime/AIS tier | ≈ $9,711 – $32,500 / mo | **≈ $116,529 – $389,955 / yr** |
| — *broker-comp range: LOW = conservative, defensible floor (Cirium flight-data $30,530, still true); HIGH = potentially-as-high-as the FlightAware Firehose / USSS-contract comp ($223,641) for the SWIM-class capability with no public SKU (§2.1 line 1).* | | |

**Ratio: renting the equivalent runs from roughly 250× the self-hosted cost at
the conservative floor to ~1,600× at the Firehose/USSS ceiling comp** — and (§4) the platform's most operationally valuable capabilities
**cannot be bought at any price**, so the rent column is a floor on a strictly
thinner product.

**Cloud-LLM and paid-data-feed spend on the self-hosted side is a measured
$0**, not an estimate: 61,417 SR-1 rows, zero cloud model rows,
`ANTHROPIC_FALLBACK_ENABLED=false`, and every ingested feed is free-to-anyone
government data, a free/reciprocal account, or a scraped public endpoint.

---

## 1. COLUMN 1 — Self-hosted COGS (what it actually costs to run as built)

Single-node deployment (`hostname` = `corporatetraveldc-dispatch`, 4 cores).
Figures carry forward from `COGS_VENDOR_COMPARISON_2026-08-18.md` §2.7–2.8
(power re-derived there from EIA + Tom's Hardware/RTL-SDR datasheets) and are
unchanged by the last 6 weeks of *software* work — no new paid dependency was
introduced.

| Line item | Monthly | Basis / note |
|---|---:|---|
| **Hardware amortization** (Pi 5 16GB + NVMe + case/PSU + 2× SDR reference RF buildout, ≈ **$765** one-time, §2.7) | **$12.75 – $21.25** | $765 over 60 mo ($12.75) to 36 mo ($21.25). One-time CapEx, near-zero marginal thereafter. |
| **Electricity** | **$1.92 – $3.25** | $23.36 – $39.17/yr (10.5 – 17.6 W continuous × 8,760 h × $0.2540/kWh, EIA Table 5.6.A DC residential May 2026). Sourced, not the old "$50–100/yr" placeholder. |
| **Domain (`example.com`, allocated share)** | **≈ $1** *[EST]* | ~$10–15/yr `.com` renewal; shared business cost across 9 public hostnames, not platform-specific. Registrar/renewal not recorded in repo — flagged UNVERIFIED in §2.8. |
| **Cloudflare** (Tunnel + WAF + DDoS, 9 hostnames) | **$0** | Free plan; WAF + unmetered DDoS included (§2.25). |
| **Tailscale** (tailnet mesh, ≤6 users) | **$0** | Personal free tier (§2.25). |
| **ntfy** (14-topic alert catalog) | **$0** | Self-hosted container on the box. |
| **Paid data feeds / API keys actually in use** | **$0** | Measured: all SWIM/NOTAM (FAA, free), METAR/NWS/NWWS-OI (NOAA public domain), TFR/NAS/ATCSCC/registries (free), Amtrak (free community API), ADS-B vendor accounts (reciprocal barter, no cash), OSINT (self-polled RSS). |
| **Cloud LLM inference** | **$0** | Measured — 0 cloud rows in 61,417 SR-1 logs; local Qwen3-4B / llama.cpp only. |
| **Network (business fiber)** | **$0 marginal** | Pre-existing business expense, not marginal to the platform (§2.8) — though the box does push ~2.7 GB/day of SWIM ingest across it. |
| **Subtotal — platform-attributable** | **≈ $16 – $25 / mo** | |
| **ProtonMail (Bridge relay)** — shared, not platform-specific | **$4 – $13** *[EST]* | Proton Mail Bridge requires a paid Proton plan: Mail Plus ≈ $4–5/mo to Unlimited ≈ $13/mo (Proton published plans, knowledge-cutoff Jan 2026 — re-check before quoting). Shared business mail relay, listed for completeness per the task. |
| **All-in incl. shared ProtonMail** | **≈ $20 – $38 / mo** | |

**Notes.**
- The 6 weeks of new work (Postgres, geometric graph, OOOI-authority,
  codeshare/FBO/DC-metro, train parity, integrity system, webhook receiver)
  **added zero recurring cash cost** on the self-hosted side. Postgres, the
  graph, GTFS-RT, and the OSM/Overpass FBO polygons all run in-box on the same
  amortized hardware and the same electricity draw. This is the whole point of
  the column: the marginal cost of the new capability is essentially the
  compute headroom already paid for.
- The FBO polygon / DC-metro aeroway layer was fetched free from **OpenStreetMap
  via Overpass** (`data/dc-metro-aeroway.geojson`, 168 KB, KDCA/KIAD/KBWI apron/
  terminal/gate footprints) — the commercial-GIS alternative is priced in
  Column 2, but here it is $0.

---

## 2. COLUMN 2 — Subscription-only replacement (rent the commercial equivalent)

**Method (carried from `COGS_VENDOR_COMPARISON_2026-08-18.md` §2.17):**
within a category, vendors are substitutes → take **one**, never sum. Across
categories, products are complements → these **sum**. Every price cites its
own source for its own scope. Where an assumption forks the total, both bounds
are shown.

### 2.1 Commodity-infrastructure layer

| # | Subsystem (what's deployed) | Commercial rent-equivalent (vendor + tier) | Annual low | Annual high | Note |
|---|---|---|---:|---:|---|
| 1 | **SWIM-class flight data** (FDPS/TBFM/ITWS/FNS push; 913,669 `flight_events`, 1,300+ airlines) | **Broker-comp range (floor → ceiling).** FLOOR **Cirium** FlightStats $30,530/yr — conservative, defensible, still true (priceable schedule subset only). CEILING **FlightAware Firehose** — the only true equivalent (surface positions + obfuscated/blocked flights), **hard to price: per-customer, no public SKU**, anchored to the real **US Secret Service Firehose contract award** ($223,641) | $30,530 | $223,641 | Real-estate/aviation-broker logic: floor = what you can *definitely* replace it for (Cirium, still true); ceiling = *potentially as high as* the USSS-contract Firehose price. **⚠ SCOPE: the USSS $223,641 covers the FLIGHT-DATA FEED ALONE — one line item, NOT the whole stack.** Every other line (intelligence/correlation, graph/vector, Postgres, weather, NOTAM, rail, OSINT, maritime) stacks *on top* of it. |
| 2 | **Consumer/prosumer ADS-B** (own 1090 MHz SDR) | FA Enterprise $1,199 + FR24 Business $500 + RadarBox $399 + PlaneFinder $20 (one substitute → all four) | $399 | $2,118 | Reciprocal today ($0 cash); this is the buy-back price. 3 of 4 are search-index snapshots after 403. |
| 3 | **Weather** (METAR + `api.weather.gov`) | Visual Crossing $420/yr → IBM/Weather Company Standard $6,000/yr | $420 | $6,000 | NWWS-OI warning-push equivalent is unpriced (no discrete SKU). |
| 4 | **NOTAM** (FNS → 314 facilities) | Notamify Pro $298.80/yr; ForeFlight Starter $130/yr | $130 | $299 | Genuinely buyable and cheap. |
| 5 | **Passenger rail — GTFS-RT train parity** *(NEW: phase/ETA state, schema 0064, `amtrak-tracker` live, 688,047 `train_events`)* | Parse.bot Amtrak API $30–100/mo → findtrain.com ≈ €3,000/yr | $360 | $3,000 | Now a real realtime-parity vertical, not just a poll. Amtrak static GTFS is free but has no realtime component, so it is not a substitute. |
| 6 | **OSINT / RSS** (22 scopes, ~270 outlets) | NewsCatcher Starter $50/mo; Event Registry 5K $90/mo (scope-matched down) | $600 | $1,080 | |
| 7 | **LLM inference** *(model swapped phi3-mini → **Qwen3-4B** this window)* — ~190,300 real model calls/yr (SR-1: 42,391 non-deterministic calls / 81.3 days ≈ 521/day) | **Claude Haiku 4.5 @ $1/$5 per MTok** (fair peer for a 4B local model) | $550 | $1,070 | *[EST]* — token counts modelled per §2.19 method, scaled +24% for the higher live call rate vs. the 2026-08-18 pass (153k → 190k calls/yr). Prompt-caching/batch could cut ~50%. |
| 8 | **Vault storage + sync** (self-hosted Nextcloud; 13,169 docs, ~1 GB) | Managed Nextcloud (Hetzner Storage Share / IONOS) + Obsidian Sync $8–10/mo | $180 | $480 | *[EST]* — up from the 2026-08-18 $180 Box/Dropbox line to reflect managed-Nextcloud + sync and the corpus more than doubling (6k → 13.2k docs). Seat-based, so largely volume-insensitive. |
| 9 | **Push delivery** (self-hosted ntfy, 14 topics) | ntfy.sh Pro $10/mo → Business $20/mo | $120 | $240 | Pro's 10 reserved topics is below the 14 in use. |
| 10 | **Managed Postgres** *(NEW: SQLite→Postgres cutover complete; 11 GB, 65 migrations, 34+ containers as concurrent writers)* | Managed OLTP+analytics Postgres — Neon/Supabase Pro ≈ $25–70/mo (low) → AWS RDS production instance (db.m6g.large + storage + backups) ≈ $150–400/mo (realistic for 24/7 multi-writer load w/ sustained geometric-reasoning writes) | $600 | $4,800 | *[EST]* — basis: Neon/Supabase published Pro tiers (low); AWS RDS on-demand pricing for a ~2–4 vCPU / 8–16 GB instance + ~20 GB gp3 + automated backups (high). The 2026-08-18 pass had **no DB line** (SQLite-era). |
| 11 | **Managed graph / vector infra + compile compute** *(NEW: geometric reasoning all 4 phases live; 1,008,002 derivations, 954,148 geometry edges, nightly compile)* | **Neo4j AuraDB Professional** (1M+ relationships, ~2–8 GB) ≈ $300–1,000/mo + **Pinecone/Weaviate** vector tier ≈ $50–70/mo + managed batch compute for the nightly Phase-1 edge build (OOM-sensitive) ≈ $50–200/mo | $4,800 | $15,240 | *[EST]* — basis: Neo4j Aura published per-GB Professional pricing; Pinecone Standard/Weaviate Flex published tiers; cloud batch-compute for a ~1M-edge nightly recompile. **The single biggest net-new rent line from this window.** The 2026-08-18 pass priced only a $540–600/yr vector DB against 39,744 concept edges — the graph has since grown ~25× and gained per-instance geometric + causal + cluster layers. |
| 12 | **GIS / geofencing** *(NEW: FBO polygon layer + DC-metro aeroway from OSM/Overpass = $0 self-hosted)* | Commercial airport-surface geometry + geofencing: Mapbox/HERE geofencing (low) → licensed aviation AMDB / enterprise GIS (high, often quote-only) | $600 | $6,000 | *[EST]* — basis: Mapbox/Google/HERE published geofencing tiers (low); licensed airport-mapping databases are enterprise/quote-only (high). OSM/Overpass makes this **$0** on the self-hosted side. |
| 13 | **Webhook / integration receiver** *(NEW: LimoAnywhere reservations + RingCentral + 3CX; auto-tracks flights/trains)* | iPaaS: Zapier/Make/Workato for inbound webhook → watchlist automation | $240 | $600 | *[EST]* — basis: Zapier/Make published mid-tier plans at this task volume. Self-hosted FastAPI route = $0. |
| — | **Codeshare mapping** (marketing↔operating carrier, 341 rows) | Bundled inside OAG/Cirium schedule products | $0 | $0 | Folded into line 1 (Cirium); no marginal add. |
| | **Commodity-infrastructure subtotal** | | **≈ $39,529** | **≈ $264,568** | |

### 2.2 Intelligence-automation layer (carried from Section E)

| Subsystem | Commercial rent-equivalent | Annual low | Annual high |
|---|---|---:|---:|
| **Cross-vertical correlation** (multi-domain signal detection over flight/rail/weather/OSINT) | One CI platform: Dataminr $22,000 (cheapest w/ any real-time claim) → Recorded Future $70,375 (closest capability) — Vendr medians | $22,000 | $70,375 |
| Security stack (rootless Podman/SELinux/Tailscale/CF/GPG manifest/tiered auth) | Scale-matched free tiers + Datadog log mgmt | $0 | $12 |
| **Entity-tracking auto-promotion** (threshold + corroboration + human-review gate + silence detection) | **NO PRODUCT — unbuyable** | $0 | $0 |
| **Historical / owned longitudinal corpus** | **Unbuyable — OAG/Kpler/ADS-B-X contractually destroy licensed data on termination** | $0 | $0 |
| **Intelligence-automation subtotal** | | **≈ $22,000** | **≈ $70,387** |

### 2.3 Column 2 total (live verticals)

| | Annual low | Annual high |
|---|---:|---:|
| Commodity infrastructure (§2.1) | $39,529 | $264,568 |
| Intelligence-automation (§2.2) | $22,000 | $70,387 |
| TOTAL — currently powered on (excludes hardware-gated vessel) | ≈ $61,529 | ≈ $334,955 |
| + maritime / AIS *(code-complete, hardware-gated: an AIS dongle or AISHub ID away)* | + $55,000 | + $55,000 |
| **TOTAL — DEPLOYMENT-READY** — broker-comp range: conservative Cirium **FLOOR** → Firehose/USSS **CEILING** | **≈ $116,529** *(defensible floor)* | **≈ $389,955** *(potentially as high as)* |
| *NB: the $223,641 USSS/Firehose figure is the flight-data LINE comp only (§2.1 line 1) — the $389,955 ceiling is that line PLUS every other line stacked on top, not the USSS number alone* | | |
| *only exclusion:* drone/UTM — genuinely capability-gated (USS poller is a stub), not counted | | — |

**Monthly equivalent: ≈ $5,125 – $11,820 / mo** (live verticals).

### 2.4 Dedicated feeder-only kit — what the antenna earns (added 2026-10-22)

The Air Tiers Analyst Desk note ("The Terminal That Doesn't Stop the Radio", 2026-10-22) argues the receiver is the
cheapest instrument in the room. This section prices it: a **feeder-only** box — no platform, no SWIM, nothing but a
1090 MHz receiver feeding the networks — and what that feeding unlocks at list price. A 4 GB Pi 5 is enough for this
use case (readsb + the feeder clients); the 16 GB unit in §1 is sized for the whole platform, not for feeding.

**Kit (one-time).** Prices checked 2026-10-22.

| Item | MSRP / official store | US street (in stock) | Source |
|---|---:|---:|---|
| Raspberry Pi 5, 4 GB | $85 | $110 | MSRP: Tom's Hardware price table, 2026-10-02 (third 2026 increase, memory costs); street: PiShop.us, Seeed Studio |
| Official 27 W USB-C PSU | $12 | $12–13 | Seeed Studio $12.00; PiShop.us $12.95 |
| Official case (fan) or Active Cooler | $10 | $10–12 | Raspberry Pi launch price; Micro Center / GigaParts |
| microSD 32 GB, A1 | ≈ $10 | ≈ $10 | *estimate, not sourced* |
| FlightAware Pro Stick Plus (SDR + 1090 MHz filter) | $45.99 | $42–46 | flightaware.store |
| FlightAware 1090 MHz antenna (outdoor) | $49.99 | $49.99 | flightaware.store |
| N-male → SMA coax jumper | ≈ $20 | ≈ $20 | *estimate, not sourced; length-dependent* |
| **Kit total** | **≈ $233** | **≈ $259** | |

FlightAware's own FlightFeeder Basic bundle (Pi Zero W, Pro Stick Plus, indoor antenna) is $119.99 — cheaper, but an
indoor antenna and a Zero W cut range and cannot run several feeder clients at once; the Pi 5 kit above is the
like-for-like for six networks.

**What feeding unlocks, at list price (per year).**

| Network | Tier given to feeders | List price of that tier | Source / confidence |
|---|---|---:|---|
| FlightAware | Enterprise account (while feeding) | $1,199.40 ($99.95/mo, 1 user) | FlightAware multi-user pricing page, prices dated 2023-01-17 — *official but 2023* |
| FlightAware AeroAPI | Personal tier: first **$10/mo waived** for ADS-B feeders (vs $5 standard) | up to $120 | flightaware.com AeroAPI page, Oct 2026 — *official*. Our use: $0.14/period → $0 actual |
| Flightradar24 | Business subscription | ≈ $499.99 | third-party FAQ, Sep 2026 — *unofficial; FR24 does not publish it* |
| AirNav Radar | Business (valid while the station connects at least every 15 days) | $399.50 (web) / $599.99 (iOS in-app) | airnavradar.com/subscribe — *official* |
| PlaneFinder | premium (feeder) | ≈ $20 | carried from §2.1 row 2 (2026-09-28 snapshot) |
| airplanes.live | Pro tier | no published price | unlocked 2026-10-22 once the feed carried its feeder ID |
| OpenSky Network | Active-feeder API tier (≥ 30 % monthly uptime): 8,000 credits/day vs 4,000 registered / 400 anonymous; `/states/own` (own receiver) free and unrestricted; value is visibility into the global sensor network, not an operational dependency | no published price | openskynetwork.github.io/opensky-api REST docs, 2026-10-22 — *official* |
| Airframes.io (ACARS/VDL2) | Feeder Pro from 2026-10-23 ("earned free" by an active station) = the Pro plan: 50,000 API req/day, 90-day message history, outbound webhooks, station analytics | $144 ($12/mo) | app.airframes.io/pricing, 2026-10-22 — *official*. Business ($39/mo: 250k req/day, 1-yr history, MQTT) is not earned by feeding |
| ACARS Drama | Jumpseat, complimentary as a "Sky Drama Farmer" (active 2026-10-22): archive search, aircraft lookup, station reports, email alerts and digests, REST API | $79 ($7.99/mo) | Jumpseat billing page, 2026-10-22 — *official* |
| **Total unlocked, list** | | **≈ $2,462 / yr** | of which ≈ $1,942 from official vendor pages |

AirNav also issues feeders a free On Demand API key (100 credits, monthly) — not counted, it is metered data, not a tier.

**On this platform the kit's marginal cost is $0.** The same two receivers already amortized in §1 (the 2× SDR
reference RF buildout inside the ≈ $765 hardware line) earn every tier above. One 1090 MHz SDR and antenna feed
ultrafeeder, which fans its Beast stream out to six networks (airplanes.live directly; FlightAware, Flightradar24,
PlaneFinder, AirNav Radar and OpenSky through their own client containers); one VHF SDR feeds acarsrouter, which
forwards ACARS/VDL2 to Airframes.io and ACARS Drama. Eight feeds, two radios, no extra hardware — the box is
repurposed, not extended. The stand-alone kit above is what a customer or a second site would buy to earn the same
tiers without the platform.

**Two buyers, two comparisons.** Personal plans are licensed for personal, non-commercial use only (FR24 states this
for Free/Silver/Gold; AirNav prices Business as the professional tier), so the honest comparison depends on who is
buying.

| | "Monitor my grid" enthusiast | Protective detail / FBO operator |
|---|---|---|
| What they'd otherwise buy | Consumer tiers: FR24 Gold ≈ $35–40/yr + AirNav Pilot $79.50/yr (Pilot already includes ad-free) | The commercial tiers they are licensed to use: FA Enterprise $1,199 + FR24 Business ≈ $500 + AirNav Business $399.50 + Airframes Pro $144 + ACARS Drama Jumpseat $79 (+ AeroAPI headroom $120) |
| Annual list price avoided | **≈ $115–120** | **≈ $2,442 per seat** |
| Kit (MSRP → street) | $233 → $259 | $233 → $259 |
| Payback | ≈ 1.9–2.3 years on consumer tiers alone — but the feeder gets the *business* tiers (≈ $2.5k list) anyway | **≈ 5–5.5 weeks** |
| Running cost | a few watts continuous (*estimate, not measured*); a few dollars a year at the §1 power basis | same, per receiver |
| What they also get that no tier sells | their own unfiltered local picture (LADD-listed and blocked aircraft are on their own receiver; the public sites hide them) | the same, plus the counter-surveillance use in the Air Tiers note: the band the principal leaks on is the band the detail listens on |

Every one of these tiers is **feeder-contingent**: they lapse if the station stops feeding (AirNav's 15-day rule is
the tightest). A dead feeder is therefore a cost event, not just a data gap. Today only ultrafeeder's own outbound links are
watched (adsb-link-watchdog). The vendors cover most of the rest: FlightAware, Flightradar24 and AirNav Radar
email the operator after 1–6 h offline, and the airplanes.live app has "notify when offline" on. Uncovered:
PlaneFinder, OpenSky, Airframes.io and ACARS Drama — the gap a platform-side silence check would close.

Not established here: FR24 Business and PlaneFinder list prices are not published by the vendors (third-party and
snapshot figures); FlightAware Enterprise list is the 2023 official table. Re-verify all three at the next
August-rigor COGS refresh.

---

## 3. Biggest single line-item deltas

**Largest lines in absolute terms (dominate the rent column):**

1. **Cross-vertical correlation CI platform — $22,000 – $70,375/yr.** Still the
   single largest line. Unchanged this window, but note **not one** of the ten
   CI vendors ingests flight/rail/weather operational feeds — the correlation
   substrate itself would still have to be built on top.
2. **SWIM-class flight data (Cirium anchor) — $30,530/yr, flat.** The largest
   commodity line. **FlightAware Firehose** — the only equivalent covering the
   platform's surface positions and blocked-flight visibility — remains
   **UNPRICEABLE**, so this line is a floor, not a ceiling.

**Largest NET-NEW deltas introduced by the last 6 weeks of work:**

3. **Managed graph/vector infra + compile compute — +$4,800 – $15,240/yr [EST].**
   The biggest net-new rent line. Direct consequence of the geometric-reasoning
   buildout: the graph went from 39,744 concept edges (Aug baseline) to
   **1,008,002 derivations / 954,148 geometry edges** — a ~25× growth that
   pushes it out of "$540/yr vector DB" territory into managed graph-DB
   (Neo4j Aura) + vector + nightly batch-compute territory.
4. **Managed Postgres — +$600 – $4,800/yr [EST].** Brand-new line; the Aug
   baseline was SQLite-era ($0 DB line). An 11 GB, 65-migration, 34-writer
   production Postgres has a real managed-service floor.
5. **GIS/geofencing — +$600 – $6,000/yr [EST], avoided entirely via OSM/Overpass.**
   The FBO polygon + DC-metro aeroway layer is the clearest new "build = free,
   rent = real money" case this window.
6. **Train parity (GTFS-RT) — now a priced parity vertical ($360 – $3,000/yr)**
   rather than an incidental free poll, reflecting the schema-0064 phase/ETA
   buildout and the live `amtrak-tracker`.

---

## 4. Capabilities that cannot be rented at any price (rent column understates)

The new work added to the already-unbuyable set. These carry **$0 in the rent
column not because they are free, but because no vendor sells them** — so the
$61.5k–$141.8k rent figure buys a strictly *thinner* platform:

- **TFMS / TBFM / ITWS** — NAS flow programs, arrival metering, terminal
  wind-shear. No commercial product at any price (FAA-vetted SWIM only).
- **LADD / blocked-aircraft visibility** — even Firehose only offers
  *obfuscated* blocked flights; own-RF is not FAA-source-derived so is not
  LADD-bound. Paying more makes this strictly worse.
- **Receive-side ACARS / VDL-M2** — SITA/Collins sell the send side only.
- **OOOI source-authority arbitration** *(NEW, schema 0065)* — no SKU sells
  cross-source (SWIM-validator-of-record vs. ACARS/ADS-B/FIDS) milestone
  arbitration; the underlying OOOI events would come only via unpriceable
  Firehose.
- **Signed whole-tree manifest with an execution gate** *(hardened this window
  with tamper-evident hash-chain audit, schema 0062)* — Chainguard/Sigstore/
  GitHub Attestations/Docker Scout all operate on images/SBOMs, none signs a
  source tree and refuses to execute on mismatch.
- **SR-2 per-skill content-hash execution gate** — no commercial equivalent.
- **Threshold-based entity auto-promotion with corroboration + human-review +
  silence/embargo detection** — zero of ten CI vendors document any of it.
- **A permanently-owned longitudinal corpus** — OAG §10.4 (reaches
  derivatives), Kpler §13.3-13.4, ADS-B Exchange §14(d) all contractually
  require destruction of licensed data on termination with written
  certification. *You cannot own this even if you pay.*

---

## 5. What this does NOT establish

This is a **replacement-cost** model, not a valuation and not a revenue claim.
The productization gaps, the deterministic-fallback rate, and the thin runsheet
noted in `COGS_VENDOR_COMPARISON_2026-08-18.md` §3/§4 still apply, as does the
**current integrity hazard** (`CODEBASE_REFERENCE_2026-09-28.md` §9.2: the
signed manifest is stale and ~23 verified-exec units are failing until re-signed
+ redeployed). The defensible claim is narrow and holds: **the recurring cost
base is near zero (~$16–$25/mo self-hosted) while the commercial replacement
cost is ~$61.5k–$141.8k/yr, and the platform's most valuable capabilities are
not purchasable at any price.**

---

*Compiled 2026-09-28 against HEAD `78660147` and live Postgres/SR-1 state.
Pricing methodology and per-vendor citations: `COGS_VENDOR_COMPARISON_2026-08-18.md`
Sections A–E. Estimates introduced this pass are marked **[EST]** with basis.
Not committed — staged for the operator's review.*
