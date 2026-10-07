# Snapshot: agent trust model, review 01 (as committed, 8259513)

> **Frozen record. Not current.** This is `docs/AGENT_TRUST_MODEL.md` exactly as committed in `8259513` on 2026-10-07, the version the external reviewer read. The current canonical document is `docs/AGENT_TRUST_MODEL.md`; the pass that changed it is `2026-10-07-02-hardening.md`. Chain index: `README.md` in this directory.

---

# Agent and operator trust model

Verified against the code at HEAD and the running system on 2026-10-07 12:00Z–13:30Z / 08:00–09:30 ET.

This document states what the platform **demonstrably enforces** about human authority, agent identity, approvals, the operator console, the agent gateway and the reader (Executive Standard) control plane. It is written for a security engineer, a regulated-industry auditor, a future maintainer, and an adversarial reviewer who has only the sanitized public repository.

Rules this document follows:

- **MUST / MUST NOT** appear only where code or configuration mechanically enforces the property. Each such statement names its enforcement point.
- Operator policy, UI wording and intended design are labelled as such.
- Every property is given a status: **ENFORCED**, **PARTIAL**, **POLICY ONLY**, **NOT ENFORCED**, **NOT APPLICABLE**, or **UNVERIFIED**.
- Where the implementation is stronger than older documentation, this document says so. Where older documentation claimed more than the implementation does, this document is the correction.

Paths are repository-relative. Private hostnames, addresses and identifiers are deliberately omitted; they are not needed to evaluate the model.

---

## 1. The one fact that frames everything: the Unix account is the root of trust

Mechanical human authority holds against **every principal that is not running as the operator's Unix account**:

- the team agent accounts (`<prefix>-agent-<vendor>-<product>`): separate Unix users with no sudo, no access to the operator's keyrings, no database credentials, and their own SSH signing key each;
- cloud agents reaching the platform through the agent gateway (OAuth 2.1 + MCP).

It does **not** hold against code running **as the operator account**. A process running as the operator — including the operator's own interactive agent session, which runs outside `agents.slice` as a designed exception (`docs/AGENT_SEGMENTATION.md`) — can read:

- the database password, the admin API token, and the operator's board signing key, which has no passphrase;
- the agent manifest-signing GPG key, which has no passphrase by design;
- a set of passwordless sudo rules (§9.1).

With those it could, for example, write a new `approval_signers` row for the operator's account, or sign the tree manifest with the agent key directly. What it **cannot** do without the human is use the operator's **approval key**. That key is passphrase-protected; `scripts/approve.sh` refuses to sign with a passphrase-less key, and the passphrase is typed by the human. Nor can it use the operator's own GPG manifest key, which is also passphrase-protected.

So the honest framing is:

> Human authority is enforced cryptographically against agents and cloud
> connectors. Against software running as the operator, it rests on the
> passphrases of the operator's approval key and GPG key, and on the
> operator not loading either into a long-lived agent. The integrity
> controls — the signed manifest, the stack-refresh audit and the integrity
> sweep — **detect** unsigned changes. They do not prevent an operator-account
> process from acting.

---

## 2. Trust-boundary diagram

```
  External / cloud agent (vendor-hosted)            Team agent account (on-box Unix user)
            |                                                    |
   OAuth 2.1 + PKCE, per-connector bearer token        own SSH key (board namespace), no sudo,
            |                                          no DB creds, no operator keyrings
   Agent gateway  /mcp/<slug>  ----------------------------------+
            |   seven tools; no approve, no publish, no sudo
   ---- policy boundary: signed approvals (approval namespace, human approval key) ----
            |
   ===================== private organization =====================
            |
   Operator console  (/console)
            |   reachable only via the organizational overlay VPN (currently Tailscale)
            |   or from processes on the host itself
   browser challenge: login approval bound to a nonce held only in that browser's cookie
            |
   operator runs scripts/approve.sh over SSH: shows the canonical request,
   needs an interactive terminal and a typed ALLOW, signs with the approval key
            |
   approval key (ed25519, passphrase-protected, distinct from every board key)
            |
   human authorization (one request, one action, until its expiry)
```

### Layered meanings

