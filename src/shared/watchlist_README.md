# Watchlist System

**Rewritten 2026-08-11 against `src/shared/watchlist.py`, `src/web/routes/watchlist.py`,
and `src/poller/main.py` as they exist today.** The previous revision (2026-06-07)
predated vessel tracking entirely.

Two tiers share one monitoring/alert pipeline:

- **Permanent** — operator-maintained JSON files in `/opt/corporatetraveldc/watchlists/`,
  survive reboots, never auto-expire.
- **Transient** — added via REST API, auto-expire after a flight/train leg.

Three entry types: **`flight`** (identifier = callsign), **`train`**
(identifier = Amtrak train number), **`vessel`** (identifier = 9-digit MMSI).

---

## Permanent watchlist files

`WatchlistFileWatcher` (poller-side) polls file mtimes every 60s — changes are
picked up within ~65 seconds, no restart needed (`_FILE_MAP` in
`src/shared/watchlist.py`):

| File | Entry type |
|---|---|
| `permanent_flights.json` | `flight` |
| `permanent_trains.json` | `train` |
| `permanent_vessels.json` | `vessel` (yachts/cruise ships, identifier = MMSI) |

All three use the same wrapper: `{"watchlist": [ ... ]}`.

**Flight entry** (`PermanentFlightItem`):

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

**Train entry** (`PermanentTrainItem`): same shape, `identifier` is the train
number (e.g. `"2171"`), `route_name` e.g. `"Acela"`.

**Vessel entry** (`PermanentVesselItem`): same shape, `identifier` is the
9-digit MMSI; `origin`/`destination`/`route_name`/`notes` optional.

Optional per-entry fields also honored by the loader: `subsection`,
`show_national`, `show_regional`, `days_active`, `sister_flight`.

Rules:

- `id` must be unique — convention `perm-flight-<ident>` / `perm-train-<ident>` /
  `perm-vessel-<mmsi>`.
- Removing an entry from the file removes it from the DB and writes a
  `permanent_removed` history record.
- `auto_remove_at` is always `null` for permanent entries — never swept by expiry.

---

## Transient entries (REST API)

