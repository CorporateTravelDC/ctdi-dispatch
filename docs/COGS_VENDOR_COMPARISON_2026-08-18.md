# CorporateTravelDC — Data-Feed Cost-Avoidance Valuation (Vendor Comparison), 2026-08-18

> **Internal planning estimate — NOT investor materials, NOT a vendor quote,
> NOT an appraisal.** Same posture as `docs/COST_STRUCTURE.md`: all figures
> are planning estimates as of **2026-08-18** (`~` = approximate; ranges are
> deliberate). Every commercial price used here is cited to a real vendor
> page with the date checked; every figure that depends on a business
> judgement (scope match, discount rate, risk weighting) is flagged as an
> **ASSUMPTION** rather than silently chosen. Promotion of anything here
> into `investor-materials/` is the founder's call — the existing HOLD on
> `COST_STRUCTURE.md` §2 is respected and extended to this document. This
> file is intentionally left **uncommitted**; the operator commits
> personally or not at all.
>
> **This document deliberately does NOT start from
> `docs/DEPLOYMENT_COST_PROJECTION_2026-08-18.md`.** That doc prices the
> platform by *engineering replacement effort* (LOC → person-months → labor
> cost). This one prices it by *avoided commercial data cost* — a different
> basis, derived independently, and only compared to the effort figure in §6
> after both numbers exist. Neither is a cross-check on the other; they are
> different questions that happen to be asked about the same asset.

---

