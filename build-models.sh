#!/usr/bin/env bash
# build-models.sh — Build corporatetraveldc custom Ollama models from Modelfiles
# All custom models are based on gemma3:4b (see each corporatetraveldc.<name>
# Modelfile's FROM line) -- run after 'ollama pull gemma3:4b' is complete.
# Run as corporatetraveldc (not root).
#
# Rewritten 2026-08-02: each production skill that calls common.llm.generate()
# now gets its own dedicated model (corporatetraveldc-pi5-<task>) with that
# skill's static persona/instruction text baked into the Modelfile's SYSTEM,
# instead of being re-sent as a runtime "system" string on every call. The
# original two general-purpose models (chat/osint) are kept for interactive
# use (open-webui, ad hoc queries) but are no longer the default for any
# scheduled skill.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

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
  [corporatetraveldc-pi5-secondbrain-daily]="secondbrain-daily"
  [corporatetraveldc-pi5-secondbrain-weekly]="secondbrain-weekly"
)

for name in "${!MODELS[@]}"; do
  suffix="${MODELS[$name]}"
  echo "=== Building ${name} (gemma3:4b + ${suffix} context) ==="
  ollama create "${name}" -f "${REPO_DIR}/corporatetraveldc.${suffix}"
  echo ""
done

echo "=== Verifying models ==="
ollama list | grep -E "corporatetraveldc|gemma"

echo ""
echo "=== Warm-loading all models (1-token probe) ==="
for name in "${!MODELS[@]}"; do
  curl -s http://100.x.x.x:11434/api/generate \
    -d "{\"model\":\"${name}\",\"prompt\":\"ping\",\"stream\":false,\"options\":{\"num_predict\":1}}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('${name}:', 'OK' if d.get('response') is not None else 'FAIL')"
done

echo ""
echo "Done. Rebuild the poller image to pick up the new ollama_model= references:"
echo "  bash build-images.sh --only poller && systemctl --user restart corporatetraveldc-poller"