| Layer | What it establishes | What it does **not** establish |
|---|---|---|
| Network admission (overlay VPN or host-local) | eligibility to reach the console | operator identity or any authority |
| Operator authentication (signed `console-login`) | control of the operator approval key at sign-in time | approval of any later action |
| Agent identity (connector token / agent SSH key) | which delegated principal is acting | that a human reviewed the action |
| Action approval (signed approval) | human authorization of one bounded request | anything outside that request's canonical text |
| Agent signature (board namespace) | that the named agent produced the message | human review of it |
| Audit evidence | what was recorded afterwards (§11) | tamper-proofing, beyond what §11 states |

---

## 3. Approval requests (`src/common/governance.py`, `src/common/db.py`)

**What is signed.** `governance.approval_canonical()` builds the exact bytes a human signs:

```
corporatetraveldc-approval v1
id: <request uuid>
action: allow|deny
kind: <sudo|console-login|connector-link|connector-hold|gateway-thaw|council|council-close|...>
requester: <requester string or ->
expires_at: <unix seconds>
command-sha256: <sha256 of the exact stored command / JSON spec>
```

The signature is an SSH signature (`ssh-keygen -Y sign`) in the namespace `corporatetraveldc-approval`. It is verified against the signer's registered **approval** key, not a board key.

| Property | Status | Enforcement point |
|---|---|---|
| Unique identifier | ENFORCED | `create_approval`: `uuid4` |
| Binds the exact command / spec | ENFORCED | `command-sha256` in the canonical text; the server recomputes it from the stored row |
| Binds the action (allow vs deny) | ENFORCED | `action:` line — an allow signature never verifies as a deny |
| Binds the requester | PARTIAL | the `requester:` line is signed, but it is a **string set by the creating code path**, not an authenticated identity: `"admin-token"` for the admin route, `"console"` for console login, `oauth:<client_id>` for connector links, the signed account for council requests |
| Binds the reason text | NOT ENFORCED | `reasoning` is shown by `approve.sh` but is **not** in the canonical text |
| Binds `command_pattern` | NOT ENFORCED | not in the canonical text, though the command itself is |
| Expiry | ENFORCED | `expires_at` is signed; `resolve_signed` refuses at or after `expires_at` (409); `get_approval_request` persists `expired` on read |
| Unanswered = denied | ENFORCED | no code path turns `pending` into `allowed` except a verified human signature; the sudo gate exits 1 on `denied`, `expired`, or when its poll window ends |
| Cannot be approved after expiry | ENFORCED | the conditional `UPDATE ... WHERE status='pending' AND expires_at > now` |
| Single resolution / allow–deny race | ENFORCED | the same conditional `UPDATE`; a second resolution gets 409 (`tests/web/test_signed_approvals.py::test_signed_resolution_is_single_use`) |
| Request material cannot change after signing | ENFORCED | any change to the command, kind, requester, expiry or id changes the canonical bytes (`test_signature_is_bound_to_the_exact_request`) |
| Self-approval | ENFORCED, scoped | `resolve_signed` refuses when `requester == signer`, and only active **human** approval signers can sign at all; agents cannot hold approval keys (`approval_signer_upsert` requires a human board signer) |
| Approval key ≠ any board key | ENFORCED | rejected at registration and again at every verification |
| Signer liveness | ENFORCED | the signer must be an active human board signer, so the team-liveness switch revokes approval authority with everything else |
| Tap / notification link can allow | NOT POSSIBLE | `db.resolve_approval_request` raises `ApprovalNeedsSignature` for `allow`; a tap can only **deny**, and only with the per-request deny key from the push |

**Expiry is per request class, not universal.** Ten minutes is the default and the value for the sudo gate and console login. It is not a system-wide invariant:

| Kind | TTL | Set in |
|---|---|---|
| `sudo` (gate) | 600 s | `scripts/sudo-approval-gate.sh` (`TTL_SECONDS=600`) |
| `sudo` via `POST /admin/approval-requests` | **caller-chosen, no upper bound** | `ApprovalRequestCreate.ttl_seconds` (default 600) — §9.2 |
| `console-login` | 600 s | `console.LOGIN_TTL_S` |
| `connector-link` | 900 s | `agent_gateway.PENDING_TTL_S` |
| `gateway-thaw` | 3600 s | `agent_gateway.request_thaw` |
| `connector-hold` | 24 h | `agent_gateway.request_hold` |
| `council` / `council-close` | 24 h | `governance.COUNCIL_APPROVAL_TTL_S` |

