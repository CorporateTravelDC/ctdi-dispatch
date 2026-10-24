# Pi-hole profile: aggressive encrypted-DNS / ECH hardening

**OPT-IN -- a more aggressive measure. Not applied by default.**

Operator 2026-10-23: stage the encrypted-DNS/ECH hardening "as a feature
branch or a foldered setting that highlights it's a more aggressive measure".
This folder is that setting. Nothing in the normal deploy path reads it; the
only way it reaches the box is the operator running `apply.sh --apply` here.

## What it does

1. **Adds HaGeZi's Encrypted DNS Bypass list** as a Pi-hole adlist:
   `https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh.txt`
   -- the hostnames of public DoH / DoT / DoQ resolvers (~3.3k entries, adblock
   syntax so every subdomain is covered; Pi-hole v6 reads the format natively).
   An app that tries to switch to its own encrypted resolver by *name* gets
   `0.0.0.0` and falls back to the system resolver, which is this Pi-hole.
2. **Strips HTTPS (type 65) records** from every answer with dnsmasq's
   `filter-rr=HTTPS`, set through Pi-hole v6's `misc.dnsmasq_lines`. HTTPS
   records are where a server publishes its ECH (Encrypted Client Hello) key;
   with no record there is no key, so no client can hide the real server name
   (SNI) in the TLS handshake and the egress firewall / traffic view keeps
   seeing who is actually being contacted.

## Why

On 2026-10-23 the driver phone resolved `dns.google` while this Pi-hole had
no DoH list at all. Pi-hole's built-in protections -- `blockESNI`,
`mozillaCanary`, `iCloudPrivateRelay`, `designatedResolver` -- were already on
and do not cover a plain resolver hostname or the HTTPS-record ECH path; the
built-ins stop *browsers* from auto-upgrading, not an app that names its own
resolver.

## Trade-offs (read before applying)

- Browsers lose the HTTP/3 (ALPN) hint carried in HTTPS records and negotiate
  normally over HTTP/2 or 1.1. Slightly slower first connection, nothing
  breaks.
- Any service a user reaches *through* a listed resolver hostname stops
  resolving (for example an app or VPN profile configured with a named public
  DoH endpoint). The symptom is "this app cannot connect" on that device.
- Apps that hard-code a resolver **IP** (e.g. `8.8.8.8`, `1.1.1.1` over
  DoT/DoQ on 853, or DoH to a bare IP) are NOT stopped by anything DNS-side;
  that needs firewall rules where the traffic exits (block 853/tcp+udp and
  DoH-to-IP egress at the router / tailnet exit), which this profile does
  not touch. Since 2026-10-24 that firewall half lives in
  [`config/exit-node/`](../../../exit-node/README.md): the POLICY 2 Mbps
  metered cap for the phone's forced exit node, plus the OPT-IN
  `--with-aggressive` DoT/DoQ + DoH-by-IP drops (HaGeZi IP list) -- same
  opt-in stance as this profile, separate `apply.sh`.
- `pihole-FTL` restarts when the dnsmasq line changes: a ~2 s DNS gap for
  every client on this box.

## How to apply

The Pi-hole on this box is compliance-locked; **only the operator runs
`apply.sh`**, as root, from the deployed repo checkout:

    cd /opt/corporatetraveldc/private/ctdi-dispatch-internal/config/pihole/profiles/aggressive-encrypted-dns
    sudo bash apply.sh                # dry-run: prints exactly what would change, changes nothing
    sudo bash apply.sh --apply        # idempotent: adlist row only if absent, filter-rr only if missing
    sudo bash apply.sh --revert       # removes only this profile's adlist row and only filter-rr=HTTPS

`apply.sh` echoes every command it would run, touches no other Pi-hole
setting, runs `pihole -g` only when the adlist actually changed, restarts
`pihole-FTL` only when `misc.dnsmasq_lines` actually changed, and never
rewrites existing dnsmasq lines -- it merges one line into the array the box
already has.

## Verify

    dig +short @127.0.0.1 dns.google                 # expect 0.0.0.0
    dig +short @127.0.0.1 cloudflare-ech.com HTTPS   # expect empty output

`apply.sh` runs both checks at the end of every mode and prints PASS / FAIL
(for `--revert` the expectations invert: an address, and an HTTPS record).

## Revert

`sudo bash apply.sh --revert`, then the same two `dig` checks should show a
real address for `dns.google` and an HTTPS record for `cloudflare-ech.com`.
If anything else on the box changed at the same time, compare
`pihole-FTL --config misc.dnsmasq_lines` with the array the dry-run printed
before you applied; the script only ever adds or removes `filter-rr=HTTPS`.

## Default vs opt-in (2026-10-24)

One setting decides whether this is on by default: `CTDC_AGGRESSIVE_PRIVACY` in the deployment's
`/etc/corporatetraveldc/dispatch.env` (parsed, never sourced).

| Deployment | Value | Effect |
|---|---|---|
| Ours (CorporateTravelDC) | `1` | aggressive measures are the default |
| Public repo deployments | `0` (shipped in `config/dispatch.env.example`) | opt-in: nothing aggressive unless you turn it on |

Command-line flags override it for one run: `--with-aggressive` / `--without-aggressive` (exit-node),
`--force-aggressive` (Pi-hole profile). `--revert` always works.
