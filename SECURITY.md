# Security Policy

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

> Review trail: how this document reached its current state, pass by pass, is recorded in `docs/security-reviews/` (start at its `README.md`).

## Supported versions

One continuously deployed reference system; no versioned release branches.
Only `main` of the private repo (and its scrubbed public mirror `ctdi-dispatch`)
receives fixes.

## Reporting a vulnerability

Email **developer@example.com** (or **embargo@example.com**
for anything embargo/LADD-sensitive). Do not open public issues for security
reports. Encrypt to a key from `docs/GPG_KEYS_PUBLISHED.md`; the public halves
are served at `https://www.example.com/keys/`.

## Keys and what each one covers

Fingerprints re-derived 2026-10-06 with `gpg --show-keys security/*.asc` and
`gpg --verify MANIFEST.sha256.asc MANIFEST.sha256` (isolated keyring).

| Fingerprint | File | Covers |
|---|---|---|
| `3B29752DACA3544CEA60D01A7B81F49CD96C1631` (primary), signing subkey `419A864CC29A09513039B6E03033FB4D01903159` | `security/trusted-signing-key.pub.asc` (first key block) | Git commit signing (`commit.gpgsign=true`, `user.signingkey` = the primary) **and the signed tree manifest at HEAD**: `MANIFEST.sha256.asc` at db64018 was made by subkey `419A86…3159` (2026-10-06 17:42Z). |
| `CC1509BD9278086F113EAF24E10F126919390B37` | `security/trusted-signing-key.pub.asc` (second, independent key block) | The agent delegate key used by `scripts/sign-manifest.sh --agent` and by `poller/skills/audit_log_archive.py` for audit checkpoints. Pinned as `AGENT_SIGNING_KEY_FINGERPRINT`; `verify-manifest.sh` accepts a manifest signed by either pinned key. |
| `C0E92095063C7AE670E590563A0E7B60576BBF22` | `security/pi-agent-signing-key.pub.asc` | Routine automated rotation signing only; not valid for break-glass. |
| `5DA4A5A13949643EB7BF93A40B0744999425A548` | `security/breakglass-authorization-key.pub.asc` | Break-glass authorization. |

The previous code-signing key, `ABD3976FCC006E0F3FE559177286B3118BA4EFB2`
("Default GPG Code Signing Key for CorporateTravelDC Repositories"), ships at
the repo root as `ABD3976FCC006E0F3FE559177286B3118BA4EFB2.gpg` and
`CorporateTravelDC.gpg` (both tracked), alongside
`419A864CC29A09513039B6E03033FB4D01903159.gpg` (the current key). It is kept
for verifying older commits; no current control pins it, and it is not in the
published `/keys/` set.

SSH keys are a separate system (see `docs/BOARD_SIGNING.md`): each team account
has its own ed25519 board/kill-order signing key, and humans hold a separate,
passphrase-protected approval key (`~/.ssh/<account>_approver_ed25519`).

## Deployment model: why the controls are shaped this way

This platform began as a **single-operator deployment** and was then made ready for **multiple human and agent user accounts**. Each account has its own Unix identity, signing keys, liveness switch and scoped grants, and only a human's passphrase-protected approval key can approve anything. Those controls hold against every principal that is not the operator's own account.

An agent running **as the human operator's own account** is a designed fallback from the single-operator origin. It is an **exception**: its safety rests on the operator's operational hygiene with their own account and their own agentic use case. The recommended rollout is a **confined, organizationally managed agent account**, or the agent gateway. Agents in personal accounts are **not recommended, and are not allowed as an organizational policy in a larger deployment**. Details: `docs/AGENT_TRUST_MODEL.md` §0.

## Integrity guarantees (what is enforced, and where)

- **Signed whole-tree manifest.** `scripts/sign-manifest.sh` hashes every
  tracked and non-ignored file; `scripts/verify-manifest.sh` checks the
  signature in an isolated keyring, then asserts the signing fingerprint
  against the root-owned pin `/etc/corporatetraveldc/signing-pin`
  (root:root 0644; it takes precedence over the tracked `security/signing.env`),
  then checks every hash and full coverage.