> NO RESPONSE IS NOT APPROVAL. UNANSWERED REQUESTS EXPIRE AND CONFER NO AUTHORITY. (ENFORCED for every kind above.)

---

## 4. Approval channels

| Channel | Request creation | Human interaction | Authorization | Server-side check | What then executes |
|---|---|---|---|---|---|
| **SSH (`scripts/approve.sh`)** | any code path above | the operator runs `approve.sh allow\|deny <id>` from an SSH session | the approval key signs the canonical text; the passphrase is typed by the human | `POST /api/v1/approvals/{id}/resolve` → `governance.resolve_signed` (Tier 0 by design: the signature is the credential; rate-limited to 20/min) | the consumer that created the request (below) |
| **ntfy push** | the sudo gate and the approval routes push the request with a **deny** link | the operator reads it on the phone | none: the push carries the deny key only | `GET /admin/approval-requests/{id}/resolve?action=deny&k=<key>` resolves **deny** only; `allow` is always 403 | nothing |
| **Native vendor access request** (connector link) | the vendor's OAuth `/oauth/authorize` → `authorize_start` | the vendor shows a consent page naming the account; the operator signs with `approve.sh` | `connector-link` approval over a spec binding connector, account, vendor, client id, redirect URI and scope | `authorize_status` issues the code once, only after `allowed`; `exchange_code` requires PKCE S256, the same client and redirect URI, single use, and an open gateway | a per-connector access token (1 h) and a rotating refresh token |

`approve.sh` itself (UI friction, not authorization): it shows the request fields, the exact command (pretty-printed JSON for specs) and its SHA-256; it requires an interactive terminal and the typed word `ALLOW` or `DENY`; it refuses a passphrase-less key. Only the passphrase is a cryptographic boundary. The tty check and the typed word stop accidents, not a determined process that holds the passphrase.

**ntfy unavailable:** fail-closed. A missing push only means the operator does not learn of the request; it expires unanswered. The SSH channel works without ntfy (`approve.sh list` reads pending requests over a signed board call).

**Consumers of an `allowed` status.**

- **Sudo gate** (`scripts/sudo-approval-gate.sh`): a client-side wrapper. It creates the request with the exact command string, polls, and on `allowed` runs the command it holds in memory. The server binds the approval to that command's hash, but **what runs is decided by the wrapper process**. The wrapper can only be used by a process that can read the operator's admin token. See §9.1 for the passwordless sudo rules that make this gate advisory for two of its own patterns.
- **Console login, connector link, connector hold, gateway thaw, council:** consumed server-side, from the stored spec (sections 5, 7 and 8).

---

## 5. Operator console (`src/web/routes/console.py`)

### 5.1 Network boundary (A)

| Path | Result | Evidence |
|---|---|---|
| Public internet via the tunnel | **no route** | every tunnel ingress targets the local reverse proxy by public hostname; the public dispatch vhost has `location ^~ /console { return 404; }`; the tunnel cannot present the overlay hostname |
| LAN | **blocked** | the reverse proxy listens on all interfaces, but the LAN firewall zone admits only ssh, mdns, samba-client, dhcpv6 and port 80; port 80 for the console host only redirects to HTTPS on the overlay name |
| Overlay VPN (currently Tailscale) | **reachable** | the overlay interface sits in a trusted firewall zone; the overlay vhost proxies `/console`; the app also listens on the overlay address |
| Processes on the host itself | **reachable** | loopback and the host's own addresses are not filtered; this includes the team agent accounts |
| App Host check (`CONSOLE_HOSTS`) | defence in depth only | other Host headers get 404, but Host is client-controlled, so this is **not** a network control |

**If the overlay is down:** the console is unreachable except from the host itself. Nothing fails open.

