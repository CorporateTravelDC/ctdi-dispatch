# Demo Data Isolation — Sovereign Scrubbed Source Plan

> **Status note, 2026-10-16 (code + quadlets; verified against the live box
> 2026-10-16 12:13Z).** Operator rule: the public demo is logically **and**
> physically separated from production. On 2026-10-16 it was not: one
> Postgres instance (`corporatetraveldc-pgsql`), one database
> `corporatetraveldc`, one superuser role `dispatch`; the
> `corporatetraveldc-demo-api` and `corporatetraveldc-runner-demo` quadlets
> mounted the production socket volume `corporatetraveldc-pgsql-sock` and
> loaded `dispatch.env` with `DISPATCH_PG_DB`/`DISPATCH_PG_USER` pointing at
> that pair. demo-api pinned `DISPATCH_DB_BACKEND=sqlite` (demo-secrets.env,
> 2026-09-28), but `src/common/db_backend.py`'s `pg_conn()` never consulted
> the backend, so `src/demo/profiles.py` (moved onto Postgres 2026-09-20,
> `pg_schema/0057_demo.sql`) targeted production and failed only for lack of
> a password — the demo login had been broken since the cutover, not isolated.
>
> **Enforced now (this commit):**
>
> - `src/common/db_backend.py`: `_pg_conninfo()` and `pg_conn()` raise
>   `PostgresRefused` when `DISPATCH_DB_BACKEND` is not `postgres` (the
>   sqlite pin is honoured by every direct caller, not only `db.conn()`), and
>   under `DISPATCH_DEMO_ISOLATION=1` refuse any connection whose dbname
>   **or** user is the production pair, before a socket is opened.
> - `src/demo/profiles.py`: under a non-postgres backend the password-gate
>   store is its own SQLite file in the demo-state volume
>   (`DEMO_ACCESS_DB`, default `/var/lib/corporatetraveldc-demo-state/
>   demo_access.db`, the pre-2026-09-20 `profiles` table byte-for-byte, so the
>   file already in that volume works as-is). Postgres `demo_profiles` remains
>   the shape for a future demo-owned instance.
> - Quadlets: `corporatetraveldc-demo-api.container` and
>   `corporatetraveldc-runner-demo.container` no longer mount
>   `corporatetraveldc-pgsql-sock` and set `Environment=DISPATCH_DEMO_ISOLATION=1`.
>   `config/demo-secrets.env.example` now tracks the live
>   `DISPATCH_DB_BACKEND=sqlite` pin; `config/dispatch.env.example` documents
>   the flag (commented out — it must never be active in the shared file).
> - Tests: `tests/common/test_db_backend_demo_isolation.py`,
>   `tests/demo/test_profiles_sqlite.py`,
>   `tests/scripts/test_demo_quadlets_isolation.py` (no public-demo quadlet
>   mounts the production socket or `/var/lib/corporatetraveldc`).
>
> **Remaining (operator decisions, not done here):**
>
> - ~~A separate demo Postgres instance and role (the demo's own
>   `demo_profiles`/`demo_snapshots` home). Until then the demo app
>   containers have no Postgres at all, by design.~~ (decided 2026-10-16:
>   built in relay 1 of the database split, addendum below.)
> - The two trusted-side producers keep production access: the recorder
>   (`corporatetraveldc-demo.container`) mounts `/var/lib/corporatetraveldc`
>   **read-write** and the production socket (it writes `demo_snapshots` to
>   the production database), as does
>   `corporatetraveldc-second-brain-demo-archiver-daily.container`. They serve
>   nothing publicly, and the isolation test names them explicitly so nothing
>   else joins them. (Relay 4 of the split narrows the recorder, addendum below.)
> - The Postgres `demo_profiles` table may hold profiles created through the
>   admin API between 2026-10-10 (when the admin token started working) and
>   2026-10-16; the sqlite file holds the 7 rows of 2026-08-02 (4 active). If
>   any newer profile matters, export it host-side (read-only) into the sqlite
>   file; otherwise re-create it through `/admin/demo/profiles` after the
>   restart.
>
> Claims in the plan below that this supersedes are struck through in place.

