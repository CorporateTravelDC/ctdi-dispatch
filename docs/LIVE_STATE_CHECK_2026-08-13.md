# Live-State Doc Check — 2026-08-13

Post-commit drift check for `4fa3f05` + `c9ebb7b` (the pre-commit/pre-push
credential-scanning hooks: installed to `.git/hooks/`, ugrep `^\+\+\+`
hard-error fixed, new-branch scan-range fixed, silent public-mirror
auto-sync removed in favor of explicit `scripts/push-and-sync.sh`; `4fa3f05`
is the cosmetic stderr-suppression follow-up). Scope: does this change
invalidate anything the current docs claim (README.md, CLAUDE.md, docs/,
src/ingest/README.md, src/shared/watchlist_README.md)? Verified against the
live system, not prior docs. This is a findings file only — nothing staged
or committed.

**Working-tree context:** the tree carries 20 uncommitted modified files
from a concurrent model-selection-migration session (CLAUDE.md, README.md,
build-models.sh, all 17 `corporatetraveldc.*` Modelfiles,
docs/DEDICATED_MODELS_PLAN.md — matches "Finding A / in-flight
model-selection migration" in `LIVE_VALIDATION_AND_PENTEST_2026-08-13.md`).
Those files were left strictly alone. This check added exactly one file:
this one.

## Drifted / needs operator awareness (1 real item)

1. **HEAD's committed `MANIFEST.sha256` attests the uncommitted working
   tree, not the blobs committed at HEAD — recurrence of the exact issue
   `57fecbf` fixed two commits ago.** Verified directly: for **all 20**
   concurrently-modified files, the manifest hash committed in `4fa3f05`
   matches `sha256sum <working-tree file>` and does **not** match
   `git show HEAD:<file> | sha256sum` (e.g. `build-models.sh`: manifest
   `56ed094b…` = working tree, HEAD blob `5a78915c…`). Cause: `4fa3f05`
   re-signed with `scripts/sign-manifest.sh` (which hashes the working
   tree) while the migration session's edits were sitting in the tree, then
   committed that manifest.
   - **Live risk: none right now.** The runtime gate verifies the deployed
     tree, which is what the manifest describes: `verify-manifest.sh`
     returns "OK — all 626 files match", and the integrity sweep failed at
     01:34 and 01:49 (edits not yet covered) then went green at 02:04:54
     after the re-sign, and has stayed green.
   - **Real consequence:** a fresh checkout of `4fa3f05` fails manifest
     verification on those 20 files, and the signed manifest committed at
     HEAD does not attest the code committed alongside it — the same
     "manifest didn't attest to the code it shipped with" defect `57fecbf`
     called out. It self-resolves the moment the migration session commits
     its work with a fresh re-sign (its Modelfiles/build-models.sh will
     then match). Flagging so whoever commits next knows the re-sign is
     load-bearing, and so this isn't misread later as tampering.
   - Also note: any further edit to those 20 files before the next re-sign
     will flip the live integrity sweep back to FAILURE (the 01:34/01:49
     failures were exactly this state).

## Doc claims this change made TRUE (no edits needed)

2. **`SECURITY.md` — "a pre-commit hook rejects staged credentials"** was
   false yesterday (pentest item 5: hook existed in `scripts/` but was not
   installed). Now verified true: `.git/hooks/pre-commit` and
   `.git/hooks/pre-push` exist, are executable, and are byte-identical to
   `scripts/pre-commit` / `scripts/pre-push`; `core.hooksPath` unset (so
   `.git/hooks/` is what runs). The scanner logic was exercised
   synthetically (fake `ghp_…` PAT through the exact hook grep pipeline
   under this system's ugrep): it matches, and the unsuppressed variant
   does emit the "stray + at start of expression" warning that `4fa3f05`'s
   stderr redirects silence — both commit claims check out.
3. **`docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md` item 5** ("CONFIRMED
   gap … pre-commit not actually enforced", also in its summary/open-items
   lists) is now **remediated**. That file is a dated point-in-time report,
   so it was not edited — this note is the record that the item closed
   later the same day.

## Checked, still accurate (no drift)

- **`scripts/pre-commit-README.md`** — pattern table still matches the
  hook's actual `PATTERNS` + env-var scan after the ugrep fix (only the
  diff-header filter changed, not the credential patterns); install
  instructions (`cp` + `chmod`) describe exactly what was done; the
  `--no-verify` and `~/.secrets/` workflow notes still hold
  (`scripts/populate-secrets.sh` exists). This also keeps
  `DOCS_REFRESH_2026-08-11.md`'s "pattern table matches the hook script"
  claim true.
- **`docs/INFRA_MAP.md`** — "Public repos are produced by `push-public.sh`
  (force-push, auto-sanitizing)" still true; `push-and-sync.sh` is a
  wrapper that calls `push-public.sh` after confirming the origin push
  succeeded, so the sentence stands. The `post-commit` hook description
  (post-commit-doc-verify) is unaffected — that hook is still installed
  alongside the two new ones.
- **`docs/SECOND_BRAIN_STATUS.md`** (2026-07-29 addendum) — "use
  `scripts/push-public.sh`, never raw `git push public main`" is not just
  still accurate, it's now *enforced*: the new pre-push hook hard-blocks
  `git push public` with a pointer to the script.
- **`CLAUDE.md`** (committed and working-tree versions) — no hook/mirror
  claims; the "Never commit or push — stage-only" agent rule is unaffected.
- **`README.md`, `src/ingest/README.md`, `src/shared/watchlist_README.md`**
  — no claims touching hooks, credential scanning, or the mirror-publish
  workflow; untouched by this change.

## Minor pre-existing nit (not from this change)

- `scripts/pre-commit`'s header comment lists a `[0-9a-f]{64}` raw-hex
  pattern that the code does not actually implement (it's in neither the
  `PATTERNS` array nor the env-var scan). `pre-commit-README.md`'s table
  correctly omits it, so the README is right and the script's own comment
  overstates. One-line fix whenever the hook is next touched.

## Live snapshot at check time (~02:30 ET)

Core units `corporatetraveldc-{web,poller,pusher}` and
`corporatetraveldc-integrity-sweep.timer` all active; `/healthz` returns
`status: ok`, CPS `GREEN/GO`, 4 active tokens; integrity sweep green since
02:04:54 (see item 1 for the two failures immediately before the re-sign).

---

# Second check, same day (~14:40 ET) — model-consolidation deploy

Separate check by a later session, appended rather than overwriting the
morning entry above. Subject: the model-selection migration that just went
live (`ollama list` shows `corporatetraveldc-pi5-chat` rebuilt 14:29,
`corporatetraveldc-pi5-brief` built 14:35 ET). What shipped, per the
working-tree diff: **16 dedicated per-skill models consolidated to 2**
(`chat` + new shared `brief`, both `FROM phi3:mini`; 15 Modelfiles
deleted); persona/ROE content centralized in
`corporatetraveldc.dispatch-persona` (~2050 tokens, injected by
`common/llm.py`'s `ollama_post_with_retry()` on every call whose caller
sets no `system=`; public mirror gets `.template` only — scrub
`DROP_FILES` updated in the same change); load-vs-generation timeout split
in `llm.py` (`OLLAMA_LOAD_TIMEOUT=180` + adaptive history-based load gate;
long generations now accepted — live `OLLAMA_TIMEOUT=1200` in
dispatch.env); `MAX_CONCURRENT_REPORT_WAITERS=2` back-off cap in
`ollama_lock.py`; `trim_to_token_budget()` caps on the two largest prompts
(2200 tokens); `build-models.sh` smoke budget 200s→900s (philosophy
change: catches pathological models, no longer a speed SLA) with
orphaned-generation unload on smoke failure. Nothing was staged or
committed by this check; the only edit is this appended section.

## Drifted — docs still describing the 16-model world

1. **`CLAUDE.md` → "Local LLM (Ollama)"** (the uncommitted 2026-08-11
   rewrite sitting in the working tree). Now-false claims: "16 dedicated
   models"; "the 4 brief-class models … are FROM phi3:mini; everything
   else is gemma3:4b" (it's 2 models, both phi3:mini — `chat` moved off
   gemma3:4b in this change); "only promotes brief models to :latest after
   a 200 s smoke test" (budget is now 900 s and deliberately generous);
   `OLLAMA_TIMEOUT=240` in the resource-limits warning box (live value
   1200, plus the new separate 180 s load gate). Still-true parts of that
   section are listed below.
