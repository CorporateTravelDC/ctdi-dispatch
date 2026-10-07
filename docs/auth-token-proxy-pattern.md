# Auth Token Proxy Pattern

Verified against HEAD db64018 and live state on 2026-10-06 18:15Z / 14:15 ET.
**Applies to:** the dispatch runner (`src/runner/main.py`, container port
8001) proxying to dispatch-web (`src/web/main.py`, port 8000).

---

## Problem

Dispatch-web resolves tiers from bearer tokens only (`src/auth/auth.py::resolve_tier`).
Network origin grants no tier, and any request carrying `X-CTDI-Public: 1`
(stamped by the public `dispatch.example.com` vhost) is pinned to
Tier 0.

| Tier | Token `tier` | Example routes |
|------|------|-------------------|
| Tier 0 | none | `/api/v1/tfr`, `/api/v1/weather` |
| Tier 1 | `cert` | `/api/v1/tfr-enriched`, `/api/v1/radio`, `GET /api/v1/watchlist`, `/api/v1/knowledge-graph/*`, `/api/v1/vault/file` |
| Tier 2 | `shares` | `/api/v1/cui/status` (audited as `cui_status_read`) |
| Admin | `admin` (optionally narrowed by `allowed_actions`) | `/admin/*`, watchlist and sector mutations, `/api/v1/remember` -- all through `require_admin(action)` |

The runner's frontend is a React SPA; a token in the browser bundle or
localStorage is readable by anyone with DevTools. The runner process can hold
a secret, so it injects one server-side.

## Who reaches which runner

| Instance | Container / port | Front door | Upstream (`DISPATCH_BASE_URL`) |
|---|---|---|---|
| Production runner | `corporatetraveldc-runner`, published on `127.0.0.1:8001` and `100.x.x.x:8001` | tailnet only: `nginx/conf.d/tailscale-dispatch-runner.conf` (443 closed on the LAN) | dispatch-web `:8000` |
| Public demo runner | `corporatetraveldc-runner-demo`, `127.0.0.1:8005` / `100.x.x.x:8005` | public `dispatch-runner.example.com` (live-only nginx vhost -> `127.0.0.1:8005`), `DEMO_MODE=true` session gate | **`demo-api` at `http://100.x.x.x:8004`**, not production web |

So the public hostname never proxies to production dispatch-web, with or
without a token. The production runner is reachable only from the tailnet,
where `_is_trusted()` is true for every caller. The two allowlists below
therefore differ in practice only if the production runner is ever exposed
off-tailnet -- which is exactly the case they exist to make safe.

`_is_trusted()` is an IP classifier (CF-Connecting-IP if present, else the
client / X-Forwarded-For) against Tailscale CGNAT `100.64.0.0/10`, RFC1918 and
loopback. It is an injection decision, not an access gate; reachability is
the network layer's job.

---

## Solution: server-side token injection

The production runner holds a cert-tier service token, `RUNNER_ENRICHED_TOKEN`.
Its value lives in the master `/etc/corporatetraveldc/dispatch-secrets.env`
and reaches the container only through the generated scoped file
`/etc/corporatetraveldc/svc/runner.env` (the name is in
`scripts/service-env/runner.allowlist`; the quadlet reads `dispatch.env` and
`svc/runner.env`, not the master file).

```
Browser --GET /api/dispatch/api/v1/tfr-enriched (no Authorization)--> runner :8001
runner  --GET /api/v1/tfr-enriched, Authorization: Bearer <runner token>--> web :8000
```

A client-supplied `Authorization` header always wins; the runner never
overwrites it.

### The injection logic

`_dispatch_proxy_headers(request, path)` (called by `proxy_dispatch()`, route
`/api/dispatch/{path:path}`, methods GET/POST/DELETE):

```python
auth = request.headers.get("Authorization")
if auth:
    headers["Authorization"] = auth                      # client token wins
elif RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS:
    headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
elif (RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS_TRUSTED_ORIGIN_ONLY
      and _is_trusted(request)):
    headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
```

`X-CTDI-Public` is not forwarded upstream. Unit tests:
`tests/runner/test_proxy_dispatch.py` (10).

