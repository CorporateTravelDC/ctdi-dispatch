# dispatch-runner — Design Document
# Version: 2.0  Date: 2026-06-14
#
# -----------------------------------------------------------------------

## Overview

dispatch-runner is the internal operational PWA for CS Executive Services
dispatch operations. It runs as a fifth container alongside the four dispatch
backend containers, proxying the dispatch web API and serving its own frontend
and runner-specific API routes.

Accessible at: https://dispatch-runner.example.com (port 8001)
Auth: Tailscale identity — 100.64.0.0/10 subnet enforced in FastAPI middleware
on every non-healthz route. Cloudflare tunnel terminates at localhost:8001.

---

## Stack

| Layer       | Choice                                      | Reason                                               |
|-------------|---------------------------------------------|------------------------------------------------------|
| Frontend    | Vite + React                                | Lightweight SPA, static build output in final image  |
| Maps        | Leaflet + OpenStreetMap (local) / airplanes.live iframe (globe) | OSM for overlay map; AL iframe for full globe |
| Realtime    | FastAPI SSE                                 | Native to FastAPI, works through CF tunnel           |
| Backend     | FastAPI (uvicorn), port 8001                | Consistent with dispatch web stack                   |
| Auth        | Tailscale IP middleware                     | 100.64.0.0/10 check on every non-healthz route       |
| RSS proxy   | httpx async, 15-min in-memory cache         | Server-side fetch avoids CORS; caches per feed URL   |
| Container   | Multi-stage (Node builder + Python runtime) | No Node in final image                               |

---

## ADS-B Modes

Two modes, switchable via toolbar toggle, persisted in localStorage:

| Mode    | Source                                               | Use case                                        |
|---------|------------------------------------------------------|-------------------------------------------------|
| GLOBE   | globe.airplanes.live iframe + local feeder Leaflet overlay | Full DC area; local feeder marked in green |
| LOCAL   | UltraFeeder /data/aircraft.json via Leaflet          | Low latency; only aircraft in antenna range     |
| LIVE    | airplanes.live API v2 via Leaflet                    | Full DC area; fills antenna gaps                |

**Globe mode** renders globe.airplanes.live in an iframe filling the lower
container, with a search bar docked above it (flex column — both always
visible simultaneously). A transparent Leaflet overlay sits on top of the
iframe to mark local feeder aircraft with green neon markers. Users can
search by callsign, N-number/registration, or ICAO hex; the iframe navigates
to the matching aircraft directly.

The search type detector: 6-char hex → ICAO hex query; alpha-prefix or N-
prefix → registration query; 2–3 letter prefix + digits → ICAO callsign.

Backend exposes both ADS-B modes as separate endpoints:
  GET /api/adsb/local  — proxies UltraFeeder /data/aircraft.json
  GET /api/adsb/live   — queries airplanes.live v2 (?lat=38.88&lon=-77.09&dist=250)

Frontend polls the active source every 10 seconds.

---

## Color Palette

| Token              | Value     | Use                                           |
|--------------------|-----------|-----------------------------------------------|
| bg-primary         | #0a0e1a   | Map background, main canvas                   |
| bg-panel           | #1a2744   | Sidebar and overlay panels                    |
| bg-panel-border    | #2a3f6f   | Panel borders                                 |
| accent-track       | #00d4ff   | Aircraft track lines, markers, podcast badge  |
| accent-tfr         | #ff6b35   | TFR polygon fill and border                   |
| accent-airspace    | #4a9eff   | Static airspace rings (FRZ, SFRA, P-56)       |
| cps-go             | #39ff14   | CPS GREEN / GO; local feeder markers          |
| cps-marginal       | #ffd700   | CPS YELLOW / MARGINAL                         |
| cps-nogo           | #ff3131   | CPS RED / NO-GO; VIP TFRs                     |
| text-primary       | #e8f0fe   | Primary UI text                               |
| text-secondary     | #8899bb   | Labels, secondary info                        |

---

## Backend API Routes

