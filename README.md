# corporatetraveldc-dispatch

Real-time executive travel intelligence platform for the Washington, DC area. Monitors commercial flights (via FAA SWIM), Amtrak trains, weather, and airspace restrictions — and delivers push alerts the moment something changes. Runs as four rootless Podman containers managed by systemd Quadlets.

📄 **[Platform Compatibility Reference (PDF)](docs/platform-compatibility.pdf)** — what works (and what doesn't) on Linux, macOS, Windows, Android, and iOS.

---

## Status

| Component | State |
|---|---|
| Web API | `https://dispatch.csexecutiveservices.com` |
| CPS | YELLOW / MARGINAL |
| All containers | Running |
| FAA SWIM NMS push feeds | Pending FAA credential provisioning |
| Local LLM (Ollama) | llama3.2:3b (chat) + mistral (OSINT) — both warm |
| Dispatch Drawer | Streaming chat with per-request model selector |

---

## Architecture

Four containers share a single SQLite database (WAL mode) under the `corporatetraveldc` user:

```
┌─────────────────────────────────────────────────────────────┐
│                    corporatetraveldc                        │
│                                                             │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────┐  │
│  │   web     │  │  poller   │  │  pusher   │  │ ingest  │  │
│  │ FastAPI   │  │ Scheduler │  │  ntfy     │  │  SWIM   │  │
│  │ REST API  │  │ + Skills  │  │  sender   │  │  NWWS   │  │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └────┬────┘  │
│        └──────────────┴──────────────┴──────────────┘       │
│                    SQLite (WAL) shared DB                    │
└─────────────────────────────────────────────────────────────┘
```

### Containers

| Container | Image | Role |
|---|---|---|
| `corporatetraveldc-web` | `localhost/corporatetraveldc-web:latest` | FastAPI REST API, tiered auth |
| `corporatetraveldc-poller` | `localhost/corporatetraveldc-poller:latest` | Async scheduler — fetchers + AI skills |
| `corporatetraveldc-pusher` | `localhost/corporatetraveldc-pusher:latest` | ntfy alert dispatcher |
| `corporatetraveldc-ingest` | `localhost/corporatetraveldc-ingest:latest` | SWIM/NWWS/Amtrak push ingest (pending NMS credentials) |

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
| Amtrak | Push ingest / poller fallback | Push / 5 min | ⚠️ Ingest not yet running |
| FDPS (flight plan + track) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |
| STDDS (surface + terminal) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |
| TFMS (GDP/GS/AFP/AAR) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |
| AIM (digital NOTAMs) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |
| TBFM (arrival sequencing) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |
| ITWS (terminal weather) | FAA SWIM NMS | Push | ⏳ Pending NMS credentials |

### Push/pull failover

The ingest container stamps heartbeats into `feed_state` every 30 seconds. Before each REST poll, the poller checks whether the heartbeat for that feed is fresher than 90 seconds. If so, it skips the REST fetch — ingest owns that feed. When ingest disconnects, the heartbeat ages out and REST polling resumes automatically. No manual intervention required.

---

## API

Base URL: `https://dispatch.csexecutiveservices.com` (Cloudflare Tunnel) or `http://100.94.80.100:8000` (Tailscale direct).

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

The CPS score is a Part 135.609-informed go/no-go assessment for HEMS operations over the DC area. Six factors are evaluated and combined:

| Factor | Source |
|---|---|
| Ceiling | METAR — DCA, IAD, BWI |
| Visibility | METAR |
| Wind | METAR |
| Precipitation | METAR precip_code |
| Airspace | Active TFRs, P-56A/B, DC FRZ/SFRA status |
| GDP | Active NAS ground delay programs |

Output: `GREEN / GO`, `YELLOW / MARGINAL`, `RED / NO-GO`. Computed by `poller/skills/cps_recompute.py` every 60 minutes and on demand via `POST /admin/force-recompute-cps`.

---

## Supported Platforms

> Full detail in **[docs/platform-compatibility.pdf](docs/platform-compatibility.pdf)** — feature matrix, per-platform notes, and package compatibility table.

The server stack (Python services + Ollama) runs on any of the platforms below. The web UI is accessible from any modern browser, including iPhone and iPad via Cloudflare Tunnel or Tailscale.

### Platform support matrix

| Platform | Architecture | Server stack | Containers | Local Ollama | Install script |
|---|---|---|---|---|---|
| **Linux x86_64** | AMD64 | ✅ Full | Podman ✅ | ✅ | `install/install.sh` |
| **Linux ARM64** (Pi 5, SBCs) | aarch64 | ✅ Full | Podman ✅ | ✅ | `install/install.sh` |
| **macOS Apple Silicon** | arm64 | ✅ Full | Podman / Docker ✅ | ✅ | `install/install.sh` |
| **macOS Intel** | x86_64 | ✅ Full | Podman / Docker ✅ | ✅ | `install/install.sh` |
| **Windows x64** | AMD64 | ✅ via WSL2 | Docker Desktop / Podman Desktop | ✅ native | `install/install-windows.ps1` |
| **Android ARM64** (tablet/kiosk) | aarch64 | ✅ bare Python | ❌ (Termux) | ✅ (Termux) | `install/install-android.sh` |
| **iOS / iPadOS** (iPhone, iPad) | arm64 | ❌ (web client only) | ❌ | ❌ | — browse to deployment URL |

### Notes by platform

**Linux (x86_64 / ARM64)** — primary deployment target. Full Podman rootless container stack with systemd Quadlets. Fedora preferred; Debian/Ubuntu/Arch also supported by the installer. The `solace-pubsubplus` SWIM ingest library has prebuilt wheels for x86_64; ARM64 requires a source build.

**macOS (Apple Silicon / Intel)** — full Python stack runs natively; Podman Machine or Docker Desktop provides the container layer. `solace-pubsubplus` (FAA SWIM NMS) is Linux-only — SWIM push feeds require running in a Linux VM or forwarding from a Pi. REST poll fallback covers all feeds automatically.

**Windows x64** — the installer sets up WSL2 (Ubuntu or Fedora) and runs the Linux stack inside it. Ollama installs natively on Windows and is accessible from WSL2 at the host IP. Full container support via Docker Desktop or Podman Desktop.

**Android ARM64 (tablet / kiosk)** — runs via Termux (install from F-Droid). All REST feeds and Ollama work; SWIM push ingest (`solace-pubsubplus`) is not supported. Use `Termux:Boot` for auto-start on reboot. Recommended models for constrained memory: `llama3.2:3b` (2.0 GB) or `phi3.5` (2.2 GB). For a tablet kiosk, point the browser at your Pi's Cloudflare Tunnel URL instead of running the server locally.

**iOS / iPadOS (iPhone, iPad)** — no server-side install supported. Browse to `https://dispatch.csexecutiveservices.com` from Safari (or your Cloudflare Tunnel URL). Add to Home Screen for a PWA-like experience. The SSE event stream (`/api/v1/events`) works in Safari for live updates.

### Python package compatibility

| Package | Linux x86_64 | Linux ARM64 | macOS | Windows (WSL2) | Android (Termux) |
|---|---|---|---|---|---|
| `fastapi` / `uvicorn` / `pydantic` | ✅ wheel | ✅ wheel | ✅ wheel | ✅ wheel | ✅ wheel |
| `requests` / `httpx` / `slixmpp` | ✅ pure | ✅ pure | ✅ pure | ✅ pure | ✅ pure |
| `lxml` | ✅ wheel | ✅ wheel | ✅ wheel | ✅ wheel | ⚠ source build |
| `solace-pubsubplus` (SWIM ingest) | ✅ wheel | ⚠ source build | ❌ Linux-only | ✅ in WSL2 | ❌ not supported |

Packages marked ⚠ require build tools (`gcc`, `libxml2-dev`) but complete successfully with the installer.

---

## Installation

### Prerequisites

- Raspberry Pi 5 running Raspberry Pi OS (Fedora target; currently RPi OS)
- Rootless Podman
- systemd user session enabled for `corporatetraveldc`

### First-time setup

**Quick install (Linux / macOS):**

```bash
curl -fsSL https://raw.githubusercontent.com/CorporateTravelDC/corporatetraveldc-dispatch/main/install/install.sh | bash
# Windows: run install\install-windows.ps1 as Administrator in PowerShell
# Android: run install/install-android.sh inside Termux
```

**Manual install:**

```bash
# Clone the repo
git clone https://github.com/CorporateTravelDC/corporatetraveldc-dispatch.git /opt/corporatetraveldc
cd /opt/corporatetraveldc

# Copy and populate secrets
cp dispatch-secrets.env.example /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# Edit dispatch-secrets.env — add FAA_NOTAM_API_KEY, NTFY_TOKEN, and any data source credentials
# No LLM API key required — all inference runs locally via Ollama

# Build all four images
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
  --user corey --tier admin --label admin-iphone
```

### After any code change

```bash
cd /opt/corporatetraveldc
bash build-images.sh
systemctl --user daemon-reload
systemctl --user restart corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher
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

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block — always, including on error. Usage is logged to `/var/lib/corporatetraveldc/api-usage.csv`. The `model` field reflects the resolved Ollama model (or `"deterministic"` for skills that don't call an LLM).

**SR-2** (`src/common/sr2_gate.py`): call `hash_gate()` before any expensive computation or LLM call. Hash only content-bearing fields (never timestamps). If gate returns `"skipped"`, call `sys.exit(0)` immediately. Support `--force` flag to bypass.

### Schema migrations

`src/common/db.py` is the single schema authority. Schema is versioned additively (`SCHEMA`, `SCHEMA_V2` … `SCHEMA_V8`). Each version is applied at startup via `init_db_v{N}()`. Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.

---

## Local LLM — Ollama

**This platform is designed to run entirely on local hardware.** No external LLM API key is required — all inference runs on-device via [Ollama](https://ollama.com). The only credentials in `dispatch-secrets.env` are for data sources (FAA, Amtrak, ntfy), not for AI providers.

### Design

```
dispatch containers
        │
        │  OLLAMA_BASE_URL=http://host.containers.internal:11434
        ▼
  Ollama daemon (host)  ◄──  llama3.2:3b (chat)  +  mistral (OSINT)
        │
        └─ GPU / CPU inference — no external API calls, no data leaves the machine
```

Two model slots are loaded simultaneously (`OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=24h`). The `ollama-warmup.service` systemd oneshot fires a 1-token probe for each model on every boot so they are in RAM before the first real request.

### Supported models

| Model | Tag | Disk | Min RAM | Best for | Config var |
|---|---|---|---|---|---|
| **Llama 3.2 3B** | `llama3.2:3b` | 2.0 GB | 4 GB | Chat — fast, default | `OLLAMA_CHAT_MODEL` |
| **Mistral 7B** | `mistral` | 4.1 GB | 8 GB | OSINT instruction-following, default | `OLLAMA_OSINT_MODEL` |
| **Phi 3.5 Mini** | `phi3.5` | 2.2 GB | 4 GB | Ultralight chat — Pi 5 8 GB, tablet/kiosk | `OLLAMA_CHAT_MODEL` |
| **Llama 3.1 8B** | `llama3.1:8b` | 4.7 GB | 8 GB | Chat upgrade — x86_64 or high-RAM ARM64 | `OLLAMA_CHAT_MODEL` |
| **Gemma 2 9B** | `gemma2:9b` | 5.5 GB | 10 GB | Reasoning / deep analysis — x86_64 preferred | `OLLAMA_OSINT_MODEL` |
| **Qwen 2.5 7B** | `qwen2.5:7b` | 4.7 GB | 8 GB | Multilingual OSINT — x86_64 preferred | `OLLAMA_OSINT_MODEL` |

Default deployment (Pi 5 16 GB) uses `llama3.2:3b` + `mistral`. Swap to lighter models (`phi3.5` for both) on an 8 GB Pi or Android tablet.

### LLM configuration — which files

No code changes are required to swap models. Everything is driven by env vars:

```
/etc/corporatetraveldc/dispatch.env          ← model names; edit freely, non-secret
/etc/corporatetraveldc/dispatch-secrets.env  ← data source credentials only (not LLM keys)
/opt/corporatetraveldc/Modelfile.chat        ← operator system prompt for chat model (.gitignored)
/opt/corporatetraveldc/Modelfile.osint       ← operator system prompt for OSINT model (.gitignored)
```

**To switch models** — edit `dispatch.env`:

```bash
# Lightweight setup (8 GB RAM, tablet/kiosk)
OLLAMA_CHAT_MODEL=phi3.5
OLLAMA_OSINT_MODEL=phi3.5

# Upgrade path (16+ GB RAM, x86_64)
OLLAMA_CHAT_MODEL=llama3.1:8b
OLLAMA_OSINT_MODEL=qwen2.5:7b
```

Then rebuild and restart: `bash build-images.sh && systemctl --user restart corporatetraveldc-{web,poller}`

**Custom operator context** — each deployment can bake in its own system prompt via Modelfiles (`.gitignored`; copy from `.template` files in the repo root):

```bash
cp Modelfile.chat.template Modelfile.chat    # fill in operator context
cp Modelfile.osint.template Modelfile.osint  # fill in operator context
bash build-models.sh                         # creates csexec-chat + csexec-osint
```

Then set `OLLAMA_CHAT_MODEL=csexec-chat` and `OLLAMA_OSINT_MODEL=csexec-osint` in `dispatch.env`.

### Cloud LLM alternatives (optional)

The platform is fully self-contained without cloud APIs. If you want to wire in a cloud provider as an optional fallback, add its key to `dispatch-secrets.env` and update the relevant skill's `OLLAMA_BASE_URL` / model env var to point at the provider's OpenAI-compatible endpoint. No official cloud provider is integrated by default.

### Context-switched routing

- **Chat queries** (Dispatch Drawer `/api/ask`) → `OLLAMA_CHAT_MODEL` — low latency, warm in RAM
- **OSINT narratives** (`osint_monitor` skill) → `OLLAMA_OSINT_MODEL` — instruction-following for EP/marketing output

### Operator model override

In the Dispatch Drawer, click the model badge in the header to select a model for the session, or type `/model <name>` in chat. To revert: click badge → Reset, or type `/model reset`. The server echoes the resolved model in an SSE `model_info` event and the `X-Dispatch-Model` response header; the drawer labels each assistant reply with the model that serviced it.

Alternatively, supply `"model": "<name>"` in the `POST /api/ask` JSON body for a single-request override.

### Network security

Ollama binds to `0.0.0.0:11434` but is firewalled to Tailscale (`tailscale0`) and loopback. Rules are persisted in `/etc/iptables/rules.v4` and restored at boot by `iptables-restore.service`. Containers reach the host via `host.containers.internal:11434`.

### Install / re-pull models

```bash
# Check what's present
ollama list

# Pull (run as corporatetraveldc)
ollama pull llama3.2:3b
ollama pull mistral

# Or pull any supported model from the table above
ollama pull phi3.5       # lightweight, 4 GB RAM
ollama pull llama3.1:8b  # chat upgrade
ollama pull gemma2:9b    # reasoning
ollama pull qwen2.5:7b   # multilingual OSINT

# Verify both warm models are loaded in RAM
curl http://127.0.0.1:11434/api/ps
```

---

## FAA SWIM / NMS credentials

When FAA NMS credentials are provisioned, add them to `/etc/corporatetraveldc/dispatch-secrets.env` following the template in `dispatch-secrets.env.example`. Six feeds activate automatically:

| Feed | Env vars | Description |
|---|---|---|
| FDPS | `SWIM_NMS_USER_FDPS` / `SWIM_NMS_PASS_FDPS` / `SWIM_NMS_QUEUE_FDPS` | Flight plan + track data |
| STDDS | `SWIM_NMS_USER_STDDS` / `SWIM_NMS_PASS_STDDS` / `SWIM_NMS_QUEUE_STDDS` | Surface + terminal tracks, TFRs |
| TFMS | `SWIM_NMS_USER_TFMS` / `SWIM_NMS_PASS_TFMS` / `SWIM_NMS_QUEUE_TFMS` | NAS programs (GDP, GS, AFP, AAR) |
| AIM | `SWIM_NMS_USER_AIM` / `SWIM_NMS_PASS_AIM` / `SWIM_NMS_QUEUE_AIM` | Digital NOTAMs |
| TBFM | `SWIM_NMS_USER_TBFM` / `SWIM_NMS_PASS_TBFM` / `SWIM_NMS_QUEUE_TBFM` | Arrival sequencing / meter fix ETAs |
| ITWS | `SWIM_NMS_USER_ITWS` / `SWIM_NMS_PASS_ITWS` / `SWIM_NMS_QUEUE_ITWS` | Terminal weather (precip, wind shear, microburst) |

No code changes required after credential entry. Rebuild and restart the ingest container:

```bash
bash build-images.sh
systemctl --user restart corporatetraveldc-ingest
```

---

## Key paths

| Path | Purpose |
|---|---|
| `/opt/corporatetraveldc/src/` | All Python source |
| `/var/lib/corporatetraveldc/corporatetraveldc.db` | SQLite database (WAL, ~432 KB) |
| `/etc/corporatetraveldc/dispatch.env` | Non-secret platform config |
| `/etc/corporatetraveldc/dispatch-secrets.env` | Credentials (mode 0600) |
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 skill usage log (model + token counts) |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist YAML files |

---

## CUI handling

**CRITICAL**: This repository never contains, and must never be modified to contain, actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies — in code, configs, exports, or documents, even password-protected. The infrastructure ships with empty placeholder files. The operator populates credentialed data from authorized sources on the Pi. The audit log is append-only, 90-day retention, and never leaves the Pi.

---

## License

Proprietary. CS Executive Services, LLC. All rights reserved.
