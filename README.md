# Corporate Travel Dispatch Intelligence (CTDI)

**Documentation snapshot: 2026-08-23** — factual claims below were verified
against the running system and current source on this date (previous full
verification 2026-08-11, partial reconciliation 2026-08-19).

Multi-region real-time travel intelligence platform. Monitors commercial
aviation (FAA SWIM push feeds plus REST fallbacks), rail, weather, and airspace
restrictions — delivering push alerts the moment something operationally
relevant changes. Runs as rootless Podman containers managed by systemd
Quadlets under a single deployment user, alongside timer-driven skill
containers, a local SDR receive stack, and host-local Ollama LLM inference.
Container/unit counts drift as feeds and skills are added, so this README does
not pin them — check the live picture instead:

```bash
systemctl --user list-units 'corporatetraveldc-*' --all --no-legend | wc -l
podman ps -a --format '{{.Names}}' | wc -l
ls .config/containers/systemd/*.container | wc -l
```

(As of 2026-08-23 14:0x EDT those returned 122 loaded units, 39 containers and
63 `.container` Quadlets in the repo — 64 are installed live, the extra being
`corporatetraveldc-ccw-demo.container`, a client-preview service that is
running but deliberately not tracked here; see `docs/INFRA_MAP.md`. Re-run the
commands rather than trusting any number written here. The container count is
especially volatile in *both* directions, for two independent reasons:
Quadlet-managed containers are *removed*, not just stopped, when their unit
stops, so a thermal LOCKDOWN or tier-1 shed makes even `podman ps -a` shrink
(the same command read 30 during a shed earlier the same morning); and the
timer-triggered **skill** containers are short-lived oneshots that exist only
while they run, so a moment when several `*-daily-watch` / brief / digest
timers overlap pushes the count *above* the long-running baseline — 34 with
the long-running stack up and no skills firing, 39 with five skill containers
mid-run. Neither direction is a fault.)

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

## Status (2026-08-23)

| Component | State |
|---|---|
| Ops dashboard (runner app) | **Tailnet-only.** `http://100.x.x.x:8001` or `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net` (nginx → :8001). The former public `ops.example.com` hostname was **retired 2026-08-02** and is hard-404'd by hostname in `src/runner/main.py` (`_RETIRED_HOSTNAMES`). |
| Public demo (runner, demo-playback) | **DOWN — crash-looping since 2026-08-15, and the password gate was never actually enabled.** `corporatetraveldc-runner-demo` exits on startup (`sqlite3.OperationalError: unable to open database file` — the 2026-08-14 F6 mount change removed `/var/lib/corporatetraveldc` from this container), auto-restarts continuously (NRestarts in the tens of thousands), `:8005` refuses connections, and `https://dispatch-runner.example.com` returns 502. `DEMO_MODE` is set **nowhere** (defaults `false`), so the password gate, signal sanitization, and ntfy suppression are all inert — the crash loop is the only thing keeping the surface dark. **Operator decision needed before fixing**: see CLAUDE.md → "Core containers". (Operator directive 2026-08-20: the intentionally-public-demo-on-sanitized-data exposure model is accepted, but `DEMO_MODE` must be set explicitly either way.) |
| Web API (browser / programmatic) | `https://dispatch.example.com` (Cloudflare Access gated; nginx stamps `X-CTDI-Public: 1`, which pins the request to Tier 0 regardless of token) |
| Tailscale direct API | `http://100.x.x.x:8000` |
| Public MCP (OpenAPI bridge) | **Retired 2026-08-18.** `mcpo`/`mcpo-public` units are gone (`systemctl --user list-units 'corporatetraveldc-mcpo*' --all` → 0 units); ports 8082/8083 refuse connections; the server checkout was renamed to `/home/corporatetraveldc/mcp/dispatch-mcp.archived-20260817`. The `mcp.example.com` nginx vhost still exists and proxies to the now-gone `:8083`, so the hostname currently returns **502** — removing that vhost is still pending. Restoring the bridge would mean un-archiving the checkout, reinstating the `mcpo`/`mcpo-public` Quadlets, and re-pointing the vhost. |
| FAA SWIM NMS push feeds | ✅ All 6 provisioned and credentialed (FDPS/STDDS/TFMS/TBFM/ITWS/FNS) — provisioned 2026-07-20, split into per-feed containers 2026-07-26. **Not continuously running by design:** `scripts/thermal-ingest-guard.py` sheds SWIM ingest containers under CPU-load/thermal pressure (see "SWIM feed liveness and thermal load-shedding"). |
| Local LLM (Ollama) | 21 dedicated `corporatetraveldc-pi5-*` Modelfile models, **all `FROM phi3:mini`** as of the 2026-08-15/16 rebuild — gemma is fully removed from every running system (see Local LLM section) |
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
| `corporatetraveldc-runner-demo` | same runner image | Demo-playback instance (port 8005 → container 8001), intended to read the demo API (:8004) instead of live feeds — public vhost at `dispatch-runner.example.com`. **Currently crash-looping (see Status), and `DEMO_MODE` is set nowhere** — the Quadlet does not set it, so every demo-mode protection is inert. Do not "fix" the crash without reading CLAUDE.md → "Core containers" first. |
| `corporatetraveldc-demo` / `corporatetraveldc-demo-api` | `localhost/corporatetraveldc-demo:latest` | Archive recorder / read-only playback API (port 8004) over `demo.db` |

