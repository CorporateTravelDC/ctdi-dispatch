# Dedicated Per-Task Models — Architecture, RAG Plan, Fine-Tuning Assessment

Written 2026-08-02. Covers the dedicated-Modelfile refactor done that night, a
design for weekly-synthesis mini-RAG, an honest fine-tuning feasibility
assessment (including the laptop-vs-Pi question), the laptop→Pi model
transfer workflow, and how this pattern is meant to serve as the benchmark
for a future executive-assistant back-office platform variant.

> ## Status update — 2026-08-11 (verified against build-models.sh, Modelfiles, and all call sites)
>
> The architecture below shipped and is live, but several §1 numbers have
> moved since 2026-08-02. Current ground truth:
>
> - **16 dedicated models** now exist (`build-models.sh` `MODELS` map), not
>   15: the 2 general-purpose (`chat`, `osint`) plus 14 task models — the
>   2026-08-02 set grew by `osint-monitor` splitting out,
>   `aam-watch`/`dispatch-desk`/`transport-digest` additions, and
>   **`disruption-weather-digest`** (2026-08-09/10), which this doc's original
>   call-site census predates.
> - **24 LLM call sites across 19 files** use dedicated models (not 13):
>   includes `disruption_weather_digest.py`, the six daily category-watch
>   skills sharing `corporatetraveldc-pi5-aam-watch`, and
>   `common/entity_tracking.py` (×2, uses `-chat`). `flight_impact.py`,
>   `train_impact.py`, and `freshness_audit.py` define `OLLAMA_MODEL` for
>   SR-1 labeling only — they make no LLM call.
> - **The 4 brief-class models (`ops-brief`, `ops-brief-trend`, `ep-advance`,
>   `ep-advance-trend`) are now `FROM phi3:mini`, not `gemma3:4b`**
>   (2026-08-10/11): gemma3's Sliding-Window Attention defeats llama.cpp
>   KV-cache reuse, forcing full re-processing of the long hourly-brief
>   prompts, blowing the 240 s `OLLAMA_TIMEOUT` and driving near-100%
>   deterministic fallback. `build-models.sh` now hard-blocks gemma2/gemma3
>   bases for brief models (`SWA_DENYLIST_REGEX`) and gates brief-model
>   promotion to `:latest` behind a 200 s smoke test on a
>   125%-of-worst-case prompt. All other Modelfiles remain `gemma3:4b`.
> - `src/common/llm.py` additionally gained `_abandon_ollama_generation()`
>   (2026-08-11) — sends Ollama a `keep_alive: 0` unload the moment a
>   caller's request times out, because client timeouts don't stop
>   server-side generation (orphaned generations piled up to a 52 load
>   average before this fix).
> - The §2 mini-RAG design and §3–5 fine-tuning/EA-variant assessments
>   remain **designed-not-built** — unchanged.

## 1. What changed tonight

Every skill that calls an LLM (11 total: `ops_brief` + its trend sub-call,
`ep_advance_brief` + its trend sub-call, `tfr_enrichment`, `route_impact`,
`weekly_summary`, `osint_monitor`, `aam_weekly_watch`, `dispatch_desk_memo`,
`transport_pattern_digest`, `second_brain_daily`, `second_brain_weekly` — 13
distinct call sites) previously shared one of two general-purpose models
(`corporatetraveldc-pi5-chat` / `corporatetraveldc-pi5-osint`) and re-sent
its full instructional system prompt as a plain string on every single call.

Now each call site has its own dedicated Ollama model
(`corporatetraveldc-pi5-<task>`), built from a Modelfile
(`corporatetraveldc.<task>` at repo root) that bakes that instructional
content in as the model's own default `SYSTEM`. `common/llm.py` was changed
so that passing `system=None`/`""` omits the `system` key from the Ollama
request entirely, letting the model's own baked-in default apply instead of
being overridden by an empty string (this was the actual mechanism gap —
every call site was always sending *some* `system` value, even empty ones,
which Ollama treats as a real override, not "use the Modelfile default").

Two real findings came out of tracing every call site before touching it:

- **`ops_brief.py` had a large, well-structured, 12-section
  `SYSTEM_PROMPT` constant (LEAD / DC METRO / NORTHEAST / TRANSCON HUBS /
  NAS PROGRAMS / ATCSCC FORECAST / TFRs / NWS ALERTS / AMTRAK NEC / ROUTE
  IMPACT / OPERATIONAL NOTES / BOTTOM LINE) that was dead code** — defined
  but never referenced by the actual generation path. The real, currently-
  running ops brief uses a much shorter inline prompt in `_call_ollama()`.
  Tonight's `corporatetraveldc.ops-brief` Modelfile was built from the
  *real* live prompt, not the elaborate unused one. **The elaborate
  12-section format is still sitting right there in the file if you want
  it wired in for real** — it looks like a genuinely stronger design than
  what's currently shipping. Worth a explicit decision, not a silent
  adoption, since it would change the actual brief format users see.
