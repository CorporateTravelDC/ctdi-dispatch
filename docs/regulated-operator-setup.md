# Addendum: Email, Phone Call & Regulated-Industry Operator Setup

This addendum covers enabling ntfy email and phone-call notifications on a self-hosted instance, and security guidance for operators working under regulated-industry requirements (aviation/transportation, public-safety/EMS, ARES/EMCOMM).

---

## 1. Email Notifications

ntfy sends email by connecting to an SMTP relay. Two options are documented here.

### Option A — ProtonMail Bridge (recommended for privacy-conscious operators)

ProtonMail Bridge runs as a local container providing an SMTP relay for your ProtonMail account. Messages go end-to-end encrypted from the Pi to ProtonMail servers.

**First-time setup:**

```bash
bash /opt/corporatetraveldc/ctdi-dispatch-internal/install/setup-protonbridge.sh
```

The setup script prompts for OAuth login and 2FA. After completing it, retrieve the Bridge SMTP password (distinct from your ProtonMail password):

```bash
podman exec systemd-corporatetraveldc-protonbridge /protonmail/vault-editor read \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(u['BridgePass']) for u in d.get('Users',[])]"
```

Copy the output into `dispatch-secrets.env`:

```env
NTFY_SMTP_SENDER_PASS=<bridge-password-here>
```

Add to `/etc/ntfy/server.yml`:

```yaml
smtp-sender-addr: host.containers.internal:1025
smtp-sender-user: your-protonmail@pm.me
smtp-sender-from: ntfy@pm.me
```

The password is injected via `NTFY_SMTP_SENDER_PASS` environment variable — it never appears in the YAML file. The ntfy Quadlet passes the full `dispatch-secrets.env` to the container via `EnvironmentFile=`.

### Option B — Transactional SMTP (SendGrid, Mailgun, SES, etc.)

```env
NTFY_SMTP_SENDER_PASS=your-api-key-or-password
```

```yaml
smtp-sender-addr: smtp.sendgrid.net:587
smtp-sender-user: apikey
smtp-sender-from: dispatch@your-domain.com
```

---

## 2. Phone Call Notifications

ntfy supports phone call alerts via Twilio. When a subscriber has a verified phone number, ntfy places a call and reads the alert title aloud via TTS.

**Twilio setup:**

1. Create an account at [console.twilio.com](https://console.twilio.com)
2. Buy a phone number with Voice capability
3. Create a Verify service (for phone number verification)
4. Collect: Account SID, Auth Token, phone number, Verify service SID

**`dispatch-secrets.env` additions:**

```env
NTFY_TWILIO_ACCOUNT=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NTFY_TWILIO_AUTH_TOKEN=your-auth-token
NTFY_TWILIO_PHONE_NUMBER=+15551234567
NTFY_TWILIO_VERIFY_SERVICE=VAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

No changes to `server.yml` needed — ntfy reads all `NTFY_TWILIO_*` env vars automatically.

**Cost note:** Twilio charges ~$0.013/min + $1/month/number. Phone calls fire only at priority 5 (max). For lower priorities, push notification is used.

**After setting Twilio credentials:** restart ntfy, then verify your phone number in the ntfy app (Settings → Notifications → Phone number).

---

## 3. Regulated-Industry Operator Security Guidance

### Credential isolation

| Secret | Location | Notes |
|--------|----------|-------|
| SMTP bridge password | `dispatch-secrets.env` | Never in server.yml or committed files |
| Twilio credentials | `dispatch-secrets.env` | Injected at container start via EnvironmentFile |
| ntfy access tokens | `dispatch-secrets.env` | Rotate via `csex-token rotate` |
| GitHub PAT | `~/.secrets/github.token` | 30-day rotation; reminder sent via ntfy |

### CUI / FOUO handling

Credentialed radio frequencies (SHARES, HEARS, HEART) exist only in `~/.secrets/` files that populate empty placeholder configs on first boot. These files are never committed, never pushed, and never included in ntfy alert bodies.

If your jurisdiction requires CUI markings on operational documents, apply them at the document layer — not in ntfy message bodies, which traverse cleartext SMTP relay.

### Audit log

Append-only at `/var/lib/corporatetraveldc/audit.log`, 90-day retention, never leaves the Pi. For longer retention requirements, mount a tamper-evident external volume and update `AUDIT_LOG_PATH` in `dispatch.env`.

### Network isolation

- All services run rootless in Podman containers
- External access only via Cloudflare Tunnel (no open ingress ports)
- Tailscale provides identity-verified mesh access to admin endpoints
- ProtonMail Bridge SMTP relay is Pi-local only — not reachable externally

### For ARES/CERT/EMS operators

- Radio frequency data is populated locally from authorized credential sources — dispatch ships placeholder configs only
- Push alerts do **not** include raw frequency data; they reference operational state (go/no-go, TFR, weather)
- The audit log captures all VIP watchlist changes and alert dispatches for AAR review
- For ICS integration: `/api/v1/cps` provides machine-readable go/no-go state for polling by incident management software

---

## 4. Restarting ntfy after credential changes

```bash
CTUID=$(id -u corporatetraveldc)
sudo runuser -l corporatetraveldc -c \
  "XDG_RUNTIME_DIR=/run/user/${CTUID} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${CTUID}/bus \
   systemctl --user restart ntfy"
```