**Network admission grants no authority.** Reaching the console yields only the sign-in page. Starting a sign-in creates a `console-login` approval request, which needs the operator's approval-key signature to mean anything. A host-local agent account could create such requests, a nuisance that is visible in `approve.sh list`, but it cannot complete one.

The requirement is "an authenticated organizational overlay network". Tailscale is the current implementation, not an architectural requirement.

### 5.2 Authentication (B)

1. `POST /console/login` creates a random nonce. The approval spec stores **only its SHA-256**; the nonce itself goes into a `__Host-` cookie (Secure, HttpOnly, SameSite=Strict, max-age 10 min) in that browser.
2. The operator signs `console-login` with `approve.sh` (§4).
3. `GET /console` redeems only if the cookie's nonce hashes to the signed spec's `nonce_sha256`, the approval is `allowed`, and the signer is still an active human. It then mints one session secret (8 h). The database stores only its hash, and `console_sessions.approval_id` is **UNIQUE**, so one approval mints at most one session.
4. Every request re-checks the session (not revoked, not expired) and the signer's liveness. Every POST needs a CSRF token derived from the session secret.
5. `POST /console/logout` revokes the session server-side.

| Property | Status | Evidence |
|---|---|---|
| Reaching the console ≠ authentication | ENFORCED | no session without a signed approval (`test_no_session_without_a_signature`) |
| Challenge bound to the originating browser | ENFORCED | nonce hash in the signed spec; the nonce lives only in that browser's cookie (`test_the_signature_only_unlocks_the_browser_that_asked`) |
| Challenge replay after login | ENFORCED | the UNIQUE `approval_id` (same test) |
| Expired request = denied | ENFORCED | an approval cannot be signed after its 10 min (§3) |
| Redemption window after a timely approval | **PARTIAL** | the server does not bound *when* an allowed approval is redeemed; the 10-minute limit on the pending cookie is browser-enforced. Single use still holds. (§9.4) |
| Session lifetime | ENFORCED | 8 h, server-side `expires_at` |
| Logout | ENFORCED | server-side `revoked_at` (`test_logout`) |
| Inert operator loses the console | ENFORCED | liveness check per request (`test_inert_operator_loses_the_console`) |

> REACHING THE CONSOLE != AUTHENTICATION · REQUEST != AUTHORITY · NOTIFICATION != AUTHORITY · SIGNATURE = AUTHORITY ONLY WITHIN ITS CANONICAL REQUEST · EXPIRED REQUEST = DENIED

---

## 6. Human key vs agent key (E)

| Key | Holder | Passphrase | Namespace / use | What a valid signature proves |
|---|---|---|---|---|
| Operator **approval** key (ed25519, SSH) | operator | **yes** (`approve.sh` refuses otherwise) | `corporatetraveldc-approval` | the holder of the operator approval key authorized **this exact canonical request** |
| Operator **board** key (SSH) | operator account | **no** | `corporatetraveldc-board` | a process with access to the operator account sent this board message; **not** proof of a human |
| Team-agent board keys (SSH, one per account) | each agent account | no | `corporatetraveldc-board` | that named agent account produced this message, under authority the human previously granted (account creation, grants, liveness) |
| Operator **GPG** manifest key | operator | **yes** | manifest signature | the operator signed this tree state |
| **Agent** GPG manifest key | operator account's keyring | **no**, by design | manifest signature | a process with access to the operator account signed this tree state (§10) |

Namespaces are distinct (`board`, `approval`, `kill`), so a signature cannot be replayed across purposes.

| Property | Status | Evidence |
|---|---|---|
| Agent signature ≠ human signature | ENFORCED | different keys and namespaces; board keys are refused as approval keys (`test_board_key_signature_never_approves`) |
| An agent cannot mint its own signing authority | ENFORCED for team and cloud agents | agent accounts have no database credentials; board-signer and approval-signer registration write the database (`scripts/board-signer-ctl.sh`, `scripts/approver-ctl.sh`); **POLICY ONLY** against operator-account processes (§1) |
| An agent cannot approve its own request | ENFORCED | agents cannot hold approval keys; requester ≠ signer |
| Downstream verification keeps signer identity | ENFORCED on the signature path | `_board_auth` returns the signing account for attribution; on `normal`-policy routes the shared board key alone is also accepted, and such a post's sender is **claimed, not proven** |