- Both `ops_brief.py` and `ep_advance_brief.py` each had a **second, separate
  live prompt** for their 6h/12h trend-narrative sub-call
  (`TREND_SYSTEM_PROMPT` / `TREND_SYSTEM_PROMPT_EP`) that a first pass would
  have missed by only grepping for `SYSTEM_PROMPT`. Both now have their own
  dedicated Modelfiles (`ops-brief-trend`, `ep-advance-trend`).

15 models exist now: the original 2 general-purpose ones (kept for
interactive/ad-hoc use via open-webui) plus 13 dedicated ones.

### A real cost this introduces: model-swap overhead

The Pi 5 has no GPU — Ollama runs everything on CPU and, confirmed tonight
via `ollama ps` while verifying this change, **holds exactly one model
resident at a time**. Swapping from serving skill A's dedicated model to
skill B's dedicated model means evicting A and loading B fresh off disk —
each of these models is ~3.3-3.4GB. With 2 shared models, most consecutive
skill runs already had the right model loaded. With 13 dedicated models,
skills that fire close together in the schedule (ops_brief and its own
trend sub-call are adjacent by design; the hourly osint_monitor could
collide with anything) now pay a reload cost more often. This didn't show
up as a functional bug in tonight's testing — the build/verification
process itself triggered a long queue of back-to-back swaps and a few
verification probes timed out waiting in that queue, not because anything
was broken. But it's a real tradeoff of "one model per task" on
single-model-resident hardware, worth watching via real scheduled-run
timing over the next few days rather than assuming it away.

## 2. Mini-RAG plan for second-brain weekly synthesis

Current state: `second_brain_weekly.py` reads the current week's 7 daily
notes from the vault and asks the model to synthesize patterns across just
those 7. It has no visibility into prior weeks at all — so "CPS trended
worse this week" has no way to mean anything relative; there's no baseline.

Proposed design (not yet built — this is the plan the operator asked for):

1. **Retrieval step, before the LLM call.** Before building the weekly
   prompt, query `second_brain.index_db` (the FTS index already built and
   maintained by tonight's/recent sessions' `index_db.py --scan`) for the
   prior 2-4 weekly syntheses (`04-Syntheses/weekly/YYYY-Www.md`), plus any
   `06-AI-Memory` notepad entries tagged from that window. This is real
   retrieval — SQL/FTS lookup by date range, not a vector search; the
   corpus is small enough (a few hundred KB of markdown) that a full FTS
   query per run is cheap and doesn't need embeddings infrastructure.
2. **Condense before injecting.** Don't paste 4 raw prior weeklies into the
   prompt — that reintroduces the "context bloat" problem this whole
   refactor is trying to avoid. Instead, extract just the closing
   patterns/trend lines from each prior weekly (the syntheses already end
   with forward-looking notes per the current `SYSTEM_PROMPT`'s rules) and
   inject those as a compact "prior trend" block ahead of this week's daily
   notes.
3. **New prompt section, not a new model.** The `corporatetraveldc.
   secondbrain-weekly` Modelfile's SYSTEM stays as-is (rules about dates,
   headers, number fidelity); the runtime prompt gains a
   `Prior weeks' trend (for context, do not repeat verbatim):` block before
   the current week's daily notes. This keeps the "static instructions
   live in the Modelfile, only real per-run data lives in the prompt"
   principle intact.
4. **Bound it.** Cap at 4 prior weeks (roughly a month of trailing
   context) so prompt size stays predictable even as the archive grows
   past a year.

This is a genuinely buildable next step — happy to implement it directly
next session; flagging as designed-not-built here since tonight's session
was already large.

## 3. Fine-tuning feasibility — direct answer

**Should you fine-tune on your laptop instead of the Pi? Yes, clearly.**
Three independent reasons, not just one:

- **No GPU on the Pi 5.** Fine-tuning (even lightweight LoRA/QLoRA
  fine-tuning of a 4B model) is dramatically faster with any GPU
  acceleration — CUDA on an NVIDIA laptop, or unified memory on Apple
  Silicon (M-series). CPU-only fine-tuning of even a 4B model is measured
  in many hours-to-days per run on a Pi 5; the same job is typically
  30-90 minutes on a modern laptop GPU.
- **The Pi is already thermal- and CPU-constrained in production.**
  Tonight's own verification pass confirmed Ollama holds one model
  resident at a time and the box is running an active thermal governor
  (SIGSTOP/SIGCONT cycling documented in prior sessions, still active
  tonight at 68°C idle-ish). Running a multi-hour fine-tuning job on the
  same box that's serving live TFR/weather alerts would either starve
  production or get starved by it.