> **Addendum, 2026-10-16 -- the demo/production DATABASE split (relay 1 of 4,
> code and tracked units only; nothing demo-facing changes until relay 2).**
>
> *The regression, in three lines.* 2026-09-18..20: the platform moved to one
> Postgres instance, one database, one superuser role, and the demo's two
> stores followed (`demo_snapshots`, `demo_profiles`, pg_schema/0057). The two
> public-demo quadlets were given the production socket volume and
> `dispatch.env`, so the public demo's only database client pointed at
> production, failing only for lack of a password (found 2026-10-16 12:13Z).
>
> *The hard rule (operator, 2026-10-16).* The public demo is fed ONLY by a
> scrub from production into a logically **and** physically separate
> database, exactly as `push-public.sh` feeds the public tree: production =
> private; the demo Postgres instance = public mirror;
> `scripts/scrub-demo-source.py` = the only bridge. LADD-listed identifiers
> never reach a demo surface (fail CLOSED); the production superuser password
> never enters `demo-secrets.env`; `runner-demo` is not a production unit.
>
> *Relays.*
>
> 1. **Relay 1 (this batch, additive).** New instance
>    `corporatetraveldc-pgsql-demo` (quadlet, `config/postgresql-demo.conf`,
>    `config/pg_hba-demo.conf`; data + socket volumes; `127.0.0.1:5433`; 512 MB)
>    in the boot order before demo-api and in the rollout list; production
>    `config/pg_hba.conf` rejects `demo` / `demo_owner` / `corporatetraveldc_demo`
>    by name before its catch-alls; schema `src/common/pg_schema_demo/0001`
>    (`snapshots`, `brief_archive`, `meta`, `demo_profiles`, `chat_messages`,
>    hash-chained `scrub_ledger`; the `demo` role created LOGIN with no
>    password, minimum grants) applied by `scripts/pg_migrate.py --instance
>    demo`; `db_backend.demo_pg_conn()` (own pool, `DEMO_PG_*`) with refusals
>    both ways and `common.db.conn()` routed by `DISPATCH_ROLE`;
>    `src/demo/profiles.py` on the demo pool under `DISPATCH_ROLE=demo`,
>    `src/demo/demo_api.py` on it under `DEMO_SOURCE=postgres` (default still
>    the sovereign file; `/healthz` names the source); the scrub rewritten PG ->
>    PG (nine endpoints, >= 24 h floor, LADD gate through the production
>    filter -- empty or failed = exit 5 -- substitutions, tail/hex sweeps,
>    fail-closed verify, 52-week prune, one ledger row per run, `--backfill
>    --days N` / `--refresh` / `--profiles` / `--verify-ledger` / `--dry-run`);
>    the refresh service runs it (timer still disabled); guards:
>    `tests/scripts/test_demo_isolation.py`, `tests/demo/test_demo_accessor_boundary.py`,
>    `tests/scripts/test_pg_schema_demo.py`, `tests/scripts/test_scrub_demo_source.py`,
>    a relay-lint rule, and the live `scripts/demo-isolation-check.sh` in the
>    15-minute sweep (`ops-health` p4); examples `config/demo.env.example`,
>    `config/pgsql-demo-secrets.env.example`, `DEMO_PG_PASSWORD` in
>    `config/demo-secrets.env.example`; the nightly backup excludes the instance.
> 2. **Relay 2 (flip).** Install `/etc/corporatetraveldc/demo.env`
>    (`DISPATCH_ROLE=demo`, `DISPATCH_DB_BACKEND=postgres`, `DEMO_PG_*`,
>    `DEMO_SOURCE=postgres`) and `DEMO_PG_PASSWORD` in ~~`demo-secrets.env`~~
>    `demo.env` (2026-10-19);
>    `ALTER ROLE demo PASSWORD` typed on the box; the two demo quadlets drop
>    `dispatch.env` for `demo.env`, mount `corporatetraveldc-pgsql-demo-sock` at
>    `/var/run/postgresql-demo`, and restart. The isolation tests then forbid
>    `dispatch.env` in them. ~~Enable `corporatetraveldc-demo-source-refresh.timer`.~~
>    (2026-10-19: the timer moved to relay 3; the grants re-audit moved here.)
> 3. ~~**Relay 3 (retire the file).** Remove the sovereign SQLite mount and
>    `DEMO_DB` from demo-api; delete the `DEMO_SOURCE=sqlite` path and
>    `/var/lib/corporatetraveldc-demo-source` after a last diff of what the
>    file holds against the instance.~~ (2026-10-19: relay 3 is the refresh
>    timer; retiring the file is relay 4, below.)
> 4. ~~**Relay 4 (producers).** The recorder keeps HTTP access to the live API but
>    loses the `/var/lib/corporatetraveldc` read-write mount; the demo archiver
>    skill likewise; the `demo` role's grants are re-audited once relay 2's
>    traffic is observed.~~ (2026-10-19: relay 4 retires the SQLite file; the
>    producers' narrowing is a later relay, not yet built; the grants re-audit
>    is in relay 2.)
>
> *Operator hand steps, relay 1 (the only ones).* Create
> `/etc/corporatetraveldc/pgsql-demo-secrets.env` (mode 0600, service user)
> with ONE line, `POSTGRES_PASSWORD=<value>`, typed through
> `read -rs ... </dev/tty` into the file -- never on a command line, never the
> production value, never quoted. Nothing else until relay 2: no quadlet
> changes to demo-api/runner-demo, the refresh timer stays disabled.

