# Corporate Travel Dispatch Intelligence (CTDI)

**Documentation snapshot: 2026-08-11** — every factual claim below was verified
against the running system and current source on this date.

Multi-region real-time travel intelligence platform. Monitors commercial
aviation (FAA SWIM push feeds plus REST fallbacks), rail, weather, and airspace
restrictions — delivering push alerts the moment something operationally
relevant changes. Runs as rootless Podman containers managed by systemd
Quadlets under a single deployment user, alongside timer-driven skill
containers, a local SDR receive stack, and host-local Ollama LLM inference.
Container/unit counts drift as feeds and skills are added — check
`systemctl --user list-units 'corporatetraveldc-*' --all` for the live picture
(145 loaded units at this snapshot).

> **Origin note:** CTDI was originally built for Washington, DC metro
> operations (executive chauffeur + CERT/ARES/Skywarn). The DC configuration
> is the reference implementation, not a constraint — see
> **[docs/REGIONALIZATION.md](docs/REGIONALIZATION.md)** for deploying elsewhere.

> **Repository note:** The system user, container prefix, and filesystem paths
> use `corporatetraveldc` — the original deployment name, preserved for
> backward compatibility. New deployments can substitute any username; only
> env config and Quadlet paths need to reflect it.

📄 **[Platform Compatibility Reference (PDF)](docs/platform-compatibility.pdf)** — what works (and what doesn't) on Linux, macOS, Windows, Android, and iOS.
📐 **[Design Principles](docs/DESIGN-PRINCIPLES.md)** — local-first, offline-capable, vendor-neutral. Read before contributing.
🌍 **[Regionalization Guide](docs/REGIONALIZATION.md)** — deploying outside DC.
📡 **[Data Sources & Access Guide](docs/DATA_SOURCES.md)** — signup portals, email templates, and policy links for every integrated feed.
🗺️ **[Internal Infra Map](docs/INFRA_MAP.md)** — full private service/host/domain map (private repo only).
⚠️ **[Single-Edge-Unit Assumptions](docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md)** — every resource guardrail in this stack is tuned for **one Raspberry Pi 5 under shared-resource contention**. Read before de-consolidating or reusing values.

All public releases are GPG signed:

```
ABD3976FCC006E0F3FE559177286B3118BA4EFB2 — Corporate Travel DC 'the operator' (original default key)
419A864CC29A09513039B6E03033FB4D01903159 — Rotated key, new default as of July 7, 2026
```

Active keys ship their pubkeys in-repo, named by full fingerprint.

---

## Status (2026-08-11)

| Component | State |
|---|---|
| Ops dashboard (runner app) | **Tailnet-only.** `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx → :8001). The former public `ops.example.com` hostname was **retired 2026-08-02** and is hard-404'd by hostname in `src/runner/main.py` (`_RETIRED_HOSTNAMES`). |
| Public demo (runner, demo-playback) | **Live** at `https://dispatch-runner.example.com` — password-gated (`DEMO_MODE`, HMAC session cookie), replays `demo.db` via the demo API. Cloudflare tunnel → nginx → port 8005. |
| Web API (browser / programmatic) | `https://dispatch.example.com` (Cloudflare Access gated; nginx stamps `X-CTDI-Public: 1`, which pins the request to Tier 0 regardless of token) |
| Tailscale direct API | `http://100.x.x.x:8000` |
| Public MCP (OpenAPI bridge) | `https://mcp.example.com` — mcpo on port 8083, `DISPATCH_MCP_PUBLIC_SAFE=1`, 26 read-only tools; admin/second-brain tools never loaded in that process |
| FAA SWIM NMS push feeds | ✅ All 6 live (FDPS/STDDS/TFMS/TBFM/ITWS/FNS) — provisioned 2026-07-20, split into per-feed containers 2026-07-26 |
| Local LLM (Ollama) | 16 dedicated `corporatetraveldc-pi5-*` Modelfile models — brief-class models on `phi3:mini`, the rest on `gemma3:4b` (see Local LLM section) |
| ADS-B receive (UltraFeeder) | ✅ **Restored 2026-08-11** — the ADS-B RTL-SDR dongle had stopped enumerating on USB (~2026-08-10, container crash-looping; `adsb-feed-silence-watchdog` detected and alerted correctly); hardware reseat brought it back midday 2026-08-11 (dongle enumerates, container up, live decode confirmed). All other SDR containers (ACARS/VDL2 chain, feeders) up throughout. |

---

## Architecture

web, poller, pusher, and all 7 ingest containers share a SQLite database (WAL
mode). The runner is the only core role that does not touch the shared DB — it
owns the ops frontend and its own JSON state:

```
┌────────────────────────────────────────────────────────────────────┐
│                    deployment user (corporatetraveldc)             │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐    │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest ×7    │    │
│  │ FastAPI   │  │ Scheduler │  │  ntfy     │  │ SWIM ×6 +    │    │
│  │ REST API  │  │ + Skills  │  │  sender   │  │ core (NWWS/  │    │
│  │  :8000    │  │           │  │           │  │ Amtrak/RF)   │    │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘    │
│        └──────────────┴──────────────┴───────────────┘            │
│                   SQLite (WAL) shared DB                           │
│                                                                    │
│  ┌──────────────────────────────────────────────────┐             │
│  │  runner (:8001) — Tailnet-only ops dashboard     │             │
│  │  FastAPI + React/Vite SPA, screen-reader ready   │             │
│  │  Intel Feed · ADS-B Map · Trains · AIS · Brief   │             │
│  │  proxies dispatch web API at :8000               │             │
│  │  owns user_rss_feeds.json (separate from DB)     │             │
│  └──────────────────────────────────────────────────┘             │
│                                                                    │
│  + runner-demo (:8005, public demo) · demo recorder · demo-api    │
│    (:8004) · SDR stack · ntfy · Nextcloud · Open WebUI · timers   │
└────────────────────────────────────────────────────────────────────┘
```

### Core containers

| Container | Image | Role |
|---|---|---|
| `corporatetraveldc-web` | `localhost/corporatetraveldc-web:latest` | FastAPI REST API (port 8000, published on 127.0.0.1 + tailnet IP), tiered auth |
| `corporatetraveldc-poller` | `localhost/corporatetraveldc-poller:latest` | Async scheduler — fetchers + AI skills as subprocesses, watchlist sweeps |
| `corporatetraveldc-pusher` | `localhost/corporatetraveldc-pusher:latest` | ntfy alert dispatcher |
| `corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}` | `localhost/corporatetraveldc-ingest:latest` (one image, 7 Quadlets) | Push ingest, split 2026-07-26 into 7 independent containers — one per SWIM feed plus "core" (NWWS-OI/Amtrak/local airspace) — so any single feed restarts without dropping the rest. See `src/ingest/README.md` and `scripts/ingest-feed-ctl.sh`. |
| `corporatetraveldc-runner` | `localhost/corporatetraveldc-runner:latest` | Ops dashboard SPA + API (port 8001) — Tailnet-only |
| `corporatetraveldc-runner-demo` | same runner image | Demo-playback instance (port 8005 → container 8001), `DEMO_MODE=true`, reads the demo API (:8004) instead of live feeds — public at `dispatch-runner.example.com`, password-gated |
| `corporatetraveldc-demo` / `corporatetraveldc-demo-api` | `localhost/corporatetraveldc-demo:latest` | Archive recorder / read-only playback API (port 8004) over `demo.db` |

### Auxiliary containers (same host)

SDR/RF: `ultrafeeder` (ADS-B + tar1090, port 8080 — restored 2026-08-11, see
Status), `acarsrouter` (:9080), `acarshub` (:9081), `dumpvdl2`,
`acars-watcher` (UDP 5005), plus aggregator feeders `piaware`, `fr24feed`
(:8754), `planefinder` (:30053), `airnavradar`. Disabled pending hardware:
`acarsdec`, `dumphfdl`, `ais`, `ais-watcher` (see `docs/SDR_SERVICES.md`).

Infra/comms: `ntfy` (:2586), `protonbridge` (SMTP relay,
100.x.x.x:1025 → container port 25 — tailnet-only, see
`docs/INFRA_MAP.md` §4),
`nextcloud-app` (:8090) + `nextcloud-db` (Postgres 16), `openwebui` (:3000),
`rss-bridge` (:3001), `csexec-contact` (website contact API, :8002),
`amtrak-tracker`.

### Data feeds

| Feed | Source | Interval | Status |
|---|---|---|---|
| METAR | AviationWeather.gov ADDS | 5 min | ✅ Active |
| NWS alerts | api.weather.gov | 5 min | ✅ Active (REST fallback; push-primary via NWWS-OI) |
| ATCSCC ops plan | ATCSCC | 1 hr | ✅ Active |
| Runsheet | Local file | 5 min | ✅ Active |
| TFR | tfr.faa.gov/tfrapi/getTfrList (JSON) | 5 min | ✅ Active — independent REST poll; **no** push-primary exists for TFRs |
| NAS programs | nasstatus.faa.gov/api/airport-status-information | 5 min | ✅ Active |
| NOTAMs (REST) | FAA NOTAM API | 5 min | ⚠️ Needs `FAA_NOTAM_API_KEY` + `FAA_NOTAM_API_SECRET` (`awaiting_credentials`). Live NOTAM data already flows via the SWIM FNS push feed regardless. |
| DCA / IAD FIDS | MWAA JSON endpoints | 5 min | ✅ Active (600 s staleness threshold — see `docs/DCA_IAD_FIDS.md`) |
| Amtrak | Push-primary in `ingest-core` (api.amtraker.com) / poller fallback | Push / 5 min | ✅ Active |
| FDPS (flight plan + track, FIXM 3.0) | FAA SWIM NMS | Push | ✅ Live |
| STDDS (surface + terminal tracks) | FAA SWIM NMS | Push | ✅ Live — carries no TFR data |
| TFMS (GDP/GS/AFP/restrictions/per-flight TMI) | FAA SWIM NMS | Push | ✅ Live |
| AIM/FNS (digital NOTAMs) | FAA SWIM NMS | Push | ✅ Live |
| TBFM (arrival sequencing) | FAA SWIM NMS | Push | ✅ Live |
| ITWS (terminal weather) | FAA SWIM NMS | Push | ✅ Live |
| NWWS-OI (NWS push) | NWWS-OI XMPP MUC | Push | ✅ Live |
| EUROCONTROL NM B2B | EUROCONTROL | 15 min | ⚠️ Needs credentials — code ships ready |
| JASDAT (Japan) | JCAB/MLIT | 15 min | ⚠️ Needs credentials — code ships ready |

### Push/pull failover

Each connected push feed stamps a `push:<feed>` heartbeat into `feed_state`
every 30 seconds. Before each REST poll that has a push-primary, the poller
checks whether the heartbeat is fresher than 90 seconds (`FALLBACK_MAX_AGE`);
if so the REST fetch is skipped. When ingest disconnects, the heartbeat ages
out and REST polling resumes automatically.

---

## API

**Base URLs:**

| Endpoint | URL | Notes |
|---|---|---|
| Ops dashboard (runner) | `http://100.x.x.x:8001` / `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` | Tailnet only; no public hostname |
| API | `https://dispatch.example.com` | CF Access gated; served as Tier 0 (nginx sets `X-CTDI-Public: 1`) |
| API (tailnet) | `http://100.x.x.x:8000` | Full tier resolution via bearer token |
| Public demo | `https://dispatch-runner.example.com` | Password-gated demo playback |
| Public MCP bridge | `https://mcp.example.com` | 26 read-only OpenAPI endpoints |

### Tier 0 — Anonymous (selection; ~50 GET routes total in `src/web/main.py`)

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Service health + snapshot age |
| GET | `/api/v1/feeds` | Feed freshness + error state |
| GET | `/api/v1/cps` | Critical Predictability State (go/no-go) |
| GET | `/api/v1/tfr` | Active TFRs (no enrichment) |
| GET | `/api/v1/weather` | METAR snapshot |
| GET | `/api/v1/alerts` | Active NWS hazardous weather alerts |
| GET | `/api/v1/wx/discussion[/{awips_id}]` | WPC forecast discussions |
| GET | `/api/v1/airmets` | AIRMET/SIGMET hazard polygons |
| GET | `/api/v1/notams` | Active NOTAMs for DC-area airports |
| GET | `/api/v1/amtrak` | Amtrak DC-area status |
| GET | `/api/v1/opsplan` | ATCSCC daily ops plan |
| GET | `/api/v1/brief` · `/brief/history` · `/brief/weekly` · `/brief/{ref}` | Brief texts + history |
| GET | `/api/v1/route` | Ground route impact narrative |
| GET | `/api/v1/events` | Live SSE event stream |
| GET | `/api/v1/train-config` · `/api/v1/wx-config` | Operator rail / meteorology config |
| GET | `/api/v1/flightplan/{callsign}` | FDPS-confirmed flight plan |
| GET | `/api/v1/fids/{airport}` · `/{airport}/arrivals` · `/{airport}/{flight}` | DCA/IAD FIDS |
| GET | `/api/v1/adsb` | airplanes.live proxy (250 NM of KDCA, 30 s cache) |
| GET | `/api/v1/aircraft/{identifier}` · `/api/v1/aircraft-registry/status` | FAA/OpenSky registry lookup |
| GET | `/api/v1/airspace[/{feature_id}]` | Static DC airspace features |
| GET | `/api/v1/demo/readiness` | Demo archive seed status |
| GET/POST/PATCH/DELETE | `/api/v1/osint/*` | OSINT feed + scopes |
| GET | `/api/v1/board*` | Coordination board (read; posts need `X-Board-Key`) |
| GET | `/api/v1/sectors*` | Sector/family alert topic state |

### Tier 1 — `cert` bearer token

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/tfr-enriched` | TFRs with AI enrichment text |
| GET | `/api/v1/radio` | Radio reference |
| GET | `/api/v1/runsheet` | Daily runsheet + watchlist sessions |
| GET | `/api/v1/opsplan/range` | Ops plan date range |
| GET/POST/DELETE | `/api/v1/watchlist` (sessions) | Watchlist session management |
| GET | `/api/v1/watchlist` + `/history` (entries) | Watchlist entries + event history |

### Tier 2 — `shares` bearer token

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/cui/status` | CUI status — audit-logged |

### Admin — `admin` bearer token

`/admin/healthz`, `/admin/feeds`, `/admin/audit`, `/admin/tokens`,
`/admin/version`, `/admin/triggers`, `POST /admin/refresh-feed/{feed}`,
`POST /admin/force-recompute-cps`, `POST /admin/force-opsplan-snapshot`,
`POST /admin/force-osint-scrape`, `POST /admin/push-alert` (legacy alias
`/admin/push-test-alert`), `GET/POST/DELETE /admin/vip`,
`GET/POST/DELETE /admin/bandwidth-priority`, `/admin/approval-requests*`,
`/admin/watchdog/status`, plus admin-gated watchlist entry mutations
(`POST /api/v1/watchlist/{flights,trains,vessels}[,/batch]`,
`POST /api/v1/watchlist/permanent/batch`, `DELETE /api/v1/watchlist/{id}`)
and `POST /api/v1/remember` (second-brain capture).

### Inbound webhooks — shared-secret header (`X-Webhook-Secret`)

Credential-gated: each returns 503 until its `*_WEBHOOK_SECRET` is set in
`dispatch-secrets.env`. See `src/web/routes/webhooks.py`.

| Method | Path | Source |
|---|---|---|
| POST | `/webhooks/limoanywhere/reservations` | LimoAnywhere Customer API |
| POST | `/webhooks/ringcentral/events` | RingCentral (handles Validation-Token handshake) |
| POST | `/webhooks/3cx/events` | 3CX Call Control API |

### Runner API (port 8001, Tailnet-only)

The runner serves its React/Vite SPA plus its own API. Sensitive surfaces
(admin proxy, non-GET API proxy) are gated by `_is_trusted()` (Tailscale
CGNAT 100.64.0.0/10 + RFC1918 + loopback; `CF-Connecting-IP` honored
exclusively when present). In demo mode, untrusted origins additionally
need the password-gated session cookie.

Routes: `/healthz`, `/api/whoami`, `/api/demo/{login,status,webhook-log}`,
`/api/adsb/{local,live}`, `/api/{vdl2,acars,hfdl}/messages`,
`/api/ais/vessels`, `/api/ask` + `/api/chat/history` (Ollama chat),
`/api/dispatch/{path}` (transparent proxy → :8000, with Tier-1 token
injection for an allowlist of paths — see
`docs/auth-token-proxy-pattern.md`), `/api/stream` (SSE),
`/api/ntfy/stream`, `/api/v1/config` (GET/PUT), `/api/v1/frontend-config`,
and the RSS engine: `/api/rss`, `/api/rss/categories` (GET/POST),
`/api/rss/custom`, `/api/rss/resolve-source`, `/api/rss/user-feeds`
(GET/POST/DELETE).

**RSS catalog** (`src/shared/rss_catalog.py`, shared with the second-brain
RSS poller): **11 built-in categories, 27 feeds** — `corporate_intel`,
`marketing_intel`, `travel_trends`, `dc_area`, `aviation`,
`advanced_air_mobility`, `gig_economy`, `concierge_luxury_travel`,
`trains_yachts`, `executive_protection`, `osint_cybersecurity_video` — plus
`__custom__` for user-defined feeds. User feeds persist in
`/var/lib/corporatetraveldc/user_rss_feeds.json` (custom categories in
`user_rss_categories.json`). `?limit=N` default 200 max 500; each feed capped
at 100 items pre-merge; dates normalized to ISO 8601; `<enclosure
type="audio/*|video/*">` items get an `audio_url` for the inline podcast
player.

### Auth model

Tokens are created with **`ctdc-token`** (`src/ctdc_token/cli.py`). Format:
`ctdc_<user>_<32-char-random>`. Only the SHA-256 hash is stored; plaintext is
shown once at creation.

```
Tier 0 → anonymous (all /api/v1/* data endpoints), and ANY request carrying
         X-CTDI-Public: 1 (stamped by the public nginx vhosts) regardless of token
Tier 1 → bearer token tier=cert
Tier 2 → bearer token tier=shares (audit-logged; CUI-adjacent)
Admin  → bearer token tier=admin (all /admin/* endpoints)
```

**Network origin no longer grants any tier.** The old
`Tailscale-User-Login`-header / source-IP tier grant was removed (it was
confirmed spoofable via XFF against the live container); a real bearer token
is required for T1+ on every path. See `src/auth/auth.py`.

---

## Watchlist system

Two tiers share one monitoring/alert pipeline — full detail in
`src/shared/watchlist_README.md`:

**Permanent** — **JSON** files in `/opt/corporatetraveldc/watchlists/`
(`permanent_flights.json`, `permanent_trains.json`, `permanent_vessels.json`).
Hot-reloaded by `WatchlistFileWatcher` within ~65 s, no restart.

**Transient** — added via REST (`POST /api/v1/watchlist/{flights,trains,vessels}`,
admin token). Auto-expire via `auto_remove_at`, swept every 60 s.

Three entry types: flight (callsign), train (Amtrak number), **vessel (MMSI —
AISHub sweep every 300 s, requires `AIS_AISHUB_ID`)**. Events fire dual ntfy
pushes (domain topic + concise `dispatch`), 5-minute content-aware dedup.
Flight monitoring source chain: FlightAware AeroAPI (if key set) →
airplanes.live (free) → local UltraFeeder ADS-B → FDPS push cache → schedule
inference. OOOI phase state machine: `pre_departure → out → off → on → in`,
phases never revert.

---

## ntfy topics (core set)

| Topic | Content | Priority |
|---|---|---|
| `tfr-alert` / `hot-alerts` | VIP/POTUS TFR, Marine One/AF1, severe-ops events | 5 |
| `flight-alerts` / `train-alerts` / `vessel-alerts` | Watchlist events per domain | 2–5 |
| `dispatch` | Concise bottom line for all events | mirrors source |
| `dispatch-debriefs` / `dispatch-ops` | Full debrief tables / weekly aggregate | 2–3 |
| `cps` | CPS score changes | 3–5 |
| `wx-alerts` | NWS + ITWS hazardous weather | 3–4 |
| `nas-alerts` | NAS program/restriction/NOTAM alerts | 2–5 |
| `<family>-alerts` + `<family>-<zone>` (tfms/tbfm/fdps/itws/aim_fns × zny/zdc/zid/zob/zatl/zhu/zla/zse) | Escalating family/sector alerts, per-topic throttled | 2–4 |
| `ops-brief` / `ep` / `ep-advance` | Hourly briefs | 2–4 |
| `ops-health` | Freshness audit, watchdogs, thermal guard | 2–5 |
| `osint-alerts` | OSINT scope hits | 2–3 |
| `approval-gate` | Sudo/ollama approval prompts (Allow/Deny) | 4 |

Full catalog + trigger/dedup logic: `docs/ALERT_REFERENCE.md`; design
rationale: `docs/ALERT_ARCHITECTURE.md`.

---

## CPS — Critical Predictability State

Part 135.609-informed go/no-go score. Factors: ceiling, visibility, wind,
precipitation (METAR), airspace (TFRs + static restricted areas), GDP (NAS
programs). Output `GREEN/GO`, `YELLOW/MARGINAL`, `RED/NO-GO`. Computed by
`poller/skills/cps_recompute.py` hourly and on demand via
`POST /admin/force-recompute-cps`.

---

## Demo Mode & Travel Pattern Intelligence

A built-in archive recorder (`corporatetraveldc-demo.service`) captures
rolling snapshots of every live feed into `demo.db` (zlib-compressed, ~52-week
retention on <500 MB). The demo-playback stack is **live and public**:

- `corporatetraveldc-demo-api.service` — read-only playback API, port 8004
- `corporatetraveldc-runner-demo.service` — second runner instance, port 8005,
  `DEMO_MODE=true`, `DISPATCH_BASE_URL=http://100.x.x.x:8004`
- Public hostname: **`https://dispatch-runner.example.com`** —
  password-gated (`POST /api/demo/login`, HMAC-signed `ctdc_demo_session`
  cookie, 8 h default); signals sanitized server-side.

Seed readiness: `GET /api/v1/demo/readiness` reports per-tier
(2w/8w/12w/24w/36w/52w) archive readiness. Config
(`DEMO_RECORDER_INTERVAL=300`, `DEMO_RECORDER_RETENTION=364`,
`DEMO_RECORDER_SEED_TARGET=14`) in `dispatch.env`.

The same archive doubles as a longitudinal dataset (NOTAM construction
windows, TFR frequency, GDP seasonality, METAR climatology, Amtrak OTP) for
quarterly planning and partnership evidence — collected as a byproduct of
normal operation.

---

## Supported Platforms

> Full detail in **[docs/platform-compatibility.pdf](docs/platform-compatibility.pdf)**.

| Platform | Server stack | Containers | Local Ollama | Install script |
|---|---|---|---|---|
| **Linux x86_64 / ARM64** (Pi 5 reference) | ✅ Full | Podman ✅ | ✅ | `install/install.sh` |
| **macOS** (Apple Silicon / Intel) | ✅ Full | Podman/Docker ✅ | ✅ | `install/install.sh` |
| **Windows x64** | ✅ via WSL2 | Docker/Podman Desktop | ✅ native | `install/install-windows.ps1` |
| **Android ARM64** (Termux) | ✅ bare Python | ❌ | ✅ | `install/install-android.sh` |
| **iOS / iPadOS** | ❌ web client only | ❌ | ❌ | browse to deployment URL |

The `solace-pubsubplus` SWIM library is Linux-only (prebuilt wheels x86_64;
ARM64 builds from source) — SWIM push ingest requires Linux; other platforms
run the REST-fallback feed set.

---

## Installation

### Prerequisites

- Linux host running Fedora (reference deployment: Fedora 44 aarch64 on a
  Pi 5, SELinux enforcing), Debian, or Ubuntu
- Rootless Podman with a systemd user session (linger enabled)
- Ollama on the host — all inference is local, no cloud LLM key required

### First-time setup

```bash
git clone <this-repo> /opt/corporatetraveldc/private/ctdi-dispatch-internal
cd /opt/corporatetraveldc/private/ctdi-dispatch-internal

# Secrets
cp dispatch-secrets.env.template /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# populate credentials (SWIM, NTFY_TOKEN, FAA NOTAM key, …)

# Build container images
bash build-images.sh

# Build the dedicated Ollama models (SWA-guarded, smoke-gated)
bash build-models.sh

# Install Quadlets
cp .config/containers/systemd/*.container ~/.config/containers/systemd/
systemctl --user daemon-reload

# Start the core stack (production uses the stagger units:
# corporatetraveldc-stack-boot-stagger + corporatetraveldc-boot-stagger)
systemctl --user start corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher

# Verify
curl http://127.0.0.1:8000/healthz

# Create an admin token
PYTHONPATH=src python3 src/ctdc_token/cli.py create \
  --user operator --tier admin --label admin-phone
```

### After any code change

```bash
bash build-images.sh          # pass a target name to rebuild one image
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web corporatetraveldc-poller \
                         corporatetraveldc-pusher corporatetraveldc-runner
```

Note: container entrypoints run `scripts/verified-exec.sh` — a signed-manifest
integrity check — so code changes require re-signing the manifest
(`scripts/sign-manifest.sh`) before rebuilt images will start.

---

## Development

All Python commands run from the repo root with `PYTHONPATH=src`:

```bash
# Run a skill manually (--force bypasses the SR-2 hash gate)
PYTHONPATH=src python3 src/poller/skills/cps_recompute.py --force
PYTHONPATH=src python3 src/poller/skills/tfr_enrichment.py --force

# Run a fetcher manually
PYTHONPATH=src python3 src/poller/fetchers/metar.py

# Token management
PYTHONPATH=src python3 src/ctdc_token/cli.py list
PYTHONPATH=src python3 src/ctdc_token/cli.py revoke --prefix ctdc_user_

# Inspect the database
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db \
  "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 3;"

# Tests
python -m pytest tests/ -x --tb=short
```

### Skill runtime rules

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block —
always. Logged to `/var/lib/corporatetraveldc/api-usage.csv`.

**SR-2** (`src/common/sr2_gate.py`): call `hash_gate()` before any expensive
computation or LLM call. Hash only content-bearing fields (never timestamps).
If it returns `"skipped"`, `sys.exit(0)` immediately. Support `--force`.

### Schema migrations

`src/common/db.py` is the single schema authority, versioned additively
(`SCHEMA`, `SCHEMA_V2`, … — check the file for the current top version; V31
exists as of this snapshot). Never drop or rename columns — only
`ALTER TABLE ADD COLUMN`.

---

## Local LLM — Ollama

**All inference is local.** No external LLM API key is required. Ollama runs
on the host, bound to the tailnet IP (`OLLAMA_BASE_URL=http://100.x.x.x:11434`),
with `OLLAMA_KEEP_ALIVE=10m` and CPU-only inference — effectively **one model
resident at a time** on this hardware.

### Dedicated per-task models (since 2026-08-02)

Each LLM-calling skill has its own Ollama model built from a Modelfile at repo
root (`corporatetraveldc.<task>` → `corporatetraveldc-pi5-<task>:latest`) that
bakes the task's system prompt in as the model default. 16 models exist:

| Base | Models |
|---|---|
| **`phi3:mini`** (brief class) | `ops-brief`, `ops-brief-trend`, `ep-advance`, `ep-advance-trend` |
| `gemma3:4b` (all others) | `chat`, `osint`, `osint-monitor`, `tfr-enrichment`, `route-impact`, `weekly-summary`, `aam-watch` (shared by 7 daily/weekly watch skills), `dispatch-desk`, `transport-digest`, `disruption-weather-digest`, `secondbrain-daily`, `secondbrain-weekly` |

**Why phi3:mini for the brief class (2026-08-10/11):** gemma3's Sliding
Window Attention defeats llama.cpp's KV-cache reuse, forcing full prompt
re-processing on the hourly briefs' long prompts — blowing the 240 s
`OLLAMA_TIMEOUT` and driving near-100% deterministic fallback.
`build-models.sh` now has:

- a hard **`SWA_DENYLIST_REGEX`** guard (`^FROM[[:space:]]+(gemma3|gemma2)…`)
  that refuses to build any brief-class model from a gemma2/gemma3 base
  (override: `BRIEF_BASE_OVERRIDE=1`), and
- a **smoke-test promotion gate**: brief models build as `:candidate`, must
  answer a 125%-of-worst-case prompt within 200 s, and only then get promoted
  to `:latest` — a failed candidate is deleted and last-known-good stays live.

**Orphaned-generation fix (2026-08-11):** a client-side timeout does *not*
stop Ollama's server-side generation. `src/common/llm.py` now sends
`_abandon_ollama_generation()` — a `keep_alive: 0` unload signal — the moment
a caller's request hits a transport error/timeout (this previously piled up
orphaned `llama-server` generations to a 52 load average). The same fix is in
`build-models.sh`'s smoke test.

