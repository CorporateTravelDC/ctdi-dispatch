#!/usr/bin/env bash
# config/exit-node/apply.sh -- exit-node firewall for the driver phone's
# forced Tailscale exit node (this box). Dedicated nftables table
# `inet ctdc_exitnode`, never firewalld's or tailscale's. See README.md.
#
# 2026-10-24 (operator 2026-10-24 ~00:00 ET). Root only. Modes:
#   (none) / --dry-run   print exactly what would change, change nothing
#   --apply              render + syntax-check the table, install it to
#                        /etc/nftables.d/ctdc-exitnode.nft and the oneshot
#                        unit corporatetraveldc-exitnode-fw.service, load it
#   --revert             disable the unit, delete ONLY our table, remove ONLY
#                        those two files
#   --with-aggressive    ALSO include aggressive/encrypted-dns-ip.nft (drop
#                        DoT/DoQ 853+8853 and 443 to HaGeZi's DoH IP set,
#                        downloaded at apply time). Without it only the
#                        POLICY metered cap (metered-cap.nft) is rendered.
#   --without-aggressive the opposite. Both override CTDC_AGGRESSIVE_PRIVACY in
#                        dispatch.env (2026-10-24: 1 = on by default, our
#                        deployment; 0 = opt-in, the public-deployment default).
#   --revert --dry-run   plan the revert
# Idempotent and minimal: files are rewritten only when their rendered
# content differs, the table is reloaded only when the file changed or is
# not loaded, daemon-reload only when the unit changed. Every command is
# echoed; dry-run prefixes the ones it would run with "would run:".
#
# Operator knobs (env, honoured in every mode, validated):
#   CTDC_TAILNET_IF (tailscale0)  CTDC_METERED_IF (enu1)  CTDC_PRIMARY_IF (wld0)
#   CTDC_CAP_KBYTES (250 = 2 Mbps) CTDC_CAP_BURST_KBYTES (256)
#   CTDC_TAILNET_PEERS4 (100.64.0.0/10)  CTDC_TAILNET_PEERS6 (fd7a:115c:a1e0::/48)
# Test hooks (tests/scripts/test_exit_node_profile.py), honoured ONLY with
# --dry-run (mirrors PIHOLE_PROFILE_TEST_ALLOW_NONROOT in the Pi-hole profile):
#   CTDC_EXITNODE_TEST_ALLOW_NONROOT=1, CTDC_EXITNODE_NFT_FILE,
#   CTDC_EXITNODE_UNIT_FILE, CTDC_EXITNODE_WANTS_DIR, CTDC_EXITNODE_PROC_ROUTE,
#   CTDC_EXITNODE_RENDER_OUT (copy of the rendered ruleset for nft -c).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
IPLIST_URL='https://raw.githubusercontent.com/hagezi/dns-blocklists/main/ips/doh.txt'
TABLE_FAMILY=inet
TABLE_NAME=ctdc_exitnode
UNIT_NAME='corporatetraveldc-exitnode-fw.service'
NFT_FILE_DEFAULT=/etc/nftables.d/ctdc-exitnode.nft
UNIT_FILE_DEFAULT="/etc/systemd/system/$UNIT_NAME"
WANTS_DIR_DEFAULT=/etc/systemd/system/multi-user.target.wants
PROC_ROUTE_DEFAULT=/proc/net/route

TAILNET_IF=${CTDC_TAILNET_IF:-tailscale0}
METERED_IF=${CTDC_METERED_IF:-enu1}
PRIMARY_IF=${CTDC_PRIMARY_IF:-wld0}
CAP_KBYTES=${CTDC_CAP_KBYTES:-250}
CAP_BURST_KBYTES=${CTDC_CAP_BURST_KBYTES:-256}
TAILNET_PEERS4=${CTDC_TAILNET_PEERS4:-100.64.0.0/10}
TAILNET_PEERS6=${CTDC_TAILNET_PEERS6:-fd7a:115c:a1e0::/48}

