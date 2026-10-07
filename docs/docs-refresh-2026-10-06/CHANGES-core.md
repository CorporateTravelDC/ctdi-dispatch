# CHANGES — core domain

Verified against HEAD 2c3f81b and live state on 2026-10-06 18:25Z / 14:25 ET.
(Work began at HEAD db64018; 2c3f81b landed at 18:10Z and touched only
`scripts/stack-refresh.sh` and the manifest, so no claim below depends on the
difference.)

Docs rewritten: `README.md`, `docs/INFRA_MAP.md`, `docs/REFERENCE_INFRA.md`,
`docs/executive_summary.md`, `docs/DESIGN-PRINCIPLES.md`,
`docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`, `docs/dispatch-runner-design.md`
(full rewrites); `docs/DISPATCH_PANEL_PARITY.md`, `docs/INTEGRATION_SPEC.md`,
`docs/CLIENT_DEMO_PATTERN.md` (targeted patches; rest verified true).

Live evidence commands used throughout (abbreviated below as **[ps]**,
**[units]**, **[timers]**, **[ss]**, **[probe]**, **[AST]**):
`podman ps`; `systemctl --user list-units --type=service,timer --all` and
`systemctl list-units`; `systemctl --user cat <timer>`; `ss -ltn`;
`curl` to 127.0.0.1 and to the public hostnames; a heredoc Python AST walk of
route decorators in `src/web/main.py`, `src/web/routes/*.py`, `src/runner/main.py`.

## README vs INFRA_MAP disagreements (resolved from code/live)

| Topic | README said | INFRA_MAP said | Truth |
|---|---|---|---|
| Postgres cutover date | 2026-09-18 | "live since the 2026-09-20 cutover" (§4) | 2026-09-18 write path; reference/chat/demo 09-19/09-20 (`docs/POSTGRES_MIGRATION.md`, `src/demo/recorder.py:20`) |
| llama restart timer | daily 03:00 ET | 19:45 ET | 19:45 ET (`systemctl --user cat corporatetraveldc-llama-restart.timer` → `OnCalendar=*-*-* 19:45:00 America/New_York`) |
| llama units | one unit | §3.2 "per-tier llama-hot … MemoryMax=4608M" | one unit, `MemoryMax=8448M` (`.config/systemd/user/corporatetraveldc-llama.service`) |
| acarshub port | `:9081` | `127.0.0.1:9081` | `100.x.x.x:8092 → 80` [ps], runner Quadlet `ACARSHUB_URL=http://100.x.x.x:8092` |
| `mcp.` hostname | 404 at edge | §9 "serves 502" | edge 404, local nginx 502 [probe] |
| Weekly cluster | — | "Sunday cluster aam-weekly 09:00 … second-brain weekly 18:15" | Monday ET: aam-weekly 02:00, dispatch-desk 03:00, weekly-summary 03:45, second-brain-weekly 04:30 [timers] |

---

## README.md

