#!/bin/bash
# scripts/ctdc-oob.sh -- the lockdown out-of-band (OOB) management channel (2026-11-18, operator).
# Root runs the INSTALLED copy (/usr/local/libexec/ctdc/ctdc-oob.sh). Interface-agnostic: every choice
# (candidates, metered plans, never-list, budget action, pins, FIDO rule, break-glass) comes from ONE
# strict config file, /etc/ctdc-oob/oob.json (config/network/oob.example.json documents it). The logic
# lives in scripts/lib/ctdc_oob.py, which prints actions; this script executes them. docs/LOCKDOWN_OOB.md.
#
#   ctdc-oob.sh enter [--reason T] [--dry-run]   lockdown.sh: open the channel (idempotent; a re-run
#                                                re-asserts and never resets a budget)
#   ctdc-oob.sh leave [--reason T] [--dry-run]   restore-network.sh: close it (policy routing and the
#                                                sshd drop-in removed; metered links capped again)
#   ctdc-oob.sh budget-check                     corporatetraveldc-oob-budget.timer, every minute
#   ctdc-oob.sh status | plan                    state and budgets | what enter WOULD install (no changes)
#   ctdc-oob.sh install-keys DIR                 key material from oob-pin-build.sh, filtered by the pins
#   ctdc-oob.sh rebaseline --candidate cN --used-gb N.NN [--as-of YYYY-MM-DDTHH:MM:SSZ]
#                                                a metered link's carrier figure AS OF the time the carrier
#                                                shows (its "cached at"): a cached figure, never live evidence
#   ctdc-oob.sh other-usage --candidate cN --used-gb N.NN [--as-of T]   use of the same allowance this box
#                                                cannot see (a phone on the same plan), added on top
#   ctdc-oob.sh prove-floor [--seconds N]        operator-run proof: pin tailscaled to the candidate a
#                                                lockdown would use, shape it to the floor (256 kbit/s), wait
#                                                for an SSH test from the phone, then put everything back
#   ctdc-oob.sh breakglass TEXT SIG              one GPG-signed break-glass command
#   ctdc-oob.sh breakglass-inbox                 corporatetraveldc-oob-breakglass.path: the drop folder
#   ctdc-oob.sh boot-reset                       ctdc-boot-guard.sh --boot: drop a stale sshd drop-in
#
# While open (operator 2026-11-18): the interface carrying the channel -- WHATEVER it is, satellite included --
# is shaped to a HARD cap (hard_cap_kbit, default 2048 kbit/s each way) with tailscaled's traffic in cake's
# priority tin and everything else in the bulk tin, so SSH over Tailscale stays usable down to ~256 kbit/s
# (a carrier's post-allowance throttle). The data budget is SOFT by default: used up -> alert, the channel
# stays up at the hard cap; once the most conservative of the 3 usage sources says the high-speed allowance
# is gone, the shaping drops to throttled_kbit (just under the floor) so this box's queue, not the carrier's,
# decides who goes first. on_budget: cut disconnects instead (explicit only). tailscaled's own sockets are
# marked (nft, cgroup) and routed by fwmark through a dedicated table via the first candidate with carrier
# and a default route (v4 and v6); sshd listens only on the tailnet addresses, public keys only, pinned keys
# only, allowed accounts only. The hard cap comes off only when the channel closes (leave).
# ASCII output only.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
# Test seam (honoured only when NOT root): CTDC_OOB_TEST_ROOT=<dir> puts every path under <dir>, takes
# the facts from <dir>/facts.json (and vnstat from <dir>/vnstat/), skips the integrity self-check, and
# ECHOES every system change ("TEST: ...") instead of making it.
TR=""
if [[ -n "${CTDC_OOB_TEST_ROOT:-}" && $EUID -ne 0 ]]; then TR="${CTDC_OOB_TEST_ROOT}"; fi

INSTALLED=0; [[ -f "${SELF_DIR}/.ctdc-installed" ]] && INSTALLED=1
if [[ -z "$TR" ]]; then
    if (( INSTALLED )); then
        selfcheck() { "${SELF_DIR}/installed-check.sh" "$@"; }
    else
        selfcheck() { local t; for t in "$@"; do "${REPO_ROOT}/scripts/verify-manifest.sh" "$t" || return 1; done; }
    fi
    CHECK=(scripts/ctdc-oob.sh scripts/lib/ctdc_oob.py)
    # the deployment's config and pins, when the root install recorded them (they are private, optional)
    for c in config/network/oob.json config/network/oob-pinned-fingerprints; do
        (( INSTALLED )) && grep " ${c}\$" "${SELF_DIR}/.ctdc-installed" >/dev/null 2>&1 && CHECK+=("$c")
    done
    if ! selfcheck "${CHECK[@]}"; then
        echo "ctdc-oob: INTEGRITY CHECK FAILED -- refusing to run" >&2
        exit 1
    fi
fi
if (( INSTALLED )); then LIB="${SELF_DIR}/ctdc_oob.py"; else LIB="${REPO_ROOT}/scripts/lib/ctdc_oob.py"; fi

OPERATOR="${CTDC_OPERATOR:-corporatetraveldc}"     # the default allowed account (lockdown.sh's CTDC_USER)
ETC="${TR}/etc/ctdc-oob"
CONFIG="${ETC}/oob.json"
PINS="${ETC}/pinned-fingerprints"
KEY_DIR="${ETC}/keys"                               # gpg-<role>.asc, installed by install-keys
AUTH_DIR="${ETC}/authorized_keys.d"                 # one file per allowed account
OVERRIDES="${ETC}/baseline-overrides.json"
if (( ! INSTALLED )) && [[ -z "$TR" ]]; then        # checkout copy: status/plan by the operator
    CONFIG="${REPO_ROOT}/config/network/oob.json"; PINS="${REPO_ROOT}/config/network/oob-pinned-fingerprints"
