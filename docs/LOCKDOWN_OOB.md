# Lockdown out-of-band (OOB) management channel

Status: added 2026-11-18. Code: `scripts/ctdc-oob.sh` (root, installed copy), `scripts/lib/ctdc_oob.py`
(logic), `scripts/oob-pin-build.sh` (operator). Config: one strict JSON file; the documented example
is `config/network/oob.example.json`. A deployment's real file (`config/network/oob.json`) and its pin
list are private and never reach the public mirror.

## What it does

A lockdown (`scripts/lockdown.sh`) cuts host reach for the containers and freezes withheld data. It
also opens a narrow management channel, so the operator can still reach the box, and only the
operator can:

1. **Uplink.** The VPN (Tailscale by default; see `vpn` below) is pinned to an OOB uplink. The uplink is the first configured candidate
   that has carrier and a default route and is not listed in `never`. A candidate can be an interface
   name or a NetworkManager connection id.
2. **Hard cap, whatever the uplink** (operator 2026-11-18). The interface carrying the channel is
   shaped to `hard_cap_kbit` (default 2048 kbit/s) each way from the moment the channel opens, satellite
   and wired links included; a candidate may only lower it. The shaping is cake `diffserv4` on the link
   and on its ifb: tailscaled's packets go in the Voice tin (egress: skb priority set by nft from the
   tailscaled cgroup; ingress: WireGuard UDP to tailscaled's port), everything else in Bulk (DERP-relay
   TCP from port 443 in Best Effort). The aim is a usable SSH-over-Tailscale session down to
   `floor_kbit` (default 256 kbit/s, a carrier's throttle once the high-speed allowance is gone). The
   hard cap comes off only when the channel closes.
3. **Metered uplinks: a SOFT budget.** When a metered candidate's OOB data budget is used up, one
   priority-5 alert goes out and the channel stays up at the hard cap (`on_budget: soft`, the
   default). When the most conservative usage estimate says the high-speed allowance itself is gone,
   the shaping drops to `throttled_kbit` (default 230, just under the floor), so this box's queue,
   where tailscaled goes first, is the bottleneck rather than the carrier's. `on_budget: cut`
   (disconnect; OOB moves to the next candidate) is available but never the default.
4. **Policy routing.** Only tailscaled's own sockets are pinned. nft marks them by systemd cgroup, an
   `ip rule` on that fwmark sends them through a dedicated routing table via the OOB uplink (IPv4 and
   IPv6), and they are masqueraded on it. Other traffic routes normally.
5. **sshd.** A drop-in that sorts first: listen on the tailnet addresses only, public keys only, keys
   from the pin list only, the allowed accounts only, never root. With FIDO in force, only hardware
   (`sk-*`) keys are accepted. For the low floor it also sets `Compression yes`, `TCPKeepAlive no`,
   `ClientAliveInterval 30`, `ClientAliveCountMax 8` (small, infrequent keepalives inside the tunnel).
6. **Break-glass commands.** These are GPG-signed and allowlisted: `status`, `clear-hold`,
   `restore-network` and `end-lockdown`.

## Authentication: the VPN is the first gate, and it stays in force during a lockdown

Note for deploying organizations (operator 2026-11-18). Reaching the channel already requires an
authenticated user before a packet ever reaches this box's sshd:

1. **The VPN's own identity.** With Tailscale, the device and its user must be signed in to the
   organization's tailnet through its identity provider (and that provider's MFA, device approval,
   key expiry and ACL rules). A lockdown does not suspend any of this. The channel only pins the VPN
   daemon's traffic to an uplink; it never opens a port to the public internet.
2. **This box's pinned keys.** sshd accepts only keys on the pin list, for the allowed accounts, never
   root, and only hardware keys once FIDO is in force (see below).
3. **Signed break-glass commands.** Only the allowlisted commands, signed by a pinned break-glass key.

An organization decides who may do what in each state, through two layers it controls:

