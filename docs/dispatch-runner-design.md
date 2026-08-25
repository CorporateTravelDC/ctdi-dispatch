# dispatch-runner — Design & Reference

**Rewritten 2026-08-11 against `src/runner/main.py` (2484 lines as of
2026-08-23), the live
Quadlets, and nginx/cloudflared config.** Supersedes the v2.0 (2026-06-14)
design doc, which predated the ops-hostname retirement, demo mode, the
AIS/ACARS views, and roughly half the current API surface.

## Overview

dispatch-runner is the operator dashboard for [operator LLC] dispatch
operations: a FastAPI backend (port 8001) serving a React/Vite SPA plus
runner-specific API routes, proxying the dispatch web API.

**Two instances of the same image run simultaneously:**

| Instance | Unit | Port | Exposure |
|---|---|---|---|
| Live ops dashboard | `corporatetraveldc-runner.service` | 8001 (127.0.0.1 + 100.x.x.x) | **Tailnet-only**: `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx 443 vhost → :8001). No public hostname. |
| Public demo (playback) | `corporatetraveldc-runner-demo.service` | 8005→8001 | **Public hostname, LIVE since 2026-08-24 ~14:52**: `https://dispatch-runner.example.com` (CF tunnel → nginx → :8005) serves 200. The 2026-08-15→08-24 crash loop (`sqlite3.OperationalError` from the 08-14 F6 mount removal) was fixed by commit `0a7f643` — a dedicated `/var/lib/corporatetraveldc-demo` host dir is mounted at the container-internal state path, isolated from production (`NRestarts=0`, stable). `DEMO_MODE` is still set **nowhere** (code default `false`), so the demo came up with the DEMO_MODE-dependent gates **inert** — exactly the naive-fix scenario this doc warned about; the operator accepted the public-demo-on-sanitized-data exposure 2026-08-20, but setting `DEMO_MODE` explicitly is still pending (open MEDIUM in `PENTEST_REVERIFICATION_2026-08-24.md`). Reads the demo API (:8004) instead of live feeds |

**Historical (accurate 2026-08-15 → 2026-08-24 morning, superseded by the
fix above):** the crash-loop-era detection guidance — a climbing
`NRestarts`, `curl 127.0.0.1:8005` connection-refused, and not trusting a
single `podman ps` snapshot — remains good general technique for any
`Restart=`-carrying unit, but no longer describes this one.

**Mechanism note (verified 2026-08-20; updated 2026-08-24):**
`_is_trusted()` is purely IP-based — it never checks `X-CTDI-Public`, and
**no Cloudflare Access policy fronts
`dispatch-runner.example.com`** — don't assume a defense there
that doesn't exist. As of the two 2026-08-24 commits (`0a7f643`,
`2bb7fbf`) several individual endpoints are now `_is_trusted`-gated at the
app layer regardless of `DEMO_MODE`: `PUT /api/v1/config` (404 untrusted),
`GET /api/v1/frontend-config` (untrusted callers get a placeholder
coordinate + empty widget key), and `GET`/`DELETE /api/chat/history`
(404 untrusted — previously readable *and destructively clearable* with
no credential). `proxy_dispatch()`'s `DEMO_MODE`+session-cookie check
remains the only *hostname-wide* gate, and it is still inert while
`DEMO_MODE` is unset.

**Retired:** `ops.example.com` (2026-08-02) — hard-404'd by
hostname in `_RETIRED_HOSTNAMES` (`src/runner/main.py`), no nginx vhost, no
tunnel ingress. Do not resurrect; the retirement was part of the XFF-spoofing
fix.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Vite + React SPA (multi-stage build: Node builder → Python runtime, no Node in final image) |
| Maps | Leaflet + OSM dark basemap; airplanes.live iframe for globe mode |
| Realtime | FastAPI SSE (`/api/stream`, 30 s) + ntfy stream proxy |
| Backend | FastAPI/uvicorn, port 8001 |
| Chat | Ollama via `OLLAMA_CHAT_MODEL` (default `corporatetraveldc-pi5-chat:latest`) |
| Container | `Containerfile.runner`; Quadlet in `.config/containers/systemd/` |

## Auth model

1. **Trusted-origin check** (`_is_trusted()`): client IP from
   `CF-Connecting-IP` exclusively when present (never falls through to
   loopback), else socket peer; trusted nets = Tailscale CGNAT
   `100.64.0.0/10`, loopback, RFC1918. Untrusted requests get 404 (not 403)
   on the admin proxy prefix (`/api/dispatch/admin`) and on any non-GET to
   `/api/dispatch/api/v1/*`.
