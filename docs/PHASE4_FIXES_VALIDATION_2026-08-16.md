# Phase 4 Fixes Validation — 2026-08-16 (second independent cold-start audit)

Scope: ONLY the 5 fixes applied in response to
`docs/PHASE4_VALIDATION_2026-08-16.md`, checked fresh with no prior
context. Read-only; nothing changed except this report (one transient
`__pycache__` byte-code file created by a syntax check was removed).
Paths relative to `/opt/corporatetraveldc/private/ctdi-dispatch-internal`.

## Verdict: all 5 fixes PASS — no new problems introduced

Nothing needs another fixing pass. Two cosmetic notes (stale comments,
one style inconsistency) recorded below; none functional.

## Fix 1 — osint_monitor.py model repin: PASS

- `src/poller/skills/osint_monitor.py:46-48` now defaults to
  `corporatetraveldc-pi5-osint-monitor:latest`; that model exists live
  (`ollama list`) and byte-matches the pin at
  `src/common/entity_tracking.py:124` (`EXTRACTION_MODEL`).
- Env resolution confirmed: neither `OLLAMA_OSINT_NARRATOR_MODEL` nor
  `OLLAMA_MODEL` is set in `/etc/corporatetraveldc/dispatch.env`, so the
  hardcoded default is the live path — and it is now correct.
