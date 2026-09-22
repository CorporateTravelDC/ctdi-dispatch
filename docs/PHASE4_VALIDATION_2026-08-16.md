# Phase 4 Validation — 2026-08-16 (independent cold-start audit)

Adversarial re-verification of the 2026-08-15 Phase-4 claim: "21 Modelfiles
rebuilt, 21 Python timeouts measured and wired, 17 systemd TimeoutStartSec
reconciled — fully complete, awaiting manifest re-sign." Read-only; nothing
was changed, staged, or committed. All paths relative to
`/opt/corporatetraveldc/private/ctdi-dispatch-internal`.

## Verdict: NOT ready to sign as-is — one real functional bug + one scrub gap

The bulk of the claim is genuine and holds up under checking: all 21
Modelfiles exist and are well-built, all 21 timeout derivations are
arithmetically sound, all 17 systemd budgets cover their worst cases, all
touched code compiles, all 21 models exist live, and the box is idle-safe.
But two findings should be fixed before the operator signs:

1. **BUG (functional): `src/poller/skills/osint_monitor.py:42-44` still
   resolves its model to the deleted `corporatetraveldc-pi5-brief:latest`.**
   The chain is `OLLAMA_OSINT_NARRATOR_MODEL` → `OLLAMA_MODEL` → that
   default; neither env var is set in `/etc/corporatetraveldc/dispatch.env`
   (only `OLLAMA_OSINT_MODEL` and `OLLAMA_CHAT_MODEL` are — a different
   var name). Every OSINT narrative call will 404 against Ollama and fall
   back to deterministic output, silently. The skill's own new timeout
   comment (timeout=300, measured against the persona models) and the
   `corporatetraveldc.osint-monitor` Modelfile's SYSTEM ("This model serves
   the osint-monitor skill") show the intent was to repin it — every other
   skill's `OLLAMA_MODEL` line was updated; this one was missed. Fix: pin
   to `corporatetraveldc-pi5-osint-monitor:latest` (as
   `src/common/entity_tracking.py:124` already does).

2. **GAP (public-mirror leak vector): `scripts/scrub-public-tree.py`
   DROP_FILES was not reconciled with the new Modelfiles.** It still lists
   the now-deleted `corporatetraveldc.dispatch-persona` (line 59) and drops
   only `corporatetraveldc.chat` of the 21 new files. The other 20 —
   including `corporatetraveldc.ep-advance`, which carries vetted
   hotel/venue names, protest-zone methodology, and EP procedural detail
   of exactly the class the old persona file was dropped for — would
   transit to the public mirror on the next push-public.sh run.
   SUBSTITUTIONS/FORBIDDEN_LITERALS would rewrite "the operator" but
   nothing blocks the venue/EP content. The deleted public `.template`
   counterparts (chat/osint/dispatch-persona) also now have no replacement.
   Operator decision needed: add the 20 to DROP_FILES (or explicitly
   accept them as public-safe) before the next mirror push.

Everything else below is confirmatory detail or minor.

## 1. The 21 Modelfiles — PASS (with one claim-accuracy caveat)

All 21 present at repo root; `FROM phi3:mini` in every one (line 13);
identical `num_thread 2` / `top_p 0.9` everywhere; per-skill num_ctx
(4096, 6144 for the three big-prompt skills: dispatch-desk-memo,
ep-advance, secondbrain-weekly), num_predict matching each call site's
max_tokens exactly (verified all 21 pairs), temperatures 0.15-0.4 scaled
sensibly to task determinism.

Shared persona: the 28-line core (identity + rules block, lines 22-48) is
**byte-identical across all 21** (md5 `0bd7b7c3...` on all 21). Caveat:
three files — dispatch-desk-memo, disruption-weather-digest,
transport-digest — append a 4-line "Exception for this skill only:
markdown output" paragraph (their line 50-52), so the full persona block is
NOT byte-identical across all 21 as claimed. The deviation is deliberate,
self-documenting, and correct — all three genuinely instruct markdown
output (desk-memo line 91, disruption line 63, transport line 60) which
would otherwise contradict the shared "Plain text only" rule — but the
"verbatim, identical across all 21" comment each Modelfile carries (line
8-9) is technically wrong for those three.