### Auxiliary containers (same host)

SDR/RF: `ultrafeeder` (ADS-B + tar1090, port 8080 — restored 2026-08-11, see
Status), `acarsrouter` (:9080), `acarshub` (:9081), `dumpvdl2`,
`acars-watcher` (UDP 5005), plus aggregator feeders `piaware`, `fr24feed`
(:8754), `planefinder` (:30053), `airnavradar`. Disabled pending hardware:
`acarsdec`, `dumphfdl`, `ais`/`ais-catcher`, `ais-watcher` — these exist only
as staged `*.container.disabled` files under `systemd/`,
`systemd/quadlets/` and (for `acarsdec`) `.config/containers/systemd/`. None
is installed in `~/.config/containers/systemd/`, so they have **no systemd
unit at all** — `systemctl --user list-unit-files` matching any of those names
returns 0, which is expected, not a fault. See `docs/SDR_SERVICES.md`.

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
| Amtrak | Push-primary in `ingest-core` (api.amtraker.com) — **no working fallback**: `poller/fetchers/amtrak.py` exists but is wired into no schedule (dead code), so `ingest-core`'s poll loop is the *only* live train-data path | Push | ✅ Active (single-path — see CLAUDE.md "Known bad") |
| FDPS (flight plan + track, FIXM 3.0) | FAA SWIM NMS | Push | ✅ Live (LOCKDOWN-only shed †) |
| STDDS (surface + terminal tracks) | FAA SWIM NMS | Push | ✅ Live — carries no TFR data (temp tier-1 shed candidate †) |
| TFMS (GDP/GS/AFP/restrictions/per-flight TMI) | FAA SWIM NMS | Push | ✅ Live (temp tier-1 shed candidate †) |
| AIM/FNS (digital NOTAMs) | FAA SWIM NMS | Push | ✅ Live (LOCKDOWN-only shed † — **no longer "never shed"**, see the 2026-08-23 redesign) |
| TBFM (arrival sequencing) | FAA SWIM NMS | Push | ✅ Live (LOCKDOWN-only shed †) |
| ITWS (terminal weather) | FAA SWIM NMS | Push | ✅ Live (LOCKDOWN-only shed †) |
| NWWS-OI (NWS push) | NWWS-OI XMPP MUC | Push | ✅ Live |
| EUROCONTROL NM B2B | EUROCONTROL | 15 min | ⚠️ Needs credentials — code ships ready |
| JASDAT (Japan) | JCAB/MLIT | 15 min | ⚠️ Needs credentials — code ships ready |

