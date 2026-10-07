# Headless Administration Access (template)

> Generalized, sanitized template of a private document (`docs/HEADLESS_ACCESS.md`).
> No real hostnames, addresses, accounts, tailnet names or key material.
> Verified against the private source on 2026-10-06 22:45Z / 18:45 ET.

How a single headless dispatch node (no monitor, no keyboard, no desktop) is
administered, in order of preference. This documents the channels and their
trust models, not general Tailscale usage. Placeholders: `<node>` (the
node's tailnet machine name), `<operator>` (the operator's local Unix
account), `<tailnet>.ts.net`, `<owner>` (the tailnet owner identity),
`192.0.2.10` (the node's tailnet address).

## What to check before trusting this doc on your own box

| Check | Expected |
|---|---|
| `systemctl is-active tailscaled` | `active`, native distro unit (not a container) |
| `tailscale debug prefs` | `RunSSH: true`; your server tags; `OperatorUser: <operator>`; `CorpDNS: false` if the node is itself the tailnet resolver; exit-node routes only if you advertise them |
| `tailscale lock status` | enabled, this node signed, the list of trusted signing keys |
| `tailscale ip -4` | the node's stable tailnet address |
| `which rpi-connect` | not found (break-glass relay not installed) |
| `/dev/ttyAMA0` (or your board's UART) | present |

## 1. Primary: Tailscale SSH

`tailscaled` runs as the native system service with host networking.
Tailscale SSH intercepts port 22 **on the tailnet address only**;
authorization comes from the tailnet policy, not from `sshd_config` or
`authorized_keys`.

**Policy.** Keep a tracked copy of the tailnet policy in the repo
(`tailscale/policy.hujson`, identities scrubbed) and re-export it whenever
the admin console changes, so the two can be diffed. Reference rule:

```hujson
"ssh": [{
  "action": "check",
  "src":    ["<owner>"],
  "dst":    ["tag:<server-tag>", "autogroup:self"],
  "users":  ["autogroup:nonroot", "<operator>"],
}]
```

- `action: check` -- Tailscale re-authenticates the owner periodically
  (step-up), so a stolen laptop session does not give indefinite shell.
- `users: autogroup:nonroot` -- any non-root local account may be the
  target. On a segmented box that includes team accounts that have a login
  shell (agent runtimes named `<prefix>-agent-<vendor>-<product>`); service
  accounts carry `/usr/sbin/nologin` and are therefore not reachable. Root is
  never a target: escalation on the box is the operator's password sudo or a
  signed approval (see `docs/SUDO_JUSTIFICATION_PROPOSAL.md`).
- Every tag the node advertises should have a tag owner and, if it grants
  anything, a rule in the tracked policy. An advertised tag with no policy
  entry is harmless but confusing -- either use it or drop it.

**LAN path, separately.** OpenSSH may also listen on `0.0.0.0:22` with the
host firewall opening ssh on the LAN zone only. That path uses
`authorized_keys`, not the tailnet policy. Rule for a segmented team: each
account holds exactly one key whose comment names the account
(`<account>@<node>`), no key is shared between accounts, and a verifier
script (`scripts/agent-segmentation/verify.sh`) asserts both.

### Re-applying tags / prefs

1. Change the tailnet policy first (admin console or API), merging, not
   replacing.
2. Then the device. `tailscale set` cannot change tags; use `tailscale up`
   with every non-default pref restated (recent clients refuse and name any
   pref you forgot):

   ```sh
   sudo tailscale up \
     --ssh \
     --advertise-tags=tag:<server-tag>[,tag:<second-tag>] \
     [--advertise-exit-node] \
     --accept-dns=false \
     --stateful-filtering=false \
     --operator=<operator>
   ```

   Cross-check against `tailscale debug prefs` before reuse (auto-update,
   for example, is a pref and not one of these flags).
3. Verify: `tailscale status` shows the tags; from an admin device,
   `tailscale ssh <operator>@<node>`.

**SELinux note.** `tailscale status` may warn that Tailscale SSH needs policy
help under enforcing SELinux. In the reference deployment it works because
`tailscaled` runs as `unconfined_service_t`. If a session dies at login,
check `ausearch -m avc -ts recent` first; keep a local module base in the
repo (`selinux/<name>-tailscaled.te`) for when it is needed.

### Tailnet Lock -- treat it as live policy

With Tailnet Lock enabled (the reference deployment runs three trusted
signing keys, one of them on this node), any node-key rotation (`tailscale
up` with changed tags, `--force-reauth`, `logout`/`login`) leaves the node
**locked out** until a trusted signer runs the exact `tailscale lock sign
nodekey:… tlpub:…` printed by `tailscale lock status`. If the node is also
the tailnet's DNS resolver, a locked-out node breaks name resolution for
every device. Lessons from a real lockout that forced a full tailnet
rebuild:

1. Change flags/tags in **one clean pass**: run once, approve the auth URL
   once, sign once. Each retry mints a new node key needing its own
   signature.
2. Sign the **current** pair, from a trusted signer that is not this node;
   confirm which signer devices are actually online before any rotation.
3. A locked-out node can still show `Running`; trust `tailscale lock status`
   and the admin console during recovery.
4. Never recover a signing device with `down` + `login` (a locked tailnet
   refuses fresh registration); use `tailscale switch` to its existing
   profile.

Hold the lock's disablement secret off-box. If a copy also lives on the node,
keep it in the operator's home at `0600` and out of every repo.

## 2. Emergency fallback: vendor relay (out of fabric)

A third-party remote-access relay (for a Raspberry Pi: Raspberry Pi Connect)
has its own auth and sits outside the tailnet policy. **Not installed** in
the reference deployment. If ever used as break-glass: enable, use, disable,
and never describe it as part of the policy-scoped access story.

## 3. Last resort: UART serial console (physical)

A 3.3 V USB-serial adapter on the board's debug header gives a console
independent of all networking. Physical possession is the trust model.

## Design rules this spec encodes

- One policy-scoped primary channel (tailnet SSH, `check` action, non-root
  targets only); LAN sshd is a separate, key-based path with one attributed
  key per account.
- No inbound port on the public internet for administration; the public
  tunnel carries application traffic only.
- Root is never an SSH target; privilege is a separate, signed or
  password-gated step on the box.
- Out-of-fabric channels are off by default and documented as break-glass,
  not as part of the access model.