- The live model's SYSTEM prompt (via `ollama show --system`) explicitly
  supports BOTH call shapes: a dedicated paragraph for the 2-sentence
  OSINT narrative, and a closing paragraph ("This model ALSO serves the
  platform's OSINT-leaned entity-extraction utility calls
  (common/entity_tracking.py)") instructing structured output to
  override the narrative shape entirely. The dual-use design is coherent
  as written, not just one-sided.
- Dead-name sweep of `src/`: `corporatetraveldc-pi5-chat` is NOT dead —
  it is one of the 21 live models (Modelfile `corporatetraveldc.chat`
  at repo root), so `src/runner/main.py:101`'s default is correct.
  `corporatetraveldc-pi5-brief:latest` survives only in:
  - executable-looking but inert defaults: `freshness_audit.py:28`,
    `train_impact.py:34`, `flight_impact.py:36` — verified these three
    files perform no inference (no `common.llm` import; `MODEL` is used
    solely as a `log_usage()` label), and in-container the env's
    `OLLAMA_OSINT_MODEL` resolves first anyway. Pre-existing debris
    already flagged in the first pass (section 4), not a regression.
  - comments/docstrings only: `train_impact.py:4`, `flight_impact.py:4`,
    `ep_advance_brief.py:4`, `weekly_summary.py:4`, `ops_brief.py:4,707`,
    `disruption_weather_digest.py:36`, `osint_monitor.py:43` (the fix's
    own explanatory comment).
- `osint_monitor.py` parses clean (`ast.parse`).

## Fix 2 — scrub-public-tree.py DROP_FILES: PASS

- All 21 Modelfile basenames present at `scripts/scrub-public-tree.py:66-86`.
  Verified programmatically (parsed `DROP_FILES` via `ast`, set-diffed
  against `ls corporatetraveldc.*`): 21 on disk, 21 in the set, zero
  mismatches in either direction. Stale `dispatch-persona` entry removed.
- Matching logic verified against code, not the comment: `scrub_tree()`
  line 403 does `if obj_type == "blob" and name in DROP_FILES` where
  `name` is the per-entry basename from `git ls-tree` — basename-only
  match at any depth, exactly as the line-87 comment claims. The
  Modelfiles sit at repo root, so they match trivially once tracked
  (they are currently untracked; the entries are correctly prospective
  for the coming commit).
- Script compiles clean (`py_compile`) and its structure (scrub →
  mktree → `verify_scrubbed` fail-closed) is intact.
- Other new/changed files from tonight checked for missing DROP entries:
  `docs/PHASE4_VALIDATION_2026-08-16.md`,
  `docs/DRIFT_GAPS_REPORT_2026-08-15.md`, `docs/SUDO_JUSTIFICATION_PROPOSAL.md`,
  `scripts/ollama-wedged-detector.sh`, `scripts/sudo-approval-gate.sh`,
  `build-models.sh`. Every sensitive literal they carry (operator name,
  business-domain subdomains, tailnet IP 100.94.80.x) is already covered
  by SUBSTITUTIONS or REGEX_SWEEPS, and `verify_scrubbed()` fails closed
  on anything unrecognized — no additional DROP_FILES entry is required
  for leak prevention. (Whether the two audit docs should transit public
  at all, even scrubbed, remains the operator-judgment item the first
  pass raised; not a defect of this fix.)
- Unchanged from the first pass, operator's call, not this fix's fault:
  the deleted public `.template` counterparts (chat/osint/persona) still
  have no public-facing replacement.

## Fix 3 — orphaned baseline-test model removal: PASS

- `ollama list` no longer shows `corporatetraveldc-dispatcher-baseline-test`
  (22 entries: the 21 pi5 models + `phi3:mini`, 1:1 with build-models.sh).
- Repo-wide grep (`*.py`, `*.sh`, `*.container`, `*.env*`): zero
  references to the name anywhere, code or comments.

## Fix 4 — dispatch-desk-memo num_ctx 6144 → 8192: PASS

- Modelfile `corporatetraveldc.dispatch-desk-memo:21` = `PARAMETER
  num_ctx 8192`, with a dated explanatory comment (lines 15-20).
- Live model WAS rebuilt: `ollama show corporatetraveldc-pi5-dispatch-desk-memo
  --parameters` shows `num_ctx 8192` / `num_predict 1100`; the model's
  modified time (minutes old vs 6h for the other 20) confirms tonight's
  rebuild.
- Margin is real, not razor-thin: measured worst case 5092 (prompt) +
  1100 (predict) = 6192 vs 8192 → 2000 tokens (~32%) headroom. Even if
  the 4-chars/token trim heuristic underestimates somewhat worse than
  the observed ~2.3x on a future worst week, the gap absorbs it.
- Resource check: the KV cache lives in host `ollama.service`
  (MemoryHigh/MemoryMax = infinity), NOT in the skill container — the
  unit's `Memory=1536m`/`--memory-swap=1536m` bound only the Python
  client and are unaffected by num_ctx. phi3:mini f16 KV is roughly
  0.4 MB/token → ~3.1 GB at 8192 vs ~2.4 GB at 6144, a ~0.8 GB increase;
  box has 16 GB with ~10 GB available and `ollama ps` shows nothing
  resident (skills run sequentially, one model loaded at a time). Fits
  comfortably; no unit change needed.
- Python-side budget math still coherent: the `trim_to_token_budget(prompt,
  2200)` at `dispatch_desk_memo.py:212` correctly stays as-is — it bounds
  the input; the ceiling now covers what that bound actually produces.
  Cosmetic: the comment at lines 204-211 is stale (references
  `DISPATCH_PERSONA` injection in `common/llm.py`, which was removed in
  the rebuild, and "the shared model's num_ctx" — it is a dedicated
  model now). No math impact; fold into the stale-comment sweep.

## Fix 5 — transport-pattern-digest TimeoutStartSec 1400 → 1600: PASS

- Applied: `.config/containers/systemd/corporatetraveldc-transport-pattern-digest.container:48`
  = `TimeoutStartSec=1600`, with an updated comment that keeps the
  original 800+600 formula visible and dates the change.
- Relationship to the Python timeout (600s at
  `transport_pattern_digest.py`, max_retries=0) unchanged and sound:
  worst case 1400, cushion now 200s.
- Consistency vs the other 16 reconciled units (all margins recomputed
  from each unit's own stated formula): the sibling convention is a
  round-up cushion of 20-90s absolute (~1-6% relative) — e.g. aam-daily
  8510→8600, desk-memo 10340→10400, ops-brief 2510→2600, sb-weekly
  5480→5500. Transport's 200s (~14%) is now the LARGEST cushion in the
  set, where the first pass's suggestion (1500) would have matched the
  round-up style exactly. This errs in the safe direction — a
  TimeoutStartSec backstop that is 100s more generous only delays the
  kill of a genuinely hung start; it cannot cause a premature kill — so
  it is a style inconsistency, not a defect. Fine to leave, or trim to
  1500 in a later polish pass if uniformity is wanted.

## Nothing-else-changed check: PASS

Files modified after the first validation report's mtime (21:57:43):
exactly `src/poller/skills/osint_monitor.py`,
`scripts/scrub-public-tree.py`, `corporatetraveldc.dispatch-desk-memo`,
and the transport `.container` — the four file-level fixes — plus
byte-code cache noise. Fixes 3 and 4's live halves (`ollama rm`, model
rebuild) are live-state changes, not files. `git status` matches the
first report's inventory otherwise; no unexpected edits, no new
untracked files beyond the already-known set.

## Outstanding (carried over, NOT part of the 5 fixes)

The first pass's items 4-6 remain open by design: operator confirmation
of the governor-policy softening, disposition of
`docs/DRIFT_GAPS_REPORT_2026-08-15.md`, and the optional polish list
(stale comments incl. the desk-memo one above, the three Modelfiles'
"identical across all 21" comment). None block the fixes audited here.
