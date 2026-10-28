Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.
Re-checked 2026-10-15 ~01:40Z against main 1491bf5a plus the staged next-relay batch (guard worktree) and the live box. The staged batch changes no cost line.

# CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers

> **Internal cost-planning doc — NOT investor materials.** All CapEx
> figures below are **operator-stated planning estimates as of
> 2026-08-05** (`~` = approximate), not vendor quotes or final prices, and
> were not re-derived or re-priced in this 2026-10-06 pass — hardware
> pricing isn't something to verify from the live box. Do **not** promote
> any of this into `investor-materials/` — see the hold note on the
> build-vs-buy section below.
>
> The core compute in Tier 1 corresponds to the primary dispatch Pi plus
> the proposed Ollama-offload Pi in `docs/INFRA_MAP.md` §3.1 — confirmed
> **still PROPOSED, not built**, live 2026-10-06
> ~~(`grep -n "3.1" docs/INFRA_MAP.md` → "PROPOSED — dedicated Ollama-offload
> Pi (second Pi 5) — NOT built")~~ (corrected 2026-10-15: the heading now
> reads "3.1 PROPOSED — second Pi for inference — NOT built",
> `docs/INFRA_MAP.md:59`; still not built). Note the naming is now stale: the
> platform's local-inference layer has since moved from Ollama to
> llama.cpp (`corporatetraveldc-llama.service`); the proposal's thermal/
> cost rationale (moving inference heat off the primary Pi) still applies
> to whichever inference stack is current if it is ever built. See
> `docs/HARDWARE_GUIDANCE.md` for thermal history and
> `docs/DESIGN-PRINCIPLES.md` for the local-inference / data-sovereignty
> rationale.
>
> **What actually runs (live, 2026-10-15).** One node: a Raspberry Pi 5
> Model B Rev 1.1, 16 GB RAM (`MemTotal` ~~16,599,696~~ 16,599,504 kB (corrected 2026-10-26, `/proc/meminfo`)), 4 cores, one
> 238 GB NVMe root volume (~~83% used, 39 GB free~~ (superseded 2026-10-25: ~~80% used, 48 GB free~~ 81% used, 45 GB free at 2026-10-26 21:58Z), `df -h /`). The tiers
> below all include the unbuilt offload Pi in their floor, so none of them
> is the price of the box that runs today. The sourced figure for the actual
> single node is **≈ $765 one-time** (Pi 5 16 GB + NVMe + case/PSU + the
> ~$160 two-SDR RF adder), `docs/COGS_VENDOR_COMPARISON_2026-08-18.md` §2.7,
> carried into `docs/COGS_BUILD_VS_RENT_2026-09-28.md` §1. That figure is
> also an operator estimate, not a receipt [UNVERIFIED as of 2026-10-15].

---

## 1. Tiered hardware cost structure (operator-stated estimates, unverified pricing)

Total cost scales with **how much local RF-ingest (SDR/antenna) capability**
is added on top of the core compute. Three tiers, floor → full-quality →
the actual current buildout.

### Tier 1 — Floor / core-only (no SDR)

Core compute only. No local RF ingest.

| Item | Role | Qty | Cost (operator estimate) |
|---|---|---|---|
| Raspberry Pi 5, 16 GB | Primary dispatch Pi (existing) | 1 | ~$350 |
| Raspberry Pi 5, 16 GB | Ollama-offload Pi (proposed, not built) | 1 | see itemized BOM below |
| Cases — 2× GeeekPi N07 MiniTower + ice-tower cooler | one per Pi | 2 | ~$90 combined |
| Power supplies | one per Pi | 2 | ~$60 combined |
| NVMe SSD | model store / boot | — | market rate |
| **Tier 1 total** | | | **not recomputed — see reconciliation note** |

