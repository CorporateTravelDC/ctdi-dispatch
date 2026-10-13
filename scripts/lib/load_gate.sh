# scripts/lib/load_gate.sh -- the rollout's "hold before restart" load gate (2026-10-13).
#
# Was a fixed `load1 >= 15` loop with no limit. The box's resting load is now 17-20
# (ingest + the local model + hourly scans), so on 2026-10-13 the full load sat holding an
# UNCHANGED acarshub image for 45 min (15:41Z -> 16:27Z) although nothing was spiking.
# The gate now asks "are we riding a spike?" rather than "is the box busy?":
#
#   limit  = max(LOAD_GATE, LOAD_GATE_REL x the load when the rollout started)
#   hold while load1 >= limit, for at most LOAD_HOLD_MAX seconds; after that proceed
#          only while load1 < LOAD_CEIL (a real overload keeps holding)
#
# Source it, call load_gate_init once at rollout start, then before each restart:
#   load_gate_wait   -> 0 = go; 2 = the caller's deadline hit while holding
# The deadline check is the caller's past_deadline() when defined.
LOAD_GATE=${LOAD_GATE:-15}
LOAD_GATE_REL=${LOAD_GATE_REL:-1.25}
LOAD_HOLD_MAX=${LOAD_HOLD_MAX:-900}
LOAD_CEIL=${LOAD_CEIL:-30}
LOAD_GATE_STEP=${LOAD_GATE_STEP:-30}
LOAD_GATE_START=${LOAD_GATE_START:-}

load_gate_now() { cut -d' ' -f1 "${LOAD_GATE_LOADAVG:-/proc/loadavg}"; }

load_gate_init() {
  [[ -n "$LOAD_GATE_START" ]] || LOAD_GATE_START=$(load_gate_now)
  LOAD_GATE_LIMIT=$(awk -v g="$LOAD_GATE" -v r="$LOAD_GATE_REL" -v s="$LOAD_GATE_START" \
    'BEGIN{l=r*s; if (l<g) l=g; printf "%.2f", l}')
  echo "$(date +%T)  load gate: hold while load1 >= ${LOAD_GATE_LIMIT} (start load ${LOAD_GATE_START}), max ${LOAD_HOLD_MAX}s unless load1 >= ${LOAD_CEIL}"
}

load_gate_wait() {
  [[ -n "${LOAD_GATE_LIMIT:-}" ]] || load_gate_init
  local held=0 l
  while l=$(load_gate_now); awk -v l="$l" -v m="$LOAD_GATE_LIMIT" 'BEGIN{exit !(l >= m)}'; do
    if declare -F past_deadline >/dev/null && past_deadline; then return 2; fi
    if (( held >= LOAD_HOLD_MAX )) && awk -v l="$l" -v c="$LOAD_CEIL" 'BEGIN{exit !(l < c)}'; then
      echo "$(date +%T)  load $l >= ${LOAD_GATE_LIMIT} after ${held}s (max ${LOAD_HOLD_MAX}s) but < ${LOAD_CEIL} -- proceeding (resting load, not a spike)"
      return 0
    fi
    echo "$(date +%T)  load $l >= ${LOAD_GATE_LIMIT}, holding before restart (${held}s)"
    sleep "$LOAD_GATE_STEP"; held=$((held + LOAD_GATE_STEP))
  done
  return 0
}
