"""
tests/conftest.py -- global, unconditional safety net for the WHOLE test
suite. Applies to every test in this repo, no opt-in required.

CRITICAL CONTEXT (2026-09-05 real incident): this repo has no separate
dev/staging box -- the Pi this runs on IS the live production dispatch
system, and every config default in common/config.py points at REAL
production infrastructure: DISPATCH_DB's default IS the real
corporatetraveldc.db path, and NTFY_URL's default (http://localhost:8080)
IS the real ntfy server, reachable from a bare host process since the
ntfy container publishes that port to the host. There is no environment
variable that needs to be SET for a test to hit production -- the
DANGEROUS state is the default, unset one, which is exactly what a
freshly-opened shell has.

Confirmed live: running the test suite directly on this box sent REAL
ntfy push notifications to the operator's phone -- fake test flights
(UAL123, AAL5265) appeared as real-looking flight alerts, because
neither a new test file nor several pre-existing ones (added_by='test'
rows had been sitting in the live watchlist_entries table since
2026-08-27/08-30, from some prior session) isolated db._db_path or the
ntfy send path, and the "obviously safe-looking" defaults were not
actually safe on this specific box. Real, live traffic sharing those
same flight numbers matched the stale test watchlist rows and fired for
real. The stale rows were deleted from production the same day this was
found; this conftest is the fix so it can't happen again.

These fixtures are intentionally unconditional (autouse=True, no marker
needed) and apply BEFORE any test module's own setup runs. A test that
wants to verify real send()/DB-path behavior mocks it itself as before
(e.g. tests/common/test_ntfy_push_*.py already does
`patch("common.ntfy_push.requests.post", ...)`) -- that mock simply
overrides these defaults for the scope of its own `with`/fixture, exactly
as it did before this file existed; none of the existing test suite was
ever relying on reaching real production infrastructure.

2026-10-03 -- SECOND REAL INCIDENT, same class: everything above only
isolated the SQLite path. The platform cut over to Postgres on
2026-09-19 (DISPATCH_DB_BACKEND=postgres in dispatch.env, which
common.config loads into os.environ at import), and
common.db_backend._pg_host() falls back from the missing
/var/run/postgresql socket to 127.0.0.1 TCP for bare-host processes --
so a pytest run with dispatch.env sourced connected straight to the
live corporatetraveldc database. Confirmed: psycopg UniqueViolation on
trigger_log_pkey, 115 InFailedSqlTransaction cascades from a shared
poisoned connection, and a test-shaped contaminant row (trigger_log
id='t1', 2026-09-20) left behind by an earlier run. The "102 pre-existing
test failures" number was produced the same way. Two layers below:
(1) DISPATCH_DB_BACKEND is forced to sqlite before common.config can
load the env file (it only sets keys not already present) and again
per-test; (2) a TRIPWIRE replaces every Postgres entry point in
db_backend so anything that still reaches for a pool fails the test
loudly instead of touching production.
"""
import os

# Layer 1a: must run before ANY `import common.config` in the test process.
# config._load_env_file() only populates keys that are not already set, so
# claiming this one first means dispatch.env's "postgres" never lands.
os.environ["DISPATCH_DB_BACKEND"] = "sqlite"

import pytest


class ProductionDatabaseTripwire(RuntimeError):
    """Raised when test code reaches a Postgres entry point. Tests run on
    an isolated SQLite file ONLY; see the 2026-10-03 note in the module
    docstring. If a test genuinely needs Postgres semantics, give it a
    throwaway database and monkeypatch these entry points itself."""


# The ONE database name Postgres-integration tests may touch. Both modules
# that carry the marker below already hard-assert on it themselves
# (tests/common/test_db_backend_postgres.py::_require_scratch_db,
# tests/scripts/test_pg_migrate.py); this is the global backstop.
POSTGRES_SCRATCH_DB = "corporatetraveldc_pgphase1_test"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "postgres_scratch: Postgres integration test; runs ONLY against the "
        f"out-of-band scratch database {POSTGRES_SCRATCH_DB!r} (never the live "
        "db) and only when that database is reachable -- otherwise skipped.")