- **Prevention, at start:** the 39 live timer-triggered skill quadlets run
  through `scripts/verified-exec.sh` (`grep -l verified-exec
  ~/.config/containers/systemd/*.container | wc -l` = 39), and
  `src/common/llm.py::_verify_before_inference()` runs before every inference.
- **Detection, after the fact:** the long-running containers (web, poller,
  pusher, ingest-*, runner) start straight from `CMD` with no verifying
  entrypoint (`podman inspect` of the web container: empty entrypoint). Their
  coverage is the 15-minute `corporatetraveldc-integrity-sweep.timer`
  (`OnUnitActiveSec=15min`) plus `scripts/stack-refresh.sh`'s audit, which
  gates every locally built image before it is restarted. See
  `docs/COMPLIANCE_SECURITY.md` §2.
- **Root never executes the checkout.** Every root-run script runs from
  root-owned copies in `/usr/local/libexec/ctdc/`, installed by
  `scripts/install-root-copies.sh` only after the checkout verifies and each
  file's hash matches its manifest entry; fail2ban actions re-check each copy
  with `installed-check.sh` before running it.
- **API credentials.** Bearer tokens are stored as SHA-256 hashes
  (`src/auth/auth.py::_hash_token`); tier resolution is token-only, and the
  public `dispatch.example.com` vhost stamps `X-CTDI-Public: 1` on
  every proxied location, which pins the request to Tier 0 before any token
  lookup. Admin tokens can be narrowed to named actions
  (`auth_tokens.allowed_actions`, migration 0070). Admin calls, allowed and
  denied, are written to `audit_log` by `require_admin(action)`; on Postgres
  that table is hash-chained (migration 0062). ~~The chain is computed by a
  database trigger and is not verified by any code, and signed approvals,
  console sign-ins, agent-gateway and reader-access events are recorded in
  their own ordinary tables, not in `audit_log`.~~ Since 2026-10-07 the
  governance events (approvals, console, agent gateway, reader access) are in
  the chain too, and the integrity sweep re-verifies the whole chain every
  15 minutes (`docs/AGENT_TRUST_MODEL.md` §11).
- **Human-in-the-loop approvals** are an SSH signature from a human approval
  key over the exact request (`scripts/approve.sh`, `src/common/governance.py`);
  a phone tap or link can only deny. What this does and does not bind, where
  it holds and where it is policy only (processes running as the operator
  account; passwordless sudo rules): `docs/AGENT_TRUST_MODEL.md`.
- **Secrets.** The master secret file is
  `/etc/corporatetraveldc/dispatch-secrets.env` (0600, operator-owned). No
  container reads it any more: since 2026-10-05 every first-party container
  gets a generated per-service file under `/etc/corporatetraveldc/svc/<svc>.env`
  (root:corporatetraveldc 0640, names allowlisted in
  `scripts/service-env/<svc>.allowlist`). Secrets never enter the public mirror
  (`scripts/push-public.sh` + `scripts/scrub-public-tree.py`); a pre-commit hook
  (`.git/hooks/pre-commit`, a copy of `scripts/pre-commit`) rejects staged
  credential patterns.

Trust boundary, stated plainly: the operator account (`corporatetraveldc`,
uid 1000) owns the checkout, the master secret file and the passphrase-less
board key, and is in `wheel`. Anything running as that uid, including an agent
session started by the operator, can do what the operator can do except
approve gated actions (separate passphrase-protected key) and run sudo
without the operator's password. The agent accounts are separate uids with
read-only repo access (see `docs/AGENT_SEGMENTATION.md`).

## CUI handling

This repository must never contain SHARES/HEARS/HEART or other FOUO/CUI radio
frequency data -- see `README.md` "CUI handling". Report any suspected CUI
leak through the channel above, immediately.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Security Policy