2. **`README.md`** — line 54 stack-table row ("16 dedicated … brief-class
   on phi3:mini, the rest on gemma3:4b") and the whole Local-LLM section
   ~lines 488–510 ("Each LLM-calling skill has its own Ollama model", the
   phi3/gemma3 base table naming all 16). Architecturally obsolete; the
   SWA/`SWA_DENYLIST_REGEX` rationale prose remains true and the guard
   still exists in build-models.sh.
3. **`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`** — `OLLAMA_TIMEOUT` listed
   as "240s (fail-fast)" (twice); the fail-fast philosophy itself was
   reversed by operator directive (slow-but-working generation is now an
   accepted cost, gated at load time instead). Its cross-ref "the 240s
   timeout rationale is in src/poller/skills/ops_brief.py" no longer
   matches that file. Its `OLLAMA_MAX_LOADED_MODELS=1`,
   `OLLAMA_PREFLIGHT_COOL_TARGET_C=70`, CPUWeight/CPUQuota rows remain
   accurate.
4. **`docs/DEDICATED_MODELS_PLAN.md`** — the (also-uncommitted) 08-11
   status addendum's "current ground truth" bullets (16 models, 24
   dedicated call sites, "all other Modelfiles remain gemma3:4b") became
   historical today. The doc is a dated design record; it needs one new
   status line pointing at the consolidation, not a rewrite.
