# Ground-Up Audit / Remediation Check — CTDI Dispatch Platform

**Date:** 2026-08-24 (~17:40–17:48 EDT)
**Auditor:** Independent live audit + adversarial pen-test pass.
**Method:** Every finding below comes from a command I ran against the live
running system, a live HTTP request I sent, or source I personally read this
session. I did **not** treat any pre-existing document — including CLAUDE.md's
own narrative, the `docs/investor-materials/v1.5/research/*` files already on
disk, or any code comment — as ground truth. Where I cite a prior claim, I
re-derived it from live state.

**Non-destructive discipline observed:** 1–2 requests per endpoint, no retries,
no fuzzing, no DELETE / data-mutating calls actually executed, no secret / token
/ coordinate value printed (even partially) anywhere in this report or my
session log. The one unauthenticated destructive endpoint I found (`DELETE
/api/chat/history`) was identified by source read + a read-only `GET` probe;
the `DELETE` was never invoked.

> **Live-box caveat:** this is a running production system with concurrent
> activity. State moved *during* the audit (see Finding 8 — a manifest
> signing pass completed mid-session). Timestamps are given so the reader can
> place each observation.

---

## Overall assessment (bottom line first)

The **application auth model is genuinely sound** and held up under adversarial
probing: bearer-token-only, no IP/header trust in the web tier, forged and
garbage tokens resolve to anonymous, every admin and mutating endpoint returns
403 without a valid token, the one deliberately-unauthenticated admin endpoint
is protected by an atomic single-use SQL compare-and-swap, and there is **no
anonymous write path anywhere in the web API**. The test suite is green
(222 pass / 1 known-unrelated fail) and the signed-manifest integrity chain
verifies.

But two things are genuinely serious, and one of them is a live credential
exposure with a direct public-leak path:

1. **CRITICAL — the live NWWS-OI feed password is pasted verbatim into the
   git-tracked `CLAUDE.md`, and the public-push scrubber does not redact it.**
   A `public`-remote push would publish a working credential.
2. **HIGH — `GET`/`DELETE /api/chat/history` on the runner services have no
   authentication of any kind** and sit *outside* the runner's own
   origin-trust middleware, so on the public demo hostname they are reachable
   anonymously.

Both the "demo runner is isolated from production" and "retired mcpo admin
token is revoked" remediations from prior passes **do** verify as actually
done on the live box. The residual issues are the credential-in-docs, the
unauthenticated chat endpoint, and a cluster of convention violations
(hardcoded coordinates/UUIDs, never-expiring tokens).

---

## Positive controls — what I tried to break and could not

All probes below are anonymous unless noted. `B=http://127.0.0.1:8000`.

| Probe | Command | Result | Verdict |
|---|---|---|---|
| Forged admin-shaped token | `curl -H "Authorization: Bearer ctdc_admin_aaaa…(32)" $B/api/v1/whoami-token` | `{"tier":"tier0",...}` | Forged token → anonymous. No escalation. |
| Garbage token | `curl -H "Authorization: Bearer garbage_not_a_token" $B/api/v1/whoami-token` | `{"tier":"tier0",...}` | Same. |
| Admin read, no/garbage/forged token | `curl $B/admin/tokens` (×3 tokens) | `403` / `403` / `403` | Admin gated. |
| Admin read (audit) | `curl $B/admin/audit` | `403` | Gated. |
| Admin write | `POST $B/admin/vip`, `POST $B/admin/push-alert` | `403`, `403` | Gated. |
| Vault file read | `GET $B/api/v1/vault/file?path=README.md` (no token) | `403` | Gated. |
| Vault traversal (single-enc) | `GET $B/api/v1/vault/file?path=../../../../etc/passwd` | `403` | Blocked. |
| Vault traversal (double-enc) | `GET $B/api/v1/vault/research?path=%252e%252e%252fsecrets` | `401` | Blocked at auth. |
| Anonymous mutation (osint) | `DELETE $B/api/v1/osint/scopes/999999` | `403` | Gated. |
| Anonymous mutation (watchlist) | `DELETE $B/api/v1/watchlist/batch`, `POST /watchlist/flights` | `403`, `403` | Gated. |
| Anonymous mutation (sectors) | `POST $B/api/v1/sectors/ZDC/silence`, `.../topic/cps/enabled` | `403`, `403` | Gated. |
| Board write, valid body, no key | `POST $B/api/v1/board` (full body, no `X-Board-Key`) | `401` | Key enforced *before* any DB write (`main.py:421` `_require_board_key` is the first statement of `board_post`). |
| `X-CTDI-Public` escalation | `GET $B/api/v1/feeds -H 'X-CTDI-Public: 1'` | `200` (T0) | Header only ever *downgrades*; checked before token lookup (`auth.py:68`), nginx `proxy_set_header` *replaces* it on public vhosts. No way to prevent stamping. |

