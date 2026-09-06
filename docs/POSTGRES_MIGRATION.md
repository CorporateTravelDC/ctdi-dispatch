# PostgreSQL migration — the dispatch write path

**Status:** Phase 0 (infrastructure) live since 2026-09-06. Every service still
runs on SQLite (`DISPATCH_DB_BACKEND=sqlite`). Phases 1–4 below are the plan.

**Decision (operator, 2026-09-05):** everything that is *written at runtime*
moves to PostgreSQL. SQLite keeps only bulk-loaded, read-mostly reference
sets. New parity feeds (trains, drones, maritime) are Postgres-native from
day one. Retention floor is **90 days** for every time-series table.

---

## 1. Why

Measured 2026-09-05 on the production box (Pi 5, 15 GB, btrfs on NVMe):

- **~728 `database is locked` failures per day** (~30/hr), 672 of them the six
  SWIM ingest containers (tbfm 164, itws 136, tfms 121, stdds 113, fdps 102,
  notam 36) fighting each other for SQLite's single writer lock — each after
  a full 10 s busy-wait, each a dropped or delayed message.
- The load is **hot-row updates, not volume**: ~50k new rows/day total, but
  2,369 distinct `flight_events` rows updated per 15 min, and ~300
  `tbfm_sequences` rows carrying ~8,200 upserts/hr — one transaction each.
- The working set is small (tbfm 298 live of 41.7k; flight_events ~2.4k
  active of 938k). The 23 GB file is history. WAL was 840 MB and not
  checkpointing because long-lived readers pinned it.
- 14 containers share the one file. Trains/drones/maritime each add writers.

Postgres's MVCC removes the failure class structurally: writers don't block
writers on different rows, readers never block anyone, and the number of
concurrent writers stops mattering. That win does not depend on RAM —
buffer size only affects cache hit rate, and the hot set is tiny.

Batching writes was rejected: it trades write latency for read freshness, and
the platform's value is pickup fidelity.

## 2. What moves, what stays

**Moves to Postgres (65 of 76 tables):** every table any service writes at
runtime — all ingest tables (`flight_events`, `tbfm_sequences`, the STDDS/
TFMS/FDPS/ITWS/TDLS/D-ATIS sets, `acars_messages`, `local_aircraft`, …),
poller/web/pusher state (`feed_state`, `watchlist_*`, `hot_alerts`,
`nas_programs`, `notams`, `nws_*`, `metar_*`, `cps_scores`, `tfrs`,
`runsheet`, `board_*`, `audit_log`, `auth_tokens`, `session_grants`, …),
trains (`amtrak_status`, `train_events`, `ustrains_departures`) and maritime
(`vessel_events`).

**Stays on SQLite (11):** bulk-loaded reference sets, rebuilt by a single
batch loader on a slow cycle and otherwise only read:
`cifp_fixes`, `cifp_holds`, `cifp_procedure_legs`, `cifp_meta` (28-day AIRAC
pull), `faa_aircraft_registry`, `faa_aircraft_reference`, `faa_registry_meta`,
`faa_ladd_aircraft`, `opensky_aircraft_registry`, `opensky_registry_meta`,
`codeshare_map`. They never contend and moving them buys nothing.

**The one rule that keeps this scaling:** a reference table that a write-path
query needs to *JOIN* gets copied into Postgres too. Today there are zero
cross-engine SQL joins (verified two ways); the access layer refuses to build
one rather than silently doing it in Python. New modes follow the same split:
positions/events/alerts → PG; GTFS static, UAS registry, MMSI registry → SQLite
reference (or PG if hot-joined).

## 3. Phase 0 — infrastructure (LIVE)