| Method          | Path                        | Auth      | Description                                          |
|-----------------|-----------------------------|-----------|------------------------------------------------------|
| GET             | /healthz                    | none      | Service health                                       |
| GET             | /api/adsb/local             | Tailscale | Proxy → UltraFeeder aircraft.json                    |
| GET             | /api/adsb/live              | Tailscale | Proxy → airplanes.live v2 (250nm KDCA)               |
| GET/POST        | /api/dispatch/{path}        | Tailscale | Transparent proxy → dispatch web at :8000            |
| GET             | /api/stream                 | Tailscale | SSE stream: CPS + TFR count + feed health (30s)      |
| GET             | /api/rss                    | Tailscale | Merged catalog + user feeds for ?category=           |
| GET             | /api/rss/categories         | Tailscale | Available categories and catalog sources             |
| GET             | /api/rss/custom             | Tailscale | Fetch and proxy arbitrary feed URL (CORS bypass)     |
| GET             | /api/rss/user-feeds         | Tailscale | List user-defined feeds                              |
| POST            | /api/rss/user-feeds         | Tailscale | Add user-defined feed (validated before saving)      |
| DELETE          | /api/rss/user-feeds/{id}    | Tailscale | Remove user-defined feed by UUID                     |
| GET             | /                           | Tailscale | React SPA (served from /app/static)                  |

---

## Intel Feed — RSS/Atom

### Catalog categories

Five built-in categories, three feeds each (15 total):

| Category          | Feeds                                              |
|-------------------|----------------------------------------------------|
| `corporate_intel` | Skift, Federal News Network, The Air Current       |
| `marketing_intel` | Robb Report Travel, Forbes Travel Guide, Lodging Magazine |
| `travel_trends`   | The Points Guy, Condé Nast Traveler, One Mile at a Time |
| `dc_area`         | WTOP Traffic & Transit, Washingtonian, ARLnow      |
| `aviation`        | AviationSource, AOPA News, Cranky Flier            |

A sixth category (`__custom__`) holds only user-defined feeds.

### User-defined feeds

Users can subscribe to any RSS, Atom, podcast, or YouTube channel feed and
assign it to any catalog tab or to Custom-only. Feeds are persisted at
`/var/lib/corporatetraveldc/user_rss_feeds.json` (volume-mounted; survives
image rebuilds). Each entry: `{id: UUID, name, url, category}`.

On POST `/api/rss/user-feeds`, the server fetches the URL to validate it
before saving (returns 422 if it fetches but parses to zero items; 409 if
the URL is already registered).

When `/api/rss?category=X` is called, catalog feeds and user feeds assigned
to that category are fetched in parallel, merged, sorted newest-first, and
capped at `?limit=` (default 200, max 500).

### Parsing and sort

`_parse_rss()` handles both RSS 2.0 and Atom 1.0. RSS `pubDate` values
(RFC 2822 format — "Fri, 12 Jun 2026 20:38:34 +0000") are normalized to
ISO 8601 via `email.utils.parsedate_to_datetime` before storage. This
ensures reverse string sort produces correct newest-first order regardless
of source. Atom feeds already emit ISO 8601 and pass through unchanged.

Each feed is sorted and capped at 100 items before merge. This prevents
a podcast archive with thousands of episodes from swamping news items in
a shared tab.

### Podcast/audio support

Items with `<enclosure type="audio/*">` or `<enclosure type="video/*">`
tags return an `audio_url` field. The frontend identifies these as podcast
episodes with a ▶ badge and a "▶ Play" toggle button that reveals an HTML5
`<audio controls preload="none">` element inline.

### Cache

15-minute in-memory cache keyed by `"{category_prefix}:{url}"`. Cache is
process-scoped (restarts clear it). No persistent RSS cache on disk.

---

## Frontend Views

### Map (ADS-B) — default tab

**Globe mode (default):**
- Search bar docked above, always visible
- airplanes.live iframe below, fills remaining height
- Transparent Leaflet overlay on iframe: local feeder aircraft in green neon
- Search navigates iframe to the matched aircraft (hex / reg / callsign)

**Local / Live mode:**
- Full Leaflet map with dark OSM basemap
- Aircraft markers: heading-aware SVG icons (cyan for remote, green for local feeder)
- TFR polygons: orange fill / red for VIP
- Static airspace overlays: P-56A, P-56B, DC FRZ (5nm), DC SFRA (15nm)
- Concentric range rings: 50/100/150/250nm centered on KDCA
- ADS-B source toggle (LOCAL / LIVE) in search bar area

### Status — Feed Health

- Per-feed freshness bars with staleness countdown
- CPS score card: full factors breakdown (ceiling/vis/wind/precip/airspace/GDP)
- Snapshot age, audit count, active token count
- Color-coded: green < 15 min, amber 15–45 min, red > 45 min