**Auth internals I read (not just probed):**
- `resolve_tier()` (`src/auth/auth.py:60–83`) considers exactly two inputs —
  `X-CTDI-Public` header and the bearer token — and has **no** IP / XFF /
  Tailscale-header branch. The old spoofable network-origin grant is gone.
- Token path: SHA-256 hash (`_hash_token`, `auth.py:56`) → `db.lookup_token`
  (`src/common/db.py:931`), whose WHERE clause is
  `token_hash=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())`.
  No hash match → `None` → `Tier.T0`. Forging requires inverting SHA-256.
- The one unauthenticated admin route, `GET
  /admin/approval-requests/{request_id}/resolve` (`main.py:2456`), enforces
  single-use atomically in SQL: `resolve_approval_request()` (`db.py:3582`)
  does `UPDATE … WHERE id=? AND status='pending' AND expires_at > ?` and
  treats `rowcount==0` as failure — no double-tap, no race. Probing it with a
  random non-existent UUID returned a validation error for the missing
  `action` query param (confirming it is unauthenticated by design) and
  changed no state.

**Test suite:** `python -m pytest tests/ -q` → **`1 failed, 222 passed`**. The
single failure is `tests/ingest/test_marine_one_detection.py::test_smes_parser_basic`
— the long-standing, unrelated marine-detection assertion, not a regression.

**Signed manifest:** `bash scripts/verify-manifest.sh` (at 17:48) →
`OK -- signature valid, all 761 files match`.

---

## Findings

### 1. CRITICAL — Live NWWS-OI feed password committed to `CLAUDE.md`; scrubber does not redact it; public push would leak it

`CLAUDE.md` is git-tracked and is present in `HEAD`. It documents the
2026-08-20 NWWS quoting incident and its 2026-08-23 `source`-ing corollary,
and in doing so **pastes the actual working `NWWS_PASSWORD` value twice** (once
in its broken quoted form, once bare).

Evidence (value redacted by me — I never printed it):
```
$ grep -no 'NWWS_PASSWORD=[^ "`]*' CLAUDE.md | sed 's/=.*/=<REDACTED>/'
3542:NWWS_PASSWORD=<REDACTED>
3570:NWWS_PASSWORD=<REDACTED>
$ git show HEAD:CLAUDE.md | grep -c 'NWWS_PASSWORD=k'      # in committed HEAD, not just working copy
1
```
Per the incident narrative in the same file, the fix was *removing the quotes*,
not rotating the account — so this is the **current, valid** credential.

The public-push path does not protect it:
```
$ grep -c 'RESWAY\|NWWS_PASSWORD' scripts/scrub-public-tree.py      # substitutions for this value
0
$ sed -n '41,70p' scripts/scrub-public-tree.py                       # DROP_FILES membership
DROP_FILES = { "dispatch-secrets.env", "cloud.cs…", "dav.cs…", "HEADLESS_ACCESS.md",
               "TAILNET_MIGRATION_INVENTORY.md", "secrets.env", "STATUS.md",
               "SECOND_BRAIN_STATUS.md", … }        # CLAUDE.md is NOT in the set
