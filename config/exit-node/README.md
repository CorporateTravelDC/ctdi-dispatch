# Exit-node firewall: metered cap (POLICY) + encrypted-DNS-by-IP drops (OPT-IN)

Operator 2026-10-24 ~00:00 ET: the driver phone now runs Tailscale always-on
with a FORCED exit node = this Raspberry Pi. Everything the phone sends to
the internet is forwarded by this box, out of whichever uplink is active:
`wld0` (Wi-Fi, primary) or `enu1` (Google Fi USB tether, metered failover:
100 GB fast, then 256 kbps, $10/GB override).

This folder holds one dedicated nftables table, `inet ctdc_exitnode`, that
only ever matches packets this box FORWARDS between `tailscale0` and an
uplink. It never touches the box's own traffic (SWIM, feeders, backups ride
the input/output hooks, which this table has no chains on) and never names
`wld0`. Being its own table, `apply.sh --apply` / `--revert` cannot collide
with firewalld's `inet firewalld` table or tailscale's `ts-*` chains.

Two parts, one installer:

| part | file | status |
|---|---|---|
| A. metered 2 Mbps cap | `metered-cap.nft` | **POLICY -- always rendered** |
| B. encrypted-DNS-by-IP drops | `aggressive/encrypted-dns-ip.nft` | **OPT-IN -- only with `--with-aggressive`** |

Companion (DNS-side, same OPT-IN stance):
[`config/pihole/profiles/aggressive-encrypted-dns/`](../pihole/profiles/aggressive-encrypted-dns/README.md)
blocks DoH resolver *hostnames* and strips HTTPS/ECH records. That profile
cannot see an app that uses a hard-coded resolver IP; part B here is the
firewall half that closes that gap.

## POLICY (required when the phone uses this box as its exit node)

**Cap: 2 Mbps TOTAL for phone traffic forwarded across `enu1`.** Operator
decision 2026-10-24: during a failover the phone rides the hotspot, capped so
SWIM keeps room. This is the **second exception** to the standing
"hotspot = SWIM/operational only" rule; the first is the Proton off-site
backup at 2 Mbps (`rclone --bwlimit 250K`). Nothing else is allowed on the
metered link.

### How the cap is implemented: policing with nft `limit`, not tc shaping

`metered-cap.nft` declares one named limit object,
`rate over 250 kbytes/second burst 256 kbytes`, and two forward-hook rules
that reference it: `iifname tailscale0 oifname enu1` (phone upload) and
`iifname enu1 oifname tailscale0` (phone download, the bulk of metered
usage). Sharing the object means 2 Mbps **total**, both directions combined.
Packets over the rate are dropped and counted. The hook runs at
`priority filter - 10`, before firewalld's forward chain; survivors continue
to firewalld's policy exactly as before.

Why policing and not an HTB/TBF qdisc on `enu1`:

- `enu1` is a USB tether. It appears and disappears with the cable/phone;
  a tc qdisc is attached to the netdevice and vanishes with it, so shaping
  would need a udev/NetworkManager dispatcher hook to re-install it on every
  hot-plug. Interface-name matches in an nft ruleset survive the interface
  coming and going -- the rules are simply dormant while `enu1` is absent.
- Shaping the *download* direction properly needs an IFB device and
  ingress redirection on `enu1` -- another moving part on a link that is
  metered precisely when things are already degraded.
- A 2 Mbps cap is a budget guard, not a QoS target: TCP and QUIC both back
  off under loss and settle just under the limit. The cost of policing is
  burstier delivery and a few retransmitted packets (counted against the
  metered quota, negligible at this rate); the benefit is zero extra state.

`tc` is present on the box and unused by this folder (apply.sh prints that).
If the operator later wants smooth shaping, add it as a separate, explicit
step; the nft cap stays as the backstop either way.

### What it does not do

- Does not touch `wld0` (apply.sh refuses to install a rendered ruleset that
  names the primary interface).
- Does not rate-limit this box's own traffic on `enu1` (SWIM, Proton backup).
- Does not change routing or failover; it only matters while `enu1` is the
  uplink carrying forwarded traffic.
- Does not distinguish tailnet peers: every device using this box as an
  exit node shares the one 2 Mbps budget on the metered link. Today that is
  the driver phone only.

## Aggressive add-on: encrypted-DNS-by-IP drops

**OPT-IN -- a more aggressive measure. Not applied by default.**

Rendered only with `--with-aggressive`, into the same table, for packets
forwarded **from** `tailscale0` (tailnet peers using the exit node), whichever
uplink they leave on:

1. **DoT / DoQ**: TCP+UDP 853 and UDP 8853 -> drop.
2. **DoH / DoT to a known public resolver IP**: TCP/UDP 443 whose destination
   is in HaGeZi's DoH/DoT resolver IP list -> drop. The list is
   `https://raw.githubusercontent.com/hagezi/dns-blocklists/main/ips/doh.txt`
   (bare addresses, one per line; ~1.4k IPv4 entries on 2026-10-24, no IPv6
   entries yet -- the IPv6 set is rendered anyway so the rule is ready).
   apply.sh downloads it **at apply time** into `set doh_ip4` /
   `set doh_ip6` (`flags interval`, `auto-merge`, so CIDRs are fine) and prints
   the count. **No copy of the list is committed.**
3. **Logging**: every drop goes through chain `ctdc_doh_drop`, which logs at
   10/minute (burst 20) with prefix `ctdc-doh-ip ` and then drops. The kernel
   log line carries `SRC=` (the tailnet peer that tried) and `DST=`:

       journalctl -k -g 'ctdc-doh-ip ' --since -1h

The v4/v6 set-match rules are additionally scoped to the tailnet peer
ranges `CTDC_TAILNET_PEERS4` (default `100.64.0.0/10`, the Tailscale CGNAT
range) and `CTDC_TAILNET_PEERS6` (default `fd7a:115c:a1e0::/48`); set either
to narrow the scope, never to a single host address.

### Metered-link guard for the download

apply.sh reads `/proc/net/route` and takes the lowest-metric default route as
the active uplink. If that is `enu1`, it does **not** download the list
(hotspot = SWIM/operational only); it reuses the element lists embedded in
the currently installed `/etc/nftables.d/ctdc-exitnode.nft`, or fails with
a clear message if there is none yet. Refresh the set by re-running
`apply.sh --apply --with-aggressive` while `wld0` is the uplink; the boot
unit never downloads anything.

### Trade-offs (read before applying)

- Any app or VPN profile on the phone that is configured with a public DoT/
  DoQ endpoint, or DoH to a bare IP on the list, stops working while it is
  on the exit node ("cannot connect"). That is the point; the symptom is
  visible in the log line above.
- Cloudflare WARP / 1.1.1.1 app and similar "DNS + VPN" apps are on the list
  and will be blocked on 443 to those addresses.
- Plain DNS (53) is untouched: the phone's resolver remains Pi-hole over the
  tailnet (MagicDNS), which is where the hostname-level profile applies.
- The list is only as current as the last apply on `wld0`.

### "No-DNS-lookup" detection -- NOT implemented (reason)

The idea: have Pi-hole's dnsmasq add every answered A/AAAA address to
timeout'd nft sets (`nftset=/#/4#inet#ctdc_exitnode#resolved4,6#inet#ctdc_exitnode#resolved6`)
and LOG (never drop) new forwarded 443/80 connections from `tailscale0` to
an address nobody resolved, prefix `ctdc-nodns `.

Checked on the box 2026-10-24, read-only:

- The system `dnsmasq(8)` man page (dnsmasq 2.92rel2) documents
  `--nftset=/<domain>[/<domain>...]/[(6|4)#[<family>#]<table>#<set>...]`
  ("Domains and subdomains are matched in the same way as --address", and
  `--address` accepts `#` for "all domains"), so the directive and the
  all-domains form exist in upstream dnsmasq >= 2.87.
- **Pi-hole FTL v6.8 embeds `pi-hole-v2.93+30` compiled with features
  `... ipset no-nftset ...`** (`pihole-FTL -vv`), and the binary carries the
  rejection string "recompile with HAVE_NFTSET defined to enable nftset
  directives". A `nftset=` line in `misc.dnsmasq_lines` would therefore make
  FTL refuse its config and DNS would stop for every client -- the opposite
  of a safe opt-in.

So: not implemented. The honest alternatives, for a later decision, are
(a) FTL's `ipset` support (compiled in) plus iptables-nft set matching, which
drags the legacy ipset/xtables layer onto a firewalld box, or (b) a Pi-hole
build with `HAVE_NFTSET`. Neither is done here and nothing in this folder
pretends otherwise.

## Persistence (where things land)

- Ruleset: **`/etc/nftables.d/ctdc-exitnode.nft`** (exact path pinned 2026-10-24;
  the failover watchdog tests for this file to tell the operator whether the
  2 Mbps exit-node cap is in place). apply.sh creates `/etc/nftables.d/` if
  missing. The file begins with `table inet ctdc_exitnode` /
  `delete table inet ctdc_exitnode`, so loading it is an atomic replace of
  our table only.
