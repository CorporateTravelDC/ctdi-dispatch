# Security Policy

_Last Verified 2026-08-11.
## Supported versions

This repository tracks a single continuously-deployed reference system —
there are no versioned release branches. Only the current `main` (and its
public mirror `ctdi-dispatch`) receives fixes.

## Reporting a vulnerability

Email **developer@example.com**. Please do not open public issues
for security reports. Encrypt sensitive reports to the current GPG code
signing key (public keys ship in-repo, named by full fingerprint):

- `419A864CC29A09513039B6E03033FB4D01903159` — default signing key since 2026-07-07
  (the `[S]` subkey of the operator's primary key
  `3B29752DACA3544CEA60D01A7B81F49CD96C1631`; the primary's public half also
  ships as `security/trusted-signing-key.pub.asc`)
- `ABD3976FCC006E0F3FE559177286B3118BA4EFB2` — previous key, still valid for verification

Three further keys ship under `security/`. They are **not** interchangeable
with the commit-signing keys above — see "Integrity guarantees" for which
artifact each one covers (fingerprints re-derived 2026-08-23 with
`gpg --show-keys security/*.asc`, not copied forward):

- `CC1509BD9278086F113EAF24E10F126919390B37` — *CTDI Dispatch Agent
  (`sign-manifest.sh` delegate)*. This is the key that actually signs
  `MANIFEST.sha256.asc` today (verified 2026-08-23:
  `gpg --verify MANIFEST.sha256.asc MANIFEST.sha256` → `Good signature from
  "CTDI Dispatch Agent …"`). A verifier who imports only the two
  commit-signing keys above **cannot** validate the manifest. Note it ships
  bundled inside `security/trusted-signing-key.pub.asc` — that file carries
  two independent primary keys, the operator's and this one, not one key
  with a subkey.
- `C0E92095063C7AE670E590563A0E7B60576BBF22` —
  `security/pi-agent-signing-key.pub.asc`, routine automated rotation signing
  only, explicitly **not** valid for break-glass.
- `5DA4A5A13949643EB7BF93A40B0744999425A548` —
  `security/breakglass-authorization-key.pub.asc`, break-glass authorization.

## Integrity guarantees

- All public releases/commits are GPG signed with the **operator** commit-signing
  key (`git config commit.gpgsign` → `true`, `user.signingkey` →
  `3B29752DACA3544CEA60D01A7B81F49CD96C1631`). The signed integrity manifest is
  a separate artifact signed by the separate agent delegate key listed above —
  do not assume one key covers both.
- Timer-triggered skill containers (via `scripts/verified-exec.sh`) and the
  LLM entry point (`src/common/llm.py`, before every inference) verify a
  signed whole-tree manifest (`MANIFEST.sha256` + `.asc`,
  `scripts/verify-manifest.sh`) before executing; periodic sweeps
  (`corporatetraveldc-integrity-sweep.timer`) re-verify the deployed tree
  every 15 minutes. The long-running core containers
  (web/poller/pusher/ingest/runner) do **not** run the check at startup
  (verified 2026-08-19) — a stale manifest blocks skills, inference, and the
  sweep, not core-container start.
- API bearer tokens are stored as SHA-256 hashes only; tier resolution is
  strictly token-based (`src/auth/auth.py`). Public vhosts pin requests to
  Tier 0 via `X-CTDI-Public`.
- Secrets live in `/etc/corporatetraveldc/dispatch-secrets.env` (mode 0600)
  and are excluded from the public mirror by `push-public.sh` /
  `scrub-public-tree.py`; a pre-commit hook rejects staged credentials.

## CUI handling

This repository must never contain SHARES/HEARS/HEART or other FOUO/CUI
radio frequency data — see the CUI section of the README. Report any
suspected CUI leak through the same channel above, immediately.
