# Auth Token Proxy Pattern

**Version:** 1.0  **Date:** 2026-06-13  
**Applies to:** dispatch-runner (port 8001) → dispatch-web (port 8000)

---

## Problem

The dispatch web API exposes data at multiple trust tiers:

| Tier | Who can call it | Example endpoints |
|------|----------------|-------------------|
| Tier 0 (anonymous) | Anyone on Tailscale | `/api/v1/tfr`, `/api/v1/weather` |
| Tier 1 (cert) | Bearer token with `tier=cert` | `/api/v1/tfr-enriched`, `/api/v1/radio` |
| Tier 2 (shares) | Bearer token with `tier=shares` | CUI-adjacent data |
| Admin | Bearer token with `tier=admin` | `/admin/*` management endpoints |

The **dispatch-runner** frontend is a React SPA. Browser JavaScript cannot hold
secrets safely — any token embedded in the bundle or stored in localStorage is
readable by anyone with DevTools. Handing the browser a cert-tier token would
effectively make all Tier-1 data public to anyone who can reach the runner URL.

At the same time, the runner itself is Tailscale-gated and CF-Access-protected.
It is a trusted internal service. It *can* hold a secret.

---

## Solution: server-side token injection

The runner holds a long-lived cert-tier service token in `dispatch-secrets.env`.
When the browser fetches a Tier-1 endpoint through the runner's transparent
proxy, the runner injects the token into the upstream request before it reaches
the web API. The browser never sees the token.

```
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

If the browser does supply its own `Authorization` header (e.g. an admin
session), it takes priority — the runner never overwrites a client-supplied
token.

---

## Implementation

### 1. Create the service token

Run inside the `systemd-corporatetraveldc-web` container (where the DB lives):

```bash
podman exec systemd-corporatetraveldc-web \
  python3 /app/ctdc_token/cli.py create \
    --user runner \
    --tier cert \
    --label enriched-proxy-runner
```

Output:
```
Token (shown once — store it now):
  ctdc_runner_<32-char-random>
```

### 2. Store it in secrets

```bash
echo "RUNNER_ENRICHED_TOKEN=ctdc_runner_<token>" \
  >> /etc/corporatetraveldc/dispatch-secrets.env
```

The secrets file is mode 0600, owned by `corporatetraveldc`, and is gitignored
by `push-public.sh` before every mirror push.

### 3. Runner env var

`src/runner/main.py` reads:

```python
RUNNER_ENRICHED_TOKEN = os.getenv("RUNNER_ENRICHED_TOKEN", "")
```

The runner container inherits `dispatch-secrets.env` via the Quadlet
`EnvironmentFile=` directive.

### 4. Path allowlist

Only specific paths get the injected token. Adding a new Tier-1 endpoint to the
proxy requires a one-line addition here:

```python
# src/runner/main.py
_TIER1_PATHS: frozenset[str] = frozenset({
    "api/v1/tfr-enriched",
    "api/v1/radio",
    "api/v1/cui/status",
})
```

### 5. Proxy injection logic

```python
@app.api_route("/api/dispatch/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_dispatch(path: str, request: Request):
    headers = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth          # client token takes priority
    elif RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS:
        headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
    ...
```

### 6. Frontend call (no token needed)

```javascript
// TfrView.jsx — browser sends no Authorization header
const r = await fetch('/api/dispatch/api/v1/tfr-enriched')
```

The runner transparently upgrades the request.

---

## Security properties

| Property | How it's achieved |
|----------|------------------|
| Token never in browser | Injected by runner, not passed through |
| Token never in git | `push-public.sh` gitignores `dispatch-secrets.env` |
| Client token always wins | `if auth: ... elif RUNNER_ENRICHED_TOKEN` ordering |
| Blast radius if runner is compromised | cert tier only — cannot reach admin or shares endpoints |
| Token rotation | `ctdc-token revoke --prefix ctdc_runner_` then recreate and update secrets.env |

---

## Token tiers and their scope

| Token tier | DB value | Dispatch Tier | Accessible |
|------------|----------|---------------|------------|
| `cert`     | `cert`   | T1            | Tier 0 + Tier 1 endpoints |
| `shares`   | `shares` | T2            | Tier 0 + T1 + T2 endpoints |
| `admin`    | `admin`  | ADMIN         | Everything including `/admin/*` |

The runner service token uses `cert` — the minimum tier needed for enriched TFR
data. It cannot reach shares-gated (CUI) or admin endpoints.

---

## Rotating the token

```bash
# 1. Revoke old token
podman exec systemd-corporatetraveldc-web \
  python3 /app/ctdc_token/cli.py revoke \
    --prefix ctdc_runner_

# 2. Create new token
podman exec systemd-corporatetraveldc-web \
  python3 /app/ctdc_token/cli.py create \
    --user runner --tier cert --label enriched-proxy-runner

# 3. Update secrets file
#    Replace the old RUNNER_ENRICHED_TOKEN= line in dispatch-secrets.env

# 4. Restart runner to pick up new env
systemctl --user restart corporatetraveldc-runner.service
```

---

## Extending to other Tier-1 endpoints

To add a new Tier-1 endpoint to the runner proxy:

1. Confirm the endpoint requires exactly Tier 1 (not admin or shares):
   ```bash
   curl http://127.0.0.1:8000/api/v1/<endpoint>
   # Should return: {"detail":"This endpoint requires tier tier1"}
   ```

2. Add the path to `_TIER1_PATHS` in `src/runner/main.py`:
   ```python
   _TIER1_PATHS: frozenset[str] = frozenset({
       "api/v1/tfr-enriched",
       "api/v1/radio",
       "api/v1/cui/status",
       "api/v1/your-new-endpoint",   # ← add here
   })
   ```

3. The frontend calls it at `/api/dispatch/api/v1/your-new-endpoint` with no token.

No token rotation or secret changes required — the existing cert-tier token
covers all Tier-1 paths.

---

## Why not just make those endpoints Tier 0?

Tier-1 endpoints return data that:

- References credentialed radio frequencies (SHARES, HEARS, HEART) — CUI rules
  prohibit exposing these to unauthenticated callers.
- Contains enriched narrative text generated from raw NOTAM/TFR data that may
  include restricted airspace identifiers.
- Powers the radio codeplug export path — exporting frequencies requires proof
  of authorization.

Tier 0 data (weather, basic TFR list, CPS score) is safe to serve
unauthenticated because it contains no credentialed or frequency-specific data.

---

*See also:* [SECURITY.md](../SECURITY.md) — overall auth model  
*See also:* [docs/dispatch-runner-design.md](dispatch-runner-design.md) — runner architecture
