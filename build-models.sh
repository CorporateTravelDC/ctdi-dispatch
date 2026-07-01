#!/usr/bin/env bash
# build-models.sh — Build csexec custom Ollama models from Modelfiles
# Run after 'ollama pull qwen3.5:9b' is complete.
# Both custom models are based on qwen3.5:9b.
# Run as corporatetraveldc (not root).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Building corporatetraveldc-pi5-chat (qwen3.5:9b + operator context) ==="
ollama create corporatetraveldc-pi5-chat -f "${REPO_DIR}/Modelfile.chat"

echo ""
echo "=== Building corporatetraveldc-pi5-osint (qwen3.5:9b + EP/marketing dual-use context) ==="
ollama create corporatetraveldc-pi5-osint -f "${REPO_DIR}/Modelfile.osint"

echo ""
echo "=== Verifying models ==="
ollama list | grep -E "csexec|qwen"

echo ""
echo "=== Warm-loading both models (1-token probe) ==="
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"csexec-chat","prompt":"ping","stream":false,"options":{"num_predict":1}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('csexec-chat:', 'OK' if d.get('response') is not None else 'FAIL')"

curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"csexec-osint","prompt":"ping","stream":false,"options":{"num_predict":1}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('csexec-osint:', 'OK' if d.get('response') is not None else 'FAIL')"

echo ""
echo "Done. Update OLLAMA_CHAT_MODEL and OLLAMA_OSINT_MODEL in dispatch.env if needed,"
echo "then rebuild containers: bash build-images.sh && systemctl --user restart corporatetraveldc-{web,poller,pusher}"