---

## 7. Agent connectors and the gateway (F, G, H)

`src/common/agent_gateway.py`, `src/web/routes/agent_gateway.py`. One MCP endpoint per agent identity, `/mcp/<slug>`, each mapped to its own team account (live today: `cowork` and `claude-code`, each a distinct account).

### 7.1 Identity separation (F)

| Property | Status | Evidence |
|---|---|---|
| Distinct account per connector | ENFORCED by configuration | `agent_connectors.slug → account`; the code allows two slugs to share an account, the live mapping does not |
| A token works only on its own connector | ENFORCED | `authenticate()` refuses when the connection's slug ≠ the requested slug (`test_one_connectors_token_is_refused_on_another_connector`) |
| Independent link state / revocation | ENFORCED | per-connection rows; `revoke_connection` revokes that connection and its tokens |
| Independent disable | ENFORCED | `connector_set_disabled(slug)` revokes that slug's live connections (`test_disable_one_connector_revokes_it`) |
| Renewal / expiry | ENFORCED | 1 h access; rotating refresh (an old refresh token dies on use); 30-day idle grant; dormancy after 7 quiet days (not revocation); an operator-signed hold of at most 90 days |
| Account liveness | ENFORCED | each refresh and call checks the account's board signer; an inactive signer **revokes** the connection; the operator dead-man (no operator login for 14 days) **refuses** without revoking |

> AGENT A AUTHORITY != AGENT B AUTHORITY (ENFORCED at the gateway.)

### 7.2 Linking is not privilege (G)

**Linking** means a human signs `connector-link` for one named connector, account, client and redirect URI. **Action approval** means a human signs a separate request for one consequential operation. A linked connector can call exactly seven tools:

| Tool | Effect | Further gate |
|---|---|---|
| `status` | read own identity | — |
| `board_read` | read a board thread | — |
| `board_post` | post as the connector's account | scrub gate |
| `research_list`, `research_read` | read the research scope of the vault | path allow-list, arena blindness, scrub gate |
| `workspace_contribute` | create-only, attributed draft | workspace grants (deny wins), scrub gate |
| `council_request` | ask for a convene | **nothing happens until a human signs** |

No tool approves, publishes, deletes, signs a manifest or reaches sudo. Linking therefore cannot grant privileged capability: ENFORCED by the tool table.

### 7.3 Revocation hierarchy (H)

| Control | Who | Signature needed | Effect |
|---|---|---|---|
| Revoke link (`gw-revoke`, CLI) | operator console session or CLI | no | that connection and its tokens revoked; re-linking needs a new signed `connector-link` |
| Disable connector | console session or CLI | no | refuses new links and every call on that slug; revokes its live connections |
| Enable connector | console session or CLI | **no** | allows linking again; **does not** restore any connection (a signed re-link is still required) |
| Account inert (team liveness / kill order) | root liveness unit | quorum rules (`docs/AGENT_SEGMENTATION.md`) | the next refresh or call revokes every connection of that account |
| **Kill all agents** (`gw-kill-all`, CLI `kill-all`) | console (with an "I mean it" checkbox) or CLI | no | **ENFORCED**: freezes the gateway (every OAuth and MCP endpoint refuses with 503), revokes **every** connection and its tokens, disables **every** connector |
| **Re-open (thaw)** | console or CLI **creates** a request | **yes**, `gateway-thaw` | unfreezes only; connectors stay disabled until re-enabled; each vendor must re-link with a fresh signature |

Across a kill-all:

- **Existing tokens:** revoked.
- **Outstanding `connector-link` approvals:** the rows survive, but signing one afterwards yields no token. Code exchange is refused while frozen, and after a signed thaw the connector is still disabled (`test_a_link_signed_after_kill_all_never_becomes_a_token`).
- **Outstanding `connector-hold` approvals:** affect only non-revoked connections, so there is nothing to hold.
- **Sudo, console and council approvals:** unaffected. The gateway is a separate plane.
- **Reopening needs a human signature:** ENFORCED (`test_kill_all_freezes_everything_and_thaw_needs_a_signature`).