> **Addendum, 2026-10-19 -- relays 2, 3 and 4 of the split (built; tracked
> files only until the operator runs them; relay 4 is preparation only).**
>
> *Relay 2 -- the flip.*
> - Quadlets `corporatetraveldc-demo-api` and `corporatetraveldc-runner-demo`:
>   `Environment=DISPATCH_ROLE=demo` (identity, signed), `Volume=corporatetraveldc-
>   pgsql-demo-sock:/var/run/postgresql-demo`, ~~`EnvironmentFile=.../dispatch.env`~~
>   removed, `EnvironmentFile=/etc/corporatetraveldc/demo.env` loaded LAST (after
>   `demo-secrets.env`, so its `DISPATCH_DB_BACKEND=postgres` wins over the old
>   sqlite pin), `DISPATCH_DEMO_ISOLATION=1` kept, `After=` the demo instance.
>   Dropping `dispatch.env` also removes `ULTRAFEEDER_URL` from runner-demo (the
>   2026-08-27 comment claimed it was never set; `dispatch.env` set it).
>   Neither container mounts `/etc/corporatetraveldc` (so `common.config`'s
>   import-time load of `dispatch.env` / `dispatch-secrets.env` finds nothing).
> - `DEMO_SOURCE` and `DISPATCH_DB_BACKEND` are in `demo.env`, NOT `Environment=`
>   lines: podman's `-e` beats `--env-file`, and the rollback must not need a
>   signed edit. **Rollback, one step:** `scripts/demo-env.py source sqlite` (sets
>   the pair), then restart the two demo containers -- playback from the
>   sovereign file (still mounted read-only), profiles from `demo_access.db`,
>   chat in the demo-only mount: exactly the pre-flip state.
>   `scripts/demo-env.py source postgres` undoes it.
> - `demo.env` (example `config/demo.env.example`, 0600, service user) holds the
>   demo identity, `DEMO_PG_PASSWORD` (moved from `demo-secrets.env`),
>   `LLAMA_BASE_URL` / `OLLAMA_CHAT_MODEL` copied BY NAME from `dispatch.env`
>   (`scripts/demo-env.py seed`; nothing else is ever copied), `DEMO_LOOP_DAYS`.
>   `/etc/corporatetraveldc` is root:corporatetraveldc 0750, so the file is
>   created once by `sudo install -m 600 -o corporatetraveldc -g corporatetraveldc
>   config/demo.env.example /etc/corporatetraveldc/demo.env`; `demo-env.py`
>   edits it in place afterwards.
> - The `demo` role's password: `scripts/demo-env.py set-password` -- the value
>   typed through `read -rs ... </dev/tty` (or generated when Enter is pressed),
>   handed to Python through the environment, written to `demo.env` first, then
>   applied as a SCRAM-SHA-256 verifier computed host-side (`ALTER ROLE demo
>   PASSWORD 'SCRAM-SHA-256$...'` on psql's STDIN inside the demo instance, as
>   demo_owner; the plaintext never reaches argv, the server or its log), and
>   verified by logging in as `demo` over the socket (`podman exec -e
>   PGPASSWORD`, passthrough). Rerun-safe: a value that logs in is left alone;
>   one that does not is re-applied.
> - Grants re-audit: `src/common/pg_schema_demo/0002_demo_grants.sql` --
>   SELECT-only on `snapshots`, `brief_archive`, `meta`, `scrub_ledger`;
>   SELECT/INSERT/UPDATE on `demo_profiles` and SELECT/INSERT/DELETE on
>   `chat_messages` (the only writes the demo's own code makes: profile
>   create/revoke, chat append/clear; SELECT-only there would break both); no
>   sequence grants (identity columns need none), no `schema_migrations`, no
>   CREATE/TEMP, CONNECT for `demo` only, CONNECTION LIMIT 12. The live sweep
>   (`scripts/demo-isolation-check.sh` §4) compares the instance against
>   exactly that set once 0002 is applied; §1b holds that a flipped container
>   carries no `DISPATCH_PG_*` name and sees no `dispatch.env`.
> - Profiles: all four active rows of `demo_access.db` already exist in the
>   instance (relay 1's `--profiles`; checked 2026-10-19 by hashed key, counts
>   only: 5 in the instance, 7 in the file, the 2 file-only rows inactive).
> - demo-api `/healthz` adds `started_at`, `uptime_s`, `requests` (per source,
>   since start) and `profiles_store`.
>
> *Relay 3 -- the nightly refresh.* `corporatetraveldc-demo-source-refresh.timer`
> enabled at ~~04:45 America/New_York~~ **03:17 UTC**; the service runs
> `scrub-demo-source.py --refresh --workers 1` at `Nice=19`, idle I/O, after
> `scripts/relay-wait-load.sh` (load1 < 24, up to an hour), with the scrub's own
> per-page pacing (`DEMO_SCRUB_MAX_LOAD=24`) and a wait before the LADD gate
> opens, `TimeoutStartSec=4h`, `OnFailure=corporatetraveldc-unit-failure-notify@`.
> Production reads go through `readonly_source()` (`SET TRANSACTION READ ONLY`).
>
> *Relay 4 -- retire the SQLite files (prepared, not run).*
> `scripts/demo-sqlite-retire-check.py --hours N` verifies N continuous hours on
> Postgres (healthz source/ok/profiles_store/uptime, zero sqlite-answered
> requests, both containers started >= N h ago with the postgres pair), lists the
> old demo SQLite files with sizes and open-file holders, writes the guardrail
> proof **G26** record (sha256, size, mtime per file), and only then prints the
> exact shred command -- for the operator to run by hand, never from a relay.
> Removing the sovereign mount and the `DEMO_SOURCE=sqlite` path (a quadlet +
> code change, not built yet) ends the rollback and must PRECEDE the shred;
> narrowing the recorder and archiver is a later relay.

