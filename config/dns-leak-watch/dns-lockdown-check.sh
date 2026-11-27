#!/usr/bin/env bash
# dns-lockdown-check.sh -- does this laptop's DNS go where you think it goes?
#
# Run it on a Linux laptop that uses a home box as its Tailscale exit node (with Pi-hole + a recursive resolver
# there), while you sit on someone else's Wi-Fi. Nine checks, PASS/FAIL each. It changes nothing.
#   1  the exit node is active
#   2  Tailscale owns DNS (systemd-resolved routes "~." -- every name -- to tailscale0)
#   3  a known ad/tracker domain is blocked (your filter answered, not the venue's)
#   4  an app that hard-codes Google DNS is still filtered (the box redirected it)
#   5  the resolver the internet sees is NOT the venue's network
#   6  your traffic leaves from the same place as your lookups
#   7-9 the OFFENSIVE checks: this script tries the bypasses an app could use -- DNS over TLS, DNS over HTTPS to well-
#      known resolvers, DNS over QUIC -- and each one must FAIL to get an answer (a forward defense on the box
#      rejects it). A PASS here means the bypass was refused, not that the internet is down: check 6 proved it is up.
# Addresses are shown to the first two parts only (e.g. 73.132.x.x) so a screenshot proves where, without saying who.
# Needs: tailscale, resolvectl (systemd-resolved), dig, curl. Optional: BLOCKED_DOMAIN (default doubleclick.net);
# kdig (knot-dnsutils) for check 9, which is SKIPPED (not passed) without it.
set -uo pipefail
BLOCKED="${BLOCKED_DOMAIN:-doubleclick.net}"
pass=0; fail=0
mask() {   # first two parts of an IPv4 or IPv6 address; the rest replaced
  local a="$1"
  if [[ "$a" == *:* ]]; then awk -F: '{printf "%s:%s:x:x:x", $1, $2}' <<< "$a"
  elif [[ "$a" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then awk -F. '{printf "%s.%s.x.x", $1, $2}' <<< "$a"
  else printf '%s' "${a:-none}"; fi
}
two() { if [[ "$1" == *:* ]]; then cut -d: -f1-2 <<< "$1"; else cut -d. -f1-2 <<< "$1"; fi; }
check() {  # $1 label, $2 detail, $3 0=pass
  if [[ "$3" == 0 ]]; then printf '  PASS  %-46s %s\n' "$1" "$2"; pass=$((pass + 1))
  else printf '  FAIL  %-46s %s\n' "$1" "$2"; fail=$((fail + 1)); fi
}

wifi="$(ip -4 route show default | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
[[ "$wifi" == tailscale0 ]] && wifi="$(ip -4 route show default table main | awk '$5!="tailscale0"{print $5; exit}')"
venue="$(nmcli -g IP4.DNS device show "${wifi:-wlan0}" 2>/dev/null | head -1)"
[[ -n "$venue" ]] || venue="$(resolvectl dns "${wifi:-wlan0}" 2>/dev/null | awk '{print $NF}' | grep -E '^[0-9]' | head -1)"
echo "on network: ${wifi:-?}   the venue's own DNS server: $(mask "$venue")"
echo

exitline="$(tailscale status 2>/dev/null | grep -m1 'exit node')"
check "1 exit node active" "$( [[ -n "$exitline" ]] && awk '{print $2}' <<< "$exitline")" "$( [[ "$exitline" == *"active; exit node"* ]] && echo 0 || echo 1)"

route="$(resolvectl domain tailscale0 2>/dev/null)"
check "2 Tailscale owns every lookup (~.)" "$(grep -o '~\.' <<< "$route" | head -1)" "$( grep -q '~\.' <<< "$route" && echo 0 || echo 1)"

a="$(dig +short +time=3 +tries=1 "$BLOCKED" | head -1)"
check "3 ${BLOCKED} blocked" "${a:-no answer}" "$( [[ "$a" == 0.0.0.0 || "$a" == "::" || -z "$a" ]] && echo 0 || echo 1)"

b="$(dig +short +time=3 +tries=1 "$BLOCKED" @8.8.8.8 | head -1)"
check "4 hard-coded 8.8.8.8 still filtered" "${b:-no answer}" "$( [[ "$b" == 0.0.0.0 || "$b" == "::" ]] && echo 0 || echo 1)"

r="$(dig +short +time=3 +tries=1 TXT o-o.myaddr.l.google.com @ns1.google.com | tr -d '"' | head -1)"
check "5 resolver the internet sees != venue" "$(mask "$r")" "$( [[ -n "$r" && -n "$venue" && "$(two "$r")" != "$(two "$venue")" ]] && echo 0 || echo 1)"

e="$(curl -s --max-time 8 https://ifconfig.co)"
e4="$(curl -s -4 --max-time 8 https://ifconfig.co)"
same=1; [[ -n "$e4" && -n "$r" && "$(two "$e4")" == "$(two "$r")" ]] && same=0
check "6 traffic leaves where lookups resolve" "$(mask "$e")  (IPv4 $(mask "$e4"))" "$same"

# 7: DoT. A TCP connection to 853 that opens is a bypass path, whether or not an app uses it.
dot=1; timeout 5 bash -c 'exec 3<>/dev/tcp/1.1.1.1/853' 2>/dev/null && dot=0
check "7 DNS over TLS (1.1.1.1:853) refused" "$( [[ $dot == 0 ]] && echo 'CONNECTED (open bypass)' || echo refused)" "$(( 1 - dot ))"

# 8: DoH by address. An answer (JSON with "Answer") from either resolver is a bypass.
doh=""
for u in "https://1.1.1.1/dns-query?name=example.com&type=A" "https://8.8.8.8/resolve?name=example.com&type=A"; do
  curl -s --max-time 6 -H 'accept: application/dns-json' "$u" 2>/dev/null | grep -q '"Answer"' && doh="${doh} $(cut -d/ -f3 <<< "$u")"
done
check "8 DNS over HTTPS (1.1.1.1, 8.8.8.8) refused" "$( [[ -n "$doh" ]] && echo "ANSWERED by${doh}" || echo refused)" "$( [[ -z "$doh" ]] && echo 0 || echo 1)"

# 9: DoQ (UDP 853), only with a client that speaks it.
if command -v kdig >/dev/null 2>&1; then
  q="$(timeout 8 kdig +quic +short +timeout=4 +retry=0 @94.140.14.140 example.com A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)"
  check "9 DNS over QUIC (94.140.14.140:853) refused" "$( [[ -n "$q" ]] && echo 'ANSWERED (open bypass)' || echo refused)" "$( [[ -z "$q" ]] && echo 0 || echo 1)"
else
  printf '  SKIP  %-46s %s\n' "9 DNS over QUIC" "kdig not installed (dnf/apt install knot-dnsutils)"
fi

echo
echo "  ${pass} passed, ${fail} failed"
exit $(( fail > 0 ))
