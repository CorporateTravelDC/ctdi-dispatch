#!/bin/bash
# scripts/net-failover-watchdog.sh
#
# Active upstream health check + automatic default-route failover between
# the two uplinks on this box (WiFi wld0 = primary, USB Ethernet enu1 =
# backup). Flips the backup below the primary when the primary path is
# dead, flips it back when the primary recovers, and pages loudly either
# way.
#
# WHY THIS EXISTS (2026-09-05 incident). The box lost external
# connectivity at ~16:06 ET and never recovered on its own -- it stayed
# down ~100 minutes until an operator reboot. Post-incident findings:
#
#   1. There is NO bond. `/proc/net/bonding/` is empty. wld0 and enu1 are
#      independent NM connections on different subnets (10.x.x.x/24 via
#      10.x.x.x, and 192.168.x.x/24 via 192.168.x.x), and the ONLY thing
#      choosing between them is the ipv4.route-metric on each connection
#      (wld0=100, enu1=600 -- both explicitly set, deliberate, and
#      confirmed correct with the operator 2026-09-05).
#   2. Metric-based selection only fails over when the losing interface's
#      route DISAPPEARS -- i.e. on carrier loss. It does nothing at all
#      for the failure mode actually observed: carrier up, link "fine",
#      upstream unreachable. The kernel happily keeps sending everything
#      out the dead metric-100 default forever.
#   3. NetworkManager could not help. Its connectivity probe is disabled
#      box-wide on purpose (/etc/NetworkManager/conf.d/no-connectivity-
#      check.conf, applied by harden-wifi.sh, marked "do not remove" --
#      the probe fired before Unbound was ready, marked links "limited",
#      and made browsers show offline). Across the entire outage NM
#      logged nothing but a routine DHCP renew: no carrier change, no
#      state change, so no failover was ever even attempted.
#      Re-enabling that probe would NOT have fixed this anyway -- NM's
#      connectivity check only reports a state, it never demotes a
#      default route or switches interfaces. Hence an external, active
#      checker: this script. (Operator decision 2026-09-05: leave the NM
#      probe disabled, do the health checking here.)
#
# DNS needs no special handling: /etc/resolv.conf is just `nameserver
# 127.0.0.1` and the local Unbound is a full RECURSIVE resolver with no
# forward-zone, so it is not bound to either interface's DHCP nameservers
# (75.75.75.75 on wifi, 192.168.x.x on ethernet). It recurses out over
# whatever the current default route is -- so flipping the route fixes
# resolution automatically. That is also why the outage surfaced as
# "Temporary failure in name resolution" everywhere: recursion could not
# reach the root servers over the dead path.
#
# PROBES ARE BY IP ON PURPOSE. A hostname probe would fail during exactly
# the outage we are detecting (recursion is down), so this would be
# diagnosing DNS, not the path. ICMP to well-known anycast resolvers,
# with a TCP:443 fallback for networks that drop ICMP.
#
# NOT gated on thermal-ingest-guard's LOCKDOWN tier, unlike
# runner-health-watchdog.sh. That gate exists so a watchdog cannot undo
# the guard's deliberate load-shedding -- but LOCKDOWN sheds SERVICES, it
# never touches routes, so there is nothing here to collide with. A box
# in LOCKDOWN with a dead uplink still wants its uplink back.
#
# Runs as corporatetraveldc via a user systemd timer every 60s. Failover
# needs FAIL_STRIKES consecutive bad checks (default 3 = ~3 min) and
# restore needs RESTORE_STRIKES consecutive good ones, so a single blip
# never flaps the route.
#
# Usage:
#   net-failover-watchdog.sh            # normal run (called by the timer)
#   net-failover-watchdog.sh --status   # print current state, no action
#
# ASCII output only -- no Unicode symbols (repo convention).
set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/net-failover-watch"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="/run/user/$(id -u)/net-failover-watchdog.lock"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

