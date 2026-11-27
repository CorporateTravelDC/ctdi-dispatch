#!/usr/bin/env bash
# config/dns-leak-watch/apply.sh -- 2026-11-27: install / remove DNS leak watch and its root collector
# (docs/DNS_LEAK_WATCH.md). Run as root (sudo). Root never runs code from the checkout: the analyzer is copied to
# /usr/local/libexec/ctdc-dnsleak/ (root-owned) and every root step below runs that installed copy.
#
#   init-conf (--clients "ADDR[,ADDR...]" | --tailnet) --iface IF [--proxy "ADDR[,ADDR...]"] [--group G]
#             [--metered "IF ..."] [--quarantine] [--exempt "HOST,..."] [--always-on "HOST,...|*"]
#             [--p5-topics "TOPIC,..."] [--release-approval "/abs/program" --release-user USER] [--force]
#                  write /etc/ctdc-dnsleak/dnsleak.conf (0640) from the example, validated by the installed copy
#   install        copy the analyzer, render + check + install the nft table, install the units, enable the firewall
#                  unit, the 3-minute watch timer and the daily resolver-list timer, fetch the list once (skipped on a
#                  metered route), run one baseline pass
#   diagnose [N]   for N seconds (default 120) count which client addresses Pi-hole logs queries from while the device
#                  is active: the device itself, a PROXY_CLIENTS address, or this host (the exit-node DNS proxy case)
#   set KEY=VALUE  change one key of the installed config (validated by the installed copy first; nothing else
#                  changes); then run install to apply it
#   release HOST|--all   lift a connection-check quarantine (manual escape)
#   status         unit states and counts
#   remove         stop and remove the units, the table and the installed copy (config, list and reports are kept)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB=/usr/local/libexec/ctdc-dnsleak
CONF=/etc/ctdc-dnsleak/dnsleak.conf
NFT_FILE=/etc/nftables.d/ctdc-dnsleak.nft
UNITS=(ctdc-dnsleak-fw.service ctdc-dnsleak-watch.service ctdc-dnsleak-watch.timer
       ctdc-dnsleak-resolvers.service ctdc-dnsleak-resolvers.timer)
die() { echo "dns-leak-watch: $*" >&2; exit 1; }
(( EUID == 0 )) || die "run as root (sudo)"

copy_lib() {
  install -d -m 0755 -o root -g root "$LIB"
  install -m 0755 -o root -g root "$HERE/dns_leak_watch.py" "$LIB/dns_leak_watch.py"
  install -m 0755 -o root -g root "$HERE/dns-lockdown-check.sh" "$LIB/dns-lockdown-check.sh"
}

init_conf() {
  local clients="" iface="" proxy="" group="root" metered="" tailnet=no quarantine=no exempt="" alwayson="" p5="" rcmd="" ruser="" force=0
  while (( $# )); do
    case "$1" in
      --clients) clients="$2"; shift 2 ;;
      --iface) iface="$2"; shift 2 ;;
      --group) group="$2"; shift 2 ;;
      --metered) metered="$2"; shift 2 ;;
      --proxy) proxy="$2"; shift 2 ;;
      --tailnet) tailnet=yes; shift ;;
      --quarantine) quarantine=alert-then-quarantine; shift ;;
      --exempt) exempt="$2"; shift 2 ;;
      --always-on) alwayson="$2"; shift 2 ;;
      --p5-topics) p5="$2"; shift 2 ;;
      --release-approval) rcmd="$2"; shift 2 ;;
      --release-user) ruser="$2"; shift 2 ;;
      --force) force=1; shift ;;
      *) die "init-conf: unknown option $1" ;;
    esac
  done
  [[ ( -n "$clients" || "$tailnet" == yes ) && -n "$iface" ]] || die "init-conf needs --clients (or --tailnet) and --iface"
  [[ -f "$CONF" && $force -eq 0 ]] && die "$CONF exists (use --force to replace it)"
  install -d -m 0750 -o root -g root /etc/ctdc-dnsleak
  copy_lib
  local tmp; tmp="$(mktemp)"
  sed -e "s|^CLIENTS=.*|CLIENTS=${clients}|" -e "s|^IFACE=.*|IFACE=${iface}|" -e "s|^READ_GROUP=.*|READ_GROUP=${group}|" \
      -e "s|^METERED_IFACES=.*|METERED_IFACES=${metered}|" -e "s|^PROXY_CLIENTS=.*|PROXY_CLIENTS=${proxy}|" \
      -e "s|^TAILNET=.*|TAILNET=${tailnet}|" -e "s|^QUARANTINE=.*|QUARANTINE=${quarantine}|" \
      -e "s|^QUARANTINE_EXEMPT=.*|QUARANTINE_EXEMPT=${exempt}|" -e "s|^ALWAYS_ON_HOSTS=.*|ALWAYS_ON_HOSTS=${alwayson}|" \
      -e "s|^NTFY_P5_TOPICS=.*|NTFY_P5_TOPICS=${p5}|" -e "s|^RELEASE_APPROVAL_CMD=.*|RELEASE_APPROVAL_CMD=${rcmd}|" \
      -e "s|^RELEASE_APPROVAL_USER=.*|RELEASE_APPROVAL_USER=${ruser}|" \
      "$HERE/dnsleak.conf.example" > "$tmp"
  python3 "$LIB/dns_leak_watch.py" render-nft --conf "$tmp" >/dev/null || { rm -f "$tmp"; die "the new config does not validate; nothing written"; }
  install -m 0640 -o root -g root "$tmp" "$CONF"; rm -f "$tmp"
  echo "dns-leak-watch: wrote $CONF (tailnet ${tailnet}, $(tr ',' '\n' <<< "$clients" | grep -c .) extra client address(es), iface ${iface}, quarantine ${quarantine})"
}

