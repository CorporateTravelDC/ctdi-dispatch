# Security Policy

_Rewritten 2026-08-11. The previous revision was untouched GitHub template
boilerplate (fictional "5.1.x / 4.0.x" version tables) and described nothing
about this project._

## Supported versions

This repository tracks a single continuously-deployed reference system —
there are no versioned release branches. Only the current `main` (and its
public mirror `ctdi-dispatch`) receives fixes.

## Reporting a vulnerability

Email **developer@csexecutiveservices.com**. Please do not open public issues
for security reports. Encrypt sensitive reports to the current GPG code
signing key (public keys ship in-repo, named by full fingerprint):

- `419A864CC29A09513039B6E03033FB4D01903159` — default signing key since 2026-07-07
- `ABD3976FCC006E0F3FE559177286B3118BA4EFB2` — previous key, still valid for verification

## Integrity guarantees

- All public releases/commits are GPG signed with the keys above.
- Containers and LLM skills verify a signed whole-tree manifest
  (`MANIFEST.sha256` + `.asc`, `scripts/verify-manifest.sh`) before
  executing; periodic sweeps (`corporatetraveldc-integrity-sweep.timer`)
  re-verify the deployed tree every 15 minutes.
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
