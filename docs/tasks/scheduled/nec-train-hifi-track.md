---
name: nec-train-hifi-track
description: NEC corridor train 2-min high-fidelity polling — Acela + NE Regional trains to WAS
---

HIGH-FIDELITY TRAIN TRACKER — NEC Corridor to Washington Union Station
Trigger keywords: train track, train hifi, nec track, acela track, high-fidelity trains, demo trains

Poll every 2 minutes. Fetch current status of all watched NEC trains from the dispatch Amtrak endpoint, then fire a consolidated ntfy alert with the snapshot.

DESIGN NOTE — TRAIN NUMBER vs IDENTIFIER:
Same principle as hex ID for aircraft: querying by train number rather than train name/route
bypasses feed-level caching and aggregator filtering. Amtrak's native API (trains.faa.gov /
GTFS-RT) uses train number as the primary key. When integrating with GTFS-RT or direct Amtrak
feeds, always use the numeric train ID (e.g. 2155, 137) not the service name ("Acela") —
service names are marketing labels and are not unique per departure. This mirrors the aircraft
practice of using ICAO hex over callsign for cache/filter bypass.

STEP 1 — Fetch Amtrak data:
GET http://100.94.80.100:8000/api/v1/amtrak

Extract the full response. Look for these watched trains in the data:
Acela: 2155, 2159, 2163, 2167, 2171, 2173, 2121
NE Regional: 137, 173, 175

For each found train extract: train number, origin, destination, scheduled arrival WAS, current delay (minutes), current status, last known location if available.

STEP 2 — Build summary:
- Count how many watched trains are running
- Count how many are delayed
- Identify worst delay among watched trains
- Note any on-time trains

STEP 3 — Always fire ntfy alert:
POST http://100.94.80.100:8000/admin/push-test-alert
Authorization: Bearer ctdc_cowork_5NC2G5DLI8CONLZCFWO5TLM5CEABD7OQ
Content-Type: application/json
Body: {"message": "NEC HIFI: [N] trains tracked, [D] delayed. Worst: [train#] +[min]min. [on-time count] on time."}

Keep message under 200 chars.

STEP 4 — Report full table:
| Train | Route | Sched WAS | Delay | Status |
For each watched train found in the feed. If a train is not in the feed, note "not in feed" (may not be running today or feed stale).

If the Amtrak endpoint returns an error or empty data:
  Alert: {"message": "NEC HIFI: Amtrak feed unavailable — dispatch endpoint error"}
  Report: "Feed error."