**Reconciliation note (operator-stated, 2026-08-05):** the `~$700`
combined total above assumed a placeholder ($200, 8 GB) for the offload
Pi. The operator's real itemized standalone cost for that Pi is
~$635–640 (rounded to ~$700 on its own) — see the addendum below. The
combined Tier 1 row is therefore stale; no new combined total is computed
here because the old ~$90 case / ~$60 PSU split was never itemized
per-Pi. Flagged for the operator rather than invented.

### Addendum — Ollama-offload Pi: itemized standalone BOM (operator-stated)

Pure no-external-SSD option — the NVMe SSD boots the OS directly.

| Item | Cost |
|---|---|
| Raspberry Pi 5, 16 GB | ~$350 |
| NVMe SSD (market price; operator's own was ~$180, rounded up) | ~$200 |
| **Pi + SSD subtotal** | **$550** |
| Dedicated power supply | $35–40 |
| Case (shipped, Amazon) | ~$50 |
| **Itemized total** | **≈ $635–640** |
| **Operator's rounded working figure** | **~$700** |

Both numbers shown deliberately: $635–640 is the itemized sum; ~$700 is
the operator's own rounding of that sum, used as the headline figure
elsewhere.

> **Guardrail — this pricing and the thermal/performance design in
> `INFRA_MAP.md` §3.1 both assume a dedicated, single-purpose node.** If
> this Pi is ever built, running unrelated services or workflows on it
> reintroduces the exact CPU/thermal contention the design exists to
> eliminate, and invalidates the cost/performance/thermal assumptions
> throughout this section and `INFRA_MAP.md` §3.1.

### Tier 2 — + SDR / antenna (full-quality build)

Adds a local RF receive chain on top of Tier 1: SDR receivers, antennas,
cable runs — the ADS-B/ACARS/VDL2 ingest stack in `INFRA_MAP.md` §4.

- Add ~$400–$500 on top of the floor, depending on antenna quality and
  cable-run length.

| Tier | Adds | Total |
|---|---|---|
| Tier 2 — full-quality RF build | +$400–$500 over floor | **≈ $1,100–$1,200 + NVMe** |

### Reference case — actual current buildout (operator)

The real deployment today is **not** the full-quality tier: ADS-B + VDL2
SDRs only, no dedicated roof runs, no tuned antennas — confirmed live
2026-10-06 against `docs/SDR_SERVICES.md` and `podman ps`: exactly two
RTL-SDR dongles in service (`ADSB1090` for ultrafeeder, `ACARS0130`
shared by dumpvdl2), no HFDL/AIS/UTM hardware installed. Re-checked
2026-10-15: `corporatetraveldc-ultrafeeder` and `corporatetraveldc-dumpvdl2`
are running (`podman ps`), no HFDL/AIS container runs, and the only units
that open an RTL-SDR dongle are those two (acarshub/acarsrouter/acars-watcher
consume their output; no HFDL/AIS unit exists). The antennas, coax, any LNA and the dongles'
replacement history are not itemized anywhere [UNVERIFIED as of
2026-10-15]; the ~$160 adder is the operator's estimate.

- Adds ~$160 on top of the floor (SDR dongles only, modest/indoor
  antennas, short cable runs).

| Tier | Adds | Total |
|---|---|---|
| Reference — actual buildout (ADS-B + VDL2, no roof/tuned antennas) | +~$160 over floor | **sub-$900 + NVMe** |

**Summary of the three tiers:**

| Tier | RF ingest | Add over floor | Approx. total |
|---|---|---|---|
| 1 — Floor / core-only | none | — | ≈ $700 + NVMe |
| Reference — actual buildout | ADS-B + VDL2 SDR only | +~$160 | **sub-$900** |
| 2 — Full-quality build | full SDR + tuned antennas + roof runs | +$400–$500 | ≈ $1,100–$1,200 |

~~Cost model across all tiers: one-time CapEx, then near-zero marginal cost
(electricity only) — no recurring per-feed or per-token charges.~~
(corrected 2026-10-15: the *hardware* has no per-feed or per-token charge,
but running the platform as built today also has recurring lines beyond
electricity: the Proton plan behind the off-box backup (live since
2026-10-12) and the mail bridge, the weekly Claude Opus job, the vendor
plans the agents run on, and the domain. See "Recurring lines" in §2. The
backup uplink currently costs nothing because it has no service.)

> **Correction (2026-10-15): this log is not evidence of inference.** SR-1
> rows record the model *label* a skill was configured with, written on
> every run whether or not a model was called. Examples from the last 24 h
> (to 2026-10-15 01:38Z): `flight-impact`, which makes no LLM call, wrote
> 277 rows labelled `corporatetraveldc-pi5-osint` (a name with no persona in
> `src/common/personas.py`, so it could not reach the model even if it
> tried); and `deterministic` rows come mostly from skills that never call
> a model (`convective-sigmet-archiver` 144, `tbfm-arrival-enrichment` 128,
> `board-sweep` 96, `feed-db-integrity-check` 48, `cps-recompute` 25,
> `ingest-feed-watch` 24, of 472). So "deterministic" is not a fallback
> rate, and the log shows only that no *skill* is configured with a cloud
> model. It says nothing about the cloud jobs that run outside SR-1 (the
> weekly Claude Opus dump, agent vendor plans). The struck conclusions
> below are kept for the record.

**Live evidence for the "no per-token charges" half of that claim
(re-derived live 2026-10-06 — the hardware figures above are unchanged
operator estimates and were not re-derived).** The SR-1 usage log
`/var/lib/corporatetraveldc/api-usage.csv` records every skill LLM
invocation on this box. Across **73,284 logged invocations spanning
2026-06-27 → 2026-10-06** (~101 days of continuous production — up from
24,338 rows/46 days at the 2026-08-23 check):

> **This log grows continuously — treat every absolute count here as a
> timestamped sample, not a fixed fact.** Re-derive with
> `wc -l /var/lib/corporatetraveldc/api-usage.csv` (minus the header).
> The *ratios* and the zero-cloud-calls result are the durable claims.
> (2026-10-15 sample: 84,744 data rows, 2026-06-27 00:00Z → 2026-10-15
> 01:38Z; `deterministic` 26,169, ~31%; cloud-model name matches 0; token
> columns still sum to 0. The ratios describe labels, not inference; see the
> correction above.) (2026-10-25 sample: 97,826 data rows, 2026-06-27 00:00Z →
> 2026-10-25 15:49Z; `deterministic` 30,954, ~32%; `none` 521; token columns
> still sum to 0.)

- ~~**Zero cloud calls, still.**~~ (corrected 2026-10-15: zero *skill rows
  labelled with a cloud model*; the Claude Opus weekly job and the agent
  plans never write to this log.) `grep -icE 'claude|anthropic|gpt|openai'`
  over the whole log returns **0** (re-confirmed 2026-10-06; still 0 on
  2026-10-15 with `sonnet|haiku|opus` added). ~~Every row's
  `model` is either a local `corporatetraveldc-pi5-*` model or the literal
  `deterministic` (template fallback).~~ (corrected 2026-10-15: 58,022 rows
  carry a `corporatetraveldc-pi5-*` label and 26,179 `deterministic`; 424
  are `none` (entity-tracking-digest, semantic-compile-daily, still being
  written as of 2026-10-14) and 146 are a malformed `csexec-osint` label
  with an inline comment, all from 2026-07-09. None names a cloud model.) (superseded 2026-10-25: 66,205 `corporatetraveldc-pi5-*`, 30,954 `deterministic`, 521 `none`, 146 `csexec-osint`; still no cloud-model name.)
- **Correction from the 2026-08-23 revision of this doc:** those local
  models are no longer served by Ollama. Ollama was retired at the
  2026-08-27 cutover; the model column still reads `corporatetraveldc-pi5-*`
  (a naming convention, not a provider tag) but the serving process today
  is `corporatetraveldc-llama.service` (llama.cpp), currently
  Qwen3-4B-Instruct-2507 (q4_0). The "local, not cloud" conclusion is
  unaffected — the provider changed, the zero-cloud-calls fact did not.
- `deterministic` is the single most common value in the full log
  (22,431 of 73,284 rows, ~31%) — ~~the degraded/fallback path is a
  **template render**, not a billed cloud call, consistent with~~
  (corrected 2026-10-15: most `deterministic` rows are skills that never
  call a model, see the correction above; a skill whose model call fails
  logs its configured model label with `status=fallback`, for example 98
  `route-impact` and 88 `tfr-enrichment` fallback rows in the last 24 h. The
  fallback is still a template render, never a cloud call, because of)
  `ANTHROPIC_FALLBACK_ENABLED=false` (confirmed set live in
  `/etc/corporatetraveldc/dispatch.env`, name/value-shape only — the
  literal value `false` is itself non-sensitive and was already quoted in
  the pre-existing doc). Next most common: `corporatetraveldc-pi5-tfr-enrichment`
  (13,418), `corporatetraveldc-pi5-route-impact` (12,685), `corporatetraveldc-pi5-osint`
  (12,196).

~~So the recurring inference cost attributable to this platform is
genuinely **$0**, not merely "low" — the only recurring line remains
power.~~ (corrected 2026-10-15: never true as written — the weekly Claude Opus
dump, `scripts/second-brain-weekly-dump.sh`, has been scheduled since
2026-08-25, before this 2026-10-06 re-derivation. What is true: **skill inference** runs on
the local llama.cpp server and costs only power. The platform also runs a
scheduled cloud job: `corporatetraveldc-second-brain-weekly-dump.timer`
(enabled, Sunday ~~02:00Z~~ (corrected 2026-10-26: 02:00 America/New_York, 06:00Z EDT / 07:00Z EST), behind the `maintenance-window-guard.sh`
ExecCondition, which skipped the 2026-10-11 run) calls
`claude --model opus` (`scripts/second-brain-weekly-dump.sh:139`) on the
operator's Claude plan, and the agents run on vendor plans. Their cost is
not recorded on the box [UNVERIFIED as of 2026-10-15]. Power is also not the
only recurring line; see "Recurring lines" in §2.)

> **Gotcha for anyone computing a token-based cost from this log:** the
> `input_tokens`/`output_tokens`/`cache_*` columns still sum to **0**
> across all 73,284 rows (re-confirmed 2026-10-06). SR-1 does not populate
> token counts for local-model calls, so the log proves *which provider*
> served each call, not how many tokens it cost.

---

## 2. Build vs. buy — one-time hardware vs. commercial subscription feeds

> **HOLD — candidate investor-slide content, not for use yet.** Held until
> the AIS and/or AAM roadmap items are added and the docs reflect them.
> Internal reference only. Do not touch anything under `investor-materials/`.

Commercial single-feed subscriptions, for comparison against the one-time
hardware cost above:

| Option | Cost | Recurring? | Data delivered | Sovereignty / ownership |
|---|---|---|---|---|
| **This platform — full-quality build (Tier 2)** | ~~**~$900–$1,200 one-time**~~ (corrected 2026-10-15: **≈ $1,100–$1,200 + NVMe**, the Tier 2 figure in §1; ~$900 is the reference buildout, not Tier 2) | **No** | ADS-B **+ ACARS + VDL + weather + full cross-referencing** | **Yes** — permanent, on-prem |
| FlightAware — enterprise-tier ADS-B feed | **~$1,200/year** | Yes | ADS-B only | No |
| FlightRadar24 — enterprise tier | **from ~$500/year** | Yes | ADS-B only | No |

**The point:** the platform's one-time hardware cost — even at the higher
~~about $900–$1,200~~ ≈ $1,100–$1,200 + NVMe (corrected 2026-10-15) full-SDR/antenna tier — is roughly comparable to, or less
than, one year of either single-data-type subscription, and delivers
materially more (ADS-B + ACARS + VDL + weather + cross-referencing +
permanent ownership) than a recurring single-feed rental.

### Operating costs (for an honest total cost of ownership)

| Operating cost | Annual | Attributable to the platform? |
|---|---|---|
| **Power** | ~~about $50–$100/year~~ (corrected 2026-10-15: **≈ $23–$39/year**, 10.5–17.6 W continuous at $0.2540/kWh, EIA Table 5.6.A DC residential May 2026, derived in `docs/COGS_VENDOR_COMPARISON_2026-08-18.md` §2.7; the $50–100 figure was an unsourced placeholder) (unverified 2026-10-25: a derived estimate, not observable from code or live state) | **Yes** |
| **Internet** (business fiber) | up to ~$5,000–$6,000/year at the high end | **Generally no** — pre-existing business expense independent of this platform |

### Recurring lines beyond power (added 2026-10-15)

Amounts are not recorded anywhere on the box; each is marked rather than
guessed.

| Line | What runs (live 2026-10-15) | Annual | Attributable to the platform? |
|---|---|---|---|
| **Off-box backup storage** | Nightly encrypted Borg archives synced to Proton Drive (`BACKUP_TARGET=proton:ctdi-backup`, live since 2026-10-12; `corporatetraveldc-backup.timer`, `-backup-freshness.timer`, `-backup-restore-test.timer` all scheduled). Each nightly sync uploads several GB; the seed was 19 GB, within a 2 TB allotment (`docs/BACKUP.md`). | [UNVERIFIED as of 2026-10-15] — Proton plan price not recorded | **Yes**, at least the storage share |
| **Proton mail relay** | `corporatetraveldc-protonbridge` (running). Bridge needs a paid Proton plan; whether it is the same plan as the Drive storage is not recorded. | [UNVERIFIED as of 2026-10-15] | Shared business cost |
| **Scheduled cloud LLM** | Weekly Claude Opus second-brain dump (see §1 correction). | [UNVERIFIED as of 2026-10-15] — rides on the operator's Claude plan | Partly |
| **Agent vendor plans** | Claude Code, Codex and ChatGPT/Cowork agents used to build and operate the platform. | [UNVERIFIED as of 2026-10-15] | Development and operations, not runtime |
| **Backup uplink** | `enu1`, a Verizon phone hotspot, ~~has had no internet service since 2026-10-06 over pricing; a prepaid SIM is planned (`docs/NETWORK_FAILOVER.md`)~~ (corrected 2026-10-25: `enu1` is up with carrier and probe-reachable -- net-failover watchdog `backup=enu1:ok`, `ip -br link`; the NetworkManager profile is still named `Verizon Hotspot`; the plan behind it is not observable from code or live state (unverified 2026-10-25), see BACKUP.md §metered uplink). | $0 today; prepaid SIM cost [UNVERIFIED as of 2026-10-15] | **Yes** once restored |
| **Domain** | `.com` renewal shared across the public hostnames | ~$10–15 [EST, carried from `docs/COGS_BUILD_VS_RENT_2026-09-28.md` §1; registrar not recorded] | Shared |

The primary uplink is the Wi-Fi gateway on `wld0` (`docs/NETWORK_FAILOVER.md`);
its plan type and price are not recorded on the box [UNVERIFIED as of 2026-10-15].

*(Same HOLD-for-investor-promotion status as the rest of §2.)*

### See also (added 2026-10-15)

- `docs/COGS_BUILD_VS_RENT_2026-09-28.md` — the most recent two-column COGS
  model (self-hosted vs subscription replacement). Dated record; read its
  status note first.
- `docs/COGS_VENDOR_COMPARISON_2026-08-18.md` §2.7 — the sourced power and
  hardware derivation used above.
- `docs/BACKUP.md` — the backup target, sizing and schedule.
- `docs/DEDICATED_MODELS_PLAN.md` — what the local model server is and who
  calls it.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers

> ~~**Internal cost-planning doc — NOT investor materials.** All figures are operator-provided planning estimates as of **2026-08-05**, not vendor quotes or final prices (`~` = approximate). Do **not** promote any of this into `investor-materials/` — see the hold note on the build-vs-buy section below.  The core compute in Tier 1 corresponds to the primary dispatch Pi plus the proposed Ollama-offload Pi in `docs/INFRA_MAP.md` §3.1 (**PROPOSED / not yet built**). See `docs/HARDWARE_GUIDANCE.md` for thermal history and `docs/DESIGN-PRINCIPLES.md` for the local-inference / data-sovereignty rationale.~~

**~~1. Tiered hardware cost structure~~** *(former heading)*


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 1. Tiered hardware cost structure › Tier 1 — Floor / core-only (no SDR)

~~Core compute only. **No local RF ingest** — no own ADS-B / ACARS / VDL reception; flight data would come entirely from the REST/API fallback layer, not a locally-hosted receive chain.~~

| ~~Item~~ | ~~Role~~ | ~~Qty~~ | ~~Cost~~ |
|---|---|---|---|
| ~~Raspberry Pi 5, 16 GB~~ | ~~Primary dispatch Pi (existing)~~ | ~~1~~ | ~~~$350~~ |
| ~~Raspberry Pi 5, 16 GB~~ | ~~Ollama-offload Pi (proposed, INFRA_MAP §3.1)~~ | ~~1~~ | ~~see itemized standalone BOM below — **supersedes the old $200/8 GB placeholder**~~ |
| ~~Cases — 2× GeeekPi N07 MiniTower + ice-tower cooler~~ | ~~one per Pi~~ | ~~2~~ | ~~~$90 combined~~ |
| ~~Power supplies~~ | ~~one per Pi~~ | ~~2~~ | ~~~$60 combined~~ |
| ~~NVMe SSD~~ | ~~model store / boot~~ | ~~—~~ | ~~market rate (commodity NVMe pricing)~~ |
| ~~**Tier 1 total**~~ |  |  | ~~**stale — see reconciliation note below**~~ |

~~**2026-08-05 reconciliation note:** the `~$700` combined total above was built on a placeholder ($200, 8 GB) for the offload Pi. The operator's real, itemized standalone cost for that Pi — board, NVMe, PSU, and case all included — is **~$635–640, rounded to ~$700 on its own** (see the addendum immediately below). That means this combined Tier 1 row is **not simply "still ~$700"**: the offload Pi alone is now roughly what the *combined* row used to claim for both boards together, before accounting for the primary Pi's own $350 and its share of case/PSU. Not recomputing a new combined grand total here without guessing at how much of the old combined ~$90 case / ~$60 PSU figures belonged to which unit — that split was never itemized per-Pi. Flagging for the operator rather than inventing a number.~~

**~~Addendum — Ollama-offload Pi: itemized standalone BOM (operator's real numbers)~~** *(former heading)*


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 1. Tiered hardware cost structure › Addendum — Ollama-offload Pi: itemized standalone BOM (operator's real numbers)

~~Replaces the earlier $200/8 GB placeholder above. Framed explicitly as a **pure no-external-SSD hardware option** — the NVMe SSD boots the OS directly, so there's no separate external SSD/enclosure line item the way some Pi builds carry.~~

| ~~Item~~ | ~~Cost~~ |
|---|---|
| ~~Raspberry Pi 5, 16 GB~~ | ~~~$350~~ |
| ~~NVMe SSD (market price — operator's own was ~$180, rounded up)~~ | ~~~$200~~ |
| ~~**Pi + SSD subtotal**~~ | ~~**$550**~~ |
| ~~Dedicated power supply~~ | ~~$35–40~~ |
| ~~Case (shipped, Amazon)~~ | ~~~$50~~ |
| ~~**Itemized total**~~ | ~~**≈ $635–640**~~ |
| ~~**Operator's rounded working figure**~~ | ~~**~$700**~~ |

~~Both numbers are shown deliberately — the itemized total ($635–640) is the real sum of the line items above; **~$700** is the operator's own rounding of that same total, used as the headline figure elsewhere. Not inflating the line items to force them to add up to $700 — the arithmetic here is the actual itemized total, and the $700 is explicitly labeled as a rounded working number, not a recomputed sum.~~

> ~~**⚠️ Guardrail — this pricing and the thermal/performance design in `INFRA_MAP.md` §3.1 both assume a dedicated, single-purpose node.** This Pi is sized, cased, and powered for **Ollama inference offload only**. Its whole justification — moving inference heat/CPU off the primary dispatch Pi's die — depends on nothing else sharing it. Running unrelated services, containers, or a user's own additional workflows on this box reintroduces the exact CPU/thermal contention this design exists to eliminate, and invalidates the cost, performance, and thermal assumptions throughout this section and INFRA_MAP.md §3.1. Same principle as the primary dispatch Pi's own resource guardrails (`docs/GUARDRAILS_JUSTIFICATION.md`) — dedicated hardware, not a shared general-purpose box.~~


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 1. Tiered hardware cost structure › Tier 2 — + SDR / antenna (full-quality build)

~~Adds a **local RF receive chain** on top of Tier 1: SDR receivers, antennas, and cable runs — the ADS-B / ACARS / VDL2 ingest stack described in `INFRA_MAP.md` §4.~~

- ~~**Add ~$400–$500 on top of the floor**, depending on **antenna quality and cable-run length** (long dedicated roof runs cost more — better antennas + longer/higher-grade coax push toward the top of that range).~~


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 1. Tiered hardware cost structure › Reference case — actual current buildout (operator)

~~The real deployment today is **not** the full-quality tier: **ADS-B + VDL2 SDRs only, no dedicated roof runs, no tuned antennas.**~~

- ~~**Adds ~$160 on top of the floor** (SDR dongles only, modest/indoor antennas, short cable runs).~~

| ~~Tier~~ | ~~RF ingest~~ | ~~Add over floor~~ | ~~Approx. total~~ |
|---|---|---|---|
| ~~1 — Floor / core-only~~ | ~~none~~ | ~~—~~ | ~~≈ $700 + NVMe~~ |
| ~~Reference — actual buildout~~ | ~~ADS-B + VDL2 SDR only, no roof/tuned antennas~~ | ~~+~$160~~ | ~~**sub-$900**~~ |
| ~~2 — Full-quality build~~ | ~~full SDR + tuned antennas + roof runs~~ | ~~+$400–$500~~ | ~~≈ $1,100–$1,200~~ |

~~Cost model across all tiers: **one-time CapEx**, then near-zero marginal cost (electricity only) — no recurring per-feed or per-token charges.~~

~~**Live evidence for the "no per-token charges" half of that claim (verified 2026-08-23, the hardware figures above are unchanged operator estimates and were not re-derived).** The SR-1 usage log `/var/lib/corporatetraveldc/api-usage.csv` records every skill LLM invocation on this box. Across **24,338 logged invocations spanning 2026-07-09 → 2026-08-23** (~46 days of continuous production):~~

> ~~**This log grows continuously — treat every absolute count in this section as a timestamped sample, not a fact.** It gained ~50 rows in the few hours between the reading above and a re-check later the same day (24,386 data rows at 2026-08-23 14:05 EDT). The *ratios* and the zero results below are the durable claims; re-derive the counts rather than quoting them: `wc -l /var/lib/corporatetraveldc/api-usage.csv` (minus the header row).~~

- ~~**Zero cloud calls.** `grep -icE 'claude|anthropic|gpt|openai'` over the whole log returns **0**. Every row's `model` is either a local `corporatetraveldc-pi5-*` Ollama model or the literal `deterministic` (the template fallback used when Ollama is unavailable).~~
- ~~`deterministic` is the second-most-common value overall (5,208 rows) and the most common in the last 24 h (341 of ~810) — i.e. the degraded path is a **template render**, not a billed cloud call. That is the `ANTHROPIC_FALLBACK_ENABLED=false` gate working as designed (`/etc/corporatetraveldc/dispatch.env:200`).~~

> ~~**Gotcha for anyone trying to compute a token-based cost from this log:** the `input_tokens` / `output_tokens` / `cache_*` columns sum to **0 across all 24,338 rows**. SR-1 does not populate token counts for Ollama calls, so the log proves *which provider* served each call, not how many tokens it cost. Don't read the zeros as "no work was done."~~


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 2. Build vs. buy — one-time hardware vs. commercial subscription feeds

> ~~**⚠️ HOLD — candidate investor-slide content, not for use yet.** the operator wants this "why build vs. buy a subscription" comparison eventually promoted into the investor materials as a **standalone slide**, but **explicitly NOT now** — hold until the **AIS and/or AAM roadmap items are added and the docs are updated to reflect them**. Until then this stays here as internal reference only. **Do not touch anything under `investor-materials/`.**~~

| ~~Option~~ | ~~Cost~~ | ~~Recurring?~~ | ~~Data delivered~~ | ~~Sovereignty / ownership~~ |
|---|---|---|---|---|
| ~~**This platform — full-quality build (Tier 2)**~~ | ~~**~$900–$1,200 one-time**~~ | ~~**No** — one-time CapEx~~ | ~~ADS-B **+ ACARS + VDL + weather + full cross-referencing**~~ | ~~**Yes** — permanent, on-prem data ownership~~ |
| ~~FlightAware — enterprise-tier ADS-B feed~~ | ~~**~$1,200 / year**~~ | ~~Yes — annual~~ | ~~**ADS-B only**~~ | ~~No — rented feed, no data sovereignty~~ |
| ~~FlightRadar24 — enterprise tier~~ | ~~**from ~$500 / year**~~ | ~~Yes — annual~~ | ~~**ADS-B only**~~ | ~~No — rented feed, no data sovereignty~~ |

~~**The point:** the platform's **one-time** hardware cost — *even at the higher ~$900–$1,200 full-SDR/antenna tier* — is **roughly comparable to, or less than, ONE YEAR** of either single-data-type subscription. And it delivers **ADS-B + ACARS + VDL + weather + full cross-referencing + permanent data ownership**, versus a **recurring rental of a single feed type** (ADS-B only, no ACARS/VDL/weather, no cross-referencing, no sovereignty). Year two onward the subscription bills again; the hardware does not.~~


### CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers › 2. Build vs. buy — one-time hardware vs. commercial subscription feeds › Operating costs (for an honest total cost of ownership)

~~CapEx alone isn't the whole picture — the recurring costs of *running* the hardware, so the comparison stays honest rather than CapEx-only:~~

| ~~Operating cost~~ | ~~Annual~~ | ~~Attributable to the platform?~~ |
|---|---|---|
| ~~**Power**~~ | ~~~$50–$100 / year~~ | ~~**Yes** — genuinely incurred by running this hardware~~ |
| ~~**Internet** (business fiber)~~ | ~~up to ~$5,000–$6,000 / year at the high end~~ | ~~**Generally no** — see note~~ |

- ~~**Power (~$50–$100/yr)** is nominal and **genuinely attributable** — a real marginal cost of running the platform.~~
- ~~**Internet** is shown at the **top of a wide range** (business fiber, high end) and **varies a lot by provider/tier** — it is **not** a fixed cost. More importantly, it is typically a **pre-existing business expense independent of this platform**: the operation already pays for business internet regardless, so it is **not a marginal cost incurred because of the dispatch platform specifically**. It's listed for completeness, **not** as a platform-attributable line the way power is. (Note too that the recurring subscription feeds in the table above ride on that same connection — they don't avoid this cost either.)~~

~~*(Same HOLD-for-investor-promotion status as the rest of §2 — internal reference only until the AIS/AAM roadmap lands.)*~~
