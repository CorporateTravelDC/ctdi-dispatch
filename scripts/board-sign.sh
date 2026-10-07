#!/usr/bin/env bash
# scripts/board-sign.sh -- sign a board API request with THIS account's SSH key
# (identity path, no BOARD_KEY needed). See docs/BOARD_SIGNING.md.
#
#   scripts/board-sign.sh POST /api/v1/board body.json          # print the curl
#   scripts/board-sign.sh POST /api/v1/board body.json --send   # perform it
#   scripts/board-sign.sh GET  /api/v1/board?thread=research --send
#
# Key: ~/.ssh/<account>_ed25519 (convention 2026-10-04: the filename names the
# account; every ctdc-agents / ctdc-ops account gets one at
# --add-agent / --add-human), falling back to ~/.ssh/cowork_ed25519 for the
# operator. Signer name = this Unix account. Signature namespace and canonical
# message must match common/board_sign.py exactly:
#   "<METHOD>\n<PATH>[?query]\n<unix-ts>\n<sha256-hex(body)>"   (query signed since 2026-10-04, U6;
#   every signature is accepted once -- run the script again for each request)
# One-line example post:
#   printf '{"from":"me","to":"dispatch","thread":"coord","subject":"hi","body":"signed"}' > /tmp/b.json \
#     && scripts/board-sign.sh POST /api/v1/board /tmp/b.json --send
set -euo pipefail
NS=corporatetraveldc-board
BASE="${BOARD_API_BASE:-http://127.0.0.1:8000}"
METHOD="${1:-}"; PATH_Q="${2:-}"; BODY_FILE=""; SEND=0
shift 2 || { echo "usage: $0 METHOD PATH [body-file] [--send]" >&2; exit 64; }
for a in "$@"; do case "$a" in --send) SEND=1 ;; *) BODY_FILE="$a" ;; esac; done
[[ -n "$METHOD" && -n "$PATH_Q" ]] || { echo "usage: $0 METHOD PATH [body-file] [--send]" >&2; exit 64; }
ME="$(id -un)"
KEY="${BOARD_SIGN_KEY:-$HOME/.ssh/${ME}_ed25519}"; [[ -f "$KEY" ]] || KEY="$HOME/.ssh/cowork_ed25519"; [[ -f "$KEY" ]] || KEY="$HOME/.ssh/id_ed25519"
[[ -f "$KEY" ]] || { echo "no signing key (~/.ssh/${ME}_ed25519, ~/.ssh/cowork_ed25519 or ~/.ssh/id_ed25519)" >&2; exit 66; }
SIGNER="${BOARD_SIGNER:-$(id -un)}"
TS=$(date +%s)
if [[ -n "$BODY_FILE" ]]; then BODY_SHA=$(sha256sum "$BODY_FILE" | cut -d' ' -f1); else BODY_SHA=$(printf '' | sha256sum | cut -d' ' -f1); fi
# 2026-10-04 (U6): the raw query string is signed with the path, exactly as
# sent -- pass PATH already percent-encoded (curl sends it verbatim)
MSG=$(printf '%s\n%s\n%s\n%s' "$(echo "$METHOD" | tr '[:lower:]' '[:upper:]')" "$PATH_Q" "$TS" "$BODY_SHA")
SIG=$(printf '%s' "$MSG" | ssh-keygen -Y sign -f "$KEY" -n "$NS" 2>/dev/null | base64 -w0)
CURL=(curl -sS -X "$(echo "$METHOD" | tr '[:lower:]' '[:upper:]')" "${BASE}${PATH_Q}"
      -H "X-Board-Signer: ${SIGNER}" -H "X-Board-Timestamp: ${TS}" -H "X-Board-Signature: ${SIG}")
[[ -n "$BODY_FILE" ]] && CURL+=(-H "Content-Type: application/json" --data-binary "@${BODY_FILE}")
# --send prints the HTTP status after the body (ctdc-agent's first signed post,
# 2026-10-04: it had to re-run the curl by hand just to see the code)
if (( SEND )); then "${CURL[@]}" -w '\nHTTP %{http_code}\n'; else printf '%q ' "${CURL[@]}"; echo; fi
