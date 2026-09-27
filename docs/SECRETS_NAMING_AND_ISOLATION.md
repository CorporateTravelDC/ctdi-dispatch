# Secrets naming convention and SELinux isolation registry

Operator directive 2026-09-24. This file is the canonical registry. A credential
that is not listed here is not governed — add the row when you add the file.

## The convention

```
<source>-<consumer>.<ext>
```

- **`source`** — who ISSUED the credential (the vendor, provider, or authority).
  Short, lowercase, stable. Never the thing consuming it.
- **`consumer`** — what USES it on this box. If one source issues several
  credentials for different consumers, the consumer disambiguates them.
- **`ext`** — `token` (bearer/API), `key` (private key material), `env`
  (key=value file), `pass` (password), `pub.asc` (public key — not a secret).

Read it as "**Cloudflare's token, for the honeypot**": `cf-honeypot.token`.
That file was already correct before this convention was written, which is why
it became the model rather than an exception.

**Separators:** hyphens, not underscores. Existing underscore names are
grandfathered and renamed opportunistically — a rename is a coordinated change
(file, consumer, fcontext rule, and any `EnvironmentFile`), never a drive-by.

### Registered source prefixes

| Prefix | Source |
| --- | --- |
| `cf` | Cloudflare |
| `gh` | GitHub |
| `ntfy` | ntfy push service |
| `pmb` | ProtonMail Bridge |
| `ts` | Tailscale |
| `faa` | FAA (SWIM, AIM-FNS) |
| `nws` | National Weather Service (NWWS-OI) |
| `ctdi` | CorporateTravelDC internal (signing, break-glass, device) |
| `csex` | [operator LLC] (website signing) |
| `afio` | airframes.io |
| `pidx` | Podcast Index |
| `sdme` | speed.me |

New source? Add the prefix to this table in the same change that adds the file.

## SELinux type naming

```
ctdc_secret_<source>_<consumer>_t     one credential
ctdc_secrets_dir_t                    the ~/.secrets directory itself
```

So `cf-honeypot.token` → `ctdc_secret_cf_honeypot_t`. The type name is the
filename with separators normalised, which makes the mapping mechanical and
means a reviewer can check it without consulting this table.

**The isolation rule, stated once:** a confined domain gets `dir search` on
`ctdc_secrets_dir_t` and `file read` on **exactly the one type it owns**. Never
read on the directory, and never on a second credential's type. Directory
*listing* is itself disclosure — fail2ban has no business knowing
`github_pat.token` exists — so `search` is granted and `read` on the dir is not.

Adding a consumer is a type declaration plus one `allow` line. Blast radius is
one file by construction, not by discipline.

## Registry

Status legend: **isolated** = has its own type and allow rule; **pending** =
consumer still runs unconfined, so a label would buy nothing yet.