| Piece | Where | Notes |
|---|---|---|
| Server | `corporatetraveldc-pgsql` quadlet, `.config/containers/systemd/corporatetraveldc-pgsql.container` | postgres:16-alpine, `production.slice`, `Memory=1536m` + `--memory-swap=3072m`, `MemoryLow=512M`, `CPUQuota=200%` |
| Tuned config | `config/postgresql.conf` (bind-mounted `:ro`) | tracked + signed like code; see §3.2 |
| Auth rules | `config/pg_hba.conf` (bind-mounted `:ro`) | scram-sha-256 on both paths; the image's initdb `trust` rules are never in force |
| Data | named volume `corporatetraveldc-pgsql-data` | `PGDATA=/var/lib/postgresql/data/pgdata` |
| Socket | named volume `corporatetraveldc-pgsql-sock` → `/var/run/postgresql` | **how app containers connect** — see §3.1 |
| TCP | `PublishPort=127.0.0.1:5432:5432` | bare-host tools only; registered in `/etc/corporatetraveldc/PORTS.md` |
| Server secret | `/etc/corporatetraveldc/pgsql-secrets.env` (0600) | `POSTGRES_PASSWORD` only — dedicated file so this container never sees SWIM/NWWS/API secrets; template `pgsql-secrets.env.template` |
| App secret | `DISPATCH_PG_PASSWORD` in `/etc/corporatetraveldc/dispatch-secrets.env` | must equal the server value |
| App config | `DISPATCH_DB_BACKEND`, `DISPATCH_PG_HOST/PORT/DB/USER` in `config/dispatch.env` (+ live copy) | `sqlite` until cutover; also the rollback switch |
| Driver | `psycopg[binary,pool]>=3.2` in `requirements.txt` | every image gets it on next rebuild; only `DISPATCH_DB_BACKEND` decides whether it is used |
| Overflow swap | `scripts/setup-disk-swap.sh` (root, btrfs-specific) | 8 G NOCOW swapfile at `/var/swap/`, priority 10 under zram's 100 — see §3.3 |

### 3.1 Connection path — unix socket, not TCP

The obvious design (bind `127.0.0.1:5432`, reach it from containers as
`host.containers.internal:5432` like ntfy) **does not work on this box** and
was measured not to on 2026-09-06: podman 5.8 starts pasta with
`--map-guest-addr 169.254.1.2`, so `host.containers.internal` maps to the
host's *default-route interface address* (10.0.0.x on wld0), never to
loopback. ntfy only works because it binds `0.0.0.0`. The alternatives were
all worse: a LAN/tailnet publish exposes the DB and breaks on the
wld0 ↔ enu1 failover the watchdog exists for.

So app containers use the **unix socket** in the shared
`corporatetraveldc-pgsql-sock` volume, mounted at `/var/run/postgresql` in
the server and in every app container. `DISPATCH_PG_HOST=/var/run/postgresql`
— psycopg treats a path as a socket directory. Benefits beyond working at
all: zero network exposure, independent of interface/failover, and no pasta
per-packet userspace hop (real CPU on a Pi at 8k upserts/hr). The image ships
the directory 3777 and the socket 0777, so any container user can reach it;
authentication is still scram.

Bare-host tools (psql, migration scripts) use TCP `127.0.0.1:5432`. Two pasta
artifacts to know: (a) a host-loopback connection arrives inside the
container with source rewritten to the host's interface address, so the hba
`host` rule must be `all` — reachability is enforced by the loopback bind,
auth by scram; (b) LAN and tailnet addresses were verified unreachable.

**Each app quadlet needs one line** when it is switched over:
`Volume=corporatetraveldc-pgsql-sock:/var/run/postgresql`.

### 3.2 Sizing rationale (`config/postgresql.conf`)

- `max_connections=60`: 14 containers × ≤4 pooled connections = 56. Ingest
  containers are single-threaded and get a pool of 2, which leaves room for
  ~10 more containers as parity modes land. Past that: pgbouncer or raise it
  (each idle backend is ~5–10 MB inside the 1.5 G cap).
- `shared_buffers=384MB` (25% of cap), `effective_cache_size=1GB`,
  `work_mem=8MB`, `maintenance_work_mem=128MB`.
- `synchronous_commit=off`: telemetry, not ledgers — a crash loses at most
  the last ~200 ms of upserts that will be re-upserted by the next message.
  `fsync` stays on; there is no corruption risk, only that window.
- Autovacuum tuned for hot-row churn: naptime 15 s, threshold 200, scale
  0.05, cost_delay 2 ms, cost_limit 400. **Autovacuum never deletes user
  rows** — it reclaims dead tuple versions left by UPDATEs. Retention is
  our own job (§6). Hot tables also get `fillfactor=70` in their DDL so
  updates stay on-page (HOT updates).
- `wal_compression=zstd`, `max_wal_size=1GB`, `checkpoint_timeout=15min`.
- `random_page_cost=1.1`, `effective_io_concurrency=200` (NVMe).
- `jit=off` (no benefit for OLTP on aarch64), `max_parallel_workers=2`.
- Guard rails: `statement_timeout=60s`, `lock_timeout=10s`,
  `idle_in_transaction_session_timeout=60s`.
