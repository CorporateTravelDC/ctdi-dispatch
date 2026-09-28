# Live State Check — 2026-08-14

Written ~01:25 EDT, immediately after `9436f67` landed as HEAD (01:17 EDT —
model consolidation to one shared brief model + central persona, OOOI
landing-push gap fix, local-first hex resolution, CF Access service-token
tooling, +4 EMS RSS feeds). Same rules as the 08-12/08-13 checks: does THIS
commit invalidate anything the current docs claim? Verified against the live
system, not prior docs. Working tree was clean at check time; nothing here
was staged or committed.

## Live snapshot verified

- `GET /healthz` → `{"status":"ok", … "cps":{"score":"GREEN","label":"GO"}}`.
- 140 `corporatetraveldc-*` user units loaded, 23 containers up (CLAUDE.md
  correctly says don't hardcode these counts).
- web :8000 and runner :8001 listening on 127.0.0.1 + 100.x.x.x as
  documented. Ollama listens on **100.x.x.x:11434 only** (matches
  `OLLAMA_BASE_URL` in dispatch.env; not on localhost).
- `scripts/verify-manifest.sh` passes clean against post-commit HEAD.
- build-models.sh `MODELS` map = `{chat, brief}`; live Ollama serves
  `corporatetraveldc-pi5-brief:latest` + `corporatetraveldc-pi5-chat:latest`;
  code references only `-brief` (29 call sites), `-chat` (3), `-osint` (3 —
  see observations). Persona injection (`corporatetraveldc.dispatch-persona`,
  loaded/verified in `common/llm.py`) confirmed in source.

## Drift found

### 1. The 16-dedicated-model narrative survived the commit that killed it

The consolidation shipped in this commit, but every doc description of the
old world was carried into HEAD unchanged. All of the following still claim
16 per-task models, a 4-model phi3 brief class with everything else on
gemma3:4b, and a 200 s promotion smoke test:

- **`CLAUDE.md:147–155`** (Local LLM bullet) — "16 dedicated models …
  4 brief-class models … FROM phi3:mini; everything else is gemma3:4b …
  200 s smoke test".
- **`README.md:54`** (stack-table row) and **`README.md:~488–512`**
  ("Dedicated per-task models (since 2026-08-02)" section, including the
  16-model base table).
- **`docs/DEDICATED_MODELS_PLAN.md`** — the "Status update — 2026-08-11"
  addendum was *added by this very commit* already stale (written
  pre-consolidation; claims 16 models / 24 dedicated-model call sites /
  "all other Modelfiles remain gemma3:4b").
- **`docs/lmstudio-dispatch-prompts.md:8–9`** — same per-task-model framing.

Ground truth after `9436f67`: **2 models** (`corporatetraveldc-pi5-brief`
shared by every batch/report skill, `corporatetraveldc-pi5-chat` for the
interactive path); persona/ROE lives centrally in
`corporatetraveldc.dispatch-persona` and is injected by `common/llm.py` on
every call; Modelfiles carry only `FROM` + `PARAMETER`; 14 per-task
Modelfiles were deleted. The SWA denylist guard survives, but the brief
smoke budget is now **900 s default** (`BRIEF_SMOKE_BUDGET_S`,
build-models.sh) with runtime load-gating moved to `OLLAMA_LOAD_TIMEOUT`
(180 s) plus an adaptive 48 h load-time baseline in llm.py. This exact drift
was called out in LIVE_STATE_CHECK_2026-08-13 items 1–2 and was not fixed
before the commit landed.

### 2. CLAUDE.md's ANTHROPIC_FALLBACK claim is now inverted

`CLAUDE.md:156–158` asserts `ANTHROPIC_FALLBACK_ENABLED` "defaults true (it
is NOT set to false in dispatch.env, despite an old llm.py docstring
claim)". Live `/etc/corporatetraveldc/dispatch.env:150` now sets
`ANTHROPIC_FALLBACK_ENABLED=false`, and llm.py's current comment says so
("this box's own dispatch.env sets it to 'false'"). The module default is
still true, but the parenthetical is exactly backwards for this deployment.

### 3. OLLAMA_TIMEOUT numbers stale everywhere they appear

`CLAUDE.md:83` (resource-limits warning box) and
`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17,25` still say `OLLAMA_TIMEOUT=240`
"fail-fast". Live value is **1200** (dispatch.env), llm.py default is 900,
and the commit's design intent is load-time-boxed / generation-time-uncapped.
Flagged 08-13, still unfixed. SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17 also cites
a "600s container `TimeoutStartSec`" — the aam-daily-watch unit's live value
is 950 s (15 min 50 s), and see the observation below about unit timeouts
now being shorter than the LLM budget. (llm.py's own generate() docstring
"currently 60s, see dispatch.env" at src/common/llm.py:1073 is a third stale
value, in code rather than docs.)

