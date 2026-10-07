#!/usr/bin/env bash
# scripts/council.sh -- council (collaborative) / arena (adversarial, blind)
# convenes (Wave 2, 2026-10-04). Any team account can REQUEST one; it does
# nothing until a HUMAN signs it (scripts/approve.sh). "Force" = a human
# convenes it with participants marked :required.
#
#   council.sh request --mode council|arena --subject S --participant ACCT[:required] [...]
#                      [--brief-file F] [--deadline-hours N]       (signed with YOUR board key)
#   council.sh convene  <same args>      request + approve it yourself (human, approval key)
#   council.sh list [requested|active|closed|denied]
#   council.sh status <council-id>
#   council.sh close  <council-id>       request the close (signed); a human then approves it
#
# On approval each participant gets the convene on the board's `council`
# thread and a write grant for task council-<id> until the deadline; output
# lands in Series/contributions/council-<id>/<account>/ (POST
# /api/v1/workspace/contribute). Arena hides the others' work until a human
# closes the round. A missed deadline by a :required participant is reported
# to the operator (board_sweep), never a kill.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIGN="${REPO_ROOT}/scripts/board-sign.sh"
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
send() {   # METHOD PATH [bodyfile] -> body; exits non-zero unless 2xx
  local out code; out="$("$SIGN" "$@" --send)"; code="$(printf '%s\n' "$out" | tail -1 | awk '{print $2}')"
  printf '%s\n' "$out" | sed '$d'; [[ "$code" == 2?? ]] || { echo "council: HTTP ${code}" >&2; exit 1; }
}
CMD="${1:-}"; shift || true
case "$CMD" in
  request|convene)
    MODE=""; SUBJ=""; BRIEF=""; HOURS=48; PARTS=()
    while (( $# )); do case "$1" in
      --mode) MODE="$2"; shift ;; --subject) SUBJ="$2"; shift ;; --brief-file) BRIEF="$2"; shift ;;
      --deadline-hours) HOURS="$2"; shift ;; --participant) PARTS+=("$2"); shift ;; *) usage ;; esac; shift; done
    [[ -n "$MODE" && -n "$SUBJ" && ${#PARTS[@]} -gt 0 ]] || usage
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    BRIEF_TEXT=""; [[ -n "$BRIEF" ]] && BRIEF_TEXT="$(cat "$BRIEF")"
    BRIEF_TEXT="$BRIEF_TEXT" python3 - "$MODE" "$SUBJ" "$HOURS" "${PARTS[@]}" > "$tmp" <<'PYEOF'
import json, os, sys
mode, subj, hours, *parts = sys.argv[1:]
print(json.dumps({"mode": mode, "subject": subj, "brief": os.environ.get("BRIEF_TEXT", ""),
                  "deadline_hours": float(hours),
                  "participants": [{"account": p.split(":")[0], "required": p.endswith(":required")} for p in parts]}))
PYEOF
    resp="$(send POST /api/v1/council "$tmp")"; echo "$resp"
    if [[ "$CMD" == convene ]]; then
      aid="$(RESP="$resp" python3 - <<'PYEOF'
import json, os
print(json.loads(os.environ["RESP"])["approval_id"])
PYEOF
)"
      exec "${REPO_ROOT}/scripts/approve.sh" allow "$aid"
    fi ;;
  list) send GET "/api/v1/council${1:+?status=$1}" ;;
  status) [[ -n "${1:-}" ]] || usage; send GET "/api/v1/council/$1" ;;
  close) [[ -n "${1:-}" ]] || usage; send POST "/api/v1/council/$1/close"; echo "a human approves the close with: scripts/approve.sh allow <close_approval_id>" ;;
  *) usage ;;
esac