# Roles. Connection NAMES (not device names) are what nmcli modifies; the
# device name is what curl/ping bind to. Both are overridable from
# dispatch.env so a re-SSID or a NIC swap needs no code change.
#
# The backup is a cellular link (renamed from the meaningless "Wired
# connection 2" to "Verizon Hotspot" on 2026-09-05 -- it presents as a USB
# CDC ethernet device, which is why it looks wired).
#
# It is TRULY UNLIMITED, not capped (operator, 2026-09-05: it carried 701 GB
# last month under a misconfigured failover with no issues). Data volume is
# therefore NOT a constraint here, and nothing in this script should be
# justified by "saving data" -- an earlier draft of this comment got that
# wrong. The real cost of sitting on cellular is THROTTLING: sustained use
# degrades throughput, so fail-back exists to restore full-speed WiFi
# promptly, not to protect a data allowance.
#
# Known limitation that follows from this: the probe tests REACHABILITY
# (ICMP), not throughput. A throttled-but-reachable hotspot reads as
# perfectly healthy. That is an accepted gap -- detecting throttling would
# need real throughput sampling, which is not worth doing on the backup's
# 60s health check.
PRIMARY_CON_DEFAULT="Jorransgateway"
PRIMARY_IF_DEFAULT="wld0"
BACKUP_CON_DEFAULT="Verizon Hotspot"
BACKUP_IF_DEFAULT="enu1"

PROBE_IPS_DEFAULT="1.1.1.1 8.8.8.8 9.9.9.9"
FAIL_STRIKES_DEFAULT=3
RESTORE_STRIKES_DEFAULT=3
FAILOVER_METRIC_DEFAULT=50      # must beat the primary's 100 while failed over
ALERT_COOLDOWN_SECS=1800        # 30 min -- do not re-page every 60s cycle

mkdir -p "${STATE_DIR}"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

# Precedence: real environment variable > dispatch.env > built-in default.
# The environment tier is what makes this script testable at all -- the
# failover path can be exercised for real against a deliberately-dead
# interface (or a deliberately-bogus connection name, to check the
# apply-failed branch) without editing dispatch.env or touching the real
# roles. It also lets a systemd Environment= line override a role.
cfg() {
    local key="$1" fallback="$2" from_env from_file
    from_env="$(printenv "${key}" 2>/dev/null)"
    if [[ -n "${from_env}" ]]; then echo "${from_env}"; return; fi
    from_file="$(read_env_var "${key}" "${ENV_FILE}")"
    if [[ -n "${from_file}" ]]; then echo "${from_file}"; return; fi
    echo "${fallback}"
}

PRIMARY_CON="$(cfg NET_FAILOVER_PRIMARY_CON "${PRIMARY_CON_DEFAULT}")"
PRIMARY_IF="$(cfg NET_FAILOVER_PRIMARY_IF "${PRIMARY_IF_DEFAULT}")"
BACKUP_CON="$(cfg NET_FAILOVER_BACKUP_CON "${BACKUP_CON_DEFAULT}")"
BACKUP_IF="$(cfg NET_FAILOVER_BACKUP_IF "${BACKUP_IF_DEFAULT}")"
PROBE_IPS="$(cfg NET_FAILOVER_PROBE_IPS "${PROBE_IPS_DEFAULT}")"
FAIL_STRIKES="$(cfg NET_FAILOVER_FAIL_STRIKES "${FAIL_STRIKES_DEFAULT}")"
RESTORE_STRIKES="$(cfg NET_FAILOVER_RESTORE_STRIKES "${RESTORE_STRIKES_DEFAULT}")"
FAILOVER_METRIC="$(cfg NET_FAILOVER_METRIC "${FAILOVER_METRIC_DEFAULT}")"
# Test runs point this at a scratch path so a rehearsal never pollutes the
# real strike counters or the saved-metric record.
STATE_FILE="$(cfg NET_FAILOVER_STATE_FILE "${STATE_FILE}")"

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
# 2026-09-05 (operator directive): ops-health, NOT hot-alerts -- see the
# identical note in runner-health-watchdog.sh. Uplink failover is
# infrastructure health, not a VIP/TFR flight alert.
NTFY_OPS="$(cfg NTFY_OPS_TOPIC ops-health)"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"
OPERATOR_EMAIL="$(read_env_var OPERATOR_EMAIL "${ENV_FILE}")"
# NOTE: deliberately not bash's ":-" default-value shorthand here -- see
# runner-health-watchdog.sh's identical note. scrub-public-tree.py's
# EMAIL_RE treats a hyphen as a valid local-part character, so a hyphen
# sitting directly against the default address gets consumed into the
# match and fails a real push-public.sh run. This comment likewise never
# writes the default address immediately after a hyphen.
if [[ -z "${OPERATOR_EMAIL}" ]]; then
    OPERATOR_EMAIL="csexecutiveservices@gmail.com"
fi

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-5}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: rotating_light" \
        -H "X-Email: ${OPERATOR_EMAIL}" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 \
        || log "warn" "ntfy_send failed (base=${NTFY_BASE} topic=${NTFY_OPS} token_set=$([[ -n "${NTFY_TOKEN}" ]] && echo yes || echo no))"
}

