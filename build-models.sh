#!/usr/bin/env bash
# build-models.sh — Build corporatetraveldc custom Ollama models from Modelfiles.
# Run as corporatetraveldc (not root).
#
# 2026-08-02: each production skill gets its own dedicated model
# (corporatetraveldc-pi5-<task>) with its persona baked into the Modelfile SYSTEM.
#
# 2026-08-08: HARD REGRESSION GUARD added for BRIEF-class models after the
# all-fallback incident. Root cause then: the brief models were FROM gemma3:4b,
# whose Sliding Window Attention (SWA) defeats llama.cpp KV-cache reuse — every
# ~2000-token brief prompt was re-processed from scratch (~4 min at ~7.7 tok/s on
# the Pi 5), blowing the 240s runtime OLLAMA_TIMEOUT, so briefs fell back to the
# deterministic template on ~100% of runs, silently, for a long time. Two gates
# below make that failure class structurally unshippable:
#   GUARD 1 (build-time): a brief model may NOT be built on a known cache-breaking
#            SWA base (gemma2/gemma3). Fails loudly; explicit override required.
#   GUARD 2 (promotion):  a brief model is built to a :candidate tag and must PROVE
#            it can generate a real response to a brief-sized prompt within the
#            smoke budget before it is promoted to the live tag. A too-slow/cache-
#            breaking model can never go live; the last-known-good keeps serving.
#
# Usage:
#   build-models.sh                 # build all models
#   build-models.sh <name> [name..] # build only the named model(s)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Signed-manifest integrity self-check (docs/COMPLIANCE_SECURITY.md "Signed
# Manifest Integrity") -- collective, whole-tree: this script builds models
# from Modelfiles it does its own per-Modelfile check on (GUARD 0 below),
# but that doesn't cover build-models.sh's own integrity, or anything else
# it might read. Run once, up front, before touching anything.
if ! "${REPO_DIR}/scripts/verify-manifest.sh"; then
    echo "XX INTEGRITY CHECK FAILED -- refusing to run build-models.sh" >&2
    exit 5
fi

OLLAMA_HOST="${OLLAMA_HOST:-100.x.x.x:11434}"; export OLLAMA_HOST
OLLAMA_URL="http://${OLLAMA_HOST}"

# name -> Modelfile suffix
declare -A MODELS=(
  [corporatetraveldc-pi5-chat]="chat"
  [corporatetraveldc-pi5-osint]="osint"
  [corporatetraveldc-pi5-ops-brief]="ops-brief"
  [corporatetraveldc-pi5-ops-brief-trend]="ops-brief-trend"
  [corporatetraveldc-pi5-tfr-enrichment]="tfr-enrichment"
  [corporatetraveldc-pi5-route-impact]="route-impact"
  [corporatetraveldc-pi5-weekly-summary]="weekly-summary"
  [corporatetraveldc-pi5-osint-monitor]="osint-monitor"
  [corporatetraveldc-pi5-ep-advance]="ep-advance"
  [corporatetraveldc-pi5-ep-advance-trend]="ep-advance-trend"
  [corporatetraveldc-pi5-aam-watch]="aam-watch"
  [corporatetraveldc-pi5-dispatch-desk]="dispatch-desk"
  [corporatetraveldc-pi5-transport-digest]="transport-digest"
  [corporatetraveldc-pi5-disruption-weather-digest]="disruption-weather-digest"
  [corporatetraveldc-pi5-secondbrain-daily]="secondbrain-daily"
  [corporatetraveldc-pi5-secondbrain-weekly]="secondbrain-weekly"
)

# ── Brief-class models: subject to the two guards above ───────────────────────
BRIEF_MODELS=(
  corporatetraveldc-pi5-ops-brief
  corporatetraveldc-pi5-ops-brief-trend
  corporatetraveldc-pi5-ep-advance
  corporatetraveldc-pi5-ep-advance-trend
)
# Known cache-breaking base families (Sliding Window Attention / hybrid-recurrent
# → llama.cpp forces full prompt re-processing → briefs blow the timeout).
SWA_DENYLIST_REGEX='^FROM[[:space:]]+(gemma3|gemma2)([:._-]|[[:space:]]|$)'
# Smoke budget MUST beat the runtime OLLAMA_TIMEOUT (240s) with margin.
SMOKE_BUDGET_S="${BRIEF_SMOKE_BUDGET_S:-200}"

is_brief_model() { local n="$1" b; for b in "${BRIEF_MODELS[@]}"; do [ "$b" = "$n" ] && return 0; done; return 1; }

assert_brief_base_ok() {  # GUARD 1
  local modelfile="$1" name="$2"
  if grep -qiE "$SWA_DENYLIST_REGEX" "$modelfile"; then
    local fromline; fromline="$(grep -m1 -iE '^FROM' "$modelfile")"
    if [ "${BRIEF_BASE_OVERRIDE:-0}" = "1" ]; then
      echo "  !! WARNING: brief model ${name} uses a DENYLISTED SWA base (${fromline}). BRIEF_BASE_OVERRIDE=1 — proceeding against the guard."
    else
      echo "  XX BLOCKED: brief model ${name} base is a known cache-breaking SWA family:"
      echo "     ${fromline}"
      echo "     Brief models must use a standard-attention base (e.g. qwen2.5:3b, llama3.2:3b)."
      echo "     This is the 2026-08-08 all-fallback root cause. Set BRIEF_BASE_OVERRIDE=1 to force (NOT recommended)."
      exit 3
    fi
  fi
}

