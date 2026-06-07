# Watchlist System

Two tiers: **permanent** (operator JSON files, survive reboots) and
**transient** (added via REST API, auto-expire after a flight or train leg).

Both fire the same dual ntfy push: domain topic (`flight-alerts` or
`train-alerts`) + `dispatch`, simultaneously.

---

## Permanent watchlist entries

Edit the files in `/opt/corporatetraveldc/watchlists/`. The poller
hot-reloads them within 65 seconds of any change (mtime-based, no inotify).

**`permanent_flights.json`** — flight callsigns to monitor permanently:
```json
{
  "watchlist": [
    {
      "id": "perm-flight-jia5438",
      "identifier": "JIA5438",
      "origin": "KCVG",
      "destination": "KPHL",
      "route_name": "PSA Airlines / American Eagle CRJ9",
      "notes": "Recurring CVG-PHL morning run",
      "added": "2026-05-27",
      "added_by": "operator"
    }
  ]
}
```

**`permanent_trains.json`** — train numbers to monitor permanently:
```json
{
  "watchlist": [
    {
      "id": "perm-train-acela-2171",
      "identifier": "2171",
      "route_name": "Acela",
      "origin": "BOS",
      "destination": "WAS",
      "notes": "Recurring daily BOS-WAS Acela",
      "added": "2026-05-27",
      "added_by": "operator"
    }
  ]
}
```

**Rules:**
- `id` must be unique. Use `perm-flight-<ident>` or `perm-train-<ident>` format.
- `identifier` is the callsign (flights) or train number (trains).
- Removing an entry from the file removes it from the DB and writes a
  `permanent_removed` history record.
- `auto_remove_at` is always `null` for permanent entries — they never expire.

---

## Transient watchlist entries (REST API)

Added via Cowork session or admin API. Auto-expire after the flight/train leg.

**Add a flight:**
```bash
curl -X POST http://localhost:8000/api/v1/watchlist/flights \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "identifier": "JIA5438",
    "origin": "KCVG",
    "destination": "KPHL",
    "scheduled_departure": "2026-05-28T05:56:00-04:00",
    "scheduled_arrival": "2026-05-28T07:13:00-04:00",
    "auto_remove_at": "2026-05-28T07:43:00-04:00",
    "notes": "Client run",
    "added_by": "cowork"
  }'
```

**Add a train:**
```bash
curl -X POST http://localhost:8000/api/v1/watchlist/trains \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "identifier": "2171",
    "route_name": "Acela",
    "origin": "BOS",
    "destination": "WAS",
    "scheduled_arrival": "2026-05-27T22:20:00-04:00",
    "auto_remove_at": "2026-05-27T22:50:00-04:00",
    "notes": "En route BOS-WAS",
    "added_by": "cowork"
  }'
```

**List active entries:**
```bash
curl http://localhost:8000/api/v1/watchlist
```

**Remove an entry:**
```bash
curl -X DELETE http://localhost:8000/api/v1/watchlist/<entry-id> \
  -H "Authorization: Bearer <admin-token>"
```

**View event history:**
```bash
curl "http://localhost:8000/api/v1/watchlist/history?limit=20"
```

---

## Auto-expiry behavior

- Transient entries with `auto_remove_at` set are swept every 60 seconds
  by the poller's `sweep_expired_transient()`.
- Expired entries are moved to `watchlist_history` with `event_type = "auto_expired"`.
- Permanent entries (`auto_remove_at = null`) are **never** swept by the expiry
  process — only removed when deleted from the JSON file or via the DELETE API.

---

## ntfy topic routing

| Entry type | Domain topic    | Also fires |
|------------|-----------------|------------|
| flight     | `flight-alerts` | `dispatch` |
| train      | `train-alerts`  | `dispatch` |

Both pushes use the same title and priority. `dispatch` carries a concise
one-line summary; the domain topic carries full event detail.

Deduplication: the same `entry_id` + `event_type` will not fire ntfy again
within 5 minutes, even if the ingest parser emits duplicate hits.