# --- state -------------------------------------------------------------
# Flat key=value; same shape as the other watchdogs in this directory.
#   fail_streak / ok_streak : consecutive primary-probe results
#   failed_over             : 0|1
#   saved_backup_metric     : the backup's own configured metric, captured
#                             BEFORE we overwrite it, so restore puts back
#                             the real value instead of a hardcoded guess
#   last_alert_epoch        : ntfy cooldown
state_get() {
    local key="$1" def="$2"
    [[ -f "${STATE_FILE}" ]] || { echo "${def}"; return; }
    local v
    v="$(grep -m1 "^${key}=" "${STATE_FILE}" 2>/dev/null | cut -d= -f2-)"
    [[ -n "${v}" ]] && echo "${v}" || echo "${def}"
}

state_set() {
    local key="$1" val="$2" tmp
    tmp="${STATE_FILE}.tmp"
    touch "${STATE_FILE}"
    grep -v "^${key}=" "${STATE_FILE}" 2>/dev/null > "${tmp}"
    echo "${key}=${val}" >> "${tmp}"
    mv "${tmp}" "${STATE_FILE}"
}

# --- probing -----------------------------------------------------------
# Success if ANY probe IP answers over $1. ICMP first (cheap, no
# handshake); TCP:443 fallback covers networks that drop ICMP.
probe_iface() {
    local ifc="$1" ip
    for ip in ${PROBE_IPS}; do
        if ping -c1 -W2 -I "${ifc}" "${ip}" >/dev/null 2>&1; then
            return 0
        fi
    done
    for ip in ${PROBE_IPS}; do
        if curl -sk --interface "${ifc}" --max-time 5 -o /dev/null \
                "https://${ip}/" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

iface_has_carrier() {
    local ifc="$1"
    [[ "$(cat "/sys/class/net/${ifc}/operstate" 2>/dev/null)" == "up" ]]
}

current_default_iface() {
    ip route show default 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}'
}

configured_metric() {
    nmcli -g ipv4.route-metric connection show "$1" 2>/dev/null
}

# Reactivating ONLY the backup connection applies its new metric without
# touching the primary -- the primary's route stays exactly as it is, so
# a still-working-but-slower primary is never torn down underneath us.
apply_backup_metric() {
    local metric="$1"
    nmcli connection modify "${BACKUP_CON}" ipv4.route-metric "${metric}" 2>&1 \
        || { log "error" "nmcli modify failed for '${BACKUP_CON}'"; return 1; }
    nmcli connection up "${BACKUP_CON}" >/dev/null 2>&1 \
        || { log "error" "nmcli up failed for '${BACKUP_CON}'"; return 1; }
    return 0
}

maybe_alert() {
    local title="$1" msg="$2" now last
    now=$(date +%s)
    last="$(state_get last_alert_epoch 0)"
    if (( now - last >= ALERT_COOLDOWN_SECS )); then
        ntfy_send "${title}" "${msg}" 5
        state_set last_alert_epoch "${now}"
    else
        log "info" "alert suppressed by cooldown ($(( ALERT_COOLDOWN_SECS - (now - last) ))s left): ${title}"
    fi
}

print_status() {
    echo "primary:      ${PRIMARY_CON} (${PRIMARY_IF}) metric=$(configured_metric "${PRIMARY_CON}")"
    echo "backup:       ${BACKUP_CON} (${BACKUP_IF}) metric=$(configured_metric "${BACKUP_CON}")"
    echo "default via:  $(current_default_iface)"
    echo "failed_over:  $(state_get failed_over 0)"
    echo "fail_streak:  $(state_get fail_streak 0)"
    echo "ok_streak:    $(state_get ok_streak 0)"
    echo "saved_backup_metric: $(state_get saved_backup_metric '(unset)')"
}

if [[ "${1:-}" == "--status" ]]; then
    print_status
    exit 0
fi

# Single instance -- a 60s timer must never stack runs while an nmcli
# reactivation is in flight.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "info" "another run holds the lock; skipping this cycle"
    exit 0
fi

failed_over="$(state_get failed_over 0)"

primary_ok=1
probe_iface "${PRIMARY_IF}" || primary_ok=0
backup_ok=1
probe_iface "${BACKUP_IF}" || backup_ok=0

if [[ "${primary_ok}" -eq 1 ]]; then
    fail_streak=0
    ok_streak=$(( $(state_get ok_streak 0) + 1 ))
