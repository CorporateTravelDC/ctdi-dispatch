#!/usr/bin/env bash
# config/dns-lock/apply.sh -- 2026-11-27 (operator): Pi-hole -> Unbound is the ONLY DNS path, on every uplink and for
# every tailnet peer, and Unbound forwards only THIS tailnet's MagicDNS names to Tailscale. Run as root (sudo), from
# the signed checkout. README.md beside this file explains the design.
#
#   --unbound    render 60-tailscale-magicdns.conf with this tailnet's suffix (from `tailscale status --json`), check
#                it with unbound-checkconf, install it, restart Unbound, and resolve this host's own MagicDNS name
#   --audit      install the nft table with verdict accept: bypassers are counted and logged (UID), nothing blocked;
#                the tailnet port-53 redirect to Pi-hole is live
#   --enforce    the same table with verdict reject: only Unbound reaches outside DNS
#   --status     counters, mode, and whether the unit is active
#   --remove     unload the table, disable and remove the unit and the rendered file
# Prints counts and names only; never a query, a payload or a secret.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NFT=/usr/sbin/nft
NFT_FILE=/etc/nftables.d/ctdc-dnslock.nft
UNIT=corporatetraveldc-dnslock.service
UNIT_FILE=/etc/systemd/system/$UNIT
UB_CONF=/etc/unbound/conf.d/60-tailscale-magicdns.conf
die() { echo "dns-lock: $*" >&2; exit 1; }
(( EUID == 0 )) || die "run as root (sudo)"

install_table() {   # $1 = accept | reject
  local verdict="$1" tmp
  tmp="$(mktemp)"; trap 'rm -f "$tmp"' RETURN
  sed "s/@VERDICT@/${verdict}/" "$HERE/ctdc-dnslock.nft" > "$tmp"
  grep -q '@VERDICT@' "$tmp" && die "verdict placeholder left in the rendered table"
  "$NFT" -c -f "$tmp" || die "nft syntax check failed; nothing installed"
  install -d -m 0755 /etc/nftables.d
  install -m 0644 -o root -g root "$tmp" "$NFT_FILE"
  install -m 0644 -o root -g root "$HERE/$UNIT" "$UNIT_FILE"
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null 2>&1
  systemctl restart "$UNIT"
  echo "dns-lock: table loaded, verdict for bypassers = ${verdict}"
}

status() {
  echo "dns-lock: unit $(systemctl is-active "$UNIT" 2>/dev/null || true), mode $(grep -oE 'name "dns_bypass" (accept|reject)' "$NFT_FILE" 2>/dev/null | awk '{print $NF}' || echo none)"
  "$NFT" list counter inet ctdc_dnslock dns_bypass 2>/dev/null | grep -oE 'packets [0-9]+' | sed 's/^/   bypass /' || echo "   (table not loaded)"
  "$NFT" list counter inet ctdc_dnslock dns_tailnet_redirect 2>/dev/null | grep -oE 'packets [0-9]+' | sed 's/^/   tailnet redirect /' || true
}

unbound_zone() {
  local js suffix tmp
  js="$(mktemp)"; tmp="$(mktemp)"; trap 'rm -f "$js" "$tmp"' RETURN
  tailscale status --json > "$js" 2>/dev/null || die "tailscale status failed"
  suffix="$(python3 "$HERE/tailnet_suffix.py" "$js")" || die "no MagicDNS suffix in tailscale status"
  [[ "$suffix" =~ ^[a-z0-9-]+\.ts\.net$ ]] || die "unexpected MagicDNS suffix shape; nothing changed"
  sed "s/@TAILNET_SUFFIX@/${suffix}/" "$HERE/60-tailscale-magicdns.conf.in" > "$tmp"
  [[ -f "$UB_CONF" ]] && cp -a "$UB_CONF" "${UB_CONF}.bak-$(date -u +%Y%m%dT%H%MZ)"
  install -m 0644 -o root -g root "$tmp" "$UB_CONF"
  if ! unbound-checkconf >/dev/null; then
    ls -t "${UB_CONF}".bak-* 2>/dev/null | head -1 | xargs -r -I{} cp -a {} "$UB_CONF"
    die "unbound-checkconf failed; previous config restored, Unbound not restarted"
  fi
  systemctl restart unbound
  sleep 2
  local me; me="$(python3 "$HERE/tailnet_suffix.py" "$js" --self)"
  if [[ -n "$(dig +short +time=3 +tries=1 "$me" @127.0.0.1 2>/dev/null)" ]]; then
    echo "dns-lock: Unbound forwards only this tailnet's names; this host's MagicDNS name resolves"
  else
    echo "dns-lock: WARNING: this host's MagicDNS name did not resolve through Pi-hole after the change"
  fi
}

case "${1:-}" in
  --unbound) unbound_zone ;;
  --audit)   install_table accept; status ;;
  --enforce) install_table reject; status ;;
  --status)  status ;;
  --remove)  systemctl disable --now "$UNIT" 2>/dev/null || true
             "$NFT" delete table inet ctdc_dnslock 2>/dev/null || true
             rm -f "$NFT_FILE" "$UNIT_FILE"; systemctl daemon-reload; echo "dns-lock: removed" ;;
  *) die "usage: apply.sh --unbound | --audit | --enforce | --status | --remove" ;;
esac
