#!/usr/bin/env bash
# brief-model-matrix.sh — COMPARISON ONLY (promotes nothing). 2026-08-08.
# Model × prompt-architecture matrix for brief generation:
#   models: gemma3:4b (control), qwen2.5:1.5b, qwen2.5:3b, phi3:mini (Phi-3-mini 3.8B)
#   archs:  baked  = system persona baked into a Modelfile (current approach)
#           runtime= base model + persona supplied as the API `system` field at call time
# Prompt: ~5100 tokens = 125% of the real 4082-token max brief prompt (worst case,
#   same size the permanent smoke-gate uses). Response: num_predict 500.
# Measures per cell: prefill/decode tok/s, total wall time, PASS(<=240s)/FAIL, and a
#   ~350-char output sample for quality eyeballing. PACED: waits for load<6 between
#   cells so the test doesn't cause the contention it's measuring. Temp models are
#   removed after use; live corporatetraveldc-pi5-* models are never touched.
set -o pipefail
OLLAMA_HOST="${OLLAMA_HOST:-100.x.x.x:11434}"; export OLLAMA_HOST
URL="http://$OLLAMA_HOST"
OUT="${1:-${TMPDIR:-/tmp}/brief-matrix-results.md}"
BUDGET=240; NUM_PREDICT=500
# 2026-08-08: clean-license set (the operator prefers truly-open). ALL Ollama tags below
# VERIFIED to exist on the registry 2026-08-08. qwen2.5:3b DROPPED (Qwen-Research
# license, restricted).
#   ~1.5B class: qwen2.5:1.5b (Apache2.0), smollm2:1.7b (Apache2.0).
#   ~3B class:   phi3:mini 3.8B (MIT) -- 4k ctx, so it TRUNCATES the 5100-tok prompt;
#                use phi3:mini-128k for a true worst-case test.
#                granite3.3:2b (Apache2.0, IBM) -- 128K ctx, handles the full prompt;
#                the clean cross-family answer to the restricted qwen-3b.
#   control:     gemma3:4b (restricted; baseline only).
# NOTE: OLMo-2-1B is NOT on the Ollama registry (only olmo2:7b/13b). For the
#   "maximally open" OLMo at ~1B, import its HuggingFace GGUF manually (extra step);
#   olmo2:7b is a fully-open QUALITY reference but won't meet the Pi timeout.
MODELS=(gemma3:4b qwen2.5:1.5b smollm2:1.7b phi3:mini phi3:mini-128k granite3.3:2b)
PERSONA="You are the dispatch intelligence officer for a corporate executive chauffeur operation based in the Washington DC metro area. Generate a concise operational briefing from the raw data below. Focus on what directly affects executive ground transport: airport delays, TFRs that indicate VIP movements, adverse weather, Amtrak disruptions on the NEC. Be factual. Use aviation/dispatch shorthand where appropriate. End with a one-sentence BOTTOM LINE suitable for an ntfy push notification."

gen_prompt() {
  echo "Raw operational data for the executive ground-transport briefing:"
  for i in $(seq 1 170); do
    printf 'FEED %02d METAR KDCA %02d00Z wind 180@12G20 3SM BR OVC008; TFR 9/%d VIP MOVEMENT KDCA %02d00-%02d00Z; Amtrak NEC #%d delayed 25min catenary Baltimore; ADS-B N%dXX sqk1200 alt3500 hdg090 twd IAD; CPS ceiling marginal vis ok wind marginal precip ok airspace restricted gdp active.\n' \
      "$i" "$((i%24))" "$((i+900))" "$((i%20))" "$(((i%20)+2))" "$((i+2100))" "$((i*7))"
  done
}

log(){ echo "[$(date +%H:%M:%S)] $*"; }
wait_for_low_load() {
  for _ in $(seq 1 45); do
    local l; l=$(cut -d' ' -f1 /proc/loadavg)
    if awk "BEGIN{exit !($l < 6)}"; then return 0; fi
    log "load $l >=6, pacing 20s before next cell..."; sleep 20
  done
}

