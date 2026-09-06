# Uplink failover — how it actually works on this box

**There is no bond.** `/proc/net/bonding/` is empty. Anything that calls
this setup "bonded" is wrong, and that misconception is why the 2026-09-05
outage was expected to self-heal when it structurally could not.

## Topology

| Interface | Connection | Address | Role |
|---|---|---|---|
| `wld0` (WiFi) | `Jorransgateway` | 10.x.x.x via 10.x.x.x | **primary**, `ipv4.route-metric=100` |
| `enu1` (USB Ethernet) | `Verizon Hotspot` | 192.168.x.x via 192.168.x.x | **backup**, `ipv4.route-metric=600` |
| `end0` (built-in) | `Wired connection 1` | — | DOWN / NO-CARRIER, unused |

Two independent NetworkManager connections on different subnets with
different gateways. The *only* thing selecting between them is the
per-connection `ipv4.route-metric`. Both metrics are explicitly set
(not inherited defaults) and the primary/backup roles were confirmed with
the operator on 2026-09-05.

Both paths were verified to carry real internet: warm HTTPS to
`api.weather.gov` returned 200 over each, 0.17–0.21 s via `wld0` and
0.24–0.46 s via `enu1`.

**The backup is a cellular link, and it is truly unlimited.** `enu1` is a
Verizon hotspot tethered over USB — it presents as a CDC ethernet device,
which is the only reason it looks wired (and why it sat under the
meaningless name "Wired connection 2" until it was renamed on 2026-09-05).

Per the operator (2026-09-05), the plan is genuinely uncapped: it carried
**701 GB last month** under a misconfigured failover with no issues. So
**data volume is not a constraint**, and nothing in this design should be
justified by "saving data" — an earlier draft of this doc wrongly framed
fail-back as a cost control.

The real cost of sitting on cellular is **throttling**: sustained use
degrades throughput. So fail-back exists to restore full-speed WiFi
promptly, not to protect an allowance. Alerts fire on every flip, so a
move onto the hotspot is never silent.

**Known limitation:** the probe tests *reachability* (ICMP), not
throughput. A throttled-but-reachable hotspot reads as perfectly healthy.
That is an accepted gap — detecting throttling would require real
throughput sampling, which is not worth doing on a 60-second health check.

## What metrics alone can and cannot do

Metric selection fails over **only when the losing interface's route
disappears** — i.e. on carrier loss. The kernel then falls through to the
next-lowest default automatically, and that has always worked here.

It does **nothing** for the failure mode actually observed on 2026-09-05:
carrier up, link "fine", upstream unreachable. The kernel keeps sending
everything out the dead metric-100 default indefinitely. The box sat that
way for ~100 minutes and only recovered on a manual reboot.

## Why NetworkManager could not save us

NM's connectivity probe is disabled box-wide on purpose:
`/etc/NetworkManager/conf.d/no-connectivity-check.conf`, applied by
`harden-wifi.sh` and marked *"do not remove"* — the probe fired before
Unbound was ready, marked connections "limited", and made browsers show
offline.

Across the entire outage NM logged nothing but a routine DHCP renew: no
carrier change, no state change. No failover was ever attempted.

Critically, **re-enabling that probe would not have helped.** NM's
connectivity check only reports a state (`nmcli networking connectivity`).
It never demotes a default route or switches interfaces. There is no
built-in "this default route has no internet, use the other one" in
NetworkManager. Operator decision 2026-09-05: leave the probe disabled,
do the health checking externally.

## The actual mechanism: `scripts/net-failover-watchdog.sh`

A user systemd timer (`corporatetraveldc-net-failover-watchdog.timer`,
every 60 s) that actively probes upstream through **each** interface and
moves the default route when the primary is dead but the backup is not.

- **Probes are by IP, never hostname** (`1.1.1.1 8.8.8.8 9.9.9.9`, ICMP
  first with a TCP:443 fallback for ICMP-dropping networks). A hostname
  probe would fail during exactly the outage being detected, since
  recursion is down — that would diagnose DNS, not the path.
- **Failover:** after `FAIL_STRIKES` (default 3, so ~3 min) consecutive
  primary failures *while the backup is healthy*, the backup connection's
  metric is set to `FAILOVER_METRIC` (50) and that connection alone is
  reactivated. The primary's route is never torn down — a degraded but
  present primary keeps its metric-100 default, so nothing is destroyed.
- **Fail back:** after `RESTORE_STRIKES` (default 3) consecutive good
  primary probes, the backup's metric is restored to the value captured
  *before* the flip (recorded in the state file, not hardcoded).
- **Both down:** alert only, no route change. That is an upstream/ISP
  outage; flipping would just obscure which link is at fault.
- Loud ntfy + email on every flip, restore, and failure-to-apply, with a
  30-minute cooldown so a stuck condition cannot page every 60 s.
- `flock` single-instance, so a 60 s timer never stacks runs while an
  `nmcli` reactivation is in flight.

Config precedence is environment variable > `dispatch.env` > built-in
default (`NET_FAILOVER_*`), which is what makes the failover path
testable without editing production config.

Deliberately **not** gated on `thermal-ingest-guard`'s LOCKDOWN tier,
unlike `runner-health-watchdog.sh`. That gate exists so a watchdog cannot
undo the guard's load-shedding — but LOCKDOWN sheds *services*, never
routes, so there is nothing to collide with. A box in LOCKDOWN with a
dead uplink still wants its uplink back.

## DNS needs no special handling

`/etc/resolv.conf` is just `nameserver 127.0.0.1`, and the local Unbound
is a full **recursive** resolver with no `forward-zone`. It is therefore
not bound to either interface's DHCP nameservers (75.75.75.75 on WiFi,
192.168.x.x on Ethernet) — it recurses out over whatever the current
default route is. Flipping the route fixes resolution automatically.

This is also why the outage surfaced everywhere as `Temporary failure in
name resolution`: recursion could not reach the root servers over the
dead path. That symptom was DNS-shaped but never a DNS misconfiguration.

## Verified behaviour (2026-09-05, on the live box)

| Case | Result |
|---|---|
| Both healthy | no action, no route change |
| Primary dead, backup healthy | real flip: `enu1` metric 50 became default, HTTP 200 through it, `wld0` route left intact |
| Primary recovered | real fail-back: metric restored to 600, default via `wld0`, HTTP 200, state cleared |
| Both dead (RFC 5737 probe target) | alert only, routes untouched |
| Sub-threshold strikes | no action, correctly held at 1/3 |
| `nmcli` apply fails (bogus connection) | error surfaced, `failed_over` not set, real connection untouched |

## Still open

`RandomizedDelaySec=` is set on only two timers, and 37 carry
`Persistent=true`; after a long outage their catch-up runs can still
bunch. Unrelated to failover, but the same "what happens when the box
comes back" question — see `docs/BOOT_STORM_TIMER_REQUIRES.md`.