### † SWIM feed liveness and thermal load-shedding

"Live" above means **provisioned, credentialed, and eligible to run** — not
"running continuously". All six `SWIM_NMS_{HOST,USER,PASS,QUEUE}_{FDPS,STDDS,
TFMS,AIM,TBFM,ITWS}` credential sets are present in
`/etc/corporatetraveldc/dispatch-secrets.env`, but `scripts/thermal-ingest-guard.py`
(2-minute timer) **stops SWIM ingest containers — and, under LOCKDOWN, the
entire dispatch stack except `web` — under CPU-load / thermal pressure, and
starts them again when the box recovers**. This is designed behaviour, not a
fault:

**Redesigned 2026-08-23 by operator directive — the two-tier load ladder this
README used to describe (tier 1 `load1 >= 10`, tier 2 `load1 >= 14`, resume
`load1 < 6.0`) is gone.** Every real trip on record had been load-driven, never
temperature-driven (peak temp across the guard's whole journal history was
~71 °C, under the 74 °C line, with an independent auto-ramping PWM fan
regulating underneath), and the old 6.0 resume bar sat *inside* normal load
noise — so load and temperature are no longer symmetric. Temperature keeps its
original two-stage trigger as a backstop; load collapses to a single
informational/LOCKDOWN split:

| Trip | Condition | What's shed |
|---|---|---|
| Temp tier 1 (mild) | `temp >= 74.0 °C` | `tfms`, `stdds` only |
| **LOCKDOWN** | `temp >= 79.0 °C` **or** `load1 >= 40.0` **or** `>= 2` load-attributed brief fallbacks in 300 s | **the entire stack except `web`**, immediately, no partial stage: all 6 SWIM feeds (`fdps,stdds,tfms,tbfm,itws,notam`), `ingest-core`, `poller`, `pusher`, `runner`, and host `ollama.service` |
| Informational only, no shed | `temp` 70–74 °C, or `load1` 15–40 | — |
| Restore | `temp < 65 °C` **and** `load1 < 15.0` **and** fallback count `< 2`, held 300 s | tier 1 restores `tfms,stdds`; LOCKDOWN restores the whole stack |