fi
STATE_DIR="${TR}/var/lib/ctdc-oob"
STATE="${STATE_DIR}/state.json"
NONCES="${STATE_DIR}/breakglass-nonces"
INBOX="${STATE_DIR}/inbox"
OUTBOX="${STATE_DIR}/outbox"
BG_LOG="${TR}/var/log/ctdc-oob-breakglass.log"
DROPIN="${TR}/etc/ssh/sshd_config.d/05-ctdc-oob.conf"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

DRY=0; REASON="manual"; USED_GB=""; CAND=""; AS_OF=""; SECONDS_ARG=300
CMD="${1:-}"; shift || true
ARGS=()
while (( $# )); do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --reason) REASON="${2:-manual}"; shift 2 ;;
        --used-gb) USED_GB="${2:-}"; shift 2 ;;
        --candidate) CAND="${2:-}"; shift 2 ;;
        --as-of) AS_OF="${2:-}"; shift 2 ;;
        --seconds) SECONDS_ARG="${2:-}"; shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
REASON="$(tr -cd '[:print:]' <<<"${REASON:0:200}")"

say() { echo "[$(date -u '+%H:%M:%SZ')] $*"; }
logit() { say "$*"; [[ -n "$TR" ]] && return 0; logger -t ctdc-oob -- "$*" 2>/dev/null || true; }
# every system change goes through x: echoed under the test seam, shown under --dry-run
x() {
    if [[ -n "$TR" ]]; then echo "TEST: $*"; return 0; fi
    if (( DRY )); then say "  [DRY-RUN] $*"; return 0; fi
    "$@"
}
PY_NOW=()                                           # the test seam may pin the clock
[[ -n "$TR" && "${CTDC_OOB_TEST_NOW:-}" =~ ^[0-9]{9,11}$ ]] && PY_NOW=(--now "$CTDC_OOB_TEST_NOW")
py() { /usr/bin/python3 "$LIB" --config "$CONFIG" --default-account "$OPERATOR" --overrides "$OVERRIDES" "${PY_NOW[@]}" "$@"; }