install_all() {
  [[ -f "$CONF" ]] || die "$CONF is missing: run init-conf first"
  copy_lib
  local tmp; tmp="$(mktemp)"
  python3 "$LIB/dns_leak_watch.py" render-nft --conf "$CONF" > "$tmp" || { rm -f "$tmp"; die "config invalid"; }
  nft -c -f "$tmp" || { rm -f "$tmp"; die "nft syntax check failed; nothing installed"; }
  install -d -m 0755 /etc/nftables.d
  install -m 0644 -o root -g root "$tmp" "$NFT_FILE"; rm -f "$tmp"
  local u; for u in "${UNITS[@]}"; do install -m 0644 -o root -g root "$HERE/$u" "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  systemctl enable ctdc-dnsleak-fw.service ctdc-dnsleak-watch.timer ctdc-dnsleak-resolvers.timer >/dev/null 2>&1
  systemctl restart ctdc-dnsleak-fw.service
  systemctl start ctdc-dnsleak-resolvers.service || true      # the first list now; it skips itself on a metered route
  systemctl start ctdc-dnsleak-watch.timer ctdc-dnsleak-resolvers.timer
  systemctl start ctdc-dnsleak-watch.service || true
  status
}

set_key() {
  [[ -f "$CONF" ]] || die "$CONF is missing: run init-conf first"
  [[ "${1:-}" =~ ^([A-Z_]{2,40})=(.*)$ && "$1" != *$'\n'* ]] || die "set needs KEY=VALUE (one line)"
  local key="${BASH_REMATCH[1]}" tmp; tmp="$(mktemp)"
  copy_lib
  { grep -v -E "^${key}=" "$CONF" || true; printf '%s\n' "$1"; } > "$tmp"
  python3 "$LIB/dns_leak_watch.py" render-nft --conf "$tmp" >/dev/null || { rm -f "$tmp"; die "$key: the new value does not validate; config unchanged"; }
  install -m 0640 -o root -g root "$tmp" "$CONF"; rm -f "$tmp"
  echo "dns-leak-watch: $key set (run: apply.sh install to apply it)"
}

status() {
  local u; for u in "${UNITS[@]}"; do printf '   %-32s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"; done
  [[ -x "$LIB/dns_leak_watch.py" ]] && python3 "$LIB/dns_leak_watch.py" status --conf "$CONF" || true
}

remove_all() {
  systemctl disable --now ctdc-dnsleak-watch.timer ctdc-dnsleak-resolvers.timer ctdc-dnsleak-fw.service 2>/dev/null || true
  nft delete table inet ctdc_dnsleak 2>/dev/null || true
  local u; for u in "${UNITS[@]}"; do rm -f "/etc/systemd/system/$u"; done
  rm -f "$NFT_FILE"; rm -rf "$LIB"
  systemctl daemon-reload
  echo "dns-leak-watch: removed (config, resolver list and reports are kept)"
}

case "${1:-}" in
  init-conf) shift; init_conf "$@" ;;
  set) shift; set_key "${1:-}" ;;
  install)   install_all ;;
  release)   [[ -x "$LIB/dns_leak_watch.py" ]] || die "not installed"
             [[ -n "${2:-}" ]] || die "release HOST, or release --all"
             python3 "$LIB/dns_leak_watch.py" release --conf "$CONF" --host "$2" ;;
  diagnose)  [[ -x "$LIB/dns_leak_watch.py" ]] || die "not installed"
             python3 "$LIB/dns_leak_watch.py" diagnose --conf "$CONF" --seconds "${2:-120}" ;;
  status)    status ;;
  remove)    remove_all ;;
  *) die "usage: apply.sh init-conf --clients ADDRS --iface IF [--tailnet] [--proxy ADDRS] [--group G] [--metered IFS] [--quarantine] [--exempt HOSTS] [--always-on HOSTS|*] [--p5-topics T,..] [--release-approval PROG --release-user U] [--force] | set KEY=VALUE | install | diagnose [SECONDS] | release HOST|--all | status | remove" ;;
esac
