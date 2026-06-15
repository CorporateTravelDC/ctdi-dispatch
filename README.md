# Corporate Travel Dispatch Intelligence (CTDI)

Multi-region real-time travel intelligence platform. Monitors commercial aviation (via FAA SWIM or equivalent regional feeds), rail, weather, and airspace restrictions — delivering push alerts the moment something operationally relevant changes. Runs as four rootless Podman containers managed by systemd Quadlets on any Linux system.

> **Origin note:** CTDI was originally built for Washington, DC metro operations (executive chauffeur + CERT/ARES/Skywarn). The system is designed for global deployment from day one — the DC configuration is the reference implementation, not a constraint. See **[docs/REGIONALIZATION.md](docs/REGIONALIZATION.md)** for a full guide to deploying elsewhere.

> **Repository note:** The system user, container prefix, and filesystem paths use `corporatetraveldc` — the original deployment name. These are preserved for backward compatibility on the reference Pi deployment. New deployments can substitute any username; only the env config and Quadlet paths need to reflect it.

> **Repository rename (2026-06):** This repository was renamed from `CorporateTravelDC/corporatetraveldc-dispatch` to `CorporateTravelDC/ctdi-dispatch` when the project was rebranded as Corporate Travel Dispatch Intelligence. GitHub automatically redirects all previous URLs — any link or `git remote` pointing at `github.com/CorporateTravelDC/corporatetraveldc-dispatch` will resolve correctly. If you arrived here via a redirect and want to confirm you're in the right place: the project description, commit history, and this note are the canonical confirmation. No content was moved to a new repository.

