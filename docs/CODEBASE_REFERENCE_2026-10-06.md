# CTDI Dispatch — Codebase Reference (2026-10-06)

Verified against HEAD 2c3f81b and live state on 2026-10-06 18:21Z / 14:21 ET (inspection 18:04Z-18:21Z / 14:04-14:21 ET).

**Status:** CURRENT. Replaces `docs/CODEBASE_REFERENCE_2026-09-28.md` (validated
against `78660147`, 68 commits ago) and the SQLite-era
`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md`. Every count below names the command
that produced it. Paths are relative to the repo root
`/opt/corporatetraveldc/private/ctdi-dispatch-internal` unless absolute. Anything not
checked is marked `[UNVERIFIED: why]`. This is a map, not a design document: the
security model, segmentation and integrity system have their own docs
(`docs/COMPLIANCE_SECURITY.md`, `docs/AGENT_SEGMENTATION.md`, `docs/BOARD_SIGNING.md`,
`docs/OPERATOR_CONSOLE.md`); this file tells a reviewer where everything is.

Contents: 1 Snapshot · 2 Package map · 3 Containers and images · 4 Web API ·
5 Data layer · 6 Ingest and feeds · 7 Scheduled work · 8 Scripts ·
9 Security surfaces · 10 TODO markers · Appendix A runner + demo routes

---

## 1. Snapshot

| Item | Value | Command |
|---|---|---|
| HEAD | `2c3f81bb389c5abdf68e650ccb7cf32f3b99a56f`, branch `main`, committed 2026-10-06 18:10:42Z / 14:10 ET ("stack-refresh audit: exclude ephemeral-range listeners …") | `git log -1 --format='%H %cI %s'` |
| Tree signed | `verify-manifest: OK -- signature valid, all 1246 files match.` (clean tree, `git status --short` empty) | `bash scripts/verify-manifest.sh \| head -1` |
| Tracked files | 1,273 | `git ls-files \| wc -l` |
| Python under `src/` | 180 files, **72,091** lines | `find src -name '*.py' \| wc -l`; `find src -name '*.py' \| xargs wc -l \| tail -1` |
| Test functions | **877** `def test_` (pytest parametrisation makes the run count higher) | `grep -rc "def test_" tests \| awk -F: '{s+=$2} END{print s}'` |
| Test files | 91 `test_*.py` | `find tests -name 'test_*.py' \| wc -l` |
| Postgres migrations | 73 (`0001` … `0073`) | `ls src/common/pg_schema/*.sql \| wc -l` |
| Tracked quadlets | 76 `.container` (+2 `.container.disabled`, 1 `.network`) | `ls .config/containers/systemd` |
| Tracked user timers | 66 | `ls .config/systemd/user/*.timer \| wc -l` |
| Running containers | 36 at 18:05Z (35 long-running + the `ops-brief` oneshot mid-run) | `podman ps -q \| wc -l` |
| Commits since the 09-28 reference | 68 | `git rev-list --count 78660147..HEAD` |

Note on timing: this reference was started at `db64018` with one uncommitted edit
(`scripts/stack-refresh.sh`, `verify-manifest` then reported `FAILED` for that file and
`corporatetraveldc-integrity-sweep.service` was in `failed` state). The operator
committed and signed it as `2c3f81b` at 18:10Z during this pass; only that one script
changed (`git diff --stat db64018 HEAD`: `MANIFEST.sha256`, `.asc`,
`scripts/stack-refresh.sh`), so every other count here holds for `2c3f81b`.

### 1.1 Tracked files by top-level entry

`for d in $(git ls-files | cut -d/ -f1 | sort -u); do … git ls-files -- "$d" | wc -l; done`

| Entry | Files | What it is |
|---|---|---|
| `src/` | 336 | Python packages (180 `.py`), 73 SQL migrations, runner React frontend, web console static, ES assets |
| `.config/` | 239 | user quadlets (`.config/containers/systemd/`) + user units/timers/slices (`.config/systemd/user/`, incl. `retired-*` subdirs) |
| `docs/` | 190 | documentation |
| `scripts/` | 170 | 132 top-level `*.sh`/`*.py` + `agent-segmentation/` (9), `lib/` (9), `service-env/` (12), `templates/` (2), `retired-20260830/` (1) |
| `tests/` | 151 | pytest suites (`auth common ingest poller runner scripts second_brain shared web`) |
| `systemd/` | 33 | ROOT/system units (`systemd/system/`, `systemd/*.service`/`.timer`), disabled quadlets, `retired-20261003/`, `tmpfiles.d/` |
| `corporatetraveldc.*` | 21 | persona prompt source texts (no longer Ollama Modelfiles; kept in sync with `src/common/personas.py` by `build-models.sh`) |
| `config/` | 19 | tracked env templates (`config/dispatch.env`, `dispatch.env.example`, …) — names only are cited here |
| `nginx/` | 14 | `conf.d/` vhosts for this repo's hostnames + `snippets/honeypot.conf` |
| `skills/` | 13 | signed agent skills (8 dirs) + `skill-capabilities.txt`, `vendor-pins.txt` |
| `selinux/` | 10 | policy modules |
| `Containerfile.*` | 8 | `amtrak-tracker demo docgen ingest poller pusher runner web` |
| `install/`, `security/`, `fail2ban/` | 6, 5, 5 | client installers; signing pubkey + `signing.env`; fail2ban action/jail/filter |
| `manifests/`, `watchlists/`, `addenda/`, `data/`, `assets/` | 4, 4, 3, 3, 4 | |
| `.agents/`, `.claude/` | 5, 5 | agent instructions / project settings |
| `cloudflared/` | 2 | `config.yml` ingress (identical to live `~/.cloudflared/config.yml` ingress, `diff` of hostname/service lines) |
| single files | — | `MANIFEST.sha256(.asc)`, `build-images.sh`, `build-models.sh`, `setup.sh`, `requirements.txt`, `README.md`, `SECURITY.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTORS.md`, `LICENSE`, 3 GPG pubkeys, 2 env templates, `containers/dumpvdl2`, `udev/`, `unbound/`, `tailscale/`, `networkmanager/` |

---

## 2. Package map (`src/`)

Per-package size: `find $d -name '*.py' | wc -l` and `… | xargs cat | wc -l`.
Entrypoints: modules containing `if __name__ == "__main__"` (marked **M**), Containerfile
`CMD`, quadlet `Exec=`. There is no `pyproject`/console-script table; CLIs are run as
`python3 -m …` or by path.

### 2.1 Inter-package imports

`grep -rhoE '^\s*(from|import) <pkg>\b' <pkg> | sort | uniq -c` (counts include
function-local imports):

| Package | Imports from (count) |
|---|---|
| `poller` | common 181, second_brain 56, shared 16, ingest 3, demo 1, executive_standard 1, pusher 1 |
| `ingest` | common 40, shared 36 |
| `web` | common 32, second_brain 9, auth 4, shared 4, demo 1, geo 1, ingest 1 |
| `runner` | shared 11, common 3 |
| `pusher` | common 5, poller 1, shared 1 |
| `common` | second_brain 4, ingest 1, shared 1 |
| `shared` | common 3 |
| `second_brain` | common 2, shared 2 |
| `demo` | common 2 |
| `ctdc_token` | auth 1, common 1 |
| `auth` | common 1 |
| `amtrak_tracker` | common 1, ingest 1 |
| `acars_watcher`, `ais_watcher`, `utm_watcher`, `executive_standard`, `geo` | none (standalone) |

`common` is the hub (DB layer, LLM funnel, ntfy, governance); it has back-edges into
`second_brain`, `ingest` and `shared`, so there is no strict layering.

### 2.2 Packages

| Package | Files / LOC | Purpose (from module docstrings) | Entrypoints |
|---|---|---|---|
| `common/` | 35 / 19,146 | Shared library: DB layer, backend selector, LLM funnel, governance, gateway, ES invites, ntfy | `compliance_egress.py` **M**, `entity_tracking.py` **M** |
| `ingest/` | 16 / 12,190 | Push-ingest service: six FAA SWIM feeds (Solace), NWWS-OI (XMPP), Amtrak, local airspace | `python3 -m ingest.main` (Containerfile.ingest CMD) |
| `poller/` | 69 / 17,836 | Async scheduler (REST fetchers, in-process skills, trigger reactor, watchlist sweep) + 47 skills + 16 fetchers | `python3 -m poller.main`; each `skills/*.py` **M**, run by quadlet `Exec=` or `SKILL_SCHEDULE` |
| `web/` | 14 / 5,823 | FastAPI API on :8000 (§4), SSE, agent gateway, phone console | `uvicorn web.main:app` (Containerfile.web CMD); `web/serve.py` **M** |
| `second_brain/` | 16 / 6,170 | Nextcloud/WebDAV vault, Postgres index, semantic layer, knowledge graph, doc generation | `index_db.py` **M** (`--scan`), `semantic/__main__.py`, `knowledge_graph/build_graph.py` **M**, `retrofit_links.py` **M**, `remember.py`, `doc_generation.py`, `client_entity_ingest.py` **M** |
| `runner/` | 1 / 2,883 (+ React frontend) | Internal PWA backend on :8001, server-side proxy over the web API + local receivers (Appendix A) | `uvicorn main:app --port 8001` (Containerfile.runner) |
| `shared/` | 6 / 2,846 | Watchlist engine, sector coalescing, RSS catalog, SSRF guard, feed resolver | library |
| `demo/` | 5 / 1,558 | Demo recorder (Postgres `demo_snapshots`) + read-only playback API over the scrubbed SQLite demo source | `python3 -m demo.recorder` **M**; `uvicorn demo.demo_api:app --port 8004` |
| `pusher/` | 2 / 731 | ntfy alert sender loop | `python3 -m pusher.main` **M** |
| `acars_watcher/` | 1 / 589 | ACARS/VDL2 watcher: UDP :5005 from local decoders + optional airframes.io and Jumpseat REST | own `src/acars_watcher/Containerfile`, `python -u acars_watcher.py` |
| `executive_standard/` | 2 / 521 | Static HTML renderer for the self-hosted Executive Standard mirror | imported by `poller/skills/executive_standard_sync.py` |
| `utm_watcher/` | 1 / 432 | OpenDroneID/UTM watcher — **not deployed** (quadlet only as `systemd/corporatetraveldc-utm-watcher.container.disabled`) | own Containerfile |
| `auth/` | 2 / 335 | Tier resolution (T0/T1/T2/admin), `require_admin(action)` with audit + per-token `allowed_actions` | library |
| `ctdc_token/` | 2 / 240 | Token management CLI | `cli.py` **M** |
| `geo/` | 2 / 221 | Static DC airspace GeoJSON (`dc_airspace.py`) | library (`/api/v1/airspace`) |
| `ais_watcher/` | 1 / 212 | AIS UDP :5006 watcher — **not deployed** (`systemd/corporatetraveldc-ais*.container.disabled`, `systemd/quadlets/corporatetraveldc-ais-catcher.container.disabled`) | own Containerfile |
| `amtrak_tracker/` | 2 / 183 | Push-primary Amtrak status for WAS (stamps `push:amtrak`) | `python3 -m amtrak_tracker.main` |
| `audit/`, `exporters/` | 1 / 0 each | empty `__init__.py` only | — |
| `swim_test.py` (top level) | 175 lines | SWIM NMS/SCDS connectivity test | manual |

### 2.3 Key modules

**`common/`** (35)
- `db.py` (7,840) — accessors for every table; still carries the SQLite `SCHEMA_Vn` blocks as the structural record; `conn()` delegates to Postgres when `DISPATCH_DB_BACKEND=postgres`.
- `db_backend.py` (830) — backend selector; `pg_conn()` (psycopg pool, `translate_sql()` SQLite→PG shim); `ref_conn()` SQLite handle (see §5.3 — its docstring is stale).
- `db_swim.py` (1,331) — SWIM extra-field tables/accessors (`SCHEMA_SWIM_V41…V47`).
- `llm.py` (1,100) — single LLM funnel to llama.cpp at `LLAMA_BASE_URL` (`llm.py:364`; `OLLAMA_BASE_URL` read only as a deprecated alias; tracked `config/dispatch.env:115` = `http://100.x.x.x:8093`); Anthropic fallback gated by `ANTHROPIC_FALLBACK_ENABLED` (default `false`, `llm.py:399`; `config/dispatch.env:101` sets `false`). `llama_pool.py` — slot discipline for the one llama.cpp server; `ollama_lock.py` — `OllamaBusyError` only (legacy name); `personas.py` (958) — persona/system-prompt registry.
- `governance.py` (539) — human-signed approvals, council/arena, shared ghostwriting workspace (Wave 2).
- `agent_gateway.py` (522) — OAuth 2.1 + remote-MCP gateway logic (migrations 0071, 0073).
- `es_invites.py` (474) — Executive Standard invites, promo codes, sessions (0072).
- `board_sign.py` (263) — SSH-signature verification for the board (also installed root copy, §8).
- `acars.py` (515) — ACARS/VDL2/HFDL flight state; reads acarshub SQLite read-only.
- `airport_fids.py` (383), `flight_resolver.py` (379), `airline_codes.py`, `cifp_lookup.py`, `airsigmet.py` — aviation lookups.
- `entity_tracking.py` (1,120), `trends_signal.py`, `rss_retrieval.py`, `aam_watch.py`, `disruption_weather_watch.py`, `export_analysis.py` — intel helpers.
- `ntfy_push.py`, `pushover.py`, `push_dedup.py` — alert delivery and dedup.
- `guardrails.py` (SR1/SR2), `sr1_log.py`, `sr2_gate.py` — usage logging / hash gate.
- `maintenance_window.py` — in-process mirror of `scripts/maintenance-window-guard.sh`; `optime.py` — operational-day helper.
- `config.py` — env loader (`DISPATCH_DB` default `/var/lib/corporatetraveldc/corporatetraveldc.db`, `config.py:65`); `compliance_egress.py` — optional audit_log export.

**`ingest/`** — `main.py` (supervisor), `swim_client.py` (914, Solace sessions), `failover.py` (push/pull coordination), `nwws.py`, `amtrak.py`, `local_airspace.py` (693), `config.py`; `parsers/` `fdps_parser.py` (2,049), `tfms_parser.py` (3,291), `smes_parser.py` (1,779), `itws_parser.py`, `tbfm_parser.py`, `aim_parser.py`, `geo_filter.py` (250 NM of DCA OR 30 core airports OR core WFOs).

**`poller/`** — `main.py` (2,334: `FetchLoop`, `SkillLoop`, `TriggerReactor`, `WatchlistSweep`, FAA/OpenSky registry sweeps); `fetchers/` (16): `tfr metar nas nws notam amtrak runsheet atcscc_opsplan dca_fids iad_fids airport_fids eurocontrol jasdat faa_registry opensky_registry ops_plan`; `skills/` (47 files, §7.3 maps each to its scheduler); `tools/watchlist_import.py` **M**.

**`web/`** — `main.py` (3,030), `sse.py`, `serve.py`; `routes/` `agent_gateway.py`, `console.py` (721), `watchlist.py` (840), `webhooks.py`, `sectors.py`, `fids.py`, `airspace.py`, `remember.py`, `data_usage.py`.

**`second_brain/`** — `index_db.py` (Postgres index; vestigial `INDEX_DB` constant at `index_db.py:78` still names the deleted `/var/lib/corporatetraveldc/second_brain_index.db`), `webdav_client.py`, `scrub_gate.py`, `remember.py`, `doc_generation.py`, `client_entity_ingest.py`; `semantic/` `compile.py` (2,064), `model.py`, `export.py`, `__main__.py`; `knowledge_graph/` `build_graph.py`, `retrofit_links.py`, `lexicon.py`.

**`shared/`** — `watchlist.py` (1,313), `sector_coalesce.py` (887), `rss_catalog.py`, `feed_resolve.py`, `ssrf_guard.py`.

### 2.4 Third-party Python / JS dependencies