2. **Demo gate** (demo instance only): `DEMO_MODE=true` + untrusted origin ⇒
   requires the `ctdc_demo_session` cookie — an HMAC-SHA256 token keyed with
   `DEMO_SESSION_SECRET`, minted by `POST /api/demo/login` (password proxied
   to the demo backend; cookie `httponly`, `secure`, `samesite=lax`, default
   8 h). The token never reaches JS. Signals are sanitized server-side
   (`DEMO_SANITIZE_SALT`).
3. **Tier-1 token injection**: for a small allowlist (`_TIER1_PATHS` — 5
   paths since 2026-07-21, plus a second set,
   `_TIER1_PATHS_TRUSTED_ORIGIN_ONLY`, 4 paths injected only for
   trusted-origin callers) the proxy injects the runner's `cert`-tier
   service token (`RUNNER_ENRICHED_TOKEN`); a client-supplied
   `Authorization` header always wins. The authoritative path lists
   live in `docs/auth-token-proxy-pattern.md` §4 — cross-reference that
   rather than this doc.

   > ⚠️ **Corrected 2026-08-23 — this bullet used to end "Watchlist reads
   > are deliberately NOT injected — dispatch-web gates them itself." That
   > is the pre-2026-07-21 design and is false against the running code.**
   > `api/v1/watchlist` and `api/v1/watchlist/history` are both **in**
   > `_TIER1_PATHS` (`src/runner/main.py:1508-1509`), i.e. the token is
   > injected **unconditionally**, on purpose: the in-code rationale at
   > `main.py:1498-1507` records the operator direction that the public Ops
   > view should see the REAL watchlist read-only, because "without it every
   > GET from the public hostname 403s at dispatch-web before the runner's
   > own logic matters." Confirmed by reading the frozenset and the
   > injection branch (`_dispatch_proxy_headers`, `elif RUNNER_ENRICHED_TOKEN
   > and path in _TIER1_PATHS`). What is actually gated is **mutation**, not
   > reads — `tailscale_gate`'s `is_v1_mutation` check rejects any non-GET
   > `/api/v1/*` from an untrusted origin before the proxy runs at all.
   >
   > **Live-code finding for the operator, not fixable from a docs pass:**
   > `src/runner/main.py:1511-1513` still carries a stale `NOTE:` comment
   > saying watchlist is "deliberately excluded from `_TIER1_PATHS`" and
   > pointing at `_WATCHLIST_PATHS` / `_is_tailnet_request()` for "the
   > actual gate" — three lines below the frozenset that includes it.
   > Neither `_WATCHLIST_PATHS` nor `_is_tailnet_request` exists anywhere
   > in the file (`grep -n _WATCHLIST_PATHS src/runner/main.py` → only that
   > comment). This doc's old wording was almost certainly copied from that
   > comment. The comment is what should be deleted; the code is correct.

## Backend API routes (verified 2026-08-11 — 31 method+path combos)

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Service health |
| GET | `/api/whoami` | Caller trust/identity info |
| POST | `/api/demo/login` | Demo password → session cookie (404 unless `DEMO_MODE`) |
| GET | `/api/demo/status` | `{demo_mode, authenticated, trusted_origin}` |
| GET | `/api/demo/webhook-log` | Demo webhook activity log |
| GET | `/api/adsb/local` | Proxy → UltraFeeder `aircraft.json` |
| GET | `/api/adsb/live` | Proxy → airplanes.live v2 (250 nm of KDCA) |
| GET | `/api/vdl2/messages` | VDL2 decode feed (acarshub) |
| GET | `/api/acars/messages` | ACARS feed |
| GET | `/api/hfdl/messages` | HFDL feed (hardware pending) |
| GET | `/api/ais/vessels` | AIS vessels (AIS-catcher/AISHub/Kpler sources) |
| POST | `/api/ask` | Ollama chat (dispatch drawer) |
| GET/DELETE | `/api/chat/history` | Chat history |
| GET/POST/DELETE | `/api/dispatch/{path}` | Transparent proxy → dispatch web API |
| GET | `/api/stream` | SSE: CPS + TFR count + feed health (30 s) |
| GET | `/api/ntfy/stream` | ntfy topic stream proxy |
| GET | `/api/v1/frontend-config` · `/api/v1/config` (GET/PUT) | Operator UI config |
| GET | `/api/rss` | Merged catalog + user feeds (`?category=`, `?limit=` ≤500) |
| GET/POST | `/api/rss/categories` | List / create categories |
| GET | `/api/rss/custom` | Server-side fetch of arbitrary feed URL (CORS bypass) |
| POST | `/api/rss/resolve-source` | Resolve a site/channel URL to its feed |
| GET/POST | `/api/rss/user-feeds` | List / add user feeds (validated; 422 empty, 409 dup) |
| DELETE | `/api/rss/user-feeds/{id}` | Remove user feed |
| GET | `/{full_path}` | SPA catch-all (static build) |

