# Snapshot: agent trust model, review 02 (after the hardening pass)

> **Frozen record. Not current once a later review exists.** This is `docs/AGENT_TRUST_MODEL.md` exactly as it stood at the end of review 02 on 2026-10-07 (the hardening commit). The current canonical document is `docs/AGENT_TRUST_MODEL.md`. Chain index: `README.md` in this directory.

---

# Agent and operator trust model

Verified against the code at HEAD and the running system on 2026-10-07 12:00Z–13:30Z / 08:00–09:30 ET; hardening pass applied the same day (status changes are marked inline, the earlier status struck through).

> Review trail: how this document reached its current state, pass by pass, is recorded in `docs/security-reviews/` (start at its `README.md`).

This document states what the platform **demonstrably enforces** about human authority, agent identity, approvals, the operator console, the agent gateway and the reader (Executive Standard) control plane. It is written for a security engineer, a regulated-industry auditor, a future maintainer, and an adversarial reviewer who has only the sanitized public repository.

Rules this document follows:

- **MUST / MUST NOT** appear only where code or configuration mechanically enforces the property. Each such statement names its enforcement point.
- Operator policy, UI wording and intended design are labelled as such.
- Every property is given a status: **ENFORCED**, **PARTIAL**, **POLICY ONLY**, **NOT ENFORCED**, **NOT APPLICABLE**, or **UNVERIFIED**.
- Where the implementation is stronger than older documentation, this document says so. Where older documentation claimed more than the implementation does, this document is the correction.

Paths are repository-relative. Private hostnames, addresses and identifiers are deliberately omitted; they are not needed to evaluate the model.

---

## 0. Deployment origin and the operator-as-agent exception

This platform began as a **single-operator deployment**: one person, one Unix account, one box, with the operator's own interactive agent sessions working directly in that account. It was then made ready for **multiple human and agent user accounts**. Each human and each agent got:

- a separate Unix account;
- separate SSH identities (one per account, for both signing and inbound access);
- a liveness switch;
- scoped grants;
- signed approvals that only a human approval key can satisfy.

The controls in this document were built for that multi-account model. They hold mechanically against every principal that is not the operator's own account.

One fallback from the single-operator origin is kept **by design**: an agent operating **as the human operator's account**, the operator's own interactive agent session. That session can read what the operator account can read (§1). It is an **exception**, not part of the model, and its safety rests on the human operator's operational hygiene with their own account and their own agentic use case:

- the operator keeps the approval key and the GPG key passphrase-protected and out of long-lived agents;
- the operator decides what an agent running as them may do.