### 4. README's "ACARS/VDL2 chain, feeders up throughout" is now false

`README.md:55` (ADS-B stack-table row): "All other SDR containers
(ACARS/VDL2 chain, feeders) up throughout." Live: the commit message itself
records ACARS hardware **confirmed dead this session** (the motivation for
the pusher OOOI fix); only `acars-watcher` is running — `acarsrouter`,
`dumpvdl2`, `acarshub` containers are not present in `podman ps -a` at all —
and `fr24feed` + `planefinder` have been in systemd failed state since
2026-08-11 18:48 EDT. `docs/SDR_SERVICES.md:15`'s "Both dongles enumerate
again; live decode confirmed" (08-11) is likewise overtaken for the ACARS
dongle. `docs/DATA_SOURCES.md` (airplanes.live, "Last verified 2025-12, no
credentials required") also deserves a note: api.airplanes.live is currently
403'ing this box entirely (verified live — `/api/v1/adsb` returns the 403
error passthrough with count=0), which is why this commit made tail→hex
resolution local-registry-first.

## Checked, no drift

- **Pusher landing-push fix** — no doc described the old (ACARS+ADS-B-only)
  landing-confirmation internals, so nothing was invalidated;
  `watchlist_README.md`'s OOOI claims ("phases never revert", the 2026-07-28
  ACARS/FDPS/FIDS/VDL authority chain) are exactly what the fix brings
  pusher into line with. `src/ingest/README.md` unaffected.
- **Local-first hex resolution** (`poller/main.py`) — README:301's
  position-source chain is unaffected (airplanes.live is still the only
  live-position source; only tail→hex mapping moved local-first).
  REGIONALIZATION.md:258 and REFERENCE_INFRA.md:103 remain accurate.
- **CF Access service-token tooling** — `docs/INFRA_MAP.md` §6a (added by
  this commit) documents the dual-policy template, both policy IDs, and the
  mint/breakglass/reconcile scripts; matches the scripts shipped.
  HEADLESS_ACCESS.md never mentioned CF Access, so nothing there to drift.
- **rss_catalog.py +4 EMS/aviation feeds** — no doc enumerates that feed
  list (dispatch-runner-design.md just points at the file). No drift.
- Auth tiers, ingest 7-container layout, ntfy topics, watchlist docs —
  untouched by this commit; ports/units spot-checked live and consistent.

## Live observations (not doc drift, but found during verification)

1. **11 orphaned per-task gemma3 models still on the Ollama server**
   (`-route-impact`, `-secondbrain-weekly`, `-osint-monitor`,
   `-dispatch-desk`, `-aam-watch`, `-transport-digest`, `-tfr-enrichment`,
   `-secondbrain-daily`, `-weekly-summary`, `-disruption-weather-digest`,
   `-osint`) alongside the new pair. The 4 old phi3 brief models are gone;
   the gemma3 cleanup flagged "pending" on 08-13 still hasn't happened.
2. **`pi5-osint` is a latent breakage**: `src/runner/main.py:101` still
   defaults `OLLAMA_OSINT_MODEL` to `corporatetraveldc-pi5-osint:latest`
   (llm.py:37 docstring example too), but that model is no longer in
   build-models.sh's map and its Modelfile was deleted — the runner OSINT
   path works only while the orphan model from (1) survives. An
   `ollama rm` cleanup or fresh rebuild silently breaks it. (The
   runner/main.py:95 "wrappers on mistral-nemo" comment was already flagged
   wrong on 08-13.)
3. **Daily watch skills are failing on unit timeout, not LLM timeout**:
   aam/aviation/concierge/gig-economy daily watches, dispatch-desk-memo,
   disruption-weather-digest, and second-brain-weekly are in systemd
   `failed` state from the 08-13 afternoon runs. Journal shows the shape:
   `pi5-brief` generation timed out → deterministic fallback output was
   written successfully → but the unit still hit `TimeoutStartSec` (950 s
   for aam-daily-watch; run consumed 16 min 30 s wall) and was SIGKILLed →
   `Failed with result 'timeout'`. With `OLLAMA_TIMEOUT=1200` (and
   generation now uncapped by design) exceeding the 950 s unit timeout, the
   unit-level kill fires before llm.py's own budget/fallback can finish
   cleanly. Today's timer runs (first ~07:30 EDT) are the first with the
   committed adaptive-baseline/priority-cap code — worth watching whether
   they clear, or whether unit `TimeoutStartSec` values need raising to
   match the new philosophy.
4. **integrity-sweep failures at 00:56/01:11 EDT were the pre-commit edit
   window** (working tree mid-edit / re-sign race), not a real integrity
   problem: manual `verify-manifest.sh` passes clean post-commit, and the
   15-minute timer (next run 01:26) should clear the failed state on its
   own.
