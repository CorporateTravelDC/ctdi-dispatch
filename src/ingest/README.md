# corporatetraveldc — Ingest Layer

**Rewritten 2026-08-11 against the live code and running system.** The previous
revision was a 2026-07-20 snapshot: it described FDPS FIXM 3.0 parsing and most
TFMS message types as unimplemented stubs (all live since 2026-07-20), and
referenced a unified `corporatetraveldc-ingest` systemd unit that was split into
seven per-feed containers on 2026-07-26 and no longer exists.

The ingest layer connects to push data feeds (FAA SWIM via NMS/Solace,
NWWS-OI XMPP, Amtrak) and writes events into the shared SQLite database.
The poller's REST fallback activates automatically whenever a push feed stops
stamping heartbeats.

## Container topology — 7 per-feed containers (since 2026-07-26)

There is **no** unified `corporatetraveldc-ingest` unit anymore. One image
(`localhost/corporatetraveldc-ingest:latest`, entrypoint `python3 -m
ingest.main`) runs as seven independent Quadlets, differentiated purely by
environment variables. Quadlet files live in `.config/containers/systemd/`
(repo copy) / `~/.config/containers/systemd/` (live):

| Unit | Covers | Key env |
|---|---|---|
| `corporatetraveldc-ingest-core` | NWWS-OI + Amtrak + local airspace, **zero SWIM feeds** | `SWIM_NMS_ENABLED=false`, `LOCAL_AIRSPACE_ENABLED=true` |
| `corporatetraveldc-ingest-fdps` | SWIM FDPS only | `SWIM_NMS_SKIP_FEEDS=stdds,tfms,fns,tbfm,itws` |
| `corporatetraveldc-ingest-stdds` | SWIM STDDS only | skip-all-but-stdds |
| `corporatetraveldc-ingest-tfms` | SWIM TFMS only | skip-all-but-tfms |
| `corporatetraveldc-ingest-tbfm` | SWIM TBFM only | skip-all-but-tbfm |
| `corporatetraveldc-ingest-itws` | SWIM ITWS only | skip-all-but-itws |
| `corporatetraveldc-ingest-notam` | SWIM FNS (digital NOTAMs) only | skip-all-but-fns — note the unit is named `notam`, the feed name is `fns` |

All 7 are **running and live** as of 2026-08-11 (`podman ps`; all six SWIM
feeds confirmed connected with fresh heartbeats since 2026-08-07).
`corporatetraveldc-boot-stagger.service` staggers their startup, and
`scripts/ingest-feed-ctl.sh` provides per-feed control:

```bash
scripts/ingest-feed-ctl.sh restart all --order=lightest-first --stagger=15
scripts/ingest-feed-ctl.sh stop tfms
```

(`ALL_FEEDS=(fdps stdds tfms tbfm itws notam)`; `lightest-first` order is
notam→itws→tbfm→tfms→stdds→fdps.)

## Modules

| File | Role |
|---|---|
| `main.py` | Entrypoint/supervisor — launches `swim_nms`, `nwws`, `amtrak` tasks + `LocalAirspaceMonitor` thread |
| `config.py` | Env-driven config (`NmsConfig`, `NwwsConfig`, `AmtrakConfig`, …) |
| `swim_client.py` | Solace PubSub+ subscriber, per-feed sessions, heartbeats, backlog handling |
| `nwws.py` | NWWS-OI XMPP MUC subscriber (`NWWS_WFO_FILTER` keeps only configured WFOs) |
| `amtrak.py` | Amtrak push-primary poller (`api.amtraker.com/v3/trains`, 300 s) |
| `local_airspace.py` | Local RF: UltraFeeder ADS-B poll (15 s) + ACARS router TCP (port 9080) |
| `failover.py` | Heartbeat contract (`push:` prefix) |
| `parsers/` | `fdps_parser.py`, `smes_parser.py` (STDDS), `tfms_parser.py`, `tbfm_parser.py`, `itws_parser.py`, `aim_parser.py` (FNS/NOTAM), `geo_filter.py` |

## SWIM feeds and credentials

