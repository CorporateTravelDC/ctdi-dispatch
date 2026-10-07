# CHANGES — data domain

Verified against HEAD db64018 and live state on 2026-10-06 18:12Z / 14:12 ET.
Live evidence: `curl -s http://127.0.0.1:8000/healthz` (18:04Z, `status: ok`),
`curl -s http://127.0.0.1:8000/api/v1/feeds` (18:05Z, 20 feeds), `podman ps`,
`systemctl --user list-timers --all`, `systemctl list-timers`, read-only
reads under `/var/lib/corporatetraveldc/`. Secrets were never read; presence
of a few non-secret names in `/etc/corporatetraveldc/dispatch.env` was
checked with `grep -c` only.

Note on the brief: `/healthz` does not carry a feed list (it returns
status/reason/snapshot age/audit count/token count/CPS). The feed list with
ages and thresholds is `/api/v1/feeds`; the DATA_SOURCES table now matches
its 20 `feed_name`s exactly.

Corrected claims: **112** across 10 documents (one per table row below).

---

## docs/DATA_SOURCES.md (33)

| claim as written | evidence | fix |
|---|---|---|
| ATCSCC ops plan "polled directly from the ATCSCC public server" | `src/poller/fetchers/atcscc_opsplan.py:1-22` — synthesizes from local NAS/NOTAM/METAR | rewritten: not an external feed |
| FAA NOTAM API at api.faa.gov, creds `FAA_NOTAM_API_KEY`/`_SECRET`, "never credentialed" | `poller/fetchers/notam.py:1-42,74-78` NMS-API, `NMS_API_CLIENT_ID/SECRET`; feeds row `error: null`, fetched 2026-10-05 13:37Z (failover test) | rewritten |
| `push:fns` simply "live" since 08-02 | CLAUDE.md 2026-10-05: VPN was `AIM`, push never delivered until fixed to `AIM_FNS`; `grep -c '^SWIM_NMS_VPN_AIM=AIM_FNS$' dispatch.env` = 1 | added VPN detail |
| `SWIM_NMS_VPN_<KEY>` absent from cred list | `ingest/config.py:136` | added |
| Amtrak: ingest-core is a push writer | `ingest-core.container` `Environment=AMTRAK_ENABLED=false` | corrected — only amtrak-tracker writes |
| "No scheduled REST fallback; poller/fetchers/amtrak.py dead code" | `poller/main.py:68-69` amtrak in FETCH_SCHEDULE with `REST_FALLBACKS["amtrak"]` (660 s) | corrected |
| amtrak-tracker "serves locally on :8898" | no port in `src/amtrak_tracker/main.py` or its quadlet (no PublishPort) | removed |
| ingest-stdds CPU 120% / tfms 90% / tbfm 80% | quadlets: 140% / 110% / 100% | corrected |
| ingest-core runs Amtrak | `AMTRAK_ENABLED=false` | corrected |
| Backlog floor default 7200 s (2 h) | `swim_client.py:77` 28800 (8 h, 2026-09-18); no live override | corrected |
| skip-branch line refs (~541, ~824, ~835), `_LOW_PRIORITY_FEEDS` at :271 | now `:277`, skip logic `:858-890` | line refs dropped, function names kept |
| Thermal restore `load1 < 15.0` | `thermal-ingest-guard.py:631-632` resume = 40 × 0.5 = 20 (2026-10-03) | corrected |
| Thermal guard has no dormancy / serialized restore | script docstring + `RESTORE_DORMANT_S` 600, serial-bringup | added |
| "Six further knobs are script-only defaults" | script reads 15 knobs; live `dispatch.env` sets 7 | corrected list |
| `dispatch.env (lines 278-284)` | line refs not stable; names checked with grep | removed |
| `guard_label` line ref `:574-576`, label list incl. "Ollama-contention" | `:737` builds label; contention no longer trips | trimmed |
| "Observed reality 2026-08-23" LOCKDOWN narrative | historical, trigger demoted 2026-08-27 | removed (history kept in git) |
| `ingest-feed-ctl.sh` default stagger 15 s | header `:23-29`: 60 s window via serial-bringup, load gate | corrected |
| scheduled-ingest-restart behaviour unspecified | `scripts/scheduled-ingest-restart.sh:103-106` 90 %, 300 s; timer 2 min | added |
| feed table "eleven rows" with 2026-08-23 live column | `/api/v1/feeds` 18:05Z: 20 rows | replaced with 20-row table |
| `push:*` heartbeat cadence uniform 30 s | `push:amtrak` stamped per 300 s poll (`failover.py:30-33`) | corrected |
| EUROCONTROL/JASDAT "stubs exist (empty) in dispatch-secrets.env" | secrets not read; allowlists list the names | dropped the secrets-content claim |
| EUROCONTROL failure = NameResolutionError | pull_detail 18:00Z: `ConnectionError … Max retries exceeded` | reworded |
| NWS endpoint `area={STATES}&severity=...` | `nws.py:27` `area=DC,MD,VA` + 3 zone forecasts | corrected |
| METAR URL `format=raw&hours=1` | `metar.py:19-22` adds `taf=false`; station list | corrected |
| Aircraft lookup "foreign-registered aircraft will 404" | `web/main.py:2300-2330` docstring: FAA + OpenSky, `source` faa/opensky/faa+opensky | corrected |
| ACARS: token read at `acars_watcher.py ~88`, runner `_acarsdrama_messages` primary | `common/acars.py:1-27,56-67`: Jumpseat → airframes.io → local acarshub | rewritten |
| Local ACARS stack (implicit acarsdec) | `podman ps`: dumpvdl2, acarsrouter, acarshub up; acarsdec, dumphfdl `.disabled` | added |
| Credentials "in dispatch-secrets.env" (everywhere) | `scripts/service-env/generate.py` docstring; quadlets mount `svc/<svc>.env` | added scoped-env section |
| Kpler token simply "in dispatch-secrets.env" | `KPLER_MARITIME_API_TOKEN` on no allowlist | corrected + finding |
| AeroAPI: "set both" names | `FLIGHTAWARE_AEROAPI_KEY` on no allowlist | corrected + finding |
| `/healthz` and `/api/v1/feeds` share push-cover logic | `web/main.py:783` uses `failover.push_covers()`; `:885` hard-codes nws/notam | documented in §8 + finding |
| `poller/fetchers/ops_plan.py` "driven from somewhere else" | `grep -rn "fetchers.ops_plan"` → no importer | marked dead code |

