# Local Model Evaluation and Swap — 2026-09-21

**Status:** decided and deployed. phi3-mini → Qwen3-4B-Instruct-2507 is live
as of this date. This doc is the record of why, with real measured data —
not vendor benchmarks, not web-search estimates, actual runs on this exact
box.

## Why this happened

Geometric reasoning Phase 1 (`docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md`)
wired a new GEOMETRY section into `ops-brief`'s prompt. Live validation found
it present in the data pull but absent from phi3-mini's synthesized
narrative — the model wasn't reliably covering every section it was
explicitly instructed to include, a documented failure class for this model
(see the 2026-08-16 echo-instead-of-synthesize incident already in
`ops_brief.py`'s own comments). That specific gap was closed with a
deterministic guaranteed-append (same pattern as `disruption_capsule`), but
it raised the real question underneath: is phi3-mini still the right model
for this job, on this hardware, now that the platform asks more of it than
it did at the 2026-08-27 Ollama-retirement cutover?

## Method

Three models, same two real production skills (`ops-brief`, `ep-advance-brief`),
same prompts, same hardware, sequential (never concurrent — the "only one
model weight EVER loaded" constraint from the 2026-09-06 consolidation
directive is non-negotiable and wasn't relaxed for this test):

1. **phi3-mini-q4_0** (baseline, already resident) — captured recent real
   production output as-is, no re-run needed.
2. **Qwen2.5-3B-Instruct-q4_0** — downloaded from `Qwen/Qwen2.5-3B-Instruct-GGUF`
   (HuggingFace), swapped in via the shared `corporatetraveldc-llama.service`
   unit, both skills triggered fresh, output captured, swapped out.
3. **Qwen3-4B-Instruct-2507-q4_0** — downloaded from
   `unsloth/Qwen3-4B-Instruct-2507-GGUF`, same procedure.

phi3-mini restored, confirmed byte-identical config to the tracked repo copy,
before the permanent decision was made and re-deployed for real.

## Results — real measurements, this box

| Model | Generation | Prompt eval | Weights |
|---|---|---|---|
| Qwen2.5-3B | 8.0 tok/s | 17.1 tok/s | ~2.0 GB |
| Qwen3-4B | 5.1 tok/s | 8.7 tok/s | ~2.4 GB |
| phi3-mini (prior prod) | **3.6 tok/s** | 15.7 tok/s | ~2.2 GB |

phi3-mini was the slowest of the three at generation despite being smaller
than Qwen3-4B. No public benchmark found during earlier research covered this
exact combination (Pi 5 CPU, raw llama.cpp not Ollama, this quantization) —
this table is the actual answer.

**Section-coverage / instruction-following** (the thing that actually
mattered):

- **phi3-mini**: silently dropped GEOMETRY from its narrative entirely.
- **Qwen2.5-3B**: genuine synthesis, not an echo — but silently dropped AAM,
  a different section, same failure class. This is the interesting negative
  result: it suggests the problem isn't phi3-specific, it's small-model
  synthesis reliability under a long, dense prompt in general.
- **Qwen3-4B**: the only model that explicitly acknowledged both AAM and
  Geometry in its own narrative ("No AAM or geometry-based disruptions")
  rather than silently omitting either. Also produced the strongest
  ep-advance-brief output of the three — specific, professional, actionable.

**Tokenizer recalibration**: re-measured chars/token on the same
aviation-dense sample string used for phi3-mini's original 2026-09-02
calibration (`ops_brief.py`'s `OPS_BRIEF_DATA_TOKEN_BUDGET`). Qwen3 runs
~2.08 chars/token vs phi3's ~1.84 on the identical text — slightly more
token-efficient, meaning the existing budget constants are still safe
(mildly conservative, not at risk of overflow) under the new model. Not
retuned tighter; that's the safe direction to leave slack in.

## Decision

Qwen3-4B-Instruct-2507 (q4_0) is the new resident model for
`corporatetraveldc-llama.service`, effective 2026-09-21. phi3-mini and the
evaluated-but-not-chosen Qwen2.5-3B GGUFs are kept on disk (not deleted) as
the historical record of this evaluation — not loaded, not referenced by
anything live.

**Known follow-up, not yet done:** the model file currently lives at
`/home/corporatetraveldc/model-downloads/qwen3-4b-instruct-2507-q4_0.gguf`
rather than the established `/var/lib/corporatetraveldc/models/` convention,
because that directory is root:root 755 and placing a file there needs the
operator's own `sudo`. Functionally identical either way; the exact move
commands are in `corporatetraveldc-llama.service`'s own comment block.
Real RSS under sustained live load hasn't been observed yet either — the
memory ceiling bump in that same unit is a reasoned estimate, not yet a
measured one; re-tighten once real numbers exist, same as every other
provisioned value in that file.

---

## The bigger picture this sits inside

This evaluation is one milestone in a longer arc worth having written down
somewhere durable, not just scattered across a hundred session logs:

**Where this started**: a cheap experiment — reportedly on the order of
**$400** of hardware and a blog post's worth of inspiration — to see whether
a single Raspberry Pi could run a real, useful, locally-inferenced dispatch
intelligence platform without any cloud LLM dependency at all.

**Where it went**: local rule-based/templated briefs, to LLM-synthesized
briefs (Ollama, then llama.cpp direct), to a real signed-manifest integrity
system and SR-1/SR-2 audit logging (because a regulated-industry-adjacent
platform needs to be provable, not just correct), to a full Postgres cutover
off SQLite for concurrent-write correctness at scale, to **geometric
reasoning** — deterministic, non-LLM spatial/temporal correlation across
flights and trains, because the operator explicitly wanted the platform's
own reasoning to work more like actual multi-angular human cognition instead
of one-fact-at-a-time lookups — to, now, **actively measuring which local
model the platform's own reasoning quality depends on**, rather than
assuming the first model that worked is still the right one as the platform
asks more of it.

**Where it's headed** (explicitly future, not built, not started):

Once the LimoAnywhere/RingCentral/3CX webhook buildout (queued, see the
operator's own stated ordering — after geometric reasoning) is done, the
next architectural step under consideration is making this platform
**ANP (Agentic Network Protocol) aware** — not replacing MCP, which this
platform already uses and isn't retiring here, but adding ANP as a second,
complementary protocol layer specifically for **inter-site federation**:
a main-campus / satellite-campus, or main-affiliate / downstream-affiliate
topology, where every site runs the same underlying platform and logic, but
sites can discover and work with each other over ANP rather than each being
an isolated island. The explicit design constraint the operator has already
stated for this, ahead of any implementation: it has to stay **provable and
audit-friendly at every location, for every operator**, not just functional
— consistent with why this platform already has a signed-manifest integrity
system, SR-1/SR-2 logging, and CUI-handling rules baked in rather than
bolted on. This is a real, live idea in progress, not a commitment or a
scoped project yet — recorded here so the thread doesn't get lost, to be
scoped properly when its turn comes.