ensure_model() {  # pull if missing (network; low CPU)
  ollama list 2>/dev/null | grep -q "$1" && return 0
  log "pulling $1 ..."; ollama pull "$1" >/dev/null 2>&1
}

run_cell() {  # $1=model $2=arch
  local model="$1" arch="$2" tag="" body prompt resp rc start end
  prompt="$(gen_prompt)"
  if [ "$arch" = baked ]; then
    tag="matrix-$(echo "$model" | tr ':./' '---')-baked"
    local mf; mf="$(mktemp)"
    printf 'FROM %s\nSYSTEM """%s"""\n' "$model" "$PERSONA" > "$mf"
    ollama create "$tag" -f "$mf" >/dev/null 2>&1; rm -f "$mf"
    body="$(python3 -c 'import json,sys;print(json.dumps({"model":sys.argv[1],"prompt":sys.stdin.read(),"stream":False,"options":{"num_predict":int(sys.argv[2])}}))' "$tag" "$NUM_PREDICT" <<<"$prompt")"
  else
    body="$(python3 -c 'import json,sys;print(json.dumps({"model":sys.argv[1],"system":sys.argv[3],"prompt":sys.stdin.read(),"stream":False,"options":{"num_predict":int(sys.argv[2])}}))' "$model" "$NUM_PREDICT" "$PERSONA" <<<"$prompt")"
  fi
  start=$(date +%s)
  resp="$(curl -s -m $((BUDGET + 30)) "$URL/api/generate" -d "$body" 2>/dev/null)"; rc=$?
  end=$(date +%s)
  [ -n "$tag" ] && ollama rm "$tag" >/dev/null 2>&1
  local tmpresp; tmpresp="$(mktemp)"; printf '%s' "$resp" > "$tmpresp"
  python3 - "$model" "$arch" "$rc" "$((end - start))" "$BUDGET" "$OUT" "$tmpresp" <<'PY'
import json,sys,os
model,arch,rc,elapsed,budget,out,respfile=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]),sys.argv[6],sys.argv[7]
raw=open(respfile).read(); os.remove(respfile)
def s(ns):return (ns or 0)/1e9
pf=dc=tot=0.0; pe=ec=0; text=""
try:
    d=json.loads(raw); g=lambda k:(d.get(k) or 0)
    pe=g("prompt_eval_count"); ec=g("eval_count")
    pf=(pe/s(g("prompt_eval_duration"))) if g("prompt_eval_duration") else 0
    dc=(ec/s(g("eval_duration"))) if g("eval_duration") else 0
    tot=s(g("total_duration")); text=(d.get("response") or "").strip().replace("\n"," ")
except Exception: pass
if rc==28 or (rc!=0 and not text):
    verdict=f"⛔ TIMEOUT/FAIL (>{elapsed}s, budget {budget}s)"
else:
    verdict=("✅ PASS" if elapsed<=budget else "⛔ OVER")+f" ({elapsed}s wall)"
row=f"| {model} | {arch} | {pf:.1f} | {dc:.1f} | {pe} | {tot:.0f}s | {verdict} | {text[:300].replace('|','/')} |\n"
open(out,"a").write(row)
print(f"  {model}/{arch}: {verdict}  prefill {pf:.1f} tok/s decode {dc:.1f} tok/s")
PY
}

: > "$OUT"
{
  echo "# Brief model × prompt-architecture matrix — $(date '+%Y-%m-%d %H:%M %Z')"
  echo "Worst-case prompt: ~5100 tokens (125% of the 4082 real max). Response: num_predict $NUM_PREDICT. Budget: ${BUDGET}s. COMPARISON ONLY — nothing promoted."
  echo ""
  echo "| model | arch | prefill tok/s | decode tok/s | prompt toks | total | verdict | output sample |"
  echo "|---|---|---|---|---|---|---|---|"
} >> "$OUT"

for m in "${MODELS[@]}"; do
  ensure_model "$m"
  for a in baked runtime; do
    wait_for_low_load
    log "cell: $m / $a"
    run_cell "$m" "$a"
    sleep 30   # cooldown between cells
  done
done
log "matrix complete -> $OUT"
