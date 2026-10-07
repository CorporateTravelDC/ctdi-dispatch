# Addendum: Email, Phone Call & Regulated-Industry Operator Setup

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.

Covers ntfy email and phone-call notifications on a self-hosted instance, and
security guidance for operators under regulated-industry requirements
(aviation/transportation, public safety/EMS, ARES/EMCOMM).

---

## 1. Email notifications

ntfy sends email through an SMTP relay.

**What this instance runs:** direct Proton SMTP submission. The live
`/etc/ntfy/server.yml` (mounted read-only into the `ntfy` container, image
`docker.io/binwiederhier/ntfy:v2.25.0`) sets

```yaml
smtp-sender-addr: smtp.protonmail.ch:587
smtp-sender-from: health-alerts@example.com
```

with the SMTP user in the same file and the password supplied as
`NTFY_SMTP_SENDER_PASS` through the container's only `EnvironmentFile=`,
`/etc/corporatetraveldc/ntfy-secrets.env` (0600). [UNVERIFIED: the env file's
contents were not read.]

### Option A -- ProtonMail Bridge (alternative, not the deployed path)

A Bridge container (`corporatetraveldc-protonbridge`, published on
`100.x.x.x:1025`, tailnet only) provides a local SMTP relay. First-time
setup: `bash /opt/corporatetraveldc/private/ctdi-dispatch-internal/install/setup-protonbridge.sh`
(OAuth + 2FA prompts). Put the Bridge password in the ntfy env file as
`NTFY_SMTP_SENDER_PASS` (never in `server.yml`) and point `server.yml` at the
relay:

```yaml
smtp-sender-addr: <bridge host>:1025
smtp-sender-user: <your ProtonMail address>
smtp-sender-from: <sender address>
```

### Option B -- transactional SMTP (SendGrid, Mailgun, SES, ...)

```yaml
smtp-sender-addr: smtp.sendgrid.net:587
smtp-sender-user: apikey
smtp-sender-from: dispatch@your-domain.com
```

with `NTFY_SMTP_SENDER_PASS=<api key>` in the ntfy env file.

---

## 2. Phone call notifications (Twilio)

**Not enabled on this instance.** `server.yml` has only a comment block about
Twilio (lines 25-28), and `journalctl --user -u ntfy` since 2026-10-03 has no
Twilio lines. The comment says the credentials come from
`dispatch-secrets.env`; that is out of date -- the ntfy container no longer
reads `dispatch-secrets.env` (since the 2026-10-05 scoped-env change no
container does), only `ntfy-secrets.env`.

To enable: create a Twilio account, buy a voice-capable number, create a
Verify service, then add to **`/etc/corporatetraveldc/ntfy-secrets.env`**
(bare values, no quotes -- `EnvironmentFile=` does not strip shell quoting):

```env
NTFY_TWILIO_ACCOUNT=...
NTFY_TWILIO_AUTH_TOKEN=...
NTFY_TWILIO_PHONE_NUMBER=...
NTFY_TWILIO_VERIFY_SERVICE=...
```

ntfy reads `NTFY_TWILIO_*` from the environment without a `server.yml` block.
Restart ntfy, then verify your number in the ntfy app. When disabling, delete
the lines rather than blanking them. Calls fire only at priority 5.

---

## 3. Regulated-industry security guidance

### Credential isolation

| Secret | Location | Notes |
|--------|----------|-------|
| ntfy SMTP password, Twilio credentials | `/etc/corporatetraveldc/ntfy-secrets.env` (0600) | injected only into the ntfy container |
| Platform secrets (admin token, board key, ntfy publish token, Nextcloud app password, feed credentials) | master: `/etc/corporatetraveldc/dispatch-secrets.env` (0600); per service: `/etc/corporatetraveldc/svc/<svc>.env` (root:corporatetraveldc 0640) | each first-party container gets only the names in `scripts/service-env/<svc>.allowlist`; `scripts/rotate-credential.sh` rotates `BOARD_KEY`, `DISPATCH_ADMIN_TOKEN`, `NTFY_TOKEN`, `NEXTCLOUD_APP_PASSWORD` without printing values |
| API bearer tokens | SHA-256 hashes in `auth_tokens` | `ctdc-token` (`src/ctdc_token/cli.py`): `create` (default expiry 365 days), `list`, `revoke --prefix`; no `rotate` subcommand. Admin tokens can be scoped to named actions (`scripts/mint-agent-api-token.sh`) |
| GitHub PAT | `~/.secrets/github_pat.token` (0600) | no rotation reminder timer exists in the repo [the previous "30-day reminder via ntfy" could not be found] |

