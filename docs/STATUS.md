# Platform Status — 2026-06-02

## Health
- **Web API:** https://dispatch.csexecutiveservices.com
- **CPS:** YELLOW / MARGINAL
- **Degraded reason:** SWIM push feeds pending FAA NMS credentials (expected)
- **All containers:** Running

## Recent commits
| Commit | Summary |
|--------|---------|
| `96ea7b9` | Fix pusher encoding, train watchlist JSON, SCHEMA_V8 + missing DB functions |
| `ebe2980` | Add amtrak-tracker service and NMS client |
| `0581636` | SSE live event stream + hot-alerts for operationally critical pushes |
| `f80f384` | SDR local airspace, ACARS ingest, batch watchlist API |

## ntfy topics (9)
`tfr-alert` · `hot-alerts` · `flight-alerts` · `train-alerts` · `dispatch` · `cps` · `wx-alerts` · `ops-brief` · `ops-health`

## SSE stream
`GET https://dispatch.csexecutiveservices.com/api/v1/events` — PWA-ready

## Open
- FAA NMS credentials → provision in `dispatch-secrets.env`, rebuild ingest
- Anthropic API credits exhausted → skills on fallback
- PWA build (SSE stream ready)
- `hot-alerts` insert path: set `ntfy_fired=0` on new watchlist history rows
