#!/usr/bin/env bash
# scripts/watchdog-tune.sh -- one-shot (Sun 2026-10-11 20:00 ET, root, installed
# copy): set RuntimeWatchdogSec to 120% of the worst stall measured by
# stall-monitor this week (floor 30s, cap 180s), then daemon-reexec and ntfy.
# --dry-run prints the decision only. Operator directive 2026-10-04.
set -euo pipefail
OUT=/var/lib/corporatetraveldc/reports/stall-monitor.json
# zz- sorts after the stock watchdog.conf, so this drop-in wins (the first
# version was 90-ctdc-..., which lost to watchdog.conf's 14s).
CONF=/etc/systemd/system.conf.d/zz-ctdc-watchdog.conf
STALE_CONF=/etc/systemd/system.conf.d/90-ctdc-watchdog.conf
DRY=0; [[ "${1:-}" == --dry-run ]] && DRY=1
read -r MAXG SINCE < <(python3 - "$OUT" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1])); print(d.get("max_gap_s", 0.0), int(d.get("since", 0)))
PYEOF
)
NEW=$(python3 - "$MAXG" <<'PYEOF'
import math, sys
g = float(sys.argv[1]); print(min(180, max(30, math.ceil(g * 1.2))))
PYEOF
)
MSG="worst stall since $(date -d @"$SINCE" '+%Y-%m-%d %H:%M') = ${MAXG}s -> RuntimeWatchdogSec=${NEW}s (120%, floor 30, cap 180)"
echo "$MSG"
(( DRY )) && exit 0
(( EUID == 0 )) || { echo "needs root" >&2; exit 77; }
tmp=$(mktemp)
{ sed -n '1,/^\[Manager\]/p' "$CONF" | sed '$d'; printf '# tuned %s: %s\n[Manager]\nRuntimeWatchdogSec=%s\n' "$(date -Is)" "$MSG" "$NEW"; } > "$tmp"
install -m 0644 -o root -g root "$tmp" "$CONF"; rm -f "$tmp" "$STALE_CONF"
systemctl daemon-reexec
systemctl show -p RuntimeWatchdogUSec
TOKEN=$(grep -m1 '^NTFY_TOKEN=' /etc/corporatetraveldc/dispatch-secrets.env 2>/dev/null | cut -d= -f2- || true)
BASE=$(grep -m1 '^NTFY_BASE_URL=' /etc/corporatetraveldc/dispatch.env 2>/dev/null | cut -d= -f2-); BASE=${BASE:-http://127.0.0.1:2586}
TOPIC=$(grep -m1 '^NTFY_OPS_TOPIC=' /etc/corporatetraveldc/dispatch.env 2>/dev/null | cut -d= -f2-); TOPIC=${TOPIC:-ops-health}
printf '%s' "$MSG" | curl -sf --max-time 5 -H @<(printf 'Authorization: Bearer %s\n' "$TOKEN") -H "Title: watchdog tuned" --data-binary @- "${BASE}/${TOPIC}" >/dev/null || true