📄 **[Platform Compatibility Reference (PDF)](docs/platform-compatibility.pdf)** — what works (and what doesn't) on Linux, macOS, Windows, Android, and iOS.
📐 **[Design Principles](docs/DESIGN-PRINCIPLES.md)** — local-first, offline-capable, vendor-neutral architecture. Read before contributing.
🌍 **[Regionalization Guide](docs/REGIONALIZATION.md)** — deploying outside DC: airports, weather offices, European and Asia-Pacific feed equivalents.
📡 **[Data Sources & Access Guide](docs/DATA_SOURCES.md)** — API signup portals, email templates, and policy links for every integrated feed — US, European, and Asia-Pacific.

---

## Status

| Component | State |
|---|---|
| Web API (browser / PWA) | `https://dispatch.csexecutiveservices.com` *(CF Access gated)* |
| Web API (programmatic / admin) | `https://ops.csexecutiveservices.com` *(no CF Access gate)* |
| Tailscale direct | `http://100.94.80.100:8000` |
| CPS | YELLOW / MARGINAL |
| All containers | Running |
| FAA SWIM NMS push feeds | ✅ Live — all 6 feeds connected (CS Exec subscription, 2026-06) |
| Local LLM (Ollama) | mistral-nemo 12B — csexec-chat + csexec-osint Modelfile wrappers |
| Dispatch Drawer | Streaming chat via csexec-chat (mistral-nemo) |

---

## Architecture

Five containers share a SQLite database (WAL mode) under the deployment user. The runner is the only container that does not touch the shared DB — it owns the PWA frontend and its own JSON state:

```
┌────────────────────────────────────────────────────────────────────┐
│                    deployment user (corporatetraveldc)             │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────┐         │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest  │         │
│  │ FastAPI   │  │ Scheduler │  │  ntfy     │  │  SWIM   │         │
│  │ REST API  │  │ + Skills  │  │  sender   │  │  NWWS   │         │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └────┬────┘         │
│        └──────────────┴──────────────┴──────────────┘             │
│                      SQLite (WAL) shared DB                        │
│                                                                    │
│  ┌─────────────────────────────────────────────────┐               │
│  │  runner  (port 8001)                            │               │
│  │  FastAPI + React/Vite PWA                       │               │
│  │  Intel Feed · ADS-B Map · Status · Brief · Chat │               │
│  │  proxies dispatch web API at :8000              │               │
│  │  owns user_rss_feeds.json (separate from DB)    │               │
│  └─────────────────────────────────────────────────┘               │
└────────────────────────────────────────────────────────────────────┘
```

### Containers

| Container | Image | Role |
|---|---|---|
| `corporatetraveldc-web` | `localhost/corporatetraveldc-web:latest` | FastAPI REST API, tiered auth |
| `corporatetraveldc-poller` | `localhost/corporatetraveldc-poller:latest` | Async scheduler — fetchers + AI skills |
| `corporatetraveldc-pusher` | `localhost/corporatetraveldc-pusher:latest` | ntfy alert dispatcher |
| `corporatetraveldc-ingest` | `localhost/corporatetraveldc-ingest:latest` | SWIM/NWWS/Amtrak push ingest — all 6 NMS feeds + NWWS-OI live |
| `corporatetraveldc-runner` | `localhost/corporatetraveldc-runner:latest` | PWA frontend (React/Vite) + runner API (port 8001) |

### Data feeds

| Feed | Source | Interval | Status |
|---|---|---|---|
| METAR | AviationWeather.gov ADDS | 5 min | ✅ Active |
| NWS alerts | api.weather.gov | 5 min | ✅ Active |
| ATCSCC ops plan | ATCSCC | 1 hr | ✅ Active |
| Runsheet | Local file | 5 min | ✅ Active |
| TFR | tfr.faa.gov XML | 5 min | ⚠️ FAA upstream issue |
| NAS programs | FAA NAS/OIS | 5 min | ⚠️ Empty upstream response |
| NOTAMs | FAA NOTAM API | 5 min | ⚠️ Needs `FAA_NOTAM_API_KEY` |
| Amtrak | Push ingest / poller fallback | Push / 5 min | ✅ Active |
| FDPS (flight plan + track) | FAA SWIM NMS | Push | ✅ Live — push:fdps heartbeat active |
| STDDS (surface + terminal tracks + TFRs) | FAA SWIM NMS | Push | ✅ Live — push:stdds heartbeat active |
| TFMS (GDP/GS/AFP/AAR) | FAA SWIM NMS | Push | ✅ Live — push:tfms heartbeat active |
| AIM/FNS (digital NOTAMs) | FAA SWIM NMS | Push | ✅ Live — push:fns heartbeat active |
| TBFM (arrival sequencing) | FAA SWIM NMS | Push | ✅ Live — push:tbfm heartbeat active |
| ITWS (terminal weather) | FAA SWIM NMS | Push | ✅ Live — push:itws heartbeat active |
| NWWS-OI (NWS push) | NWWS-OI XMPP MUC | Push | ✅ Live — push:nws heartbeat active |

### Push/pull failover

The ingest container stamps heartbeats into `feed_state` every 30 seconds. Before each REST poll, the poller checks whether the heartbeat for that feed is fresher than 90 seconds. If so, it skips the REST fetch — ingest owns that feed. When ingest disconnects, the heartbeat ages out and REST polling resumes automatically. No manual intervention required.

---

## Deploying outside DC

**The feed credentials themselves don't change when you move regions — only the flags for what you're monitoring do.** You're pointing the same credential infrastructure at different geographic filters.

Three files contain all DC-specific geography. Swap these and the system works anywhere:

### 1. Airport hub list — `src/poller/skills/ops_brief.py`

```python
# Line ~48 — replace with your local primary + regional hub airports
HUB_AIRPORTS = "KDCA,KIAD,KBWI,KJFK,KEWR,KLGA,KBOS,KPHL,KORD,KATL,KLAX,KSFO,KSEA,KDEN,KDFW"
```

For example, a Chicago-based deployment might be:
```python
HUB_AIRPORTS = "KORD,KMDW,KMKE,KDTW,KSTL,KDEN,KLAX,KJFK,KBOS,KSFO"
```

For European deployments, use ICAO 4-letter codes (same format — AviationWeather.gov covers them):
```python
HUB_AIRPORTS = "EGLL,EGKK,EHAM,LFPG,EDDF,LEMD,LIRF,EBBR,LPPT,LSZH"
```

The `_metar_section()` function already handles ICAO format correctly. The "transcontinental hubs" label in briefings is cosmetic — rename it in the Ollama system prompt (`SYSTEM_PROMPT` in ops_brief.py) to match your context: "EUROPEAN HUBS", "INTL CONNECTIONS", whatever reads naturally for your operation.

### 2. NWS alert area — `src/poller/skills/ops_brief.py`

```python
# Line ~51-53 — replace state/territory codes for your region
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?area=VA,MD,DC,NY,NJ,CT,MA,PA,DE,RI&status=actual&severity=Extreme,Severe,Moderate"
)
```

This uses NWS FIPS state codes. Replace with your states/territories. Outside the US, the NWS feed won't apply — see [docs/REGIONALIZATION.md](docs/REGIONALIZATION.md) for international weather API equivalents.

### 3. NWS weather field office filter — `dispatch.env`

```bash
# DC reference deployment: LWX (Sterling VA), AKQ (Wakefield VA), CTP (State College PA)
# Replace with your local WFO codes — find yours at https://www.weather.gov/srh/nwsoffices
NWWS_WFO_FILTER=LWX,AKQ,CTP
```

The NWWS-OI XMPP feed delivers products from all WFOs nationwide. This filter keeps only the ones you care about. Without it, every WFO's output lands in your ingest queue.

> **Note for operators outside the US:** The NWS API (`api.weather.gov`) and NWWS-OI feed cover US territory only. For international deployments, replace these with regional equivalents — see [docs/REGIONALIZATION.md](docs/REGIONALIZATION.md) for EUROCONTROL, JMA (Japan), BoM (Australia), and other regional weather APIs that integrate into the same poller slots.

---

## API

**Base URLs:**

| Endpoint | URL | Notes |
|---|---|---|
| Browser / PWA | `https://dispatch.csexecutiveservices.com` | CF Access gated — browser auth required |
| Programmatic / admin | `https://ops.csexecutiveservices.com` | No CF Access gate — Bearer token only; use for API scripts and machine calls |
| Tailscale direct | `http://100.94.80.100:8000` | Always available on tailnet; preferred fallback |

> **Note:** `dispatch.csexecutiveservices.com` has Cloudflare Access enabled. Use `ops.csexecutiveservices.com` for all programmatic admin calls — Bearer token provides the actual authorization.

### Tier 0 — Anonymous

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Service health + snapshot age |
| GET | `/api/v1/feeds` | Feed freshness + error state |
| GET | `/api/v1/cps` | Critical Predictability State (HEMS go/no-go) |
| GET | `/api/v1/tfr` | Active TFRs (no enrichment) |
| GET | `/api/v1/weather` | METAR snapshot — DCA, IAD, BWI + surrounding stations |
| GET | `/api/v1/alerts` | Active NWS hazardous weather alerts |
| GET | `/api/v1/notams` | Active NOTAMs for DC-area airports |
| GET | `/api/v1/amtrak` | Amtrak DC-area status |
| GET | `/api/v1/opsplan` | ATCSCC daily ops plan |
| GET | `/api/v1/brief` | Latest daily brief text |
| GET | `/api/v1/route` | Latest ground route impact narrative |
| GET | `/api/v1/events` | Live SSE event stream (PWA-ready) |

### Tier 1 — Tailscale / CERT bearer token

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/tfr-enriched` | TFRs with AI enrichment text |
| GET | `/api/v1/radio` | Radio reference placeholder |
| GET | `/api/v1/runsheet` | Daily runsheet + watchlist sessions |
| GET | `/api/v1/opsplan/range` | Ops plan date range (pattern analysis) |
| GET/POST/DELETE | `/api/v1/watchlist` | Watchlist session management |

### Admin — admin bearer token

| Method | Path | Description |
|---|---|---|
| GET | `/admin/healthz` | Admin health |
| GET | `/admin/feeds` | Feed state (admin view) |
| GET | `/admin/audit` | Audit log |
| GET | `/admin/tokens` | Active auth tokens |
| GET | `/admin/version` | Build/version info |
| GET | `/admin/triggers` | Trigger queue |
| POST | `/admin/refresh-feed/{feed}` | Manual feed refresh |
| POST | `/admin/force-recompute-cps` | Force CPS recalculation |
| POST | `/admin/force-opsplan-snapshot` | Force ops plan snapshot |
| POST | `/admin/push-test-alert` | Send test ntfy alert |
| GET/POST/DELETE | `/admin/vip` | VIP watchlist management |

### Runner API (port 8001 / `dispatch-runner.csexecutiveservices.com`)

The runner exposes its own API alongside the static PWA build. All routes are Tailscale-gated (100.64.0.0/10 enforced by FastAPI middleware).

**ADS-B**

| Method | Path | Description |
|---|---|---|
| GET | `/api/adsb/local` | Proxy → UltraFeeder aircraft.json (local antenna) |
| GET | `/api/adsb/live` | Proxy → airplanes.live v2, 250nm radius from KDCA |

**Intel Feed — RSS/Atom**

| Method | Path | Description |
|---|---|---|
| GET | `/api/rss` | Merged feed: catalog + user-defined feeds for `?category=` |
| GET | `/api/rss/categories` | Available categories and their catalog sources |
| GET | `/api/rss/custom` | Fetch and proxy an arbitrary feed URL server-side (CORS bypass) |
| GET | `/api/rss/user-feeds` | List all user-defined feeds |
| POST | `/api/rss/user-feeds` | Add a user-defined feed (validates by fetching before saving) |
| DELETE | `/api/rss/user-feeds/{id}` | Remove a user-defined feed by UUID |

`?category=` accepts: `corporate_intel`, `marketing_intel`, `travel_trends`, `dc_area`, `aviation`, `__custom__`.
`?limit=N` (default 200, max 500) controls max items returned. Each individual feed is capped at 100 items before merging. Dates are normalized to ISO 8601 on parse so sort order is always correct regardless of source date format.

RSS items with `<enclosure type="audio/*">` or `<enclosure type="video/*">` tags are returned with an `audio_url` field. The frontend renders these as podcast episodes with an inline HTML5 player.

User-defined feeds are persisted to `/var/lib/corporatetraveldc/user_rss_feeds.json` (volume-mounted; survives container rebuilds). Each entry has: `id` (UUID), `name`, `url`, `category`.

**Dispatch proxy**

| Method | Path | Description |
|---|---|---|
| GET/POST | `/api/dispatch/{path}` | Transparent proxy → dispatch web API at :8000 |
| GET | `/api/stream` | SSE stream: CPS + TFR count + feed health (30s interval) |
| GET | `/healthz` | Runner service health |

### Auth model

Tokens are created with `csex-token`. Format: `ctdc_<user>_<32-char-random>`. Only the SHA-256 hash is stored in the database; plaintext is shown once at creation and never stored.

```
Tier 0 → anonymous (all /api/v1/* data endpoints)
Tier 1 → Tailscale-User-Login header | 100.x.x.x source IP | cert bearer token
Tier 2 → bearer token tier=shares (audit-logged; CUI-adjacent)
Admin  → bearer token tier=admin (all /admin/* endpoints)
```

---

## Watchlist system

Two tiers of watchlist entries share the same monitoring and alert infrastructure:

**Permanent** — loaded from YAML files in `/opt/corporatetraveldc/watchlists/`. Monitored every operating day indefinitely. File changes are picked up by `WatchlistFileWatcher` without a restart.

**Transient** — added via REST API (`POST /api/v1/watchlist`). Have an `auto_remove_at` timestamp. Swept automatically by `WatchlistSweep` every 60 seconds.

Both types fire dual ntfy pushes on every event: a detailed push to the domain topic (`flight-alerts` / `train-alerts`) and a concise push to `dispatch`. A 5-minute dedup window suppresses re-fires of the same event type for the same entry.

Flight monitoring uses a priority source chain: FlightAware AeroAPI (if key set) → airplanes.live (free, no key) → local UltraFeeder ADS-B → FDPS cache (when NMS provisioned) → schedule inference fallback.

OOOI phase state machine: `pre_departure → out → off → on → in`. Phases never revert.

---

## ntfy topics

| Topic | Content | Priority |
|---|---|---|
| `tfr-alert` | VIP/POTUS TFR active | 5 (max) |
| `hot-alerts` | VIP TFR + operationally critical events | 5 |
| `flight-alerts` | OOOI events, diversions, landings | 4–5 |
| `train-alerts` | Amtrak delay events | 4–5 |
| `dispatch` | Concise bottom line for all events | mirrors source |
| `cps` | CPS score changes | 3–5 |
| `wx-alerts` | NWS hazardous weather | 3–4 |
| `ops-brief` | Daily / weekly brief | 3 |
| `ops-health` | Freshness audit | 2 |

---

## CPS — Critical Predictability State

The CPS score is a Part 135.609-informed go/no-go assessment for HEMS operations. Six factors are evaluated and combined:

| Factor | Source |
|---|---|
| Ceiling | METAR — primary airports |
| Visibility | METAR |
| Wind | METAR |
| Precipitation | METAR precip_code |
| Airspace | Active TFRs, static restricted areas |
| GDP | Active NAS ground delay programs |

Output: `GREEN / GO`, `YELLOW / MARGINAL`, `RED / NO-GO`. Computed by `poller/skills/cps_recompute.py` every 60 minutes and on demand via `POST /admin/force-recompute-cps`.

---

## Supported Platforms

> Full detail in **[docs/platform-compatibility.pdf](docs/platform-compatibility.pdf)** — feature matrix, per-platform notes, and package compatibility table.

| Platform | Architecture | Server stack | Containers | Local Ollama | Install script |
|---|---|---|---|---|---|
| **Linux x86_64** | AMD64 | ✅ Full | Podman ✅ | ✅ | `install/install.sh` |
| **Linux ARM64** (Pi 5, SBCs) | aarch64 | ✅ Full | Podman ✅ | ✅ | `install/install.sh` |
| **macOS Apple Silicon** | arm64 | ✅ Full | Podman / Docker ✅ | ✅ | `install/install.sh` |
| **macOS Intel** | x86_64 | ✅ Full | Podman / Docker ✅ | ✅ | `install/install.sh` |
| **Windows x64** | AMD64 | ✅ via WSL2 | Docker Desktop / Podman Desktop | ✅ native | `install/install-windows.ps1` |
| **Android ARM64** | aarch64 | ✅ bare Python | ❌ (Termux) | ✅ (Termux) | `install/install-android.sh` |
| **iOS / iPadOS** | arm64 | ❌ (web client only) | ❌ | ❌ | — browse to deployment URL |

### Notes by platform

**Linux (x86_64 / ARM64)** — primary deployment target. Full Podman rootless container stack with systemd Quadlets. Fedora preferred. The `solace-pubsubplus` SWIM ingest library has prebuilt wheels for x86_64; ARM64 requires a source build.

**macOS (Apple Silicon / Intel)** — full Python stack runs natively; Podman Machine or Docker Desktop provides the container layer. `solace-pubsubplus` (FAA SWIM NMS) is Linux-only — SWIM push feeds require running in a Linux VM or forwarding from a Pi.

**Windows x64** — the installer sets up WSL2 and runs the Linux stack inside it. Ollama installs natively on Windows and is accessible from WSL2 at the host IP.

**Android ARM64 (tablet / kiosk)** — runs via Termux (install from F-Droid). All REST feeds and Ollama work; SWIM push ingest is not supported. Recommended models for constrained memory: `llama3.2:3b` (2.0 GB) or `phi3.5` (2.2 GB).

**iOS / iPadOS (iPhone, iPad)** — no server-side install. Browse to your Cloudflare Tunnel URL. Add to Home Screen for a PWA experience.

---

## Installation

### Prerequisites

- Linux host running Fedora (preferred), Debian, or Ubuntu
- Rootless Podman with systemd user session enabled
- Ollama installed on the host (all inference runs locally — no cloud LLM key required)

### First-time setup

```bash
git clone https://github.com/CorporateTravelDC/corporatetraveldc-dispatch.git /opt/corporatetraveldc
cd /opt/corporatetraveldc

# Copy and populate secrets
cp dispatch-secrets.env.example /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# Edit dispatch-secrets.env — add FAA_NOTAM_API_KEY, NTFY_TOKEN, and any feed credentials
# No LLM API key required — all inference runs locally via Ollama

# Build all container images
bash build-images.sh

# Install Quadlets
cp .config/containers/systemd/corporatetraveldc-*.container \
   ~/.config/containers/systemd/

# Start services
systemctl --user daemon-reload
systemctl --user start corporatetraveldc-web
systemctl --user start corporatetraveldc-poller
systemctl --user start corporatetraveldc-pusher

# Verify
curl http://127.0.0.1:8000/healthz

# Create admin token
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py create \
  --user operator --tier admin --label admin-phone
```

### After any code change

```bash
cd /opt/corporatetraveldc
bash build-images.sh          # rebuilds all images; pass a target name to rebuild one
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web corporatetraveldc-poller \
                              corporatetraveldc-pusher corporatetraveldc-runner
```

---

## Development

All Python commands run from `/opt/corporatetraveldc` with `PYTHONPATH=src`:

```bash
# Run a skill manually (--force bypasses SR-2 hash gate)
PYTHONPATH=src ./venv/bin/python src/poller/skills/cps_recompute.py --force
PYTHONPATH=src ./venv/bin/python src/poller/skills/route_impact.py --force
PYTHONPATH=src ./venv/bin/python src/poller/skills/tfr_enrichment.py --force

# Run a fetcher manually
PYTHONPATH=src ./venv/bin/python src/poller/fetchers/metar.py
PYTHONPATH=src ./venv/bin/python src/poller/fetchers/tfr.py

# Token management
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py list
PYTHONPATH=src ./venv/bin/python src/ctdc_token/cli.py show-cost

# Inspect the database directly
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db \
  "SELECT * FROM cps_scores ORDER BY computed_at DESC LIMIT 3;"

# Run tests
python -m pytest tests/ -x --tb=short
```

### Skill runtime rules

Every automated skill must follow two rules:

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block — always, including on error. Usage is logged to `/var/lib/corporatetraveldc/api-usage.csv`.

**SR-2** (`src/common/sr2_gate.py`): call `hash_gate()` before any expensive computation or LLM call. Hash only content-bearing fields (never timestamps). If gate returns `"skipped"`, call `sys.exit(0)` immediately. Support `--force` flag to bypass.

### Schema migrations

`src/common/db.py` is the single schema authority. Schema is versioned additively (`SCHEMA`, `SCHEMA_V2` … `SCHEMA_V8`). Each version is applied at startup via `init_db_v{N}()`. Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.

---

## Local LLM — Ollama

**This platform is designed to run entirely on local hardware.** No external LLM API key is required. All inference runs on-device via [Ollama](https://ollama.com).

```
dispatch containers
        │
        │  OLLAMA_BASE_URL=http://host.containers.internal:11434
        ▼
  Ollama daemon (host)  ◄──  llama3.2:3b (chat)  +  mistral-nemo (OSINT)
        │
        └─ GPU / CPU inference — no external API calls, no data leaves the machine
```

Two model slots are loaded simultaneously (`OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=24h`).

### Supported models

| Model | Tag | Disk | Min RAM | Best for | Config var |
|---|---|---|---|---|---|
| **Llama 3.2 3B** | `llama3.2:3b` | 2.0 GB | 4 GB | Chat — fast, default | `OLLAMA_CHAT_MODEL` |
| **Mistral-Nemo 12B** | `mistral-nemo` | 7.1 GB | 12 GB | OSINT instruction-following | `OLLAMA_OSINT_MODEL` |
| **Phi 3.5 Mini** | `phi3.5` | 2.2 GB | 4 GB | Ultralight chat — 8 GB Pi | `OLLAMA_CHAT_MODEL` |
| **Llama 3.1 8B** | `llama3.1:8b` | 4.7 GB | 8 GB | Chat upgrade — x86_64 | `OLLAMA_CHAT_MODEL` |
| **Gemma 2 9B** | `gemma2:9b` | 5.5 GB | 10 GB | Reasoning / deep analysis | `OLLAMA_OSINT_MODEL` |
| **Qwen 2.5 7B** | `qwen2.5:7b` | 4.7 GB | 8 GB | Multilingual OSINT | `OLLAMA_OSINT_MODEL` |

### LLM configuration

No code changes are required to swap models — everything is driven by env vars:

```bash
# /etc/corporatetraveldc/dispatch.env
OLLAMA_CHAT_MODEL=llama3.2:3b
OLLAMA_OSINT_MODEL=mistral-nemo

# Lightweight (8 GB RAM / Pi 5 8 GB)
OLLAMA_CHAT_MODEL=phi3.5
OLLAMA_OSINT_MODEL=phi3.5

# Upgrade path (16+ GB / x86_64)
OLLAMA_CHAT_MODEL=llama3.1:8b
OLLAMA_OSINT_MODEL=qwen2.5:7b
```

**Custom operator context** — bake in your own system prompt via Modelfiles:

```bash
cp Modelfile.chat.template Modelfile.chat    # fill in operator context
cp Modelfile.osint.template Modelfile.osint  # fill in operator context
bash build-models.sh                         # creates csexec-chat + csexec-osint
```

Then set `OLLAMA_CHAT_MODEL=csexec-chat` and `OLLAMA_OSINT_MODEL=csexec-osint` in `dispatch.env`.

---

## FAA SWIM / NMS credentials

When FAA NMS credentials are provisioned, add them to `/etc/corporatetraveldc/dispatch-secrets.env`. Six feeds activate automatically:

| Feed | Env vars | Description |
|---|---|---|
| FDPS | `SWIM_NMS_USER_FDPS` / `SWIM_NMS_PASS_FDPS` / `SWIM_NMS_QUEUE_FDPS` | Flight plan + track data |
| STDDS | `SWIM_NMS_USER_STDDS` / `SWIM_NMS_PASS_STDDS` / `SWIM_NMS_QUEUE_STDDS` | Surface + terminal tracks, TFRs |
| TFMS | `SWIM_NMS_USER_TFMS` / `SWIM_NMS_PASS_TFMS` / `SWIM_NMS_QUEUE_TFMS` | NAS programs (GDP, GS, AFP, AAR) |
| AIM | `SWIM_NMS_USER_AIM` / `SWIM_NMS_PASS_AIM` / `SWIM_NMS_QUEUE_AIM` | Digital NOTAMs |
| TBFM | `SWIM_NMS_USER_TBFM` / `SWIM_NMS_PASS_TBFM` / `SWIM_NMS_QUEUE_TBFM` | Arrival sequencing |
| ITWS | `SWIM_NMS_USER_ITWS` / `SWIM_NMS_PASS_ITWS` / `SWIM_NMS_QUEUE_ITWS` | Terminal weather |

To request FAA SWIM credentials, see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — includes the email template and portal link.

No code changes required after credential entry. Rebuild and restart:

```bash
bash build-images.sh
systemctl --user restart corporatetraveldc-ingest
```

---

## Key paths

| Path | Purpose |
|---|---|
| `/opt/corporatetraveldc/src/` | All Python source |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | SQLite database (WAL) |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret platform config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Credentials (mode 0600) |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 skill usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist YAML files |
| `/var/lib/corporatetraveldc/user_rss_feeds.json` | Runner: user-defined Intel Feed subscriptions |

---

## CUI handling

**CRITICAL**: This repository never contains, and must never be modified to contain, actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies — in code, configs, exports, or documents, even password-protected. The infrastructure ships with empty placeholder files. The operator populates credentialed data from authorized sources on the deployment host. The audit log is append-only, 90-day retention, and never leaves the host.

---

## License

Proprietary. CS Executive Services, LLC. All rights reserved.
