Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.
Re-checked 2026-10-15 ~01:15Z against main 1491bf5a (plus the staged batch) and the live box: ~~the backup uplink is
still down~~ (superseded 2026-10-26: the backup `enu1` is up -- `backup=enu1:ok` on 10 of 10 watchdog cycles,
`journalctl --user -u corporatetraveldc-net-failover-watchdog --since -10min` 22:00Z; see the status updates); the mechanism below is unchanged.

# Uplink failover — how it actually works on this box

**There is no bond.** ~~`/proc/net/bonding/` is empty (re-confirmed live).~~
(corrected 2026-10-25: the directory does not exist at all -- the bonding
module is not loaded; `ls /proc/net/bonding/` 21:48Z.)
Anything that calls this setup "bonded" is wrong — that misconception is
why the 2026-09-05 outage was expected to self-heal when it structurally
could not.

## Status 2026-11-18 -- the lockdown OOB management channel

A lockdown now opens an out-of-band management channel. Tailscale is pinned to the first configured
OOB uplink that has carrier and a default route; uplinks on the `never` list are excluded. A metered
uplink runs uncapped within a data budget, and the configured action applies when the budget is used
up. sshd listens on the tailnet addresses only, with pinned public keys only. GPG-signed break-glass
commands are accepted. `restore-network.sh` closes the channel and re-caps metered uplinks. The design
and configuration are in `docs/LOCKDOWN_OOB.md`; this deployment's choices are in the private config
and doc.

## Status 2026-11-18 -- three tiers; the failover polkit grant is guarded; lockdowns survive a reboot

**Uplink tiers (operator 2026-11-18), IPv4 and IPv6 alike, every profile dual stack with IPv6 preferred:**

| Metric | NM profile | Interface | Role |
|---|---|---|---|
| 100 | Starlink (planned) | `end0` | primary once installed (router mode, not bypass) |
| 600 | `jorransgateway 1` | `wld0` | primary today |
| 1100 | `Hockey Puck` (renamed from `Verizon Hotspot`) | `enu1` | backup: the metered Google Fi hotspot over USB |
| 1600 | `Corporate Traveler WiFi` | `wld0` | last resort: the same hotspot's own WiFi, joined only when `jorransgateway 1` is out of range |

The host side (profile rename, metrics, `NET_FAILOVER_BACKUP_CON=Hockey Puck`) is applied by
`/srv/ctdc-ea/ipv6-first-local.relay`; the watchdog's defaults (`PRIMARY_CON_DEFAULT`, `BACKUP_CON_DEFAULT`,
the saved-metric fallback 1100) match. `FAILOVER_METRIC` stays 50, below every tier.

**The guard on the failover polkit rule.** `config/polkit/49-ctdc-net-failover.rules` still grants only the
operator account the two NetworkManager actions, but only while no hold is in force. It returns NO while
any of these exist:
- `/run/corporatetraveldc-lockdown-active` (lockdown.sh, volatile);
- `/var/lib/ctdc-lockdown/lockdown-active` (lockdown.sh, persistent: survives a reboot);
- `/var/lib/ctdc-lockdown/uncommanded-reboot-hold` (written at boot after any reboot not made through
  `scripts/ctdc-reboot.sh`);
- and while `/run/ctdc-boot-guard/verified` is ABSENT, i.e. this boot has not been verified. If the boot
  guard never runs, the rule holds.

Any error evaluating the holds returns NOT_HANDLED, so NetworkManager's own default (`auth`) applies: it
fails closed, never open. Every decision is logged by polkit (`ctdc-net-failover: YES|NO|NOT_HANDLED ...`).
`/var/lib/ctdc-lockdown` is root-only-writable, so the account the rule grants cannot clear a hold.

**Commanded and uncommanded reboots.**
- Reboot with `sudo /usr/local/libexec/ctdc/ctdc-reboot.sh --reason "..."`. It records the time, the invoking
  user and the reason in `/var/lib/ctdc-lockdown/reboot-commanded`, then runs `systemctl reboot`.