| File | Source | Consumer | SELinux type | Status |
| --- | --- | --- | --- | --- |
| `cf-honeypot.token` | Cloudflare | fail2ban `cf-honeypot-ban.sh` (zone firewall rules, one zone) | `ctdc_secret_cf_honeypot_t` | **isolated** |
| `cf_management_api.token` | Cloudflare | dispatch — CF Access / management API (**account-scoped**) | `ctdc_secret_cf_management_t` | pending |
| `airframes.token` | airframes.io | `ingest/acars.py` | `ctdc_secret_afio_ingest_t` | pending |
| `aim_fns.token` | FAA | SWIM AIM-FNS feed (`push:fns`) | `ctdc_secret_faa_fns_t` | pending |
| `nwws-oi.token` | NWS | NWWS-OI XMPP ingest | `ctdc_secret_nws_ingest_t` | pending |
| `ntfy-fcm.token` | ntfy | push delivery (FCM) | `ctdc_secret_ntfy_fcm_t` | pending |
| `remote-admin-agentic.token` | CTDI | skills — dispatch admin API (`auth_tokens` id 23) | `ctdc_secret_ctdi_skills_t` | pending |
| `github_pat.token` | GitHub | git push / `gh` API | `ctdc_secret_gh_push_t` | pending |
| `board.key` | CTDI | `populate-secrets.sh`, board tokens | `ctdc_secret_ctdi_board_t` | pending |
| `protonmailbridge_smtp_ntfy_dispatch.token` | ProtonMail Bridge | ntfy → SMTP, dispatch topics | `ctdc_secret_pmb_ntfy_dispatch_t` | pending |
| `protonmailbridge_smtp_ntfy_health.token` | ProtonMail Bridge | ntfy → SMTP, health topics | `ctdc_secret_pmb_ntfy_health_t` | pending |
| `protonmailbridge_smtp_website.token` | ProtonMail Bridge | website contact mail | `ctdc_secret_pmb_website_t` | pending |
| `corporatetraveldc_tailscale0.key` | Tailscale | tailnet node auth | `ctdc_secret_ts_node_t` | pending |
| `podcastindex.key` / `.token` | Podcast Index | podcast ingest | `ctdc_secret_pidx_ingest_t` | pending |
| `speedofme.token` | speed.me | link speed probe | `ctdc_secret_sdme_probe_t` | pending |
| `ctdi-signing.env` | CTDI | manifest signing — **GPG fingerprints only, not secret** | n/a | not a secret |
| `csexec-website-signing.env` | [operator LLC abbreviation] | website signing — **fingerprints only, not secret** | n/a | not a secret |
| `*.pub.asc` (4 files) | various | public keys — trusted-signing, break-glass, pi-agent | n/a | not a secret |
| `bridgepass` | ProtonMail Bridge | Bridge SMTP auth — the relay all outbound mail goes through | `ctdc_secret_pmb_bridge_t` | pending |
| `tailnet-disable` | Tailscale | tailnet disable / kill-switch | `ctdc_secret_ts_disable_t` | pending |
| `ctdi_corporateatravler-asus` | CTDI | **laptop** device credential | `ctdc_secret_ctdi_laptop_t` | pending |
| `ctdi_corporateatravler-graphene` | CTDI | **phone** device credential (GrapheneOS) | `ctdc_secret_ctdi_phone_t` | pending |

All four resolved by the operator 2026-09-24; none is orphaned.

### Renames owed under the convention

These four predate the convention and should be renamed when their consumers
are next touched. A rename is a coordinated change — file, consumer, fcontext
rule, any `EnvironmentFile` — never a drive-by.

| Current | Should be | Note |
| --- | --- | --- |
| `bridgepass` | `pmb-bridge.pass` | no source prefix, no extension |
| `tailnet-disable` | `ts-tailnet-disable.key` | no source prefix, no extension |
| `ctdi_corporateatravler-asus` | `ctdi-laptop.key` | underscores; and **`corporateatravler` is a typo** for `corporatetraveler`, which is itself the wrong axis — the device is the consumer, so name it `laptop` |
| `ctdi_corporateatravler-graphene` | `ctdi-phone.key` | same; `graphene` names the OS, not the role |

**Cross-reference to `auth_tokens`.** The two device credentials line up with
active admin tokens confirmed by the operator 2026-09-23:

- `ctdi-phone` ↔ id 21, `corporatetravel` / `admin-grapheneOS`, expires 2027-08-29
- `ctdi-laptop` ↔ id 6, `corporatetraveler` / `mobile-browser`, **never expires**

Worth noting id 6 carries no expiry while its sibling does. Not a finding on its
own — the operator confirmed all seven active tokens as valid, access-controlled
devices — but an admin token with no expiry is the kind of thing that should be
a deliberate choice rather than a default, and the asymmetry between two device
credentials of the same class suggests it may not have been.

## Why "pending" is the right state, not laziness

Every pending consumer currently runs `unconfined_service_t`, where SELinux
makes no access decisions at all. Labelling those files today changes nothing
for security and risks breaking readers for no gain.

**The label follows the confinement, not the other way round.** When a service
is confined, its credential gets its type and its allow rule in the same change.
fail2ban is isolated first because it is the only confined host consumer on this
box — which is precisely why it was the only one that could not read its own
token, and how this whole gap surfaced.

Containers are already confined (`container_t` with per-container MCS
categories) and do not read `~/.secrets` — they receive credentials through
systemd `EnvironmentFile` injection. The remaining exposure is entirely
host-side: **31 active host services, all unconfined.**

## Adding a credential — checklist

1. Name it `<source>-<consumer>.<ext>`; add the source prefix above if new.
2. `chmod 600`. Every file in `~/.secrets` is `600` — no exceptions.
3. Add a registry row, including the consumer. No orphans.
4. If the consumer is confined: declare `ctdc_secret_<source>_<consumer>_t` in
   `selinux/corporatetraveldc-secrets-isolation.te`, add the one `allow` line,
   add the `semanage fcontext` rule, `restorecon`.
5. If the consumer is unconfined: mark **pending** and stop. Do not label it.
