# Platform Status — 2026-06-08

## Health
- **Web API:** https://dispatch.csexecutiveservices.com
- **CPS:** YELLOW / MARGINAL
- **Degraded reason:** SWIM push feeds pending FAA NMS credentials (expected)
- **All containers:** Running

## Services (10 active)
| Service | Status | Notes |
|---------|--------|-------|
| corporatetraveldc-web | running | FastAPI, port 8000 |
| corporatetraveldc-poller | running | fetchers + skill scheduler |
| corporatetraveldc-pusher | running | ntfy alert sender |
| corporatetraveldc-ingest | running | SWIM pending creds; idling |
| amtrak-tracker | running | 46 trains at WAS live |
| ntfy | running | port 2586, auth enforced |
| corporatetraveldc-ultrafeeder | installed | awaiting SDR dongle (ADSB1090) |
| corporatetraveldc-acarsrouter | installed | awaiting SDR dongle (ACARS0130) |
| corporatetraveldc-acarshub | installed | awaiting hardware |
| corporatetraveldc-dumpvdl2 | installed | awaiting hardware |

## Cloudflare Tunnel
- **Tunnel:** `dispatch` (`28bde9a2-0bb2-4cca-a207-9b759c4739f1`)
- **Connections:** 4 active (Atlanta edge)
- **Credentials:** rotated 2026-06-08 after SD card failure

| Subdomain | Backend | Access |
|-----------|---------|--------|
| dispatch.csexecutiveservices.com | :8000 | Tailscale A record + CF Access |
| ops.csexecutiveservices.com | :8000 | CF Tunnel, open |
| dispatch-runner.csexecutiveservices.com | :8001 | CF Tunnel, open |
| adsb.csexecutiveservices.com | :8080 | CF Tunnel, open |
| acars.csexecutiveservices.com | :9081 | CF Tunnel, open |
| ntfy.csexecutiveservices.com | :2586 | CF Tunnel, open |
| pihole.csexecutiveservices.com | :8091 | Tailscale A record + CF Access |

## Recent commits
| Commit | Summary |
|--------|---------|
| `37eeb31` | Add cloudflared tunnel systemd service |
| `e5d5f9b` | Deploy all Quadlet units, amtrak-tracker image, CLAUDE.md, docs |
| `89f8a76` | Clean: remove home directory artifacts |

## ntfy topics (10)
`tfr-alert` · `hot-alerts` · `flight-alerts` · `train-alerts` · `dispatch` · `dispatch-debriefs` · `cps` · `wx-alerts` · `ops-brief` · `ops-health`

## SSE stream
`GET https://dispatch.csexecutiveservices.com/api/v1/events` — PWA-ready

## Open
- FAA NMS credentials → provision in `dispatch-secrets.env`, rebuild ingest
- Anthropic API credits exhausted → skills on fallback
- PWA build (SSE stream ready) — website repo: csexecutiveservices-website
- SDR dongles: tag with serials ADSB1090 / ACARS0130, then start ultrafeeder + ACARS stack
- `hot-alerts` insert path: set `ntfy_fired=0` on new watchlist history rows
- ntfy CF tunnel endpoint: update `base-url` in `/etc/ntfy/server.yml` once live