`[UNVERIFIED]` kept in text: whether `FLIGHTAWARE_API_KEY`, `AIS_AISHUB_ID`
have values; `acars_messages` row count.

## docs/ALERT_ARCHITECTURE.md (12)

| claim as written | evidence | fix |
|---|---|---|
| `_throttle_allows()` at `sector_coalesce.py:301-309`, `_load_silence_state()` at `:371` | now `:391`, `:492` | refs by name |
| No cold-start guard | `sector_coalesce.py:86-100` `_PROCESS_START`, 2 × window | added |
| `fdps` family isolated siblings: only `fdps_notam` | `fdps_parser.py:1184,1391` `fdps_alt_saturation`, `fdps_diversion_continuation` isolated, p4 | added |
| `fdps_notam` base priority 4 | `aim_parser.py:472-475` `tfr_pri or 4` | corrected |
| STDDS priority split at `smes_parser.py:518-521` | `:535-538` | ref updated |
| Topic count "87 (2026-08-23)" | `ntfy-topic-count-watchdog.sh --status` 18:12Z: 86, peak 111 | updated |
| INFRA_MAP §8.1 | no §8.1 in `docs/INFRA_MAP.md` (§8 at :722) | corrected ref |
| Layer 2 "escalating" implies a worsening trend | count-based (`record_event`); operator backlog 2026-10-05 | added caveat |
| Layer-model scope omits pusher | `pusher/main.py:57-65,127-128,662-669` (`hot_push` + Pushover) | added |
| `zatl` facility table "Representative airports" incl. ATL only etc. | `_ARTCC_GROUPS:136-160` | replaced with exact sets |
| `OTHER` sector undocumented | `resolve_sector()` `:516-522` | added |
| stdds_safety silence "2026-09-05 live" | silence file 18:12Z `silenced_feeds: ["stdds_safety"]` | re-confirmed |

## docs/ALERT_REFERENCE.md (24)