**Recommended rollout for anything beyond a single operator:** run every agent in a **confined, organizationally managed agent account** (`<prefix>-agent-<vendor>-<product>`: no sudo, no operator keyrings, no database credentials, its own signing key, liveness-switched) or through the agent gateway. **Agents running in a personal account are not recommended, and in a larger deployment they are not allowed as an organizational policy.** The operator-as-agent fallback exists for the single-operator case only.

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
> connectors. Since 2026-10-07 the approval-gated **root** commands hold even
> against operator-account processes: a root-owned executor re-verifies the
> human signature against a root-owned key pin (§9.1).
> For everything else running as the operator account, the rule is §0's designed exception. Against software running as the operator, it rests on the
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
corporatetraveldc-approval v2
id: <request uuid>
action: allow|deny
kind: <sudo|console-login|connector-link|connector-hold|gateway-thaw|council|council-close|...>
requester: <requester identity or ->
expires_at: <unix seconds>
command-sha256: <sha256 of the exact stored command / JSON spec>
pattern-sha256: <sha256 of the command pattern>
reason-sha256: <sha256 of the reasoning shown to the approver>
```

~~v1 (until 2026-10-07) had no pattern or reason lines.~~ v2 binds both; `scripts/approve.sh` and the server changed together.

The signature is an SSH signature (`ssh-keygen -Y sign`) in the namespace `corporatetraveldc-approval`. It is verified against the signer's registered **approval** key, not a board key.

| Property | Status | Enforcement point |
|---|---|---|
| Unique identifier | ENFORCED | `create_approval`: `uuid4` |
| Binds the exact command / spec | ENFORCED | `command-sha256` in the canonical text; the server recomputes it from the stored row |
| Binds the action (allow vs deny) | ENFORCED | `action:` line — an allow signature never verifies as a deny |
| Binds the requester | ~~PARTIAL~~ **ENFORCED (2026-10-07)** | the `requester:` line is signed and each path binds its authenticated identity: `token:<label>` of the admin token that asked (was the literal `"admin-token"`), `oauth:<client_id>` for connector links, the signed account for council requests. Console sign-in binds `console` plus the browser nonce (§5.2) |
| Binds the reason text | ~~NOT ENFORCED~~ **ENFORCED (2026-10-07)** | `reason-sha256` in canonical v2; editing the reason after signing breaks the signature (`test_canonical_v2_binds_pattern_and_reason`) |
| Binds `command_pattern` | ~~NOT ENFORCED~~ **ENFORCED (2026-10-07)** | `pattern-sha256` in canonical v2 |
| Expiry | ENFORCED | `expires_at` is signed; `resolve_signed` refuses at or after `expires_at` (409); `get_approval_request` persists `expired` on read |
| Unanswered = denied | ENFORCED | no code path turns `pending` into `allowed` except a verified human signature; the sudo gate exits 1 on `denied`, `expired`, or when its poll window ends |
| Cannot be approved after expiry | ENFORCED | the conditional `UPDATE ... WHERE status='pending' AND expires_at > now` |
| Single resolution / allow–deny race | ENFORCED | the same conditional `UPDATE`; a second resolution gets 409 (`tests/web/test_signed_approvals.py::test_signed_resolution_is_single_use`) |
| Request material cannot change after signing | ENFORCED | any change to the command, kind, requester, expiry or id changes the canonical bytes (`test_signature_is_bound_to_the_exact_request`) |
| Self-approval | ENFORCED, scoped | `resolve_signed` refuses when `requester == signer`, and only active **human** approval signers can sign at all; agents cannot hold approval keys (`approval_signer_upsert` requires a human board signer) |
| Approval key ≠ any board key | ENFORCED | rejected at registration and again at every verification |
| Signer liveness | ENFORCED | the signer must be an active human board signer, so the team-liveness switch revokes approval authority with everything else |
| Tap / notification link can allow | NOT POSSIBLE | `db.resolve_approval_request` raises `ApprovalNeedsSignature` for `allow`; a tap can only **deny**, and only with the per-request deny key from the push |

**Expiry is per request class, not universal.** Ten minutes is the default and the value for the sudo gate and console login. It is not a system-wide invariant. Since 2026-10-07 every kind has a **hard maximum** (`governance.MAX_TTL_S`, the values below); a caller may ask for less, never more:

| Kind | TTL | Set in |
|---|---|---|
| `sudo` (gate) | 600 s | `scripts/sudo-approval-gate.sh` (`TTL_SECONDS=600`) |
| `sudo` via `POST /admin/approval-requests` | ~~caller-chosen, no upper bound~~ **≤ 600 s (2026-10-07)** | `governance.MAX_TTL_S` caps every kind at creation; a longer request is refused (400) |
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

- **Sudo gate** (`scripts/sudo-approval-gate.sh`): ~~a client-side wrapper that, on `allowed`, runs the command it holds in memory (§9.1).~~ Since 2026-10-07 a `sudo` command is executed by the **root-owned** `approved-exec.py`, not by the wrapper (§9.1). Non-privileged commands (e.g. the `/bin/true` placeholder the manifest signers use) still run in the wrapper; they confer nothing the caller could not do anyway.
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
| Redemption window after a timely approval | ~~PARTIAL~~ **ENFORCED (2026-10-07)** | the server refuses redemption later than the request's expiry + 10 min (`test_an_allowed_login_cannot_be_redeemed_long_after_expiry`); single use holds as before |
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

### 9.1 Passwordless sudo made the sudo gate advisory for its own patterns — ~~NOT ENFORCED~~ **ENFORCED once the 2026-10-07 sudoers change is applied**

`sudo -n -l` for the operator account lists `NOPASSWD` rules including `/usr/bin/dnf remove *` and `/usr/bin/semanage port -a *`, the two patterns `scripts/sudo-approval-gate.sh` exists to gate. Any process running as the operator can run them directly, with no approval. There are also stale `NOPASSWD` rules for services that no longer exist (`ollama.service`, `ollama-governor.service`).

~~The operator's general `(ALL) ALL` rule requires the operator's password and is not affected. Remediation is operator-side: remove the passwordless rules for the gated patterns. Until then, the sudo approval gate is a workflow, not a boundary, for those commands.~~

**Fix (2026-10-07).** The passwordless rules for `dnf remove *`, `dnf autoremove`, `semanage port -a *` and the dead `ollama*` services are replaced by **one** rule:

```
<operator> ALL=(root) NOPASSWD: /usr/local/libexec/ctdc/approved-exec.py
```

`approved-exec.py` is root-owned, installed by `scripts/install-root-copies.sh` against the signed manifest, and runs with `python3 -I`. Given a request id, it:

1. fetches the record and **trusts nothing in it** until the human's SSH signature over its canonical v2 text verifies against a **root-owned** pin (`/etc/corporatetraveldc/approval-allowed-signers`). A process that can write the database can forge a row, but it cannot forge the signature, and it cannot change the pin;
2. requires `status = allowed`, kind `sudo`, a signature made before expiry, and execution starting within 5 min of that expiry;
3. requires the **signed** command to fullmatch the root-owned allowlist entry for its signed pattern (`approved-exec.conf`: `dnf-remove`, `dnf-autoremove`, `semanage-port-add`), with an absolute binary path;
4. records the id in a root-only ledger with `O_EXCL` (each approval runs **at most once**);
5. runs the command as root **without a shell**, logging to the journal and the ledger.

This holds **even against processes running as the operator account**. Tests: `tests/scripts/test_approved_exec.py` (canonical lockstep with the server, forged-row rejection, allowlist shapes, timing, single use, root-ownership of trust files). The general `(ALL) ALL` rule still requires the operator's password and is unaffected. **Until the sudoers change in the deploy relay is applied, the earlier status stands.**

### 9.2 The admin approval route accepted any TTL — ~~PARTIAL~~ **FIXED 2026-10-07**

~~`POST /admin/approval-requests` takes a caller-chosen `ttl_seconds` with no upper bound.~~ Now capped per kind (`governance.MAX_TTL_S`; sudo ≤ 600 s; longer is a 400). What follows is the record of the gap: A holder of the admin token could create a long-lived pending request. It still needs a human signature, and the expiry is part of what the human signs and is shown by `approve.sh`, but "requests cannot remain latent indefinitely" is not enforced on this route. A cap (for example 600 s for kind `sudo`) would close it.

### 9.3 Requester and reason were not authenticated bindings — ~~PARTIAL~~ **FIXED 2026-10-07**

~~`requester` is chosen by the creating code path, and `reasoning` is not signed (§3).~~ The admin route now binds the authenticating token's label, and canonical v2 signs the reason and the pattern (§3).

### 9.4 Console redemption window — ~~PARTIAL~~ **FIXED 2026-10-07**

~~After a timely approval, the server does not limit when the browser redeems it.~~ The server now refuses redemption later than the request's expiry + 10 min. Record of the gap: Exploiting this needs the victim browser's HttpOnly, SameSite=Strict cookie, and a redemption is single-use. A server-side bound (for example approval `expires_at` + 10 min) would close it.

### 9.5 Operator-account processes — **POLICY ONLY (designed exception, §0)**

See §0 and §1. This is the single-operator fallback kept by design; a multi-account deployment runs agents in managed agent accounts instead. Within the exception, notably:

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

Since 2026-10-07 the governance events below are written to the hash-chained `audit_log`. They are written **after** the state change commits, in a separate write that never raises, so an audit failure cannot change an authorization result (`db._audit_quiet`, `governance.audit`).

| Event class | Hash-chained `audit_log` | Other record |
|---|---|---|
| Approval request created (any kind) | ~~admin route only~~ **yes** — `approval.requested` (id, kind, requester, pattern, command sha256, TTL) | `approval_requests` row |
| Approval resolved by signature | ~~no~~ **yes** — `approval.resolved` (status, signer, via=signature) | `approval_requests.resolution_sig` |
| Approval resolved by deny tap | ~~no~~ **yes** — `approval.resolved` (via=deny-link) | `approval_requests` row |
| Approval expired | ~~no~~ **yes** — `approval.expired` | `approval_requests.status` |
| Console sign-in, sign-out | ~~no~~ **yes** — `console.login`, `console.logout` | `console_sessions` |
| Console actions | ~~no~~ **yes** — `console.action` (action, target reference — reader emails as a hash, never links or codes; whether the "I mean it" box was ticked) | the target tables |
| Agent link established / revoked | ~~no~~ **yes** — `agent.link.approved`, `agent.link.revoked` | `agent_connections` |
| Connector disabled / enabled | ~~no~~ **yes** — `agent.connector.disabled` / `.enabled` | `agent_connectors.disabled_at` |
| Gateway killed / reopened | ~~no~~ **yes** — `gateway.killed`, `gateway.reopened` | `agent_gateway_settings`, ntfy push |
| Reader invites, revokes, sign-outs, promos, freeze, resume, kill-all | ~~no~~ **yes** — `reader.*` (actor, target id) | `es_invite_events` |
| Root execution of an approved sudo command | journal (`approved-exec`) + root-only ledger | the ledger file holds argv, signer and exit code |
| Agent manifest signing | yes (script-written) | the signature itself |

**Verification.** ~~No code verifies the chain.~~ `src/common/audit_chain.py` recomputes every chained row **in SQL**, with the trigger's exact expression, and checks every `prev_hash` link. `scripts/scheduled-integrity-sweep.sh` runs it every 15 minutes, fails with a priority-5 push on any mismatch or broken link, and logs the head hash each run, which keeps a copy of the chain head outside the database. First live run: 419 chained rows intact, plus 12,250 rows written before the trigger existed, counted and not judged.

**What this still does not prove.** A writer who can rewrite the table can also recompute the whole chain. Detecting that needs an anchor the writer cannot change: the journaled head hashes are the current anchor, and signed checkpoints (`audit_checkpoints`, schema present) are the planned one. The governance tables themselves remain ordinary mutable state; the chain covers the **event record**, not the tables.

## 12. Security invariants / end policies

| # | Invariant | Status | Enforcement point / reason |
|---|---|---|---|
| 1 | Default denial: absence of authorization never becomes approval | **ENFORCED** | only `resolve_signed` sets `allowed`; consumers act only on `allowed` |
| 2 | Expiration: requests cannot remain latent authority indefinitely | ~~PARTIAL~~ **ENFORCED (2026-10-07)** | every kind expires, is checked at resolution, and has a hard maximum TTL (`governance.MAX_TTL_S`) |
| 3 | Human authority: agents cannot grant themselves privileged authority | **ENFORCED** for team and cloud agents, and since 2026-10-07 for approval-gated **root** commands even against operator-account processes (§9.1); otherwise **POLICY ONLY** for operator-account processes, the designed exception (§0) | §0, §1, §6, §9.1 |
| 4 | Identity separation: human and agent keys differ in meaning | **ENFORCED** | separate keys, namespaces and registries; `tests/scripts/test_signing_identities_distinct.py` |
| 5 | Connector separation | **ENFORCED** | `authenticate()` slug check |
| 6 | Network separation: no console on the public surface | **ENFORCED** | §5.1 |
| 7 | Least privilege | **PARTIAL** | connectors have seven bounded tools; console sessions carry full console authority for 8 h; sudo grants are pattern-scoped |
| 8 | Revocability: agent and reader access revocable independently | **ENFORCED** | separate planes, per-connection and per-grant revocation |
| 9 | Emergency containment: global controls fail closed | **ENFORCED** | gateway freeze refuses every endpoint; reader freeze refuses every sign-in path |
| 10 | Explicit reopening | **ENFORCED** for the agent gateway (signed thaw); **NOT ENFORCED** for reader sign-ins (resume needs a session, no signature) | §7.3, §8 |
| 11 | Canonical authorization: signatures bind canonical material, not labels | ~~ENFORCED for the command / spec; NOT ENFORCED for the reason text~~ **ENFORCED (2026-10-07)** for command, pattern, reason, requester, kind, expiry and action | §3 |
| 12 | Audit honesty | **ENFORCED** for the event record (governance events chained and verified every 15 min); **PARTIAL** against a writer who recomputes the whole chain (external anchor: journaled head hashes) | §11 |
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
| Direct invocation bypassing the sudo gate | ~~possible for NOPASSWD patterns~~ **refused once the sudoers change is applied**: the only passwordless rule is the root executor, which re-verifies the signature | `sudo -n -l` after the deploy relay; `tests/scripts/test_approved_exec.py` |
| Agent GPG key used without a grant | **possible for operator-account processes** — the designed exception (§0); not possible for managed agent accounts | key protection and script inspection (§10) |
| Forged approval row (database write) used to run a root command | refused: the signature must verify against the root-owned pin | `test_a_valid_signature_verifies_and_a_forged_row_does_not` |
| Approval executed twice; late execution | refused: root ledger with `O_EXCL`; 5-minute window | `test_each_approval_executes_at_most_once`, `test_row_state_and_timing` |
| Reason or pattern edited after signing | signature fails | `test_canonical_v2_binds_pattern_and_reason` |
| Over-long request lifetime | refused (400) | `test_ttl_is_capped_per_kind`, `test_admin_route_binds_the_token_label_as_requester` |
| Console login redeemed long after approval | refused | `test_an_allowed_login_cannot_be_redeemed_long_after_expiry` |
| Audit row edited or removed | flagged within 15 min | `common.audit_chain` in the integrity sweep |
| Production destructive tests | not performed | by design; covered by fixtures above |

---

## 14. Drift checks

These are stable, deterministic checks, not prose greps:

- `tests/scripts/test_signing_identities_distinct.py`: the operator and agent manifest keys differ, in the tracked pin and in the root-owned live pin.
- `tests/web/test_signed_approvals.py`: the expiry, single-use, exact-binding, self-approval and board-key checks above.
- `tests/web/test_agent_gateway.py`: the connector-isolation and kill-all checks above.
- `tests/web/test_console.py::test_only_the_tailnet_host`: the console answers only on the configured overlay host.
- `tests/scripts/test_approved_exec.py`: the root executor's canonical text stays byte-identical to the server's, and its refusals hold.
- `tests/web/test_signed_approvals.py`: TTL caps, v2 binding, requester binding, governance audit events.
- The integrity sweep's audit-chain verification (live, every 15 minutes).

Deliberately **not** checked: TTL numbers (they are policy and may change; this document's §3 table records them), and network reachability (not deterministic in CI; verified by the live inspection recorded in §5.1).

## 15. Public mirror

This document is public-safe as written. It contains no addresses, hostnames, credentials or key fingerprints. Private-only material stays out: the exact firewall rules, the overlay hostname, the sudoers file contents beyond the rule patterns named in §9.1, and the operator's account names.


---

## Superseded (kept for the record)

Text replaced by the 2026-10-07 hardening pass, in its original wording. It is **not** current.

### 11. Audit evidence (K) — as first published 2026-10-07


| ~~Event class~~ | ~~Hash-chained `audit_log`~~ | ~~Other record~~ |
|---|---|---|
| ~~Approval request created via `POST /admin/approval-requests`~~ | ~~**yes** (`require_admin` audits admin routes, including denied calls)~~ | ~~`approval_requests` row~~ |
| ~~Approval resolved by signature~~ | ~~no~~ | ~~`approval_requests.status / resolved_by / resolution_sig` (mutable table; the signature can be re-verified only while the signer's key is still registered: re-keying replaces it); one journal line~~ |
| ~~Approval resolved by deny tap~~ | ~~no~~ | ~~`approval_requests` row~~ |
| ~~Approval expired~~ | ~~no~~ | ~~`approval_requests.status = expired` (set on read)~~ |
| ~~Console login requested / approved / denied / expired~~ | ~~no~~ | ~~`approval_requests` (kind `console-login`), `console_sessions`~~ |
| ~~Console actions~~ | ~~no~~ | ~~the target tables (`es_*`, `agent_*`)~~ |
| ~~Agent link requested / approved~~ | ~~no~~ | ~~`oauth_pending`, `approval_requests`, `agent_connections`~~ |
| ~~Agent link revoked, connector disabled~~ | ~~no~~ | ~~`agent_connections.revoked_at / revoke_reason`, `agent_connectors.disabled_at`~~ |
| ~~Gateway killed / reopened~~ | ~~no~~ | ~~`agent_gateway_settings` (`updated_by`), ntfy push~~ |
| ~~Reader invites, revokes, freeze, kill-all~~ | ~~no~~ | ~~`es_invite_events` (append pattern, ordinary table)~~ |
| ~~Agent manifest signing~~ | ~~**yes** (script-written)~~ | ~~the signature itself~~ |

~~The `audit_log` hash chain (migration 0062) is computed by a database trigger. **No code verifies the chain**, and the database role that writes rows could also rewrite them. It is tamper-evident only to someone who recomputes it independently. The governance and gateway tables above are ordinary mutable state, **not** hash-chained.~~