usage() {
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
}


# 2026-10-24 (operator): aggressive is the DEFAULT on our deployment and an OPT-IN toggle for anyone deploying the
# public repo. One setting, CTDC_AGGRESSIVE_PRIVACY, in the deployment's dispatch.env (the live /etc copy is
# authoritative; parsed, never sourced). Ours = 1; config/dispatch.env.example ships 0. A command-line flag
# overrides it either way. The environment variable of the same name overrides the file (tests).
ENV_FILE=${CTDC_ENV_FILE:-/etc/corporatetraveldc/dispatch.env}
aggressive_setting() {
    local v="${CTDC_AGGRESSIVE_PRIVACY:-}"
    if [[ -z "$v" && -r "$ENV_FILE" ]]; then v=$(grep -m1 '^CTDC_AGGRESSIVE_PRIVACY=' "$ENV_FILE" | cut -d= -f2-); fi
    [[ "$v" == 1 || "$v" == true || "$v" == yes ]] && echo 1 || echo 0
}
ACTION=apply
DRY=1
AGGR=$(aggressive_setting)
AGGR_SRC="deployment setting CTDC_AGGRESSIVE_PRIVACY=${AGGR}"
explicit=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)         DRY=1 ;;
        --apply)           ACTION=apply;  explicit=1 ;;
        --revert)          ACTION=revert; explicit=1 ;;
        --with-aggressive)    AGGR=1; AGGR_SRC="--with-aggressive" ;;
        --without-aggressive) AGGR=0; AGGR_SRC="--without-aggressive" ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
    esac
done
# --apply / --revert without --dry-run means do it; --dry-run anywhere wins.
if [[ $explicit -eq 1 ]]; then
    for arg in "$@"; do [[ $arg == --dry-run ]] && DRY=1 && break; DRY=0; done
fi

echo "aggressive encrypted-DNS IP rules: $([[ $AGGR -eq 1 ]] && echo INCLUDED || echo not included) (${AGGR_SRC})"

if [[ ${EUID} -ne 0 ]]; then
    if [[ $DRY -eq 1 && ${CTDC_EXITNODE_TEST_ALLOW_NONROOT:-0} == 1 ]]; then
        echo "note: non-root dry-run permitted by CTDC_EXITNODE_TEST_ALLOW_NONROOT=1 (tests only)"
    else
        echo "refusing: apply.sh must run as root (sudo bash $0 ${*:-})" >&2
        exit 1
    fi
fi

# Test path overrides: dry-run only. A real apply/revert ignores them loudly.
NFT_FILE=$NFT_FILE_DEFAULT; UNIT_FILE=$UNIT_FILE_DEFAULT
WANTS_DIR=$WANTS_DIR_DEFAULT; PROC_ROUTE=$PROC_ROUTE_DEFAULT; RENDER_OUT=''
for v in CTDC_EXITNODE_NFT_FILE CTDC_EXITNODE_UNIT_FILE CTDC_EXITNODE_WANTS_DIR CTDC_EXITNODE_PROC_ROUTE CTDC_EXITNODE_RENDER_OUT; do
    [[ -n ${!v:-} ]] || continue
    if [[ $DRY -eq 1 ]]; then
        case $v in
            CTDC_EXITNODE_NFT_FILE)   NFT_FILE=${!v} ;;
            CTDC_EXITNODE_UNIT_FILE)  UNIT_FILE=${!v} ;;
            CTDC_EXITNODE_WANTS_DIR)  WANTS_DIR=${!v} ;;
            CTDC_EXITNODE_PROC_ROUTE) PROC_ROUTE=${!v} ;;
            CTDC_EXITNODE_RENDER_OUT) RENDER_OUT=${!v} ;;
        esac
        echo "note: test override $v honoured (dry-run only)"
    else
        echo "note: ignoring test override $v (not a dry-run)"
    fi
done
NFT_DIR=$(dirname "$NFT_FILE")