- **Its VPN policy:** which users and devices may reach this box at all, in normal running and during a
  lockdown (for example, a tailnet ACL that admits only a lockdown-approved group to this host).
- **This box's config:** which validated keys are lockdown-approved (`pins`, with a role per key),
  which accounts may log in (`ssh.allowed_accounts`), whether hardware keys are required
  (`ssh.fido`), and which keys may sign which break-glass commands (`breakglass.signers`,
  `breakglass.commands`). A key or account that is not listed is refused in every state.

**Other VPNs.** The channel does not depend on Tailscale; the VPN is one config block, `vpn`
(the superseded name `tailscale` is still accepted and means the same block; giving both is an
error). Any organizational VPN that runs as a systemd service and authenticates its own users can
take Tailscale's place by setting:

- `vpn.service`: the systemd unit whose sockets are pinned to the OOB uplink;
- `vpn.interface`: its tunnel interface, whose addresses sshd listens on (default `tailscale0`);
- `vpn.protocol` and `vpn.port`: its transport, which gets the priority tin on the OOB link
  (default `udp` 41641, WireGuard; `null` for no port match);
- `vpn.relay_tcp_port`: its TCP relay fallback, put in Best Effort (default 443, Tailscale's DERP;
  `null` to drop that match, for example for a VPN that already runs over TCP).

Example for a TCP VPN on `tun0`:
`"vpn": {"service": "corp-vpn.service", "interface": "tun0", "protocol": "tcp", "port": 1194, "relay_tcp_port": null}`.

`scripts/restore-network.sh` closes the channel. The policy routing and the sshd drop-in are removed,
and every metered candidate with `cap_on_leave` is capped again. A lockdown that survives a reboot
is re-asserted by the boot guard, which re-runs `lockdown.sh`. The guard's early `--boot` pass first
removes a stale sshd drop-in, because a tailnet address does not exist that early.
`corporatetraveldc-oob-budget.timer` runs every minute while the channel is open. It counts bytes,
applies the budget action, and re-asserts the routing and the drop-in after a tailscaled restart, a
late tailnet address or a link change.

## Configuration (`/etc/ctdc-oob/oob.json`, installed from `config/network/oob.json`)

Unknown keys are errors at every level, as in `src/common/deployment_profile.py`. A config that does
not validate is refused. `leave` still removes the routing and the drop-in.