Written 2026-08-13. Design/planning document for operator review — **no code
in this repo changes as part of this doc**. It responds to finding F6 of the
red-team pass (`docs/LIVE_PENTEST_REDTEAM_2026-08-13.md`, branch
`live-pentest-redteam-2026-08-13`, not yet merged) plus a related usability
complaint about the demo's stale playback window, and proposes one pipeline
that fixes both: live DB → scrub pass → sovereign demo-source file →
demo playback, with the demo containers' filesystem access narrowed to that
sovereign file alone, read-only. Every path, line number, and row count below
was verified against the running system tonight, not quoted from memory.

## 1. Problem statement

The public demo's isolation from live production data is **partial**, and the
partial half is the half that carries the most sensitive prose.

**What is genuinely separate today.** The nine time-series endpoint types
(`tfr`, `weather`, `alerts`, `cps`, `notams`, `amtrak`, `opsplan`, `route`,
plus the basic `brief`/ops type) are served from
`/var/lib/corporatetraveldc/demo.db` — a physically distinct SQLite file,
1.8 GB, 32,277 snapshot rows spanning 2026-06-27 through today, built by
`src/demo/recorder.py` polling the live HTTP API
(`http://100.x.x.x:8000/api/v1`) every 300 s since 2026-06-27
(zlib level 6, ~95% savings, sha256 dedup). `src/demo/demo_api.py` reads
these only from `DEMO_DB`, through `_virtual_timestamp()` (lines 146–173) —
the function its own module docstring describes as a "hard, not cosmetic"
privacy boundary: playback replays a fixed looping historical window and can
never reach real "now" data.

**What is not separate.** Weekly briefs, EP-advance briefs, and paginated
brief history bypass all of that. `demo_api.py` line 48 defines
`LIVE_DB = os.environ.get("LIVE_DB", "/var/lib/corporatetraveldc/corporatetraveldc.db")`
— the actual 20.5 GB production database — and `_live_conn()` (line 245)
opens it directly. `_brief_archive_lookup()` / `_brief_archive_history()`
(lines 251–293) query its `brief_archive` table (822 ep-advance, 604 ops,
28 weekly rows tonight), filtered only by the virtual timestamp at query
time. The comment block at lines 368–375 documents this as a deliberate
2026-08-02 shortcut — "reading brief_archive directly from the live app's
DB … rather than trying to grow a second recorder archive from scratch" —
done to fix BriefView.jsx's weekly/ep-advance 404s. It was never paid down.

**Why this is worse than an app-level query filter.** Both
`corporatetraveldc-demo-api.container` (line 27) and
`corporatetraveldc-runner-demo.container` (line 34) mount the **entire**
live state directory: `Volume=/var/lib/corporatetraveldc:/var/lib/corporatetraveldc:z`
— no `:ro`. The app opens its live connection with `mode=ro` in the SQLite
URI, but that is a courtesy the demo-api process pays itself, not a boundary
the OS enforces. A compromised demo-api process — a lower-trust,
public-surface-by-design component — holds real read-write mount permissions
on `corporatetraveldc.db`, which contains `auth_tokens`, `board_tokens`,
`approval_requests`, `audit_log`, `runsheet`, `watchlist_*`, `osint_*`, and
every client-facing brief ever generated. This is exactly the
insider/lateral-movement threat model F6 flagged: the pentest's own words
were that isolation is "**logical** (demo-api's time-window replay), not
physical," left as `NEEDS HUMAN REVIEW`. This doc is that review, and the
answer is: not by design any longer — close it.