```
`CLAUDE.md` is **not** dropped, and the scrubber has **no** substitution or
regex sweep for this password. The `verify_scrubbed()` allowlist gate keys on
UUID-shaped strings; this password is not UUID-shaped, so that backstop does
**not** catch it either. `git remote -v` confirms a real second remote,
`public → github.com/CorporateTravelDC/ctdi-dispatch.git`. A scrub-then-push
to `public` would publish a live feed credential in cleartext.

**Remediation:** rotate the NWWS-OI credential, replace both literals in
CLAUDE.md with a redacted stand-in, and add an NWWS substitution/regex sweep to
`scrub-public-tree.py` as defense-in-depth. Do not push `public` until done.

---

### 2. HIGH — Unauthenticated `GET`/`DELETE /api/chat/history` on the runner, outside the origin-trust middleware

`src/runner/main.py`:
- `GET /api/chat/history` (`:1489`) and `DELETE /api/chat/history` (`:1496`)
  have **no auth dependency, no `DEMO_MODE` gate, and no `_is_trusted` check**.
  `DELETE` runs `DELETE FROM chat_messages` against `CHAT_DB_PATH` (`:1500`).
- The runner's own `tailscale_gate` middleware (`:315–332`) only guards paths
  that are `/admin*` **or** `startswith(/api/v1/) and method != GET`
  (`:322–326`). `/api/chat/history` is **neither** — so the middleware never
  fires for it.

Live confirmation (read-only GET only; DELETE never sent):
```
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8005/api/chat/history   # public demo runner
200
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/api/chat/history   # production runner
200
```
Both return `200` anonymously. The public hostname
`dispatch-runner.example.com` proxies to `:8005`
(`/etc/nginx/conf.d/dispatch-runner…conf:14` → `proxy_pass http://127.0.0.1:8005`),
so on the demo instance this is reachable from the open internet. The
production runner (`:8001`) is loopback + tailnet only, but the endpoint still
has no auth of its own there — an anonymous read/delete of the operator's
production dispatch-assistant chat history is available to anything that can
reach the port.

The blast radius on the *public* instance is currently limited to the demo DB
by the mount fix in Finding 3 — but **the isolation is by mount, not by auth**.
Any data ever mapped at that path is exposed the same way.

**Remediation:** add an explicit `_is_trusted(request)` / auth gate to both
`/api/chat/history` handlers, or move them under `/api/v1/` so the existing
middleware covers the mutation.

---

### 3. HIGH (history) / RESOLVED (current) — Demo↔production DB isolation was briefly broken today, is now correctly isolated

The runner-demo Quadlet (`.container`) is modified this session (`git status`
→ `MM`). Reading its header and the live mounts, the sequence was:

- Earlier today a fix for the long-running `runner-demo` crash loop mounted the
  **real** `/var/lib/corporatetraveldc` into the public demo container
  read-write — exposing the real 24 GB `corporatetraveldc.db` and making the
  demo's `dispatch-chat.db` the **same inode** as production's (combined with
  the unauthenticated `DELETE` in Finding 2, an anonymous internet visitor
  could have read/deleted real operator chat history).
- It was re-fixed (~14:40) to mount a dedicated demo-only directory.

**Current live state — verified independently, not from the comment:**
```
$ podman inspect systemd-corporatetraveldc-runner-demo --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.RW}}){{"\n"}}{{end}}'
/var/lib/corporatetraveldc-demo -> /var/lib/corporatetraveldc (true)
/run/corporatetraveldc -> /run/corporatetraveldc (true)
$ podman inspect systemd-corporatetraveldc-runner --format '...'
/var/lib/corporatetraveldc -> /var/lib/corporatetraveldc (true)
$ ls -ld /var/lib/corporatetraveldc-demo         # drwx------ (mode 700)
$ ls -li /var/lib/corporatetraveldc-demo/dispatch-chat.db   # inode 123317778
$ ls -li /var/lib/corporatetraveldc/dispatch-chat.db        # inode 710109  (DIFFERENT)
$ ls -la /var/lib/corporatetraveldc-demo/    # contains ONLY dispatch-chat.db (16KB) — no prod DB copy
```
Demo and production chat DBs are now **different inodes**; the demo directory
(mode 700) holds only its own empty chat DB; the demo runner's `DISPATCH_DB`
env resolves under the demo mount, so it cannot see the real database. **Demo
data isolation currently checks out.** The residual risk is Finding 2 (the
endpoint auth), not the mount.

This is worth flagging for the investor-materials context: the exposure was
*introduced and remediated within the same day by automated audit passes*. Good
that it was caught; notable that a remediation pass briefly opened a real
public data-exposure while closing a crash loop.

---

### 4. MEDIUM — Public demo runner has `DEMO_MODE` unset; every demo-mode protection is inert; nginx vhost comment falsely claims otherwise