[[ -f "$CONFIG" ]] || { [[ "$CMD" == boot-reset || "$CMD" == leave ]] || { echo "ctdc-oob: no config at ${CONFIG} (see config/network/oob.example.json)" >&2; exit 78; }; }
CONFIG_BAD=0; TABLE=3313; RULE_PRIO=5200; MARK=0xcdc; MASK=0xfff; TS_SERVICE=tailscaled.service; SSH_ENABLED=0; BG_ENABLED=0
HARD_CAP_KBIT=2048; FLOOR_KBIT=256; TS_PORT=41641; VPN_PROTO=udp; VPN_IF=tailscale0; RELAY_TCP_PORT=443; SSH_ACCOUNT=""; PF_DEV=""
if [[ -f "$CONFIG" ]]; then
    if ! sc="$(py shell-config)"; then
        echo "ctdc-oob: the config is invalid -- refusing" >&2
        [[ "$CMD" == boot-reset || "$CMD" == leave ]] || exit 78
        CONFIG_BAD=1
    fi
    while IFS='=' read -r k v; do
        case "$k" in
            TABLE|RULE_PRIO|SSH_ENABLED|BG_ENABLED|HARD_CAP_KBIT|FLOOR_KBIT) [[ "$v" =~ ^[0-9]+$ ]] && printf -v "$k" '%s' "$v" ;;
            TS_PORT|RELAY_TCP_PORT) [[ "$v" =~ ^([0-9]{1,5}|-)$ ]] && printf -v "$k" '%s' "$v" ;;
            VPN_PROTO) [[ "$v" == udp || "$v" == tcp ]] && VPN_PROTO="$v" ;;
            VPN_IF) [[ "$v" =~ ^[A-Za-z0-9_.:-]{1,15}$ ]] && VPN_IF="$v" ;;
            MARK|MASK) [[ "$v" =~ ^0x[0-9a-f]+$ ]] && printf -v "$k" '%s' "$v" ;;
            TS_SERVICE) [[ "$v" =~ ^[A-Za-z0-9@_.-]+\.service$ ]] && TS_SERVICE="$v" ;;
            SSH_ACCOUNT) [[ "$v" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] && SSH_ACCOUNT="$v" ;;
        esac
    done <<<"${sc:-}"
fi

env_get() { [[ -f "${ENV_FILE}" ]] && grep -m1 "^$1=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"\r' || true; }
ntfy_send() {   # priority title message -- the root alert path lockdown.sh uses (token on a private fd)
    local prio="$1" title="$2" msg="$3"
    if [[ -n "$TR" ]]; then echo "TEST: ntfy priority ${prio}: ${title}: ${msg}"; return 0; fi
    (( DRY )) && return 0
    local base topic tok="" fd="" auth=() tf="${NTFY_ROOT_TOKEN_FILE:-/etc/corporatetraveldc/ntfy-root.token}"
    base="$(env_get NTFY_BASE_URL)"; base="${base:-http://127.0.0.1:2586}"
    topic="$(env_get NTFY_HOT_TOPIC)"; topic="${topic:-hot-alerts}"
    [[ -r "$tf" ]] && tok="$(head -c 200 "$tf" | tr -d '[:space:]')"
    if [[ -n "$tok" ]]; then exec {fd}<<<"Authorization: Bearer ${tok}"; auth=(-H "@/dev/fd/${fd}"); fi
    curl -sf --max-time 5 "${auth[@]}" -H "Title: ${title}" -H "Priority: ${prio}" -d "${msg}" \
        "${base}/${topic}" >/dev/null 2>&1 || logit "ntfy send failed: ${title}"
    [[ -n "$fd" ]] && exec {fd}<&-; return 0
}

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
facts() {   # -> $WORK/facts.json and $WORK/vnstat/
    if [[ -n "$TR" ]]; then
        cp "${TR}/facts.json" "$WORK/facts.json" 2>/dev/null || echo '{}' >"$WORK/facts.json"
        mkdir -p "$WORK/vnstat"; cp "${TR}"/vnstat/vnstat-*.json "$WORK/vnstat/" 2>/dev/null || true
    else
        py facts --vnstat-dir "$WORK/vnstat" >"$WORK/facts.json" || echo '{}' >"$WORK/facts.json"
    fi
}
fact_gw() {   # 4|6 DEV
    /usr/bin/python3 - "$WORK/facts.json" "$1" "$2" <<'EOF'
import json, sys
print(json.load(open(sys.argv[1])).get("ifaces", {}).get(sys.argv[3], {}).get("gw" + sys.argv[2], ""))
EOF
}
tailnet_addrs() {
    /usr/bin/python3 - "$WORK/facts.json" <<'EOF'
import json, sys
print("\n".join(json.load(open(sys.argv[1])).get("tailnet", [])))
EOF
}
state_prepare() { mkdir -p "$STATE_DIR"; chmod 0711 "$STATE_DIR" 2>/dev/null || true; }   # traverse only: inbox/ outbox/
active() { [[ -f "$STATE" ]] && grep '"active": true' "$STATE" >/dev/null; }

# -- link cap / cut ------------------------------------------------------------------------------
dev_ok() { [[ "$1" =~ ^[A-Za-z0-9_.:-]{1,15}$ ]]; }
capped() {   # DEV RATE: the normal-times cap is on (tc prints 2Mbit for 2mbit; an OOB shape does not count)
    [[ -z "$TR" ]] || return 1
    [[ -f "${STATE_DIR}/shape-${1}" ]] && return 1
    tc qdisc show dev "$1" root 2>/dev/null | grep "cake" | grep -v "diffserv4" | grep -i "bandwidth ${2} " >/dev/null
}
cap_on() {   # DEV RATE
    local dev="$1" rate="$2" ifb="ifb${1:0:12}"
    dev_ok "$dev" || return 1
    capped "$dev" "$rate" && return 0
    x modprobe -q ifb numifbs=0; x modprobe -q sch_cake; x modprobe -q act_mirred; x modprobe -q cls_matchall
    rm -f "${STATE_DIR}/shape-${dev}" 2>/dev/null
    x tc qdisc replace dev "$dev" root cake bandwidth "$rate"
    if [[ -n "$TR" ]] || ! ip link show "$ifb" >/dev/null 2>&1; then x ip link add "$ifb" type ifb; fi
    x ip link set "$ifb" up
    x tc qdisc del dev "$dev" ingress 2>/dev/null
    x tc qdisc add dev "$dev" handle ffff: ingress
    x tc filter add dev "$dev" parent ffff: matchall action mirred egress redirect dev "$ifb"
    x tc qdisc del dev "$ifb" root 2>/dev/null           # an OOB shape's filters go with it
    x tc qdisc add dev "$ifb" root cake bandwidth "$rate"
    say "  cap ON: ${dev} at ${rate} each way"
}
# The OOB hard cap: cake diffserv4 both ways. Egress: tailscaled's packets carry skb priority 1:4 (set in
# the nft table below) = the Voice tin; everything else leaving this device is put in 1:1 = Bulk. Ingress
# (via the ifb): the VPN's transport (config vpn.protocol/vpn.port; Tailscale: WireGuard UDP 41641) -> Voice;
# TCP from the VPN's relay port (vpn.relay_tcp_port; Tailscale DERP: 443, which also matches other HTTPS)
# -> Best Effort; everything else -> Bulk. "-" disables a match. cake gives Voice its share first, so at
# 256 kbit/s the SSH-over-VPN stream is served before anything else on the link.
shaped_oob() {   # DEV KBIT PORT PROTO RELAY: already shaped exactly so (recorded, and the qdiscs still there)
    [[ -z "$TR" ]] || return 1
    [[ "$(cat "${STATE_DIR}/shape-${1}" 2>/dev/null)" == "${2} ${3} ${4} ${5}" ]] || return 1
    tc qdisc show dev "$1" root 2>/dev/null | grep "cake" | grep "diffserv4" >/dev/null || return 1
    tc filter show dev "ifb${1:0:12}" parent 2: 2>/dev/null | grep "matchall" >/dev/null
}
shape_oob() {   # DEV KBIT PORT PROTO RELAY  (PORT / RELAY "-" = no such match)
    local dev="$1" kbit="$2" port="${3:--}" proto="${4:-udp}" relay="${5:--}" ifb="ifb${1:0:12}" pn
    dev_ok "$dev" && [[ "$kbit" =~ ^[0-9]{2,7}$ && "$port" =~ ^([0-9]{1,5}|-)$ && "$relay" =~ ^([0-9]{1,5}|-)$ ]] || return 1
    case "$proto" in udp) pn=17 ;; tcp) pn=6 ;; *) return 1 ;; esac
    shaped_oob "$dev" "$kbit" "$port" "$proto" "$relay" && return 0
    x modprobe -q ifb numifbs=0; x modprobe -q sch_cake; x modprobe -q act_mirred; x modprobe -q cls_matchall; x modprobe -q cls_u32
    x tc qdisc replace dev "$dev" root handle 1: cake bandwidth "${kbit}kbit" diffserv4
    if [[ -n "$TR" ]] || ! ip link show "$ifb" >/dev/null 2>&1; then x ip link add "$ifb" type ifb; fi
    x ip link set "$ifb" up
    x tc qdisc del dev "$dev" ingress 2>/dev/null
    x tc qdisc add dev "$dev" handle ffff: ingress
    x tc filter add dev "$dev" parent ffff: matchall action mirred egress redirect dev "$ifb"
    x tc qdisc del dev "$ifb" root 2>/dev/null           # drops any earlier filters with it
    x tc qdisc add dev "$ifb" root handle 2: cake bandwidth "${kbit}kbit" diffserv4 ingress
    if [[ "$port" != - ]]; then
        x tc filter add dev "$ifb" parent 2: protocol ip prio 1 u32 match ip protocol "$pn" 0xff match ip dport "$port" 0xffff classid 2:4
        x tc filter add dev "$ifb" parent 2: protocol ipv6 prio 2 u32 match ip6 protocol "$pn" 0xff match ip6 dport "$port" 0xffff classid 2:4
    fi
    if [[ "$relay" != - ]]; then
        x tc filter add dev "$ifb" parent 2: protocol ip prio 3 u32 match ip protocol 6 0xff match ip sport "$relay" 0xffff classid 2:2
        x tc filter add dev "$ifb" parent 2: protocol ipv6 prio 4 u32 match ip6 protocol 6 0xff match ip6 sport "$relay" 0xffff classid 2:2
    fi
    x tc filter add dev "$ifb" parent 2: protocol all prio 9 matchall classid 2:1
    if [[ -z "$TR" ]] && (( ! DRY )); then state_prepare; echo "${kbit} ${port} ${proto} ${relay}" >"${STATE_DIR}/shape-${dev}"; fi
    say "  OOB hard cap ON: ${dev} at ${kbit} kbit/s each way, the VPN first"
}
cap_off() {   # DEV
    local dev="$1"
    dev_ok "$dev" || return 1
    [[ -z "$TR" ]] && ! tc qdisc show dev "$dev" root 2>/dev/null | grep cake >/dev/null && ! ip link show "ifb${dev:0:12}" >/dev/null 2>&1 && return 0
    x tc qdisc del dev "$dev" root 2>/dev/null
    x tc qdisc del dev "$dev" ingress 2>/dev/null
    x ip link del "ifb${dev:0:12}" 2>/dev/null
    rm -f "${STATE_DIR}/shape-${dev}" 2>/dev/null
    say "  cap OFF: ${dev}"
}