| claim as written | evidence | fix |
|---|---|---|
| fire_family_alert lines tfms `:583/:1262/:1348`, tbfm `:323`, fdps `:923`, itws `:696`, aim `:317/:335`, smes `:390/:584/:902/:1047` | grep 18:12Z: tfms 603/1501/1600, tbfm 497, fdps 1841, itws 778, aim 472/490, smes 407/607/1031/1283 | updated |
| TFMS gates `:541/:1232/:1311`, `_DC_FACILITIES` `:136` | `:551`, `:1471`, `:1550`; `:136` ok | updated |
| STDDS dedups at `:540-542`, bitmask early return `:869` | `:561-563`, `:982` | updated |
| ITWS dedup `should_push` at `:657` | `:739` instance, `:766` `should_push_periodic` | corrected semantics |
| Marine One = callsign/squawk | `fdps_parser.py:200-210` adds `^CRANE\d{2}$` | added |
| VIP NOTAM = POTUS/AF1/Marine One keywords | `aim_parser.py:273-282` + callsign regex + CRANE callsign | added |
| `vessel-alerts` refs `:371-381`, `:444-445`, `:534-535` | `watchlist.py:665`, `web/routes/watchlist.py:469,558` | updated |
| pusher docstring topics (`ops-brief`, `ops-health` freshness) | `pusher/main.py` functions: VIP TFR, CPS, wx, landings, retries | rewritten §4 |
| No Pushover channel mentioned | `pusher/main.py:57-65` `hot_push()` | added |
| `wx-alerts` publishers = itws only | `pusher/main.py:662` wx change | added |
| approval-gate: Allow/Deny buttons, p4 | `sudo-approval-gate.sh:63-68,121-141` deny-only, p5 rules | corrected |
| watchdog system services include `unbound` | `watchdog.sh:127-131` pihole-FTL, cloudflared, tailscaled | corrected |
| watchdog.sh runs from checkout | `systemctl cat corporatetraveldc-watchdog.service` ExecStart `/usr/local/libexec/ctdc/watchdog.sh` | corrected |
| `web/main.py:2409-2418` watchdog status | `:3013` | ref updated |
| thermal-guard alert lines `:432/:586/:603/:651/:668` | `ntfy_alert` `:553`; labels `:737/:756` | refs dropped |
| thermal restore load 15 | 20 (see DATA_SOURCES) | corrected |
| ~20 standalone publishers not listed | sweep of `scripts/` for topic literals (19 additional scripts) | added row, priorities `[UNVERIFIED]` |
| `unit-failure-notify.sh` absent | `OnFailure=corporatetraveldc-unit-failure-notify@%n.service` on 7 units | added |
| `ep` topic used by daily watches (implied) | `aviation_daily_watch.py:116,202` — `"ep"` is a framing key | not listed |
| board_sweep `ops-health` p3 only | `board_sweep.py:296-298` `hot-alerts` p5 email; `:372` ops-health | corrected |
| weekly_summary `send_dual` at `:209` | `:210`, `email=True` | updated |
| ops_brief refs `:450`, `:764` | `:460`, `:866` | updated |
| OOOI watchlist events absent | `db.py:6095-6170`, `poller/main.py:1094,1487-1527` | added §5 (ADS-B ON/IN deny, tfms_airline SMES deny, departure overdue) |
| `ntfy_push` "only path with a fallback" fallback unset | `config.py:52`; `grep -c '^NTFY_FALLBACK_URL=.' dispatch.env` = 0; ntfy quadlet `PublishPort=2586:2586` | re-confirmed |

## docs/DCA_IAD_FIDS.md (6)

| claim as written | evidence | fix |
|---|---|---|
| resolver tiers swim → website → none | `flight_resolver.py:300-360` includes `aeroapi` tier | corrected |
| live counts 2026-08-23 | 18:08Z DCA 875/867, IAD 679/678 | updated |
| `/arrivals` DCA example answered from website | 18:08Z `source_used: "swim"` | updated |
| `flight_resolver.py:73` | `:71` | ref updated |
| FIDS role unstated | `db.py:6108-6113` FIDS denied OOOI | added |
| times' timezone unstated | `airport_fids.py:39-52` `_AIRPORT_TZ` America/New_York | added |

## docs/CIFP_DATA.md (5)

| claim as written | evidence | fix |
|---|---|---|
| tables in `db.py SCHEMA_V45` | Postgres `pg_schema/0052_reference_cifp.sql` | corrected |
| cifp_lookup exposes 4 functions | `cifp_lookup.py` 8 public functions incl. `runway_eta_epoch` | updated |
| "Not yet done": no consumer | `runway_eta_epoch` used by `fdps_parser.py:1684`, `tbfm_parser.py:583`, `tbfm_arrival_enrichment.py` | added consumers; `expand_arrival_route` still unwired |
| zip name `CIFP_<cycle>.zip` | on disk `CIFP_261001.zip` (effective-date form) | corrected |
| parse "refused" | refusal lives in `db.cifp_replace_all()` `:7783-7797` | corrected |

## docs/LADD_CUI_HANDLING.md (3)

