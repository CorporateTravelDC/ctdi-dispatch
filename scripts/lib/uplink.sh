# scripts/lib/uplink.sh -- which uplink carries the default route, and is it the metered one (2026-10-23).
#
# Operator, 2026-10-23: the failover hotspot (Google Fi over USB tether, enu1) is METERED and
# emergency-only -- 100 GB high-speed, then 256 kbps, overrides $10/GB. On it the box carries SWIM and
# live operational data only, plus ONE exception: the Proton off-site backup upload, capped at 2 Mbps,
# so a grid-down failover never leaves the box more than ~24 h without a live off-site backup.
# Rebuilds, image pulls, downloads and backfills never run on it. Source this file, then:
#
#   uplink_default_iface    # the device the first default route uses (empty if none)
#   uplink_is_metered       # exit 0 when that device is in NET_METERED_IFACES
#
# NET_METERED_IFACES (space-separated, default "enu1") comes from the environment, then dispatch.env.
# UPLINK_DEFAULT_IFACE overrides the route lookup (tests).

uplink_default_iface() {
  if [[ -n "${UPLINK_DEFAULT_IFACE+x}" ]]; then echo "${UPLINK_DEFAULT_IFACE}"; return; fi
  ip route show default 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}'
}

uplink_metered_ifaces() {
  local v="${NET_METERED_IFACES:-}"
  [[ -n "$v" ]] || v="$(grep -m1 '^NET_METERED_IFACES=' "${CTDC_ENV_FILE:-/etc/corporatetraveldc/dispatch.env}" 2>/dev/null | cut -d= -f2-)"
  echo "${v:-enu1}"
}

uplink_is_metered() {
  local dev m
  dev="$(uplink_default_iface)"
  [[ -n "$dev" ]] || return 1
  for m in $(uplink_metered_ifaces); do [[ "$dev" == "$m" ]] && return 0; done
  return 1
}
