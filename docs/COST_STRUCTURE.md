# CorporateTravelDC — Cost Structure: Dispatch Platform Hardware Tiers

> **Internal cost-planning doc — NOT investor materials.** All figures are
> operator-provided planning estimates as of **2026-08-05**, not vendor
> quotes or final prices (`~` = approximate). Do **not** promote any of this
> into `investor-materials/` — see the hold note on the build-vs-buy section
> below.
>
> The core compute in Tier 1 corresponds to the primary dispatch Pi plus the
> proposed Ollama-offload Pi in `docs/INFRA_MAP.md` §3.1 (**PROPOSED / not
> yet built**). See `docs/HARDWARE_GUIDANCE.md` for thermal history and
> `docs/DESIGN-PRINCIPLES.md` for the local-inference / data-sovereignty
> rationale.

---

## 1. Tiered hardware cost structure

Total cost scales with **how much local RF-ingest (SDR/antenna) capability**
is added on top of the core compute. Three tiers, floor → full-quality →
the actual current buildout.

### Tier 1 — Floor / core-only (no SDR)

Core compute only. **No local RF ingest** — no own ADS-B / ACARS / VDL
reception; flight data would come entirely from the REST/API fallback layer,
not a locally-hosted receive chain.

| Item | Role | Qty | Cost |
|---|---|---|---|
| Raspberry Pi 5, 16 GB | Primary dispatch Pi (existing) | 1 | ~$350 |
| Raspberry Pi 5, 16 GB | Ollama-offload Pi (proposed, INFRA_MAP §3.1) | 1 | see itemized standalone BOM below — **supersedes the old $200/8 GB placeholder** |
| Cases — 2× GeeekPi N07 MiniTower + ice-tower cooler | one per Pi | 2 | ~$90 combined |
| Power supplies | one per Pi | 2 | ~$60 combined |
| NVMe SSD | model store / boot | — | market rate (commodity NVMe pricing) |
| **Tier 1 total** | | | **stale — see reconciliation note below** |

**2026-08-05 reconciliation note:** the `~$700` combined total above was
built on a placeholder ($200, 8 GB) for the offload Pi. The operator's real,
itemized standalone cost for that Pi — board, NVMe, PSU, and case all
included — is **~$635–640, rounded to ~$700 on its own** (see the addendum
immediately below). That means this combined Tier 1 row is **not simply
"still ~$700"**: the offload Pi alone is now roughly what the *combined*
row used to claim for both boards together, before accounting for the
primary Pi's own $350 and its share of case/PSU. Not recomputing a new
combined grand total here without guessing at how much of the old
combined ~$90 case / ~$60 PSU figures belonged to which unit — that split
was never itemized per-Pi. Flagging for the operator rather than inventing a
number.

### Addendum — Ollama-offload Pi: itemized standalone BOM (operator's real numbers)

Replaces the earlier $200/8 GB placeholder above. Framed explicitly as a
**pure no-external-SSD hardware option** — the NVMe SSD boots the OS
directly, so there's no separate external SSD/enclosure line item the way
some Pi builds carry.