- `corporatetraveldc-boot-guard.service` runs early at every boot, before NetworkManager. A record written
  less than 30 minutes before this boot started is consumed and the boot is marked verified. Anything else
  (no record, a stale record, a record dated after the boot) means an uncommanded reboot: power loss, a crash,
  a plain `sudo reboot`, or someone else's reboot. It writes the hold (boot id, time, the previous boot's last
  journal line) and leaves the boot unverified.
- `corporatetraveldc-boot-guard-alert.service` runs after `network-online`. It sends one priority-5 ntfy
  alert for a hold written this boot, through the root alert path lockdown.sh uses (it retries for up to 10
  minutes, because ntfy is a user container), and re-asserts a lockdown by running the installed
  lockdown.sh while the persistent lockdown marker exists.
- **Clearing:** `sudo /usr/local/libexec/ctdc/ctdc-reboot.sh --clear-hold --reason "..."` removes only the
  hold and marks the boot verified. `restore-network.sh` clears every marker, including the hold, and marks
  the boot verified. `ctdc-reboot.sh --status` shows them all.

**Trade-off, stated plainly.** After a power blip or any uncommanded reboot, the watchdog cannot reroute
until the operator clears the hold. NetworkManager's own connectivity penalty would still move traffic, but
it is OFF until Starlink (2026-11-17 decision), so on this box nothing reroutes automatically in that window.
Clear the hold promptly after checking why the box rebooted.

**Lockdown before 2026-11-18 did not survive a reboot.** Its only marker lived in `/run`. After a reboot the
quadlet edits stayed in place, but restore-network.sh saw "not locked down" and refused to restore them, and
nothing re-asserted the lockdown. Now lockdown.sh writes the persistent marker first, the boot guard
re-asserts it, and restore-network.sh works whether or not the `/run` marker survived.

**Other kill switches, checked 2026-11-18 (unchanged):**
- the gateway kill-all and the withheld kill-all keep their state in Postgres
  (`agent_gateway_settings.frozen`, `withheld_settings frozen*`), so they survive a reboot;
- team-liveness inert locks the account (`usermod -L`, expiry) with a marker under
  `/var/lib/corporatetraveldc/team-liveness/inert/`, which survives a reboot. Its "offline lockdown" authority
  path still reads only the `/run` lockdown marker, so after a reboot during a lockdown it no longer sees one.
  Follow-up: have it read the persistent marker too.

## Status 2026-11-17 -- failover was blocked by polkit; IPv6 first; NM connectivity check off

**Outage and root cause (watch.log, vnstat, live checks):**
- At 14:37Z the WiFi router (10.x.x.x) stopped answering ARP. `wld0` stayed associated with a good signal (-56 dBm) the whole time, so this was upstream-dead, not a WiFi drop. Since 2026-11-01 the primary was down 52 min (11-01), 14 min (11-05), 1 min (11-09) and 187+ min (11-17), each time on the router side.
- The watchdog saw it in 3 probes, then from 14:39:15Z logged `backup profile metric update failed` on every 60 s attempt (98+ by 16:15Z). polkit refused its `nmcli connection modify`: `settings.modify.system` and `network-control` are `auth` for a process with no active local session, and the box has had none since it went headless on 2026-11-03. Failover last worked 2026-10-29, while a desktop autologin session existed.
- The box stayed online only because NetworkManager's connectivity check penalized `wld0` (+20000, metric 20100) at about 14:41Z.
- Cost: the hotspot carried the full load, about 15 Mbit/s (mostly SWIM ingest; the box normally moves about 190 GB a day). That was 21.9 GB by 18:36Z.
- Side effect: poller REST fetches (metar, NAS, TFR, FIDS, NOTAM, runsheet) failed from 14:37Z. The host could reach them over the hotspot, but `wld0`'s IPv6 default route still pointed at the dead router.

**Fix:**
- `config/polkit/49-ctdc-net-failover.rules` grants the operator account exactly those two NetworkManager actions. It's installed with the other root-installed config (ROOTCFG) and checked by `installed-check.sh`.
- The watchdog's `apply_backup_metric` now sets `ipv4.route-metric` and `ipv6.route-metric` together, and `wait_for_route` also verifies the IPv6 default when the expected uplink holds a global IPv6 address.