| claim as written | evidence | fix |
|---|---|---|
| import takes two files | `import-ladd-filter.py:97-108` `--remove FILE` repeatable | added |
| no removal persistence | `pg_schema/0063_ladd_removals.sql`; `_apply_removals` after every import | added |
| no live count | `/api/v1/aircraft-registry/status` ladd 73,369 | added |

## docs/REGIONALIZATION.md (8)

| claim as written | evidence | fix |
|---|---|---|
| "Three files contain all DC-specific geography" | ≥ 15 constants (table in rewrite), e.g. `metar.py DC_STATIONS`, `nws.py ALERTS_URL`, `aim_parser._PERMANENT_AIRPORTS`, `db._SMES_AIRPORTS` | replaced with full table |
| NOTAM zones not mentioned | `ingest/config.py` `NOTAM_HOME_ARTCCS`/`NOTAM_MONITOR_ARTCCS`; live ZDC / ZNY,ZID,ZOB,ZLA,ZTL | added |
| `NWWS_WFO_FILTER=LWX,AKQ,CTP,PHI` example as config | template now `CHANGE_ME` | presented as guidance only |
| persona labels DC METRO / NORTHEAST / TRANSCON HUBS, `SYSTEM_PROMPT` | `personas.py:598-626` LEAD, DC METRO, NAS PROGRAMS, … ; `task` field | corrected |
| sole Amtrak path = ingest/amtrak.py; poller fetcher dead | amtrak-tracker + scheduled REST fallback | corrected |
| JMA/KMA/NAIPS/CMA "stub in dispatch-secrets.env" | secrets not read; names read by no code | reworded as research |
| aircraft endpoint "plus OpenSky cross-check" | FAA + OpenSky with `source` field | clarified |
| ingest container "SWIM slots can be adapted to NM B2B" | EUROCONTROL is a poller REST fetcher | dropped |

## docs/GPS_COORDINATE_CONFIGURATION.md (4)

| claim as written | evidence | fix |
|---|---|---|
| READSB/TAR1090 sourced from dispatch-secrets.env | ultrafeeder quadlet `EnvironmentFile=…/ultrafeeder-secrets.env` (since 2026-08-26) | corrected |
| ULTRAFEEDER_LAT read from dispatch-secrets.env by containers | allowlists ingest/poller/runner/web → `svc/<svc>.env` | corrected |
| consumers: runner, web | also `ingest/local_airspace.py:47-48` | added |
| feeder list | ultrafeeder `ULTRAFEEDER_CONFIG`: FlightAware, FR24, ADSBHub, airplanes.live, OpenSky MLAT | corrected |

## docs/POSTGRES_MIGRATION.md (14)

| claim as written | evidence | fix |
|---|---|---|
| `max_connections=60` | `config/postgresql.conf:44` = 100 (2026-09-20) | corrected |
| pool ≤ 4 (ingest 2) | `db_backend.py:492-510` default 8, per-process | corrected |
| "65-table write path", "76 of 76 tables" | `PG_TABLES` = 102; 122 distinct tables in pg_schema | corrected |
| migration count implicit / "48 files" (ingest/main.py comment) | 73 files 0001–0073, contiguous | stated |
| `DISPATCH_PG_PASSWORD` in dispatch-secrets.env read by apps | scoped `svc/*.env` allowlists | corrected |
| "§4 Phase 1 — access layer (next)" | shipped (db_backend.py live) | rewritten as as-built |
| Phase 4 weekly partitions | `grep -rn "PARTITION BY" pg_schema` = 0 | marked not implemented |
| retention "brought up to 90 at cutover" | `flight_events_cleanup.py:56` 30; `db.prune_train_events(days=30)` | corrected + finding |
| 23 GB SQLite file "archived, not deleted" | `corporatetraveldc.db` 69 KB on disk | corrected, archive location `[UNVERIFIED]` |
| SWIM backlog default 2 h | 8 h | corrected |
| "Each app quadlet needs one Volume line when switched" | 55 tracked quadlets mount the socket | as-built |
| `config/dispatch.env` only | also live `/etc/.../dispatch.env` (grep -c = 1) | stated |
| `scripts/safe-pg-image-update.sh` absent | exists | added |
| §3.3 memory narrative (llama tiers 7.2 G etc.) | not re-measurable read-only | removed |

## docs/CAUSAL_REASONING_ROADMAP.md (3)