### TFR — Active TFRs

- Cards from /api/v1/tfr-enriched (AI-enriched narrative)
- VIP TFRs flagged; effective window timeline per TFR

### Brief — Daily Brief

- Ops-brief narrative from /api/v1/brief
- Timestamp, model used, CPS at time of generation

### Intel — RSS/Atom intelligence feed

- Tab bar: Corporate Intel | Marketing Intel | Client Travel Trends | DC Area | Aviation | Custom ⋯
- Each tab fetches `/api/rss?category=<tab>` — catalog feeds merged with any
  user-defined feeds assigned to that category
- Items sorted newest-first (ISO 8601 normalized dates)
- "Load more" pagination — 15 items per page
- Podcast episodes: ▶ badge on title, inline HTML5 player on click
- Custom tab: "My Feeds" manager (add / remove user-defined feeds with
  category selector) + items from `__custom__` category
- Custom tab badge shows count of registered user feeds

### Signals — ntfy feed

- Displays live ntfy topics for all dispatch channels
- Shows message history, priority coloring

### Admin — Trigger Panel

- Manual feed refresh buttons
- Force CPS recompute
- Push test alert
- VIP watchlist management
- Requires admin bearer token (set once in UI, persisted in localStorage)

### Chat — Dispatch Drawer

- Streaming chat via csexec-chat (mistral-nemo Modelfile wrapper)
- Backed by /api/dispatch/api/v1/chat endpoint

---

## SSE Event Schema

Events pushed every 30 seconds or on meaningful state change:

  data: {"type": "state", "cps": {...}, "feeds": {...}, "tfr_count": N, "vip_count": N}

Aircraft position updates are polled by the frontend on a timer (every 10s),
not pushed, to manage request rate independently of the SSE state stream.

---

## Auth Model

FastAPI middleware checks every request except /healthz:
1. Extract client IP from X-Forwarded-For (set by CF tunnel)
2. Verify IP is within 100.64.0.0/10 (Tailscale CGNAT range)
3. Return 403 if not in range

Admin routes (/api/dispatch/admin/*) additionally require:
  Authorization: Bearer <csex_token>
Token set once in the Admin view, sent by frontend on admin requests.

---

## Key Paths

| Path | Purpose |
|------|---------|
| `/opt/corporatetraveldc/src/runner/main.py` | FastAPI app: ADS-B proxy, RSS engine, dispatch proxy |
| `/opt/corporatetraveldc/src/runner/frontend/src/` | React source (components, styles) |
| `/opt/corporatetraveldc/Containerfile.runner` | Multi-stage build (Node → Python) |
| `/var/lib/corporatetraveldc/user_rss_feeds.json` | User-defined feed registry (volume-mounted) |

---

## PWA Manifest

  name: "CS Executive Services Dispatch"
  short_name: "Dispatch"
  theme_color: "#0a0e1a"
  background_color: "#0a0e1a"
  display: "standalone"
  orientation: "landscape-primary"

Service worker: cache-first for static assets, network-first for API calls.

---

## Build

  # Rebuild runner only:
  bash build-images.sh runner
  systemctl --user restart corporatetraveldc-runner

  # Full rebuild:
  bash build-images.sh
  systemctl --user daemon-reload
  systemctl --user restart corporatetraveldc-runner

---

## Deployment

  Quadlet: ~/.config/containers/systemd/corporatetraveldc-runner.container
  Port: 8001 (127.0.0.1 + Tailscale IP)
  Volume: /var/lib/corporatetraveldc:/var/lib/corporatetraveldc:Z
  Tunnel: dispatch-runner.example.com → localhost:8001
  DISPATCH_BASE_URL: http://127.0.0.1:8000 (dispatch web API)

---

## Deferred / Future

- AIS vessel track overlay on ADS-B map (when AIS dongle active)
- Amtrak position markers on map (NEC corridor)
- HFDL message overlay when HFDL dongle active
- dispatch-runner-demo: sanitized past-data replay, public-safe hostname
- WebSocket upgrade from SSE when volume justifies it
- EP topics in Signals view (ep, ep-advance, ep-briefs)
- FAA NOTAM API v2 key (APIC4E endpoint)
- Persistent RSS cache on disk (currently process-scoped memory only)
