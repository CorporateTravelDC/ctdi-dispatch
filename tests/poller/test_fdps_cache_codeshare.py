"""
Regression test for the 2026-08-27 fix: poller.main._check_flight_fdps_cache
(the PERIODIC per-tick FDPS recheck) only ever tried
db.get_flight_plan_by_callsign(ident) -- a direct match on the watchlist
entry's own identifier. For a codeshare/regional-operated flight (e.g.
"AAL5265" marketed by American, actually filed with FAA under "JIA5265",
PSA Airlines operating as American Eagle) that never matches, so the
entry's FDPS status silently never updated after the initial add, even
though add_flight_watchlist's own add-time flow already has a fallback
for exactly this case.

Confirmed live: AAL5265 (PHL-DCA) never got last_fdps_status populated by
the periodic recheck despite a real, active JIA5265 KPHL-KDCA plan sitting
in flight_events the whole time.

Fix: prefer a CONFIRMED codeshare_map mapping over a blind
ignore-the-carrier flight_num+origin scan on every tick (flight numbers
are reused across unrelated carriers/routes same-day -- confirmed live,
bare flight_num 5265 alone matched CAL/SKW/EDV/JIA, four different
routes, the same day). Only fall back to the broad scan when no mapping
exists yet, and seed codeshare_map on a successful fallback hit so future
ticks go straight to the precise path.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import common.db as db


def _isolated_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig_db_path = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    return orig_db_path, tmp.name


def _make_entry(identifier="AAL5265", origin="PHL", destination="DCA"):
    return {
        "id": f"wl-flight-{identifier.lower()}-test",
        "entry_type": "flight",
        "tier": "transient",
        "identifier": identifier,
        "origin": origin,
        "destination": destination,
        "route_name": None,
        "scheduled_departure": None,
        "scheduled_arrival": None,
        "auto_remove_at": None,
        "added_at": "2026-08-27T00:00:00Z",
        "added_by": "test",
        "notes": None,
        "last_event_at": None,
        "last_event_summary": None,
    }


def test_codeshare_fallback_finds_plan_and_seeds_mapping():
    orig_db_path, tmp_name = _isolated_db()
    try:
        entry = _make_entry()
        db.upsert_watchlist_entry(entry)
        db.upsert_flight_event(
            flight_id="JIA5265-test", airline="JIA", flight_num="5265",
            origin="KPHL", destination="KDCA", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )

        with patch("shared.watchlist._fire_ntfy_dual"):
            from poller.main import _check_flight_fdps_cache
            _check_flight_fdps_cache(entry, "AAL5265")

        updated = db.get_watchlist_entries(entry_type="flight")[0]
        assert updated["last_fdps_status"] == "active"

        mappings = db.get_codeshare_mapping_by_marketing("AAL", "5265")
        assert len(mappings) == 1
        assert mappings[0]["operating_carrier"] == "JIA"
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)


def test_confirmed_mapping_skips_broad_scan_on_later_ticks():
    """Once codeshare_map already has a confirmed pairing, a later tick
    must resolve via the direct operating-carrier callsign, never falling
    through to the broad ignore-carrier flight_num+origin scan again."""
    orig_db_path, tmp_name = _isolated_db()
    try:
        entry = _make_entry()
        db.upsert_watchlist_entry(entry)
        db.upsert_flight_event(
            flight_id="JIA5265-test2", airline="JIA", flight_num="5265",
            origin="KPHL", destination="KDCA", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )
        db.upsert_codeshare_mapping(
            marketing_carrier="AAL", marketing_flight_num="5265",
            operating_carrier="JIA", operating_flight_num="5265",
            origin="KPHL", destination="KDCA", source="test-seed",
        )

        with patch("shared.watchlist._fire_ntfy_dual"), \
             patch("common.db.get_flight_plan_by_flight_num") as mock_broad_scan:
            from poller.main import _check_flight_fdps_cache
            _check_flight_fdps_cache(entry, "AAL5265")
            mock_broad_scan.assert_not_called()

        updated = db.get_watchlist_entries(entry_type="flight")[0]
        assert updated["last_fdps_status"] == "active"
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)


def test_unrelated_same_day_flight_number_reuse_does_not_collide():
    """The whole point of preferring codeshare_map over a blind scan: a
    flight number reused by an unrelated carrier/route the same day must
    never get attached to this entry."""
    orig_db_path, tmp_name = _isolated_db()
    try:
        entry = _make_entry()
        db.upsert_watchlist_entry(entry)
        # Only an unrelated same-flight-number flight exists -- no real
        # JIA5265 PHL-DCA plan on file yet.
        db.upsert_flight_event(
            flight_id="SKW5265-test", airline="SKW", flight_num="5265",
            origin="KDEN", destination="KRAP", aircraft_type=None,
            departure_time=None, arrival_time=None, status="active",
            position_lat=None, position_lon=None, altitude_ft=None,
            ground_speed_kt=None, raw_json="{}",
        )

        with patch("shared.watchlist._fire_ntfy_dual"):
            from poller.main import _check_flight_fdps_cache
            _check_flight_fdps_cache(entry, "AAL5265")

        updated = db.get_watchlist_entries(entry_type="flight")[0]
        assert updated["last_fdps_status"] is None
        assert db.get_codeshare_mapping_by_marketing("AAL", "5265") == []
    finally:
        db._db_path = orig_db_path
        Path(tmp_name).unlink(missing_ok=True)