brief_smoke_prompt() {  # ~5100-token block = 125% of the real 4082-token max prompt observed
                        # across the pipeline (Ollama logs, 2026-08-08) — the gate must prove a
                        # model handles the WORST case + margin, not a typical ~2000-token prompt.
  echo "Raw operational data for the executive ground-transport briefing:"
  for i in $(seq 1 170); do
    printf 'FEED %02d | METAR KDCA %02d00Z wind 180@12G20 3SM BR OVC008; TFR 9/%d VIP MOVEMENT KDCA %02d00-%02d00Z; Amtrak NEC #%d delayed 25min catenary Baltimore; ADS-B N%dXX sqk1200 alt3500 hdg090 twd IAD; CPS ceiling marginal vis ok wind marginal precip ok airspace restricted gdp active.\n' \
      "$i" "$((i%24))" "$((i+900))" "$((i%20))" "$(((i%20)+2))" "$((i+2100))" "$((i*7))"
  done
}

smoke_test_brief_model() {  # GUARD 2 — real generation within budget, non-empty response
  local candidate="$1" prompt body resp text start end elapsed rc
  prompt="$(brief_smoke_prompt)"
  body="$(python3 -c 'import json,sys;print(json.dumps({"model":sys.argv[1],"prompt":sys.stdin.read(),"stream":False,"options":{"num_predict":200,"temperature":0.2}}))' "$candidate" <<<"$prompt")"
  start=$(date +%s)
  resp="$(curl -s -m "$SMOKE_BUDGET_S" "$OLLAMA_URL/api/generate" -d "$body" 2>/dev/null)"; rc=$?
  end=$(date +%s); elapsed=$((end - start))
  if [ $rc -ne 0 ]; then
    echo "  SMOKE FAIL: ${candidate} did not respond within ${SMOKE_BUDGET_S}s (curl rc=${rc}, ${elapsed}s). Likely a slow/cache-breaking base."
    return 1
  fi
  text="$(printf '%s' "$resp" | python3 -c 'import json,sys
try: print((json.load(sys.stdin).get("response") or "").strip())
except Exception: print("")')"
  if [ -z "$text" ]; then
    echo "  SMOKE FAIL: ${candidate} returned an empty response (${elapsed}s)."
    return 1
  fi
  echo "  SMOKE PASS: ${candidate} produced ${#text} chars in ${elapsed}s (budget ${SMOKE_BUDGET_S}s, runtime timeout 240s)."
  return 0
}

assert_modelfile_integrity() {  # GUARD 0 -- applies to every model, not just brief-class
  local suffix="$1" name="$2"
  local relpath="corporatetraveldc.${suffix}"
  if ! "${REPO_DIR}/scripts/verify-manifest.sh" "${relpath}"; then
    echo "  XX BLOCKED: ${name}'s Modelfile (${relpath}) failed the signed-manifest"
    echo "     integrity check -- refusing to build a model from a Modelfile that"
    echo "     doesn't match its signed hash. Run scripts/sign-manifest.sh after"
    echo "     reviewing/approving the change, or investigate why it doesn't match."
    exit 5
  fi
}

build_one() {
  local name="$1" suffix="$2"
  local modelfile="${REPO_DIR}/corporatetraveldc.${suffix}"
  [ -f "$modelfile" ] || { echo "XX missing Modelfile: ${modelfile}"; exit 2; }
  assert_modelfile_integrity "$suffix" "$name"
  if is_brief_model "$name"; then
    echo "=== BRIEF model ${name} (guarded build) ==="
    assert_brief_base_ok "$modelfile" "$name"
    echo "  building ${name}:candidate ..."
    ollama create "${name}:candidate" -f "$modelfile"
    if smoke_test_brief_model "${name}:candidate"; then
      ollama cp "${name}:candidate" "${name}:latest"
      ollama rm "${name}:candidate" >/dev/null 2>&1 || true
      echo "  ✅ PROMOTED ${name} -> :latest"
    else
      ollama rm "${name}:candidate" >/dev/null 2>&1 || true
      echo "  ⛔ NOT PROMOTED — ${name}:latest left as last-known-good. Fix the base and rebuild."
      FAILED_BRIEF=1
    fi
  else
    echo "=== Building ${name} (${suffix}) ==="
    ollama create "${name}" -f "$modelfile"
  fi
  echo ""
}

# Resolve which models to build (all, or the names passed as args)
declare -a TO_BUILD=()
if [ "$#" -gt 0 ]; then
  for arg in "$@"; do
    if [ -n "${MODELS[$arg]:-}" ]; then TO_BUILD+=("$arg")
    else echo "XX unknown model: ${arg} (valid: ${!MODELS[*]})"; exit 2; fi
  done
else
  TO_BUILD=("${!MODELS[@]}")
fi

FAILED_BRIEF=0
for name in "${TO_BUILD[@]}"; do build_one "$name" "${MODELS[$name]}"; done

echo "=== Verifying models ==="
ollama list | grep -E "corporatetraveldc|gemma|qwen|llama" || true

if [ "$FAILED_BRIEF" = "1" ]; then
  echo ""
  echo "⛔ One or more BRIEF models FAILED the smoke-test gate and were NOT promoted."
  echo "   The live brief models are unchanged (last-known-good). Investigate before shipping."
  exit 4
fi

echo ""
echo "Done. (Skills reference models by name; a base swap needs NO poller rebuild.)"
