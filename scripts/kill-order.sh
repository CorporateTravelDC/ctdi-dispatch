#!/usr/bin/env bash
# scripts/kill-order.sh -- issue a signed KILL ORDER against a team account.
#
# Operator directive 2026-10-04: "the exact same logic from an admin user on
# the human side to kill another rogue human account ... a two-man or
# three-man human override of an admin: two or three non-admin users
# cross-signing a revocation ... with a quorum of at least two."
#
# Any team account (human or agent, non-root) can ISSUE an order; only the
# root-run liveness switch (scripts/team-liveness.sh, hourly timer + a .path
# unit that reacts within minutes) EXECUTES it, after verifying:
#   * the signature against the issuer's registered key (board_signers),
#   * the issuer is alive and active, ts within 7 days, issuer != target,
#   * the target is not the operator (root of trust; killing the operator is
#     a physical / ssh-level act, never a file in a directory),
#   * quorum: target role member/service or kind agent -> ONE admin issuer OR
#     QUORUM_NON_ADMIN (2) distinct non-admin issuers; target role admin ->
#     QUORUM_FOR_ADMIN (2; operator may set 3) distinct issuers of any role.
#
# Orders live in /var/lib/corporatetraveldc/team-liveness/orders/<target>/
# as <issuer>.<ts>.json + .sig (ssh-keygen -Y sign, namespace
# corporatetraveldc-kill, over the canonical JSON). The directory is
# root-owned, group ctdc-dev, mode 3775 (setgid + sticky) so any team member
# can drop an order and nobody can remove another's.
#
#   kill-order.sh issue <target> "<reason>"     sign with ~/.ssh/<account>_ed25519
#                                               (fallback ~/.ssh/cowork_ed25519)
#   kill-order.sh list [target]                 pending / executed / rejected
# Env: KILL_ORDERS_DIR (tests), KILL_ORDER_KEY (explicit private key path),
#      KILL_ORDER_ISSUER (tests only -- real runs use the Unix account).
set -euo pipefail
ORDERS="${KILL_ORDERS_DIR:-/var/lib/corporatetraveldc/team-liveness/orders}"
NS="corporatetraveldc-kill"
CMD="${1:-}"; shift || true
case "$CMD" in
  issue)
    TARGET="${1:-}"; REASON="${2:-}"
    [[ -n "$TARGET" && -n "$REASON" ]] || { echo "usage: kill-order.sh issue <target> \"<reason>\"" >&2; exit 64; }
    [[ "$TARGET" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "bad target name" >&2; exit 65; }
    ISSUER="${KILL_ORDER_ISSUER:-$(id -un)}"
    [[ "$ISSUER" != "$TARGET" ]] || { echo "an account cannot issue a kill order against itself" >&2; exit 65; }
    KEY="${KILL_ORDER_KEY:-}"
    if [[ -z "$KEY" ]]; then
      for k in "$HOME/.ssh/$(id -un)_ed25519" "$HOME/.ssh/cowork_ed25519" "$HOME/.ssh/id_ed25519"; do [[ -r "$k" ]] && { KEY="$k"; break; }; done
    fi
    [[ -n "$KEY" && -r "$KEY" ]] || { echo "no private key (looked for ~/.ssh/<account>_ed25519, ~/.ssh/cowork_ed25519; or set KILL_ORDER_KEY)" >&2; exit 66; }
    TS=$(date +%s)
    DIR="${ORDERS}/${TARGET}"; mkdir -p "$DIR" 2>/dev/null || { echo "cannot create ${DIR} (orders dir missing? see docs/AGENT_SEGMENTATION.md install steps)" >&2; exit 73; }
    BASE="${DIR}/${ISSUER}.${TS}"
    # canonical JSON: sorted keys, no whitespace -- the signed bytes
    python3 - "$ISSUER" "$TARGET" "$REASON" "$TS" "${BASE}.json" <<'PYEOF'
import json, sys
issuer, target, reason, ts, out = sys.argv[1:6]
doc = {"issuer": issuer, "reason": reason[:500], "target": target, "ts": int(ts)}
with open(out, "w") as fh:
    fh.write(json.dumps(doc, sort_keys=True, separators=(",", ":")))
PYEOF
    ssh-keygen -Y sign -f "$KEY" -n "$NS" -q <"${BASE}.json" >"${BASE}.sig" 2>/dev/null \
      || { rm -f "${BASE}.json" "${BASE}.sig"; echo "ssh-keygen -Y sign failed (key unreadable or not ed25519?)" >&2; exit 67; }
    chmod 0644 "${BASE}.json" "${BASE}.sig" 2>/dev/null || true
    # nudge the .path unit: it watches the orders ROOT, orders land in subdirs
    touch "${ORDERS}/.new" 2>/dev/null || true
    echo "kill order filed: target=${TARGET} issuer=${ISSUER} ts=${TS} -> ${BASE}.json (+.sig)"
    echo "it executes when the liveness switch verifies it and quorum is met (one admin, or two non-admins; an admin target needs two)."
    ;;
  list)
    T="${1:-}"
    for d in "${ORDERS}"/*/; do
      [[ -d "$d" ]] || continue; t=$(basename "$d"); [[ -n "$T" && "$t" != "$T" ]] && continue
      pend=$(ls "$d"/*.json 2>/dev/null | wc -l); ex=$(ls "$d/executed"/*.json 2>/dev/null | wc -l); rej=$(ls "$d/rejected"/*.json 2>/dev/null | wc -l)
      printf '%-24s pending=%s executed=%s rejected=%s\n' "$t" "$pend" "$ex" "$rej"
      for f in "$d"/*.json; do [[ -f "$f" ]] && printf '    pending: %s\n' "$(basename "$f" .json)"; done
    done
    ;;
  *) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;;
esac