| claim as written | evidence | fix |
|---|---|---|
| "this README does not pin [counts]"; 2026-08-23 figures 122/39/63 | [ps] 35 running; `ls .config/containers/systemd/*.container \| wc -l` → 76; `grep -l '^Type=oneshot'` → 38 | Pinned current counts in a dated table with the commands |
| 63 Quadlets in repo, ccw-demo untracked | `diff <(ls .config/containers/systemd) <(ls ~/.config/containers/systemd)`: ccw-demo and ccw-preview1 are tracked; demo-portal-client/personal tracked but not live | Corrected |
| 65 migrations `0001`–`0065` | `ls src/common/pg_schema/*.sql \| wc -l` → 73 (0073_console_and_gateway_switch.sql) | 73 |
| Unix socket `/var/run/postgresql` | path absent on host; `corporatetraveldc-pgsql.container:63` `Volume=corporatetraveldc-pgsql-sock:/var/run/postgresql` | Socket is a shared volume inside containers; host uses 127.0.0.1:5432 |
| Demo "UP … replays a recent window" (implicit) | `ls -la /var/lib/corporatetraveldc-demo-source/` → `demo-source.db` 2026-08-14T14:21:42Z; `systemctl --user is-enabled corporatetraveldc-demo-source-refresh.timer` → disabled, `ExecMainExitTimestamp=` empty | Stated the data is frozen 2026-08-14 |
| `DEMO_SESSION_SECRET` set in the Quadlet | `corporatetraveldc-runner-demo.container:131` `EnvironmentFile=/etc/corporatetraveldc/demo-secrets.env`; only `DEMO_MODE=true` is in the Quadlet (:124) | Corrected |
| Commit `0a7f643 [SHA unverified; likely f63ee17]` | not needed for current state | Removed |
| Public MCP "Retired"; no mention of the gateway | `src/web/routes/agent_gateway.py` (10 routes); `curl https://agents.example.com/.well-known/oauth-authorization-server` → 200; cloudflared ingress `agents.` | Added agent gateway row; mcpo retirement kept as one row |
| Console, ES invite/members not mentioned | `src/web/routes/console.py`; tailnet `/console` 200 via `--resolve`; public `/console` 404; `invite.` 200, `members.` 302 [probe] | Added |
| "Not continuously running"; SWIM all live | `/api/v1/feeds`: every `push:*` 15–29 s old | Stated measured state |
| Ops dashboard vhost etc. | unchanged | kept |
| Architecture: "all 7 ingest … core (NWWS/Amtrak/RF)" | `Environment=AMTRAK_ENABLED=false` in every ingest Quadlet (e.g. `corporatetraveldc-ingest-core.container:45`, comment dated 2026-09-24) | core = NWWS + local airspace; Amtrak push is amtrak-tracker only |
| Core containers omitted pgsql, amtrak-tracker, verifier | [ps] | Added |
| ultrafeeder "port 8080" | [ps] `100.x.x.x:8080->80, :8081->80, 0.0.0.0:30005, 127.0.0.1:30003` | Corrected |
| acarshub `:9081` | [ps] `100.x.x.x:8092->80` | Corrected |
| Disabled SDR files under `systemd/quadlets/` | `ls systemd/` → `corporatetraveldc-{ais,ais-watcher,dumphfdl,utm-watcher}.container.disabled` in `systemd/`; acarsdec in `.config/containers/systemd/` | Corrected locations; added utm-watcher |
| openwebui/ntfy ports unqualified | [ps] `0.0.0.0:3000`, `0.0.0.0:2586` | Bind addresses added |
| ccw-demo, ccw-preview1 missing | [ps] | Added |
| Feeds table: 15 rows incl. "Runsheet", no amtrak REST | `/api/v1/feeds` → 20 rows; `FETCH_SCHEDULE` (`src/poller/main.py:34-74`) 12 entries incl. `amtrak` (REST fallback since 2026-09-06) | Rebuilt from the API + schedule |
| Amtrak: "Still no REST fallback … no FETCH_SCHEDULE entry" | `src/poller/main.py:60-70` amtrak entry; `src/ingest/failover.py:54` `push_max_age: 660` | Corrected |
| NOTAM REST needs `FAA_NOTAM_API_KEY` + `_SECRET` | `src/poller/fetchers/notam.py:74,78` `NMS_API_CLIENT_ID`, `NMS_API_CLIENT_SECRET` | Corrected names |
| METAR "AviationWeather.gov ADDS" | `notam`/`metar` fetcher URLs: `aviationweather.gov/api/data/metar`; `DC_STATIONS` 7 (`metar.py:26`) | Corrected |
| Thermal restore `load1 < 15.0` | `scripts/thermal-ingest-guard.py:631-632` resume_load = load_lockdown × 0.5 = 20 (2026-10-03); `dispatch.env` does not set `THERMAL_GUARD_RESUME_LOAD` | 20 |
| No dormancy window | `thermal-ingest-guard.py:638,657-663` `RESTORE_DORMANT_S` 600 | Added |
| "corporatetraveldc-llama-* units excluded" | `LOCKDOWN_USER_UNITS` (`:236`) = poller, pusher, runner | Reworded to the one llama unit |
| `FALLBACK_MAX_AGE` 90 s | `src/ingest/failover.py:38` = 90 | kept; added Amtrak 660 s override |
| "57 `@app.get()` routes; 37 anon / 10 tier / 10 admin" | [AST] main.py 82 decorators (61 GET/16 POST/4 DELETE/1 PATCH) + 44 in routes = 126; `require_tier` 15 (14 T1, 1 T2); `require_admin` 38 routes | Replaced |
| Tier 0 lists `/api/v1/osint/*` GET/POST/PATCH/DELETE | `main.py`: `/osint/feed` and `GET /osint/scopes` `require_tier(T1)`; POST/PATCH/DELETE `require_admin` | Moved to T1/Admin |
| Admin list incomplete (no sectors/osint/bandwidth) | [AST] | Rebuilt |
| Resolve route at `main.py:2601`, "UUID4 + single-use", 404 when unauthenticated | `main.py:2804-2827`: needs key `k` (403 if wrong), `action=allow` always 403 since 2026-10-04 | Rewritten |
| Runner routes list (no `/api/utm/drones`) | [AST] runner 30 decorators incl. `GET /api/utm/drones` | Added |
| RSS "11 categories, 32 feeds … 34 unique" + `python3 -c` command | static AST of `_RSS_CATALOG` → 11/32 | Kept 11/32; removed the `python3 -c` command (operator rule) and the unverifiable 34 |
| Watchlist "~65 s" hot-reload | `src/shared/watchlist.py:1112` `POLL_INTERVAL = 60` | 60 s poll |
| Transient sweep only | `src/poller/main.py:491-494` expiry 60, flights 120, trains 300, vessels 300 | Added |
| OOOI authority "ACARS > SMES > TFMS > TBFM/ADS-B > FIDS" | `src/common/db.py:5994` `{"fids": -1, "adsb": 0, "tbfm": 1, "tfms_airline": 2, "tfms": 2, "smes": 3, "acars": 4}`; deny rules `db.py:6126-6132` | TBFM outranks ADS-B; added tfms_airline + ADS-B ON/IN deny |
| "airplanes.live no longer queried programmatically" (implies fully local) | `src/runner/frontend/src/components/MapView.jsx:49` embeds `globe.airplanes.live` | Kept server-side claim; noted browser-side embed |
| Topic zones list | `src/shared/sector_coalesce.py:140-154` | kept |
| `reservations`/`calls` topics missing | `src/web/routes/webhooks.py:220,257,287` | Added |
| CPS hourly | `SKILL_SCHEDULE` cps-recompute `interval: 3600` | kept, cited |
| Recorder writes `demo.db`, config `recorder.py:41-43`, token in `dispatch-secrets.env` | `src/demo/recorder.py:20-23,45-56` → Postgres `demo_snapshots`; `demo.db` mtime 2026-09-21; demo Quadlet reads `svc/demo.env` | Corrected |
| Install: `cp dispatch-secrets.env.template …` only; build before sign; start web/poller/pusher | `config/dispatch.env.example` exists; scoped env generator `scripts/service-env/generate.py`; `scripts/pg-migrate.sh`; manifest gate needs sign→build | Rewrote steps |
| Install `build-models.sh` step | `build-models.sh` only diffs Modelfiles | Removed from install, kept in LLM section |
| External images: `weekly-external-image-update.sh` plain `podman auto-update` | `systemctl --user cat corporatetraveldc-weekly-external-image-update.service` → `ExecStart=…/scripts/stack-refresh.sh --weekly` | Rewrote: stack-refresh weekly + tripwire |
| Manifest gate "skill quadlets + llm.py + sweep" | `grep -l verified-exec .config/containers/systemd/*.container \| wc -l` → 39 (38 oneshots + demo-api) | Count added |
| `pytest` with no count | `grep -rc "def test_" tests/` summed → 877 | Added |
| Schema in `db.py`/`db_swim.py` is "the schema authority" | Postgres DDL is `src/common/pg_schema/*.sql`; top SQLite constant `SCHEMA_SWIM_V46` | Reframed as legacy/rollback |
| `-np 2 --kv-unified -c 12288`, `CPUQuota=200%` | unit file | kept; added full flags |
| "carries its own CPUWeight" | unit file `CPUWeight=9000`; `systemctl --user show corporatetraveldc-llama -p CPUWeight` → 10000 via `~/.config/systemd/user.control/…/50-CPUWeight.conf` | Stated drift |
| "~23 personas" / "~21 Modelfiles" | AST `PERSONAS` → 22 keys; `ls corporatetraveldc.* \| wc -l` → 21 | 22 / 21 |
| `OLLAMA_BASE_URL` is the name | `src/common/llm.py:364` `LLAMA_BASE_URL` first, `OLLAMA_BASE_URL` fallback; `dispatch.env` sets `LLAMA_BASE_URL` | Corrected |
| "models permanently resident per tier" | one model, one process | Corrected |
| SWIM creds in `dispatch-secrets.env` | ingest Quadlets `EnvironmentFile=/etc/corporatetraveldc/svc/ingest.env`; no Quadlet references `dispatch-secrets.env` in `EnvironmentFile=` | Corrected |
| `SWIM_NMS_VPN_<KEY>` (no default noted) | `src/ingest/config.py:136` default = key; `dispatch.env` `SWIM_NMS_VPN_AIM=AIM_FNS` | Added |
| `corporatetraveldc.db` "no longer exists … confirmed absent 2026-09-28" | `ls -la` → 69,632 bytes, mtime 2026-10-05 23:21Z | Corrected; finding |
| Audit log "90-day retention" | `src/poller/main.py` SKILL_SCHEDULE: `audit-log-prune` removed 2026-09-22, `audit-log-archive` daily, `audit_log_prune.py` "refusing stub" | Corrected |
| Reservation example `notes: "Smith pickup"` | — | Neutralised (no names) |
| License summary | `LICENSE:6-60` Licensor, version 2026.08, Change Date 2030-08-24, GPL v3+ | Added concrete fields |
| Doc banner history (08-23 … 09-28) | — | Removed; replaced by the verification line |