5. **`docs/lmstudio-dispatch-prompts.md`** line 8 — repeats the
   "brief-class on phi3:mini, others on gemma3:4b" per-skill-model claim.
6. Cosmetic: `CLAUDE.md`'s "(145 loaded units at this snapshot)" — 140
   now; the doc already tells you not to trust that number.
7. Code-comment nit (not docs, flagging for whoever edits next): the
   `ops_brief.py` / `ep_advance_brief.py` module docstrings updated in
   this change still describe `corporatetraveldc-pi5-brief` as
   "mistral-nemo 12B" — it is `FROM phi3:mini` (3.8B).

## Checked, still accurate (verified live, not assumed)

- `common/llm.py` remains the single entry point; thermal preflight live
  target is still 70 °C (`OLLAMA_PREFLIGHT_COOL_TARGET_C=70.0` in
  dispatch.env overrides the code default); `_abandon_ollama_generation()`
  claim holds and build-models.sh now applies the same unload on smoke
  failure; manifest verification before inference now also covers
  `dispatch-persona` itself.
- Cloud-fallback story: `ANTHROPIC_FALLBACK_ENABLED` still defaults true,
  watch/brief skills still pass `allow_anthropic=False` (seen live in
  today's aviation-daily-watch journal), deterministic-template fallback
  exercised live today, `brief-fallback-monitor.timer` active.
- Skill/unit topology unchanged: all skill units and timers keep their
  names (ops-brief :00, ep-advance :30, etc.) — only the model name each
  skill sends changed, so `docs/ALERT_REFERENCE.md` /
  `docs/ALERT_ARCHITECTURE.md` are unaffected.
- `src/ingest/README.md` and `src/shared/watchlist_README.md` — no
  model/LLM-dependent claims; unaffected by this change.
- Working-tree `MANIFEST.sha256` was re-signed after the migration edits:
  `verify-manifest.sh` returns "OK — signature valid, all 615 files
  match" live. (The morning entry's item 1 about the *HEAD-committed*
  manifest still stands until the migration is committed with that
  re-sign.)
- `/healthz` ok, CPS GREEN/GO, core web/poller/pusher active.

## Live-state notes for the operator (not doc drift)

- **11 orphaned models still resident in Ollama** (~2.2 GB each:
  `-osint`, `-osint-monitor`, `-tfr-enrichment`, `-route-impact`,
  `-weekly-summary`, `-aam-watch`, `-dispatch-desk`, `-transport-digest`,
  `-disruption-weather-digest`, `-secondbrain-daily`,
  `-secondbrain-weekly`). No code references them anymore; `ollama rm`
  cleanup pending. The 4 old phi3 brief models are already gone.
- **No production run has succeeded on `pi5-brief` yet** as of 14:40 ET:
  the 14:30 ep-advance run hit an Ollama timeout and fell back to
  deterministic — it collided with the 14:29–14:35 model-build window
  itself, so expected. First clean test is the 15:00 ops-brief;
  brief-fallback-monitor will alert on `ops-health` if fallback persists.
- The 10 failed units on the board predate this deploy: fr24feed /
  planefinder / dispatch-desk-memo / second-brain-weekly from 08-11, and
  this morning's 04:35–08:17 Ollama-timeout failures
  (disruption-weather-digest, aviation/gig/concierge/AAM watches,
  ingest-feed-watch) — the diagnostic arc that motivated this migration
  (see build-models.sh's new 2026-08-13 comments), not a result of it.