Skill layers: all 21 unique (21 distinct hashes), 12-59 lines each,
genuinely differentiated — spot-diffed the closest pairs
(aam-daily vs aam-weekly, ep-advance-trend vs ops-brief-trend): real
analytical differences (windows, framing, thresholds), not name-swaps.
Read all 21 layers; each matches its skill's actual data shape and output
contract. `corporatetraveldc.osint-monitor` explicitly covers both the
2-sentence narrative shape AND entity_tracking's structured-extraction
calls — consistent with the repin decision recorded in
entity_tracking.py (which makes finding #1 above unambiguous).

## 2. Python timeout wiring — PASS (arithmetic verified for all 21)

Zero `TIMEOUT-TBD` markers anywhere in `src/` (only unrelated
"TBD" string literal at train_impact.py:95). All 21 call sites carry a
2026-08-15 spike-measured derivation comment; I recomputed every one:
`total = eval_s + cap/tok_s; delta = total - persona_ref; scaled = delta x
(53/ref) [skipped when ref >= 53]; final = (53 + scaled) x 1.25, rounded
up`. All 21 reproduce to within rounding of the stated intermediates:

| call site | timeout | check |
|---|---|---|
| aam_daily_watch.py:141 | 1890 | (53+1457.8)x1.25=1888.5 ✓ |
| aam_weekly_watch.py:275 | 2250 | 2249.6 ✓ |
| aviation_daily_watch.py:156 | 1380 | calc 1355, rounded to match sibling 1380s ✓ |
| concierge_travel:142 | 1470 | 1460.1 ✓ |
| dispatch_desk_memo.py:223 | 4770 | 4749.75 ✓ |
| disruption_weather:154 | 810 | 789.4 ✓ |
| ep_advance main (const:70) | 2220 | 2204.4 ✓ |
| ep_advance trend (const:71) | 540 | 536.6 ✓ |
| exec_protection:145 | 1560 | 1552.4 ✓ |
| gig_economy:151 | 1380 | 1355.75 ✓ |
| ops_brief main (const:74) | 1200 | 1181.0 ✓ |
| ops_brief trend (const:75) | 510 | 508.9 ✓ |
| osint_monitor:428 | 300 | 292.4 ✓ (but see model bug, finding #1) |
| route_impact.py:96 | 480 | 472.0 ✓ (incl. +16s cold-load, hot path) |
| second_brain_daily:235 | 870 | 848.1 ✓ |
| second_brain_weekly:150 | 2340 | 2322.1 ✓ |
| tfr_enrichment.py:132 | 540 | 526.25 ✓ |
| trains_yachts:142 | 1380 | 1351.4 ✓ |
| transport_pattern:197 | 600 | 576.75 ✓ |
| weekly_summary.py:153 | 990 | 963.25 ✓ |
| entity_tracking extraction (:141) | 1740 | 1725.9 ✓; domain guess (:142) 150 | 140.25 ✓ |
| runner chat (main.py:1290) | read=110 | deliberate fail-fast under nginx proxy_read_timeout=120s (both conf files verified at 120s) ✓ |

(That's 21 dedicated-model wirings: 20 skill calls + chat; entity_tracking's
two shapes ride the osint-monitor model.)

Cosmetic inconsistencies only:
- tfr_enrichment comment states 0.79 tok/s but 266.2s at 220 tokens implies
  0.826 tok/s — ~12s discrepancy, absorbed by the 1.25 margin.
- aam_daily_watch.py:146-148 retry comment still says "3x240=720s still
  fits TimeoutStartSec=950" — stale numbers from the pre-reset era (real:
  3x1890 vs 8600, which still fits, so harmless but should be updated).

One marginal real issue: **dispatch-desk-memo worst case slightly exceeds
its context window** — measured 5092-token prompt + 1100-token cap = 6192
vs num_ctx 6144 (over by 48). The 2200-"token" trim at
dispatch_desk_memo.py:212 uses the 4-chars/token heuristic, which the
measurement shows underestimates real tokens ~2.3x for this content.
Worst-case effect is tail truncation/context-shift on the largest weeks,
not a hang. Worth a num_ctx bump to 8192 or a tighter trim eventually.

## 3. Systemd TimeoutStartSec reconciliation — PASS

Exactly 17 `.container` units modified, matching the 17 skills that run as
oneshot containers. Every budget comment recomputed and every assumption
checked against code:

- Formula: 800s fixed overhead + (attempts x per-call timeout) [+ 1740s
  extraction + 2x150s domain guesses where the skill uses entity
  tracking], rounded up. All 17 sums are arithmetically correct.
- Attempt counts verified against code: max_retries=2 → 3 attempts
  (6 daily watches + aam-weekly), max_retries=0 → 1 (ops-brief x2,
  ep-advance x2, disruption, transport), default → 2 (desk-memo,
  sb-daily, sb-weekly, weekly-summary; default confirmed =
  OLLAMA_MAX_RETRIES env-default "1" at llm.py:295, not overridden in
  dispatch.env).
- Entity-extraction inclusion verified: exactly the 6 daily watches import
  entity_tracking; aam-weekly correctly excludes it;
  personal-export-watch's inclusion is legitimate via
  common/export_analysis.py:215 → entity_tracking.extract_entities().
- Every TimeoutStartSec ≥ its computed worst case: margins 20-90s
  (rounding-up style) except **transport-pattern-digest = 1400 exactly
  equal to its 800+600 worst case, zero rounding margin** — the only unit
  with no headroom over its own stated budget; inconsistent with the
  others, worth a bump to 1500.
- Non-container LLM paths: poller in-process `_OLLAMA_SKILL_TIMEOUT=2000`
  (poller/main.py:71) covers tfr-enrichment (540) and route-impact (480)
  worst cases with room, and the manual-trigger path (line 309) now reuses
  the constant; osint-monitor's documented can-exceed case is an accepted,
  self-healing loss of one sweep. Chat: 110s < nginx 120s ✓.
- No unreconciled LLM unit exists: the 18 files importing common.llm map
  exactly onto the 17 containers + the 3 poller-scheduled skills.

The ep-advance class of failure (Python timeout > systemd cap) is closed
everywhere I could construct it.

## 4. Code health — PASS with stale-comment debris

- `python3 -m py_compile` clean on all 22 touched .py files.
- No orphaned imports/constants found in the touched files: the stopgap
  `OLLAMA_TIMEOUT` in osint_monitor was removed; runner's dead
  `OLLAMA_OSINT_MODEL` constant was removed with no remaining references;
  llm.py's persona loader/injector/verify were removed cleanly and
  `trim_to_token_budget`/`sanitize_prompt_text` remain used.
- Stale references to retired model names (all comments/labels, no
  execution impact except finding #1): ops_brief.py:707 and :4,
  weekly_summary.py:4, ep_advance_brief.py:4, train_impact.py:4,
  flight_impact.py:4, llm.py:37 (docstring example
  "corporatetraveldc-pi5-osint:latest"). train_impact/flight_impact/
  freshness_audit still default their log-label MODEL chain to
  "corporatetraveldc-pi5-brief:latest" (lines 34/36/28) — they perform no
  inference and in-container the env's OLLAMA_OSINT_MODEL resolves first,
  so this is cosmetic, but the defaults should be cleaned.
- Design note, not a bug: the chat path (`/api/ask`) always sends its own
  system message (runner/main.py:1367-1383 → _llm_stream payload), which
  overrides the pi5-chat Modelfile's baked SYSTEM in Ollama's chat API.
  The baked persona applies only to direct Ollama/OpenWebUI use. The
  110s measurement used the runner prompt, so the timeout is still valid.

## 5. Scope check — three items beyond the stated scope, flag to operator

In-scope and clean: 22 .py files, 17 .container files, build-models.sh
(21-model map matches disk 1:1), dispatch.env model-var comments, staged
deletions of the 6 old model/persona/template files.

Outside "Modelfiles + timeouts + systemd", all uncommitted in the same
tree:
1. `scripts/ollama-wedged-detector.sh` — full redesign: 65/80/110/120s
   escalation ladder, ntfy alerting, TIER1/TIER2 feed-shedding, and an
   approval-gated SIGKILL stage. Derived from the same 53s baseline;
   coherent, but a new automated-mitigation surface.
2. `scripts/sudo-approval-gate.sh` — auto-promotes any `kill`/
   `ollama-governor`/DR-flagged request to ntfy priority 5.
3. `docs/SUDO_JUSTIFICATION_PROPOSAL.md` — **softens the
   ollama-governor "never touch under any circumstance" rule** to
   "never silently/automatically; human Allow tap required." The doc
   attributes this to a 2026-08-15 operator decision — the operator should
   confirm that attribution before signing, since this edits the agent's
   own guardrail policy.
4. Leftover untracked file from a prior audit session:
   `docs/DRIFT_GAPS_REPORT_2026-08-15.md` (would be swept into the next
   commit/manifest sign if not handled deliberately).

## 6. Live system state — PASS with one orphan

- `ollama list`: all 21 `corporatetraveldc-pi5-*` models present, names
  matching build-models.sh's map 1:1; spot-verified live SYSTEM + params
  byte-match the on-disk Modelfiles for ops-brief and ep-advance;
  `phi3:mini` base present. **One orphan: `corporatetraveldc-dispatcher-
  baseline-test:latest` (2.2GB, 10h old)** — leftover from the baseline
  measurement session, no Modelfile on disk, referenced nowhere; remove
  or document before deploy.
- `ollama ps`: empty — nothing loaded/running.
- No synthetic load processes (no stress/dd/openssl burners in ps); load
  avg ~5 is the normal ingest working set (SWIM ingest.main containers +
  readsb/rbfeeder), CPU temp 58.4C, ollama-governor.service active.
- `corporatetraveldc-thermal-ingest-guard.timer` **active/waiting** (user
  scope) — the guard paused for spike testing was properly resumed; the
  thermal-sample and sdr-crashloop timers are also active.

## 7. Manifest state — fails as expected, plus a pre-existing failure

`scripts/verify-manifest.sh`: **INTEGRITY FAILURE — 48 checksum
mismatches + 5 unreadable (deleted) files.** Breakdown:
- The 5 unreadable are the staged deletions (corporatetraveldc.brief,
  .chat.template, .dispatch-persona, .dispatch-persona.template,
  .osint.template) — expected.
- Most mismatches are tonight's uncommitted Phase-4 working-tree changes —
  expected pending the human re-sign.
- BUT the set also includes files untouched tonight (build-images.sh,
  dispatch-secrets.env.template, docs/INFRA_MAP.md, src/ctdc_token/cli.py):
  HEAD itself (be958ac and neighbors, the 4 unpushed commits) was
  committed without re-signing — the pre-existing failure independently
  documented as D1 in docs/DRIFT_GAPS_REPORT_2026-08-15.md. So the
  integrity sweep has been failing since before Phase 4 began, and the
  coming re-sign must cover both the Phase-4 changes and that backlog.
- Verified the re-sign will capture the new Modelfiles despite being
  untracked: sign-manifest.sh hashes `git ls-files --cached --others
  --exclude-standard` (tracked + untracked non-ignored), and
  llm.py `_verify_integrity` fails closed on any model whose
  `corporatetraveldc.<suffix>` file is absent from the manifest — so an
  incomplete sign would surface immediately at first inference, not
  silently.

## Fix list before sign/deploy (in order)

1. osint_monitor.py:44 — repin default to
   `corporatetraveldc-pi5-osint-monitor:latest`.
2. scrub-public-tree.py — decide DROP_FILES treatment for the 20 new
   Modelfiles (ep-advance at minimum); remove the stale dispatch-persona
   entry.
3. `ollama rm corporatetraveldc-dispatcher-baseline-test` (or document
   keeping it).
4. Operator confirms the ollama-governor policy softening + the
   wedged-detector/approval-gate behavior changes were actually his calls.
5. Decide fate of docs/DRIFT_GAPS_REPORT_2026-08-15.md before commit.
6. Optional polish: transport-digest TimeoutStartSec 1400→1500;
   desk-memo num_ctx headroom; stale-comment sweep (section 4 list);
   fix the three Modelfiles' "identical across all 21" comment to mention
   the markdown exception.