| claim as written | evidence | fix |
|---|---|---|
| Phase 2 lexicon has no system entities | `lexicon.py` has 27 `system` entries; ops mechanisms absent | corrected |
| `ollama.service` as a Phase 2 entity | retired 2026-08-27 | noted |
| watchdog runs `scripts/watchdog.sh`; restricts unbound | installed copy `/usr/local/libexec/ctdc/watchdog.sh`; SYSTEM_SERVICES 3 | corrected |

## [UNVERIFIED] items left in the docs

- Values of `FLIGHTAWARE_API_KEY`, `AIS_AISHUB_ID`, Pushover keys (secrets not read).
- `acars_messages` row count; current CIFP cycle row counts; applied migration set (database not queried).
- Where the former 23 GB SQLite main file went.
- Priorities of 19 newly catalogued standalone ntfy publishers.

---

## Findings for the operator

1. **`/api/v1/feeds` push-cover logic diverges from `/healthz`.**
   `src/web/main.py:885` hard-codes `push_covers = {"nws","notam"}` and a
   300 s window; `/healthz` (`:783`) uses `failover.push_covers()`. Live
   18:05Z: `amtrak` age 26,459 s vs 3600 s threshold, `push_covered: false`
   in `/api/v1/feeds` while `push:amtrak` is 171 s old — the dashboards show
   a healthy feed as stale. Same handler leaves `push:tfms`/`push:tbfm` on
   the 3600 s default although `failover.push_stale_thresholds()` says 300 s.
2. **Freshness audit and daily brief notify nobody.**
   `freshness_audit.py:10` and `daily_brief.py:11` docstrings claim ntfy
   pushes; neither file calls ntfy. The freshness audit timer is enabled
   (daily 06:00 America/New_York, 10:00Z while EDT). Unchanged since the 2026-08-19 finding.
3. **No alert if ntfy dies.** `ntfy.container:6` `OnFailure=ntfy-container-alert.service`;
   that unit does not exist (`systemctl --user list-unit-files`).
   `NTFY_FALLBACK_URL` unset; ntfy publishes only 2586. Only the pusher's
   Pushover co-fire for p5 events is independent of ntfy.
4. **Two secrets cannot reach their consumers.** `FLIGHTAWARE_AEROAPI_KEY`
   (`poller/main.py:626`) and `KPLER_MARITIME_API_TOKEN` (`runner/main.py:76`)
   are on no `scripts/service-env/*.allowlist`; setting them changes nothing.
   Low impact today (both dormant) but silent.
5. **Retention below policy; partitioning not built.** `flight_events` and
   `train_events` delete at 30 days (`flight_events_cleanup.py:56`,
   `db.prune_train_events`), against the 90-day floor decided 2026-09-05;
   no partitioned tables exist.
6. **Schedule-inferred IN bypasses the OOOI authority gate.**
   `poller/main.py` (`_check_flight_schedule_inference`) writes `in` via
   `db.update_watchlist_oooi_phase`, not `update_watchlist_oooi_phase_authoritative`.
   It does require ACARS `on`/`in` first, so it matches the stated rule, but
   the write is not recorded through `_oooi_authority_check`.
7. **`/admin/watchdog/status` is permanently `available: false`** —
   `watchdog-last-run.json` is never written (file absent 18:12Z).
8. **Tracked file carries a feeder UUID literal.**
   `.config/containers/systemd/corporatetraveldc-ultrafeeder.container:58`
   `ULTRAFEEDER_CONFIG=…uuid=<literal>…`, while the comment at `:30-34` says
   the UUID comes only from `ultrafeeder-secrets.env`. Check whether it
   should be scrubbed from the public mirror.
9. **Dead code / stale comments (low):** `poller/fetchers/ops_plan.py` has
   no importer; `swim_client.py:8-14` docstring still maps stdds→TFRs and
   tfms→NAS; `pull_path_verify.py` comment names `FAA_NOTAM_API_KEY`;
   `ingest/main.py` says "48 files" (73 now); `ingest-feed-ctl.sh:28` says
   `SERIAL_BRINGUP_LOAD_MAX (12)` (code 18); `pusher/main.py` docstring
   topic list; `weekly_summary.py:9` says `ops-brief`; the
   `flight_resolver` website-fallback `note` claims SWIM "can structurally
   never match" while live `/arrivals` answers from SWIM;
   `config/dispatch.env.example` lists `ULTRAFEEDER_LAT/LON` as non-secret
   while deployment treats them as secrets.
10. **`bandwidth_priority_state` row** still carries `set_by: auto-ollama`
    (inactive) from before the 2026-08-28 removal — cosmetic.