| Item | Cost |
|---|---|
| Raspberry Pi 5, 16 GB | ~$350 |
| NVMe SSD (market price — operator's own was ~$180, rounded up) | ~$200 |
| **Pi + SSD subtotal** | **$550** |
| Dedicated power supply | $35–40 |
| Case (shipped, Amazon) | ~$50 |
| **Itemized total** | **≈ $635–640** |
| **Operator's rounded working figure** | **~$700** |

Both numbers are shown deliberately — the itemized total ($635–640) is the
real sum of the line items above; **~$700** is the operator's own rounding of that
same total, used as the headline figure elsewhere. Not inflating the line
items to force them to add up to $700 — the arithmetic here is the actual
itemized total, and the $700 is explicitly labeled as a rounded working
number, not a recomputed sum.

> **⚠️ Guardrail — this pricing and the thermal/performance design in
> `INFRA_MAP.md` §3.1 both assume a dedicated, single-purpose node.** This
> Pi is sized, cased, and powered for **Ollama inference offload only**. Its
> whole justification — moving inference heat/CPU off the primary dispatch
> Pi's die — depends on nothing else sharing it. Running unrelated
> services, containers, or a user's own additional workflows on this box
> reintroduces the exact CPU/thermal contention this design exists to
> eliminate, and invalidates the cost, performance, and thermal assumptions
> throughout this section and INFRA_MAP.md §3.1. Same principle as the
> primary dispatch Pi's own resource guardrails (`docs/GUARDRAILS_JUSTIFICATION.md`)
> — dedicated hardware, not a shared general-purpose box.

### Tier 2 — + SDR / antenna (full-quality build)

Adds a **local RF receive chain** on top of Tier 1: SDR receivers, antennas,
and cable runs — the ADS-B / ACARS / VDL2 ingest stack described in
`INFRA_MAP.md` §4.

- **Add ~$400–$500 on top of the floor**, depending on **antenna quality and
  cable-run length** (long dedicated roof runs cost more — better antennas +
  longer/higher-grade coax push toward the top of that range).

| Tier | Adds | Total |
|---|---|---|
| Tier 2 — full-quality RF build | +$400–$500 over floor | **≈ $1,100–$1,200 + NVMe** |

### Reference case — actual current buildout (operator)

The real deployment today is **not** the full-quality tier: **ADS-B + VDL2
SDRs only, no dedicated roof runs, no tuned antennas.**

- **Adds ~$160 on top of the floor** (SDR dongles only, modest/indoor
  antennas, short cable runs).

| Tier | Adds | Total |
|---|---|---|
| Reference — actual buildout (ADS-B + VDL2, no roof/tuned antennas) | +~$160 over floor | **sub-$900 + NVMe** |

**Summary of the three tiers:**

| Tier | RF ingest | Add over floor | Approx. total |
|---|---|---|---|
| 1 — Floor / core-only | none | — | ≈ $700 + NVMe |
| Reference — actual buildout | ADS-B + VDL2 SDR only, no roof/tuned antennas | +~$160 | **sub-$900** |
| 2 — Full-quality build | full SDR + tuned antennas + roof runs | +$400–$500 | ≈ $1,100–$1,200 |

Cost model across all tiers: **one-time CapEx**, then near-zero marginal cost
(electricity only) — no recurring per-feed or per-token charges.

**Live evidence for the "no per-token charges" half of that claim
(verified 2026-08-23, the hardware figures above are unchanged operator
estimates and were not re-derived).** The SR-1 usage log
`/var/lib/corporatetraveldc/api-usage.csv` records every skill LLM
invocation on this box. Across **24,338 logged invocations spanning
2026-07-09 → 2026-08-23** (~46 days of continuous production):

> **This log grows continuously — treat every absolute count in this
> section as a timestamped sample, not a fact.** It gained ~50 rows in the
> few hours between the reading above and a re-check later the same day
> (24,386 data rows at 2026-08-23 14:05 EDT). The *ratios* and the zero
> results below are the durable claims; re-derive the counts rather than
> quoting them:
> `wc -l /var/lib/corporatetraveldc/api-usage.csv` (minus the header row).

- **Zero cloud calls.** `grep -icE 'claude|anthropic|gpt|openai'` over the
  whole log returns **0**. Every row's `model` is either a local
  `corporatetraveldc-pi5-*` Ollama model or the literal `deterministic`
  (the template fallback used when Ollama is unavailable).
- `deterministic` is the second-most-common value overall (5,208 rows) and
  the most common in the last 24 h (341 of ~810) — i.e. the degraded path
  is a **template render**, not a billed cloud call. That is the
  `ANTHROPIC_FALLBACK_ENABLED=false` gate working as designed
  (`/etc/corporatetraveldc/dispatch.env:200`).

So the recurring inference cost attributable to this platform is genuinely
**$0**, not merely "low" — the only recurring line remains power.

> **Gotcha for anyone trying to compute a token-based cost from this log:**
> the `input_tokens` / `output_tokens` / `cache_*` columns sum to **0 across
> all 24,338 rows**. SR-1 does not populate token counts for Ollama calls,
> so the log proves *which provider* served each call, not how many tokens
> it cost. Don't read the zeros as "no work was done."

---

## 2. Build vs. buy — one-time hardware vs. commercial subscription feeds

> **⚠️ HOLD — candidate investor-slide content, not for use yet.** the operator wants
> this "why build vs. buy a subscription" comparison eventually promoted into
> the investor materials as a **standalone slide**, but **explicitly NOT
> now** — hold until the **AIS and/or AAM roadmap items are added and the docs
> are updated to reflect them**. Until then this stays here as internal
> reference only. **Do not touch anything under `investor-materials/`.**

Commercial single-feed subscriptions, for comparison against the one-time
hardware cost above:

| Option | Cost | Recurring? | Data delivered | Sovereignty / ownership |
|---|---|---|---|---|
| **This platform — full-quality build (Tier 2)** | **~$900–$1,200 one-time** | **No** — one-time CapEx | ADS-B **+ ACARS + VDL + weather + full cross-referencing** | **Yes** — permanent, on-prem data ownership |
| FlightAware — enterprise-tier ADS-B feed | **~$1,200 / year** | Yes — annual | **ADS-B only** | No — rented feed, no data sovereignty |
| FlightRadar24 — enterprise tier | **from ~$500 / year** | Yes — annual | **ADS-B only** | No — rented feed, no data sovereignty |

**The point:** the platform's **one-time** hardware cost — *even at the higher
~$900–$1,200 full-SDR/antenna tier* — is **roughly comparable to, or less
than, ONE YEAR** of either single-data-type subscription. And it delivers
**ADS-B + ACARS + VDL + weather + full cross-referencing + permanent data
ownership**, versus a **recurring rental of a single feed type** (ADS-B only,
no ACARS/VDL/weather, no cross-referencing, no sovereignty). Year two onward
the subscription bills again; the hardware does not.

### Operating costs (for an honest total cost of ownership)

CapEx alone isn't the whole picture — the recurring costs of *running* the
hardware, so the comparison stays honest rather than CapEx-only:

| Operating cost | Annual | Attributable to the platform? |
|---|---|---|
| **Power** | ~$50–$100 / year | **Yes** — genuinely incurred by running this hardware |
| **Internet** (business fiber) | up to ~$5,000–$6,000 / year at the high end | **Generally no** — see note |

- **Power (~$50–$100/yr)** is nominal and **genuinely attributable** — a real
  marginal cost of running the platform.
- **Internet** is shown at the **top of a wide range** (business fiber, high
  end) and **varies a lot by provider/tier** — it is **not** a fixed cost.
  More importantly, it is typically a **pre-existing business expense
  independent of this platform**: the operation already pays for business
  internet regardless, so it is **not a marginal cost incurred because of the
  dispatch platform specifically**. It's listed for completeness, **not** as a
  platform-attributable line the way power is. (Note too that the recurring
  subscription feeds in the table above ride on that same connection — they
  don't avoid this cost either.)

*(Same HOLD-for-investor-promotion status as the rest of §2 — internal
reference only until the AIS/AAM roadmap lands.)*
