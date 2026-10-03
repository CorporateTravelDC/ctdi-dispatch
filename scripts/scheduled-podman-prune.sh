#!/bin/bash
# scripts/scheduled-podman-prune.sh
# Reclaim dangling (untagged) podman images -- the layers every image
# rebuild orphans and nothing ever cleaned up.
#
# Why (2026-10-03): 7,265 image records / 57 GB reclaimable had accumulated
# since 2026-06, almost all from the Aug-Sep rebuild cadence, driving the
# root filesystem to 92% and leaving a corrupted image record behind
# (6b09d823ebec) that broke `podman system df` / `podman images` outright.
# One manual prune took ~1h. Kept incremental here so it never gets there
# again.
#
# Two triggers, either one fires a prune (operator directive):
#   - threshold: reclaimable >= PODMAN_PRUNE_THRESHOLD_GB (default 5)
#   - weekly floor: >= PODMAN_PRUNE_MAX_AGE_DAYS (default 7) since last prune
# plus --force (manual: skip the triggers, operator-run).
#
# ROLLBACK WINDOW (operator directive 2026-10-03): nothing younger than
# PODMAN_PRUNE_MIN_AGE (default 24h) is ever pruned, in ANY mode including
# --force -- `podman image prune --filter until=24h`. A rebuild that eats mud
# must leave its predecessor's layers on disk for a day. This is also why
# build-images.sh does NOT call this script after a build (it did, briefly,
# the same day): it tags the outgoing image :previous instead, which keeps
# it non-dangling and therefore untouchable here for as long as it holds
# that tag, and makes rollback one `podman tag ... :latest` + restart.
#
# Why 5 and not 10 (measured 2026-10-03 from the layer chains): one full
# build-images.sh rebuild orphans only ~2.2-3.2 GB (six images at ~0.55 GB
# each, ~0.40 GB unique -- the python base layers are shared and cached),
# and the post-build --force hook already handles that case. The threshold
# only governs the OTHER sources of dangling images (Sunday's external
# image update, ad-hoc builds, manual pulls); dangling-only at idle I/O
# priority costs nothing, so there is no reason to let it reach 10.
#
# Only ever `podman image prune` WITHOUT -a: dangling/untagged only. Tagged
# previous builds and every external image stay. Runs nice/ionice-idle so it
# yields to the ingest/LLM workload, and refuses to overlap a running
# `podman build` or another prune (both would just contend on the storage
# lock).
#
# Usage:
#   scheduled-podman-prune.sh            # normal run (timer)
#   scheduled-podman-prune.sh --force    # prune regardless of triggers
#   scheduled-podman-prune.sh --dry-run
#   scheduled-podman-prune.sh --status
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/scheduled-podman-prune"
LOG_FILE="${STATE_DIR}/prune.log"
STATE_FILE="${STATE_DIR}/state.json"
LOCK_FILE="${STATE_DIR}/.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

THRESHOLD_GB="${PODMAN_PRUNE_THRESHOLD_GB:-5}"
MAX_AGE_DAYS="${PODMAN_PRUNE_MAX_AGE_DAYS:-7}"
MIN_AGE="${PODMAN_PRUNE_MIN_AGE:-24h}"   # never prune anything younger -- rollback window

mkdir -p "${STATE_DIR}"

# Same reason as scheduled-ingest-restart.sh: dispatch.env is a podman
# --env-file, not bash-source-safe. Pull only the keys needed. The ntfy
# token is read here and used only inside curl -- never echoed/logged.
read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}
NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")";  NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"; NTFY_TOKEN="${NTFY_TOKEN%%:*}"

MODE="run"
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --status)  MODE="status" ;;
    --force)   MODE="force" ;;
esac

log() {
    local level="$1"; shift
    local ts; ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-2}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 "${auth_args[@]}" \
        -H "Title: ${title}" -H "Priority: ${priority}" -H "Tags: broom" \
        -d "${msg}" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 \
        || log "warn" "ntfy_send failed (token_set=$([[ -n "${NTFY_TOKEN}" ]] && echo yes || echo no))"
}

# Bytes held by DANGLING images only -- the set `podman image prune` (no -a)
# can actually remove. NOT `podman system df`'s "reclaimable", which also
# counts unused-but-tagged images (:integrity-test, :debug, docgen...) that
# this script deliberately never touches; thresholding on that number would
# trigger a do-nothing prune every single day. Size includes layers shared
# with other images, so it overstates what freeing them returns -- fine for
# a threshold, and the post-run "freed" figure is measured, not estimated.
dangling_bytes() {
    podman images --format json 2>/dev/null | python3 -c '
import json, sys
imgs = json.load(sys.stdin)
imgs = imgs if isinstance(imgs, list) else [imgs]
total = 0
for i in imgs:
    if i.get("Dangling") or not (i.get("Names") or i.get("RepoTags")):
        total += int(i.get("Size") or 0)
print(total)
' 2>/dev/null || echo 0
}

# podman system df "reclaimable" for the Images row (dangling + unused
# tagged) -- reported in --status for context only, never used to trigger.
reclaimable_bytes() {
    podman system df --format json 2>/dev/null | python3 -c '
import json, re, sys
def to_bytes(v):
    if isinstance(v, (int, float)): return int(v)
    # podman 5.x emits e.g. "6.685GB (49%)" -- parse the leading number+unit,
    # ignore any trailing percentage. No end anchor on purpose.
    m = re.match(r"\s*([\d.]+)\s*([kKMGT]?i?B?)", str(v))
    if not m: return 0
    n, u = float(m.group(1)), m.group(2).lower().rstrip("ib")
    return int(n * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}.get(u, 1))
