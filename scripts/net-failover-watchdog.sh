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
#      2026-10-29 correction: NM connectivity checking can add a route
#      penalty to failing devices. The operator requested it enabled in
#      addition to this explicit failover watchdog. Neither repairs SDIO.
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
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

STATE_DIR="/var/lib/corporatetraveldc/net-failover-watch"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${STATE_DIR}/watch.log"
LOCK_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/net-failover-watchdog.lock"
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
#
# SUPERSEDED 2026-10-23: the two paragraphs above were right for the
# Verizon line (unlimited, but shared with other devices on that plan,
# which caused its own issues). From 2026-10-23 the backup is Google Fi
# over USB tether -- METERED: 100 GB high-speed, then 256 kbps, overrides
# $10/GB. Data volume IS now a constraint, enforced outside this script:
# while the default route is on it, heavy jobs defer
# (scripts/lib/heavy_lock.sh) and the Proton backup upload is capped at
# 2 Mbps (scripts/backup/rate_guard.py); SWIM and live operational data
# ride it freely. This script's job is unchanged: fail over when the
# primary is dead, fail back as soon as it recovers.
# 2026-10-06: lowercase is the current, correct profile name (recreated after
# the hard reset); the old capitalised default left `--status` blank.
# 2026-11-18 (operator): the active WiFi profile is "jorransgateway 1" and the hotspot profile is
# renamed "Hockey Puck" (enu1). Three tiers, IPv4 and IPv6 alike: 100 Starlink (planned), 600
# "jorransgateway 1", 1100 "Hockey Puck", 1600 "Corporate Traveler WiFi" (the puck's own WiFi on wld0,
# last resort). dispatch.env sets NET_FAILOVER_PRIMARY_CON / NET_FAILOVER_BACKUP_CON to match.
# 2026-11-27 (operator): the wired switch is the primary -- end0 "Auto Ethernet" (the router today,
# Starlink later on the same switch) at 100, then "jorransgateway 1" (wld0) 600, "Hockey Puck" (enu1)
# 1100, "Corporate Traveler WiFi" 1600. The watchdog now CHAINS the first three: NET_FAILOVER_CHAIN
# lists them best first as "connection@device" separated by commas, and the default route goes to the
# best tier that is answering. Failing down needs FAIL_STRIKES bad probes on the tier in use; moving
# back up needs RESTORE_STRIKES good probes on the better tier. A tier is moved to by setting its
# profile metric to FAILOVER_METRIC (both families); the metric it had is saved and put back when it
# is no longer the one in use. The fourth tier is left to NetworkManager: it shares wld0 with
# "jorransgateway 1", so it is only ever up when that one is not, and its 1600 already puts it last.
# The old NET_FAILOVER_PRIMARY_* / NET_FAILOVER_BACKUP_* keys are ignored (logged once per run).
CHAIN_DEFAULT="Auto Ethernet@end0,jorransgateway 1@wld0,Hockey Puck@enu1"

PROBE_IPS_DEFAULT="1.1.1.1 8.8.8.8 9.9.9.9"
FAIL_STRIKES_DEFAULT=3
RESTORE_STRIKES_DEFAULT=3
FAILOVER_METRIC_DEFAULT=50      # must beat the primary's 600 (and Starlink's planned 100) while failed over
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

