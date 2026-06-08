# corporatetraveldc-dispatch

Real-time executive travel intelligence platform for the Washington, DC area. Monitors commercial flights (via FAA SWIM), Amtrak trains, weather, and airspace restrictions — and delivers push alerts the moment something changes. Runs as four rootless Podman containers managed by systemd Quadlets.

---

## Status

| Component | State |
|---|---|
| Web API | `https://dispatch.csexecutiveservices.com` |
| CPS | YELLOW / MARGINAL |
| All containers | Running |
| FAA SWIM NMS push feeds | Pending FAA credential provisioning |
| Anthropic API skills | Pending credits |

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

## Installation

### Prerequisites

- Raspberry Pi 5 running Raspberry Pi OS (Fedora target; currently RPi OS)
- Rootless Podman
- systemd user session enabled for `corporatetraveldc`

### First-time setup

```bash
# Clone the repo
git clone https://github.com/CorporateTravelDC/corporatetraveldc-dispatch.git /opt/corporatetraveldc
cd /opt/corporatetraveldc

# Copy and populate secrets
cp dispatch-secrets.env.example /etc/corporatetraveldc/dispatch-secrets.env
chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
# Edit dispatch-secrets.env and fill in ANTHROPIC_API_KEY, DISPATCH_TOKEN_SECRET, NTFY_TOKEN

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

Every skill that calls the Anthropic API must follow two rules:

**SR-1** (`src/common/sr1_log.py`): call `log_usage()` in a `finally` block — always, including on error. Usage is logged to `/var/lib/corporatetraveldc/api-usage.csv`.

**SR-2** (`src/common/sr2_gate.py`): call `hash_gate()` before the API call. Hash only content-bearing fields (never timestamps). If gate returns `"skipped"`, call `sys.exit(0)` immediately. Support `--force` flag to bypass.

### Schema migrations

`src/common/db.py` is the single schema authority. Schema is versioned additively (`SCHEMA`, `SCHEMA_V2` … `SCHEMA_V8`). Each version is applied at startup via `init_db_v{N}()`. Never drop or rename columns — only `ALTER TABLE ADD COLUMN`.

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
| `/var/lib/corporatetraveldc/api-usage.csv` | SR-1 Anthropic usage log |
| `/var/lib/corporatetraveldc/skill-state/` | SR-2 hash gate state |
| `/run/corporatetraveldc/triggers/` | Admin trigger files |
| `/opt/corporatetraveldc/watchlists/` | Permanent watchlist YAML files |

---

## CUI handling

**CRITICAL**: This repository never contains, and must never be modified to contain, actual SHARES, HEARS, HEART, or any FOUO/CUI radio frequencies — in code, configs, exports, or documents, even password-protected. The infrastructure ships with empty placeholder files. The operator populates credentialed data from authorized sources on the Pi. The audit log is append-only, 90-day retention, and never leaves the Pi.

---

## License

Proprietary. CS Executive Services, LLC. All rights reserved.