Naming and SELinux labelling of host-side secrets:
`docs/SECRETS_NAMING_AND_ISOLATION.md`.

### CUI / FOUO handling

Credentialed radio frequencies (SHARES, HEARS, HEART) never enter code,
configs, exports, ntfy bodies or this repo; the shipped configs are
placeholders. ntfy bodies traverse SMTP relays -- apply any CUI markings at
the document layer, not in push text. Agent writes to the shared vault
workspace pass the CUI/PII scrub gate.

### Audit log

The `audit_log` table in the local Postgres database (SQLite on the
dev/rollback path). It records admin API calls (allowed and denied) with the
calling token's prefix, including VIP watchlist changes (`admin.vip.*`),
watchlist mutations (`watchlist.*`) and operator-initiated pushes
(`admin.alert.push`); automatic alert fan-out from the pusher is **not**
audited per message. On Postgres every row is hash-chained (migration 0062).
Retention: rows past `AUDIT_ARCHIVE_RETENTION_DAYS` (default 90) are archived
to the operator's Nextcloud with a GPG-signed checkpoint and a signed on-box
stub (`poller/skills/audit_log_archive.py`), never deleted bare; the old
90-day prune is retired. Details: `docs/COMPLIANCE_SECURITY.md` §3.

### Network isolation

- Every application service runs in rootless Podman.
- Public HTTPS arrives only through the Cloudflare Tunnel (nginx on
  127.0.0.1:80); `dispatch.example.com` is additionally behind
  Cloudflare Access.
- On the LAN interfaces firewalld (zone `FedoraWorkstation`) opens ssh, mdns,
  samba-client, dhcpv6-client and 80/tcp; port 443 and the service ports are
  not open there.
- `tailscale0` is a trusted zone; admin surfaces (runner `:8001`, console,
  llama `:8093`, Bridge `:1025`) bind the tailnet IP or loopback.

### For ARES/CERT/EMS operators

- Radio frequency data is populated locally from authorized sources; the
  platform ships placeholders only.
- Push alerts carry operational state (go/no-go, TFR, weather), never raw
  frequencies.
- For ICS integration, `GET /api/v1/cps` returns machine-readable go/no-go
  state (Tier 0).

---

## 4. Restarting ntfy after credential changes

As the operator (rootless podman, user unit):

```bash
systemctl --user restart ntfy
```

From another account with sudo:

```bash
CTUID=$(id -u corporatetraveldc)
sudo runuser -l corporatetraveldc -c \
  "XDG_RUNTIME_DIR=/run/user/${CTUID} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${CTUID}/bus \
   systemctl --user restart ntfy"
```

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup

~~**Verified against the live system 2026-08-11.**~~

~~This addendum covers enabling ntfy email and phone-call notifications on a self-hosted instance, and security guidance for operators working under regulated-industry requirements (aviation/transportation, public-safety/EMS, ARES/EMCOMM).~~

**~~1. Email Notifications~~** *(former heading)*


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 1. Email Notifications

~~ntfy sends email by connecting to an SMTP relay. Two options are documented here.~~

~~**What's actually deployed (verified 2026-08-23):** neither option below, exactly — the live `/etc/ntfy/server.yml` uses **direct Proton SMTP submission**, not the local Bridge relay:~~

*Superseded block:*
```text superseded
smtp-sender-addr: smtp.protonmail.ch:587
smtp-sender-user: health-alerts@example.com
smtp-sender-from: health-alerts@example.com
```

