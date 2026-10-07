# Operator console, Executive Standard invites, gateway kill switch

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.
Shipped 2026-10-05.

## The phone console

`https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net/console`
(`src/web/routes/console.py`).

**Reach.** Only the tailnet vhost (`nginx/conf.d/tailscale-dispatch-runner.conf`,
`location ^~ /console` -> web `127.0.0.1:8000`) forwards `/console`. Port 443
is closed on the LAN interfaces (firewalld zone `FedoraWorkstation` on `enu1`,
`wld0` opens 80/tcp but not 443; `tailscale0` is in the `trusted` zone), and
the LAN-reachable port-80 server block for the tailnet name only redirects
to https. The public vhost has `location ^~ /console { return 404; }`; in
practice Cloudflare Access answers first (302 to its login, observed
2026-10-06). The app itself returns 404 to any `Host` not in `CONSOLE_HOSTS`
(default: the tailnet name).

**Sign-in is the approval key, not a password.**

1. Tap **Sign in**. The server creates an approval (kind `console-login`,
   10-minute window) bound to a random nonce stored only in this browser's
   `__Host-ctdc_console_login` cookie, and shows `approve.sh allow <id>`.
2. Run that on the box from the phone's SSH app; it signs with the
   passphrase-protected approval key.
3. Tap **I signed it -- reload**. The page mints the session for that browser
   only. Nothing depends on a background tab polling (mobile browsers pause
   them).

One approval mints at most one session. Session: 8 h (`SESSION_TTL_S`),
cookie `__Host-ctdc_console`, Secure, HttpOnly, SameSite=Strict; every POST
carries a CSRF token derived from the session secret and compared with
`secrets.compare_digest`; only hashes are stored (`console_sessions`,
migration 0073). On every request the console re-checks that the signer is an
active `kind=human` board signer **and** an active approval signer, so an
account the liveness switch makes inert loses the console at once.

**What it does.** The same actions as `scripts/es-invite.sh` and
`scripts/agent-gateway.sh`, except two that stay signature-gated: re-opening
the gateway after a kill-all (`gateway-thaw`) and holding an agent link open
(`connector-hold`). For those the console creates the request and shows the
`approve.sh` command. Pending approvals (up to 20) are listed with their
commands; signing only happens over SSH.

Tests: `tests/web/test_console.py` (11).

## Executive Standard reader access

Logic `src/common/es_invites.py` (migration 0072). Verification runs in the
loopback container `corporatetraveldc-execstandard-verifier` (poller image,
`executivestandard-website/verifier/verify.py` mounted read-only,
`127.0.0.1:8787`, scoped env `/etc/corporatetraveldc/svc/execstandard-verifier.env`
with one secret name).

- **Invite host** `invite.executivestandard.example.com`
  (Cloudflare tunnel ingress; nginx vhost tracked in the Executive Standard
  repo; not behind Cloudflare Access; 200 on 2026-10-06). Exposes `/`, `/p`,
  `/i/<code>`; its access log maps `/i/<code>` to `/i/-`, so the code is never
  logged. Carries the honeypot snippet.
- **Members host** `members.executivestandard.example.com`: not
  signed in -> 302 to the invite host (observed 2026-10-06). Sign-in is
  `/redeem?x=<hand-off>`. Ungated paths: `/manifest.json`, `/icons/`,
  `/sw.js` only.
- **Flow:** link or code -> a page with one button (GETs spend nothing, so
  link previews cannot burn an invite) -> POST mints a single-use hand-off
  valid 120 s (`EXCHANGE_TTL_S`) -> the members host swaps it for a session
  cookie. Only hashes are stored. A device unused for 90 days
  (`SESSION_IDLE_S`) signs in again; a permanent link still works.

| | Personal invite | Promo code |
|---|---|---|
| Default | permanent until revoked | code valid 7 days, each reader's access 7 days |
| Limits | `--days N` for a short one; 1-10 devices (default 3) | code <= 30 d, access <= 30 d, never permanent; capped uses (max 10,000) |
| Shape | `https://invite.../i/<code>` | `ES-XXXX-XXXX-XX` (Crockford alphabet, 50 bits) or `https://invite.../p?code=...` |
| Sharing | a sign-in beyond the device cap signs out the oldest; more sign-ins than devices is flagged "shared?" | each use is its own anonymous grant |

Publisher controls (CLI or console): `revoke` (link + every device),
`sign-out` (devices only), `reissue` (new link, old dies, devices stay),
`revoke-promo [--readers]`, `freeze` / `thaw` (all new sign-ins),
`kill-all` (freeze + sign out every device; links survive), `retire-legacy`
(revoke the pre-invite `?token` grants). Grants keep their
`exec_standard_sources` tag, so `es-access source disable X` still cuts a
channel live.

Batch: `es-invite.sh invite --batch FILE [--days N] [--devices N]
[--campaign C]`, one `email[,name]` per line, `#` comments, up to 500
(`BATCH_MAX`). Nothing is issued if any line is invalid (errors name line
numbers only); repeats and emails with a live invite are skipped. The list
reaches the container through the environment, never argv; links go to a
unique 0600 file `~/es-invites-<UTC>-XXXXXX.csv`. Send each reader their own
line, then delete the file.

`es-invite.sh` runs inside the web container through `scripts/lib/gov-exec.sh`
with the operator's rootless podman, so no agent account can issue or revoke
reader access.

Installable apps: members as "Exec Standard"; console as "Dispatch Ops" (no
service worker -- nothing cached on the phone).

Tests: `tests/web/test_es_invites.py` (15).

## Agent gateway kill switch