| key | meaning | default |
|---|---|---|
| `version` | `1` | required |
| `candidates` | ordered list; each `{"interface": NAME}` or `{"connection": NM_ID}`, optional `metered` | required |
| `never` | interface names or connection ids that must never carry OOB (e.g. the primary uplink); a candidate may not also be listed here | `[]` |
| `on_budget` | `soft` (budget used up: alert, the channel stays up at the hard cap) or `cut` (disconnect the link; OOB moves to the next candidate). `cap` and `alert-only` are accepted as the superseded names for `soft` | `soft` |
| `hard_cap_kbit` | the cap on WHATEVER interface carries the channel, each way (64..1000000); a candidate's own `hard_cap_kbit` may only be lower | `2048` |
| `floor_kbit` | the rate the channel must stay usable at (a carrier's post-allowance throttle); `prove-floor` tests it | `256` |
| `throttled_kbit` | the shaping rate once the high-speed allowance looks used up (at most `floor_kbit`) | 90% of `floor_kbit` |
| `cap_rate` | the NORMAL-times cake rate of a metered link (`cap_on_leave`, and while it is not the OOB link), e.g. `2mbit` | `2mbit` |
| `vpn` (superseded name: `tailscale`) | `service` (systemd unit), `interface` (tunnel interface sshd listens on), `protocol` (`udp`/`tcp`), `port` (its transport port, priority tin; `null` = none), `relay_tcp_port` (TCP relay fallback, Best Effort; `null` = none), `table`, `rule_priority` (< 5210, ahead of tailscale's own rules), `mark`/`mask` (outside tailscale's `0xff0000` bits) | `tailscaled.service`, `tailscale0`, `udp`, 41641, 443, 3313, 5200, `0xcdc`/`0xfff` |
| `ssh` | `enabled`, `allowed_accounts`, `fido`: `auto` / `required` / `off` | on; the operator; `auto` |
| `pins` | pin SOURCES, each with a `role` label (below) | `[]` |
| `breakglass` | `enabled`, `commands` (subset of the four), `max_ttl_hours` (1-168), `signers` (gpg pin roles) | off |

A `metered` block has these keys:

- required: `allowance_gb` (decimal GB) and `cycle_day` (1-28);
- `timezone` (default `UTC`): the cycle resets on `cycle_day` at 00:00 local time;
- required: `baseline_used_gb` and `baseline_at` (UTC), which come from the carrier;
- optional: `baseline_vnstat_month_bytes`;
- `budget_fraction` (default 0.5);
- optional: `cap_rate`;
- `cap_on_leave` (default true);
- optional: `other_usage_gb` with `other_usage_at` (UTC): use of the same allowance this box cannot
  see, e.g. a phone on the same plan.

A candidate (metered or not) may carry `hard_cap_kbit`, lower than the global one.

### Budget math: three sources, the most conservative wins

Operator 2026-11-18: "soft over 3 sources available. Cached at is not live evidence." The budget is
fixed when the lockdown starts, from three sources, each reported with its age:

- **live**: this box's vnstat bytes on the device since the cycle start. Live, but it sees only this
  box.
- **carrier**: the operator's carrier figure (`baseline_used_gb`) plus this box's vnstat bytes since
  that figure's own time (`baseline_at`). A carrier app shows a CACHED reading, so the figure is
  recorded at the carrier's "as of" time and always labelled "carrier figure as of TIME, not live". A
  figure from an earlier cycle is ignored.
- **other**: `other_usage_gb` as of `other_usage_at`, use of the same allowance this box cannot see
  (a phone on the same plan, which typically fails over at the same moment). Added on top; a figure
  from an earlier cycle is ignored.

- **Estimated used** = max(live, carrier) + other. **Remaining** = allowance - estimated used.
- **Budget.** `budget_fraction` x remaining.
- **When neither live nor carrier can be computed** (no vnstat data, a counter below the baseline),
  the budget is **0** (alerted at once), and the channel stays up at the hard cap: an unknown
  allowance is not treated as a used-up one.

During the lockdown, the kernel byte counter of the link is summed every minute; a counter reset is
handled. When the sum reaches the budget, the `on_budget` action runs and one priority-5 alert goes
out. When it reaches the remaining allowance, the link drops to `throttled_kbit` and a second
priority-5 alert goes out. A repeated lockdown trigger re-asserts the channel and never resets a
budget.

To re-baseline from the carrier's figure, give the time the carrier shows for it:

    sudo /usr/local/libexec/ctdc/ctdc-oob.sh rebaseline --candidate cN --used-gb N.NN --as-of 2026-11-18T17:55:00Z

To record use this box cannot see (a phone on the same plan):

    sudo /usr/local/libexec/ctdc/ctdc-oob.sh other-usage --candidate cN --used-gb N.NN --as-of 2026-11-18T17:55:00Z

Both write `/etc/ctdc-oob/baseline-overrides.json`, which wins while it is newer than the config's
figure. Fold the figures into the config at the next release. `ctdc-oob.sh status` shows all three
sources with their ages and the remaining allowance.

### Proving the floor

    sudo /usr/local/libexec/ctdc/ctdc-oob.sh prove-floor [--seconds 300]

Operator-run, outside a lockdown only (it refuses while the channel is active). It pins tailscaled to
the candidate a lockdown would use now, shapes that link to `floor_kbit` each way, prints the two
commands to run from the phone (Tailscale on, Wi-Fi off), and waits for Enter or the timeout. Then
it removes the pin and puts the link back to its normal-times treatment, however it ends.

### Pin sources and roles

| `source` | fields | what is pinned |
|---|---|---|
| `board_signers` | `account` | that account's registered board/kill-order SSH key (with its role and active flag) |
| `approval_signers` | `account` | that account's approval SSH key (active only while both signer rows are active) |
| `ssh_pubkey_file` | `account` (the login account), `path` | one OpenSSH public key from a file |
| `gpg_asc` | `path`, optional `fingerprint` (expected) | an OpenPGP key's v4 fingerprint, for break-glass commands only |

`scripts/oob-pin-build.sh` runs as the operator, because the signer tables are not readable to
agents. It writes the pin list with role, source, key type and SHA256 or OpenPGP fingerprint only,
and never a key body. A source that cannot be found is recorded as `missing` and the build continues;
nothing is invented. The public key material goes to a directory for root:

    scripts/oob-pin-build.sh --material-dir DIR
    sudo /usr/local/libexec/ctdc/ctdc-oob.sh install-keys DIR

Root checks every SSH key and every `.asc` against the installed pins again before using it. The
per-account `authorized_keys` files are built only from pinned, active fingerprints of allowed
accounts. Anything else is rejected and reported.

### FIDO (`ssh.fido`)

- `off`: file keys and hardware keys are both accepted.
- `required`: only `sk-ssh-ed25519` / `sk-ecdsa-sha2-nistp256` keys, and sshd's
  `PubkeyAcceptedAlgorithms` is restricted to them.
- `auto`: as `required` once **at least one pinned SSH key is a hardware key on an active signer row
  of an admin account**; until then, file keys are accepted. A non-admin or inactive hardware key
  does not switch it.

The mode is re-derived on every pin build and every time root reads the pin list, so a hand-edited
`mode:` line is refused. See `docs/HARDWARE_KEYS_DESIGN.md` for enrolling keys.

### Break-glass commands

A command is a small ASCII file with exactly these keys:

    ctdc-oob-breakglass: 1
    command: status | clear-hold | restore-network | end-lockdown
    nonce: <32-64 lowercase hex, fresh each time>
    expires-utc: YYYY-MM-DDTHH:MM:SSZ   (in the future, at most max_ttl_hours ahead)
    reason: <3-200 printable characters>

Make a detached signature for it with a key pinned under `breakglass.signers`. Then do either of
these:

- Drop `NAME.cmd` and `NAME.cmd.sig` into `/var/lib/ctdc-oob/inbox/`. The drop folder is write-only
  for the operator group. A path unit runs the command, and the result appears in
  `/var/lib/ctdc-oob/outbox/NAME.result`.
- Run `sudo /usr/local/libexec/ctdc/ctdc-oob.sh breakglass NAME.cmd NAME.cmd.sig`.

Verification uses a fresh, dedicated keyring that holds only the pinned key. It never uses the
ambient keyring or an agent. The signer's primary fingerprint must equal the pin. A used nonce is
recorded before the command runs, and a replay is refused. Every attempt, accepted or refused, is
logged to `/var/log/ctdc-oob-breakglass.log` and the journal, and alerted at priority 5. What each
command does:

- `restore-network`: closes the OOB channel only. The lockdown markers stay.
- `end-lockdown`: runs `restore-network.sh`, which lifts the lockdown and closes the channel.
- `clear-hold`: clears only the uncommanded-reboot hold.

## Verifying without a lockdown

    sudo /usr/local/libexec/ctdc/ctdc-oob.sh plan     # uplink, routing + nft (nft -c), sshd drop-in (sshd -t), pins, budgets
    /usr/local/libexec/ctdc/ctdc-oob.sh status        # state and today's budget numbers (no root needed for the numbers)

## Rollback

Run `sudo /usr/local/libexec/ctdc/ctdc-oob.sh leave --reason rollback` to remove a live channel. To
remove the feature, revert the commit, re-run the root install and `daemon-reload`, then disable
`corporatetraveldc-oob-budget.timer` and `corporatetraveldc-oob-breakglass.path`.