- **RAM headroom.** The Pi 5 in this deployment has limited system RAM
  shared between the OS, all the ingest containers, and Ollama itself.
  Fine-tuning needs headroom beyond just the base model's footprint
  (optimizer states, gradients, even with LoRA). A laptop with 16GB+
  unified/dedicated memory has more slack to work with.

**What "minor fine-tuning" would actually look like, concretely:**
LoRA or QLoRA fine-tuning of `gemma3:4b` (not full fine-tuning — full
fine-tuning of even a 4B model needs far more compute/data than this
use case justifies) using a small, curated dataset built from this
platform's own real outputs: pairs of (raw data snapshot → good, already-
human-reviewed brief/synthesis). Tonight's corrected daily notes, the
2-week demo archive, and any future manually-edited briefs are exactly
the kind of paired data this would need. Realistic tools: **Unsloth**
(fastest path, works well for Gemma, runs on both CUDA and Apple Silicon
via MLX conversion) or **MLX-LM** directly if the laptop is a Mac (native
Apple Silicon fine-tuning, no CUDA needed). Either produces a LoRA adapter
that gets merged into the base weights to produce a new standalone model.

**What this is not, yet:** a claim that fine-tuning will definitely improve
output quality more than the Modelfile-per-task + mini-RAG changes already
do. Those two are lower-risk, lower-cost, and address the concrete problems
actually observed tonight (hallucinated dates, generic prompts, no trend
baseline). Fine-tuning is worth doing as a *next* step once there's a real
corpus of corrected/approved outputs to train on — training on too little
data risks making the model worse, not better. Recommend treating tonight's
corrected notes as the start of that dataset, not rushing to fine-tune on
what exists today.

## 4. Model transfer workflow: laptop → Pi

Once a LoRA adapter is trained and merged into a standalone model on the
laptop, moving it onto the Pi to become a real `corporatetraveldc-pi5-*`
model:

1. **Export to GGUF on the laptop.** `llama.cpp`'s `convert_hf_to_gguf.py`
   (or Unsloth's built-in GGUF export, which wraps the same conversion)
   turns the merged Hugging Face-format model into a single `.gguf` file.
   Quantize to `Q4_K_M` or similar at export time — keeps the file in the
   same rough size class (3-4GB) as the current gemma3:4b-based models,
   which the Pi has already proven it can serve.
2. **Copy the GGUF to the Pi.** `scp model.gguf corporatetraveldc@100.x.x.x:~/`
   over Tailscale — no different from any other file transfer already used
   tonight.
3. **Write a Modelfile pointing at the local GGUF instead of a registry
   name.** Same format as tonight's Modelfiles, just a different FROM line:
   ```
   FROM ./model.gguf
   SYSTEM """
   ...
   """
   ```
4. **`ollama create corporatetraveldc-pi5-<name> -f Modelfile`** — same
   command already used for all 15 models tonight; Ollama treats a
   GGUF-backed model exactly like a registry-pulled one from that point on.
5. **Swap the relevant skill's `OLLAMA_MODEL` constant** to the new name,
   same pattern as tonight's call-site patches.

No new infrastructure needed on the Pi side — the whole pipeline built
tonight (per-task Modelfiles, `system=None` passthrough, dedicated model
per skill) is already the target shape for a fine-tuned model to slot into.

## 5. Framework for a future EA back-office variant

the operator's framing tonight — "use this as a framework and benchmark for a
future planned executive assistant back-office variant based off of the
overall platform itself" — maps cleanly onto what's built:

- **One dedicated model per task, not one model for everything.** An EA
  back-office variant would have its own task set (inbox triage, meeting
  prep, expense categorization, whatever it ends up covering) — the same
  pattern (per-task Modelfile, thin runtime prompt carrying only the
  real per-call data) generalizes directly, it's not chauffeur-specific.
- **Static instructional context lives in the model, not re-sent per
  call.** This is the actual reusable insight, independent of domain —
  it's what makes a "platform" out of what would otherwise be a pile of
  one-off prompts per feature.
- **The mini-RAG pattern (§2) generalizes too** — any back-office task
  that benefits from "what happened last time" (recurring meeting prep,
  monthly expense patterns) can reuse the same FTS-over-markdown-vault
  retrieval approach instead of needing a vector DB from day one.
- **Fine-tuning readiness is a data problem, not an infra problem** —
  once there's a real corpus of human-corrected outputs from whichever
  domain the EA variant covers, the transfer workflow in §4 is already
  the whole pipeline needed to turn that into a dedicated model.

The honest gap: none of this has been tested against a second domain yet.
Tonight's work proves the pattern *within* the chauffeur/dispatch domain.
Treating it as a validated benchmark for an EA variant means the next real
test is standing up one small EA-variant task end-to-end using this exact
pattern, not assuming it transfers because it's architecturally clean.