~~(Proton's SMTP submission service — SMTP AUTH on 587 with a per-address token, injected via `NTFY_SMTP_SENDER_PASS` as below.) The Bridge setup in Option A remains a valid alternative but is not what this instance runs.~~

**~~Option A — ProtonMail Bridge (alternative; not the deployed path)~~** *(former heading)*


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 1. Email Notifications › Option A — ProtonMail Bridge (alternative; not the deployed path)

~~ProtonMail Bridge runs as a local container providing an SMTP relay for your ProtonMail account. Messages go end-to-end encrypted from the Pi to ProtonMail servers.~~

~~**First-time setup:**~~

*Superseded block:*
```text superseded
bash /opt/corporatetraveldc/ctdi-dispatch-internal/install/setup-protonbridge.sh
```

~~The setup script prompts for OAuth login and 2FA. After completing it, retrieve the Bridge SMTP password (distinct from your ProtonMail password):~~

*Superseded block:*
```text superseded
podman exec systemd-corporatetraveldc-protonbridge /protonmail/vault-editor read \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(u['BridgePass']) for u in d.get('Users',[])]"
```

~~Copy the output into `dispatch-secrets.env`:~~

*Superseded block:*
```text superseded
NTFY_SMTP_SENDER_PASS=<bridge-password-here>
```

~~Add to `/etc/ntfy/server.yml`:~~

*Superseded block:*
```text superseded
smtp-sender-addr: host.containers.internal:1025
smtp-sender-user: your-protonmail@pm.me
smtp-sender-from: ntfy@pm.me
```

~~The password is injected via `NTFY_SMTP_SENDER_PASS` environment variable — it never appears in the YAML file. The ntfy Quadlet passes the full `dispatch-secrets.env` to the container via `EnvironmentFile=`.~~

**~~Option B — Transactional SMTP (SendGrid, Mailgun, SES, etc.)~~** *(former heading)*


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 1. Email Notifications › Option B — Transactional SMTP (SendGrid, Mailgun, SES, etc.)

*Superseded block:*
```text superseded
NTFY_SMTP_SENDER_PASS=your-api-key-or-password
```

**~~2. Phone Call Notifications~~** *(former heading)*


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 2. Phone Call Notifications

~~ntfy supports phone call alerts via Twilio. When a subscriber has a verified phone number, ntfy places a call and reads the alert title aloud via TTS.~~

> ~~**Not enabled on this instance (verified 2026-08-23).** All four `NTFY_TWILIO_*` keys exist in `dispatch-secrets.env` and `/etc/ntfy/server.yml` has no Twilio block. This section is setup guidance for enabling it, not a description of a running capability — no priority-5 alert on this box places a phone call today (three days of `journalctl --user -u ntfy` contain zero Twilio lines).  **Correction 2026-08-23 — the four keys are not "empty", they are set to the literal string `CHANGE_ME`.** That distinction matters and is worth keeping straight before anyone "enables" this: an unset/empty var and a var carrying a real nine-character value are not the same input to ntfy, which reads all `NTFY_TWILIO_*` env vars automatically (see below) rather than requiring a `server.yml` block to opt in. It happens to be benign today — ntfy is not attempting Twilio calls — but do not reason about this as "unconfigured because the value is blank." When enabling, replace the placeholders outright; when *disabling*, delete the lines rather than blanking them, and re-read the bare-values rule in CLAUDE.md ("Operational conventions for agents") first — these files are consumed via systemd `EnvironmentFile=`, which does not strip shell-style quoting.~~

~~**Twilio setup:**~~

1. ~~Create an account at [console.twilio.com](https://console.twilio.com)~~
2. ~~Buy a phone number with Voice capability~~
3. ~~Create a Verify service (for phone number verification)~~
4. ~~Collect: Account SID, Auth Token, phone number, Verify service SID~~

~~**`dispatch-secrets.env` additions:**~~

*Superseded block:*
```text superseded
NTFY_TWILIO_ACCOUNT=YOUR_ACCOUNT_SID
NTFY_TWILIO_AUTH_TOKEN=YOUR_AUTH_TOKEN
NTFY_TWILIO_PHONE_NUMBER=+15551234567
NTFY_TWILIO_VERIFY_SERVICE=YOUR_VERIFY_SERVICE_SID
```

~~No changes to `server.yml` needed — ntfy reads all `NTFY_TWILIO_*` env vars automatically.~~

~~**Cost note:** Twilio charges ~$0.013/min + $1/month/number. Phone calls fire only at priority 5 (max). For lower priorities, push notification is used.~~

~~**After setting Twilio credentials:** restart ntfy, then verify your phone number in the ntfy app (Settings → Notifications → Phone number).~~

**~~3. Regulated-Industry Operator Security Guidance~~** *(former heading)*


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 3. Regulated-Industry Operator Security Guidance › Credential isolation

| ~~Secret~~ | ~~Location~~ | ~~Notes~~ |
|--------|----------|-------|
| ~~SMTP bridge password~~ | ~~`dispatch-secrets.env`~~ | ~~Never in server.yml or committed files~~ |
| ~~Twilio credentials~~ | ~~`dispatch-secrets.env`~~ | ~~Injected at container start via EnvironmentFile~~ |
| ~~API bearer tokens~~ | ~~SHA-256 hashes in the dispatch DB~~ | ~~Managed via `ctdc-token` (`src/ctdc_token/cli.py`). There is no `rotate` subcommand — rotate by `ctdc-token revoke --prefix <ctdc_user_>` then `ctdc-token create`~~ |
| ~~ntfy access token~~ | ~~`dispatch-secrets.env` (`NTFY_TOKEN`)~~ | ~~Rotate in the ntfy server config, then update the env file~~ |
| ~~GitHub PAT~~ | ~~`~/.secrets/github_pat.token`~~ | ~~30-day rotation; reminder sent via ntfy. (Corrected 2026-08-23 — an earlier revision named this `github.token`, which does not exist on disk.)~~ |


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 3. Regulated-Industry Operator Security Guidance › CUI / FOUO handling

~~Credentialed radio frequencies (SHARES, HEARS, HEART) exist only in `~/.secrets/` files that populate empty placeholder configs on first boot. These files are never committed, never pushed, and never included in ntfy alert bodies.~~

~~If your jurisdiction requires CUI markings on operational documents, apply them at the document layer — not in ntfy message bodies, which traverse cleartext SMTP relay.~~


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 3. Regulated-Industry Operator Security Guidance › Audit log

~~The audit trail is the `audit_log` table in the platform database, never leaves the Pi. Which engine holds it depends on `DISPATCH_DB_BACKEND`: `sqlite` → `/var/lib/corporatetraveldc/corporatetraveldc.db`; `postgres` → the local Postgres instance (`audit_log` is one of the tables that moves — see `docs/POSTGRES_MIGRATION.md`). Retention is 90 days, enforced by the daily `audit_log_prune` poller skill (`db.prune_audit_log(days=90)`). There is no separate audit log file and no `AUDIT_LOG_PATH` variable — for longer retention requirements, adjust the prune skill's retention window and/or archive the table to a tamper-evident external volume.~~


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 3. Regulated-Industry Operator Security Guidance › Network isolation

- ~~All services run rootless in Podman containers~~
- ~~External access only via Cloudflare Tunnel (no open ingress ports)~~
- ~~Tailscale provides identity-verified mesh access to admin endpoints~~
- ~~ProtonMail Bridge SMTP relay (when used) binds the tailnet IP (`100.x.x.x:1025`) — tailnet-reachable, not public; note it is not loopback-only~~


### Addendum: Email, Phone Call & Regulated-Industry Operator Setup › 3. Regulated-Industry Operator Security Guidance › For ARES/CERT/EMS operators

- ~~Radio frequency data is populated locally from authorized credential sources — dispatch ships placeholder configs only~~
- ~~Push alerts do **not** include raw frequency data; they reference operational state (go/no-go, TFR, weather)~~
- ~~The audit log captures all VIP watchlist changes and alert dispatches for AAR review~~
- ~~For ICS integration: `/api/v1/cps` provides machine-readable go/no-go state for polling by incident management software~~