- Observability: `pg_stat_statements` preloaded, `track_io_timing`,
  `log_min_duration_statement=500ms`, `log_lock_waits`, `log_checkpoints`.

**Config edits need `systemctl --user restart corporatetraveldc-pgsql`, not
`pg_reload_conf()`.** The files are bind-mounted individually; an editor that
replaces the file (new inode) leaves the container holding the old one.

### 3.3 Memory

production.slice was 10.5 G of its 13.8 G cap with the two resident llama
tiers holding 7.2 G. Postgres's cap is 1.5 G RAM + 1.5 G swap. The only swap
was 8 G zram — compressed RAM, which cannot make room for anything, only
buy ~2–3× on what is already there. `scripts/setup-disk-swap.sh` adds an
8 G NVMe swapfile at priority 10 (zram is 100): zram still absorbs first,
the file only takes what zram cannot. A swapped-out Postgres is slow, not
broken; the llama tiers keep their `MemoryLow` floors. btrfs requires the
file be NOCOW/preallocated in its own subvolume — the script uses
`btrfs filesystem mkswapfile` and refuses on any other filesystem.

Idle footprint measured: 36 MB RSS.

### 3.4 Verification done 2026-09-06

- `pg_isready` healthy; config values confirmed from `pg_settings`.
- Socket from a container mounting the volume: wrong password → `password
  authentication failed`; `DISPATCH_PG_PASSWORD` → connected,
  `inet_client_addr() IS NULL`.
- TCP loopback from the host: same two outcomes.
- `pg_isready` against the wld0 and tailscale addresses: no response.
- `scripts/check-env-quoting.sh`: OK.

### 3.5 Rotating the password

Change both files (`/etc/corporatetraveldc/pgsql-secrets.env` and
`/etc/corporatetraveldc/dispatch-secrets.env`), then
`ALTER ROLE dispatch PASSWORD '…'` — `POSTGRES_PASSWORD` is only read by
initdb on first start. Never quote either value.

## 4. Phase 1 — access layer (next)

`src/common/db_backend.py` (name provisional) behind the existing `conn()`:

- `DISPATCH_DB_BACKEND=sqlite` → today's thread-local `sqlite3` connection,
  byte-for-byte unchanged behaviour. Tests keep running on this backend.
- `DISPATCH_DB_BACKEND=postgres` → `psycopg_pool.ConnectionPool` per process
  (ingest 2, others ≤4), `row_factory=dict_row`, `autocommit` off with the
  same `with conn() as c:` commit-on-exit contract.
- Placeholder translation `?` → `%s` in the shim (563 sites), so accessor
  bodies do not change in this phase. Sites already using `%` formatting
  must be escaped — the shim asserts on ambiguous statements at import.
- `sqlite3.Row` → dict rows: `dict(row)` and `row["col"]` both keep working;
  positional `row[0]` access is audited (rare).
- Bare-host processes: if `DISPATCH_PG_HOST` is a path that does not exist,
  connect to `127.0.0.1:DISPATCH_PG_PORT` instead.
- `ref_conn()` — a separate, SQLite-only handle for the 11 reference tables.
  Any statement that names both a PG table and a reference table raises.
- Consolidated PG schema (`src/common/pg_schema/…`, numbered migrations
  applied through a `schema_migrations` table) replacing the 19
  `executescript` blocks and the duplicate-column swallowing. SQLite-isms
  rewritten there: `AUTOINCREMENT` → `GENERATED BY DEFAULT AS IDENTITY`,
  `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING` (6, load-bearing dedupe),
  `INSERT OR REPLACE` (1) verified and rewritten, `unixepoch()` (34) →
  `extract(epoch from now())`, `json_extract` (2) → `->>`, `strftime`/
  `date(x,'unixepoch')` (3) rewritten.
- Timestamp conventions are **kept as they are** in Phase 1 (REAL epoch on
  legacy tables, TEXT ISO-8601 on SWIM tables) so accessor logic and the
  `feed_db_integrity_check` epoch/iso switch do not change. PG rejects text
  in numeric columns, so live data is audited before the copy. New parity
  tables use `TIMESTAMPTZ`.
- `src/ingest/**` needs no changes (zero direct SQL, 54 accessor functions).
  The 17 raw `sqlite3.connect()` read sites in 9 files are routed through
  the layer.