- `requirements.txt` (web, poller, pusher, ingest, amtrak, demo images): fastapi, python-multipart, uvicorn[standard], pydantic, sse-starlette, requests, psycopg[binary,pool], slixmpp, httpx, lxml, anthropic, trendspy, python-docx, python-pptx, html2text, gtfs-realtime-bindings, cryptography.
- `src/ingest/requirements.txt`: solace-pubsubplus.
- `src/runner/requirements.txt`: fastapi, uvicorn[standard], httpx, python-multipart, psycopg[binary,pool].
- `src/runner/frontend/package.json`: react 18, react-dom, react-router-dom 7, leaflet; dev: vite 6, @vitejs/plugin-react, vite-plugin-pwa.
- All Python bases are `python:3.13-slim`, except the three standalone watchers (`python:3.12-slim`); runner frontend builds on `node:20-alpine`.

---

## 3. Containers and images

### 3.1 Local images (`build-images.sh`, `scripts/stack-refresh.sh`, `scripts/serialized-rollout.sh`)

`grep -nE '^(FROM|COPY|CMD)' Containerfile.* src/*/Containerfile`

| Image tag | Containerfile | Bakes | CMD |
|---|---|---|---|
| `localhost/corporatetraveldc-web:latest` | `Containerfile.web` | all of `src/`, `requirements.txt`, `scripts/verify-manifest.sh`, `scripts/verified-exec.sh`, `security/trusted-signing-key.pub.asc`, `security/signing.env`, `MANIFEST.sha256(.asc)`, `corporatetraveldc.*` | `uvicorn web.main:app` |
| `localhost/corporatetraveldc-poller:latest` | `Containerfile.poller` | same set | `python3 -m poller.main` |
| `localhost/corporatetraveldc-pusher:latest` | `Containerfile.pusher` | same set | `python3 -m pusher.main` |
| `localhost/corporatetraveldc-ingest:latest` | `Containerfile.ingest` | same set + `src/ingest/requirements.txt` (solace) | `python3 -m ingest.main` |
| `localhost/corporatetraveldc-amtrak-tracker:latest` | `Containerfile.amtrak-tracker` | same set | `python3 -m amtrak_tracker.main` |
| `localhost/corporatetraveldc-demo:latest` | `Containerfile.demo` | same set | `python3 -m demo.recorder` |
| `localhost/corporatetraveldc-runner:latest` | `Containerfile.runner` (multi-stage) | **only** `src/runner/main.py`, `src/shared/`, `src/common/`, built frontend — no manifest, no verified-exec | `uvicorn main:app --port 8001` |
| `localhost/corporatetraveldc-acars-watcher:latest` | `src/acars_watcher/Containerfile` | `acars_watcher.py` only | `python -u acars_watcher.py` |
| (docgen) | `Containerfile.docgen` | `src/` + manifest set | no quadlet references it `[UNVERIFIED: no build/usage site found in quadlets]` |
| (ais, utm watchers) | `src/ais_watcher/Containerfile`, `src/utm_watcher/Containerfile` | single file | not deployed |
| `localhost/csexec-contact:latest` | not in this repo (contact form, separate repo) | — | — |

`build-images.sh` builds `web poller pusher ingest amtrak-tracker` then `runner`, re-tagging
the outgoing `:latest` as `:previous` first. Images that bake the manifest refuse to run
skills from an unsigned tree: quadlet `Exec=` lines prepend `scripts/verified-exec.sh`.

### 3.2 Quadlets (`.config/containers/systemd/`, 76)

Generated from the tracked quadlets; "Schedule" is the paired `.config/systemd/user/<name>.timer`
(`OnCalendar`/`OnBootSec`/`OnUnitActiveSec`); "Queue" = listed in
`scripts/lib/maintenance-queue.txt`; "Live" = present in `~/.config/containers/systemd/`
and, for services, in `podman ps` at 18:05Z. Tracked and live quadlets are byte-identical
where both exist (`cmp` over every file). Env files: 63 quadlets load
`/etc/corporatetraveldc/dispatch.env` (non-secret) plus a per-service scoped file
`/etc/corporatetraveldc/svc/<svc>.env` (`poller` ×39, `ingest` ×7, `web`, `runner`,
`pusher`, `demo`, `amtrak-tracker`, `acars-watcher`, `execstandard-verifier`); `demo-api`
and `runner-demo` load `/etc/corporatetraveldc/demo-secrets.env`; third-party containers
load their own `*-secrets.env` (`grep -h EnvironmentFile *.container | sort | uniq -c`).
Contents of those files were not read.

#### `localhost/corporatetraveldc-acars-watcher:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-acars-watcher` | service | image CMD — ports 5005:5005/udp | — |  | live, running |

#### `localhost/corporatetraveldc-amtrak-tracker:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `amtrak-tracker` | service | image CMD | — |  | live, running |

#### `localhost/corporatetraveldc-demo:latest` (2)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-demo-api` | service | verified-exec: uvicorn demo.demo_api:app — ports 127.0.0.1:8004:8004, 100.x.x.x:8004:8004 | — |  | live, running |
| `corporatetraveldc-demo` | service | image CMD | — |  | live, running |

#### `localhost/corporatetraveldc-ingest:latest` (7)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-ingest-core` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-fdps` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-itws` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-notam` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-stdds` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-tbfm` | service | image CMD | — |  | live, running |
| `corporatetraveldc-ingest-tfms` | service | image CMD | — |  | live, running |

#### `localhost/corporatetraveldc-poller:latest` (40)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-aam-daily-watch` | oneshot | verified-exec: src/poller/skills/aam_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-aam-weekly-watch` | oneshot | verified-exec: src/poller/skills/aam_weekly_watch.py (flock -w 3600 -E 75) | Mon *-*-* 02:00:00 America/New_York |  | live |
| `corporatetraveldc-aviation-daily-watch` | oneshot | verified-exec: src/poller/skills/aviation_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-board-sweep` | oneshot | verified-exec: src/poller/skills/board_sweep.py | *:0/15 |  | live |
| `corporatetraveldc-concierge-travel-daily-watch` | oneshot | verified-exec: src/poller/skills/concierge_travel_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-convective-sigmet-archiver` | oneshot | verified-exec: src/poller/skills/convective_sigmet_archiver.py | *:4/10 |  | live |
| `corporatetraveldc-daily-opsplan` | oneshot | verified-exec: src/poller/fetchers/atcscc_opsplan.py | *-*-* 07:00:00 America/New_York |  | live |
| `corporatetraveldc-dispatch-desk-memo` | oneshot | verified-exec: src/poller/skills/dispatch_desk_memo.py (flock -w 3600 -E 75) | Mon *-*-* 03:00:00 America/New_York |  | live |
| `corporatetraveldc-disruption-weather-digest` | oneshot | verified-exec: src/poller/skills/disruption_weather_digest.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-entity-tracking-digest` | oneshot | verified-exec: src/poller/skills/entity_tracking_digest.py | *-*-* 00,06,12,18:12:00 America/New_York |  | live |
| `corporatetraveldc-ep-advance-venues` | oneshot | verified-exec: src/poller/skills/ep_advance_venues.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-ep-advance` | oneshot | verified-exec: src/poller/skills/ep_advance_brief.py (flock -w 600 -E 75) | *-*-* *:35:00 America/New_York |  | live |
| `corporatetraveldc-execstandard-verifier` | service | python3 /opt/es-verify.py — ports 127.0.0.1:8787:8787 | — |  | live, running |
| `corporatetraveldc-executive-protection-daily-watch` | oneshot | verified-exec: src/poller/skills/executive_protection_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-faa-cifp-parse` | oneshot | verified-exec: src/poller/skills/faa_cifp_parse.py | Thu *-*-* 08:20:00 America/New_York |  | live |
| `corporatetraveldc-faa-cifp-pull` | oneshot | verified-exec: src/poller/skills/faa_cifp_pull.py | Thu *-*-* 08:00:00 America/New_York |  | live |
| `corporatetraveldc-feed-db-integrity-check` | oneshot | verified-exec: src/poller/skills/feed_db_integrity_check.py | *:0/30 |  | live |
| `corporatetraveldc-freshness-audit` | oneshot | verified-exec: src/poller/skills/freshness_audit.py | *-*-* 06:00:00 America/New_York |  | live |
| `corporatetraveldc-gig-economy-daily-watch` | oneshot | verified-exec: src/poller/skills/gig_economy_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-ingest-feed-watch` | oneshot | verified-exec: src/poller/skills/ingest_feed_watch.py | *:05:00 |  | live |
| `corporatetraveldc-knowledge-graph-compile` | oneshot | verified-exec: -m second_brain.knowledge_graph.build_graph | *-*-* 00,06,12,18:08:00 America/New_York (+ .path trigger) |  | live |
| `corporatetraveldc-nms-v240-check` | oneshot | verified-exec: src/poller/skills/nms_v240_post_deploy_check.py | 2026-08-08 02:30:00 |  | live |
| `corporatetraveldc-ops-brief-deferred` | oneshot | verified-exec: src/poller/skills/ops_brief.py (flock -w 600 -E 75) | — |  | live |
| `corporatetraveldc-ops-brief` | oneshot | verified-exec: src/poller/skills/ops_brief.py (flock -w 600 -E 75) | *-*-* *:05:00 America/New_York |  | live |
| `corporatetraveldc-personal-export-watch` | oneshot | verified-exec: src/poller/skills/personal_export_watch.py | Sat *-*-* 10:00:00 America/New_York |  | live |
| `corporatetraveldc-personal-notes-import` | oneshot | verified-exec: src/poller/skills/second_brain_personal_notes_import.py | boot+2min; every 2min |  | live |
| `corporatetraveldc-poller` | service | image CMD | — |  | live, running |
| `corporatetraveldc-pull-path-verify` | oneshot | verified-exec: src/poller/skills/pull_path_verify.py | *-*-* 06,18:00:00 |  | live |
| `corporatetraveldc-research-board-mirror` | oneshot | verified-exec: src/poller/skills/second_brain_research_board_mirror.py | boot+5min; every 15min |  | live |
| `corporatetraveldc-retrofit-links` | oneshot | verified-exec: -m second_brain.knowledge_graph.retrofit_links | *-*-* 00,06,12,18:05:00 America/New_York |  | live |
| `corporatetraveldc-second-brain-daily` | oneshot | verified-exec: src/poller/skills/second_brain_daily.py (flock -w 3600 -E 75) | *-*-* 23:45:00 America/New_York; every 120min |  | live |
| `corporatetraveldc-second-brain-demo-archiver-daily` | oneshot | verified-exec: src/poller/skills/second_brain_demo_archiver_daily.py | *-*-* 04:15:00 America/New_York |  | live |
| `corporatetraveldc-second-brain-index-scan` | oneshot | verified-exec: -m second_brain.index_db | *-*-* 04:00:00 America/New_York |  | live |
| `corporatetraveldc-second-brain-rss` | oneshot | verified-exec: src/poller/skills/second_brain_rss.py | *-*-* 0/2:10:00 America/New_York |  | live |
| `corporatetraveldc-second-brain-weekly` | oneshot | verified-exec: src/poller/skills/second_brain_weekly.py (flock -w 3600 -E 75) | Mon *-*-* 04:30:00 America/New_York |  | live |
| `corporatetraveldc-semantic-compile-daily` | oneshot | verified-exec: src/poller/skills/semantic_compile_daily.py | *-*-* 00,06,12,18:02:00 America/New_York (+ .path trigger) |  | live |
| `corporatetraveldc-tbfm-arrival-enrichment` | oneshot | verified-exec: src/poller/skills/tbfm_arrival_enrichment.py | *:0/15 |  | live |
| `corporatetraveldc-trains-yachts-daily-watch` | oneshot | verified-exec: src/poller/skills/trains_yachts_daily_watch.py (flock -w 3600 -E 75) | — | enrolled | live |
| `corporatetraveldc-transport-pattern-digest` | oneshot | verified-exec: src/poller/skills/transport_pattern_digest.py (flock -w 1200) | — | enrolled | live |
| `corporatetraveldc-weekly-summary` | oneshot | verified-exec: src/poller/skills/weekly_summary.py (flock -w 3600 -E 75) | Mon *-*-* 03:45:00 America/New_York |  | live |

#### `localhost/corporatetraveldc-pusher:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-pusher` | service | image CMD | — |  | live, running |

#### `localhost/corporatetraveldc-runner:latest` (2)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-runner-demo` | service | image CMD — ports 127.0.0.1:8005:8001, 100.x.x.x:8005:8001 | — |  | live, running |
| `corporatetraveldc-runner` | service | image CMD — ports 127.0.0.1:8001:8001, 100.x.x.x:8001:8001 | — |  | live, running |

#### `localhost/corporatetraveldc-web:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-web` | service | image CMD — ports 127.0.0.1:8000:8000, 100.x.x.x:8000:8000 | — |  | live, running |

#### `localhost/csexec-contact:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `csexec-contact` | service | image CMD — ports 127.0.0.1:8002:8002 | — |  | live, running |

#### `docker.io/binwiederhier/ntfy:v2.25.0` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `ntfy` | service | serve — ports 2586:2586 | — |  | live, running |

#### `docker.io/library/nextcloud:stable-apache` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `nextcloud-app` | service | image CMD — ports 127.0.0.1:8090:80 | — |  | live, running |

#### `docker.io/library/nginx:alpine` (5)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-ccw-demo` | service | image CMD — ports 127.0.0.1:8085:8085, 100.x.x.x:8085:8085 | — |  | live, running |
| `corporatetraveldc-ccw-preview1` | service | image CMD — ports 127.0.0.1:8086:8086, 100.x.x.x:8086:8086 | — |  | live, running |
| `corporatetraveldc-client-demo@` | template | image CMD | — |  | live |
| `corporatetraveldc-demo-portal-client` | service | image CMD — ports 127.0.0.1:8088:8088, 100.x.x.x:8088:8088 | — |  | NOT INSTALLED, not running |
| `corporatetraveldc-demo-portal-personal` | service | image CMD — ports 127.0.0.1:8087:8087, 100.x.x.x:8087:8087 | — |  | NOT INSTALLED, not running |

#### `docker.io/library/postgres:16-alpine` (2)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-pgsql` | service | postgres -c config_file=/etc/postgresql/postgresql.conf — ports 127.0.0.1:5432:5432 | — |  | live, running |
| `nextcloud-db` | service | image CMD | — |  | live, running |

#### `docker.io/rssbridge/rss-bridge:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `rss-bridge` | service | image CMD — ports 100.x.x.x:3001:80 | — |  | live, running |

#### `docker.io/schklom/protonmail-bridge:latest-arm64` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-protonbridge` | service | image CMD — ports 100.x.x.x:1025:25 | — |  | live, running |

#### `ghcr.io/open-webui/open-webui:main` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `openwebui` | service | image CMD — ports 3000:8080 | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/acars_router:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-acarsrouter` | service | image CMD — ports 9080:9080, 15555:15555 | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-acarshub:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-acarshub` | service | image CMD — ports 100.x.x.x:8092:80 | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-ultrafeeder` | service | image CMD — ports 100.x.x.x:8080:80, 100.x.x.x:8081:80, 30005:30005, 127.0.0.1:30003:30003 | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-airnavradar:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-airnavradar` | service | image CMD | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-dumpvdl2:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-dumpvdl2` | service | image CMD | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-flightradar24:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-fr24feed` | service | image CMD — ports 8754:8754 | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-piaware:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-piaware` | service | image CMD | — |  | live, running |

#### `ghcr.io/sdr-enthusiasts/docker-planefinder:latest` (1)

| Quadlet | Kind | Entry / Exec | Schedule (paired user timer) | Queue | Live |
|---|---|---|---|---|---|
| `corporatetraveldc-planefinder` | service | image CMD — ports 127.0.0.1:30053:30053 | — |  | live, running |

### 3.3 Quadlet notes

