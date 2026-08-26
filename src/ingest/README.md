# corporatetraveldc — Ingest Layer

**Rewritten 2026-08-11 against the live code and running system.** The previous
revision was a 2026-07-20 snapshot: it described FDPS FIXM 3.0 parsing and most
TFMS message types as unimplemented stubs (all live since 2026-07-20), and
referenced a unified `corporatetraveldc-ingest` systemd unit that was split into
seven per-feed containers on 2026-07-26 and no longer exists.

The ingest layer connects to push data feeds (FAA SWIM via NMS/Solace,
NWWS-OI XMPP, Amtrak) and writes events into the shared SQLite database.
The poller's REST fallback activates automatically whenever a push feed stops
stamping heartbeats — for the SWIM feeds and `nws`/`notam`. **Amtrak is the
exception: it has no wired fallback** (see the module table below).

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
| `corporatetraveldc-ingest-notam` | SWIM AIM/FNS — a full sixth SWIM feed, **not** a NOTAM-only afterthought | skip-all-but-fns — note the unit is named `notam`, the heartbeat/feed key is `fns`, and the credentials are the `AIM` set |

All 7 are **provisioned and credentialed** — all six SWIM feeds were
confirmed connected with fresh heartbeats on 2026-08-07, and all 7 units
were running when this was written on 2026-08-11.

> ⚠️ **Do not read that as "all 7 should be running right now."**
> `scripts/thermal-ingest-guard.py` (timer, every 2 min) deliberately
> **stops** SWIM ingest containers — and, under LOCKDOWN, most of the rest
> of the stack — under load or thermal pressure. **Thresholds were
> redesigned 2026-08-23** (temperature and load are no longer symmetric,
> because every real trip on record has been load-driven, never
> temperature-driven). Current defaults in `main()`, all overridable from
> `dispatch.env`. **Corrected 2026-08-23: seven of them *are* set there** —
> `dispatch.env:278-284` carries `THERMAL_GUARD_{ENABLED,TIER1_TEMP_C,
> TIER2_TEMP_C,RESUME_TEMP_C,RESUME_DWELL_S,TIER1_FEEDS,TIER2_FEEDS}`, all
> agreeing with the script defaults. What is genuinely unset is the
> *load/fallback* family added in the redesign (`LOAD_INFO_MIN`,
> `LOAD_LOCKDOWN`, `RESUME_LOAD`, `TEMP_INFO_C`, `FALLBACK_TRIGGER_COUNT`,
> `FALLBACK_WINDOW_S`), which therefore runs on script-only defaults — the
> intended `_cfg()` pattern, not a misconfiguration. Re-derive both sides
> with `grep -n 'THERMAL_GUARD_' scripts/thermal-ingest-guard.py` and
> `grep -n '^THERMAL_GUARD_' /etc/corporatetraveldc/dispatch.env`:
>
> | Trip | Condition | What's shed |
> |---|---|---|
> | Temp tier 1 | `temp ≥ 74.0 °C` | `THERMAL_GUARD_TIER1_FEEDS` = `tfms,stdds` |
> | **LOCKDOWN** | `temp ≥ 79.0 °C` **or** `load1 ≥ 40.0` **or** ≥2 load-attributed brief fallbacks in 300 s | all six SWIM feeds (`ALL_SWIM_FEEDS`) + `ingest-core` + `LOCKDOWN_USER_UNITS` (`poller`, `pusher`, `runner`) + host `ollama.service`. Only `web` survives — this set is a hardcoded constant, deliberately *not* env-tunable. |
> | Informational, no shed | `temp` 70–74 °C, or `load1` 15–40 | — |
> | Restore | `temp < 65 °C` **and** `load1 < 15.0` **and** fallback count < 2, held 300 s | tier 1 restores `tfms,stdds`; LOCKDOWN restores the whole stack |
>
> "Load-attributed brief fallback" comes from `common/llm.py`'s
> `_record_load_fallback()` (`/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl`),
> logged only for `OllamaBusyError` / generate-call `httpx.TimeoutException`
> — deliberately **not** for `ConnectError`, so a deliberately-stopped
> Ollama can never make ingest shed itself.
>
> So finding a unit `inactive (dead)` with `Result=success` is **expected
> behaviour, not an outage** — and restarting it by hand just masks the load
> problem until the guard sheds it again. Check
> `/var/lib/corporatetraveldc/thermal_ingest_guard_state.json` and
> `journalctl --user -u corporatetraveldc-thermal-ingest-guard` before
> treating it as a fault. See CLAUDE.md → "Ingest load-shedding".

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
| `nwws.py` | NWWS-OI XMPP MUC subscriber (`NWWS_WFO_FILTER` keeps only configured WFOs). Two silent-drop bugs fixed 2026-08-22: (1) the filter compared bare 3-letter codes (`LWX`) against the raw 4-letter ICAO `cccc` attribute (`KLWX`), so **every** configured-WFO product was dropped for the life of the feature — fixed by K-stripping normalization before the membership check (confirmed working: real KLWX/KAKQ rows in `nws_alerts` since the fix); (2) WPC products were looked up by AWIPS id (`PMDSPD`) against a `ttaaii`-keyed table (`FXUS02`) — fixed to prefer `ttaaii`. |
| `amtrak.py` | Amtrak push-primary poller (`api.amtraker.com/v3/trains`, 300 s). ⚠️ **This is the only live train-data path**: despite this module's docstring, the poller-side REST fallback does not exist in runnable form — `poller/fetchers/amtrak.py` has a `run()` but is wired into no schedule (dead code, confirmed 2026-08-22). If this loop goes down there is no automatic recovery for train data. |
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