## Intel Feed — RSS/Atom

Catalog lives in **`src/shared/rss_catalog.py`** (split out 2026-07-28,
shared with the second-brain RSS poller): **11 built-in categories, 32
feeds** — `corporate_intel`, `marketing_intel`, `travel_trends`, `dc_area`,
`aviation`, `advanced_air_mobility`, `gig_economy`,
`concierge_luxury_travel`, `trains_yachts`, `executive_protection`,
`osint_cybersecurity_video` (the last via the local RSS-Bridge container at
`100.x.x.x:3001`) — plus `__custom__` and user-created categories.

User feeds persist at `/var/lib/corporatetraveldc/user_rss_feeds.json`;
custom categories at `user_rss_categories.json`. Parsing handles RSS 2.0 +
Atom; RFC 2822 dates normalized to ISO 8601; per-feed cap 100 items
pre-merge; default `limit=200`, max 500; `<enclosure audio/video>` items get
`audio_url` for the inline podcast player. 15-min in-memory cache,
process-scoped.

## Frontend views (`src/runner/frontend/src/App.jsx`)

| Route | View |
|---|---|
| `/` | Overview — CPS, weather, TFR, feed-health cards |
| `/map` | ADS-B map (globe iframe / LOCAL UltraFeeder / LIVE airplanes.live) |
| `/trains` | NEC train tracking with watchlist highlighting |
| `/ais` | AIS maritime map |
| `/status` | Feed freshness / error state |
| `/wx` | Meteorology (radar, discussions) |
| `/tfr`, `/signals` | TFRs, NWS alerts, signals |
| `/brief` | Ops brief / weekly / EP tabs |
| `/feed` | Live ntfy notification stream |
| `/intel` | RSS/Atom intelligence feeds |
| `/admin` | Admin panel — rendered only for tailnet-verified clients |

CPS badge is in the global header, not a route. The app declares
`apple-mobile-web-app-capable` (iOS Add-to-Home-Screen works); it does not
ship a manifest.json + service worker, so it is not Chrome/Edge
install-eligible — known gap.

## Key env vars (code defaults; live values from Quadlet/env files)

| Var | Code default | Live (runner) |
|---|---|---|
| `DISPATCH_BASE_URL` | `http://127.0.0.1:8000` | **`http://100.x.x.x:8000`** (demo instance: `http://100.x.x.x:8004`) |
| `ULTRAFEEDER_URL` | `http://127.0.0.1:8080/data/aircraft.json` | `http://100.x.x.x:8080/data/aircraft.json` |
| `NTFY_URL` | `http://host.containers.internal:2586` | (env file) |
| `ACARSHUB_URL` | `http://127.0.0.1:9081` | |
| `AIS_CATCHER_URL` | `http://127.0.0.1:8110` | (hardware pending) |
| `OLLAMA_CHAT_MODEL` / `OLLAMA_OSINT_MODEL` | `corporatetraveldc-pi5-chat:latest` / `-osint:latest` | |
| `DEMO_MODE` / `DEMO_SESSION_SECRET` / `DEMO_SANITIZE_SALT` | `false` / `""` / derived | demo instance only |
| `RUNNER_ENRICHED_TOKEN` | `""` | cert-tier proxy token |
| `SSE_INTERVAL_SEC` | 30 | 30 |

## Build / deploy

```bash
bash build-images.sh runner
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-runner corporatetraveldc-runner-demo
```

Quadlets: `.config/containers/systemd/corporatetraveldc-runner{,-demo}.container`
(Memory 1536m / swap=memory, CPUWeight 100, CPUQuota 300%, production.slice,
`After=corporatetraveldc-web.service`; startup owned by the stack-boot-stagger
unit, so no `WantedBy=`).

## Deferred / future

- Full Chrome/Edge PWA installability (manifest + service worker)
- HFDL message overlay when the HFDL dongle is acquired
- AIS local receive (`ais`/`ais-watcher` Quadlets ship `.disabled`)
- WebSocket upgrade from SSE if volume justifies it
- Persistent RSS cache on disk (currently in-memory)