- Not installed live: `corporatetraveldc-demo-portal-client`, `corporatetraveldc-demo-portal-personal` (tracked only). Live-only (untracked) units: `corporatetraveldc-ccw-demo-webdev-expiry.{service,timer}`, `blog-substack-reminder.{service,timer}`, `corporatetraveldc-ops-brief-deferred.timer`, `corporatetraveldc-claude-remote-control.service`, `corporatetraveldc-ollama-swap-alert.service`, `nextcloud-net.network` (`diff <(ls tracked) <(ls live)`).
- `corporatetraveldc-execstandard-verifier` runs the poller image but executes `/opt/es-verify.py`, bind-mounted from `/opt/corporatetraveldc/private/executivestandard-website/verifier/verify.py` (another repo, outside this manifest). It serves the members `auth_request` gate and the invite host on 127.0.0.1:8787 (§9).
- Long-runner lock: oneshots that use the LLM take `/var/lib/corporatetraveldc/llama-pool/long-runner-unit.lock` via `flock -w N -E 75` with `SuccessExitStatus=75`; `transport-pattern-digest` is the exception (`/bin/sh -c 'flock -w 1200 … ; rc=$?; [ "$rc" -eq 1 ] && … exit 0'`, see CHANGES findings).
- Retired/disabled quadlets kept for reference: `systemd/corporatetraveldc-{ais,ais-watcher,utm-watcher,dumphfdl}.container.disabled`, `systemd/quadlets/*.disabled`, `.config/containers/systemd/{amtrak-tracker,corporatetraveldc-acarsdec}.container.disabled`, `systemd/retired-20261003/`.

### 3.4 External images (pinning)

| Image | Quadlet(s) | Pin |
|---|---|---|
| `docker.io/library/postgres:16-alpine` | `corporatetraveldc-pgsql` (127.0.0.1:5432), `nextcloud-db` | major tag; updated only via `scripts/safe-pg-image-update.sh` |
| `docker.io/library/nextcloud:stable-apache` | `nextcloud-app` (127.0.0.1:8090) | channel tag (major jumps held by `stack-refresh.sh`) |
| `docker.io/binwiederhier/ntfy:v2.25.0` | `ntfy` (*:2586) | exact version |
| `ghcr.io/open-webui/open-webui:main` | `openwebui` (*:3000) | branch tag |
| `docker.io/rssbridge/rss-bridge:latest` | `rss-bridge` (100.x.x.x:3001) | `latest` |
| `docker.io/schklom/protonmail-bridge:latest-arm64` | `corporatetraveldc-protonbridge` (100.x.x.x:1025) | `latest-arm64` |
| `docker.io/library/nginx:alpine` | `ccw-demo`, `ccw-preview1`, `client-demo@`, `demo-portal-client`, `demo-portal-personal` | `alpine` |
| `ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder:latest` | `corporatetraveldc-ultrafeeder` | `latest` |
| `ghcr.io/sdr-enthusiasts/{docker-acarshub,acars_router,docker-dumpvdl2,docker-piaware,docker-flightradar24,docker-planefinder,docker-airnavradar}:latest` | the seven SDR feeder/decoder quadlets | `latest` |

Not containers: the llama.cpp server is the user unit `corporatetraveldc-llama.service`
(`/usr/local/lib/ollama/llama-server -m /var/lib/corporatetraveldc/models/qwen3-4b-instruct-2507-q4_0.gguf --host 100.x.x.x --port 8093 -np 2 …`,
`systemctl --user show corporatetraveldc-llama.service -p ExecStart`); `cloudflared` is
the user unit `cloudflared.service` (`--config ~/.cloudflared/config.yml`); Pi-hole,
unbound, chrony (NTS on :4460), nginx, fail2ban and tailscaled are host services.

---

## 4. Web API (`src/web/`, container `corporatetraveldc-web`, :8000 on 127.0.0.1 and 100.x.x.x)

App setup (`src/web/main.py:70-111`): `docs_url=None`, `openapi_url=None` (no schema
endpoint); CORS allowlist `https://corporatetraveldc-dispatch.tailxxxxxxx.ts.net`,
`https://dispatch-runner.example.com`; routers included in this order:
watchlist, fids, airspace, data_usage, webhooks, sectors, remember, agent_gateway,
console — **before** any `@app.*` route in `main.py` is registered, so a router route
wins over a `main.py` route with the same method and path (Starlette first-match).

Auth tiers used below (code: `src/auth/auth.py`, `src/web/main.py:223-406`):

| Label | Meaning | Code |
|---|---|---|
| T0 | anonymous | no dependency, or `resolve_tier` with no token |
| T1 / T2 | bearer token with tier `cert` / `shares` (`auth_tokens`, SHA-256 hashed) | `Depends(require_tier(Tier.T1/T2))` |
| admin (`action`) | admin bearer token; writes an `audit_log` row; refused if the token's `allowed_actions` (migration 0070, fnmatch list) excludes `action` | `Depends(require_admin("action"))`, `auth.py:203-270` |
| public-origin clamp | `X-CTDI-Public: 1` (set by the nginx public vhost on every location) forces T0 before any token lookup | `auth.py:69`, `auth.py:111` |
| board `normal` | `X-Board-Key` (master `BOARD_KEY` or a minted `board_tokens` row of the right scope) **or** a valid SSH signature from a registered `board_signers` account | `_board_auth`, policy table `BOARD_AUTH_POLICY` `main.py:271-285` |
| board `signed` | the acting account's SSH signature (namespace `corporatetraveldc-board`); a key alone never qualifies | same |
| board `high` / `cosign` | defined (`main.py:393-400`) but no route uses them at HEAD | same |

A wrong signature is a 401, never a downgrade to key auth (`_board_signer_ok`).

### 4.1 Route table (126 decorators, `src/web/main.py` + `src/web/routes/*.py`)

Generated with an AST walk over the route decorators (method, path incl. router prefix,
handler, line), tier read from each handler's `Depends(...)`/`_board_auth(...)` call.
Two `main.py` routes are shadowed (see CHANGES findings).