# -- tailscaled pinned to the OOB device -----------------------------------------------------------
nft_rules() {   # DEV
    cat <<EOF
table inet ctdc_oob
delete table inet ctdc_oob
table inet ctdc_oob {
  chain out {
    type route hook output priority mangle; policy accept;
    socket cgroupv2 level 2 "system.slice/${TS_SERVICE}" meta mark set meta mark or ${MARK} meta priority set 1:4
  }
  chain prio {
    type filter hook postrouting priority mangle; policy accept;
    oifname "$1" meta priority != 1:4 meta priority set 1:1
  }
  chain post {
    type nat hook postrouting priority srcnat; policy accept;
    oifname "$1" meta mark and ${MASK} == ${MARK} masquerade
  }
}
EOF
}
route_plan() {   # DEV -> commands, one per line
    local dev="$1" f g
    for f in 4 6; do
        g="$(fact_gw "$f" "$dev")"
        if [[ -z "$g" ]]; then echo "# no IPv${f} default route on ${dev}: IPv${f} not pinned"; continue; fi
        if [[ "$g" == - ]]; then echo "ip -${f} route replace default dev ${dev} table ${TABLE}"
        else echo "ip -${f} route replace default via ${g} dev ${dev} table ${TABLE}"; fi
        echo "ip -${f} rule add priority ${RULE_PRIO} fwmark ${MARK}/${MASK} table ${TABLE}"
    done
    echo "sysctl -qw net.ipv4.conf.${dev}.rp_filter=2"
}
route_current() { [[ -f "${STATE_DIR}/route-dev" ]] && cat "${STATE_DIR}/route-dev"; }
route_present() {
    [[ -n "$TR" ]] && return 0
    ip -4 rule show priority "$RULE_PRIO" 2>/dev/null | grep "lookup ${TABLE}" >/dev/null && nft list table inet ctdc_oob >/dev/null 2>&1
}
route_on() {   # DEV (idempotent)
    local dev="$1" ln f
    dev_ok "$dev" || return 1
    [[ "$(route_current)" == "$dev" ]] && route_present && return 0
    route_off quiet
    if [[ -z "$TR" ]] && (( ! DRY )); then
        sysctl -n "net.ipv4.conf.${dev}.rp_filter" >"${STATE_DIR}/rpf-prev" 2>/dev/null || echo 1 >"${STATE_DIR}/rpf-prev"
    fi
    while IFS= read -r ln; do
        if [[ "$ln" == \#* ]]; then say "  ${ln#\# }"; continue; fi
        read -r -a f <<<"$ln"
        x "${f[@]}" || { logit "route step failed: ${ln}"; return 1; }
    done < <(route_plan "$dev")
    if [[ -n "$TR" ]]; then echo "TEST: nft -f - (table inet ctdc_oob via ${dev})"
    elif (( DRY )); then say "  [DRY-RUN] nft -f - <<table inet ctdc_oob via ${dev}>>"
    else nft -f - < <(nft_rules "$dev") || { logit "nft rules failed (is ${TS_SERVICE} running?)"; return 1; }; fi
    (( DRY )) || echo "$dev" >"${STATE_DIR}/route-dev"
    logit "tailscaled pinned to ${dev} (fwmark ${MARK}/${MASK} -> table ${TABLE}, priority ${RULE_PRIO})"
}
route_off() {
    local f dev
    for f in 4 6; do
        if [[ -n "$TR" ]] || (( DRY )); then x ip "-$f" rule del priority "$RULE_PRIO"
        else while ip "-$f" rule del priority "$RULE_PRIO" 2>/dev/null; do :; done; fi
        x ip "-$f" route flush table "$TABLE" 2>/dev/null
    done
    if [[ -n "$TR" ]]; then echo "TEST: nft delete table inet ctdc_oob"
    elif (( ! DRY )); then nft delete table inet ctdc_oob 2>/dev/null || true; fi
    dev="$(route_current)"
    if [[ -n "$dev" ]] && dev_ok "$dev" && [[ -f "${STATE_DIR}/rpf-prev" ]]; then
        x sysctl -qw "net.ipv4.conf.${dev}.rp_filter=$(tr -cd '0-2' <"${STATE_DIR}/rpf-prev")"
    fi
    (( DRY )) || rm -f "${STATE_DIR}/route-dev" "${STATE_DIR}/rpf-prev"
    [[ "${1:-}" == quiet ]] || say "  tailscaled policy routing removed"
}

# -- sshd ------------------------------------------------------------------------------------------
keys_build() {   # installed key material -> per-account authorized_keys, ONLY pinned fingerprints
    [[ -f "$PINS" && -f "${KEY_DIR}/ssh.pub" ]] || { logit "no pinned fingerprints or key material -- run oob-pin-build.sh + install-keys"; return 1; }
    (( DRY )) && { say "  [DRY-RUN] rebuild ${AUTH_DIR} from the pins"; return 0; }
    mkdir -p "$AUTH_DIR"; chmod 0755 "$AUTH_DIR"
    py pins-check --pins "$PINS" --keys "${KEY_DIR}/ssh.pub" --out-dir "$AUTH_DIR" | sed 's/^/  authorized: /'
    (( ${PIPESTATUS[0]} == 0 )) || { logit "pins-check accepted no key"; return 1; }
}
sshd_text() {
    local a=() ad
    while read -r ad; do [[ -n "$ad" ]] && a+=(--listen "$ad"); done < <(tailnet_addrs)
    (( ${#a[@]} )) || { echo "ctdc-oob: ${VPN_IF} has no VPN address yet" >&2; return 1; }
    py sshd-config --pins "$PINS" "${a[@]}"
}
sshd_test() {   # CANDIDATE -> sshd -t with it ahead of the live config
    [[ -n "$TR" ]] && { echo "TEST: sshd -t (candidate drop-in)"; return 0; }
    local main; main="$(mktemp)"
    printf 'Include %s\nInclude /etc/ssh/sshd_config\n' "$1" >"$main"
    sshd -t -f "$main"; local rc=$?
    rm -f "$main"; return $rc
}
sshd_on() {
    keys_build || { logit "sshd OOB NOT installed: no pinned key would be accepted (the live sshd is left as it was)"; return 1; }
    local text; text="$(sshd_text)" || { logit "sshd OOB NOT installed yet: no tailnet address (budget-check retries every minute)"; return 1; }
    if [[ -f "$DROPIN" ]] && [[ "$(cat "$DROPIN")" == "$text" ]]; then return 0; fi
    (( DRY )) && { say "  [DRY-RUN] install ${DROPIN}; sshd -t; systemctl reload sshd"; return 0; }
    mkdir -p "$(dirname "$DROPIN")"
    printf '%s\n' "$text" >"${DROPIN}.new"; chmod 0600 "${DROPIN}.new"
    if ! sshd_test "${DROPIN}.new"; then rm -f "${DROPIN}.new"; logit "sshd -t FAILED with the OOB drop-in -- not installed"; return 1; fi
    mv -f "${DROPIN}.new" "$DROPIN"
    x systemctl reload sshd.service
    if [[ -z "$TR" ]] && ! systemctl is-active --quiet sshd.service; then
        rm -f "$DROPIN"; systemctl reload-or-restart sshd.service; logit "sshd not active after the OOB reload -- drop-in removed"; return 1
    fi
    logit "sshd: tailnet addresses only, publickey only, pinned keys only, mode $(py pins-mode --pins "$PINS")"
}
sshd_off() {
    [[ -f "$DROPIN" ]] || return 0
    (( DRY )) && { say "  [DRY-RUN] rm ${DROPIN}; systemctl reload sshd"; return 0; }
    rm -f "$DROPIN"
    x systemctl reload sshd.service
    say "  sshd OOB drop-in removed"
}

# -- actions from ctdc_oob.py --------------------------------------------------------------------------
run_actions() {   # FILE: one tab-separated action per line
    local a b c d e f
    while IFS=$'\t' read -r a b c d e f; do
        case "$a" in
            cap_on) cap_on "$b" "$c" ;;
            cap_off) cap_off "$b" ;;
            shape_oob) shape_oob "$b" "$c" "$d" "$e" "$f" ;;
            disconnect) dev_ok "$b" && { x nmcli device disconnect "$b" >/dev/null 2>&1 || x ip link set "$b" down; logit "cut: ${b} disconnected (on_budget=cut)"; } ;;
            connect) dev_ok "$b" && { x nmcli device connect "$b" >/dev/null 2>&1 || x ip link set "$b" up; } ;;
            route_on) route_on "$b" ;;
            route_off) route_off ;;
            sshd_on) (( SSH_ENABLED )) && sshd_on ;;
            sshd_off) sshd_off ;;
            alert) ntfy_send "$b" "$c" "$d" ;;
            log) logit "$b" ;;
            *) logit "unknown action ignored: ${a:0:40}" ;;
        esac
    done <"$1"
}
step() {   # enter|tick|leave
    facts; state_prepare
    local nw=(); (( DRY )) && nw=(--no-write)
    py "$1" --facts "$WORK/facts.json" --state "$STATE" --vnstat-dir "$WORK/vnstat" --reason "$REASON" "${nw[@]}" >"$WORK/actions" \
        || { logit "ctdc_oob ${1} failed -- nothing changed"; return 1; }
    run_actions "$WORK/actions"
}

