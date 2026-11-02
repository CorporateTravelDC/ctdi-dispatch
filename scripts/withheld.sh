#!/usr/bin/env bash
# scripts/withheld.sh -- operator CLI for withheld categories (common/withheld.py, 2026-11-02):
# LADD aircraft, privileged trains, privileged vessels, and any category added later.
# Operator account over SSH only: everything runs inside the web container through
# scripts/lib/gov-exec.sh, which only the operator's own podman can reach.
#
#   withheld.sh issue DEVICE --categories aircraft,rail,vessel [--hours N]
#                                   a pass for one screen (N <= 12, default 12); asks for YOUR
#                                   SSH signature, then prints a one-time pairing code
#                                   (10 minutes) to enter at /api/withheld/pair on that device
#   withheld.sh master DEVICE [--hours N]
#                                   MASTER token (every category, present and future) for YOU,
#                                   an active human admin signer only: GPG-signs the request with
#                                   your pinned primary key, then asks for your SSH signature,
#                                   then prints the pairing code. Any kill or lockdown wipes it.
#   withheld.sh status              switch state, audit key, list counts, live passes (no tokens)
#   withheld.sh revoke DEVICE       revoke every pass / master token on that device now
#   withheld.sh off [reason]        KILL SWITCH: freeze every category, revoke every pass, wipe
#                                   the master token (lockdown.sh and agent-gateway.sh kill-all
#                                   run the same)
#   withheld.sh off --category C [reason]   freeze one category and revoke its passes only
#   withheld.sh on                  thaw after off/lockdown; asks for YOUR signature
#   withheld.sh list-add CATEGORY KIND      add to an operator-kept list (aircraft opt-outs: reg | callsign | hex; rail: train;
#   withheld.sh list-remove CATEGORY KIND   vessel: mmsi | imo | name | callsign); the value is
#                                           typed at a hidden prompt, never argv
#   withheld.sh audit-check [--since H]     test a suspected identifier against the audit log
#                                           (hidden prompt)
#
# A pass is a token naming category grants (ladd.view, rail.view, vessel.view). An agent
# account gets a grant only through scripts/mint-agent-api-token.sh --actions ...; it can
# never hold the master token.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "${REPO_ROOT}/scripts/lib/gov-exec.sh"
usage() { sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
OPERATOR="${CTDC_OPERATOR_USER:-corporatetraveldc}"
ME="$(id -un)"
if [[ "$ME" != "$OPERATOR" && "${GOV_EXEC_LOCAL:-0}" != 1 ]]; then
  echo "withheld.sh: operator account only (${OPERATOR}); you are ${ME}" >&2
  exit 77
fi
hours_opt() { HOURS=12; while [[ $# -gt 0 ]]; do case "$1" in --hours) HOURS="${2:?}"; shift 2 ;; *) usage ;; esac; done; }
print_code() {
  echo
  echo "Pairing code (once, 10 minutes):  $1"
  echo "On $2: open the live panel at /api/withheld/pair and enter it."
}
CMD="${1:-}"; shift || true
case "$CMD" in
  issue)
    [[ -n "${1:-}" ]] || usage
    DEV="$1"; shift; CATS=""; HOURS=12
    while [[ $# -gt 0 ]]; do
      case "$1" in --categories) CATS="${2:?}"; shift 2 ;; --hours) HOURS="${2:?}"; shift 2 ;; *) usage ;; esac
    done
    [[ -n "$CATS" ]] || { echo "name --categories (aircraft,rail,vessel)" >&2; exit 64; }
    OUT="$(gov_py "$DEV" "$HOURS" "$ME" "$CATS" <<'PYEOF'
import sys
from common import withheld as w
try:
    r = w.request_pass(sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4].split(","))
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
print(r["approval_id"]); print(r["code"])
PYEOF
)"
    AID="$(sed -n 1p <<<"$OUT")"; CODE="$(sed -n 2p <<<"$OUT")"
    echo "pass requested for ${DEV} (${CATS}, ${HOURS} h, approval ${AID}); signing it now"
    "${REPO_ROOT}/scripts/approve.sh" allow "$AID"
    print_code "$CODE" "$DEV" ;;
  master)
    [[ -n "${1:-}" ]] || usage
    DEV="$1"; shift; hours_opt "$@"
    FPR="$(grep -m1 '^SIGNING_KEY_FINGERPRINT=' "${REPO_ROOT}/security/signing.env" | cut -d= -f2- | tr -cd 'A-Fa-f0-9')"
    [[ ${#FPR} -eq 40 ]] || { echo "no operator signing key pinned in security/signing.env" >&2; exit 78; }
    OUT="$(gov_py "$DEV" "$HOURS" "$ME" <<'PYEOF'
import sys
from common import withheld as w
try:
    r = w.request_master(sys.argv[1], float(sys.argv[2]), sys.argv[3])
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
print(r["approval_id"]); print(r["code"]); print(r["spec"])
PYEOF
)"
    AID="$(sed -n 1p <<<"$OUT")"; CODE="$(sed -n 2p <<<"$OUT")"; SPEC="$(sed -n 3p <<<"$OUT")"
    TMP="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/withheld-master.XXXXXX")"
    trap 'rm -rf "$TMP"' EXIT
    printf '%s' "$SPEC" > "$TMP/spec"
    echo "master token requested for ${DEV} (${HOURS} h, approval ${AID})"
    echo "1/2 GPG: signing the request with your primary key ${FPR: -16}"
    # The pinned fingerprint is the PRIMARY key, which is certify-only; no trailing "!" so gpg
    # signs with that key's signing subkey (attach_gpg accepts the primary or the subkey).
    gpg --armor --detach-sign --local-user "${FPR}" --output "$TMP/spec.asc" "$TMP/spec"
    WITHHELD_GPG_SIG="$(cat "$TMP/spec.asc")"; export WITHHELD_GPG_SIG
    GOV_PASS_ENV="WITHHELD_GPG_SIG" gov_py "$AID" <<'PYEOF'
import os, sys
from common import withheld as w
try:
    fpr = w.attach_gpg(sys.argv[1], os.environ.get("WITHHELD_GPG_SIG", ""))
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
print(f"    GPG signature verified (...{fpr[-16:]})")
PYEOF
    unset WITHHELD_GPG_SIG
    echo "2/2 SSH: approval signature"
    "${REPO_ROOT}/scripts/approve.sh" allow "$AID"
    print_code "$CODE" "$DEV" ;;
  status)
    gov_py <<'PYEOF'
