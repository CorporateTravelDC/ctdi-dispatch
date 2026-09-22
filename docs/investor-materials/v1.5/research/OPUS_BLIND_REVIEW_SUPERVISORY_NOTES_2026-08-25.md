# Supervisory Verification of OPUS_BLIND_REVIEW_2026-08-25

**Date:** 2026-08-25
**Role:** Independent supervisory/verification agent (adversarial check on the test agent's blind audit)
**Method:** Every claim below was re-derived from source and/or reproduced against the live running system. Non-destructive throughout: no writes, no mutating endpoints completed, no secret/token/coordinate value printed. The runner *resolve* (passwordless-sudo) endpoint was **not** invoked at all; the gate bypass was proven with the harmless GET `/admin/version` instead, whose 403-vs-404 response distinguishes "reached dispatch-web auth" from "gate blocked."

**Verdict key:** CONFIRMED-BY-INDEPENDENT-RECHECK / PARTIALLY-CONFIRMED / COULD-NOT-REPRODUCE / NEEDS-HUMAN-REVIEW.

---

## Priority claims

### C-1 — Runner proxy `/./` dot-segment bypasses the admin/mutation gate → passwordless-sudo endpoint
**Verdict: PARTIALLY-CONFIRMED — bypass mechanism is real and live; public-internet severity is OVERSTATED.**

The mechanism is exactly as described and reproduces live:

- `tailscale_gate` (`src/runner/main.py:334-350`) inspects the **raw** `request.url.path` with `path.startswith(_ADMIN_PROXY_PREFIX)` / `path.startswith("/admin")`.
- `proxy_dispatch` (`:1668`) builds `url = f"{DISPATCH_BASE}/{path}"` and hands it to `httpx`, which **normalizes dot segments**. Confirmed: `httpx.URL(".../api/v1/../../admin/vip").path == "/admin/vip"` (httpx 0.28.1).
- Live on **:8001** from a forced-untrusted origin (`CF-Connecting-IP: 8.8.8.8`), using `curl --path-as-is` (plain curl normalizes `/./` client-side, which is why a naive re-test wrongly shows no bypass):

```
/api/dispatch/admin/version        -> 404 {"detail":"Not Found"}          (gate blocks)
/api/dispatch/./admin/version      -> 403 {"detail":"Admin tier required"} (gate BYPASSED; reached dispatch-web :8000 auth)
```

The 403 (not 404) proves the request passed `tailscale_gate` and hit dispatch-web's own tier check. `GET /admin/approval-requests/{id}/resolve` genuinely has **no auth dependency** (`src/web/main.py:2462-2471`, docstring: "Tier 0 -- deliberately no auth dependency"). So on :8001 the bypass does front the unauthenticated sudo resolver, as the report states for :8001.

**The overstatement — and this materially changes remediation priority.** The report's prose ("reachable from the public hostname," ranked #1, "remotely exploitable today") does not hold for the public internet:

- **:8001 binds only to `127.0.0.1` and `100.x.x.x` (tailnet)** — verified via `ss -ltnp`. It is **not** the public hostname's backend.
- The public hostname `dispatch-runner.example.com` → nginx (`proxy_pass http://127.0.0.1:8005`) → **runner-demo :8005**, whose `DISPATCH_BASE_URL = http://100.x.x.x:8004` (the **isolated demo-api**), not dispatch-web :8000. Verified in the running container's env and its quadlet.
- Live proof the public chain cannot reach the sudo endpoint: on **:8005** the same dot-segment request returns **404** (not the :8001 403), because its backend demo-api **:8004 has no `/admin/*` surface at all** (`curl :8004/admin/version` → 404 `Not Found`).
- Independently, nginx itself normalizes `/./` (public-host test through :80 returned 404 for both forms).

So the bypass is a genuine **defense-in-depth failure on the tailnet/loopback-facing runner (:8001)**, not an unauthenticated-internet-to-root primitive. Further narrowing its practical value: anyone who can already reach :8001 is on the tailnet, where their real IP makes `_is_trusted` return True and no trick is needed; the bypass only helps a request that reaches :8001 while being *classified* untrusted (a narrow scenario). **Fix is still correct and worth doing** (`posixpath.normpath` + reject `.`/`..` before the gate), but this should drop from "Critical / #1 / publicly exploitable" to "real bug, tailnet-scoped."

### C-0a — Signed-manifest gate covers `.py` but Python runs `.pyc` from a writable `/app`
**Verdict: CONFIRMED-BY-INDEPENDENT-RECHECK (persistence primitive; requires a prior write foothold).**

- `sign-manifest.sh:156` hashes `git ls-files --cached --others --exclude-standard` → `__pycache__/*.pyc` is gitignored and excluded. `MANIFEST.sha256` contains **0** `.pyc` entries (764 lines total). `verify-manifest.sh` / `verified-exec.sh` (`exec "$@"` at `:44`) validate only `.py`.
- Live preconditions in the running poller container: runs as **uid=0 (root)**, `/app` is **WRITABLE** (touch test succeeded), `PYTHONDONTWRITEBYTECODE` is **unset**, no `-B`, no `ReadOnly=`/`NoNewPrivileges=` in the quadlets, and **184 `.pyc` files already present** under `/app/src` (bytecode caching is active for imported modules).
- A PEP-552 unchecked-hash `.pyc` planted in a module's `__pycache__` executes without any comparison to the `.py`, so any write foothold in a skill container plants persistent code the gate cannot see. Real. Note it is a **persistence / gate-evasion** primitive (needs existing code-exec or a write foothold), not initial access — "Critical" is defensible only in combination with the SSRF/injection chains, but the operator should understand the precondition.

### C-3 — FAA SWIM TLS certificate validation disabled in the running image
**Verdict: CONFIRMED-BY-INDEPENDENT-RECHECK (most severe standing issue).**

- `src/ingest/swim_client.py:436`: `tls = TLS.create().without_certificate_validation()` — the sole TLS builder; **no** `with_certificate_validation`/trust-store/CA-pin alternative exists anywhere under `src/ingest/`.
- Present in the **running** `localhost/corporatetraveldc-ingest:latest` image (grep inside the image = 1).
- All six FAA feeds connect to `tcps://ems1/ems2.swim.faa.gov:55443` with basic auth over a TLS session whose peer certificate/hostname is never verified → the six real `SWIM_NMS_USER_*`/`PASS_*` credential sets are MITM-harvestable by an on-path attacker, and every downstream XML parser's input becomes attacker-controllable. (Transport is still encrypted, just unauthenticated — MITM-exposed, not cleartext; the report says this correctly.) Fix is CA-pinning, not disabling. Genuinely the highest-value standing fix.

### C-2 — `_is_trusted()` spoofable with a client `X-Forwarded-For` / `CF-Connecting-IP`
**Verdict: CONFIRMED-BY-INDEPENDENT-RECHECK, and consistent with the prior deferred-finding understanding (tailnet/direct-scoped, not a public bypass).**

Live on :8001 (`GET /api/whoami`):

```
(no header)                  -> {"tailnet":true}
X-Forwarded-For: 10.x.x.x    -> {"tailnet":true}
X-Forwarded-For: 8.8.8.8     -> {"tailnet":false}
CF-Connecting-IP: 8.8.8.8    -> {"tailnet":false}
CF-Connecting-IP: 100.64.1.1 -> {"tailnet":true}
```

Mechanism confirmed: uvicorn runs `--forwarded-allow-ips=*`, so it rewrites `request.client.host` from XFF; and `_is_trusted` (`:231`) early-returns on `CF-Connecting-IP` with nothing proving the request actually traversed Cloudflare. This matches the existing understanding exactly: exploitable only where `CF-Connecting-IP` is client-controlled, i.e. **direct :8001/tailnet reachability**, not through the real public Cloudflare path (CF overwrites `CF-Connecting-IP` at its edge, and the public path lands on the isolated :8005/:8004 anyway). Nothing new or broader than the deferred finding. "Critical" is high given the reachability scoping; real bug, worth fixing (resolve real IP at nginx, trust only `request.client.host`).

### C-5/C-6 — pusher/alerting fail-open cluster
**C-6 (ntfy 401/403 reported as success): CONFIRMED-BY-INDEPENDENT-RECHECK.**
`src/common/ntfy_push.py:144-163` returns `True` on HTTP 401/403 with no resend. The live dedup file `/var/lib/corporatetraveldc/pusher-ntfy-ambiguous-status-dedup.json` holds **1,274** entries (report said 1,273; it grew by one — actively written, mtime today). A revoked/wrong `NTFY_TOKEN` is indistinguishable from delivery on an alerting platform. Real.

---

## Additional findings spot-checked (15), across severity levels

| # | Claim | Verdict | Note |
|---|-------|---------|------|
| C-0c | `pre-push` checks remote name not URL | **CONFIRMED** | `.git/hooks/pre-push` uses `$1` only; `grep '$2'` = 0. `git push <url>` skips the guard. |
| C-4 | Manifest verify does not pin key fingerprint | **CONFIRMED** | `verify-manifest.sh` imports+trusts tracked `security/trusted-signing-key.pub.asc`; 0 refs to FINGERPRINT/VALIDSIG/status-fd/signing.env. Attacker who can write tracked files swaps the key and re-signs. |
| C-5 | Anonymous board read of research notes | **CONFIRMED** | `GET /api/v1/board` with `X-CTDI-Public: 1` → 200 with real message bodies (redaction masks vault host/credential, serves substance). |
| C-9 | `require_admin` never audits *failed* authz; stores unredacted bodies | **CONFIRMED** | `src/auth/auth.py:170-202`: 403 raised before `db.audit()`; POST/PUT/PATCH/DELETE body stored verbatim as `detail`. |
| C-11 | Wildcard CORS on tunnel-exposed API | **CONFIRMED** | `main.py:79-80` `allow_origins=["*"]`; live preflight from `evil.example` → `access-control-allow-origin: *`, methods incl. DELETE. |
| C-12 | `/openapi.json` served despite `docs_url=None` | **CONFIRMED** | Live 200, 76,885 bytes (exact), 21 `/admin/` path refs. |
| C-17 | Anon `/api/v1/aircraft` blocking full-scan | **CONFIRMED** | Anon reachable (404, no auth); `db.py:2901/3417/3436` wrap indexed cols in `LOWER()`/`UPPER(REPLACE())` → non-indexed scan on the single-worker loop. |
| C-13 | Unauthenticated SSRF `GET /api/rss/custom` | **CONFIRMED (source)** | `runner/main.py:2358` `rss_custom(url,...)`, no auth dep, `follow_redirects=True`, no host/IP denylist. |
| C-19 | `fdps_parser.py:950` `gufi_override` nonexistent key | **CONFIRMED (latent)** | `entry.get("gufi_override","")` — key set nowhere; when an inbound msg has empty GUFI, `gufi=="" ` matches every entry. Latent because real fixtures populate GUFI. |
| C-22 | DEMO_MODE inert on live public demo | **CONFIRMED** | `/api/demo/status` → `{"demo_mode":false,...}`. |
| C-28 | 20 tokens, all `expires_at IS NULL`, 5 active | **CONFIRMED** | `auth_tokens`: total 20, null-expiry 20, active 5. |
| C-30 | Scrubber blind to OOXML; real name/domain in public decks | **CONFIRMED (with intended-public nuance)** | 17 `.docx`/`.pptx` in `public/main`; `.docx`/`.pptx` NOT in `BINARY_SKIP_EXTENSIONS` → raw-blob byte path; decompress-scan of a public deck found "the operator"/"Sheldon"/"csexecutiveservices"/"CSExecutive" in the deflated XML that the byte-level `FORBIDDEN_LITERALS` cannot see. For investor decks this is plausibly intended; the accurate point is the *control enforces nothing* on this file class. |
| C-31 | `faa_upsert_ladd()` wipes LADD on empty parse | **CONFIRMED** | `db.py:2852` unconditional `DELETE` then insert; no `if not n_numbers: return` guard (siblings `expire_tfrs`/`expire_nws_alerts` have it). Caller `_parse_ladd` returns `[]` on missing file (warning only). |
| C-32 | `board_consume_nonce()` read-then-write race | **CONFIRMED (source)** | `db.py:462-488` SELECT → Python check → INSERT+UPDATE, non-atomic; two racers mint two tokens from one single-use nonce. |
| C-8 | Cloud-egress kill switch defaults open | **CONFIRMED (latent footgun)** | `llm.py:383` defaults `"true"`, read once from raw process env (no `common.config`). On the live box it is **doubly closed**: `dispatch.env:200` sets `ANTHROPIC_FALLBACK_ENABLED=false` AND `ANTHROPIC_API_KEY` is not set to any value (the grep matches are all comment lines). Real structural default-open footgun; not live-open today. |

All 15 held up. Severity nuances noted for C-8 (latent, not live-open), C-30 (intended-public for these files), C-19 (latent).

---

## New / sharper findings from this pass

1. **C-1 severity correction (the important one).** The passwordless-sudo endpoint is **not** reachable from the public internet through this bypass. Public → nginx → runner-demo :8005 → **isolated demo-api :8004** (which has no `/admin/*`). The sudo endpoint is reachable via the bypass only on **:8001** (tailnet + loopback). The report's own C-22 acknowledges the demo backend is isolated, which contradicts C-1's "public hostname → passwordless sudo" framing. Reprioritize accordingly — still fix it, but it is tailnet-scoped defense-in-depth, not the #1 internet-exploitable critical.
2. **Test-methodology caveat worth recording:** reproducing C-1 requires `curl --path-as-is`. Plain `curl`/most HTTP clients collapse `/./` before sending, which yields a false negative. A future re-tester who omits it will wrongly conclude the bypass is fixed.
3. **C-2/C-1 share the same reachability ceiling:** both depend on reaching :8001 directly (tailnet/loopback). Neither is a public-internet primitive; the CF public path lands on the isolated demo stack and CF sets `CF-Connecting-IP` authoritatively.

---

## Compact status

- **C-1 (dot-segment bypass → sudo):** PARTIALLY-CONFIRMED — bypass is real and live on :8001, but public-internet reach to the sudo endpoint does not exist (public → isolated demo-api :8004); severity overstated, still worth fixing as tailnet-scoped defense-in-depth.
- **C-0a (.pyc bypass):** CONFIRMED — /app writable, root, no PYTHONDONTWRITEBYTECODE, 184 live .pyc, manifest covers only .py; a persistence primitive requiring a prior write foothold.
- **C-3 (SWIM TLS disabled):** CONFIRMED — `without_certificate_validation()` in the running image, no CA-pin anywhere; six real FAA cred sets MITM-exposed. Highest-value standing fix.
- **C-2 (_is_trusted XFF/CF-IP spoof):** CONFIRMED — reproduced live; consistent with the known tailnet/direct-scoped deferred finding, nothing broader.
- **C-6 (ntfy 401/403 = success):** CONFIRMED — 1,274 real dedup entries, actively growing.

**Remaining findings checked:** 15 of ~31, spanning Critical→Low. **All 15 held up**, with severity nuances on C-8 (latent, not live-open), C-30 (intended-public for the investor files), and C-19 (latent). **Zero flat-wrong / COULD-NOT-REPRODUCE** results.

**Overall verdict:** The test agent's report is accurate, well-disciplined, and reproduces cleanly — its individual technical claims are trustworthy and it did not fabricate. The **one correction that matters for the operator** is C-1's blast radius: it is not an unauthenticated-internet-to-root chain (the public demo backend is isolated), so it should not sit at #1. On the actual fix order, the standing issues that are both real and reachable by their stated threat model are, in priority: **C-3 (SWIM TLS)** first — the only one exposing real production credentials on the wire; then the **fail-open cluster C-6/C-7/C-8/C-9** (silent failures on an alerting platform); then **C-4** (unpinned manifest trust root) and **C-0a/C-0c** (integrity-gate evasion); then **C-31/C-14** (silent data-wipe bugs) and **C-5/C-30** (information exposure, some intended). C-1 and C-2 remain worth fixing but are tailnet-scoped defense-in-depth, not the top of the list.