**The related usability problem.** `_anchor()` (demo_api.py lines 116–143)
pins the loop to the earliest point where every endpoint has coverage —
verified tonight as **2026-07-09T19:53Z** (alerts/notams start 06-27, the
other seven endpoints 07-09) — and caches it for process lifetime.
`DEMO_ANCHOR_OVERRIDE` (lines 51–63) exists but is unset. So the demo
permanently replays 07-09 → 07-23: a five-week-old slice that predates the
AIRMET/SIGMET overlay, the maritime/prog chart expansion, the dedicated-model
brief improvements, and everything since. The operator has explicitly ruled
out "just move the anchor to July 30" — meaningful capability has landed
since then too. The requirement is that the demo be seeded from **current**
live state and keep tracking platform growth on a schedule, not from any
hand-picked fixed date.

**Separately confirmed today, out of scope here but more urgent:** the main-
branch pentest (`docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md` §6) confirms
the second-brain vault and knowledge-graph endpoints are still readable
unauthenticated on the public production hostname — that report's own "finding
to lead with," unremediated as of tonight. It needs its own fix pass; nothing
in this plan addresses it.

## 2. Goals

1. **Sovereign demo-source file.** A new SQLite file in a new **top-level**
   directory — proposed `/var/lib/corporatetraveldc-demo-source/` — not a
   subdirectory of the live `/var/lib/corporatetraveldc` tree, so it carries
   independent ownership/permissions today and can become its own mount
   point without restructuring if a second physical device is ever added.
2. **A scrub pipeline modeled on `scripts/scrub-public-tree.py`.** Same
   two-layer discipline that already gates the public mirror: proactive
   substitutions (layer 1) plus an allowlist-based verification scan of the
   *output* that fails closed (layer 2). Redact/genericize PII/CUI — client
   names, addresses, phone numbers, precise EP/principal-movement detail,
   radio frequencies, exact venue/hotel specifics — and never copy
   credential/token tables at all. Keep aggregate public-safe content:
   TFR/weather/NAS data, public flight/train tracking, and brief narrative
   after substitution.
3. **Demo playback fed by the scrub output, not by live reads.** The
   sovereign file is the only thing demo-api reads. `_live_conn()`,
   `LIVE_DB`, and both `_brief_archive_*` live-read functions are deleted
   outright once the sovereign file carries scrubbed `brief_archive` content.
4. **Backfill from current state.** The initial pipeline run seeds the
   sovereign archive with the trailing window ending **today (2026-08-13)**,
   so the demo timeline starts current — not at 07-09, not at any other
   fixed old date.
5. **Ongoing recurring refresh.** A scheduled job keeps promoting freshly
   scrubbed data so the demo tracks real platform growth without manual
   re-seeding — while the existing loop/virtual-timestamp mechanism stays.
   Only the anchor's *derivation* changes (§3, Phase 3): playback still never
   shows literal real-time "now" data.
6. **Narrowed, read-only container mounts.** demo-api and runner-demo lose
   the live-directory mount entirely; what remains is the sovereign demo
   directory, `:ro` at the podman mount level. A fully compromised demo-api
   process ends up with zero filesystem path to live data — not an
   app-level convention against using one.
7. **Stretch goal, explicitly out of scope for this software-only pass:**
   device-level separation. Verified tonight via lsblk/df: one physical NVMe
   (`nvme0n1`), one btrfs partition backing both `/` and `/home`, no second
   device attached. True disk-level isolation needs new hardware (USB SSD or
   second NVMe). This plan delivers real file/directory-level physical
   separation now and leaves the sovereign directory shaped so a future
   device can simply mount over it.

## 3. Proposed design and phasing

### Architecture in one paragraph