| # | Method | Path | Auth | Handler (file:line) | Purpose |
|---|---|---|---|---|---|
| 1 | GET | `/api/v1/whoami-token` | T0 (resolve_identity; reports the caller identity, anonymous at T0) | `whoami_token` src/web/main.py:185 | Added 2026-08-02 for the department/multi-operator RSS feed visibility |
| 2 | GET | `/api/v1/board/health` | T0 | `board_health` src/web/main.py:589 | Board reachability probe -- Tier 0, no auth. |
| 3 | GET | `/api/v1/board` | T0 for anonymous threads; board `normal` (X-Board-Key or SSH signature) for gated threads | `board_get` src/web/main.py:598 | Read board messages. Tier-0/anonymous reads are deliberate for the |
| 4 | GET | `/api/v1/board/threads` | T0 | `board_threads` src/web/main.py:651 | List board threads + last-activity ts -- Tier 0. |
| 5 | GET | `/api/v1/board/enroll` | T0 + single-use nonce (returns a board-write token once) | `board_enroll` src/web/main.py:657 | One-time enrollment fetch -- Tier-0, no Authorization (the CF tunnel |
| 6 | GET | `/api/v1/board/refresh` | keyed: valid X-Board-Key token (self-rotation) | `board_refresh` src/web/main.py:683 | Self-rotate a still-valid board-write token (X-Board-Key header) into a |
| 7 | POST | `/api/v1/board` | board `normal` (X-Board-Key or SSH signature) | `board_post` src/web/main.py:729 | Post a board message. Authenticates via X-Board-Key (NOT Authorization -- |
| 8 | GET | `/healthz` | T0 | `healthz` src/web/main.py:758 | Overall health check — Tier 0. |
| 9 | GET | `/api/v1/cps` | T0 | `get_cps` src/web/main.py:825 | Current CPS score — Tier 0. |
| 10 | GET | `/api/v1/feeds` | T0 | `get_feeds` src/web/main.py:847 | Feed freshness — Tier 0. |
| 11 | GET | `/api/v1/events` | T0 | `get_events` src/web/main.py:938 | Live event stream — Tier 0. Emits typed SSE events as data changes. |
| 12 | GET | `/api/v1/tfr` | T0 | `get_tfr` src/web/main.py:944 | Active TFRs — Tier 0. No enriched text at Tier 0. |
| 13 | GET | `/api/v1/weather` | T0 | `get_weather` src/web/main.py:961 | METAR snapshot — Tier 0. |
| 14 | GET | `/api/v1/brief` | T0 | `get_brief` src/web/main.py:980 | Latest daily brief — Tier 0. |
| 15 | GET | `/api/v1/brief/history` | T0 | `get_brief_history` src/web/main.py:989 | Return metadata for the last `limit` briefs. Optional ?type=ops/weekly filter. Tier 0. |
| 16 | GET | `/api/v1/brief/weekly` | T0 | `get_brief_weekly` src/web/main.py:996 | Latest weekly summary — from DB archive or weekly-summary.txt fallback. Tier 0. |
| 17 | GET | `/api/v1/brief/{brief_ref}` | T0 | `get_brief_by_ref` src/web/main.py:1010 | Return brief by integer ID or the most recent brief of a type slug. Tier 0. |
| 18 | GET | `/api/v1/route` | T0 | `get_route` src/web/main.py:1030 | Latest route impact narrative — Tier 0. |
| 19 | GET | `/api/v1/alerts` | T0 | `get_alerts` src/web/main.py:1046 | Active NWS hazardous weather alerts — Tier 0. |
| 20 | GET | `/api/v1/wx/discussion` | T0 | `get_wx_discussion` src/web/main.py:1067 | Latest WPC national forecast discussion(s) -- Tier 0. |
| 21 | GET | `/api/v1/wx/discussion/{awips_id}` | T0 | `get_wx_discussion_by_id` src/web/main.py:1113 | Path-form convenience: /api/v1/wx/discussion/FXUS02 -- Tier 0. |
| 22 | GET | `/api/v1/notams` | T0 | `get_notams` src/web/main.py:1131 | Active NOTAMs for DC-area airports — Tier 0. |
| 23 | GET | `/api/v1/amtrak` | T0 | `get_amtrak` src/web/main.py:1149 | Latest Amtrak DC-area status — Tier 0. |
| 24 | GET | `/api/v1/flightplan/{callsign}` | T0 | `get_flight_plan` src/web/main.py:1170 | Confirmed flight-plan details from FAA FDPS (SWIM/SFDPS FIXM feed) — |
| 25 | GET | `/api/v1/train-config` | T0 | `get_train_config` src/web/main.py:1202 | Operator rail config — primary station, regional filter, map center — Tier 0. |
| 26 | GET | `/api/v1/wx-config` | T0 | `get_wx_config` src/web/main.py:1255 | Operator meteorology config -- Tier 0. |
| 27 | GET | `/api/v1/airmets` | T0 | `get_airmets` src/web/main.py:1444 | Active AIRMET/SIGMET hazard polygons — Tier 0. Public FAA/AWC data. |
| 28 | GET | `/api/v1/bandwidth-priority` | T0 | `get_bandwidth_priority_route` src/web/main.py:1451 | Current bandwidth-priority override state — Tier 0 (no auth), so any |
| 29 | GET | `/api/v1/data-usage` | T0 | `get_data_usage` src/web/main.py:1461 | Network data usage from vnstat CSV log — Tier 0. |
| 30 | GET | `/api/v1/demo/readiness` | T0 | `get_demo_readiness` src/web/main.py:1511 | Demo archive seed status — Tier 0. |
| 31 | GET | `/api/v1/runsheet` | T1 | `get_runsheet` src/web/main.py:1543 | Daily runsheet — scheduled trips + watchlist sessions for a calendar day. |
| 32 | POST | `/api/v1/watchlist` | T1 | `start_watchlist` src/web/main.py:1583 | Start a flight, train, or custom watchlist session for the current runsheet day. |
| 33 | GET | `/api/v1/watchlist` | T1 | `list_watchlists` src/web/main.py:1629 | List all currently active watchlist sessions. **SHADOWED** by the router route registered earlier (unreachable) |
| 34 | DELETE | `/api/v1/watchlist/{session_id}` | T1 | `terminate_watchlist` src/web/main.py:1649 | Terminate a watchlist session. Data is preserved in the runsheet. **SHADOWED** by the router route registered earlier (unreachable) |
| 35 | GET | `/api/v1/opsplan` | T0 | `get_opsplan` src/web/main.py:1671 | ATCSCC daily ops plan snapshot with pattern tags. Historical dates kept indefinitely. |
| 36 | GET | `/api/v1/opsplan/range` | T1 | `get_opsplan_range` src/web/main.py:1692 | ATCSCC ops plan for a date range — for pattern analysis. Tier 1 required. |
| 37 | GET | `/api/v1/osint/feed` | T1 | `osint_feed` src/web/main.py:1729 | Recent OSINT items, newest first. Filter by scope_id and/or min_score. |
| 38 | GET | `/api/v1/knowledge-graph/html` | T1 | `knowledge_graph_html` src/web/main.py:1773 | Self-contained interactive vault knowledge-graph viz (canvas-rendered, |
| 39 | GET | `/api/v1/knowledge-graph/meta` | T1 | `knowledge_graph_meta` src/web/main.py:1801 | Just the meta block (node/edge counts, generated_at) -- cheap enough |
| 40 | GET | `/api/v1/vault/file` | T1 | `vault_file` src/web/main.py:1836 | Serve one vault file's raw content via a server-side authenticated |
| 41 | GET | `/api/v1/vault/research` | board `normal` (read scope) | `vault_research_read` src/web/main.py:1882 | Tier-0-shaped (no Authorization header required) but X-Board-Key |
| 42 | GET | `/api/v1/vault/research/list` | board `normal` (read scope) | `vault_research_list` src/web/main.py:1958 | List files (depth-1, non-recursive) under a research-vault folder -- |
| 43 | GET | `/api/v1/osint/scopes` | T1 | `osint_list_scopes` src/web/main.py:2015 | Return all OSINT scopes (enabled and disabled). |
| 44 | POST | `/api/v1/osint/scopes` | admin (`osint.scope.create`) | `osint_create_scope` src/web/main.py:2031 | Create a new OSINT monitoring scope. |
| 45 | PATCH | `/api/v1/osint/scopes/{scope_id}` | admin (`osint.scope.update`) | `osint_update_scope` src/web/main.py:2074 | Partially update a scope (label, query_terms, feed_urls, push_threshold, |
| 46 | DELETE | `/api/v1/osint/scopes/{scope_id}` | admin (`osint.scope.delete`) | `osint_delete_scope` src/web/main.py:2088 | Delete an OSINT scope and all its items. Admin-gated -- see |
| 47 | GET | `/api/v1/radio` | T1 | `get_radio` src/web/main.py:2103 | Radio reference data — Tier 1 (CERT/Tailscale). |
| 48 | GET | `/api/v1/tfr-enriched` | T1 | `get_tfr_enriched` src/web/main.py:2119 | Active TFRs with enrichment text — Tier 1. |
| 49 | GET | `/api/v1/cui/status` | T2 | `get_cui_status` src/web/main.py:2141 | CUI status endpoint — Tier 2. Audit-logged. |
| 50 | GET | `/api/v1/adsb` | T0 | `get_adsb_live` src/web/main.py:2204 | ADS-B snapshot from this box's own local ADS-B receiver (ultrafeeder/ |
| 51 | GET | `/api/v1/aircraft/{identifier}` | T0+ (resolve_tier; output depends on tier) | `get_aircraft` src/web/main.py:2301 | Look up an aircraft by N-number/tail or ICAO hex, cross-referencing |
| 52 | GET | `/api/v1/aircraft-registry/status` | T0 | `get_faa_registry_status` src/web/main.py:2438 | Return FAA registry import status and record counts. |
| 53 | GET | `/admin/healthz` | admin (`admin.healthz`) | `admin_healthz` src/web/main.py:2451 | Admin health — includes token count and audit tail. |
| 54 | GET | `/admin/feeds` | admin (`admin.feeds.list`) | `admin_feeds` src/web/main.py:2465 | — |
| 55 | GET | `/admin/audit` | admin (`admin.audit.list`) | `admin_audit` src/web/main.py:2473 | — |
| 56 | GET | `/admin/tokens` | admin (`admin.tokens.list`) | `admin_tokens` src/web/main.py:2484 | — |
| 57 | GET | `/admin/version` | admin (`admin.version`) | `admin_version` src/web/main.py:2507 | — |
| 58 | GET | `/admin/triggers` | admin (`admin.triggers.list`) | `admin_triggers` src/web/main.py:2517 | — |
| 59 | POST | `/admin/refresh-feed/{feed_name}` | admin (`admin.feed.refresh`) | `refresh_feed` src/web/main.py:2534 | Drop a trigger file for the poller reactor to pick up. |
| 60 | POST | `/admin/force-recompute-cps` | admin (`admin.cps.force_recompute`) | `force_recompute_cps` src/web/main.py:2565 | — |
| 61 | POST | `/admin/force-opsplan-snapshot` | admin (`admin.opsplan.force_snapshot`) | `force_opsplan_snapshot` src/web/main.py:2589 | Force an immediate ATCSCC ops plan snapshot. Optionally specify date for backfill. |
| 62 | POST | `/admin/force-osint-scrape` | admin (`admin.osint.force_scrape`) | `force_osint_scrape` src/web/main.py:2611 | Force an immediate OSINT scrape pass across all enabled scopes. |
| 63 | POST | `/admin/push-alert` | admin (`admin.alert.push`) | `push_alert` src/web/main.py:2626 | Send an ntfy push to any topic. NOT idempotent — each POST sends a separate push. |
| 64 | POST | `/admin/push-test-alert` | admin (`admin.alert.push`) | `push_alert` src/web/main.py:2626 | Send an ntfy push to any topic. NOT idempotent — each POST sends a separate push. |
| 65 | GET | `/admin/vip` | admin (`admin.vip.list`) | `get_vip` src/web/main.py:2672 | — |
| 66 | POST | `/admin/vip` | admin (`admin.vip.add`) | `add_vip` src/web/main.py:2681 | — |
| 67 | DELETE | `/admin/vip/{entry}` | admin (`admin.vip.remove`) | `delete_vip` src/web/main.py:2696 | — |
| 68 | POST | `/admin/bandwidth-priority` | admin (`admin.bandwidth_priority.set`) | `set_bandwidth_priority_route` src/web/main.py:2726 | — |
| 69 | DELETE | `/admin/bandwidth-priority` | admin (`admin.bandwidth_priority.clear`) | `clear_bandwidth_priority_route` src/web/main.py:2745 | — |
| 70 | POST | `/admin/approval-requests` | admin (`admin.approval_request.create`) | `create_approval_request_route` src/web/main.py:2779 | — |
| 71 | GET | `/admin/approval-requests/{request_id}` | admin (`admin.approval_request.get`) | `get_approval_request_route` src/web/main.py:2794 | — |
| 72 | GET | `/admin/approval-requests/{request_id}/resolve` | keyed: per-request resolve key (migration 0068); deny-only for tap/link (allow needs a signature) | `resolve_approval_request_route` src/web/main.py:2805 | Tier 0 (no bearer -- the CF tunnel strips Authorization) but keyed: |
| 73 | GET | `/admin/approval-requests` | admin (`admin.approval_request.list`) | `list_approval_requests_route` src/web/main.py:2831 | Recent approvals for a pattern -- backs the frequency-promotion check. |
| 74 | GET | `/api/v1/approvals` | board `normal` (read scope) | `approvals_pending_route` src/web/main.py:2870 | — |
| 75 | GET | `/api/v1/approvals/{request_id}` | board `normal` (read scope) | `approval_view_route` src/web/main.py:2877 | — |
| 76 | POST | `/api/v1/approvals/{request_id}/resolve` | human SSH signature over the request (approval_signers, namespace corporatetraveldc-approval) | `approval_signed_resolve_route` src/web/main.py:2896 | Tier 0 by design (the signature IS the credential; the CF tunnel strips |
| 77 | POST | `/api/v1/council` | board `signed` (account SSH signature) | `council_request_route` src/web/main.py:2923 | — |
| 78 | GET | `/api/v1/council` | board `normal` (read scope) | `council_list_route` src/web/main.py:2945 | — |
| 79 | GET | `/api/v1/council/{cid}` | board `normal` (read scope) | `council_get_route` src/web/main.py:2952 | — |
| 80 | POST | `/api/v1/council/{cid}/close` | board `signed` | `council_close_route` src/web/main.py:2962 | — |
| 81 | POST | `/api/v1/workspace/contribute` | board `signed` | `workspace_contribute_route` src/web/main.py:2979 | Create-only, attributed write into the shared ghostwriting workspace. |
| 82 | GET | `/admin/watchdog/status` | admin (`admin.watchdog.status`) | `watchdog_status` src/web/main.py:3014 | Return last ctdi-watchdog run result. |
| 83 | GET | `/.well-known/oauth-authorization-server` | T0 (public metadata) | `as_metadata` src/web/routes/agent_gateway.py:45 | — |
| 84 | GET | `/.well-known/oauth-protected-resource` | T0 (public metadata) | `pr_metadata` src/web/routes/agent_gateway.py:63 | — |
| 85 | GET | `/.well-known/oauth-protected-resource/mcp/{slug}` | T0 (public metadata) | `pr_metadata` src/web/routes/agent_gateway.py:63 | — |
| 86 | POST | `/oauth/register` | T0 (dynamic client registration, public clients) | `oauth_register` src/web/routes/agent_gateway.py:70 | — |
| 87 | GET | `/oauth/authorize` | T0 page; grant requires operator-signed approval (kind connector-link) | `oauth_authorize` src/web/routes/agent_gateway.py:113 | — |
| 88 | GET | `/oauth/authorize/status` | T0 (polled by consent page) | `oauth_authorize_status` src/web/routes/agent_gateway.py:138 | — |
| 89 | POST | `/oauth/token` | OAuth client: authorization_code + PKCE S256 / refresh_token | `oauth_token` src/web/routes/agent_gateway.py:146 | — |
| 90 | POST | `/oauth/revoke` | OAuth client (RFC 7009) | `oauth_revoke` src/web/routes/agent_gateway.py:162 | — |
| 91 | GET | `/mcp/{slug}` | none (always 405) | `mcp_get` src/web/routes/agent_gateway.py:274 | — |
| 92 | POST | `/mcp/{slug}` | OAuth Bearer access token bound to the slug's connector | `mcp_post` src/web/routes/agent_gateway.py:279 | — |
| 93 | GET | `/api/v1/airspace` | T0 | `get_airspace` src/web/routes/airspace.py:17 | Returns a GeoJSON FeatureCollection with: |
| 94 | GET | `/api/v1/airspace/{feature_id}` | T0 | `get_airspace_feature` src/web/routes/airspace.py:36 | Returns a single GeoJSON Feature. |
| 95 | GET | `/console/manifest.webmanifest` | Host must be in CONSOLE_HOSTS (tailnet name), else 404 | `console_manifest` src/web/routes/console.py:555 | — |
| 96 | GET | `/console/icons/{name}` | Host must be in CONSOLE_HOSTS, else 404 | `console_icon` src/web/routes/console.py:562 | — |
| 97 | GET | `/console` | CONSOLE_HOSTS + console session cookie (sign-in page otherwise) | `console` src/web/routes/console.py:569 | — |
| 98 | POST | `/console/login` | CONSOLE_HOSTS; creates an approval (kind console-login) the operator must SSH-sign | `console_login` src/web/routes/console.py:604 | — |
| 99 | POST | `/console/logout` | console session + CSRF | `console_logout` src/web/routes/console.py:628 | — |
| 100 | POST | `/console/act` | console session + CSRF; gateway-thaw and connector-hold additionally need a signed approval | `console_act` src/web/routes/console.py:638 | — |
| 101 | GET | `/api/v1/feeds/usage` | T0 | `get_data_usage` src/web/routes/data_usage.py:10 | Per-feed data usage statistics since last restart. |
| 102 | GET | `/api/v1/fids/{airport}` | T0 | `fids_snapshot` src/web/routes/fids.py:45 | Feed health snapshot for an airport -- Tier 0. |
| 103 | GET | `/api/v1/fids/{airport}/arrivals` | T0 | `fids_arrivals` src/web/routes/fids.py:63 | Layered arrivals lookup -- Tier 0. |
| 104 | GET | `/api/v1/fids/{airport}/{flight}` | T0 | `fids_flight` src/web/routes/fids.py:100 | Gate + baggage carousel + status for a specific arrival -- Tier 0. |
| 105 | POST | `/api/v1/remember` | admin (`vault.remember`) | `remember` src/web/routes/remember.py:34 | Capture a manual note into the vault (01-Sources/manual/). Admin |
| 106 | GET | `/api/v1/sectors` | T0 | `get_sectors` src/web/routes/sectors.py:32 | Per-sector rollup: rolling 15-min window count, prior-window count, |
| 107 | GET | `/api/v1/sectors/by-feed` | T0 | `get_sectors_by_feed` src/web/routes/sectors.py:43 | Reverse view: per-feed rollup of which sectors it's touching right now |
| 108 | POST | `/api/v1/sectors/{sector}/silence` | admin (`sectors.sector.silence`) | `silence_sector` src/web/routes/sectors.py:56 | Opt a sector in/out of alert silencing. Silencing suppresses ntfy |
| 109 | POST | `/api/v1/sectors/feed/{feed_name}/silence` | admin (`sectors.feed.silence`) | `silence_feed` src/web/routes/sectors.py:70 | Opt a feed (e.g. 'tfms', 'tfms_aptc', 'tfms_gadv') in/out of alert |
| 110 | GET | `/api/v1/sectors/topic/{topic}` | T0 | `get_topic` src/web/routes/sectors.py:94 | Effective throttle/enable/sanitize settings for one literal ntfy |
| 111 | POST | `/api/v1/sectors/topic/{topic}/throttle` | admin (`sectors.topic.throttle`) | `throttle_topic` src/web/routes/sectors.py:102 | Override the minimum interval (seconds) between pushes for one |
| 112 | POST | `/api/v1/sectors/topic/{topic}/enabled` | admin (`sectors.topic.enabled`) | `enable_topic` src/web/routes/sectors.py:117 | Turn one topic's pushes on/off entirely, independent of sector/feed |
| 113 | POST | `/api/v1/sectors/topic/{topic}/sanitize` | admin (`sectors.topic.sanitize`) | `sanitize_topic` src/web/routes/sectors.py:130 | Mark one topic's pushes to be identifier-masked (N-numbers, ICAO |
| 114 | GET | `/api/v1/watchlist` | T1 | `list_watchlist_entries` src/web/routes/watchlist.py:83 | List all active watchlist entries (permanent + transient). Tier 1+. |
| 115 | GET | `/api/v1/watchlist/history` | T1 | `watchlist_history` src/web/routes/watchlist.py:94 | Recent watchlist events. Tier 1+. |
| 116 | POST | `/api/v1/watchlist/flights` | admin (`watchlist.flight.add`) | `add_flight_watchlist` src/web/routes/watchlist.py:137 | Add a transient flight watchlist entry. Admin required. |
| 117 | POST | `/api/v1/watchlist/trains` | admin (`watchlist.train.add`) | `add_train_watchlist` src/web/routes/watchlist.py:354 | Add a transient train watchlist entry. Admin required. |
| 118 | POST | `/api/v1/watchlist/vessels` | admin (`watchlist.vessel.add`) | `add_vessel_watchlist` src/web/routes/watchlist.py:431 | Add a transient vessel (yacht/cruise ship) watchlist entry by MMSI. Admin required. |
| 119 | DELETE | `/api/v1/watchlist/batch` | admin (`watchlist.batch_remove`) | `remove_watchlist_batch` src/web/routes/watchlist.py:491 | Remove multiple watchlist entries by ID array. Admin required. |
| 120 | DELETE | `/api/v1/watchlist/{entry_id}` | admin (`watchlist.entry.remove`) | `remove_watchlist_entry` src/web/routes/watchlist.py:535 | Remove a watchlist entry (either tier). Admin required. |
| 121 | POST | `/api/v1/watchlist/flights/batch` | admin (`watchlist.flight.add_batch`) | `add_flight_watchlist_batch` src/web/routes/watchlist.py:589 | Add multiple transient flight watchlist entries. Admin required. |
| 122 | POST | `/api/v1/watchlist/trains/batch` | admin (`watchlist.train.add_batch`) | `add_train_watchlist_batch` src/web/routes/watchlist.py:666 | Add multiple transient train watchlist entries. Admin required. |
| 123 | POST | `/api/v1/watchlist/permanent/batch` | admin (`watchlist.permanent.add_batch`) | `add_permanent_watchlist_batch` src/web/routes/watchlist.py:798 | Merge entries into permanent watchlist JSON files atomically. |
| 124 | POST | `/webhooks/limoanywhere/reservations` | shared-secret header (503 until secret set) | `limoanywhere_reservation` src/web/routes/webhooks.py:178 | Receives LimoAnywhere Customer API reservation webhook deliveries. |
| 125 | POST | `/webhooks/ringcentral/events` | shared-secret header (503 until secret set) | `ringcentral_event` src/web/routes/webhooks.py:235 | — |
| 126 | POST | `/webhooks/3cx/events` | shared-secret header (503 until secret set) | `threecx_event` src/web/routes/webhooks.py:269 | Receives 3CX Call Control / WebSocket-bridged call events. |

### 4.2 Reachability by hostname (nginx, live `/etc/nginx/conf.d/` == tracked `nginx/conf.d/` for the three files below, `diff`)

| Path into :8000 | Host / vhost | What is exposed |
|---|---|---|
| `dispatch.example.com` (Cloudflare tunnel, CF Access app) | `nginx/conf.d/dispatch.example.com.conf` | everything except `^~ /console` (returns 404); every location sets `X-CTDI-Public: 1`, so all bearer tiers clamp to T0. Board/approval/gateway/webhook routes authenticate by their own means and still work here. |
| `agents.example.com` (tunnel, no CF Access by design) | `nginx/conf.d/agents.example.com.conf` | only the two `.well-known/oauth-*` documents (optionally `/mcp/<slug>`-suffixed), `/oauth/{register,authorize,authorize/status,token,revoke}` and `/mcp/<slug>` (`[a-z0-9-]+`), by one regex `location` (line 19); `location / { return 404; }` |
| `corporatetraveldc-dispatch.tailxxxxxxx.ts.net` :443 (tailnet) | `nginx/conf.d/tailscale-dispatch-runner.conf` | `^~ /console` → :8000; everything else → runner :8001 |
| `100.x.x.x:8000`, `127.0.0.1:8000` | direct (no nginx) | all routes; no `X-CTDI-Public`, so bearer tokens resolve normally. The console still refuses any `Host` not in `CONSOLE_HOSTS` (`routes/console.py:45-75`). The gateway routes have no in-app Host check. |

### 4.3 Agent gateway MCP surface (`src/web/routes/agent_gateway.py`)

`POST /mcp/<slug>` (JSON-RPC, streamable HTTP, Bearer access token) exposes seven tools:
`status`, `board_read`, `board_post`, `research_list`, `research_read`,
`workspace_contribute`, `council_request` — attributed to the connector's account. Kill
switch and per-connector state: `agent_gateway_settings` / `agent_connectors`
(0071/0073), CLI `scripts/agent-gateway.sh`.

---

## 5. Data layer

### 5.1 Backend

- Live backend: Postgres 16 in `corporatetraveldc-pgsql` (127.0.0.1:5432; containers use the `corporatetraveldc-pgsql-sock` volume at `/var/run/postgresql`). Selected by `DISPATCH_DB_BACKEND=postgres` (`config/dispatch.env:30`); the code default is still `sqlite` (`db_backend.py:139`).
- Migration runner: `scripts/pg_migrate.py` via `scripts/pg-migrate.sh`, filename order, tracked in `schema_migrations`.
- Applied state of 0071–0073 on the live database: `[UNVERIFIED: no DB credentials used; healthz returns "status":"ok"]`.
- Table count: 119 distinct `CREATE TABLE IF NOT EXISTS` across the migrations (`grep -rhoE "CREATE TABLE IF NOT EXISTS [a-z_0-9]+" src/common/pg_schema/*.sql | sort -u | wc -l`).

### 5.2 Migrations (`src/common/pg_schema/`, 73 files)

0002–0045 are auto-translations of the SQLite `SCHEMA*` blocks in `common/db.py` /
`common/db_swim.py`; 0046 onward are hand-written. There is no 0074.

| Migration | Purpose (file header) | Tables created (+ altered) |
|---|---|---|
| `0001_bootstrap.sql` | bootstrap the schema_migrations tracking table itself. scripts/pg_migrate.py creates this before applying anything else and | `schema_migrations` |
| `0002_schema.sql` | SCHEMA (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `audit_log`, `auth_tokens`, `cps_scores`, `feed_state`, `hot_alerts`, `metar_snapshot`, `nas_programs`, `tfrs`, `trigger_log` |
| `0003_schema_v2.sql` | SCHEMA_V2 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `amtrak_status`, `notams`, `nws_alerts`, `nws_forecast`, `ops_plan` |
| `0004_schema_v3.sql` | SCHEMA_V3 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `atcscc_opsplan`, `runsheet`, `watchlist_sessions` |
| `0005_schema_v4.sql` | SCHEMA_V4 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `flight_events`, `ustrains_departures` |
| `0006_schema_v5.sql` | SCHEMA_V5 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `surface_tracks`, `swim_alerts`, `terminal_tracks`, `watchlist_entries`, `watchlist_history` |
| `0007_schema_usage.sql` | SCHEMA_USAGE (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `feed_data_usage` |
| `0008_schema_v6.sql` | SCHEMA_V6 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `itws_alerts`, `tbfm_sequences` |
| `0009_schema_v7.sql` | SCHEMA_V7 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `acars_messages`, `local_aircraft`, `local_airspace_alerts` |
| `0010_schema_v8.sql` | SCHEMA_V8 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_history` |
| `0011_schema_v9.sql` | SCHEMA_V9 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `brief_archive` |
| `0012_schema_v10.sql` | SCHEMA_V10 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `osint_items`, `osint_scopes` |
| `0013_schema_v12.sql` | SCHEMA_V12 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `wpc_discussions` |
| `0014_schema_v13.sql` | SCHEMA_V13 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `notams` |
| `0015_schema_v14.sql` | SCHEMA_V14 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `webhook_events` |
| `0016_schema_v15.sql` | SCHEMA_V15 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `international_aviation_feed` |
| `0017_schema_v16.sql` | SCHEMA_V16 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0018_schema_v18.sql` | SCHEMA_V18 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0019_schema_v19.sql` | SCHEMA_V19 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0020_schema_v20.sql` | SCHEMA_V20 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `bandwidth_priority_state` |
| `0021_schema_v21.sql` | SCHEMA_V21 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `approval_requests` |
| `0022_schema_v22.sql` | SCHEMA_V22 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | — |
| `0023_schema_v23.sql` | SCHEMA_V23 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0024_schema_v24.sql` | SCHEMA_V24 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | — |
| `0025_schema_v26.sql` | SCHEMA_V26 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `train_events`, `vessel_events` |
| `0026_schema_v28.sql` | SCHEMA_V28 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `stdds_safety_status`, `surface_movement_events` |
| `0027_schema_v29.sql` | SCHEMA_V29 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `audit_log` |
| `0028_schema_v30.sql` | SCHEMA_V30 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `stdds_safety_status_history` |
| `0029_schema_v31.sql` | SCHEMA_V31 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `vessel_events` |
| `0030_schema_v32.sql` | SCHEMA_V32 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `osint_scopes` |
| `0031_schema_v33.sql` | SCHEMA_V33 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `osint_items` |
| `0032_schema_v34.sql` | SCHEMA_V34 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `flight_ooooi_times` |
| `0033_schema_v35.sql` | SCHEMA_V35 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `session_grants` |
| `0034_schema_v36.sql` | SCHEMA_V36 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `nas_programs` |
| `0035_schema_v37.sql` | SCHEMA_V37 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0036_schema_v39.sql` | SCHEMA_V39 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0037_schema_v40.sql` | SCHEMA_V40 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0038_schema_v43.sql` | SCHEMA_V43 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | alter `watchlist_entries` |
| `0039_schema_v44.sql` | SCHEMA_V44 (src/common/db.py) Auto-translated from the SQLite schema block of the same name | `metar_history`, `stdds_rvr_history` |
| `0040_schema_swim_v41.sql` | SCHEMA_SWIM_V41 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | `datis_snapshots`, `fdps_destination_changes`, `stdds_rvr`, `tdes_departure_events`, `tdls_messages`, `tfms_edct_slots` + alter `flight_events` |
| `0041_schema_swim_v42.sql` | SCHEMA_SWIM_V42 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | `tfms_param_delay_stats`, `tfms_reroutes` + alter `tdls_messages` |
| `0042_schema_swim_v44.sql` | SCHEMA_SWIM_V44 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | `fdps_diversion_continuations` |
| `0043_schema_swim_v45.sql` | SCHEMA_SWIM_V45 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | alter `fdps_diversion_continuations` |
| `0044_schema_swim_v46.sql` | SCHEMA_SWIM_V46 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | `fdps_route_versions`, `tfms_plan_removals` |
| `0045_schema_swim_v47.sql` | SCHEMA_SWIM_V47 (src/common/db_swim.py) Auto-translated from the SQLite schema block of the same name | `convective_sigmet_archive` |
| `0046_v46_tbfm_eta_kind.sql` | v46 (src/common/db.py init_db_v46, no SCHEMA_V46 constant -- inline PRAGMA-guarded ALTER, hand-translated). | alter `tbfm_sequences` |
| `0047_ensure_pull_path_and_board.sql` | pull_path_status + board_messages Hand-translated -- these tables are NOT defined via any SCHEMA_V* | `pull_path_status`, `board_messages` |
| `0048_ensure_board_auth.sql` | board_enroll_nonces, board_tokens, board_presence, board_refresh_grace. | `board_enroll_nonces`, `board_tokens`, `board_presence`, `board_refresh_grace` |
| `0049_auth_tokens_department.sql` | auth_tokens.department -- Phase 2 drift fix. Found during Phase 2 (data-copy rehearsal, docs/POSTGRES_MIGRATION.md §5) | alter `auth_tokens`, `in` |
| `0050_widen_real_to_double_precision.sql` | REAL -> DOUBLE PRECISION on every numeric column that mirrors a SQLite REAL column -- Phase 2 pre-copy audit fix. | alter `acars_messages`, `amtrak_status`, `approval_requests`, `atcscc_opsplan`, `audit_log`, `auth_tokens`, `bandwidth_priority_state`, `board_enroll_nonces`, `board_presence`, `board_refresh_grace`, `board_tokens`, `cps_scores`, `fdps_route_versions`, `feed_data_usage`, `feed_state`, `flight_events`, `flight_ooooi_times`, `hot_alerts`, `in`, `international_aviation_feed`, `local_aircraft`, `local_airspace_alerts`, `metar_history`, `metar_snapshot`, `nas_programs`, `notams`, `nws_alerts`, `nws_forecast`, `ops_plan`, `osint_items`, `osint_scopes`, `pull_path_status`, `runsheet`, `session_grants`, `surface_movement_events`, `surface_tracks`, `terminal_tracks`, `tfms_param_delay_stats`, `tfms_plan_removals`, `tfrs`, `train_events`, `trigger_log`, `ustrains_departures`, `vessel_events`, `watchlist_sessions`, `webhook_events`, `wpc_discussions` |
| `0051_widen_feed_data_usage_bytes_in.sql` | 0051_widen_feed_data_usage_bytes_in.sql Same class of bug as 0050 (SQLite INTEGER is dynamically-sized/64-bit | alter `feed_data_usage` |
| `0052_reference_cifp.sql` | CIFP reference tables (was SQLite-only per docs/POSTGRES_MIGRATION.md §2's original "11 reference tables stay | `cifp_fixes`, `cifp_procedure_legs`, `cifp_holds`, `cifp_meta` |
| `0053_reference_faa.sql` | FAA registry reference tables -- same reversal and rationale as 0052's header (JOIN-driven, not a policy override). | `faa_aircraft_registry`, `faa_aircraft_reference`, `faa_registry_meta`, `faa_ladd_aircraft` |
| `0054_reference_opensky_codeshare.sql` | OpenSky registry + codeshare_map -- same reversal and rationale as 0052/0053's headers. | `codeshare_map`, `opensky_aircraft_registry`, `opensky_registry_meta` |
| `0055_second_brain_index.sql` | second-brain vault index + semantic layer (was /var/lib/corporatetraveldc/second_brain_index.db, SQLite -- | `semantic_note_derivations`, `vault_documents`, `vault_notes_fulltext`, `entities`, `vault_links`, `semantic_note_chronology`, `semantic_meta`, `semantic_facets`, `semantic_concepts`, `semantic_labels`, `semantic_relations`, `semantic_agents`, `semantic_metrics`, `semantic_note_concepts`, `semantic_unmapped_tags` |
| `0056_dispatch_chat.sql` | dispatch chat history (was /var/lib/corporatetraveldc/dispatch-chat.db, a standalone SQLite file | `chat_messages` |
| `0057_demo.sql` | demo archive (demo.db) + demo access profiles (demo_access.db) -- the last two LIVE, non-reference SQLite stores | `demo_snapshots`, `demo_profiles` |
| `0058_station_coordinates.sql` | Amtrak station coordinate reference table. Geometric reasoning Phase 0 (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md, | `station_coordinates` |
| `0059_semantic_geometry_refs.sql` | geometric-reasoning Phase 0 instance-reference table. docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md's assign_geometry() needs | `semantic_note_instance_refs` |
| `0060_aircraft_type_designators.sql` | ICAO aircraft type designator reference table Static reference data (designator -> aircraft type name), sourced from | `aircraft_type_designators` |
| `0061_site_origin.sql` | site-of-origin marking on the semantic-layer graph tables. Added 2026-09-21 ahead of geometric reasoning Phase 2/3, on the finding in | alter `semantic_note_derivations`, `semantic_note_instance_refs` |
| `0062_audit_tamper_evidence.sql` | tamper-evident audit log (hash chain + signed checkpoints + signed archive stubs). | `audit_checkpoints`, `audit_archive_stubs` + alter `audit_log` |
| `0063_ladd_removals.sql` | persistent LADD removal list. The FAA distributes LADD as two filter files (FAA Source, Industry) plus a | `faa_ladd_removals` |
| `0064_train_phase_eta.sql` | train phase state + live ETA on watchlist_entries. Closes parity breaks 1 and 2 from docs/TRAIN_PARITY_DESIGN_2026-09-23.md, | alter `watchlist_entries` |
| `0065_oooi_authority_lock.sql` | source-authority lock for OOOI phase. Operator directive 2026-09-23, written after two live false-positive | alter `watchlist_entries` |
| `0066_board_signers.sql` | identity-based board signing registry. Operator directive 2026-10-04: keep X-Board-Key (master BOARD_KEY + minted | `board_signers` |
| `0067_board_authz_tiers.sql` | roles/kinds for board signers, token revocation stamps. Operator directive 2026-10-04 (authorisation model): | alter `board_signers`, `board_tokens` |
| `0068_approval_resolve_keys.sql` | per-request, per-action resolve keys for the approval gate (adversarial duel 2026-10-04, finding X1). | alter `approval_requests` |
| `0069_signed_approvals_council_workspace.sql` | Wave 2 (2026-10-04): human-signed approvals, council/arena convenes, shared-workspace grants. | `approval_signers`, `council_sessions`, `workspace_grants` + alter `approval_requests` |
| `0070_auth_token_allowed_actions.sql` | per-token action scopes (2026-10-05). An admin-tier API token used to mean "every admin route". Cowork held one | alter `auth_tokens` |
| `0071_agent_gateway.sql` | OAuth + remote-MCP gateway for CLOUD agents (Cowork via Claude custom connectors, ChatGPT via developer-mode MCP / GPT | `agent_connectors`, `oauth_clients`, `oauth_pending`, `agent_connections`, `oauth_tokens` |
| `0072_es_invites.sql` | Executive Standard reader invites + promo codes (2026-10-05). Logic + sqlite twin: src/common/es_invites.py. Public host: | `exec_standard_sources`, `es_grants`, `es_promos`, `es_exchanges`, `es_sessions`, `es_invite_settings`, `es_invite_events` |
| `0073_console_and_gateway_switch.sql` | 0073_console_and_gateway_switch.sql (2026-10-05) * agent_gateway_settings: the gateway kill switch (common/agent_gateway.py | `agent_gateway_settings`, `console_sessions` |

### 5.3 SQLite paths still in use

`grep -rln 'sqlite3.connect' src scripts`; `grep -rn DISPATCH_DB src config`; `ls -la /var/lib/corporatetraveldc/*.db`.

| Path | Opened by | State (ls, 18:10Z) |
|---|---|---|
| `/var/lib/corporatetraveldc-demo-source/demo-source.db` (`DEMO_DB`) | `src/demo/demo_api.py:64,147` read-only (`mode=ro`); written only by `scripts/scrub-demo-source.py` | 1.88 GB, mtime 2026-08-14 — deliberate: the sovereign scrubbed public-demo source stays SQLite |
| `/var/lib/corporatetraveldc/acarshub/messages.db` (`ACARSHUB_DB_PATH`) | `src/common/acars.py:43,279` read-only | acarshub container's own DB |
| `DISPATCH_DB` = `/var/lib/corporatetraveldc/corporatetraveldc.db` (`config.py:65`, `config/dispatch.env:12`) | `db_backend.ref_conn()` (SQLite-only handle; no caller outside `db_backend.py` found by `grep -rn 'ref_conn()'`), the `sqlite` backend path, tests | **exists**: 69,632 bytes, mtime 2026-10-05 23:21Z. The 09-28 reference said this file was gone; it has been recreated small (CLAUDE.md records a 10-05 CLI smoke test that created `es_*` tables in it and were dropped). Writer of the 23:21Z mtime `[UNVERIFIED]`. |
| `/var/lib/corporatetraveldc/demo.db` (4.45 GB, mtime 2026-09-21), `demo_access.db`, `dispatch-chat.db`, `dispatch.db` (0 bytes) | none in production code (migrated by 0056/0057; `scripts/migrate-*-to-pg.py` read them) | stale files still on disk |
| `/var/lib/corporatetraveldc/second_brain_index.db` | none (vestigial constants `second_brain/index_db.py:78`, `semantic/compile.py:91`) | **deleted** (`ls`: No such file) — the 09-28 hazard is closed for the file; the constants remain |

`db_backend.py`'s module docstring (lines 40-46) and `ref_conn()` docstring still say the
11 reference tables "never move" and live in SQLite; migrations 0052–0054 moved them to
Postgres and `cifp_lookup.py:20` says so. Docstring is stale, not the behaviour.

### 5.4 Main tables and their writers

Heuristic: for each accessor in `common/db.py`, `db_swim.py`, `governance.py`,
`agent_gateway.py`, `es_invites.py`, `second_brain/index_db.py`, `demo/db.py` whose body
contains `INSERT/UPDATE/DELETE` on a table, the packages that call that accessor
(AST + grep; direct SQL outside those modules added by hand where found).

| Domain | Tables | Written by |
|---|---|---|
| Feed health | `feed_state`, `feed_data_usage`, `pull_path_status`, `bandwidth_priority_state` | ingest (`push:<feed>` heartbeats, usage counters), poller fetchers/skills, web/pusher (bandwidth priority) |
| SWIM flight / surface | `flight_events`, `flight_ooooi_times`, `fdps_route_versions`, `fdps_destination_changes`, `fdps_diversion_continuations`, `surface_tracks`, `terminal_tracks`, `surface_movement_events`, `stdds_safety_status(_history)`, `stdds_rvr(_history)`, `tdes_departure_events`, `tdls_messages`, `datis_snapshots`, `swim_alerts` | ingest parsers; `flight_events` also poller skills (`tbfm_arrival_enrichment`, cleanup); `surface_movement_events` cleanup by poller |
| SWIM traffic management | `nas_programs`, `tfms_edct_slots`, `tfms_reroutes`, `tfms_plan_removals`, `tfms_param_delay_stats`, `tbfm_sequences`, `itws_alerts` | ingest (`tfms_parser`, `tbfm_parser`, `itws_parser`); `nas_programs` also poller `nas` fetcher |
| Aviation state (REST) | `tfrs`, `metar_snapshot`, `metar_history`, `notams`, `nws_alerts`, `nws_forecast`, `wpc_discussions`, `atcscc_opsplan`, `ops_plan`, `runsheet`, `international_aviation_feed`, `convective_sigmet_archive` | poller fetchers; `notams`/`nws_alerts`/`wpc_discussions` also ingest (AIM, NWWS); `tfrs` enrichment by poller skills + pusher |
| Local receivers | `local_aircraft`, `acars_messages`, `local_airspace_alerts` | ingest `local_airspace.py` |
| Rail / marine | `amtrak_status`, `train_events`, `ustrains_departures`, `vessel_events` | amtrak-tracker, ingest, poller `amtrak` fetcher; `vessel_events` poller main |
| Watchlist | `watchlist_entries`, `watchlist_history`, `watchlist_sessions`, `codeshare_map` | web routes, poller `WatchlistSweep`, `shared/watchlist.py`, ingest (OOOI updates), pusher (history/sessions); `codeshare_map` also `scripts/seed-dc-codeshare.py` (timer `corporatetraveldc-codeshare-seed`) |
| Registries / reference | `faa_aircraft_registry`, `faa_aircraft_reference`, `faa_ladd_aircraft`, `faa_ladd_removals`, `faa_registry_meta`, `opensky_aircraft_registry`, `opensky_registry_meta`, `cifp_*`, `aircraft_type_designators`, `station_coordinates` | poller fetchers (`faa_registry`, `opensky_registry`), poller skills (`faa_cifp_parse`), `scripts/import-ladd-filter.py` |
| Decision output | `cps_scores`, `hot_alerts`, `brief_archive`, `osint_scopes`, `osint_items` | poller skills (`osint_scopes` by web admin routes) |
| Governance / auth | `audit_log`, `audit_checkpoints`, `audit_archive_stubs`, `auth_tokens`, `trigger_log`, `approval_requests`, `approval_signers`, `session_grants`, `council_sessions`, `workspace_grants` | `auth.require_admin` (audit), web, poller skills (`board_sweep`, `audit_log_archive`), CLIs (`ctdc_token`, `scripts/mint-agent-api-token.sh`, `approver-ctl.sh`, `workspace-grants.sh`) |
| Board | `board_messages`, `board_tokens`, `board_enroll_nonces`, `board_presence`, `board_refresh_grace`, `board_signers` | web board routes, poller `board_sweep`, `scripts/board-*.py`, `scripts/board-signer-ctl.sh` |
| Agent gateway / console | `agent_connectors`, `agent_connections`, `oauth_clients`, `oauth_pending`, `oauth_tokens`, `agent_gateway_settings`, `console_sessions` | web (`routes/agent_gateway.py`, `routes/console.py`), `scripts/agent-gateway.sh`, team-liveness (revocations) |
| Executive Standard | `exec_standard_sources`, `es_grants`, `es_promos`, `es_exchanges`, `es_sessions`, `es_invite_settings`, `es_invite_events` | web console, `scripts/es-invite.sh`, the execstandard verifier (sessions/exchanges) |
| Second brain / semantic | `vault_documents`, `vault_notes_fulltext`, `vault_links`, `entities`, `semantic_*` (11 tables), `semantic_note_instance_refs` | `second_brain` (index scan, semantic compile), poller skills |
| Chat / demo / webhooks | `chat_messages`; `demo_snapshots`, `demo_profiles`; `webhook_events` | runner (direct SQL); demo recorder / demo-api admin; web webhooks |

---

## 6. Ingest and feeds

### 6.1 SWIM (six Solace feeds, `src/ingest/swim_client.py`, `src/ingest/config.py:116-154`)

Per feed key `K`, env var NAMES: `SWIM_NMS_HOST_K` (fallback `SWIM_NMS_HOST`, default
`tcps://ems1.swim.faa.gov:55443`), `SWIM_NMS_VPN_K` (default = `K`), `SWIM_NMS_USER_K`,
`SWIM_NMS_PASS_K`, `SWIM_NMS_QUEUE_K`. Tracked `config/dispatch.env:40-48` sets `SWIM_NMS_HOST=tcps://ems2.swim.faa.gov:55443` and VPNs `FDPS STDDS TFMS AIM_FNS TBFM ITWS` (live `/etc` copy not read). Global `SWIM_NMS_ENABLED`, `SWIM_NMS_SKIP_FEEDS`,
`SWIM_BACKLOG_STALE_SECONDS` (default 28,800 s). One container per feed: each
`corporatetraveldc-ingest-<feed>` quadlet sets `SWIM_NMS_SKIP_FEEDS` to the other five,
`NWWS_ENABLED=false`, `AMTRAK_ENABLED=false`, `LOCAL_AIRSPACE_ENABLED=false`.

| Session key | Env key `K` | Container | Parser | Main tables | Heartbeat | REST twin |
|---|---|---|---|---|---|---|
| `fdps` | `FDPS` | `ingest-fdps` | `parsers/fdps_parser.py` (FIXM) | `flight_events`, `fdps_*` | `push:fdps` | none (by decision) |
| `stdds` | `STDDS` | `ingest-stdds` | `parsers/smes_parser.py` (SMES/ASDE-X, TDES, TDLS, RVR) | `surface_*`, `terminal_tracks`, `stdds_*`, `tdes_*`, `tdls_messages`, `datis_snapshots` | `push:stdds` | none |
| `tfms` | `TFMS` | `ingest-tfms` | `parsers/tfms_parser.py` | `nas_programs`, `tfms_*`, `flight_events` | `push:tfms` | none (`nas` fetcher runs independently) |
| `fns` | `AIM` (VPN `AIM_FNS`) | `ingest-notam` | `parsers/aim_parser.py` (AIXM 5.1) | `notams` | `push:fns` | `notam` fetcher |
| `tbfm` | `TBFM` | `ingest-tbfm` | `parsers/tbfm_parser.py` | `tbfm_sequences` | `push:tbfm` | none |
| `itws` | `ITWS` | `ingest-itws` | `parsers/itws_parser.py` | `itws_alerts` | `push:itws` | none |

Cadence / thresholds as coded: heartbeat every 30 s (`HEARTBEAT_INTERVAL`,
`swim_client.py:41`); feed priority `fdps,tfms` high, `stdds,tbfm,itws,fns` low
(`swim_client.py:276-277`); usage counters flushed every 5 s; backlog older than 8 h is
fast-forwarded. All parsers share `parsers/geo_filter.py`.

### 6.2 Push/pull failover (`src/ingest/failover.py`)

- `PUSH_FEEDS = fdps stdds fns tbfm tfms itws nws amtrak`.
- Poller gate: a REST twin stays idle while its push heartbeat is fresher than `FALLBACK_MAX_AGE = 90` s; `amtrak` uses `push_max_age = 660` s.
- `REST_FALLBACKS = {nws: push nws, notam: push fns, amtrak: push amtrak}` — the only three REST fetchers with a push twin.
- Status-page floor `PUSH_STALE_FLOOR_S = 300` s (`/healthz`, `/api/v1/feeds`).
- Backstop: `scripts/failover-kickover-guardrail.py` (timer every 5 min).

### 6.3 Poller REST fetchers (`src/poller/main.py:34-81`, `FETCH_SCHEDULE`)

| Fetcher | Interval | Source |
|---|---|---|
| `tfr` | 300 s | `https://tfr.faa.gov/tfrapi/getTfrList` |
| `metar` | 300 s | aviationweather.gov |
| `nas` | 300 s | `https://nasstatus.faa.gov/api/airport-status-information` |
| `nws` | 300 s, gated by `push:nws` | api.weather.gov |
| `notam` | 300 s, gated by `push:fns` | `https://api-nms.aim.faa.gov/nmsapi/v1/notams` |
| `amtrak` | 300 s, gated by `push:amtrak` (660 s) | `https://api.amtraker.com` v3 |
| `runsheet` | 300 s | operator-populated scheduled-trip JSON + watchlist sessions (`fetchers/runsheet.py`; `fetchers/ops_plan.py` is the older reader) |
| `atcscc_opsplan` | 3,600 s (+ quadlet `corporatetraveldc-daily-opsplan` 11:00Z / 07:00 ET daily during EDT) | no external call: synthesised from NAS programs, NOTAMs and METAR already in the DB (module docstring) |
| `dca_fids`, `iad_fids` | 300 s | flyreagan.com / flydulles.com via `common/airport_fids.py` |
| `eurocontrol` | 900 s | EUROCONTROL NM B2B — `TODO` parse not implemented (`eurocontrol.py:69`) |
| `jasdat` | 900 s | jasdat.go.jp |

Also inside the poller: FAA registry import and OpenSky freshness check on their own
intervals (`poller/main.py:509-575, 2148-2178`), `TriggerReactor` consuming trigger
files dropped by `/admin/refresh-feed`, `/admin/force-*`, and `WatchlistSweep`.
Optional third-party lookups gated on keys: FlightAware AeroAPI
(`poller/main.py:821`, `common/flight_resolver.py:96`, key name `FLIGHTAWARE_API_KEY`),
AISHub (`AIS_AISHUB_BASE`, `poller/main.py:1823`, `runner/main.py:79`). Whether those
keys are set: `[UNVERIFIED: secret files not read]`.

### 6.4 Other feeds

| Feed | Code | Cadence / threshold (as coded) |
|---|---|---|
| NWWS-OI (XMPP) | `ingest/nwws.py`; env `NWWS_XMPP_SERVER`, `NWWS_JID`, `NWWS_MUC`, `NWWS_NICK` | runs in `ingest-core` (`NWWS_ENABLED` code default false, not set in the quadlet, so enabled via an env file); live: `push:nws` age 7 s at 18:16Z (`/api/v1/feeds`) |
| Amtrak | `amtrak_tracker/main.py` (container `amtrak-tracker`) primary; `ingest/amtrak.py` disabled in `ingest-core` (`AMTRAK_ENABLED=false`); poller fallback | `POLL_INTERVAL_SECS` 300, `FILTER_STATION` WAS, `HISTORY_HOURS` 24 |
| Local ADS-B | `ingest/local_airspace.py` (ingest-core only) reading ultrafeeder (`ULTRAFEEDER_URL`, scan radius `ULTRAFEEDER_SCAN_RADIUS_NM` 80); web `/api/v1/adsb` (radius ≤ 250 NM, local receiver only) | watchdogs `adsb-feed-silence-watchdog`, `adsb-link-watchdog` (timers **disabled**) |
| ACARS / VDL2 | `dumpvdl2` + `acarsrouter` (:9080, :15555) + `acarshub` (100.x.x.x:8092); `ingest/local_airspace.py` reads the router (`ACARS_ROUTER_HOST/PORT`, idle warn 1,800 s); `acars-watcher` UDP :5005 + optional `api.airframes.io` (60 s) and Jumpseat (90 s) | `acars-feed-silence-watchdog` timer **disabled**; `sdr-crashloop-guard` every 5 min |
| FIDS DCA/IAD | `common/airport_fids.py`, `poller/fetchers/{dca,iad}_fids.py`, `/api/v1/fids/*` | 300 s |
| METAR / TFR / NWS | §6.3 | 300 s |
| AWC AIRMET/SIGMET | `common/airsigmet.py` (`/api/v1/airmets`), `convective_sigmet_archiver` | archiver every 10 min (`*:4/10`) |
| AIS | `src/ais_watcher/` (UDP :5006) | **not deployed** |
| UTM / drones | `src/utm_watcher/` | **not deployed** |
| RSS / OSINT | `shared/rss_catalog.py`, `osint_monitor` (900 s), `second_brain_rss` (`0/2:10` America/New_York: every even Eastern hour at :10), `rss-bridge` container | |
| Google Trends | `common/trends_signal.py` (trendspy) | called from entity tracking |

---

## 7. Scheduled work

**Host clock is UTC** (`timedatectl`: `Time zone: UTC (UTC, +0000)`). An `OnCalendar`
with `America/New_York` fires on Eastern wall-clock (UTC−4 in EDT until 2026-11-01, UTC−5 after); one with **no suffix fires on UTC**.
Four absolute calendars have no suffix (marked below); see CHANGES findings.

### 7.1 User timers (`.config/systemd/user/*.timer`, 66 tracked)

Live state from `systemctl --user is-enabled/is-active <timer>`. Live-only timers not in
the repo: `blog-substack-reminder.timer` (no next run), `corporatetraveldc-ccw-demo-webdev-expiry.timer`
(last 2026-08-25), `corporatetraveldc-ops-brief-deferred.timer`
(`systemctl --user list-timers --all`).

| Timer | Schedule | TZ suffix | Rand. delay | Runs | Live state (enabled/active) |
|---|---|---|---|---|---|
| `corporatetraveldc-aam-weekly-watch` | Mon *-*-* 02:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-aam-weekly-watch` | enabled/active |
| `corporatetraveldc-acars-feed-silence-watchdog` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/acars-feed-silence-watchdog.sh` | disabled/inactive |
| `corporatetraveldc-adsb-feed-silence-watchdog` | *:1/5 | n/a (relative/sub-hourly) | — | `scripts/adsb-feed-silence-watchdog.sh` | disabled/inactive |
| `corporatetraveldc-adsb-link-watchdog` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/adsb-link-watchdog.sh` | disabled/inactive |
| `corporatetraveldc-board-sweep` | *:0/15 | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-board-sweep` | enabled/active |
| `corporatetraveldc-brief-fallback-monitor` | *-*-* *:50:00; boot+9min | n/a (relative/sub-hourly) | — | `scripts/brief-fallback-monitor.sh` | enabled/active |
| `corporatetraveldc-claude-md-drift-daily` | *-*-* 05:15:00 America/New_York | America/New_York | — | `scripts/claude-md-drift-daily.sh` | enabled/active |
| `corporatetraveldc-client-demo-webdev-expiry@` | — | n/a (relative/sub-hourly) | — | `scripts/client-demo-webdev-expire.sh %i` | disabled/ |
| `corporatetraveldc-codeshare-seed` | *-*-* 04:40:00 America/New_York | America/New_York | 300 | `scripts/with-dispatch-env.sh python3 scripts/seed-dc-codeshare.py` | enabled/active |
| `corporatetraveldc-compliance-egress-push` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/compliance-egress-push.sh` | enabled/active |
| `corporatetraveldc-container-mem-watch` | boot+1min; every 2min | n/a (relative/sub-hourly) | — | `scripts/container-mem-watch.sh` | enabled/active |
| `corporatetraveldc-convective-sigmet-archiver` | *:4/10 | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-convective-sigmet-archiver` | enabled/active |
| `corporatetraveldc-cowork-coord-24h` | boot+30min; every 24h | n/a (relative/sub-hourly) | 12h | `scripts/cowork-coord-24h-check.sh` | enabled/active |
| `corporatetraveldc-cowork-coord-7d` | boot+45min; every 7d | n/a (relative/sub-hourly) | 12h | `scripts/cowork-coord-7d-check.sh` | enabled/active |
| `corporatetraveldc-daily-opsplan` | *-*-* 07:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-daily-opsplan` | enabled/active |
| `corporatetraveldc-data-usage-snapshot` | *-*-* 00:05:00 America/New_York | America/New_York | — | `scripts/snapshot-data-usage.py` | enabled/active |
| `corporatetraveldc-demo-source-refresh` | *-*-* 04:45:00 America/New_York | America/New_York | — | `scripts/scrub-demo-source.py --refresh` | disabled/inactive |
| `corporatetraveldc-dispatch-desk-memo` | Mon *-*-* 03:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-dispatch-desk-memo` | enabled/active |
| `corporatetraveldc-dns-restart` | *-*-* 01:35:00 America/New_York | America/New_York | — | `scripts/scheduled-dns-restart.sh` | enabled/active |
| `corporatetraveldc-docs-drift-weekly` | Mon *-*-* 09:00:00 | none (host clock = UTC) | — | `scripts/weekly-doc-drift-check.sh` | enabled/active |
| `corporatetraveldc-entity-tracking-digest` | *-*-* 00,06,12,18:12:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-entity-tracking-digest` | enabled/active |
| `corporatetraveldc-ep-advance` | *-*-* *:35:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-ep-advance` | enabled/active |
| `corporatetraveldc-faa-cifp-parse` | Thu *-*-* 08:20:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-faa-cifp-parse` | enabled/active |
| `corporatetraveldc-faa-cifp-pull` | Thu *-*-* 08:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-faa-cifp-pull` | enabled/active |
| `corporatetraveldc-failover-kickover-guardrail` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/failover-kickover-guardrail.py` | enabled/active |
| `corporatetraveldc-feed-db-integrity-check` | *:0/30 | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-feed-db-integrity-check` | enabled/active |
| `corporatetraveldc-freshness-audit` | *-*-* 06:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-freshness-audit` | enabled/active |
| `corporatetraveldc-governor-watch` | boot+300; every 6h | n/a (relative/sub-hourly) | — | `scripts/governor-watch.py` | enabled/active |
| `corporatetraveldc-ingest-feed-watch` | *:05:00 | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-ingest-feed-watch` | enabled/active |
| `corporatetraveldc-ingest-restart` | boot+1min; every 2min | n/a (relative/sub-hourly) | — | `scripts/scheduled-ingest-restart.sh` | enabled/active |
| `corporatetraveldc-integrity-sweep` | boot+2min; every 15min | n/a (relative/sub-hourly) | — | `scripts/scheduled-integrity-sweep.sh` | enabled/active |
| `corporatetraveldc-knowledge-graph-compile` | *-*-* 00,06,12,18:08:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-knowledge-graph-compile` | disabled/inactive |
| `corporatetraveldc-llama-restart` | *-*-* 19:45:00 America/New_York | America/New_York | — | `scripts/scheduled-llama-restart.sh` | enabled/active |
| `corporatetraveldc-maintenance-dispatch` | *-*-* *:00,20,40:00 America/New_York | America/New_York | 15min | `scripts/maintenance-dispatch.sh` | enabled/active |
| `corporatetraveldc-net-failover-watchdog` | *:*:0 | n/a (relative/sub-hourly) | — | `scripts/net-failover-watchdog.sh` | enabled/active |
| `corporatetraveldc-nextcloud-health` | boot+2min; every 5min | n/a (relative/sub-hourly) | — | `scripts/nextcloud-health-check.sh` | enabled/active |
| `corporatetraveldc-nms-v240-check` | 2026-08-08 02:30:00 | none (host clock = UTC) | — | quadlet `corporatetraveldc-nms-v240-check` | enabled/active |
| `corporatetraveldc-ntfy-topic-count-watchdog` | *:0/15 | n/a (relative/sub-hourly) | — | `scripts/ntfy-topic-count-watchdog.sh` | enabled/active |
| `corporatetraveldc-ops-brief` | *-*-* *:05:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-ops-brief` | enabled/active |
| `corporatetraveldc-personal-export-watch` | Sat *-*-* 10:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-personal-export-watch` | enabled/active |
| `corporatetraveldc-personal-notes-import` | boot+2min; every 2min | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-personal-notes-import` | enabled/active |
| `corporatetraveldc-podman-prune` | *-*-* 07:15:00 America/New_York | America/New_York | — | `scripts/scheduled-podman-prune.sh` | enabled/active |
| `corporatetraveldc-pull-path-verify` | *-*-* 06,18:00:00 | none (host clock = UTC) | — | quadlet `corporatetraveldc-pull-path-verify` | enabled/active |
| `corporatetraveldc-quiet-window-report` | *-*-* 05:30:00 America/New_York | America/New_York | — | `/bin/sh -c 'if [ "$(date +%%u)" = 7 ]; then exec scripts/with-dispatch-env.sh python3 scri` | enabled/active |
| `corporatetraveldc-research-board-mirror` | boot+5min; every 15min | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-research-board-mirror` | enabled/active |
| `corporatetraveldc-retrofit-links` | *-*-* 00,06,12,18:05:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-retrofit-links` | disabled/inactive |
| `corporatetraveldc-runner-health-watchdog` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/runner-health-watchdog.sh` | enabled/active |
| `corporatetraveldc-sdr-crashloop-guard` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/sdr-crashloop-guard.sh` | enabled/active |
| `corporatetraveldc-second-brain-daily` | *-*-* 23:45:00 America/New_York; every 120min | America/New_York | — | quadlet `corporatetraveldc-second-brain-daily` | enabled/active |
| `corporatetraveldc-second-brain-demo-archiver-daily` | *-*-* 04:15:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-second-brain-demo-archiver-daily` | enabled/active |
| `corporatetraveldc-second-brain-index-scan` | *-*-* 04:00:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-second-brain-index-scan` | enabled/active |
| `corporatetraveldc-second-brain-rss` | *-*-* 0/2:10:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-second-brain-rss` | enabled/active |
| `corporatetraveldc-second-brain-weekly-dump` | Sun *-*-* 02:00:00 | none (host clock = UTC) | — | `scripts/second-brain-weekly-dump.sh` | enabled/active |
| `corporatetraveldc-second-brain-weekly` | Mon *-*-* 04:30:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-second-brain-weekly` | enabled/active |
| `corporatetraveldc-semantic-compile-daily` | *-*-* 00,06,12,18:02:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-semantic-compile-daily` | enabled/active |
| `corporatetraveldc-stack-refresh-tripwire` | hourly | n/a (relative/sub-hourly) | 50min | `scripts/stack-refresh.sh --tripwire` | enabled/active |
| `corporatetraveldc-swim-session-health` | boot+2min; every 5min | n/a (relative/sub-hourly) | — | `scripts/swim-session-health-watch.sh` | enabled/active |
| `corporatetraveldc-tbfm-arrival-enrichment` | *:0/15 | n/a (relative/sub-hourly) | — | quadlet `corporatetraveldc-tbfm-arrival-enrichment` | enabled/active |
| `corporatetraveldc-thermal-ingest-guard` | boot+60; every 120 | n/a (relative/sub-hourly) | — | `scripts/thermal-ingest-guard.py` | enabled/active |
| `corporatetraveldc-thermal-sample` | *:0/5 | n/a (relative/sub-hourly) | — | `scripts/thermal-sample.sh` | enabled/active |
| `corporatetraveldc-uber-traffic-watch` | boot+60; every 120 | n/a (relative/sub-hourly) | — | `scripts/uber-traffic-watch.py` | enabled/active |
| `corporatetraveldc-website-integrity-sweep` | boot+2min; every 15min | n/a (relative/sub-hourly) | — | `/opt/corporatetraveldc/private/csexecutiveservices-website/scripts/scheduled-integrity-swe` | enabled/active |
| `corporatetraveldc-weekly-external-image-update` | Sun *-*-* 04:15:00 America/New_York | America/New_York | — | `scripts/stack-refresh.sh --weekly` | enabled/active |
| `corporatetraveldc-weekly-summary` | Mon *-*-* 03:45:00 America/New_York | America/New_York | — | quadlet `corporatetraveldc-weekly-summary` | enabled/active |
| `nextcloud-cron` | boot+2min; every 5min | n/a (relative/sub-hourly) | — | `/usr/bin/podman exec --user www-data nextcloud-app php -f /var/www/html/cron.php` | enabled/active |
| `ops-brief-rebuild-watcher` | boot+2min; every 2min | n/a (relative/sub-hourly) | — | `%h/ops-brief-rebuild-watcher.sh` | disabled/inactive |

Path units (user): `corporatetraveldc-knowledge-graph-compile.path`,
`corporatetraveldc-semantic-compile-daily.path` (both enabled/active — the knowledge-graph
compile and retrofit-links timers are disabled, so those jobs run on path triggers or not
at all). Slices: `agents.slice`, `humans.slice`, `production.slice`.

### 7.2 System (root) timers and units — tracked in `systemd/`, installed by `scripts/install-root-copies.sh`

| Unit | Schedule | Runs | Live next run (`systemctl list-timers --all`, 18:05Z) |
|---|---|---|---|
| `corporatetraveldc-watchdog.timer` | boot+90 s, every 90 s | `/usr/local/libexec/ctdc/watchdog.sh` (User=root) | 18:06:41Z |
| `corporatetraveldc-tailscale-cert-renew.timer` | `*-*-* 04:00:00 America/New_York` | `/usr/local/libexec/ctdc/renew-tailscale-cert.sh` | 2026-10-07 08:00Z / 04:00 ET |
| `corporatetraveldc-nts-cert-refresh.timer` | `*-*-* 02:15:00 America/New_York` | tracked `ExecStart` names the checkout path; the installed unit (`systemctl cat`) runs `/usr/local/libexec/ctdc/tailscale-cert-refresh-nts.sh` (installer rewrites it) | 2026-10-07 06:15Z / 02:15 ET |
| `corporatetraveldc-team-liveness.timer` + `corporatetraveldc-team-liveness-orders.path` (`PathChanged=/var/lib/corporatetraveldc/team-liveness/orders`) | hourly, ±20 min | `/usr/local/libexec/ctdc/team-liveness.sh --execute --i-am-the-operator` | 18:14Z |
| `corporatetraveldc-skill-grants.timer` | hourly, ±15 min | `/usr/local/libexec/ctdc/skill-grants.sh apply --execute` | 18:06Z |
| `corporatetraveldc-llama-council.timer` | `*-*-* *:41:00` ±5 min | `User=ctdc-agent-llama`; `ExecStartPre=` checkout `scripts/verify-manifest.sh scripts/llama-council-responder.py src/common/board_sign.py`; `ExecStart=/usr/bin/python3` checkout `scripts/llama-council-responder.py` | 18:42Z |
| `corporatetraveldc-watchdog-tune.timer` | `2026-10-11 20:00:00 America/New_York` (one-shot) | `/usr/local/libexec/ctdc/watchdog-tune.sh` | 2026-10-12 00:00Z / 2026-10-11 20:00 ET |
| `corporatetraveldc-stall-monitor.service` | long-running | `/usr/bin/python3 /usr/local/libexec/ctdc/stall-monitor.py` | — |
| `unbound-anchor-refresh.service` (+ `unbound-anchor.service.d/reload.conf`) | via OS `unbound-anchor.timer` | `/usr/local/sbin/unbound-anchor-update` | 2026-10-07 00:00Z |

OS timers also present: `dnf-makecache`, `dnf5-automatic`, `logrotate`,
`plocate-updatedb`, `systemd-tmpfiles-clean`, `raid-check`, `pihole-gravity-update`,
`fstrim`. Failed units at 18:05Z: user `corporatetraveldc-integrity-sweep.service`
(failed while `scripts/stack-refresh.sh` was edited but unsigned; expected to clear on its
next 15-min run after the 18:10Z signed commit — not re-checked); system: none.

### 7.3 Skills → scheduler

`src/poller/skills/*.py` (47) matched against quadlet `Exec=` lines and
`SKILL_SCHEDULE` (`src/poller/main.py:97-147`).

| Scheduler | Skills |
|---|---|
| Poller `SKILL_SCHEDULE` (in-process subprocesses) | `tfr_enrichment` 300 s, `route_impact` 300 s, `cps_recompute` 3,600 s, `train_impact` 900 s (300 s when a train session is active), `flight_impact` 900 s (300 s active), `osint_monitor` 900 s, `flight_events_cleanup` 86,400 s, `surface_events_cleanup` 86,400 s (maintenance-only), `audit_log_archive` 86,400 s (maintenance-only), `retention_prune` 86,400 s |
| Quadlet + timer | `aam_weekly_watch`, `board_sweep`, `convective_sigmet_archiver`, `dispatch_desk_memo`, `entity_tracking_digest`, `ep_advance_brief`, `faa_cifp_pull`, `faa_cifp_parse`, `feed_db_integrity_check`, `freshness_audit`, `ingest_feed_watch`, `ops_brief`, `personal_export_watch`, `second_brain_personal_notes_import`, `pull_path_verify`, `second_brain_research_board_mirror`, `second_brain_daily`, `second_brain_demo_archiver_daily`, `second_brain_rss`, `second_brain_weekly`, `semantic_compile_daily`, `tbfm_arrival_enrichment`, `weekly_summary`, `nms_v240_post_deploy_check` (timer date 2026-08-08, elapsed) |
| Quadlet, rolling maintenance queue | `executive_protection_daily_watch`, `aam_daily_watch`, `aviation_daily_watch`, `concierge_travel_daily_watch`, `gig_economy_daily_watch`, `trains_yachts_daily_watch`, `disruption_weather_digest`, `transport_pattern_digest`, `ep_advance_venues` |
| Quadlet, no timer | `ops_brief` again via `corporatetraveldc-ops-brief-deferred` (live-only timer) |
| None found in this repo | `daily_brief.py`, `executive_standard_sync.py`, `nms_v240_baseline_capture.py` (one-shot, 2026-08), `audit_log_prune.py` (retired refusing stub, referenced only in a comment at `poller/main.py:137`) |

### 7.4 Rolling maintenance windows

- `scripts/quiet-window-report.py` (timer `corporatetraveldc-quiet-window-report`, 09:30Z / 05:30 ET daily during EDT; Sundays also to the vault) buckets the 30-day `thermal-sample` load log by America/New_York weekday × hour and writes `/var/lib/corporatetraveldc/reports/quiet-windows.{md,json}`. It picks `N_WIN=4` windows of 2 h, at least `SPACING_H=4` h apart, from the top `POOL=10`, one forced overnight (start 22:00–03:00 America/New_York) (`quiet-window-report.py:121-137`). Live `today_windows` (generated 2026-10-06 09:30Z): start hours 05, 10, 22 America/New_York (09:00Z, 14:00Z, 02:00Z during EDT), 2 h each (three found).
- `scripts/maintenance-window-guard.sh --rolling` reads `today_windows`, falls back to the fixed `CTDC_MAINTENANCE_WINDOW_*` window (default 03:00Z–09:00Z / 23:00–05:00 ET during EDT, `WINDOW_TZ` default America/New_York) when the profile is missing or stale.
- `scripts/maintenance-dispatch.sh` (timer `*:00,20,40` ±15 min) starts the first not-yet-run unit from `scripts/lib/maintenance-queue.txt` inside a window, one at a time; state in `~/.cache/maintenance-queue.json` via `scripts/lib/maintenance_queue.py`; operational day rolls at 09:00Z / 05:00 ET (EDT); ntfy if jobs remain unrun.
- `scripts/lib/maintenance-queue.txt` (9 units, priority order): executive-protection-daily-watch, aam-daily-watch, aviation-daily-watch, concierge-travel-daily-watch, gig-economy-daily-watch, trains-yachts-daily-watch, disruption-weather-digest, transport-pattern-digest, ep-advance-venues. Their old timers are in `.config/systemd/user/retired-rolling-queue-20261004/`.
- Stack refresh: `corporatetraveldc-weekly-external-image-update.timer` (Sun 08:15Z / 04:15 ET during EDT) runs `scripts/stack-refresh.sh --weekly`; `corporatetraveldc-stack-refresh-tripwire.timer` (hourly ±50 min) runs `--tripwire`, which acts only at the instant drawn by `scripts/lib/tripwire_draw.py` (36 h–7 d after the last run, snapped to a quiet window).

---

## 8. Scripts (`scripts/`, 132 top-level `*.sh`/`*.py`)

**[root]** = listed in `install-root-copies.sh` `SCRIPTS=(…)` and executed by root from
`/usr/local/libexec/ctdc/` (hash-checked against `MANIFEST.sha256`; also installs
`src/common/board_sign.py` to `/usr/local/libexec/ctdc/lib/` and the fail2ban
action/jail/filter files to `/etc/fail2ban`). **[timer]** = run by a tracked user timer.

**Signing and integrity**
- `sign-manifest.sh` — generate + GPG-sign `MANIFEST.sha256(.asc)` over the tree (operator).
- `verify-manifest.sh` — verify signature and every hash (or scoped targets).
- `verified-exec.sh` — scoped manifest check, then exec (prefix of every skill quadlet `Exec=`).
- `scheduled-integrity-sweep.sh` [timer] — collective manifest check every 15 min.
- `installed-check.sh` [root] — hash check of root-installed copies against `.ctdc-installed`.
- `install-root-copies.sh` — verify manifest, install root-run scripts/units/fail2ban files.
- `sign-db-manifest.sh`, `snapshot-db-manifest.py` — signed content-hash manifest of the database.
- `skill-snapshot.sh` — signed offline-verifiable snapshot of a skill; `skills-sync.sh` — twin gate for agent skills.
- `check-claude-md-drift.sh`, `claude-md-drift-daily.sh` [timer], `post-commit-doc-verify.sh`, `weekly-doc-drift-check.sh` [timer] — doc/live-state drift checks.
- `check-env-quoting.sh`, `check-timer-requires.sh` — config guards.

**Build, deploy, rollout**
- `../build-images.sh`, `../build-models.sh`, `../setup.sh`, `session-restore.sh` — builds and bootstrap.
- `stack-refresh.sh` [timer ×2] — weekly refresh + adversarial tripwire audit; `serialized-rollout.sh` — one-container-at-a-time rebuild/restart.
- `weekly-external-image-update.sh` — older external-image updater (the weekly timer now calls `stack-refresh.sh --weekly`).
- `safe-pg-image-update.sh` — Postgres image update procedure.
- `stack-boot-ctl.sh`, `ingest-feed-ctl.sh` — serialized, load-gated startup (units `corporatetraveldc-stack-boot-stagger`, `corporatetraveldc-boot-stagger`).
- `restart-stack.sh` — manual full restart (root).
- `pg-migrate.sh`, `pg_migrate.py` — apply migrations; `migrate-sqlite-to-pg.py`, `migrate-second-brain-to-pg.py`, `migrate-demo-to-pg.py` — one-time migration tools.
- `push-public.sh`, `push-and-sync.sh`, `scrub-public-tree.py` (+ `.example` templates), `lib/public-manifest.sh` — public mirror; `site-public.sh`, `site_public.py` — website public mirrors.
- `deploy-acars-stack.sh`, `rtl-eeprom-reserialize.sh`, `setup-disk-swap.sh`, `setup-nts-server.sh`, `fix-executive-standard-selinux.sh` — host setup.
- `dedupe-authorship.sh` — one-off history rewrite tool.

**Agents, governance, team**
- `team-liveness.sh` [root] — dead-man switch + kill-order executor; `kill-order.sh` — issue a signed kill order.
- `agent-segmentation/plan.sh`, `verify.sh`, `rollback.sh`, `secrets-subset.py` (+ `secrets-allowlist-{agent,ops}.txt`), `render-onboarding.sh`, `publish-pamphlet-to-vault.sh`, `onboarding-template.md` — account creation/segmentation.
- `skill-grants.sh` [root] + `lib/skill_grants.py` [root] — per-agent/per-task skill grants.
- `board-sign.sh`, `board-signer-ctl.sh`, `board-token.py`, `board-mint-nonce.py`, `board-presence-attest.sh`, `board-presence-ingest.py` — coordination board identity/tokens.
- `approve.sh`, `approver-ctl.sh` — human SSH-signed approvals; `sudo-approval-gate.sh`, `grant-agent-session.sh` — approval-gated sudo.
- `council.sh`, `workspace-contribute.sh`, `workspace-grants.sh`, `llama-council-responder.py` — council/arena and shared workspace.
- `agent-gateway.sh` — gateway CLI (link/revoke/hold/kill-all/thaw); `agent-run.sh` — run an interactive agent CLI in its account; `es-invite.sh` — Executive Standard invites/promos.
- `cowork-coord-24h-check.sh`, `cowork-coord-7d-check.sh` [timer] — board token/presence backups.
- `lib/gov-exec.sh`, `lib/with_dispatch_env.py`, `with-dispatch-env.sh` — helpers.

**Credentials and network**
- `rotate-credential.sh` — rotate BOARD_KEY / DISPATCH_ADMIN_TOKEN / NTFY_TOKEN / Nextcloud app password; `mint-agent-api-token.sh` — scoped per-account API token; `populate-secrets.sh`; `check-pat-expiry.sh`.
- `service-env/generate.py`, `scan.py`, `*.allowlist` (9) — per-service scoped env files.
- `cf-dns-record.sh`, `cf-service-token-{mint,reconcile,breakglass}.sh` — Cloudflare via API (no dashboard).
- `cf-honeypot-ban.sh` [root], `cf-honeypot-notes.sh` [root], `lockdown.sh` [root], `restore-network.sh` [root], `threat-initiate.sh`, `threat-resolve.sh` — fail2ban/lockdown actions.
- `renew-tailscale-cert.sh` [root], `tailscale-cert-refresh-nts.sh` [root], `net-failover-watchdog.sh` [timer], `scheduled-dns-restart.sh` [timer].

**Health, watchdogs, diagnostics**
- `watchdog.sh` [root], `watchdog-tune.sh` [root], `stall-monitor.py` [root].
- [timer]: `container-mem-watch.sh`, `ntfy-topic-count-watchdog.sh`, `nextcloud-health-check.sh`, `runner-health-watchdog.sh`, `swim-session-health-watch.sh`, `sdr-crashloop-guard.sh`, `thermal-sample.sh`, `thermal-ingest-guard.py`, `governor-watch.py`, `uber-traffic-watch.py`, `brief-fallback-monitor.sh`, `failover-kickover-guardrail.py`, `scheduled-ingest-restart.sh`, `scheduled-llama-restart.sh`, `scheduled-podman-prune.sh`, `compliance-egress-push.sh`, `maintenance-dispatch.sh`, `quiet-window-report.py`, `snapshot-data-usage.py`.
- Timer disabled: `acars-feed-silence-watchdog.sh`, `adsb-feed-silence-watchdog.sh`, `adsb-link-watchdog.sh`.
- `maintenance-window-guard.sh`, `maintenance-window-on.sh`, `maintenance-window-off.sh`, `gui-window.sh`, `lib/tripwire_draw.py`, `lib/maintenance_queue.py`, `lib/serial-bringup.sh`.
- `unit-failure-notify.sh` (`corporatetraveldc-unit-failure-notify@.service`), `smoke-test-platform.sh`, `pg-catchup-monitor.py`, `brief-model-matrix.sh`, `local-llm-synthesize.sh`, `redact-screenshot.py`.

**Data, vault, demo**
- `seed-dc-codeshare.py` [timer `corporatetraveldc-codeshare-seed`, 08:40Z / 04:40 ET during EDT], `import-ladd-filter.py`, `backfill_fdps_route_versions.py`, `export_registries.py`, `hub_arrivals_lookup.py`.
- `second-brain-search.sh` (header still says "FTS5 index"; the index is Postgres since 0055), `second-brain-weekly-dump.sh` [timer], `rss-watch-trigger.sh`, `podcast-feed-resolve.py`.
- `scrub-demo-source.py` (timer `demo-source-refresh`, disabled), `demo-portal.sh`, `demo-portal-init.sh`, `new-client-demo.sh`, `client-demo-webdev-expire.sh`, `templates/*.tmpl`.
- `retired-20260830/ollama-keepwarm.sh` — retired.

---

## 9. Security surfaces

### 9.1 Listening sockets (`ss -ltnH`, ports < 32768 or > 60999, 18:10Z)

| Bind | Port | Owner (from quadlet `PublishPort` / config; process owner not read) |
|---|---|---|
| 0.0.0.0 / [::] | 22 | sshd |
| 0.0.0.0 / [::] | 80, 443 | nginx (all vhosts; 443 = tailnet vhost) |
| 0.0.0.0 / [::] | 53 (tcp+udp) | Pi-hole FTL |
| 0.0.0.0 / [::] | 4460 | chrony NTS-KE (`scripts/setup-nts-server.sh`) |
| 0.0.0.0 / [::] | 8091 | Pi-hole web (proxied by `pihole.example.com.conf`) |
| * | 2586 | `ntfy` |
| * | 3000 | `openwebui` |
| * | 8754 | `fr24feed` |
| * | 9080, 15555 | `acarsrouter` |
| * | 30005 | `ultrafeeder` (Beast out) |
| * (udp) | 5005 | `acars-watcher` |
| 100.x.x.x | 8000, 8001, 8004, 8005 | web, runner, demo-api, runner-demo |
| 100.x.x.x | 8080, 8081 | ultrafeeder (tar1090) |
| 100.x.x.x | 8085, 8086 | ccw-demo, ccw-preview1 |
| 100.x.x.x | 8092 | acarshub |
| 100.x.x.x | 8093 | llama.cpp server |
| 100.x.x.x | 1025, 3001 | protonbridge (SMTP), rss-bridge |
| 127.0.0.1 | 5432, 8000, 8001, 8002, 8004, 8005, 8085, 8086, 8090, 8787, 30003, 30053, 5335 (+[::1]), 631 (+[::1]), 20241 | pgsql, web, runner, csexec-contact, demo-api, runner-demo, ccw-demo, ccw-preview1, nextcloud, ES verifier, ultrafeeder SBS, planefinder, unbound, cups, cloudflared metrics `[UNVERIFIED: owner of 20241]` |

Host firewall (firewalld zones) not inspected `[UNVERIFIED]`; the `*`/`0.0.0.0` binds are
LAN-reachable unless the firewall blocks them.

### 9.2 Public hostnames

Cloudflare tunnel ingress (`cloudflared/config.yml`, identical to live
`~/.cloudflared/config.yml`), all → `http://127.0.0.1:80` (nginx) unless noted:
`dispatch.`, `dispatch-runner.`, `openwebui.`, `ollama.`, `cloud.`, `dav.`,
`mcp.` (→ `http_status:404`, sinkholed), `ntfy.`, `agents.`, `pihole.`, `wrangler.`,
apex, `www.` (all `.example.com`), `members.executivestandard.example.com`,
`invite.executivestandard.example.com`; catch-all 404.

Live nginx `server_name`s (`/etc/nginx/conf.d/*.conf`): `_` (default catch-all),
`acars.`, `adsb.`, `agents.`, `cloud.`, `dav.`, `dispatch.`, `dispatch-runner.`,
`invite.executivestandard.`, `members.executivestandard.`, `mcp.`, `ntfy.`,
`openwebui.`, `pihole.`, `www.`/apex/`wrangler.`, and the tailnet name
`corporatetraveldc-dispatch.tailxxxxxxx.ts.net`. This repo tracks 9 of them in
`nginx/conf.d/`; `members.`, `invite.`, `www.`, `dispatch-runner.`, `acars.`, `adsb.` and
`000-default-catchall.conf` are tracked elsewhere (site repos) or only live. No live vhost
matches `ollama.` (its tunnel route falls to the default catch-all).

Executive Standard paths: invite host `/`, `/p`, `/i/<16-64 url-safe chars>` →
`127.0.0.1:8787/_invite…`, everything else 404; members host gated by server-scope
`auth_request /_esauth` → `:8787/auth`, `/redeem` → `:8787/redeem`, with ungated
`/.well-known/`, `/subscribe`, feed probes, `/manifest.json`, `/icons/`, `/sw.js`.

### 9.3 Auth model in brief (depth: `docs/COMPLIANCE_SECURITY.md`, `docs/BOARD_SIGNING.md`, `docs/AGENT_SEGMENTATION.md`, `docs/OPERATOR_CONSOLE.md`)

- API bearer tiers + per-token `allowed_actions`, audit on every admin action: `src/auth/auth.py`, `src/ctdc_token/cli.py`, `scripts/mint-agent-api-token.sh`.
- Public-origin clamp `X-CTDI-Public: 1`: `nginx/conf.d/dispatch.example.com.conf`, `auth.py:69`.
- Board: `X-Board-Key` or SSH signature (`src/common/board_sign.py`, `board_signers`, replay cache), policy table `src/web/main.py:271`.
- Human approvals: SSH signature with a separate approval key (`src/common/governance.py`, `approval_signers`, `scripts/approve.sh`); tap/link can only deny.
- Agent gateway: OAuth 2.1 + PKCE, consent = signed approval, tokens bound to account liveness (`src/common/agent_gateway.py`, `scripts/agent-gateway.sh`).
- Console: tailnet Host only, SSH-signed login approval, 8 h `__Host-` session, CSRF (`src/web/routes/console.py`).
- ES readers: hashed invites/promos/sessions, 3-device cap (`src/common/es_invites.py`, verifier in the executivestandard-website repo).
- Team accounts: liveness AND-gate, signed kill orders with quorum (`scripts/team-liveness.sh`, `scripts/kill-order.sh`, migrations 0066/0067); per-service scoped env (`scripts/service-env/`).
- Integrity: signed manifest + `verified-exec.sh` in images + root-installed copies (§8).
- Network: Tailscale for operator access, Cloudflare tunnel + Access for public hostnames, fail2ban (`fail2ban/`) honeypot + rate-limit jails, lockdown scripts.

---

## 10. TODO / FIXME / XXX / KNOWN GAP markers

`grep -rnE '\b(TODO|FIXME|XXX|KNOWN GAP)\b' src scripts --include='*.py' --include='*.sh' --include='*.js' --include='*.jsx' --include='*.sql'` (excluding `XXXX` placeholders):

- `src/ingest/parsers/tfms_parser.py:902` — `# KNOWN GAP, deliberately not papered over: the return value is now …`
- `src/ingest/parsers/tfms_parser.py:953` — `"watchlist hit below still fires; see the KNOWN GAP note above."`
- `src/poller/fetchers/eurocontrol.py:69` — `records: list[dict] = []  # TODO: parse resp.content once schema is confirmed`
- `src/poller/skills/flight_impact.py:16` — `TODO(sfdps-live): once parse_sfdps is wired and a real SFDPS sample confirmed, …`
- `src/web/main.py:83` — historical mention (`the "tighten at nginx" TODO`), not an open item.
- `src/shared/sector_coalesce.py:111` — `"OCEANIC_ATLANTIC": set(),  # TODO: populate once a real oceanic-tagged sample is confirmed`
- `src/shared/sector_coalesce.py:112` — `"GULF": set(),  # TODO: populate once a real Gulf/ZHU-tagged sample is confirmed`

No matches in `scripts/`.

---

## Appendix A. Runner (:8001) and demo API (:8004) routes

`grep -hoE '^@app\.(get|post|put|delete|patch)\("[^"]+"' src/runner/main.py src/demo/demo_api.py`

Runner (29 + SPA catch-all): `GET /api/whoami`, `POST /api/demo/login`, `GET /api/demo/status`,
`GET /healthz`, `GET /api/adsb/local`, `GET /api/adsb/live`, `GET /api/vdl2/messages`,
`GET /api/acars/messages`, `GET /api/hfdl/messages`, `GET /api/ais/vessels`,
`GET /api/utm/drones`, `POST /api/ask`, `GET|DELETE /api/chat/history`,
`GET /api/v1/frontend-config`, `GET|PUT /api/v1/config`, `GET /api/stream`,
`GET /api/demo/webhook-log`, `GET /api/ntfy/stream`, `GET /api/rss`,
`GET|POST /api/rss/categories`, `GET /api/rss/custom`, `POST /api/rss/resolve-source`,
`GET|POST /api/rss/user-feeds`, `DELETE /api/rss/user-feeds/{feed_id}`, plus the
`/api/dispatch/…` proxy to :8000 and `GET /{full_path:path}` (SPA). Gate: `tailscale_gate`
middleware (`runner/main.py:381`, retired hostnames rejected) and `_is_trusted(request)`
on mutating routes (404 otherwise); RSS writes additionally need a bearer identity; the
runner injects its own cert-tier token for proxied T1 paths. Per-route auth was not
tabulated `[UNVERIFIED: runner per-route review not done in this pass]`.

Demo API (12): `GET /healthz`, `GET /api/v1/demo/readiness`, `GET /api/v1/brief/weekly`,
`GET /api/v1/brief/history`, `GET /api/v1/brief/{brief_ref}`, `GET /api/v1/wx-config`,
`GET /api/v1/airmets`, `GET /api/v1/feeds`, `POST /api/v1/demo/login`,
`POST|GET /admin/demo/profiles`, `DELETE /admin/demo/profiles/{profile_id}`.