---

## 8. Executive Standard reader control plane (I)

`src/common/es_invites.py`. It is separate from the agent plane: different tables, a different global switch (`es_invite_settings.frozen`), and no shared tokens.

> READER CONTROL PLANE != AGENT CONTROL PLANE

| Property | Behaviour | Status |
|---|---|---|
| Personal invite | permanent by default, or `days=N`; hashes only stored | ENFORCED |
| Device cap | default 3; one more sign-in ends the oldest device | ENFORCED (`test_device_cap_signs_out_the_oldest`) |
| Hand-off | single-use, 120 s, minted by a POST (link previews never burn it) | ENFORCED |
| Promo code | code lifetime ≤ 30 days, each redemption ≤ 30 days of access, capped uses, never permanent | ENFORCED |
| Session | 90-day idle; checked per request against session **and** grant (revoked / expired) | ENFORCED |
| Sign out (one reader) | ends that grant's sessions; the link still works | ENFORCED |
| Revoke | ends the grant, every device, and the link | ENFORCED |
| Pause new sign-ins (freeze) | refuses invite claims, promo redemptions and hand-off exchanges; signed-in readers keep reading | ENFORCED |
| Sign out every reader (kill-all) | freeze **and** end every session **and** burn unused hand-offs; **grants survive** | ENFORCED |
| Resume (thaw) | lifts the freeze only; readers sign in again with their **same** links | ENFORCED, and needs **no signature** (console session or CLI) |

---

## 9. Bypasses, exceptions and gaps

### 9.1 Passwordless sudo makes the sudo gate advisory for its own patterns — **NOT ENFORCED**

`sudo -n -l` for the operator account lists `NOPASSWD` rules including `/usr/bin/dnf remove *` and `/usr/bin/semanage port -a *`, the two patterns `scripts/sudo-approval-gate.sh` exists to gate. Any process running as the operator can run them directly, with no approval. There are also stale `NOPASSWD` rules for services that no longer exist (`ollama.service`, `ollama-governor.service`).

The operator's general `(ALL) ALL` rule requires the operator's password and is not affected. Remediation is operator-side: remove the passwordless rules for the gated patterns. **Until then, the sudo approval gate is a workflow, not a boundary, for those commands.**

### 9.2 The admin approval route accepts any TTL — **PARTIAL**

`POST /admin/approval-requests` takes a caller-chosen `ttl_seconds` with no upper bound. A holder of the admin token could create a long-lived pending request. It still needs a human signature, and the expiry is part of what the human signs and is shown by `approve.sh`, but "requests cannot remain latent indefinitely" is not enforced on this route. A cap (for example 600 s for kind `sudo`) would close it.

### 9.3 Requester and reason are not authenticated bindings — **PARTIAL**

`requester` is chosen by the creating code path, and `reasoning` is not signed (§3). The command and spec are what the signature binds.

### 9.4 Console redemption window — **PARTIAL**

After a timely approval, the server does not limit when the browser redeems it. Exploiting this needs the victim browser's HttpOnly, SameSite=Strict cookie, and a redemption is single-use. A server-side bound (for example approval `expires_at` + 10 min) would close it.

### 9.5 Operator-account processes — **POLICY ONLY**

See §1. Notably:

- `scripts/grant-agent-session.sh` is "human-run only" by policy; a grant is a database row an operator-account process could write;
- the agent GPG key can be used directly with `gpg`, bypassing `sign-manifest.sh --agent`'s grant check;
- `approval_signers` can be re-keyed by anything holding the database password.

### 9.6 Destructive console actions (J)

| Action | Friction | Signature |
|---|---|---|
| Kill all agents, sign out every reader | "I mean it" checkbox, server-checked (`confirm=yes`) | no |
| Re-open gateway, hold a link | — | **yes** (`gateway-thaw`, `connector-hold`) |
| Revoke link, disable / enable connector, revoke reader, sign out, stop promo, pause / resume sign-ins | none beyond session + CSRF | no |

The checkbox is UI friction, not an authorization boundary. Authorization for every console action is the signed console session plus CSRF. Only re-opening and holds need a fresh signature.