Capture stays where it is proven: `recorder.py` keeps polling the live API
every 5 minutes into `demo.db`, which is **reclassified as a private,
live-side staging archive** — it stops being the file the demo reads. A new
scrub pipeline (`scripts/scrub-demo-source.py` + a shared rules module
`src/demo/scrub_rules.py`) is the *only* component that both touches live
data and writes to the demo side: it reads staged endpoint payloads from
`demo.db` and `brief_archive` rows directly from `corporatetraveldc.db`
(read-only), passes every payload/row through substitutions + fail-closed
verification, and appends the survivors into
`/var/lib/corporatetraveldc-demo-source/demo-source.db`. demo-api mounts
only that directory, `:ro`. This honors the operator's "live → scrub →
sovereign file → feeds the demo" pipeline exactly at the trust-boundary
level; the one deliberate deviation from a literal reading of it is that
time-series capture remains payload-level HTTP polling rather than
raw-table extraction, because the demo's whole design contract
(demo_api.py's "exact path parity … lets an unmodified runner swap
DISPATCH_BASE_URL") depends on replaying *rendered* endpoint responses —
re-deriving those from raw tables would duplicate `src/web/main.py`'s
rendering logic and drift from it forever. The scrub gate, not the capture
method, is what makes the source sovereign: nothing becomes reachable by
the demo surface without passing it.

### Phase 0 — Decisions and prep (operator, this doc)

Sign off on the open questions in §5, and on the two directory names:

- `/var/lib/corporatetraveldc-demo-source/` — sovereign scrub output,
  mounted `:ro` into demo-api (and runner-demo only if verification in
  Phase 4 shows it needs any mount at all).
- `/var/lib/corporatetraveldc-demo-state/` — tiny read-write directory for
  `demo_access.db` (profiles/passwords, 12 KB, ~~currently at
  `/var/lib/corporatetraveldc/demo_access.db` per `src/demo/profiles.py`
  line 33~~ relocated there 2026-08-26; moved onto the production Postgres
  2026-09-20; back in this directory as the demo's own store since
  2026-10-16, see the status note). It is already fully independent of live
  data, but it **must**
  leave the live directory or demo-api keeps a live-dir mount forever and
  the whole point of Phase 4 is lost. Kept separate from demo-source so the
  scrub output stays a strictly read-only surface for every demo process.

### Phase 1 — Scrub rules + pipeline, signed before first use

`src/demo/scrub_rules.py`: a `SUBSTITUTIONS` dict + `REGEX_SWEEPS` list +
`FORBIDDEN_PATTERNS` verification set, deliberately mirroring
scrub-public-tree.py's structure so the discipline transfers, but targeting
*prose/payload* content rather than repo blobs: client and principal names,
street addresses, US phone-number shapes, radio-frequency shapes
(`1xx.xxx` MHz forms), venue/hotel proper nouns, and the operator-identity
values the existing scrubber already tracks. Like the public-tree scrubber,
layer 1 (substitution) is best-effort and layer 2 (verification of the
*output*) is what actually gates: any payload or brief still matching a
forbidden pattern after substitution is **dropped from the sovereign set
and queued for manual review** — a gap in demo history is acceptable; a
leak is not.

`scripts/scrub-demo-source.py`: the pipeline. `--backfill` and `--refresh`
modes (Phases 2 and 5). Copy semantics are **extract-allowlist-only**: it
copies exactly two content tables (`snapshots` from demo.db,
`brief_archive` from the live DB) plus a small `meta` table it maintains
itself (`promoted_at`, `window_end`). It never copies-then-deletes — with a
20.5 GB live DB that would be both operationally absurd and fail-open (a
missed DELETE ships data; a missed INSERT ships nothing). Both source
connections are opened `mode=ro` — and unlike demo-api, this script runs on
the trusted host side, so that convention is defense-in-depth rather than
the boundary itself.

Integrity discipline: both new files go into `MANIFEST.sha256` and get
signed via `scripts/sign-manifest.sh` before the first real run, and the
pipeline's first action is to run `scripts/verify-manifest.sh` against
itself and `scrub_rules.py`, refusing to proceed on failure — the same
self-verifying pattern `_verify_before_inference()` already applies in
`src/common/llm.py` before any inference. A privileged live-DB reader that
feeds a public surface is precisely the kind of process that discipline
exists for. (Housekeeping noticed while grounding this: multiple code
comments cite a "Signed Manifest Integrity" section of
`docs/COMPLIANCE_SECURITY.md` that doesn't exist under that heading in the
tracked doc — the mechanism is real and live, the doc section reference is
drift. Worth fixing during this work.)

### Phase 2 — Backfill from current state

One `--backfill` run seeds `demo-source.db` with the trailing **28 days**
of staged snapshots (2026-07-16 → today: 27,497 of the 32,277 rows, ≈1.5 GB
— the archive is bottom-heavy toward recent NOTAM churn) plus all
`brief_archive` rows in the same window, everything through the scrub.
28 days rather than the 14-day `LOOP_DAYS` gives immediate headroom for the
`2w` tier plus margin, without waiting on any re-seeding period — the demo
is current on day one. The run emits a report: rows promoted, rows dropped
by verification (with pattern class, never content), per-endpoint coverage.

**Gate before anything consumes it:** operator manually spot-checks a
sample of scrubbed briefs — at minimum several ep-advance briefs, the
highest-sensitivity prose in the platform — against the originals. The
sovereign file exists but nothing serves it until this sign-off.

### Phase 3 — demo_api.py cutover

- `DEMO_DB` default → `/var/lib/corporatetraveldc-demo-source/demo-source.db`.
- The three brief sub-routes (`/brief/weekly`, `/brief/history`,
  `/brief/{ref}`) re-point at the sovereign `brief_archive` table through
  the same `_conn()` read-only path as everything else.
- **Delete `_live_conn()`, `LIVE_DB`, `_brief_archive_lookup()`,
  `_brief_archive_history()`** — full closure of the 2026-08-02 shortcut.
- Anchor rework: `_anchor()` derives from the sovereign `meta` table —
  `anchor = window_end − window_days` — instead of oldest-coverage-forever.
  The anchor now advances, but **only when a promotion writes a new
  `window_end`**, never as a function of wall-clock time at query time.
  This is a deliberate restatement of the privacy boundary, and the module
  docstring must be rewritten to match: the invariant changes from "only
  old data is reachable" to "**only data that has passed scrub+verify and
  been promoted is reachable, and it is always at least one promotion
  cycle behind now**." That is a strictly stronger property — the old
  boundary was temporal only; the new one is artifact-based and temporal.
  `DEMO_ANCHOR_OVERRIDE` stays as a curation knob. `profiles.py`'s `DB`
  default moves to the demo-state directory (done 2026-08-26; ~~undone by the
  2026-09-20 Postgres move~~ restored 2026-10-16 as `DEMO_ACCESS_DB`).

### Phase 4 — Container mount narrowing (highest-risk step)

Quadlet changes, one service at a time, demo-api first while runner-demo
still runs the old config:

- `corporatetraveldc-demo-api.container`: replace the live-dir volume with
  `Volume=/var/lib/corporatetraveldc-demo-source:/var/lib/corporatetraveldc-demo-source:ro,z`
  plus `…-demo-state:…-demo-state:z` (rw — profile admin writes). (Done
  2026-08-14. ~~The 2026-09-20 Postgres move then added the production socket
  volume `corporatetraveldc-pgsql-sock` to this container~~ — removed
  2026-10-16, status note.)
- `corporatetraveldc-runner-demo.container`: first *verify* what its
  live-dir mount is actually for — the runner in demo mode proxies demo-api
  over HTTP and plausibly needs no state mount at all; if so, delete the
  volume line outright rather than narrowing it.
- `corporatetraveldc-demo.container` (recorder): keep its live-API HTTP
  access (it is trusted-side by design) but narrow its mount to a staging
  directory holding `demo.db` — after this phase, ~~**no container in the
  demo pipeline mounts `/var/lib/corporatetraveldc` at all**~~ (not done as
  of 2026-10-16: the recorder still mounts `/var/lib/corporatetraveldc`
  read-write and the production Postgres socket — remaining item, status
  note).

**Rollback path, explicit:** the quadlets are git-tracked; rollback is
`git checkout` of the previous `.container` file + `systemctl --user
daemon-reload` + restart — config-only, no data migration to unwind,
because `demo.db` and the live DB are never moved or modified by any phase.
Keep the pre-cutover demo_api.py able to run against the old paths (env
vars, not hardcodes — already true) for the same reason. SELinux note: the
new directories need the same `:z`/`:ro,z` labeling treatment as the
existing volume; create-and-label before first start, not during an
incident.

### Phase 5 — Recurring refresh

`--refresh` mode: promote everything staged since the last `window_end`
through the same scrub+verify, append into the sovereign DB (single writer
— the scrub job; demo-api is `mode=ro`, same single-writer discipline
recorder.py documents for demo.db today), prune beyond retention, bump
`meta.window_end`. Append-and-prune, not rebuild-and-swap — no atomic-replace
dance against live readers, and demo-api's per-request open/close connection
pattern (`_conn()` per handler) tolerates appends trivially.

Schedule: `corporatetraveldc-demo-source-refresh.timer` +  oneshot
`.service`, **daily at 04:45 America/New_York, `Persistent=true`**, run
host-side (uncontainerized, like the other script-driven user services) so
the one privileged live-DB read never enters a container that could be
conflated with the demo surface. The time slots into the existing quiet-
period convention — `corporatetraveldc-second-brain-demo-archiver-daily.timer`
runs 04:15 ET "right after the 04:00 vault index scan, quiet period, no
Ollama use so no stacking concern"; this job is the same shape (no Ollama
use under the recommended brief handling, §5 Q1). Net freshness: the demo
trails live by 0–28 hours — current-feeling, and the promotion lag doubles
as a minimum-age privacy floor.