Requires: sign (`scripts/sign-manifest.sh --agent`) + rebuild of all 8
images (psycopg), no service restarts yet.

## 5. Phase 2 — copy, Phase 3 — cutover

`scripts/migrate-sqlite-to-pg.py`: per-table copy with a 90-day window for
time-series tables and full copy for state tables; batched `COPY`; row-count
and spot-hash verification; **rehearsed repeatedly while every service stays
on SQLite** (the copy is idempotent — truncate and reload).

Cutover window (target < 15 min of ingest gap):
1. stop ingest + poller + pusher (web stays up read-only on SQLite);
2. delta copy of rows changed since the rehearsal;
3. flip `DISPATCH_DB_BACKEND=postgres` in the live `dispatch.env`, add the
   socket `Volume=` line to each quadlet, `daemon-reload`;
4. staggered start: web → poller → pusher → ingest one feed at a time;
5. verify: zero `database is locked` in journal, `pg_stat_activity`, feed
   freshness, watchlist round-trip.

**Rollback** = flip the flag back and restart. SQLite is untouched during
the window; anything written to PG in between is re-derived from the feeds.

## 6. Phase 4 — retention and partitioning

Time-series tables (`flight_events`, `train_events`, `vessel_events`,
`*_history`, `tbfm_sequences`, `surface_movement_events`, `acars_messages`,
…) become **weekly range partitions** on their timestamp column, created a
month ahead by a nightly job; retention is a **partition drop** of anything
older than the table's floor (never below **90 days** — policy 2026-09-05;
current SQLite code prunes `flight_events`/`train_events` at 30 and is
brought up to 90 at cutover). Dropping a partition is instant and leaves no
dead tuples, which is why autovacuum's job stays small. After cutover the
SQLite main-DB dependency is removed from all writers; the 23 GB file is
archived, not deleted, until the first 90-day cycle completes on PG.

## 7. Where the next choke point is

Postgres removes the database as the bottleneck. The next one is the Pi's
CPU/thermal — the six SWIM parsers already trip the thermal guard, and every
new mode adds a parser. That is a parsing-offload / second-box conversation,
not a database one.

---

## Appendix A — migration inventory (measured 2026-09-05)

- 36 ingest-written tables (23 via `db.py`, 13 via `db_swim.py`); 13 have
  shared writers (poller/web/pusher), 13 are write-only.
- `src/ingest/**`: zero direct SQL; 54 accessor writer functions; ~78
  accessor reader sites in 34 files; 17 raw-SQL read sites in 9 files.
- Cross-engine SQL joins: 0. Cross-engine transactions / app-level joins: 11
  (worst: `get_protected_flight_ids()` spanning `watchlist_sessions` +
  `flight_events` in one transaction — resolved by moving both; the
  CIFP-side joins in `cifp_lookup.py`/`tbfm_arrival_enrichment.py` are
  Python-side and stay two-engine).
- SQLite-isms in scope: 563 `?`, 203 `excluded.` (compatible), 25
  `ON CONFLICT DO UPDATE` (compatible), 34 `unixepoch()`, 21 `AUTOINCREMENT`,
  6 `INSERT OR IGNORE`, 1 `INSERT OR REPLACE`, 2 `executemany`, 19
  `executescript`, 49 `ALTER TABLE` (idempotent by swallowing "duplicate
  column"), 4 `PRAGMA`, 1 global `sqlite3.Row`. Zero `rowid`/`lastrowid`/
  `julianday`/`GROUP_CONCAT`/`ATTACH`.
- Timestamp split: REAL epoch on `feed_state`, `nas_programs`, `hot_alerts`,
  `nws_alerts`, `amtrak_status`, `flight_events`, `feed_data_usage`,
  `wpc_discussions`, `train_events`, `flight_ooooi_times`; TEXT ISO-8601
  (lexicographic compare) on every SWIM/STDDS table.
- Demo/scrub pipeline: no direct exposure (`demo.db` is separate;
  `scrub-demo-source.py` touches only `snapshots`/`brief_archive`/
  `faa_ladd_aircraft`). Indirect: 6 of 13 recorder endpoints read in-set
  tables through web accessors.
- Per-table byte sizes are still unmeasured (`dbstat` on the 23 GB file
  timed out); the 90-day disk figure is derived at rehearsal time from the
  actual copy.