Guard 3: `corporatetraveldc-brief-fallback-monitor.timer` (hourly) alerts
loudly if briefs degrade to deterministic fallback anyway.

Cloud fallback: `ANTHROPIC_FALLBACK_ENABLED` defaults `true` but brief skills
pass `allow_anthropic=False`; with Ollama unavailable, skills fall back to
deterministic templates. Design history: `docs/DEDICATED_MODELS_PLAN.md`.

### Rebuilding models

```bash
bash build-models.sh          # verifies signed manifest, applies guards, builds all 16
```

`corporatetraveldc-ollama-prewarm-*` timers pre-load each skill's model 2–3
minutes before its scheduled run (bounded keep_alive, not permanent).

---

## FAA SWIM / NMS credentials

All six feeds are provisioned and live. Reference for rotation/re-provisioning
(`<KEY>` ∈ FDPS, STDDS, TFMS, AIM, TBFM, ITWS — note AIM credentials serve the
`fns` feed/heartbeat):

```
SWIM_NMS_USER_<KEY> / SWIM_NMS_PASS_<KEY> / SWIM_NMS_QUEUE_<KEY>
SWIM_NMS_VPN_<KEY>  / SWIM_NMS_HOST_<KEY>   (fallback SWIM_NMS_HOST,
                                             default tcps://ems1.swim.faa.gov:55443)
```