### Phase 6 — Documentation, manifest, and F6 closure

Update `docs/COMPLIANCE_SECURITY.md` §2 (data sovereignty/isolation) and
`docs/INFRA_MAP.md` to describe the sovereign-source architecture; rewrite
demo_api.py's boundary docstring (Phase 3); re-sign the manifest; record F6
as remediated-with-evidence (mount lines, deleted functions) when the
pentest branch merges or in a follow-up dated doc.

## 4. Risks and things to watch

- **The scrub pipeline is itself a new privileged process.** It reads the
  production DB and writes to a public-adjacent surface — the exact profile
  that must be under signed-manifest integrity (Phase 1) and why it runs
  host-side on a timer, not inside any demo container. Its failure mode
  must stay fail-closed: verify-layer violation → drop + review queue,
  never ship-with-warning.
- **Narrative prose is harder to scrub than config.** scrub-public-tree.py
  polices a finite value set in a tree it fully controls; briefs are
  free-form LLM prose where names inflect and context identifies. The
  drop-on-violation posture plus the Phase 2 manual sample gate are the
  mitigations; expect to iterate `scrub_rules.py` over the first weeks the
  way the public scrubber's allowlists grew (its 2026-07-12 audit entries
  are the precedent).
- **If regeneration is chosen over substitution (§5 Q1), it lands on the
  Ollama load story just re-worked today (2026-08-13).** Backfilling 1,454
  archived briefs at realistic 5–15-minute brief-class generation times is
  on the order of 10+ days of continuous Pi 5 compute, colliding with the
  guardrails just added: the load/generation timeout split
  (`OLLAMA_LOAD_TIMEOUT`, static 180 s with an adaptive baseline learned
  from recorded load-duration samples in `src/common/llm.py`), the
  `MAX_CONCURRENT_REPORT_WAITERS` cap (default 2) and hot-pending back-off
  in `src/common/ollama_lock.py`, `_abandon_ollama_generation()`'s
  orphaned-generation cleanup, and the centralized `sanitize_prompt_text()`
  hygiene pass. Any regeneration schedule would have to run
  `priority="report"` inside those guardrails and would still contend with
  every production brief. This is a large part of why substitution is the
  recommendation, not just a preference.