Two gates run before the proxy: the `tailscale_gate` middleware returns 404
for `/admin*` paths and for any non-GET `/api/v1/*` from an untrusted origin;
with `DEMO_MODE=true` an untrusted request without a valid demo session
cookie is rejected.

### The two allowlists

**(a) `_TIER1_PATHS` -- unconditional.**

```python
"api/v1/tfr-enriched", "api/v1/radio", "api/v1/cui/status",
"api/v1/watchlist", "api/v1/watchlist/history",
```

**`api/v1/cui/status` is a no-op here:** dispatch-web requires Tier 2
(`require_tier(Tier.T2)`) for it and the runner token is cert/Tier 1, so an
injected request still gets 403. It reads as if CUI status were exposed to
the runner's callers; it is not.

**(b) `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY` -- only when `_is_trusted(request)`.**

```python
"api/v1/knowledge-graph/html", "api/v1/knowledge-graph/meta",
"api/v1/vault/file", "api/v1/osint/scopes",   # GET listing only
```

Added 2026-08-13 after a pentest found these readable without auth. The
`osint/scopes` mutations are `require_admin`, so injecting a cert token there
would still 403; they are deliberately not listed.

| | `_TIER1_PATHS` | `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY` |
|---|---|---|
| Trusted origin (tailnet/LAN/loopback) | injected | injected |
| Untrusted origin | injected | not injected -> 403 at dispatch-web |
| Intended for | data acceptable to any runner caller | operator-only data |

---

## Security properties

| Property | How |
|----------|------------------|
| Token never in the browser | injected by the runner |
| Token never in git or the public mirror | value only in `/etc/corporatetraveldc/` (outside the repo); `push-public.sh` scrubs and the pre-commit hook blocks credential patterns |
| Token not in other containers | only `runner.allowlist` names it among the generated service files |
| Client token wins | `if auth:` is evaluated first |
| Blast radius of a compromised runner | the cert-tier token: Tier 0 + Tier 1 reads; no Tier 2, no admin |

---

## Creating and rotating the token

`ctdc-token` is `src/ctdc_token/cli.py` (subcommands `create`, `list`,
`revoke`, `show-cost`). `create` now defaults to `--expires 365` days; pass
`--expires 0` only if a non-expiring token is a deliberate choice, and diary
the expiry date if not.

```bash
# 1. revoke the old token. --prefix matches token_prefix, which is "ctdc_runner_"
#    for EVERY runner token, so revoking after minting would kill the new one too.
#    The runner's Tier-1 views 403 until step 4.
podman exec systemd-corporatetraveldc-web python3 /app/src/ctdc_token/cli.py revoke --prefix ctdc_runner_
# 2. mint (inside the web container, which has the DB)
podman exec systemd-corporatetraveldc-web \
  python3 /app/src/ctdc_token/cli.py create --user runner --tier cert --label runner-enriched-proxy
# 3. put the value in /etc/corporatetraveldc/dispatch-secrets.env (in-place edit; the
#    directory is root-owned, so no temp-file-and-rename), never on a command line
# 4. regenerate the scoped files (root only) and restart the runner
sudo python3 scripts/service-env/generate.py --write runner
systemctl --user restart corporatetraveldc-runner.service
```

[UNVERIFIED: the live `auth_tokens` row for `runner-enriched-proxy` and its
expiry; the table was not queried.] `scripts/rotate-credential.sh` does not
cover this token (it handles `BOARD_KEY`, `DISPATCH_ADMIN_TOKEN`, `NTFY_TOKEN`,
`NEXTCLOUD_APP_PASSWORD`).

---

## Adding a Tier-1 endpoint to the proxy

1. Confirm the route is exactly Tier 1 (`require_tier(Tier.T1)` in
   `src/web/main.py`); `curl http://127.0.0.1:8000/api/v1/<endpoint>` without
   a token should answer `This endpoint requires tier tier1`. If it needs
   Tier 2 or admin, stop: the runner token cannot reach it.
2. Choose the list. Would you show this to any caller of the runner,
   including one arriving off-tailnet if the runner were ever exposed? If
   not, use `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`. When in doubt, use it: the
   failure mode is your own browser seeing a 403, not strangers seeing data.
