"""
Regression test for the 2026-08-26 C-7 fix (Opus blind review): the FAA and
OpenSky aircraft-registry mark-and-sweep prunes (faa_registry_sweep_removed,
opensky_registry_sweep_removed) used to run unconditionally after the
upsert loop in each fetcher. The callers' own comments claimed the
try/except around that loop already guarded this, but that only catches an
exception -- a 200-OK response that parses to zero rows raises nothing, so
the run's cutoff timestamp predates every existing row and the sweep
deleted the ENTIRE table. Confirmed live for both registries (316,222 and
519,991 rows respectively). db._safe_mark_and_sweep() is the shared fix,
used by both sweep functions: refuse to delete if doing so would empty a
table that currently has rows.
"""
import time
import tempfile
from pathlib import Path

import common.db as db


def _faa_record(n_number: str, updated_at: float) -> dict:
    return {
        "n_number": n_number,
        "mode_s_hex": None,
        "serial_number": None,
        "mfr_mdl_code": None,
        "year_mfr": None,
        "registrant_name": None,
        "city": None,
        "state": None,
        "status_code": None,
        "type_aircraft": None,
        "type_engine": None,
        "expiration_date": None,
        "last_action_date": None,
        "cert_issue_date": None,
        "updated_at": updated_at,
    }


def test_faa_registry_sweep_refuses_to_wipe_on_zero_upsert_run():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        old_ts = time.time() - 3600
        db.faa_upsert_aircraft([
            _faa_record("N123AB", old_ts),
            _faa_record("N456CD", old_ts),
        ])

        # Simulate a run that upserted nothing (bad/empty upstream response,
        # no exception raised) -- cutoff captured "now", after both rows'
        # updated_at. This is exactly the shape that wiped 316,222 rows live.
        run_cutoff = time.time()
        removed = db.faa_registry_sweep_removed(run_cutoff)

        assert removed == 0, (
            "a zero-upsert run must not be allowed to look like every "
            "aircraft deregistered at once"
        )
        assert db.faa_registry_count()["total"] == 2

        # A genuine sweep (real deregistration) still works: one row
        # refreshed this run, one genuinely dropped out of the source.
        run_cutoff2 = time.time()
        db.faa_upsert_aircraft([_faa_record("N123AB", run_cutoff2 + 1)])
        removed2 = db.faa_registry_sweep_removed(run_cutoff2)
        assert removed2 == 1
        assert db.faa_registry_count()["total"] == 1
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)


def _opensky_record(icao24: str, updated_at: float) -> dict:
    return {
        "icao24": icao24,
        "registration": None,
        "manufacturer_icao": None,
        "manufacturer_name": None,
        "model": None,
        "typecode": None,
        "serial_number": None,
        "icao_aircraft_type": None,
        "operator": None,
        "operator_icao": None,
        "operator_iata": None,
        "owner": None,
        "registered": None,
        "reg_until": None,
        "status": None,
        "built": None,
        "updated_at": updated_at,
    }


def test_opensky_registry_sweep_refuses_to_wipe_on_zero_upsert_run():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        old_ts = time.time() - 3600
        db.opensky_upsert_aircraft([
            _opensky_record("a1b2c3", old_ts),
            _opensky_record("d4e5f6", old_ts),
        ])

        run_cutoff = time.time()
        removed = db.opensky_registry_sweep_removed(run_cutoff)

        assert removed == 0, (
            "a zero-upsert run must not be allowed to look like every "
            "aircraft deregistered at once"
        )
        assert db.opensky_registry_count() == 2
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)


def test_safe_mark_and_sweep_noop_on_already_empty_table():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    try:
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()

        # An empty table is a legitimate starting state (fresh install) --
        # must not be treated as "refuse" (there's nothing to protect).
        removed = db.faa_registry_sweep_removed(time.time())
        assert removed == 0
    finally:
        db._db_path = orig_db_path
        Path(tmp.name).unlink(missing_ok=True)