else
    ok_streak=0
    fail_streak=$(( $(state_get fail_streak 0) + 1 ))
fi
state_set fail_streak "${fail_streak}"
state_set ok_streak "${ok_streak}"

log "info" "primary=${PRIMARY_IF}:$([[ ${primary_ok} -eq 1 ]] && echo ok || echo DOWN) \
backup=${BACKUP_IF}:$([[ ${backup_ok} -eq 1 ]] && echo ok || echo DOWN) \
default_via=$(current_default_iface) failed_over=${failed_over} \
fail_streak=${fail_streak} ok_streak=${ok_streak}"

# --- both down: upstream/ISP problem, not a path-selection problem -----
# Flipping the route cannot help here, and doing it anyway would just
# hide which link is really at fault. Alert only.
if [[ "${primary_ok}" -eq 0 && "${backup_ok}" -eq 0 ]]; then
    if (( fail_streak >= FAIL_STRIKES )); then
        maybe_alert "NET: BOTH uplinks down" \
            "Neither ${PRIMARY_IF} nor ${BACKUP_IF} can reach any of: ${PROBE_IPS}. This is an upstream/ISP-level outage, not a failover case -- no route change made. carrier: ${PRIMARY_IF}=$(iface_has_carrier "${PRIMARY_IF}" && echo up || echo down), ${BACKUP_IF}=$(iface_has_carrier "${BACKUP_IF}" && echo up || echo down)."
    fi
    exit 0
fi

# --- fail over ---------------------------------------------------------
if [[ "${failed_over}" -eq 0 && "${primary_ok}" -eq 0 && "${backup_ok}" -eq 1 ]]; then
    if (( fail_streak < FAIL_STRIKES )); then
        log "info" "primary down (${fail_streak}/${FAIL_STRIKES}) -- not acting yet"
        exit 0
    fi
    saved="$(configured_metric "${BACKUP_CON}")"
    [[ -n "${saved}" ]] || saved="600"
    state_set saved_backup_metric "${saved}"
    log "warn" "primary ${PRIMARY_IF} down ${fail_streak}x and backup ${BACKUP_IF} healthy -- failing over (${BACKUP_CON}: ${saved} -> ${FAILOVER_METRIC})"
    if apply_backup_metric "${FAILOVER_METRIC}"; then
        state_set failed_over 1
        sleep 3
        maybe_alert "NET: failed over to ${BACKUP_IF}" \
            "Primary uplink ${PRIMARY_IF} (${PRIMARY_CON}) failed ${fail_streak} consecutive upstream probes with carrier $(iface_has_carrier "${PRIMARY_IF}" && echo UP || echo down). Switched default route to ${BACKUP_IF} (${BACKUP_CON}) by setting its metric ${saved} -> ${FAILOVER_METRIC}. Default now via: $(current_default_iface). Will fail back automatically after ${RESTORE_STRIKES} consecutive good primary probes."
    else
        maybe_alert "NET: FAILOVER FAILED" \
            "Primary ${PRIMARY_IF} is down and backup ${BACKUP_IF} is healthy, but the nmcli metric change on '${BACKUP_CON}' did not apply. Manual intervention needed -- see ${LOG_FILE}."
    fi
    exit 0
fi

# --- fail back ---------------------------------------------------------
if [[ "${failed_over}" -eq 1 && "${primary_ok}" -eq 1 ]]; then
    if (( ok_streak < RESTORE_STRIKES )); then
        log "info" "primary recovered (${ok_streak}/${RESTORE_STRIKES}) -- holding on backup"
        exit 0
    fi
    saved="$(state_get saved_backup_metric 600)"
    log "info" "primary ${PRIMARY_IF} healthy ${ok_streak}x -- failing back (${BACKUP_CON}: ${FAILOVER_METRIC} -> ${saved})"
    if apply_backup_metric "${saved}"; then
        state_set failed_over 0
        sleep 3
        ntfy_send "NET: restored to ${PRIMARY_IF}" \
            "Primary uplink ${PRIMARY_IF} (${PRIMARY_CON}) passed ${ok_streak} consecutive probes. Restored ${BACKUP_CON} metric to ${saved}; default now via: $(current_default_iface)." 3
        state_set last_alert_epoch "$(date +%s)"
    else
        maybe_alert "NET: FAIL-BACK FAILED" \
            "Primary ${PRIMARY_IF} recovered but restoring '${BACKUP_CON}' to metric ${saved} did not apply -- the box is still on the backup uplink. See ${LOG_FILE}."
    fi
    exit 0
fi

exit 0
