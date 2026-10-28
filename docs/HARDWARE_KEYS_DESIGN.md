# Hardware-backed signing: design (proposal, not wired) -- 2026-10-13, operator decisions recorded

> **Status (2026-10-15):** still a proposal. No code references a key class, attestation or `verify-required`, and no hardware key is enrolled; every approval today is a software passphrase-key SSH signature (`docs/AGENT_TRUST_MODEL.md` §3). Tooling versions in §1 re-checked: OpenSSH 10.2p1, libfido2 1.16.0, OpenSC 0.27.1, GnuPG 2.4.9. (Re-checked 2026-10-25 21:53Z: unchanged -- same four versions; `approval_signers` has columns `account, pubkey, key_comment, active, registered_at, deactivated_at, note`, no key-class column, and one active signer whose key type is `ssh-ed25519`; `grep -rn` for `verify-required`, `attestation` or a key class in `src/` and `scripts/` ~~finds only the unrelated board-presence attestation in `src/common/db.py`~~ (corrected 2026-10-25, re-checked 2026-10-26 22:00Z: `grep -rln` over `src/` and `scripts/` matches 11 files, all for the unrelated board-presence attestation; none for a key class or hardware `verify-required`).)

Operator request 2026-10-13. Goal: high-leverage actions (commits, pushes, approvals, lockdown thaw) can
require a **physical key plus a secret the operator knows**, without locking anyone into one vendor or one
key model. YubiKey 5C is the operator's default; the design must also work with older YubiKeys and other
vendors' keys. Builds on the existing approval system (SSH signatures over a canonical request,
`scripts/approve.sh`, `common/governance.py`) and the lockdown cadence. Respects the standing rule: **no
ssh-agent or any passphrase-caching agent** (corrected 2026-10-25 for precision: no ssh-agent is enabled --
`ssh-agent.service` is `indirect`, `gpg-agent.socket` disabled, `SSH_AUTH_SOCK` unset in the operator session --
but GnuPG's own `gpg-agent` runs for signing with its cache cut to one second (`default-cache-ttl 1`,
`max-cache-ttl 1`) and `enable-ssh-support` present in `~/.gnupg/gpg-agent.conf`) -- every option below works without one.

## 1. Standards, not models

The platform asks for a *capability*, never a model. Three open standards cover nearly every hardware key:

| Standard | What it gives | Works on (examples) | On this box |
|---|---|---|---|
| **FIDO2 / U2F security keys in OpenSSH** (`sk-ecdsa-sha2-nistp256@openssh.com`, `sk-ssh-ed25519@openssh.com`) | SSH auth and `ssh-keygen -Y sign` signatures; touch on every use; with `-O verify-required` also the key's PIN | `sk-ecdsa`: any FIDO2 or U2F key (YubiKey 4 included, which is U2F). `sk-ed25519`: FIDO2 keys with Ed25519 (YubiKey 5 firmware 5.2.3+, many others) | OpenSSH 10.2 + libfido2 1.16 |
| **PIV smartcard (PKCS#11)** | an ordinary key that never leaves the card; PIN required (slots 9a/9c); touch is a vendor extension | YubiKey 4/5, most PIV cards and tokens | OpenSC `opensc-pkcs11.so` |
| **OpenPGP card** | GPG signing (commits, the integrity manifest) on the card; PIN; touch is a vendor extension | YubiKey 4/5, Nitrokey, Token2, others | GnuPG 2.4 `gpg-card` |

Capability ladder used at enrollment, best first:
1. `sk-ssh-ed25519` with `verify-required` (touch + PIN), then 2. `sk-ecdsa-sha2-nistp256` with
`verify-required`, then 3. `sk-ecdsa` touch-only (U2F-only keys, e.g. YubiKey 4: no PIN at the FIDO
layer, so pair it with a passphrase), then 4. PIV slot 9a/9c with PIN (+ touch policy where the vendor
supports it), then 5. OpenPGP card with PIN (+ touch where supported). A deployment can raise the
minimum rung.

Notes on the operator's model questions:
- **YubiKey 4** has no FIDO2, but it does have U2F, so `sk-ecdsa` (touch, no PIN) works; PIV and OpenPGP
  also work. It is not locked out; it lands on rungs 3-5.
- **YubiKey 5 below firmware 5.7:** `ed25519-sk` works from 5.2.3. What 5.7 added is Ed25519/X25519 in
  **PIV**, larger PIV RSA sizes and more resident FIDO2 credentials. So pre-5.7 keys use FIDO for Ed25519
  and RSA/ECC P-256 for PIV.
- **PIV slots:** 9a (authentication) and 9c (digital signature) require the PIN; 9c asks for it on every
  use. **9e (card authentication) needs no PIN by default and 9d is for encryption**, so neither is used
  for a "key plus something you know" requirement.

## 2. Proving it is hardware (pinning and attestation)

A public key alone does not prove the private key lives in hardware (except `sk-*` types, which can only
exist on a security key). Enrollment therefore records an **attestation**:
- FIDO: `ssh-keygen -t ecdsa-sk|ed25519-sk -O write-attestation=...`, verified against the vendor's root via
  the **FIDO Metadata Service (MDS3)**, the cross-vendor root list.
- PIV: the slot's attestation certificate chained to the vendor's PIV attestation root (vendor-specific,
  YubiKey supported first).
- OpenPGP card: vendor attestation where available (YubiKey 5.2+); otherwise enrollment is marked
  "hardware, operator-asserted" and ranks below attested keys.

The registry (`approval_signers`, plus a new key class column) records: account, public key, key class
(`software` | `hardware-attested` | `hardware-asserted`), standard (fido / piv / openpgp), PIN enforced
(yes/no), touch enforced (yes/no), attestation hash, enrolled-at. Pins are also listed in the signing-key
documentation (`docs/BOARD_SIGNING.md`, `docs/GPG_KEYS_PUBLISHED.md`) as today.

## 3. Modes (per deployment setting)

| Mode | Normal actions | Step-up / high-risk / thaw |
|---|---|---|
| `software` (today) | on-box passphrase key | on-box passphrase key |
| `hardware-backstopped` (proposed default) | on-box passphrase key, current timeouts | **passphrase key + a pinned hardware key** (two signatures over the same canonical request), or one hardware credential with PIN |
| `hardware-reliant` | pinned hardware key only | pinned hardware key with PIN **and** touch |

"Not necessarily the same device": the two signatures may come from different keys and devices (e.g. the
passphrase key on the box and the hardware key at the operator's laptop). The canonical request text binds
them to the same action.

## 4. When a hardware key is required (backstopped mode)

- **Step-up after a failure:** a passphrase prompt that timed out or failed puts that action (and that
  account) into step-up for a window (default 30 min): the retry needs the passphrase key **and** a pinned
  hardware key. A guessed or captured passphrase alone is not enough to retry.
- **High-risk, always:** public push, any change to signer/key registries, credential rotation, sudo
  approvals, kill orders and gateway thaw, lockdown thaw, council close of a security matter. Each needs a
  hardware credential with its PIN (rungs 1, 2, 4, 5) or a passphrase key plus a touch-only key (rung 3).
- **Lockdown thaw:** hardware-only, with PIN, from a pinned, attested key. Optionally also the second
  signature from a different device.

## 5. Beat the clock under lockdown (links the operator's 2026-10-13 idea)

As lockdown criteria trend (not only when lockdown is set): shorten and randomize pinentry and the box's own
SSH login grace; require the announced-prompt challenge (two random words shown in the relay terminal and
in the phone push) before any passphrase; alarm on any unannounced prompt; at lockdown, disable push
remotes until a hardware-signed approval re-enables them. Remote side: branch protection (signed commits
from pinned keys only, no force-push or deletion) on both repos, so a stolen software push key cannot
rewrite history. Recovery is a clean-room rebuild from a repo whose HEAD is cross-witnessed (backup ledger
on Proton, vault, phone push).

## 6. Open questions for the operator

1. **Where is the key when you sign?** Signing happens on the box today, but you are usually remote. Either
   the key is plugged into the box (works only when you are there), or high-risk approvals are signed
   **off-box** where the key is (laptop, or the phone if its SSH app can use a USB-C/NFC key) and submitted:
   `approve.sh message`/`submit` already supports off-box signatures. This decides the whole workflow.
2. Is your 5C the **NFC** model (affects phone use)?
3. Default mode: `hardware-backstopped` everywhere, `hardware-reliant` for this deployment, or reliant only
   during lockdown?
4. Git: keep GPG commit signing on an OpenPGP card, or move commits to SSH signatures from a FIDO key (one
   key for signing and pushing; GitHub verifies both)?

## 7. Staging (each a signed batch with tests)

1. Registry: key class, standard, PIN/touch flags, attestation; enrollment script with the capability
   ladder; `approve.sh` accepts sk/PIV signatures (`ssh-keygen -Y verify` already handles sk keys).
2. Policy: modes, step-up window after failed/timed-out prompts, high-risk action list, two-signature
   canonical requests.
3. Lockdown: thaw requires hardware; beat-the-clock tightening; push freeze; announced-prompt challenge.
4. Git and repos: commit signing choice, GitHub branch protection, cross-witnessed release anchors,
   clean-room rebuild runbook.

## 8. Operator decisions (2026-10-13)

1. **Signing location:** remote by default (off-box: the operator signs where the key is and submits), but
   the code supports all three placements, chosen per deployment:
   - `remote`: the key stays with the operator; the box prints the canonical request, the signature comes
     back (`approve.sh message` / `submit`, extended to every high-risk action).
   - `local`: the key is plugged into the box (operator on site).
   - `hsm`: a server-attached HSM (PKCS#11, e.g. a YubiHSM 2 or any PKCS#11 HSM) holds keys for the box's
     own automated signatures under policy; it can never satisfy the human-presence factor, so it backs
     machine actions (manifest signing, release anchors), never an approval in place of a person.
2. **Key:** YubiKey 5C **NFC**, so phone signing over NFC is realistic. Phone path to verify in stage 1:
   OpenPGP-card signing over NFC (e.g. OpenKeychain) produces an OpenPGP signature, so the approval verifier
   should accept an OpenPGP signature from a pinned card key as well as an SSH (`-Y sign`) signature.
3. **Policy:** high-risk actions are hardware-backstopped (passphrase key + pinned hardware key, or a
   hardware credential with PIN); a retry of a high-risk action after a failed or timed-out prompt is the
   same. **Everything else stays on today's on-box keys in normal operation and becomes hardware-reliant
   (pinned hardware key with PIN only) while lockdown is set** (operator confirmation 2026-10-13: "on
   lockdown").
4. **Git:** hardware signing is the default for commits; the **public** mirror accepts either a software or
   a hardware signature, or both (hybrid), per deployment.
5. **Lockdown is conditional on the deployment (operator 2026-10-13):** when a deployment has at least one
   pinned hardware key enrolled, hardware is the **default** for lockdown -- every signature while locked
   down, and the thaw (securing the box back out of lockdown), require a pinned hardware key with PIN. A
   deployment with no hardware enrolled keeps today's lockdown and thaw path (signed approvals and the
   kill-order quorum with software keys), so the platform never strands a deployment that has no keys.
   Enrolling the first hardware key switches the deployment's lockdown default automatically; removing the
   last one is itself a high-risk action (hardware-backstopped while any key remains).
6. **Hybrid deployments and the two-person rule (operator 2026-10-13).** For high-risk actions, lockdown
   signatures and the thaw:
   - **Single trusted admin:** two factors from that admin -- a software (passphrase) signature **and** a
     hardware signature over the same canonical request.
   - **Multi-user deployment:** a **two-person rule** (quorum, as in the nuclear "two-man rule"): signatures
     from at least **two distinct human accounts** over the same canonical request. If the deployment has
     any hardware key enrolled, **at least one** of the two signatures must be hardware-backed (pinned, with
     PIN). With no hardware enrolled, both are passphrase-key signatures.
   - The same person can never count twice (two keys of one account are one person), and the requester can
     never be one of the two (the existing requester-is-not-approver rule).
   - This extends today's kill-order quorum (~~one admin, or two non-admins~~ (corrected 2026-10-25: for a
     member, service or agent target one admin issuer or two distinct non-admin issuers; for an admin target
     two distinct issuers of any role, `QUORUM_FOR_ADMIN` 2, operator may set 3 -- ~~`scripts/kill-order.sh:16-18`,
     `scripts/team-liveness.sh:492-498`~~ (corrected 2026-10-25: enforced at `scripts/team-liveness.sh:115-116` and `:611-614`))) to every high-risk action, with
     the hardware condition added.
