#!/usr/bin/env bash
# scripts/skill-grants.sh -- per-agent / per-task skill grants with clawback
# (operator directive 2026-10-04). Thin wrapper over scripts/lib/skill_grants.py;
# design, grammar and threat notes live there and in docs/AGENT_SEGMENTATION.md
# ("Skills: grants and clawback").
#
#   skill-grants.sh list [ACCOUNT]                 effective skills per agent
#   skill-grants.sh check                          validate the grants file
#   sudo skill-grants.sh grant  ACCOUNT|'*' SKILL|'*' [--task ID] [--until ISO+offset]
#   sudo skill-grants.sh deny   ACCOUNT|'*' SKILL|'*' [--task ID] [--until ISO+offset]
#   sudo skill-grants.sh revoke ACCOUNT|'*' SKILL|'*'
#   skill-grants.sh apply                          dry run: what would change
#   sudo skill-grants.sh apply --execute           materialize grants (hourly unit)
#   skill-grants.sh pin-vendor NAME...             operator: re-pin vendor skills, then sign
#
# Root runs the INSTALLED copy (/usr/local/libexec/ctdc, install-root-copies.sh),
# never the checkout. Before applying, the checkout must verify against the
# signed manifest (checked as the operator); a failure HOLDs -- nothing changes.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SELF_DIR}/.ctdc-installed" ]]; then
  REPO_ROOT="${CTDC_REPO_ROOT:-/opt/corporatetraveldc/private/ctdi-dispatch-internal}"; PY="${SELF_DIR}/skill_grants.py"
else
  REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"; PY="${REPO_ROOT}/scripts/lib/skill_grants.py"
fi
OPERATOR=corporatetraveldc
ENV_FILE=/etc/corporatetraveldc/dispatch.env; SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
read_env_var() { local key="$1" file="$2"; [[ -r "$file" ]] || return 0; grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2-; }
ntfy_send() {
  local base ops tok auth=()
  base="$(read_env_var NTFY_BASE_URL "$ENV_FILE")"; base="${base:-http://127.0.0.1:2586}"
  ops="$(read_env_var NTFY_OPS_TOPIC "$ENV_FILE")"; ops="${ops:-ops-health}"
  tok="$(read_env_var NTFY_TOKEN "$SECRETS_FILE")"; [[ -n "$tok" ]] && authhdr auth "${tok}"
  curl -sf --max-time 5 "${auth[@]}" -H "Title: $1" -H "Priority: ${3:-3}" -H "Tags: books" -d "$2" "${base}/${ops}" >/dev/null 2>&1 || true
}

if [[ "${1:-}" == apply && " $* " == *" --execute "* && $EUID -eq 0 && -z "${SKILL_GRANTS_FAKE_ROOT:-}" ]]; then
  # 2026-10-06 (operator choice "b"): the whole-tree gate stays; the push fires
  # only when a hold STARTS and when it CLEARS. Before this, every hourly run
  # during normal staging pushed a p4 (13 on 2026-10-06). HOLD_STATE is
  # root-only, outside every container mount.
  HOLD_STATE="${SKILL_GRANTS_HOLD_STATE:-/var/lib/ctdc-skill-grants/hold-since}"
  install -d -m 0700 "$(dirname "$HOLD_STATE")"
  if ! runuser -u "$OPERATOR" -- "${REPO_ROOT}/scripts/verify-manifest.sh" >/dev/null 2>&1; then
    if [[ -f "$HOLD_STATE" ]]; then
      echo "[HOLD] checkout does not verify against the signed manifest -- nothing changed (holding since $(cat "$HOLD_STATE"); already notified)"
    else
      date -u +%Y-%m-%dT%H:%MZ >"$HOLD_STATE"
      echo "[HOLD] checkout does not verify against the signed manifest -- nothing changed (hold started)"
      ntfy_send "skill-grants: HOLD started" "checkout failed verify-manifest; agent skills left as they were until the next sign (no further pushes until it clears)" 4
    fi
    exit 0
  fi
  if [[ -f "$HOLD_STATE" ]]; then
    since=$(cat "$HOLD_STATE"); rm -f "$HOLD_STATE"
    echo "[info] hold cleared (held since ${since})"
    ntfy_send "skill-grants: hold cleared" "checkout verifies again; grants applied (held since ${since})" 3
  fi
  out=$(python3 "$PY" --repo "$REPO_ROOT" --checkout "$REPO_ROOT" "$@"); rc=$?
  printf '%s\n' "$out"
  if (( rc != 0 )); then
    ntfy_send "skill-grants: $(grep -c '^\[FINDING\]' <<<"$out") finding(s)" "$(grep '^\[FINDING\]' <<<"$out" | head -8 | cut -c11-)" 4
  fi
  exit "$rc"
fi
exec python3 "$PY" --repo "$REPO_ROOT" --checkout "$REPO_ROOT" "$@"