---

## 10. Manifest signing and the `sign-manifest:agent-key` grant (L)

`scripts/sign-manifest.sh` signs `MANIFEST.sha256` with either:

- the **operator** GPG key: passphrase typed by the human;
- or, with `--agent`, the **agent** GPG key: no passphrase. It signs if an active `session_grants` row for `sign-manifest:agent-key` exists; otherwise it runs the sudo approval gate for that pattern and signs on `allowed`.

`scripts/verify-manifest.sh` accepts a signature by **either** pinned fingerprint. The pin is read literally from a root-owned file when present.

| Question | Answer |
|---|---|
| Who can create the grant | `scripts/grant-agent-session.sh`, human-run by policy; mechanically, anything that can write the database (§9.5) |
| Lifetime / scope | a bounded `expires_at` per grant; the scope is the pattern `sign-manifest:agent-key` |
| What it authorizes | a **signing capability** for the grant's lifetime, **not one exact artifact**. A one-off approval binds the gate's request to its command string, which does not include the tree's contents |
| Replay | each `sign-manifest.sh --agent` run checks the grant or creates a fresh approval; a grant permits any number of signatures until it expires |
| Audit evidence | `audit_log` rows `agent_sign_manifest` (via grant or via a pending approval) — **written by the script itself** |
| Is it "self-signing"? | **Not for team agent accounts**: they lack the key and the database. **For operator-account processes, effectively yes**: the key is passphrase-less in the operator's keyring, and the grant check lives in the client script |

So a manifest signature by the **agent** key attests "produced on the operator account through the agent signing path". It does **not** attest human review of that tree. A signature by the **operator** key attests the human typed the passphrase for that signing run. `gpg --verify` names which key signed, and the signer identity is preserved in the signature.

---

## 11. Audit evidence (K)

