# scripts/lib/heavy_lock.sh -- ONE turn at a time for the box's heavy jobs (2026-10-12).
#
# The nightly backup (dumps + Borg + off-box sync), the weekly restore test and the
# serialized full-load rollout each push load past the rollout's own 15 hold point; on
# 2026-10-12 the 03:40Z backup fired 6 min into a relay's full load (load 20.5 on 4 cores)
# and the rollout sat holding for load. Source this file, then:
#
#   heavy_lock_acquire <name> [max_wait_s]   # waits its turn (default 7200 s); 1 = timed out
#
# The lock is held until the calling shell exits (fd 8). The holder's name, pid and start
# time are written next to the lock so a waiter can say WHO it is waiting on. A child of the
# holder (CTDC_HEAVY_LOCK_HELD set, e.g. stack-refresh -> serialized-rollout) skips it.
#
# 2026-10-23 (operator): while the default route is on the METERED failover hotspot, only the jobs in
# HEAVY_METERED_OK may take the turn -- the nightly backup (its off-site upload caps itself at 2 Mbps)
# and the restore test (local Borg only). Every other heavy job (rollout, stack refresh, image pulls)
# WAITS for the primary uplink within its max_wait, then gives up with "deferred: metered uplink".
# Default-deny: a heavy job added later defers on the hotspot unless it is added here. See lib/uplink.sh.
HEAVY_LOCK_DIR="${HEAVY_LOCK_DIR:-${HOME}/.local/state/ctdc-heavy}"
HEAVY_METERED_OK=("nightly backup" "restore test")
source "$(dirname "${BASH_SOURCE[0]}")/uplink.sh"

heavy_metered_ok() { local n; for n in "${HEAVY_METERED_OK[@]}"; do [[ "$1" == "$n" ]] && return 0; done; return 1; }

heavy_lock_holder() { cat "${HEAVY_LOCK_DIR}/holder" 2>/dev/null || echo "unknown"; }

heavy_lock_acquire() {
  local name="$1" max="${2:-7200}" waited=0 step=30
  (( max < 60 )) && step=1
  if [[ -n "${CTDC_HEAVY_LOCK_HELD:-}" ]]; then
    echo "[heavy-lock] ${name}: inside ${CTDC_HEAVY_LOCK_HELD}'s turn; not taking it again"; return 0
  fi
  if ! heavy_metered_ok "$name"; then
    until ! uplink_is_metered; do
      (( waited >= max )) && { echo "[heavy-lock] ${name}: deferred: metered uplink ($(uplink_default_iface)) still the default route after ${waited}s" >&2; return 1; }
      (( waited % 300 == 0 )) && echo "[heavy-lock] ${name}: default route is the metered uplink ($(uplink_default_iface)); waiting for the primary (${waited}s so far)"
      sleep "$step"; waited=$((waited + step))
    done
  fi
  mkdir -p -m 0700 "$HEAVY_LOCK_DIR"
  exec 8>"${HEAVY_LOCK_DIR}/lock"
  until flock -n 8; do
    (( waited >= max )) && { echo "[heavy-lock] ${name}: still busy after ${waited}s (holder: $(heavy_lock_holder))" >&2; return 1; }
    (( waited % 300 == 0 )) && echo "[heavy-lock] ${name}: waiting for $(heavy_lock_holder) (${waited}s so far)"
    sleep "$step"; waited=$((waited + step))
  done
  printf '%s pid=%s since=%s\n' "$name" "$$" "$(date -u +%FT%TZ)" > "${HEAVY_LOCK_DIR}/holder"
  export CTDC_HEAVY_LOCK_HELD="$name"
  (( waited > 0 )) && echo "[heavy-lock] ${name}: got the turn after waiting ${waited}s" || echo "[heavy-lock] ${name}: got the turn"
  return 0
}
