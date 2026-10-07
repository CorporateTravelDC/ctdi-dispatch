# Published GPG keys (/keys/)

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

Canonical set (operator-directed 2026-09-03). Source of truth:
`src/executive_standard/assets/keys/` in this repo;
`build_site()` in `src/poller/skills/executive_standard_sync.py` copies that
directory into the blog build on every sync. The main site carries its own
copy under `www/keys/` in the website repo.

| Host | URL | State on 2026-10-06 |
|---|---|---|
| Main site | `https://www.example.com/keys/<name>.pub` | **200**; all five files byte-identical to this repo (sha256 compared) |
| Blog | `https://executivestandard.example.com/keys/<name>.pub` | **302 to `/welcome`** -- behind the Executive Standard reader gate, so not publicly fetchable [UNVERIFIED: since when]. Point readers at the main-site URLs. |

| File | Fingerprint | UID | Purpose |
|---|---|---|---|
| `developer.pub` | `3B29752DACA3544CEA60D01A7B81F49CD96C1631` | Corporate Travel DC, "Rotated Production GPG Key - Rotated July 2026", `developer@example.com` | Current production key: git commit signing and, via its signing subkey `419A864CC29A09513039B6E03033FB4D01903159`, the tree manifest at HEAD. |
| `developer-legacy.pub` | `7C961E3F4AA00DACC6EAE09C4D8ECF145865A6F6` | `developer@example.com` | Superseded by the July 2026 rotation; kept published for contacts who still encrypt to it. Key material says created 2025-10-25. |
| `operator_sheldon.pub` | `9D41B32F413B1E74E22EB376DF400C5404735E0A` | the operator's business address at `example.com` | Personal/business identity, separate from the developer signing key. |
| `operatorwsheldon.pub` | `CF68244D782F2C2CDC28679D19BBCEA4C2F3AEF1` | the operator's personal ProtonMail address (in the key UID; not repeated here) | Personal identity. |
| `embargo.pub` | `FFE7969B97A3D2FB1FC1D0300C2BC838EFD1C9F2` | `embargo@example.com` | FAA LADD correspondence and security reports. |

Deliberately **not** published at `/keys/`: the CTDI Break-Glass
Authorization key, the CTDI Pi Agent Signing key, the CTDI Dispatch Agent
(`sign-manifest.sh --agent` delegate) key and the developer-laptop signing
key. Their public halves that a verifier needs ship in this repo's
`security/` directory instead (see `SECURITY.md`); a stranger verifying the
public mirror imports `security/trusted-signing-key.pub.asc`, which carries
both the operator key and the agent delegate key.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Published GPG keys (/keys/)

~~Canonical set (operator-directed 2026-09-03), published at `/keys/<name>.pub` on both the blog (`executivestandard.example.com`, live) and the main site (`www.example.com`, also live -- verified 2026-09-03 19:15 EDT, both serving bytes identical to this repo's copies; this doc's original "staged pending `./deploy.sh`" note was already stale at commit time, the deploy had happened). Source of truth for the files themselves is `src/executive_standard/assets/keys/` in this repo -- `build_site()` in `executive_standard_sync.py` copies whatever is actually present there into the served site on every sync, so adding a new key here is the only step needed to publish it on the blog; the main site needs the matching file dropped into `www/keys/` in the other repo and deployed separately.~~

| ~~File~~ | ~~Fingerprint~~ | ~~UID~~ | ~~Purpose~~ |
|---|---|---|---|
| ~~`developer.pub`~~ | ~~`3B29752DACA3544CEA60D01A7B81F49CD96C1631`~~ | ~~Corporate Travel DC (the operator) "Rotated Production GPG Key - Rotated July 2026" `<developer@example.com>`~~ | ~~Current production key -- git commit signing, day-to-day.~~ |
| ~~`developer-legacy.pub`~~ | ~~`7C961E3F4AA00DACC6EAE09C4D8ECF145865A6F6`~~ | ~~`<developer@example.com>`~~ | ~~Superseded by the July 2026 rotation to `developer.pub`. (Key material says created 2025-10-25 -- this doc's original "created 2026-07-03, five days before" claim contradicted the .pub itself; corrected 2026-09-03.) Kept published deliberately, not stale cruft -- backward compatibility for encrypted chat/mail from contacts who still have this as the recipient key.~~ |
| ~~`operator_sheldon.pub`~~ | ~~`9D41B32F413B1E74E22EB376DF400C5404735E0A`~~ | ~~`<operator@example.com>`~~ | ~~Personal/business identity, separate from the developer signing key.~~ |
| ~~`operatorwsheldon.pub`~~ | ~~`CF68244D782F2C2CDC28679D19BBCEA4C2F3AEF1`~~ | ~~`<owner@example.com>`~~ | ~~Personal ProtonMail identity.~~ |
| ~~`embargo.pub`~~ | ~~`FFE7969B97A3D2FB1FC1D0300C2BC838EFD1C9F2`~~ | ~~`<embargo@example.com>`~~ | ~~FAA LADD (Limiting Aircraft Data Displayed) correspondence and anything security-report related.~~ |

~~Deliberately NOT published: the CTDI Break-Glass Authorization key, the CTDI Pi Agent Signing key, the CTDI Dispatch Agent (Claude Code sign-manifest.sh delegate) key, and the "Developer Laptop GPG Signing Key" -- all four are internal/automated-signing keys with no reason for an external party to hold or verify against them.~~