Six SWIM feeds (`swim_client.py` `_FEED_HANDLERS`): `fdps`, `stdds`, `tfms`,
`fns` (AIM credentials, `fns` heartbeat key), `tbfm`, `itws`. Per-feed env
pattern (`<KEY>` = FDPS/STDDS/TFMS/AIM/TBFM/ITWS):

```
SWIM_NMS_HOST_<KEY>   (fallback SWIM_NMS_HOST, default tcps://ems1.swim.faa.gov:55443)
SWIM_NMS_VPN_<KEY>
SWIM_NMS_USER_<KEY>
SWIM_NMS_PASS_<KEY>
SWIM_NMS_QUEUE_<KEY>
```

All credentials live in `/etc/corporatetraveldc/dispatch-secrets.env` and all
six feeds are provisioned and live. To re-provision a single feed: update its
vars, then `scripts/ingest-feed-ctl.sh restart <feed>` — no code changes.

## Heartbeat / failover contract

- Each connected feed stamps `push:<feed>` in `feed_state` every 30 s
  (`HEARTBEAT_INTERVAL = 30`); `PUSH_FEEDS = ("fdps", "stdds", "fns", "tbfm",
  "tfms", "itws", "nws")`.
- The poller checks `push_is_healthy(feed, max_age=90s)` before each REST poll
  it has a push-primary for; a fresh heartbeat means push owns that feed.
- On disconnect the heartbeat ages out and REST polling resumes automatically.
- Reconnect backoff: 15/30/60/60/60 s. Stale-backlog handling:
  `SWIM_BACKLOG_STALE_SECONDS` (default 7200) /
  `SWIM_BACKLOG_RECENT_FRACTION` (default 0.10).
- Bad messages are captured to
  `/var/lib/corporatetraveldc/swim_bad_message_captures` (max 200).

## Parser status (all verified 2026-08-11)

| Feed | Parser | Status |
|---|---|---|
| FDPS | `fdps_parser.py` | **Live — FIXM 3.0 parser implemented 2026-07-20** (`_parse_fdps_message_fixm30`; version auto-detected by `_detect_fixm_version`, FIXM 4.2 path kept as legacy). Sources handled: `FH` (flight plan), `TH` (track), `CL` (cancel), `HP`/`OH` (handoff), `HZ` (heartbeat), plus `AH`/`BA`/`LH`/`HX` (generic extraction). Extracts gufi, callsign, squawk, origin/destination, aircraft type, registration, position, altitude, ground speed, controlling facility, flight status. Marine One / VIP detection (callsigns + squawks, 50 NM of DCA), watchlist matching, 50 NM approach alerts (10-min dedup). |
| STDDS/SMES | `smes_parser.py` | Live — ASDE-X surface tracks (`asdexMsg` position/mlat/adsb reports) at DCA/IAD/BWI → `surface_tracks` |
| STDDS/TAIS | `smes_parser.py` | Live — `TATrackAndFlightPlan` terminal radar tracks → `terminal_tracks` |
| TFMS | `tfms_parser.py` | Live — see message-type table below |
| TBFM | `tbfm_parser.py` | Live — `tbfm_sequences`, metering alerts |
| ITWS | `itws_parser.py` | Live — terminal weather products (microburst/wind shear/precip/etc., 25+ `product_msg_name` values observed), severity-gated `wx-alerts` |
| FNS/AIM | `aim_parser.py` | Live — digital NOTAMs → `notams` table |

### TFMS message types (`tfms_parser.py`)

Family 1 — `fiOutput > fiMessage[msgType]`, all 8 types implemented:
`TMI_FLIGHT_LIST`, `RSTR` (restrictions/MIT), `APTC` (airport config —
cached, alerts on rate drops ≥20% / IMC degradation), `GADV` (general
advisories → nas-alerts), `GDP`, `GS`, `FXA`, `TMI_UPDATE`.

Family 2 — `fltdOutput > fltdMessage[msgType]`, all implemented:
`FlightModify`/`FlightTimes`, `trackInformation`, `departureInformation`,
`arrivalInformation`, `flightPlanAmendmentInformation`, `FlightRoute`,
`flightPlanCancellation`, `FlightCreate`, `FlightScheduleActivate`,
`oceanicReport`.

Known-unhandled (silently skipped by design): `flightPlanInformation`
(a dead-code stub handler exists but is registered in neither dispatch
table), `FlightSectors`, `boundaryCrossingUpdate`, `RAPT`.

