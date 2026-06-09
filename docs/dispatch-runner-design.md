# dispatch-runner — Design Document
# Version: 1.0  Date: 2026-06-09
#
# -----------------------------------------------------------------------

## Overview

dispatch-runner is the internal operational web interface for CS Executive
Services dispatch operations. It is Tailscale-gated, never client-facing,
and provides a live visualisation of all dispatch data sources in a single
dark-ops interface. Think Norse Corp / LookingGlass threat map applied to
aviation and ground operations.

Accessible at: https://dispatch-runner.csexecutiveservices.com
Auth: Tailscale identity — 100.64.0.0/10 subnet only, enforced in FastAPI
middleware. Cloudflare tunnel terminates at port 8001; CF Access provides
secondary Tailscale identity check.

---

## Stack

| Layer       | Choice                          | Reason                                          |
|-------------|---------------------------------|-------------------------------------------------|
| Frontend    | Vite + React                    | Lightweight SPA, fast HMR, static build output  |
| Map         | Leaflet + OpenStreetMap         | Open source, no API key, handles all overlays   |
| Realtime    | FastAPI SSE                     | Native to FastAPI, works through CF tunnel      |
| Backend     | FastAPI (uvicorn), port 8001    | Consistent with dispatch web stack              |
| Auth        | Tailscale IP middleware         | 100.64.0.0/10 check on every non-healthz route  |
| PWA         | Vite PWA plugin (Workbox)       | manifest.json + service worker generation       |
| Container   | Multi-stage (Node builder + Python runtime) | No Node in final image          |

---

## ADS-B Source Toggle

Two modes, switchable via toolbar toggle, persisted in localStorage:

| Mode    | Source                                          | Use case                                    |
|---------|-------------------------------------------------|---------------------------------------------|
| LOCAL   | UltraFeeder /data/aircraft.json (port 8080)     | Low latency; only aircraft in antenna range |
| LIVE    | airplanes.live API v2 (250nm radius from KDCA)  | Full DC area window; fills antenna gaps     |

Backend exposes both as separate endpoints:
  GET /api/adsb/local  -- proxies UltraFeeder directly
  GET /api/adsb/live   -- queries airplanes.live ?lat=38.88&lon=-77.09&dist=250

Frontend polls the active source every 10 seconds.
On mode switch the map clears and re-populates immediately.

---

## Color Palette

| Token              | Value     | Use                                      |
|--------------------|-----------|------------------------------------------|
| bg-primary         | #0a0e1a   | Map background, main canvas              |
| bg-panel           | #1a2744   | Sidebar and overlay panels               |
| bg-panel-border    | #2a3f6f   | Panel borders                            |
| accent-track       | #00d4ff   | Aircraft track lines and markers         |
| accent-tfr         | #ff6b35   | TFR polygon fill and border              |
| accent-airspace    | #4a9eff   | Static airspace rings (FRZ, SFRA, P-56)  |
| cps-go             | #39ff14   | CPS GREEN / GO                           |
| cps-marginal       | #ffd700   | CPS YELLOW / MARGINAL                    |
| cps-nogo           | #ff3131   | CPS RED / NO-GO                          |
| text-primary       | #e8f0fe   | Primary UI text                          |
| text-secondary     | #8899bb   | Labels, secondary info                   |

---

## Backend API Routes

| Method | Path                    | Auth      | Description                              |
|--------|-------------------------|-----------|------------------------------------------|
| GET    | /healthz                | none      | Service health                           |
| GET    | /api/adsb/local         | Tailscale | Proxy -> UltraFeeder aircraft.json       |
| GET    | /api/adsb/live          | Tailscale | Proxy -> airplanes.live v2 (250nm KDCA)  |
| GET    | /api/dispatch/{path}    | Tailscale | Transparent proxy -> dispatch :8000      |
| GET    | /api/stream             | Tailscale | SSE stream: CPS + TFR + feed health      |
| GET    | /                       | Tailscale | React SPA (served from /app/static)      |

---

## Frontend Views

### / -- Live Ops Map (default)
- Full-screen Leaflet map, dark OSM basemap
- Aircraft markers: heading-aware icons, tooltip shows callsign/alt/speed
- TFR polygons: orange fill, pulsing border on VIP TFRs
- Static airspace overlays: P-56A, P-56B, DC FRZ (5nm), DC SFRA (15nm)
- Concentric range rings: 50/100/150/250nm centered on KDCA
- Weather layer toggle: METAR wind barbs at primary stations
- ADS-B source toggle: LOCAL / LIVE in toolbar
- Mini CPS panel: top-right traffic light + one-line narrative

### /status -- Feed Health Dashboard
- Per-feed freshness bars with staleness countdown
- CPS score card: full factors breakdown (ceiling/vis/wind/precip/airspace/gdp)
- Snapshot age, audit count, active token count
- Color-coded: green <15min, amber 15-45min, red >45min

### /tfr -- TFR Detail
- Cards from /api/v1/tfr-enriched
- VIP TFRs highlighted, enriched narrative displayed
- Effective window timeline per TFR

### /brief -- Daily Brief
- Renders ops-brief narrative from /api/v1/brief
- Timestamp, model used, CPS at time of generation

### /admin -- Trigger Panel
- Manual feed refresh buttons (metar, tfr, nws, ops_plan)
- Force CPS recompute
- Push test alert
- VIP watchlist management (add/remove entries)
- Requires admin token in localStorage (set once, persisted)

---

## SSE Event Schema

Events pushed every 30 seconds or on meaningful state change:

  data: {"type": "state", "cps": {...}, "feeds": {...}, "tfr_count": N, "vip_count": N}

Map layer updates (aircraft) are NOT SSE -- polled by frontend on timer to
manage request rate independently of the state stream.

---

## Auth Model

FastAPI middleware checks every request except /healthz:
1. Extract client IP from X-Forwarded-For (CF tunnel sets this)
2. Verify IP is within 100.64.0.0/10 (Tailscale CGNAT range)
3. Return 403 if not in range

Admin routes (/api/dispatch/admin/*) additionally require:
  Authorization: Bearer <csex_token>
Token set once in browser localStorage, sent by frontend on admin requests.

---

## PWA Manifest

  name: "CS Executive Services Dispatch"
  short_name: "Dispatch"
  theme_color: "#0a0e1a"
  background_color: "#0a0e1a"
  display: "standalone"
  orientation: "landscape-primary"
  icons: 192x192, 512x512 (dark ops compass/radar style)

Service worker: cache-first for static assets, network-first for API calls.
Offline mode: shows last-cached map state with "offline" banner.

---

## Build Process

  # In Containerfile.runner (multi-stage):
  # Stage 1: Node 20 Alpine -- builds React static assets
  # Stage 2: Python 3.13 slim -- FastAPI runtime + copied dist

  # On Pi:
  bash build-images.sh  # already includes runner target
  systemctl --user restart corporatetraveldc-runner.service

---

## Deployment

  Quadlet: ~/.config/containers/systemd/corporatetraveldc-runner.container
  Port: 8001 (127.0.0.1 + Tailscale IP)
  Tunnel hostname: dispatch-runner.csexecutiveservices.com -> localhost:8001
  Auth: Tailscale identity -- not publicly accessible

---

## Future / Deferred

- dispatch-runner-demo: sanitized past-data replay, public-safe, separate hostname
- HFDL message overlay on map (when HFDL0008 dongle active)
- AIS vessel track overlay on map (when AIS0162 dongle active)
- Amtrak position markers on map (Union Station / NEC corridor)
- WebSocket upgrade from SSE once volume justifies it
