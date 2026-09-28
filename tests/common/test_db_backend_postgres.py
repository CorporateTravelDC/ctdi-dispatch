"""
Integration tests for common.db_backend's postgres-mode connection pool,
run against the REAL corporatetraveldc-pgsql container -- but only ever
against the scratch database corporatetraveldc_pgphase1_test, never the
production `corporatetraveldc` database (see _require_scratch_db() below,
which hard-asserts on the resolved database name before any test runs a
single statement).

Skips the whole module (not a failure) if postgres is unreachable or the
scratch database has not been created -- this suite must still be safely
runnable on a box without the container, or before the scratch DB setup
step below has been run:

    export PGPASSWORD=$(grep DISPATCH_PG_PASSWORD /etc/corporatetraveldc/dispatch-secrets.env | cut -d= -f2)
    python3 -c "
    import psycopg
    c = psycopg.connect('host=127.0.0.1 port=5432 dbname=corporatetraveldc user=dispatch password=$PGPASSWORD', autocommit=True)
    c.execute('DROP DATABASE IF EXISTS corporatetraveldc_pgphase1_test')
    c.execute('CREATE DATABASE corporatetraveldc_pgphase1_test')
    "
    python3 scripts/pg_migrate.py --database corporatetraveldc_pgphase1_test

This intentionally does NOT create the scratch database itself (tests
should not have CREATE DATABASE privilege exercised implicitly) -- it is
provisioned once, out of band, exactly like the above.
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import config, db_backend  # noqa: E402

SCRATCH_DB = "corporatetraveldc_pgphase1_test"


def _require_scratch_db(monkeypatch) -> None:
    """Every postgres test in this module MUST call this first. Hard stop
    if anything resolves to the real database name -- this is the same
    "the dangerous state is the default" lesson tests/conftest.py's
    _isolate_default_db_path fixture encodes for SQLite, applied here for
    Postgres: DISPATCH_PG_DB is NOT covered by that global fixture (it
    only patches common.db._db_path), so this module protects itself."""
    monkeypatch.setenv("DISPATCH_PG_DB", SCRATCH_DB)
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    resolved = config.get("DISPATCH_PG_DB", "corporatetraveldc")
    assert resolved == SCRATCH_DB, (
        f"refusing to run postgres tests against {resolved!r} -- expected "
        f"the scratch database {SCRATCH_DB!r}"
    )


def _postgres_reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        conninfo = db_backend._pg_conninfo().replace(  # noqa: SLF001
            f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}",
            f"dbname={SCRATCH_DB}",
        )
        with psycopg.connect(conninfo, connect_timeout=3) as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason=(
        f"corporatetraveldc-pgsql unreachable or scratch database "
        f"{SCRATCH_DB!r} does not exist -- see this module's docstring "
        f"to provision it"
    ),
)


@pytest.fixture(autouse=True)
def _pg_env(monkeypatch):
    _require_scratch_db(monkeypatch)
    db_backend.close_pool()
    yield
    db_backend.close_pool()


@pytest.fixture
def _clean_table():
    """feed_state is migration 0002's, always present after pg_migrate.py
    has run against the scratch DB -- used as a scratch table for these
    tests, cleaned up before and after."""
    with db_backend.pg_conn() as c:
        c.execute("DELETE FROM feed_state WHERE feed_name LIKE 'pgphase1-test-%'")
    yield
    with db_backend.pg_conn() as c:
        c.execute("DELETE FROM feed_state WHERE feed_name LIKE 'pgphase1-test-%'")


def test_pg_conn_insert_and_select_round_trip(_clean_table):
    name = f"pgphase1-test-{uuid.uuid4().hex[:8]}"
    with db_backend.pg_conn() as c:
        c.execute(
            "INSERT INTO feed_state (feed_name, fetched_at, error) VALUES (?, ?, ?)",
            (name, 123.5, None),
        )
    with db_backend.pg_conn() as c:
        row = c.execute(
            "SELECT * FROM feed_state WHERE feed_name=?", (name,)
        ).fetchone()
    assert row["feed_name"] == name
    assert row["fetched_at"] == 123.5
    assert row["error"] is None


def test_pg_conn_dict_row_supports_dict_and_key_access(_clean_table):
    name = f"pgphase1-test-{uuid.uuid4().hex[:8]}"
    with db_backend.pg_conn() as c:
        c.execute(
            "INSERT INTO feed_state (feed_name, fetched_at, consecutive_failures) VALUES (?, ?, ?)",
            (name, 1.0, 3),
        )
        row = c.execute("SELECT * FROM feed_state WHERE feed_name=?", (name,)).fetchone()
        as_dict = dict(row)
    assert as_dict["feed_name"] == name
    assert as_dict["consecutive_failures"] == 3
    assert row["consecutive_failures"] == 3


def test_pg_conn_rollback_on_exception(_clean_table):
    name = f"pgphase1-test-{uuid.uuid4().hex[:8]}"
    try:
        with db_backend.pg_conn() as c:
            c.execute(
                "INSERT INTO feed_state (feed_name, fetched_at) VALUES (?, ?)",
                (name, 1.0),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db_backend.pg_conn() as c:
        row = c.execute("SELECT * FROM feed_state WHERE feed_name=?", (name,)).fetchone()
    assert row is None  # rolled back, not committed


def test_pg_conn_named_placeholders_work(_clean_table):
    name = f"pgphase1-test-{uuid.uuid4().hex[:8]}"
    with db_backend.pg_conn() as c:
        c.execute(
            "INSERT INTO feed_state (feed_name, fetched_at) VALUES (:name, :ts)",
            {"name": name, "ts": 42.0},
        )
    with db_backend.pg_conn() as c:
        row = c.execute(
            "SELECT * FROM feed_state WHERE feed_name=:name", {"name": name}
        ).fetchone()
    assert row["fetched_at"] == 42.0


def test_pg_conn_insert_or_ignore_dedupes(_clean_table):
    """osint_items-shaped test against a simpler scratch table: same
    INSERT-OR-IGNORE dedupe contract the real accessor functions rely on
    (content_hash UNIQUE)."""
    with db_backend.pg_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS pgphase1_test_dedupe (k TEXT UNIQUE NOT NULL, v TEXT)")
        c.execute("DELETE FROM pgphase1_test_dedupe")
    with db_backend.pg_conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO pgphase1_test_dedupe (k, v) VALUES (?, ?)", ("k1", "first"))
        first_rowcount = cur.rowcount
    with db_backend.pg_conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO pgphase1_test_dedupe (k, v) VALUES (?, ?)", ("k1", "second"))
        second_rowcount = cur.rowcount
        row = c.execute("SELECT v FROM pgphase1_test_dedupe WHERE k=?", ("k1",)).fetchone()
    assert first_rowcount == 1
    assert second_rowcount == 0  # conflicted, silently ignored -- same as sqlite's rowcount contract
    assert row["v"] == "first"  # value from the FIRST insert, never overwritten


def test_pg_conn_insert_or_replace_upserts(_clean_table):
    token = f"pgphase1-test-{uuid.uuid4().hex[:8]}"
    with db_backend.pg_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO board_refresh_grace "
            "(old_token_hash, new_token, new_expires_at, grace_expires_at) VALUES (?, ?, ?, ?)",
            (token, "tokA", 100.0, 200.0),
        )
    with db_backend.pg_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO board_refresh_grace "
            "(old_token_hash, new_token, new_expires_at, grace_expires_at) VALUES (?, ?, ?, ?)",
            (token, "tokB", 300.0, 400.0),
        )
        row = c.execute(
            "SELECT * FROM board_refresh_grace WHERE old_token_hash=?", (token,)
        ).fetchone()
    assert row["new_token"] == "tokB"  # replaced, not duplicated
    assert row["new_expires_at"] == 300.0
    with db_backend.pg_conn() as c:
        c.execute("DELETE FROM board_refresh_grace WHERE old_token_hash=?", (token,))


def test_pool_max_size_respects_env(monkeypatch):
    monkeypatch.setenv("DISPATCH_PG_POOL_MAX", "2")
    db_backend.close_pool()
    pool = db_backend._get_pool()  # noqa: SLF001
    assert pool.max_size == 2


def test_bare_host_tcp_fallback_used_for_this_test_run(monkeypatch):
    """This test process IS a bare-host tool (a pytest run, not a
    container with the socket volume mounted) -- confirms the fallback
    documented in docs/POSTGRES_MIGRATION.md §3.1 actually engages rather
    than silently failing to connect. Explicitly forces the container-side
    socket-directory path rather than relying on this box's own
    DISPATCH_PG_HOST value, so the test means the same thing anywhere."""
    monkeypatch.setenv("DISPATCH_PG_HOST", "/var/run/postgresql")
    assert db_backend._pg_host() == "127.0.0.1"  # noqa: SLF001