After credential entry, restart just the affected feed:

```bash
scripts/ingest-feed-ctl.sh restart <feed>          # or:
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
```

To request SWIM access for a new deployment see
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

---

## Key paths

| Path | Purpose |
|---|---|
| `/opt/corporatetraveldc/private/ctdi-dispatch-internal/` | This repo (`/opt/corporatetraveldc/ctdi-dispatch-internal` is a symlink to it) |
| `src/` | All Python source |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | SQLite database (WAL) |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret platform config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Credentials (mode 0600) |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 skill usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist **JSON** files |
| `/var/lib/corporatetraveldc/user_rss_feeds.json` | Runner: user Intel Feed subscriptions |
| `.config/containers/systemd/` (repo) → `~/.config/containers/systemd/` (live) | Quadlet unit files |

---

## CUI handling

**CRITICAL**: This repository never contains, and must never be modified to
contain, actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies — in
code, configs, exports, or documents, even password-protected. The
infrastructure ships with empty placeholder files; the operator populates
credentialed data from authorized sources on the deployment host. The audit
log is append-only, 90-day retention, and never leaves the host.

---

## Reservation System Integration

CTDI can add flights/trains to the watchlist automatically when a reservation
is created in livery/booking software, via the credential-gated inbound
webhooks (`/webhooks/limoanywhere/reservations`, `/webhooks/ringcentral/events`,
`/webhooks/3cx/events`) or by calling the watchlist API directly:

```
POST /api/v1/watchlist/flights          (admin bearer token)
{"identifier": "UAL2341", "origin": "KORD", "destination": "KDCA",
 "auto_remove_at": "2026-07-01T22:00:00Z", "notes": "Smith pickup"}
```

For trains use `/api/v1/watchlist/trains` with the train number. Platforms
without native webhooks can poll their reservations API on cron and sync the
same way. Permanent entries: edit the JSON files in
`/opt/corporatetraveldc/watchlists/` (hot-reloaded).

---

## License

Proprietary. CS Executive Services LLC. All rights reserved.