## docs/INFRA_MAP.md

| claim as written | evidence | fix |
|---|---|---|
| Five dated reconciliation banners | — | Removed (git history) |
| §2 public `corporatetravel-dispatch-mcp` "still exists as a standalone project" | `ls /opt/corporatetraveldc/public/` → `corporatetravel-dispatch-mcp.archived-20260817` | Archived |
| §2 missing `executivestandard-website`, public `example.com`, `executivestandard.example.com` | `ls /opt/corporatetraveldc/{private,public}/` | Added |
| §3 peers `corporatetraveldc-pixel10` | `tailscale status` → `corporatetraveldc` at 100.x.x.x | Corrected to the listed name |
| §3.2 entire Ollama governance subsection | no Ollama unit; `/usr/local/bin/ollama_governor.py` present without a unit | Cut to one line |
| §4 "37 containers", "63 vs 63/64" | [ps] 35; 76 tracked | Corrected |
| §4 research-board-mirror timer repo-only | `ls ~/.config/systemd/user/*.timer` includes it; [timers] shows it active every 15 min | Removed |
| §4 core spine (10) omitted pgsql | [ps] | 11 |
| §4 ingest-core "NWWS-OI + Amtrak + local airspace" | `AMTRAK_ENABLED=false` | Corrected |
| §4.1 restore load 15 | guard `:631-632` → 20 | Corrected |
| §4.1 "first real LOCKDOWN … ollama.service" narrative | history | Removed |
| §4.2 "33 gated skill quadlets" | 39 | Corrected; added demo-api |
| §4 runner-demo `DEMO_SESSION_SECRET` in Quadlet; commit `0a7f643` | as README | Corrected |
| §4 demo-source "frozen 2026-08-14 10:21, 1.88 GB" | 14:21Z (10:21 EDT), 1,881,227,264 bytes | Same fact, UTC time |
| §4 acarshub `127.0.0.1:9081`, planefinder etc. | [ps] | Corrected ports |
| §4 Comms "6", ccw-demo untracked | [ps], repo | 6 with ccw-demo + ccw-preview1, both tracked |
| §4 host-level: llama "CORRECTED 2026-09-24" inline notes; watchdog | system timers [units] | Rewritten; added §4.4 root units table (team-liveness, skill-grants, llama-council, NTS, cert renew, watchdog-tune, stall-monitor) |
| §4 "postgres live since the 2026-09-20 cutover" | see disagreement table | 2026-09-18 |
| §4 timer highlights: ops-brief :05, ep-advance :35 | [timers] | kept |
| §4 six daily watches on a `/3`-hour grid | no `*-daily-watch.timer` exists [timers]; `scripts/lib/maintenance-queue.txt` lists all six | Replaced with rolling maintenance description |
| §4 Sunday cluster | [timers] all Monday | Corrected |
| §4 semantic-compile 03:47 daily | `OnCalendar=*-*-* 00,06,12,18:02:00 America/New_York` | Corrected |
| §4 transport-pattern-digest every 12 h; disruption-weather 04:35 | both in maintenance queue, no timer | Corrected |
| §4 docs-drift named the AI model used and "against dispatch-mcp" | `ExecStart=…/scripts/weekly-doc-drift-check.sh`; model names removed per BRIEF | Removed |
| §4 weekly-external-image-update = plain `podman auto-update` | `ExecStart=…/stack-refresh.sh --weekly` | Corrected |
| §6 `dispatch-runner` vhost nginx; mcp 404 | [probe] | kept, plus "vhost live-only" |
| §6 missing `agents.` row; `ollama.` "gone" | cloudflared ingress lists `agents.` and `ollama.`; edge `ollama.` → 302; no nginx vhost | Added; flagged stale ingress |
| §6 invite/console "Staged 2026-10-05" | [probe] 200 | Live |
| §6b live-only list | `cmp` loop: untracked `000-default-catchall`, `dispatch-runner.`, `acars.`, `adsb.`; `www.` in csexecutiveservices-website; `members.`/`invite.`/`es-headers` in executivestandard-website | Corrected |
| §6a policy ids | dashboard state | Kept generic, marked UNVERIFIED |
| §7 Admin row | `require_admin` + `allowed_actions` (0070) | Updated; added other credential types |
| §8 OOOI ordering | `db.py:5994` | Corrected |
| §9 three residue items | `grep -c` on desktop config → wrapper path 1, `FLIGHTAWARE_API_KEY` 1; mcp vhost present | Kept two; token item UNVERIFIED |
| §9 no agent gateway | routes/agent_gateway.py | Added |
| §10 FTS index `/var/lib/corporatetraveldc/second_brain_index.db` | `src/second_brain/index_db.py:15-25`; file absent | Postgres since 2026-09-19 |
| §10 knowledge graph "built and live" | `corporatetraveldc-knowledge-graph-compile.timer` disabled | Qualified |
| §11 item 5 counts 9,251/3,567 | `wc -l` 30,064; `grep -c '^timestamp,'` 11,846 | Updated |
| §11 item 10 stale cert-renew description | `systemctl list-units` shows `Description=Renew Tailscale HTTPS cert for corporatetraveldc-dispatch.tailxxxxxxx.ts.net` | Resolved, removed |
| §11 item 11 drift list | `diff` of trees | Rebuilt |
| §11 "daily-opsplan/freshness-audit/weekly-summary .timer in Quadlet dir" | `ls .config/containers/systemd/` contains no `.timer` | Removed |
| §12 secrets table: `dispatch-secrets.env` holds everything used | `/etc/corporatetraveldc/svc/` 9 files; per-container secrets files | Rebuilt (names only) |
| §12 "Five fingerprints" detail | repo root 3 `.gpg`, `security/` 3 `.pub.asc` | Shortened, points to SECURITY.md |