**Hotspot IPv6 test** (`/srv/ctdc-ea/hotspot-ipv6-test.relay`, about 3 min, automatic restore):
- A global address and a default route came up at once. No DNS64 on the box's resolver.
- Ping under the SWIM load: IPv6 231 ms average vs IPv4 376 ms.
- `tailscale netcheck`: IPv6 yes, `MappingVariesByDestIP: false`, DERP nyc/iad about 222 ms (466 ms earlier the same day, IPv4 only).
- NetworkManager's connectivity penalty turned the IPv6 default into a multipath route across the dead `wld0` next hop and `enu1`.
- Throughput was inconclusive: Cloudflare's test endpoint returned 403 for both address families.

**Decisions (operator 2026-11-17):**
- **IPv6 first on every uplink, the hotspot included.** `config/network/gai.conf` drops the `precedence ::ffff:0:0/96 100` line, so glibc's defaults prefer IPv6. Every profile gets `ipv6.method auto` with the same metric as IPv4: `jorransgateway 1` 100; `Verizon Hotspot` and `Corporate Traveler WiFi` 600. ~~(superseded 2026-11-18: metrics 600 / 1100 / 1600 and the hotspot profile renamed `Hockey Puck`; see the 2026-11-18 status.)~~
- **NetworkManager's connectivity check is off** (`config/network/zz-ctdc-connectivity.conf`) until Starlink is the primary. The watchdog is the only route arbiter, and it is switched off only after the watchdog has proven it can fail over.
- **Open risk:** the 2026-09-02 "hotspot IPv6 throttling", where image builds stalled for 2+ hours over the tether's IPv6. A 3-minute test can't rule out a slow-onset throttle. Watch hotspot IPv6 throughput during the next failover. Rollback: restore the gai.conf precedence line, and set `ipv6.method disabled` on the two hotspot profiles.

**Starlink, planned:**
- Starlink becomes the primary, `wld0` the second line, `enu1` the third. The watchdog will need three tiers.
- Run Starlink's router in router mode, not bypass. Starlink's carrier-grade NAT hands out WAN addresses in 100.64.0.0/10, Tailscale's range, and in bypass mode the Pi itself would hold one.
- Starlink's native IPv6 is what keeps Tailscale paths direct.

## Topology (re-verified live 2026-10-06; RFC1918/tailnet addresses only)

