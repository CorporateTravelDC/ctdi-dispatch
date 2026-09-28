"""
Regression test for the 2026-08-26 C-17 fix (Opus blind review):
faa_lookup_by_hex()/opensky_lookup_by_hex()/opensky_lookup_by_registration()
wrap the indexed column in LOWER()/UPPER(REPLACE()), which SQLite can't
satisfy with a plain index on the bare column -- every call was a full
table scan. This confirms the new expression indexes (SCHEMA_V38) are
actually used by the query planner against a realistically-sized table,
not just present and unused (SQLite reasonably skips indexes on tiny/empty
tables, so an empty-table check would pass even with the index unused).
"""
import sqlite3
import tempfile
import time
from pathlib import Path

import common.db as db


def _seeded_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return tmp.name


def _query_plan(cursor, sql, params):
    cursor.row_factory = sqlite3.Row
    return [dict(r) for r in cursor.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()]


def test_faa_lookup_by_hex_uses_expression_index():
    path = _seeded_db()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(path)
        db.init_db_all()
        now = time.time()
        with db.conn() as c:
            c.executemany(
                "INSERT INTO faa_aircraft_registry (mode_s_hex, n_number, updated_at) VALUES (?, ?, ?)",
                [(f"{i:06x}", f"N{i}", now) for i in range(2000)],
            )
            c.execute("ANALYZE")
            plan = _query_plan(
                c,
                "SELECT * FROM faa_aircraft_registry WHERE LOWER(mode_s_hex)=?",
                ("abc123",),
            )
        assert any("idx_faa_registry_mode_s_hex_lower" in row["detail"] for row in plan), plan
    finally:
        db._db_path = orig_db_path
        Path(path).unlink(missing_ok=True)


def test_opensky_lookup_by_hex_uses_expression_index():
    path = _seeded_db()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(path)
        db.init_db_all()
        with db.conn() as c:
            c.executemany(
                "INSERT INTO opensky_aircraft_registry (icao24, registration, updated_at) VALUES (?, ?, ?)",
                [(f"{i:06x}", f"N{i}", time.time()) for i in range(2000)],
            )
            c.execute("ANALYZE")
            plan = _query_plan(
                c,
                "SELECT * FROM opensky_aircraft_registry WHERE LOWER(icao24)=?",
                ("abc123",),
            )
        assert any("idx_opensky_registry_icao24_lower" in row["detail"] for row in plan), plan
    finally:
        db._db_path = orig_db_path
        Path(path).unlink(missing_ok=True)


def test_opensky_lookup_by_registration_uses_expression_index():
    path = _seeded_db()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(path)
        db.init_db_all()
        with db.conn() as c:
            c.executemany(
                "INSERT INTO opensky_aircraft_registry (icao24, registration, updated_at) VALUES (?, ?, ?)",
                [(f"{i:06x}", f"N{i}", time.time()) for i in range(2000)],
            )
            c.execute("ANALYZE")
            plan = _query_plan(
                c,
                "SELECT * FROM opensky_aircraft_registry "
                "WHERE UPPER(REPLACE(registration, '-', ''))=?",
                ("N12345",),
            )
        assert any(
            "idx_opensky_registry_registration_upper_nodash" in row["detail"] for row in plan
        ), plan
    finally:
        db._db_path = orig_db_path
        Path(path).unlink(missing_ok=True)