**`flightPlanAmendmentInformation` dedup (added 2026-08-10):**
`_handle_flight_plan_amendment` keys its dedup on message *content* —
`content_hash(f"tfms:amendment:{entry['id']}:{route_text}")` against the
shared 30-minute `_TFMS_ALERT_DEDUP` window — so an unchanged rebroadcast of
the same amendment is suppressed indefinitely while a genuinely new route
amendment fires immediately. This mirrors `_handle_track_information`'s
approach-alert pattern (trigger `tfms_track_approach`), which keys on entry-id
only because positions naturally change every cycle. Previously this handler
had no dedup beyond the generic 5-minute watchlist window.

## Local airspace monitoring (inside `ingest-core`)

`local_airspace.py` handles the two local RF feeds; sources degrade gracefully
if hardware is absent:

- **UltraFeeder ADS-B** — polls `ULTRAFEEDER_URL` `/data/aircraft.json` every
  15 s, 80 NM scan radius around DCA (`38.8816, -77.0910`). Note: the
  UltraFeeder container was down ~2026-08-10 → midday 2026-08-11 (ADS-B
  RTL-SDR dongle stopped enumerating on USB; restored by a hardware reseat —
  see `docs/INFRA_MAP.md` §11); the monitor degrades cleanly whenever it's
  dark.
- **ACARS** — TCP to the acars_router container (`ACARS_ROUTER_HOST` /
  `ACARS_ROUTER_PORT`, default `host.containers.internal:9080`), 10 s poll.

Alert routing (canonical, per module docstring):

| Event | Topics | Priority |
|---|---|---|
| Watchlist aircraft in range (≤30 NM) | `flight-alerts` + `dispatch` | 4 |
| Marine One / VIP callsign (≤50 NM) | `dispatch` only | 5 |
| Emergency squawk 7700/7500/7600 | `dispatch` | 4 |
| ACARS OOOI event for watched flight | `flight-alerts` + `dispatch` | 3–4 |

5-minute dedup per ICAO hex. Heartbeat files:
`/var/lib/corporatetraveldc/feed_state/{ultrafeeder,acars}.heartbeat`.

Tables: `local_aircraft`, `acars_messages`, `local_airspace_alerts`.

## Hardware setup (RTL-SDR dongles)

Dongles are addressed by EEPROM serial via udev symlinks — see
`docs/SDR_SERVICES.md` for the full serialization/udev procedure and the
current enabled/disabled service map. Quick reference:

```bash
rtl_eeprom -d 0 -s ADSB1090      # tag dongles by serial (one-time, idle)
rtl_eeprom -d 1 -s ACARS0130
# udev rules in /etc/udev/rules.d/99-rtlsdr.rules create
# /dev/rtl_sdr_adsb and /dev/rtl_sdr_acars symlinks
```

## Verifying

```bash
# Heartbeats (all push:* feeds should be fresh)
curl -s http://localhost:8000/api/v1/feeds | \
  python3 -c "import json,sys; [print(f['feed_name'], f['age_secs']) for f in json.load(sys.stdin)['feeds'] if f['feed_name'].startswith('push:')]"

# Recent ACARS traffic
sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db \
  "SELECT received_at, tail, flight, label FROM acars_messages ORDER BY id DESC LIMIT 10;"
```

Hourly health watch: `corporatetraveldc-ingest-feed-watch.timer` (at :05) runs
`src/poller/skills/ingest_feed_watch.py`, which checks `/healthz` +
`/api/v1/feeds` via `http://host.containers.internal:80` with a
`Host: dispatch.example.com` header (changed 2026-08-10 from a
direct `:8000` URL that was unreachable from inside the container), plus the
ntfy health endpoint — change-only pushes to `ops-health`, 6 h dedup.
Staleness thresholds live in `src/web/main.py` (`healthz()` and
`get_feeds()`): most REST feeds 900 s, `nws` 2700 s, `atcscc_opsplan` 7200 s,
`dca_fids`/`iad_fids` **600 s** (raised 2026-08-10 from 180 s, which was
tighter than the real 300 s poll interval), push heartbeats 300 s.
