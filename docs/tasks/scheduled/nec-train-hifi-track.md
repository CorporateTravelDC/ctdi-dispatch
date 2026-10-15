---
name: nec-train-hifi-track
description: NEC corridor train 2-min high-fidelity polling — Acela + NE Regional trains to WAS
---

HIGH-FIDELITY TRAIN TRACKER — NEC Corridor to Washington Union Station

STATUS 2026-10-15: the skill that runs is `skills/nec-train-hifi-track/SKILL.md` (shipped in this repo; it pushes to `train-alerts` and `dispatch-debriefs` through `POST /admin/push-alert`). This file is the original scheduled-task prompt, kept for its design note; where they differ, the skill wins.
Trigger keywords: train track, train hifi, nec track, acela track, high-fidelity trains, demo trains

Poll every 2 minutes. Fetch current status of all watched NEC trains from the dispatch Amtrak endpoint, then fire a consolidated ntfy alert with the snapshot.

DESIGN NOTE — TRAIN NUMBER vs IDENTIFIER:
Same principle as hex ID for aircraft: querying by train number rather than train name/route
bypasses feed-level caching and aggregator filtering. Amtrak's real-time feeds use the train
number as the primary key. When integrating with GTFS-RT or direct Amtrak feeds, always use
the numeric train ID (e.g. `<NNNN>`) not the service name ("Acela") — service names are
marketing labels and are not unique per departure. This mirrors the aircraft practice of
using ICAO hex over callsign for cache/filter bypass.

CORRECTED 2026-08-23 — this note used to cite "Amtrak's native API (trains.faa.gov /
GTFS-RT)". There is no such host: `trains.faa.gov` does not exist, and the FAA does not
operate Amtrak data feeds at all. The train-number-as-primary-key principle is unaffected
and still correct; only the sourcing was wrong. What this platform actually reads is the
unofficial `https://api.amtraker.com/v3/trains` (no credentials), ~~consumed by
`src/ingest/amtrak.py` — the single live train-data path.~~ See docs/REGIONALIZATION.md
("Rail feed equivalents") for regional alternatives~~, and note that `poller/fetchers/amtrak.py`
is unscheduled dead code, so there is no automatic failover if that one loop stops~~.
(Corrected 2026-10-15: the live push writer is the `amtrak-tracker` container
(`src/amtrak_tracker/main.py`, stamps `push:amtrak`); `src/ingest/amtrak.py` is switched off in
every ingest container (`AMTRAK_ENABLED=false`). `poller/fetchers/amtrak.py` has been the scheduled
REST fallback since 2026-09-06 (`FETCH_SCHEDULE` entry `amtrak`, `failover.REST_FALLBACKS`), so there
is a failover. Live 01:31Z: `/api/v1/amtrak` available, 30 trains.)

STEP 1 — Fetch Amtrak data:
GET http://100.x.x.x:8000/api/v1/amtrak

Extract the full response. Look for these watched trains in the data:
Acela: `<train numbers from the live watchlist>`
NE Regional: `<train numbers from the live watchlist>`
(Watched train numbers removed 2026-10-15; the live watchlist is the source.)

For each found train extract: train number, origin, destination, scheduled arrival WAS, current delay (minutes), current status, last known location if available.

STEP 2 — Build summary:
- Count how many watched trains are running
- Count how many are delayed
- Identify worst delay among watched trains
- Note any on-time trains

STEP 3 — Always fire ntfy alert:
POST http://100.x.x.x:8000/admin/push-test-alert   (legacy alias of /admin/push-alert, src/web/main.py:2636; the skill uses /admin/push-alert)
Authorization: Bearer <DISPATCH_ADMIN_TOKEN from /etc/corporatetraveldc/svc env, never written here>
Content-Type: application/json
Body: {"message": "NEC HIFI: [N] trains tracked, [D] delayed. Worst: [train#] +[min]min. [on-time count] on time."}

Keep message under 200 chars.

STEP 4 — Report full table:
| Train | Route | Sched WAS | Delay | Status |
For each watched train found in the feed. If a train is not in the feed, note "not in feed" (may not be running today or feed stale).

If the Amtrak endpoint returns an error or empty data:
  Alert: {"message": "NEC HIFI: Amtrak feed unavailable — dispatch endpoint error"}
  Report: "Feed error."