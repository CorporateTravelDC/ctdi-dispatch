# Ground-Up Audit & Adversarial Re-Validation — CTDI Dispatch Platform
**Date:** 2026-08-24 (evening, EDT)
**Scope:** Full independent, ground-up live audit + non-destructive penetration test of the running production system at `/opt/corporatetraveldc/private/ctdi-dispatch-internal` (rootless Podman/systemd on a single Raspberry Pi 5).

## Independence statement

Every finding below comes from source I personally read, a query I ran against the live running system, or a request I sent to a live endpoint during this session. I deliberately did **not** read any pre-existing research/findings/pentest document under `docs/investor-materials/v1.5/research/` (or elsewhere) as a source of truth. Where a live artifact (a Quadlet comment, CLAUDE.md) is quoted, it is quoted as *evidence of the running system's state*, then independently verified — not accepted as a prior conclusion. Discipline held throughout: 1–2 requests per endpoint, no retries, no fuzzing, no `DELETE`/data-mutating calls against real state, and no secret/token/coordinate value printed anywhere.

Non-destructive method for mutating endpoints: send a no-token request and read the HTTP status. `401/403` = auth enforced before the handler; `422` = auth bypassed and body validation was reached; `2xx` = anonymous write. Well-formed bodies were only sent where source review confirmed the auth check runs before any write.

---

## 1. Overall assessment

The platform's **core bearer-token auth model is sound and correctly implemented**: admin endpoints reject missing and forged tokens, token lookup is constant-shape with real expiry enforcement, network origin grants no tier, webhooks fail closed, the board-write path uses constant-time comparison with single-use nonces, and a genuinely careful multi-round path-traversal guard is in place. The signed-manifest integrity gate is *working* — it is actively refusing to run mismatched code right now.

The material risk is concentrated in **two areas**, both real and both current:

1. **The public "demo" runner surface.** `dispatch-runner.example.com` is a live public Cloudflare ingress, `runner-demo` is **currently up and serving** (not crash-looping as the internal docs claim), and its app-layer demo protections are **entirely inert because `DEMO_MODE` is unset**. Data-leak blast radius is bounded today by two accidents of configuration (the demo API lacks a watchlist route; the ntfy broker returns 403), not by the demo gate that is supposed to bound it. Unauthenticated read + destructive `DELETE` on `/api/chat/history` exists on both runner ports; production exposure is closed only by network placement and a same-day mount fix.

2. **Public-mirror / git-history hygiene.** No *current* secret value from the env files leaks into the current public tree, but: a **6-character contiguous fragment of the current live NWWS-OI password** sits in a public research doc; a **live ADS-B feeder UUID is hardcoded in a tracked Quadlet**; a **former (pre-rotation) NWWS password is quoted verbatim in `CLAUDE.md`**; and a large body of internal pentest/incident narrative is published to the public GitHub mirror.

Plus one operational-integrity finding that a naive check misses: **~18 scheduled intelligence skills are failing closed against a stale poller image** while the on-disk manifest verifies clean.

Severity roll-up: **0 critical remote-unauth-to-production-data**, **2 high** (demo surface inert protections; unauth chat DELETE), **several medium** (confused-deputy token injection, GET-based sudo grant, secret fragment in public doc, hardcoded feeder UUID), **several low**. This is a security-conscious system with real defense-in-depth, whose sharpest current edges are the public demo surface and mirror/history hygiene rather than the core API.

---

## 2. Live system state (as observed)

Commands: `systemctl --user list-units 'corporatetraveldc-*' --all`, `podman ps`.

- Core stack **up**: `corporatetraveldc-{web,poller,pusher,runner}` and all 7 ingest containers (`ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}`) — `Up 3 hours`.
- `web` on `127.0.0.1:8000` + `100.x.x.x:8000`; `runner` on `:8001`; `runner-demo` on `:8005`; `demo-api` on `:8004`; `ccw-demo` on `:8085`.
- `GET /healthz` → **HTTP 200** with body `{"status":"degraded","reason":"Stale feeds: notam", ... "token_count_active":5, "cps":{"score":"GREEN"}}`. A status-code-only health probe reads this as healthy; it is degraded. (Confirms the documented check-9 blind spot is a real property of the running system.)
- **18 timer-triggered units in `failed` state** — see §9.