3. Test from an untrusted origin (or with `CF-Connecting-IP` set to a public
   address): operator-only paths must 403.
4. The frontend calls `/api/dispatch/api/v1/<endpoint>` with no token.

## Why not make these Tier 0?

Tier 1 data includes credentialed-radio references (SHARES/HEARS/HEART are
CUI and must not reach unauthenticated callers), enriched NOTAM/TFR narrative,
the codeplug export path, and vault/knowledge-graph content.

*See also:* `SECURITY.md`, `docs/dispatch-runner-design.md`,
`docs/COMPLIANCE_SECURITY.md` §6.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-security.md`.


### Auth Token Proxy Pattern

~~**Version:** 1.3  **Reconciled:** 2026-08-23 against live `src/auth/auth.py`, `src/runner/main.py`, the `auth_tokens` table, and the Cloudflare Access app list (v1.2 was 2026-08-19) **Applies to:** dispatch-runner (port 8001) → dispatch-web (port 8000)~~

> ~~**Correction to the v1.1 stamp.** v1.1 was dated 2026-08-11 and claimed to be "verified against `src/runner/main.py`", but its `_TIER1_PATHS` listing showed only 3 entries when the code already had 5 — `api/v1/watchlist` was added **2026-07-21**, three weeks *before* that verification date. The stamp was therefore never accurate. More seriously, v1.1 omitted the second, conditionally-injected path set entirely (`_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`, added 2026-08-13), which meant its "Extending to other Tier-1 endpoints" guidance pointed implementers at the *unconditional* set for every case — the wrong default for an operator-only endpoint. Both are fixed below.~~


### Auth Token Proxy Pattern › Problem

~~The dispatch web API exposes data at multiple trust tiers. Tier resolution (`resolve_tier()` in `src/auth/auth.py`) is **purely bearer-token based** — network origin grants no tier (the old Tailscale-header/IP grant was removed as spoofable), and any request carrying `X-CTDI-Public: 1` (stamped by the public nginx vhosts) is pinned to Tier 0 regardless of token:~~

| ~~Tier~~ | ~~Who can call it~~ | ~~Example endpoints~~ |
|------|----------------|-------------------|
| ~~Tier 0 (anonymous)~~ | ~~Anyone who can reach the API~~ | ~~`/api/v1/tfr`, `/api/v1/weather`~~ |
| ~~Tier 1 (cert)~~ | ~~Bearer token with `tier=cert`~~ | ~~`/api/v1/tfr-enriched`, `/api/v1/radio`~~ |
| ~~Tier 2 (shares)~~ | ~~Bearer token with `tier=shares`~~ | ~~`/api/v1/cui/status` (audit-logged)~~ |
| ~~Admin~~ | ~~Bearer token with `tier=admin`~~ | ~~`/admin/*`, watchlist mutations, `/api/v1/remember`~~ |

~~The **dispatch-runner** frontend is a React SPA. Browser JavaScript cannot hold secrets safely — any token embedded in the bundle or stored in localStorage is readable by anyone with DevTools. Handing the browser a cert-tier token would effectively make all Tier-1 data public to anyone who can reach the runner URL.~~

~~At the same time, the runner's admin instance (`:8001`) is reachable only over the tailnet-only `tailscale-dispatch-runner.conf` vhost, where tag-scoped Tailscale ACLs gate reachability before a packet arrives. The public `ops.example.com` hostname was retired 2026-08-02 and is hard-404'd. The deliberate exception is the demo instance's `:8005` path in nginx (`dispatch-runner.example.com`), an accepted public exposure per `docs/dispatch-runner-design.md`. The runner is a trusted internal service. It *can* hold a secret.~~

> ~~**Precision note, 2026-08-23 — don't read `_is_trusted()` as a reachability control.** An earlier revision of this paragraph attributed the runner's restricted reach *to* `_is_trusted()`. It does not do that job. `_is_trusted()` (`src/runner/main.py`) is a pure IP classifier — `CF-Connecting-IP` when present, else `request.client.host`/`X-Forwarded-For` against `_TRUSTED_NETS` (Tailscale CGNAT 100.64.0.0/10 + RFC1918 + loopback) — evaluated *after* a request has already arrived. What keeps `:8001` unreachable is the network layer (tailnet-only bind + Tailscale ACL); what `_is_trusted()` actually decides in this document is the §4(b) conditional-injection branch, nothing more. This distinction matters because `dispatch-runner.example.com` has **no** Cloudflare Access policy fronting it (re-confirmed live 2026-08-23 by listing the account's Access applications — none matches that hostname), so on the public path `_is_trusted()` returning false is the *only* thing withholding the token, and it is doing so as an injection decision, not as an access gate.~~


### Auth Token Proxy Pattern › Solution: server-side token injection

~~The runner holds a long-lived cert-tier service token in `dispatch-secrets.env`. When the browser fetches a Tier-1 endpoint through the runner's transparent proxy, the runner injects the token into the upstream request before it reaches the web API. The browser never sees the token.~~

*Superseded block:*
```text superseded
Browser                  Runner (port 8001)             Web API (port 8000)
  │                            │                               │
  │  GET /api/dispatch/        │                               │
  │  api/v1/tfr-enriched       │                               │
  │  (no Authorization)        │                               │
  │ ─────────────────────────► │                               │
  │                            │  GET /api/v1/tfr-enriched     │
  │                            │  Authorization: Bearer        │
  │                            │  ctdc_runner_<secret>         │
  │                            │ ─────────────────────────────►│
  │                            │  200 OK + enriched payload    │
  │                            │ ◄─────────────────────────────│
  │  200 OK + enriched payload │                               │
  │ ◄───────────────────────── │                               │
```

~~If the browser does supply its own `Authorization` header (e.g. an admin session), it takes priority — the runner never overwrites a client-supplied token.~~

~~Injection is allowlisted by path, and there are **two allowlists** — one unconditional, one gated on a trusted request origin. See [§4](#4-path-allowlists--there-are-two-with-different-security-properties) before adding anything to either.~~

**~~Implementation~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation

**~~1. Create the service token~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 1. Create the service token

~~Run inside the `systemd-corporatetraveldc-web` container (where the DB lives):~~

*Superseded block:*
```text superseded
podman exec systemd-corporatetraveldc-web \
  python3 /app/src/ctdc_token/cli.py create \
    --user runner \
    --tier cert \
    --label runner-enriched-proxy
```

> ~~**Label corrected 2026-08-23.** Both code blocks in this document said `enriched-proxy-runner`; the token actually in the live `auth_tokens` table carries `device_label = runner-enriched-proxy` (verified: `sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT token_prefix,tier,device_label FROM auth_tokens WHERE token_prefix LIKE 'ctdc_runner_%';"`). Following the old text on a rotation would have minted a differently-labeled token and made the two hard to correlate.~~

~~Output:~~

*Superseded block:*
```text superseded
Token (shown once — store it now):
  ctdc_runner_<32-char-random>
```

**~~2. Store it in secrets~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 2. Store it in secrets

*Superseded block:*
```text superseded
echo "RUNNER_ENRICHED_TOKEN=ctdc_runner_<token>" \
  >> /etc/corporatetraveldc/dispatch-secrets.env
```

~~The secrets file is mode 0600, owned by `corporatetraveldc`, and is gitignored by `push-public.sh` before every mirror push.~~

**~~3. Runner env var~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 3. Runner env var

~~`src/runner/main.py` reads:~~

*Superseded block:*
```text superseded
RUNNER_ENRICHED_TOKEN = os.getenv("RUNNER_ENRICHED_TOKEN", "")
```

~~The runner container inherits `dispatch-secrets.env` via the Quadlet `EnvironmentFile=` directive.~~

**~~4. Path allowlists — there are **two**, with different security properties~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 4. Path allowlists — there are **two**, with different security properties

~~Only allowlisted paths get the injected token, and **which list a path is on decides who can reach it.** This is the most important distinction in this document; getting it wrong widens an operator-only endpoint to the public Ops view.~~

~~**(a) `_TIER1_PATHS` — unconditional injection.** Any caller reaching the proxy gets the service token attached, including a request arriving from the public `dispatch-runner.example.com` hostname. Use this only for data that is *intentionally* visible to the public Ops view.~~

> ~~**Status note (2026-08-23; superseded 2026-09-03):** `dispatch-runner.example.com` was restored 2026-08-24 and serves 200, and the `runner-demo` Quadlet now sets `DEMO_MODE=true` + `DEMO_SESSION_SECRET` — so on the public path the demo session gate runs *in addition to* the injection rules described here. The policy guidance in this document stands unchanged.~~

*Superseded block:*
```text superseded
# src/runner/main.py:1494 as of 2026-08-23 (line numbers drift — search the symbol)
_TIER1_PATHS: frozenset[str] = frozenset({
    "api/v1/tfr-enriched",
    "api/v1/radio",
    "api/v1/cui/status",
    "api/v1/watchlist",          # added 2026-07-21
    "api/v1/watchlist/history",  # added 2026-07-21
})
```

~~The two watchlist paths were added 2026-07-21 per operator direction: Ops should see the **real** watchlist read-only. That is safe only because the runner's own `tailscale_gate` middleware already rejects every non-GET `/api/v1/*` request from a non-trusted origin *before* the proxy runs — so injecting here widens the READ and nothing else. (An older comment block further down in `main.py` still says watchlist is "deliberately excluded" from `_TIER1_PATHS`; that comment predates the 2026-07-21 change and is stale — the frozenset above is what executes.)~~

~~**(b) `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY` — injection conditional on `_is_trusted(request)`.** Same token, same tier, but the runner attaches it **only** when the request arrives from a trusted origin (Tailscale CGNAT / RFC1918 / loopback). An untrusted caller gets no token and then correctly 403s at dispatch-web. Use this for anything that must stay operator-only.~~

*Superseded block:*
```text superseded
# src/runner/main.py:1527 as of 2026-08-23 (line numbers drift — search the symbol)
_TIER1_PATHS_TRUSTED_ORIGIN_ONLY: frozenset[str] = frozenset({
    "api/v1/knowledge-graph/html",
    "api/v1/knowledge-graph/meta",
    "api/v1/vault/file",
    "api/v1/osint/scopes",   # GET config listing only
})
```

~~This set was added 2026-08-13 after a live pentest pass found these vault / knowledge-graph endpoints with **no auth at all** on dispatch-web. They were tier-gated there (`require_tier(Tier.T1)`), but because T1 is purely token-based and the PWA's own `fetch()` calls carry no token, an unconditional fix would have 403'd the feature for its legitimate Tailscale users too. Conditional injection is what lets the operator keep the feature while the public hostname stays locked out.~~

~~Note the deliberate exclusion inside that set: the `osint/scopes` **mutation** routes (POST/PATCH/DELETE) are not listed, because they are `require_admin`, not `require_tier` — and `RUNNER_ENRICHED_TOKEN` is cert/T1. Injecting it there would be a no-op that still 403s while implying false reachability. Admin actions need an explicitly supplied admin token.~~

|  | ~~`_TIER1_PATHS`~~ | ~~`_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`~~ |
|---|---|---|
| ~~Token injected for trusted origin~~ | ~~yes~~ | ~~yes~~ |
| ~~Token injected for public Ops hostname~~ | ~~**yes**~~ | ~~**no** → 403 at dispatch-web~~ |
| ~~Intended audience~~ | ~~public Ops view, read-only~~ | ~~operator only~~ |
| ~~Reached via~~ | ~~`elif ... path in _TIER1_PATHS`~~ | ~~`elif ... and _is_trusted(request)`~~ |

**~~5. Proxy injection logic~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 5. Proxy injection logic

~~Three branches, evaluated in order. **The logic does not live inside `proxy_dispatch()`** — it was re-extracted into a standalone `_dispatch_proxy_headers(request, path)` helper on 2026-08-20 (it had been inlined at some point, silently breaking `tests/runner/test_proxy_dispatch.py` with `AttributeError` and leaving that code path untested; behavior is unchanged — path corrected 2026-08-23, the file is under `tests/runner/`, there is no `tests/test_proxy_dispatch.py`). `proxy_dispatch()` calls it. Verified against `src/runner/main.py` 2026-08-23 — `_dispatch_proxy_headers` at `:1550`, the three branches at `:1577-1585`, `proxy_dispatch` at `:1590` calling it at `:1618`; line numbers drift, search the symbols:~~

*Superseded block:*
```text superseded
def _dispatch_proxy_headers(request: Request, path: str) -> dict:
    headers = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth          # client token takes priority
    elif RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS:
        headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
    elif (RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS_TRUSTED_ORIGIN_ONLY
          and _is_trusted(request)):
        headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
    ...

@app.api_route("/api/dispatch/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_dispatch(path: str, request: Request):
    ...
    headers = _dispatch_proxy_headers(request, path)
```

~~If you are changing injection behavior, edit `_dispatch_proxy_headers` — that is what the unit tests exercise directly.~~

~~Two further gates run *before* any of this, and are easy to miss when reasoning about reachability: `tailscale_gate` middleware rejects admin paths and non-GET `/api/v1/*` mutations from untrusted origins, and when `DEMO_MODE=true` an untrusted request must present a valid demo session cookie or is rejected outright with 401.~~

**~~6. Frontend call (no token needed)~~** *(former heading)*


### Auth Token Proxy Pattern › Implementation › 6. Frontend call (no token needed)

*Superseded block:*
```text superseded
// TfrView.jsx — browser sends no Authorization header
const r = await fetch('/api/dispatch/api/v1/tfr-enriched')
```

~~The runner transparently upgrades the request.~~


### Auth Token Proxy Pattern › Security properties

| ~~Property~~ | ~~How it's achieved~~ |
|----------|------------------|
| ~~Token never in browser~~ | ~~Injected by runner, not passed through~~ |
| ~~Token never in git~~ | ~~`push-public.sh` gitignores `dispatch-secrets.env`~~ |
| ~~Client token always wins~~ | ~~`if auth: ... elif RUNNER_ENRICHED_TOKEN` ordering~~ |
| ~~Blast radius if runner is compromised~~ | ~~cert tier only — cannot reach admin or shares endpoints~~ |
| ~~Token rotation~~ | ~~`ctdc-token revoke --prefix ctdc_runner_` then recreate and update secrets.env~~ |

**~~Token tiers and their scope~~** *(former heading)*


### Auth Token Proxy Pattern › Token tiers and their scope

| ~~Token tier~~ | ~~DB value~~ | ~~Dispatch Tier~~ | ~~Accessible~~ |
|------------|----------|---------------|------------|
| ~~`cert`~~ | ~~`cert`~~ | ~~T1~~ | ~~Tier 0 + Tier 1 endpoints~~ |
| ~~`shares`~~ | ~~`shares`~~ | ~~T2~~ | ~~Tier 0 + T1 + T2 endpoints~~ |
| ~~`admin`~~ | ~~`admin`~~ | ~~ADMIN~~ | ~~Everything including `/admin/*`~~ |

~~The runner service token uses `cert` — the minimum tier needed for enriched TFR data. It cannot reach shares-gated (CUI) or admin endpoints.~~

**~~Rotating the token~~** *(former heading)*


### Auth Token Proxy Pattern › Rotating the token

*Superseded block:*
```text superseded
# 1. Revoke old token
podman exec systemd-corporatetraveldc-web \
  python3 /app/src/ctdc_token/cli.py revoke \
    --prefix ctdc_runner_

# 2. Create new token
podman exec systemd-corporatetraveldc-web \
  python3 /app/src/ctdc_token/cli.py create \
    --user runner --tier cert --label runner-enriched-proxy
#    Optional but currently unused platform-wide: --expires <DAYS>. Re-verified
#    2026-08-23 -- all 19 rows in auth_tokens have expires_at IS NULL, this
#    one included ("SELECT COUNT(*), SUM(expires_at IS NULL) FROM auth_tokens;"
#    -> 19|19), even though expiry is fully implemented and enforced in
#    db.lookup_token() (src/common/db.py:931, the WHERE clause's
#    "expires_at IS NULL OR expires_at > unixepoch()"; list_tokens() applies
#    the same predicate). See docs/COMPLIANCE_SECURITY.md's "Token expiry"
#    section before deciding whether to set one here.

# 3. Update secrets file
#    Replace the old RUNNER_ENRICHED_TOKEN= line in dispatch-secrets.env

# 4. Restart runner to pick up new env
systemctl --user restart corporatetraveldc-runner.service
```

**~~Extending to other Tier-1 endpoints~~** *(former heading)*


### Auth Token Proxy Pattern › Extending to other Tier-1 endpoints

~~To add a new Tier-1 endpoint to the runner proxy:~~

1. ~~Confirm the endpoint requires exactly Tier 1 (not admin or shares):~~

*Superseded block:*
```text superseded
   curl http://127.0.0.1:8000/api/v1/<endpoint>
   # Should return: {"detail":"This endpoint requires tier tier1"}
```

~~If it returns an admin/shares error instead, **stop** — the cert-tier service token cannot reach it, and adding it to either set below produces a 403 that merely looks like a bug. Admin actions require an explicitly supplied admin token.~~

2. ~~**Choose the right set. This is a security decision, not a formality.** Ask one question: *should a caller on the public `dispatch-runner.example.com` hostname see this data?*~~

   - ~~**No — operator only** (vault content, knowledge graph, internal config, anything you would not paste into a public dashboard): add it to **`_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`** (search the symbol in `main.py` — line numbers drift). Injection is conditional on `_is_trusted(request)`, so a public caller gets no token and 403s at dispatch-web — which is the desired outcome.~~

*Superseded block:*
```text superseded
     _TIER1_PATHS_TRUSTED_ORIGIN_ONLY: frozenset[str] = frozenset({
         "api/v1/knowledge-graph/html",
         "api/v1/knowledge-graph/meta",
         "api/v1/vault/file",
         "api/v1/osint/scopes",
         "api/v1/your-new-endpoint",   # ← operator-only: add HERE
     })
```

   - ~~**Yes — intentionally public, read-only** (the deliberate carve-outs: enriched TFR, radio, CUI status, watchlist reads): add it to **`_TIER1_PATHS`** (search the symbol in `main.py` — line numbers drift).~~

*Superseded block:*
```text superseded
     _TIER1_PATHS: frozenset[str] = frozenset({
         "api/v1/tfr-enriched",
         "api/v1/radio",
         "api/v1/cui/status",
         "api/v1/watchlist",
         "api/v1/watchlist/history",
         "api/v1/your-new-endpoint",   # ← public-Ops-visible: add HERE
     })
```

~~**When in doubt, use `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`.** It is the safe default: the failure mode is "the operator's own browser 403s and you notice immediately", versus "public callers silently gain Tier-1 data and nobody notices". Putting an operator-only path on the unconditional list is exactly the mistake the 2026-08-13 pentest pass was cleaning up.~~

3. ~~Verify the choice, don't assume it — from an untrusted origin (or with `CF-Connecting-IP` set to a public address), confirm the endpoint 403s if it is meant to be operator-only, and 200s if it is meant to be public.~~

4. ~~The frontend calls it at `/api/dispatch/api/v1/your-new-endpoint` with no token, in either case.~~

~~No token rotation or secret changes required — the same cert-tier token backs both sets; only the *condition* under which it is attached differs.~~

**~~Why not just make those endpoints Tier 0?~~** *(former heading)*


### Auth Token Proxy Pattern › Why not just make those endpoints Tier 0?

~~Tier-1 endpoints return data that:~~

- ~~References credentialed radio frequencies (SHARES, HEARS, HEART) — CUI rules prohibit exposing these to unauthenticated callers.~~
- ~~Contains enriched narrative text generated from raw NOTAM/TFR data that may include restricted airspace identifiers.~~
- ~~Powers the radio codeplug export path — exporting frequencies requires proof of authorization.~~

~~Tier 0 data (weather, basic TFR list, CPS score) is safe to serve unauthenticated because it contains no credentialed or frequency-specific data.~~

~~*See also:* [SECURITY.md](../SECURITY.md) — overall auth model *See also:* [docs/dispatch-runner-design.md](dispatch-runner-design.md) — runner architecture~~