~~_Rewritten 2026-08-11. The previous revision was untouched GitHub template boilerplate (fictional "5.1.x / 4.0.x" version tables) and described nothing about this project._~~


### Security Policy › Supported versions

~~This repository tracks a single continuously-deployed reference system — there are no versioned release branches. Only the current `main` (and its public mirror `ctdi-dispatch`) receives fixes.~~


### Security Policy › Reporting a vulnerability

~~Email **developer@example.com**. Please do not open public issues for security reports. Encrypt sensitive reports to the current GPG code signing key (public keys ship in-repo, named by full fingerprint):~~

- ~~`419A864CC29A09513039B6E03033FB4D01903159` — default signing key since 2026-07-07 (the `[S]` subkey of the operator's primary key `3B29752DACA3544CEA60D01A7B81F49CD96C1631`; the primary's public half also ships as `security/trusted-signing-key.pub.asc`)~~
- ~~`ABD3976FCC006E0F3FE559177286B3118BA4EFB2` — previous key, still valid for verification~~

~~Three further keys ship under `security/`. They are **not** interchangeable with the commit-signing keys above — see "Integrity guarantees" for which artifact each one covers (fingerprints re-derived 2026-08-23 with `gpg --show-keys security/*.asc`, not copied forward):~~

- ~~`CC1509BD9278086F113EAF24E10F126919390B37` — *CTDI Dispatch Agent (`sign-manifest.sh` delegate)*. This is the key that actually signs `MANIFEST.sha256.asc` today (verified 2026-08-23: `gpg --verify MANIFEST.sha256.asc MANIFEST.sha256` → `Good signature from "CTDI Dispatch Agent …"`). A verifier who imports only the two commit-signing keys above **cannot** validate the manifest. Note it ships bundled inside `security/trusted-signing-key.pub.asc` — that file carries two independent primary keys, the operator's and this one, not one key with a subkey.~~
- ~~`C0E92095063C7AE670E590563A0E7B60576BBF22` — `security/pi-agent-signing-key.pub.asc`, routine automated rotation signing only, explicitly **not** valid for break-glass.~~
- ~~`5DA4A5A13949643EB7BF93A40B0744999425A548` — `security/breakglass-authorization-key.pub.asc`, break-glass authorization.~~

**~~Integrity guarantees~~** *(former heading)*


### Security Policy › Integrity guarantees

- ~~All public releases/commits are GPG signed with the **operator** commit-signing key (`git config commit.gpgsign` → `true`, `user.signingkey` → `3B29752DACA3544CEA60D01A7B81F49CD96C1631`). The signed integrity manifest is a separate artifact signed by the separate agent delegate key listed above — do not assume one key covers both.~~
- ~~Timer-triggered skill containers (via `scripts/verified-exec.sh`) and the LLM entry point (`src/common/llm.py`, before every inference) verify a signed whole-tree manifest (`MANIFEST.sha256` + `.asc`, `scripts/verify-manifest.sh`) before executing; periodic sweeps (`corporatetraveldc-integrity-sweep.timer`) re-verify the deployed tree every 15 minutes. The long-running core containers (web/poller/pusher/ingest/runner) do **not** run the check at startup (verified 2026-08-19) — a stale manifest blocks skills, inference, and the sweep, not core-container start.~~
- ~~API bearer tokens are stored as SHA-256 hashes only; tier resolution is strictly token-based (`src/auth/auth.py`). Public vhosts pin requests to Tier 0 via `X-CTDI-Public`.~~
- ~~Secrets live in `/etc/corporatetraveldc/dispatch-secrets.env` (mode 0600) and are excluded from the public mirror by `push-public.sh` / `scrub-public-tree.py`; a pre-commit hook rejects staged credentials.~~


### Security Policy › CUI handling

~~This repository must never contain SHARES/HEARS/HEART or other FOUO/CUI radio frequency data — see the CUI section of the README. Report any suspected CUI leak through the same channel above, immediately.~~