---

## 3. Auth model as actually implemented

Two independent enforcement planes (read from `src/auth/auth.py`, `src/common/db.py`, `src/web/main.py`, `src/runner/main.py`):

**Plane 1 — `dispatch-web` (:8000): pure bearer-token tiering.**
- `resolve_tier()` (`src/auth/auth.py:60`): `X-CTDI-Public == "1"` forces `Tier.T0` *before* any token lookup (`:68`); otherwise SHA-256 the bearer, `db.lookup_token()` (`:73`), map tier string → enum, default `T0`. `X-CTDI-Public` can only *downgrade*, never elevate. The old Tailscale-header/IP grant is gone (`:20-28`) — **network origin grants no tier**, verified in code.
- `require_admin(action)` is a factory (`:140`) that resolves the caller prefix and writes a `db.audit()` row (actor, action, tier, IP, and body on mutating verbs) *before* the handler runs (`:169-201`).
- **Token expiry is genuinely enforced** — `db.lookup_token()` (`src/common/db.py:931`): `WHERE token_hash=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > unixepoch())`. `NULL` expiry = permanent by design.

**Plane 2 — `runner` (:8001/:8005): IP-origin trust proxy, no tiers of its own.**
- `_is_trusted()` (`src/runner/main.py:193`) is IP-based only — `CF-Connecting-IP` if present, else `request.client.host`/XFF against `_TRUSTED_NETS` (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `100.64/10`).
- `tailscale_gate` middleware (`:333`) 404s `/admin/*` and **non-GET** `/api/dispatch/api/v1/*` from untrusted origins.
- `proxy_dispatch()` (`:1624`) forwards to `DISPATCH_BASE_URL`, injecting a cert-tier service token for `_TIER1_PATHS`.

---

## 4. Live API penetration test — tier matrix

`B=http://127.0.0.1:8000`. Enumerated 94 paths via `GET /openapi.json`.

### 4.1 Admin endpoints — correctly gated
No token and forged tokens both rejected:

| Request | Result |
|---|---|
| `GET /admin/{tokens,audit,feeds,version,healthz,watchdog/status,vip,triggers,approval-requests}` (no token) | **403** each |
| `GET /admin/tokens` `-H "Authorization: Bearer ctdc_admin_00000000...0"` (right prefix, wrong hash) | **403** `{"detail":"Admin tier required"}` |
| `GET /admin/audit` `-H "Authorization: Bearer notatoken"` | **403** |
| `GET /admin/tokens` `-H "Authorization: Bearer ctdc_admin_deadbeef...deadbeef"` | **403** `{"detail":"Admin tier required"}` |

Forging a token with the correct `ctdc_admin_` prefix does **not** work — only the SHA-256 hash lookup matters. Verified.

### 4.2 Mutating endpoints — correctly gated
No-token `POST/DELETE` (`-d '{}'`):

| Endpoint | Code | Verdict |
|---|---|---|
| `POST /api/v1/remember` | 403 | gated |
| `POST /api/v1/watchlist{,/flights,/trains,/vessels}` | 403 | gated |
| `DELETE /api/v1/watchlist/batch` | 403 | gated |
| `POST /api/v1/osint/scopes` | 403 | gated |
| `POST /admin/push-alert`, `POST /admin/vip` | 403 | gated |
| `POST /api/v1/sectors/topic/dispatch/throttle` | 403 | gated |
| `POST /api/v1/board` | 422 → then **401** with a well-formed body | gated (key checked *inside* handler after body validation) |
| `POST /webhooks/{3cx,limoanywhere,ringcentral}/events` | 503 | fail-closed (secret env unset) |

`POST /api/v1/board`: the `422` was only Pydantic body validation; `board_post()` (`src/web/main.py:421`) calls `_require_board_key(request)` before any DB write. A well-formed body with no `X-Board-Key` returns **401** (verified live). No anonymous board write.

### 4.3 Anonymous-readable endpoints (Tier-0) — info-disclosure review
Returned data with **no token**:

- `GET /api/v1/whoami-token` → `{"tier":"tier0",...}` — benign (just reports the caller is anonymous).
- **`GET /api/v1/osint/feed` → 200 with real OSINT items** — titles, source URLs, scored narratives (`[LOW] NBC4 Washington ...`). This endpoint has **no tier dependency** (`osint_feed()`, `src/web/main.py:1438`), while its sibling `/api/v1/osint/scopes` *is* tier-gated. The OSINT item feed can carry executive-protection / subject-monitoring output. **MEDIUM info-disclosure** — see F6.
- **`GET /api/v1/data-usage` → 200** with `grand_total_gb: 3205.94`, per-interface rx/tx (`tailscale0`, `wld0`), and a 30-day daily byte breakdown. Anonymous infrastructure/telemetry disclosure. **LOW**.
- `GET /api/v1/vault/research/list` → **401 `{"detail":"missing or invalid X-Board-Key"}`** — correctly key-gated (separate credential plane).
- `GET /api/v1/vault/file` → **403** — bearer-gated. Good.

### 4.4 Passwordless-sudo resolve endpoint — design review
`GET /admin/approval-requests/{request_id}/resolve?action=allow|deny` (`src/web/main.py:2456`) is deliberately Tier-0 (no auth dependency; CF strips `Authorization`). Live probe with a bogus UUID and no `action` → **422** (validation reached, confirming no auth dependency). The single-use/expiry protection is **real and atomic** in code — `db.resolve_approval_request()` (`src/common/db.py:3582`):
```sql
UPDATE approval_requests SET status=?, resolved_at=?
 WHERE id=? AND status='pending' AND expires_at > ?
```
with a `rowcount == 0` guard, and `action` constrained to `^(allow|deny)$` at the route. Residual weakness is the **HTTP shape**, not the DB logic — see F5.

---

## 5. Demo / runner isolation (deep-dive)

### 5.1 `runner-demo` is UP, not crash-looping (divergence from internal docs)
- `podman ps`: `systemd-corporatetraveldc-runner-demo ... Up 5 hours`.
- `systemctl --user show corporatetraveldc-runner-demo.service -p NRestarts -p ActiveState -p SubState` → **`NRestarts=0`, `active`, `running`**.
- `GET http://127.0.0.1:8005/` → 200; `GET /api/whoami` → `{"tailnet":true}`.

CLAUDE.md describes this unit as "constant crash loop, `NRestarts` ~56,461." **That is no longer true.** Root cause (missing DB dir) was fixed by re-adding a mount.

### 5.2 Demo data isolation — remediated same-day, verified real
- `podman inspect` mounts: `/var/lib/corporatetraveldc-demo -> /var/lib/corporatetraveldc`. The container's internal production path is backed by a **separate host directory**.
- `ls -ld` : `/var/lib/corporatetraveldc-demo` is **`drwx------` (mode 700)**, sibling of production `/var/lib/corporatetraveldc` (755). Contents: a single fresh `dispatch-chat.db` (16 KB). Production DB is **not** exposed to the demo.
- The Quadlet header comment (`~/.config/containers/systemd/corporatetraveldc-runner-demo.container`) documents that a live pentest earlier today found the real 24 GB `corporatetraveldc.db` had been mounted read-write into the public demo and that `dispatch-chat.db` shared an inode with production; both were remediated by the demo-scoped mount. I verified the fixed end-state directly (isolated dir, empty demo chat DB).

### 5.3 F1 (HIGH) — demo protections are inert on a live public surface
`DEMO_MODE = os.getenv("DEMO_MODE","false") ... == "true"` (`src/runner/main.py:48`). Verified unset:
- `podman exec systemd-corporatetraveldc-runner-demo env | grep -i DEMO_MODE` → **no output**.
- `grep -i DEMO_MODE /etc/corporatetraveldc/dispatch.env dispatch-secrets.env` → **no output**.
- Quadlet sets `DISPATCH_BASE_URL`, `ULTRAFEEDER_URL`, `SSE_INTERVAL_SEC` — **no `DEMO_MODE` line**.

Every demo protection keys off this flag, so all no-op on the publicly-reachable instance:
- `proxy_dispatch()` password gate `if DEMO_MODE and not _is_trusted` (`:1646`) — never fires ⇒ no login.
- Signal sanitization `_should_sanitize_signals()` = `DEMO_MODE and not _is_trusted` (`:757`) — always False ⇒ raw signals.
- Synthetic ntfy replacement (`_synthetic_ntfy_stream`, `:1951`) — never triggers ⇒ the raw real-ntfy proxy runs instead.

