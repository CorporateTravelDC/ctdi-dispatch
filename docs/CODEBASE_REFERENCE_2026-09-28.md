# CTDI Dispatch — Codebase Reference (2026-09-28)

**Status:** CURRENT, code-validated. This document replaces the stale
`docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md` (which described the SQLite-era
codebase). Every quantitative and structural claim below was re-verified this
session against live code, `git`, running containers, and the Postgres database.
File paths are relative to the repo root
(`/opt/corporatetraveldc/private/ctdi-dispatch-internal`) unless noted. Where a
claim could not be fully verified, it is marked **[UNVERIFIED]**; where the code
contradicts a prior belief, it is marked **[CORRECTION]**.

Validation basis: repo `HEAD` = `78660147fa6dc68103ab7194197406084c352c09`
(2026-09-27 22:46 -0400, "data(fbo): DC-metro apron/terminal/gate footprints
from OpenStreetMap"), branch `fix/oooi-premature-landing-tfms-fids`. Working
tree has uncommitted edits (notably `src/second_brain/semantic/compile.py`) —
this matters for the integrity system (§9).

---

## 0. Scale (measured live 2026-09-28)

| Metric | Value | Source of measurement |
|---|---|---|
| Python files under `src/` | **172** | `find src -name '*.py' | wc -l` |
| Python LOC under `src/` | **67,748** | `find src -name '*.py' -exec cat {} + | wc -l` |
| Postgres migrations | **65** (`0001_bootstrap.sql` … `0065_oooi_authority_lock.sql`) | `ls src/common/pg_schema/*.sql | wc -l` |
| Live container quadlets (`~/.config/containers/systemd/*.container`) | **73** | `ls | wc -l` |
| Host-level systemd user timers | **74** | `ls ~/.config/systemd/user/*.timer | wc -l` |
| Running containers | **34** | `podman ps -q | wc -l` |
| Operational scripts (`scripts/`) | **109** | `ls scripts/ | wc -l` |
| Commits in last 6 weeks (since 2026-08-17) | **173** | `git log --since=... --oneline | wc -l` |

The draft's "~61,100 lines / 163 files" figure is now stale; the numbers above
supersede it.

---

## 1. System overview

CTDI ("Corporate Travel Dispatch Intelligence") is a self-hosted, single-box
aviation/ground-transport dispatch-intelligence platform on a Raspberry Pi 5 for
[operator LLC] / CorporateTravelDC. Everything runs locally: rootless
Podman containers under systemd user quadlets, one shared **Postgres** database,
a host-level llama.cpp inference stack, a Nextcloud-backed knowledge vault, and
ntfy/Pushover alerting. Standing operator directive: everything local — no
third-party position/identity APIs, no cloud LLM calls
(`ANTHROPIC_FALLBACK_ENABLED=false`), no secrets in the repo.

What it does, unchanged in shape from the draft: FAA SWIM ingest (six Solace
feeds) + NWWS-OI + local ADS-B/ACARS + Amtrak; REST polling with push/pull
failover; deterministic CPS scoring; LLM-synthesized briefs/watches (~45 skills);
EP/OSINT intel; a second brain with a semantic layer and knowledge graph; a
public demo surface; and operator interfaces (FastAPI web API + React PWA
runner + ntfy).

**The single biggest change since the draft: the datastore.** The platform is
now on Postgres end to end.

---

## 2. Database backend — POSTGRES (the headline change)

- `DISPATCH_DB_BACKEND=postgres` is live in `/etc/corporatetraveldc/dispatch.env`
  and `config/dispatch.env`.
- `common/db_backend.py:pg_conn()` opens a `psycopg` connection over the
  **unix socket** `host=/var/run/postgresql` (`db_backend.py:415`), against the
  `corporatetraveldc-pgsql` container. Containers reach it via the shared socket,
  not TCP.
- **The old ~23 GB SQLite file is gone.** `/var/lib/corporatetraveldc/corporatetraveldc.db`
  no longer exists (`ls` → "No such file or directory"); its space was reclaimed.
- **SQLite is rollback-only.** `DISPATCH_DB_BACKEND=sqlite` survives as a
  dev/rollback path; the SQLite `SCHEMA`/`SCHEMA_Vn` blocks in `common/db.py`
  are retained as the structural record, but the live engine is Postgres.
- Migrations are now numbered SQL files in `src/common/pg_schema/` (0001–0065),
  not in-code `SCHEMA_Vn` string blocks. The completion of the SQLite→Postgres
  removal landed in commit
  `de036ad7bc20a222b2367ef4393e23ca424692e3` ("Complete SQLite-to-Postgres
  removal…").

### 2.1 Second-brain index + semantic layer also on Postgres (schema 0055)

The second-brain vault index and semantic layer — historically a *separate*
SQLite store (`/var/lib/corporatetraveldc/second_brain_index.db`) — moved to the
same Postgres instance on 2026-09-19 via
`src/common/pg_schema/0055_second_brain_index.sql`. The migration header
(`0055_...sql:1-14`) documents the reason: geometric reasoning's Phase 0
backfill is a sustained writer against `semantic_note_derivations` while the
second-brain skills read/write the same tables concurrently — SQLite's
one-writer WAL model would stall them, so it was reversed onto Postgres.

### 2.2 [CORRECTION] The SQLite index foot-gun is mostly neutralized in code, but the physical file remains

The prior concern that "code still defaults to the SQLite index at
`compile.py:93` / `index_db.py:78`" is **now only partially true** — the code was
routed to Postgres, but the vestigial constant lines remain:

- `src/second_brain/semantic/compile.py:91-94` still defines
  `INDEX_DB = os.environ.get("SECOND_BRAIN_INDEX_DB", "/var/lib/corporatetraveldc/second_brain_index.db")`,
  **but the adjacent comment (line 94) explicitly says it is "vestigial (no
  longer read by `_connect()`)…kept for anything still importing it for
  display/logging."** `_connect()` wraps `common.db_backend.pg_conn()` directly
  (`compile.py:85-90`).
- `src/second_brain/index_db.py:78` likewise still defines `INDEX_DB` with the
  same default, but the comment above it (`index_db.py:74-77`) says "nothing
  here still opens this path directly (`get_conn()` below is Postgres now)"; the
  historical `sqlite3.connect(INDEX_DB)` call sites were converted to
  `get_conn()`.
- `compile.py:305-311` retains a `sqlite3.connect(db_path)` path, but **only**
  when an explicit `db_path` is passed (the test harness injects a throwaway
  SQLite connection — `compile.py:155`). No production path reaches it.

**Residual hazard (still real):**
1. The **physical file** `/var/lib/corporatetraveldc/second_brain_index.db`
   still exists — 104 MB (104,054,784 bytes), **last written 2026-09-18
   23:15:48 -0400** (matches the "frozen 2026-09-18" expectation). It is a
   retired, frozen store that nothing in production now writes, but it is a
   foot-gun for any operator/agent who assumes it is live.
2. The vestigial `INDEX_DB` constants and `SECOND_BRAIN_INDEX_DB` env default
   still name that path. **Recommended action:** delete/relocate the stale file
   and remove the vestigial constants (or repoint the env default to a
   sentinel), so nothing can accidentally re-open it.

---

## 3. Container / service map

34 containers running. Names below are as `podman ps` reports them
(systemd-managed containers carry the `systemd-` prefix).

### 3.1 This repo's services (always-on)

| Container | Role |
|---|---|
| `systemd-corporatetraveldc-web` | FastAPI API, port 8000 (127.0.0.1 + tailnet 100.x.x.x). §7. |
| `systemd-corporatetraveldc-poller` | Async scheduler: REST fetchers + in-process skills + trigger reactor + watchlist sweeps. §5. |
| `systemd-corporatetraveldc-ingest-core` | NWWS-OI + Amtrak + local airspace monitor (zero SWIM feeds). |
| `systemd-corporatetraveldc-ingest-fdps` / `-stdds` / `-tfms` / `-notam` / `-tbfm` / `-itws` | Six single-SWIM-feed containers sharing the ingest image. |
| `systemd-corporatetraveldc-pusher` | 30 s alert loop (VIP TFRs, CPS changes, wx deltas, watchlist landings). |
| `systemd-corporatetraveldc-runner` | Internal React PWA backend, port 8001. §7.3. |
| `systemd-corporatetraveldc-runner-demo` | Demo-mode PWA instance, port 8005. |
| `systemd-corporatetraveldc-demo` | Archive recorder (polls live API into `demo_snapshots`). |
| `systemd-corporatetraveldc-demo-api` | Read-only public playback API, port 8004, over the sovereign scrubbed DB. |
| `systemd-amtrak-tracker` | Push-primary Amtrak status for WAS. **Train parity is LIVE.** |
| `corporatetraveldc-acars-watcher` | Triple-source ACARS/VDL2 watcher. |
| `corporatetraveldc-pgsql` | **Postgres** — the shared datastore. |
| `corporatetraveldc-protonbridge` (`systemd-…-protonbridge`) | ProtonMail Bridge mail relay. |

### 3.2 Supporting third-party containers (not this repo's code)

SDR/feed stack: `corporatetraveldc-ultrafeeder`, `-piaware`, `-fr24feed`,
`-planefinder`, `-airnavradar`, `-acarshub`, `-acarsrouter`, `-dumpvdl2`. Plus
`ntfy`, `nextcloud-app` + `nextcloud-db`, `openwebui`, `rss-bridge`,
`csexec-contact`, and the Cowork demo/preview containers
(`corporatetraveldc-ccw-demo`, `-ccw-preview1`). The llama.cpp servers are
**host-level systemd services**, not containers.

### 3.3 [CORRECTION] Vessel/AIS + UTM/drone parity: CODE PRESENT, NOT DEPLOYED

- Source exists: `src/ais_watcher/ais_watcher.py` (+ `Containerfile`) and
  `src/utm_watcher/utm_watcher.py` (+ `Containerfile`).
- **All related quadlets are `.disabled` and none are running.** They live in the
  repo's reference dir, not the live quadlet dir:
  - `systemd/corporatetraveldc-ais-watcher.container.disabled`
  - `systemd/corporatetraveldc-ais.container.disabled`
  - `systemd/corporatetraveldc-utm-watcher.container.disabled`
  - `systemd/quadlets/corporatetraveldc-ais-catcher.container.disabled`
- No AIS/UTM container appears in `podman ps`. Reason: no receiver hardware yet;
  the utm_watcher UDP parser has never seen a real OpenDroneID capture and its
  USS REST poller is a stub. **State explicitly: VESSEL/AIS parity is code-complete and HARDWARE/CONFIG-gated (deployment-ready -- an AIS dongle or an AISHub AIS_AISHUB_ID away). DRONE/UTM parity is the only CAPABILITY-gated piece: the USS REST poller is a stub, needing code work.**

---

## 4. Data ingest layer (`src/ingest/`)

Unchanged in architecture from the draft (asyncio supervisor `ingest/main.py`;
one `_NmsFeedSession` per SWIM feed in `swim_client.py`; push/pull failover in
`ingest/failover.py`; parsers in `src/ingest/parsers/`). Key points that remain
accurate: TLS is genuinely validated against the container trust store;
client-side ack on all six feeds; backlog fast-forward triage; bandwidth-priority
backpressure; `push:<feed>` heartbeats every 30 s. Parsers: `fdps_parser.py`
(FIXM 3.0), `tfms_parser.py` (largest), `smes_parser.py` (STDDS/ASDE-X/TDES),
`tbfm_parser.py`, `itws_parser.py`, `aim_parser.py` (AIXM 5.1 NOTAMs). The
`geo_filter.py` relevance filter (250 NM of DCA / core airports / core WFOs) is
shared across parsers.

See the 2026-09-03 draft §3 for the parser-by-parser detail; the parser
structure has not materially changed. The material ingest change since is the
**OOOI source-authority lock** — see §8.

---

## 5. Poller and skills layer (`src/poller/`)

`poller/main.py` still runs `FetchLoop` (REST fetchers with push-failover
deference), `SkillLoop` (skills as subprocesses), `TriggerReactor` (admin
trigger files), and `WatchlistSweep`. ~45 skills in `src/poller/skills/`; ~17
fetchers in `src/poller/fetchers/`. The skill contract (Gather → Synthesize via
`common.llm.generate` → Write to vault/ntfy → Account via SR-1/SR-2) is
unchanged.

**Model change:** the local LLM was swapped from phi3:mini-class to **Qwen3-4B**
after a live A/B/C evaluation (commit
`fe43973a2317bbd5a53161acae1d14a2ba8a6bd0`, "Swap local model phi3-mini →
Qwen3-4B…"). The persona registry (`common/personas.py`) and the
Ollama-vocabulary-without-Ollama funnel (`common/llm.py`, llama.cpp behind
`OLLAMA_*` names) are otherwise as the draft described. **[UNVERIFIED]** the
exact live GGUF path/quant — `config/dispatch.env` still shows the
`corporatetraveldc-pi5-*` persona model names, not a Qwen file path directly.

**maintenance_window enforcement (new).** `src/common/maintenance_window.py` is
an in-process mirror of `scripts/maintenance-window-guard.sh`: the overnight
window (default 23:00–05:00 America/New_York, from three
`CTDC_MAINTENANCE_WINDOW_*` vars) confines long report-tier work to quiet hours
so it never contends with the latency-sensitive alert path. systemd units gate
via `ExecCondition=scripts/maintenance-window-guard.sh`; poller-internal skills
use the Python mirror. Both handle midnight-wrap and `start == end` (→ always
open) identically. The **shell** guard + migrations `0061`/`0062` landed in
`97f4e805b9ae1b15a2e6bf5d90de59e9e1746037`; the **Python** mirror
`src/common/maintenance_window.py` itself was added separately in
`2af693eabad259de20f46ad684fe61b6046ff44e` (verified via `git log
--diff-filter=A` — it is *not* part of `97f4e80` despite that commit's title;
see `docs/INTEGRITY_AND_MAINTENANCE_2026-09-28.md` §1).

---

## 6. Database schema domains (Postgres, `src/common/pg_schema/`)

65 numbered migrations. Domains (representative, not exhaustive):

- **Feed health:** `feed_state`, `feed_data_usage`, `pull_path_status`,
  `bandwidth_priority_state`.
- **Aviation state:** `tfrs`, `metar_snapshot`, `nas_programs`, `notams`,
  `nws_alerts`/`forecast`, `atcscc_opsplan`, `ops_plan`, `runsheet`.
- **SWIM-derived:** `flight_events`, `surface_tracks`, `terminal_tracks`,
  `tbfm_sequences`, `itws_alerts`, `stdds_safety_status`,
  `surface_movement_events`, `stdds_rvr`, `tdes_departure_events`,
  `tdls_messages`, `datis_snapshots`, `tfms_edct_slots`, `tfms_reroutes`,
  `tfms_plan_removals`, `fdps_route_versions`, `fdps_diversion_continuations`.
- **Watchlist / multimodal:** `watchlist_entries`, `watchlist_history`,
  `watchlist_sessions`, `train_events`, `vessel_events`, `codeshare_map`.
- **Registries:** `faa_aircraft_registry`, `faa_aircraft_reference`,
  `faa_ladd_aircraft`, `opensky_aircraft_registry`.
- **Decision output:** `cps_scores`, `hot_alerts`, `brief_archive`,
  `swim_alerts`, `local_aircraft`, `acars_messages`.
- **Governance:** `audit_log`, `auth_tokens`, `trigger_log`, `approval_requests`,
  `session_grants`, plus the coordination board (`board_messages`,
  `board_tokens`, `board_presence`, …).
- **Second brain / semantic (0055):** `vault_documents`, `vault_notes_fulltext`
  (Postgres `tsvector`/GIN replacing SQLite FTS5), `vault_links`, and the
  `semantic_*` tables (`semantic_note_derivations`, `semantic_meta`,
  `station_coordinates`, …).

### 6.1 Notable recent migrations

| Migration | What it adds | Commit |
|---|---|---|
| `0054_reference_opensky_codeshare.sql` | `codeshare_map` (marketing↔operating carrier/flight-number mapping) + indexes | — |
| `0055_second_brain_index.sql` | Second-brain index + semantic layer onto Postgres | 2026-09-19 |
| `0058_station_coordinates.sql` | Phase 0 station-coordinate reference table | `de036ad` |
| `0061_site_origin.sql` | Site-of-origin provenance column on semantic graph tables (added *before* Phase 2/3 so derived-edge origin is knowable, not backfilled) | `97f4e80` |
| `0062_audit_tamper_evidence.sql` | Tamper-evident audit log: hash chain + signed checkpoints + signed archive stubs (closes the "append-only by convention only" gap) | `97f4e80` |
| `0064_train_phase_eta.sql` | Train phase/ETA state (train-parity buildout) | `37b68d5` |
| `0065_oooi_authority_lock.sql` | OOOI source-authority lock (SWIM = validator of record) | `37ce72c` / `1e61a89` |

---

## 7. Web/API layer (`src/web/`) and the runner (`src/runner/`)

Unchanged in shape from the draft. `web/main.py` on uvicorn:8000
(127.0.0.1 + tailnet), OpenAPI schema disabled, explicit CORS allowlist. Tiered
auth (T0 anonymous / T1 cert / T2 shares / admin) in `src/auth/auth.py`; the
`X-CTDI-Public: 1` header forces T0 on the Cloudflare-tunnel vhost so a valid
admin token can never elevate from the public tunnel. `require_admin(action)`
both authorizes and writes an `audit_log` row. `ctdc-token` CLI mints/lists/
revokes tokens.

Inbound webhooks (`web/routes/webhooks.py`): LimoAnywhere reservations,
RingCentral, 3CX — shared-secret header auth, 503 until the secret is set. Since
the draft, LimoAnywhere reservations now auto-track flights/trains onto the
watchlist (commit `6dc9d64`).

Runner (`src/runner/main.py`, port 8001): server-side proxy/aggregator over the
web API + local receivers; React 18/Vite frontend under
`src/runner/frontend/`. Chat history (`chat_messages`) is on the shared Postgres
DB. Demo instance on 8005.

---

## 8. OOOI source-authority lock (schema 0065) — SWIM is validator-of-record

This is the fix that closed the premature-landing sweeps. Documented verbatim in
`src/common/pg_schema/0065_oooi_authority_lock.sql:1-30` and the paired code
change in commit `1e61a89c46...` ("stop TFMS asserting future milestones; FIDS
is enrichment-only"):

- Trigger incidents (2026-09-23, both same evening): **UA1240** swept as
  "landed (oooi_phase=in, ACARS/ADS-B confirmed)" while verifiably at FL370 over
  southern Ohio at 504 kts (ADS-B age 0.0s), with SWIM reporting ON/IN times
  still in the *future*; **UA2408** swept 3.5 hours before scheduled arrival.
- Root cause: nothing recorded *which source was entitled* to assert the phase.
  `watchlist.py`'s sweep fired on `phase == "in"` alone, with a **hardcoded**
  "ACARS/ADS-B confirmed" reason string regardless of the actual writer; four
  writers wrote `oooi_phase`, last-write-wins.
- The rule encoded: **SWIM (FDPS + SWIM-borne FIDS) is the ultimate validator.**
  Local receivers may never authoritatively claim off/on/in unless (a) SWIM is
  *measurably* down (from `feed_state`, not assumed) **and** (b) that same
  receiver already held a prior active track on the entry. **FIDS and TFMS are
  enrichment-only and may not assert future milestones.**

---

## 9. Integrity / verified-execution system — AND ITS CURRENT FAILURE

### 9.1 How it works

- `scripts/sign-manifest.sh` (human-run GPG; `--agent` mode uses a
  no-passphrase agent key gated on a session-grant or a live ntfy Allow/Deny
  approval, every agent signature audited) produces `MANIFEST.sha256` + `.asc`.
- `scripts/verify-manifest.sh` checks the tree (or scoped targets) against the
  signed manifest.
- Enforcement points: `scripts/verified-exec.sh` is prepended to **every skill
  container's `Exec=`**; `common/llm.py` verifies the calling skill file +
  Modelfile before any inference; `push-public.sh` refuses on verification
  failure.

**Contract for contributors: ANY change to a covered `src/` file MUST be
followed by `scripts/sign-manifest.sh` + a redeploy, or verified-exec-gated jobs
fail by design.**

### 9.2 [RESOLVED 2026-09-28] Manifest re-signed, units recovered

> The hazard below was live on 2026-09-27. As of 2026-09-28 the manifest was re-signed, the poller/ingest images rebuilt, and the ~23 verified-exec casualties re-ran green. A final re-sign + commit of the pending doc edits closes it fully. Historical detail retained:

### 9.2-hist [was CURRENT HAZARD] The manifest is stale — ~23 units are failing right now

Verified this session:

- `MANIFEST.sha256` was last signed **2026-09-25 07:26 -0400**, but `HEAD` is
  2026-09-27 and the working tree has uncommitted edits.
- `bash scripts/verify-manifest.sh` **exits 1** with "INTEGRITY FAILURE — one or
  more files do not match the signed manifest": **13 computed checksums did NOT
  match**, including `src/poller/main.py`, `src/second_brain/semantic/compile.py`,
  and `tests/ingest/test_aim_parser_notam_scope.py`.
- `systemctl --user list-units --state=failed` shows **23 failed units** — the
  skill oneshots whose `verified-exec.sh` entrypoint check fails: `ops-brief`,
  `ep-advance`, all six daily watches (aam / aviation / concierge-travel /
  gig-economy / executive-protection), `board-sweep`,
  `convective-sigmet-archiver`, `entity-tracking-digest`, `faa-cifp-pull` +
  `faa-cifp-parse`, `feed-db-integrity-check`, `ingest-feed-watch`,
  `integrity-sweep`, `knowledge-graph-compile`, `personal-notes-import`,
  `pull-path-verify`, `research-board-mirror`, `second-brain-daily`,
  `second-brain-rss`, `semantic-compile-daily`, `tbfm-arrival-enrichment`.
- The rebuilt poller image carries `build-date=20260928T001428Z` (today) but
  bakes in the 2026-09-25 MANIFEST against newer source, so the in-container
  check fails and the oneshot exits 1.

**Remediation:** commit/settle the working tree, run
`scripts/sign-manifest.sh`, rebuild the affected image(s), and redeploy. Until
then these ~23 timer-fired jobs will keep failing.

---

## 10. Geometric reasoning — ALL FOUR PHASES LIVE

All phases live in `src/second_brain/semantic/compile.py`, per
`docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md`, retrievable via
`query_all_angles`. **Edge counts below were re-verified against live Postgres**
(`semantic_meta`, compiled_at `2026-09-27T16:45:52Z`, layer version `1.1.0`):

| Phase | Relation | Where | Live count (`semantic_meta`) |
|---|---|---|---|
| **Phase 0** | position/station resolution (`station_coordinates`, `_known_station_codes`, `_lookup_flight_position`) | `compile.py:108`, `782-978` | reference table (0058) |
| **Phase 1** | `proximate_to` (per-instance geometric edges) | `compile.py:882-1019` | `geometry_edges` = **954,148** |
| **Phase 1.5** | chronology edges | — | `chronology_edges` = 52,291 |
| **Phase 2** | `assign_causal_associations` → `statistically_associated` (kind='causal') | `compile.py:1050-1296` | `causal_edges` = **1,380** |
| **Phase 3** | `assign_clusters` → `member_of_cluster` | `compile.py` (cluster section) | `cluster_edges` = **56** |

Total `semantic_note_derivations` rows = **1,008,002**. All three brief-cited
counts (954,148 / 1,380 / 56) match live Postgres exactly. **NB (2026-09-28): phase NUMBERS are muddled across sources — the design doc, this reference, and GEOMETRIC_REASONING_DESIGN's as-built section number them differently. The canonical, unambiguous identifiers are the RELATION names — `proximate_to` (plausible/geometric), `statistically_associated` (provable/causal), `member_of_cluster` (cluster) — not the phase index.** Phase 2 explicitly
labels its output "statistically_associated" (not causation) with recorded
evidence; site-origin provenance (0061) was added ahead of Phase 2/3 precisely
so derived-edge origin need not be backfilled.

---

## 11. Recent additions (last ~6 weeks)

173 commits since 2026-08-17. The material ones for a reader:

1. **SQLite → Postgres, complete** — `de036ad` ("Complete SQLite-to-Postgres
   removal…"), plus the second-brain/reference-table reversals onto Postgres
   (`0052`–`0057`). Old 23 GB SQLite file reclaimed.
2. **Local model swap phi3-mini → Qwen3-4B** — `fe43973`, after live A/B/C eval.
3. **Geometric reasoning Phase 0 → 2/3** — Phase 0 in `de036ad`; Phases 2/3 in
   `d0874cd` / `1e3ed26` / `2291716` (with OOM fixes and baseline-math restore).
4. **OOOI source-authority lock** — `37ce72c` (schema 0065) + `1e61a89`
   (TFMS/FIDS enrichment-only). Closed the premature-landing sweeps (§8).
5. **Train parity LIVE** — `37b68d5` ("train parity: phase/ETA state, provider
   guard, surface archival, GTFS-RT deps"), schema `0064`; `1e3ed26` added train
   alerts. `amtrak-tracker` container running.
6. **codeshare_map seeding** — `5e96779`: new `scripts/seed-dc-codeshare.py`
   (5,669 B, DC-metro seeder). FDPS files plans under the *operating* callsign
   only; for wholly-owned regionals the mainline is deterministic (auto-seeded),
   for contract regionals (Republic/SkyWest/Mesa/GoJet/Air Wisconsin) it is
   genuinely ambiguous and is counted+reported, never guessed. Private/
   fractional operators (NetJets/EJA, Flexjet/LXJ, bare N-numbers) are **not
   dropped** — they simply have no codeshare mapping (the FBO/tarmac private-jet
   lane; tail→hex identity resolution lives in `poller/main.py:879-919`,
   `shared/watchlist.py:240`, `common/db.py:2435`).
7. **DC-metro aeroway GeoJSON** — `7866014`: new
   `data/dc-metro-aeroway.geojson` (168,049 B), OSM apron/terminal/gate
   footprints (KDCA/KIAD/KBWI…) fetched via Overpass 2026-09-28.
8. **maintenance_window enforcement, audit tamper-evidence (0062), site-origin
   provenance (0061)** — `97f4e80`.
9. **Audit retention: archive+sign off-box, retire in-place prune** — `2af693e`
   (pairs with 0062).
10. **Webhook buildout** — `6dc9d64`: LimoAnywhere reservations auto-track
    flights/trains on the watchlist.
11. **FIDS local-time fix** — `fffe641` (compute lookup date in airport-local
    time, not container UTC); **NOTAM VIP fixes** — `73d5aa9`, `42ff5d5`.
12. **psycopg pool close at exit** — `c7d481a`; **runner missing psycopg dep
    fix** — `fe43973`.
13. **CF service-token IPv4-egress scripts** — `f21db56` / `b3b29f8` (matches
    the account-scoped `curl -4` requirement).

---

## 12. Known rough edges / hazards (current)

1. **[HAZARD] Signed-manifest is stale → ~23 units failing** (§9.2). Highest-
   priority operational gap. Re-sign + redeploy.
2. **[HAZARD] Retired SQLite second-brain index still on disk** (§2.2): 104 MB
   frozen file (last written 2026-09-18), vestigial `INDEX_DB` constants at
   `compile.py:93` / `index_db.py:78`. Retire the file and constants.
3. **Vessel/AIS + UTM/drone parity dormant** (§3.3): code present, all quadlets
   `.disabled`, no hardware. Not deployed.
4. **[UNVERIFIED] Exact live Qwen3-4B GGUF path/quant** — config exposes persona
   model names, not the file path; confirm in the host llama.cpp service units if
   an exact model string is needed.
5. Ollama-vocabulary-without-Ollama persists (`OLLAMA_*` env, `ollama_model=`,
   `OllamaBusyError`) — deliberate call-site compatibility; the backend is
   llama.cpp.
6. Sovereign demo DB (`/var/lib/corporatetraveldc-demo-source/demo-source.db`)
   remains **SQLite by deliberate decision** (1.88 GB, separate from the
   Postgres cutover) — not a bug; the public playback boundary is physical.
7. SR-1 token accounting: [UNVERIFIED this session whether the 0/0-token gap
   from the draft §5.7 was closed] — treat as likely still open pending a
   re-read of `common/sr1_log.py` call sites.

---

*Validated 2026-09-28 against HEAD `78660147`. This document is the current
platform reference; `CODEBASE_REFERENCE_DRAFT_2026-09-03.md` is superseded and
should be treated as historical (SQLite-era).*