`scripts/agent-gateway.sh kill-all` (or the console) calls
`common.agent_gateway.kill_all()`: sets `agent_gateway_settings.frozen=1`
(every OAuth and MCP endpoint then answers 503), revokes every connection and
disables every connector, and pushes an ntfy notice. It does **not** remove
the nginx vhost, the tunnel ingress or the web process: the hostname still
answers (with 503s from the app). To take the hostname itself down, remove the
`agents.` ingress / vhost by hand.

Re-open: `agent-gateway.sh thaw` creates a `gateway-thaw` approval and runs
`approve.sh allow` on it (your signature); then `enable` each connector and
re-link each vendor with a fresh signed `connector-link`. `disable SLUG` /
`enable SLUG` act on one connector; `revoke ACCOUNT|SLUG` ends connections.
Design and per-account authority (signer active, operator dead-man 14 days,
vendor dormancy 7 days, holds <= 90 days): `docs/AGENT_SEGMENTATION.md`
"Agent gateway". Tests: `tests/web/test_agent_gateway.py` (15).

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### (top of document)

**~~Operator console, Executive Standard invites, gateway kill switch (2026-10-05)~~** *(former heading)*


### Operator console, Executive Standard invites, gateway kill switch (2026-10-05) › The phone console

~~`https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net/console` -- tailnet only (tailscale nginx vhost `location ^~ /console` -> web :8000; the public dispatch vhost returns 404 for `/console` and the app 404s any other Host).~~

~~Sign-in is your approval key, not a password:~~

1. ~~Tap **Sign in**. The page shows `approve.sh allow <id>` (copy button).~~
2. ~~Run it from your phone's SSH app (passphrase-protected approval key).~~
3. ~~Come back and tap **I signed it -- reload**. Nothing waits on a background tab, so Vanadium / incognito pausing tabs does not matter.~~

~~The approval is bound to a random nonce that lives only in the browser that asked; anyone else who sees the approval id gets nothing. One approval mints at most one session (8 h, `__Host-`, Secure, HttpOnly, SameSite=Strict, CSRF on every form). If your account goes inert (liveness), the console signs out.~~

~~What it does: everything `scripts/es-invite.sh` and `scripts/agent-gateway.sh` do, except two things that stay signature-gated -- re-opening the agent gateway after a kill-all, and holding an agent link open. For those the console creates the request and shows you the `approve.sh` command. Pending approvals are listed with their commands (signing only ever happens over SSH).~~


### Operator console, Executive Standard invites, gateway kill switch (2026-10-05) › Executive Standard reader access

~~Logic: `src/common/es_invites.py` (migration 0072). Served by the existing loopback verifier (`executivestandard-website/verifier/verify.py`, poller image).~~

- ~~**Invite host:** `invite.executivestandard.example.com` (tunnel ingress; nginx vhost in the site repo; NOT under Cloudflare Access). Exposes only `/`, `/p`, `/i/<code>`; access log writes `/i/-`, never the code.~~
- ~~**Members:** not signed in -> 302 to the invite host (the branded static page remains only for a verifier outage). Sign-in = `/redeem?x=<hand-off>`.~~
- ~~**Flow:** link or code -> a page with one button (GETs spend nothing, so iMessage / Slack / mail-scanner previews cannot burn invites) -> POST mints a single-use 2-minute hand-off -> members swaps it for a session cookie on the members host only. Only hashes are stored.~~

|  | ~~Personal invite~~ | ~~Promo code~~ |
|---|---|---|
| ~~Default~~ | ~~permanent until you revoke it~~ | ~~code works 7 days, each reader 7 days~~ |
| ~~Limits~~ | ~~`--days N` for a short one; 1-10 devices (default 3)~~ | ~~code <= 30 d, access <= 30 d, never permanent; capped uses~~ |
| ~~Shape~~ | ~~`https://invite.../i/<32 chars>`~~ | ~~`ES-XXXX-XXXX-XX` (typo-tolerant) or `https://invite.../p?code=...`~~ |
| ~~Sharing~~ | ~~a 4th device signs out the oldest; sign-ins > devices shows as "shared?"~~ | ~~each use is its own anonymous grant~~ |

~~Publisher controls (CLI or console): revoke (link + every device), sign out devices (link survives), new link (old link dies, devices stay), stop a promo (optionally ending everyone it let in), pause new sign-ins, sign out every reader (links survive a resume). Grants keep their `exec_standard_sources` tag: `es-access source disable X` still cuts a whole channel live.~~

~~Inviting a list: `es-invite.sh invite --batch FILE [--days N] [--campaign C]` (or "Invite a list instead" on the console). One `email[,name]` per line, `#` comments allowed, up to 500. Nothing is issued if any line is invalid (the error names the line numbers only); emails repeated in the list or already holding a live invite are skipped. The list travels to the container in the environment, never on a command line, and the links go to a unique 0600 file `~/es-invites-<UTC>-XXXXXX.csv` -- send each reader their own line, then delete it.~~

~~Installable: the members edition installs as "Exec Standard" (blog icon set; `/manifest.json`, `/icons/`, `/sw.js` are the only ungated paths); the invite pages carry the same icons; the console installs as "Dispatch Ops" with the Dispatch Intelligence icon set (no service worker -- nothing cached on the phone).~~

~~Retiring the pre-invite `?token` grants: issue yourself an invite, sign in, then `es-invite.sh retire-legacy`.~~


### Operator console, Executive Standard invites, gateway kill switch (2026-10-05) › Agent gateway kill switch

~~`agent-gateway.sh kill-all` (or the console): freezes the gateway (every OAuth and MCP endpoint answers 503), revokes every link, disables every connector. Re-open: `agent-gateway.sh thaw` -> your signature (kind `gateway-thaw`), then `enable` each connector and re-link each vendor (a fresh signed `connector-link`). `disable SLUG` / `enable SLUG` do the same for one connector.~~
