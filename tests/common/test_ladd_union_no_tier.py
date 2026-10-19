"""2026-10-19 (glossary rule "LADD is the union of both lists"): the LADD set is the union of the FAA
Source and Industry filter files, and which list an entry came from is never stored. Static checks
only: no database, no filter file, no identifier."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CREATE = re.compile(r"CREATE TABLE IF NOT EXISTS faa_ladd_aircraft\s*\((.*?)\);", re.S)


def _columns(body: str) -> list[str]:
    return [ln.split()[0] for ln in (x.strip() for x in body.split(",")) if ln and not ln.upper().startswith(("PRIMARY", "UNIQUE", "CONSTRAINT"))]


def test_ladd_table_holds_the_identifier_and_a_stamp_only():
    sources = [REPO / "src/common/db.py"] + sorted((REPO / "src/common/pg_schema").glob("*.sql"))
    bodies = [m.group(1) for p in sources for m in CREATE.finditer(p.read_text(encoding="utf-8"))]
    assert len(bodies) >= 2, "expected the SQLite and PostgreSQL definitions"
    for body in bodies:
        assert _columns(body) == ["n_number", "updated_at"], _columns(body)


def test_no_migration_adds_a_column_to_the_ladd_table():
    alter = re.compile(r"ALTER TABLE (IF EXISTS )?faa_ladd_aircraft\s+ADD", re.I)
    hits = [p.name for p in sorted((REPO / "src/common/pg_schema").glob("*.sql")) if alter.search(p.read_text(encoding="utf-8"))]
    assert hits == [], f"a column on faa_ladd_aircraft would record the list of origin: {hits}"


def test_importer_merges_every_supplied_file_into_one_set():
    src = (REPO / "scripts/import-ladd-filter.py").read_text(encoding="utf-8")
    assert "combined: set[str] = set()" in src and "combined |= idents" in src
    assert not re.search(r"\b(tier|list_of_origin|source_list)\b\s*=", src), "the importer must not tag entries by list"
