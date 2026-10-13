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
HEAVY_LOCK_DIR="${HEAVY_LOCK_DIR:-${HOME}/.local/state/ctdc-heavy}"

heavy_lock_holder() { cat "${HEAVY_LOCK_DIR}/holder" 2>/dev/null || echo "unknown"; }

heavy_lock_acquire() {
  local name="$1" max="${2:-7200}" waited=0 step=30
  (( max < 60 )) && step=1
  if [[ -n "${CTDC_HEAVY_LOCK_HELD:-}" ]]; then
    echo "[heavy-lock] ${name}: inside ${CTDC_HEAVY_LOCK_HELD}'s turn; not taking it again"; return 0
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
