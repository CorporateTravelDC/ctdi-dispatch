"""nas_programs lifecycle (2026-10-18, lean-out L1, operator-approved).

deactivate_absent_programs() (REST fetcher, every 300 s) used to rewrite every
row not in the REST list: the ~59k already-inactive rows (39.4 M of 63.8 M
updates in 59 h) and every TFMS-sourced program within 5 minutes of TFMS
writing it. Now: active REST rows only; TFMS rows end at their own end_time
(expire_ended_tfms_programs). nas_programs.source is pg_schema/0076 on
Postgres and init_db_v47 on SQLite, with the same REST-shape backfill.
"""
import json
import re
import sys
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))

from common import db  # noqa: E402

MIGRATION = REPO / "src/common/pg_schema/0076_nas_programs_source.sql"


def _setup():
    db.init_db_all()


def _rows() -> dict:
    with db.conn() as c:
        return {r["program_id"]: dict(r) for r in c.execute("SELECT * FROM nas_programs").fetchall()}


def _changes() -> int:
    with db.conn() as c:
        return c.total_changes


def _tfms(pid, ptype, facility, end_time):
    db.upsert_nas_program(pid, ptype, facility,
                          json.dumps({"program_id": pid, "type": ptype, "end_time": end_time}),
                          source=db.NAS_SOURCE_TFMS)


def test_rest_deactivation_never_touches_tfms_rows():
    _setup()
    future = time.time() + 3600
    _tfms("RSTR-1", "MIT", "KDCA", future)
    _tfms("GS-IAD-1755943200", "GS", "IAD", future)
    _tfms("AFP-FCAJX5-1", "AFP", "FCAJX5", future)
    # A TFMS row written before the source column existed (source NULL).
    db.upsert_nas_program("RSTR-2", "STOP", "KIAD", json.dumps({"end_time": future}))
    db.upsert_nas_program("KDCA-DEP-WX", "DEP", "KDCA", "{}", source=db.NAS_SOURCE_REST)
    db.upsert_nas_program("KBWI-DEP-WX", "DEP", "KBWI", "{}", source=db.NAS_SOURCE_REST)

    n = db.deactivate_absent_programs(["KBWI-DEP-WX"])

    rows = _rows()
    assert n == 1
    assert rows["KDCA-DEP-WX"]["active"] == 0
    assert rows["KBWI-DEP-WX"]["active"] == 1
    for pid in ("RSTR-1", "GS-IAD-1755943200", "AFP-FCAJX5-1", "RSTR-2"):
        assert rows[pid]["active"] == 1, pid


def test_rest_deactivation_does_not_rewrite_inactive_rows():
    _setup()
    for i in range(5):
        db.upsert_nas_program(f"KDCA-DEP-R{i}", "DEP", "KDCA", "{}", source=db.NAS_SOURCE_REST)
    db.upsert_nas_program("KIAD-ARR-X", "ARR", "KIAD", "{}", source=db.NAS_SOURCE_REST)

    assert db.deactivate_absent_programs(["KIAD-ARR-X"]) == 5
    before = _changes()
    # Same list again: the five are already inactive -- nothing is written.
    assert db.deactivate_absent_programs(["KIAD-ARR-X"]) == 0
    assert _changes() == before


def test_rest_deactivation_covers_legacy_rest_rows_without_source():
    """A REST row written by the old code between the migration and the
    rollout (source NULL) is still the REST job's to switch off."""
    _setup()
    db.upsert_nas_program("KDCA-Closure", "Closure", "KDCA", "{}")
    db.upsert_nas_program("KIAD-GS-THUNDERSTORM", "GS", "KIAD", "{}")
    assert db.deactivate_absent_programs(["KBWI-DEP-OTHER"]) == 2
    rows = _rows()
    assert rows["KDCA-Closure"]["active"] == 0 and rows["KIAD-GS-THUNDERSTORM"]["active"] == 0


def test_empty_rest_list_still_touches_nothing():
    _setup()
    db.upsert_nas_program("KDCA-DEP-WX", "DEP", "KDCA", "{}", source=db.NAS_SOURCE_REST)
    assert db.deactivate_absent_programs([]) == 0
    assert _rows()["KDCA-DEP-WX"]["active"] == 1