@pytest.fixture(autouse=True)
def _force_sqlite_backend_and_trip_on_postgres(monkeypatch, request):
    """Layer 1b + layer 2: pin the backend to sqlite for every test and
    make any Postgres pool/connection attempt fail loudly.

    2026-10-03: tests marked `postgres_scratch` are the deliberate
    exception -- they exist to exercise the Postgres backend and the
    migration runner, and they already pin DISPATCH_PG_DB to the scratch
    database. For those the tripwire is replaced by a conninfo guard that
    refuses any database name other than the scratch one, so "real
    Postgres, never production" holds for them too."""
    import common.db_backend as db_backend

    if request.node.get_closest_marker("postgres_scratch"):
        # Resolve the scratch db directly (config.get reads the environment
        # first), so a fixture that templates its conninfo from
        # _pg_conninfo() gets the scratch name without string surgery.
        monkeypatch.setenv("DISPATCH_PG_DB", POSTGRES_SCRATCH_DB)
        real_conninfo = db_backend._pg_conninfo

        def _guarded_conninfo():
            ci = real_conninfo()
            if f"dbname={POSTGRES_SCRATCH_DB}" not in ci:
                raise ProductionDatabaseTripwire(
                    "postgres_scratch test resolved a database other than "
                    f"{POSTGRES_SCRATCH_DB!r} -- refusing (tests/conftest.py)")
            return ci

        monkeypatch.setattr(db_backend, "_pg_conninfo", _guarded_conninfo)
        monkeypatch.setattr(db_backend, "_pool", None, raising=False)
        yield
        # Never leave a scratch pool behind for the next (sqlite) test.
        try:
            pool = getattr(db_backend, "_pool", None)
            if pool is not None:
                pool.close()
        except Exception:
            pass
        monkeypatch.setattr(db_backend, "_pool", None, raising=False)
        return

    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")

    def _trip(*_a, **_k):
        raise ProductionDatabaseTripwire(
            "test reached a Postgres entry point in common.db_backend -- the "
            "suite runs on isolated SQLite only (tests/conftest.py, 2026-10-03)")

    monkeypatch.setattr(db_backend, "backend", lambda: "sqlite")
    for name in ("_build_pool", "_get_pool", "pg_conn", "_pg_conninfo"):
        if hasattr(db_backend, name):
            monkeypatch.setattr(db_backend, name, _trip)
    monkeypatch.setattr(db_backend, "_pool", None, raising=False)
    yield


@pytest.fixture(autouse=True)
def _block_real_ntfy_sends(monkeypatch):
    """Hard block on the actual HTTP call ntfy_push.send() makes --
    regardless of what NTFY_URL/NTFY_FALLBACK_URL/NTFY_TOKEN resolve to,
    no test can ever reach a real ntfy server through this call site."""
    import common.ntfy_push as ntfy_push

    class _SafeFakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

    def _safe_post(*args, **kwargs):
        return _SafeFakeResponse()

    monkeypatch.setattr(ntfy_push.requests, "post", _safe_post)


@pytest.fixture(autouse=True)
def _isolate_default_db_path(monkeypatch, tmp_path):
    """Every test gets an isolated DB file by default. db._db_path() never
    resolves to the real corporatetraveldc.db unless a test explicitly
    re-monkeypatches it -- no test in this suite should ever need to.

    2026-10-03: common.db caches ONE sqlite connection per thread
    (db._local.conn, see _open_connection/close_thread_connection). It is
    opened against whatever _db_path() returned the FIRST time this thread
    touched the DB and never re-resolved, so swapping _db_path per test
    (this fixture, and the many tests that swap it themselves) left every
    later test reading/writing the previous test's file: counts
    accumulated ("assert 8 == 1"), freshly-initialised tables were "no
    such table" because the DDL ran on the new file while queries went to
    the old one. 40+ of the "pre-existing failures" were this. Dropping
    the cached connection before and after each test makes _db_path()
    authoritative again."""
    import common.db as db
    db.close_thread_connection()
    fallback_db = tmp_path / "conftest-default.db"
    monkeypatch.setattr(db, "_db_path", lambda: fallback_db)
    yield
    db.close_thread_connection()


@pytest.fixture(autouse=True)
def _isolate_state_dir(monkeypatch, tmp_path):
    """common.config.state_dir() (DISPATCH_STATE_DIR) backs several
    real-file writers besides the DB -- common.push_dedup.PushDedup's
    pusher-{name}-dedup.json files among them (confirmed live: a fake
    "watch-entry-1"/"watch-entry-2" test key ended up written into the
    real production pusher-tbfm_watchlist-dedup.json during this same
    incident). Redirected here so any such writer defaults to a tmp dir
    instead of /var/lib/corporatetraveldc."""
    import common.config as config
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "state_dir", lambda: str(state_dir))