import time
from common import withheld as w
s = w.status()
print(f"frozen: {', '.join(s['frozen']) or 'none'}; audit key: {'present' if s['audit_key'] else 'MISSING (nothing is revealed)'}")
print("lists: " + ", ".join(f"{k} {v}" for k, v in sorted(s["lists"].items())))
for p in s["passes"]:
    left = int(((p["expires_at"] or 0) - time.time()) / 60)
    what = "MASTER" if p["master"] else ",".join(p["categories"])
    print(f"  {p['user']:<24} {p['device'] or '-':<20} {what:<24} {left} min left")
print(f"{len(s['passes'])} live pass(es)")
PYEOF
    ;;
  revoke)
    [[ -n "${1:-}" ]] || usage
    gov_py "$1" "$ME" <<'PYEOF'
import sys
from common import withheld as w
print(f"revoked {w.revoke_device(sys.argv[1], sys.argv[2])} token(s) for {sys.argv[1]}")
PYEOF
    ;;
  off)
    CAT=""
    if [[ "${1:-}" == --category ]]; then CAT="${2:?}"; shift 2; fi
    gov_py "${SUDO_USER:-$ME}" "${1:-withheld.sh off}" "$CAT" <<'PYEOF'
import sys
from common import withheld as w
cats = [sys.argv[3]] if sys.argv[3] else None
try:
    n = w.kill_all(sys.argv[1], sys.argv[2], cats)
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
print(f"FROZEN: {', '.join(cats or sorted(w.CATEGORIES))}; {n} token(s) revoked or wiped.")
print("Undo: withheld.sh on (your signature), then issue passes again" + ("" if cats else " and re-mint the master token."))
PYEOF
    ;;
  on)
    AID="$(gov_py <<'PYEOF'
from common import withheld as w
print(w.request_thaw()["approval_id"])
PYEOF
)"
    echo "thaw requested (approval ${AID}); signing it now"
    exec "${REPO_ROOT}/scripts/approve.sh" allow "$AID" ;;
  list-add|list-remove)
    [[ -n "${1:-}" && -n "${2:-}" ]] || usage
    read -rsp "Value for ${1}/${2} (hidden): " WITHHELD_VALUE; echo
    export WITHHELD_VALUE
    GOV_PASS_ENV="WITHHELD_VALUE" gov_py "$CMD" "$1" "$2" "$ME" <<'PYEOF'
import os, sys
from common import withheld as w
fn = w.list_add if sys.argv[1] == "list-add" else w.list_remove
try:
    n = fn(sys.argv[2], sys.argv[3], os.environ.get("WITHHELD_VALUE", ""), sys.argv[4])
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
print(f"{sys.argv[1]} {sys.argv[2]}/{sys.argv[3]}: {n} change(s); lists now " +
      ", ".join(f"{k} {v}" for k, v in sorted(w.list_counts().items())))
PYEOF
    unset WITHHELD_VALUE ;;
  audit-check)
    SINCE_H=720
    if [[ "${1:-}" == --since ]]; then SINCE_H="${2:?}"; fi
    read -rsp "Suspected tail, hex, callsign, train number or MMSI (hidden): " WITHHELD_SUSPECT; echo
    export WITHHELD_SUSPECT
    GOV_PASS_ENV="WITHHELD_SUSPECT" gov_py "$SINCE_H" <<'PYEOF'
import os, sys, time
from common import withheld as w
since = time.time() - float(sys.argv[1]) * 3600
try:
    rows = w.audit_check([os.environ.get("WITHHELD_SUSPECT", "")], since)
except w.WithheldError as e:
    sys.exit(f"refused: {e.detail}")
for r in rows:
    t = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(r["ts"]))
    print(f"{t}  {r['kind']:<8} {r['category']:<8} {r['user_label'] or '-':<24} {r['device_label'] or '-':<20} {r['path'] or ''}")
print(f"{len(rows)} matching audit row(s) in the last {sys.argv[1]} h")
PYEOF
    unset WITHHELD_SUSPECT ;;
  *) usage ;;
esac