## Parser status (verified 2026-08-11; TFMS/NWWS updates 2026-08-22 noted inline)

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

Family 1 — `fiOutput > fiMessage[msgType]`, all 9 types implemented:
`TMI_FLIGHT_LIST`, `RSTR` (restrictions/MIT), `APTC` (airport config —
cached, alerts on rate drops ≥20% / IMC degradation), `GADV` (general
advisories → nas-alerts), `GDP`, `GS`, `FXA`, `TMI_UPDATE`, and — added
2026-08-22 — `AFP` (Airspace Flow Program declarations/cancellations,
`_handle_airspace_flow_program` → `nas_programs`; FCA identifiers like
`FCAJX5` deliberately bypass the airport-based geo-filter, which would
otherwise silently drop every AFP row). Before 2026-08-22 every AFP message
fell into the unknown-msgType bypass and was never persisted.

**GDP/GS element-truthiness key bug — FIXED 2026-08-23** (found 2026-08-22,
originally deferred because fixing re-keys live `nas_programs` rows). Their
`elem_a or elem_b` start-time selection treated childless ElementTree
elements as falsy, so a text-only leaf like `startTime` always lost the `or`
chain and `program_id` actually keyed on `advisoryValidPeriod`'s start, not
`cumulativeProgramPeriod`'s (GDP) / `groundStopPeriod`'s (GS) as the
docstrings claim. All three instances (GDP, GS, and the milder
never-confirmed-to-have-manifested `ncsmFlightModify or ncsmFlightTimes`
selection in `_handle_flight_times`) now go through the shared
`_first_present(*elems)` helper, which does explicit `is not None`
selection. The AFP handler never had the bug — it used `is not None` from
day one. Regression coverage: `tests/ingest/test_tfms_gdp_gs_key_fix.py`.

The re-key risk is handled additively rather than by silently re-keying:
`SCHEMA_V36` adds `key_scheme` (NULL = N/A / REST-sourced / AFP, `1` =
written under the old wrong key, `2` = written under the fixed key) and
`legacy_correlate_id` to `nas_programs`; `init_db_v36()`'s one-time backfill
stamped every pre-existing GDP/GS row `key_scheme=1`, and
`write_tfms_programs()` stamps `key_scheme=2` plus a best-effort
`db.find_legacy_nas_program()` correlation on every new GDP/GS write.
Nothing is deleted or renamed. **Retention guarantee (operator directive):
nothing in this repo prunes `nas_programs` today — if a prune job is ever
added it MUST exempt `key_scheme=1` rows younger than 30 days.**

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
`dedup_key = content_hash(f"tfms:amendment:{entry['id']}")` (per-flight
identity) paired with `content_key = content_hash(route_text or "")` (the
amendment content), split this way by the 2026-08-16 shared-slot fix so
distinct flights no longer collapse into one dedup slot — against the
shared 30-minute `_TFMS_ALERT_DEDUP` window, so an unchanged rebroadcast of
the same amendment is suppressed indefinitely while a genuinely new route
amendment fires immediately. This mirrors `_handle_track_information`'s
approach-alert pattern (trigger `tfms_track_approach`), which keys on entry-id
only because positions naturally change every cycle. Previously this handler
had no dedup beyond the generic 5-minute watchlist window.

## Local airspace monitoring (inside `ingest-core`)

`local_airspace.py` handles the two local RF feeds; sources degrade gracefully
if hardware is absent:

- **UltraFeeder ADS-B** — polls `ULTRAFEEDER_URL` `/data/aircraft.json` every
  15 s, 80 NM scan radius around DCA (`39.0000, -77.0000`). Note: the
  UltraFeeder container was down ~2026-08-10 → midday 2026-08-11 (ADS-B
  RTL-SDR dongle stopped enumerating on USB; restored by a hardware reseat —
  see `docs/INFRA_MAP.md` §11); the monitor degrades cleanly whenever it's
  dark.
- **ACARS** — TCP to the acars_router container (`ACARS_ROUTER_HOST` /
  `ACARS_ROUTER_PORT`, default `host.containers.internal:9080`), 10 s poll.
  ⚠️ **`acars_messages` has zero rows ever** (re-confirmed 2026-08-23:
  `SELECT COUNT(*)` → 0): the `acars.heartbeat` file only proves the reader
  thread is alive, not that data flows. Since 2026-08-22 the reader tracks
  `lines_received`/`parse_failures` and logs a throttled connected-but-idle
  `WARNING` (`ACARS_IDLE_WARN_S`, default 1800 s) — check those counters
  before trusting the heartbeat. **That instrumentation has now answered
  its own question:** every idle warning in `journalctl --user -u
  corporatetraveldc-ingest-core` reports `lines_received=0
  parse_failures=0` (12+ continuous hours on 2026-08-23 before the
  midday LOCKDOWN restart), so the acars_router is genuinely emitting
  nothing — this reader is not silently dropping an unparseable format.
  The real fix is upstream of this box (router config or its own feed
  source), not in `local_airspace.py`.

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
  python3 -c "import json,sys; [print(f['feed_name'], f['age_seconds']) for f in json.load(sys.stdin)['feeds'] if f['feed_name'].startswith('push:')]"

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
tighter than the real 300 s poll interval), push heartbeats 300 s — except
`push:tfms`/`push:tbfm`, which have no per-feed entry in that dict and fall
through to its 3600 s default (`stale_thresholds.get(name, 3600)`), per
`docs/DATA_SOURCES.md`.
