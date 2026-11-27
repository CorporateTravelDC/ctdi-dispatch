# DNS lock: Pi-hole and Unbound only (2026-11-27)

**Operator's requirement:** every DNS lookup goes through **Pi-hole, then Unbound**. That covers this box's own
traffic on all three uplinks (`end0`, `wld0`, `enu1`), and everything from tailnet peers, exit-node clients
included. Unbound forwards only **this tailnet's** MagicDNS names to Tailscale.

## Before this change (checked live 2026-11-27)

- **The box's own lookups:** `/etc/resolv.conf` is `nameserver 127.0.0.1` only, and immutable (`chattr +i`).
  So DHCP and router advertisements can't change it.
  - Pi-hole-FTL (user `pihole`) listens on :53 and forwards to Unbound (user `unbound`) on 127.0.0.1:5335.
    Unbound is fully recursive.
  - NetworkManager still accepted DHCP DNS on "Auto Ethernet" and "jorransgateway 1". It was never used, because
    of the immutable file, but the relay switches `ignore-auto-dns` on for every profile anyway.
- **Tailnet peers:** the tailnet's global nameserver is this box (100.x.x.x), so MagicDNS clients already use
  Pi-hole. The exit-node table already dropped DoT/DoQ and known DoH addresses from peers.
- **Gap 1, outside resolvers:** any process, container or peer with a hard-coded resolver could query an outside
  server directly. A test query from the box to 75.75.75.75 over `end0` was answered.
- **Gap 2, the ts.net forward:** Unbound sent all of `ts.net.` to Tailscale's resolver, not just this tailnet's
  names.

## What it changes

- **`ctdc-dnslock.nft`** is its own `inet ctdc_dnslock` table, loaded by `corporatetraveldc-dnslock.service`.
  - **Outbound DNS:** loopback is free. Unbound may reach 53/853. Every other outbound 53/853 packet is logged
    with its UID (rate-limited, prefix `ctdc-dns-bypass`), counted, then accepted (`--audit`) or rejected
    (`--enforce`).
  - **Tailnet peers:** a port-53 query arriving on `tailscale0` is redirected to this box's Pi-hole.
- **`60-tailscale-magicdns.conf.in`** is rendered into `/etc/unbound/conf.d/`:
  - **The forward:** only `<this tailnet's MagicDNS suffix>.` goes to 100.100.100.100.
  - **Rendering:** the suffix is filled in at install from `tailscale status --json`, so it is never written
    into a tracked file, which this repo's public mirror would publish.
  - **Ownership:** this replaces the copy in `pihole-unbound-selinux-internal`.

## Order

1. Run `--unbound`.
2. Run `--audit`, and watch the `ctdc-dns-bypass` log lines for a while.
3. Run `--enforce`, only once nothing but Unbound is counted.

A container or peer that was quietly using an outside resolver shows up in step 2 by UID. Fix it first; an enforced
block would cut it off.

**Undo:** `--remove` unloads the table and the unit. The previous Unbound file is kept as
`60-tailscale-magicdns.conf.bak-<UTC>`.