def test_tfms_programs_end_at_their_own_end_time():
    _setup()
    now = 2_000_000_000.0
    _tfms("ENDED", "MIT", "KDCA", now - 60)
    _tfms("RUNNING", "APREQ", "KDCA", now + 600)
    db.upsert_nas_program("NOEND-OLD", "TBM", "KDCA", json.dumps({"type": "TBM"}),
                          source=db.NAS_SOURCE_TFMS)
    db.upsert_nas_program("NOEND-NEW", "TBM", "KIAD", json.dumps({"type": "TBM"}),
                          source=db.NAS_SOURCE_TFMS)
    db.upsert_nas_program("KDCA-DEP-WX", "DEP", "KDCA", "{}", source=db.NAS_SOURCE_REST)
    with db.conn() as c:
        c.execute("UPDATE nas_programs SET fetched_at = ? WHERE program_id = 'NOEND-OLD'",
                  (now - db.NAS_TFMS_NO_END_STALE_S - 1,))
        c.execute("UPDATE nas_programs SET fetched_at = ? WHERE program_id <> 'NOEND-OLD'", (now,))

    assert db.expire_ended_tfms_programs(now=now) == 2
    rows = _rows()
    assert rows["ENDED"]["active"] == 0 and rows["NOEND-OLD"]["active"] == 0
    assert rows["RUNNING"]["active"] == 1 and rows["NOEND-NEW"]["active"] == 1
    assert rows["KDCA-DEP-WX"]["active"] == 1          # a REST row is never the TFMS expiry's
    before = _changes()
    assert db.expire_ended_tfms_programs(now=now) == 0  # nothing rewritten on the next run
    assert _changes() == before


def test_writers_stamp_their_source():
    _setup()
    from ingest.parsers.tfms_parser import write_tfms_programs
    from poller.fetchers import nas

    assert write_tfms_programs([{"program_id": "GS-IAD-1", "type": "GS", "facility": "IAD",
                                 "start_time": 1.0, "end_time": time.time() + 600}]) == 1
    with mock.patch.object(nas, "fetch", return_value=[
            {"program_id": "KDCA-DEP-WX", "type": "DEP", "facility": "KDCA", "raw": {}}]):
        out = nas.run()
    assert out["count"] == 1
    rows = _rows()
    assert rows["GS-IAD-1"]["source"] == "tfms" and rows["GS-IAD-1"]["active"] == 1
    assert rows["KDCA-DEP-WX"]["source"] == "rest"


def test_upsert_without_source_keeps_the_stored_one():
    _setup()
    db.upsert_nas_program("KDCA-DEP-WX", "DEP", "KDCA", "{}", source=db.NAS_SOURCE_REST)
    db.upsert_nas_program("KDCA-DEP-WX", "DEP", "KDCA", "{}")
    assert _rows()["KDCA-DEP-WX"]["source"] == "rest"


def test_v47_backfill_stamps_rest_shape_only_and_is_idempotent():
    _setup()
    with db.conn() as c:
        c.execute("UPDATE nas_programs SET source = NULL")
    for pid, t, f in [("KDCA-DEP-WX", "DEP", "KDCA"), ("KDCA-Closure", "Closure", "KDCA"),
                      ("GS-IAD-1755943200", "GS", "IAD"), ("RSTR-9", "MIT", "KDCA"),
                      ("KIAD-GS-SNOW", "GS", "KIAD")]:
        with db.conn() as c:
            c.execute("INSERT INTO nas_programs (program_id, type, facility, raw_json, active, fetched_at) "
                      "VALUES (?, ?, ?, '{}', 1, 0)", (pid, t, f))
    db.init_db_v47()
    db.init_db_v47()
    src = {k: v["source"] for k, v in _rows().items()}
    assert src == {"KDCA-DEP-WX": "rest", "KDCA-Closure": "rest", "KIAD-GS-SNOW": "rest",
                   "GS-IAD-1755943200": None, "RSTR-9": None}


def test_pg_migration_backfill_is_the_same_predicate():
    """0076's backfill and db.NAS_REST_SHAPE_SQL must never drift apart."""
    sql = MIGRATION.read_text()
    norm = lambda s: re.sub(r"\s+", " ", s).replace("( ", "(").replace(" )", ")").strip()
    m = re.search(r"WHERE source IS NULL\s+AND (.*?);", sql, re.S)
    assert m, "0076 backfill UPDATE not found"
    assert norm(m.group(1)) == norm(db.NAS_REST_SHAPE_SQL.strip()[1:-1])
    assert "ADD COLUMN IF NOT EXISTS source TEXT" in sql


def test_tfms_resend_of_ended_program_stays_inactive():
    """2026-10-21: TFMS re-sends programs after they end; the upsert must not
    switch an ended one back on. A live re-send still reactivates."""
    _setup()
    past, future = time.time() - 3600, time.time() + 3600
    _tfms("MIT-EWR-ENDED", "MIT", "KEWR", past)
    _tfms("MIT-LGA-LIVE", "MIT", "KLGA", future)
    rows = _rows()
    assert rows["MIT-EWR-ENDED"]["active"] == 0
    assert rows["MIT-LGA-LIVE"]["active"] == 1
    _tfms("MIT-EWR-ENDED", "MIT", "KEWR", future)   # extended by a later re-send
    assert _rows()["MIT-EWR-ENDED"]["active"] == 1