Note the two consequences a reader of the old table would get wrong: the
`notam` container (which runs the AIM/**FNS** feed, a real 6th SWIM feed — not a
NOTAM-only afterthought) is now shed under LOCKDOWN, where it previously never
was; and LOCKDOWN stops `poller`/`pusher`/`runner`/`ollama.service` too, so a
stopped core container is no longer automatically a fault either. `web` is the
only thing guaranteed to survive.

"Load-attributed brief fallback" is a signal from `src/common/llm.py`
(`_record_load_fallback()` → `/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl`),
logged only for `OllamaBusyError` or a generate-call `httpx.TimeoutException`
— deliberately *not* for `httpx.ConnectError`, so a deliberately-stopped Ollama
can never cause ingest to shed itself.

A real LOCKDOWN fired and fully restored on 2026-08-23; verify current state
from the guard's own journal and state file rather than from this table.

**Consequences for anyone reading unit state:**

- Finding `corporatetraveldc-ingest-<feed>.service` `inactive (dead)` with
  `Result=success` is **expected** and is not something to "fix" by restarting
  it — the guard will start it again on its own, and a manual start just gets
  shed again on the next 2-minute pass. Under LOCKDOWN the same is true of
  `poller`, `pusher`, `runner`, and host `ollama.service`.
- These sheds are **silent to `systemctl list-units` failure greps**, because
  the units exit 0.
- The authoritative check is the guard's own state, not `systemctl`:

```bash
cat /var/lib/corporatetraveldc/thermal_ingest_guard_state.json
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "24 hours ago"
```

Thresholds are overridable via `THERMAL_GUARD_*` in `dispatch.env` (defaults
listed in the script header).

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
| Public demo | `https://dispatch-runner.example.com` | **Currently 502** (runner-demo crash loop, see Status); password gate not enabled (`DEMO_MODE` unset) |
| ~~Public MCP bridge~~ | ~~`https://mcp.example.com`~~ | **Retired 2026-08-18** — mcpo/mcpo-public units removed, ports 8082/8083 refuse connections, server checkout archived at `/home/corporatetraveldc/mcp/dispatch-mcp.archived-20260817`. The nginx vhost still exists and proxies to the dead `:8083`, so the hostname returns **502**; vhost removal is still pending. |

### Tier 0 — Anonymous (selection)

`src/web/main.py` declares **57** `@app.get()` routes across all tiers
(re-count with `grep -c '^@app\.get(' src/web/main.py` — this grows; an
earlier revision said "~50"). Of those, 37 are anonymous, 10 carry a
`require_tier` dependency (T1/T2) and 10 carry `require_admin` (re-derived
2026-08-23 by AST-classifying every `@app.get` decorator + signature, not by
grepping mention counts); the
`src/web/routes/*.py` modules add further routes on top. The table below is a
selection of the Tier-0 subset, not an exhaustive list.

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
RSS poller): **11 built-in categories, 32 built-in feeds** (re-verified live
2026-08-23; the catalog grows, so re-count with
`PYTHONPATH=src python3 -c "import shared.rss_catalog as r; print(len(r._RSS_CATALOG), sum(len(v) for v in r._RSS_CATALOG.values()), len(r.all_feed_urls()))"`
— which also reports the whole pool including operator-added feeds, **34**
unique URLs as of 2026-08-23) — `corporate_intel`,
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
Admin  → bearer token tier=admin (all /admin/* endpoints except one
         deliberate exception, below)
```

**One `/admin/*` endpoint is unauthenticated by design.**
`GET /admin/approval-requests/{request_id}/resolve` (`src/web/main.py:2373`)
carries no auth dependency — Cloudflare strips `Authorization` through the
tunnel, so a token-gated resolve link would be untappable from a phone off
the tailnet. Security rests on the UUID4 request id plus single-use
enforcement in `resolve_approval_request()`. Verified live 2026-08-23: an
unauthenticated request to that path returns an app-level `404` (reaches
the handler), while `/admin/healthz` returns `403`. See
`docs/COMPLIANCE_SECURITY.md`.

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
retention on <500 MB). The demo-playback stack:

- `corporatetraveldc-demo-api.service` — read-only playback API, port 8004
- `corporatetraveldc-runner-demo.service` — second runner instance, port 8005,
  `DISPATCH_BASE_URL=http://100.x.x.x:8004`. **Down since 2026-08-15**
  (startup crash loop — see Status) — and `DEMO_MODE` is **not set anywhere**
  (defaults `false`), so the app-layer protections below are configured in
  code but not active.
- Public hostname: **`https://dispatch-runner.example.com`** —
  currently returns 502. When the instance runs *with* `DEMO_MODE=true`, it
  is password-gated (`POST /api/demo/login`, HMAC-signed `ctdc_demo_session`
  cookie, 8 h default) with signals sanitized server-side; fixing the crash
  without setting `DEMO_MODE=true` would put the surface live **ungated**
  (operator decision pending — see CLAUDE.md).

Seed readiness: `GET /api/v1/demo/readiness` reports per-tier
(2w/8w/12w/24w/36w/52w) archive readiness. Config
(`DEMO_RECORDER_INTERVAL=300`, `DEMO_RECORDER_RETENTION=364`,
`DEMO_RECORDER_SEED_TARGET=14`) — these values are correct, but they are **not
set in `dispatch.env`**; they exist only as `os.environ.get` defaults in
`src/demo/recorder.py:41-43` (alongside `DEMO_RECORDER_API_BASE`, and
`DEMO_RECORDER_API_TOKEN` which *is* read from
`/etc/corporatetraveldc/dispatch-secrets.env`). To change one, either add it to
`dispatch.env` (nothing reads it from there today) or edit the default in
`recorder.py`.

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
# AND set ULTRAFEEDER_LAT / ULTRAFEEDER_LON — see the note below

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

**If you're self-hosting a real ADS-B receiver, hard-code your actual GPS
coordinates — don't run on the placeholder.** Set `ULTRAFEEDER_LAT`/
`ULTRAFEEDER_LON` in `dispatch-secrets.env` (see the template's own note)
to your antenna's real position, and set the UltraFeeder Quadlet's
`READSB_LAT`/`READSB_LON`/`TAR1090_DEFAULTCENTERLAT`/
`TAR1090_DEFAULTCENTERLON` to match. Left unset, the platform still runs —
it silently falls back to a generic Washington-DC-area placeholder — but
every distance-from-you calculation, the compass summary, the tactical
map's range rings, and the ADS-B embed's native "H"/home key and per-
aircraft distance columns will all be wrong, and MLAT positioning accuracy
depends on the receiver's own site position being correct, not just the
display. `runner`'s `/api/v1/frontend-config` endpoint is the one place
the frontend reads this from — if you're extending the UI, read from
there rather than hardcoding a literal into a `.jsx`/`.js` file (see
`docs/GPS_COORDINATE_CONFIGURATION.md` for the incident that made this a
documented rule instead of an assumption).

Note on signed-manifest integrity (scope verified 2026-08-19): the
`verified-exec.sh` gate covers the timer-triggered **skill** quadlets,
`src/common/llm.py` before every inference, and the 15-minute
`corporatetraveldc-integrity-sweep` timer. The long-running core containers
(web/poller/pusher/ingest/runner) do **not** run the check at startup — but a
stale manifest still blocks every skill run and inference, so re-sign
(`scripts/sign-manifest.sh`) after any code change, **before** rebuilding
images: web/poller bake `MANIFEST.sha256` into the image, and building
against an unsigned tree ships a permanently-mismatched manifest (correct
order is **sign → build → restart**).

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
(`SCHEMA`, `SCHEMA_V2`, … — check the file for the current top version with
`grep -oE 'SCHEMA_V[0-9]+' src/common/db.py | sort -u -V | tail -1`; **V36** is
the top as of 2026-08-23 — this number moves fast, re-run the grep rather
than trusting it). Never drop or rename columns — only
`ALTER TABLE ADD COLUMN`.

---

## Local LLM — Ollama

**All inference is local.** No external LLM API key is required. Ollama runs
on the host, bound to the tailnet IP (`OLLAMA_BASE_URL=http://100.x.x.x:11434`),
CPU-only — effectively **one model resident at a time** on this hardware.

> **Update 2026-08-19 (verified live 2026-08-23):** the resource drop-in
> `systemd/ollama.service.d/20-resource-limits.conf` — which a 2026-08-19
> morning audit found had never been installed — **was installed the same day**
> (copied to `/etc/systemd/system/ollama.service.d/`, daemon-reload, restart).
> Live `ollama.service` now runs governed: `CPUWeight=500`, `CPUQuota=300%`,
> `MemoryLow=4850M` / `MemoryHigh=6050M` / `MemoryMax=7250M`,
> `MemorySwapMax=0`, `OLLAMA_KEEP_ALIVE=10m`, `LLAMA_ARG_CACHE_RAM=0`. The
> drop-in's own acceptance test is confirmed: since the governed restart,
> every model load logs `prompt cache is disabled` (previously
> `prompt cache is enabled, size limit: 8192 MiB`). `OLLAMA_MAX_LOADED_MODELS`
> remains unset (auto) — single-model residency is still emergent from memory
> pressure, not enforced.

### Dedicated per-task models (since 2026-08-02)

Each LLM-calling skill has its own Ollama model built from a Modelfile at repo
root (`corporatetraveldc.<task>` → `corporatetraveldc-pi5-<task>:latest`) that
bakes the task's system prompt in as the model default. **21 models exist, all
`FROM phi3:mini`** (verified 2026-08-19 with
`grep -h '^FROM' corporatetraveldc.* | sort | uniq -c` → `21 FROM phi3:mini`,
cross-checked against `ollama list`):

| Base | Models |
|---|---|
| **`phi3:mini`** (every model) | `ops-brief`, `ops-brief-trend`, `ep-advance`, `ep-advance-trend`, `chat`, `osint-monitor`, `tfr-enrichment`, `route-impact`, `weekly-summary`, `transport-digest`, `disruption-weather-digest`, `dispatch-desk-memo`, `secondbrain-daily`, `secondbrain-weekly`, `aam-daily-watch`, `aam-weekly-watch`, `aviation-daily-watch`, `concierge-travel-daily-watch`, `executive-protection-daily-watch`, `gig-economy-daily-watch`, `trains-yachts-daily-watch` |

> **This section was stale until 2026-08-19.** README's previous revision was
> commit `ff5e005` (2026-08-14), *"Consolidate 16 brief models into one shared
> model + centralized persona"* — **that change was reversed the next day and is
> dead.** A future reader must not reinstate it from that commit message. Three
> models this section used to name (`osint`, `dispatch-desk`, `aam-watch`) do
> not exist; the live equivalents are `osint-monitor`, `dispatch-desk-memo`,
> and per-domain `*-daily-watch` / `aam-weekly-watch` models. Likewise
> `corporatetraveldc.dispatch-persona` **no longer exists as a file** — since
> the 2026-08-15/16 rebuild the dispatcher persona is baked into each per-skill
> Modelfile's own `SYSTEM` line rather than injected centrally. Gemma is fully
> removed from every running system.
>
> 20 of the 21 models are brief-class (guarded candidate/smoke/promote build);
> only `chat` — the interactive path, which has its own `num_predict` cap — is
> exempt.

**Why phi3:mini for the brief class (2026-08-10/11):** gemma3's Sliding
Window Attention defeats llama.cpp's KV-cache reuse, forcing full prompt
re-processing on the hourly briefs' long prompts — blowing the 240 s
`OLLAMA_TIMEOUT` and driving near-100% deterministic fallback.
`build-models.sh` now has:

- a hard **`SWA_DENYLIST_REGEX`** guard (`^FROM[[:space:]]+(gemma3|gemma2)…`)
  that refuses to build any brief-class model from a gemma2/gemma3 base
  (override: `BRIEF_BASE_OVERRIDE=1`), and
- a **smoke-test promotion gate**: brief models build as `:candidate`, must
  produce a real, non-empty generation for a 125%-of-worst-case prompt within
  `SMOKE_BUDGET_S`, and only then get promoted to `:latest` — a failed
  candidate is deleted and last-known-good stays live.

  The budget is **900 s** by default, not the 200 s this README used to claim
  (`build-models.sh:125` → `SMOKE_BUDGET_S="${BRIEF_SMOKE_BUDGET_S:-900}"`).
  The operator deliberately changed the philosophy on 2026-08-13 from a tight
  build-time budget to a generous one, moving the real gate to the runtime-side
  load-phase timeout (`OLLAMA_LOAD_TIMEOUT`). Don't tighten it back down
  without reading that rationale in `build-models.sh` first.

**Orphaned-generation fix (2026-08-11):** a client-side timeout does *not*
stop Ollama's server-side generation. `src/common/llm.py` now sends
`_abandon_ollama_generation()` — a `keep_alive: 0` unload signal — the moment
a caller's request hits a transport error/timeout (this previously piled up
orphaned `llama-server` generations to a 52 load average). The same fix is in
`build-models.sh`'s smoke test.

Guard 3: `corporatetraveldc-brief-fallback-monitor.timer` (hourly) alerts
loudly if briefs degrade to deterministic fallback anyway.

Cloud fallback: **closed on this box — zero cloud calls.**
`/etc/corporatetraveldc/dispatch.env` sets `ANTHROPIC_FALLBACK_ENABLED=false`
(since 2026-08-12; the `true` default only applies when the var is unset,
which it is not here — every skill container loads that env file). Brief
skills additionally pass `allow_anthropic=False` as a second, narrower
opt-out. With Ollama unavailable, skills fall back to deterministic
templates; `brief-fallback-monitor` (hourly) alerts when that happens.
Design history: `docs/DEDICATED_MODELS_PLAN.md`.

### Rebuilding models

```bash
bash build-models.sh          # verifies signed manifest, applies guards, builds all 21
```

> **Correction 2026-08-23 — model prewarming is retired; this README claimed
> otherwise.** There are **no** `corporatetraveldc-ollama-prewarm-*` units
> anywhere on this box or in this repo's live unit directory
> (`systemctl --user list-unit-files 'corporatetraveldc-ollama-prewarm*'` → 0;
> `ls ~/.config/systemd/user/ | grep -c prewarm` → 0). The original single
> prewarm unit was retired 2026-08-03 and the 28 per-skill prewarm
> `.service`/`.timer` files were retired 2026-08-14 — all of them now sit in
> `.config/systemd/user/retired-20260803/` and `retired-20260814/`. Skills
> therefore pay the cold model-load cost themselves, bounded by
> `OLLAMA_LOAD_TIMEOUT`, which is exactly why the load-phase timeout (not the
> build-time smoke budget) is the real runtime gate — see the
> `SMOKE_BUDGET_S` note above. `scripts/ollama-prewarm.sh` still exists but is
> **orphaned**: no live unit references it
> (`grep -rIl ollama-prewarm ~/.config/systemd/user/` → 0 files).

---

## FAA SWIM / NMS credentials

All six feeds are provisioned and credentialed (all
`SWIM_NMS_{HOST,USER,PASS,QUEUE}_<KEY>` sets present). Note that provisioned
does not mean permanently running: `thermal-ingest-guard` sheds SWIM ingest
containers under load — see "† SWIM feed liveness and thermal load-shedding"
above before diagnosing a quiet feed as a credential problem. Reference for
rotation/re-provisioning
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

**Business Source License 1.1** (source-available, not OSI-approved open
source). Full text: [`LICENSE`](LICENSE). Summary, not a substitute for the
actual terms:

- Free for non-production use (evaluation, development, testing) always.
- Free for production use as a personal self-hosted deployment, or as an
  internal relay/middleware layer within an organization of any size,
  **provided** that use never serves a fee-based product or service
  rendered to a third-party client or customer (see the Additional Use
  Grant in `LICENSE` for the exact boundary, including the white-label,
  hosted-service, and platform-absorption carve-outs).
- Any other production use -- reselling as a hosted/managed service,
  white-labeling, embedding it as a component of another commercial
  platform, or using it (even invisibly) in the course of any fee-based
  service to a client -- requires a commercial license from [operator LLC abbreviation]utive
  Services, LLC.
- Each release converts automatically to GPL v3-or-later four years after
  its first public distribution (per-version Change Date; see `LICENSE`),
  so the platform becomes fully open source over time rather than staying
  locked up indefinitely.

> **Status: the Additional Use Grant language in `LICENSE` is a working
> draft and is currently under legal review.** It reflects the intended
> terms but has not yet been confirmed by counsel. Do not treat it as
> final for a production licensing decision -- contact [operator LLC abbreviation]utive
> Services, LLC directly to confirm current terms before relying on it.