rows = json.load(sys.stdin)
rows = rows if isinstance(rows, list) else [rows]
for r in rows:
    if str(r.get("Type", "")).lower().startswith("image"):
        # podman 5.x: RawReclaimable is exact bytes; Reclaimable is the
        # human string ("6.685GB (49%)"). Prefer the exact one.
        v = r.get("RawReclaimable")
        print(to_bytes(v if v is not None else r.get("Reclaimable", 0))); break
else:
    print(0)
' 2>/dev/null || echo 0
}

last_prune_epoch() {
    [[ -f "${STATE_FILE}" ]] || { echo 0; return; }
    grep -o '"last_prune_epoch":[0-9]*' "${STATE_FILE}" 2>/dev/null | grep -o '[0-9]*$' || echo 0
}

write_state() {
    printf '{"last_prune_epoch":%s,"last_prune_iso":"%s","last_reclaimable_bytes":%s,"last_mode":"%s"}\n' \
        "$1" "$(date -d "@$1" '+%Y-%m-%dT%H:%M:%S%z')" "$2" "$3" > "${STATE_FILE}"
}

human_gb() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1e9 }'; }

# A build or another prune in flight means the storage lock is busy -- back
# off rather than queue behind it. Matches `podman build` processes only, so
# build-images.sh calling us at ITS end (after its builds finish) is not a
# false positive on its own name.
busy_reason() {
    # Anchored (^): a real podman process's argv starts with "podman"; an
    # unanchored pattern also matches any shell whose own command line
    # happens to contain it (bit this script's smoke test twice).
    if pgrep -f "^podman build" >/dev/null 2>&1; then echo "podman build in progress"; return 0; fi
    if pgrep -f "^podman (image|system) prune" >/dev/null 2>&1; then echo "another prune in progress"; return 0; fi
    return 1
}

now_epoch=$(date +%s)
last=$(last_prune_epoch)
age_days=$(( (now_epoch - last) / 86400 ))

if [[ "${MODE}" == "status" ]]; then
    recl=$(dangling_bytes)
    echo "threshold:            ${THRESHOLD_GB} GB (of dangling-image bytes)"
    echo "weekly floor:         ${MAX_AGE_DAYS} days"
    echo "rollback window:      images younger than ${MIN_AGE} are never pruned"
    echo "dangling now:         $(human_gb "${recl}") GB  <- what a prune can touch"
    echo "podman 'reclaimable': $(human_gb "$(reclaimable_bytes)") GB  (includes unused TAGGED images; never pruned here)"
    echo "days since last prune: $( (( last > 0 )) && echo "${age_days}" || echo "never recorded" )"
    echo "busy:                 $(busy_reason || echo no)"
    exit 0
fi

if reason=$(busy_reason); then
    log "info" "skipping -- ${reason}"
    exit 0
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "info" "skipping -- lock held by another run"
    exit 0
fi

recl=$(dangling_bytes)
recl_gb=$(human_gb "${recl}")
threshold_bytes=$(awk -v g="${THRESHOLD_GB}" 'BEGIN { printf "%d", g * 1e9 }')

trigger=""
if [[ "${MODE}" == "force" ]]; then
    trigger="forced (manual)"
elif (( recl >= threshold_bytes )); then
    trigger="threshold (${recl_gb} GB dangling >= ${THRESHOLD_GB} GB)"
elif (( last > 0 && age_days >= MAX_AGE_DAYS )); then
    trigger="weekly floor (${age_days} days since last prune)"
elif (( last == 0 )); then
    trigger="first run (no prune ever recorded)"
fi

if [[ -z "${trigger}" ]]; then
    log "info" "no trigger -- ${recl_gb} GB dangling (< ${THRESHOLD_GB} GB), ${age_days}d since last prune (< ${MAX_AGE_DAYS}d)"
    exit 0
fi

log "info" "-------- prune: ${trigger} --------"
if [[ "${MODE}" == "dry-run" ]]; then
    log "info" "[DRY-RUN] would run: podman image prune -f --filter until=${MIN_AGE}  (dangling only, never -a, nothing younger than ${MIN_AGE})"
    exit 0
fi

start=$(date +%s)
if nice -n 19 ionice -c3 podman image prune -f --filter "until=${MIN_AGE}" >>"${LOG_FILE}" 2>&1; then
    after=$(dangling_bytes)
    freed=$(( recl - after )); (( freed < 0 )) && freed=0
    took=$(( $(date +%s) - start ))
    write_state "${now_epoch}" "${after}" "${MODE}"
    log "info" "pruned: freed $(human_gb "${freed}") GB in ${took}s (dangling now $(human_gb "${after}") GB; anything younger than ${MIN_AGE} was left for rollback)"
    # Only worth a push when it actually did something -- a trigger that
    # found nothing older than the rollback window is a log line, not an alert.
    if (( freed >= 100000000 )); then
        ntfy_send "podman prune: ran" \
            "Trigger: ${trigger}. Freed $(human_gb "${freed}") GB in ${took}s; $(human_gb "${after}") GB dangling remains (under ${MIN_AGE} old, kept for rollback)." 2
    fi
else
    log "error" "podman image prune failed (see ${LOG_FILE})"
    ntfy_send "podman prune: FAILED" \
        "Trigger: ${trigger}. podman image prune returned non-zero -- check ${LOG_FILE}. If 'locating item named manifest' appears, a corrupted image record needs a manual rmi (see 2026-10-03 note)." 4
    exit 1
fi
log "info" "-------- end --------"