**And `dispatch-runner.example.com` is a live public ingress:** `~/.cloudflared/config.yml:34-37` — `# dispatch-runner — public rolling demo ... Open over tunnel same as ops`, `- hostname: dispatch-runner.example.com`.

**What actually bounds the leak today (both accidental, not the demo gate):**
- Proxied production data: `GET http://127.0.0.1:8005/api/dispatch/api/v1/watchlist` → **404** — because `DISPATCH_BASE_URL` points at the demo-api (`:8004`), whose route set (from its `/openapi.json`) has **no** `/api/v1/watchlist`. (By contrast the production runner `:8001` proxies the same path to `:8000` and returns real VIP data — `{"entries":[{"id":"perm-flight-ba293",...}]}` — but `:8001` is tailnet-only.)
- Real ntfy alerts: `GET http://127.0.0.1:8005/api/ntfy/stream` (2 s grab) → `data: {"type":"heartbeat"}` then `data: {"type":"error","detail":"ntfy returned 403"}`. The synthetic-demo replacement is inert, so it attempted the **real** ntfy broker and was blocked by ntfy's own **403**. Real alerts (which carry flight/TFR/ops intelligence) are prevented from leaking only by the ntfy server's auth, a fragile backstop.

Fix: set `DEMO_MODE=true` on the demo container, or make the runner fail closed when `DEMO_MODE` is unset on a publicly-published instance.

### 5.4 F2 (HIGH) — unauthenticated read + destructive DELETE on `/api/chat/history`
`src/runner/main.py:1507` `GET /api/chat/history` and `:1514` `DELETE /api/chat/history` (`DELETE FROM chat_messages`, `:1519`) have **no auth dependency, no `_is_trusted` check, no `DEMO_MODE` gate** (confirmed by reading the handlers).
- `GET http://127.0.0.1:8005/api/chat/history` → 200 `{"messages":[],"count":0}` (demo, now isolated).
- `GET http://127.0.0.1:8001/api/chat/history` → 200, `count: 2` (**real production chat history**, tailnet-only).
- I did **not** issue the `DELETE`.

The demo mount fix contained the *data*; the endpoints remain fully unauthenticated. A mount fix is not an auth fix. Any origin that reaches a runner port can read and erase chat history. Production is protected only by network placement (tailnet).

### 5.5 F3 (MEDIUM) — unconditional cert-token injection for Tier-1 GETs
`_dispatch_proxy_headers()` (`src/runner/main.py:1611-1613`) injects `Authorization: Bearer {RUNNER_ENRICHED_TOKEN}` for `path in _TIER1_PATHS` (`:1528`: `tfr-enriched`, `radio`, `cui/status`, `watchlist`, `watchlist/history`) **regardless of origin trust** (unlike the `_is_trusted`-gated `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`). `tailscale_gate` only blocks `/admin/*` and non-GET `/api/v1/*`, so any untrusted origin's **GET** reaches the proxy and is handed the cert token. Real-world exposure today: bounded on the demo (`DISPATCH_BASE_URL=:8004`, no watchlist route → 404) and network-bounded on prod (`:8001` tailnet-only). But the code **default** for `DISPATCH_BASE_URL` is production `:8000` (`:40`) — any public runner using the default points this straight at production Tier-1/CUI data.

### 5.6 F4 (MEDIUM) — confused deputy: runner strips the public-origin downgrade
The runner never forwards `X-CTDI-Public` (`_dispatch_proxy_headers` sets only `Authorization`/`Content-Type`), and a client-supplied `Authorization` wins (`:1607-1610`). Since `resolve_tier()` only forces T0 on `X-CTDI-Public == "1"`, a public caller presenting a valid cert/shares token via `/api/dispatch/...` reaches dispatch-web *without* the T0 downgrade the direct public vhost applies. Limited to Tier-1/2 GET reads (`tailscale_gate` still blocks mutations/admin), but it means a leaked non-admin token is usable from the open internet through the runner. Fix: forward `X-CTDI-Public: 1` on untrusted-origin proxy calls, or drop client `Authorization` on untrusted origins.