- Unit: **`/etc/systemd/system/corporatetraveldc-exitnode-fw.service`**
  (oneshot, `RemainAfterExit`, `After=network-online.target tailscaled.service
  firewalld.service nftables.service`, `ConditionPathExists=` the file).
  `ExecStart=nft -f /etc/nftables.d/ctdc-exitnode.nft`, `ExecStop=nft delete
  table inet ctdc_exitnode`.
- **No include line is added to `/etc/sysconfig/nftables.conf`.** On this
  Fedora box the firewall manager is firewalld (`/etc/firewalld` present,
  `firewalld.service` enabled); `nftables.service` is not enabled, and its
  `ExecReload`/`ExecStop` flush the *whole* ruleset. Enabling it just to
  carry an include would be worse than the unit above. apply.sh detects both
  by reading the `multi-user.target.wants` symlinks and warns if
  `nftables.service` is ever enabled (then re-run apply.sh after any manual
  `systemctl reload nftables`).

## How to apply

Root only, from the deployed repo checkout. Dry-run first, always:

    cd /opt/corporatetraveldc/private/ctdi-dispatch-internal/config/exit-node
    sudo bash apply.sh                                  # dry-run: POLICY cap plan, changes nothing
    sudo bash apply.sh --dry-run --with-aggressive      # dry-run incl. the OPT-IN drops (downloads the list to look)
    sudo bash apply.sh --apply                          # POLICY cap only
    sudo bash apply.sh --apply --with-aggressive        # cap + OPT-IN drops (also the refresh command)
    sudo bash apply.sh --revert                         # removes ONLY our table, unit and file

Every command is echoed (dry-run as `would run:`). `--apply` renders the
ruleset, prints it with the element list collapsed to a count, runs
`nft -c -f` on it, writes the file and the unit only if their content
changed, reloads the table only if the file changed or the table is not
loaded, `daemon-reload`s only if the unit changed, then `enable --now`s
the unit. The end of every mode prints the live table summary (counts
only: chains, rules, set sizes), whether the file exists and whether the
unit is enabled, and PASS/FAIL.

Knobs (env, all modes): `CTDC_TAILNET_IF` (tailscale0), `CTDC_METERED_IF`
(enu1), `CTDC_PRIMARY_IF` (wld0, asserted never-matched), `CTDC_CAP_KBYTES`
(250 = 2 Mbps), `CTDC_CAP_BURST_KBYTES` (256), `CTDC_TAILNET_PEERS4/6`.

## Verify

    sudo nft list table inet ctdc_exitnode           # the table, with live counters on each drop rule
    sudo nft list set inet ctdc_exitnode doh_ip4     # aggressive only: the downloaded IPs
    systemctl status corporatetraveldc-exitnode-fw.service
    test -f /etc/nftables.d/ctdc-exitnode.nft && echo cap-installed
    journalctl -k -g 'ctdc-doh-ip ' --since -1h      # aggressive only: who tried DoT/DoH-by-IP

To see the cap bite: while `enu1` is the uplink, run a speed test on the
phone and watch `counter` on the two `forward_metered_cap` rules climb.

## Revert

`sudo bash apply.sh --revert` -- `systemctl disable --now` the unit, `nft
delete table inet ctdc_exitnode`, remove the unit file and the ruleset file,
`daemon-reload`. `/etc/nftables.d/` itself is left in place. firewalld and
tailscale tables are never read or written by any mode.

## Tests

`tests/scripts/test_exit_node_profile.py`: `bash -n`, dry-run plans with PATH
stubs (nft, tc, curl, systemctl, pihole-FTL, sqlite3) asserting nothing is
changed and only reads run, enu1-only cap with no `wld0` anywhere in the
rendered ruleset, aggressive rules only with `--with-aggressive`, the
metered-link download guard, revert planning only our table/unit/file, the
non-root refusal, and an `nft -c -f` syntax check of the rendered ruleset
inside an unprivileged user+net namespace (`unshare -rn`), skipped with a
reason where that is not permitted.

## Default vs opt-in (2026-10-24)

One setting decides whether this is on by default: `CTDC_AGGRESSIVE_PRIVACY` in the deployment's
`/etc/corporatetraveldc/dispatch.env` (parsed, never sourced).

| Deployment | Value | Effect |
|---|---|---|
| Ours (CorporateTravelDC) | `1` | aggressive measures are the default |
| Public repo deployments | `0` (shipped in `config/dispatch.env.example`) | opt-in: nothing aggressive unless you turn it on |

Command-line flags override it for one run: `--with-aggressive` / `--without-aggressive` (exit-node),
`--force-aggressive` (Pi-hole profile). `--revert` always works.
