# DNS leak watch

`config/dns-leak-watch/` is a small, self-contained monitor for one question: **does an app on a phone connect to
addresses that no DNS lookup handed out?** It's written so anyone with a Linux box running Pi-hole can repeat the test
on their own, stock phone. Results from many phones, carriers and app versions say more than one device can.

The same root collector also gathers the evidence for a second question, **is the phone resolving names outside
Pi-hole?**:
- plain DNS to another resolver;
- DNS over TLS or QUIC (port 853);
- DNS over HTTPS to a known resolver.

That evidence goes into `latest.json`. In this repository, `scripts/dns-anomaly-watch.py` reads it and reports it as
the **DNS bypass** class, next to its other DNS anomalies. If you don't run the anomaly watch, the evidence is still
in `latest.json`, and `summary.txt` shows its count.

## The two classes

| Class | Reported by | What it means |
|---|---|---|
| **Hard-coded IP** | this tool (`summary.txt`, CSV, push) | the phone connected to an address no DNS answer gave **anyone** in the lookback window (`INDEX_HOURS`, default 48 h) |
| **DNS bypass** | DNS anomaly watch, from `latest.json` `bypass_evidence` | the phone sent DNS somewhere other than Pi-hole: `dns53` (plain DNS to another server), `dot` (TCP 853), `doq` (UDP 853), `doh` (443 to an address on the encrypted-resolver list); plus the count of plain-DNS queries the optional dns-lock table redirected to Pi-hole |

Hard-coded IP is tiered, so context is never mistaken for a finding:

| Tier | Meaning | Treat as |
|---|---|---|
| `hardcoded` | no Pi-hole answer to **any** client gave this address in the window | the finding |
| `other-client` | answered in the window, but only for names **other** devices asked for (`shared-address` when it's a known CDN or cloud address) | context only, never alerted |
| `cached-stale` | answered for **this** device, but only before the window | context only, never alerted (an old cache) |
| `lookup` | answered for this device in the window | normal |

A destination that matches a bypass kind is exported as bypass evidence and left out of the hard-coded report. An
encrypted resolver is usually a built-in address anyway, and what matters there is that DNS went around Pi-hole.

A destination on the **known-infrastructure** list (Tailscale's relay servers, below) is class `infra`: listed under
its own heading in `summary.txt` and never alerted. Tailscale reaches its relays by address on purpose (ports 80, 443
and 3478), so before 2026-11-27 every relay contact read as `hardcoded`; four such pushes in one afternoon
(derp10c, 11f, 27b, 27e) were all Tailscale itself.

## Known infrastructure: Tailscale relays

The daily refresh writes `INFRA_LIST` (`/etc/ctdc-dnsleak/known-infra.txt`) from the community list
[jmaddington/tailscale-ips](https://github.com/jmaddington/tailscale-ips) by
[JM Addington](https://github.com/jmaddington) ([jmaddington.com](https://www.jmaddington.com)), which parses
Tailscale's DERP map once a day into plain address lists. Thanks to them for publishing it.

Because this list can only ever *silence* an alert, the watch does not trust it alone: every address is cross-checked
against Tailscale's own DERP map (`INFRA_CHECK_URL`), and only addresses both carry are kept; the rest are dropped and
named in the refresh log. When the official map is unreachable the last good list stays. On a first run with no list
and no map, `INFRA_SEED` (here: the bundler repo's pinned submodule, `tailscale-ips/tailscale.all.txt`) is installed
as-is. Relay addresses are never blocked.

## Encrypted resolvers: known, learned, and the forward defense

The daily refresh (`ctdc-dnsleak-resolvers.timer`, skipped on a metered route) builds `RESOLVER_LIST` from:

| Source | What | Key |
|---|---|---|
| known, public | DoH/DoT/DoQ address lists: [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists) `ips/doh.txt`, [dibdot/DoH-IP-blocklists](https://github.com/dibdot/DoH-IP-blocklists) IPv4 + IPv6 | `RESOLVER_LIST_URL`, `RESOLVER_EXTRA_URLS` |
| learned, public | addresses Pi-hole handed out for a resolver host name (hagezi `wildcard/doh-onlydomains.txt`) | `RESOLVER_DOMAINS_URL` |
| learned, hard-coded | addresses an app sent DNS to **directly**: port 53 (seen by the `pre_dns` chain *before* the dns-lock redirect rewrites it), 853, 8853 | recorded by every watch run |

Each source keeps a last good copy; learned entries are dropped after `LEARNED_DAYS` (90) unseen. They are kept in
`OUT_DIR/learned-resolvers.json` with how each was learned and which devices used it. A new hard-coded resolver
learned from direct DNS is pushed once (forwarded 853/8853 ones are DNS anomaly watch's to alert on).

`summary.txt` now names **which device sent DNS straight to which outside resolver**, from the `pre_dns` set. The
dns-lock table's redirect counter alone could only count those queries.

**`BLOCK=yes` (the forward defense).** After the watch records a new forwarded connection, the forward chain
*rejects* (TCP reset / ICMP, so the app falls back at once to normal DNS, which Pi-hole answers):
- DNS over TLS (853/tcp) and DNS over QUIC (853, 8853/udp), by port;
- plain DNS that was not redirected to this host (on `tailscale0` the dns-lock redirect catches port 53 first);
- 443 (TCP and UDP) to an address in `blocked-resolvers.txt` or a learned hard-coded resolver.

Never blocked: LAN, tailnet, loopback, link-local and unique-local addresses, Tailscale relays, and any address
Pi-hole also answered (in the window) for an ordinary, non-resolver name: shared hosting, where blocking would break
sites. A newly learned resolver is added to the live set at once (`nft add element`); the full sets are reloaded by
`load-sets` after each refresh and whenever the fw unit loads the table. A blocked attempt does **not** fail a
device's connection check (it got nowhere); it is counted in the verdict, marked `"blocked": true` in
`latest.json`, and DNS anomaly watch reports it as a *blocked attempt* at priority 3.

What `BLOCK=yes` cannot stop: DoH to a resolver on no list and not yet learned (it looks like any HTTPS
connection), DoH behind a shared CDN address, and DoH inside an on-device VPN.

**ECH (Encrypted Client Hello)** is not a resolver: it hides the *site name* inside TLS, and the lookup still goes
through Pi-hole, which still filters it. The refresh lists the ECH-capable names this tailnet looked up (HTTPS record
with an `ech=` config, up to `ECH_PROBE_MAX` names, plus `ECH_NAMES`) as context in `summary.txt`. Its real risk is
DoH carried to an ECH-fronted CDN address, which neither an address list nor the name can catch.

### Offensive checks (on a Linux peer)

`dns-lockdown-check.sh` now also **tries** the bypasses an app could use, through the exit node, and each must be
refused: 7 DNS over TLS to 1.1.1.1:853, 8 DNS over HTTPS by address to 1.1.1.1 and 8.8.8.8, 9 DNS over QUIC (only
with `kdig`; otherwise SKIP, never a pass). With `DEVICE_SSH_CHECK=yes` the watch runs it on Linux peers every 6 h,
so the defense is tested from the outside, not only asserted.

## How classification works

Pi-hole's log records which client asked for a name (`query[A] name from client`). It records the answer separately
(`reply name is address`), without the client. So an answer counts as given to every client that asked for that name.
Every client address listed in `CLIENTS` is treated as one device.

The index keeps answers for 30 days. Only those within `INDEX_HOURS` decide `hardcoded` versus `other-client`; the
older ones decide `cached-stale`. Each destination (address, protocol, port) is classified **once**, the first time
it's seen. If Pi-hole answers for it later, that name is shown beside it as context, and the original class stands.

## Reading the tiers

- **`hardcoded` is the finding.** No DNS answer to anyone on your network produced that address in the lookback
  window. It's the only tier this tool alerts on.
- **`other-client` is weak context, never an alert.** The address was handed out, but for a name some *other* client
  looked up. Usually the address is shared: one CDN or cloud address serves many names, so your phone reached an
  address another device happened to resolve.
  - **`shared-address`** marks an other-client hit whose Pi-hole answer name, or its reverse-DNS name, ends in a large
    CDN or cloud suffix: CloudFront, Akamai, Google (`1e100.net`, `googleusercontent.com`), AWS, Azure, Fastly, Apple,
    Meta and a few others. The list is `SHARED_SUFFIXES` in `dns_leak_watch.py`.
  - **That marker is a heuristic.** CDNs without reverse-DNS records (most Cloudflare and Fastly addresses) aren't
    marked. A company's own server on one of those clouds is marked "shared" too.
  - Pi-hole often logs the final CNAME target as the answer name (`…cloudfront.net is …`), which is what makes the
    name check useful.
- **`cached-stale` is context, never an alert.** Your device did look this address up, just before the window. It's
  an old cache.

### When Pi-hole sees this host instead of the phone (`PROXY_CLIENTS`)

With a Tailscale exit node, the phone's DNS can reach Pi-hole *through* this host. Pi-hole then logs this host
(loopback, or its own tailnet address) as the client, and every answer reads as `other-client`. Run:

    sudo config/dns-leak-watch/apply.sh diagnose 120

Use the phone during those 120 seconds. The command counts which kind of client address Pi-hole logged queries from:
the device, a `PROXY_CLIENTS` address, this host, or something else. It shows counts only, never the addresses.

If the queries come from this host, list this host's loopback and tailnet addresses in `PROXY_CLIENTS`
(`init-conf --proxy "127.0.0.1,::1,<host tailnet v4>,<host tailnet v6>"`). This host's own lookups then count as the
device's. That can hide a hard-coded address only in one case: this host happened to resolve the same address itself.

## Connection check (every tailnet device, `TAILNET=yes`)

With `TAILNET=yes`, the collector watches every device on the tailnet, not just one phone. `CLIENTS` then includes
Tailscale's two ranges, `100.64.0.0/10` and `fd7a:115c:a1e0::/48`; those two are the only networks wider than 65,536
addresses the config accepts.

Each root run reads `tailscale status --json` to tell devices apart by host name. Device addresses, and the Pi-hole
index (which holds client addresses), stay in `root-state.json`, which is root-only (0600). Reports and `latest.json`
use host names.

**When a device is checked.** A device gets a `CHECK_WINDOW_MIN` (default 10 minutes) evaluation window when it comes
online, or when it first sends traffic through this box. While it stays online, a new window starts after each
verdict. At the end of each window:

| Verdict | Means | Pushed |
|---|---|---|
| **PASS** | traffic went through this box, Pi-hole saw lookups from the device (or from `PROXY_CLIENTS` while the device was active), and there was no bypass evidence | only when it recovers from FAIL or NOT-ROUTED |
| **FAIL** | traffic through this box but **no** Pi-hole lookups, so its DNS goes somewhere else or is broken; **or** any bypass evidence (plain DNS elsewhere, 853, a known DoH resolver) | yes |
| **NOT-ROUTED** | online, but no traffic through this box: it isn't using this exit node, so the box can't vouch for it | yes |

Pushes go out once per device per verdict change, through the same ntfy path. The first run is a silent baseline.
Verdicts are in `summary.txt` (the **CONNECTION CHECK** section) and in `latest.json` under `devices`.

### Quarantine (enforcement, `QUARANTINE=alert-then-quarantine`)

**Off by default** (`QUARANTINE=no`). When it's on:
- **When it triggers:** a device whose last verdict was FAIL, and whose current window still shows no sign of passing,
  is quarantined `QUARANTINE_GRACE_MIN` (default 5) minutes after its first FAIL.
- **What's dropped:** its addresses go into the nft sets `quar4`/`quar6`, which drop its **forwarded** traffic except
  traffic to the tailnet itself.
  - **What keeps working:** DNS to this host and Tailscale's own control traffic aren't forwarded traffic, so they
    keep working. The device can still resolve names, and still reach this box.
  - **Its traffic is still recorded.** The drop comes after the watch records each attempt.
- **Release:** automatic on its next PASS. Manually: `sudo config/dns-leak-watch/apply.sh release <host>`, or
  `release --all`.
- **Never quarantined:** this host, any `PROXY_CLIENTS` address, loopback, or a host listed in `QUARANTINE_EXEMPT`.
- **Pushes:** every add and every removal.

**Why it's off by default:** a FAIL can be false.
- A resolver restart on the device, a captive portal, or a VPN reconnect can produce a window with traffic and no
  lookups.
- An app reusing a long-cached answer generates traffic with no new lookup at all.
- A device that goes quiet reads as NOT-ROUTED, which never quarantines.

Turn it on only after a few days of verdicts look right for your devices. Keep a way back in: a host in
`QUARANTINE_EXEMPT`, and `release --all`.

### Off the tunnel: the captive-portal rule (`ALWAYS_ON_HOSTS`)

Some devices should always be on the tunnel, for example a phone set to use this box as its exit node with "block
connections without VPN". For those hosts (`ALWAYS_ON_HOSTS`, or `*` for every peer), an **off-tunnel episode** starts
when a device that was routed through this box in the last `ROUTED_RECENT_MIN` (default 30) minutes either:
- goes **tailnet-offline**; or
- stays **online but sends no traffic through this box**.

Two kinds of device are never watched for this:
- this host itself;
- any `PROXY_CLIENTS` address.

A device that was never using the exit node never pages, because the recent-routing condition still applies.

- **The alert.** After `OFF_TUNNEL_ALERT_MIN` (default 5) minutes, one priority-5 push goes to every topic in
  `NTFY_P5_TOPICS`: "*host* off the tunnel 5+ min (captive portal?)". It's one push per topic per episode.
- **Why the box can't block it then.** While the device is off the tunnel, its traffic isn't routed through this box,
  so there's nothing to drop. The push is the only thing the box can do during the outage.
- **The return hold.** When the device comes back (online, with traffic through this box) after an **alerted**
  episode, it's put on hold at once in the same quarantine sets. DNS to this host and Tailscale keep working, so it
  can be checked and released remotely. A blip shorter than the alert time is ignored.
- **Release, any of:**
  1. **Signed:** a signed captive-portal release (below) lifts the hold on the next run.
  2. **Automatic:** once the device **passes** the connection check, and at least `CAPTIVE_MIN_HOLD_MIN` (default
     10) minutes have passed since it came back.
  3. **Manual:** `sudo config/dns-leak-watch/apply.sh release <host>`, which is logged as manual.

  Each release is pushed with how it happened: signed, auto or manual.
- **Never held:** this host, `PROXY_CLIENTS` addresses, and `QUARANTINE_EXEMPT` hosts. Exempt hosts still get the
  push.
- **False positives.** Dead zones (a tunnel, a parking garage, a flight with the phone in airplane mode), a phone
  that's switched off, or a long Tailscale reconnect all look like an episode. So does a device that's simply idle
  while online. Hence the return hold has two exits that need nobody at the box: the automatic release, and the
  signed one.

**Signed release (`RELEASE_APPROVAL_CMD`).** The root run never trusts a plain file. It runs `RELEASE_APPROVAL_CMD`
as `RELEASE_APPROVAL_USER` (never root, no shell) with `check --host HOST --since EPOCH`. Only an exact `ok <id>` line
counts. The program must verify a human signature itself.

In this repository that's `scripts/captive-release-gate.sh`. It checks for an approval of kind
`captive-portal-release`, signed by a human through `scripts/approve.sh`, and re-verifies its signature with the
governance code. To release a held device, from the operator account:

    scripts/captive-release-gate.sh request --host <host> --reason "hotel Wi-Fi captive portal, checked"
    scripts/approve.sh allow <id printed by the request>

The request waits 10 minutes for the signature. An agent can make a request, but only a human approval key can sign
one.

### On-device check (`DEVICE_SSH_CHECK=yes`, Linux peers)

**Off by default.** When on, each Linux peer's evaluation window also runs the vendored, unchanged
`dns-lockdown-check.sh` on the device itself. It runs over Tailscale SSH as `DEVICE_SSH_USER` (a non-root user),
with a 60-second limit, at most every 6 hours per device. Only its `PASS`/`FAIL` lines are kept. They appear under
the device in `summary.txt` and `latest.json`, and are reported as context; they don't change the verdict.

Prerequisites:
- Tailscale SSH enabled on the device (`tailscale set --ssh`).
- An ACL `ssh` rule that lets this box's node, or its tag, connect as that user, with `action: accept`, not `check`.
  `check` would wait for a browser login.
- `resolvectl`, `dig` and `curl` on the device.

## What it proves, and what it can't

- It **can** show that a connection happened, when, to which address, port and protocol, and whether any DNS answer
  explains that address.
- It **cannot** see inside encrypted traffic. A hard-coded HTTPS connection could be an API call to a built-in address,
  or DNS over HTTPS to a server the list doesn't know. On the wire they look the same. Your evidence is the
  reverse-DNS hint (who owns the address), the timing, and repetition across sessions and phones.
- It **cannot** see anything when the phone's traffic doesn't pass through the box. Using Pi-hole only as the phone's
  DNS server shows the lookups, but **not the connections**. One of the two routed setups below is required.
- It **cannot** see DoH or DoT inside a VPN or proxy app that runs on the phone itself.

**Known false positives:**
- **CDN and cloud address reuse.** One address serves many names. When the name another device looked up is the one
  in use, that's `other-client`, not `hardcoded`. When nobody looked anything up, as with a pinned CDN address, it's
  `hardcoded` even though it's ordinary.
- **IPv6 literals in apps.** Some apps and OS services ship IPv6 addresses for connectivity checks or push services.
  Look at the reverse-DNS hint before drawing conclusions.
- **Captive portals and connectivity checks.** OS probes and portal redirects can connect without a matching lookup.
- **Long-lived caches.** A lookup from before the window reads as `cached-stale`, or as `hardcoded` once its answer
  ages out of the 30-day index. Restart the app, or the phone, before a test session.
- **Encrypted-resolver list gaps.** `doh` only knows the addresses on the lists (known plus learned). A resolver that
  isn't listed shows up as a hard-coded HTTPS destination instead.
- **Tailscale relays** were the main false positive until the known-infrastructure list (above); a relay that joined
  Tailscale's map since the last daily refresh can still read as `hardcoded` for up to a day.

## Setup A: the Pi-hole box as a Tailscale exit node (works anywhere)

1. **The Linux box:** Pi-hole for DNS, plus Tailscale with exit-node advertising. Redirecting plain DNS that arrives on
   `tailscale0` to Pi-hole is optional; this repo's `config/dns-lock/` does it, and its counter is read if present.
2. **The phone:** install Tailscale and set the box as its **exit node**. Turn on **block connections without VPN** as
   well:
   - **Android:** Settings, Network, VPN, Tailscale, then *Always-on VPN* and *Block connections without VPN*.
   - **iOS:** Tailscale's *On Demand* rules.

   Without it, the phone quietly uses the carrier whenever the tunnel drops.
3. **Point the tool at the phone.** Use the phone's two Tailscale addresses from `tailscale status`. Add `--metered`
   with any metered uplink the box uses, so the daily list refresh skips itself there:

       sudo config/dns-leak-watch/apply.sh init-conf --clients "<phone v4>,<phone v6>" --iface tailscale0 --group <your group> [--metered "<if>"]
       sudo config/dns-leak-watch/apply.sh install
    sudo config/dns-leak-watch/apply.sh diagnose 120     # does Pi-hole see the phone, or this host?

## Setup B: the Pi-hole box as the phone's network gateway

The box routes for your LAN, as the DHCP gateway or a manually set gateway. Give the phone a DHCP reservation, then:

    sudo config/dns-leak-watch/apply.sh init-conf --clients "<phone LAN v4>,<phone LAN v6>" --iface <LAN interface> --group <your group>
    sudo config/dns-leak-watch/apply.sh install

Only traffic the box forwards is seen, so turn off mobile data during a test.

## How it works

- **The nft table.** `inet ctdc_dnsleak` is loaded by its own unit, `ctdc-dnsleak-fw.service`, and touches no other
  table.
  - **What it records:** every new TCP or UDP connection forwarded from `CLIENTS` on `IFACE`, added to a dynamic set
    keyed on destination, protocol and port. Each entry has a connection counter and expires `SET_TIMEOUT` after its
    last connection.
  - **Why a set, not log lines:** logging would need a rate limit, and a rate-limited log drops entries when an app is
    busy. A set is bounded and loses nothing.
  - **Counters:** new connections, port 53, port 853, UDP 443 (QUIC) and TCP 443.
- **The watch.** `dns_leak_watch.py run` runs every 3 minutes from `ctdc-dnsleak-watch.timer`, as root at idle
  priority, from the root-owned copy in `/usr/local/libexec/ctdc-dnsleak/`. It reads only the new part of the Pi-hole
  log on each pass (the first run also reads yesterday's rotation), and classifies each new destination once.
- **The resolver list.** `ctdc-dnsleak-resolvers.timer` runs daily, at a randomized time, and fetches
  `RESOLVER_LIST_URL` (by default hagezi's `doh.txt`). It's the only thing that downloads anything. It:
  - skips the fetch while the default route is metered;
  - refuses a list with fewer than `RESOLVER_MIN` entries;
  - keeps the last good list on any failure;
  - logs the entry count and the sha256;
  - replaces the file atomically (root, 0644).

  The watch only reads that file.
- **Outputs**, in `OUT_DIR` (default `/var/lib/ctdc-dnsleak/`, 0640 root:`READ_GROUP`):

  | File | What it holds |
  |---|---|
  | `summary.txt` | the hard-coded section first, then other-client / cached-stale as context, then normal lookups by name; plus the bypass-evidence count |
  | `YYYY-MM-DD.csv` | one row per destination on the day it's first seen: time, class, protocol, port, address, Pi-hole name, reverse-DNS hint |
  | `latest.json` | the stable machine-readable form (schema 1): `hardcoded.findings`, `bypass_evidence.findings` (with the dns-lock redirect delta and total), class totals, counters, whether the resolver list is loaded; findings from the last 7 days |
  | `state.json` | what has already been reported |
- **Optional push (ntfy).** One message per run with new `hardcoded` destinations, once per destination, and a daily
  digest. The token file should hold a publish-only token. Bypass alerts come from DNS anomaly watch, not from here.
- **The first run is a baseline.** It records what's already in the sets and sends nothing.

## Running a test session

1. Restart the app being tested (force-stop it, or swipe it away). That clears its cached lookups.
2. Use it normally for 15–30 minutes. For a gig app: go online, take or decline a request, go offline, leave it in the
   background.
3. Read the **hard-coded** section of `summary.txt`. For each address, ask:
   - who owns it, from the reverse-DNS hint or WHOIS?
   - does it repeat?
   - does it line up with the app going online or offline?

## Privacy

The report lists everywhere **your** phone connected. Keep `OUT_DIR` private; that's what `READ_GROUP` is for. If you
share results, share destinations and counts. Never share the phone's own addresses, or anything that identifies a
person, a place or another device.

## Remove

`sudo config/dns-leak-watch/apply.sh remove` unloads the table and removes the units and the installed copy. The
config, the resolver list and the reports are kept.