### 5.7 F7 (LOW) — header-derived trust is brittle
`_is_trusted()` decides from `CF-Connecting-IP` when present, else client `X-Forwarded-For`/`request.client.host` against `_TRUSTED_NETS` (which includes `127/8` and all RFC1918). The whole origin-trust model rests on the invariant "public traffic always carries a Cloudflare-set `CF-Connecting-IP` and no other network path to the port exists." Any non-Cloudflare path to a runner port + a spoofed `X-Forwarded-For: 100.64.0.1` (or `CF-Connecting-IP` set to a trusted value) satisfies trust. Not practically exploitable today (ports bound only to loopback + tailnet), but worth an explicit "only honor `CF-Connecting-IP` from the known cloudflared peer" assertion.

### 5.8 Other demo surfaces — clean
- `demo-api` (`:8004`): `GET /admin/demo/profiles` → **503** (feature-gated); `GET /api/v1/feeds` → `{"mode":"demo-playback",...}`; `GET /api/v1/brief` → 500 (empty demo DB). No real production data observed. Backed by `/var/lib/corporatetraveldc-demo-source` + `-demo-state` (isolated).
- `ccw-demo` (`:8085`): unrelated nginx static preview, HTTP Basic active. Out of scope for dispatch auth.

---

## 6. Secrets in the tracked tree

Method: enumerated env-file **keys only** (`cut -d= -f1`), then cross-referenced every live value ≥4 chars against the tracked tree via `git grep -nF`, reporting locations only. No value printed.

**Env inventory (names only):** `/etc/corporatetraveldc/dispatch-secrets.env` holds ~96 keys — SWIM NMS per-feed creds, FlightAware/FR24/airframes/feeder keys + UUIDs, GPS lat/lon/alt sets, `CF_MANAGEMENT_API_TOKEN`/`CF_ACCOUNT_ID`/`TAILSCALE_API_KEY`, platform tokens (`DISPATCH_ADMIN_TOKEN`, `RUNNER_ENRICHED_TOKEN`, `BOARD_KEY`, `NTFY_TOKEN`, `DEMO_SESSION_SECRET`), `NWWS_PASSWORD`, international-aviation and marine keys. `dispatch.env` holds non-secret config only. The env files live in `/etc/corporatetraveldc/` — **outside the repo** (`git check-ignore` confirms out-of-tree); `.gitignore` additionally lists `dispatch-secrets.env`/`secrets.env` explicitly. No env file is tracked.

**Findings:**

- **CONFIRMED-LEAK — live ADS-B feeder UUID hardcoded in a tracked Quadlet.** `.config/containers/systemd/corporatetraveldc-ultrafeeder.container:56` — the `Environment=ULTRAFEEDER_CONFIG=...` line embeds `uuid=<36-char UUID>` byte-identical to the live `FEEDER_ID`/`PIAWARE_FEEDER_ID`. Present in the private tree and git history (scrubbed on the public mirror). The scrubber's own comment already flags it and recommends moving it to the secrets env.
- **CONFIRMED-PRESENT / ACCEPTED-BY-DESIGN** — the real SWIM NMS username and both feeder UUIDs appear as substitution *literals* inside `scripts/scrub-public-tree.py:230-231,290-291`. A substitution list must contain the literal to catch it, and the file drops itself from the public tree (`DROP_FILES`). In private history only. No SWIM *password*, queue, or any API-key value appears anywhere in the tracked tree.
- **NEEDS-REVIEW — former NWWS-OI password quoted verbatim in `CLAUDE.md:3575` and `:3604`.** A 12-char password-shaped literal assigned to `NWWS_PASSWORD` in the quoting-incident write-ups. Hash comparison vs the live value: **does not match** (pre-rotation credential). `CLAUDE.md` is now in the scrubber's `DROP_FILES`, so it cannot reach the public mirror — but it is a real historical federal-feed credential in private git history. Confirm rotation post-dates the leak; consider redacting.
- **CLEARED** — no tracked literal matches any real receiver coordinate at ≥3 decimals; the `38.x/-77.x` literals in `src/geo/dc_airspace.py`, `src/ingest/parsers/geo_filter.py`, `src/web/main.py:1877`, `useReceiverLocation.js` are public DCA-area constants / a deliberate untrusted-caller placeholder. `dispatch-secrets.env.template` is `CHANGE_ME`-only. No `ctdc_<user>_<32>` tokens, `sk-*` keys, or `BEGIN PRIVATE KEY` blocks anywhere in the tree.