CHAIN="$(cfg NET_FAILOVER_CHAIN "${CHAIN_DEFAULT}")"
TIER_CON=(); TIER_IF=()
IFS=',' read -r -a _tiers <<< "${CHAIN}"
for _t in "${_tiers[@]}"; do
    _t="${_t#"${_t%%[![:space:]]*}"}"; _t="${_t%"${_t##*[![:space:]]}"}"
    [[ -n "${_t}" ]] || continue
    if [[ "${_t}" != *@* || -z "${_t%@*}" || ! "${_t##*@}" =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; then
        echo "net-failover-watchdog: NET_FAILOVER_CHAIN entry '${_t}' is not 'connection@device'" >&2
        exit 2
    fi
    TIER_CON+=("${_t%@*}"); TIER_IF+=("${_t##*@}")
done
NTIERS=${#TIER_CON[@]}
if (( NTIERS < 2 )); then
    echo "net-failover-watchdog: NET_FAILOVER_CHAIN needs at least two tiers" >&2
    exit 2
fi
# The helpers in lib/net_failover_routes.sh act on BACKUP_CON / BACKUP_IF (the profile being changed)
# and PRIMARY_IF (the device the default must end up on for a 'failback'); tier_metric sets them per call.
PRIMARY_CON="${TIER_CON[0]}"; PRIMARY_IF="${TIER_IF[0]}"
BACKUP_CON="${TIER_CON[1]}"; BACKUP_IF="${TIER_IF[1]}"
PROBE_IPS="$(cfg NET_FAILOVER_PROBE_IPS "${PROBE_IPS_DEFAULT}")"
FAIL_STRIKES="$(cfg NET_FAILOVER_FAIL_STRIKES "${FAIL_STRIKES_DEFAULT}")"
RESTORE_STRIKES="$(cfg NET_FAILOVER_RESTORE_STRIKES "${RESTORE_STRIKES_DEFAULT}")"
FAILOVER_METRIC="$(cfg NET_FAILOVER_METRIC "${FAILOVER_METRIC_DEFAULT}")"
# 2026-10-24 (operator): the driver phone uses this box as a FORCED Tailscale exit node, so on a
# failover its traffic rides the metered hotspot -- capped at 2 Mbps by config/exit-node
# (operator-applied, root). Every failover page says whether that cap is actually installed.
EXITNODE_CAP_FILE="$(cfg NET_EXITNODE_CAP_FILE /etc/nftables.d/ctdc-exitnode.nft)"
METERED_IFACES="$(cfg NET_METERED_IFACES enu1)"
is_metered() { local m; for m in ${METERED_IFACES}; do [[ "$1" == "$m" ]] && return 0; done; return 1; }
exitnode_note() {   # $1 = the device now carrying the default; says nothing for an unmetered one
    is_metered "$1" || return 0
    if [[ -r "${EXITNODE_CAP_FILE}" || -e "${EXITNODE_CAP_FILE}" ]]; then
        echo "Exit-node traffic (driver phone) is CAPPED at 2 Mbps while on $1."
    else
        echo "WARNING: exit-node cap NOT installed (${EXITNODE_CAP_FILE} missing) -- the driver phone's exit-node traffic is UNCAPPED on the metered $1."
    fi
}
# Test runs point this at a scratch path so a rehearsal never pollutes the
# real strike counters or the saved-metric record.
STATE_FILE="$(cfg NET_FAILOVER_STATE_FILE "${STATE_FILE}")"
LOG_FILE="$(cfg NET_FAILOVER_LOG_FILE "${LOG_FILE}")"

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
    [[ -n "${NTFY_TOKEN}" ]] && authhdr auth_args "${NTFY_TOKEN}"
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
#   ok_streak_N / fail_streak_N : consecutive probe results for tier N (0 = best)
#   boosted                     : the tier moved to by metric, or -1 (tier 0 by its own metric)
#   boost_metric                : the metric the tier in use was given (FAILOVER_METRIC, or 10 less)
#   saved_metric_N              : tier N's own configured metric, captured
#                                 BEFORE we overwrite it, so restore puts back
#                                 the real value instead of a hardcoded guess
#   last_alert_epoch            : ntfy cooldown
#   (failed_over / saved_backup_metric: the two-tier watchdog's keys, carried over once)
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

source "$(dirname "${BASH_SOURCE[0]}")/lib/net_failover_routes.sh"

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
    local i b
    for (( i = 0; i < NTIERS; i++ )); do
        echo "tier $(( i + 1 )):       ${TIER_CON[$i]} (${TIER_IF[$i]}) metric=$(configured_metric "${TIER_CON[$i]}") ok_streak=$(state_get "ok_streak_${i}" 0) fail_streak=$(state_get "fail_streak_${i}" 0)"
    done
    b="$(state_get boosted -1)"
    if (( b >= 0 && b < NTIERS )); then
        echo "in use:       tier $(( b + 1 )) (${TIER_CON[$b]}), its own metric $(state_get "saved_metric_${b}" '(unset)') saved"
    else
        echo "in use:       tier 1 by its own metric"
    fi
    echo "default via:  $(current_default_iface)"
    echo "parked:       $(cfg NET_FAILOVER_PARKED false)"
}

if [[ "${1:-}" == "--status" ]]; then
    print_status
    exit 0
fi

# Single instance -- a 60s timer must never stack runs while an nmcli
# reactivation is in flight.
if ! { exec 9>"${LOCK_FILE}"; }; then
    log "error" "cannot open the single-instance lock"
    exit 1
fi
if ! flock -n 9; then
    log "info" "another run holds the lock; skipping this cycle"
    exit 0
fi

for _k in NET_FAILOVER_PRIMARY_CON NET_FAILOVER_PRIMARY_IF NET_FAILOVER_BACKUP_CON NET_FAILOVER_BACKUP_IF; do
    [[ -z "$(cfg "${_k}" "")" ]] || log "info" "${_k} is set but ignored since 2026-11-27: NET_FAILOVER_CHAIN governs"
done

# tier_metric I METRIC EXPECTED_DEV [failback]: set tier I's profile to METRIC (both families) and verify
# the default then goes via EXPECTED_DEV (the helper's checks: right profile active, live reapply, route).
tier_metric() {
    BACKUP_CON="${TIER_CON[$1]}"; BACKUP_IF="${TIER_IF[$1]}"; PRIMARY_IF="$3"
    apply_backup_metric "$2" "${4:-}"
}

# A tier whose device is up on a DIFFERENT profile (wld0 on "Corporate Traveler WiFi" instead of
# "jorransgateway 1") cannot be moved to: its configured profile is not the one carrying traffic.
tier_eligible() {
    BACKUP_CON="${TIER_CON[$1]}"; BACKUP_IF="${TIER_IF[$1]}"
    ! iface_connected "${BACKUP_IF}" || backup_profile_is_active
}

# 2026-11-27: a state file from the two-tier watchdog (failed_over=1 onto the old backup) is carried over
# as "that tier is in use", so its saved metric is still put back.
if [[ -z "$(state_get boosted '')" ]]; then
    boosted=-1
    if [[ "$(state_get failed_over 0)" -eq 1 ]]; then
        for (( i = 0; i < NTIERS; i++ )); do
            if [[ "${TIER_CON[$i]}" == "Hockey Puck" ]]; then
                boosted=$i; state_set "saved_metric_${i}" "$(state_get saved_backup_metric 1100)"
            fi
        done
    fi
    state_set boosted "${boosted}"
    state_set failed_over 0
fi
boosted="$(state_get boosted -1)"
[[ "${boosted}" =~ ^-?[0-9]+$ ]] && (( boosted < NTIERS )) || boosted=-1

# --- parked ------------------------------------------------------------
# 2026-11-01 (operator): the failover SIM failed, so there is no backup
# uplink for now. NET_FAILOVER_PARKED=true in dispatch.env stops all
# probing and route changes (a dead enu1 otherwise costs every 60s cycle
# its full probe timeouts). A park that lands while moved off tier 1
# restores the moved tier's saved metric once, so the tiers' own metrics
# decide the default route again. Remove the key, or set it to false, to
# resume failover.
if [[ "$(cfg NET_FAILOVER_PARKED false)" == "true" ]]; then
    if (( boosted >= 0 )); then
        saved="$(state_get "saved_metric_${boosted}" 1100)"
        log "warn" "failover parked while on tier $(( boosted + 1 )) -- restoring ${TIER_CON[$boosted]} metric ${FAILOVER_METRIC} -> ${saved}"
        if tier_metric "${boosted}" "${saved}" "${TIER_IF[0]}" failback; then
            state_set boosted -1
        else
            maybe_alert "NET: PARK RESTORE FAILED" \
                "Failover is parked, but restoring '${TIER_CON[$boosted]}' to metric ${saved} did not apply -- the box may still route via ${TIER_IF[$boosted]}. See ${LOG_FILE}."
        fi
    fi
    exit 0
fi

# --- probe every tier --------------------------------------------------
# A device with no carrier is down without spending the probe timeouts on it.
ok=(); summary=""
for (( i = 0; i < NTIERS; i++ )); do
    ok[i]=0
    if iface_has_carrier "${TIER_IF[$i]}" && probe_iface "${TIER_IF[$i]}"; then ok[i]=1; fi
    if (( ok[i] )); then
        state_set "ok_streak_${i}" $(( $(state_get "ok_streak_${i}" 0) + 1 )); state_set "fail_streak_${i}" 0
    else
        state_set "fail_streak_${i}" $(( $(state_get "fail_streak_${i}" 0) + 1 )); state_set "ok_streak_${i}" 0
    fi
    summary+="${TIER_IF[$i]}:$( (( ok[i] )) && echo ok || echo DOWN)($(state_get "ok_streak_${i}" 0)/$(state_get "fail_streak_${i}" 0)) "
done
in_use=$(( boosted >= 0 ? boosted : 0 ))
log "info" "tiers ${summary}in_use=tier$(( in_use + 1 )) default_via=$(current_default_iface)"

# --- choose the tier ---------------------------------------------------
# Up: the best tier above the one in use with RESTORE_STRIKES good probes.
# Down: the tier in use has failed FAIL_STRIKES probes -> the best other tier answering now.
target=${in_use}
for (( i = 0; i < in_use; i++ )); do
    if (( $(state_get "ok_streak_${i}" 0) >= RESTORE_STRIKES )) && tier_eligible "$i"; then target=$i; break; fi
done
if (( target == in_use && $(state_get "fail_streak_${in_use}" 0) >= FAIL_STRIKES )); then
    target=-1
    for (( i = 0; i < NTIERS; i++ )); do
        if (( i != in_use && ok[i] )) && tier_eligible "$i"; then target=$i; break; fi
    done
    if (( target < 0 )); then
        # Every tier is down: an upstream outage, not a path choice. Moving the route cannot help and
        # would hide which link is at fault. Alert only.
        carriers=""
        for (( i = 0; i < NTIERS; i++ )); do carriers+="${TIER_IF[$i]}=$(iface_has_carrier "${TIER_IF[$i]}" && echo up || echo down) "; done
        maybe_alert "NET: ALL uplinks down" \
            "No tier (${TIER_IF[*]}) can reach any of: ${PROBE_IPS}. Upstream outage, not a failover case -- no route change made. carrier: ${carriers}"
        exit 0
    fi
fi
if (( target == in_use )); then
    n="$(state_get "fail_streak_${in_use}" 0)"
    (( n == 0 )) || log "info" "tier $(( in_use + 1 )) ${TIER_IF[$in_use]} down (${n}/${FAIL_STRIKES}) -- not acting yet"
    exit 0
fi

# --- move --------------------------------------------------------------
from_if="${TIER_IF[$in_use]}"; to_if="${TIER_IF[$target]}"
if (( target < in_use )); then
    why="tier $(( target + 1 )) ${to_if} (${TIER_CON[$target]}) passed $(state_get "ok_streak_${target}" 0) consecutive probes"
else
    why="tier $(( in_use + 1 )) ${from_if} (${TIER_CON[$in_use]}) failed $(state_get "fail_streak_${in_use}" 0) consecutive upstream probes with carrier $(iface_has_carrier "${from_if}" && echo UP || echo down)"
fi
if (( target == 0 )); then
    saved="$(state_get "saved_metric_${boosted}" 1100)"
    log "info" "${why} -- back to tier 1 (${TIER_CON[$boosted]}: ${FAILOVER_METRIC} -> ${saved})"
    if tier_metric "${boosted}" "${saved}" "${to_if}" failback; then
        state_set boosted -1
        sleep 3
        ntfy_send "NET: restored to ${to_if}" \
            "${why}. Restored ${TIER_CON[$boosted]} metric to ${saved}; default now via: $(current_default_iface)." 3
        state_set last_alert_epoch "$(date +%s)"
    else
        maybe_alert "NET: FAIL-BACK FAILED" \
            "Tier 1 ${to_if} recovered but restoring '${TIER_CON[$boosted]}' to metric ${saved} did not apply -- the box is still on ${from_if}. See ${LOG_FILE}."
    fi
    exit 0
fi
saved="$(configured_metric "${TIER_CON[$target]}")"
[[ -n "${saved}" && "${saved}" != "${FAILOVER_METRIC}" ]] || saved="$(state_get "saved_metric_${target}" 1100)"
state_set "saved_metric_${target}" "${saved}"
# Moving from one moved-to tier to another: the old one still holds the failover metric until it is put back,
# and two equal defaults are an ECMP coin toss. The new tier takes 10 less, so it wins outright.
boost_m="${FAILOVER_METRIC}"
(( boosted >= 0 )) && [[ "$(state_get boost_metric "${FAILOVER_METRIC}")" == "${FAILOVER_METRIC}" ]] && boost_m=$(( FAILOVER_METRIC - 10 ))
log "warn" "${why} -- moving to tier $(( target + 1 )) ${to_if} (${TIER_CON[$target]}: ${saved} -> ${boost_m})"
if ! tier_metric "${target}" "${boost_m}" "${to_if}"; then
    maybe_alert "NET: FAILOVER FAILED" \
        "${why}; tier $(( target + 1 )) ${to_if} is healthy, but the nmcli metric change on '${TIER_CON[$target]}' did not apply. Manual intervention needed -- see ${LOG_FILE}."
    exit 0
fi
if (( boosted >= 0 && boosted != target )); then
    old="$(state_get "saved_metric_${boosted}" 1100)"
    tier_metric "${boosted}" "${old}" "${to_if}" failback \
        || log "error" "moved to ${to_if}, but restoring ${TIER_CON[$boosted]} to metric ${old} did not apply"
fi
state_set boosted "${target}"
state_set boost_metric "${boost_m}"
sleep 3
title="NET: failed over to ${to_if}"
(( target > in_use )) || title="NET: back up to ${to_if}"
maybe_alert "${title}" \
    "${why}. Default route moved to tier $(( target + 1 )) ${to_if} (${TIER_CON[$target]}) by setting its metric ${saved} -> ${boost_m}. Default now via: $(current_default_iface). Moves back up after ${RESTORE_STRIKES} consecutive good probes on a better tier. $(exitnode_note "${to_if}")"
exit 0
