# Published GPG keys (/keys/)

Canonical set (operator-directed 2026-09-03), published at
`/keys/<name>.pub` on both the blog (`executivestandard.example.com`,
live) and the main site (`www.example.com`, also live --
verified 2026-09-03 19:15 EDT, both serving bytes identical to this
repo's copies; this doc's original "staged pending `./deploy.sh`" note
was already stale at commit time, the deploy had happened). Source of truth
for the files themselves is `src/executive_standard/assets/keys/` in this
repo -- `build_site()` in `executive_standard_sync.py` copies whatever is
actually present there into the served site on every sync, so adding a
new key here is the only step needed to publish it on the blog; the main
site needs the matching file dropped into `www/keys/` in the other repo
and deployed separately.

| File | Fingerprint | UID | Purpose |
|---|---|---|---|
| `developer.pub` | `3B29752DACA3544CEA60D01A7B81F49CD96C1631` | Corporate Travel DC (the operator) "Rotated Production GPG Key - Rotated July 2026" `<developer@example.com>` | Current production key -- git commit signing, day-to-day. |
| `developer-legacy.pub` | `7C961E3F4AA00DACC6EAE09C4D8ECF145865A6F6` | `<developer@example.com>` | Superseded by the July 2026 rotation to `developer.pub`. (Key material says created 2025-10-25 -- this doc's original "created 2026-07-03, five days before" claim contradicted the .pub itself; corrected 2026-09-03.) Kept published deliberately, not stale cruft -- backward compatibility for encrypted chat/mail from contacts who still have this as the recipient key. |
| `operator_sheldon.pub` | `9D41B32F413B1E74E22EB376DF400C5404735E0A` | `<operator@example.com>` | Personal/business identity, separate from the developer signing key. |
| `operatorwsheldon.pub` | `CF68244D782F2C2CDC28679D19BBCEA4C2F3AEF1` | `<owner@example.com>` | Personal ProtonMail identity. |
| `embargo.pub` | `FFE7969B97A3D2FB1FC1D0300C2BC838EFD1C9F2` | `<embargo@example.com>` | FAA LADD (Limiting Aircraft Data Displayed) correspondence and anything security-report related. |

Deliberately NOT published: the CTDI Break-Glass Authorization key, the
CTDI Pi Agent Signing key, the CTDI Dispatch Agent (Claude Code
sign-manifest.sh delegate) key, and the "Developer Laptop GPG Signing
Key" -- all four are internal/automated-signing keys with no reason for
an external party to hold or verify against them.
