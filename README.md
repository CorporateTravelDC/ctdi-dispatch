# CorporateTravelDC Dispatch Platform

> **Beta · Proof of Concept**
> Public reference implementation of the CorporateTravelDC autonomous dispatch
> platform. All internal credentials, identifiers, Tailscale IPs, and tunnel
> UUIDs have been replaced with placeholders. Not for production use without
> operator configuration.

---

## What this is

A 24/7 autonomous operational dispatch module for executive ground transportation,
running on a Raspberry Pi 5. Polls and receives live aviation, weather, NOTAM,
NAS, Amtrak, and TFR data; scores a Critical Predictability State (HEMS go/no-go);
fires push alerts for Marine One TFRs and airspace events; maintains a VIP watchlist
for flight and train tracking.

Self-hosted. Privacy-first. No cloud dependency for core operations.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Raspberry Pi 5 (BCM2712) — Arlington County, VA │
│                                                   │
│  corporatetraveldc-web      FastAPI REST API :8000│
│  corporatetraveldc-poller   REST poll scheduler   │
│  corporatetraveldc-pusher   ntfy alert dispatcher │
│  corporatetraveldc-ingest   Push-primary ingest   │
│  ntfy                       Self-hosted push      │
│                                                   │
│  SQLite WAL DB  ←→  all four containers           │
└─────────────────────────────────────────────────┘
         │ cloudflared (outbound-only tunnel)
         ▼
  dispatch.csexecutiveservices.com
```

All containers: rootless Podman, Quadlet systemd units (`systemctl --user`).

## Data sources

| Feed | Source | Method |
|---|---|---|
| METAR / TAF | AviationWeather.gov ADDS | REST poll |
| NWS alerts | api.weather.gov | REST poll |
| TFRs | tfr.faa.gov | REST poll |
| NOTAMs | FAA NOTAM API | REST poll |
| ATCSCC ops plan | atcscc.faa.gov | REST poll |
| Digital NOTAMs / AIM | FAA SWIM NMS | Push (Solace JMS) |
| FDPS / TFMS / STDDS | FAA SWIM SCDS | Push (Solace JMS) |
| NWWS-OI | NOAA | Push (TCP) |
| Amtrak | Amtrak feed | Push / poller fallback |

## REST API

Base: `https://dispatch.csexecutiveservices.com`

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Service health + snapshot age |
| GET | `/api/v1/feeds` | Feed freshness for all sources |
| GET | `/api/v1/tfr` | Active TFRs |
| GET | `/api/v1/tfr-enriched` | TFRs with AI enrichment |
| GET | `/api/v1/weather` | METAR snapshot — DC area stations |
| GET | `/api/v1/alerts` | Active NWS alerts |
| GET | `/api/v1/notams` | Active NOTAMs |
| GET | `/api/v1/cps` | Critical Predictability State |
| GET | `/api/v1/amtrak` | Amtrak status at WAS |
| GET | `/api/v1/brief` | Daily operational brief |
| GET | `/api/v1/route` | Ground route impact |
| GET | `/api/v1/opsplan` | ATCSCC ops plan |

Admin routes (`/admin/*`) require bearer token — create with `csex-token create`.

## Critical Predictability State (CPS)

Composite go/no-go score for Part 135.609 operations:

```
ceiling × visibility × wind × precipitation × airspace × GDP → CPS
```

Drives ntfy push alerts at configurable thresholds.

## Repository layout

```
src/
  common/         Shared DB, config, logging
  auth/           Token auth (csex-token CLI)
  poller/         REST poll scheduler + feed fetchers + AI skills
  pusher/         ntfy alert dispatcher
  ingest/         Push-primary SWIM / NWWS-OI / Amtrak ingest
  web/            FastAPI REST API
  shared/         VIP watchlist, cross-container shared logic
Containerfile.*   Container build files (one per service)
build-images.sh   Build all images
requirements.txt  Python dependencies
dispatch-secrets.env.example  Credential template (copy, never commit)
docs/             Architecture, boot config, LM Studio prompts
tests/            Unit and integration tests
```

## Configuration placeholders

Replace before deploying:

| Placeholder | Replace with |
|---|---|
| `<YOUR_GITHUB_PAT>` | GitHub PAT (Contents: write) |
| `<TAILSCALE_IP>` | `tailscale ip -4` on Pi |
| `<UUID>` | Cloudflare tunnel UUID |
| `FAA_NOTAM_API_KEY` | FAA API portal key |
| `AMTRAK_FEED_URL` | Amtrak GTFS-RT endpoint |
| `SOLACE_*` | FAA SWIM SCDS credentials |

All secrets: `/etc/corporatetraveldc/dispatch-secrets.env` (mode 0600, never commit).

## Quick start

```bash
# Build all images
bash build-images.sh

# Install Quadlets
cp .config/containers/systemd/corporatetraveldc-*.container \
   ~/.config/containers/systemd/

systemctl --user daemon-reload
systemctl --user enable --now corporatetraveldc-web
systemctl --user enable --now corporatetraveldc-poller
systemctl --user enable --now corporatetraveldc-pusher

# Verify
curl http://127.0.0.1:8000/healthz
```

## Security note

> **Git history advisory:** The private source repository contains a prior commit
> where `~/.config/gh/config.yml` was accidentally included. Those tokens have
> been revoked. The private repo history will be cleaned via BFG Repo Cleaner or
> recreated from clean source before any production re-deployment.
> This public PoC contains only clean `src/` code — no home directory artifacts.

## Endpoint naming

All CS Executive Services endpoints follow `[oneword][-xxx|regional].csexecutiveservices.com`.
See `docs/ARCHITECTURE.md` in the website repo for the full convention.

---

*CS Executive Services, LLC · Arlington County, Virginia*
*All operational data self-hosted. Privacy-first by design.*