| Event class | Hash-chained `audit_log` | Other record |
|---|---|---|
| Approval request created via `POST /admin/approval-requests` | **yes** (`require_admin` audits admin routes, including denied calls) | `approval_requests` row |
| Approval resolved by signature | no | `approval_requests.status / resolved_by / resolution_sig` (mutable table; the signature can be re-verified only while the signer's key is still registered: re-keying replaces it); one journal line |
| Approval resolved by deny tap | no | `approval_requests` row |
| Approval expired | no | `approval_requests.status = expired` (set on read) |
| Console login requested / approved / denied / expired | no | `approval_requests` (kind `console-login`), `console_sessions` |
| Console actions | no | the target tables (`es_*`, `agent_*`) |
| Agent link requested / approved | no | `oauth_pending`, `approval_requests`, `agent_connections` |
| Agent link revoked, connector disabled | no | `agent_connections.revoked_at / revoke_reason`, `agent_connectors.disabled_at` |
| Gateway killed / reopened | no | `agent_gateway_settings` (`updated_by`), ntfy push |
| Reader invites, revokes, freeze, kill-all | no | `es_invite_events` (append pattern, ordinary table) |
| Agent manifest signing | **yes** (script-written) | the signature itself |

The `audit_log` hash chain (migration 0062) is computed by a database trigger. **No code verifies the chain**, and the database role that writes rows could also rewrite them. It is tamper-evident only to someone who recomputes it independently. The governance and gateway tables above are ordinary mutable state, **not** hash-chained.

---

## 12. Security invariants / end policies

| # | Invariant | Status | Enforcement point / reason |
|---|---|---|---|
| 1 | Default denial: absence of authorization never becomes approval | **ENFORCED** | only `resolve_signed` sets `allowed`; consumers act only on `allowed` |
| 2 | Expiration: requests cannot remain latent authority indefinitely | **PARTIAL** | every kind expires and is checked at resolution; the admin route's TTL is unbounded (§9.2) |
| 3 | Human authority: agents cannot grant themselves privileged authority | **ENFORCED** for team and cloud agents; **POLICY ONLY** for operator-account processes | §1, §6 |
| 4 | Identity separation: human and agent keys differ in meaning | **ENFORCED** | separate keys, namespaces and registries; `tests/scripts/test_signing_identities_distinct.py` |
| 5 | Connector separation | **ENFORCED** | `authenticate()` slug check |
| 6 | Network separation: no console on the public surface | **ENFORCED** | §5.1 |
| 7 | Least privilege | **PARTIAL** | connectors have seven bounded tools; console sessions carry full console authority for 8 h; sudo grants are pattern-scoped |
| 8 | Revocability: agent and reader access revocable independently | **ENFORCED** | separate planes, per-connection and per-grant revocation |
| 9 | Emergency containment: global controls fail closed | **ENFORCED** | gateway freeze refuses every endpoint; reader freeze refuses every sign-in path |
| 10 | Explicit reopening | **ENFORCED** for the agent gateway (signed thaw); **NOT ENFORCED** for reader sign-ins (resume needs a session, no signature) | §7.3, §8 |
| 11 | Canonical authorization: signatures bind canonical material, not labels | **ENFORCED** for the command / spec; **NOT ENFORCED** for the reason text | §3 |
| 12 | Audit honesty | this document | §11 |
| 13 | No equivalence inflation: an agent signature is not a human signature | **ENFORCED** | §6, §10 |
| 14 | Private control plane: overlay membership is a prerequisite, not authorization | **ENFORCED** (overlay or host-local) | §5.1 |

---

## 13. Adversarial validation

| Attack | Result | How verified |
|---|---|---|
| Expired approval replay / approval after timeout | refused (409), reads `expired` | `test_expired_request_cannot_be_signed_and_reads_as_expired` |
| Duplicate consumption; concurrent allow / deny | refused (409) | `test_signed_resolution_is_single_use`; conditional `UPDATE` |
| Altered command after approval | signature fails | `test_signature_is_bound_to_the_exact_request` |
| Requester self-approval | refused | `test_requester_cannot_approve_itself` |
| Board-key signature used as an approval | refused | `test_board_key_signature_never_approves` |
| Tap link turned into allow | refused | `test_deny_link_cannot_be_turned_into_allow`, `test_no_key_ever_allows_wave2` |
| Connector A token on connector B | 401 | `test_one_connectors_token_is_refused_on_another_connector` |
| Browser B redeeming browser A's challenge; replay after login | refused | `test_the_signature_only_unlocks_the_browser_that_asked` |
| Session after logout | refused | `test_logout` |
| Agent after link revocation / connector disable / account inert | refused | `test_our_side_kills_the_chain`, `test_disable_one_connector_revokes_it`, `test_revoke_account_hook` |
| Agent after kill-all; a link signed after kill-all | refused | `test_kill_all_freezes_everything_and_thaw_needs_a_signature`, `test_a_link_signed_after_kill_all_never_becomes_a_token` |
| Re-open the gateway without a signature | impossible | thaw is applied only by the `gateway-thaw` approval hook |
| Console via the public tunnel / LAN | 404 / firewall-blocked | proxy and firewall inspection, safe GETs (§5.1) |
| Direct invocation bypassing the sudo gate | **possible for `NOPASSWD` patterns** | `sudo -n -l` (§9.1) |
| Agent GPG key used without a grant | **possible for operator-account processes** | key protection and script inspection (§10) |
| Production destructive tests | not performed | by design; covered by fixtures above |

---

## 14. Drift checks

These are stable, deterministic checks, not prose greps:

- `tests/scripts/test_signing_identities_distinct.py`: the operator and agent manifest keys differ, in the tracked pin and in the root-owned live pin.
- `tests/web/test_signed_approvals.py`: the expiry, single-use, exact-binding, self-approval and board-key checks above.
- `tests/web/test_agent_gateway.py`: the connector-isolation and kill-all checks above.
- `tests/web/test_console.py::test_only_the_tailnet_host`: the console answers only on the configured overlay host.

Deliberately **not** checked: TTL numbers (they are policy and may change; this document's §3 table records them), and network reachability (not deterministic in CI; verified by the live inspection recorded in §5.1).

## 15. Public mirror

This document is public-safe as written. It contains no addresses, hostnames, credentials or key fingerprints. Private-only material stays out: the exact firewall rules, the overlay hostname, the sudoers file contents beyond the rule patterns named in §9.1, and the operator's account names.
