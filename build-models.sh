#!/usr/bin/env bash
# build-models.sh — Build corporatetraveldc custom Ollama models from Modelfiles
# Both custom models are based on gemma3:4b (see corporatetraveldc.chat / corporatetraveldc.osint
# FROM line) -- run after 'ollama pull gemma3:4b' is complete.
# Run as corporatetraveldc (not root).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Building corporatetraveldc-pi5-chat (gemma3:4b + operator context) ==="
ollama create corporatetraveldc-pi5-chat -f "${REPO_DIR}/corporatetraveldc.chat"

echo ""
echo "=== Building corporatetraveldc-pi5-osint (gemma3:4b + EP/marketing dual-use context) ==="
ollama create corporatetraveldc-pi5-osint -f "${REPO_DIR}/corporatetraveldc.osint"

echo ""
echo "=== Verifying models ==="
ollama list | grep -E "corporatetraveldc|gemma"

echo ""
echo "=== Warm-loading both models (1-token probe) ==="
curl -s http://100.x.x.x:11434/api/generate \
  -d '{"model":"corporatetraveldc-pi5-chat","prompt":"ping","stream":false,"options":{"num_predict":1}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('corporatetraveldc-pi5-chat:', 'OK' if d.get('response') is not None else 'FAIL')"

curl -s http://100.x.x.x:11434/api/generate \
  -d '{"model":"corporatetraveldc-pi5-osint","prompt":"ping","stream":false,"options":{"num_predict":1}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('corporatetraveldc-pi5-osint:', 'OK' if d.get('response') is not None else 'FAIL')"

echo ""
echo "Done. Update OLLAMA_CHAT_MODEL and OLLAMA_OSINT_MODEL in dispatch.env if needed,"
echo "then rebuild containers: bash build-images.sh && systemctl --user restart corporatetraveldc-{web,poller,pusher}"