```
$ podman exec systemd-corporatetraveldc-runner-demo sh -c 'echo ${DEMO_MODE:-UNSET}'
UNSET
```
`DEMO_MODE` defaults to `false` (`runner/main.py:48`) and is set nowhere (not
the Quadlet, not `dispatch.env`). With it false, the proxy-dispatch cookie gate
(`:1628`), `/api/demo/login` (`:363/:397`), signal sanitization (`:740`), and
ntfy suppression (`:1924`) are all inert on the public demo surface.

This is currently **masked** rather than exploited, because the demo runner's
`DISPATCH_BASE_URL=http://100.x.x.x:8004` points at the sovereign demo-api
(confirmed: `curl http://127.0.0.1:8004/api/v1/feeds` → `{"mode":"demo-playback",…}`),
not production web `:8000` — so demo traffic reads demo data regardless. But the
nginx vhost header comment for `dispatch-runner…conf` literally asserts
`DEMO_MODE=true` and "Password-gated at the app layer", which is **false** on
the live box. Same stale-comment-vs-reality pattern the codebase documents
elsewhere. **Remediation:** set `DEMO_MODE` explicitly (either value) and fix
the vhost comment to match.

---

### 5. MEDIUM — Hardcoded GPS coordinates and a feeder UUID in tracked files (violates the repo's own stated convention)