do_enter() { say "OOB channel: enter (reason: ${REASON})"; step enter; }
do_leave() {
    say "OOB channel: leave (reason: ${REASON})"
    if [[ -f "$CONFIG" ]] && (( ! CONFIG_BAD )) && step leave; then :
    else logit "leave without a usable config: routing and sshd removed, metered caps NOT re-applied"; route_off; sshd_off; fi
    if [[ -f "$STATE" ]] && (( ! DRY )); then mv -f "$STATE" "${STATE_DIR}/ended-$(date -u +%Y%m%dT%H%M%SZ).json"; fi
    logit "OOB channel closed (${REASON})"
}
do_budget_check() { active || return 0; step tick; }
do_status() {
    facts
    echo "OOB state: $( active && echo ACTIVE || echo inactive )"
    [[ -f "$STATE" ]] && sed 's/^/  /' "$STATE"
    echo "budgets today (and the candidate a lockdown would use now):"
    py budgets --facts "$WORK/facts.json" --vnstat-dir "$WORK/vnstat" | sed 's/^/  /'
    [[ -f "$PINS" ]] && echo "pin mode: $(py pins-mode --pins "$PINS" 2>&1)"
    [[ -f "$DROPIN" ]] && echo "sshd OOB drop-in: present" || echo "sshd OOB drop-in: absent"
    return 0
}
do_plan() {
    facts
    echo "== OOB plan (nothing is changed)"
    py validate
    local dev; dev="$(py budgets --facts "$WORK/facts.json" --vnstat-dir "$WORK/vnstat" | sed -n 's/.*"would_select": "\([^"]*\)", "device": "\([^"]*\)".*/\2/p')"
    echo "device now: ${dev:-none}"
    echo "hard cap while open: ${HARD_CAP_KBIT} kbit/s each way on that device (a candidate may lower it), tailscaled first; designed floor ${FLOOR_KBIT} kbit/s (prove-floor)"
    if [[ -n "$dev" ]]; then
        echo "-- routing:"; route_plan "$dev" | sed 's/^/  /'
        echo "-- nft:"; nft_rules "$dev" | sed 's/^/  /'
        if [[ -z "$TR" ]] && command -v nft >/dev/null; then
            if [[ $EUID -eq 0 ]]; then nft -c -f - < <(nft_rules "$dev") && echo "  nft -c: OK" || echo "  nft -c: FAILED"
            else echo "  nft -c: skipped (needs root)"; fi
        fi
    fi
    echo "-- sshd drop-in:"
    local text t
    if text="$(sshd_text)"; then
        sed 's/^/  /' <<<"$text"
        if [[ $EUID -eq 0 || -n "$TR" ]]; then
            t="$(mktemp)"; printf '%s\n' "$text" >"$t"
            sshd_test "$t" && echo "  sshd -t: OK" || echo "  sshd -t: FAILED"
            rm -f "$t"
        else echo "  sshd -t: skipped (needs root)"; fi
    fi
    if [[ -f "${KEY_DIR}/ssh.pub" ]]; then echo "-- pinned keys:"; py pins-check --pins "$PINS" --keys "${KEY_DIR}/ssh.pub" 2>&1 | sed 's/^/  /'; fi
    echo "-- budgets:"; py budgets --facts "$WORK/facts.json" --vnstat-dir "$WORK/vnstat" | sed 's/^/  /'
}
do_install_keys() {   # DIR from oob-pin-build.sh: ssh.pub + gpg-<role>.asc
    local src="${ARGS[0]:-}" f n
    [[ -d "$src" && -f "$src/ssh.pub" ]] || { echo "usage: ctdc-oob.sh install-keys DIR (oob-pin-build.sh --material-dir)" >&2; return 64; }
    [[ -f "$PINS" ]] || { echo "ctdc-oob: ${PINS} missing -- install the signed pins first" >&2; return 1; }
    local new="$WORK/keys"; mkdir -p "$new"
    head -c 65536 "$src/ssh.pub" | tr -cd '[:print:]\n' >"$new/ssh.pub"
    py pins-check --pins "$PINS" --keys "$new/ssh.pub" >/dev/null || echo "ctdc-oob: WARNING no SSH key passes the pins (break-glass GPG keys may still install)" >&2
    for f in "$src"/gpg-*.asc; do
        [[ -f "$f" && ! -L "$f" ]] || continue
        n="$(basename "$f")"; [[ "$n" =~ ^gpg-[a-z0-9-]{1,32}\.asc$ ]] || continue
        head -c 1048576 "$f" >"$new/$n"
        local want have; want="$(sed -n "s/^gpg ${n:4:-4} [^ ]* openpgp \([0-9A-F]\{40\}\) .*/\1/p" "$PINS")"
        have="$(/usr/bin/python3 "$LIB" openpgp-fpr "$new/$n" 2>/dev/null)"
        if [[ -z "$want" || "$have" != "$want" ]]; then rm -f "$new/$n"; echo "  rejected ${n}: not the pinned fingerprint" >&2; fi
    done
    mkdir -p "$ETC"; rm -rf "${KEY_DIR}.new"; cp -r "$new" "${KEY_DIR}.new"; chmod -R go-w "${KEY_DIR}.new"
    rm -rf "${KEY_DIR}.old"; [[ -d "$KEY_DIR" ]] && mv "$KEY_DIR" "${KEY_DIR}.old"; mv "${KEY_DIR}.new" "$KEY_DIR"; rm -rf "${KEY_DIR}.old"
    say "key material installed: $(wc -l <"${KEY_DIR}/ssh.pub") ssh line(s), $(find "$KEY_DIR" -name 'gpg-*.asc' | wc -l) pinned gpg key(s)"
    keys_build || true
    if active; then facts; sshd_on || true; fi
    return 0
}
do_rebaseline() {   # a carrier figure is a CACHED reading: record it at the time the carrier shows (--as-of)
    [[ "$CAND" =~ ^c[0-9]{1,2}$ && "$USED_GB" =~ ^[0-9]{1,4}(\.[0-9]{1,3})?$ ]] || { echo "usage: ctdc-oob.sh rebaseline --candidate cN --used-gb N.NN [--as-of YYYY-MM-DDTHH:MM:SSZ] (cN = the metered candidate's index)" >&2; return 64; }
    [[ -z "$AS_OF" || "$AS_OF" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || { echo "ctdc-oob: --as-of must be YYYY-MM-DDTHH:MM:SSZ (UTC)" >&2; return 64; }
    facts
    mkdir -p "$ETC"
    /usr/bin/python3 - "$CONFIG" "$OVERRIDES" "$CAND" "$USED_GB" "$WORK/facts.json" "$WORK/vnstat" "$AS_OF" <<'EOF' || return 1
import datetime as dt, json, os, sys
cfg, ovp, key, used, factsp, vdir, as_of = sys.argv[1:8]
c = json.load(open(cfg))["candidates"][int(key[1:])]
if "metered" not in c:
    sys.exit(f"{key} is not a metered candidate")
now = dt.datetime.now(dt.timezone.utc)
if as_of and dt.datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) > now:
    sys.exit("--as-of is in the future")
ov = json.load(open(ovp)) if os.path.isfile(ovp) else {}
e = dict(ov.get(key, {}), baseline_used_gb=float(used))
if as_of:
    # the figure is as of the carrier's own time: the vnstat bytes since THEN are added (hour series)
    e.update(baseline_at=as_of, baseline_vnstat_month_bytes=None)
else:
    facts = json.load(open(factsp))
    dev = c.get("interface") or facts.get("nm_active", {}).get(c.get("connection"))
    vn = json.load(open(os.path.join(vdir, f"vnstat-{dev}.json"))) if dev else {}
    month = [i for i in vn.get("interfaces", []) if i.get("name") == dev][0]["traffic"]["month"][-1]
    e.update(baseline_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), baseline_vnstat_month_bytes=month["rx"] + month["tx"])
    print("NOTE: no --as-of given, so the figure is taken as of NOW; a carrier app's figure is usually cached -- pass its 'as of' time")
ov[key] = e
json.dump(ov, open(ovp + ".new", "w"), indent=1, sort_keys=True); os.chmod(ovp + ".new", 0o644); os.replace(ovp + ".new", ovp)
print(f"re-baselined {key}: carrier figure {used} GB as of {e['baseline_at']}; fold it into the config at the next release")
EOF
    logit "metered candidate ${CAND} re-baselined at ${USED_GB} GB (as of ${AS_OF:-now})"
}
do_other_usage() {   # use of the same allowance this box cannot see (e.g. a phone on the same plan)
    [[ "$CAND" =~ ^c[0-9]{1,2}$ && "$USED_GB" =~ ^[0-9]{1,4}(\.[0-9]{1,3})?$ ]] || { echo "usage: ctdc-oob.sh other-usage --candidate cN --used-gb N.NN [--as-of YYYY-MM-DDTHH:MM:SSZ]" >&2; return 64; }
    [[ -z "$AS_OF" || "$AS_OF" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || { echo "ctdc-oob: --as-of must be YYYY-MM-DDTHH:MM:SSZ (UTC)" >&2; return 64; }
    mkdir -p "$ETC"
    /usr/bin/python3 - "$CONFIG" "$OVERRIDES" "$CAND" "$USED_GB" "$AS_OF" <<'EOF' || return 1
import datetime as dt, json, os, sys
cfg, ovp, key, used, as_of = sys.argv[1:6]
if "metered" not in json.load(open(cfg))["candidates"][int(key[1:])]:
    sys.exit(f"{key} is not a metered candidate")
as_of = as_of or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ov = json.load(open(ovp)) if os.path.isfile(ovp) else {}
ov[key] = dict(ov.get(key, {}), other_usage_gb=float(used), other_usage_at=as_of)
json.dump(ov, open(ovp + ".new", "w"), indent=1, sort_keys=True); os.chmod(ovp + ".new", 0o644); os.replace(ovp + ".new", ovp)
print(f"{key}: other use of the allowance {used} GB as of {as_of} (added on top of what this box counts)")
EOF
    logit "metered candidate ${CAND}: other use ${USED_GB} GB recorded (as of ${AS_OF:-now})"
}
do_prove_floor() {   # operator-run: is SSH over Tailscale usable at the floor on the link a lockdown would use?
    [[ "$SECONDS_ARG" =~ ^[0-9]{2,4}$ ]] && (( SECONDS_ARG >= 30 && SECONDS_ARG <= 1800 )) || { echo "ctdc-oob: --seconds 30..1800" >&2; return 64; }
    if active; then echo "ctdc-oob: the OOB channel is ACTIVE (a real lockdown) -- prove-floor refuses to touch it" >&2; return 1; fi
    facts; state_prepare
    local plan dev port kbit proto relay
    plan="$(py floor-plan --facts "$WORK/facts.json")" || return 1
    /usr/bin/python3 - "$plan" "$WORK" <<'EOF' || return 1
import json, sys
p = json.loads(sys.argv[1])
port = p["port"] if p["port"] is not None else "-"
relay = p["relay_tcp_port"] if p["relay_tcp_port"] is not None else "-"
open(sys.argv[2] + "/floor", "w").write(f"{p['device']} {port} {p['floor_kbit']} {p['protocol']} {relay}\n")
open(sys.argv[2] + "/restore", "w").write("\t".join(p["restore"]) + "\n")
EOF
    read -r dev port kbit proto relay <"$WORK/floor"
    dev_ok "$dev" || return 1
    local ta; ta="$(tailnet_addrs | head -n 1)"
    say "prove-floor: tailscaled pinned to ${dev}, shaped to ${kbit} kbit/s each way, for at most ${SECONDS_ARG} s"
    # put everything back however this ends (Enter, timeout, Ctrl-C, an error)
    PF_DEV="$dev"
    trap 'route_off quiet; run_actions "$WORK/restore"; say "prove-floor: ${PF_DEV} restored"; rm -rf "$WORK"' EXIT
    shape_oob "$dev" "$kbit" "$port" "$proto" "$relay" || return 1
    route_on "$dev" || return 1
    local tgt="${SSH_ACCOUNT:-$OPERATOR}@${ta:-THIS-BOX-TAILNET-ADDRESS}" cli="s""sh"
    echo
    echo "From the PHONE (Tailscale on, Wi-Fi off), run -- it should log in and print the times:"
    echo "  time ${cli} ${tgt} 'echo ok'"
    echo "  time ${cli} ${tgt} 'head -c 32000 /dev/zero' | wc -c     # about 1 s of data at the floor"
    echo
    if [[ -n "$TR" ]]; then return 0; fi
    read -r -t "$SECONDS_ARG" -p "Press Enter when done (auto-restore in ${SECONDS_ARG} s) " _ </dev/tty || true
    echo
    return 0
}

# -- break-glass ----------------------------------------------------------------------------------------
bg_run() {   # TEXT SIG -> runs the command; one log line + one alert per attempt
    local text="$1" sig="$2" out rc cmd reason who res="" th
    (( BG_ENABLED )) || { echo "break-glass commands are disabled in the config"; return 1; }
    state_prepare; mkdir -p "$(dirname "$BG_LOG")"
    out="$(py breakglass --pins "$PINS" --key-dir "$KEY_DIR" --text "$text" --sig "$sig" --nonces "$NONCES" 2>&1)"; rc=$?
    th="$(sha256sum "$text" | cut -c1-16)"
    if (( rc != 0 )); then
        printf '%s REFUSED text=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$th" "$(tr -cd '[:print:]' <<<"${out:0:200}")" >>"$BG_LOG"
        logit "break-glass REFUSED (${out:0:160})"
        ntfy_send 5 "Break-glass command REFUSED" "${out:0:200}"
        echo "$out"; return 1
    fi
    read -r cmd who reason < <(/usr/bin/python3 - "$out" <<'EOF'
import json, sys
d = json.loads(sys.argv[1]); print(d["command"], d["signer"], d["reason"])
EOF
)
    printf '%s ACCEPTED text=%s command=%s signer=%s reason=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$th" "$cmd" "$who" "$reason" >>"$BG_LOG"
    logit "break-glass ACCEPTED: ${cmd} (signer ${who}; ${reason})"
    ntfy_send 5 "Break-glass command: ${cmd}" "Signed by pinned key ${who}. Reason: ${reason}"
    case "$cmd" in
        status) res="$(do_status 2>&1)" ;;
        clear-hold) if [[ -n "$TR" ]]; then res="TEST: ctdc-reboot.sh --clear-hold"; else res="$("${SELF_DIR}/ctdc-reboot.sh" --clear-hold --reason "break-glass: ${reason}" 2>&1)"; fi ;;
        restore-network) REASON="break-glass: ${reason}"; res="$(do_leave 2>&1)" ;;
        end-lockdown) if [[ -n "$TR" ]]; then res="TEST: restore-network.sh"; else res="$("${SELF_DIR}/restore-network.sh" --reason "break-glass: ${reason}" 2>&1)"; fi ;;
    esac
    echo "$res"
}
do_breakglass() {
    local t="${ARGS[0]:-}" s="${ARGS[1]:-}"
    [[ -f "$t" && -f "$s" ]] || { echo "usage: ctdc-oob.sh breakglass TEXT SIG" >&2; return 64; }
    head -c 4097 "$t" >"$WORK/cmd"; head -c 8192 "$s" >"$WORK/sig"
    bg_run "$WORK/cmd" "$WORK/sig"
}
do_breakglass_inbox() {
    [[ -d "$INBOX" ]] || return 0
    mkdir -p "$OUTBOX"; chmod 0755 "$OUTBOX"
    local s n t d
    for s in "$INBOX"/*.cmd.sig; do
        [[ -e "$s" ]] || continue
        n="$(basename "$s" .cmd.sig)"; t="${INBOX}/${n}.cmd"
        [[ "$n" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || { rm -f "$s"; continue; }
        d="$(mktemp -d)"
        # private copies first (size-capped, regular files only), then the originals go
        if [[ -f "$t" && ! -L "$t" && ! -L "$s" ]]; then head -c 4097 "$t" >"$d/cmd"; head -c 8192 "$s" >"$d/sig"; fi
        rm -f "$t" "$s"
        if [[ -s "$d/cmd" ]]; then bg_run "$d/cmd" "$d/sig" >"${OUTBOX}/${n}.result" 2>&1
        else echo "refused: missing or unusual ${n}.cmd" >"${OUTBOX}/${n}.result"; fi
        chmod 0644 "${OUTBOX}/${n}.result"; rm -rf "$d"
    done
}

# -- main ---------------------------------------------------------------------------------------------
case "$CMD" in
    status|plan) ;;
    enter|leave|budget-check|install-keys|rebaseline|other-usage|prove-floor|breakglass|breakglass-inbox|boot-reset)
        [[ -n "$TR" || $EUID -eq 0 ]] || { echo "ctdc-oob: ${CMD} must run as root" >&2; exit 77; } ;;
    *) sed -n '2,37p' "$0" >&2; exit 64 ;;
esac
case "$CMD" in
    enter) do_enter ;;
    leave) do_leave ;;
    budget-check) do_budget_check ;;
    status) do_status ;;
    plan) do_plan ;;
    install-keys) do_install_keys ;;
    rebaseline) do_rebaseline ;;
    other-usage) do_other_usage ;;
    prove-floor) do_prove_floor ;;
    breakglass) do_breakglass ;;
    breakglass-inbox) do_breakglass_inbox ;;
    boot-reset) if [[ -f "$DROPIN" ]]; then rm -f "$DROPIN"; logit "boot: stale sshd OOB drop-in removed (re-asserted after network-online)"; fi ;;
esac
