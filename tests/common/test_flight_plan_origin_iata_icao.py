"""
Regression test for the 2026-08-27 fix: db.get_flight_plan_by_flight_num's
`origin` filter used exact string equality against flight_events.origin,
which is stored in ICAO form (4-letter, e.g. "KPHL"). Every real caller
(add_flight_watchlist's body.origin, a human typing a watchlist entry by
hand) commonly passes the IATA 3-letter form ("PHL") instead -- confirmed
live: get_flight_plan_by_flight_num("5265", origin="PHL") returned None
while origin="KPHL" found a real, live-tracked JIA5265 PHL-DCA flight plan
for the exact same flight. Fixed by widening a bare 3-letter origin to also
try the K-prefixed ICAO form (and the reverse).
"""
import tempfile
from pathlib import Path

import common.db as db


def _isolated_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    return orig_db_path, tmp.name


def test_iata_origin_matches_icao_stored_row():
    orig_db_path, tmp_name = _isolated_db()
    try:
        db.upsert_flight_event(
            flight_id="JIA5265-test", airline="JIA", flight_num="5265",
            origin="KPHL", destination="KDCA", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )

        plan = db.get_flight_plan_by_flight_num("5265", origin="PHL")
        assert plan is not None
        assert plan["airline"] == "JIA"
        assert plan["origin"] == "KPHL"
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)


def test_icao_origin_still_matches_directly():
    """Regression guard: the widening must not break the existing
    exact-ICAO-match case."""
    orig_db_path, tmp_name = _isolated_db()
    try:
        db.upsert_flight_event(
            flight_id="JIA5265-test2", airline="JIA", flight_num="5265",
            origin="KPHL", destination="KDCA", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )

        plan = db.get_flight_plan_by_flight_num("5265", origin="KPHL")
        assert plan is not None
        assert plan["airline"] == "JIA"
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)


def test_unrelated_origin_still_does_not_match():
    """Flight number reuse across unrelated routes (confirmed live: 5265
    alone matched CAL/SKW/EDV/JIA on the same day) must not collide just
    because the widening is more permissive on form, not on which airport."""
    orig_db_path, tmp_name = _isolated_db()
    try:
        db.upsert_flight_event(
            flight_id="SKW5265-test", airline="SKW", flight_num="5265",
            origin="KDEN", destination="KRAP", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )

        plan = db.get_flight_plan_by_flight_num("5265", origin="PHL")
        assert plan is None
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)