## docs/REFERENCE_INFRA.md

| claim as written | evidence | fix |
|---|---|---|
| dispatch-mcp optional, "still works standalone" | archived in `public/` | Archived; gateway described instead |
| DB "sqlite or postgres" | Postgres live, 73 migrations | Corrected |
| Restore < 15 | guard → 20 | Corrected; dormancy added |
| "per-tier llama.cpp units" | one unit | Corrected |
| Watchlist "short dedup window" | forward-only content-hash dedup (README/watchlist_README) | Corrected |
| Transient expiry "expiry timestamp" | defaults 6/3/24 h | Added |
| Data-flow MCP branch via dispatch-mcp | gateway | Redrawn |
| "Also tested on Pi 4, ARM cloud, x86_64" | not testable here | [UNVERIFIED] |

## docs/executive_summary.md

| claim as written | evidence | fix |
|---|---|---|
| Header: "public password-gated demo live"; body: demo replays "a real, recent two-week window" | `demo-source.db` 2026-08-14 14:21Z; refresh timer disabled | Stated stale |
| "20–40 minutes ahead"; "within seconds of the FAA order" | no benchmark in repo | Marked [UNVERIFIED] as design intent |
| METAR at "DCA, IAD, BWI and eight surrounding stations" | `metar.py:26` 7 stations | Corrected |
| "There is nothing left to switch on" | `/api/v1/feeds` eurocontrol, jasdat `awaiting_credentials`; demo refresh disabled | Corrected |
| TFRs "over the DC area" alert | TFR REST poll 300 s; 87 active | Reworded; TFR is REST, not SWIM |
| "no reliance on … third-party aggregators" | server-side local; GLOBE map embeds airplanes.live | Narrowed to resolution path |
| Missing: load-shedding caveat | guard | Added plain-language caveat |
| v1.2.0 correction banners | — | Replaced by v1.3.0 |

