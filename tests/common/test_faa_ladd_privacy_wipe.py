"""
Regression test for the 2026-08-25 C-31/C-14 fix (Opus blind review):
db.faa_upsert_ladd() used to unconditionally DELETE + re-insert the FAA
LADD privacy opt-out list, so any upstream hiccup that made the parse come
back empty (redirect swallowed as a valid zip, format change, transient
outage -- all non-fatal per faa_registry.py's own except clause) silently
wiped every previously-protected tail number's privacy flag to false, with
nothing louder than an info-level "0 entries stored" line. Confirmed live:
faa_ladd_aircraft held 0 rows. This locks in the corrected contract: an
empty replacement is refused, not applied.
"""
import tempfile
from pathlib import Path

import common.db as db


def test_faa_upsert_ladd_refuses_to_wipe_on_empty_input():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        stored = db.faa_upsert_ladd(["N123AB", "N456CD", "N789EF"])
        assert stored == 3
        assert db.faa_ladd_count() == 3

        # The regression: an empty parse result must NOT clear the table.
        refused = db.faa_upsert_ladd([])
        assert refused == 3, (
            "an empty LADD parse must leave the existing privacy list "
            "intact -- this is the exact wipe-to-zero the FAA LADD privacy "
            "list actually suffered live"
        )
        assert db.faa_ladd_count() == 3

        # A genuine, non-empty replacement still works normally.
        replaced = db.faa_upsert_ladd(["N999ZZ"])
        assert replaced == 1
        assert db.faa_ladd_count() == 1
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)


def test_faa_upsert_ladd_ignores_blank_strings_without_treating_as_empty():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        db.faa_upsert_ladd(["N123AB"])
        # A real (non-blank) replacement list still replaces normally even
        # if it also contains blank/whitespace noise mixed in.
        stored = db.faa_upsert_ladd(["", "  ", "N456CD"])
        assert stored == 1
        assert db.faa_ladd_count() == 1
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)