| Interface | NM connection | Address | Role |
|---|---|---|---|
| `wld0` (WiFi) | ~~`jorransgateway`~~ `jorransgateway 1` (2026-11-18) | 10.x.x.x via 10.x.x.x | **primary**, ~~`ipv4.route-metric=100`~~ 600, IPv4 and IPv6 (2026-11-18) |
| `enu1` (USB Ethernet) | ~~`Verizon Hotspot`~~ `Hockey Puck` (renamed 2026-11-18) | 192.168.x.x via 192.168.x.x | **backup**, ~~`ipv4.route-metric=600`~~ 1100, IPv4 and IPv6 (2026-11-18) |
| `wld0` (WiFi) | `Corporate Traveler WiFi` (the hotspot's own WiFi) | hotspot DHCP | **last resort**, 1600, IPv4 and IPv6 (2026-11-18) |
| `end0` (built-in) | ~~`Wired connection 1`~~ (superseded 2026-10-25: no NM profile exists for it now; `nmcli con show` lists `jorransgateway`, `Verizon Hotspot`, `tailscale0`, `lo`, `corporatetravel`, `Corporate Traveler WiFi`, 2026-10-26 02:19Z) | — | DOWN / NO-CARRIER, unused |

The WiFi address has changed since the connection was last documented
(previously 10.x.x.x, now 10.x.x.x — same subnet, DHCP re-lease, not a
topology change). The connection ID is live-confirmed as lowercase
`jorransgateway` (recreated in lowercase after the 2026-10-04 hard reset; the
script default was corrected to match on 2026-10-06).

Two independent NetworkManager connections on different subnets with
different gateways. The *only* thing selecting between them is the
per-connection `ipv4.route-metric`, both explicitly set.

## Current live status (2026-10-06 18:10Z / 14:10 ET) — ~~backup is down right now~~ (superseded 2026-10-26: backup `enu1:ok` on 10 of 10 watchdog cycles, 22:00Z; this section is the 2026-10-06 record)

`scripts/net-failover-watchdog.sh --status`:

```
primary:      jorransgateway (wld0) metric=100
backup:       Verizon Hotspot (enu1) metric=600
default via:  wld0
failed_over:  0
```

Direct probe replication (same method the watchdog uses — ICMP then
TCP:443, by interface):

- `wld0` → 1.1.1.1: **reachable**, 29ms.
- (superseded 2026-10-25: `enu1` probes succeed again -- see the 2026-10-25 note under the status update below.) `enu1` → 1.1.1.1 / 8.8.8.8 / 9.9.9.9: **100% packet loss on all three**,
  and the TCP:443 fallback via `curl --interface enu1` also failed
  (`http_code=000` on all). The watchdog's own log confirms this has been
  continuous for at least the visible window (`backup=enu1:DOWN` on every
  60s cycle from 2026-10-06 05:30 ET through the time of this check, ~12.5
  hours straight).
- `enu1` still shows L2 carrier up (`/sys/class/net/enu1/carrier` = `1`)
  and a valid DHCP lease (192.168.x.x) — the hotspot is associated and
  has handed out an address, but currently has **no working path to the
  internet** over that link. This is exactly the "carrier up, link fine,
  upstream unreachable" failure mode this watchdog exists to catch *on the
  primary*; right now it is happening on the **backup** instead, which the
  watchdog correctly treats as "backup not healthy" and so does not fail
  over to it (both-down branch would be the one to fire if the primary
  also failed right now — see below).

~~This means: **if the primary (`wld0`) were to fail right now, this box has
no working failover path.** The backup connection exists, has carrier, and
looks "up" to a casual check, but is not actually reaching the internet.~~
(superseded 2026-10-26: the backup reaches the internet again -- `backup=enu1:ok` on 10 of 10
watchdog cycles, 22:00Z -- so a primary failure would take the failover branch, not
"both down"; failover has not been exercised end to end (unverified 2026-10-26).)
See Findings for the operator.

**Context — 2026-10-06 06:01 ET incident (operator-reported):** the
primary Wi-Fi lost egress while still associated, and the Verizon USB
hotspot backup (`enu1`, presents as a `cdc_eem` USB CDC Ethernet device)
was down at the same time, so no failover was possible. The backup being
unreachable right now (12+ hours later, at the time of this review) is
consistent with that report rather than a one-off blip that has since
cleared.

**The backup is a cellular link, and it is (per the operator) truly
unlimited** — it carried 701 GB in a month under a misconfigured failover
with no issues, so data volume is not a constraint. The real cost of
sitting on cellular is throttling, which is why fail-back exists.

## Status update 2026-10-13 -- still down, by decision; weekly reminder

- ~~The backup link (enu1, a Verizon-backed phone hotspot) has had no internet since 2026-10-06 over a
  pricing issue. The operator plans a prepaid SIM before winter. Until then failover has nothing to fail
  over to.~~ (corrected 2026-10-25: `enu1` reaches the internet again. `net-failover-watchdog.sh --status` 21:20Z:
  `default via: wld0`, `failed_over: 0`, `ok_streak: 1951`; the log reads `primary=wld0:ok backup=enu1:ok` on
  every minute cycle; `~/.local/state/ctdc-uplink/state.json` = `down_since 2026-10-06, last_check 2026-10-25,
  ~~up_days 2, announced_stable false`, so the "back and stable" push fires after the third good daily check.~~
  (superseded 2026-10-26: `state.json` read 22:00Z shows `up_days` 3 and `announced_stable` true, so the
  "back and stable" push has already fired.)
  The NM profile is still named `Verizon Hotspot` (metric 600, autoconnect yes). Failover has not been
  exercised end to end since the link returned.)
- `corporatetraveldc-uplink-reminder.timer` (user, daily 14:07 UTC) runs `scripts/uplink_reminder.py`: an
  IPv4 HTTPS probe bound to enu1 (link state alone reads "connected" with no service). While the link has
  no internet it pushes a reminder on Mondays; after 3 consecutive good daily checks it pushes once
  ("back and stable") and goes quiet; if the link drops again the weekly reminders restart. State:
  `~/.local/state/ctdc-uplink/state.json`. Tests: `tests/scripts/test_uplink_reminder.py`.
- **Re-check 2026-10-15 01:14Z:** the watchdog ~~still logs `primary=wld0:ok backup=enu1:DOWN default_via=wld0
  failed_over=0` every minute~~ (superseded 2026-10-25: logs `primary=wld0:ok backup=enu1:ok default_via=wld0 failed_over=0`, 21:20Z). ~~Each run now takes about 22 s because every enu1 probe waits out its timeout.~~
  (superseded 2026-10-25: a run takes about 2 s, `ExecMainStartTimestamp` 21:48:00Z to `ExecMainExitTimestamp` 21:48:02Z.) The
  reminder timer is enabled (last run 2026-10-14 14:07Z). ~~A primary failure today still lands in the "both down,
  alert only" branch.~~ (superseded 2026-10-25: with `backup=enu1:ok` a primary failure would take the failover
  branch; not exercised end to end since the link returned.) The nightly Proton sync (`docs/BACKUP.md`) runs over wld0 only (`BACKUP_IFACE=wld0`).
