# Geometric Reasoning + Second Brain Rework — Design Proposal

**Status:** proposal, not implemented. No code changes have been made for any
of this. Written 2026-09-17 after (1) a full reread of the live platform
(`src/common/personas.py`, `src/second_brain/semantic/{model,compile}.py`,
`ontology.json`), (2) a 3-agent review against Mouselinos/Michalewski/
Malinowski, *"Beyond Lines and Circles: Unveiling the Geometric Reasoning Gap
in Large Language Models"* (arXiv 2402.03877v3), and (3) the operator's
stated goals below.

## Why this exists

Two explicit operator goals, 2026-09-17, verbatim-equivalent:

1. Get the LLM and the Second Brain's own logic closer to human cognitive
   reasoning — specifically **multi-angular, simultaneous** reasoning (many
   relational lenses held at once) rather than one-fact-at-a-time lookups.
2. Surface both **plausible** and **provable** causal effects, and **causal
   swarms/clusters** (groups of 3+ mutually-linked events), not just
   pairwise links — explicitly framed by the operator as support for how
   they process information (ADHD), not a nice-to-have.

Everything below is designed to serve those two goals directly, using the
platform's existing deterministic, no-embeddings, no-LLM-classification
house style wherever the data supports it, and calling out precisely the
few places that house style would need to bend.

## Current state (verified against the live repo, 2026-09-17)

- **Personas** (`common/personas.py`, 20 of them): flat prompt-text
  constructs (tier/preamble/task/sampling params). No structured or spatial
  fields. Each skill's own Python does 100% of data-gathering before
  handing the model finished text — no retrieval concept exists today.