**Scrubber (`scripts/scrub-public-tree.py`) — stronger than CLAUDE.md claims, with residual gaps.** It now dynamically reads every live secret value ≥8 chars and hard-fails the push if any appears in the output (`verify_scrubbed()` / `_load_live_secret_values()`), independent of its literal lists — and `CLAUDE.md` is now in `DROP_FILES` (line 122; CLAUDE.md's own in-file claim to the contrary is stale). Residual gaps: (a) live-value sweep only covers values **≥8 chars** (`_MIN_SECRET_VALUE_LEN=8`); (b) **fails open** if the secrets file is missing; (c) never reads `dispatch.env`; (d) blind to *rotated-old* values (moot only because CLAUDE.md is dropped wholesale).

---

## 7. Public git remote (`public` → github.com/CorporateTravelDC/ctdi-dispatch.git)

Commands: `git fetch public --prune --tags`, `git ls-remote public`, `git ls-tree -r --name-only public/main`, `git grep <patterns> public/main`, `git log public/main --oneline`.

- **Refs:** exactly **one** — `refs/heads/main @ ee341041...`. No tags, no other branches.
- **History:** exactly **one** squashed commit (`ee34104 chore(public): sanitize for public mirror [auto by push-public.sh]`). No internal detail in the message.
- **Tree:** 717 files public vs 776 tracked internally; the 59-file delta matches `DROP_FILES` (`CLAUDE.md`, `MANIFEST.*`, vault nginx confs, `INFRA_MAP.md`, `HEADLESS_ACCESS.md`, `SECOND_BRAIN_STATUS.md`, the scrubber/push scripts, all Modelfiles, investor PDFs). No internal-only file slipped through as a *file*.
- **Secret-value cross-check:** all live secret values ≥8 chars grepped against all 717 public blobs → **zero hits.** No `BEGIN PRIVATE KEY`; the two root `.gpg` blobs are PUBLIC KEY blocks. Real receiver coords, tailnet IP `100.x.x.x`, feeder UUIDs, and the operator's personal email are **absent** from the current public tree.

**🔴 CONFIRMED (content-level) — 6-char fragment of the *current live* NWWS password on public.** `docs/investor-materials/v1.5/research/GROUND_UP_AUDIT_REMEDIATION_CHECK_2026-08-24.md:124` (inside a quoted `grep` command) contains a token that embeds a **6-character contiguous substring of the current live 12-char `NWWS_PASSWORD`** (verified by in-memory longest-common-substring against the live secrets file; nothing printed). The full value is nowhere public and the password *was* rotated from the burned value — but the rotated value still shares this 6-char core with the publicly-quoted fragment, halving its effective secrecy. **Recommend: rotate to a value sharing no substring with either the burned value or this fragment, and redact that doc line.**

**CONFIRMED historical, remediated on current ref (values burned):** (1) real receiver + operator former-residence coordinates were previously published in a `src/runner/main.py` comment block (now redacted to env-var names); (2) the NWWS password reached public before `CLAUDE.md` entered `DROP_FILES`. The squashed history hides these on the current ref, but GitHub caches/forks may retain the pre-squash blobs — treat both as burned.

**SUSPECTED overshare (operator judgment):** 10× `docs/LIVE_STATE_CHECK_*.md`, `CLAUDE_MD_DRIFT_REPORT.md`, `DRIFT_AUDIT_*`, `SUDO_JUSTIFICATION_PROPOSAL.md`, and multiple `research/PENTEST_*` / `GROUND_UP_AUDIT_*` / `ADVERSARIAL_*` docs are all on public. They avoid printing secret *values* but disclose the production security architecture (ports, auth-tier design, trust-spoofing results, remediation gaps) — the same incident-narrative class for which `CLAUDE.md` was dropped. Also 5 residual `csexecutiveservices` string occurrences survive the scrub, including `src/web/main.py:291` where the redaction regex itself reveals the internal vault hostname `cloud.example.com`.

---

## 8. Manifest, tests, dependencies, hooks

- **Signed manifest — CLEAN right now.** `bash scripts/verify-manifest.sh` → exit **0**, `verify-manifest: OK -- signature valid, all 762 files match.` `gpg --verify` → **Good signature**, made 2026-08-24 19:47:03 EDT by EdDSA key **419A864CC29A09513039B6E03033FB4D01903159** ("Rotated Production GPG Key"). **Note:** this is a *third* key, distinct from both the commit-signing key (`3B29...1631`) and the agent-signing key (`CC15...0B37`) that `SECURITY.md`/`CLAUDE.md` name — the production manifest key is undocumented in those files.
- **Ghost tracked file.** `MANIFEST.sha256.3zbNoX` (a stray mktemp temp file) was committed in `0a7f643` and is now deleted-but-tracked with the deletion uncommitted. Invisible to the manifest (excluded by pattern) but inflates `git ls-files` (776 vs 762 manifest entries). `git rm --cached` it.
- **Tests.** `PYTHONPATH=src python -m pytest tests/ -q` → **222 passed, 1 failed** of 223. The single failure is the long-standing, documented `tests/ingest/test_marine_one_detection.py::test_smes_parser_basic` (unrelated marine detection). Healthy.
- **Dependencies.** `requirements.txt` uses **floors only, no pins**, and there is **no lockfile** of any kind → non-reproducible builds (`build-images.sh` resolves newest-above-floor each run). Installed versions are recent, not vulnerable-old (fastapi 0.139, starlette 1.3.1, cryptography 49.0.0, urllib3 2.7.0, requests 2.33.1); the risk is reproducibility, not staleness.
- **Git hooks.** `pre-commit` and `pre-push` in `.git/hooks/` are byte-identical to their `scripts/` sources. **`post-commit` is still stale** (installed mtime 2026-08-11 vs `scripts/post-commit-doc-verify.sh` 2026-08-18) — missing the second-brain prior-findings lookup and the persist step. The documented one-command fix has **not** been applied.

---

## 9. Operational integrity — ~18 scheduled skills failing closed against a stale image

`systemctl --user list-units 'corporatetraveldc-*' --state=failed` → **18 failed units**: `aam-daily-watch`, `aviation-daily-watch`, `board-sweep`, `concierge-travel-daily-watch`, `docs-drift-weekly`, `entity-tracking-digest`, `ep-advance`, `executive-protection-daily-watch`, `feed-db-integrity-check`, `gig-economy-daily-watch`, `ingest-feed-watch`, `ops-brief`, `personal-notes-import`, `pull-path-verify`, `second-brain-daily`, `second-brain-rss`, `tbfm-arrival-enrichment`, `trains-yachts-daily-watch`.

Cause (from `journalctl --user -u ...`): **`verified-exec: INTEGRITY CHECK FAILED -- refusing to run`** / `verify-manifest: INTEGRITY FAILURE`. The running `poller` image is `build-date=20260824T211010Z` (21:10 UTC / 17:10 EDT); the on-disk manifest was re-signed at **19:47 EDT** — *after* that image was built. This is the classic **sign-after-build ordering trap** CLAUDE.md documents repeatedly: the baked source no longer matches the current signed manifest, so every `verified-exec`-gated skill fails closed.

**The gate is working correctly** — it is refusing to execute code that doesn't match the signature. But the operational consequence is that the platform's entire scheduled-intelligence layer (aviation/AAM/EP/gig/concierge/trains watches, ops-brief, ep-advance, board sweep, second-brain digests, feed-integrity checks) is **silently not running**, and a naive `verify-manifest.sh` on disk (exit 0, "all 762 files match") *masks* it. Fix is the documented order: `sign-manifest.sh` → `bash build-images.sh poller` → done. Independent operational finding, not a security hole in itself, but it is a real availability gap that the clean on-disk manifest hides.

---

## 10. Independently-confirmed divergences from CLAUDE.md

Recorded because the task was to discover current reality, not trust prior write-ups:

1. **`runner-demo` is NOT crash-looping.** Live: `NRestarts=0`, up 5 h, serving 200. CLAUDE.md says constant crash loop, ~56k restarts. (Fixed by the demo mount.)
2. **The retired `ctdc_admin_` / mcpo admin token IS now revoked.** `SELECT ... FROM auth_tokens` shows `ctdc_admin_ | admin | revoked`. CLAUDE.md says it was "still not revoked." (But a *new* active admin token `ctdc_dispatch-...` now exists.)
3. **All tokens still never expire.** `SELECT count(*) FROM auth_tokens WHERE expires_at IS NOT NULL` → **0**; 2 active admin tokens, both `expires_at=NULL`. The no-TTL finding stands.
4. **`CLAUDE.md` IS now in the scrubber's `DROP_FILES`** — CLAUDE.md's own claim that it is not is stale.
5. **Tree/manifest counts** are 776 tracked / 762 manifest, well past CLAUDE.md's "706."

---

## 11. Prioritized findings

| # | Sev | Finding | Evidence |
|---|-----|---------|----------|
| F1 | **HIGH** | Public demo (`runner-demo`, live + public) has all `DEMO_MODE` protections inert; leak bounded only by demo-api lacking a watchlist route and ntfy returning 403 | `env`/`grep` DEMO_MODE unset; cloudflared:34-37; `/api/ntfy/stream`→ntfy 403; `runner/main.py:48,757,1646,1951` |
| F2 | **HIGH** | `GET`/`DELETE /api/chat/history` fully unauthenticated on both runner ports; prod chat readable on `:8001` | `runner/main.py:1507-1521`; live 200s |
| F3 | MED | Runner injects cert-tier token for Tier-1 GETs regardless of origin; code default `DISPATCH_BASE_URL`=prod `:8000` | `runner/main.py:40,1528,1611-1613` |
| F4 | MED | Runner strips the `X-CTDI-Public` T0 downgrade → leaked non-admin token usable from internet via proxy | `runner/main.py:1606-1620`; `auth.py:68` |
| F5 | MED | Passwordless-sudo grant is a bare state-mutating **GET** (prefetch/unfurl risk); DB single-use logic itself is sound | `web/main.py:2456`; `db.py:3582` |
| F6 | MED | `GET /api/v1/osint/feed` anonymous — EP/subject-monitoring narratives public-readable | live 200; `web/main.py:1438` |
| P1 | **HIGH** (hygiene) | 6-char fragment of current live NWWS password in a public research doc | public agent LCS check; `research/..._REMEDIATION_CHECK_2026-08-24.md:124` |
| P2 | MED | Live feeder UUID hardcoded in tracked `ultrafeeder.container:56` (private tree + history) | value cross-ref |
| P3 | MED | Pre-rotation NWWS password verbatim in `CLAUDE.md:3575/3604` (private history) | `git grep` + hash mismatch vs live |
| P4 | MED | ~18 scheduled skills failing closed against stale poller image (masked by clean on-disk manifest) | `journalctl` INTEGRITY FAILURE; image build 21:10 UTC vs sign 19:47 EDT |
| P5 | LOW | Overshare of pentest/incident narrative docs + internal vault hostname to public mirror | `git ls-tree public/main`; `web/main.py:291` |
| F7 | LOW | Header-derived origin trust brittle (spoofable off non-Cloudflare path) | `runner/main.py:186,242-248` |
| D1 | LOW | `data-usage` anonymous infra telemetry; no dependency pins/lockfile; stale `post-commit` hook; undocumented 3rd (production) GPG key; ghost `MANIFEST.sha256.3zbNoX` tracked | §4.3, §8 |

### Recommended immediate actions
1. Set `DEMO_MODE=true` on `runner-demo` (or fail closed when unset on any published instance). **[F1]**
2. Add an auth/trust gate to `GET`/`DELETE /api/chat/history`. **[F2]**
3. Rotate `NWWS_PASSWORD` to a value sharing no substring with the burned value or the public 6-char fragment; redact `research/..._REMEDIATION_CHECK_2026-08-24.md:124`. **[P1]**
4. Rebuild the `poller` image against the current signed manifest (sign → build → restart) to restore the ~18 scheduled skills. **[P4]**
5. Gate `/api/v1/osint/feed` behind Tier-1. **[F6]**
6. Make `_TIER1_PATHS` token injection `_is_trusted`-conditional and forward `X-CTDI-Public` on untrusted-origin proxying. **[F3/F4]**

---

*Prepared by an independent adversarial re-validation pass. Every claim above is traceable to a command, query, or file:line cited inline. No secret, token, or coordinate value was printed at any point.*