- When the new SIM is in: validate failover end to end (pull the primary, confirm SWIM and the tunnel move
  over and back), keep IPv6 off on that link (the 2026-09-02 hotspot IPv6 throttle), keep bulk traffic
  (the nightly off-box sync, image pulls) off it by default, and add it to vnStat tracking.

## What metrics alone can and cannot do

Metric selection fails over **only when the losing interface's route
disappears** — i.e. on carrier loss. The kernel then falls through to the
next-lowest default automatically. It does **nothing** for "carrier up,
link fine, upstream unreachable" — the kernel keeps sending everything out
the dead metric-100 default indefinitely. That is the 2026-09-05 failure
mode (primary), and it is what the backup ~~is currently exhibiting too~~
exhibited from 2026-10-06 until it recovered (by 2026-10-25) (superseded 2026-10-26: `backup=enu1:ok` on 10 of 10 watchdog cycles, 22:00Z) — the mechanism doesn't care which side degrades this way,
it just can't see it without an active prober on that side.

## Why NetworkManager could not save us

The earlier installation deliberately disabled NM connectivity checking after
local DNS startup produced false limited/offline reports. The operator requested
it re-enabled after the 2026-10-29 SDIO incident. The signed override is
`config/network/zz-ctdc-connectivity.conf`; the combined operator relay installs
it and reloads NM configuration without disconnecting interfaces. The original
vendor URI/response/300-second interval are retained. Preparation alone does not
prove the installed or D-Bus enabled state; the relay verifies both.