- **Second Brain semantic layer**: one graph table,
  `semantic_note_derivations(path, relation, target, evidence, kind)`.
  Two `kind`s exist today: `derivation` (`leans_on`/`derives_from`/
  `reutilizes`) and `chronological` (`preceded_by`, added by
  `compile.py::assign_chronology()` onto the *same* table — the operator's
  explicit precedent, 2026-08-24: *"I don't want it as a second mechanism...
  baked into the causal chain."* Everything below follows that precedent.
  Deliberately no embeddings, no LLM classification anywhere in this layer.
- **Real geometric data**: flights have real lat/lon. Amtrak has station
  codes + delay minutes but no coordinates yet (small fix, ~30 NEC
  stations). Maritime/drone tracking has code but no receiver hardware and
  no populated data — **deferred but not absent** (see decision #6 below):
  schema support goes in now, seeded from a public sample dataset where one
  exists, rather than waiting entirely on hardware.
- **The sibling pattern this whole design follows**: `src/poller/skills/
  disruption_weather_digest.py` already does a 30-day rolling statistical
  digest across all three transport verticals (flights via real FAA/SWIM
  reason codes, trains via delay-minutes + an honest regional-weather-proxy
  caveat, maritime as a plainly-labeled `insufficient_data` stub), reading
  `analyze_disruption_weather_split()`/`analyze_train_disruption_summary()`/
  `analyze_vessel_disruption_summary()` in `common/db.py`. That whole family
  of `analyze_*` functions already shares two established defaults:
  `min_samples=5` and `days=30` (`delay_threshold_minutes=15` on the train
  side). Per operator direction 2026-09-17, this is the load-bearing
  convention for the new geometric/causal logic below — reuse these
  numbers and this honesty pattern (sample-window disclosure,
  `insufficient_data` labeling for sparse verticals) rather than inventing
  a parallel one.

## The paper, and what did/didn't transfer

The paper is about LLMs failing at *constructive geometry* (compass-and-
straightedge proofs), a different sense of "geometric" than this platform's
physical/spatial one. Its fix is a multi-agent Solver+Validator dialogue
framework, because that domain has no computable ground truth — the
validator exists to substitute for a missing oracle.

**This platform has the oracle** (haversine distance, real timestamps), so
the 3-agent review's unanimous verdict: **do not import Solver+Validator
dialogue anywhere in the compute path.** Three platform constraints make it
actively harmful, not just unnecessary: `compile.py` recomputes wholesale on
every run (so any LLM call inside it is re-paid every recompile, not once);
the hard 2-core llama cap can't absorb iterative multi-round dialogue; and
Claude CLI credits already ran out twice in one day during this session's
own work, including a subagent mid-run — evidence for the paper's own
"multi-agent overhead isn't earned without clean role separation" caveat,
not a hypothetical risk.

Two things from the paper *did* transfer, both refinements to presentation,
not the compute path:

- **Visual Relations Prompt** → the platform's design already does the
  paper's core insight better (compute once from structured data > extract
  once from an image), but it validated a gap: output must be one canonical,
  byte-identical block every consumer reads verbatim (not re-derived per
  persona), with explicit coverage ("N candidates evaluated, threshold X")
  so silence means "checked, nothing found," and a computed-at/staleness
  stamp since the underlying entities move.
- **Variable-naming bias** → the paper's finding that labeling choices
  smuggle in unearned semantic weight applies directly to how proximity gets
  narrated. Fix: sort by distance not narrative importance, raw identifiers
  only, no causal-implying adjectives ("nearby," "linked"), explicit
  disclaimer where the claim doesn't support causation.

## The design

### 1. Extend the one graph, don't build a second mechanism

Three new `kind`s on `semantic_note_derivations`, following the exact
pattern `chronological` already set:

| kind | relation | computed by | what it claims |
|---|---|---|---|
| `geometric` | `proximate_to` | `assign_geometry()` (new) | real-time spatial+temporal co-occurrence — **plausible tier** |
| `causal` | `statistically_associated` | `assign_causal_associations()` (new) | longitudinal, N-backed statistical association — **provable tier** |
| `cluster` | `member_of_cluster` | `assign_clusters()` (new) | 3+ entities that repeatedly co-occur as a group |

All three are pure deterministic compile steps against the semantic
layer's Postgres tables (SQLite when this was written; migrated in the
2026-09-19/20 cutover, `src/common/pg_schema/0055_second_brain_index.sql`
— `compile.py`'s live path now goes through `common.db_backend.pg_conn()`),
same cost profile as `assign_chronology()` today — **zero LLM calls, zero
embeddings, zero marginal tokens, no competition for the 2-core cap.**

### 2. Plausible tier — `proximate_to` (single instance)

`assign_geometry()`: haversine distance + time-window overlap over real
flight lat/lon and (once added) Amtrak station coordinates. Evidence packs
`distance_km`, `time_delta_min`, `method=haversine`, plus which dimension(s)
matched (see below). This is exactly what was designed before the paper
review; unchanged except for the framing rules in §6.

**Decided 2026-09-17 — thresholds, sibling to the 30-day disruption digest's
conventions:**
- **Time window**: ~30 minutes, structurally scaled from the same "30"
  convention rather than an unrelated invented number (the sibling file's
  30 is a *day* window for a statistical aggregate; this is a *minute*
  window for a live single-instance check — same numeral, correctly
  rescaled to this context's unit).
- **Distance**: no bespoke km constant. Amtrak-side proximity reuses the
  platform's *existing* `regional_stations()` membership (already
  configurable via `AMTRAK_REGIONAL_STATIONS`) as the spatial gate, rather
  than a new hardcoded figure. The general lat/lon case (flight-to-flight,
  or once maritime/drone positions exist) falls back to a new configurable
  env var (`GEO_PROXIMITY_RADIUS_KM`, same pattern as `AMTRAK_CORE_ROUTES`)
  so an operator can tune it without a code change — no magic number baked
  into the compile step itself.

**Decided 2026-09-17 — space vs. time gating:** both together is the
strong/preferred signal, but neither alone is discarded — the operator was
explicit that dropping a real spatial-only or time-only match "would be a
travesty." So `proximate_to` fires on *either* dimension matching, and
`evidence` records exactly which one(s) did (`matched=both|space_only|
time_only`), so a consumer can tell a strong compound match from a partial
one without losing the partial case entirely.

### 3. Provable tier — `statistically_associated` (longitudinal)

`assign_causal_associations()` — per entity-type pair (e.g. "NEC Amtrak
delay" × "DCA arrival delay"):

- **N**: count of historical `proximate_to` co-occurrences for that pair
- **Baseline rate**: how often each event type occurs independently, so the
  association is measured against chance
- **Direction**: pulled from existing `preceded_by` chronological edges —
  does A reliably lead B by a consistent interval
- **Minimum-N gate**: nothing gets written below the sample-size floor

Evidence packs N, rate-vs-baseline, lead-lag interval, and the specific
instance-edges it's built from (fully drillable back to source). The label
stays honest: "statistically associated," never "causes."

**Decided 2026-09-17 — sibling to the same `db.py` convention as §2:**
rolling `days=30` window, recomputed daily (matching
`disruption-weather-digest`'s own schedule, not the separate weekly
second-brain dump), `min_samples=5` floor before any edge gets written —
identical numbers already governing every `analyze_*` function in
`common/db.py`, not new ones invented for this layer.

### 4. Causal swarms/clusters — `member_of_cluster`

`assign_clusters()`: standard deterministic graph community detection
(connected-components or modularity clustering) over the accumulated
`proximate_to`/`causal` edges — finds groups of 3+ entities that repeatedly
show up interlinked, not just pairs. Each member edge carries its own
plausible/provable tier from §2/§3, so a cluster can be "3 plausible links,
1 provable" — the tiering doesn't get lost at the group level.

### 5. Multi-angular composite retrieval (the "simultaneous" piece)

New function, e.g. `second_brain.semantic.query_all_angles(entity)`, that
pulls every `kind` for a given entity **in one call** — derivation,
chronological, geometric, causal, cluster — and returns one structured
composite view. This is the literal mechanism for "multi-angular,
simultaneous" reasoning instead of sequential single-lens lookups: today
nothing queries more than one `kind` at a time, so even though the graph
already holds multiple relational axes, nothing actually reasons across
them together. This function is the fix, and it's what personas should call
instead of ad hoc single-kind queries.

### 6. Persona/prompt framing rules

Personas stay prompt-text constructs — no structural change. A skill's
data-builder gets a retrieval step (calling §5's composite query) spliced
in before prompt-build, same pattern `ep-advance` already uses for its
venue-section splice. Rules for the resulting block, from the paper review:

- One canonical block, byte-identical across every consumer — never
  re-derived/re-worded per persona
- Explicit coverage: "N candidates evaluated, threshold used"
- Computed-at timestamp, explicit snapshot framing
- Sort by distance/strength, never narrative importance
- Raw identifiers, no role adjectives
- Plausible-tier facts get a co-occurrence-only disclaimer; provable-tier
  facts get their N and baseline explicitly stated, never asserted as bare
  causation

Example:

> GEOMETRY (computed 2026-09-17T14:02 ET, snapshot; threshold 25km/30min)
> Pair 1 — AMTK2157 ↔ AAL1284: separation 12.4km; time-window overlap 18min.
> 3 candidates evaluated; 1 within threshold. Co-occurrence only, no causal
> relation computed.
>
> HISTORICAL PATTERN (N=14, past 90 days): WAS Acela delays >15min preceded
> a DCA arrival delay within 30min in 71% of instances (baseline: 12%).
> Statistical association, not a confirmed causal mechanism.

### 7. Where embeddings might enter later (gated, not default)

Out of scope for the above — everything in §2–§6 is deterministic. The one
place embeddings were seriously considered: cross-corpus **pattern analogy**
("does this resemble a past incident," as opposed to "what's near it now"),
which has no ground-truth metric, structurally the same problem the paper's
embedding-based retrieval solved. Recommendation if this is wanted later:
try deterministic typed-feature nearest-neighbor first (`kind='analogous'`,
matching on cause code/delay magnitude/carrier/weather-flag), and only
escalate to embeddings if that demonstrably underperforms against a
hand-labeled eval set. Either way, relaxing "no embeddings" is a house-style
rule change requiring explicit operator sign-off — never a silent decision
inside an implementation pass.

## Phasing

- **Phase 0**: station-coordinate reference table (in-repo static dataset,
  `cifp_*`-style — owned by the operator or a future self-hosted operator
  of this platform, decision #5); `geometric`/`cluster`/`causal` schema
  added to the ontology and compile pipeline; `assign_geometry()` **backfilled
  across the full existing vault corpus** (decision #1 — all ~7,000+ notes
  are the seed, not just events going forward) and inspected before
  anything consumes it. Zero persona changes.
- **Phase 1**: `query_all_angles()` composite retrieval built; wired into
  ONE persona (`ops-brief`) via its existing data-builder pattern; validate
  the worked example live.
- **Phase 2**: `assign_causal_associations()` (provable tier) added, daily
  cadence per decision #4; roll composite retrieval out to `ep-advance`/
  `disruption-weather-digest`/`transport-digest`.
- **Phase 3**: `assign_clusters()` added once enough `causal` edges exist
  for clustering to be meaningful (needs Phase 2's data first).
- **Maritime/drone (decision #6 — deferred but not absent)**: ontology
  entity types and schema fields go in during Phase 0 alongside the rest,
  so the platform *can* represent vessel/drone geometry — just with no live
  feed yet. Seed with a public sample dataset rather than leaving it
  schema-only-and-empty: NOAA's Marine Cadastre AIS dataset is a solid,
  well-established public candidate for maritime (free, historical,
  real-world AIS positions). No equally standard public drone/UTM dataset
  is known yet — that needs real research when this phase starts, not a
  guess now.
- **Deferred, gated separately**: `analogous`/embeddings — needs explicit
  operator approval as a house-style change before any implementation,
  independent of the phases above.

## Decisions locked in, 2026-09-17

1. **Retrofit scope**: backfill — all ~7,000+ existing vault notes are the
   seed corpus, not just events going forward.
2. **Plausible-tier thresholds**: ~30 minutes (sibling-scaled from the
   disruption digest's `days=30`, rescaled to minutes for a live check);
   distance uses the platform's existing `regional_stations()` membership
   for Amtrak, and a new configurable `GEO_PROXIMITY_RADIUS_KM` env var
   (matching the `AMTRAK_CORE_ROUTES` pattern) for the general case — no
   hardcoded km constant.
3. **Space/time gating**: either dimension alone is recorded (never
   silently dropped), `evidence` tags which one(s) matched
   (`both|space_only|time_only`); both together is the strongest signal.
4. **Provable-tier**: `days=30` rolling window, daily cadence, `min_samples=5`
   floor — identical to the established `common/db.py` `analyze_*` family
   defaults, not new numbers.
5. **Station-coordinate table**: in-repo static dataset, owned by the
   operator or a future self-hosted operator of this platform.
6. **Maritime/drone**: deferred but not absent — schema included now,
   seeded from a public sample dataset where one exists (NOAA Marine
   Cadastre AIS for maritime; drone/UTM dataset TBD, needs research).

All six are now settled. Ready to move to Phase 0 implementation on
confirmation.
