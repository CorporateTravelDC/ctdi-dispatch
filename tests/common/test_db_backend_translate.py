"""
Pure unit tests for common.db_backend's SQL translation pipeline and the
ref_conn() cross-engine guard. No network/DB required for translate_sql()
itself; ref_conn() tests use a throwaway SQLite file (tmp_path), same as
the rest of this suite -- never postgres, never the real DB.

Postgres-pool-backed tests (pg_conn() against the real corporatetraveldc-
pgsql container, on a scratch database) live in
test_db_backend_postgres.py and skip themselves if postgres is
unreachable.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import db_backend


# ── translate_sql() ──────────────────────────────────────────────────────────


def test_positional_placeholder():
    assert db_backend.translate_sql("SELECT * FROM t WHERE a=? AND b=?") == \
        "SELECT * FROM t WHERE a=%s AND b=%s"


def test_named_placeholder():
    assert db_backend.translate_sql("UPDATE t SET a=:a WHERE id=:id") == \
        "UPDATE t SET a=%(a)s WHERE id=%(id)s"


def test_question_mark_inside_string_literal_is_not_a_placeholder():
    sql = "SELECT * FROM t WHERE note = 'is this a question?'"
    out = db_backend.translate_sql(sql)
    assert out == sql  # untouched -- the ? is inside a string literal


def test_colon_inside_string_literal_is_not_a_placeholder():
    sql = "SELECT * FROM t WHERE note = 'time is 10:30'"
    out = db_backend.translate_sql(sql)
    assert "%(30" not in out
    assert "10:30" in out


def test_double_quoted_identifier_is_preserved():
    sql = 'SELECT "a?b" FROM t WHERE x=?'
    out = db_backend.translate_sql(sql)
    assert '"a?b"' in out
    assert out.endswith("x=%s")


def test_percent_in_like_pattern_is_doubled():
    sql = "SELECT * FROM t WHERE evidence NOT LIKE '%\"flew\": true%'"
    out = db_backend.translate_sql(sql)
    assert "%%\"flew\": true%%" in out


def test_percent_doubling_survives_placeholder_translation_round_trip():
    """The real db_swim.py call sites: a LIKE pattern with literal % AND a
    ? placeholder in the same statement."""
    sql = "SELECT COUNT(*) FROM t WHERE detected_at > ? AND evidence NOT LIKE '%\"flew\": true%'"
    out = db_backend.translate_sql(sql)
    assert out == "SELECT COUNT(*) FROM t WHERE detected_at > %s AND evidence NOT LIKE '%%\"flew\": true%%'"


def test_insert_or_ignore_rewritten_to_on_conflict_do_nothing():
    sql = "INSERT OR IGNORE INTO osint_items (a,b) VALUES (?,?)"
    out = db_backend.translate_sql(sql)
    assert "INSERT INTO osint_items (a,b) VALUES (%s,%s)" in out
    assert "ON CONFLICT DO NOTHING" in out
    assert "OR IGNORE" not in out


def test_insert_or_replace_rewritten_to_on_conflict_do_update():
    sql = (
        "INSERT OR REPLACE INTO board_refresh_grace "
        "(old_token_hash, new_token, new_expires_at, grace_expires_at) "
        "VALUES (?, ?, ?, ?)"
    )
    out = db_backend.translate_sql(sql)
    assert "INSERT INTO board_refresh_grace (old_token_hash, new_token, new_expires_at, grace_expires_at)" in out
    assert "ON CONFLICT (old_token_hash) DO UPDATE SET" in out
    assert "new_token=EXCLUDED.new_token" in out
    assert "new_expires_at=EXCLUDED.new_expires_at" in out
    assert "grace_expires_at=EXCLUDED.grace_expires_at" in out
    assert "old_token_hash=EXCLUDED.old_token_hash" not in out  # conflict target, not a SET column


def test_strftime_now_rewritten_to_interval_math():
    sql = "SELECT * FROM t WHERE fetched_at >= strftime('%s','now',?)"
    out = db_backend.translate_sql(sql)
    assert "extract(epoch from (now() + (%s)::interval))" in out
    assert "strftime" not in out


def test_date_unixepoch_rewritten():
    sql = "SELECT date(fetched_at, 'unixepoch') FROM t"
    out = db_backend.translate_sql(sql)
    assert "(to_timestamp(fetched_at))::date" in out


def test_bare_unixepoch_rewritten():
    sql = "SELECT * FROM notams WHERE inserted_at > unixepoch()-3600"
    out = db_backend.translate_sql(sql)
    assert "extract(epoch from now())-3600" in out


def test_json_extract_rewritten_to_arrow_operator():
    sql = "SELECT json_extract(raw_json,'$.reason') AS reason FROM t"
    out = db_backend.translate_sql(sql)
    assert "(raw_json::json->>'reason') AS reason" in out


def test_double_colon_cast_from_our_own_rewrites_is_not_mistaken_for_a_placeholder():
    """Regression test: an earlier version of _replace_placeholders()
    mangled the `::type` casts introduced by _rewrite_sqlite_functions()
    (e.g. `(%s)::interval` -> `(%s):%(interval)s`) because it did not
    special-case `::` before matching a bare `:name`."""
    sql = "SELECT * FROM t WHERE fetched_at >= strftime('%s','now',?)"
    out = db_backend.translate_sql(sql)
    assert "::interval" in out
    assert ":%(interval)s" not in out
    assert "):%" not in out


def test_import_time_ambiguous_percent_guard_passes_on_real_modules():
    """db.py/db_swim.py contain zero '%'-formatted SQL statements as of
    2026-09-17 -- this must keep passing; a future accessor that builds a
    query with the % operator should fail this instead of silently
    reaching translate_sql()."""
    db_backend._validate_known_modules()  # raises ImportError on violation


def test_ambiguous_percent_formatting_is_detected():
    src = (
        "def f(conn, value):\n"
        "    conn.execute(\"SELECT * FROM t WHERE x=%s\" % (value,))\n"
    )
    violations = db_backend._scan_for_percent_formatted_sql(src, "fake_module.py")
    assert len(violations) == 1
    assert "fake_module.py:2" in violations[0]


# ── ref_conn() ────────────────────────────────────────────────────────────────


@pytest.fixture
def _ref_db(monkeypatch, tmp_path):
    """Point ref_conn() at a throwaway sqlite file, never the real DB, and
    make sure no connection leaks into the next test."""
    db_file = tmp_path / "ref-test.db"
    monkeypatch.setenv("DISPATCH_DB", str(db_file))
    db_backend.close_ref_thread_connection()
    yield db_file
    db_backend.close_ref_thread_connection()


# 2026-09-19: cifp_fixes/cifp_holds/faa_registry_meta moved from
# REFERENCE_TABLES to PG_TABLES (the 11-reference-table migration --
# docs/POSTGRES_MIGRATION.md sec2's JOIN exception). REFERENCE_TABLES is
# now empty; these tests' example tables are updated to gtfs_stops, one
# of that same doc section's named FUTURE reference-table candidates
# (GTFS static / UAS registry / MMSI registry) -- still genuinely
# hypothetical (not in PG_TABLES), so still exercises the same guard
# behavior these tests were written to prove.


def test_ref_conn_reads_and_writes_a_reference_table(_ref_db):
    with db_backend.ref_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS gtfs_stops (stop_id TEXT PRIMARY KEY, lat REAL)")
        c.execute("INSERT INTO gtfs_stops (stop_id, lat) VALUES (?, ?)", ("ABC", 38.9))
    with db_backend.ref_conn() as c:
        row = c.execute("SELECT * FROM gtfs_stops WHERE stop_id=?", ("ABC",)).fetchone()
        assert dict(row) == {"stop_id": "ABC", "lat": 38.9}


def test_ref_conn_raises_on_write_path_table_reference(_ref_db):
    with pytest.raises(ValueError, match="flight_events"):
        with db_backend.ref_conn() as c:
            c.execute("SELECT * FROM gtfs_stops JOIN flight_events ON 1=1")


def test_ref_conn_raises_on_write_path_table_alone(_ref_db):
    with pytest.raises(ValueError, match="watchlist_entries"):
        with db_backend.ref_conn() as c:
            c.execute("SELECT * FROM watchlist_entries")


def test_ref_conn_does_not_raise_for_reference_only_statement(_ref_db):
    with db_backend.ref_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS gtfs_meta (key TEXT PRIMARY KEY, value TEXT)")
        c.execute(
            "INSERT OR REPLACE INTO gtfs_meta (key, value) VALUES (?, ?)",
            ("last_full_import", "2026-09-17T00:00:00Z"),
        )  # ref_conn() never translates SQL -- this stays plain sqlite3 syntax


def test_ref_conn_rollback_on_exception(_ref_db):
    with db_backend.ref_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS gtfs_routes (id TEXT PRIMARY KEY)")
    try:
        with db_backend.ref_conn() as c:
            c.execute("INSERT INTO gtfs_routes (id) VALUES ('x')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db_backend.ref_conn() as c:
        rows = c.execute("SELECT * FROM gtfs_routes").fetchall()
    assert rows == []  # the insert was rolled back
