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

> **2026-08-30 SWIM-audit update** (external "unread SWIM fields" document
> + independent blind sweep; every item below verified against real
> captured samples under `/var/lib/corporatetraveldc/*_debug*`, never
> against the document's own claims):
> - **FDPS batch fix**: real FIXM 3.0 `MessageCollection` documents batch
>   up to 100 `message` children; the parser only ever unwrapped the
>   first, silently dropping the rest before any geo filter, Marine One
>   check, or watchlist check. Fixed via `parse_fdps_messages()` (one
>   dict per child). `flight_events` gains
>   squawk/registration/controlling_facility (parsed since 2026-07-20,
>   dropped at write time until now), plus same-GUFI destination-change
>   logging to `fdps_destination_changes`.
> - **STDDS/TDES/APDS**: the queue also carries RVR
>   (`RVRDataUpdateMessage` → `stdds_rvr`), tower departure events with
>   gate numbers (`TowerDepartureEventMessage` → `tdes_departure_events`
>   + watchlist hit), TDLS PDC/CPDLC text (`TDLSCSPMessage` →
>   `tdls_messages`, envelope+raw body only), and digital ATIS
>   (`DATISData` → `datis_snapshots`) -- all previously fell through the
>   handler chain unparsed despite real captures in `smes_debug/`.
> - **TFMS FADT**: per-flight EDCT/slot broadcasts (GS/GDP controlled
>   departure/arrival times) → `tfms_edct_slots` + watchlist hit; was in
>   the unknown-msgType bypass like GDP/GS/AFP before it. Still
>   deliberately unhandled there: REROUTE, PARAM, FXASF, CMPR.
> - **ITWS**: Runway Configuration Product (active runway config,
>   DC-sited), Terminal Weather Text Normal/Special -- all three real,
>   tiny, previously dropped as unrecognized.
> - **TBFM**: no `<sta>` schedule family has EVER been observed in a real
>   capture on this queue (the external document's flagship claim);
>   rather than parse a guessed schema, an unknown-`<air>`-child capture
>   (`tbfm_debug_unknown_kind/`) now records any non-flt/eta shape that
>   ever arrives, settling the question empirically.
>
> New tables live in `common/db_swim.py` (`init_db_swim_v41()`, called
> from `ingest/main.py`) -- see that module's docstring for why they are
> not a `db.py` `SCHEMA_Vn` this time.
>
> **2026-08-30 afternoon pass** (audit backlog items; built + tested,
> **staged only -- deliberately NOT deployed**, see below):
> - **RVR -> CPS**: `poller/skills/cps_recompute.py` now prefers
>   touchdown RVR on the ITWS-active-config runways (worst-of fallback
>   across all reporting runways) over METAR `visibility_sm`, converting
>   via the official 14 CFR 91.175(h) RVR/visibility correlation table
>   (values transcribed in that file -- operator must verify against the
>   CFR at sign-off). Saturated 6000+ readings and offline sensors
>   (NULL) defer to METAR; the chosen source is recorded in the score's
>   own narrative and the RVR rows ride the SR-2 input hash. **This is a
>   Part 135.609 scoring change and must not go live before the
>   operator's own sign-off** -- hence staged-not-deployed for the whole
>   pass.
> - **TDLS body parsing**: `smes_parser.parse_tdls_dcl_body()` regex-
>   extracts SID/transition/expected runway/EDCT/altitudes/frequency/
>   route from the raw PDC/CPDLC-DCL bodies (v42 nullable columns on
>   `tdls_messages`; raw body still stored verbatim). Every pattern
>   derived from real captured bodies -- including a real
>   "REVISED EDCT 1330" (UAL1803/KPIT) and the asterisked REVISED-RTE
>   origin ("KMIA*.FOLZZ3...", AAL861 KMIA->KDCA).
> - **TFMS PARAM + REROUTE**: both left the unknown-msgType bypass.
>   PARAM (paramGsUpdt/paramAfpGdpUpdt) -> `tfms_param_delay_stats`
>   (modeled GS/GDP delay statistics, PROPOSED never overwrites ACTUAL);
>   REROUTE -> `tfms_reroutes` (advisory general data + waypoint-free
>   segment summary, `dc_relevant` precomputed; 7 of the 11 distinct
>   captured advisories were DC-relevant). Storage-only, no new alerts
>   yet. Still unhandled: FXASF, CMPR.
> - Schema: `db_swim.init_db_swim_v42()` (called from `ingest/main.py`).
>   Tests: `tests/poller/test_cps_rvr_20260830.py`,
>   `tests/ingest/test_swim_audit_20260830_pm.py`.
> - Deferred again: alternate-saturation detectors
>   (`fdps_destination_changes` had 0 rows when checked -- needs weeks of
>   accumulation), ITWS raster/heavy products (real grid-decode work).
>
> **2026-08-30 evening pass** (backlog items 2 & 3; built + tested,
> staged only, same not-deployed discipline as the afternoon pass):
> - **REROUTE -> watchlist**: REROUTE advisories carry NO per-flight
>   identifier (re-verified against all 15 captures: zero
>   aircraftId/gufi/callsign hits -- only airport/center scope lists,
>   route strings, waypoints), so per-flight matching is by SCOPE
>   IMPLICATION: a watched flight whose origin AND destination both sit
>   in an ACTIVE advisory's INCLUDE-segment origin/destin lists fires a
>   `tfms_reroute` hit (`_check_reroute_watchlist_hits`, 6 h dedicated
>   dedup keyed per entry+rerouteId, content-keyed on route/status/
>   window so a real revision fires immediately). Center-only-scoped
>   segments (very common; center codes even appear inside `<airport>`
>   tags) are a deliberate false negative -- no airport->center table
>   exists here to guess with. The FADT EDCT watchlist hit already
>   shipped in the morning pass; this pass pins its
>   quiet-on-identical-rebroadcast contract with a test.
> - **Destination-change normalization fix**: the detector's entire
>   first live day (7/7 rows) was FAA-vs-ICAO spelling flapping of one
>   airport across FDPS source types (E25->KE25, KMFR->MFR->KMFR,
>   KACK->ACK via FH/TH/CL), zero real diversions -- comparison is now
>   on normalized spellings (`fdps_parser._norm_airport`). The 7 noise
>   rows were left in place (append-only table; they age out of every
>   detector window).
> - **Alternate-saturation detector**
>   (`fdps_parser._check_alternate_saturation`): >=3 distinct flights
>   re-filing TO the same normalized airport within 60 min (return-to-
>   origin rows excluded) fires one `fdps_alt_saturation` family alert
>   (escalating_only=False, isolate=True; content-keyed on the flight
>   set so each additional convergent flight re-fires). **Threshold is a
>   conservative cold-start value, not a tuned one**: the planned
>   backfill-from-`flight_events` derivation is impossible -- that table
>   keys `flight_id` as PRIMARY KEY (verified: 910,138 rows == 910,138
>   distinct GUFIs), one current-state row per flight, no history to
>   reconstruct -- and the only live observation is a real-change rate
>   of ~0/hour. RETUNE from live rows in a few weeks.
> - Tests: `tests/ingest/test_swim_audit_20260830_eve.py` (8, all real
>   captures/isolated DBs). No schema change this pass.
>
> **2026-08-30 night pass** (backlog + new components; built + tested,
> staged only, same not-deployed discipline as the afternoon/evening
> passes):
> - **Diversion-continuation detector**
>   (`fdps_parser._check_diversion_continuation`): a
>   `fdps_destination_changes` B->C diversion followed within 6 h by a
>   NEW-GUFI filing C->B from the same callsign (or same registration
>   via `flight_events.registration` -- no continuation-naming
>   convention exists in this repo to match on) is recorded to
>   `fdps_diversion_continuations` (`db_swim.init_db_swim_v44`) and
>   fires ONE `fdps_diversion_continuation` family alert per pair,
>   gated by the table's own UNIQUE constraint. ACARS corroboration
>   (`acars_messages`, same tail, route/divert-consistent text) is
>   attached as `confidence="fdps+acars"` when present -- a bonus,
>   never a gate (operator refinement; this box's ACARS feed has never
>   produced a row). Runs only on first-sighting FH/AH filings. No real
>   pairs exist yet to validate against (0 real diversions on record) --
>   synthetic-row tests only, window is a cold-start value.
> - **`drone` watchlist entry type** (`shared/watchlist.py` EntryType +
>   `permanent_drones.json` in `_FILE_MAP`, same hot-reload as
>   train/vessel). Part 107 UAS deliberately do NOT use the 5-phase
>   OOOI machine: collapsed `launched`/`landed` status lives in
>   dedicated `uas_phase*` columns (`db.init_db_v43`,
>   `update_watchlist_uas_phase()` -- the TBFM dedicated-column
>   pattern, chosen over a parallel phase-order list because a UAS
>   cycles launched<->landed and would break the machine's forward-only
>   invariant; see the SCHEMA_V43 comment block). flight/train/vessel
>   OOOI is untouched; AAM/eVTOL remains OSINT-only (`common/aam_watch`),
>   no watchlist tie-in exists to migrate.
> - **utm_watcher** (`src/utm_watcher/`, quadlet shipped `.disabled`
>   like ais-watcher): OpenDroneID-shaped UDP listener (:5007, parser
>   is defensive best-effort -- NO receiver or vendored schema exists
>   to verify against) + inert USS API poller stub
>   (`USS_API_BASE`/`USS_API_KEY` empty by default, idles like the
>   pre-credential airframes.io precedent); watchlist synced from
>   entry_type=="drone" with `UTM_STATIC_IDS` pins.
> - **EP-Advance LLM path unified** onto `common.llm.generate()`
>   (both direct `ollama_post_with_retry()` call sites replaced,
>   matching ops_brief). Root cause of its reliable failures CONFIRMED
>   from llama-chat's journal: "request (5908 tokens) exceeds the
>   available context size (4096 tokens)" -- since the 2026-08-27
>   cutover all report traffic shares the chat port (`-c 4096`) while
>   this skill's prompt+persona is ~5908 tokens. The refactor does NOT
>   fix that (service-level sizing decision, operator's call);
>   `ollama_post_with_retry()` now logs the backend error body so the
>   next such failure is not an opaque 400.
> - Tests: `tests/ingest/test_swim_audit_20260830_night.py` (9,
>   isolated DBs; synthetic rows -- see above).
>
> **2026-08-30 late pass** (external SWIM diversion-detection document
> applied to the now-live continuation/alt-saturation detectors; built +
> tested, staged only, same not-deployed discipline):
> - **Operator-class gate** (`fdps_parser._operator_class`): per the
>   document, ~85% of raw continuation-shaped candidates nationwide are
>   fractional/charter (airline-SHAPED callsigns -- EJA/LXJ/... -- which
>   pass a tail-number filter) or tail-number GA running normal
>   multi-leg trips. Pairs from either class are STORED
>   (`operator_class` column, `db_swim.init_db_swim_v45`) but never
>   alerted; only `scheduled` fires. Prefix set is a starter allowlist
>   (only EJA/LXJ are document-measured; the rest common knowledge) --
>   extend from live stored rows.
> - **Net-change collapse** (`_net_destination_changes`): both detectors
>   now read each flight's NET change (earliest filed vs latest current,
>   normalized) instead of raw rows -- a destination that oscillates and
>   returns home (the document's KPHL<->KPIT flap) no longer counts as
>   converging or seeds a false continuation, and "originally filed
>   destination" now genuinely means the earliest value on multi-hop
>   amendments (chaining-rule condition 3). The evening spelling
>   normalization handled the SPELLING flavor only; this handles genuine
>   oscillation. Alt-saturation additionally got a growth-only re-fire
>   gate (`_ALT_SAT_LAST_ALERTED` subset check): a set SHRINKING as rows
>   age out / flap home was a new content-key under the old dedup and
>   would have re-alerted on fewer flights.
> - **Trap-5 guard**: a diverted leg FILED origin==destination
>   (maintenance/positioning) can never seed a continuation pair.
> - **diversionIndicator closed vocabulary** (`tfms_parser`):
>   AIRBORN_* (airborne divert, priority 4) split from GROUND_* (plan
>   abandoned/re-filed on ground, priority 3); values outside
>   {NO_DIVERSION, AIRBORN_NOCTL/CTL, GROUND_NOCTL/CTL} WARN once per
>   value per process (NAS behavior change alarm) and alert at airborne
>   urgency. Non-quiet members transcribed from the document -- locally
>   only ""/NO_DIVERSION have ever been captured.
> - Succession storage verified already directional (ordered
>   diverted->continuation columns + ordered UNIQUE); GUFI-keyed
>   matching means the document's match-by-recency wrong-plan bug does
>   not apply here. Deliberately NOT built (real, wanted, larger):
>   Detector C (cancellation classification via fdTrigger + never-flew
>   test + settle window) and Detector D (weather-attributed reroute
>   cost). Retention spot-check: no prune job touches
>   `fdps_destination_changes`/`fdps_diversion_continuations`.
> - Tests: `tests/ingest/test_swim_audit_20260830_late.py` (13, all
>   synthetic -- zero real diversions/non-quiet indicators on record).
>
> **2026-08-30 late-night pass** (the two detectors the late pass
> deferred, from the same external document; investigate-first scope --
> built only what real, already-flowing data supports; built + tested,
> staged only, same not-deployed discipline):
> - **Detector C (cancellations)**: there is no cancellation message,
>   only plan removal (`flightPlanCancellation` + `fdTrigger`) for
>   opposite-meaning reasons. `fdTrigger` WAS already flowing but went
>   nowhere: read only in `_handle_flight_plan_cancellation`, only for
>   watchlist-matched callsigns, into an alert detail dict -- never
>   stored. Now EVERY removal (national scope) is stored to
>   `tfms_plan_removals` (`db_swim.init_db_swim_v46`) with the closed-
>   vocabulary classification (`_REMOVAL_TRIGGER_KINDS`: cancellation /
>   superseded / left_coverage, warn-once on unknown values), LEG-keyed
>   (callsign+igtd+airports -- flightRef would invert the ranking, and
>   locally verified: TFMS removal messages carry NO gufi element).
>   Flew-anyway/reinstatement evidence comes from the same fltd stream:
>   an O(1) acid-attribute watch in the dispatch loop
>   (`_note_plan_removal_activity`, leg-corroborated by igtd or airport
>   pair) records departure/track/arrival (flew) vs FlightCreate/Modify/
>   etc (reinstated) into the row's evidence JSON; a throttled inline
>   sweep (`_maybe_sweep_removals`, 5 min) applies the document's full
>   confirmation conjunction (cancellation-kind AND US-surveilled origin
>   AND no flew-evidence AND not reinstated AND settle window past igtd
>   AND removal -- `TFMS_CANCEL_SETTLE_SECS`, default 3600, cold-start).
>   Watchlist alert behavior: cancellation-kind unchanged (priority 4);
>   superseded/unknown now honestly labeled "plan removed (kind)" at
>   priority 3 (reference data: 70-92% of those fly anyway). Storage
>   only otherwise -- NO confirmed-cancellation alert and NO per-airport
>   cluster detector yet (cold start: zero local removal history existed;
>   `db_swim.measure_removal_fly_rates()` re-derives OUR fly-rate
>   distribution once ~a week of rows accumulates -- the reference
>   percentages are a prior, not ground truth). Locally observed
>   triggers (12 real 2026-07-20 captures, snapshotted to
>   `tests/ingest/fixtures/swim_audit/tfms_plan_removals/`):
>   FD_FLIGHT_CANCEL_MSG x8, HCS_CANCELLATION_MSG x4 -- the other four
>   vocabulary members are document-transcribed, not yet locally seen.
>   No prune job covers the new table (same open item as
>   `fdps_destination_changes`).
> - **Detector D (weather-reroute cost) -- partial by design**: the
>   route-side half is real and built; the weather half is NOT, because
>   its required data does not exist on this box. Found: FDPS FIXM 3.0
>   carries the full agreed route (`agreed > route/@nasRouteText`) plus
>   the arrival runway estimate -- confirmed in fresh captures (JIA5230
>   filed "KDCA.CLTCH3.MAULS..." then re-expressed "KDCA./.MAULS...",
>   same GUFI) and 42,893 live `flight_events.raw_json` blobs -- but the
>   parser never extracted it, and the HU source type (which carried the
>   filed-route half of that very pair) was silently DROPPED by
>   `_KNOWN_SOURCES_FIXM30` (now added, same discovery path as HF/RH).
>   Built: route_text/eta extraction, `fdps_route_versions` incremental
>   distinct-(flight,route) table (v46; rides INSIDE write_flight_event's
>   DC-area gate for bounded growth), and the document's genuine-vs-noise
>   classifier (`_parse_nas_route`/`_classify_route_change`: genuine =
>   arrival-procedure change / dep-procedure change both-non-null / body
>   divergence; noise = re_expression, suffix_trim, entry_fix_only,
>   notation_only) with `eta_delta_min` cost per new version. Storage +
>   classification ONLY -- no alert (document: median reroute costs
>   nothing; the signal is the p90 tail, which needs accumulated rows).
>   BLOCKED, honestly: weather attribution requires an ARCHIVED,
>   timestamped convective-SIGMET polygon history ("was there weather
>   when THIS reroute happened") and none exists -- `web/main.py`/
>   `demo_api.py`'s airsigmet endpoints are LIVE-snapshot proxies that
>   store nothing, NWWS is WFO-filtered (LWX/AKQ/CTP/PHI) so AWC KKCI
>   convective SIGMETs never arrive, `international_aviation_feed` has 0
>   rows, and ITWS is terminal-scale. Legwork for a future pass: a small
>   poller fetcher on AWC's `/api/data/airsigmet` (hazard=CONV) + an
>   append-only polygon archive table; `tfms_reroutes` (archiving since
>   this afternoon, with declared origin/destin scope lists) is the
>   document-preferred scope-based attribution source once it has depth.
> - **Test-suite capture protection**: running the ingest test suite had
>   ALWAYS silently overwritten live `/var/lib/corporatetraveldc/*_debug*`
>   capture files (per-process capture counters restart at 0 under
>   pytest) -- proven this pass when a full-suite run destroyed the only
>   captured copies of the JIA5230 route pair minutes after capture
>   (field values survive as repo fixtures with provenance headers;
>   `fdps_debug_fixm30/sample_{0,1}.xml` + `fdps_debug/sample_{0,1}_unk.xml`
>   currently hold fixture-derived content from the same incident until
>   the next fdps container restart naturally re-captures).
>   `tests/ingest/conftest.py` now redirects every capture-dir constant
>   (all 6 parsers + swim_client bad-message captures) into a session
>   temp dir, with hard asserts so a renamed constant can't silently
>   re-open the hole.
> - Tests: `tests/ingest/test_swim_audit_20260830_ln.py` (17: 12 real
>   removal captures + real route fixtures; synthetic XML only for
>   shapes with no real capture, labeled). Pre-existing, unrelated:
>   2 `tests/shared/test_watchlist.py` resolve_flight_identity tests
>   fail against COMMITTED code (ultrafeeder-first resolve vs stale
>   test expectations) -- not touched by this pass.
>
> **2026-08-31 pass** (the late-night pass's one explicit blocker:
> Detector D's convective-SIGMET archive; built + tested, staged only,
> same not-deployed discipline):
> - **Convective SIGMET fetcher + archive**
>   (`poller/skills/convective_sigmet_archiver.py`, 10-min quadlet
>   timer `corporatetraveldc-convective-sigmet-archiver.{container,timer}`):
>   independent fetch of AWC's `/api/data/airsigmet` JSON (the same
>   unblocked Data API the web overlay uses; live-verified this pass --
>   16 convective SIGMETs active nationwide at test time), filtered to
>   `hazard == CONVECTIVE`, archived append-only to
>   `convective_sigmet_archive` (`db_swim.init_db_swim_v47()`, called by
>   the SKILL itself -- poller-owned, ingest/main.py deliberately does
>   not call it). Normalization factored into `common/airsigmet.py` and
>   `web/main.py._normalize_airsigmet` now wraps it (color-only;
>   behavior-identical, response gains 3 ignorable keys). Two
>   live-observed corrections to the design brief: AWC serves
>   `validTimeFrom/To` as EPOCH INTS (converted to ISO at archive
>   time), and composite series ids RECYCLE across days (today's
>   KKCI-38E-E != tomorrow's), so the insert-once key is
>   `UNIQUE(sigmet_id, valid_from)`, not id alone. Full raw product
>   text stored (12/16 real records exceed the overlay's 600-char
>   display cap). NO prune job may cover the table (attribution
>   backtesting needs seasons of depth; verified absent from
>   `retention_prune._PRUNE_JOBS`, with a test pinning that).
> - **Deliberately NOT built**: the attribution matching itself
>   (joining a reroute's timing/route against archived polygons) --
>   zero archive history exists until this timer runs for a while, and
>   building match logic against near-zero data is the faked-data
>   anti-pattern every pass has refused. Future pass, once depth exists.
> - Tests: `tests/poller/test_convective_sigmet_archiver_20260831.py`
>   (13, against the verbatim live capture
>   `tests/poller/fixtures/awc_airsigmet_live_20260831.json`; synthetic
>   only for degenerate shapes, labeled). End-to-end smoke against a
>   scratch DB hit the live endpoint: 16 archived, re-run 0 (insert-once).

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