Correction to the earlier explanation: NetworkManager's connectivity check can
penalize a failing device's default-route metric by 20000; it is not merely a
status flag. See the upstream [connectivity section](https://www.networkmanager.dev/docs/api/latest/NetworkManager.conf.html).
The external 60-second probe/watchdog remains useful and verifies the route
actually selected after changing the backup metric. Active connections use
`nmcli device reapply` rather than a disruptive connection restart. A profile
write alone never confirms failover/fail-back. Carrier is read from the actual
carrier file with connected NM state as corroboration, not operstate alone.

The separate root-managed `corporatetraveldc-wifi-sdio-recovery.timer` checks for
a recent current-boot brcmfmac SDIO halt. The installed helper verifies the
primary driver, failed primary probes, a healthy backup and the actual backup
route before reloading only brcmfmac. It rechecks the backup immediately before
removal, reserves attempts before any action, and defaults to a 30-minute
cooldown and at most two attempts per boot. It never reboots the host or grants
an agent arbitrary modprobe/sudo access. A driver reload is not a claim that
connectivity recovered; the existing watchdog must observe recovery and verify
fail-back. Missing or invalid evidence withholds the reload. No live driver
reload was performed to test this prepared repair.

## The actual mechanism: `scripts/net-failover-watchdog.sh`

A user systemd timer (`corporatetraveldc-net-failover-watchdog.timer`,
every 60s — confirmed live, last fired 2026-10-06 18:05:06 UTC) that
actively probes upstream through **each** interface and moves the default
route when the primary is dead but the backup is not.

- **Probes are by IP, never hostname** (`1.1.1.1 8.8.8.8 9.9.9.9`, ICMP
  first with a TCP:443 fallback). A hostname probe would fail during
  exactly the outage being detected.
- **Failover:** after `FAIL_STRIKES` (default 3, ~3 min) consecutive
  primary failures *while the backup is healthy*, the backup's metric is
  set to `FAILOVER_METRIC` (50) and only that connection is reactivated.
  The primary's route is never torn down.
- **Fail back:** after `RESTORE_STRIKES` (default 3) consecutive good
  primary probes, the backup's metric is restored to the value captured
  before the flip (from the state file, not hardcoded) — live state file
  confirms `saved_backup_metric=600`.
- **Both down:** alert only, no route change.
- 30-minute ntfy cooldown; `flock` single-instance.

Config precedence is environment variable > `dispatch.env` > built-in
default (`NET_FAILOVER_*`). Confirmed live: no `NET_FAILOVER_PRIMARY_CON`
or `NET_FAILOVER_BACKUP_CON` override is set in `dispatch.env` (name check
only — `grep -c '^NET_FAILOVER_PRIMARY_CON=\|^NET_FAILOVER_BACKUP_CON='`
→ `0`), so the script is running on its hardcoded defaults.

Deliberately **not** gated on `thermal-ingest-guard`'s LOCKDOWN tier —
LOCKDOWN sheds services, never routes.

## DNS needs no special handling

`/etc/resolv.conf` is `nameserver 127.0.0.1` (re-confirmed live), and the
local Unbound is a full **recursive** resolver ~~with no `forward-zone`
(re-confirmed live: `grep -c forward-zone /etc/unbound/unbound.conf` → `0`)~~ (corrected 2026-10-25: one
`forward-zone`, `ts.net.` → `100.100.100.100` (Tailscale MagicDNS) in
`/etc/unbound/conf.d/60-tailscale-magicdns.conf`; every other name recurses; `grep -rn forward-zone /etc/unbound` 21:20Z).
It recurses out over whatever the current default route is, so flipping
the route fixes resolution automatically.

## Verified behaviour (2026-09-05, historical)

| Case | Result |
|---|---|
| Both healthy | no action, no route change |
| Primary dead, backup healthy | real flip: `enu1` metric 50 became default, HTTP 200 through it, `wld0` route left intact |
| Primary recovered | real fail-back: metric restored to 600, default via `wld0`, HTTP 200, state cleared |
| Both dead (RFC 5737 probe target) | alert only, routes untouched |
| Sub-threshold strikes | no action, correctly held at 1/3 |
| `nmcli` apply fails (bogus connection) | error surfaced, `failed_over` not set, real connection untouched |

## Findings for the operator

1. ~~**Backup uplink (`enu1`/Verizon Hotspot) passes no traffic** (operator 2026-10-06: acceptable for now while the hotspot stays associated/bonded)
   despite showing carrier-up and a valid DHCP lease — 100% ICMP loss and
   failed TCP:443 on all three probe IPs, continuously for at least the
   12.5 hours of watchdog log visible at review time. The documented
   failover protection is not actually available right now: a primary
   failure at this moment would hit the "both down, alert only" branch,
   not a real failover. This is consistent with the reported 2026-10-06
   06:01 ET incident and suggests the backup has not recovered since.~~
   (superseded 2026-10-25: `enu1` passes traffic again -- `backup=enu1:ok` on
   every minute cycle, `ok_streak 1979` at 21:48Z; the end-to-end failover
   validation is still due.)
2. (fixed 2026-10-06) `PRIMARY_CON_DEFAULT` now matches the live lowercase profile `jorransgateway`.
3. ~~`RandomizedDelaySec=` is set on only two timers fleet-wide, and most
   timers carry `Persistent=true`; see `docs/BOOT_STORM_TIMER_REQUIRES.md`
   for the related, more serious finding about two root timers still
   carrying a self-referential `Requires=`.~~ (corrected 2026-10-15:
   `RandomizedDelaySec=` is now on ~~12~~ live timers, ~~8~~ of ~~78~~ (corrected 2026-10-26: 79; `ls ~/.config/systemd/user/*.timer` 22:36Z) user timers and
   4 of 9 `corporatetraveldc-*` system timers; ~~43~~ live user timers carry
   `Persistent=true` (corrected 2026-10-25: 13 timers, 9 user + 4 system; ~~44 user timers~~ `Persistent=true`; `grep -l` over the unit files 21:22Z) (corrected 2026-10-26: 45 of 79 user `.timer` files carry `Persistent=true`; `grep -l '^Persistent=true' ~/.config/systemd/user/*.timer`, `ls`, 22:00Z), so post-outage catch-up can still bunch. The two root
   timers' self-referential `Requires=` lines were removed on 2026-10-06 and
   no live timer carries one now; see
   [`docs/BOOT_STORM_TIMER_REQUIRES.md`](BOOT_STORM_TIMER_REQUIRES.md).)

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### Uplink failover — how it actually works on this box

~~**There is no bond.** `/proc/net/bonding/` is empty. Anything that calls this setup "bonded" is wrong, and that misconception is why the 2026-09-05 outage was expected to self-heal when it structurally could not.~~

**~~Topology~~** *(former heading)*


### Uplink failover — how it actually works on this box › Topology

| ~~Interface~~ | ~~Connection~~ | ~~Address~~ | ~~Role~~ |
|---|---|---|---|
| ~~`wld0` (WiFi)~~ | ~~`Jorransgateway`~~ | ~~10.x.x.x via 10.x.x.x~~ | ~~**primary**, `ipv4.route-metric=100`~~ |
| ~~`enu1` (USB Ethernet)~~ | ~~`Verizon Hotspot`~~ | ~~192.168.x.x via 192.168.x.x~~ | ~~**backup**, `ipv4.route-metric=600`~~ |
| ~~`end0` (built-in)~~ | ~~`Wired connection 1`~~ | ~~—~~ | ~~DOWN / NO-CARRIER, unused~~ |

~~Two independent NetworkManager connections on different subnets with different gateways. The *only* thing selecting between them is the per-connection `ipv4.route-metric`. Both metrics are explicitly set (not inherited defaults) and the primary/backup roles were confirmed with the operator on 2026-09-05.~~

~~Both paths were verified to carry real internet: warm HTTPS to `api.weather.gov` returned 200 over each, 0.17–0.21 s via `wld0` and 0.24–0.46 s via `enu1`.~~

~~**The backup is a cellular link, and it is truly unlimited.** `enu1` is a Verizon hotspot tethered over USB — it presents as a CDC ethernet device, which is the only reason it looks wired (and why it sat under the meaningless name "Wired connection 2" until it was renamed on 2026-09-05).~~

~~Per the operator (2026-09-05), the plan is genuinely uncapped: it carried **701 GB last month** under a misconfigured failover with no issues. So **data volume is not a constraint**, and nothing in this design should be justified by "saving data" — an earlier draft of this doc wrongly framed fail-back as a cost control.~~

~~The real cost of sitting on cellular is **throttling**: sustained use degrades throughput. So fail-back exists to restore full-speed WiFi promptly, not to protect an allowance. Alerts fire on every flip, so a move onto the hotspot is never silent.~~

~~**Known limitation:** the probe tests *reachability* (ICMP), not throughput. A throttled-but-reachable hotspot reads as perfectly healthy. That is an accepted gap — detecting throttling would require real throughput sampling, which is not worth doing on a 60-second health check.~~


### Uplink failover — how it actually works on this box › What metrics alone can and cannot do

~~Metric selection fails over **only when the losing interface's route disappears** — i.e. on carrier loss. The kernel then falls through to the next-lowest default automatically, and that has always worked here.~~

~~It does **nothing** for the failure mode actually observed on 2026-09-05: carrier up, link "fine", upstream unreachable. The kernel keeps sending everything out the dead metric-100 default indefinitely. The box sat that way for ~100 minutes and only recovered on a manual reboot.~~


### Uplink failover — how it actually works on this box › Why NetworkManager could not save us

~~NM's connectivity probe is disabled box-wide on purpose: `/etc/NetworkManager/conf.d/no-connectivity-check.conf`, applied by `harden-wifi.sh` and marked *"do not remove"* — the probe fired before Unbound was ready, marked connections "limited", and made browsers show offline.~~

~~Across the entire outage NM logged nothing but a routine DHCP renew: no carrier change, no state change. No failover was ever attempted.~~

~~Critically, **re-enabling that probe would not have helped.** NM's connectivity check only reports a state (`nmcli networking connectivity`). It never demotes a default route or switches interfaces. There is no built-in "this default route has no internet, use the other one" in NetworkManager. Operator decision 2026-09-05: leave the probe disabled, do the health checking externally.~~


### Uplink failover — how it actually works on this box › The actual mechanism: `scripts/net-failover-watchdog.sh`

~~A user systemd timer (`corporatetraveldc-net-failover-watchdog.timer`, every 60 s) that actively probes upstream through **each** interface and moves the default route when the primary is dead but the backup is not.~~

- ~~**Probes are by IP, never hostname** (`1.1.1.1 8.8.8.8 9.9.9.9`, ICMP first with a TCP:443 fallback for ICMP-dropping networks). A hostname probe would fail during exactly the outage being detected, since recursion is down — that would diagnose DNS, not the path.~~
- ~~**Failover:** after `FAIL_STRIKES` (default 3, so ~3 min) consecutive primary failures *while the backup is healthy*, the backup connection's metric is set to `FAILOVER_METRIC` (50) and that connection alone is reactivated. The primary's route is never torn down — a degraded but present primary keeps its metric-100 default, so nothing is destroyed.~~
- ~~**Fail back:** after `RESTORE_STRIKES` (default 3) consecutive good primary probes, the backup's metric is restored to the value captured *before* the flip (recorded in the state file, not hardcoded).~~
- ~~**Both down:** alert only, no route change. That is an upstream/ISP outage; flipping would just obscure which link is at fault.~~
- ~~Loud ntfy + email on every flip, restore, and failure-to-apply, with a 30-minute cooldown so a stuck condition cannot page every 60 s.~~
- ~~`flock` single-instance, so a 60 s timer never stacks runs while an `nmcli` reactivation is in flight.~~

~~Config precedence is environment variable > `dispatch.env` > built-in default (`NET_FAILOVER_*`), which is what makes the failover path testable without editing production config.~~

~~Deliberately **not** gated on `thermal-ingest-guard`'s LOCKDOWN tier, unlike `runner-health-watchdog.sh`. That gate exists so a watchdog cannot undo the guard's load-shedding — but LOCKDOWN sheds *services*, never routes, so there is nothing to collide with. A box in LOCKDOWN with a dead uplink still wants its uplink back.~~


### Uplink failover — how it actually works on this box › DNS needs no special handling

~~`/etc/resolv.conf` is just `nameserver 127.0.0.1`, and the local Unbound is a full **recursive** resolver with no `forward-zone`. It is therefore not bound to either interface's DHCP nameservers (75.75.75.75 on WiFi, 192.168.x.x on Ethernet) — it recurses out over whatever the current default route is. Flipping the route fixes resolution automatically.~~

~~This is also why the outage surfaced everywhere as `Temporary failure in name resolution`: recursion could not reach the root servers over the dead path. That symptom was DNS-shaped but never a DNS misconfiguration.~~

**~~Verified behaviour (2026-09-05, on the live box)~~** *(former heading)*


### Uplink failover — how it actually works on this box › Verified behaviour (2026-09-05, on the live box)

**~~Still open~~** *(former heading)*


### Uplink failover — how it actually works on this box › Still open

~~`RandomizedDelaySec=` is set on only two timers, and 37 carry `Persistent=true`; after a long outage their catch-up runs can still bunch. Unrelated to failover, but the same "what happens when the box comes back" question — see `docs/BOOT_STORM_TIMER_REQUIRES.md`.~~