## docs/DESIGN-PRINCIPLES.md

| claim as written | evidence | fix |
|---|---|---|
| Inference targets `OLLAMA_BASE_URL`, default empty skips local | `llm.py:364,924`; `llama_pool.py:55-61` falls back to `100.x.x.x:8093` | `LLAMA_BASE_URL`; noted the pool fallback |
| "llama.cpp tier servers" | one server | Corrected |
| Credentials "FAA NOTAM API key" | `NMS_API_CLIENT_ID/SECRET` | Corrected |
| "poller falls back to REST" for all | only nws/notam/amtrak have REST twins | Corrected |
| Infra table lacked Postgres | live | Added |
| Checklist: new secret → template only | scoped env allowlists `scripts/service-env/*.allowlist` | Added allowlist step |

## docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md

| claim as written | evidence | fix |
|---|---|---|
| Per-container CPUWeight 100 | `grep -H '^CPUWeight=' *.container`: 9000 web/poller/pusher/ntfy, 7500 ×7 ingest, 150 nextcloud-app, 50 rss-bridge, 100 ×25 | Corrected |
| `ollama.service` rows (CPUWeight 500, memory, `LLAMA_ARG_CACHE_RAM`, governor, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_LOAD_TIMEOUT`) | no Ollama unit; `llm.py:663` comment: load timeout retired | Removed; llama unit row added |
| llama "per-tier … llama-hot CPUWeight=9000, MemoryMax=4608M" | one unit, MemoryMax 8448M; live CPUWeight 10000 | Corrected |
| Guard LOCKDOWN includes "≥2 Ollama-contention fallbacks" | informational since 2026-08-27 | Corrected |
| `OLLAMA_TIMEOUT` live 3600 (`dispatch.env:118`) | `grep ^OLLAMA_TIMEOUT= /etc/corporatetraveldc/dispatch.env` → 240; code default 900 | Corrected |
| Brief `TimeoutStartSec` 3600/4500/2800/10400 | Quadlets: 4300/5700/6500/14100 (+ transport 3300) | Corrected |
| `llm.py:482` fallback 62.0 | `llm.py:463` | Line corrected |
| Boot stagger 19 units, 15 s | `scripts/stack-boot-ctl.sh:113-158` 32 units; `serial-bringup.sh:73` default 60 s | Corrected |
| Cross-ref to `CLAUDE.md` container resource section, `/usr/local/bin/ollama_governor.py` installer `scripts/ollama_governor.sh` | `scripts/ollama_governor.sh` absent | Removed |

## docs/dispatch-runner-design.md

| claim as written | evidence | fix |
|---|---|---|
| `main.py` 2,876 lines | `wc -l` → 2,883 | Corrected |
| 31 method+path combos, no `/api/utm/drones` | [AST] 30 decorators / 32 pairs | Corrected |
| No manifest.json/service worker, "not install-eligible" | `vite.config` uses `VitePWA`; `curl :8001/sw.js` 200, `/manifest.webmanifest` 200 | Installable |
| Views table lacks `/utm`, `/events`, `/graph` | `App.jsx` routes | Added |
| `/map` "LOCAL UltraFeeder / LIVE" modes | `App.jsx:89-93` modes `globe`, `tactical` | Corrected |
| `ACARSHUB_URL` live blank | runner Quadlet `:42` `http://100.x.x.x:8092` | Corrected |
| `_TIER1_PATHS` at `main.py:1508-1509` + stale NOTE comment finding | now `main.py:1803/1833`; `grep _WATCHLIST_PATHS` → no match | Line refs updated; stale-comment finding closed |
| `POST /api/ask` "Ollama chat" | llama_pool streaming | Corrected |
| Chat env `OLLAMA_CHAT_MODEL` only | added `LLAMA_BASE_URL`, `OPENWEBUI_*` unused | Added |
| Demo instance `DEMO_SESSION_SECRET` in Quadlet | `demo-secrets.env` | Corrected |
| RSS 15-min cache | `_RSS_TTL = 900` (`main.py:2422`) | kept, cited |

## docs/DISPATCH_PANEL_PARITY.md (patched)

| claim as written | evidence | fix |
|---|---|---|
| Lines refer to `97b9075` | `git diff 97b9075 HEAD -- src/runner/main.py` → one line changed (98) | Stated still valid |
| `llama_pool.py:53-59` HOST = `LLAMA_POOL_HOST` default Tailscale | `llama_pool.py:53-67` adds `LLAMA_BASE_URL` derivation | Corrected |
| `has_llm = True` at `main.py:1659` | line 1658 (at both commits) | Corrected |
| `OLLAMA_BASE_URL` at `main.py:98` | `LLAMA_BASE_URL` (fallback `OLLAMA_BASE_URL`) | Corrected |
| "~78 TFRs active" | `/api/v1/tfr` → 87; `_context_to_str` unchanged | Updated, defect re-confirmed |
| Corpus counts 68,081 lines / 1,132 files / 134 docs / 16 SKILL.md | 72,091 / 1,273 / 139 / 12 | Updated; DB row counts left dated |
| `chat_messages` 0 rows | not re-queried | [UNVERIFIED] marker |

## docs/INTEGRATION_SPEC.md (patched)

| claim as written | evidence | fix |
|---|---|---|
| See `docs/SECURITY.md` | file absent (root `SECURITY.md`) | → `docs/COMPLIANCE_SECURITY.md` |
| Path-embedded single-use token precedent | resolve link: query key `k`, deny-only (`main.py:2804-2827`) | Corrected |
| Access bypass for `/webhooks/*` described as a recommendation | edge GET `/webhooks/3cx/events` → 405 (app) vs `/` → 302 | Stated in place |
| "Platform-specific notes table … in README" | no such table | Removed |
| `auto_tracked` present only on successful add | `webhooks.py:131-157,209-228`: set on extraction; add best-effort, failure logged | Corrected |
| ntfy side-effects not documented | topics `reservations` / `calls`, p3 | Added |
| README documents a bundled `POST /api/v1/watchlist` | README documents `/flights`; bare route = T1 sessions | Corrected |
| IATA form "will silently fail" | codeshare resolution `watchlist.py:185-200` | Qualified |
| Batch routes absent | `watchlist.py:588,665,797` | Added |

## docs/CLIENT_DEMO_PATTERN.md (patched)

| claim as written | evidence | fix |
|---|---|---|
| Origin instance is its "own still-live unit file" (untracked implication) | `corporatetraveldc-ccw-demo.container`, `-ccw-preview1.container` tracked | Corrected; named both ports |
| Pattern presented as in use | no `client-demo@<slug>` units installed | Stated no instance exists |
| Successor absent | `corporatetraveldc-demo-portal-{client,personal}.container` (git f63ee17, 2026-09-18), not installed, no ingress; `docs/DEMO_PORTALS.md` missing | Added |

---

## [UNVERIFIED] items left in the docs

- Platform-compatibility rows other than Linux aarch64 (README); "tested on Pi 4 / ARM cloud / x86_64" (REFERENCE_INFRA).
- SWIM/consumer-app latency claims (executive_summary).
- Whether webhook secrets are configured on production (would need a POST probe; BRIEF limits probes to GET).
- CF Access application/policy ids and settings (dashboard state).
- Whether the retired mcpo admin token is still active in `auth_tokens` (not queried).
- `chat_messages` row count; vault/semantic table sizes in DISPATCH_PANEL_PARITY §5.1.
- Second Pi hardware status (INFRA_MAP §3.1); Nextcloud outbound-mail token status.
- `dav.`, `ntfy.`, `openwebui.`, `pihole.` edge behaviour (not probed).

## Findings for the operator

1. **Public demo serves 2026-08-14 data.** `demo-source.db` mtime 2026-08-14T14:21:42Z; `corporatetraveldc-demo-source-refresh.timer` disabled, never fired. The executive summary previously told investors it replays a recent window.
2. **Host TZ change shifted un-zoned timers by 4 h** (host is UTC since today): `docs-drift-weekly` (`OnCalendar=Mon *-*-* 09:00:00`), `pull-path-verify` (`06,18:00:00`), `second-brain-weekly-dump` (`Sun 02:00:00`). Decide whether to add `America/New_York`.
3. **Integrity sweep failed 17:35Z and 18:05Z** on an unsigned `scripts/stack-refresh.sh` edit; cleared 18:20Z by signed commit 2c3f81b. (Shows a 30-minute window where a tracked edit sat unsigned while the tree was live.)
4. **Legacy SQLite file reappeared**: `/var/lib/corporatetraveldc/corporatetraveldc.db`, 69,632 bytes, 2026-10-05 23:21Z (CLI smoke-test incident). Not read; decide whether to remove it.
5. **Untracked live config** (no DR source): nginx `000-default-catchall.conf`, `dispatch-runner.example.com.conf` (the public demo), `acars.` and `adsb.` vhosts; `nextcloud-net.network` (both Nextcloud Quadlets reference it); `blog-substack-reminder.timer`; `corporatetraveldc-ccw-demo-webdev-expiry.{service,timer}`; two `ultrafeeder.container.bak-*`.
6. **Stale `ollama.example.com` tunnel ingress** with no nginx vhost (edge 302 to Access; local catch-all drops). And the `mcp.` nginx vhost still present (repo + live).
7. **SDR silence watchdogs disabled**: `adsb-feed-silence-watchdog`, `acars-feed-silence-watchdog`, `adsb-link-watchdog` timers `disabled` (unit files installed). Older docs credited the ADS-B one with catching the 2026-08-10 outage.
8. **llama CPUWeight drift**: tracked unit 9000, live 10000 via a set-property drop-in outside the repo.
9. **Runner chat context defect still live**: `_context_to_str` reads pre-cutover shapes; model told "TFRS: none active" with 87 active (`main.py:1346-1414`).
10. **Webhook `auto_tracked` can report a track that failed** (best-effort add, response unchanged).
11. **Dangling reference**: demo-portal Quadlets cite `docs/DEMO_PORTALS.md`, which does not exist; those two Quadlets are tracked but not installed.
12. **`thermal-samples.csv`**: 11,846 of 30,064 lines are header rows (39 %), growing one per sample.
13. **`~/.config/Claude/claude_desktop_config.json`** still references the pre-archive MCP wrapper path and contains a `FLIGHTAWARE_API_KEY` entry (counted with `grep -c`; value not read).
14. **`/api/v1/feeds` shows the `amtrak` REST row 26,630 s old against a 3,600 s threshold with `push_covered: false`**, while `push:amtrak` is 41 s old. `/healthz` reports `ok`, so this is display-only, but the feeds view will read Amtrak as stale.
15. **Runner GLOBE map embeds `globe.airplanes.live`** in the browser, and the TACTICAL layer is labelled "airplanes.live" though its data is local — at odds with the local-only directive's wording.
16. **`OLLAMA_TIMEOUT=240`** in `dispatch.env` while older docs and the 2026-08-13 design said 3600; confirm 240 is intended (code default 900).

## Totals

Claims corrected: 155 table rows across the ten docs (160 per-doc rows, 5 confirmed unchanged), plus the 6 README-vs-INFRA_MAP disagreements resolved above.
