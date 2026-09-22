"""
Backend-selecting database access layer -- Postgres migration Phase 1.

See docs/POSTGRES_MIGRATION.md §4 (Phase 1) for the plan this implements,
and §3.1 for why a bare unix-socket-path host means "fall back to TCP
loopback" instead of "fail" (a podman 5.8 pasta quirk already solved in
Phase 0, not re-derived here).

DISPATCH_DB_BACKEND=sqlite (the default, and the only thing this module
changes when it is active: NOTHING) -- common.db.conn() keeps using its
existing thread-local sqlite3 connection exactly as it does today. This
module is not even imported unless something asks for it.

DISPATCH_DB_BACKEND=postgres -- common.db.conn() delegates to pg_conn()
below: one psycopg_pool.ConnectionPool per process, dict_row rows,
autocommit off, the same "with conn() as c: ... commit on success,
rollback on exception" contract db.py's sqlite path has always had
(psycopg_pool's own pool.connection() context manager already provides
exactly that, so pg_conn() just wraps it for SQL translation rather than
reimplementing it).

Why accessor bodies do not need to change for the postgres path (per the
Phase 1 mandate): every statement string that reaches conn().execute(...)
is run through translate_sql() first, which:
  - doubles bare `%` (LIKE '%foo%' etc.) -- psycopg needs `%%` for a
    literal `%` regardless of SQL quoting, sqlite text never legitimately
    contains an unescaped `%s`/`%(name)s` so this is always safe;
  - rewrites the two SQLite date/JSON functions actually used in current
    queries (`strftime('%s','now',?)`, `date(x,'unixepoch')`,
    `json_extract(x,'$.key')`) to their Postgres equivalents;
  - rewrites `INSERT OR IGNORE INTO` / `INSERT OR REPLACE INTO` (SQLite
    syntax Postgres has no equivalent keyword for at all -- placeholder
    translation alone cannot fix these, so the shim does it) to
    `... ON CONFLICT DO NOTHING` / `... ON CONFLICT (<first col>) DO
    UPDATE SET ...`;
  - translates `?` -> `%s` (positional) and `:name` -> `%(name)s` (named,
    psycopg's native pyformat style) outside of quoted literals/comments,
    so tuple and dict params both keep working unchanged.

ref_conn() is a second, independent, SQLite-ONLY handle for the 11
reference tables (docs/POSTGRES_MIGRATION.md §2) -- always SQLite, no
matter what DISPATCH_DB_BACKEND says, because those tables never move.
Its statement wrapper raises if a statement names any write-path (PG-
bound) table, so a query cannot silently keep depending on the two table
families still physically living in the same SQLite file today.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable

from common import config

log = logging.getLogger(__name__)


# ── Table inventory (docs/POSTGRES_MIGRATION.md §2) ─────────────────────────
#
# Verified against src/common/db.py + src/common/db_swim.py on 2026-09-17
# (every CREATE TABLE / ALTER TABLE / CREATE INDEX ... ON target, including
# the three lazy `_ensure_*()` inline-DDL functions the doc's "19
# executescript blocks" inventory did not cover -- see pg_schema/0047 and
# 0048's headers). This was 65 tables, matching the doc's original "65 of
# 76" count, with an 11-table reference-only list held back deliberately.
#
# 2026-09-19: the 11 reference tables moved too, under §2's own stated
# exception -- "a reference table that a write-path query needs to JOIN
# gets copied into Postgres too." Geometric reasoning's Phase 0 JOINs
# cifp_fixes/cifp_procedure_legs against flight_events (fix-proximity) and
# faa_aircraft_registry/opensky_aircraft_registry (airframe correlation) --
# that JOIN materializing, not a policy override. Schema: pg_schema/0052-
# 0054. REFERENCE_TABLES is left as an empty frozenset (not deleted) --
# the mechanism (ref_conn()/_guard_cross_engine() below) stays live for
# genuinely future SQLite-only reference sets (GTFS static, UAS registry,
# MMSI registry -- named as likely candidates in §2) that don't get
# JOINed against the write path.

REFERENCE_TABLES: frozenset[str] = frozenset()

PG_TABLES: frozenset[str] = frozenset({
    "acars_messages", "amtrak_status", "approval_requests", "atcscc_opsplan",
    "audit_log", "auth_tokens", "bandwidth_priority_state",
    "board_enroll_nonces", "board_messages", "board_presence",
    "board_refresh_grace", "board_tokens", "brief_archive",
    "cifp_fixes", "cifp_holds", "cifp_meta", "cifp_procedure_legs",
    "codeshare_map",
    "convective_sigmet_archive", "cps_scores", "datis_snapshots",
    "faa_aircraft_reference", "faa_aircraft_registry", "faa_ladd_aircraft",
    "faa_registry_meta",
    "fdps_destination_changes", "fdps_diversion_continuations",
    "fdps_route_versions", "feed_data_usage", "feed_state", "flight_events",
    "flight_ooooi_times", "hot_alerts", "international_aviation_feed",
    "itws_alerts", "local_aircraft", "local_airspace_alerts",
    "metar_history", "metar_snapshot", "nas_programs", "notams",
    "nws_alerts", "nws_forecast", "ops_plan",
    "opensky_aircraft_registry", "opensky_registry_meta",
    "osint_items", "osint_scopes",
    "pull_path_status", "runsheet", "session_grants", "stdds_rvr",
    "stdds_rvr_history", "stdds_safety_status", "stdds_safety_status_history",
    "surface_movement_events", "surface_tracks", "swim_alerts",
    "tbfm_sequences", "tdes_departure_events", "tdls_messages",
    "terminal_tracks", "tfms_edct_slots", "tfms_param_delay_stats",
    "tfms_plan_removals", "tfms_reroutes", "tfrs", "train_events",
    "trigger_log", "ustrains_departures", "vessel_events",
    "watchlist_entries", "watchlist_history", "watchlist_sessions",
    "webhook_events", "wpc_discussions",
})


def backend() -> str:
    """DISPATCH_DB_BACKEND, defaulting to sqlite. This is an operator
    switch in /etc/corporatetraveldc/dispatch.env -- nothing in this
    codebase ever writes it."""
    return config.get("DISPATCH_DB_BACKEND", "sqlite").strip().lower()


# ── SQL translation ──────────────────────────────────────────────────────────

_INSERT_OR_IGNORE_RE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_INSERT_OR_REPLACE_RE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_STRFTIME_NOW_RE = re.compile(
    r"strftime\(\s*'%s'\s*,\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE
)
_DATE_UNIXEPOCH_RE = re.compile(
    r"\bdate\(\s*([A-Za-z0-9_.]+)\s*,\s*'unixepoch'\s*\)", re.IGNORECASE
)
_JSON_EXTRACT_RE = re.compile(
    r"json_extract\(\s*([A-Za-z0-9_.]+)\s*,\s*'\$\.([A-Za-z0-9_]+)'\s*\)",
    re.IGNORECASE,
)
_BARE_UNIXEPOCH_RE = re.compile(r"\bunixepoch\(\)", re.IGNORECASE)
# 2026-09-19: found live, actively flooding the postgres log (hundreds of
# errors/sec under real ingest traffic) -- get_watchlist_entries(),
# sweep_expired_watchlist_entries(), and a fourth site at db.py:4831 all
# wrap a TEXT timestamp column in SQLite's datetime() to normalize two
# differently-formatted ISO-ish strings before comparing them (2026-07-28
# fix, see get_watchlist_entries' docstring for the original root cause).
# Postgres has no datetime() function at all -- confirmed live:
# ERROR: function datetime(text) does not exist. The columns these wrap
# (auto_remove_at, confirmed live as `text` in the live pg schema) stay
# TEXT on Postgres too (never converted to a native timestamp type by the
# migration), so the fix is the same normalize-before-compare intent,
# expressed as a cast instead of a function call: datetime(?)/datetime(x)
# -> (?)::timestamptz/(x)::timestamptz. timestamptz's parser accepts the
# 'T'-separated, 'Z'-suffixed ISO format this codebase writes
# (time.strftime("%Y-%m-%dT%H:%M:%SZ", ...)) natively.
_DATETIME_CAST_RE = re.compile(r"\bdatetime\(\s*([^()]+?)\s*\)", re.IGNORECASE)
# 2026-09-19: found live in the same flood -- `col IS ?` is SQLite's
# NULL-safe parameterized equality (a bound NULL correctly matches via
# IS rather than needing separate NULL-vs-value branches). Postgres's IS
# only accepts the fixed keywords NULL/NOT NULL/TRUE/FALSE/UNKNOWN, never
# a parameter placeholder -- confirmed live: syntax error at or near "$3"
# (db_swim.py's tfms_plan_removals dedup check, db.py's codeshare lookup).
# `IS NOT DISTINCT FROM ?` is Postgres's (and standard SQL's) direct
# equivalent NULL-safe equality operator -- same semantics, not a
# workaround. Placeholder is still literal `?` here; _replace_placeholders
# runs after this in translate_sql()'s pipeline.
_IS_PARAM_RE = re.compile(r"\bIS\s+\?", re.IGNORECASE)


def _rewrite_sqlite_functions(sql: str) -> str:
    """SQLite date/JSON functions actually used in current DML queries
    (verified against src/common/db.py, src/poller/fetchers/nws.py,
    src/poller/skills/nms_v240_{baseline_capture,post_deploy_check}.py
    2026-09-17: 3x strftime('%s','now',?), 1x date(x,'unixepoch'), 2x
    json_extract, 3x bare unixepoch() used directly in a WHERE/INSERT
    expression rather than inside strftime() -- the doc's Appendix A
    counts "34 unixepoch()" but that tally is DDL DEFAULT-clause usage
    only (see pg_schema/*.sql, translated at migration-generation time,
    not here); these 3 DML sites are additional and were not in that
    count). 2026-09-19 adds datetime(x) -> (x)::timestamptz (see
    _DATETIME_CAST_RE above). Order matters: this runs BEFORE
    percent-doubling, since it matches literal '%s' SQLite syntax; and the
    bare-unixepoch() rewrite runs LAST so it does not also rewrite the
    unixepoch() text inside a strftime(...) call already handled above."""
    sql = _STRFTIME_NOW_RE.sub(
        "extract(epoch from (now() + (?)::interval))", sql
    )
    sql = _DATE_UNIXEPOCH_RE.sub(r"(to_timestamp(\1))::date", sql)
    sql = _JSON_EXTRACT_RE.sub(r"(\1::json->>'\2')", sql)
    sql = _DATETIME_CAST_RE.sub(r"(\1)::timestamptz", sql)
    sql = _IS_PARAM_RE.sub("IS NOT DISTINCT FROM ?", sql)
    sql = _BARE_UNIXEPOCH_RE.sub("extract(epoch from now())", sql)
    return sql


def _rewrite_sqlite_insert_dialect(sql: str) -> str:
    """INSERT OR IGNORE / INSERT OR REPLACE have no Postgres keyword
    equivalent at all -- `?`/`:name` placeholder translation cannot fix
    this, so the shim rewrites the statement text itself.

    INSERT OR IGNORE -> ON CONFLICT DO NOTHING (no target needed; verified
    against every current PG-bound call site -- tdes_departure_events,
    fdps_diversion_continuations, convective_sigmet_archive, osint_items,
    bandwidth_priority_state -- each already has exactly one PK/UNIQUE
    constraint an unqualified DO NOTHING correctly protects).

    INSERT OR REPLACE -> ON CONFLICT (<first listed column>) DO UPDATE SET
    the rest. Verified against the one current PG-bound call site
    (board_refresh_grace: PRIMARY KEY old_token_hash is exactly the first
    column in its INSERT's explicit column list). The other 5 raw
    INSERT-OR-REPLACE sites in db.py target reference tables (faa_*,
    opensky_*, cifp_*) and never reach this function -- ref_conn() always
    executes on plain sqlite3, unmodified."""
    if _INSERT_OR_IGNORE_RE.search(sql):
        sql = _INSERT_OR_IGNORE_RE.sub("INSERT INTO", sql)
        return sql.rstrip() + "\nON CONFLICT DO NOTHING"

    m = _INSERT_OR_REPLACE_RE.search(sql)
    if m:
        table, cols_raw = m.group(1), m.group(2)
        cols = [c.strip() for c in cols_raw.split(",") if c.strip()]
        if not cols:
            raise ValueError(
                f"INSERT OR REPLACE INTO {table}: could not parse a column "
                "list to build an ON CONFLICT target -- fix the statement "
                "or extend _rewrite_sqlite_insert_dialect()."
            )
        conflict_col = cols[0]
        set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols[1:])
        sql = _INSERT_OR_REPLACE_RE.sub(
            f"INSERT INTO {table} ({cols_raw})", sql, count=1
        )
        tail = f"\nON CONFLICT ({conflict_col})"
        tail += f" DO UPDATE SET {set_clause}" if set_clause else " DO NOTHING"
        return sql.rstrip() + tail

    return sql


def _double_percent(sql: str) -> str:
    """Every literal `%` doubled, unconditionally, wherever it appears
    (inside or outside quoted literals -- psycopg's placeholder scanner
    needs the escape regardless of SQL quoting). Safe because raw SQLite
    query text in this codebase never legitimately contains an unescaped
    `%s`/`%(name)s`/`%d`-shaped token -- SQLite has no such placeholder
    style -- so there is nothing for this to collide with. Run AFTER the
    SQLite-function and insert-dialect rewrites (which match on literal
    '%s' text) and BEFORE the ?/: placeholder pass."""
    return sql.replace("%", "%%")


def _replace_placeholders(sql: str) -> str:
    """Quote/comment-aware `?` -> `%s`, `:name` -> `%(name)s`. Never
    touches text inside '...' string literals (with '' as an escaped
    quote), "..." identifiers, `--` line comments, or `/* */` block
    comments, so a literal '?' or ':name'-shaped substring inside a
    string value is never mistaken for a placeholder."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(sql[i:j])
            i = j
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(sql[i:j])
            i = j
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        if ch == ":" and i + 1 < n and sql[i + 1] == ":":
            # Postgres `::` type-cast operator (introduced by our own
            # _rewrite_sqlite_functions() rewrites -- SQLite has no `::`
            # syntax so original query text never contains this). Must be
            # passed through literally, never mistaken for the start of a
            # `:name` placeholder.
            out.append("::")
            i += 2
            continue
        if ch == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(f"%({sql[i + 1:j]})s")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def translate_sql(sql: str) -> str:
    """The full postgres-mode statement translation pipeline. Order is
    load-bearing -- see each helper's docstring for why."""
    sql = _rewrite_sqlite_functions(sql)
    sql = _rewrite_sqlite_insert_dialect(sql)
    sql = _double_percent(sql)
    sql = _replace_placeholders(sql)
    return sql


# ── Import-time ambiguous-%-formatting guard ─────────────────────────────────
#
# translate_sql()'s blind `%` -> `%%` doubling makes every LITERAL `%`
# character safe to send to psycopg regardless of where it came from. The
# one thing it cannot make safe is a statement that was itself ASSEMBLED
# with Python's old-style `%` string-formatting operator (e.g.
# `"...WHERE x=%s" % (value,)`) -- that is a SQL-injection-shaped bug
# class independent of backend, and by the time such a string reaches
# translate_sql() the %s is long gone (replaced with the interpolated
# value), so no runtime check can catch it. This module statically scans
# common.db / common.db_swim's source for exactly that shape at import
# time and refuses to import if it finds one.

def _scan_for_percent_formatted_sql(module_source: str, filename: str) -> list[str]:
    """Return a list of human-readable violation descriptions: any
    `.execute(...)`/`.executemany(...)` call whose first argument is a
    `<str> % <...>` BinOp -- i.e. the query text itself was built with
    old-style Python percent-formatting rather than a bind parameter."""
    violations = []
    tree = ast.parse(module_source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("execute", "executemany"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod):
            violations.append(
                f"{filename}:{node.lineno}: {node.func.attr}() called with a "
                "'%'-formatted SQL string -- ambiguous under the postgres "
                "shim's own '%' placeholder syntax and a SQL-injection-"
                "shaped pattern independent of backend. Use a ?/: bind "
                "parameter instead."
            )
    return violations


def _validate_known_modules() -> None:
    import inspect

    from common import db as _db
    from common import db_swim as _db_swim

    violations: list[str] = []
    for mod in (_db, _db_swim):
        try:
            src = inspect.getsource(mod)
        except (OSError, TypeError):
            continue
        violations += _scan_for_percent_formatted_sql(src, mod.__file__ or mod.__name__)
    if violations:
        raise ImportError(
            "db_backend: ambiguous '%'-formatted SQL found at import time:\n"
            + "\n".join(violations)
        )


# ── Connection pool (postgres mode) ──────────────────────────────────────────

_pool = None
_pool_lock = threading.Lock()


def _pg_host() -> str:
    """Bare-host fallback (docs/POSTGRES_MIGRATION.md §3.1): app
    CONTAINERS reach Postgres over the unix socket volume mounted at
    DISPATCH_PG_HOST (a directory path); a bare-host process (migration
    scripts, an interactive shell, a cron job run directly on the Pi) has
    no such mount, so if the configured host looks like a path and that
    path does not exist, fall back to TCP loopback instead of failing to
    connect at all. This is a one-time check at pool-build time, not
    per-connection -- the answer cannot change during a process's life."""
    host = config.get("DISPATCH_PG_HOST", "/var/run/postgresql")
    if host.startswith("/") and not Path(host).exists():
        log.info(
            "db_backend: DISPATCH_PG_HOST=%s does not exist on this host -- "
            "falling back to 127.0.0.1 (bare-host TCP loopback path, see "
            "docs/POSTGRES_MIGRATION.md §3.1)",
            host,
        )
        return "127.0.0.1"
    return host


def _pg_app_name() -> str:
    """2026-09-20: every connection this platform opens was showing up in
    pg_stat_activity with an empty application_name -- confirmed live,
    40/60 connections in use with zero attributable to a service, a real
    diagnosability gap during the PoolTimeout investigation.

    First attempt used PODMAN_SYSTEMD_UNIT, assuming podman injects it
    into the container's own process environment because it appears on
    every container's journal log lines -- wrong, confirmed live
    (`podman exec ... env` has no such var): that's podman/journald's own
    logging metadata, not something exported into the process. Real fix:
    every quadlet now sets Environment=CORPORATETRAVELDC_UNIT=%N, a
    systemd specifier that expands to the unit's own name at generation
    time (confirmed live) -- this is what actually reaches the process.

    Second bug in the original fallback, also found live: it used
    os.path.basename(sys.argv[0]), which collapses web/poller/ingest's
    main.py to the identical "main.py" (all three are literally named
    that, just in different directories) -- and os.getpid() is always 1
    inside a container's own PID namespace, so the pid suffix added zero
    differentiation either. Fixed to use the fuller relative path for
    whatever host/bare-script case still hits this fallback."""
    unit = os.environ.get("CORPORATETRAVELDC_UNIT")
    if unit:
        return unit
    argv0 = sys.argv[0] or "python"
    return "/".join(argv0.replace("\\", "/").split("/")[-3:])


def _pg_libpq_quote(value: str) -> str:
    """libpq keyword=value conninfo format: wrap in single quotes and
    escape any embedded backslash/quote -- needed since application_name
    can contain a literal '.service' (not special) but this keeps it
    correct for any value, not just the ones seen live."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _pg_conninfo() -> str:
    host = _pg_host()
    port = config.get("DISPATCH_PG_PORT", "5432")
    dbname = config.get("DISPATCH_PG_DB", "corporatetraveldc")
    user = config.get("DISPATCH_PG_USER", "dispatch")
    password = config.get("DISPATCH_PG_PASSWORD", "")
    parts = [f"host={host}", f"port={port}", f"dbname={dbname}", f"user={user}"]
    if password:
        parts.append(f"password={password}")
    parts.append(f"application_name={_pg_libpq_quote(_pg_app_name())}")
    return " ".join(parts)


def _pool_max_size() -> int:
    """2026-09-20 (operator directive, real incident): raised 4->8 platform-
    wide. web hit psycopg_pool.PoolTimeout twice under concurrent request
    bursts with only 4 slots to go around -- that's a per-process pool
    exhaustion (psycopg_pool's own client-side error, never reaches
    Postgres's connection-acceptance layer), not a Postgres-side "too many
    clients" condition, so the fix is sizing each process's own pool
    larger, not just Postgres's global max_connections. Applied uniformly
    (not just web) so every service has equivalent headroom. Paired with
    max_connections=60->100 (config/postgresql.conf) as the global
    backstop: real worst-case math (see CLAUDE.md / second-brain
    checkpoint note) stays within the container's --memory-swap=3072m
    ceiling even if every connection simultaneously held work_mem --
    degrades gracefully into swap rather than OOM-killing.
    The previous docstring here ("ingest containers set
    DISPATCH_PG_POOL_MAX=2 in their own env") was aspirational and never
    actually wired up anywhere -- confirmed live, no such override exists
    in any quadlet or env file. Removed rather than left stale."""
    return int(config.get("DISPATCH_PG_POOL_MAX", "8"))


def _reset_pooled_connection(conn) -> None:
    """2026-09-19 (operator directive, post-cutover): before a connection
    goes back in the pool for the NEXT caller, force it to a genuinely
    clean slate rather than trust whatever state the previous caller left
    it in. Confirmed live: /healthz and other endpoints intermittently
    500'd/succeeded across repeated identical requests in a pattern that
    tracked which of the pool's few connections got handed out, not
    anything about the request itself -- every pg_stat_activity check
    during a failure window showed every connection cleanly idle (no
    stuck transaction), so whatever state was leaking was Python-side
    (row_factory/cursor description or similar), not a lingering SQL
    transaction. `check=` below (verify before handing out) and this
    `reset=` (force clean before returning) together are psycopg_pool's
    own purpose-built mechanism for exactly this: a suspect connection
    gets discarded and replaced rather than silently reused, so any
    caller's unit of work is effectively one-shot against a verified-
    fresh connection instead of trusting long-lived pooled state. This is
    the new STANDARD, not a one-off for this pool -- any future
    DISPATCH_DB_BACKEND=postgres-style pool on this platform should carry
    the same check=/reset= pair unless there's a specific, stated reason
    a given database's pool can't (e.g. a reset step that's provably
    unsafe/too expensive for that connection's workload)."""
    try:
        conn.rollback()
    except Exception:
        pass


def _build_pool():
    import psycopg_pool
    from psycopg.rows import dict_row

    _validate_known_modules()
    return psycopg_pool.ConnectionPool(
        conninfo=_pg_conninfo(),
        min_size=1,
        max_size=max(1, _pool_max_size()),
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=True,
        check=psycopg_pool.ConnectionPool.check_connection,
        reset=_reset_pooled_connection,
    )


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool()
    return _pool


def close_pool() -> None:
    """Close and drop the process pool, if any. Tests use this between
    cases that need a fresh pool (e.g. after changing DISPATCH_PG_* env
    vars); production code never needs to call it."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
            _pool = None


class _TranslatingCursor:
    """Proxies a psycopg cursor, translating SQL on every execute."""

    __slots__ = ("_raw",)

    def __init__(self, raw):
        object.__setattr__(self, "_raw", raw)

    def execute(self, sql, params=None, **kw):
        return self._raw.execute(translate_sql(sql), params, **kw)

    def executemany(self, sql, params_seq, **kw):
        return self._raw.executemany(translate_sql(sql), params_seq, **kw)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)

    def __iter__(self):
        return iter(self._raw)


class _TranslatingConnection:
    """Proxies a pooled psycopg connection. `.execute()`/`.executemany()`
    (the shorthand db.py's accessor code universally uses, mirroring
    sqlite3.Connection's own shorthand -- verified zero `.cursor()` call
    sites in db.py/db_swim.py 2026-09-17) run SQL through translate_sql()
    first; everything else (commit/rollback/etc.) passes straight through
    to the real connection, which is what pool.connection() already
    commits-on-exit / rolls-back-on-exception for us."""

    __slots__ = ("_raw",)

    def __init__(self, raw):
        object.__setattr__(self, "_raw", raw)

    def execute(self, sql, params=None, **kw):
        return self._raw.execute(translate_sql(sql), params, **kw)

    def executemany(self, sql, params_seq, **kw):
        """2026-09-19: psycopg.Connection has no executemany() at all --
        unlike sqlite3.Connection (which has always had this shorthand)
        and unlike this class's own execute() (a real psycopg3 Connection
        convenience method), executemany() exists ONLY on Cursor. This
        was a latent bug: db.py has 7 call sites of `c.executemany(...)`
        (the accessor-code shorthand every caller uses, mirroring
        sqlite3.Connection's API -- see this class's own docstring) that
        would all have hit this exact AttributeError the first time any
        of them actually ran on Postgres. Found live via
        second_brain.semantic.compile.assign()'s first real compile_layer()
        run against Postgres. Fixed by opening a cursor explicitly --
        inherits this connection's row_factory (dict_row) by default, so
        callers see no behavior change."""
        with self._raw.cursor() as cur:
            return cur.executemany(translate_sql(sql), params_seq, **kw)

    def executescript(self, sql, *a, **kw):
        """No-op on Postgres. 2026-09-18: every common.db.init_db[_vN]()
        function (~44 of them) calls `with conn() as c: c.executescript(
        SCHEMA_VN)` unconditionally -- real, SQLite-only DDL -- and psycopg
        has no executescript() at all, so __getattr__ below forwarded this
        straight to the raw connection and crashed every single ingest/web/
        poller container on first startup after the cutover (confirmed
        live: AttributeError: 'Connection' object has no attribute
        'executescript', src/common/db.py:223). On Postgres the schema is
        already fully built by the 48 files in src/common/pg_schema/,
        applied and tracked by scripts/pg_migrate.py -- re-running SQLite's
        CREATE-TABLE DDL here would be both the wrong dialect and pure
        redundancy, never a real bootstrap step on this backend. Silently
        doing nothing is correct, not a swallowed error."""
        return None

    def cursor(self, *a, **kw):
        return _TranslatingCursor(self._raw.cursor(*a, **kw))

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)


@contextmanager
def pg_conn() -> Generator[_TranslatingConnection, None, None]:
    """Postgres counterpart to common.db.conn()'s sqlite contract:
    `with pg_conn() as c: ...` commits on normal exit, rolls back on
    exception -- provided by psycopg_pool.ConnectionPool.connection()
    itself, not reimplemented here."""
    pool = _get_pool()
    with pool.connection() as raw:
        yield _TranslatingConnection(raw)


# ── ref_conn(): SQLite-only handle for the 11 reference tables ──────────────

_ref_local = threading.local()


def _open_ref_connection() -> sqlite3.Connection:
    from common import config as _config

    p = Path(_config.db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def close_ref_thread_connection() -> None:
    c = getattr(_ref_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except sqlite3.Error:
            pass
        _ref_local.conn = None


_TABLE_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


def _guard_cross_engine(sql: str) -> None:
    """Raise if `sql` names any write-path (Postgres-bound) table. Today
    every reference table and every write-path table still physically
    live in the same SQLite file (nothing has moved yet -- this is Phase
    1, purely additive), so a query joining across both would silently
    "work" against stale/soon-to-be-removed data instead of failing loud.
    This is a textual guard (whole-word match against the known 65-table
    PG_TABLES set), deliberately over the actual SQL text rather than a
    query plan -- cheap, and every current ref-table call site is a
    simple single-table statement so false positives are not expected in
    practice; a genuine false positive (a column or alias that happens to
    share a PG table's name) should rename the alias rather than route
    around this guard."""
    found = {m.group(1) for m in _TABLE_NAME_RE.finditer(sql)} & PG_TABLES
    if found:
        raise ValueError(
            f"ref_conn(): statement references write-path table(s) "
            f"{sorted(found)} -- reference tables (SQLite-only) and "
            f"write-path tables (Postgres-bound) cannot share a query or "
            f"transaction. See docs/POSTGRES_MIGRATION.md §2."
        )


class _RefGuardedConnection:
    __slots__ = ("_raw",)

    def __init__(self, raw):
        object.__setattr__(self, "_raw", raw)

    def execute(self, sql, *a, **kw):
        _guard_cross_engine(sql)
        return self._raw.execute(sql, *a, **kw)

    def executemany(self, sql, *a, **kw):
        _guard_cross_engine(sql)
        return self._raw.executemany(sql, *a, **kw)

    def executescript(self, sql, *a, **kw):
        _guard_cross_engine(sql)
        return self._raw.executescript(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)


@contextmanager
def ref_conn() -> Generator[_RefGuardedConnection, None, None]:
    """SQLite-only handle for the 11 reference tables. Always SQLite,
    regardless of DISPATCH_DB_BACKEND -- reference tables never move (see
    module docstring). Independent thread-local connection from
    common.db.conn()'s, so using both in the same thread/request does not
    fight over one cached handle."""
    c = getattr(_ref_local, "conn", None)
    if c is None:
        c = _open_ref_connection()
        _ref_local.conn = c
    guarded = _RefGuardedConnection(c)
    try:
        yield guarded
        c.commit()
    except Exception:
        try:
            c.rollback()
        except sqlite3.Error:
            close_ref_thread_connection()
        raise


# ── sqlite3.Row positional-access audit (informational) ─────────────────────
#
# dict_row makes `dict(row)` and `row["col"]` keep working unchanged under
# postgres (a dict_row *is* a plain dict, so `dict(row)` is just a shallow
# copy). Positional `row[0]`-style access does NOT: a dict raises KeyError
# on an int key. Per the Phase 1 mandate accessor bodies are not rewritten
# in this phase (the postgres path is inert/unused in production), but
# every current positional-access site is listed here so Phase 2/3 do not
# have to rediscover them. Audited 2026-09-17 against src/common/db.py
# (src/common/db_swim.py has none; PRAGMA table_info() introspection
# sites, e.g. init_db_v46, are SQLite-schema-only code that never runs
# under postgres and are excluded):
#
#   db.py:1070, 1237, 1315, 3407, 3517, 3615, 4066, 6195, 6257
#       `row[0]` on a single-column SELECT's fetchone() result
#   db.py:2033, 2038                    `row[0] for row in c.execute(...)`
#   db.py:2052                          `{r[0] for r in rows}`
#   db.py:2154-2161                     `r[0]` through `r[5]` (feed_data_usage
#                                        usage-stats row unpacked positionally)
#
# None of these are reachable today (DISPATCH_DB_BACKEND defaults to and
# stays sqlite in every env file). Before any of these accessor functions
# is exercised under DISPATCH_DB_BACKEND=postgres, rewrite the access to
# `row["col"]` first.