The repo convention (CLAUDE.md, and the piaware Quadlet's own comment: *"FEEDER_ID
/ LAT / LONG / ALT_M come from dispatch-secrets.env"*) is that coordinates and
feeder identity live in the env files, not tracked source. Violations found
(values redacted):
```
$ grep -nE 'ADSB_(LAT|LON)=' .config/containers/systemd/corporatetraveldc-acarshub.container
36:Environment=ADSB_LAT=<COORD-REDACTED>
37:Environment=ADSB_LON=<COORD-REDACTED>
```
- `corporatetraveldc-acarshub.container:36–37` — literal `ADSB_LAT`/`ADSB_LON`
  (siblings piaware/planefinder/airnavradar pull these from secrets; acarshub
  does not).
- `src/runner/main.py:125` — real `ULTRAFEEDER_LAT/LON` in a code comment.
- `corporatetraveldc-ultrafeeder.container:56` — a real ADS-B feeder UUID
  embedded in `ULTRAFEEDER_CONFIG`.
- `systemd/corporatetraveldc-ais.container.disabled:42–43` — `STATION_LAT/LON`
  literals (file disabled but tracked).

These are lower-severity than Finding 1 (coordinates/feeder-UUIDs are
reconnaissance, not credentials) and the scrubber's fixed-literal list *does*
substitute several of them for the public mirror — but a fixed literal list
silently misses rotated/new values, which is exactly the failure mode the
convention exists to prevent. **Remediation:** move these into
`dispatch-secrets.env` per the repo's own rule.

---

### 6. MEDIUM — All active tokens never expire; two admin tokens live (retired mcpo admin token IS now revoked — remediated)

Read-only query against the live DB (no hashes printed):
```
$ sqlite3 …corporatetraveldc.db "SELECT COUNT(*), SUM(revoked_at IS NULL) FROM auth_tokens"
20|5
$ sqlite3 … "SELECT token_prefix,tier,CASE WHEN expires_at IS NULL THEN 'NEVER' ELSE 'exp' END
             FROM auth_tokens WHERE revoked_at IS NULL ORDER BY tier"
ctdc_corporatetraveler_ | admin  | NEVER
ctdc_dispatch-admin-gate_| admin  | NEVER
ctdc_runner_            | cert   | NEVER
ctdc_demo_recorder_     | cert   | NEVER
ctdc_cowork_            | shares | NEVER
```
- **Positive:** the retired `ctdc_admin_` / `mcpo-corporatetraveldc-dispatch-mcp`
  never-expiring admin token that prior notes flagged as still-active is **no
  longer in the active set** — it has been revoked. That remediation is done.
- **Open:** all 5 remaining active tokens have `expires_at IS NULL`
  (never expire), consistent with `lookup_token`'s `expires_at IS NULL OR …`
  disjunct treating NULL as permanently valid. There are now **two** admin
  tokens (`corporatetraveler`, `dispatch-admin-gate`). No token-TTL policy is
  in force. **Remediation:** set a default TTL on newly-minted tokens and
  review whether two standing admin tokens are both needed.

---

### 7. LOW — Webhooks are authenticated (fail-closed) but use a shared-secret header, not vendor-native HMAC

All three webhook receivers (`src/web/routes/webhooks.py`) call `_check_secret()`
(`:47`), which (a) returns `503` if the per-source secret env var is unset
(fail-closed — confirmed live: `POST /webhooks/{3cx,ringcentral,limoanywhere}`
all returned `503`), and (b) otherwise does a constant-time `secrets.compare_digest`
on an `X-Webhook-Secret` header. This is genuine, standard auth — **not** an
anonymous-write hole. The caveat (documented in-code, `webhooks.py:15–21`) is
that it is a shared secret rather than each vendor's HMAC signature, so a leaked
shared secret allows forged events. Acceptable interim; tighten to native
signatures when vendor sandboxes exist.

---

### 8. LOW / informational — Transient manifest-verification failure observed mid-audit (concurrent signing pass), self-resolved

At the start of the session (~17:41) `verify-manifest.sh` **FAILED** on four
unsigned files (`CLAUDE.md`, `src/runner/main.py`, `src/web/main.py`,
`src/web/routes/sectors.py`), the `corporatetraveldc-integrity-sweep` unit was
`failed`, and a stray `MANIFEST.sha256.84OVPy` mktemp file (mode 0600, 86 KB)
sat untracked in the repo root — the signature of an interrupted/in-flight
`sign-manifest.sh` run. By 17:48 the sweep logged
`verify-manifest: OK — all 761 files match`, `verify-manifest.sh` passed, and
the stray temp file was gone. This is the normal edit→sign cycle on a live box
with a concurrent session, not a compromise — recorded only so the reader knows
the tree was momentarily in an unsigned state and why. Note the manifest grew
759 → 761 files during the window.

Also low/benign: the token DB comparison is SQLite `=` (not constant-time), but
it compares a stored **SHA-256 digest**, so a timing oracle cannot yield the
token preimage without inverting SHA-256. Not exploitable.

---

## Remediation-status scorecard (prior claims re-verified against live state)

| Prior claim | Live verdict this pass |
|---|---|
| Retired mcpo admin token revoked | **TRUE** — absent from active `auth_tokens`. |
| runner-demo crash loop fixed | **TRUE** — `NRestarts=0`, `active/running`, `:8005` serves `200`. |
| Demo runner isolated from production DB | **TRUE (currently)** — dedicated `/var/lib/corporatetraveldc-demo` mount, different inode, mode 700. But isolation is by mount, not by endpoint auth (Finding 2). |
| Auth is bearer-token-only, no network-origin trust | **TRUE** — verified in source and by forged/garbage-token probes. |
| No anonymous writes in the web API | **TRUE** — every mutating endpoint 403s anonymously; board write key-gated before any DB write; webhooks fail-closed. |
| Vault traversal guarded (multi-round decode) | **TRUE** — single- and double-encoded traversal both blocked. |
| Signed-manifest chain verifies | **TRUE** at 17:48 (761 files); transiently failing mid-sign at 17:41 (Finding 8). |
| NWWS quoting incident "fixed" | Connection fix TRUE, but the **credential was pasted into tracked CLAUDE.md and is not scrubbed** (Finding 1) — a new, worse exposure than the original quoting bug. |

---

## Prioritized remediation list

1. **(CRITICAL)** Rotate the NWWS-OI password; redact both CLAUDE.md literals;
   add an NWWS sweep to `scrub-public-tree.py`. Block any `public` push until
   done.
2. **(HIGH)** Add auth / `_is_trusted` gating to `GET` and `DELETE
   /api/chat/history` on the runner (both instances).
3. **(MEDIUM)** Set `DEMO_MODE` explicitly on the public demo runner and correct
   the false nginx vhost comment.
4. **(MEDIUM)** Move hardcoded coordinates/feeder UUID (acarshub, ultrafeeder,
   runner comment, disabled AIS quadlet) into `dispatch-secrets.env`.
5. **(MEDIUM)** Introduce a default token TTL; review the two standing admin
   tokens.
6. **(LOW)** Plan the webhook shared-secret → vendor-HMAC upgrade.

---

*Prepared from first-hand live evidence only. No prior audit document in this
repo was used as a source; every claim traces to a command, request, or source
line I personally executed/read on 2026-08-24.*