- **The mount cutover is the highest-risk single step** — it is also the
  step with the cleanest rollback (config-only, §3 Phase 4). The residual
  risk is discovering a hidden dependency on the live-dir mount (runner-demo
  is the suspect); the phase order (verify, demo-api first, runner-demo
  second) exists to surface that with the old config still one
  daemon-reload away.
- **Boundary-semantics change needs to be deliberate, not incidental.** The
  trailing anchor weakens "demo only ever shows five-week-old data" and
  strengthens "demo only ever shows scrubbed, promoted artifacts." That
  trade is the point of this plan, but it must be written down (docstring,
  compliance doc) and signed off, not slipped in — F6's own conclusion asked
  for exactly this kind of conscious sign-off.
- **Disk:** the sovereign file adds ≈1.5 GB on the same single btrfs volume
  as everything else — no quota isolation between live and demo storage is
  possible until the hardware stretch goal lands. Retention/prune keeps it
  bounded; the refresh report should log resulting file size the way
  recorder.py logs its own.

## 5. Open questions for operator review

1. **Brief scrubbing: text-substitution (recommended) or regeneration?**
   Recommend substitution because the demo's credibility rests on showing
   what the platform *actually produced*, the Ollama cost of regeneration
   is severe (§4), and substitution+verify is fail-closed. A middle option
   exists — substitution plus an LLM-assisted PII *detection* (not
   rewriting) pass as a third layer — worth considering later, not blocking.
2. **Refresh cadence.** Nightly 04:45 ET recommended (0–28 h lag). If a
   harder minimum-age floor is wanted (e.g. promote only data older than
   24 h → 24–52 h lag), say so — one-line change in `--refresh`.
3. **EP-advance briefs: include-with-strict-scrub (recommended) or exclude
   the type from the demo entirely?** They are the platform's most
   sensitive prose and its most impressive demo artifact. Recommend
   include, gated on the Phase 2 manual sample review going clean.
4. **`demo_access.db` relocation** to `/var/lib/corporatetraveldc-demo-state/`
   — recommend yes, in this pass: it's demo-owned state, and leaving it in
   the live directory forces demo-api to keep a live-dir mount, defeating
   Phase 4. The only real question is timing, and doing it any later means
   doing Phase 4 twice. (Decided yes, done 2026-08-26; ~~superseded by the
   2026-09-20 move onto Postgres~~ — that move put the demo's only database
   client on production and is reversed 2026-10-16, status note.)
5. **Default loop window**: keep `LOOP_DAYS=14` with the trailing anchor
   (recommended — profiles/tiers already handle longer windows), or widen
   the default to 28 to match the backfill span?

## 6. After approval

Next step is implementation of Phases 1–2 (rules module, pipeline, backfill
run, and the manual review gate), then the cutover phases in order — none of
which is covered by this document. Nothing changes on the running system
until this doc is approved; the demo continues serving the 07-09 anchored
loop, and `_live_conn()` remains live, until the operator signs off.