All mutating watchlist routes require an **admin** bearer token; listing/history
require Tier 1. Since the 2026-08-19 audit change, `require_admin` is a
*factory* — each of the 8 mutating routes here passes its own action name
(`Depends(require_admin("watchlist.flight.add"))`, `…train.add`,
`…vessel.add`, `…flight.add_batch`, `…train.add_batch`,
`…permanent.add_batch`, `…entry.remove`, `…batch_remove`) and the factory
writes an `audit_log` row (actor, action, tier, source IP, request body)
before the handler runs. Routes live in `src/web/routes/watchlist.py` under
`/api/v1/watchlist`:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/watchlist` | T1 | List active entries |
| GET | `/api/v1/watchlist/history?limit=N` | T1 | Event history |
| POST | `/api/v1/watchlist/flights` | admin | Add flight (201) |
| POST | `/api/v1/watchlist/trains` | admin | Add train (201) |
| POST | `/api/v1/watchlist/vessels` | admin | Add vessel (201) |
| POST | `/api/v1/watchlist/flights/batch` | admin | Batch add flights (201) |
| POST | `/api/v1/watchlist/trains/batch` | admin | Batch add trains (201) |
| POST | `/api/v1/watchlist/permanent/batch` | admin | Merge into the permanent JSON files (atomic write) |
| DELETE | `/api/v1/watchlist/{entry_id}` | admin | Remove one entry (204) |
| DELETE | `/api/v1/watchlist/batch` | admin | Batch remove |

Example — add a flight:

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

If `auto_remove_at` is omitted, `_default_auto_remove_at()` sets it to
scheduled arrival **+ 6 h for flights and vessels, + 3 h for trains**
(`buffer_hours=` at each call site in `src/web/routes/watchlist.py`; the
batch routes use the same per-type values). If no `scheduled_arrival` is
known at add time, it falls back to **added-time + 24 h**
(`fallback_hours`) so a missing arrival estimate can't leave a transient
entry effectively permanent. *(Corrected 2026-08-23 — this previously read
"+ 6 h" flat, which is right for flights/vessels but not for trains.)*

The add-time value is no longer final for flights: since 2026-08-23
(deployed 2026-08-24), `extend_auto_remove_for_delay()`
(`src/shared/watchlist.py`), called from `tfms_parser.py`'s
`_handle_flight_times` the moment TFMS first reports a real
`airlineOffTime`, revisits it — re-basing onto the real
`scheduled_arrival + 6 h` if the entry had started on the added-time
fallback, then adding the actual departure delay on top (never shrinking
the window for an early departure). Once-only per entry, guarded by
`watchlist_entries.departure_delay_min IS NULL` (`SCHEMA_V37`). See
CLAUDE.md's "Delay-extended `auto_remove_at`" entry for the worked
example and test coverage. *(Added 2026-08-24 — the feature shipped in
the same overnight batch as the 08-23 correction above but this README
hadn't been told about it.)*

---

## Sweeps (poller `WatchlistSweep`, `src/poller/main.py`)

| Sweep | Interval | What it does |
|---|---|---|
| Expiry | 60 s | `sweep_expired_transient()` — moves past-`auto_remove_at` entries to `watchlist_history` (`auto_expired`) |
| Flight | 120 s | Live position/OOOI check on active flight entries |
| Train | 300 s | Amtrak status check; `sweep_landed_trains()` retirement |
| Local aircraft | 60 s | Local UltraFeeder ADS-B proximity |
| Vessel | 300 s | `_do_vessel_sweep()` — AISHub bbox query per active vessel entry (`VESSEL_SWEEP_INTERVAL = 300`, a hardcoded class constant, **not** an env var) |
| FAA registry | daily | Registry refresh |

**Vessel sweep detail:** requires `AIS_AISHUB_ID` in the environment — with no
AISHub ID configured the sweep is a silent no-op. ⚠️ **Live state 2026-08-23:
vessel tracking is wired but dormant, on two independent counts** — the
`AIS_AISHUB_ID=` line in `/etc/corporatetraveldc/dispatch-secrets.env` is
present but **empty**, and there are zero `vessel` rows in
`watchlist_entries` (`permanent_vessels.json` is `{"watchlist": []}`), so the
sweep has nothing to iterate over either way. `vessel_events` has **0 rows**.
Neither is a fault; don't debug the vessel path as broken before setting an
AISHub ID and adding at least one MMSI. It queries
`data.aishub.net/ws.php` with a ~120 NM bounding box around DC (free tier has
no single-MMSI lookup), persists *every* returned vessel into the
`vessel_events` table (`source="aishub.net"`), then fires a
`vessel_position` watchlist event if a watched MMSI is in the box (priority 2,
suppressed when the position summary is unchanged). Kpler Maritime was
evaluated and dropped 2026-07-21.

---

## ntfy topic routing

Every watchlist event fires a dual push via `_fire_ntfy_dual()`: full detail to
the domain topic + a concise line to `dispatch`. 3 retry attempts, 0.5 s
doubling backoff.

| Entry type | Domain topic | Also fires |
|---|---|---|
| flight | `flight-alerts` | `dispatch` |
| train | `train-alerts` | `dispatch` |
| vessel | `vessel-alerts` | `dispatch` |

> **2026-08-11:** an earlier revision of this file documented a live bug —
> `watchlist_event_hit()` in `src/shared/watchlist.py` had no vessel branch,
> so vessel **position events** fell through to the train `else` and landed
> on `train-alerts` with a `TRN`-prefixed title. **Fixed later the same
> day**: the function now has an explicit `vessel` branch
> (`domain_topic="vessel-alerts"`, MMSI + `notes`-as-name body, `VSL ` title
> prefix). The follow-on cleanup that revision flagged is **also done** —
> verified 2026-08-23, `src/shared/watchlist.py:39` now reads
> `EntryType = Literal["flight", "train", "vessel"]`, so the annotation
> matches the three types the code actually handles.

**Deduplication:** the same `entry_id` + `event_type` (content-aware — detail
hashed with timestamps bucketed to 10-minute windows) will not re-fire within
5 minutes (`_DEDUP_WINDOW_SECS = 300`).

---

## Flight identity resolution (2026-08-22)

`resolve_flight_identity(entry, ident, source=)` in `src/shared/watchlist.py`
owns hex/tail resolution for flight entries — extracted from the poller's
`_check_flight_airplanes_live()` sweep (same lookup-priority order and
false-positive protections: hex-authoritative-once-known, callsign-only
during bootstrap, local FAA/OpenSky registries before airplanes.live's flaky
`/v2/reg/`). Two callers:

- the poller's 120 s flight sweep (unchanged behavior), and
- `ingest/parsers/tfms_parser.py::_handle_flight_times`, which forces a
  resolve attempt the moment `airlineOutTime` first appears in a TFMS
  message for an entry with no `hex_id` yet — i.e. at the real OUT moment,
  instead of waiting up to a sweep interval. Gated on the OUT transition
  only and wrapped in its own try/except so pre-OUT messages can never fail
  because of it.

The moment a hex-lock actually happens, a one-time-per-entry
`watchlist_event_hit` fires with `watchlist_trigger: identity_resolved`,
carrying the resolved hex/registration and a
`https://globe.airplanes.live/?icao={hex}` click-through. Any caller-supplied
`event_detail["tracking_url"]` is appended to the push **body** as its own
line (never the title — raw newlines in ntfy titles break the push).
Re-resolving an already-hex-locked entry never re-notifies.

Also since 2026-08-20: `POST /api/v1/watchlist/flights` responses include
`ontime_history_14d` (real airline-reported OOOI delay history from
`flight_ooooi_times`, watchlist-gated capture — `insufficient_data: true` is
the expected response until an entry has accumulated watched days; see
CLAUDE.md → "Database schema").