> ## ⚠️ INDEPENDENT RE-VERIFICATION — 2026-08-19 ~07:00 EDT
>
> This document was re-checked line-by-line against live system state by a
> second pass roughly 13.5 hours after it was written. Findings, in order of
> materiality:
>
> 1. ~~**This document contains no cost-avoidance figure.**~~
>    **RESOLVED 2026-08-19 ~07:45 EDT — §2 through §6 have now been written**
>    against real, cited, dated vendor pricing, at the operator's request.
>    §2 is split into **Section A** (§2.2–2.8, this instance, live — the
>    only basis for this deployment's avoided-cost arithmetic), **Section B**
>    (§2.9–2.11, global build-out ceiling, explicitly *not* implemented here
>    and *not* summed into anything), **Section C** (§2.12–2.16, added
>    2026-08-19 ~11:15 EDT — the subscription-only counterfactual in which
>    the platform pays cash for everything and shares no data with any third
>    party, at identical fidelity), and **Section D** (§2.17–2.21, added
>    2026-08-19 ~13:00 EDT — full multi-vertical subscription-replication
>    COGS, which **supersedes Section C's narrower framing**). Headline
>    results:
>    - **Section A defensible avoided cost ≈ $2,159 – $2,655 / yr net**,
>      against **≈ $23 – $39 / yr** of actual recurring cost (electricity,
>      derived in §2.7 from EIA and Tom's Hardware/RTL-SDR datasheet inputs)
>      and **≈ $765** of one-time hardware.
>    - **The governing finding is §2.1:** FAA SWIM is free of charge, NOAA
>      data is public domain, and FAA NOTAM data is free. **Most of what
>      this platform ingests costs nothing for anyone to obtain**, so the
>      avoided cost is an *integration* cost, not a data-licence cost — and
>      the cost-avoidance thesis therefore **cannot support the $65k–$100k
>      band it was offered to justify** (§6).
>    - ~60–73 % of even that figure is **reciprocal barter** for our own
>      ADS-B data, not cost avoided (§2.5).
>    - The largest true equivalents (FlightAware Firehose, Cirium, Spire)
>      are **quote-only and remain UNPRICEABLE**; they are excluded from
>      every total rather than guessed (§2.4).
>    - **Section C (§2.16): a strict no-data-sharing policy would cost
>      $399 – $2,118 / yr**, taking actual recurring cost from ≈$23–$39/yr
>      to **≈$422–$2,157/yr**. All of that delta is ADS-B vendor accounts;
>      **8 of 10 feed groups are policy-proof.** But the money buys a
>      *filtered global viewing account*, not the *unfiltered local
>      reception* that lapses — so Section C is simultaneously an upper
>      bound on cash **and** a capability downgrade (§2.13).
>    - **Section D (§2.21) — the real answer to "what would it cost to buy
>      all of this?": ≈ $33,700 – $43,100 / yr** for the live verticals, or
>      **up to ≈ $98,100 / yr** including the documented-but-unbuilt maritime
>      tier. That is a **floor**: FlightAware Firehose (the only vendor
>      covering surface positions) is **unpriceable**, and enterprise
>      actual-buyer data runs far above published tiers. **Six capabilities
>      cannot be bought at any price** (§2.20) — TFMS, TBFM, ITWS,
>      LADD/blocked-aircraft visibility, receive-side ACARS, and scheduled
>      LLM briefs over a private corpus with an auto-built concept graph.
>      Section D corrects Section C's "8 of 10 categories are policy-proof
>      / $0" framing, which was right for *ending barter* and wrong for
>      *replicating the platform commercially*.
>    - **Section E (§2.22–2.28), added 2026-08-19 ~14:30 EDT — corrects
>      Section D rows 14–16**, which priced the intelligence-automation layer
>      as three commodity SKUs ($1,163–$1,642/yr). Correctly scoped it is
>      **$22,623 – $71,429/yr — a 19× to 44× undervaluation.** Keeping the
>      two separable: **commodity infrastructure $32,559–$41,467** vs.
>      **intelligence-automation capability $22,623–$71,429**, combined
>      **$55,182–$112,896** (or **$167,896** including the not-live maritime
>      tier). Four further capabilities are unpurchasable at any price —
>      most importantly a **permanently-owned corpus**: OAG §10.4, Kpler
>      §13.3–13.4 and ADS-B Exchange §14(d) all contractually require
>      **destruction of licensed data on termination, with written
>      certification**. *You cannot own this even if you pay.*
> 2. **§1.1's SWIM verdicts have flipped.** Five of the six SWIM push feeds
>    (FDPS, TFMS, STDDS, TBFM, ITWS) have been **dead since 2026-08-18
>    23:08 EDT** — their containers were stopped and never restarted after
>    the 21:19 host reboot. Verified three independent ways:
>    `systemctl --user list-units 'corporatetraveldc-ingest*' --all` shows
>    all five `inactive (dead)`; `feed_state` shows `push:{fdps,itws,stdds,
>    tbfm,tfms}` last-fetch 7.8–8.0 h stale; and the platform's own
>    `curl -s http://127.0.0.1:8000/healthz` returns
>    `{"status":"degraded","reason":"Stale feeds: push:fdps, push:itws,
>    push:stdds, push:tbfm, push:tfms"}`. Only `push:fns` (NOTAM) is still
>    live. **The §1.2 throughput table — declared "the single most
>    load-bearing input in §2" — is measuring feeds that are currently off.**
> 3. **§1.3 finding #1 (ACARS/VDL dead) is no longer true.** `acarshub/
>    messages.db` `MAX(msg_time)` = 1787137090 = **2026-08-19 10:58:10 UTC
>    (now)**. ACARS/VDL reception recovered. Note the row count *fell*
>    (41,331 → 38,536), i.e. that table is a rolling window, not a
>    cumulative total — the doc reads it as a total.
> 4. **§1.3 finding #3 (METAR degraded) is no longer true.** `feed_state`
>    `metar` shows `consecutive_failures = 0`, last fetch 0.0 h ago.
> 5. **§1.5's "100 % local inference" claim is VERIFIED TRUE** — the
>    strongest-evidenced claim in the file. See the inline note at §1.5.
> 6. Several §1.1/§1.5 counts have drifted (all cited inline below). None
>    of the drift is large; the SWIM outage and the ACARS reversal are the
>    only material changes.
>
> **Nothing in this banner is a new cost figure.** No cost figure exists in
> this document to correct.

---

## 0. What was asked, and the posture taken

The operator's stated (informal, previously unrecorded) valuation basis for
the **$65,000–$100,000** figure is:

> the platform ingests real-time operational data directly from authoritative
> sources instead of paying a commercial data middleman, so it is worth the
> **avoided cost** of those subscriptions, **plus** the automation built on
> top, **plus** privacy/data-sovereignty value.

That logic was **verified adversarially**, not accepted. Specifically:

1. Every feed was checked against the **running system** — live row counts,
   live timestamps, live container logs, live feed-health state — not against
   what the docs claim. Several documented-as-healthy feeds are **not**
   healthy right now (§1.3).
2. Commercial prices were researched from **vendor pricing pages**, with URLs
   and dates checked (§2). No price in this document is invented.
3. Scope was matched **downward**, not upward: where this platform replicates
   only part of a vendor's product, only that part is credited (§2.6).
4. The cost-avoidance framing itself was stress-tested, and it has a
   **serious structural weakness** that is stated plainly rather than buried
   (§5.1).

**Provenance of the prior $65k–$100k figure: searched, still not found.**
Independently re-checked tonight against the second-brain vault index
(`/var/lib/corporatetraveldc/second_brain_index.db`, 5,761 vault documents /
4,992 FTS-indexed notes): no note matches `avoided cost`, `FlightAware`,
`valuation`, or `vendor` in a valuation context. This confirms the same
negative result reported in `DEPLOYMENT_COST_PROJECTION_2026-08-18.md` §1.
**The original methodology is genuinely unrecorded**; this document
reconstructs a defensible version of it rather than recovering the original.

> **[2026-08-19] Conclusion stands; the supporting statement is overstated.**
> Vault size is now 5,903 `vault_documents` / 5,134 `vault_notes_fts` (was
> 5,761 / 4,992 — normal growth). But "no note matches" is not what the
> index returns: `SELECT COUNT(*) FROM vault_notes_fts WHERE
> vault_notes_fts MATCH …` gives **`"avoided cost"` → 1, `FlightAware` → 1,
> `valuation` → 3, `COGS` → 1**. The doc's own qualifier ("in a valuation
> context") is doing the real work and is a human judgement, not a query
> result — so a reader cannot reproduce the negative finding from the text
> as written. The five matches were inspected and none records a
> methodology, so **the load-bearing conclusion — that the $65k–$100k
> derivation is unrecorded — is confirmed**; only the absoluteness of "no
> note matches" is wrong. Recommend quoting the query and its non-zero hit
> counts rather than asserting zero.

---

## 1. Step 1 — What is actually live (verified against the running system)

All checks below were run directly against the production Pi on
**2026-08-18, ~17:20–17:25 America/New_York** (epoch ~1787087900), via
`sqlite3` on `/var/lib/corporatetraveldc/corporatetraveldc.db`,
`journalctl --user`, `podman ps/logs`, and the receiver's own JSON endpoint.

> **Re-verified 2026-08-19 ~07:00 EDT.** The "Status **right now**" column
> below is a point-in-time snapshot whose shelf life turned out to be under
> 6 hours: the host rebooted at 2026-08-18 21:19:04 EDT
> (`uptime -s`) and the five SWIM ingest containers were stopped at
> 23:08 EDT and never restarted. Per-row corrections are marked
> **[2026-08-19]** inline. A status column with no `as-of` timestamp *per
> row* cannot be audited later — recommend future revisions emit this table
> from a script (e.g. a `feed_state` + `list-units` join) rather than by
> hand, so the timestamp travels with the data.

### 1.1 Verdict table

| Feed / capability | Source actually used | Status **right now** | Evidence |
|---|---|---|---|
| **SWIM FDPS** (flight plans/positions) | Solace PubSub+ @ `ems1.swim.faa.gov` | ~~**LIVE — strongest feed**~~ → **[2026-08-19] DEAD ~7.8 h** | `flight_events` 886,651 rows; 12,546 in last 24 h; latest write 17:20:34, national pairs (KDEN→KBOS, KLAX→PHLI, KSDF→KEWR) — **[2026-08-19] unit `inactive (dead)`, stopped 08-18 23:08:28 EDT (`journalctl --user -u corporatetraveldc-ingest-fdps`, "Stopping…" then "Consumed 4min 59.892s CPU … 435M memory peak"); `flight_events` now 890,015 rows, `MAX(updated_at)` = 1787108907 = 08-19 03:08:27 UTC** |
| **SWIM TFMS** (NAS flow programs) | same | ~~**LIVE**~~ → **[2026-08-19] PUSH DEAD ~8.0 h** | `nas_programs` 21,960 rows, 779 in 24 h; log `tfms: wrote N NAS program(s)` at 17:19 — **[2026-08-19] `push:tfms` last fetch 08-19 02:57:56 UTC. `nas_programs` is still growing (22,163 rows, newest 0.04 h old) but that is the *free REST* `nasstatus.faa.gov` poller, not TFMS push — this row credits SWIM for data a free endpoint is supplying. Cost-avoidance impact: the two sources must be separated before either is priced.** |
| **SWIM STDDS** (surface/terminal) | same | ~~**LIVE but heavily throttled**~~ → **[2026-08-19] DEAD ~8.0 h** | `surface_movement_events` 118,038; `surface_tracks` 12,285 (last 17:18:55); **suspended 189× in 24 h** by bandwidth governor — **[2026-08-19] unit dead; `surface_tracks` unchanged at 12,285, `MAX(last_seen)` = 2026-08-19T02:55:14Z. The "189×" suspension count is not reproducible from `journalctl --user --since '24 hours ago' \| grep -c bandwidth_priority=ollama` (42 lines total); no query is given for how 189 was counted.** |
| **SWIM TBFM** (arrival sequencing) | same | ~~**LIVE**~~ → **[2026-08-19] DEAD ~7.8 h** | `tbfm_sequences` 33,782, last 17:18:49; 10+ ARTCCs (ZDC 13,079; ZID, ZOB, ZTL, ZBW, ZJX, ZNY, ZAU, ZME, ZDV) — **[2026-08-19] unit dead; 33,815 rows, `MAX(last_seen)` = 2026-08-19T03:07:05Z** |
| **SWIM ITWS** (terminal wx alerts) | same | ~~**LIVE**~~ → **[2026-08-19] DEAD ~7.8 h** | `itws_alerts` 21 rows = 7 product types × KDCA/KIAD/KBWI, last 17:18:16 — **[2026-08-19] unit dead; still exactly 21 rows, `MAX(last_seen)` = 2026-08-19T03:07:00Z. 21 rows is an upsert-per-product ceiling, not a volume signal; it should not be read as throughput.** |
| **SWIM AIM/FNS** (digital NOTAMs) | same | **LIVE** ✅ **[2026-08-19] still live — the only surviving SWIM push feed** | `notams` 5,917 rows / **314 distinct facilities**; 851 refreshed in 24 h; log `aim: wrote N NOTAM(s)` at 17:19 — **[2026-08-19] 5,998 rows / 314 facilities / 926 refreshed in 24 h, `MAX(last_seen_at)` 0.01 h ago** |
| **Local ADS-B (1090 MHz RF)** | own SDR → `readsb`/ultrafeeder | **LIVE but small footprint** | 19.39 M msgs since boot; **15 aircraft in view, max 50 nm, median 33.6 nm** at check time; all-time max 88 nm |
| **Local ACARS / VDL-M2 (RF)** | own SDR → `dumpvdl2`/acarshub | ~~**DEAD — no message in ~4.3 days**~~ → **[2026-08-19] LIVE — receiving now** | `acarshub/messages.db`: 41,331 msgs total, **`MAX(msg_time)` = 1786714089 ≈ 2026-08-13**; **0 in last 24 h**; `acars_messages` in main DB = **0 rows** — **[2026-08-19] `MAX(msg_time)` = 1787137090 = 2026-08-19 10:58:10 UTC, i.e. current. Two corrections to the original reading: (a) reception recovered, so §1.3 #1 no longer holds; (b) the row count *fell* 41,331 → 38,536, proving `messages` is a rolling-retention window, not a cumulative total — the doc reads it as "msgs total". `acars_messages` in the main DB is still **0 rows** with `corporatetraveldc-acars-watcher` `active (running)`, so the *pipeline from acarshub into the platform DB* is the real broken link, not RF reception. That is a different (and more valuable) finding than the one recorded.** |
| **TFR** | `tfr.faa.gov/tfrapi/getTfrList` (REST, free) | **LIVE** | `tfrs` 119 rows, 0 VIP active, last insert 16:00 |
| **NAS status** | `nasstatus.faa.gov` (REST, free) | **LIVE** | feed_state `nas` OK, hash rotating |
| **NOAA NWWS-OI** (weather wire) | XMPP MUC, credentialed | **DOWN ~2 days** | Continuous `Authentication failed: not-authorized` since **2026-08-16 22:07**; last good MUC join 2026-08-16 13:46; `nws_alerts` = **1 row** |
| **METAR** | `aviationweather.gov` (free) | ~~**DEGRADED**~~ → **[2026-08-19] RECOVERED** | feed_state: **6 consecutive failures**, read timeouts; last good fetch 16:09; pusher actively suppressing wx alerts as "not fresh" — **[2026-08-19] `feed_state.metar` `consecutive_failures = 0`, last fetch 0.0 h ago, no error. Self-cleared without intervention; a transient upstream timeout was recorded here as a platform gap.** |
| **NWS forecast (API)** | `api.weather.gov` (free) | **LIVE** | `nws_forecast` 3 zones (DC001, MDZ014, VAZ036), all fetched 17:14 |
| **Amtrak** | **`api.amtraker.com/v3/trains`** — free **third-party community** API | **LIVE, high volume** | `train_events` 684,646 rows, **25,831 in 24 h**; log at 17:17 "83 train(s) at WAS — 13/83 delayed" |
| **DCA / IAD FIDS** | MWAA public JSON (`flyreagan.com`, `flydulles.com`) — **scraped**, free | **LIVE** | feed_state both OK, hashes rotating at 17:14 |
| **FAA aircraft registry** | `registry.faa.gov` bulk ZIP (free) | **LIVE, fresh** | 315,940 rows, full import **today 05:58 UTC** |
| **OpenSky registry** | bulk CSV (free) | **LIVE but STALE upstream** | 519,991 rows; imported 2026-07-21 but **upstream `source_last_modified` = 2024-11-04** |
| **OSINT / RSS** | 22 enabled scopes, ~32 public RSS/Google-News URLs | **LIVE** | `osint_items` 586 rows, **77 in 24 h**, last ingest 16:44 |
| **ATCSCC ops plan** | public | **LIVE** | 42 rows, last 16:31 |
| **EUROCONTROL NM B2B** | — | **NOT LIVE — `awaiting_credentials`** | feed_state; code is a graceful-skip stub |
| **JASDAT (Japan)** | — | **NOT LIVE — `awaiting_credentials`** | feed_state; code is a graceful-skip stub |
| **FAA NOTAM REST API** | `api.faa.gov` | **NOT LIVE — `awaiting_credentials`** | feed_state (note: NOTAMs still arrive via SWIM FNS, a different path) |
| **AIS / maritime** | — | **NOT IMPLEMENTED** (operator-confirmed roadmap) | `src/ais_watcher/` = 212 LOC, **no systemd unit**, `vessel_events` = **0 rows** |
| **LinkedIn / gig-economy** | operator's own periodic data exports | **NOT a live feed** | `common/export_analysis.py` — manual export drop, weekly/monthly, by design |
| **WMATA / transit** | — | **NOT INTEGRATED** | no fetcher exists; only incidental mentions in RSS catalog/lexicon |
| **`ustrains_departures`** | findtrain.com fetcher | **DEAD** | table exists, **0 rows**, not in `FETCH_SCHEDULE` |
| **Second-brain / automation** | local Ollama + vault | **LIVE** | 5,761 vault docs, 4,992 FTS notes, 39,744 note↔concept edges; 21 purpose-built models; 297 briefs in 7 days |

### 1.2 Measured throughput (the number that actually drives pricing)

From the platform's own `feed_data_usage` table (its built-in metering),
using only the **four feeds with a ≥14.8 h measurement window** (TFMS and
STDDS windows had just reset and are excluded as unreliable to extrapolate):

> **⚠️ [2026-08-19] NOT REPRODUCIBLE — the numbers in this table cannot be
> re-derived, and the method that produced them is unsound as stated.**
>
> **(a) The measurement window is destroyed on every ingest restart.**
> `src/common/db.py` `init_feed_usage()` is called at ingest startup and
> does `bytes_in=0, records_in=0, records_accepted=0,
> window_start=excluded.window_start` — a hard counter reset. So
> `feed_data_usage` measures *time since that feed's container last
> started*, not a stable sampling window. Because
> `corporatetraveldc-ingest-restart.timer` polls memory every 2 min and
> restarts ingest containers on a known OOM leak, these windows reset
> frequently and unpredictably. The doc's "≥14.8 h window" filter is a
> reasonable instinct but it does not fix this: a 14.8 h window is simply
> 14.8 h since the last restart, and it silently averages across any
> bandwidth-governor suspensions inside it.
>
> **(b) Re-running the same query now gives wildly different rates.**
> As of 2026-08-19 ~07:00 EDT the windows are 0.11–0.32 h for the SWIM
> feeds (post-reboot restart) and 9.62 h for FNS:
>
> | Feed | window (h) | bytes_in | records_in | implied MB/day | doc's MB/day |
> |---|---:|---:|---:|---:|---:|
> | fdps | 0.32 | 1,394,259,013 | 48,641 | ~98,285 | ~1,682 |
> | tfms | 0.17 | 351,204,952 | 28,196 | ~48,288 | *(excluded)* |
> | itws | 0.29 | 23,893,749 | 3,156 | ~1,861 | ~639 |
> | tbfm | 0.30 | 19,334,709 | 32,653 | ~1,480 | ~220 |
> | stdds | 0.11 | 1,956,047 | 799 | ~403 | *(excluded)* |
> | fns | **9.62** | 45,609,667 | 6,085 | **~109** | ~161 |
>
> The one feed with a long, comparable window (FNS, 9.62 h) comes in at
> **~109 MB/day against the doc's ~161 MB/day — 32 % lower.** The short
> windows are startup backlog-drain bursts and are *not* offered here as
> corrected figures; they are shown only to demonstrate that this metric
> swings by ~60× depending on when you sample it.
>
> **(c) Per-record size is the only stable quantity.** fdps now reads
> 1,394,259,013 B / 48,641 records = **28.7 KB/record**; the doc's own
> row implies 1,682 MB / 64,300 = **26.7 KB/record**. Those agree. The
> *rates* do not. So the doc's record-count and MB/day columns are the
> unreliable half of this table, and they are exactly the half §2 was
> going to price.
>
> **UNVERIFIED — could not confirm against live state as of 2026-08-19
> 07:00 EDT:** the `~2.7 GB/day` subtotal, the `~80 GB/month`, the
> `~90–150 GB/month` all-six-feed range, and the **`~1.04 million accepted
> operational records/month`** figure that the text below calls "the
> single most load-bearing input in §2." No replacement figures are
> offered — deriving one honestly requires a metering table that does not
> reset on restart (e.g. append-only interval rows), which does not exist
> today. **Recommend §2 not be written against this table until that is
> fixed.**

| Feed | MB/day | records received/day | records **accepted**/day | accept rate |
|---|---:|---:|---:|---:|
| FDPS | ~1,682 | ~64,300 | ~25,700 | 40.0 % |
| ITWS | ~639 | ~90,300 | ~1,450 | 1.6 % |
| TBFM | ~220 | ~409,000 | ~3,670 | 0.9 % |
| FNS (NOTAM) | ~161 | ~28,500 | ~3,760 | 13.2 % |
| **Subtotal (4 feeds)** | **~2.7 GB/day** | **~592,000/day** | **~34,600/day** | **~5.8 %** |

- **~80 GB/month** of raw SWIM ingest across these four alone (all six feeds
  together plausibly **~90–150 GB/month**; the wider figure is not precise
  enough to rely on).
- **~1.04 million accepted operational records/month** — this is the volume a
  commercial equivalent would have to bill for, and it is the single most
  load-bearing input in §2.
- The **~94 % discard rate** is deliberate, not waste: see §1.4.

### 1.3 Adversarial findings — things the docs claim that the system does not currently do

These are stated up front because they materially reduce the number:

> **[2026-08-19] Status of these ten findings on re-check, ~13.5 h later:**
> **#1 REVERSED** (ACARS live — but see the corrected §1.1 row: the real
> gap is acarshub → `acars_messages`, still 0 rows).
> **#3 REVERSED** (METAR `consecutive_failures = 0`).
> **#10 REVERSED** — `corporatetraveldc-transport-pattern-digest.service`
> is **not** in `failed` state; its 2026-08-19 00:42:26 run exited
> `status=0/SUCCESS`. It is a timer-triggered oneshot, so `list-units`
> shows only the most recent run — the doc read a transient
> `TimeoutStartSec=1600` kill as a standing failure. The two units
> actually `failed` right now are `corporatetraveldc-integrity-sweep`
> (expected — unsigned edits, per CLAUDE.md) and
> `corporatetraveldc-second-brain-weekly`.
> **#2 UNCHANGED but understated** — `nws_alerts` is still exactly 1 row.
> **#4, #6, #7, #8, #9 UNCHANGED and independently confirmed** —
> `eurocontrol`/`jasdat`/`notam` all still `awaiting_credentials` in
> `feed_state`; `international_aviation_feed` 0 rows;
> `ustrains_departures` 0 rows; `vessel_events` 0 rows; `runsheet` newest
> `run_date` still 2026-07-28 / `trip_count` 1; `audit_log` still 12 rows,
> **all 12 `egress_status='pending'`**, and `/healthz` reports
> `"audit_count_24h": 0`.
> **#5 NEWLY UNDERSTATED** — the suspension problem is worse and broader
> than "STDDS 189×": over the last 24 h the governor names **fns 216×,
> tbfm 139×, itws 129×, stdds 120×, tfms 12×**, and `feed_state` currently
> carries `error = "suspended: bandwidth_priority=ollama"` on
> `push:itws`, `push:stdds` and `push:tbfm` simultaneously. (These counts
> come from `journalctl --user --since '24 hours ago'` grepped per feed
> and are offered as an order-of-magnitude signal, not as a precise
> replacement for the doc's unsourced 189.)
>
> **NEW finding #11 — the whole SWIM tier is down, and nothing paged.**
> Five of six SWIM push containers have been dead ~8 h. The platform
> *detects* this (`/healthz` → `"status":"degraded"`) but no unit restarted
> them and the failure is not visible in `systemctl list-units` as
> `failed`, because a cleanly-stopped container is `inactive (dead)`, not
> failed. For a valuation premised on "authoritative real-time feeds", an
> 8-hour silent outage of the authoritative tier is a more serious defect
> than any single item in this list.

1. **ACARS/VDL local reception is dead.** `docs/COST_STRUCTURE.md` §2 sells
   "ADS-B **+ ACARS + VDL** + weather" as the differentiator against a
   FlightAware/FR24 subscription. **The ACARS/VDL half of that claim is not
   true today** — zero messages in 4.3 days. This is not theoretical: the
   pusher is visibly degraded by it, logging
   `BA293 absent from feed 5970s … ADS-B-dark alone no longer confirms
   landed, awaiting ACARS`. The OOOI confirmation logic is running without
   one of its two required sources.
2. **NWWS-OI has been auth-failing for ~2 days.** The credentialed NOAA
   Weather Wire feed — the "authoritative weather source" in the pitch — is
   down, and `nws_alerts` holds exactly **1 row**. The free `api.weather.gov`
   forecast poller is carrying weather single-handedly.
3. **METAR is degraded** (6 consecutive failures) and the platform knows it —
   `feed_gate` is correctly suppressing stale weather alerts. Good
   engineering; still a gap in delivered data.
4. **"Global reach" is aspirational, not real.** EUROCONTROL NM B2B and
   JASDAT are well-written graceful-skip stubs in `awaiting_credentials`.
   `international_aviation_feed` has **0 rows**. Today this is a
   **US-only** platform. Any valuation crediting international coverage is
   crediting code that has never received a byte.
5. **SWIM feeds are duty-cycled against local LLM inference.** A
   `bandwidth_priority=ollama` governor suspends queue draining whenever
   Ollama runs — **STDDS was suspended 189 times in 24 hours**. FDPS is
   exempt (0 suspensions), so the primary feed is protected, but surface,
   terminal-weather, NOTAM and sequencing data have real, routine gaps. A
   commercial feed would not.
6. **Amtrak is not an authoritative-source integration.** It polls
   `api.amtraker.com`, a **free third-party community API**. It works well
   (25,831 events/day) but it is exactly the "commercial middleman" pattern
   the thesis claims to avoid — just an unpaid one. **Cost avoided: $0**,
   because the alternative is also free.
7. **DCA/IAD FIDS are scraped**, not licensed — undocumented public JSON
   endpoints behind a static cookie. Free, valuable, and **fragile**: MWAA
   can break this with one deploy, and there is no contract or SLA.
8. **The operational runsheet is effectively empty.** `runsheet` latest
   entry is `run_date = 2026-07-28` with `trip_count = 1`. Whatever the data
   platform is worth, it is **not currently being consumed by a busy
   dispatch operation** — 297 briefs were generated in 7 days against
   approximately one recorded trip.
9. **The audit log is thin.** `audit_log` holds **12 rows** total
   (7 `board_refresh`, 5 SR1/SR2 guardrail decisions), newest ~1.8 days old,
   and **every row's `egress_status` is `pending`** — the compliance-egress
   push has never successfully shipped a record. It is a real mechanism, not
   a fake one, but it is nowhere near "every access is logged."
10. **The `transport-pattern-digest` service is in `failed` state.**

### 1.4 The scope that IS real, stated precisely

This matters more than any single price, so it is stated exactly
(`src/ingest/parsers/geo_filter.py`):

- SWIM data is accepted only if it falls within **250 nm of KDCA** *or*
  touches one of **30 named core US airports** (DCA/IAD/BWI, JFK/LGA/EWR,
  BOS, PHL, ORD/MDW, MSP, ATL, MIA/FLL/MCO, CLT, DFW/DAL, IAH/HOU, DEN, SLC,
  LAS, PHX, LAX, SFO, SJC, OAK, SEA, PDX).
- **STDDS surface tracks: KDCA, KIAD, KBWI only.** **Terminal tracks: PCT
  (Potomac TRACON) only.** **ITWS: KDCA/KIAD/KBWI only.**
- **TBFM and FDPS are genuinely multi-center / near-national** in reach
  (10+ ARTCCs; 1,306 distinct airlines seen in `flight_events`).
- **Local RF is a single modest indoor receiver**: 15 aircraft in view,
  median 33.6 nm, max 88 nm all-time. `COST_STRUCTURE.md` itself confirms
  the buildout is "ADS-B + VDL2 SDRs only, no dedicated roof runs, no tuned
  antennas."

**So the honest one-line scope statement is:** *a 30-airport US network with
DC-metro depth, not a global feed; national in flight-plan and flow data,
regional in surface/terminal data, hyper-local in own-RF data.*

### 1.5 Privacy / data-sovereignty mechanisms — verified real, with caveats

**Verified real:**

- **GPG-signed release manifest.** `gpg --verify MANIFEST.sha256.asc` →
  `Good signature from "Corporate Travel DC (the operator) …"`, EDDSA key
  `419A864C…`, signed today 16:46 EDT, covering **680 files**.
  **[2026-08-19] Re-verified: still `Good signature`, same EDDSA key
  `419A864CC29A09513039B6E03033FB4D01903159`, re-signed 06:58:37 EDT,
  now covering 685 files (`wc -l MANIFEST.sha256`). Note
  `DEPLOYMENT_COST_PROJECTION_2026-08-18.md` §2 states **671** files for
  the same manifest on the same day — the two documents disagree; neither
  cites the command. It grows on every signing pass, so any fixed count
  here is stale on write.**
- **Tiered bearer auth** (`src/auth/auth.py`): `tier0/tier1(CERT)/tier2(SHARES)/admin`,
  SHA-256-hashed tokens, FastAPI dependency enforcement, **15 tokens issued**.
  **[2026-08-19] `SELECT COUNT(*) FROM auth_tokens` = 15 ✅, but
  `/healthz` reports `"token_count_active": 5`. "15 issued" is correct and
  10 of them are revoked/inactive — the wording invites the reader to hear
  15 live credentials.**
- **SR1/SR2 guardrails** (`common/guardrails.py`) — mutation gate + model-tier
  routing, native (no MCP dependency), logging to `audit_log`.
- **CUI/PII scrub gate** (`second_brain/scrub_gate.py`) — a **block** gate,
  not a redaction gate, on every second-brain write path. Its own docstring
  honestly labels it "a first-pass heuristic gate (regex-based), not
  exhaustive."
- **100 % local inference.** 21 purpose-built Ollama models
  (`corporatetraveldc-pi5-*`, 2.2 GB each) on a `phi3:mini` base. No
  operational content is sent to any cloud LLM.

  > **[2026-08-19] ✅ VERIFIED — this is the best-evidenced claim in the
  > document, and it is the one that carries the cost conclusion.** Four
  > independent confirmations:
  > 1. `ollama list` → exactly **21** `corporatetraveldc-pi5-*` models
  >    (+ the `phi3:mini` base), all 2.2 GB, all rebuilt 29–31 h ago.
  > 2. The SR-1 usage log `/var/lib/corporatetraveldc/api-usage.csv` holds
  >    **21,081 rows since 2026-07-09** and
  >    `grep -icE 'claude|anthropic|sonnet|haiku|opus'` returns **0**.
  >    Every row's `model` is either a `corporatetraveldc-pi5-*` tag or
  >    the literal `deterministic`.
  > 3. `/etc/corporatetraveldc/dispatch.env` sets
  >    **`ANTHROPIC_FALLBACK_ENABLED=false`**, which `src/common/llm.py:373`
  >    reads and `:1222` uses to close the gate
  >    (`anthropic_gate_open = allow_anthropic and ANTHROPIC_FALLBACK_ENABLED`)
  >    before the `:1268` `if anthropic_gate_open and ANTHROPIC_API_KEY:`
  >    call site. (Note this *contradicts* CLAUDE.md, which currently
  >    asserts the flag "is NOT set to false in dispatch.env" — CLAUDE.md
  >    is the stale one here; not corrected in this doc, flagged only.)
  > 4. `src/common/llm.py` is the **only** Anthropic call site in `src/`
  >    (`import anthropic as _anthropic_sdk`, line 1340), and SR-1
  >    instrumentation is complete: all 18 skills in `src/poller/skills/`
  >    that import `common.llm` also call `log_usage()` (set difference is
  >    empty), so there is no un-logged inference path.
  >
  > **Therefore recurring cloud-LLM cost = $0.00, and this is measured,
  > not assumed.** Two honest caveats: (a) `src/common/sr1_log.py` writes
  > every token column as whatever the caller passes, and **every one of
  > the 21,081 rows has `input_tokens=output_tokens=cache_*=0`** — so the
  > usage log by itself proves *which model ran*, never *what it cost*;
  > the $0 conclusion rests on the model-name column plus the env gate,
  > not on the token columns. (b) The claim is about *cloud LLM* egress
  > only and should not be read more broadly — see the aggregator caveat
  > immediately below.
  >
  > **⚠️ But "21 models" overstates what is actually being inferred.** Of
  > the 5,718 SR-1 rows in the last 7 days, **1,972 (34.5 %) recorded
  > `model = deterministic`** — i.e. Ollama was unavailable or failed and
  > the skill emitted a hard-coded template instead. Over a third of this
  > platform's "local-inference intelligence layer" output in the last week
  > involved no inference at all. That does not change the $0 cost figure,
  > but any valuation crediting the LLM layer as a differentiator should
  > be discounted by roughly that fraction.
- **Compliance egress ships disabled by default**, with no default target —
  verified in `common/compliance_egress.py`.

**Caveats that cut against the sovereignty claim (§3.2 discounts for these):**

- The box **actively feeds five third-party aggregators**: PiAware/FlightAware
  (280,648 msgs sent, observed live), FR24, AirNav RadarBox, PlaneFinder, and
  adsbhub. **The local ADS-B data does leave the box**, to five commercial
  parties, continuously. "Nothing routes through a third party's servers" is
  **not accurate for the ADS-B layer**.

  > **[2026-08-19] Confirmed, and it is six, not five.** `podman ps` shows
  > dedicated containers `corporatetraveldc-{piaware,fr24feed,planefinder,
  > airnavradar,ultrafeeder}` all running, and
  > `podman inspect corporatetraveldc-ultrafeeder` resolves outbound
  > connectors to `piaware.flightaware.com`, `data-out.flightradar24.com`,
  > `data.adsbhub.org` **and `collector.opensky-network.org`** — OpenSky is
  > a sixth recipient the list omits. This cuts directly against the
  > cost-avoidance thesis in a way §5.1 should absorb: the platform is
  > simultaneously an unpaid *supplier* to FlightAware and FR24 and the
  > party claiming to have avoided FlightAware's and FR24's subscription
  > fees. Several of those vendors grant a free enterprise-tier account to
  > feeders, so part of the "avoided cost" may in fact be a *barter price
  > already being paid in data* rather than a cost avoided. **This needs a
  > line in §2 before any FlightAware/FR24 price is credited.**
- **Cloudflare Tunnel** fronts nine public hostnames (dispatch, runner,
  openwebui, ollama, adsb, acars, cloud, ntfy, pihole). Access-gated, but
  user-facing traffic transits Cloudflare. The repo's own config comment
  flags that `mcp.`, `cockpit.`, and `dav.` are live on the real tunnel and
  **undocumented** — "worth an audit."
- The audit log's thinness and stuck `pending` egress (§1.3 #9).

---

## 2. Step 2 — Commercial pricing

*Populated 2026-08-19 from live vendor-pricing research. Every figure carries
vendor, product, price, unit, URL and date accessed. Where a vendor does not
publish a price, that is recorded as **quote-only** rather than estimated.*

> **How to read this section.** It is split into **Section A** (this
> deployment, currently live — the only basis for this instance's
> avoided-cost arithmetic) and **Section B** (§2.9, the global build-out
> ceiling — explicitly *not* implemented here and *not* summed into
> anything). The single most important finding is stated up front in §2.1.

### 2.1 The structural finding that governs every number below

**Almost everything this platform ingests is free-to-anyone government or
community data. The avoided cost is therefore an integration cost, not a
data-licence cost.**

Three sourced facts drive this:

1. **FAA SWIM/SCDS is free of charge.** The FAA's own SWIM Q&A states the
   service is provided free of charge and "currently there is no cost for
   data"; the consumer bears only their own interface-development cost.
   Access requires a signed Service Access Agreement and NDRB approval, not
   a fee. — <https://www.faa.gov/air_traffic/technology/swim/questions_answers>
   and <https://support.swim.faa.gov/> (accessed 2026-08-19; both URLs
   return HTTP 403 to automated fetch, content confirmed via search-engine
   index of those exact pages — **flagged as indirectly verified**).
2. **NOAA/NWS data is public domain.** "The information on National Weather
   Service (NWS) Web pages are in the public domain, unless specifically
   noted otherwise, and may be used without charge for any lawful purpose."
   — <https://www.weather.gov/disclaimer> (accessed 2026-08-19). Applies to
   `api.weather.gov`, `aviationweather.gov` and NWWS-OI alike.
3. **FAA NOTAM data is free**, via public NOTAM Search and via FNS-NDS under
   a no-cost data-sharing agreement — the FAA agreement-portal page carries
   terms and LADD-compliance conditions but **no pricing, tiers or cost**. —
   <https://aa.data.faa.gov/data/service.jsf?uuid=56910255-54c7-49d9-8577-cf93291bf698>
   (accessed 2026-08-19, fetched directly).

**Consequence:** a competitor with the same FAA approvals and the same SDR
hardware pays the same $0 for the same data. What this platform actually
owns is the *integration* — the Solace client, the parsers, the geo-filter,
the correlation layer. Any valuation that credits "avoided data-subscription
cost" as if the underlying data were purchasable-only is crediting something
the FAA gives away. That does not make the asset worthless; it means the
value lives in §3 (automation), not here.

---

# SECTION A — This instance, currently live

**Scope rule:** a feed appears in the priced rollup only if it is *running
and writing data* at check time (2026-08-19 ~07:25 EDT). Feeds that are
configured-but-dead, or coded-but-never-deployed, are listed at **$0
credit** with the evidence, exactly so this section does not repeat the
error the sibling audit caught in `DEPLOYMENT_COST_PROJECTION`'s MCP row.

### 2.2 Live-state gate (re-verified immediately before pricing)

> ⚠️ **Correction to the working assumption.** This section was commissioned
> on the understanding that "all 6 SWIM feeds [are] just confirmed active
> again." **That is not what live state shows. Four of six are live; STDDS
> and TFMS are `inactive (dead)`.** Verified at 07:24 EDT via
> `systemctl --user show corporatetraveldc-ingest-<feed> -p ActiveState`
> and by last-write timestamps in the target tables:

| SWIM feed | Unit `ActiveState` | Last write to its table | Verdict |
|---|---|---|---|
| FDPS | `active` | `flight_events` 2026-08-19 11:24:23Z | **LIVE** |
| TBFM | `active` | `tbfm_sequences` 2026-08-19T11:24:15Z | **LIVE** (self-suspends, below) |
| ITWS | `active` | `itws_alerts` 2026-08-19T11:24:11Z | **LIVE** (self-suspends, below) |
| FNS (NOTAM) | `active` | `notams` 2026-08-19 11:24:16Z | **LIVE** |
| **STDDS** | **`inactive`** | `surface_tracks` 2026-08-19T02:55:14Z (~8.5 h stale) | **DEAD — $0 credit** |
| **TFMS** | **`inactive`** | — | **DEAD — $0 credit** |

Two further caveats that bear on fidelity, both quoted from live logs:

- TBFM and ITWS are **duty-cycled against local LLM inference**:
  `swim_client WARNING swim_client tbfm: suspending message consumption --
  bandwidth priority = 'ollama' … Connection stays open; not draining the
  queue is what saves bandwidth.` A commercial feed does not do this.
- **`feed_state` is not a reliable per-feed liveness signal** and should not
  be cited as one (including in §1.1 above). Each per-feed container runs
  the same image with `SWIM_NMS_SKIP_FEEDS` set to the *other five* feeds
  (e.g. `corporatetraveldc-ingest-fdps.container`:
  `Environment=SWIM_NMS_SKIP_FEEDS=stdds,tfms,fns,tbfm,itws`), and
  `src/ingest/swim_client.py:811` writes `disabled: SWIM_NMS_SKIP_FEEDS`
  into `feed_state` for every skipped feed. Sibling containers therefore
  **overwrite each other's rows**, which is why `push:fns` — demonstrably
  live — currently shows `disabled: SWIM_NMS_SKIP_FEEDS`. Use unit state
  plus table writes instead.

### 2.3 Section A — feed-by-feed commercial equivalents

Price categories: **(a)** vendor's own published pricing page · **(b)**
credible third-party report/aggregator · **(c)** historical/stale (date
given). All accessed **2026-08-19**.

| # | Feed (live?) | Actual source & fidelity — evidence | Closest commercial equivalent at that fidelity | Price | Cat. | Creditable avoided cost/yr |
|---|---|---|---|---|---|---|
| 1 | **SWIM FDPS** ✅ LIVE | Solace PubSub+ push, per-feed VPN + durable queue (`src/ingest/swim_client.py:2-6, 335, 454`), national: `flight_events` 891,573 rows, 11,342/24 h, **1,307 distinct airlines** | **FlightAware Firehose** (streaming enterprise flight feed) — the only true like-for-like | **Not publicly priced.** "Total monthly pricing is established on a customer basis… fixed monthly fee" <https://flightaware.com/commercial/firehose> | (a) | **UNPRICEABLE** — see §2.4 |
| 2 | **SWIM TBFM** ✅ LIVE | Solace push; `tbfm_sequences` 33,821 rows, **20 ARTCC/facilities**; arrival metering/sequencing | No commercial reseller of TBFM-class arrival-sequencing data identified in this research pass | **None found** | — | **$0** (no market comparable) |
| 3 | **SWIM ITWS** ✅ LIVE | Solace push; `itws_alerts` 21 rows = 7 product types × KDCA/KIAD/KBWI. **21 is an upsert ceiling, not volume** | No commercial reseller of ITWS terminal-weather-alert data identified | **None found** | — | **$0** (no market comparable) |
| 4 | **SWIM FNS → NOTAM** ✅ LIVE | Solace push; `notams` 5,949 rows / **314 facilities** / 878 refreshed in 24 h. *(REST `notam` fetcher excluded — `feed_state` = `awaiting_credentials`, never live)* | No standalone raw-NOTAM feed is sold. Jeppesen/Collins NOTAM data is **bundled inside** EFB subscriptions (ForeFlight), not sold as a data feed | **Not sold standalone**; FAA source is free (§2.1 #3) | (a) | **$0** — source is free to anyone |
| 5 | ~~SWIM STDDS~~ ❌ **DEAD** | Unit `inactive`; `surface_tracks` frozen at 12,285, last 02:55:14Z | *(would be surface-movement data; no reseller identified)* | — | — | **$0 — not live** |
| 6 | ~~SWIM TFMS~~ ❌ **DEAD** | Unit `inactive`. `nas_programs` still grows (22,236 rows) but via the **free REST** `nasstatus.faa.gov` poller, not TFMS push | L3Harris SWIM capability; Mosaic ATM "Fuser" (SWIM-ingesting DaaS) | Both **quote-only** — <https://www.l3harris.com/all-capabilities/system-wide-information-management-swim>, <https://mosaicatm.com/commercial-aviation-analytics-solution/aviation-data-fuser/> | (a) | **$0 — not live** |
| 7 | **ADS-B feeder stack** ✅ LIVE | Own 1090 MHz RTL-SDR → `ultrafeeder`; **~26 msg/s ≈ 2.3 M msg/day** measured over 10 s. **Footprint is small: 5 aircraft in view, 3 with position, max 29.1 nm, median 6.7 nm.** Feeds 6 aggregators | **Reciprocal access — see §2.5.** FlightAware Enterprise; FR24 Contributor; AirNav RadarBox Business; PlaneFinder; adsbhub; OpenSky | FA Enterprise **$99.95/mo** (page dated 2023-01-17) · RadarBox Business **$399/yr** · FR24 Contributor **retail price UNVERIFIED** | (c)/(b) | **~$1,598/yr** *(reciprocal, not avoided — §2.5)* |
| 8 | **ACARS / VDL-M2** ⚠️ PARTIAL | Own VHF RTL-SDR → `dumpvdl2` → `acarsrouter` → `acarshub`. **641 msgs in 24 h.** RF layer live, **but `acars_messages` in the platform DB = 0 rows** — the pipeline into the platform is broken | **No commercial receive-side ACARS/VDLM feed exists.** SITA AIRCOM and Collins/ARINC GLOBALink are *send-side* carrier networks airlines pay to route their own messages — a different product. airframes.io feeder tier is free; its commercial tier is "in development", unpriced | **No market product to price** — <https://docs.airframes.io/api/pricing/>, <https://www.sita.aero/solutions/sita-for-aircraft/data-and-platforms/aircom-serverplatform/>, <https://www.collinsaerospace.com/what-we-do/industries/commercial-aviation/connected-cockpit/arinc-globalink> | (a) | **$0** — no purchasable equivalent, and not reaching the DB anyway |
| 9 | **NWWS-OI** ❌ **DOWN** | `nwws-oi.weather.gov` XMPP MUC (`src/ingest/config.py:37-41`), WFO filter `LWX,AKQ,CTP,PHI`. Live log at 07:19 EDT: `NWWS-OI lost (NWWS-OI disconnected); reconnecting in 120s`. `nws_alerts` = **1 row** | Free NOAA product by design — no commercial equivalent is being avoided | **FREE government data** (§2.1 #2) | (a) | **$0** — free either way, *and* currently down |
| 10 | **METAR** ✅ LIVE | `https://aviationweather.gov/api/data/metar` (`src/poller/fetchers/metar.py:19`); `feed_state.metar` 0 failures | CheckWX / AVWX / Meteomatics / DTN aviation weather — **but see caveat** | CheckWX & AVWX paid-tier prices **not obtainable** (JS/login-gated); Meteomatics & DTN **quote-only** | (a) | **$0** — free government source (§2.1 #2) |
| 11 | **NWS forecast** ✅ LIVE | `api.weather.gov` (`src/poller/fetchers/nws.py:2,27,31`), 3 zones DC001/MDZ014/VAZ036 | Same as row 10 | Same | (a) | **$0** — free government source |
| 12 | **Amtrak** ✅ LIVE | `https://api.amtraker.com/v3/trains` (`src/ingest/amtrak.py:10`; `src/amtrak_tracker/main.py:32`). **21,439 events/24 h**, 691,869 total | Amtrak publishes **no official API**; `api.amtraker.com` is an unofficial community wrapper (maintainer `eiiot`, <https://github.com/eiiot/amtraker-v3>); Amtrak's static **GTFS is free** (<https://mobilitydatabase.org/feeds/gtfs/mdb-11>) | **Free** (unofficial + free official GTFS) | (a)/(b) | **$0** — the alternative is also free |
| 13 | **Marine AIS** ❌ **NOT LIVE** | `src/ais_watcher/ais_watcher.py` = 212 LOC reading AIS-catcher UDP. **No systemd unit exists** (`systemctl --user list-units '*ais*'` → none); `vessel_events` = **0 rows** | *(VesselFinder €330/10k credits; Spire Maritime quote-only; MarineTraffic now enterprise-only)* | Priced equivalents exist but **are not creditable** — nothing is running | (a) | **$0 — not implemented** |
| 14 | **OSINT / RSS** ✅ LIVE | 22 enabled scopes / 22 feed URLs (`osint_scopes`), **270 distinct outlets**, 622 items, **74 in 24 h** | Scope-matched **downward** to actual volume (74 items/day is small): Event Registry 5K **$90/mo**; NewsCatcher Starter **$50/mo**. *(NewsAPI Business $449/mo and NewsCatcher Scale $500/mo are 250k–2M req/mo — grossly oversized, not credited.)* **GDELT is free** for commercial use | **$600–$1,080/yr** — <https://newsapi.ai/plans>, <https://www.newscatcherapi.com/pricing>; GDELT free per <https://www.gdeltproject.org/about.html> | (a) | **$600–$1,080/yr** |
| 15 | **TFR / NAS / ATCSCC / FIDS / registries** ✅ LIVE | `tfr.faa.gov`, `nasstatus.faa.gov`, ATCSCC ops plan, MWAA FIDS JSON (scraped), FAA registry 316,031 rows, OpenSky 519,991 rows | All free public/government endpoints; FIDS is scraped undocumented public JSON (fragile, no SLA) | **FREE** | (a) | **$0** |

### 2.4 Why the SWIM rows cannot be given a dollar figure

The honest answer is that **the closest true equivalent to SWIM FDPS is
FlightAware Firehose, and FlightAware does not publish a price.** Their own
page says pricing "is established on a customer basis." No amount of
research turns that into a number, and inventing one would be exactly the
failure mode this document was written to avoid.

What *can* be stated, with sources:

- **FlightAware AeroAPI** (the REST product, not Firehose) publishes tiers:
  Starter $0 · Bronze **$25/mo** · Silver **$99/mo** · Gold **$249/mo** ·
  Platinum **$999/mo** (500,000 queries, $0.0015/query overage) —
  <https://www.flightaware.com/commercial/aeroapi/v3/pricing.rvt>, (a). This
  is **not** a scope match for a Solace firehose: AeroAPI is request/response
  metered, this platform consumes an unmetered durable push queue.
- **Cirium** publishes no self-serve price; the buyer-side aggregator Vendr
  reports an **average contract value of ~$30,530/yr** —
  <https://www.vendr.com/buyer-guides/cirium>, (b), page vintage 2025.
- **Spire Aviation**: third-party estimate "starts at more than
  **$10,000/month**" — <https://datarade.ai/data-providers/spire/profile>,
  (b). Spire publishes nothing itself.
- **ADS-B Exchange Enterprise**: "minimum annual commitments", amount
  undisclosed — <https://www.adsbexchange.com/products/enterprise-api/>, (a).

> **UNVERIFIED — a defensible dollar figure for the SWIM tier could not be
> established as of 2026-08-19.** The only same-class product (Firehose) is
> quote-only; the only published annual figure for an adjacent product
> (Cirium ~$30.5k/yr) is a *different vendor's average contract for a
> different data model*, is third-party-sourced, and is a year stale. It is
> recorded here as context and **deliberately not summed into any total.**
> Getting a real number requires an actual quote request to FlightAware,
> Cirium or Spire — a founder action, not a research action.

### 2.5 The ADS-B row is reciprocal access, not a windfall — stated plainly

This is the one row with real, published, currently-valid prices, and it is
also the row most easily overstated. The honest framing:

**We do not receive these accounts because the vendors are generous or
because non-feeders get them free. We receive them because we give the
vendors our data, continuously.** Verified reciprocity, with sources:

| Vendor | What a feeder receives | Retail price of that tier | Source (accessed 2026-08-19) |
|---|---|---|---|
| FlightAware (PiAware) | Free **Enterprise Account**: 8 months historical data, unlimited alerts, tail numbers, ATC callsigns, ad-free maps | **$99.95/mo** single user ⇒ **$1,199.40/yr** — **⚠️ page dated 2023-01-17, category (c) stale** | <https://flightaware.com/adsb/piaware/> · <https://go.flightaware.com/enterprise-and-enterprise-wx-multi-user-monthly-pricing> |
| AirNav RadarBox | Auto-upgrade to **Business account**: 1 year historical, fleet tracker, airport view, raw flight data | **$399/yr** (reported on AirNav's own blog) | <https://en.airnavradar.com/blog/benefits-of-being-a-radarbox-ads-b-feeder> |
| Flightradar24 | "Complimentary **Contributor** plan for as long as your feed remains active" | **UNVERIFIED** — `flightradar24.com/premium` returned **HTTP 403**; only Silver $28.80/yr and Gold $78.80/yr surfaced via search index, and neither is the Contributor/Business tier | <https://support.fr24.com/support/solutions/articles/3000115332-complimentary-contributor-plan-when-sharing-data> |
| PlaneFinder | Feeder benefit **UNVERIFIED** in this pass | — | — |
| adsbhub / OpenSky | Reciprocal exchange / open network, no fee either direction | **$0** | — |

Four qualifications that must travel with the ~$1,598/yr figure:

1. **It is barter, not savings.** We pay in data. If the receiver is turned
   off, the accounts lapse. Booking it as "avoided cost" while *also*
   claiming FlightAware/FR24 subscription avoidance elsewhere double-counts
   the same relationship — and it sits awkwardly beside §1.5's data-
   sovereignty claim, since the same act that earns the accounts is the act
   that sends our ADS-B data to six commercial parties.
2. **These are consumer flight-tracking accounts, not data feeds.** The
   FlightAware page describing feeder benefits **does not mention free or
   discounted API access**. So this $1,598/yr does not substitute for the
   Firehose/AeroAPI access in row 1 — it is a different, much smaller thing.
3. **The FlightAware price is stale** (2023-01-17 page). It is the largest
   component of the figure and it is the least current.
4. **Our receiver earns these on a very small footprint** — 5 aircraft in
   view, median 6.7 nm, max 29.1 nm at check time. The reciprocal accounts
   are granted for *participation*, not for volume, so the arrangement is
   generous to us. Do not restate that as evidence of a valuable feed.

### 2.6 Section A rollup — commercial-equivalent vs. actual cost

Sorted into three honest tiers. **Only Tier 1 is summed.**

**Tier 1 — published, dated, scope-matched prices (summable):**

| Item | Annual | Confidence |
|---|---|---|
| FlightAware Enterprise (reciprocal) | $1,199.40 | ⚠️ stale (2023 page) |
| AirNav RadarBox Business (reciprocal) | $399.00 | (b) vendor blog |
| OSINT/news API, scope-matched to 74 items/day | $600 – $1,080 | (a) published tiers |
| **Tier 1 total** | **≈ $2,198 – $2,678 / yr** | |

**Tier 2 — real equivalents that exist but are quote-only (NOT summed):**
FlightAware Firehose · Cirium (~$30.5k/yr avg contract, (b), 2025) · Spire
Aviation (>$10k/mo, (b)) · ADS-B Exchange Enterprise · L3Harris SWIM ·
Mosaic ATM Fuser · Meteomatics · DTN · FR24 Contributor retail price.
**A defensible total for this tier does not exist without a vendor quote.**

**Tier 3 — genuinely free to anyone, so $0 avoided by definition:**
All six SWIM feeds (FAA, free) · NOTAM (FAA, free) · METAR / NWS /
NWWS-OI (NOAA public domain) · TFR · NAS status · ATCSCC · FAA & OpenSky
registries · Amtrak (no official API; free community wrapper + free GTFS) ·
GDELT. **This is the largest group by data volume, and it is why the
"avoided subscription cost" thesis is weaker than it looks.**

**Actual recurring cost of this deployment — derived, not asserted (§2.7):
≈ $23 – $39 / yr (electricity only).**

> **Section A headline, stated conservatively:**
> **Defensible avoided cost ≈ $2,200 – $2,700/yr against ≈ $23 – $39/yr of
> actual recurring cost** — a real but modest figure, of which the $1,598
> ADS-B component (**~60 % of the high end, ~73 % of the low end**) is
> **reciprocal barter for our own data, not a cost anyone avoided.**
> The large-ticket items (SWIM-class flight data) are genuinely
> **UNPRICEABLE from public sources** and are excluded rather than guessed.
> **The "zero cost beyond hardware" claim is TRUE as to data-feed fees**
> (every feed is free, reciprocal, or scraped) **and very nearly true as to
> recurring cost** — the honest correction is "≈$30/yr of electricity
> beyond hardware," not literally zero. See §2.8 for the two small
> recurring costs the phrase glosses over.

### 2.7 Actual cost, sourced and shown — replacing the unsourced "$50–100/yr"

`docs/COST_STRUCTURE.md` line 159 asserts power at "~$50–$100/year" with no
wattage, no kWh and no tariff anywhere in the file (`grep -niE
'watt|kwh|power_draw'` returns nothing). Derived here from cited inputs:

**Inputs (all accessed 2026-08-19):**

| Component | Draw | Source |
|---|---|---|
| Pi 5, official heatsink, **stress test** | **6.8 W** (idle 2.6 W) | Tom's Hardware Pi 5 review, published 2023-10-23 — <https://www.tomshardware.com/reviews/raspberry-pi-5> |
| Pi 5, `stress -c 4` (independent) | **~8.8 W** (idle ~1.8 W) | Jeff Geerling — <https://www.jeffgeerling.com/blog/2023/reducing-raspberry-pi-5s-power-consumption-140x/> ⚠️ via search index; direct fetch 403 |
| 2 × RTL-SDR Blog V3 dongle | 270–280 mA @ 5 V ⇒ **~1.35–1.4 W each** ⇒ **~2.7–2.8 W** | RTL-SDR Blog V3 datasheet — <https://www.rtl-sdr.com/wp-content/uploads/2018/02/RTL-SDR-Blog-V3-Datasheet.pdf> |
| NVMe SSD (Phison 238.5 GB, `lsblk`) | **NOT DOCUMENTED** for Pi 5; hard-bounded by the Pi's PCIe **5 V/1 A = 5 W** ceiling | Raspberry Pi Forums — <https://forums.raspberrypi.com/viewtopic.php?t=372024> |
| Active cooler | **NOT DOCUMENTED** — official product brief gives only "5 V DC via four-pin fan header", no wattage | <https://datasheets.raspberrypi.com/cooling/raspberry-pi-active-cooler-product-brief.pdf> |
| DC residential electricity | **25.40 ¢/kWh** (May 2026; May 2025 was 20.43 ¢) | **EIA Table 5.6.A**, released 2026-07-23 — <https://www.eia.gov/electricity/monthly/xls/table_5_06_a.xlsx> |

**Load basis:** this box is *not* idling. `/proc/loadavg` at check time read
**10.74 on 4 cores**, CPU 63 °C, uptime 10 h. The sustained-load figure is
the correct one.

**Arithmetic, shown:**

- Low bound: 6.8 W (Pi, stressed) + 2.7 W (2 × SDR) + ~1 W (NVMe, low) ≈ **10.5 W**
- High bound: 8.8 W (Pi, stressed) + 2.8 W (2 × SDR) + 5 W (NVMe at the PCIe ceiling) + ~1 W (cooler) ≈ **17.6 W**
- 10.5 W × 8,760 h = **92.0 kWh/yr** → × $0.2540 = **$23.36/yr**
- 17.6 W × 8,760 h = **154.2 kWh/yr** → × $0.2540 = **$39.17/yr**

> **Sourced power cost ≈ $23 – $39 / yr** (midpoint ~$31). **`COST_STRUCTURE.md`'s
> "~$50–$100/yr" is roughly 1.6× – 3× too high for this single-node
> deployment.** It becomes plausible only for the *two-node* recommended BOM
> that `DEPLOYMENT_COST_PROJECTION` §5 prices — and that second node **is not
> built** (`hostname` = `corporatetraveldc-dispatch`, `nproc` = 4, and
> `OLLAMA_BASE_URL` resolves to this box's own tailnet IP).
> **Still partly UNVERIFIED:** the NVMe and active-cooler draws are not
> documented, so the high bound is a *ceiling argument* (PCIe limit), not a
> measurement. A $15 inline USB-C power meter would settle this in a day and
> is worth doing before the figure goes in front of a buyer.

**One-time hardware actually deployed** (single node, from
`COST_STRUCTURE.md`'s own line items, lines 31/33/34/61/111):
Pi 5 16 GB ~$350 + case share ~$45 + PSU share ~$30 + NVMe ~$180 + reference
RF adder (2 × SDR + modest antennas) ~$160 ⇒ **≈ $765 one-time.**

### 2.8 What "zero cost beyond hardware" quietly omits

Small, but a hostile reader will find them:

- **Electricity ≈ $23–$39/yr** — real and attributable (above).
- **Domain registration.** Nine public hostnames run under
  `example.com` (`docs/INFRA_MAP.md`). A `.com` registration is
  a real recurring cost. **UNVERIFIED** — the registrar and renewal price
  are not recorded anywhere in the repo, and it is a shared business cost,
  not platform-specific.
- **Internet.** `COST_STRUCTURE.md` lines 160–172 correctly argues this is a
  pre-existing business expense, not marginal to the platform. Agreed — but
  note the platform *does* push ~2.7 GB/day of SWIM ingest across it, so
  "not marginal" assumes an unmetered connection.
- **FAA SCDS approval** costs no money but does cost lead time and requires
  a signed Service Access Agreement — a real barrier to a buyer, priced at
  $0 but not free of friction.
- **$0.00 cloud LLM spend** — measured, not assumed (§1.5).

---

# SECTION B — Full global-entity suite (illustrative, not this instance)

> ## 🚫 **This section is not implemented on this deployment. It exists to show the addressable ceiling for a global-entity build-out, not to inflate this instance's COGS.**
>
> **Nothing in Section B is summed into Section A, §4.1, or any avoided-cost
> figure for this platform.** Every source below is either a documented
> roadmap item in `docs/DATA_SOURCES.md` with **no code path**, or a global
> vendor this deployment has never contracted with.

### 2.9 Section B — verification that these are genuinely not built

Spot-checked before pricing, so this section cannot be mistaken for
delivered capability (the same error the sibling audit caught in
`DEPLOYMENT_COST_PROJECTION` §2's MCP row):

```
$ grep -rl "METEOFRANCE\|JMA_API\|NAIPS\|DWD_\|KMA_\|BOM_\|CMA_\|G_INFO" src/ scripts/
(no output — zero hits for any of them)
```

`docs/DATA_SOURCES.md` documents these across ~39 mentions, but as
**acquisition roadmap**: portal URLs, eligibility notes, and pre-written
enquiry email templates (e.g. lines 164–181 for FAA LADD, 379–383 for UK CAA
G-INFO), plus empty env stubs such as `METEOFRANCE_API_KEY=` at line 419.
Two entries are wired but credential-blocked, not coded-blocked:
`eurocontrol` and `jasdat` both sit at `awaiting_credentials` in
`feed_state` as graceful-skip stubs, and `international_aviation_feed`
holds **0 rows**. The only LADD-adjacent code hit is
`src/poller/fetchers/faa_registry.py`, which handles the *US* registry.

**Conclusion: none of Section B is delivering data. $0 creditable to this
instance from anything below.**

### 2.10 Section B — global suite pricing (market-sizing only)

All accessed **2026-08-19**. Same category key: **(a)** own published
page/tariff · **(b)** third-party · **(c)** stale (date given).

**B.1 — National meteorological services.** The dominant pattern is a
*free open-data track alongside a live paid tariff* — not "now free":

| Body | Open track | Paid product | Price | Source | Cat |
|---|---|---|---|---|---|
| **Météo-France** | FREE, Licence Ouverte/Etalab, commercial reuse OK — <https://donneespubliques.meteofrance.fr/?fond=faq&id_dossier=5%2F1000> | Radar animation France, 5 min | **€7,790/yr** (HT) | Barème commercial v13, eff. 2024-10-01 — <https://services.meteofrance.com/sites/default/files/2025-11/MF_GT___BPSC.pdf> | (a) |
| Météo-France | — | Lightning feed, 5 min (Météorage) | **€6,419/yr** | same tariff | (a) |
| Météo-France | — | PreviExpert bulletin | **€1,039/yr** | same tariff | (a) |
| Météo-France | — | Vigimet Flash (alerting) | **€208/yr** | same tariff | (a) |
| **DWD** | FREE, CC BY 4.0, no registration, per DWD-Gesetz — <https://www.dwd.de/DE/leistungen/opendata/faqs_opendata.html> | **Aviation weather data feed** | **€95/mo ⇒ €1,140/yr** (net) | Preisliste eff. 2024-07-01, amended 2025-12-12 — <https://www.dwd.de/SharedDocs/downloads/DE/allgemein/preisliste_2024.pdf?__blob=publicationFile&v=11> | (a) |
| DWD | — | AUTO TAF / TAF-Guidance (non-Eurocontrol) | **€50.40/mo ⇒ €604.80/yr** | same | (a) |
| **JMA** | FREE, Public Data License v1.0 — <https://www.jma.go.jp/jma/en/copyright.html> | — | — | — | (a) |
| **JMBSC** (JMA's designated commercial redistributor) | — | Online feed: ¥50,000 setup + ¥4,200/mo base + ¥1,000/mo per data type + ¥3,000/mo per ID + ¥30/GB | **≈ ¥98,400/yr + ¥50,000 setup** | <https://www.jmbsc.or.jp/jp/online/c-onlineC.html> | (a) |
| JMBSC | — | Offline datasets (e.g. JRA-55 reanalysis ¥450,560; Himawari full-disc ¥310,310) | one-off per dataset | <https://www.jmbsc.or.jp/jp/offline/data/cd_list.pdf> | (a) |
| **KMA** | FREE, KOGL Type 1, commercial reuse OK, 20k calls/day — <https://apihub.kma.go.kr/apiInfo.do> | GK-2A satellite commercial tier | **not publicly priced** | <https://nmsc.kma.go.kr/enhome/html/base/cmm/selectPage.do?page=satellite.gk2a.dataServicePlan> | (a) |
| **BoM** (Australia) | FREE CC BY for catalogued datasets | Registered User: $1,335 setup + $1,282/yr FTP; product subscriptions **AUD $183 – $38,106/yr**; GIS2Web $4,304/yr | as listed (AUD, FY2026/27, GST inc.) | <https://reg.bom.gov.au/other/charges.shtml> | (a) |
| **CMA** (China) | — | **Not publicly priced** — no fee schedule found in English or indexed Chinese pages | — | <https://data.cma.cn/en/> | (a) negative |

**B.2 — Aviation authorities / aeronautical information:**

| Body | Product | Price | Source | Cat |
|---|---|---|---|---|
| **UK CAA G-INFO** | Online search | **FREE** | <https://www.caa.co.uk/aircraft-register/g-info/search-g-info/> | (a) |
| UK CAA G-INFO | Bulk download: single issue £450 · quarterly **£745/yr** · monthly **£1,745/yr** · corporate licence £1,855 (all incl. VAT) | as listed | <https://www.caa.co.uk/aircraft-register/g-info/g-info-forms-and-fees/> | (a) |
| **Airservices Australia NAIPS** | Briefing/flight-plan service for registered pilots | **FREE** — "no fees charged for user registration or the use of NAIPS via the NIS" | <https://www.airservicesaustralia.com/naips/Content/Files/documents/NAIPS-Internet-Service-FAQ.pdf> | (a) |
| Airservices Australia | Bulk electronic aeronautical data (25 products) | **not publicly priced**; only published figure is an **AUD $355** minimum copyright-licence fee | <https://data.airservicesaustralia.com/data-products> · <https://www.airservicesaustralia.com/industry-info/aeronautical-information-management/electronic-data/copyright-licence-request-process/> | (a) |
| **EUROCONTROL NM B2B** | Network Manager B2B web services | **Not simply free.** First 2 digital certificates per location free, then **€200 per additional certificate** (one-off, 3-yr validity); eligibility gated to ANSPs/operators/airports/ground handlers. A new charging scheme is flagged "under consideration… for roll out in 2025/2026" | <https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services> | (a) |
| **FAA LADD** | — | **FREE, and not a data product.** A privacy/opt-out program under the 2024 FAA Reauthorization Act §803, letting owners filter their data from SWIM redistribution and public trackers. **It cannot be "acquired" at any price** — pricing it would be a category error | <https://www.faa.gov/pilots/ladd> (403 to fetch; corroborated <https://nbaa.org/aircraft-operations/security/privacy/limiting-aircraft-data-displayed-ladd/>) | (a)/(b) |
| **Jeppesen** (Boeing) | NavData "Americas" **$349/yr**; Charts "all North America" **$449/yr** | GA/consumer tier | <https://generalaviationnews.com/2024/07/20/jeppesen-cuts-prices-simplifies-navdata-and-chart-coverages-for-garmin-and-avidyne-panels/> | **(c) 2024 trade press** |
| Jeppesen / **Lido** (Lufthansa Systems) / **NAVBLUE** (Airbus) | Airline-grade aeronautical data & flight planning | **All quote-only** | <https://ww2.jeppesen.com/navigation-solutions/flitedeck-pro/> · <https://www.lhsystems.com/solutions/operations-control-center/lido-flight-4d> | (a) |

**B.3 — Global flight-data feeds (the largest line, and the least priced):**

| Vendor | Product | Price | Source | Cat |
|---|---|---|---|---|
| **FlightAware** | **Firehose** (global real-time feed) | **Not publicly priced** — "fixed price licensing agreement," negotiated per customer, confirmed by FlightAware staff on their own forum | <https://www.flightaware.com/commercial/firehose/> · <https://discussions.flightaware.com/t/firehose-cost/16722> | (a) |
| FlightAware | AeroAPI (query-based, *not* Firehose) | Standard **$100/mo min**; Premium **$1,000/mo min** (includes Aireon space-based ADS-B) | <https://www.flightaware.com/commercial/aeroapi/> | (a) |
| **Cirium** | FlightStats Flex APIs | Not publicly priced; third-party average contract **~$30,530/yr** | <https://developer.flightstats.com/getting-started/pricing> · <https://www.vendr.com/buyer-guides/cirium> | (a)/(b) 2025 |
| **OAG** | Flight Info API | Not publicly priced beyond a free trial; RapidAPI listing reports $249–$449/mo (unconfirmed) | <https://www.oag.com/flight-info-api> | (a)/(b) |
| **Spire Aviation** | Aviation Data Plans | Not publicly priced ("talk to sales"); third-party ">$10K/month" | <https://spire.com/aviation/aviation-data-plans/> | (a)/(b) |
| **Aireon** | Global space-based ADS-B | **Not publicly priced** — ANSP/B2B model, no end-customer price list exists | — | (a) negative |
| **Flightradar24** | API Advanced tier | **$900/mo ⇒ $10,800/yr** (4.05 M credits) | <https://geekflare.com/guides/flight-data-api/> — ⚠️ FR24's own pricing page returned 403/404 on every direct attempt | (b) |
| **ADS-B Exchange** | Enterprise API | **Not publicly priced** — "minimum annual commitment" | <https://www.adsbexchange.com/data-products/> | (a) |

**B.4 — Global ACARS/VDLM: confirmed to have no purchasable equivalent.**
For both **SITA** (AIRCOM) and **Collins/ARINC** (GLOBALink), every source —
official and third-party — describes only the **operate side**: an airline
paying a Datalink Service Provider to carry *its own* messages. **No
standing commercial product exists that sells an unaffiliated third party a
raw receive-side ACARS/VDL feed**, at any price, globally. This is a
direct-fetch-confirmed negative finding, and it is the same conclusion as
Section A row 8. Sources: <https://www.sita.aero/solutions/sita-for-aircraft/data-and-platforms/aircom-serverplatform/>,
<https://www.collinsaerospace.com/what-we-do/industries/commercial-aviation/connected-cockpit/arinc-globalink>.
Global receive-side coverage is therefore not a *procurement* problem but a
*receiver-network* problem — i.e. it can only be built, not bought, which is
arguably the strongest strategic point in this entire document.

### 2.11 Section B total — separate, and not part of this instance

**Currencies are NOT summed.** Converting to a single USD figure requires a
dated FX rate that was not sourced in this pass; inventing one would break
this document's own rule. Native-currency subtotals of the
**published, recurring, annualisable** prices only:

| Currency | Recurring annual subtotal | One-time | Composition |
|---|---|---|---|
| **EUR** | **≈ €17,201 / yr** | €200 (EUROCONTROL extra cert) | MF radar €7,790 + MF lightning €6,419 + MF PreviExpert €1,039 + MF Vigimet €208 + DWD aviation €1,140 + DWD AUTO TAF €604.80 |
| **GBP** | **£1,745 / yr** | — | UK CAA G-INFO monthly subscription |
| **AUD** | **$1,465 – $39,388 / yr** | $1,335 setup (+$355 licence) | BoM FTP registration $1,282 + one product subscription, floor-to-ceiling of the published $183–$38,106 range |
| **JPY** | **≈ ¥98,400 / yr** | ¥50,000 setup | JMBSC online feed: (¥4,200 base + ¥1,000 per data type + ¥3,000 per ID) × 12 |
| **USD** | **≈ $42,128 / yr** | — | Cirium $30,530 + FR24 Advanced $10,800 + Jeppesen GA NavData/Charts $798 |

**Explicitly excluded from these subtotals** (quote-only, so unpriceable):
FlightAware Firehose · Spire Aviation · Aireon · OAG · ADS-B Exchange
Enterprise · Jeppesen/Lido/NAVBLUE enterprise · SITA · Collins ARINC ·
Airservices bulk data · CMA · KMA GK-2A commercial tier. **These are almost
certainly the largest line items in a real global build-out**, so the
subtotals above are a *floor on the priced fraction*, not an estimate of
total global cost.

> **Section B reading, stated carefully:** a global-entity build-out would
> face **tens of thousands per year in *published* data tariffs alone**
> (≈€17.2k + £1.7k + A$1.3–39k + ¥98k + ≈US$42.6k), **plus an unknown and
> probably larger sum in quote-only enterprise contracts**, **plus** a
> physical receiver network for ACARS/VDLM that cannot be bought at all.
> **None of this is a cost this deployment avoids, because this deployment
> does not do any of it.** It is presented solely to size the addressable
> ceiling.
>
> **Two honest counterweights before anyone reuses these figures:**
> 1. **Most national met data is now free.** Météo-France, DWD, JMA, KMA and
>    NAIPS all publish open, commercially-reusable data at $0. A global
>    build-out that only needed *observations and model output* could get
>    most of the way for free — the paid tariffs above are for
>    *value-added/aviation-specialist* products, and a build-out might
>    legitimately skip them. Treat the EUR subtotal as an upper-bound
>    shopping list, not a required spend.
> 2. **These are list prices for products, not an integration.** Buying all
>    of the above yields feeds, not a platform. The §3 argument still has to
>    carry the valuation.

---

# SECTION C — Subscription-only COGS (no data shared back)

> **The counterfactual.** Section A prices what this deployment actually
> pays *today*, under a **reciprocal-feeder posture**: we receive
> FlightAware Enterprise, FR24 Contributor and AirNav RadarBox Business at
> no cash cost **because we continuously send those vendors our own
> aircraft-sighting data**. Section C models the opposite policy — **pay
> cash for everything, share nothing with anyone** — at **identical
> fidelity**: same feeds, same coverage, same cadence. Only the
> *acquisition model* changes, never the scope.
>
> **Sections A and B are unchanged. This is additive.**

### 2.12 What actually changes, and what does not

The no-sharing constraint only bites where access was **earned with data**.
Everything obtained by FAA vetting, by public-domain licence, or by our own
radio receivers is untouched.

| Row | Section A basis | Affected by "share nothing"? | Section C treatment |
|---|---|---|---|
| SWIM FDPS / TBFM / ITWS / FNS (4 of 6 live) | FAA SCDS vetting + signed SAA — **not** reciprocal | **No** | Carried forward unchanged, **$0** |
| SWIM STDDS / TFMS | Units `inactive (dead)` | No | **$0 — still not live** |
| NWWS-OI / METAR / NWS forecast | NOAA public domain | No | Carried forward, **$0** |
| NOTAM (via FNS push) | FAA free | No | Carried forward, **$0** |
| Amtrak | Free community API + free GTFS | No | Carried forward, **$0** |
| TFR / NAS / ATCSCC / FAA & OpenSky registries | Free public downloads | No | Carried forward, **$0** |
| OSINT / RSS | Our own RSS polling — **already $0 actual** | No | Carried forward, **$0 actual** (see note below) |
| **ADS-B vendor accounts** | **Reciprocal barter** | **YES — this is the whole delta** | **§2.13** |
| **OpenSky API allowance** | **Contribution-tiered** | **YES** | **§2.14** |
| **ACARS / VDL-M2** | Own VHF receiver | No — and unpurchasable anyway | **§2.15** |
| Own ADS-B receiver itself | Our own RF hardware | **No** | Unchanged — we keep receiving; we simply stop *transmitting* |

> ⚠️ **One correction to the framing of this request.** OSINT/RSS was
> described as "already a paid subscription model in your Section A
> pricing." It is not. In Section A the $600–$1,080/yr is the **commercial
> equivalent we avoid**, not a bill we pay — the platform polls 22 free RSS
> and Google-News feeds itself. Its **actual** cost is **$0** in both
> Section A and Section C, and it is carried forward as $0. Booking it as a
> Section C outflow would overstate the subscription-only total by up to
> $1,080/yr.

### 2.13 ADS-B under the no-sharing constraint — the core finding

**Stop feeding, and all four reciprocal accounts lapse.** FR24 states the
Contributor plan lasts "for as long as your feed remains active"; RadarBox
auto-upgrades on feed detection; FlightAware grants Enterprise for feeding.
To hold fidelity we must buy them back. Published retail prices:

| Vendor | Tier we currently get free | Retail price | Annual | Source (accessed 2026-08-19) | Cat |
|---|---|---|---|---|---|
| FlightAware | Enterprise (single user) | **$99.95/mo** | **$1,199.40** | <https://go.flightaware.com/enterprise-and-enterprise-wx-multi-user-monthly-pricing> — page footer reads **"© 2026 FlightAware"** but the schedule is headed **"New Pricing (As of 1/17/2023)"** | (a) — *currently published, last revised 2023-01-17* |
| Flightradar24 | Business / "Contributor" | — | **$499.99** | Search-index snapshot of <https://www.flightradar24.com/premium> — ⚠️ **the vendor page itself returned HTTP 403 on every direct attempt** | (b) |
| AirNav RadarBox | Business | — | **$399.00** | <https://en.airnavradar.com/blog/benefits-of-being-a-radarbox-ads-b-feeder> (vendor's own blog); `airnavradar.com/subscribe` returned **403** | (b) |
| PlaneFinder | Premium | $3.99/mo | **$19.99** | Search-index snapshot of <https://planefinder.net/plans>; `api.planefinder.net/pricing` returned **403** | (b) |
| **Replace all four, like-for-like** | | | **$2,118.38 / yr** | | |
| **Minimum single substitute** (RadarBox Business alone) | | | **$399.00 / yr** | | |

**Range used for Section C: $399 – $2,118 / yr.** The low bound assumes the
four accounts are largely redundant — they are all global flight-tracking
front-ends — so one suffices. The high bound assumes strict like-for-like
replacement. **Which is correct is a product judgement, not a research
finding, and is flagged as an ASSUMPTION.**

**⚠️ Fidelity mismatch — stated plainly rather than force-fitted.** The
request asked whether the paid SKUs actually match what we run. **They do
not, in either direction, and the mismatch matters more than the price:**

1. **No vendor sells "local ADS-B coverage at your own site."** Every
   product above sells access to *the vendor's aggregated global network*
   via their app or API. Our receiver produces a **raw local RF picture**:
   ~26 msg/s ≈ 2.3 M messages/day of Mode-S/BEAST, sub-second, from
   **5 aircraft, max 29.1 nm, median 6.7 nm**. **There is no SKU for that.**
   It can only be built, not bought.
2. **A subscription is strictly *less* capable in the dimension that
   matters most here.** `docs/DATA_SOURCES.md:152` records that
   independent receiver networks "are not FAA-source-derived and are not
   bound by LADD restrictions," whereas FlightAware and FR24 honour
   LADD/blocking. **Our own receiver sees blocked and LADD-filtered
   aircraft; a paid subscription does not.** For an executive-protection
   and discreet-movement platform that is the single most valuable property
   of own-RF, and **no amount of money buys it back.**
3. **The paid tiers are scoped far above our actual usage.** FlightAware
   Global runs Silver $600/yr, Gold $1,680/yr, Platinum $3,540/yr
   (<https://www.flightaware.com/commercial/global>) — per-tail *fleet*
   tracking. AeroAPI's Standard tier carries a **$100/mo minimum** and
   Premium **$1,000/mo minimum**
   (<https://www.flightaware.com/commercial/aeroapi/>). FR24's API Advanced
   tier is **$900/mo** for 4.05 M credits. **Our real need — a 5-aircraft
   local picture — sits below the smallest commitment any of these vendors
   offers.** The floor is set by minimum contracts, not by our consumption:
   **we would be paying for scale we do not use, to obtain a filtered
   product that is worse at our actual job.**
4. **The raw-data route is worse value still.** If the goal were a
   machine-readable feed rather than a viewing account, the comparable
   products are AeroAPI ($1,200/yr minimum), FR24 API Advanced
   ($10,800/yr), or ADS-B Exchange Enterprise (**quote-only**, "minimum
   annual commitment"). **Priced into Section C's range? No** — deliberately
   excluded, because buying a global API is a *scope increase*, and the
   brief fixed scope. Recorded here so the omission is visible.

### 2.14 OpenSky Network — contribution-only, with no paid alternative

Confirmed as a sixth outbound recipient: the ultrafeeder Quadlet carries
`OPENSKY_USERNAME` and `podman inspect` resolves
`collector.opensky-network.org` as a live connector.

OpenSky's access model is **tiered by contribution, not by payment**:
registered non-contributors receive **4,000 API credits/day**; users with a
receiver at least **30 % online** receive **8,000/day**
(<https://openskynetwork.github.io/opensky-api/rest.html>, accessed
2026-08-19; the `opensky-network.org/data/apidoc` page returned 403).

> **Section C consequence: OpenSky API allowance halves, and there is
> nothing to buy.** No paid tier for non-contributors was found. Cash cost
> of the policy change: **$0.** Capability cost: **–50 % of API
> allowance, unpurchasable.** This is the clearest illustration of the
> section's theme — *some access is simply not for sale at any price.*
>
> Unaffected: the **bulk OpenSky aircraft registry** (`opensky_aircraft_registry`,
> 519,991 rows) is a separate open CSV download, not the API, and carries
> forward at **$0**.

### 2.15 ACARS / VDL-M2 — restated so it is not mistaken for barter

**Unchanged from Section A row 8, and unchanged by this policy.** ACARS
reception is not reciprocal — it comes from our own VHF RTL-SDR
(`dumpvdl2` → `acarsrouter` → `acarshub`, 641 msgs in 24 h). Nothing is
traded for it, so nothing is lost by ceasing to share.

**And there is nothing to subscribe to.** Confirmed for both SITA (AIRCOM)
and Collins/ARINC (GLOBALink): every product describes the **send side** —
an airline paying a Datalink Service Provider to carry *its own* traffic.
**No commercial receive-side ACARS/VDLM feed exists for an unaffiliated
third party, at any price, anywhere.** So this row is **$0 in Section C not
because it is free, but because it is unbuyable** — a materially different
statement, and the distinction the request asked to preserve.

*(Caveat carried forward: `acars_messages` in the platform DB is still
**0 rows**, so the RF layer works but the pipeline into the platform does
not. Section C prices the capability as configured, not as delivered.)*

### 2.16 Section C total, and the price of the policy

| Line | Section A (today, mixed barter/free) | Section C (subscription-only, share nothing) |
|---|---|---|
| SWIM (4 of 6 live) | $0 | **$0** |
| NOTAM / FNS | $0 | **$0** |
| NWWS-OI / METAR / NWS | $0 | **$0** |
| Amtrak | $0 | **$0** |
| TFR / NAS / ATCSCC / registries | $0 | **$0** |
| OSINT / RSS (self-polled) | $0 | **$0** |
| ACARS / VDL-M2 | $0 (own RF) | **$0 — unbuyable** |
| OpenSky API | $0 (8,000 credits/day, earned) | **$0 — but allowance halves to 4,000/day, no paid remedy** |
| **ADS-B vendor accounts** | **$0 — reciprocal barter** | **$399 – $2,118** |
| Electricity (§2.7, unchanged — receivers still run) | $23 – $39 | **$23 – $39** |
| **TOTAL ACTUAL RECURRING** | **$23 – $39 / yr** | **$422 – $2,157 / yr** |
| One-time hardware | ~$765 | ~$765 (unchanged) |

> ### Headline
>
> **Section A actual recurring cost: ≈ $23 – $39 / yr.**
> **Section C actual recurring cost: ≈ $422 – $2,157 / yr.**
>
> **The price of a strict no-data-sharing policy is $399 – $2,118 / yr.**
> On midpoints the recurring bill rises from **~$31/yr to ~$1,290/yr — a
> ~42× increase**, though still under ~$2.2k/yr in absolute terms. Every
> dollar of that delta is the ADS-B vendor accounts; nothing else in the
> stack is barter-dependent.
>
> **Three findings that matter more than the number:**
>
> 1. **Most of the platform is policy-proof.** 8 of 10 feed groups are
>    unaffected, because they rest on FAA vetting, public-domain licences,
>    or our own antennas. **A no-sharing policy does not threaten the data
>    architecture** — it costs at most ~$2.1k/yr and touches one layer.
> 2. **The money cannot buy back the capability.** What lapses is
>    *unfiltered local reception* (not LADD-bound, per
>    `DATA_SOURCES.md:152`) plus half the OpenSky allowance. What $2,118
>    buys is *filtered global viewing accounts*. **These are different
>    products.** Section C's total is therefore an **upper bound on cash**
>    and simultaneously a **capability downgrade** — the platform would pay
>    more and see less.
> 3. **It cuts the other way for the sovereignty claim.** §1.5 notes the
>    box feeds six third parties, undercutting "nothing routes through a
>    third party's servers." **Section C prices the fix at $399–$2,118/yr.**
>    If data sovereignty is a selling point, this is what it costs to make
>    it literally true — and that is a genuinely cheap remedy for a
>    material claim.
>
> **UNVERIFIED in Section C:** FR24 Business $499.99/yr, RadarBox Business
> $399/yr and PlaneFinder Premium $19.99/yr all rest on **search-index
> snapshots or vendor blog copy** — all three vendors' own pricing pages
> returned **HTTP 403** to direct fetch. The FlightAware $99.95/mo figure
> *was* fetched directly and the page is live under a **© 2026** footer,
> but its schedule is headed **"As of 1/17/2023"** — current, yet
> three years unrevised. **The single-vs-four-account choice
> ($399 vs $2,118) is an ASSUMPTION, not a finding.** Retail prices should
> be re-checked from a browser before this range is quoted externally.

---

# SECTION D — Full Subscription-Replication COGS

> **Supersedes Section C's framing.** Section C asked a narrow question — *what
> does ending ADS-B barter cost?* — and correctly answered "$399–$2,118/yr,
> because 8 of 10 feed groups rest on FAA vetting or public-domain licences."
> That framing was right for its question and **wrong for the one the operator
> is actually asking**, which is:
>
> **What would it cost, annually, to replicate this entire platform — every
> vertical, at the fidelity currently run — buying only commercial
> subscriptions, taking nothing by free-government-status, and sharing no data
> with anyone?**
>
> Under that constraint "the FAA gives it away" is no longer an answer, because
> a commercial replicator does not have FAA-vetted access. Unlike Sections A–C,
> **quote-only vendors are not excluded here** — where a price is genuinely
> unpublished, the best credible secondary anchor is used and labelled, and
> where no anchor exists at all that is reported as a finding rather than a
> blank. **Sections A, B and C are unchanged.**

### 2.17 Methodology guardrail — how this section avoids the error that broke the original estimate

The first COGS figure in this doc set failed by **stretching two ADS-B-only
quotes into a "per feed type" rate card applied across unrelated feeds** — one
price standing in for things it did not cover. Section D's temptation is the
**mirror image**: summing FlightAware Firehose *plus* Cirium *plus* Spire
*plus* ADS-B Exchange Enterprise would produce an impressive total that is
simply **one category counted four times**, since all four sell overlapping
flight position/status data. Every individual citation would be correct and
the total would still be nonsense.

Three rules are therefore enforced throughout, and every row states which
applies:

1. **Within a category, vendors are SUBSTITUTES → take one, or show a range
   across them. Never sum.**
2. **Across categories, products are COMPLEMENTS → these sum.** No vendor
   found sells more than one of: flight data, weather, AIS, NOTAM, rail,
   OSINT, LLM inference, document storage, vector search, push delivery.
3. **Every price cites its own source for its own scope.** Specifically:
   Cirium's ~$30,530 is Vendr's *average contract value for FlightStats Flex
   APIs — flight status and schedules*. It is **not** used as a price for
   TFMS/TBFM/STDDS NAS operational data, which is a different data class.
   Where a data class has no product, the row carries no dollar figure.
4. **Where an assumption forks the total, both totals are shown** rather than
   one silently chosen.

**"Verified" below means re-derived this pass.** Secondary anchors are
labelled **(b)** with vintage; anything reachable only through a search-index
snapshot after an HTTP 403 says so.

> ⚠️ **Research-tooling gaps, disclosed rather than papered over.**
> `faa.gov` (domain-wide), `flightradar24.com`, `cirium.com`,
> `support.flightaware.com`, SEC EDGAR full-text search, and
> USASpending/FPDS/SAM.gov (POST-only JS apps) **all returned 403 or were
> unreachable** across every research pass. This blocked primary-source
> confirmation of FAA SWIM eligibility policy, FR24's blocking policy, and
> every government-contract dollar anchor. Affected findings rest on
> secondary corroboration (NBAA, Vendr, vendor forums) and say so inline.

### 2.18 Section D — the nine categories

Confidence key: **✅ verified** (re-derived this pass from a directly-fetched
vendor page) · **⚠️ secondary** (credible third-party anchor, vintage given) ·
**❌ UNPRICEABLE / NO PRODUCT** (explicit negative finding).

| # | Vertical | Fidelity currently run | Commercial product | Price | Conf. |
|---|---|---|---|---|---|
| **1** | **SWIM-class flight data** *(substitutes — one taken)* | Solace push, per-feed VPN + durable queue; `flight_events` 891k rows, 1,307 airlines | **Cirium** FlightStats Flex APIs — *the only option with any price anchor*. Covers flight status/position **only** | **$30,530/yr** <https://www.vendr.com/buyer-guides/cirium> (avg contract value, 5+ deals / 3 buyers) | ⚠️ secondary, 2025 vintage, re-fetched 2026-08-19 |
| 1a | *— same category, not summed* | | **FlightAware Firehose** — best coverage (adds surface positions + obfuscated blocked flights) | **❌ UNPRICEABLE.** "Total monthly pricing is established on a per customer basis" <https://www.flightaware.com/commercial/firehose>; staff confirm "fixed price licensing agreement" <https://discussions.flightaware.com/t/firehose-cost/16722> | ✅ verified negative |
| 1b | *— same category, not summed* | | **Spire Aviation** | **❌ no usable anchor.** The circulating ">$10,000/month" traces to a single unverified aggregator and **could not be corroborated**; Spire's own Q2 2026 investor materials (2026-08-12) disclose **no** aviation-segment revenue, ARR or per-customer figure | ✅ verified negative |
| 1c | *— same category, not summed* | | **ADS-B Exchange Enterprise** | **❌ no anchor of any kind.** "Ongoing subscription services with minimum annual commitments," no figure <https://www.adsbexchange.com/data-products/> | ✅ verified negative |
| 1d | *— not a substitute at all* | | **L3Harris** | **❌ sells no commercial SWIM data product.** It is the FAA's *infrastructure contractor*. Its ~$2.1B cumulative FTI obligations are network spend — **explicitly not conflated with a data price** | ✅ verified negative |
| 1e | *— not a substitute at all* | | **Mosaic ATM "Fuser"** | **❌ quote-only, and it presupposes you already hold SWIM access** — its own page frames it as simplifying "SWIM application deployment." Not a route around vetting | ✅ verified negative |
| **2** | **TFMS** — ground stops, GDPs, airspace flow programs, reroutes | `nas_programs` 22,236 rows | **❌ NO COMMERCIAL PRODUCT EXISTS AT ANY PRICE.** Zero mentions of "ground delay program," "ground stop," "airspace flow program" or "TFM" across Firehose docs, AeroAPI, Cirium or OAG product pages | ✅ **hard gap** |
| **3** | **TBFM** — arrival metering / meter-fix sequencing | `tbfm_sequences` 33,821 rows, 20 facilities | **❌ NO COMMERCIAL PRODUCT EXISTS AT ANY PRICE.** No mention of "TBFM," "metering" or "meter fix" on any vendor page checked | ✅ **hard gap** |
| **4** | **ITWS** — terminal wind-shear / microburst alerts | `itws_alerts`, 7 products × KDCA/KIAD/KBWI | **❌ NO COMMERCIAL PRODUCT EXISTS AT ANY PRICE.** Generic aviation weather is a different data class and is *not* accepted as a substitute here | ✅ **hard gap** |
| **5** | **STDDS** — surface movement / surveillance | `surface_movement_events` 118k, `surface_tracks` 12,285 | ⚠️ **PARTIALLY AVAILABLE — my hypothesis was wrong here and is corrected.** Firehose documents a subscribable **"Surface Positions"** layer: *"Most major USA airports (ASDE-X and ADS-B), Worldwide (ADS-B only)"* <https://www.flightaware.com/commercial/firehose/documentation>. Likely narrower than full STDDS, but "no commercial surface data exists" is **not true** | ✅ verified — **price folded into the unpriceable Firehose line (1a)** |
| **6** | **FNS → NOTAM** | `notams` 5,949 rows / 314 facilities | ✅ **genuinely buyable, and cheap.** Notamify Pro **$298.80/yr** <https://notamify.com/notam-api> (a real standalone NOTAM API, not EFB-bundled); or ForeFlight Starter **$130/yr** <https://foreflight.com/pricing/> which includes NOTAMs | ✅ verified |
| **7** | **LADD / blocked-aircraft visibility** | Own ground receiver — **not FAA-source-derived, so not bound by LADD** (`docs/DATA_SOURCES.md:152`) | **❌ NO COMMERCIAL SUBSCRIPTION BUYS THIS AT ANY PRICE.** Firehose's own docs list *"Obfuscated visibility of Blocked Flights"* as the data layer — i.e. **even the enterprise feed obfuscates**. FlightAware restricts blocked-aircraft detail to the owner/operator <https://flightaware.com/about/faq> | ✅ **hard gap** |
| **8** | **Consumer/prosumer ADS-B** *(a LOWER, INSUFFICIENT tier — not a SWIM substitute)* | ~26 msg/s ≈ 2.3M msg/day, 5 aircraft, max 29.1nm | FA Enterprise $1,199.40 + FR24 Business $499.99 + RadarBox Business $399 + PlaneFinder $19.99 | **$399 – $2,118/yr** (carried from §2.13) | ⚠️ three of four are search-index snapshots after 403 |
| **9** | **Weather** — METAR + NWS forecast | `metar`, `api.weather.gov` 3 zones | Self-serve: Visual Crossing **$420/yr** <https://visualcrossing.com/weather-api/> · WeatherAPI.com Business **$780/yr**. Cheapest published tier from a vendor credibly covering aviation + alerting: **IBM/The Weather Company Standard $500/mo = $6,000/yr** <https://weathercompany.com/weather-data-apis/weather-data-apis-packages-pricing/> | **$420 – $6,000/yr** | ✅ verified |
| 9a | **NWWS-OI equivalent** — real-time NWS warning push | XMPP MUC, WFO filter LWX/AKQ/CTP/PHI | ⚠️ **No vendor publishes a standalone price for NWS-warning redistribution as a discrete feed.** Baron is the closest confirmed product (incorporates NWS watches/warnings) but publishes nothing. DTN, Spire Weather, Meteomatics: all quote-only | **❌ unpriced** | ✅ verified negative |
| **10** | **Maritime / AIS** ⚠️ **NOT LIVE** | `ais_watcher.py` 212 LOC, **no systemd unit**, `vessel_events` 0 rows, and **`permanent_vessels.json` watchlist is empty (0 entries)** vs 13 flights / 304 trains | VesselFinder **€330 / 10,000 credits** (12-mo validity) <https://www.vesselfinder.com/vessel-positions-api> · Kpler platform **~$55,000/yr median** <https://www.vendr.com/marketplace/kpler>. MarineTraffic killed self-serve API pricing; now Kpler quote-only | **€330 – $55,000/yr** | ✅ / ⚠️ secondary |
| **11** | **ACARS / VDL-M2** | Own VHF SDR, 641 msgs/24h (`acars_messages` in DB still **0 rows**) | **❌ NO COMMERCIAL RECEIVE-SIDE PRODUCT EXISTS AT ANY PRICE.** SITA AIRCOM and Collins/ARINC GLOBALink are *send-side* carrier networks airlines pay to route their own traffic — a different product | ✅ **hard gap** |
| **12** | **Passenger rail (Amtrak)** | `api.amtraker.com`, 21,439 events/24h | **findtrain.com €250/mo (≈€3,000/yr)** <https://findtrain.com/pricing/> — *notable: this is the vendor behind our own dead `ustrains_departures` table (`src/common/db.py:1354`, 0 rows)*. Cheaper: Parse.bot Amtrak API **$30–$100/mo**. Amtrak's static GTFS is free but **has no realtime component**, so it is not a substitute | **$360 – $1,200/yr** (Parse.bot) or **≈€3,000/yr** (findtrain) | ✅ verified |
| **13** | **OSINT / RSS** | 22 scopes, 270 outlets, 74 items/24h | NewsCatcher Starter $50/mo · Event Registry 5K $90/mo — scope-matched *down* to actual volume | **$600 – $1,080/yr** | ✅ verified |
| **14** | **LLM inference** *(second brain)* | 21 local `phi3:mini` models; **17,330 real model calls in 41.32 days = 419.4/day ≈ 153,077/yr** (SR-1 log, excluding `deterministic` and gate-skips) | **Claude Haiku 4.5 @ $1/$5 per MTok** — the fair peer for a 3.8B local model | **$443 – $862/yr** — see §2.19 for the full token derivation | ⚠️ **estimated, assumptions shown** |
| **15** | **Vault storage / sync** | Self-hosted Nextcloud; **5,999 docs, 805.5 MB** (`SUM(size_bytes)`) | Box Business $5/user/mo (3-seat min) or Dropbox Standard $15/user/mo (1 seat) — both land identically | **$180/yr** | ✅ verified |
| **16** | **Semantic layer / concept graph / scheduled digests** | 39,744 concept edges, 96 concepts, FTS+semantic search, 270 briefs/7d | **❌ NO SINGLE COMMERCIAL SKU REPLICATES THIS.** Nothing evaluated combines scheduled LLM brief-generation over a private multi-source corpus with an auto-built concept graph. Assembled floor: vector DB **$540–$600/yr** (Weaviate Flex / Pinecone Standard) **+ custom integration** | **$540 – $600/yr** *(components only)* | ✅ **hard gap on the whole** |
| **17** | **Push notification delivery** | Self-hosted ntfy, 14-topic catalog | ntfy.sh Pro $10/mo (10 reserved topics — **below our 14**) · Business $20/mo <https://ntfy.sh/#pricing> | **$120 – $240/yr** | ✅ verified |

### 2.19 The LLM row's derivation, shown in full (the one estimated line)

This is the only row built from assumptions rather than a quoted price, so
the arithmetic is exposed for challenge. **SR-1 logs no token counts** (all
21,268 rows have zero in every token column), so tokens are modelled, not
measured.

**Measured inputs:** 17,330 calls that actually reached a model (excluding
`model = deterministic` fallbacks and gate-skips) over **41.32 days** ⇒
**419.4 calls/day ⇒ 153,077/yr**. Call mix from the SR-1 `skill` column:
tfr-enrichment 6,731 · route-impact 5,333 · flight-impact 2,397 ·
ops-brief 1,199 · ep-advance 948 · osint-monitor 450 · other 272.

**Modelled inputs**, weighted by that mix against each Modelfile's own
`num_ctx` / `num_predict` and SYSTEM-block size, converted at the 4 chars/token
estimator in `src/common/llm.py:157`:

| Quantity | Value |
|---|---|
| Weighted `num_ctx` | 4,208 |
| Weighted `num_predict` (output ceiling) | 356 |
| Weighted SYSTEM block | 1,103 tokens |
| Input/call — **low** (SYSTEM + ~900-token payload) | 2,003 |
| Input/call — **high** (fills context: `num_ctx − num_predict`) | 3,852 |
| Output/call — low (50 % of ceiling) / high (at ceiling) | 178 / 356 |
| **Annual volume** | **307–590 MTok in · 27.2–54.5 MTok out** |

| Model | Annual |
|---|---|
| **Claude Haiku 4.5 ($1/$5)** — used in the total | **$443 – $862** |
| Claude Sonnet 5 ($2/$10) | $886 – $1,725 |
| Claude Opus 5 ($5/$25) | $2,215 – $4,312 |
| Haiku 4.5 + Batch API (−50 %) | $222 – $431 |

*Pricing re-checked against the live vendor page 2026-08-19. Note **Sonnet 5
is $2/$10, not $3/$15** — the scheduled 2026-09-01 increase was cancelled.*
**Not applied to the total** (so the figure stays conservative): prompt
caching would cut input cost from $307–$590 to **$155–$438**, since the
~1,103-token SYSTEM block is identical per skill and sits just above the
~1,024-token minimum cacheable prefix. Caching and batch discounts stack.

**⚠️ This row carries real uncertainty and is marked estimated, not verified.**
The high bound assumes prompts fill the context window; the low bound assumes
a modest payload. Actual spend would land somewhere between and could fall
below both with caching enabled.

### 2.20 Hard capability gaps — what no amount of money buys

**This list matters more than the total.** Six capabilities the platform has
today cannot be purchased at any price from any vendor found:

| # | Capability | Why it cannot be bought |
|---|---|---|
| 1 | **TFMS** — ground stops, GDPs, airspace flow programs, reroutes | No commercial vendor sells NAS traffic-flow-management data. Available only through FAA-vetted SWIM access. |
| 2 | **TBFM** — arrival metering, meter-fix sequencing | Same. No vendor page checked even mentions the data class. |
| 3 | **ITWS** — terminal wind-shear / microburst alerts | No vendor sells ITWS-class terminal alerts. Generic aviation weather is a different product and is not accepted as a substitute. |
| 4 | **LADD / blocked-aircraft visibility** | **The sharpest gap.** Firehose's own docs offer only *"Obfuscated visibility of Blocked Flights."* Our own receiver is not FAA-source-derived and so is not bound by LADD at all (`docs/DATA_SOURCES.md:152`; NBAA: *"these protections apply to FAA data systems only"*). **Paying more makes this strictly worse.** |
| 5 | **ACARS / VDL-M2 receive-side data** | SITA and Collins/ARINC sell the send side only. No third-party receive feed exists globally. |
| 6 | **Scheduled LLM briefs over a private corpus + auto-built concept graph** | No single SKU. Enterprise RAG platforms (Glean, Dashworks, Hebbia, Sana) are chat-on-demand, not autonomous scheduled digest generation; the auto-linking products that exist (Mem.ai, Obsidian Smart Connections, Reor) only link notes already inside their own app and ingest no RSS/OSINT. |

**A seventh, softer gap — scale mismatch.** Repeatedly, the only products that
exist are priced for organisations far larger than this deployment:
**Glean** runs ~$50–75/user/mo + ~$15/user/mo AI add-on against a **~100-seat
minimum ⇒ a ~$78,000/yr floor** for a one-operator system; AeroAPI's Standard
tier carries a $100/mo minimum; ADS-B Exchange Enterprise requires an
undisclosed "minimum annual commitment"; Kpler and Vectara are enterprise-only.
**The floor is set by minimum contracts, not by our consumption.**

### 2.21 Section D total

Currencies are **not** summed — a dated FX rate was not sourced, and inventing
one would break this document's rule. EUR items are listed separately.

| Line | Low | High |
|---|---:|---:|
| SWIM-class flight data (Cirium — single anchor) | $30,530 | $30,530 |
| Consumer/prosumer ADS-B | $399 | $2,118 |
| Weather | $420 | $6,000 |
| NOTAM | $130 | $299 |
| Passenger rail (Parse.bot) | $360 | $1,200 |
| OSINT / RSS | $600 | $1,080 |
| LLM inference (Haiku 4.5) | $443 | $862 |
| Vault storage | $180 | $180 |
| Vector DB (semantic components) | $540 | $600 |
| Push delivery | $120 | $240 |
| **CORE TOTAL — live verticals only (USD)** | **$33,722** | **$43,109** |
| Maritime / AIS *(vertical **not live**; documented-intent only)* | €330 | $55,000 |
| **FULL TOTAL — incl. documented-but-unbuilt AIS** | **$33,722 + €330** | **$98,109** |

**Shown separately and deliberately NOT summed** (they are substitutes for
lines already counted, or scale-mismatched):

- **FlightAware Firehose — UNPRICEABLE**, and it is the *only* vendor covering
  STDDS-class surface positions and obfuscated blocked flights. Its absence is
  why the total is a floor.
- Weather enterprise actual-buyer median — **$72,052/yr** (Tomorrow.io, Vendr).
  Substitutes for the $420–$6,000 weather line; would raise the total by ~$66k.
- Glean enterprise floor — **~$78,000/yr**. Substitutes for the $540–$600
  vector-DB line at 100-seat minimum.
- findtrain.com — **≈€3,000/yr**. Substitutes for the Parse.bot rail line.

> ### Section D headline
>
> **Replicating this platform by subscription costs ≈ $33,700 – $43,100 / yr
> for the verticals that are actually live, or up to ≈ $98,100 / yr including
> the documented-but-unbuilt maritime tier** — against **≈ $23–$39/yr** of
> actual recurring cost today (§2.7) and ~$765 of one-time hardware.
>
> **That is a floor, not a ceiling, for three stated reasons:**
> 1. **FlightAware Firehose is unpriceable** and is the only product covering
>    surface positions — the largest single omission.
> 2. **Enterprise actual-buyer data runs far above published tiers** (weather
>    alone: $72k median vs. the $6k list tier used).
> 3. **Six capabilities cannot be bought at all** (§2.20), so the money does
>    not purchase the same platform — it purchases a filtered, thinner one.
>
> **The honest one-line claim:** *this deployment delivers, for ~$31/yr of
> electricity on ~$765 of owned hardware, a capability set whose purchasable
> subset alone lists at ≈$34k–$98k/yr — and whose most operationally valuable
> elements (NAS flow/metering data, blocked-aircraft visibility, receive-side
> ACARS, scheduled-brief automation over a private corpus) are not purchasable
> at any price.*
>
> **What this does NOT establish.** Section D is a *replacement-cost* measure,
> not a valuation and not a revenue claim. It does not mean the asset is worth
> a multiple of $34k–$98k; §3's productization gaps and §1.3's availability
> findings (feeds silently dead for hours, 34.5 % deterministic-fallback rate)
> still apply, and the runsheet still records ~1 trip. It is evidence that the
> **recurring cost base is near zero while the replacement cost is not** —
> which is a genuine and defensible finding, and a different one from §6's
> conclusion about the $65k–$140k development-cost bands.

---

# SECTION E — Intelligence-automation layer

> **Corrects Section D rows 14–16.** Section D priced the second brain as three
> generic, swappable infrastructure SKUs — LLM inference $443–$862, vault
> storage $180, a vector DB $540–$600, totalling **$1,163–$1,642/yr**. That was
> wrong. It priced the *components* and missed the *capability*: a system that
> permanently owns a longitudinal multi-domain corpus, correlates entities
> across verticals, and runs threshold-based pattern detection with a
> human-review gate. **Section E re-derives that properly and supersedes those
> three rows.** Sections A–D are otherwise unchanged.
>
> **Headline: the correct figure is $22,623 – $71,429/yr — 19× to 44× the
> commodity-infra stand-in.** And the single most valuable element still
> cannot be bought at any price.

### 2.22 E.1 — The longitudinal archive: owning vs. renting

**What is actually accumulated** (every populated table's real `MIN`/`MAX`
timestamp and row count, not a sample):

| Metric | Verified value |
|---|---|
| Main DB | **2,797,708 rows** across 45 populated tables |
| Longest continuous span | **53 days**, 2026-06-27 → 2026-08-19 |
| — tables at full span | `hot_alerts` 14,767 · `tbfm_sequences` 33,939 · `notams` 5,947 · `brief_archive` 1,680 · `cps_scores` 1,014 · `atcscc_opsplan` 43 |
| `flight_events` | **900,963 rows / 30 days** |
| `train_events` | **699,948 rows / 22 days** |
| `surface_movement_events` | 118,039 / 16 days · `surface_tracks` 12,285 / 15 days |
| Registries | FAA 316,031 · OpenSky 519,991 |
| **Recorder `demo.db`** | **2.25 GB · 37,524 snapshots · 53 days** |
| Second-brain vault | 6,000 docs · 805.5 MB |

**The demo archiver is the mechanism that makes this real, not a separate
capability.** `src/poller/skills/second_brain_demo_archiver_daily.py` reads
`demo.db` directly (not the privacy-safe playback API) on a 30-hour rolling
window and writes per-endpoint change counts plus the latest full payload into
the vault — deliberately raw feed data across amtrak/notams/weather/route/
opsplan/cps, "so that it has more data than just the ops process plan and the
[operator LLC abbreviation]s' predictive and retroactive look backs on the briefs." That is what
gives the corpus breadth beyond brief narrative.

**What a vendor charges for historical access specifically** (distinct from
live feed, which Section D already prices):

| Vendor | Distinct historical tier? | Price | Source |
|---|---|---|---|
| FlightAware AeroAPI | **Yes** — tier-gated priced endpoints | **$0.020–$0.200 per result set** (`history/flights`, `/track`, `/arrivals`) | flightaware.com/commercial/aeroapi |
| FlightAware Firehose | **No** — folded in as "PITR"; "backfill"/"archive" appear zero times | bespoke | flightaware.com/commercial/firehose |
| **Cirium** | **No — explicit negative finding.** "Historical flight schedules" is a bundled bullet, not a priced tier | — | cirium.com/products |
| Spire Aviation | Partial — named products, unpriced | quote | spire.com/aviation |
| VesselFinder | **Yes** — named "Historical AIS Data" product | custom-quoted by volume | vesselfinder.net/historical-ais-data |
| Kpler / MarineTraffic / Spire Maritime | Partial — "10+ years historical AIS" marketed, no SKU | — | kpler.com/product/maritime/data-services |
| Visual Crossing | **Yes** — 50+ years history | **$0.0001/record**; free 1,000 records/day | visualcrossing.com |
| Meteomatics | Yes — dedicated product | fully sales-gated | meteomatics.com/en/api/historical-weather-data |
| DTN | **No — explicit negative finding** | — | dtn.com/weather |
| NOAA / NCEI | N/A — **free public-domain bulk data** | $0 (only shipping/certification fees exist) | ncei.noaa.gov |

> #### ⛔ The hard finding: **you cannot own this even if you pay.**
>
> Aviation and maritime data are licensed on **term** licences requiring
> cessation of use and destruction on termination — usually with written
> certification. Verbatim, with section numbers:
>
> **OAG §4.2:** *"You understand and agree that the information contained
> within the Products and Services is licenced to You and not sold. We grant
> You a non-exclusive, non-transferable, revocable, worldwide licence… for the
> duration of Initial Term as specified on the Order…"*
> **OAG §10.4:** *"Where the Licence granted under clause 4 terminates, for
> whatever reason, You will cease to have any rights to use the Products, Data
> or receive and/or use the Service. You will at Our sole option (i) return
> the Product and/or the Data to Us, and/or (ii) certify to Us that You have
> removed all Product and/or Data from Your computer systems and have
> destroyed all copies of the Product and/or Data **and any derivatives
> thereof**."* — oag.com/terms-for-online-sales-subscriptions-and-services
> **OAG's destruction duty is the broadest found: it reaches derivatives.**
>
> **Kpler §13.3:** *"Customer shall … cease using the Services and Data, with
> effect on and from the date of termination… No other use of the Data is
> permitted following termination."*
> **Kpler §13.4:** *"…agree[s] to promptly and securely purge and delete from
> its systems … all Data … [and] to provide written confirmation to Kpler
> within thirty (30) days … certifying the complete and irreversible deletion
> and purging of all such Data…"* — kpler.com/company/terms-of-use
>
> **ADS-B Exchange §14(d):** *"Upon expiration or termination … Customer shall
> (a) delete all Company Confidential Information within thirty (30) days …
> and (b) provide written certification, signed by an executive of Customer
> confirming such deletion…"*
>
> **Spire is the one meaningful exception.** §9.2: *"Customer reserves all
> rights … title and interest in and to the Derivative Works created by the
> Customer."* §17.5 still requires ceasing use and destroying **the Data**,
> while acknowledging it *"may persist on archival or backup systems … but
> that the Data will not be used following termination."* So derived works
> survive; the underlying corpus does not.
>
> **FlightAware's operative licence text is private** — its public terms say
> paid data "may be used solely in accordance with the specific terms of any
> additional license provided to you at the time of purchase," and prohibit
> reproduction/distribution absent an express licence. **Cirium's terms exist
> but sit behind Incapsula bot protection and were genuinely unread** — not
> assumed either way.
>
> **Therefore: no amount of subscription spending produces the 53-day (and
> lengthening) owned corpus this platform holds. A subscriber's archive is
> contingent on continued payment and is contractually destroyed on exit.**
> This is a **hard capability gap**, and it is stronger than any price.

> ⚠️ **Honest caveat against the ownership claim.**
> `flight_events_cleanup.py` enforces **30-day live retention**, and
> `flight_events`' oldest row is **29.9 days old** — the boundary is biting
> now. The design is export-to-Nextcloud-then-delete with upload confirmed
> before deletion, so ownership is architecturally preserved. But the skill
> logs only `rc=0` with no "archived N rows" lines, and **no archive tarball
> could be located on disk**. The longitudinal claim therefore rests on an
> archival leg that **could not be verified to have ever produced a file.**
> Fix or verify that before the archive is cited to a buyer.

### 2.23 E.2 — Cross-vertical correlation: the real commercial substitute

Not a $540 vector DB — an enterprise signal-detection platform. **These are
SUBSTITUTES: one is taken, never summed** (§2.17 rule 1).

| Vendor | Median annual contract | Range (n) | Real-time operational domains? |
|---|---|---|---|
| **Recorded Future** | **$70,375** | $27,000–$216,385 (n=47) | **No** — open/dark web, technical feeds, telemetry only |
| **Klue** | $30,000 | $16,000–$60,000 (n=106) | No — competitor web/text only |
| **Crayon** | $30,000 | ~$12,700–$46,000 (n=93) | No |
| **Dataminr** (Corporate Security) | **$22,000** | $15,000–$62,500 | **Partial** — claims "sensor data," undefined; no flight/AIS/weather connector page |
| **AlphaSense** | $17,500 | $9,250–$51,000 (n=38) | No — financial/business documents |
| Kompyte | no figure obtainable | — | No — web/text only |
| Palantir Foundry | deployment-scale only (£330M NHS; $10B US Army) | — | Partial/mostly no — 200+ connectors, **none** for flight/AIS/weather |

All medians are **Vendr (b)**, accessed 2026-08-19; every vendor's own pricing
page is quote-only. **Range used: $22,000 (Dataminr, cheapest with any
real-time claim) – $70,375 (Recorded Future, closest capability match).**

**Note what the money does not buy:** not one of these ingests flight,
maritime, rail and weather operational feeds. Palantir would require building
those connectors yourself on generic streaming infrastructure. **The
correlation substrate this platform correlates over does not exist in any of
these products.**

### 2.24 E.3 — Entity tracking: a second, distinct hard gap

Not a vague "semantic layer." The exact spec, from `src/common/entity_tracking.py`:

- `ROLLING_WINDOW_DAYS = 7`, `RECURRENCE_THRESHOLD = 5`, `DISTINCT_FEED_THRESHOLD = 2` (lines 121–123). Auto-promotes on **≥5 recurrences in a rolling 7-day window OR corroboration by ≥2 distinct feeds**, whichever fires first.
- **11 signal types**; `_NOVEL_SIGNAL_TYPES = SIGNAL_TYPES - {"routine"}` — routine is the only type that may auto-promote silently. Everything else gets human eyes first, in `00-Inbox/cross-link-findings/`.
- **Never** auto-creates a top-level category; structural decisions always route to a human.
- First-mover triggers: deterministic (`first_seen == today`) plus LLM-tagged non-routine signal type — including `absence_notable` and `absence_embargo`, i.e. **detecting that expected reporting did *not* happen**.

**It is running, not scaffolding** (`rss_entity_tracker.json`, 590 KB):
**336 entities** across 6 categories · **15 auto-promoted** · **1,103
mentions** · **259 cross-link-findings notes indexed in the vault**. Signal
mix: routine 436, other 214, **first_mover_jv 156, absence_embargo 141**,
policy_change 41, new_venture 40, market_signal 40, training_cert 15, legal
14, threat_intel 4, absence_notable 2. **667 of 1,103 (60.5%) were non-routine
and routed to human review — including 143 detections of notable silence.**

> #### ⛔ Hypothesis tested across all ten vendors: **CONFIRMED, no exceptions.**
> **No commercial product documents (iii) a user-visible numeric recurrence
> threshold, (iv) independent-feed corroboration gating before promotion,
> (v) a human review queue for novel findings, or (vi) silence/embargo
> detection.** Crayon has "AI importance scoring" and Kompyte "AI filters out
> the noise" — neither is a documented threshold. Recorded Future's Insikt
> Group is a human analyst team, but nothing establishes it as a review gate
> on automated findings.
>
> **This is a distinct gap on top of §2.23's correlation gap, not a restatement
> of it.** Even after paying $70,375/yr for Recorded Future, the
> threshold-based auto-promotion, the corroboration gate, the human holding
> area, and silence detection would all still have to be built.

### 2.25 E.4 — Security stack: real, and almost free at this scale

Verified live: rootless Podman (`Rootless: true`), SELinux `Enforcing`, **24
quadlets on `Network=pasta:--map-gw` and `Network=host` count exactly 0**
across 61 quadlets, Tailscale `Running`, `cloudflared` active, `X-CTDI-Public`
enforced at `src/auth/auth.py:68`, GPG manifest `Good signature` over 685
files, and **32 `require_admin("…")` call sites** (23 on write verbs) each
writing an actor-identified, **payload-capturing** audit row at
`src/auth/auth.py:197`, with `audit_log_prune.py` `RETENTION_DAYS = 90` wired
daily at `poller/main.py:95`.

| Layer | Scale-matched commercial equivalent | Cost |
|---|---|---|
| Edge WAF / DDoS | Cloudflare **Free** (WAF + unmetered DDoS included) | **$0** |
| ZTNA / mesh VPN | Tailscale **Personal, ≤6 users**; Twingate free ≤5; NetBird free ≤5; CF Zero Trust free ≤**50** | **$0** |
| IAM / tokens | Auth0 (25k MAU), Cognito (10k MAU), Keycloak, FusionAuth, Stytch — all free here | **$0** |
| SIEM / audit | Datadog Log Management, no minimum commit | **<$1/mo ≈ $12/yr** |
| Supply-chain integrity | **NO PRODUCT EXISTS** | **hard gap** |
| SR-1/SR-2 runtime gate | **NO PRODUCT EXISTS** | **hard gap** |

**Tailscale's free tier is not an outlier — it is the market standard at 1–3
users.** Okta is the *only* vendor that would cost money, and only via a
**$1,500/yr contract floor** (3 seats list at ~$216/yr).

**Two more hard gaps, confirmed with zero exceptions:**
- **Signed whole-tree manifest with an execution gate.** All of Chainguard,
  Sigstore, GitHub Artifact Attestations, Docker Scout, Snyk, JFrog Xray,
  Anchore and Sysdig operate on container images, SBOMs or runtime behaviour.
  **None signs an entire source tree and refuses to execute on mismatch.**
- **SR-2's content-hash gate.** Langfuse/LangSmith/Arize confirm logging is
  commodity and gating absent. Helicone Cache and Portkey are nearest but
  hash the *whole request*, **return a stored response rather than a true
  no-op skip**, and are global caches, not a per-skill idempotency gate
  against that skill's own last successful run.

> **Enterprise-tier contrast — shown, and deliberately NOT summed.** One
> vendor per category at Vendr medians (Cloudflare Enterprise $21,600 +
> Tailscale Standard $288 + Okta $1,500 + Splunk $94,200 + Chainguard $49,250
> + Langfuse $348) ≈ **$167,000/yr**. For a one-operator deployment that is
> **evidence of scale mismatch, not savings** — Akamai's floor alone is
> ~11,000× realistic edge spend. Booking it as avoided cost would be the
> inflation error §2.17 exists to prevent.

### 2.26 E.5 — Portability: valued qualitatively, on purpose

**This does not resolve to a defensible dollar figure, and manufacturing one
would violate this document's own rules.** What can be stated precisely:

Every layer — rootless Podman/Quadlets, SQLite, self-hosted Nextcloud, local
Ollama with open-weight models, Tailscale, Cloudflare Tunnel config — is
redeployable to another host or provider without re-architecture. Section D's
stack has the inverse property, and §2.22's clauses make the asymmetry
concrete: **cancel a vendor and that vertical goes dark, and the accumulated
data is contractually destroyed with written certification.** Migration risk
under lock-in is not merely rebuild time; it is **permanent, contractual loss
of the corpus**.

> ⚠️ **Honest asterisk.** Portability is not absolute here.
> `docs/INFRA_MAP.md:434` records that the live nginx config is **partly
> untracked** — the `corporatetraveldc_demo_login` rate-limit zone and three
> vhosts "exist only on the host," so they are **not restorable from the
> repo**. Redeployment would silently lose them. That is a real, fixable gap
> in the portability claim and it should be closed before the claim is made
> to a buyer. (Related correction: the stack has **one** rate-limit zone
> scoped to the demo login at 1r/s — not per-vhost rate limiting.)

### 2.27 E.6 — Raw data vs. rented output: reprocessability

Related to §2.22 but genuinely distinct. §2.22 is about **retention** — how
long you keep it. This is about **what you keep**.

This platform stores **raw ingested payloads**: raw SWIM XML/JSON, raw
Mode-S/BEAST messages, raw observations. `demo.db`'s 37,524 snapshots are
recorder payloads, not summaries. A vendor subscription delivers the vendor's
**already-processed output** — FlightAware's normalised flight status, not the
underlying transponder data.

Three consequences no retention policy addresses:
1. **Analysis the vendor didn't anticipate is impossible.** You can only ask
   questions their schema supports.
2. **Their processing logic can change under you**, silently re-basing your
   historical series with no way to recompute from source.
3. **Reprocessing is one-way.** Owning raw payloads means a parser
   improvement can be applied retroactively to 53 days of history; owning
   processed output means the old rows keep the old logic forever.

**Not priced.** No vendor sells "the raw inputs behind our product," so there
is no SKU to quote. Recorded as a structural property, not a line item.

### 2.28 E.7 — Section E total

| Line | Low | High |
|---|---:|---:|
| Cross-vertical correlation — **one** CI platform (§2.23) | $22,000 | $70,375 |
| LLM inference (Haiku 4.5, carried from D) | $443 | $862 |
| Vault storage (carried from D) | $180 | $180 |
| Security stack, scale-matched (§2.25) | $0 | $12 |
| Entity-tracking auto-promotion logic (§2.24) | **$0 — NO PRODUCT** | **$0** |
| Historical-archive access (§2.22) | **$0 — per-query only** | **$0** |
| **SECTION E TOTAL** | **$22,623** | **$71,429** |

**Versus the $1,163–$1,642 those rows carried in Section D: an undervaluation
of 19× to 44×.** The operator's pushback was correct.

**Revised overall picture, kept separable:**

| Layer | Low | High |
|---|---:|---:|
| **Section D — commodity infrastructure** (D core minus superseded rows 14–16) | $32,559 | $41,467 |
| **Section E — intelligence-automation capability** | $22,623 | $71,429 |
| **Combined** | **$55,182** | **$112,896** |
| + maritime/AIS (documented, **not live**) | — | +$55,000 ⇒ **$167,896** |

> ### Section E headline
>
> **≈ $22,600 – $71,400 / yr** to buy the intelligence-automation layer's
> *purchasable* parts — and **four things in it cannot be bought at any
> price:**
>
> 1. **A permanently-owned longitudinal corpus.** OAG, Kpler and ADS-B
>    Exchange all contractually require destruction on termination, with
>    written certification; OAG's clause reaches derivatives. *You cannot own
>    this even if you pay.*
> 2. **Threshold-based auto-promotion with independent-feed corroboration,
>    a human-review holding area, and silence/embargo detection.** Zero of
>    ten CI vendors document any of it — while this system has logged **143
>    notable-silence detections** and routed **60.5 % of 1,103 mentions** to
>    human review.
> 3. **A signed whole-tree manifest that refuses to execute on mismatch.**
> 4. **SR-2's per-skill content-hash execution gate.**
>
> **And a fifth that is structural rather than purchasable:** raw-payload
> reprocessability (§2.27) and genuine portability (§2.26) — the latter
> carrying a real asterisk over untracked nginx config.
>
> **What this does NOT establish.** Like Section D, this is replacement cost,
> not valuation and not revenue. The §3 productization gaps, the 34.5 %
> deterministic-fallback rate, and the ~1-trip runsheet all still apply — and
> the archive claim rests on an archival leg §2.22 flags as unverified.

---

## 3. Step 3 — Automation and privacy value

Since §2.1 establishes that the *data* is largely free to anyone, this is
where the asset's real value has to live. Assessed on the same adversarial
basis — what is measurably running, discounted for what is not.

**Credited (verified live):**

| Capability | Evidence | Commercial framing |
|---|---|---|
| **100 % local LLM inference, $0 cloud spend** | 21 `corporatetraveldc-pi5-*` models (`ollama list`); 21,081 SR-1 rows with **zero** cloud-model rows; `ANTHROPIC_FALLBACK_ENABLED=false` gating at `src/common/llm.py:1222` | Genuine differentiator. A cloud-LLM pipeline at this call volume would carry a real monthly bill; this one carries none, and no operational content leaves the box to an LLM vendor |
| **Brief generation at volume** | `brief_archive` **270 briefs in the last 7 days** (153 ep-advance, 114 ops, 3 weekly) | Real output |
| **Second-brain / knowledge layer** | 5,903 `vault_documents`, 5,134 FTS notes, 39,744 concept edges | Real, but see discount below |
| **Integrity + auth** | GPG-signed manifest over **685 files**, `Good signature`, EDDSA `419A864C…`; tiered bearer auth, 15 tokens issued / **5 active** | Real security engineering |
| **CUI/PII scrub gate** | `second_brain/scrub_gate.py`, a *block* gate on every write path; its own docstring calls it "a first-pass heuristic gate (regex-based), not exhaustive" | Real, honestly self-labelled |

**Discounts that must be applied:**

1. **~1 in 3 "inference" runs is not inference.** 1,972 of 5,718 SR-1 rows
   in 7 days (**34.5 %**) recorded `model = deterministic` — a hard-coded
   template because Ollama was unavailable or failed. Any premium for the
   LLM layer should be discounted by roughly that fraction.
2. **The knowledge graph is a tagging layer.** 39,744 edges span only **96**
   `semantic_concepts` and **74** `semantic_relations`, with **10**
   `vault_links`. Size it honestly before pricing it as a subsystem.
3. **Nobody is consuming the output.** `runsheet`'s newest entry is still
   `run_date = 2026-07-28`, `trip_count = 1`. 270 briefs in 7 days against
   ~1 recorded trip. Automation value is capacity, not realised demand.
4. **The audit trail is thin.** `audit_log` = **12 rows**, all with
   `egress_status = 'pending'`; `/healthz` reports `"audit_count_24h": 0`.
   "Every access is logged" is not supportable.
5. **Sovereignty is qualified.** The same ADS-B data that earns the §2.5
   reciprocal accounts is continuously sent to **six** third parties, and
   Cloudflare Tunnel fronts nine public hostnames.

> **§3 verdict:** the automation layer is the genuine asset, but it is
> capacity-not-demand, ~1/3 template-not-inference, and its sovereignty
> claim is partial. **No dollar figure is asserted here** — converting
> "capacity to generate briefs" into a price requires a customer, and
> `runsheet` says there isn't one yet.

---

## 4. Step 4 — Total, and adversarial stress test

### 4.1 The total (Section A only)

| | Annual |
|---|---|
| Commercial-equivalent cost, **Tier 1 only** (published, dated, scope-matched) | **$2,198 – $2,678** |
| Commercial-equivalent cost, Tier 2 (quote-only) | **UNPRICEABLE** — deliberately not summed |
| Commercial-equivalent cost, Tier 3 (free to anyone) | **$0 by definition** |
| **Actual recurring cost** (electricity, sourced §2.7) | **$23 – $39** |
| **Net defensible avoided cost** | **≈ $2,159 – $2,655 / yr** |
| One-time hardware actually deployed | ≈ **$765** |

### 4.2 Stress test — five ways this number is attacked

1. **"Most of your avoided cost is barter."** Correct. $1,598 of the
   $2,198–$2,678 (≈60–73 %) is reciprocal ADS-B access (§2.5). Turn the receiver off
   and it evaporates. A buyer who does not want to run an SDR gets none of
   it.
2. **"Your biggest feeds are free to me too."** Correct, and this is the
   strongest attack. FAA SWIM is free to any approved subscriber (§2.1 #1);
   NOAA data is public domain (§2.1 #2). A competitor with the same
   approvals pays $0 for the same bytes. **What is being sold is the
   integration, not the access.**
3. **"Your uptime is not a subscription's uptime."** Two of six SWIM feeds
   were dead at check time (§2.2); the whole SWIM tier was silently dead
   for ~8 h earlier the same morning; TBFM/ITWS self-suspend whenever Ollama
   runs. Cost-avoidance credited at 100 % of list price assumes 100 % of a
   vendor's availability, and this does not have it.
4. **"Half your differentiator isn't delivering."** ACARS/VDLM reaches the
   RF layer (641 msgs/24 h) but **`acars_messages` = 0 rows** — it never
   reaches the platform DB. AIS has no unit at all and 0 rows.
5. **"$2.2k/yr does not support a $90k–$140k asset price."** It does not,
   and it was never meant to. At a 10× revenue multiple this feed layer is
   worth ~$22k. **The cost-avoidance thesis, honestly executed, supports a
   figure roughly an order of magnitude below the headline band** — which
   is why §6 concludes the two bases should not be blended.

### 4.3 What would move the Section A number most

In descending order of impact: **(1)** an actual FlightAware Firehose or
Cirium quote — that single number is larger than everything in Tier 1
combined; **(2)** fixing the acarshub → `acars_messages` pipeline and
standing up AIS, which would move two rows out of the $0 column; **(3)**
keeping all six SWIM feeds up, which is an availability argument rather than
a price argument; **(4)** a $15 USB power meter to retire the last
UNVERIFIED input on the cost side.

---

## 5. Weakest points of this methodology

Stated plainly, worst first, because a buyer's analyst will find all of them.

1. **The thesis is largely wrong on its own terms.** The premise is that the
   platform is worth "the avoided cost of commercial subscriptions." But
   **the FAA gives SWIM away and NOAA data is public domain** (§2.1). The
   subscriptions being "avoided" mostly do not exist as a required purchase.
   This is the structural weakness §0 promised to state rather than bury,
   and it is larger than any pricing detail below it.
2. **The feeder/consumer conflict.** The box supplies FlightAware, FR24,
   PlaneFinder, AirNav RadarBox, adsbhub and OpenSky continuously. ~60–73 % of
   the Tier 1 figure is **reciprocal barter for our own data**, not cost
   avoided — and counting it as savings while also claiming ADS-B data
   sovereignty is having it both ways.
3. **Availability is not priced in.** Two of six SWIM feeds dead at check
   time; the whole tier silently dead ~8 h that morning; TBFM/ITWS
   self-suspend under `bandwidth_priority=ollama`. A subscription includes
   an SLA; this does not.
4. **The largest line items are unpriceable from public sources.** Firehose,
   Cirium, Spire, ADS-B Exchange Enterprise, L3Harris, Mosaic ATM and DTN
   are all quote-only. **The Tier 1 total is therefore a floor built from
   the small, publishable end of the market** — it is conservative, but it
   is conservative because the big numbers are unavailable, not because
   they were carefully bounded.
5. **Several cited prices are indirectly verified.** FAA `.gov` pages,
   FR24's pricing page, RadarBox, PlaneFinder and RapidAPI all returned
   **HTTP 403 or a JS shell** to automated fetch; those figures come from
   search-engine snapshots of the same URLs, not from a page I could read
   line-by-line. The FlightAware Enterprise price — the single largest Tier
   1 component — comes from a page **dated 2023-01-17**.
6. **Scope matching is a judgement, not a measurement.** Row 14 credits
   $600–$1,080/yr for OSINT by matching *downward* to 74 items/day rather
   than to NewsAPI's $449/mo tier. That is deliberately conservative, but a
   different analyst could defend a figure 5× higher. Flagged as an
   **ASSUMPTION**, not a derived number.
7. **§1.2's throughput table remains unusable** as a pricing input (window
   resets on every container restart — see the §1.2 note). Nothing in §2 is
   built on it, which is why §2 prices *capability and access*, not volume.
8. **Two rows are aspirational.** ACARS/VDLM does not reach the platform DB
   (`acars_messages` = 0); AIS has no systemd unit and 0 rows. Both are
   credited at **$0** here — but earlier drafts of the wider doc set treated
   them as delivered capability.

---

## 6. Relationship to the $65k–$100k and $90k–$140k figures

**These are different questions and should not be blended.** This document
prices *avoided recurring data cost*; `DEPLOYMENT_COST_PROJECTION` prices
*engineering replacement effort*. Neither is a cross-check on the other, as
this document's own header states.

| Basis | Figure | Status |
|---|---|---|
| Avoided data cost (this doc, Section A, Tier 1) | **≈ $2,159 – $2,655 / yr net** | Derived here from cited prices |
| Operator's prior COGS band | $65,000 – $100,000 | Methodology unrecoverable (§0) |
| `DEPLOYMENT_COST_PROJECTION` §3 headline | $90,000 – $140,000 | **Does not follow from its own stated arithmetic** — see that file's re-verification banner |

Three conclusions:

1. **Capitalising the avoided cost does not reach the headline band.** Even
   at a generous 10× multiple, $2.2–2.7k/yr capitalises to ~$22–27k — well
   under half the bottom of $65k–$100k, and roughly a fifth of the $115k
   midpoint. **The cost-avoidance thesis cannot carry the valuation.** It
   was the operator's stated basis for $65k–$100k (§0); executed honestly,
   it does not support that number.
2. **That is not the same as saying the asset is worth $22k.** It says the
   value is in the *integration and automation* (§3), not in avoided
   subscriptions. The valuation should be argued on replacement-effort
   grounds, with this section used only to show the recurring cost base is
   near zero — which it genuinely is.
3. **Do not reconcile against $90k–$140k until that figure is fixed.** Its
   stated construction rule yields ≈$70k–$109k, not $90k–$140k, and its
   Method 2 LOC→person-month conversion is off by ~2×. Reconciling against
   an arithmetically broken figure would import the error.

> **Bottom line for the founder:** the defensible, fully-sourced claim is
> **"this platform runs on ≈$765 of hardware and ≈$23–$39/yr of
> electricity, with $0 in data-feed fees and $0 in cloud-LLM spend, and
> displaces ≈$2.2–2.7k/yr of commercial equivalents it would otherwise
> plausibly buy."** That claim survives hostile review. The claim that it
> displaces tens of thousands per year in data subscriptions **does not**,
> because the FAA and NOAA give the underlying data away.

---
