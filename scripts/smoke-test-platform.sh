#!/usr/bin/env bash
# scripts/smoke-test-platform.sh -- full-platform smoke test.
#
# Built 2026-08-17 after discovering the existing health-check tool
# (~/bin/dispatch-stack-guardian.sh, run on a ~20min cron all day) only
# checks 4 HTTP endpoints and has ZERO visibility into systemd --user unit
# failures or signed-manifest integrity drift. That blind spot let 18 of
# 20 scheduled skills (including corporatetraveldc-ep-advance -- the EP
# brief) silently fail for 9+ hours: MANIFEST.sha256/.asc was last signed
# 2026-08-16 14:35, a container rebuild since then changed 12 tracked
# files without a re-sign, and every skill invocation going through
# scripts/verified-exec.sh has been refusing to run ever since -- with
# zero surfaced alert, because nothing was checking systemd unit health.
#
# This script closes that specific gap. It does NOT replace
# dispatch-stack-guardian.sh's endpoint checks (kept here too, for one
# combined report) -- it adds the two checks that were missing:
#   1. Any failed systemd --user unit right now.
#   2. Signed-manifest integrity (delegates to scripts/verify-manifest.sh,
#      the same check verified-exec.sh already runs before every skill --
#      this just SURFACES its result instead of letting it fail silently
#      inside a container nobody's watching).
#
# Exit 0 always (failures are reported, not thrown) so this is safe to run
# from a cron/timer the same way the endpoint guardian is. Human-readable
# stdout; a machine-readable summary line is the last line of output.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

fail_count=0
echo "=== 1. systemd --user failed units ==="
mapfile -t failed_units < <(systemctl --user list-units --type=service --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}')
if [[ ${#failed_units[@]} -eq 0 ]]; then
  echo "  OK: no failed units"
else
  echo "  FAIL: ${#failed_units[@]} failed unit(s):"
  for u in "${failed_units[@]}"; do
    echo "    - ${u}"
  done
  fail_count=$((fail_count + 1))
fi

echo "=== 2. signed manifest integrity (delegates to verify-manifest.sh) ==="
if [[ -x scripts/verify-manifest.sh ]]; then
  if manifest_out=$(scripts/verify-manifest.sh 2>&1); then
    echo "  OK: deployed src/ matches signed MANIFEST.sha256"
  else
    echo "  FAIL: signed manifest does not match current tree -- every"
    echo "        verified-exec.sh-gated skill will refuse to run until"
    echo "        the operator re-runs scripts/sign-manifest.sh (GPG"
    echo "        passphrase required by design -- not automatable)."
    echo "${manifest_out}" | sed 's/^/    /'
    fail_count=$((fail_count + 1))
  fi
else
  echo "  SKIP: scripts/verify-manifest.sh not found or not executable"
fi

echo "=== 3. core HTTP endpoints (same 4 as dispatch-stack-guardian.sh) ==="
declare -A ENDPOINTS=(
  [dispatch-web]="http://100.x.x.x:8000/healthz"
  [dispatch-runner]="http://100.x.x.x:8001/healthz"
  [ntfy]="http://100.x.x.x:2586/v1/health"
  [ollama]="http://100.x.x.x:11434/api/tags"
)
for name in "${!ENDPOINTS[@]}"; do
  url="${ENDPOINTS[$name]}"
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    echo "  OK: ${name} (${code})"
  else
    echo "  FAIL: ${name} (${code})"
    fail_count=$((fail_count + 1))
  fi
done

echo "=== 4. ACARS ingest freshness (0 rows ever as of 2026-08-17 audit -- known-dead, tracked here so a real fix is visible) ==="
acars_count=$(sqlite3 -readonly -cmd ".timeout 5000" /var/lib/corporatetraveldc/corporatetraveldc.db \
  "SELECT COUNT(*) FROM acars_messages WHERE received_at > strftime('%s','now','-6 hours');" 2>/dev/null || echo "ERR")
if [[ "$acars_count" == "ERR" ]]; then
  echo "  SKIP: could not query acars_messages (db locked or unreachable)"
elif [[ "$acars_count" -gt 0 ]]; then
  echo "  OK: ${acars_count} ACARS message(s) in last 6h"
else
  echo "  KNOWN-FAIL: 0 ACARS messages in last 6h (acars-watcher/acarsrouter show"
  echo "              'active' but the pipeline -- local UDP AND airframes.io REST"
  echo "              -- has produced zero rows, ever, as of 2026-08-17. Not"
  echo "              counted in fail_count below since this is a known, already-"
  echo "              reported issue, not a new smoke-test failure -- remove this"
  echo "              carve-out once it's actually fixed."
fi

echo
if [[ $fail_count -eq 0 ]]; then
  echo "SMOKE-TEST: PASS (0 failing categories)"
else
  echo "SMOKE-TEST: FAIL (${fail_count} failing categor$([[ $fail_count -eq 1 ]] && echo y || echo ies))"
fi
exit 0