# --- knob validation (these values are pasted into an nft ruleset) --------
die() { echo "error: $*" >&2; exit 1; }
[[ $TAILNET_IF =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "CTDC_TAILNET_IF: bad interface name"
[[ $METERED_IF =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "CTDC_METERED_IF: bad interface name"
[[ $PRIMARY_IF =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "CTDC_PRIMARY_IF: bad interface name"
[[ $METERED_IF != "$PRIMARY_IF" && $METERED_IF != "$TAILNET_IF" && $PRIMARY_IF != "$TAILNET_IF" ]] || die "tailnet / metered / primary interfaces must differ"
[[ $CAP_KBYTES =~ ^[1-9][0-9]{0,5}$ ]] || die "CTDC_CAP_KBYTES: integer kbytes/second expected"
[[ $CAP_BURST_KBYTES =~ ^[1-9][0-9]{0,5}$ ]] || die "CTDC_CAP_BURST_KBYTES: integer kbytes expected"
[[ $TAILNET_PEERS4 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || die "CTDC_TAILNET_PEERS4: IPv4 CIDR expected"
[[ $TAILNET_PEERS6 =~ ^[0-9A-Fa-f:]+/[0-9]{1,3}$ ]] || die "CTDC_TAILNET_PEERS6: IPv6 CIDR expected"
CAP_MBPS=$(awk -v k="$CAP_KBYTES" 'BEGIN { printf "%.2g", k * 8 / 1000 }')

NFT_BIN=$(command -v nft || true)
[[ -n $NFT_BIN ]] || die "nft is not on PATH (install nftables)"

mode_label="DRY-RUN ($ACTION plan)"; [[ $DRY -eq 1 ]] || mode_label="$ACTION"
[[ $AGGR -eq 1 ]] && mode_label+=" + aggressive (OPT-IN)"
echo "== exit-node firewall: $mode_label =="
echo "   table: $TABLE_FAMILY $TABLE_NAME   file: $NFT_FILE   unit: $UNIT_FILE"
echo "   tailnet if: $TAILNET_IF   metered uplink: $METERED_IF   primary uplink (never matched): $PRIMARY_IF"
echo "   cap: $CAP_KBYTES kB/s = $CAP_MBPS Mbps total (burst $CAP_BURST_KBYTES kB), forwarded $TAILNET_IF <-> $METERED_IF only"

# --- environment (read-only detection, nothing executed) -------------------
fw_dir=no; [[ -d /etc/firewalld ]] && fw_dir=yes
nft_svc=no; [[ -L $WANTS_DIR/nftables.service ]] && nft_svc=yes
fw_svc=no;  [[ -L $WANTS_DIR/firewalld.service ]] && fw_svc=yes
tc_bin=$(command -v tc || echo none)
echo "   env: firewalld config dir: $fw_dir (service enabled: $fw_svc); nftables.service enabled: $nft_svc; nft: $NFT_BIN; tc: $tc_bin (not used, see README)"
if [[ $nft_svc == yes ]]; then
    echo "   WARNING: nftables.service is enabled; its reload/stop flushes the WHOLE ruleset (ours included)."
    echo "            The unit is ordered After=nftables.service; after a manual 'systemctl reload nftables' re-run this script."
fi

# Active uplink = lowest-metric default route (read-only, /proc/net/route).
active_uplink() {
    awk 'NR > 1 && $2 == "00000000" { if (best == "" || $7 + 0 < bm) { best = $1; bm = $7 + 0 } } END { print best }' "$PROC_ROUTE" 2>/dev/null || true
}
UPLINK=$(active_uplink)
echo "   active default uplink: ${UPLINK:-unknown}"

# run CMD...: echo, then execute unless dry-run.
run() {
    if [[ $DRY -eq 1 ]]; then
        echo "   would run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}
# read_cmd CMD...: echo and execute in every mode (read-only commands).
read_cmd() { echo "+ $*" >&2; "$@"; }

table_loaded() { "$NFT_BIN" list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1; }
unit_enabled() { [[ -L $WANTS_DIR/$UNIT_NAME ]]; }

# --- HaGeZi DoH/DoT resolver IP list (aggressive only) ----------------------
DOH4=(); DOH6=()
cached_elements() {   # $1 = set name -> prints one element per line from the installed file
    [[ -r $NFT_FILE ]] || return 0
    awk -v want="set $1 {" '
        $0 ~ "^[[:space:]]*set [a-z0-9_]+ \\{" { inset = (index($0, want) > 0) }
        inset && /^[[:space:]]*elements = \{/ { inel = 1; next }
        inset && inel && /^[[:space:]]*\}/ { inel = 0; inset = 0; next }
        inset && inel { gsub(/[[:space:],]/, ""); if ($0 != "") print }
    ' "$NFT_FILE"
}
load_doh_list() {
    local src tmp skipped=0 line
    if [[ -n $UPLINK && $UPLINK == "$METERED_IF" ]]; then
        echo "   uplink is the METERED interface ($METERED_IF): NOT downloading the IP list (hotspot = SWIM/operational only)"
        mapfile -t DOH4 < <(cached_elements doh_ip4)
        mapfile -t DOH6 < <(cached_elements doh_ip6)
        if [[ ${#DOH4[@]} -eq 0 && ${#DOH6[@]} -eq 0 ]]; then
            die "no cached DoH IP list in $NFT_FILE; re-run when $PRIMARY_IF is the uplink"
        fi
        echo "   reusing cached DoH IP set from $NFT_FILE: ${#DOH4[@]} IPv4, ${#DOH6[@]} IPv6"
        return 0
    fi
    tmp=$(mktemp)
    read_cmd curl -fsS --max-time 60 -o "$tmp" "$IPLIST_URL" || { rm -f "$tmp"; die "download failed: $IPLIST_URL"; }
    while IFS= read -r line; do
        line=${line%%#*}; line=${line//[$'\r\t ']/}
        [[ -z $line ]] && continue
        if [[ $line =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ ]]; then DOH4+=("$line")
        elif [[ $line == *:* && $line =~ ^[0-9A-Fa-f:]+(/[0-9]{1,3})?$ ]]; then DOH6+=("$line")
        else skipped=$((skipped + 1)); fi
    done < "$tmp"
    rm -f "$tmp"
    echo "   HaGeZi DoH/DoT resolver IPs: ${#DOH4[@]} IPv4, ${#DOH6[@]} IPv6 (skipped $skipped non-address lines) from $IPLIST_URL"
    [[ ${#DOH4[@]} -gt 0 ]] || die "downloaded list has no IPv4 entries -- refusing to install an empty set"
}

# --- render -------------------------------------------------------------------
subst_vars() {
    sed -e "s|@TAILNET_IF@|$TAILNET_IF|g" -e "s|@METERED_IF@|$METERED_IF|g" -e "s|@PRIMARY_IF@|$PRIMARY_IF|g" \
        -e "s|@CAP_KBYTES@|$CAP_KBYTES|g" -e "s|@CAP_BURST_KBYTES@|$CAP_BURST_KBYTES|g" -e "s|@CAP_MBPS@|$CAP_MBPS|g" \
        -e "s|@TAILNET_PEERS4@|$TAILNET_PEERS4|g" -e "s|@TAILNET_PEERS6@|$TAILNET_PEERS6|g"
}
emit_elements() {   # $@ = elements -> an `elements = { ... }` block, one per line (diff/cache friendly)
    local n=$# i=0 e
    if [[ $n -eq 0 ]]; then echo "        # (no entries in the downloaded list for this family)"; return; fi
    echo "        elements = {"
    for e in "$@"; do
        i=$((i + 1))
        if [[ $i -lt $n ]]; then echo "            $e,"; else echo "            $e"; fi
    done
    echo "        }"
}
render_fragment() {   # $1 = fragment path (comment lines dropped, @VARS@ substituted, element blocks expanded)
    local line
    while IFS= read -r line; do
        case $line in
            '#'*) continue ;;
            '@DOH_IP4_ELEMENTS@') emit_elements ${DOH4[@]+"${DOH4[@]}"} ;;
            '@DOH_IP6_ELEMENTS@') emit_elements ${DOH6[@]+"${DOH6[@]}"} ;;
            *) printf '%s\n' "$line" | subst_vars ;;
        esac
    done < "$1"
}
render_ruleset() {   # $1 = output path. No timestamps: identical input must render identically.
    {
        echo "# $NFT_FILE_DEFAULT -- rendered by config/exit-node/apply.sh; DO NOT EDIT, re-run apply.sh"
        echo "# source: config/exit-node/metered-cap.nft (POLICY: $CAP_MBPS Mbps cap, forwarded $TAILNET_IF <-> $METERED_IF)"
        [[ $AGGR -eq 1 ]] && echo "# source: config/exit-node/aggressive/encrypted-dns-ip.nft (OPT-IN) + $IPLIST_URL"
        echo "table $TABLE_FAMILY $TABLE_NAME"
        echo "delete table $TABLE_FAMILY $TABLE_NAME"
        echo "table $TABLE_FAMILY $TABLE_NAME {"
        render_fragment "$HERE/metered-cap.nft"
        [[ $AGGR -eq 1 ]] && render_fragment "$HERE/aggressive/encrypted-dns-ip.nft"
        echo "}"
    } > "$1"
}
render_unit() {
    sed -e '/^#/d' -e "s|@NFT_BIN@|$NFT_BIN|g" -e "s|@NFT_FILE@|$NFT_FILE_DEFAULT|g" "$HERE/$UNIT_NAME" > "$1"
}
show_plan() {   # print the rendered ruleset with element lists collapsed to a count
    awk '
        /^[[:space:]]*elements = \{/ { inel = 1; n = 0; print; next }
        inel && /^[[:space:]]*\}/ { printf "            ... (%d entries elided)\n", n; inel = 0; print; next }
        inel { n++; next }
        { print }
    ' "$1" | sed 's/^/   | /'
}

changed_files=0
case "$ACTION" in
apply)
    [[ $AGGR -eq 1 ]] && load_doh_list
    RENDERED=$(mktemp); UNIT_RENDERED=$(mktemp)
    trap 'rm -f "$RENDERED" "$UNIT_RENDERED"' EXIT
    render_ruleset "$RENDERED"
    render_unit "$UNIT_RENDERED"
    [[ -n $RENDER_OUT ]] && { cp "$RENDERED" "$RENDER_OUT"; echo "   rendered ruleset copied to $RENDER_OUT"; }
    echo "   --- rendered $TABLE_FAMILY $TABLE_NAME ---"
    show_plan "$RENDERED"
    echo "   --- rendered $UNIT_NAME ---"
    sed 's/^/   | /' "$UNIT_RENDERED"
    echo "   --- end ---"
    if grep -q "\"$PRIMARY_IF\"" "$RENDERED"; then die "rendered ruleset names the primary uplink $PRIMARY_IF -- refusing"; fi
    if [[ ${EUID} -eq 0 ]]; then
        read_cmd "$NFT_BIN" -c -f "$RENDERED" || die "nft rejected the rendered ruleset (see above); nothing installed"
        echo "   nft -c: syntax OK"
    else
        echo "   nft -c: skipped (non-root test dry-run; the test suite checks syntax in an unprivileged netns)"
    fi

    # 1. ruleset file
    if [[ -r $NFT_FILE ]] && cmp -s "$RENDERED" "$NFT_FILE"; then
        echo "   $NFT_FILE unchanged"
    else
        [[ -d $NFT_DIR ]] || run mkdir -p "$NFT_DIR"
        run install -m 0644 -o root -g root "$RENDERED" "$NFT_FILE"
        changed_files=1
    fi
    # 2. unit file
    unit_changed=0
    if [[ -r $UNIT_FILE ]] && cmp -s "$UNIT_RENDERED" "$UNIT_FILE"; then
        echo "   $UNIT_FILE unchanged"
    else
        run install -m 0644 -o root -g root "$UNIT_RENDERED" "$UNIT_FILE"
        run systemctl daemon-reload
        unit_changed=1
    fi
    # 3. load (atomic: the file deletes and re-adds our table in one transaction)
    if [[ $changed_files -eq 1 ]] || ! table_loaded; then
        run "$NFT_BIN" -f "$NFT_FILE_DEFAULT"
    else
        echo "   table already loaded and file unchanged -- no reload"
    fi
    if [[ $unit_changed -eq 1 ]] || ! unit_enabled; then
        run systemctl enable --now "$UNIT_NAME"
    else
        echo "   $UNIT_NAME already enabled"
    fi
    ;;
revert)
    if [[ -e $UNIT_FILE ]]; then
        run systemctl disable --now "$UNIT_NAME"
    else
        echo "   $UNIT_FILE not present -- unit step skipped"
    fi
    if table_loaded; then
        run "$NFT_BIN" delete table "$TABLE_FAMILY" "$TABLE_NAME"
    else
        echo "   table $TABLE_FAMILY $TABLE_NAME not loaded -- nothing to delete"
    fi
    if [[ -e $UNIT_FILE ]]; then
        run rm -f "$UNIT_FILE"
        run systemctl daemon-reload
    fi
    if [[ -e $NFT_FILE ]]; then
        run rm -f "$NFT_FILE"
    else
        echo "   $NFT_FILE not present -- nothing to remove"
    fi
    echo "   ($NFT_DIR itself is left in place; other includes may live there)"
    ;;
esac

# --- verify (counts only) -----------------------------------------------------
echo "   --- verify ---"
ok=1
listing=$("$NFT_BIN" list table "$TABLE_FAMILY" "$TABLE_NAME" 2>/dev/null || true)
if [[ -n $listing ]]; then
    chains=$(printf '%s\n' "$listing" | grep -cE '^[[:space:]]*chain ' || true)
    rules=$(printf '%s\n' "$listing" | grep -cE '^[[:space:]]+[^#].*(counter|jump [a-z_]+|log prefix)' || true)
    e4=$(printf '%s\n' "$listing" | awk '/set doh_ip4 \{/,/^[[:space:]]*\}/' | { grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]+)?' || true; } | wc -l)
    e6=$(printf '%s\n' "$listing" | awk '/set doh_ip6 \{/,/^[[:space:]]*\}/' | { grep -oE '[0-9A-Fa-f]*:[0-9A-Fa-f:]+(/[0-9]+)?' || true; } | wc -l)
    echo "   live table $TABLE_FAMILY $TABLE_NAME: $chains chains, $rules rules, doh_ip4 $e4 entries, doh_ip6 $e6 entries"
    state=loaded
else
    echo "   live table $TABLE_FAMILY $TABLE_NAME: not loaded"
    state=absent
fi
f_state=absent; [[ -e $NFT_FILE ]] && f_state=present
u_state="not enabled"; unit_enabled && u_state=enabled
echo "   $NFT_FILE: $f_state;  $UNIT_NAME: $u_state"
if [[ $ACTION == apply ]]; then
    [[ $state == loaded && $f_state == present && $u_state == enabled ]] || ok=0
else
    [[ $state == absent && $f_state == absent && $u_state == "not enabled" ]] || ok=0
fi
if [[ $ok -eq 1 ]]; then
    echo "RESULT: PASS"
elif [[ $DRY -eq 1 ]]; then
    echo "RESULT: FAIL (dry-run -- this is the current state, not an error; nothing was changed)"
else
    echo "RESULT: FAIL"
    exit 1
fi
