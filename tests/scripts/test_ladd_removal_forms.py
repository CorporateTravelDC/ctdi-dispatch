"""tests/scripts/test_ladd_removal_forms.py -- 2026-11-04: a removal deletes EVERY stored form of an
identifier. The remove file and the filter file can list one aircraft as "N<number>" and "<number>";
an exact-string delete left 2 removed aircraft withheld (caught by ladd-import.sh's exhaustive check).
Synthetic identifiers only, built at runtime (public scrub rule)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from common import db

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import-ladd-filter.py"
BARE = "12" + "QZ"          # registry form
NFORM = "N" + BARE          # filter-file form


def _importer():
    spec = importlib.util.spec_from_file_location("import_ladd_filter_t", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_removal_in_one_form_deletes_the_other_form_too():
    db.init_db_v11()
    m = _importer()
    with db.conn() as c:
        # the test backend (SQLite) has no pg_schema migrations: same columns as 0063_ladd_removals.sql
        c.execute("CREATE TABLE IF NOT EXISTS faa_ladd_removals (n_number TEXT PRIMARY KEY, "
                  "removed_at DOUBLE PRECISION NOT NULL, source_file TEXT, note TEXT)")
        c.execute("DELETE FROM faa_ladd_aircraft")
        c.execute("DELETE FROM faa_ladd_removals")
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", (BARE, 0))
        c.execute("INSERT INTO faa_ladd_aircraft (n_number, updated_at) VALUES (?, ?)", ("QKEEP1", 0))
        c.execute("INSERT INTO faa_ladd_removals (n_number, removed_at, source_file) VALUES (?, ?, ?)",
                  (NFORM, 0, "remove.txt"))
    assert db.faa_is_ladd(NFORM)           # withheld through the other form before the removal runs
    with db.conn() as c:
        recorded, deleted = m._apply_removals(c)
    assert (recorded, deleted) == (1, 1)
    assert not db.faa_is_ladd(NFORM) and not db.faa_is_ladd(BARE)
    assert db.faa_is_ladd("QKEEP1")
